import html
from datetime import datetime

import streamlit as st

from src import auth, db

DEFAULTS = {
    "job_id": None, "resume_result": None,
    "interview_agent": None, "feedback": None, "interview_context": "", "interview_job_id": None,
    "interview_role_prefill": "", "spoken": 0, "mic_n": 0, "replay": False,
}


def require_store():
    """Return (store, user) for the signed-in user, or stop the page. Every view starts here."""
    user = auth.current_user()
    if user is None:
        st.warning("Please sign in to continue.")
        st.stop()
    S = st.session_state
    if S.get("_uid") != user["id"]:      # a different person on this browser session: drop all old state
        S.clear()
        S["_uid"] = user["id"]
        db.upsert_user(user["id"], user["email"], user["name"])
    for key, value in DEFAULTS.items():
        S.setdefault(key, value)
    return db.Store(user["id"]), user


def page_head(title: str, sub: str = "") -> None:
    sub_html = f"<p>{html.escape(sub)}</p>" if sub else ""
    st.markdown(f'<div class="page-head"><h1>{html.escape(title)}</h1>{sub_html}</div>', unsafe_allow_html=True)


def chip(status: str) -> str:
    slug = status.lower().replace(" ", "-")
    return f'<span class="chip chip-{slug}">{html.escape(status)}</span>'


def when(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%b %d, %H:%M").replace(" 0", " ")


def job_name(job: dict) -> str:
    title, company = job.get("title") or "Untitled role", job.get("company")
    return f"{title} at {company}" if company else title


def score_html(score) -> str:
    if score is None:
        return '<span class="score none">–</span>'
    tone = "hi" if score >= 80 else "mid" if score >= 60 else "lo"
    return f'<span class="score {tone}">{score}</span>'


def go(page: str) -> None:
    st.switch_page(f"views/{page}.py")


@st.fragment(run_every=2)
def task_panel(task_id: int, waiting: str = "Working on it") -> None:
    """Live progress for a background task. Reloads the page when the task finishes."""
    store, _ = require_store()
    task = store.get_task(task_id)
    if task is None or task["status"] in ("done", "failed"):
        st.rerun()
    if task["status"] == "queued":
        text = "Retrying…" if task["attempts"] else "Waiting for a worker…"
    else:
        text = task["progress"] or waiting + "…"
    st.markdown(f'<div class="working"><span class="dot"></span>{html.escape(text)}</div>', unsafe_allow_html=True)
