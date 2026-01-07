"""
Routes API pour les utilisateurs
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.core.database import get_db
from app.core.auth import get_current_user
from app.models.models import Subscription
from pydantic import BaseModel
from typing import Optional

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