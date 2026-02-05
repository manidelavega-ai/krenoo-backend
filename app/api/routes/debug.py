"""
Routes de debug (à désactiver en production)
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from uuid import UUID
from datetime import datetime, timezone

from app.core.database import get_db
from app.core.auth import get_current_user
from app.models.models import UserAlert, Club, PushToken
from app.services.push_service import send_push_notification

router = APIRouter(prefix="/debug", tags=["debug"])


@router.post("/simulate-slot-notification")
async def simulate_slot_notification(
    alert_id: str = None,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Simule une notification de créneau disponible.
    Si alert_id fourni, utilise les infos de cette alerte.
    Sinon, envoie une notification de test générique.
    """
    user_id = UUID(current_user.id)
    
    # Récupérer les push tokens de l'utilisateur
    result = await db.execute(
        select(PushToken).where(
            and_(
                PushToken.user_id == user_id,
                PushToken.is_active == True
            )
        )
    )
    tokens = result.scalars().all()
    
    if not tokens:
        raise HTTPException(
            status_code=400,
            detail="Aucun push token enregistré. Ouvrez l'app mobile d'abord."
        )
    
    # Données de test
    club_name = "Club Test"
    slot_data = {
        "playground_name": "Padel 3",
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "start_time": "18:30",
        "price_total": 36.0
    }
    
    # Si alert_id fourni, récupérer les vraies infos
    if alert_id:
        alert_result = await db.execute(
            select(UserAlert).where(
                and_(
                    UserAlert.id == UUID(alert_id),
                    UserAlert.user_id == user_id
                )
            )
        )
        alert = alert_result.scalar_one_or_none()
        
        if alert:
            club_result = await db.execute(
                select(Club).where(Club.id == alert.club_id)
            )
            club = club_result.scalar_one_or_none()
            if club:
                club_name = club.name
            
            slot_data["date"] = alert.target_date.strftime("%Y-%m-%d")
            slot_data["start_time"] = alert.time_from.strftime("%H:%M")
    
    # Envoyer la notification à tous les appareils
    results = []
    for token in tokens:
        title = f"🎾 Créneau dispo - {club_name}"
        body = f"{slot_data['playground_name']} • {slot_data['date']} à {slot_data['start_time']} • {slot_data['price_total']}€"
        
        success = await send_push_notification(
            push_token=token.token,
            title=title,
            body=body,
            data={
                "type": "test_notification",
                "club_name": club_name,
                **slot_data
            }
        )
        results.append({
            "device_type": token.device_type,
            "success": success
        })
    
    return {
        "message": "Notification(s) envoyée(s)",
        "tokens_count": len(tokens),
        "results": results
    }


@router.get("/my-push-tokens")
async def get_my_push_tokens(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Liste les push tokens enregistrés pour l'utilisateur"""
    user_id = UUID(current_user.id)
    
    result = await db.execute(
        select(PushToken).where(PushToken.user_id == user_id)
    )
    tokens = result.scalars().all()
    
    return [
        {
            "id": str(t.id),
            "token": t.token[:30] + "...",
            "device_type": t.device_type,
            "is_active": t.is_active,
            "created_at": t.created_at.isoformat() if t.created_at else None
        }
        for t in tokens
    ]