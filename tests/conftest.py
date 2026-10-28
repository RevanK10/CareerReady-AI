import json
import pytest

from config import settings
from src import db
from src.services import llm_client
from src.services.llm_client import LLMClient

PROFILE = {**db.PROFILE_FIELDS, "full_name": "Jane Q Doe", "email": "jane@example.com", "phone": "555-0100",
           "linkedin": "https://linkedin.com/in/jane", "location": "Atlanta, GA", "work_authorization": "US citizen",
           "needs_sponsorship": "No", "resume_text": "Jane Doe. Python engineer. " * 20}


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    """Fresh database per test; local single-user mode unless a test overrides it."""
    monkeypatch.setattr(settings, "AUTH_MODE", "local")
    monkeypatch.setattr(settings, "HOSTED", False)
    monkeypatch.setattr(settings, "ENCRYPTION_KEY", "")
    db.configure(f"sqlite:///{tmp_path / 't.db'}")
    yield
    db.engine().dispose()


@pytest.fixture
def store():
    db.upsert_user("local")
    return db.Store("local")


@pytest.fixture
def fake_llm(monkeypatch):
    """Route every model call through a function the test controls."""
    state = {"calls": []}
    monkeypatch.setattr(llm_client, "OPENROUTER_API_KEY", "test-key")

    def chat(self, messages, temperature=0.7):
        prompt = messages[-1]["content"]
        state["calls"].append(prompt)
        if "Assess how well" in prompt:
            return json.dumps({"title": "Backend Engineer", "company": "Acme", "score": "82", "summary": "Good.",
                               "strengths": ["Python"], "gaps": ["Go"], "red_flags": []})
        if "MASTER RESUME" in prompt:
            return "# Jane Doe\n## Experience\n- Built APIs\n=====NOTES=====\n### What changed\n- x"
        if "Write a cover letter" in prompt:
            return "Dear Hiring Team,\nHello.\nJane Doe"
        if "You fill in a job application" in prompt:
            return state.get("answers", "{}")
        return "ok"

    monkeypatch.setattr(LLMClient, "chat", chat)
    return state
