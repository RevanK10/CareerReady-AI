"""Who is using the app. Hosted mode uses Streamlit's built-in OIDC login (st.login)."""
import streamlit as st

from config import settings


def _oidc_identity() -> dict | None:
    """Identity from the OIDC provider, or None when nobody is signed in."""
    user = st.user
    if not getattr(user, "is_logged_in", False):
        return None
    if user.get("email_verified") is False:        # never trust an unverified email
        return None
    return {"id": user.get("sub") or user.get("email") or "", "email": user.get("email") or "",
            "name": user.get("name") or ""}


def current_user() -> dict | None:
    if not settings.HOSTED:
        return {"id": "local", "email": "", "name": "You"}
    ident = _oidc_identity()
    return ident if ident and ident["id"] else None
