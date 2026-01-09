"""
KRENOO - Configuration Backend
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
    
    # === Stripe ===
    STRIPE_SECRET_KEY: str
    STRIPE_WEBHOOK_SECRET: str
    STRIPE_PRICE_ID_PREMIUM: str  # 3.99€/mois
    STRIPE_PRICE_ID_BOOST_SINGLE: str  # 1.49€
    STRIPE_PRICE_ID_BOOST_PACK: str  # 5.99€ (5 boosts)
    
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
# QUOTAS PAR PLAN
# ============================================

PLAN_QUOTAS = {
    "free": {
        "max_alerts": 2,
        "check_interval_minutes": 10,
        "min_days_ahead": 1,
        "max_days_ahead": 14,
        "max_time_window_hours": 6,
        "available_intervals": [10],
    },
    "premium": {
        "max_alerts": 10,
        "check_interval_minutes": 3,
        "min_days_ahead": 1,
        "max_days_ahead": 60,
        "max_time_window_hours": 12,
        "available_intervals": [3, 5, 10],
    },
}

# Boost configuration
BOOST_CONFIG = {
    "check_interval_seconds": 30,  # 30 secondes !
    "duration_hours": 24,
    "single_price_cents": 149,  # 1.49€
    "pack_count": 5,
    "pack_price_cents": 599,  # 5.99€
}


def get_quotas_for_plan(plan: str) -> dict:
    """Retourne les quotas pour un plan donné"""
    return PLAN_QUOTAS.get(plan, PLAN_QUOTAS["free"])