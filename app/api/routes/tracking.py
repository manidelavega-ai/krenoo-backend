"""
Routes de tracking et statistiques
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, cast, Date, text, case
from datetime import date, timedelta, datetime
from typing import Optional
from uuid import UUID
from pydantic import BaseModel
import logging

from app.core.database import get_db
from app.core.auth import get_current_user
from app.core.config import settings
from app.models.models import TrackingEvent, UserAlert, DetectedSlot, Club

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/tracking", tags=["tracking"])


# --- Schemas ---

class TrackEventRequest(BaseModel):
    event_type: str           # booking_click, share_click
    source: str               # alert, search, push_notification
    club_id: Optional[str] = None
    alert_id: Optional[str] = None
    metadata: Optional[dict] = None


class StatsResponse(BaseModel):
    period_days: int
    booking_clicks: int
    booking_clicks_from_alerts: int
    booking_clicks_from_search: int
    booking_clicks_from_push: int
    share_clicks: int
    total_alerts_created: int
    total_slots_detected: int
    conversion_rate: float          # alerts avec booking_click / alerts avec detected_slots
    active_users_7d: int
    top_clubs: list
    hourly_distribution: list
    daily_clicks: list


# --- Routes ---

@router.post("", status_code=201)
async def track_event(
    payload: TrackEventRequest,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Enregistre un événement de tracking (fire-and-forget côté client)"""
    import json

    event = TrackingEvent(
        user_id=current_user.id,
        event_type=payload.event_type,
        source=payload.source,
        club_id=UUID(payload.club_id) if payload.club_id else None,
        alert_id=UUID(payload.alert_id) if payload.alert_id else None,
        metadata_=json.dumps(payload.metadata) if payload.metadata else None,
    )
    db.add(event)
    await db.commit()
    return {"status": "ok"}


@router.get("/stats")
async def get_stats(
    days: int = Query(default=30, ge=1, le=365),
    admin_key: str = Query(...),
    db: AsyncSession = Depends(get_db)
):
    """
    Stats agrégées (protégé par admin_key).
    Usage: GET /api/tracking/stats?admin_key=YOUR_SECRET_KEY&days=30
    """
    if admin_key != settings.SECRET_KEY:
        raise HTTPException(status_code=403, detail="Forbidden")

    since = datetime.utcnow() - timedelta(days=days)

    # --- Booking clicks ---
    bc = await db.execute(
        select(
            func.count(TrackingEvent.id).label("total"),
            func.count(case((TrackingEvent.source == "alert", 1))).label("from_alert"),
            func.count(case((TrackingEvent.source == "search", 1))).label("from_search"),
            func.count(case((TrackingEvent.source == "push_notification", 1))).label("from_push"),
        )
        .where(
            TrackingEvent.event_type == "booking_click",
            TrackingEvent.created_at >= since
        )
    )
    bc_row = bc.one()

    # --- Share clicks ---
    sc = await db.execute(
        select(func.count(TrackingEvent.id))
        .where(TrackingEvent.event_type == "share_click", TrackingEvent.created_at >= since)
    )
    share_clicks = sc.scalar() or 0

    # --- Total alerts created (période) ---
    ac = await db.execute(
        select(func.count(UserAlert.id)).where(UserAlert.created_at >= since)
    )
    total_alerts = ac.scalar() or 0

    # --- Total detected slots (période) ---
    ds = await db.execute(
        select(func.count(DetectedSlot.id)).where(DetectedSlot.detected_at >= since)
    )
    total_detected = ds.scalar() or 0

    # --- Conversion: alerts ayant un booking_click / alerts ayant des detected_slots ---
    alerts_with_detections = await db.execute(
        select(func.count(func.distinct(DetectedSlot.alert_id)))
        .where(DetectedSlot.detected_at >= since)
    )
    alerts_detected_count = alerts_with_detections.scalar() or 0

    alerts_with_booking = await db.execute(
        select(func.count(func.distinct(TrackingEvent.alert_id)))
        .where(
            TrackingEvent.event_type == "booking_click",
            TrackingEvent.alert_id.isnot(None),
            TrackingEvent.created_at >= since
        )
    )
    alerts_booked_count = alerts_with_booking.scalar() or 0
    conversion = (alerts_booked_count / alerts_detected_count * 100) if alerts_detected_count > 0 else 0

    # --- Active users (7 derniers jours) ---
    seven_days_ago = datetime.utcnow() - timedelta(days=7)
    au = await db.execute(
        select(func.count(func.distinct(UserAlert.user_id)))
        .where(UserAlert.is_active == True, UserAlert.created_at >= seven_days_ago)
    )
    active_users = au.scalar() or 0

    # --- Top clubs par booking clicks ---
    tc = await db.execute(
        select(
            Club.name,
            Club.city,
            func.count(TrackingEvent.id).label("clicks")
        )
        .join(Club, TrackingEvent.club_id == Club.id)
        .where(
            TrackingEvent.event_type == "booking_click",
            TrackingEvent.created_at >= since
        )
        .group_by(Club.name, Club.city)
        .order_by(func.count(TrackingEvent.id).desc())
        .limit(10)
    )
    top_clubs = [{"club": r.name, "city": r.city, "clicks": r.clicks} for r in tc.all()]

    # --- Distribution horaire des alertes (time_from) ---
    hd = await db.execute(
        select(
            func.extract("hour", UserAlert.time_from).label("hour"),
            func.count(UserAlert.id).label("count")
        )
        .where(UserAlert.created_at >= since)
        .group_by(text("hour"))
        .order_by(text("hour"))
    )
    hourly = [{"hour": int(r.hour), "count": r.count} for r in hd.all()]

    # --- Booking clicks par jour (pour graphique) ---
    dc = await db.execute(
        select(
            cast(TrackingEvent.created_at, Date).label("day"),
            func.count(TrackingEvent.id).label("clicks")
        )
        .where(
            TrackingEvent.event_type == "booking_click",
            TrackingEvent.created_at >= since
        )
        .group_by(text("day"))
        .order_by(text("day"))
    )
    daily = [{"date": r.day.isoformat(), "clicks": r.clicks} for r in dc.all()]

    return StatsResponse(
        period_days=days,
        booking_clicks=bc_row.total,
        booking_clicks_from_alerts=bc_row.from_alert,
        booking_clicks_from_search=bc_row.from_search,
        booking_clicks_from_push=bc_row.from_push,
        share_clicks=share_clicks,
        total_alerts_created=total_alerts,
        total_slots_detected=total_detected,
        conversion_rate=round(conversion, 1),
        active_users_7d=active_users,
        top_clubs=top_clubs,
        hourly_distribution=hourly,
        daily_clicks=daily,
    )