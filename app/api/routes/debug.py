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


@router.post("/simulate-match/{alert_id}")
async def simulate_match_from_db(
    alert_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Récupère une VRAIE alerte en BDD, crée un FAUX créneau correspondant,
    et envoie la notification push au propriétaire de l'alerte.
    """
    try:
        alert_uuid = UUID(alert_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Format UUID invalide")

    # 1. Récupérer l'alerte et le club associé
    result = await db.execute(
        select(UserAlert, Club)
        .join(Club, UserAlert.club_id == Club.id)
        .where(UserAlert.id == alert_uuid)
    )
    row = result.first()
    
    if not row:
        raise HTTPException(status_code=404, detail="Alerte non trouvée")
        
    alert, club = row

    # 2. Créer un créneau fictif qui matche parfaitement l'alerte
    # On utilise target_date et time_from pour garantir le match
    
    # Gestion de la préférence indoor (si null, on met true par défaut pour le test)
    is_indoor = alert.indoor_only if alert.indoor_only is not None else True
    
    fake_slot = {
        "playground_name": "Terrain Démo (Simulé)",
        "club_name": club.name,
        "date": alert.target_date.strftime("%Y-%m-%d"), 
        "start_time": alert.time_from.strftime("%H:%M"),
        "duration": 90,
        "price_total": 32.0,
        "indoor": is_indoor,
        "link": "https://doinsport.app/link-test"
    }

    # 3. Récupérer les tokens de l'utilisateur PROPRIÉTAIRE de l'alerte
    # (Pas forcément celui qui lance la commande, bien que ce soit souvent le même en dev)
    tokens_result = await db.execute(
        select(PushToken).where(
            and_(
                PushToken.user_id == alert.user_id,
                PushToken.is_active == True
            )
        )
    )
    tokens = tokens_result.scalars().all()

    if not tokens:
        return {
            "status": "warning",
            "message": "Alerte trouvée et créneau généré, mais aucun token push actif pour cet utilisateur.",
            "simulated_slot": fake_slot
        }

    # 4. Envoyer la notification
    results = []
    for token in tokens:
        title = f"🎾 Créneau trouvé !"
        body = f"{fake_slot['club_name']} : {fake_slot['start_time']} le {datetime.strptime(fake_slot['date'], '%Y-%m-%d').strftime('%d/%m')} ({fake_slot['playground_name']})"
        
        success = await send_push_notification(
            push_token=token.token,
            title=title,
            body=body,
            data={
                "type": "slot_found", # Type utilisé par l'app pour la navigation
                "alert_id": str(alert.id),
                "slot": fake_slot
            }
        )
        results.append({
            "device_type": token.device_type,
            "success": success
        })

    return {
        "status": "success", 
        "message": f"Notification simulée pour l'alerte {alert.id}",
        "target_user": str(alert.user_id),
        "simulated_data": fake_slot,
        "push_results": results
    }


@router.post("/simulate-slot-notification")
async def simulate_slot_notification(
    alert_id: str = None,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Simule une notification de créneau disponible (Générique).
    Si alert_id fourni, utilise les infos de cette alerte.
    Sinon, envoie une notification de test générique.
    """
    user_id = UUID(current_user.id)
    
    # Récupérer les push tokens de l'utilisateur connecté
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
    
    # Données de test par défaut
    club_name = "Club Test"
    slot_data = {
        "playground_name": "Padel 3",
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "start_time": "18:30",
        "price_total": 36.0,
        "indoor": True
    }
    
    # Si alert_id fourni, récupérer les vraies infos (Support legacy pour ce paramètre)
    if alert_id:
        try:
            alert_uuid = UUID(alert_id)
            alert_result = await db.execute(
                select(UserAlert).where(
                    and_(
                        UserAlert.id == alert_uuid,
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
        except ValueError:
            pass # On ignore si l'ID est invalide et on envoie le fake par défaut
    
    # Envoyer la notification
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