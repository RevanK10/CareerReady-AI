import json

import pytest

from config import settings
from src import billing, db, worker
from src.agents import apply_agent, orchestrator
from src.errors import PermanentError, QuotaError
from src.services import forms, tasks
from src.services.scraper import ScrapeError
from tests.conftest import PROFILE
from tests.fixtures import GREENHOUSE, LEVER

JD = "We need a backend engineer who knows Python and APIs. " * 5
FORM = {"url": "https://jobs.lever.co/acme/1/apply", "ats": "lever", "fields": forms.parse_form(LEVER), "captcha": False}


@pytest.fixture
def clock(monkeypatch):
    """Time that jumps forward on every call, so retry back-off never blocks a test."""
    it = iter(range(2 * 10**9, 10**10, 100))
    monkeypatch.setattr(db, "_now", lambda: next(it))


@pytest.fixture
def user(store):
    store.save_profile(PROFILE)
    return store


def drain():
    n = 0
    while worker.process_one():
        n += 1
        assert n < 50
    return n


def job_with_prepared_form(store, form=FORM, url="https://jobs.lever.co/acme/1"):
    jid = store.add_job(title="Dev", company="Acme", url=url, description=JD, resume_md="# Jane\n- x", status="Ready to review")
    tid = store.enqueue("prepare_application", jid, {"url": url})
    db.claim_task()
    db.finish_task(tid, {"form": form, "plan": {}})
    return jid, tid


# ------------------------------------------------------------------ pipeline
def test_job_runs_in_background_and_ends_ready(user, fake_llm):
    jid = tasks.submit_job(user, user.get_profile(), description=JD)
    assert user.get_job(jid)["status"] == "Processing"                       # UI returns instantly
    assert user.latest_task("process_job", jid)["status"] == "queued"
    drain()
    job = user.get_job(jid)
    assert job["status"] == "Ready to review" and (job["title"], job["fit_score"]) == ("Backend Engineer", 82)
    assert job["resume_md"].startswith("# Jane") and job["cover_letter"].startswith("Dear")
    assert user.latest_task("process_job", jid)["status"] == "done"
    assert {e["kind"] for e in user.list_events(jid)} >= {"created", "fit", "resume", "cover_letter"}


def test_retry_only_redoes_missing_steps(user, fake_llm, monkeypatch, clock):
    real, boom = orchestrator.write_cover_letter, {"left": 1}
    def flaky(profile, job):
        if boom["left"]:
            boom["left"] -= 1
            raise RuntimeError("model hiccup")
        return real(profile, job)
    monkeypatch.setattr(orchestrator, "write_cover_letter", flaky)
    jid = tasks.submit_job(user, user.get_profile(), description=JD)
    assert worker.process_one()
    t = user.latest_task("process_job", jid)
    assert t["status"] == "queued" and user.get_job(jid)["resume_md"] and user.get_job(jid)["status"] == "Processing"
    drain()
    assert user.get_job(jid)["status"] == "Ready to review"
    calls = fake_llm["calls"]
    assert sum("Assess how well" in c for c in calls) == 1 and sum("MASTER RESUME" in c for c in calls) == 1   # not repeated


