"""
Routes API pour les utilisateurs (Version gratuite)
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.core.database import get_db
from app.core.auth import get_current_user
from app.core.config import APP_QUOTAS
from app.models.models import PushToken
from pydantic import BaseModel
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/users", tags=["users"])


class QuotasResponse(BaseModel):
    max_alerts: int
    check_interval_minutes: int
    min_days_ahead: int
    max_days_ahead: int
    max_time_window_hours: int


class UserInfoResponse(BaseModel):
    id: str
    email: str
    quotas: QuotasResponse


@router.get("/quotas", response_model=QuotasResponse)
async def get_user_quotas(current_user=Depends(get_current_user)):
    """Retourne les quotas de l'application"""
    return QuotasResponse(**APP_QUOTAS)


@router.get("/me", response_model=UserInfoResponse)
async def get_current_user_info(current_user=Depends(get_current_user)):
    """Retourne les infos de l'utilisateur connecté"""
    return UserInfoResponse(
        id=str(current_user.id),
        email=current_user.email,
        quotas=QuotasResponse(**APP_QUOTAS)
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
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Enregistre ou met à jour le push token de l'utilisateur"""
    
    try:
        result = await db.execute(
            select(PushToken).where(PushToken.token == data.token)
        )
        existing_token = result.scalar_one_or_none()
        
        if existing_token:
            if existing_token.user_id != current_user.id:
                existing_token.user_id = current_user.id
            existing_token.device_type = data.device_type
            existing_token.is_active = True
            logger.info(f"📱 Push token mis à jour pour user {current_user.id}")
        else:
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