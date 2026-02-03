"""
KRENOO - Pydantic Schemas (Version gratuite)
"""
from pydantic import BaseModel
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
# ALERTS
# ============================================

class AlertCreate(BaseModel):
    club_id: UUID
    target_date: date
    time_from: time
    time_to: time
    indoor_only: Optional[bool] = None  # None = tous, True = indoor, False = outdoor


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
    club_name: Optional[str] = None
    target_date: date
    time_from: time
    time_to: time
    indoor_only: Optional[bool]
    is_active: bool
    check_interval_minutes: int
    last_checked_at: Optional[datetime]
    created_at: datetime
    
    class Config:
        from_attributes = True


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
# QUOTAS
# ============================================

class QuotasResponse(BaseModel):
    """Quotas de l'application"""
    max_alerts: int
    check_interval_minutes: int
    min_days_ahead: int
    max_days_ahead: int
    max_time_window_hours: int


# ============================================
# USER
# ============================================

class UserResponse(BaseModel):
    id: UUID
    email: str
    created_at: Optional[datetime] = None

class UserPreferenceBase(BaseModel):
    preferred_region_slug: str | None = None

class UserPreferenceCreate(UserPreferenceBase):
    pass

class UserPreferenceUpdate(UserPreferenceBase):
    pass

class UserPreferenceResponse(UserPreferenceBase):
    user_id: UUID
    created_at: datetime
    updated_at: datetime

class Config:
    from_attributes = True


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
        



# ============================================
# REGIONS
# ============================================   
class RegionResponse(BaseModel):
    slug: str
    name: str
    display_name: str
    cities: List[str]
    # Nouveaux champs
    parent_region_slug: Optional[str] = None
    parent_region_name: Optional[str] = None
    is_flagship: bool = False
    
    class Config:
        from_attributes = True