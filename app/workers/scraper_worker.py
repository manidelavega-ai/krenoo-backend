"""
Worker pour scraping automatique - Version améliorée
"""
import asyncio
from datetime import datetime, timedelta, timezone, date as date_type
from typing import Optional
import logging

from sqlalchemy import select, and_, delete
from sqlalchemy.ext.asyncio import AsyncSession
from supabase import create_client, Client

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.models import UserAlert, DetectedSlot, Club, PushToken
from app.services.doinsport_scraper import DoinsportScraper
from app.services.email_service import send_slot_notification
from app.services.push_service import send_slot_push_notification

# Configuration logging
logging.basicConfig(
    level=settings.LOG_LEVEL,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Client Supabase singleton (évite de recréer à chaque notif)
_supabase_client: Optional[Client] = None

def get_supabase_client() -> Client:
    global _supabase_client
    if _supabase_client is None:
        _supabase_client = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)
    return _supabase_client


async def get_user_info(user_id: str) -> tuple[str, str]:
    """Récupère email et nom de l'utilisateur"""
    try:
        supabase = get_supabase_client()
        user_data = supabase.auth.admin.get_user_by_id(user_id)
        if user_data and user_data.user:
            email = user_data.user.email
            name = user_data.user.user_metadata.get('name') or email.split('@')[0]
            return email, name
    except Exception as e:
        logger.error(f"❌ Erreur récupération user {user_id}: {e}")
    return None, None


async def send_notification(user_id: str, club_name: str, slot: dict, detected_slot: DetectedSlot, alert_id: str, db: AsyncSession):
    """Envoie notification (email + push)"""
    email, name = await get_user_info(str(user_id))
    notifications_sent = 0
    
    # 1. Email
    if email:
        email_sent = send_slot_notification(
            to_email=email,
            user_name=name,
            club_name=club_name,
            slot=slot
        )
        
        if email_sent:
            detected_slot.email_sent = True
            detected_slot.email_sent_at = datetime.now(timezone.utc)
            logger.info(f"📧 Email envoyé à {email}")
            notifications_sent += 1
    
    # 2. Push notifications - récupérer tous les tokens de l'utilisateur
    try:
        result = await db.execute(
            select(PushToken).where(
                and_(
                    PushToken.user_id == user_id,
                    PushToken.is_active == True
                )
            )
        )
        push_tokens = result.scalars().all()
        
        for pt in push_tokens:
            success = await send_slot_push_notification(
                push_token=pt.token,
                club_name=club_name,
                slot=slot,
                alert_id=alert_id
            )
            if success:
                logger.info(f"📲 Push envoyé ({pt.device_type})")
                notifications_sent += 1
            else:
                # Token potentiellement invalide - le désactiver
                logger.warning(f"⚠️ Push échoué pour token {pt.token[:20]}...")
                
    except Exception as e:
        logger.error(f"❌ Erreur envoi push: {e}")
    
    return notifications_sent > 0


async def process_alert(alert_id: str) -> dict:
    """
    Traite une alerte: scrape et notifie si nouveaux créneaux
    Retourne des stats pour le monitoring
    """
    stats = {"new_slots": 0, "notifications_sent": 0, "errors": 0}
    
    async with AsyncSessionLocal() as db:
        try:
            # Récupérer l'alerte avec le club (jointure implicite)
            result = await db.execute(
                select(UserAlert).where(UserAlert.id == alert_id)
            )
            alert = result.scalar_one_or_none()
            
            if not alert:
                logger.warning(f"⚠️ Alerte {alert_id} non trouvée")
                return stats
            
            if not alert.is_active:
                logger.debug(f"⏸️ Alerte {alert_id} inactive, skip")
                return stats
            
            # Vérifier si la date cible est passée
            today = datetime.now(timezone.utc).date()
            if alert.target_date < today:
                logger.info(f"📅 Alerte {alert_id} expirée (date: {alert.target_date}), désactivation")
                alert.is_active = False
                await db.commit()
                return stats
            
            # Récupérer le club
            result = await db.execute(select(Club).where(Club.id == alert.club_id))
            club = result.scalar_one_or_none()
            
            if not club:
                logger.error(f"❌ Club {alert.club_id} non trouvé pour alerte {alert_id}")
                stats["errors"] += 1
                return stats
            
            is_baseline = not alert.baseline_scraped
            
            logger.info(f"🔍 Alert {alert_id[:8]}... | {club.name} | {alert.target_date} | "
                       f"{'BASELINE' if is_baseline else 'SCAN'}")
            
            # Scraper Doinsport
            scraper = DoinsportScraper()
            try:
                slots = await scraper.get_available_slots(
                    club_id=str(club.doinsport_id),
                    date=alert.target_date.strftime("%Y-%m-%d"),
                    time_from=alert.time_from.strftime("%H:%M:%S"),
                    time_to=alert.time_to.strftime("%H:%M:%S"),
                    indoor_only=alert.indoor_only
                )
            finally:
                await scraper.close()
            
            # Traiter les créneaux
            for slot in slots:
                # Parser date/time
                slot_date = datetime.strptime(slot['date'], "%Y-%m-%d").date()
                slot_time = datetime.strptime(slot['start_time'], "%H:%M").time()
                
                # Vérifier si déjà en DB
                existing = await db.execute(
                    select(DetectedSlot).where(
                        and_(
                            DetectedSlot.alert_id == alert.id,
                            DetectedSlot.playground_id == slot['playground_id'],
                            DetectedSlot.date == slot_date,
                            DetectedSlot.start_time == slot_time
                        )
                    )
                )
                
                if existing.scalar_one_or_none():
                    continue  # Déjà connu
                
                # Nouveau créneau !
                detected_slot = DetectedSlot(
                    alert_id=alert.id,
                    club_id=club.id,
                    playground_id=slot['playground_id'],
                    playground_name=slot['playground_name'],
                    date=slot_date,
                    start_time=slot_time,
                    duration_minutes=slot['duration_minutes'],
                    price_total=slot['price_total'],
                    indoor=slot['indoor']
                )
                db.add(detected_slot)
                stats["new_slots"] += 1
                
                # Notifier seulement si baseline déjà établie
                if not is_baseline:
                    logger.info(f"🆕 Nouveau: {slot['playground_name']} | {slot['date']} {slot['start_time']}")
                    
                    if await send_notification(alert.user_id, club.name, slot, detected_slot, str(alert.id), db):
                        stats["notifications_sent"] += 1
                else:
                    logger.debug(f"📋 Baseline: {slot['playground_name']} | {slot['date']} {slot['start_time']}")
            
            # Marquer baseline comme fait
            if is_baseline:
                alert.baseline_scraped = True
                logger.info(f"✅ Baseline établie: {len(slots)} créneaux référencés")
            
            # Update timestamp
            alert.last_checked_at = datetime.now(timezone.utc)
            await db.commit()
            
            if stats["new_slots"] > 0:
                logger.info(f"✅ Alert {alert_id[:8]}... | {stats['new_slots']} nouveaux | {stats['notifications_sent']} notifs")
            
        except Exception as e:
            logger.error(f"❌ Erreur traitement alerte {alert_id}: {e}")
            stats["errors"] += 1
            # Ne pas faire remonter l'exception pour continuer avec les autres alertes
    
    return stats


