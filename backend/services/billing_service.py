import base64
import hashlib
import hmac
import os
import re
import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import httpx
from fastapi import HTTPException, Request

from backend.db.database import SessionLocal
from backend.models.enterprise import BillingEvent, Invoice, PaymentTransaction, Subscription
from backend.services.enterprise_service import DEFAULT_LIMITS, ensure_subscription


SUPPORTED_CURRENCIES = [item.strip().upper() for item in os.getenv("SUPPORTED_CURRENCIES", "KES,USD,EUR,GBP").split(",") if item.strip()]
DEFAULT_CURRENCY = os.getenv("DEFAULT_CURRENCY", "KES").upper()
PLAN_PRICES = {
    ("free", "monthly", "KES"): int(os.getenv("FREE_PLAN_PRICE", "0")),
    ("free", "yearly", "KES"): int(os.getenv("FREE_PLAN_PRICE", "0")),
    ("pro", "monthly", "KES"): int(os.getenv("PRO_PLAN_MONTHLY_KES", "2500")),
    ("pro", "yearly", "KES"): int(os.getenv("PRO_PLAN_YEARLY_KES", "25000")),
    ("enterprise", "monthly", "KES"): int(os.getenv("ENTERPRISE_PLAN_MONTHLY_KES", "15000")),
    ("enterprise", "yearly", "KES"): int(os.getenv("ENTERPRISE_PLAN_YEARLY_KES", "150000")),
}
PLAN_PRICES_CENTS = {
    "free": 0,
    "pro": PLAN_PRICES[("pro", "monthly", "KES")] * 100,
    "enterprise": PLAN_PRICES[("enterprise", "monthly", "KES")] * 100,
}
PAYMENT_DESTINATIONS = [
    os.getenv("MPESA_DESTINATION_PHONE_1", "254798733849"),
    os.getenv("MPESA_DESTINATION_PHONE_2", "254119625818"),
]


def _amount_cents(plan: str, billing_cycle: str = "monthly", currency: str = DEFAULT_CURRENCY) -> int:
    plan = plan.lower()
    billing_cycle = billing_cycle.lower()
    currency = currency.upper()
    if currency not in SUPPORTED_CURRENCIES:
        raise HTTPException(status_code=400, detail="Unsupported currency")
    if plan not in DEFAULT_LIMITS:
        raise HTTPException(status_code=400, detail="Unsupported billing plan")
    if billing_cycle not in {"monthly", "yearly"}:
        raise HTTPException(status_code=400, detail="Unsupported billing cycle")

    if currency == "KES":
        return PLAN_PRICES.get((plan, billing_cycle, currency), 0) * 100

    kes_amount = PLAN_PRICES.get((plan, billing_cycle, "KES"), 0)
    fx = {"USD": 0.0077, "EUR": 0.0071, "GBP": 0.0061}.get(currency, 1)
    return int(round(kes_amount * fx * 100))


def _normalize_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if digits.startswith("0") and len(digits) == 10:
        digits = "254" + digits[1:]
    if digits.startswith("7") and len(digits) == 9:
        digits = "254" + digits
    if not re.fullmatch(r"254\d{9}", digits):
        raise HTTPException(status_code=400, detail="Use a valid Kenyan phone number like 2547XXXXXXXX")
    return digits


def _new_reference(prefix: str) -> str:
    return f"{prefix}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(4)}"


def _transaction_by_key(db, idempotency_key: str):
    return db.query(PaymentTransaction).filter(PaymentTransaction.idempotency_key == idempotency_key).first()


