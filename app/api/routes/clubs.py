"""
Routes API pour la gestion des clubs - VERSION CORRIGÉE
Utilise l'endpoint /clubs/playgrounds/plannings pour récupérer le club_id
"""
from fastapi import APIRouter, Depends, HTTPException
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


def normalize_for_matching(text: str) -> str:
    """Normalise un texte pour la comparaison (minuscules, sans accents, sans tirets)"""
    import unicodedata
    text = text.lower().strip()
    text = unicodedata.normalize('NFD', text)
    text = ''.join(c for c in text if unicodedata.category(c) != 'Mn')
    text = re.sub(r'[-_\s]+', '', text)
    return text


def slug_matches_club_name(slug: str, club_name: str) -> bool:
    """Vérifie si le slug correspond au nom du club"""
    slug_norm = normalize_for_matching(slug)
    name_norm = normalize_for_matching(club_name)
    
    # Match exact après normalisation
    if slug_norm == name_norm:
        return True
    
    # Le slug est contenu dans le nom ou vice-versa
    if slug_norm in name_norm or name_norm in slug_norm:
        return True
    
    # Vérifier si tous les mots du slug sont dans le nom
    slug_words = set(slug.lower().replace("-", " ").split())
    name_words = set(club_name.lower().split())
    
    # Ignorer les mots génériques
    ignore_words = {'padel', 'club', 'de', 'du', 'la', 'le', 'les', 'center', 'centre'}
    slug_words = slug_words - ignore_words
    
    if slug_words and slug_words.issubset(name_words):
        return True
    
    return False


async def fetch_club_info_from_doinsport(slug: str) -> dict:
    """
    Récupère les infos du club depuis l'API Doinsport.
    Stratégie: chercher dans les playgrounds de padel et matcher par nom.
    """
    # Timeout élevé car l'API Doinsport peut être lente avec beaucoup de données
    timeout = httpx.Timeout(60.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        test_date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        
        # Récupérer TOUS les terrains de padel disponibles
        url = f"{settings.DOINSPORT_API_BASE}/clubs/playgrounds/plannings/{test_date}"
        params = {
            "activities.id": settings.PADEL_ACTIVITY_ID,
            "bookingType": "unique",
            "itemsPerPage": 200  # Réduit pour accélérer la requête
        }
        
        try:
            logger.info(f"🔍 Recherche club pour slug: {slug}")
            logger.info(f"📡 Appel API: {url} avec params: {params}")
            response = await client.get(url, params=params)
            logger.info(f"📥 Réponse reçue: status={response.status_code}")
            
            if response.status_code != 200:
                logger.error(f"❌ API error: {response.status_code}")
                return {"valid": False, "message": f"Erreur API Doinsport: {response.status_code}"}
            
            data = response.json()
            members = data.get("hydra:member", [])
            logger.info(f"📊 {len(members)} playgrounds récupérés")
            
            # Grouper par club et chercher celui qui correspond au slug
            clubs_found = {}
            
            for pg in members:
                club_data = pg.get("club", {})
                if not isinstance(club_data, dict):
                    continue
                
                club_id = club_data.get("id")
                club_name = club_data.get("name", "")
                
                if not club_id or not club_name:
                    continue
                
                # Vérifier si ce club correspond au slug
                if slug_matches_club_name(slug, club_name):
                    if club_id not in clubs_found:
                        clubs_found[club_id] = {
                            "id": club_id,
                            "name": club_name,
                            "city": club_data.get("city"),
                            "padel_courts": set()
                        }
                    
                    # Compter ce terrain de padel (vérifier qu'il a bien l'activité padel)
                    activities = pg.get("activities", [])
                    has_padel = any(
                        act.get("id") == settings.PADEL_ACTIVITY_ID
                        for act in activities
                        if isinstance(act, dict)
                    )
                    
                    if has_padel:
                        pg_id = pg.get("id")
                        if pg_id:
                            clubs_found[club_id]["padel_courts"].add(pg_id)
            
            # Analyser les résultats
            if not clubs_found:
                logger.warning(f"❌ Aucun club trouvé pour slug: {slug}")
                return {
                    "valid": False,
                    "message": f"Club '{slug}' non trouvé. Vérifiez l'URL du club."
                }
            
            # Prendre le meilleur match (celui avec le plus de terrains si plusieurs)
            best_club = max(clubs_found.values(), key=lambda c: len(c["padel_courts"]))
            courts_count = len(best_club["padel_courts"])
            
            logger.info(f"✅ Club trouvé: {best_club['name']} (ID: {best_club['id']}) - {courts_count} terrains")
            
            if courts_count > 0:
                return {
                    "valid": True,
                    "club_id": best_club["id"],
                    "club_name": best_club["name"],
                    "slug": slug,
                    "has_padel": True,
                    "courts_count": courts_count,
                    "city": best_club.get("city")
                }
            else:
                return {
                    "valid": True,
                    "club_id": best_club["id"],
                    "club_name": best_club["name"],
                    "slug": slug,
                    "has_padel": False,
                    "courts_count": 0,
                    "message": "Ce club n'a pas de terrains de padel disponibles"
                }
                
        except httpx.TimeoutException as e:
            logger.error(f"⏱️ Timeout après 60s: {e}")
            return {"valid": False, "message": "Timeout - Doinsport ne répond pas"}
        except Exception as e:
            logger.error(f"❌ Erreur: {e}")
            return {"valid": False, "message": f"Erreur: {str(e)}"}


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
        address=None,  # L'API playgrounds ne retourne pas l'adresse
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