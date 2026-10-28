"""What the UI calls to start agent work. Quotas, approval and mode rules are enforced HERE, on the
server side, so no front-end tweak can bypass them. The worker then re-checks the dangerous parts."""
from config import settings
from src import billing
from src.errors import QuotaError
from src.services import forms


def _charge(store, metric: str) -> None:
    ok, _, _ = billing.check(store.uid, metric)
    if not ok:
        raise QuotaError(billing.denial_message(store.uid, metric))
    billing.consume(store.uid, metric)


def submit_job(store, profile: dict, url: str = "", description: str = "") -> int:
    url, description = url.strip(), description.strip()
    if len(profile.get("resume_text", "")) < 100:
        raise ValueError("Add your resume on the Profile page first.")
    if not (url or description):
        raise ValueError("Paste a job link or the job description.")
    _charge(store, "jobs")
    job_id = store.add_job(url=url, description=description, status="Processing")
    store.log_event(job_id, "created", "Added from link" if url else "Added from pasted text")
    store.enqueue("process_job", job_id, {"metric": "jobs"})
    return job_id


def request_prepare(store, job_id: int, url: str) -> int:
    if forms.is_blocked(url):
        raise ValueError("This site doesn't allow automated applications.")
    return store.enqueue("prepare_application", job_id, {"url": url.strip()})


def request_run(store, job_id: int, prepare_task_id: int, answers: dict, mode: str, approved: bool) -> int:
    allowed = {"preview", "submit"} | ({"handover"} if not settings.HOSTED else set())
    if mode not in allowed:
        raise ValueError("That mode isn't available here.")
    prep = store.get_task(prepare_task_id)
    if not prep or prep["status"] != "done" or prep["job_id"] != job_id:
        raise ValueError("Prepare the application again; the saved form is gone.")
    if mode == "submit" and approved is not True:
        raise ValueError("You need to approve this application before it can be submitted.")
    metric = {"preview": "previews", "submit": "submits", "handover": None}[mode]
    if metric:
        _charge(store, metric)
    return store.enqueue("run_application", job_id, {
        "prepare_task_id": prepare_task_id, "mode": mode, "approved": approved,
        "answers": answers, "metric": metric})