def subscription_overview(organization_id: int) -> Dict[str, Any]:
    subscription = ensure_subscription(organization_id)
    db = SessionLocal()
    try:
        events = db.query(BillingEvent).filter(BillingEvent.organization_id == organization_id).order_by(BillingEvent.created_at.desc()).limit(20).all()
        invoices = db.query(Invoice).filter(Invoice.organization_id == organization_id).order_by(Invoice.issued_at.desc()).limit(10).all()
        payments = db.query(PaymentTransaction).filter(PaymentTransaction.organization_id == organization_id).order_by(PaymentTransaction.created_at.desc()).limit(20).all()
        return {
            **subscription,
            "currency": DEFAULT_CURRENCY,
            "supported_currencies": SUPPORTED_CURRENCIES,
            "plan_prices": plan_catalog(),
            "payment_destinations": PAYMENT_DESTINATIONS,
            "providers": provider_status(),
            "events": [serialize_billing_event(event) for event in events],
            "invoices": [serialize_invoice(invoice) for invoice in invoices],
            "payments": [serialize_payment(payment) for payment in payments],
            "analytics": billing_analytics(organization_id),
        }
    finally:
        db.close()


def update_plan(organization_id: int, plan: str) -> Dict[str, Any]:
    if plan not in DEFAULT_LIMITS:
        raise HTTPException(status_code=400, detail="Unsupported billing plan")

    db = SessionLocal()
    try:
        subscription = db.query(Subscription).filter(Subscription.organization_id == organization_id).first()
        if not subscription:
            ensure_subscription(organization_id, plan=plan)
            subscription = db.query(Subscription).filter(Subscription.organization_id == organization_id).first()
        subscription.plan = plan
        subscription.status = "active"
        subscription.limits_json = DEFAULT_LIMITS[plan]
        subscription.updated_at = datetime.utcnow()
        db.add(
            BillingEvent(
                organization_id=organization_id,
                event_type="plan_changed",
                quantity=1,
                amount_cents=PLAN_PRICES_CENTS[plan],
                metadata_json={"plan": plan},
            )
        )
        db.commit()
        return subscription_overview(organization_id)
    finally:
        db.close()


def record_usage_event(organization_id: int, metric: str, amount: int = 1, metadata: Optional[dict] = None) -> Dict[str, Any]:
    db = SessionLocal()
    try:
        subscription = db.query(Subscription).filter(Subscription.organization_id == organization_id).first()
        if not subscription:
            ensure_subscription(organization_id)
            subscription = db.query(Subscription).filter(Subscription.organization_id == organization_id).first()

        usage = dict(subscription.usage_json or {})
        usage[metric] = int(usage.get(metric, 0)) + int(amount)
        subscription.usage_json = usage
        subscription.updated_at = datetime.utcnow()

        event = BillingEvent(
            organization_id=organization_id,
            event_type=f"usage.{metric}",
            quantity=amount,
            amount_cents=0,
            metadata_json=metadata or {},
        )
        db.add(event)
        db.commit()
        db.refresh(event)
        return serialize_billing_event(event)
    finally:
        db.close()


def enforce_quota(organization_id: int, metric: str, amount: int = 1):
    subscription = ensure_subscription(organization_id)
    usage = subscription.get("usage", {})
    limits = subscription.get("limits", {})
    limit = limits.get(metric)
    if limit is not None and int(usage.get(metric, 0)) + amount > int(limit):
        raise HTTPException(status_code=402, detail=f"{metric} quota exceeded for current plan")


def generate_invoice(organization_id: int) -> Dict[str, Any]:
    overview = subscription_overview(organization_id)
    plan = overview.get("plan", "free")
    amount = PLAN_PRICES_CENTS.get(plan, 0)
    invoice_number = f"INV-{organization_id}-{int(datetime.utcnow().timestamp())}"
    line_items = [
        {"label": f"{plan.title()} plan", "quantity": 1, "amount_cents": amount},
        {"label": "AI usage credits", "quantity": overview.get("usage", {}).get("ai_calls", 0), "amount_cents": 0},
    ]

    db = SessionLocal()
    try:
        invoice = Invoice(
            organization_id=organization_id,
            invoice_number=invoice_number,
            status="open" if amount else "paid",
            amount_cents=amount,
            line_items=line_items,
            due_at=datetime.utcnow() + timedelta(days=14),
        )
        db.add(invoice)
        db.commit()
        db.refresh(invoice)
        return serialize_invoice(invoice)
    finally:
        db.close()


