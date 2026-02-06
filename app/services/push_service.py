"""
Service d'envoi de Push Notifications via Expo
"""
import httpx
from typing import List, Dict, Optional
import logging

logger = logging.getLogger(__name__)

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"


async def send_push_notification(
    push_token: str,
    title: str,
    body: str,
    data: Optional[Dict] = None,
    sound: str = "default",
    badge: int = 1
) -> bool:
    """
    Envoie une push notification à un appareil via Expo
    
    Args:
        push_token: Token Expo Push (ExponentPushToken[xxx])
        title: Titre de la notification
        body: Corps du message
        data: Données additionnelles (pour navigation, etc.)
        sound: Son de la notification
        badge: Numéro du badge sur l'icône
    
    Returns:
        True si envoyé avec succès
    """
    
    if not push_token or not push_token.startswith('ExponentPushToken'):
        logger.warning(f"⚠️ Token invalide: {push_token}")
        return False
    
    message = {
        "to": push_token,
        "title": title,
        "body": body,
        "sound": sound,
        "badge": badge,
        "priority": "high",
        "channelId": "default",
    }
    
    if data:
        message["data"] = data
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                EXPO_PUSH_URL,
                json=message,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                }
            )
            
            result = response.json()
            
            if response.status_code == 200:
                # Vérifier le statut dans la réponse
                if result.get("data", {}).get("status") == "ok":
                    logger.info(f"✅ Push envoyé: {title}")
                    return True
                else:
                    error = result.get("data", {}).get("message", "Unknown error")
                    logger.warning(f"⚠️ Push status not ok: {error}")
                    return False
            else:
                logger.error(f"❌ Push failed: {response.status_code} - {result}")
                return False
                
    except Exception as e:
        logger.error(f"❌ Erreur envoi push: {e}")
        return False


async def send_push_to_multiple(
    push_tokens: List[str],
    title: str,
    body: str,
    data: Optional[Dict] = None
) -> Dict[str, int]:
    """
    Envoie une push notification à plusieurs appareils
    
    Returns:
        Dict avec 'success' et 'failed' counts
    """
    
    # Filtrer les tokens valides
    valid_tokens = [t for t in push_tokens if t and t.startswith('ExponentPushToken')]
    
    if not valid_tokens:
        return {"success": 0, "failed": len(push_tokens)}
    
    # Expo accepte des batches de 100 max
    messages = []
    for token in valid_tokens:
        messages.append({
            "to": token,
            "title": title,
            "body": body,
            "sound": "default",
            "priority": "high",
            "channelId": "default",
            "data": data or {}
        })
    
    results = {"success": 0, "failed": 0}
    
    try:
        async with httpx.AsyncClient() as client:
            # Envoyer par batch de 100
            for i in range(0, len(messages), 100):
                batch = messages[i:i+100]
                
                response = await client.post(
                    EXPO_PUSH_URL,
                    json=batch,
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    }
                )
                
                if response.status_code == 200:
                    data_response = response.json().get("data", [])
                    for item in data_response:
                        if item.get("status") == "ok":
                            results["success"] += 1
                        else:
                            results["failed"] += 1
                else:
                    results["failed"] += len(batch)
                    
    except Exception as e:
        logger.error(f"❌ Erreur batch push: {e}")
        results["failed"] += len(valid_tokens) - results["success"]
    
    logger.info(f"📤 Push batch: {results['success']} success, {results['failed']} failed")
    return results


async def send_slot_push_notification(
    push_token: str,
    club_name: str,
    slot: Dict,
    alert_id: str,
    booking_url: Optional[str] = None
) -> bool:
    """
    Envoie une notification push pour un nouveau créneau
    """
    
    title = f"🎾 Créneau dispo - {club_name}"
    body = f"{slot['playground_name']} • {slot['date']} à {slot['start_time']} • {slot['price_total']}€"
    
    data = {
        "type": "new_slot",
        "alert_id": alert_id,
        "club_name": club_name,
        "playground_name": slot['playground_name'],
        "date": slot['date'],
        "start_time": slot['start_time'],
        "price": slot['price_total'],
        "booking_url": booking_url,
    }
    
    return await send_push_notification(
        push_token=push_token,
        title=title,
        body=body,
        data=data
    )