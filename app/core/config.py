"""
KRENOO - Configuration Backend (Version gratuite)
"""
from pydantic_settings import BaseSettings
from typing import Optional
from functools import lru_cache


class Settings(BaseSettings):
    # === App ===
    APP_NAME: str = "Krenoo"
    API_URL: str = "https://api.krenoo.fr"
    FRONTEND_URL: str = "krenoo://"
    SECRET_KEY: str
    LOG_LEVEL: str = "INFO"
    
    # === Supabase ===
    SUPABASE_URL: str
    SUPABASE_KEY: str  # anon key
    SUPABASE_SERVICE_KEY: str  # service role key
    DATABASE_URL: str
    
    # === Resend (emails) ===
    RESEND_API_KEY: str
    FROM_EMAIL: str = "contact@krenoo.fr"
    
    # === Doinsport ===
    DOINSPORT_API_BASE: str = "https://api-v3.doinsport.club"
    PADEL_ACTIVITY_ID: str = "ce8c306e-224a-4f24-aa9d-6500580924dc"
    
    # === Worker ===
    WORKER_CHECK_INTERVAL: int = 60  # secondes entre chaque cycle
    
    # === Redis (optionnel) ===
    REDIS_URL: Optional[str] = None
    
    class Config:
        env_file = ".env"
        case_sensitive = True


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()


# ============================================
# QUOTAS (Version gratuite - plan unique)
# ============================================

APP_QUOTAS = {
    "max_alerts": 3,
    "check_interval_minutes": 3,
    "min_days_ahead": 0,  # Aujourd'hui
    "max_days_ahead": 60,
    "max_time_window_hours": 12,
}


def get_quotas() -> dict:
    """Retourne les quotas de l'application"""
    return APP_QUOTAS