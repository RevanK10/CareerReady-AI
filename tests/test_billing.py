import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

from config import settings
from src import billing, db, webhook

SECRET = "whsec_test"


def sign(body: bytes, secret=SECRET, ts=None) -> str:
    ts = ts or int(time.time())
    return f"t={ts},v1=" + hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", SECRET)
    return TestClient(webhook.app)


def post(client, event, header=None):
    body = json.dumps(event).encode()
    return client.post("/stripe/webhook", content=body, headers={"stripe-signature": header or sign(body)})


def test_signature_checks():
    body = b'{"a":1}'
    assert webhook.verify_signature(body, sign(body), SECRET)
    assert not webhook.verify_signature(body, sign(body, "other"), SECRET)                  # wrong secret
    assert not webhook.verify_signature(b'{"a":2}', sign(body), SECRET)                      # tampered body
    assert not webhook.verify_signature(body, sign(body, ts=int(time.time()) - 3600), SECRET)  # replay
    assert not webhook.verify_signature(body, "garbage", SECRET) and not webhook.verify_signature(body, "", SECRET)
    assert not webhook.verify_signature(body, sign(body, ""), "")                            # no secret configured


def test_rejects_unsigned_and_unconfigured(client, monkeypatch):
    assert client.post("/stripe/webhook", content=b"{}").status_code == 400
    assert client.post("/stripe/webhook", content=b"{}", headers={"stripe-signature": "t=1,v1=x"}).status_code == 400
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", "")
    assert client.post("/stripe/webhook", content=b"{}").status_code == 503


def test_checkout_upgrades_and_cancellation_downgrades(client):
    db.upsert_user("alice")
    ev = {"id": "evt_1", "type": "checkout.session.completed",
          "data": {"object": {"client_reference_id": "alice", "customer": "cus_9"}}}
    assert post(client, ev).json() == {"status": "upgraded"}
    assert db.get_user("alice")["plan"] == "pro" and db.get_user("alice")["stripe_customer_id"] == "cus_9"
    assert post(client, ev).json() == {"status": "duplicate"}                                # Stripe retried
    gone = {"id": "evt_2", "type": "customer.subscription.deleted", "data": {"object": {"customer": "cus_9"}}}
    assert post(client, gone).json() == {"status": "downgraded"} and db.get_user("alice")["plan"] == "free"


def test_subscription_updates_and_unknowns(client):
    db.upsert_user("bob"); db.set_plan("bob", "pro", "cus_b")
    ev = lambda i, s: {"id": i, "type": "customer.subscription.updated", "data": {"object": {"customer": "cus_b", "status": s}}}
    assert post(client, ev("e1", "unpaid")).json()["status"] == "downgraded" and db.get_user("bob")["plan"] == "free"
    assert post(client, ev("e2", "active")).json()["status"] == "upgraded" and db.get_user("bob")["plan"] == "pro"
    assert post(client, {"id": "e3", "type": "checkout.session.completed",
                         "data": {"object": {"client_reference_id": "ghost"}}}).json()["status"] == "no matching user"
    assert post(client, {"id": "e4", "type": "invoice.created", "data": {"object": {}}}).json()["status"] == "ignored"


def test_limits_apply_only_when_hosted(monkeypatch):
    db.upsert_user("u")
    assert billing.check("u", "jobs") == (True, 0, None)                                      # local: unlimited
    monkeypatch.setattr(settings, "HOSTED", True)
    for _ in range(5):
        assert billing.check("u", "jobs")[0]; billing.consume("u", "jobs")
    ok, used, limit = billing.check("u", "jobs")
    assert (ok, used, limit) == (False, 5, 5) and "5 of 5" in billing.denial_message("u", "jobs")
    billing.refund("u", "jobs"); assert billing.check("u", "jobs")[0]
    assert not billing.check("u", "submits")[0] and "doesn't include" in billing.denial_message("u", "submits")
    db.set_plan("u", "pro"); assert billing.check("u", "submits")[0] and billing.check("u", "jobs")[2] == 100


def test_upgrade_url_carries_user_id(monkeypatch):
    assert billing.upgrade_url("a/b") == ""
    monkeypatch.setattr(settings, "STRIPE_PAYMENT_LINK", "https://buy.stripe.com/abc")
    assert billing.upgrade_url("a/b", "x@y.com") == "https://buy.stripe.com/abc?client_reference_id=a%2Fb&prefilled_email=x%40y.com"
