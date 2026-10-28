import json
from pathlib import Path

from streamlit.testing.v1 import AppTest

from config import settings
from src import auth, db, worker
from src.agents import apply_agent
from tests.conftest import PROFILE
from tests.fixtures import LEVER
from tests.test_workflow import FORM

ROOT = Path(__file__).resolve().parent.parent
PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478da63fcffff3f0300050001a5f645400000000049454e44ae426082")
JD = "We need a backend engineer who knows Python and APIs. " * 5


def page(name="jobs"):
    return AppTest.from_file(str(ROOT / "views" / f"{name}.py"), default_timeout=30)


def open_job(jid, name="jobs"):
    """Start a session, then select a job (the first run initialises the session for the user)."""
    at = page(name).run()
    at.session_state["job_id"] = jid
    return at.run()


def drain():
    while worker.process_one():
        pass


def test_async_workflow_end_to_end(store, fake_llm, monkeypatch, tmp_path):
    store.save_profile(PROFILE)
    at = page().run()
    assert not at.exception

    # add a job: returns immediately, work happens in the background
    at.text_area(key="add_text").set_value(JD)
    at.button[0].click().run()
    assert not at.exception, at.exception
    jid = store.list_jobs()[0]["id"]
    assert store.get_job(jid)["status"] == "Processing"
    assert any("Waiting for a worker" in m.value for m in at.markdown)       # live progress, no tabs yet
    assert len(at.tabs) == 0

    drain(); at.run()                                                         # worker finishes; page reload shows the result
    assert store.get_job(jid)["status"] == "Ready to review" and len(at.tabs) == 5
    store.update_job(jid, url="https://jobs.lever.co/acme/1")

    # prepare the application in the background
    monkeypatch.setattr(apply_agent, "load_form", lambda url: FORM)
    fake_llm["answers"] = json.dumps({"q5": {"value": "No", "source": "profile"}})
    at = open_job(jid)
    at.button(key=f"prep_{jid}").click().run()
    assert any("Waiting for a worker" in m.value for m in at.markdown)
    drain(); at.run()
    assert at.text_input(key=f"ans_{jid}_q1").value == "Jane Q Doe"
    assert at.selectbox(key=f"ans_{jid}_q5").value == "No"

    # the user edits an answer; the edit is what gets sent
    at.text_input(key=f"ans_{jid}_q4").set_value("https://linkedin.com/in/jane-edited").run()
    sent = []
    shot = tmp_path / "s.png"; shot.write_bytes(PNG)
    def fake_run(url, fields, answers, files, mode, out_dir):
        sent.append((mode, {k: v["value"] for k, v in answers.items()}))
        return {"ok": True, "report": [], "screenshot": str(shot), "submitted": mode == "submit", "confirmed": True}
    monkeypatch.setattr(apply_agent, "run_application", fake_run)

    at.button(key=f"run_{jid}").click().run(); drain(); at.run()              # preview
    assert sent[0][0] == "preview" and sent[0][1]["q4"].endswith("jane-edited")
    assert store.latest_task("run_application", jid)["result"]["screenshot_b64"]      # screenshot persisted for display

    modes = at.radio(key=f"mode_{jid}"); modes.set_value(modes.options[-1]).run()   # submit mode
    assert at.button(key=f"run_{jid}").disabled                                # locked until approved
    at.checkbox(key=f"ok_{jid}").check().run()
    at.button(key=f"run_{jid}").click().run(); drain(); at.run()
    assert sent[1][0] == "submit" and store.get_job(jid)["status"] == "Applied"
    assert any("Submitted" in s.value for s in at.success)


def hosted_as(monkeypatch, ident):
    monkeypatch.setattr(settings, "HOSTED", True)
    monkeypatch.setattr(auth, "_oidc_identity", lambda: ident)


def test_signed_out_visitor_gets_nothing(monkeypatch):
    hosted_as(monkeypatch, None)
    for name in ("home", "jobs", "profile", "account"):
        at = page(name).run()
        assert any("sign in" in w.value.lower() for w in at.warning), name
        assert len(at.markdown) == 0 or not any("Your search" in m.value for m in at.markdown)


def test_user_cannot_open_another_users_job(monkeypatch):
    db.upsert_user("bob"); bob = db.Store("bob")
    bob.save_profile(PROFILE)
    secret = bob.add_job(title="Bob's secret role", company="Hidden Inc", resume_md="BOB RESUME", status="Saved")
    hosted_as(monkeypatch, {"id": "alice", "email": "a@x.com", "name": "Alice"})
    alice = db.Store("alice"); db.upsert_user("alice"); alice.save_profile(PROFILE)
    at = open_job(secret)                                                     # forged selection
    assert not at.exception
    page_text = " ".join(m.value for m in at.markdown)
    assert "Bob's secret role" not in page_text and "BOB RESUME" not in page_text
    assert at.session_state["job_id"] is None and len(at.tabs) == 0


def test_switching_accounts_clears_session(monkeypatch):
    hosted_as(monkeypatch, {"id": "alice", "email": "a@x.com", "name": "A"})
    db.upsert_user("alice"); db.Store("alice").save_profile(PROFILE)
    at = page("profile").run()
    at.session_state["job_id"] = 99; at.session_state["feedback"] = "alice's private feedback"
    monkeypatch.setattr(auth, "_oidc_identity", lambda: {"id": "bob", "email": "b@x.com", "name": "B"})
    at.run()
    assert at.session_state["feedback"] is None and at.session_state["job_id"] is None
    assert db.get_user("bob")["email"] == "b@x.com"


def test_account_page_shows_plan_usage_and_upgrade(monkeypatch):
    monkeypatch.setattr(settings, "STRIPE_PAYMENT_LINK", "https://buy.stripe.com/x")
    hosted_as(monkeypatch, {"id": "alice", "email": "a@x.com", "name": "A"})
    db.upsert_user("alice"); db.add_usage("alice", "jobs", 2)
    at = page("account").run()
    assert not at.exception, at.exception
    texts = " ".join(p.text for p in at.get("progress"))
    assert "2 of 5" in texts and "not included" in texts                      # free plan: no submits


def test_delete_my_data_from_account_page(store):
    store.save_profile(PROFILE); store.add_job(title="x")
    at = page("account").run()
    at.text_input(key="del_confirm").set_value("DELETE").run()
    next(b for b in at.button if b.label == "Delete everything").click().run()
    assert store.list_jobs() == [] and store.get_profile()["resume_text"] == ""


def test_signed_out_landing_page_via_real_entrypoint(monkeypatch):
    hosted_as(monkeypatch, None)
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not at.exception, at.exception
    assert [b.label for b in at.button] == ["Sign in"]                         # nothing else is reachable
    assert not any("Your search" in m.value for m in at.markdown)


def test_signed_in_user_gets_the_app_via_real_entrypoint(monkeypatch):
    hosted_as(monkeypatch, {"id": "alice", "email": "a@x.com", "name": "A"})
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not at.exception, at.exception
    assert "Sign in" not in [b.label for b in at.button]