def plan_catalog() -> list[dict]:
    return [
        {
            "plan": plan,
            "billing_cycle": cycle,
            "currency": currency,
            "amount_cents": _amount_cents(plan, cycle, currency),
            "features": DEFAULT_LIMITS.get(plan, {}),
        }
        for plan in ("free", "pro", "enterprise")
        for cycle in ("monthly", "yearly")
        for currency in SUPPORTED_CURRENCIES
    ]


def provider_status() -> Dict[str, Any]:
    return {
        "mpesa": {
            "enabled": bool(os.getenv("MPESA_CONSUMER_KEY") and os.getenv("MPESA_CONSUMER_SECRET")),
            "environment": os.getenv("MPESA_ENVIRONMENT", "sandbox"),
            "destination_numbers": PAYMENT_DESTINATIONS,
        },
        "paypal": {"enabled": bool(os.getenv("PAYPAL_CLIENT_ID") and os.getenv("PAYPAL_CLIENT_SECRET"))},
        "flutterwave": {"enabled": bool(os.getenv("FLUTTERWAVE_SECRET_KEY"))},
        "paystack": {"enabled": bool(os.getenv("PAYSTACK_SECRET_KEY"))},
        "paddle": {"enabled": bool(os.getenv("PADDLE_API_KEY")), "adapter_ready": True},
    }


def billing_analytics(organization_id: int) -> Dict[str, Any]:
    db = SessionLocal()
    try:
        payments = db.query(PaymentTransaction).filter(PaymentTransaction.organization_id == organization_id).all()
        paid = [p for p in payments if p.status == "paid"]
        failed = [p for p in payments if p.status == "failed"]
        return {
            "total_payments": len(payments),
            "successful_payments": len(paid),
            "failed_payments": len(failed),
            "revenue_cents": sum(p.amount_cents for p in paid),
            "pending_payments": len([p for p in payments if p.status == "pending"]),
            "providers": sorted({p.provider for p in payments}),
        }
    finally:
        db.close()


def create_checkout_session(
    organization_id: int,
    user_id: int,
    provider: str,
    plan: str,
    billing_cycle: str = "monthly",
    currency: str = DEFAULT_CURRENCY,
    idempotency_key: str | None = None,
) -> Dict[str, Any]:
    provider = provider.lower()
    if provider not in {"paypal", "flutterwave", "paystack", "paddle"}:
        raise HTTPException(status_code=400, detail="Unsupported checkout provider")

    amount_cents = _amount_cents(plan, billing_cycle, currency)
    idempotency_key = idempotency_key or _new_reference("idem")

    db = SessionLocal()
    try:
        existing = _transaction_by_key(db, idempotency_key)
        if existing:
            return serialize_payment(existing)

        reference = _new_reference(provider.upper())
        checkout_url = f"https://checkout.example.com/{provider}?reference={reference}"
        tx = PaymentTransaction(
            organization_id=organization_id,
            user_id=user_id,
            provider=provider,
            method="checkout",
            plan=plan,
            billing_cycle=billing_cycle,
            currency=currency.upper(),
            amount_cents=amount_cents,
            status="pending" if amount_cents else "paid",
            idempotency_key=idempotency_key,
            provider_reference=reference,
            checkout_url=checkout_url,
            metadata_json={"mode": "provider_adapter", "server_validated_amount": amount_cents},
        )
        db.add(tx)
        db.add(BillingEvent(
            organization_id=organization_id,
            event_type=f"payment.{provider}.checkout_created",
            amount_cents=amount_cents,
            metadata_json={"reference": reference, "plan": plan, "billing_cycle": billing_cycle, "currency": currency},
        ))
        db.commit()
        db.refresh(tx)
        return serialize_payment(tx)
    finally:
        db.close()


