"""
Routes API pour la gestion des clubs - VERSION CORRIGÉE V2
Scrape le site web Doinsport pour récupérer le club_id
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel, validator
from typing import List, Optional
import httpx
import re
import logging
from datetime import datetime, timedelta

from app.core.database import get_db
from app.core.auth import get_current_user
from app.core.config import settings
from app.models.models import Club

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/clubs", tags=["clubs"])

# === SCHEMAS ===

class ClubAddRequest(BaseModel):
    url: str
    
    @validator('url')
    def validate_url(cls, v):
        v = v.strip().lower()
        pattern = r'(?:https?://)?([a-z0-9-]+)\.doinsport\.club'
        match = re.match(pattern, v)
        if not match:
            raise ValueError("URL invalide. Format attendu: votreclub.doinsport.club")
        return v

class ClubResponse(BaseModel):
    id: str
    name: str
    slug: str
    city: Optional[str]
    address: Optional[str]
    enabled: bool
    
    class Config:
        from_attributes = True

class ClubVerifyResponse(BaseModel):
    valid: bool
    club_name: Optional[str] = None
    club_id: Optional[str] = None
    has_padel: bool = False
    courts_count: int = 0
    message: str = ""

# === HELPERS ===

def extract_slug_from_url(url: str) -> str:
    """Extrait le slug depuis l'URL Doinsport"""
    pattern = r'(?:https?://)?([a-z0-9-]+)\.doinsport\.club'
    match = re.match(pattern, url.lower().strip())
    if match:
        return match.group(1)
    raise ValueError("URL invalide")


