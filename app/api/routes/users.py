from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from uuid import UUID

from app.core.database import get_db
from app.core.auth import get_current_user
from app.core.config import APP_QUOTAS
from app.models.models import PushToken, UserPreference, Region
from app.schemas.schemas import (
    PushTokenCreate,
    PushTokenResponse,
    UserPreferenceUpdate,
    UserPreferenceResponse,
)

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me")
async def get_current_user_info(current_user=Depends(get_current_user)):
    """Retourne les infos de l'utilisateur connecté."""
    return {
        "id": current_user.id,
        "phone": current_user.phone,
        "created_at": current_user.created_at,
    }


@router.get("/quotas")
async def get_user_quotas(current_user=Depends(get_current_user)):
    """Retourne les quotas de l'application (identiques pour tous)."""
    return APP_QUOTAS


# ============================================
# PRÉFÉRENCES UTILISATEUR
# ============================================

@router.get("/preferences", response_model=UserPreferenceResponse | None)
async def get_preferences(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Récupère les préférences de l'utilisateur."""
    user_id = UUID(current_user.id)
    
    result = await db.execute(
        select(UserPreference).where(UserPreference.user_id == user_id)
    )
    pref = result.scalar_one_or_none()
    
    return pref


@router.put("/preferences", response_model=UserPreferenceResponse)
async def update_preferences(
    data: UserPreferenceUpdate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Crée ou met à jour les préférences utilisateur."""
    user_id = UUID(current_user.id)
    
    # Vérifier que la région existe
    if data.preferred_region_slug:
        region_result = await db.execute(
            select(Region).where(Region.slug == data.preferred_region_slug)
        )
        if not region_result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Région '{data.preferred_region_slug}' non trouvée",
            )
    
    # Chercher préférence existante
    result = await db.execute(
        select(UserPreference).where(UserPreference.user_id == user_id)
    )
    pref = result.scalar_one_or_none()
    
    if pref:
        # Update
        pref.preferred_region_slug = data.preferred_region_slug
    else:
        # Create
        pref = UserPreference(
            user_id=user_id,
            preferred_region_slug=data.preferred_region_slug,
        )
        db.add(pref)
    
    await db.commit()
    await db.refresh(pref)
    
    return pref


# ============================================
# PUSH TOKENS
# ============================================

@router.post("/register-push-token", response_model=PushTokenResponse)
async def register_push_token(
    data: PushTokenCreate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Enregistre ou met à jour un token push Expo."""
    user_id = UUID(current_user.id)
    
    # Chercher token existant
    result = await db.execute(
        select(PushToken).where(PushToken.token == data.token)
    )
    existing = result.scalar_one_or_none()
    
    if existing:
        existing.user_id = user_id
        existing.device_type = data.device_type
        existing.is_active = True
        token = existing
    else:
        token = PushToken(
            user_id=user_id,
            token=data.token,
            device_type=data.device_type,
            is_active=True,
        )
        db.add(token)
    
    await db.commit()
    await db.refresh(token)
    
    return token