def test_gives_up_cleanly_and_refunds_quota(user, fake_llm, monkeypatch, clock):
    monkeypatch.setattr(settings, "HOSTED", True)
    for name in ("analyze_fit", "tailor_resume", "write_cover_letter"):
        monkeypatch.setattr(orchestrator, name, lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    jid = tasks.submit_job(user, user.get_profile(), description=JD)
    assert user.usage()["jobs"] == 1
    drain()
    assert user.get_job(jid)["status"] == "Needs attention" and user.latest_task("process_job", jid)["status"] == "failed"
    assert user.usage()["jobs"] == 0                                         # nothing produced, nothing charged


def test_unreadable_posting_fails_fast(user, fake_llm, monkeypatch):
    monkeypatch.setattr(orchestrator, "scrape_job_posting", lambda url: (_ for _ in ()).throw(ScrapeError("needs login")))
    jid = tasks.submit_job(user, user.get_profile(), url="https://acme.example/job")
    drain()
    t = user.latest_task("process_job", jid)
    assert t["attempts"] == 1 and t["status"] == "failed" and "Paste the job description" in t["error"]
    assert user.get_job(jid)["status"] == "Needs attention"


def test_submit_job_validation(user):
    with pytest.raises(ValueError, match="resume"):
        tasks.submit_job(user, {**PROFILE, "resume_text": ""}, description=JD)
    with pytest.raises(ValueError, match="link"):
        tasks.submit_job(user, user.get_profile())


# ---------------------------------------------------------------- quotas
def test_free_plan_quota_enforced_server_side(user, fake_llm, monkeypatch):
    monkeypatch.setattr(settings, "HOSTED", True)
    for _ in range(5):
        tasks.submit_job(user, user.get_profile(), description=JD)
    with pytest.raises(QuotaError, match="5 of 5"):
        tasks.submit_job(user, user.get_profile(), description=JD)
    assert len(user.list_jobs()) == 5                                        # the denied job was never created


def test_request_run_rules(user, monkeypatch):
    jid, tid = job_with_prepared_form(user)
    with pytest.raises(ValueError, match="approve"):
        tasks.request_run(user, jid, tid, {}, "submit", approved=False)
    monkeypatch.setattr(settings, "HOSTED", True)
    with pytest.raises(ValueError, match="isn't available"):
        tasks.request_run(user, jid, tid, {}, "handover", approved=False)    # no visible browser on a server
    with pytest.raises(QuotaError, match="doesn't include"):
        tasks.request_run(user, jid, tid, {}, "submit", approved=True)       # free plan can't submit
    db.set_plan(user.uid, "pro")
    assert tasks.request_run(user, jid, tid, {}, "submit", approved=True)
    assert user.usage()["submits"] == 1
    other = user.add_job(title="Other")
    with pytest.raises(ValueError, match="Prepare"):
        tasks.request_run(user, other, tid, {}, "preview", approved=False)   # prepared form belongs to another job


def test_cannot_use_another_users_prepared_form():
    db.upsert_user("alice"); db.upsert_user("bob")
    a, b = db.Store("alice"), db.Store("bob")
    jid, tid = job_with_prepared_form(a)
    bj = b.add_job(title="mine")
    with pytest.raises(ValueError, match="Prepare"):
        tasks.request_run(b, bj, tid, {}, "preview", approved=False)


# ------------------------------------------------------------ apply agent
def stub_browser(monkeypatch, tmp_path, **extra):
    shot = tmp_path / "p.png"; shot.write_bytes(b"\x89PNG fake")
    seen = []
    def run(url, fields, answers, files, mode, out_dir):
        seen.append((mode, {k: v["value"] for k, v in answers.items()}, sorted(files)))
        return {"ok": True, "report": [], "screenshot": str(shot), "submitted": mode == "submit", **extra}
    monkeypatch.setattr(apply_agent, "run_application", run)
    return seen


def test_prepare_then_preview_then_confirmed_submit(user, fake_llm, monkeypatch, tmp_path):
    monkeypatch.setattr(apply_agent, "load_form", lambda url: FORM)
    fake_llm["answers"] = json.dumps({"q5": {"value": "No", "source": "profile"}})
    jid = user.add_job(title="Dev", company="Acme", url="https://jobs.lever.co/acme/1", description=JD,
                       resume_md="# Jane\n- x", status="Ready to review")
    ptid = tasks.request_prepare(user, jid, "https://jobs.lever.co/acme/1")
    drain()
    prep = user.get_task(ptid)
    assert prep["status"] == "done" and prep["result"]["plan"]["q1"]["value"] == "Jane Q Doe"
    answers = {k: v["value"] for k, v in prep["result"]["plan"].items()}

    seen = stub_browser(monkeypatch, tmp_path, confirmed=True)
    rid = tasks.request_run(user, jid, ptid, answers, "preview", approved=False); drain()
    res = user.get_task(rid)["result"]
    assert seen[0][0] == "preview" and res["screenshot_b64"] and user.get_job(jid)["status"] == "Ready to review"

    rid = tasks.request_run(user, jid, ptid, answers, "submit", approved=True); drain()
    assert seen[1][0] == "submit" and seen[1][2] == ["resume"]
    assert user.get_job(jid)["status"] == "Applied"                          # site confirmed, so it's marked automatically


def test_worker_refuses_to_submit_when_answers_incomplete(user, fake_llm, monkeypatch, tmp_path):
    jid, ptid = job_with_prepared_form(user)
    seen = stub_browser(monkeypatch, tmp_path)
    rid = tasks.request_run(user, jid, ptid, {"q1": "Jane", "q2": "j@x.com"}, "submit", approved=True)   # sponsorship missing
    drain()
    t = user.get_task(rid)
    assert t["status"] == "failed" and "sponsorship" in t["error"] and seen == []                       # browser never opened


def test_worker_refuses_to_submit_past_a_captcha(user, monkeypatch, tmp_path):
    form = {**FORM, "captcha": True}
    jid, ptid = job_with_prepared_form(user, form)
    seen = stub_browser(monkeypatch, tmp_path)
    full = {"q1": "Jane", "q2": "j@x.com", "q5": "No"}
    rid = tasks.request_run(user, jid, ptid, full, "submit", approved=True); drain()
    assert "CAPTCHA" in user.get_task(rid)["error"] and seen == []


def test_unapproved_submit_cannot_slip_past_the_worker(user, monkeypatch, tmp_path):
    jid, ptid = job_with_prepared_form(user)
    seen = stub_browser(monkeypatch, tmp_path)
    rid = user.enqueue("run_application", jid, {"prepare_task_id": ptid, "mode": "submit", "approved": False,
                                                "answers": {"q1": "Jane", "q2": "j@x.com", "q5": "No"}})  # bypassing the service layer
    drain()
    assert user.get_task(rid)["status"] == "failed" and seen == []


def test_failed_run_refunds_the_preview(user, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "HOSTED", True)
    jid, ptid = job_with_prepared_form(user)
    monkeypatch.setattr(apply_agent, "run_application", lambda *a, **k: {"ok": False, "error": "browser crashed"})
    tasks.request_run(user, jid, ptid, {}, "preview", approved=False)
    assert user.usage()["previews"] == 1
    drain()
    assert user.usage()["previews"] == 0


# ------------------------------------------------------------- autopilot
@pytest.mark.parametrize("threshold, url, expect", [
    ("70", "https://jobs.lever.co/acme/1", True), ("90", "https://jobs.lever.co/acme/1", False),
    ("", "https://jobs.lever.co/acme/1", False), ("70", "https://www.linkedin.com/jobs/view/1", False)])
def test_auto_prepare_after_pipeline(user, fake_llm, threshold, url, expect):
    user.save_profile({**PROFILE, "auto_prepare_min_score": threshold})
    jid = tasks.submit_job(user, user.get_profile(), url=url, description=JD)
    assert worker.process_one()
    assert (user.latest_task("prepare_application", jid) is not None) is expect
