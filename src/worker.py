"""Background worker:  python -m src.worker
Runs queued agent tasks. Safe to run several copies; tasks are claimed atomically."""
import base64
import logging
import os
import signal
import tempfile
import threading
import time

from src import billing, db
from src.agents import apply_agent
from src.agents.apply_agent import ApplyError
from src.agents.orchestrator import run_pipeline
from src.errors import PermanentError
from src.services import forms
from src.services.scraper import ScrapeError

log = logging.getLogger("careerready.worker")
MAX_SHOT_BYTES = 1_500_000


# ------------------------------------------------------------------ handlers
def handle_process_job(task: dict, store: db.Store) -> dict:
    job_id = task["job_id"]
    progress = lambda label, state: db.set_progress(task["id"], label + "…") if state == "running" else None
    run_pipeline(store, job_id, store.get_profile(), on_step=progress)
    job = store.get_job(job_id)
    # Agentic hand-off: prepare the application automatically when the fit clears the user's threshold.
    threshold = store.get_profile()["auto_prepare_min_score"]
    if (threshold.isdigit() and (job["fit_score"] or 0) >= int(threshold) and job["url"]
            and not forms.is_blocked(job["url"])):
        store.enqueue("prepare_application", job_id, {"url": job["url"], "auto": True})
        store.log_event(job_id, "auto_prepare", f"Fit {job['fit_score']} ≥ {threshold}: preparing the application")
    return {}


def handle_prepare_application(task: dict, store: db.Store) -> dict:
    job_id = task["job_id"]
    db.set_progress(task["id"], "Reading the application form…")
    job = store.get_job(job_id)
    form = apply_agent.load_form(task["payload"]["url"])
    db.set_progress(task["id"], "Drafting answers…")
    plan = apply_agent.plan_answers(store.get_profile(), job, form["fields"])
    store.log_event(job_id, "apply_prepared", f"{len(form['fields'])} fields ({form['ats']})")
    return {"form": form, "plan": plan}


def handle_run_application(task: dict, store: db.Store) -> dict:
    job_id, p = task["job_id"], task["payload"]
    mode = p["mode"]
    prep = store.get_task(p["prepare_task_id"])
    if not prep or prep["status"] != "done":
        raise PermanentError("The prepared form is missing. Prepare the application again.")
    form, profile, job = prep["result"]["form"], store.get_profile(), store.get_job(job_id)
    out_dir = tempfile.mkdtemp(prefix="careerready_")
    files = apply_agent.build_files(profile, job, form["fields"], out_dir)
    answers = {k: {"value": v} for k, v in p["answers"].items()}

    if mode == "submit":   # last line of defence, independent of the UI
        if p.get("approved") is not True:
            raise PermanentError("Not submitted: the application wasn't approved.")
        issues = apply_agent.validate(form["fields"], answers, files, form["captcha"])
        if issues:
            raise PermanentError("Not submitted: " + "; ".join(issues))

    db.set_progress(task["id"], "Working in the browser…")
    res = apply_agent.run_application(form["url"], form["fields"], answers, files, mode, out_dir)
    if not res.get("ok"):
        raise PermanentError(res.get("error", "The browser step failed."))

    shot = res.pop("screenshot", None)
    if shot and os.path.exists(shot) and os.path.getsize(shot) <= MAX_SHOT_BYTES:
        with open(shot, "rb") as fh:
            res["screenshot_b64"] = base64.b64encode(fh.read()).decode()
    res.pop("out_dir", None)
    res["mode"] = mode
    store.log_event(job_id, f"apply_{mode}", form["url"])
    if mode == "submit" and res.get("confirmed"):
        store.update_job(job_id, status="Applied")
        store.log_event(job_id, "status", "Applied (confirmed by the site)")
    return res


HANDLERS = {"process_job": handle_process_job, "prepare_application": handle_prepare_application,
            "run_application": handle_run_application}


# --------------------------------------------------------------------- loop
def _on_final_failure(task: dict, store: db.Store, error: str) -> None:
    """The task will not be retried: leave the job in a sensible state and give back unused quota."""
    if task["kind"] == "process_job":
        job = store.get_job(task["job_id"])
        if job:
            produced = bool(job["resume_md"] or job["fit_json"])
            store.update_job(task["job_id"], status="Saved" if produced else "Needs attention")
            store.log_event(task["job_id"], "error", f"Gave up: {error[:300]}")
            if produced:
                return                      # they got value; keep the charge
    metric = task["payload"].get("metric")
    if metric:
        billing.refund(task["user_id"], metric)


def process_one() -> bool:
    """Run one task if any is ready. Returns whether a task was handled."""
    db.requeue_stale()
    task = db.claim_task()
    if not task:
        return False
    store = db.Store(task["user_id"])
    try:
        result = HANDLERS[task["kind"]](task, store)
        db.finish_task(task["id"], result)
    except Exception as e:
        permanent = isinstance(e, (PermanentError, ScrapeError, ApplyError))
        log.warning("task %s (%s) failed: %s", task["id"], task["kind"], e)
        if db.fail_task(task["id"], str(e), retry=not permanent) == "failed":
            _on_final_failure(task, store, str(e))
    return True


def run_forever(poll: float = 2.0, stop: threading.Event | None = None) -> None:
    stop = stop or threading.Event()
    while not stop.is_set():
        try:
            if not process_one():
                stop.wait(poll)
        except Exception:
            log.exception("worker loop error")
            stop.wait(poll)


def start_background_thread() -> threading.Event:
    """Embedded worker for local installs, so `streamlit run app.py` is all you need."""
    stop = threading.Event()
    threading.Thread(target=run_forever, kwargs={"stop": stop}, daemon=True, name="careerready-worker").start()
    return stop


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    halt = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: halt.set())
    log.info("worker started")
    run_forever(stop=halt)
