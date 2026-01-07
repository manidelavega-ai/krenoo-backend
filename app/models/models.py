from sqlalchemy import Column, String, Boolean, Integer, DateTime, Date, Time, Numeric, ARRAY, ForeignKey, CheckConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from app.core.database import Base
import uuid

class Subscription(Base):
    __tablename__ = "subscriptions"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False, unique=True)
    stripe_customer_id = Column(String(255), unique=True)
    stripe_subscription_id = Column(String(255), unique=True)
    plan = Column(String(20), nullable=False, default="free")
    status = Column(String(20), default="active")
    current_period_end = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class Club(Base):
    __tablename__ = "clubs"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    doinsport_id = Column(UUID(as_uuid=True), nullable=False, unique=True)
    name = Column(String(255), nullable=False)
    city = Column(String(100))
    address = Column(String)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class UserAlert(Base):
    __tablename__ = "user_alerts"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False)
    club_id = Column(UUID(as_uuid=True), nullable=False)
    
    # Préférences
    target_date = Column(Date, nullable=False)  # NOUVEAU: Date cible de l'alerte
    time_from = Column(Time, nullable=False)
    time_to = Column(Time, nullable=False)
    indoor_only = Column(Boolean, nullable=True)
    
    # Metadata
    is_active = Column(Boolean, default=True)
    check_interval_minutes = Column(Integer, default=15)
    baseline_scraped = Column(Boolean, default=False)  # NOUVEAU: Flag baseline
    last_checked_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class DetectedSlot(Base):
    __tablename__ = "detected_slots"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    alert_id = Column(UUID(as_uuid=True), nullable=False)
    club_id = Column(UUID(as_uuid=True), nullable=False)
    
    # Données slot
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
    
    detected_at = Column(DateTime(timezone=True), server_default=func.now())

class PushToken(Base):
    __tablename__ = "push_tokens"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    token = Column(String(255), nullable=False, unique=True)
    device_type = Column(String(20))  # 'ios' ou 'android'
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())