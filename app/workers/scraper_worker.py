"""
Worker pour scraping automatique (Version gratuite)
"""
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional
import logging

from sqlalchemy import select, and_, delete
from sqlalchemy.ext.asyncio import AsyncSession
from supabase import create_client, Client

from app.core.config import settings, APP_QUOTAS
from app.core.database import AsyncSessionLocal
from app.models.models import UserAlert, DetectedSlot, Club, PushToken
from app.services.doinsport_scraper import DoinsportScraper
from app.services.email_service import send_slot_notification
from app.services.push_service import send_slot_push_notification

logging.basicConfig(
    level=settings.LOG_LEVEL,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

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

"""Envoie notification (email + push)"""
async def send_notification(user_id: str, club_name: str, slot: dict, detected_slot: DetectedSlot, alert_id: str, db: AsyncSession, club_slug: str = None):    
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
    
    # 2. Push notifications
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
        
        booking_url = f"https://{club_slug}.doinsport.club/home" if club_slug else None
        
        # CORRECTION ICI : Réalignement de la boucle for
        for pt in push_tokens:
            success = await send_slot_push_notification(
                push_token=pt.token,
                club_name=club_name,
                slot=slot,
                alert_id=alert_id,
                booking_url=booking_url
            )
            # CORRECTION ICI : Réalignement du if success
            if success:
                logger.info(f"📲 Push envoyé ({pt.device_type})")
                notifications_sent += 1
    except Exception as e:
        logger.error(f"❌ Erreur envoi push: {e}")
    
    return notifications_sent > 0


async def process_alert(alert_id: str) -> dict:
    """Traite une alerte: scrape et notifie si nouveaux créneaux"""
    stats = {"new_slots": 0, "notifications_sent": 0, "errors": 0}
    
    async with AsyncSessionLocal() as db:
        try:
            result = await db.execute(
                select(UserAlert).where(UserAlert.id == alert_id)
            )
            alert = result.scalar_one_or_none()
            
            if not alert or not alert.is_active:
                return stats
            
            # Vérifier si la date cible est passée
            today = datetime.now(timezone.utc).date()
            if alert.target_date < today:
                logger.info(f"📅 Alerte {alert_id} expirée, désactivation")
                alert.is_active = False
                await db.commit()
                return stats
            
            # Récupérer le club
            result = await db.execute(select(Club).where(Club.id == alert.club_id))
            club = result.scalar_one_or_none()
            
            if not club:
                logger.error(f"❌ Club {alert.club_id} non trouvé")
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
                slot_date = datetime.strptime(slot['date'], "%Y-%m-%d").date()
                slot_time = datetime.strptime(slot['start_time'], "%H:%M").time()
                
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
                    continue
                
                # Nouveau créneau
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
                
                if not is_baseline:
                    logger.info(f"🆕 Nouveau: {slot['playground_name']} | {slot['date']} {slot['start_time']}")
                    if await send_notification(alert.user_id, club.name, slot, detected_slot, str(alert.id), db, club_slug=club.slug):
                        stats["notifications_sent"] += 1
            
            if is_baseline:
                alert.baseline_scraped = True
                logger.info(f"✅ Baseline établie: {len(slots)} créneaux")
            
            alert.last_checked_at = datetime.now(timezone.utc)
            await db.commit()
            
        except Exception as e:
            logger.error(f"❌ Erreur traitement alerte {alert_id}: {e}")
            stats["errors"] += 1
    
    return stats


async def cleanup_expired_data():
    """Nettoie les données expirées"""
    async with AsyncSessionLocal() as db:
        try:
            today = datetime.now(timezone.utc).date()
            week_ago = today - timedelta(days=7)
            
            # Désactiver les alertes expirées
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
                await db.delete(alert)  # CASCADE supprime les detected_slots
                logger.info(f"🗑️ Alerte {alert.id} supprimée (date dépassée)")
            
            await db.commit()
            logger.info(f"🧹 Cleanup: {len(expired_alerts)} alertes supprimées")
            
        except Exception as e:
            logger.error(f"❌ Erreur cleanup: {e}")


async def scheduler_loop():
    """Boucle principale du scheduler"""
    check_interval_seconds = APP_QUOTAS["check_interval_minutes"] * 60
    
    logger.info("🚀 Worker Scheduler démarré")
    logger.info(f"⚙️ Check interval: {APP_QUOTAS['check_interval_minutes']} minutes")
    
    await cleanup_expired_data()
    
    loop_count = 0
    
    while True:
        loop_count += 1
        cycle_start = datetime.now(timezone.utc)
        
        try:
            async with AsyncSessionLocal() as db:
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
                    # Vérifier intervalle
                    if alert.last_checked_at:
                        last_check = alert.last_checked_at
                        if last_check.tzinfo is None:
                            last_check = last_check.replace(tzinfo=timezone.utc)
                        
                        seconds_since = (datetime.now(timezone.utc) - last_check).total_seconds()
                        
                        if seconds_since < check_interval_seconds:
                            continue
                    
                    stats = await process_alert(str(alert.id))
                    alerts_processed += 1
                    total_new_slots += stats["new_slots"]
                    total_notifications += stats["notifications_sent"]
                    
                    await asyncio.sleep(1)
                
                cycle_duration = (datetime.now(timezone.utc) - cycle_start).total_seconds()
                logger.info(f"✅ Cycle #{loop_count} terminé en {cycle_duration:.1f}s | "
                           f"{alerts_processed} traitées | {total_new_slots} nouveaux | {total_notifications} notifs")
            
            # Cleanup périodique
            if loop_count % 100 == 0:
                await cleanup_expired_data()
            
            await asyncio.sleep(settings.WORKER_CHECK_INTERVAL)
            
        except Exception as e:
            logger.error(f"❌ Erreur scheduler: {e}")
            import traceback
            traceback.print_exc()
            await asyncio.sleep(10)


if __name__ == "__main__":
    asyncio.run(scheduler_loop())