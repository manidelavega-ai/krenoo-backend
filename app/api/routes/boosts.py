"""
KRENOO - Routes Boosts
Fichier: app/api/routes/boosts.py
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload
from datetime import datetime, timedelta
from uuid import UUID

from app.core.database import get_db
from app.core.auth import get_current_user
from app.core.config import BOOST_CONFIG
from app.models.models import UserBoost, UserAlert, BoostPurchase
from app.schemas.schemas import (
    UserBoostResponse,
    BoostPurchaseResponse,
    AlertActivateBoost,
    AlertResponse,
)

router = APIRouter(prefix="/boosts", tags=["boosts"])


# ============================================
# GET /boosts - Compteur de boosts de l'utilisateur
# ============================================
@router.get("", response_model=UserBoostResponse)
async def get_user_boosts(
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Récupère le nombre de boosts disponibles"""
    result = await db.execute(
        select(UserBoost).where(UserBoost.user_id == current_user.id)
    )
    user_boost = result.scalar_one_or_none()
    
    if not user_boost:
        # Créer une entrée avec 0 boosts
        user_boost = UserBoost(user_id=current_user.id, boost_count=0)
        db.add(user_boost)
        await db.commit()
        await db.refresh(user_boost)
    
    return user_boost


# ============================================
# GET /boosts/history - Historique des achats
# ============================================
@router.get("/history", response_model=list[BoostPurchaseResponse])
async def get_boost_history(
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: int = 20
):
    """Récupère l'historique des achats de boosts"""
    result = await db.execute(
        select(BoostPurchase)
        .where(BoostPurchase.user_id == current_user.id)
        .order_by(BoostPurchase.created_at.desc())
        .limit(limit)
    )
    return result.scalars().all()


# ============================================
# POST /boosts/activate - Activer un boost sur une alerte
# ============================================
@router.post("/activate", response_model=AlertResponse)
async def activate_boost_on_alert(
    payload: AlertActivateBoost,
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Consomme 1 boost et l'active sur une alerte existante.
    Le boost dure 24h avec check toutes les 30 secondes.
    """
    # 1. Vérifier que l'alerte appartient à l'utilisateur
    result = await db.execute(
        select(UserAlert)
        .options(selectinload(UserAlert.club))  # Eager load
        .where(UserAlert.id == payload.alert_id)
        .where(UserAlert.user_id == current_user.id)
)
    )
    alert = result.scalar_one_or_none()
    
    if not alert:
        raise HTTPException(404, "Alerte non trouvée")
    
    # 2. Vérifier si un boost est déjà actif
    if alert.boost_active and alert.boost_expires_at and alert.boost_expires_at > datetime.utcnow():
        raise HTTPException(400, "Un boost est déjà actif sur cette alerte")
    
    # 3. Vérifier que l'utilisateur a des boosts disponibles
    boost_result = await db.execute(
        select(UserBoost).where(UserBoost.user_id == current_user.id)
    )
    user_boost = boost_result.scalar_one_or_none()
    
    if not user_boost or user_boost.boost_count < 1:
        raise HTTPException(400, "Aucun boost disponible")
    
    # 4. Décrémenter le compteur de boosts
    user_boost.boost_count -= 1
    user_boost.updated_at = datetime.utcnow()
    
    # 5. Activer le boost sur l'alerte
    boost_duration = timedelta(hours=BOOST_CONFIG["duration_hours"])
    alert.boost_active = True
    alert.boost_expires_at = datetime.utcnow() + boost_duration
    alert.is_active = True  # Réactiver si elle était en pause
    alert.updated_at = datetime.utcnow()
    
    await db.commit()
    await db.refresh(alert)
    
    # Ajouter club_name pour la réponse
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


# ============================================
# POST /boosts/deactivate - Désactiver un boost (optionnel)
# ============================================
@router.post("/deactivate/{alert_id}")
async def deactivate_boost(
    alert_id: UUID,
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Désactive manuellement un boost sur une alerte.
    Note: Le boost consommé n'est PAS remboursé.
    """
    result = await db.execute(
        select(UserAlert)
        .options(selectinload(UserAlert.club))  # Eager load
        .where(UserAlert.id == payload.alert_id)
        .where(UserAlert.user_id == current_user.id)
    )
    alert = result.scalar_one_or_none()
    
    if not alert:
        raise HTTPException(404, "Alerte non trouvée")
    
    if not alert.boost_active:
        raise HTTPException(400, "Aucun boost actif sur cette alerte")
    
    alert.boost_active = False
    alert.boost_expires_at = None
    alert.updated_at = datetime.utcnow()
    
    await db.commit()
    
    return {"message": "Boost désactivé", "alert_id": str(alert_id)}


# ============================================
# Helpers (utilisés par d'autres modules)
# ============================================
async def add_boosts_to_user(
    db: AsyncSession, 
    user_id: UUID, 
    count: int
) -> int:
    """
    Ajoute des boosts à un utilisateur (upsert).
    Retourne le nouveau total.
    """
    result = await db.execute(
        select(UserBoost).where(UserBoost.user_id == user_id)
    )
    user_boost = result.scalar_one_or_none()
    
    if user_boost:
        user_boost.boost_count += count
        user_boost.updated_at = datetime.utcnow()
    else:
        user_boost = UserBoost(user_id=user_id, boost_count=count)
        db.add(user_boost)
    
    await db.flush()
    return user_boost.boost_count


async def get_boost_count(db: AsyncSession, user_id: UUID) -> int:
    """Récupère le nombre de boosts d'un utilisateur"""
    result = await db.execute(
        select(UserBoost.boost_count).where(UserBoost.user_id == user_id)
    )
    count = result.scalar_one_or_none()
    return count or 0