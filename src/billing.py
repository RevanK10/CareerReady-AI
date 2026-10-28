"""Plans and usage limits. Limits apply only in hosted mode; local installs are unlimited."""
from urllib.parse import quote

from config import settings
from src import db

PLANS = {
    "free": {"name": "Free", "jobs": 5, "previews": 10, "submits": 0, "interviews": 3},
    "pro": {"name": "Pro", "jobs": 100, "previews": 300, "submits": 50, "interviews": 60},
}
LABELS = {"jobs": "jobs prepared", "previews": "application previews",
          "submits": "applications submitted", "interviews": "practice interviews"}


def plan_of(user_id: str) -> dict:
    user = db.get_user(user_id) or {}
    return {"key": user.get("plan", "free"), **PLANS.get(user.get("plan", "free"), PLANS["free"])}


def check(user_id: str, metric: str) -> tuple[bool, int, int | None]:
    """(allowed, used, limit). limit is None when unlimited."""
    used = db.get_usage(user_id).get(metric, 0)
    if not settings.HOSTED:
        return True, used, None
    limit = plan_of(user_id)[metric]
    return used < limit, used, limit


def consume(user_id: str, metric: str) -> None:
    db.add_usage(user_id, metric)


def refund(user_id: str, metric: str) -> None:
    db.add_usage(user_id, metric, -1)


def denial_message(user_id: str, metric: str) -> str:
    _, used, limit = check(user_id, metric)
    plan = plan_of(user_id)
    if limit == 0:
        return f"The {plan['name']} plan doesn't include {LABELS[metric]}. Upgrade to use this."
    return f"You've used {used} of {limit} {LABELS[metric]} this month on the {plan['name']} plan."


def upgrade_url(user_id: str, email: str = "") -> str:
    """Stripe Payment Link tagged with the user id so the webhook knows who paid."""
    if not settings.STRIPE_PAYMENT_LINK:
        return ""
    sep = "&" if "?" in settings.STRIPE_PAYMENT_LINK else "?"
    url = f"{settings.STRIPE_PAYMENT_LINK}{sep}client_reference_id={quote(user_id, safe='')}"
    return url + (f"&prefilled_email={quote(email)}" if email else "")
