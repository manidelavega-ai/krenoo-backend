"""
Recherche de créneaux en temps réel
"""
from fastapi import APIRouter, Query, HTTPException
from typing import Optional
from datetime import date, datetime
from pydantic import BaseModel
import httpx
import asyncio
import logging
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from sqlalchemy import select, text

logger = logging.getLogger(__name__)
router = APIRouter(tags=["slots"])

DOINSPORT_BASE = settings.DOINSPORT_API_BASE
PADEL_ACTIVITY_ID = settings.PADEL_ACTIVITY_ID

class DurationOption(BaseModel):
    duration_minutes: int
    price_per_person: float
    price_total: float

class SlotInfo(BaseModel):
    playground_id: str
    playground_name: str
    indoor: bool
    start_time: str
    durations: list[DurationOption]  # Liste des durées disponibles

class ClubResult(BaseModel):
    club_id: str
    club_name: str
    city: str
    slots: list[SlotInfo]
    slots_count: int
    error: Optional[str] = None

class SearchResponse(BaseModel):
    region: str
    date: str
    total_slots: int
    clubs_with_availability: int
    results: list[ClubResult]

@router.get("/slots/regions")
async def get_regions():
    """Liste des régions disponibles"""
    async with AsyncSessionLocal() as db:
        result = await db.execute(text("""
            SELECT r.slug, r.display_name, r.cities, 
                   COUNT(c.id) as clubs_count,
                   COALESCE(SUM(c.courts_count), 0) as total_courts
            FROM regions r
            LEFT JOIN clubs c ON c.region_slug = r.slug AND c.enabled = true
            GROUP BY r.slug, r.display_name, r.cities
            ORDER BY r.display_name
        """))
        rows = result.fetchall()
        return [
            {
                "slug": r.slug,
                "display_name": r.display_name,
                "cities": r.cities or [],
                "clubs_count": r.clubs_count,
                "total_courts": int(r.total_courts)
            }
            for r in rows
        ]

@router.get("/slots/search", response_model=SearchResponse)
async def search_slots(
    region: str = Query(..., description="Slug de la région"),
    date: date = Query(..., description="Date YYYY-MM-DD"),
    time_from: str = Query("08:00", description="Heure début HH:MM"),
    time_to: str = Query("22:00", description="Heure fin HH:MM"),
    indoor_only: Optional[bool] = Query(None, description="Filtrer indoor/outdoor")
):
    """Recherche créneaux disponibles en temps réel"""
    
    async with AsyncSessionLocal() as db:
        result = await db.execute(text("""
            SELECT c.id, c.doinsport_id, c.name, c.city
            FROM clubs c
            WHERE c.region_slug = :region AND c.enabled = true
        """), {"region": region})
        clubs = result.fetchall()
    
    if not clubs:
        raise HTTPException(404, f"Aucun club trouvé pour la région '{region}'")
    
    async def fetch_club_slots(club):
        try:
            url = f"{DOINSPORT_BASE}/clubs/playgrounds/plannings/{date}"
            params = {
                "club.id": str(club.doinsport_id),
                "activities.id": PADEL_ACTIVITY_ID,
                "from": f"{time_from}:00",
                "to": f"{time_to}:00",
                "bookingType": "unique"
            }
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()
            
            slots_dict = {}  # Clé: (playground_id, start_time)
            for pg in data.get("hydra:member", []):
                is_indoor = pg.get("indoor", False)
                if indoor_only is not None and is_indoor != indoor_only:
                    continue
                
                for act in pg.get("activities", []):
                    for slot in act.get("slots", []):
                        for price in slot.get("prices", []):
                            if price.get("bookable"):
                                key = (pg["id"], slot["startAt"])
                                duration_opt = DurationOption(
                                    duration_minutes=price["duration"] // 60,
                                    price_per_person=price["pricePerParticipant"] / 100,
                                    price_total=(price["pricePerParticipant"] * price["participantCount"]) / 100
                                )
                                
                                if key not in slots_dict:
                                    slots_dict[key] = SlotInfo(
                                        playground_id=pg["id"],
                                        playground_name=pg["name"],
                                        indoor=is_indoor,
                                        start_time=slot["startAt"],
                                        durations=[duration_opt]
                                    )
                                else:
                                    # Ajouter la durée si pas déjà présente
                                    existing_durations = [d.duration_minutes for d in slots_dict[key].durations]
                                    if duration_opt.duration_minutes not in existing_durations:
                                        slots_dict[key].durations.append(duration_opt)
            
            # Trier les durées par ordre croissant
            slots = []
            for slot in slots_dict.values():
                slot.durations.sort(key=lambda d: d.duration_minutes)
                slots.append(slot)
            
            return ClubResult(
                club_id=str(club.id),
                club_name=club.name,
                city=club.city or "",
                slots=slots,
                slots_count=len(slots)
            )
        except Exception as e:
            logger.error(f"Erreur scraping {club.name}: {e}")
            return ClubResult(
                club_id=str(club.id),
                club_name=club.name,
                city=club.city or "",
                slots=[],
                slots_count=0,
                error=str(e)
            )
    
    results = await asyncio.gather(*[fetch_club_slots(c) for c in clubs])
    results_with_slots = [r for r in results if r.slots_count > 0]
    total = sum(r.slots_count for r in results)
    
    return SearchResponse(
        region=region,
        date=str(date),
        total_slots=total,
        clubs_with_availability=len(results_with_slots),
        results=sorted(results, key=lambda x: -x.slots_count)
    )