"""
Routes API pour la gestion des alertes
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from sqlalchemy.orm import selectinload
from datetime import date, timedelta, datetime, timezone
from typing import List
from uuid import UUID
import logging

from app.core.database import get_db
from app.core.auth import get_current_user
from app.core.config import PLAN_QUOTAS, BOOST_CONFIG
from app.models.models import UserAlert, Club, Subscription, DetectedSlot, UserBoost
from app.schemas.schemas import AlertCreate, AlertResponse, AlertUpdate, DetectedSlotResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/alerts", tags=["alerts"])


@router.post("", response_model=AlertResponse, status_code=status.HTTP_201_CREATED)
async def create_alert(
    alert_data: AlertCreate,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Créer une nouvelle alerte avec validation plan Free/Premium"""
    
    # Vérifier le plan de l'utilisateur
    result = await db.execute(
        select(Subscription).where(Subscription.user_id == current_user.id)
    )
    subscription = result.scalar_one_or_none()
    plan = subscription.plan if subscription else "free"
    quota = PLAN_QUOTAS[plan]
    
    # === VALIDATION QUOTAS ===
    
    # 1. Vérifier quota alertes
    result = await db.execute(
        select(UserAlert).where(
            UserAlert.user_id == current_user.id,
            UserAlert.is_active == True
        )
    )
    active_alerts = len(result.scalars().all())
    
    if active_alerts >= quota["max_alerts"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Quota atteint: max {quota['max_alerts']} alerte(s) pour le plan {plan}"
        )
    
    # 2. Vérifier plage de dates
    today = date.today()
    min_date = today + timedelta(days=quota["min_days_ahead"])
    max_date = today + timedelta(days=quota["max_days_ahead"])
    
    if alert_data.target_date < min_date:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Plan {plan}: La date doit être au minimum {min_date.strftime('%d/%m/%Y')}"
        )
    
    if alert_data.target_date > max_date:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Plan {plan}: La date ne peut pas dépasser {max_date.strftime('%d/%m/%Y')}"
        )
    
    # 3. Vérifier plage horaire
    time_from_minutes = alert_data.time_from.hour * 60 + alert_data.time_from.minute
    time_to_minutes = alert_data.time_to.hour * 60 + alert_data.time_to.minute
    time_window_hours = (time_to_minutes - time_from_minutes) / 60
    
    if time_window_hours > quota["max_time_window_hours"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Plan {plan}: La plage horaire est limitée à {quota['max_time_window_hours']}h"
        )
    
    # 4. Vérifier que le club existe
    result = await db.execute(
        select(Club).where(Club.id == alert_data.club_id)
    )
    club = result.scalar_one_or_none()
    if not club:
        raise HTTPException(status_code=404, detail="Club non trouvé")
    
    # 5. Si use_boost, vérifier que l'utilisateur a des boosts
    user_boost = None
    if alert_data.use_boost:
        result = await db.execute(
            select(UserBoost).where(UserBoost.user_id == current_user.id)
        )
        user_boost = result.scalar_one_or_none()
        
        if not user_boost or user_boost.boost_count < 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Aucun boost disponible"
            )
    
    # === CRÉATION ALERTE ===
    
    new_alert = UserAlert(
        user_id=current_user.id,
        club_id=alert_data.club_id,
        target_date=alert_data.target_date,
        time_from=alert_data.time_from,
        time_to=alert_data.time_to,
        indoor_only=alert_data.indoor_only,
        check_interval_minutes=quota["check_interval_minutes"],
        baseline_scraped=False
    )
    
    # Si use_boost, activer le boost sur l'alerte
    if alert_data.use_boost and user_boost:
        # Décrémenter le compteur de boosts
        user_boost.boost_count -= 1
        user_boost.updated_at = datetime.now(timezone.utc)
        
        # Activer le boost
        boost_duration = timedelta(hours=BOOST_CONFIG["duration_hours"])
        new_alert.boost_active = True
        new_alert.boost_expires_at = datetime.now(timezone.utc) + boost_duration
        
        logger.info(f"🚀 Boost activé à la création pour user {current_user.id}")
    
    db.add(new_alert)
    await db.commit()
    await db.refresh(new_alert)
    
    logger.info(f"✅ Alert created: {new_alert.id} by user {current_user.id} - Date: {alert_data.target_date} - Boost: {alert_data.use_boost}")
    
    return AlertResponse(
        id=new_alert.id,
        user_id=new_alert.user_id,
        club_id=new_alert.club_id,
        club_name=club.name,
        target_date=new_alert.target_date,
        time_from=new_alert.time_from,
        time_to=new_alert.time_to,
        indoor_only=new_alert.indoor_only,
        is_active=new_alert.is_active,
        check_interval_minutes=new_alert.check_interval_minutes,
        last_checked_at=new_alert.last_checked_at,
        boost_active=new_alert.boost_active,
        boost_expires_at=new_alert.boost_expires_at,
        created_at=new_alert.created_at,
    )


