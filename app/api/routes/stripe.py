"""
Routes Stripe (checkout, webhooks, customer portal)
"""
from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel, Field
from typing import Optional, Literal
from datetime import datetime, timezone
from uuid import UUID

from app.core.config import settings, BOOST_CONFIG
from app.core.database import get_db
from app.core.auth import get_current_user
from app.models.models import Subscription, UserBoost, BoostPurchase
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
    boost_count: int = 0  # NOUVEAU


class CheckoutRequest(BaseModel):
    product_type: Literal["premium", "boost_single", "boost_pack"]


class CheckoutResponse(BaseModel):
    url: str


class PortalResponse(BaseModel):
    url: str


# === HELPERS ===

async def get_or_create_stripe_customer(db: AsyncSession, user_id: UUID, email: str) -> str:
    """Récupère ou crée un customer Stripe"""
    result = await db.execute(
        select(Subscription).where(Subscription.user_id == user_id)
    )
    subscription = result.scalar_one_or_none()
    
    if subscription and subscription.stripe_customer_id:
        return subscription.stripe_customer_id
    
    # Créer un nouveau customer
    customer = stripe.Customer.create(
        email=email,
        metadata={"user_id": str(user_id)}
    )
    
    # Sauvegarder le customer_id
    if subscription:
        subscription.stripe_customer_id = customer.id
    else:
        subscription = Subscription(
            user_id=user_id,
            stripe_customer_id=customer.id,
            plan="free",
            status="active"
        )
        db.add(subscription)
    
    await db.commit()
    return customer.id


async def add_boosts_to_user(db: AsyncSession, user_id: str, count: int) -> int:
    """Ajoute des boosts à un utilisateur (upsert). Retourne le nouveau total."""
    user_uuid = UUID(user_id)
    
    result = await db.execute(
        select(UserBoost).where(UserBoost.user_id == user_uuid)
    )
    user_boost = result.scalar_one_or_none()
    
    if user_boost:
        user_boost.boost_count += count
        user_boost.updated_at = datetime.now(timezone.utc)
    else:
        user_boost = UserBoost(user_id=user_uuid, boost_count=count)
        db.add(user_boost)
    
    await db.flush()
    return user_boost.boost_count


async def get_boost_count(db: AsyncSession, user_id: UUID) -> int:
    """Récupère le nombre de boosts d'un utilisateur"""
    result = await db.execute(
        select(UserBoost.boost_count).where(UserBoost.user_id == user_id)
    )
    count = result.scalar_one_or_none()
    return count or 0


# === ROUTES ===

@router.get("/subscription/status", response_model=SubscriptionStatus)
async def get_subscription_status(
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Récupère le statut de l'abonnement + boosts"""
    
    result = await db.execute(
        select(Subscription).where(Subscription.user_id == current_user.id)
    )
    subscription = result.scalar_one_or_none()
    
    # Récupérer le nombre de boosts
    boost_count = await get_boost_count(db, current_user.id)
    
    if not subscription:
        return SubscriptionStatus(
            plan="free",
            status="active",
            is_premium=False,
            current_period_end=None,
            cancel_at_period_end=False,
            boost_count=boost_count
        )
    
    # Vérifier cancel_at_period_end sur Stripe
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
        cancel_at_period_end=cancel_at_period_end,
        boost_count=boost_count
    )


