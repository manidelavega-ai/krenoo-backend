from pydantic_settings import BaseSettings
from typing import Optional

class Settings(BaseSettings):
    # Supabase
    SUPABASE_URL: str
    SUPABASE_KEY: str
    SUPABASE_SERVICE_KEY: str
    DATABASE_URL: str
    
    # Redis
    REDIS_URL: str
    
    # Resend
    RESEND_API_KEY: str
    FROM_EMAIL: str = "alerts@padelspot.com"
    
    # Stripe
    STRIPE_SECRET_KEY: str
    STRIPE_WEBHOOK_SECRET: str
    STRIPE_PRICE_ID_PREMIUM: str
    
    # App
    APP_NAME: str = "PadelSpot"
    API_URL: str 
    FRONTEND_URL: str = "padelspot://"
    SECRET_KEY: str
    
    # Doinsport
    DOINSPORT_API_BASE: str = "https://api-v3.doinsport.club"
    PADEL_ACTIVITY_ID: str = "ce8c306e-224a-4f24-aa9d-6500580924dc"
    LE_GARDEN_CLUB_ID: str = "a126b4d4-a2ee-4f30-bee3-6596368368fb"
    
    # Worker
    WORKER_CHECK_INTERVAL: int = 60
    LOG_LEVEL: str = "INFO"
    
    class Config:
        env_file = ".env"
        case_sensitive = True

settings = Settings()
