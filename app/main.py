"""
FastAPI application principale
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.api.routes import alerts, clubs, stripe, users
import logging

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,  # Valeur par défaut si LOG_LEVEL n'existe pas
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title=settings.APP_NAME,
    description="API pour notifications créneaux padel",
    version="0.1.0"
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes
app.include_router(alerts.router)
app.include_router(clubs.router)
app.include_router(stripe.router)
app.include_router(users.router)

@app.get("/")
async def root():
    return {
        "app": settings.APP_NAME,
        "version": "0.1.0",
        "status": "running"
    }

@app.on_event("startup")
async def startup_event():
    """Événement de démarrage"""
    logger.info("🚀 Application démarrée")
    logger.info(f"📍 Environment: {getattr(settings, 'ENVIRONMENT', 'development')}")

@app.on_event("shutdown")
async def shutdown_event():
    """Événement d'arrêt"""
    logger.info("🛑 Application arrêtée")

@app.get("/health")
async def health():
    return {"status": "healthy"}