@router.post("/checkout", response_model=CheckoutResponse)
async def create_checkout_session(
    payload: CheckoutRequest,
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Créer une session Stripe Checkout (Premium ou Boosts)"""
    
    product_type = payload.product_type
    
    # Mapping produit -> price_id + mode
    product_config = {
        "premium": {
            "price_id": settings.STRIPE_PRICE_ID_PREMIUM,
            "mode": "subscription",
            "boost_count": 0,
        },
        "boost_single": {
            "price_id": settings.STRIPE_PRICE_ID_BOOST_SINGLE,
            "mode": "payment",
            "boost_count": 1,
        },
        "boost_pack": {
            "price_id": settings.STRIPE_PRICE_ID_BOOST_PACK,
            "mode": "payment",
            "boost_count": BOOST_CONFIG["pack_count"],  # 5
        },
    }
    
    config = product_config.get(product_type)
    if not config:
        raise HTTPException(400, f"Type de produit invalide: {product_type}")
    
    # Vérifier si déjà premium (pour subscription uniquement)
    if product_type == "premium":
        result = await db.execute(
            select(Subscription).where(Subscription.user_id == current_user.id)
        )
        subscription = result.scalar_one_or_none()
        
        if subscription and subscription.plan == "premium" and subscription.status == "active":
            raise HTTPException(400, "Vous êtes déjà Premium !")
    
    try:
        # Récupérer ou créer le customer Stripe
        customer_id = await get_or_create_stripe_customer(db, current_user.id, current_user.email)
        
        # Définir URLs de retour
        success_url = f"{settings.FRONTEND_URL}/premium?success=true&product={product_type}"
        cancel_url = f"{settings.FRONTEND_URL}/premium?canceled=true"
        
        checkout_session = stripe.checkout.Session.create(
            customer=customer_id,
            payment_method_types=["card"],
            line_items=[{"price": config["price_id"], "quantity": 1}],
            mode=config["mode"],
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={
                "user_id": str(current_user.id),
                "product_type": product_type,
                "boost_count": str(config["boost_count"]),
            },
            allow_promotion_codes=True,
        )
        
        logger.info(f"✅ Checkout {product_type} créé pour user {current_user.id}")
        return CheckoutResponse(url=checkout_session.url)
    
    except Exception as e:
        logger.error(f"❌ Checkout error: {e}")
        raise HTTPException(400, str(e))


# Garder l'ancien endpoint pour compatibilité
@router.post("/subscription/checkout", response_model=CheckoutResponse)
async def create_subscription_checkout(
    current_user = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """[DEPRECATED] Utiliser POST /checkout avec product_type"""
    return await create_checkout_session(
        CheckoutRequest(product_type="premium"),
        current_user,
        db
    )


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
        raise HTTPException(404, "Aucun abonnement trouvé")
    
    try:
        portal_session = stripe.billing_portal.Session.create(
            customer=subscription.stripe_customer_id,
            return_url=f"{settings.API_URL}/api/redirect/profile",
        )
        
        logger.info(f"✅ Portal session créé pour user {current_user.id}")
        return PortalResponse(url=portal_session.url)
    
    except Exception as e:
        logger.error(f"❌ Portal error: {e}")
        raise HTTPException(400, str(e))


@router.post("/webhooks/stripe")
async def stripe_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """Webhook Stripe pour abonnements ET achats de boosts"""
    
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
        )
    except ValueError:
        logger.error("❌ Webhook: Invalid payload")
        raise HTTPException(400, "Invalid payload")
    except stripe.error.SignatureVerificationError:
        logger.error("❌ Webhook: Invalid signature")
        raise HTTPException(400, "Invalid signature")
    
    event_type = event["type"]
    data = event["data"]["object"]
    
    logger.info(f"📨 Stripe webhook: {event_type}")
    
    # === ÉVÉNEMENTS ===
    
    if event_type == "checkout.session.completed":
        session = data
        user_id = session.get("client_reference_id") or session.get("metadata", {}).get("user_id")
        product_type = session.get("metadata", {}).get("product_type", "premium")
        
        if not user_id:
            logger.error("❌ No user_id in checkout session")
            return JSONResponse(content={"status": "error", "message": "no user_id"})
        
        customer_id = session["customer"]
        
        # === ACHAT DE BOOSTS ===
        if product_type in ["boost_single", "boost_pack"]:
            boost_count = int(session.get("metadata", {}).get("boost_count", 1))
            
            # Ajouter les boosts
            new_total = await add_boosts_to_user(db, user_id, boost_count)
            
            # Enregistrer l'achat dans l'historique
            purchase = BoostPurchase(
                user_id=UUID(user_id),
                stripe_payment_intent_id=session.get("payment_intent"),
                product_type=product_type,
                boost_count=boost_count,
                amount_cents=session.get("amount_total", 0),
            )
            db.add(purchase)
            
            await db.commit()
            logger.info(f"✅ User {user_id} acheté {boost_count} boost(s) (total: {new_total})")
        
        # === ABONNEMENT PREMIUM ===
        elif product_type == "premium":
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
    
    
@router.get("/redirect/profile")
async def redirect_to_app():
    """Redirige vers l'app mobile après le portal Stripe"""
    return RedirectResponse(url="krenoo://profile")