"""
Routes API pour les utilisateurs
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from app.core.database import get_db
from app.core.auth import get_current_user
from app.models.models import Subscription, PushToken
from pydantic import BaseModel
from typing import Optional
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/users", tags=["users"])

# Quotas par plan (même que dans alerts.py)
PLAN_QUOTAS = {
    "free": {
        "max_alerts": 1, 
        "max_clubs": 1, 
        "check_interval": 15,
        "available_intervals": [15],  # Options disponibles dans le UI
        "max_time_window_hours": 4,
        "min_days_ahead": 1,
        "max_days_ahead": 7
    },
    "premium": {
        "max_alerts": 999, 
        "max_clubs": 3, 
        "check_interval": 1,
        "available_intervals": [1, 2, 5, 10, 15],  # Toutes les options
        "max_time_window_hours": 24,
        "min_days_ahead": 0,
        "max_days_ahead": 90
    }
}

class UserQuotasResponse(BaseModel):
    plan: str
    max_alerts: int
    max_clubs: int
    check_interval: int
    available_intervals: list[int]
    max_time_window_hours: int
    min_days_ahead: int
    max_days_ahead: int

class UserInfoResponse(BaseModel):
    id: str
    email: str
    plan: str
    quotas: UserQuotasResponse

@router.get("/quotas", response_model=UserQuotasResponse)
async def get_user_quotas(
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Retourne les quotas de l'utilisateur selon son plan"""
    
    result = await db.execute(
        select(Subscription).where(Subscription.user_id == current_user.id)
    )
    subscription = result.scalar_one_or_none()
    plan = subscription.plan if subscription else "free"
    
    quotas = PLAN_QUOTAS[plan]
    
    return UserQuotasResponse(
        plan=plan,
        **quotas
    )

@router.get("/me", response_model=UserInfoResponse)
async def get_current_user_info(
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Retourne les infos de l'utilisateur connecté"""
    
    result = await db.execute(
        select(Subscription).where(Subscription.user_id == current_user.id)
    )
    subscription = result.scalar_one_or_none()
    plan = subscription.plan if subscription else "free"
    
    quotas = PLAN_QUOTAS[plan]
    
    return UserInfoResponse(
        id=str(current_user.id),
        email=current_user.email,
        plan=plan,
        quotas=UserQuotasResponse(plan=plan, **quotas)
    )


# === PUSH NOTIFICATIONS ===

class PushTokenRequest(BaseModel):
    token: str
    device_type: str  # 'ios' ou 'android'

class PushTokenResponse(BaseModel):
    success: bool
    message: str

@router.post("/register-push-token", response_model=PushTokenResponse)
async def register_push_token(
    data: PushTokenRequest,
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Enregistre ou met à jour le push token de l'utilisateur"""
    
    try:
        # Vérifier si le token existe déjà
        result = await db.execute(
            select(PushToken).where(PushToken.token == data.token)
        )
        existing_token = result.scalar_one_or_none()
        
        if existing_token:
            # Token existe - mettre à jour le user_id si différent
            if existing_token.user_id != current_user.id:
                existing_token.user_id = current_user.id
            existing_token.device_type = data.device_type
            existing_token.is_active = True
            logger.info(f"📱 Push token mis à jour pour user {current_user.id}")
        else:
            # Nouveau token
            new_token = PushToken(
                user_id=current_user.id,
                token=data.token,
                device_type=data.device_type,
                is_active=True
            )
            db.add(new_token)
            logger.info(f"📱 Nouveau push token enregistré pour user {current_user.id}")
        
        await db.commit()
        
        return PushTokenResponse(success=True, message="Token enregistré")
        
    except Exception as e:
        logger.error(f"❌ Erreur enregistrement push token: {e}")
        raise HTTPException(status_code=500, detail="Erreur enregistrement token")