"""
KRENOO - Pydantic Schemas
"""
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime, date, time
from uuid import UUID


# ============================================
# CLUBS
# ============================================

class ClubBase(BaseModel):
    name: str
    city: Optional[str] = None
    address: Optional[str] = None

class ClubCreate(ClubBase):
    doinsport_id: UUID

class ClubResponse(ClubBase):
    id: UUID
    doinsport_id: UUID
    enabled: bool
    
    class Config:
        from_attributes = True


# ============================================
# BOOSTS
# ============================================

class UserBoostResponse(BaseModel):
    """Compteur de boosts d'un utilisateur"""
    user_id: UUID
    boost_count: int
    updated_at: datetime
    
    class Config:
        from_attributes = True


class BoostPurchaseResponse(BaseModel):
    """Historique d'un achat de boost"""
    id: UUID
    product_type: str  # 'boost_single' | 'boost_pack'
    boost_count: int
    amount_cents: int
    created_at: datetime
    
    class Config:
        from_attributes = True


# ============================================
# ALERTS
# ============================================

class AlertCreate(BaseModel):
    club_id: UUID
    target_date: date
    time_from: time
    time_to: time
    indoor_only: Optional[bool] = None  # None = tous, True = indoor, False = outdoor
    use_boost: bool = False  # Activer un boost sur cette alerte

class AlertUpdate(BaseModel):
    target_date: Optional[date] = None
    time_from: Optional[time] = None
    time_to: Optional[time] = None
    indoor_only: Optional[bool] = None
    is_active: Optional[bool] = None

class AlertResponse(BaseModel):
    id: UUID
    user_id: UUID
    club_id: UUID
    club_name: Optional[str] = None  # Ajouté via jointure
    target_date: date
    time_from: time
    time_to: time
    indoor_only: Optional[bool]
    is_active: bool
    check_interval_minutes: int
    last_checked_at: Optional[datetime]
    # Boost
    boost_active: bool = False
    boost_expires_at: Optional[datetime] = None
    # Timestamps
    created_at: datetime
    
    class Config:
        from_attributes = True


class AlertActivateBoost(BaseModel):
    """Pour activer un boost sur une alerte existante"""
    alert_id: UUID


# ============================================
# DETECTED SLOTS
# ============================================

class DetectedSlotResponse(BaseModel):
    id: UUID
    playground_name: str
    date: date
    start_time: time
    duration_minutes: Optional[int]
    price_total: Optional[float]
    indoor: Optional[bool]
    detected_at: datetime
    
    class Config:
        from_attributes = True


class DetectedSlotsGrouped(BaseModel):
    """Slots groupés par date pour l'historique"""
    date: date
    slots: list[DetectedSlotResponse]


# ============================================
# SUBSCRIPTION & QUOTAS
# ============================================

class SubscriptionResponse(BaseModel):
    plan: str  # 'free' | 'premium'
    is_premium: bool
    status: Optional[str] = "active"
    current_period_end: Optional[datetime] = None
    cancel_at_period_end: bool = False
    # Boosts
    boost_count: int = 0
    
    class Config:
        from_attributes = True


class QuotasResponse(BaseModel):
    """Quotas actuels de l'utilisateur"""
    plan: str
    max_alerts: int
    current_alerts: int
    check_interval_minutes: int
    min_days_ahead: int
    max_days_ahead: int
    max_time_window_hours: int
    available_intervals: list[int]
    boost_count: int = 0


# ============================================
# CHECKOUT
# ============================================

class CheckoutRequest(BaseModel):
    product_type: str = Field(
        ..., 
        pattern="^(premium|boost_single|boost_pack)$",
        description="Type de produit: premium, boost_single, boost_pack"
    )
    # Optionnel: pour redirect après paiement
    success_url: Optional[str] = None
    cancel_url: Optional[str] = None


class CheckoutResponse(BaseModel):
    url: str  # URL Stripe Checkout


# ============================================
# USER
# ============================================

class UserResponse(BaseModel):
    id: UUID
    email: str
    created_at: Optional[datetime] = None


class UserDashboard(BaseModel):
    """Vue dashboard complète"""
    user: UserResponse
    subscription: SubscriptionResponse
    quotas: QuotasResponse
    active_alerts: int
    boosted_alerts: int
    total_slots_detected: int
    slots_last_week: int


# ============================================
# PUSH TOKENS
# ============================================

class PushTokenCreate(BaseModel):
    token: str
    device_type: Optional[str] = None  # 'ios' | 'android'


class PushTokenResponse(BaseModel):
    id: UUID
    token: str
    device_type: Optional[str]
    is_active: bool
    
    class Config:
        from_attributes = True