async def initiate_mpesa_stk_push(
    organization_id: int,
    user_id: int,
    phone: str,
    plan: str,
    billing_cycle: str = "monthly",
    currency: str = "KES",
    idempotency_key: str | None = None,
    destination_phone: str | None = None,
) -> Dict[str, Any]:
    if currency.upper() != "KES":
        raise HTTPException(status_code=400, detail="M-Pesa payments must use KES")

    customer_phone = _normalize_phone(phone)
    destination_phone = destination_phone if destination_phone in PAYMENT_DESTINATIONS else PAYMENT_DESTINATIONS[0]
    amount_cents = _amount_cents(plan, billing_cycle, "KES")
    amount_kes = max(1, amount_cents // 100) if amount_cents else 0
    idempotency_key = idempotency_key or _new_reference("mpesa-idem")

    db = SessionLocal()
    try:
        existing = _transaction_by_key(db, idempotency_key)
        if existing:
            return serialize_payment(existing)

        reference = _new_reference("MPESA")
        tx = PaymentTransaction(
            organization_id=organization_id,
            user_id=user_id,
            provider="mpesa",
            method="stk_push",
            plan=plan,
            billing_cycle=billing_cycle,
            currency="KES",
            amount_cents=amount_cents,
            status="pending",
            idempotency_key=idempotency_key,
            provider_reference=reference,
            customer_phone=customer_phone,
            destination_phone=destination_phone,
            metadata_json={"server_validated_amount": amount_cents, "amount_kes": amount_kes},
        )
        db.add(tx)
        db.add(BillingEvent(
            organization_id=organization_id,
            event_type="payment.mpesa.stk_created",
            amount_cents=amount_cents,
            metadata_json={"reference": reference, "customer_phone": customer_phone, "destination_phone": destination_phone},
        ))
        db.commit()
        db.refresh(tx)
    finally:
        db.close()

    if not provider_status()["mpesa"]["enabled"]:
        payload = serialize_payment(tx)
        payload["message"] = "M-Pesa sandbox credentials are not configured. Transaction recorded as pending provider action."
        return payload

    # Daraja call is intentionally backend-only. In sandbox this can be enabled
    # by setting MPESA_* variables; failures leave the transaction pending.
    try:
        await _call_daraja_stk(customer_phone, amount_kes, reference)
    except Exception as exc:
        db = SessionLocal()
        try:
            db_tx = db.query(PaymentTransaction).filter(PaymentTransaction.provider_reference == reference).first()
            if db_tx:
                metadata = dict(db_tx.metadata_json or {})
                metadata["daraja_error"] = str(exc)[:300]
                db_tx.metadata_json = metadata
                db.commit()
        finally:
            db.close()

    return verify_payment(organization_id, reference)


async def _call_daraja_stk(customer_phone: str, amount_kes: int, reference: str) -> None:
    consumer_key = os.getenv("MPESA_CONSUMER_KEY", "")
    consumer_secret = os.getenv("MPESA_CONSUMER_SECRET", "")
    shortcode = os.getenv("MPESA_SHORTCODE", "")
    passkey = os.getenv("MPESA_PASSKEY", "")
    callback_url = os.getenv("MPESA_CALLBACK_URL", "")
    environment = os.getenv("MPESA_ENVIRONMENT", "sandbox")
    base_url = "https://sandbox.safaricom.co.ke" if environment == "sandbox" else "https://api.safaricom.co.ke"
    if not all([consumer_key, consumer_secret, shortcode, passkey, callback_url]):
        return

    async with httpx.AsyncClient(timeout=20) as client:
        token_res = await client.get(
            f"{base_url}/oauth/v1/generate?grant_type=client_credentials",
            auth=(consumer_key, consumer_secret),
        )
        token_res.raise_for_status()
        token = token_res.json()["access_token"]
        timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        password = base64.b64encode(f"{shortcode}{passkey}{timestamp}".encode()).decode()
        res = await client.post(
            f"{base_url}/mpesa/stkpush/v1/processrequest",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "BusinessShortCode": shortcode,
                "Password": password,
                "Timestamp": timestamp,
                "TransactionType": "CustomerPayBillOnline",
                "Amount": amount_kes,
                "PartyA": customer_phone,
                "PartyB": shortcode,
                "PhoneNumber": customer_phone,
                "CallBackURL": callback_url,
                "AccountReference": reference,
                "TransactionDesc": "AI Recruitment SaaS subscription",
            },
        )
        res.raise_for_status()