async def cleanup_expired_data():
    """Nettoie les données expirées"""
    async with AsyncSessionLocal() as db:
        try:
            today = datetime.now(timezone.utc).date()
            week_ago = today - timedelta(days=7)
            
            # 1. Désactiver les alertes expirées (date passée)
            result = await db.execute(
                select(UserAlert).where(
                    and_(
                        UserAlert.target_date < today,
                        UserAlert.is_active == True
                    )
                )
            )
            expired_alerts = result.scalars().all()
            
            for alert in expired_alerts:
                alert.is_active = False
                logger.info(f"📅 Alerte {alert.id} désactivée (expirée)")
            
            # 2. Supprimer les DetectedSlots de plus de 7 jours
            await db.execute(
                delete(DetectedSlot).where(DetectedSlot.date < week_ago)
            )
            
            await db.commit()
            logger.info(f"🧹 Cleanup: {len(expired_alerts)} alertes expirées, slots > 7j supprimés")
            
        except Exception as e:
            logger.error(f"❌ Erreur cleanup: {e}")


async def scheduler_loop():
    """Boucle principale du scheduler"""
    logger.info("🚀 Worker Scheduler démarré")
    logger.info(f"⚙️ Check interval: {settings.WORKER_CHECK_INTERVAL}s")
    
    # Cleanup au démarrage
    await cleanup_expired_data()
    
    loop_count = 0
    
    while True:
        loop_count += 1
        cycle_start = datetime.now(timezone.utc)
        
        try:
            async with AsyncSessionLocal() as db:
                # Récupérer alertes actives avec date future ou aujourd'hui
                today = datetime.now(timezone.utc).date()
                
                result = await db.execute(
                    select(UserAlert).where(
                        and_(
                            UserAlert.is_active == True,
                            UserAlert.target_date >= today
                        )
                    )
                )
                alerts = result.scalars().all()
                
                logger.info(f"📋 Cycle #{loop_count} | {len(alerts)} alerte(s) active(s)")
                
                alerts_processed = 0
                total_new_slots = 0
                total_notifications = 0
                
                for alert in alerts:
                    # Vérifier si besoin de check selon l'intervalle
                    if alert.last_checked_at:
                        # S'assurer que last_checked_at a un timezone
                        last_check = alert.last_checked_at
                        if last_check.tzinfo is None:
                            last_check = last_check.replace(tzinfo=timezone.utc)
                        
                        minutes_since = (datetime.now(timezone.utc) - last_check).total_seconds() / 60
                        
                        if minutes_since < alert.check_interval_minutes:
                            logger.debug(f"⏳ Alert {str(alert.id)[:8]}... check dans {alert.check_interval_minutes - minutes_since:.1f} min")
                            continue
                    
                    # Traiter l'alerte
                    stats = await process_alert(str(alert.id))
                    alerts_processed += 1
                    total_new_slots += stats["new_slots"]
                    total_notifications += stats["notifications_sent"]
                    
                    # Petit délai entre les alertes pour ne pas surcharger l'API
                    await asyncio.sleep(1)
                
                cycle_duration = (datetime.now(timezone.utc) - cycle_start).total_seconds()
                logger.info(f"✅ Cycle #{loop_count} terminé en {cycle_duration:.1f}s | "
                           f"{alerts_processed} traitées | {total_new_slots} nouveaux | {total_notifications} notifs")
            
            # Cleanup périodique (toutes les 100 boucles)
            if loop_count % 100 == 0:
                await cleanup_expired_data()
            
            # Attendre avant le prochain cycle
            await asyncio.sleep(settings.WORKER_CHECK_INTERVAL)
            
        except Exception as e:
            logger.error(f"❌ Erreur scheduler: {e}")
            import traceback
            traceback.print_exc()
            await asyncio.sleep(10)  # Retry après 10s


if __name__ == "__main__":
    asyncio.run(scheduler_loop())