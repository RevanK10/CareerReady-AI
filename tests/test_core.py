import pytest

from src.agents import orchestrator
from src.agents.orchestrator import PipelineIncomplete
from src.errors import PermanentError
from src.services.scraper import ScrapeError
from tests.conftest import PROFILE

JD = "We need a backend engineer who knows Python and APIs. " * 5


def test_full_pipeline(fake_llm, store):
    job_id = store.add_job(description=JD)
    steps = []
    orchestrator.run_pipeline(store, job_id, PROFILE, on_step=lambda s, st: steps.append((s, st)))
    job = store.get_job(job_id)
    assert job["status"] == "Ready to review"
    assert (job["title"], job["company"], job["fit_score"]) == ("Backend Engineer", "Acme", 82)
    assert job["resume_md"].startswith("# Jane") and job["cover_letter"].startswith("Dear")
    assert ("Scoring your fit", "done") in steps
    assert {e["kind"] for e in store.list_events(job_id)} >= {"fit", "resume", "cover_letter"}


def test_pipeline_keeps_partial_progress(fake_llm, store, monkeypatch):
    def boom(*a):
        raise RuntimeError("boom")
    monkeypatch.setattr(orchestrator, "write_cover_letter", boom)
    job_id = store.add_job(description=JD)
    with pytest.raises(PipelineIncomplete, match="cover letter"):
        orchestrator.run_pipeline(store, job_id, PROFILE)
    job = store.get_job(job_id)
    assert job["status"] != "Ready to review"            # not marked ready
    assert job["resume_md"] and not job["cover_letter"]   # finished steps are kept


def test_pipeline_retry_only_redoes_missing_steps(fake_llm, store, monkeypatch):
    real = orchestrator.write_cover_letter
    monkeypatch.setattr(orchestrator, "write_cover_letter", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    job_id = store.add_job(description=JD)
    with pytest.raises(PipelineIncomplete):
        orchestrator.run_pipeline(store, job_id, PROFILE)
    calls_after_first = len(fake_llm["calls"])
    monkeypatch.setattr(orchestrator, "write_cover_letter", real)
    orchestrator.run_pipeline(store, job_id, PROFILE)
    assert len(fake_llm["calls"]) == calls_after_first + 1   # only the cover letter was regenerated
    assert store.get_job(job_id)["status"] == "Ready to review"


def test_pipeline_needs_resume_and_posting(fake_llm, store, monkeypatch):
    job_id = store.add_job(description=JD)
    with pytest.raises(PermanentError, match="Profile"):
        orchestrator.run_pipeline(store, job_id, {**PROFILE, "resume_text": ""})

    def nope(url):
        raise ScrapeError("nope")
    monkeypatch.setattr(orchestrator, "scrape_job_posting", nope)
    url_job = store.add_job(url="https://x.com/job")
    with pytest.raises(PermanentError, match="Paste the job description"):
        orchestrator.run_pipeline(store, url_job, PROFILE)
    assert store.get_job(url_job)["status"] == "Needs attention"


def test_profile_roundtrip_and_status_counts(store):
    store.save_profile({"full_name": " Jane ", "email": "j@x.com", "junk": "ignored"})
    p = store.get_profile()
    assert p["full_name"] == "Jane" and "junk" not in p
    a = store.add_job(title="A")
    store.add_job(title="B")
    store.update_job(a, status="Applied")
    assert store.status_counts()["Applied"] == 1 and store.status_counts()["Saved"] == 1
    store.delete_job(a)
    assert [j["title"] for j in store.list_jobs()] == ["B"]
