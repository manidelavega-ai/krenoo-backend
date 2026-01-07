"""
Routes Stripe (checkout, webhooks, customer portal)
"""
from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timezone
from app.core.config import settings
from app.core.database import get_db
from app.core.auth import get_current_user
from app.models.models import Subscription
import stripe
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["stripe"])

stripe.api_key = settings.STRIPE_SECRET_KEY


# === SCHEMAS ===

class SubscriptionStatus(BaseModel):
    plan: str
    status: str
    is_premium: bool
    current_period_end: Optional[datetime] = None
    cancel_at_period_end: bool = False

class CheckoutResponse(BaseModel):
    url: str

class PortalResponse(BaseModel):
    url: str


# === ROUTES ===

@router.get("/subscription/status", response_model=SubscriptionStatus)
async def get_subscription_status(
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Récupère le statut de l'abonnement de l'utilisateur"""
    
    result = await db.execute(
        select(Subscription).where(Subscription.user_id == current_user.id)
    )
    subscription = result.scalar_one_or_none()
    
    if not subscription:
        return SubscriptionStatus(
            plan="free",
            status="active",
            is_premium=False,
            current_period_end=None,
            cancel_at_period_end=False
        )
    
    # Vérifier si l'abonnement est actif sur Stripe
    cancel_at_period_end = False
    if subscription.stripe_subscription_id:
        try:
            stripe_sub = stripe.Subscription.retrieve(subscription.stripe_subscription_id)
            cancel_at_period_end = stripe_sub.cancel_at_period_end
        except:
            pass
    
    return SubscriptionStatus(
        plan=subscription.plan,
        status=subscription.status,
        is_premium=subscription.plan == "premium" and subscription.status == "active",
        current_period_end=subscription.current_period_end,
        cancel_at_period_end=cancel_at_period_end
    )


@router.post("/subscription/checkout", response_model=CheckoutResponse)
async def create_checkout_session(
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Créer une session Stripe Checkout pour upgrade Premium"""
    
    # Vérifier si déjà premium
    result = await db.execute(
        select(Subscription).where(Subscription.user_id == current_user.id)
    )
    subscription = result.scalar_one_or_none()
    
    if subscription and subscription.plan == "premium" and subscription.status == "active":
        raise HTTPException(status_code=400, detail="Vous êtes déjà Premium !")
    
    try:
        checkout_session = stripe.checkout.Session.create(
            customer_email=current_user.email,
            client_reference_id=str(current_user.id),
            payment_method_types=["card"],
            line_items=[
                {
                    "price": settings.STRIPE_PRICE_ID_PREMIUM,
                    "quantity": 1,
                },
            ],
            mode="subscription",
            success_url=f"{settings.FRONTEND_URL}/premium?success=true",
            cancel_url=f"{settings.FRONTEND_URL}/premium?canceled=true",
            metadata={
                "user_id": str(current_user.id)
            },
            allow_promotion_codes=True,  # Autoriser les codes promo
        )
        
        logger.info(f"✅ Checkout session created for user {current_user.id}")
        return CheckoutResponse(url=checkout_session.url)
    
    except Exception as e:
        logger.error(f"❌ Checkout error: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/subscription/portal", response_model=PortalResponse)
async def create_customer_portal(
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Créer un lien vers le Customer Portal Stripe"""
    
    result = await db.execute(
        select(Subscription).where(Subscription.user_id == current_user.id)
    )
    subscription = result.scalar_one_or_none()
    
    if not subscription or not subscription.stripe_customer_id:
        raise HTTPException(
            status_code=404, 
            detail="Aucun abonnement trouvé"
        )
    
    try:
        portal_session = stripe.billing_portal.Session.create(
            customer=subscription.stripe_customer_id,
            return_url=f"{settings.FRONTEND_URL}/profile",
        )
        
        logger.info(f"✅ Portal session created for user {current_user.id}")
        return PortalResponse(url=portal_session.url)
    
    except Exception as e:
        logger.error(f"❌ Portal error: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/webhooks/stripe")
async def stripe_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """Webhook Stripe pour gérer les événements d'abonnement"""
    
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
        )
    except ValueError:
        logger.error("❌ Webhook: Invalid payload")
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError:
        logger.error("❌ Webhook: Invalid signature")
        raise HTTPException(status_code=400, detail="Invalid signature")
    
    event_type = event["type"]
    data = event["data"]["object"]
    
    logger.info(f"📨 Stripe webhook: {event_type}")
    
    # === ÉVÉNEMENTS ===
    
    if event_type == "checkout.session.completed":
        session = data
        user_id = session.get("client_reference_id")
        
        if not user_id:
            logger.error("❌ No user_id in checkout session")
            return JSONResponse(content={"status": "error"})
        
        customer_id = session["customer"]
        subscription_id = session.get("subscription")
        
        result = await db.execute(
            select(Subscription).where(Subscription.user_id == user_id)
        )
        subscription = result.scalar_one_or_none()
        
        if subscription:
            subscription.stripe_customer_id = customer_id
            subscription.stripe_subscription_id = subscription_id
            subscription.plan = "premium"
            subscription.status = "active"
        else:
            subscription = Subscription(
                user_id=user_id,
                stripe_customer_id=customer_id,
                stripe_subscription_id=subscription_id,
                plan="premium",
                status="active"
            )
            db.add(subscription)
        
        await db.commit()
        logger.info(f"✅ User {user_id} upgraded to Premium")
    
    elif event_type == "customer.subscription.updated":
        sub = data
        
        result = await db.execute(
            select(Subscription).where(
                Subscription.stripe_subscription_id == sub["id"]
            )
        )
        subscription = result.scalar_one_or_none()
        
        if subscription:
            subscription.status = sub["status"]
            # Convertir timestamp Unix en datetime
            if sub.get("current_period_end"):
                subscription.current_period_end = datetime.fromtimestamp(
                    sub["current_period_end"], tz=timezone.utc
                )
            await db.commit()
            logger.info(f"✅ Subscription updated: {sub['id']}")
    
    elif event_type == "customer.subscription.deleted":
        sub = data
        
        result = await db.execute(
            select(Subscription).where(
                Subscription.stripe_subscription_id == sub["id"]
            )
        )
        subscription = result.scalar_one_or_none()
        
        if subscription:
            subscription.status = "canceled"
            subscription.plan = "free"
            await db.commit()
            logger.info(f"🚫 Subscription canceled: {sub['id']}")
    
    elif event_type == "invoice.payment_failed":
        invoice = data
        subscription_id = invoice.get("subscription")
        
        if subscription_id:
            result = await db.execute(
                select(Subscription).where(
                    Subscription.stripe_subscription_id == subscription_id
                )
            )
            subscription = result.scalar_one_or_none()
            
            if subscription:
                subscription.status = "past_due"
                await db.commit()
                logger.warning(f"⚠️ Payment failed: {subscription_id}")
    
    return JSONResponse(content={"status": "success"})