def verify_payment(organization_id: int, reference: str) -> Dict[str, Any]:
    db = SessionLocal()
    try:
        tx = db.query(PaymentTransaction).filter(
            PaymentTransaction.organization_id == organization_id,
            PaymentTransaction.provider_reference == reference,
        ).first()
        if not tx:
            raise HTTPException(status_code=404, detail="Payment not found")
        return serialize_payment(tx)
    finally:
        db.close()


def cancel_subscription(organization_id: int) -> Dict[str, Any]:
    db = SessionLocal()
    try:
        subscription = db.query(Subscription).filter(Subscription.organization_id == organization_id).first()
        if not subscription:
            ensure_subscription(organization_id)
            subscription = db.query(Subscription).filter(Subscription.organization_id == organization_id).first()
        subscription.status = "cancelled"
        subscription.updated_at = datetime.utcnow()
        db.add(BillingEvent(organization_id=organization_id, event_type="subscription.cancelled"))
        db.commit()
        return subscription_overview(organization_id)
    finally:
        db.close()


async def handle_provider_webhook(provider: str, payload: dict, headers: dict | None = None) -> Dict[str, Any]:
    provider = provider.lower()
    if provider not in {"mpesa", "paypal", "flutterwave", "paystack", "paddle"}:
        raise HTTPException(status_code=400, detail="Unsupported payment provider")
    _verify_webhook_signature(provider, payload, headers or {})
    reference, status_value, amount_cents = _extract_webhook_result(provider, payload)
    if not reference:
        raise HTTPException(status_code=400, detail="Missing provider reference")

    db = SessionLocal()
    try:
        tx = db.query(PaymentTransaction).filter(PaymentTransaction.provider_reference == reference).first()
        if not tx:
            raise HTTPException(status_code=404, detail="Unknown payment reference")
        if amount_cents is not None and int(amount_cents) != int(tx.amount_cents):
            tx.status = "failed"
            tx.metadata_json = {**(tx.metadata_json or {}), "amount_mismatch": amount_cents}
            db.commit()
            raise HTTPException(status_code=400, detail="Payment amount mismatch")
        if tx.status == "paid":
            return serialize_payment(tx)
        tx.status = status_value
        tx.updated_at = datetime.utcnow()
        if status_value == "paid":
            tx.completed_at = datetime.utcnow()
            subscription = db.query(Subscription).filter(Subscription.organization_id == tx.organization_id).first()
            if not subscription:
                ensure_subscription(tx.organization_id, plan=tx.plan)
                subscription = db.query(Subscription).filter(Subscription.organization_id == tx.organization_id).first()
            subscription.plan = tx.plan
            subscription.status = "active"
            subscription.limits_json = DEFAULT_LIMITS[tx.plan]
            subscription.updated_at = datetime.utcnow()
        db.add(BillingEvent(
            organization_id=tx.organization_id,
            event_type=f"payment.{provider}.{status_value}",
            amount_cents=tx.amount_cents,
            metadata_json={"reference": reference},
        ))
        db.commit()
        db.refresh(tx)
        return serialize_payment(tx)
    finally:
        db.close()


