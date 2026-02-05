"""
Routes API pour la gestion des alertes (Version gratuite)
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func
from sqlalchemy.orm import selectinload
from datetime import date, timedelta
from typing import List
from uuid import UUID
import logging

from app.core.database import get_db
from app.core.auth import get_current_user
from app.core.config import APP_QUOTAS
from app.models.models import UserAlert, Club, DetectedSlot
from app.schemas.schemas import AlertCreate, AlertResponse, AlertUpdate, DetectedSlotResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.post("", response_model=AlertResponse, status_code=status.HTTP_201_CREATED)
async def create_alert(
    alert_data: AlertCreate,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Créer une nouvelle alerte"""
    
    # === VALIDATION QUOTAS ===
    
    # 1. Vérifier quota alertes actives
    result = await db.execute(
        select(UserAlert).where(
            UserAlert.user_id == current_user.id,
            UserAlert.is_active == True
        )
    )
    active_alerts = len(result.scalars().all())
    
    if active_alerts >= APP_QUOTAS["max_alerts"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Quota atteint: maximum {APP_QUOTAS['max_alerts']} alertes actives"
        )
    
    # 2. Vérifier plage de dates
    today = date.today()
    min_date = today + timedelta(days=APP_QUOTAS["min_days_ahead"])
    max_date = today + timedelta(days=APP_QUOTAS["max_days_ahead"])
    
    if alert_data.target_date < min_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"La date doit être au minimum {min_date.strftime('%d/%m/%Y')}"
        )
    
    if alert_data.target_date > max_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"La date ne peut pas dépasser {max_date.strftime('%d/%m/%Y')}"
        )
    
    # 3. Vérifier plage horaire
    time_from_minutes = alert_data.time_from.hour * 60 + alert_data.time_from.minute
    time_to_minutes = alert_data.time_to.hour * 60 + alert_data.time_to.minute
    time_window_hours = (time_to_minutes - time_from_minutes) / 60
    
    if time_window_hours > APP_QUOTAS["max_time_window_hours"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"La plage horaire est limitée à {APP_QUOTAS['max_time_window_hours']}h"
        )
    
    # 4. Vérifier que le club existe
    result = await db.execute(
        select(Club).where(Club.id == alert_data.club_id)
    )
    club = result.scalar_one_or_none()
    if not club:
        raise HTTPException(status_code=404, detail="Club non trouvé")
    
    # === CRÉATION ALERTE ===
    
    new_alert = UserAlert(
        user_id=current_user.id,
        club_id=alert_data.club_id,
        target_date=alert_data.target_date,
        time_from=alert_data.time_from,
        time_to=alert_data.time_to,
        indoor_only=alert_data.indoor_only,
        check_interval_minutes=APP_QUOTAS["check_interval_minutes"],
        baseline_scraped=False
    )
    
    db.add(new_alert)
    await db.commit()
    await db.refresh(new_alert)
    
    logger.info(f"✅ Alert created: {new_alert.id} by user {current_user.id} - Date: {alert_data.target_date}")
    
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
        created_at=new_alert.created_at,
        detected_count=0,
    )


@router.get("", response_model=List[AlertResponse])
async def list_alerts(
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Liste toutes les alertes de l'utilisateur avec le nombre de créneaux détectés"""
    from sqlalchemy import func
    
    # Sous-requête pour compter les detected_slots par alerte
    count_subq = (
        select(
            DetectedSlot.alert_id,
            func.count(DetectedSlot.id).label('detected_count')
        )
        .group_by(DetectedSlot.alert_id)
        .subquery()
    )
    
    # Requête principale avec LEFT JOIN
    result = await db.execute(
        select(UserAlert, func.coalesce(count_subq.c.detected_count, 0).label('detected_count'))
        .options(selectinload(UserAlert.club))
        .outerjoin(count_subq, UserAlert.id == count_subq.c.alert_id)
        .where(UserAlert.user_id == current_user.id)
    )
    rows = result.all()
    
    return [
        AlertResponse(
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
            created_at=alert.created_at,
            detected_count=detected_count,
        )
        for alert, detected_count in rows
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
    
    # Compter detected_slots pour cette alerte
    count_result = await db.execute(
        select(func.count(DetectedSlot.id)).where(DetectedSlot.alert_id == alert_id)
    )
    detected_count = count_result.scalar() or 0

return AlertResponse(
    # ... existants ...
    detected_count=detected_count,
)
    
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