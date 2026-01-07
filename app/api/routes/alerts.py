"""
Routes API pour la gestion des alertes
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.core.database import get_db
from app.core.auth import get_current_user
from app.models.models import UserAlert, Club, Subscription, DetectedSlot
from app.schemas.schemas import AlertCreate, AlertResponse, AlertUpdate, DetectedSlotResponse
from typing import List
from uuid import UUID
from datetime import date, timedelta
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/alerts", tags=["alerts"])

# Quotas par plan MIS À JOUR
PLAN_QUOTAS = {
    "free": {
        "max_alerts": 1, 
        "max_clubs": 1, 
        "check_interval": 15,
        "max_time_window_hours": 4,  # NOUVEAU: 4h max
        "min_days_ahead": 1,  # NOUVEAU: À partir de demain
        "max_days_ahead": 7   # NOUVEAU: Jusqu'à J+7
    },
    "premium": {
        "max_alerts": 999, 
        "max_clubs": 3, 
        "check_interval": 1,
        "max_time_window_hours": 24,  # NOUVEAU: 24h max
        "min_days_ahead": 0,  # NOUVEAU: À partir d'aujourd'hui
        "max_days_ahead": 90  # NOUVEAU: Jusqu'à J+90 (3 mois)
    }
}

@router.post("", response_model=AlertResponse, status_code=status.HTTP_201_CREATED)
async def create_alert(
    alert_data: AlertCreate,
    current_user = Depends(get_current_user),
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
    
    # 2. Vérifier plage de dates (J+1 à J+7 pour Free, J à J+90 pour Premium)
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
    
    # 3. Vérifier plage horaire (4h pour Free, 24h pour Premium)
    time_from_minutes = alert_data.time_from.hour * 60 + alert_data.time_from.minute
    time_to_minutes = alert_data.time_to.hour * 60 + alert_data.time_to.minute
    time_window_hours = (time_to_minutes - time_from_minutes) / 60
    
    if time_window_hours > quota["max_time_window_hours"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Plan {plan}: La plage horaire est limitée à {quota['max_time_window_hours']}h (actuellement {time_window_hours:.1f}h)"
        )
    
    # 4. Vérifier que le club existe
    result = await db.execute(
        select(Club).where(Club.id == alert_data.club_id)
    )
    club = result.scalar_one_or_none()
    if not club:
        raise HTTPException(status_code=404, detail="Club non trouvé")
    
    # === CRÉATION ALERTE ===
    
    # Calculer day_of_week depuis target_date
    day_of_week = alert_data.target_date.isoweekday()  # 1=Lundi, 7=Dimanche
    
    new_alert = UserAlert(
        user_id=current_user.id,
        club_id=alert_data.club_id,
        target_date=alert_data.target_date,
        time_from=alert_data.time_from,
        time_to=alert_data.time_to,
        indoor_only=alert_data.indoor_only,
        check_interval_minutes=quota["check_interval"],
        baseline_scraped=False  # Sera fait au premier scan
    )
    
    db.add(new_alert)
    await db.commit()
    await db.refresh(new_alert)
    
    logger.info(f"✅ Alert created: {new_alert.id} by user {current_user.id} - Date: {alert_data.target_date} - Plan: {plan}")
    return new_alert

@router.get("", response_model=List[AlertResponse])
async def list_alerts(
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Liste toutes les alertes de l'utilisateur"""
    
    result = await db.execute(
        select(UserAlert).where(UserAlert.user_id == current_user.id)
    )
    alerts = result.scalars().all()
    return alerts

@router.get("/{alert_id}/history", response_model=List[DetectedSlotResponse])
async def get_alert_history(
    alert_id: UUID,
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Historique des créneaux détectés pour une alerte"""
    
    # Vérifier ownership
    result = await db.execute(
        select(UserAlert).where(
            UserAlert.id == alert_id,
            UserAlert.user_id == current_user.id
        )
    )
    alert = result.scalar_one_or_none()
    if not alert:
        raise HTTPException(status_code=404, detail="Alerte non trouvée")
    
    # Récupérer l'historique
    result = await db.execute(
        select(DetectedSlot)
        .where(DetectedSlot.alert_id == alert_id)
        .order_by(DetectedSlot.detected_at.desc())
        .limit(100)
    )
    slots = result.scalars().all()
    return slots

@router.patch("/{alert_id}", response_model=AlertResponse)
async def update_alert(
    alert_id: UUID,
    alert_update: AlertUpdate,
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Modifier une alerte (pause/resume, etc.)"""
    
    result = await db.execute(
        select(UserAlert).where(
            UserAlert.id == alert_id,
            UserAlert.user_id == current_user.id
        )
    )
    alert = result.scalar_one_or_none()
    if not alert:
        raise HTTPException(status_code=404, detail="Alerte non trouvée")
    
    # Update fields
    update_data = alert_update.dict(exclude_unset=True)
    
    # Si target_date change, reset baseline
    if 'target_date' in update_data:
        update_data['baseline_scraped'] = False
    
    for field, value in update_data.items():
        setattr(alert, field, value)
    
    await db.commit()
    await db.refresh(alert)
    
    logger.info(f"✅ Alert updated: {alert_id}")
    return alert

@router.delete("/{alert_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_alert(
    alert_id: UUID,
    current_user = Depends(get_current_user),
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

@router.get("/history/all", response_model=List[DetectedSlotResponse])
async def get_all_history(
    limit: int = 50,
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Récupère tous les créneaux détectés pour l'utilisateur
    (toutes alertes confondues)
    """
    
    # Récupérer les IDs des alertes de l'utilisateur
    result = await db.execute(
        select(UserAlert.id).where(UserAlert.user_id == current_user.id)
    )
    alert_ids = [row[0] for row in result.fetchall()]
    
    if not alert_ids:
        return []
    
    # Récupérer tous les slots détectés
    result = await db.execute(
        select(DetectedSlot)
        .where(DetectedSlot.alert_id.in_(alert_ids))
        .order_by(DetectedSlot.detected_at.desc())
        .limit(limit)
    )
    slots = result.scalars().all()
    
    return slots