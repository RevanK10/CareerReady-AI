"""The job pipeline: posting -> fit score -> tailored resume -> cover letter -> ready to review.

Resumable: each step is skipped if its output already exists, so a retry after a failure only
redoes what's missing instead of starting over and paying for the model calls twice.
"""
import json
from typing import Callable

from src.agents.cover_letter_agent import write_cover_letter
from src.agents.fit_agent import analyze_fit
from src.agents.resume_agent import tailor_resume
from src.errors import PermanentError
from src.services.scraper import scrape_job_posting

Step = Callable[[str, str], None]  # (step label, "running" | "done" | "failed")


class PipelineIncomplete(Exception):
    """Some steps failed; the finished ones are saved. Worth retrying."""


def run_pipeline(store, job_id: int, profile: dict, on_step: Step | None = None) -> None:
    say = on_step or (lambda *_: None)
    job = store.get_job(job_id)
    if len(profile.get("resume_text", "")) < 100:
        raise PermanentError("Add your resume on the Profile page first.")

    if not job["description"].strip():
        say("Reading the job posting", "running")
        try:
            text = scrape_job_posting(job["url"])
        except Exception as e:
            store.update_job(job_id, status="Needs attention")
            store.log_event(job_id, "error", f"Reading the posting: {e}")
            raise PermanentError(f"{e} Paste the job description instead.") from e
        store.update_job(job_id, description=text)
        say("Reading the job posting", "done")
        job = store.get_job(job_id)
    description = job["description"]

    failures: list[str] = []

    def attempt(label: str, fn) -> None:
        say(label, "running")
        try:
            fn()
            say(label, "done")
        except Exception as e:
            say(label, "failed")
            failures.append(f"{label}: {e}")
            store.log_event(job_id, "error", f"{label}: {e}")

    def do_fit():
        fit = analyze_fit(profile["resume_text"], description)
        store.update_job(job_id, title=job["title"] or fit["title"], company=job["company"] or fit["company"],
                         fit_score=fit["score"], fit_json=json.dumps(fit))
        store.log_event(job_id, "fit", f"Fit score {fit['score']}")

    def do_resume():
        tailored = tailor_resume(profile["resume_text"], description)
        store.update_job(job_id, resume_md=tailored["resume"], notes=tailored["notes"])
        store.log_event(job_id, "resume", "Tailored resume generated")

    def do_letter():
        letter = write_cover_letter(profile, store.get_job(job_id))
        store.update_job(job_id, cover_letter=letter)
        store.log_event(job_id, "cover_letter", "Cover letter generated")

    if not job["fit_json"]:
        attempt("Scoring your fit", do_fit)
    if not job["resume_md"]:
        attempt("Tailoring your resume", do_resume)
    if not store.get_job(job_id)["cover_letter"] and store.get_job(job_id)["resume_md"]:
        attempt("Writing a cover letter", do_letter)

    final = store.get_job(job_id)
    if final["resume_md"] and final["cover_letter"]:
        store.update_job(job_id, status="Ready to review")
    if failures:
        raise PipelineIncomplete("; ".join(failures))