@router.get("", response_model=List[AlertResponse])
async def list_alerts(
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Liste toutes les alertes de l'utilisateur"""
    result = await db.execute(
        select(UserAlert)
        .options(selectinload(UserAlert.club))
        .where(UserAlert.user_id == current_user.id)
    )
    alerts = result.scalars().all()
    
    return [
        AlertResponse(
            id=a.id,
            user_id=a.user_id,
            club_id=a.club_id,
            club_name=a.club.name if a.club else None,
            target_date=a.target_date,
            time_from=a.time_from,
            time_to=a.time_to,
            indoor_only=a.indoor_only,
            is_active=a.is_active,
            check_interval_minutes=a.check_interval_minutes,
            last_checked_at=a.last_checked_at,
            boost_active=a.boost_active,
            boost_expires_at=a.boost_expires_at,
            created_at=a.created_at,
        )
        for a in alerts
    ]


@router.get("/{alert_id}/history", response_model=List[DetectedSlotResponse])
async def get_alert_history(
    alert_id: UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Historique des créneaux détectés pour une alerte"""
    result = await db.execute(
        select(UserAlert).where(
            UserAlert.id == alert_id,
            UserAlert.user_id == current_user.id
        )
    )
    alert = result.scalar_one_or_none()
    if not alert:
        raise HTTPException(status_code=404, detail="Alerte non trouvée")
    
    result = await db.execute(
        select(DetectedSlot)
        .where(DetectedSlot.alert_id == alert_id)
        .order_by(DetectedSlot.detected_at.desc())
        .limit(100)
    )
    return result.scalars().all()


@router.patch("/{alert_id}", response_model=AlertResponse)
async def update_alert(
    alert_id: UUID,
    alert_update: AlertUpdate,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Modifier une alerte (pause/resume, etc.)"""
    result = await db.execute(
        select(UserAlert)
        .options(selectinload(UserAlert.club))
        .where(
            UserAlert.id == alert_id,
            UserAlert.user_id == current_user.id
        )
    )
    alert = result.scalar_one_or_none()
    if not alert:
        raise HTTPException(status_code=404, detail="Alerte non trouvée")
    
    update_data = alert_update.dict(exclude_unset=True)
    
    if 'target_date' in update_data:
        update_data['baseline_scraped'] = False
    
    for field, value in update_data.items():
        setattr(alert, field, value)
    
    await db.commit()
    await db.refresh(alert)
    
    logger.info(f"✅ Alert updated: {alert_id}")
    
    return AlertResponse(
        id=alert.id,
        user_id=alert.user_id,
        club_id=alert.club_id,
        club_name=alert.club.name if alert.club else None,
        target_date=alert.target_date,
        time_from=alert.time_from,
        time_to=alert.time_to,
        indoor_only=alert.indoor_only,
        is_active=alert.is_active,
        check_interval_minutes=alert.check_interval_minutes,
        last_checked_at=alert.last_checked_at,
        boost_active=alert.boost_active,
        boost_expires_at=alert.boost_expires_at,
        created_at=alert.created_at,
    )


@router.delete("/{alert_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_alert(
    alert_id: UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Supprimer une alerte"""
    result = await db.execute(
        select(UserAlert).where(
            UserAlert.id == alert_id,
            UserAlert.user_id == current_user.id
        )
    )
    alert = result.scalar_one_or_none()
    if not alert:
        raise HTTPException(status_code=404, detail="Alerte non trouvée")
    
    await db.delete(alert)
    await db.commit()
    
    logger.info(f"🗑️ Alert deleted: {alert_id}")
    return None


@router.get("/history")
async def get_all_history(
    limit: int = 50,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Récupère tous les créneaux détectés pour l'utilisateur"""
    alerts_result = await db.execute(
        select(UserAlert.id).where(UserAlert.user_id == current_user.id)
    )
    alert_ids = [row[0] for row in alerts_result.fetchall()]
    
    if not alert_ids:
        return []
    
    result = await db.execute(
        select(DetectedSlot)
        .where(DetectedSlot.alert_id.in_(alert_ids))
        .order_by(desc(DetectedSlot.detected_at))
        .limit(limit)
    )
    slots = result.scalars().all()
    
    return [
        {
            "id": str(slot.id),
            "alert_id": str(slot.alert_id),
            "club_id": str(slot.club_id),
            "playground_id": str(slot.playground_id),
            "playground_name": slot.playground_name,
            "date": slot.date.isoformat(),
            "start_time": slot.start_time.strftime("%H:%M"),
            "duration_minutes": slot.duration_minutes,
            "price_total": float(slot.price_total) if slot.price_total else None,
            "indoor": slot.indoor,
            "email_sent": slot.email_sent,
            "detected_at": slot.detected_at.isoformat() if slot.detected_at else None,
        }
        for slot in slots
    ]