def _verify_webhook_signature(provider: str, payload: dict, headers: dict) -> None:
    raw = str(payload).encode()
    if provider == "flutterwave":
        secret = os.getenv("FLUTTERWAVE_WEBHOOK_SECRET", "")
        signature = headers.get("verif-hash") or headers.get("Verif-Hash")
        if secret and signature != secret:
            raise HTTPException(status_code=401, detail="Invalid Flutterwave webhook signature")
    elif provider == "paystack":
        secret = os.getenv("PAYSTACK_WEBHOOK_SECRET", "")
        signature = headers.get("x-paystack-signature") or headers.get("X-Paystack-Signature")
        expected = hmac.new(secret.encode(), raw, hashlib.sha512).hexdigest() if secret else ""
        if secret and not hmac.compare_digest(signature or "", expected):
            raise HTTPException(status_code=401, detail="Invalid Paystack webhook signature")
    elif provider == "paddle":
        secret = os.getenv("PADDLE_WEBHOOK_SECRET", "")
        if secret and not headers.get("Paddle-Signature"):
            raise HTTPException(status_code=401, detail="Missing Paddle webhook signature")
    elif provider == "paypal":
        # Full PayPal verification requires a server-to-server API call with PAYPAL_WEBHOOK_ID.
        if os.getenv("PAYPAL_WEBHOOK_ID") and not headers.get("Paypal-Transmission-Id"):
            raise HTTPException(status_code=401, detail="Missing PayPal webhook headers")


def _extract_webhook_result(provider: str, payload: dict) -> tuple[str | None, str, int | None]:
    if provider == "mpesa":
        stk = payload.get("Body", {}).get("stkCallback", {})
        metadata = {item.get("Name"): item.get("Value") for item in stk.get("CallbackMetadata", {}).get("Item", [])}
        reference = metadata.get("AccountReference") or payload.get("reference")
        amount = metadata.get("Amount")
        return reference, "paid" if int(stk.get("ResultCode", 1)) == 0 else "failed", int(float(amount) * 100) if amount else None
    data = payload.get("data", payload)
    reference = data.get("reference") or data.get("tx_ref") or data.get("id") or payload.get("reference")
    status_value = str(data.get("status") or payload.get("status") or "").lower()
    paid = status_value in {"success", "successful", "completed", "paid", "approved"}
    amount = data.get("amount")
    return str(reference) if reference else None, "paid" if paid else "failed", int(float(amount) * 100) if amount is not None else None


def serialize_billing_event(event: BillingEvent) -> Dict[str, Any]:
    return {
        "id": event.id,
        "event_type": event.event_type,
        "quantity": event.quantity,
        "amount_cents": event.amount_cents,
        "metadata": event.metadata_json or {},
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


def serialize_invoice(invoice: Invoice) -> Dict[str, Any]:
    return {
        "id": invoice.id,
        "invoice_number": invoice.invoice_number,
        "status": invoice.status,
        "amount_cents": invoice.amount_cents,
        "line_items": invoice.line_items or [],
        "issued_at": invoice.issued_at.isoformat() if invoice.issued_at else None,
        "due_at": invoice.due_at.isoformat() if invoice.due_at else None,
    }


def serialize_payment(payment: PaymentTransaction) -> Dict[str, Any]:
    return {
        "id": payment.id,
        "provider": payment.provider,
        "method": payment.method,
        "plan": payment.plan,
        "billing_cycle": payment.billing_cycle,
        "currency": payment.currency,
        "amount_cents": payment.amount_cents,
        "status": payment.status,
        "reference": payment.provider_reference,
        "checkout_url": payment.checkout_url,
        "customer_phone": payment.customer_phone,
        "destination_phone": payment.destination_phone,
        "metadata": payment.metadata_json or {},
        "created_at": payment.created_at.isoformat() if payment.created_at else None,
        "updated_at": payment.updated_at.isoformat() if payment.updated_at else None,
        "completed_at": payment.completed_at.isoformat() if payment.completed_at else None,
    }