async def get_club_id_from_website(slug: str) -> Optional[dict]:
    """
    Scrape le site web du club pour extraire le club_id depuis le HTML/JS.
    Le site Doinsport stocke le club_id dans les appels API du frontend.
    """
    timeout = httpx.Timeout(30.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        try:
            # Étape 1: Charger la page d'accueil du club
            url = f"https://{slug}.doinsport.club"
            logger.info(f"🌐 Scraping website: {url}")
            
            response = await client.get(url)
            if response.status_code != 200:
                logger.warning(f"❌ Site non accessible: {response.status_code}")
                return None
            
            html = response.text
            
            # Étape 2: Chercher le club_id dans le HTML/JS
            # Pattern 1: Dans les appels API (ex: /clubs/83abc3cd-22ee-4fbd-ac57-5f95b4971d9d)
            patterns = [
                r'/clubs/([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})',
                r'"clubId"\s*:\s*"([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})"',
                r"'clubId'\s*:\s*'([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})'",
                r'club\.id["\s:=]+([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})',
            ]
            
            club_id = None
            for pattern in patterns:
                match = re.search(pattern, html, re.IGNORECASE)
                if match:
                    club_id = match.group(1)
                    logger.info(f"✅ Club ID trouvé dans HTML: {club_id}")
                    break
            
            # Étape 3: Si pas trouvé, essayer de charger un JS qui contient la config
            if not club_id:
                # Chercher les scripts JS
                js_matches = re.findall(r'src="([^"]*\.js[^"]*)"', html)
                for js_url in js_matches[:5]:  # Limiter à 5 scripts
                    if not js_url.startswith('http'):
                        js_url = f"https://{slug}.doinsport.club{js_url}"
                    try:
                        js_resp = await client.get(js_url)
                        if js_resp.status_code == 200:
                            for pattern in patterns:
                                match = re.search(pattern, js_resp.text, re.IGNORECASE)
                                if match:
                                    club_id = match.group(1)
                                    logger.info(f"✅ Club ID trouvé dans JS: {club_id}")
                                    break
                        if club_id:
                            break
                    except:
                        continue
            
            if not club_id:
                logger.warning(f"❌ Club ID non trouvé dans le site web")
                return None
            
            # Étape 4: Récupérer les infos du club via l'API
            club_url = f"{settings.DOINSPORT_API_BASE}/clubs/{club_id}"
            logger.info(f"📡 Appel API club: {club_url}")
            
            club_resp = await client.get(club_url)
            if club_resp.status_code == 200:
                club_data = club_resp.json()
                
                # Vérifier si le club a du padel
                activities = club_data.get("activities", [])
                has_padel = any(
                    act.get("@id", "").endswith(settings.PADEL_ACTIVITY_ID) or 
                    act.get("id") == settings.PADEL_ACTIVITY_ID
                    for act in activities
                )
                
                address = club_data.get("address", [])
                if isinstance(address, list):
                    address = ", ".join(address) if address else None
                
                return {
                    "id": club_id,
                    "name": club_data.get("name"),
                    "city": club_data.get("city"),
                    "address": address,
                    "has_padel": has_padel
                }
            else:
                logger.warning(f"❌ API club error: {club_resp.status_code}")
                return {"id": club_id, "name": None, "city": None, "has_padel": False}
                
        except Exception as e:
            logger.error(f"❌ Erreur scraping: {e}")
            return None


async def count_padel_courts(club_id: str) -> int:
    """Compte les terrains de padel d'un club"""
    timeout = httpx.Timeout(30.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        test_date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        
        url = f"{settings.DOINSPORT_API_BASE}/clubs/playgrounds/plannings/{test_date}"
        params = {
            "club.id": club_id,
            "activities.id": settings.PADEL_ACTIVITY_ID,
            "bookingType": "unique"
        }
        
        logger.info(f"📡 Comptage terrains: {url} avec club.id={club_id}")
        
        try:
            response = await client.get(url, params=params)
            if response.status_code == 200:
                data = response.json()
                total = data.get("hydra:totalItems", 0)
                logger.info(f"🎾 {total} terrain(s) de padel trouvé(s)")
                return total
            else:
                logger.warning(f"❌ API error: {response.status_code}")
                return 0
        except Exception as e:
            logger.error(f"❌ Erreur comptage: {e}")
            return 0


async def fetch_club_info_from_doinsport(slug: str) -> dict:
    """
    Récupère les infos du club depuis Doinsport.
    1. Scrape le site web pour obtenir le club_id
    2. Appelle l'API pour les infos détaillées
    3. Compte les terrains de padel
    """
    logger.info(f"🔍 Recherche club pour slug: {slug}")
    
    # Étape 1: Récupérer le club_id depuis le site web
    club_info = await get_club_id_from_website(slug)
    
    if not club_info or not club_info.get("id"):
        return {
            "valid": False,
            "message": f"Club '{slug}' non trouvé. Vérifiez l'URL du club."
        }
    
    club_id = club_info["id"]
    club_name = club_info.get("name") or slug.replace("-", " ").title()
    
    # Étape 2: Compter les terrains de padel
    courts_count = await count_padel_courts(club_id)
    
    if courts_count > 0:
        return {
            "valid": True,
            "club_id": club_id,
            "club_name": club_name,
            "slug": slug,
            "has_padel": True,
            "courts_count": courts_count,
            "city": club_info.get("city"),
            "address": club_info.get("address")
        }
    elif club_info.get("has_padel"):
        # Le club a l'activité padel mais pas de terrains disponibles demain
        return {
            "valid": True,
            "club_id": club_id,
            "club_name": club_name,
            "slug": slug,
            "has_padel": True,
            "courts_count": 0,
            "city": club_info.get("city"),
            "address": club_info.get("address"),
            "message": "Aucun terrain disponible demain (vérifiez sur d'autres dates)"
        }
    else:
        return {
            "valid": True,
            "club_id": club_id,
            "club_name": club_name,
            "slug": slug,
            "has_padel": False,
            "courts_count": 0,
            "message": "Ce club n'a pas de terrains de padel"
        }


# === ROUTES ===

@router.get("", response_model=List[ClubResponse])
async def list_clubs(db: AsyncSession = Depends(get_db)):
    """Liste tous les clubs actifs"""
    result = await db.execute(select(Club).where(Club.enabled == True))
    clubs = result.scalars().all()
    
    return [
        ClubResponse(
            id=str(club.id),
            name=club.name,
            slug=club.slug if hasattr(club, 'slug') else "",
            city=club.city,
            address=club.address,
            enabled=club.enabled
        )
        for club in clubs
    ]


@router.post("/verify", response_model=ClubVerifyResponse)
async def verify_club(
    request: ClubAddRequest,
    current_user = Depends(get_current_user)
):
    """Vérifie si un club Doinsport existe et a des terrains de padel"""
    try:
        slug = extract_slug_from_url(request.url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    logger.info(f"🔍 Vérification club: {slug}")
    result = await fetch_club_info_from_doinsport(slug)
    
    return ClubVerifyResponse(
        valid=result.get("valid", False),
        club_name=result.get("club_name"),
        club_id=result.get("club_id"),
        has_padel=result.get("has_padel", False),
        courts_count=result.get("courts_count", 0),
        message=result.get("message", "")
    )


@router.post("/add", response_model=ClubResponse)
async def add_club(
    request: ClubAddRequest,
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Ajoute un nouveau club après vérification"""
    try:
        slug = extract_slug_from_url(request.url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    # Vérifier si le club existe déjà
    result = await db.execute(select(Club).where(Club.slug == slug))
    existing_club = result.scalar_one_or_none()
    
    if existing_club:
        return ClubResponse(
            id=str(existing_club.id),
            name=existing_club.name,
            slug=existing_club.slug,
            city=existing_club.city,
            address=existing_club.address,
            enabled=existing_club.enabled
        )
    
    # Vérifier sur Doinsport
    club_info = await fetch_club_info_from_doinsport(slug)
    
    if not club_info.get("valid"):
        raise HTTPException(status_code=404, detail=club_info.get("message", "Club non trouvé"))
    
    if not club_info.get("has_padel"):
        raise HTTPException(status_code=400, detail="Ce club n'a pas de terrains de padel")
    
    if not club_info.get("club_id"):
        raise HTTPException(status_code=400, detail="Impossible de récupérer l'ID du club")
    
    # Créer le club
    new_club = Club(
        doinsport_id=club_info["club_id"],
        name=club_info["club_name"],
        slug=slug,
        city=club_info.get("city"),
        address=club_info.get("address"),
        enabled=True
    )
    
    db.add(new_club)
    await db.commit()
    await db.refresh(new_club)
    
    logger.info(f"✅ Club ajouté: {new_club.name} ({slug}) - {club_info['courts_count']} terrains")
    
    return ClubResponse(
        id=str(new_club.id),
        name=new_club.name,
        slug=slug,
        city=new_club.city,
        address=new_club.address,
        enabled=new_club.enabled
    )
    
@router.get("/clubs")
async def get_clubs(
    region: Optional[str] = Query(None, description="Filtrer par region_slug"),
    db: AsyncSession = Depends(get_db)
):
    """Liste des clubs actifs, optionnellement filtrés par région"""
    query = select(Club).where(Club.enabled == True)
    
    if region:
        query = query.where(Club.region_slug == region)
    
    query = query.order_by(Club.name)
    result = await db.execute(query)
    clubs = result.scalars().all()
    
    return [
        {
            "id": str(c.id),
            "name": c.name,
            "city": c.city,
            "doinsport_id": str(c.doinsport_id),
            "region_slug": c.region_slug
        }
        for c in clubs
    ]