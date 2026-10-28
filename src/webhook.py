"""Stripe webhook service.  Run:  uvicorn src.webhook:app --port 8502
Upgrades/downgrades plans from signed Stripe events. Unsigned or stale requests are rejected."""
import hashlib
import hmac
import json
import time

from fastapi import FastAPI, HTTPException, Request

from config import settings
from src import db

app = FastAPI(title="CareerReady webhooks")
ACTIVE, ENDED = {"active", "trialing"}, {"canceled", "unpaid", "incomplete_expired"}


def verify_signature(payload: bytes, header: str, secret: str, tolerance: int = 300, now: float | None = None) -> bool:
    if not secret or not header:
        return False
    try:
        parts = [p.split("=", 1) for p in header.split(",")]
        stamp = next(v for k, v in parts if k == "t")
        sigs = [v for k, v in parts if k == "v1"]
        expected = hmac.new(secret.encode(), f"{stamp}.".encode() + payload, hashlib.sha256).hexdigest()
        fresh = abs((now or time.time()) - int(stamp)) <= tolerance
    except (ValueError, StopIteration):
        return False
    return fresh and any(hmac.compare_digest(expected, s) for s in sigs)


def handle_event(event: dict) -> str:
    kind, obj = event.get("type", ""), event.get("data", {}).get("object", {})
    customer = obj.get("customer")
    if kind == "checkout.session.completed":
        uid = obj.get("client_reference_id")
        if uid and db.get_user(uid):
            db.set_plan(uid, "pro", customer)
            return "upgraded"
        return "no matching user"
    if kind in ("customer.subscription.deleted", "customer.subscription.updated"):
        user = db.find_user_by_customer(customer) if customer else None
        if not user:
            return "no matching user"
        if kind.endswith("deleted") or obj.get("status") in ENDED:
            db.set_plan(user["id"], "free")
            return "downgraded"
        if obj.get("status") in ACTIVE:
            db.set_plan(user["id"], "pro")
            return "upgraded"
    return "ignored"


@app.get("/healthz")
def health():
    return {"ok": True}


@app.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    if not settings.STRIPE_WEBHOOK_SECRET:
        raise HTTPException(503, "Webhook secret not configured")
    payload = await request.body()
    if not verify_signature(payload, request.headers.get("stripe-signature", ""), settings.STRIPE_WEBHOOK_SECRET):
        raise HTTPException(400, "Bad signature")
    event = json.loads(payload)
    result = handle_event(event)           # idempotent, so a retry after a crash is safe
    if db.seen_webhook(event.get("id", "")):
        return {"status": "duplicate"}
    return {"status": result}
