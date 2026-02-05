"""
FastAPI application principale (Version gratuite)
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.api.routes import alerts, clubs, users, slots_router
from app.api.routes.debug import router as debug_router

import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title=settings.APP_NAME,
    description="API pour notifications créneaux padel",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes
app.include_router(alerts.router, prefix="/api")
app.include_router(clubs.router, prefix="/api")
app.include_router(users.router, prefix="/api")
app.include_router(slots_router.router, prefix="/api")
app.include_router(debug_router, prefix="/api")


@app.get("/")
async def root():
    return {
        "app": settings.APP_NAME,
        "version": "1.0.0",
        "status": "running"
    }


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.on_event("startup")
async def startup_event():
    logger.info("🚀 Application démarrée")


@app.on_event("shutdown")
async def shutdown_event():
    logger.info("🛑 Application arrêtée")