"""
KRENOO - SQLAlchemy Models (Version gratuite)
"""
from sqlalchemy import (
    Column, String, Boolean, Integer, DateTime, Date, Time,
    ForeignKey, Numeric, Text
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid
from app.core.database import Base


class Club(Base):
    __tablename__ = "clubs"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    doinsport_id = Column(UUID(as_uuid=True), nullable=False, unique=True)
    name = Column(String(255), nullable=False)
    city = Column(String(100))
    address = Column(Text)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relations
    alerts = relationship("UserAlert", back_populates="club")


class UserAlert(Base):
    __tablename__ = "user_alerts"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False)
    club_id = Column(UUID(as_uuid=True), ForeignKey("clubs.id"), nullable=False)
    
    # Préférences
    target_date = Column(Date, nullable=False)
    time_from = Column(Time, nullable=False)
    time_to = Column(Time, nullable=False)
    indoor_only = Column(Boolean)  # True=intérieur, False=extérieur, None=tous
    
    # État
    is_active = Column(Boolean, default=True)
    check_interval_minutes = Column(Integer, nullable=False, default=3)
    baseline_scraped = Column(Boolean, default=False)
    last_checked_at = Column(DateTime(timezone=True))
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Relations
    club = relationship("Club", back_populates="alerts")
    detected_slots = relationship("DetectedSlot", back_populates="alert", cascade="all, delete-orphan")


class DetectedSlot(Base):
    __tablename__ = "detected_slots"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    alert_id = Column(UUID(as_uuid=True), ForeignKey("user_alerts.id", ondelete="CASCADE"), nullable=False)
    club_id = Column(UUID(as_uuid=True), ForeignKey("clubs.id"), nullable=False)
    
    # Données du slot
    playground_id = Column(UUID(as_uuid=True), nullable=False)
    playground_name = Column(String(100), nullable=False)
    date = Column(Date, nullable=False)
    start_time = Column(Time, nullable=False)
    duration_minutes = Column(Integer)
    price_total = Column(Numeric(6, 2))
    indoor = Column(Boolean)
    
    # Notification
    email_sent = Column(Boolean, default=False)
    email_sent_at = Column(DateTime(timezone=True))
    push_sent = Column(Boolean, default=False)
    push_sent_at = Column(DateTime(timezone=True))
    
    detected_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Relations
    alert = relationship("UserAlert", back_populates="detected_slots")


class PushToken(Base):
    __tablename__ = "push_tokens"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False)
    token = Column(String(255), unique=True, nullable=False)
    device_type = Column(String(20))  # 'ios' ou 'android'
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
class UserPreference(Base):
    __tablename__ = "user_preferences"
    
    user_id = Column(UUID(as_uuid=True), ForeignKey("auth.users.id", ondelete="CASCADE"), primary_key=True)
    preferred_region_slug = Column(String(100), ForeignKey("regions.slug"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
