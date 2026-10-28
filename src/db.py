"""Multi-tenant storage on SQLAlchemy (SQLite locally, Postgres in production).

Every row a user can see is reached through a Store bound to that user's id, so a query can't forget
the tenant filter. Sensitive columns are encrypted at rest (see src/crypto.py).
"""
import json
import threading
import time

from sqlalchemy import (Column, Float, Integer, MetaData, String, Table, Text, UniqueConstraint, and_,
                        create_engine, delete, func, insert, select, update)
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError

from config import settings
from src import crypto

STATUSES = ["Processing", "Needs attention", "Saved", "Ready to review", "Applied",
            "Interviewing", "Offer", "Rejected", "Archived"]

PROFILE_FIELDS = {
    "full_name": "", "email": "", "phone": "", "location": "",
    "linkedin": "", "github": "", "website": "",
    "work_authorization": "", "needs_sponsorship": "", "earliest_start": "", "salary_expectation": "",
    "resume_text": "", "extra_facts": "", "auto_prepare_min_score": "",
}
JOB_COLUMNS = {"title", "company", "url", "description", "status", "fit_score", "fit_json",
               "resume_md", "cover_letter", "notes"}
ENCRYPTED = {"jobs": {"resume_md", "cover_letter", "notes", "fit_json"}, "tasks": {"payload", "result"}}

metadata = MetaData()
users = Table("users", metadata,
              Column("id", String(255), primary_key=True), Column("email", String(320), default=""),
              Column("name", String(255), default=""), Column("plan", String(32), nullable=False, default="free"),
              Column("stripe_customer_id", String(64), index=True), Column("created_at", Float))
profiles = Table("profiles", metadata,
                 Column("user_id", String(255), primary_key=True), Column("data", Text, nullable=False))
jobs = Table("jobs", metadata,
             Column("id", Integer, primary_key=True, autoincrement=True), Column("user_id", String(255), index=True, nullable=False),
             Column("title", String(500), default=""), Column("company", String(500), default=""),
             Column("url", Text, default=""), Column("description", Text, default=""),
             Column("status", String(32), nullable=False, default="Saved"), Column("fit_score", Integer),
             Column("fit_json", Text, default=""), Column("resume_md", Text, default=""),
             Column("cover_letter", Text, default=""), Column("notes", Text, default=""),
             Column("created_at", Float, nullable=False), Column("updated_at", Float, nullable=False))
events = Table("events", metadata,
               Column("id", Integer, primary_key=True, autoincrement=True), Column("user_id", String(255), index=True, nullable=False),
               Column("job_id", Integer, index=True, nullable=False), Column("ts", Float, nullable=False),
               Column("kind", String(64), nullable=False), Column("detail", Text, default=""))
tasks = Table("tasks", metadata,
              Column("id", Integer, primary_key=True, autoincrement=True), Column("user_id", String(255), index=True, nullable=False),
              Column("job_id", Integer, index=True), Column("kind", String(64), nullable=False),
              Column("status", String(16), nullable=False, index=True, default="queued"),
              Column("payload", Text, default=""), Column("result", Text, default=""),
              Column("progress", String(200), default=""), Column("error", Text, default=""),
              Column("attempts", Integer, nullable=False, default=0), Column("run_after", Float, nullable=False),
              Column("created_at", Float, nullable=False), Column("started_at", Float), Column("finished_at", Float))
usage = Table("usage", metadata,
              Column("id", Integer, primary_key=True, autoincrement=True), Column("user_id", String(255), nullable=False),
              Column("period", String(7), nullable=False), Column("metric", String(32), nullable=False),
              Column("count", Integer, nullable=False, default=0),
              UniqueConstraint("user_id", "period", "metric"))
webhook_events = Table("webhook_events", metadata,
                       Column("id", String(255), primary_key=True), Column("ts", Float, nullable=False))

_engine = None
_init_lock = threading.RLock()


def configure(url: str | None = None) -> None:
    """(Re)connect and create tables. Call with a URL in tests.

    Thread-safe: the web page and the embedded worker both touch the database on first launch. The engine
    is published only AFTER its tables exist, so no thread can use a half-initialised database.
    """
    global _engine
    with _init_lock:
        url = url or settings.DATABASE_URL
        if url.startswith("sqlite"):
            import os
            os.makedirs(settings.DATA_DIR, exist_ok=True)
        kwargs = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {"pool_pre_ping": True}
        crypto.require_key_in_production()
        new_engine = create_engine(url, **kwargs)
        try:
            metadata.create_all(new_engine)
        except (OperationalError, ProgrammingError):
            metadata.create_all(new_engine)   # another PROCESS (e.g. the worker container) won the race; checkfirst now sees its tables
        old, _engine = _engine, new_engine
        if old is not None:
            old.dispose()


def engine():
    if _engine is None:
        with _init_lock:
            if _engine is None:               # double-checked: only one thread initialises
                configure()
    return _engine


def _now() -> float:
    return time.time()


def _period() -> str:
    return time.strftime("%Y-%m")


def _encode(table: str, values: dict) -> dict:
    enc = ENCRYPTED.get(table, set())
    return {k: (crypto.encrypt(v) if k in enc and isinstance(v, str) else v) for k, v in values.items()}


def _decode(table: str, row) -> dict:
    d = dict(row._mapping)
    for k in ENCRYPTED.get(table, ()):
        if isinstance(d.get(k), str):
            d[k] = crypto.decrypt(d[k])
    return d


def _task(row) -> dict:
    d = _decode("tasks", row)
    for k in ("payload", "result"):
        d[k] = json.loads(d[k]) if d.get(k) else {}
    return d


# ------------------------------------------------------------------- users
def upsert_user(user_id: str, email: str = "", name: str = "") -> dict:
    with engine().begin() as c:
        row = c.execute(select(users).where(users.c.id == user_id)).first()
        if row is None:
            c.execute(insert(users).values(id=user_id, email=email, name=name, plan="free", created_at=_now()))
        elif (email and email != row.email) or (name and name != row.name):
            c.execute(update(users).where(users.c.id == user_id).values(email=email or row.email, name=name or row.name))
        return dict(c.execute(select(users).where(users.c.id == user_id)).first()._mapping)


def get_user(user_id: str) -> dict | None:
    with engine().connect() as c:
        row = c.execute(select(users).where(users.c.id == user_id)).first()
    return dict(row._mapping) if row else None


def set_plan(user_id: str, plan: str, customer_id: str | None = None) -> None:
    vals = {"plan": plan}
    if customer_id:
        vals["stripe_customer_id"] = customer_id
    with engine().begin() as c:
        c.execute(update(users).where(users.c.id == user_id).values(**vals))


def find_user_by_customer(customer_id: str) -> dict | None:
    with engine().connect() as c:
        row = c.execute(select(users).where(users.c.stripe_customer_id == customer_id)).first()
    return dict(row._mapping) if row else None


def delete_account(user_id: str) -> None:
    with engine().begin() as c:
        for t in (events, tasks, usage, jobs, profiles):
            c.execute(delete(t).where(t.c.user_id == user_id))
        c.execute(delete(users).where(users.c.id == user_id))


def seen_webhook(event_id: str) -> bool:
    """Record a Stripe event id; returns True if it was already processed."""
    try:
        with engine().begin() as c:
            c.execute(insert(webhook_events).values(id=event_id, ts=_now()))
        return False
    except IntegrityError:
        return True


# ------------------------------------------------------------------- usage
def add_usage(user_id: str, metric: str, n: int = 1) -> None:
    period = _period()
    where = and_(usage.c.user_id == user_id, usage.c.period == period, usage.c.metric == metric)
    for _ in range(2):  # second pass handles two writers inserting the first row at once
        with engine().begin() as c:
            if c.execute(update(usage).where(where).values(count=usage.c.count + n)).rowcount:
                return
            try:
                c.execute(insert(usage).values(user_id=user_id, period=period, metric=metric, count=max(n, 0)))
                return
            except IntegrityError:
                continue


def get_usage(user_id: str) -> dict:
    with engine().connect() as c:
        rows = c.execute(select(usage.c.metric, usage.c.count).where(
            and_(usage.c.user_id == user_id, usage.c.period == _period())))
    return {r.metric: r.count for r in rows}


# ------------------------------------------------------------------- tasks
def enqueue_task(user_id: str, kind: str, job_id: int | None = None, payload: dict | None = None) -> int:
    now = _now()
    vals = _encode("tasks", {"payload": json.dumps(payload or {})})
    with engine().begin() as c:
        return c.execute(insert(tasks).values(user_id=user_id, job_id=job_id, kind=kind, status="queued",
                                              run_after=now, created_at=now, **vals)).inserted_primary_key[0]


def claim_task() -> dict | None:
    """Atomically take the oldest runnable task (SKIP LOCKED on Postgres, status guard on SQLite)."""
    now = _now()
    with engine().begin() as c:
        row = c.execute(select(tasks.c.id).where(and_(tasks.c.status == "queued", tasks.c.run_after <= now))
                        .order_by(tasks.c.id).limit(1).with_for_update(skip_locked=True)).first()
        if row is None:
            return None
        won = c.execute(update(tasks).where(and_(tasks.c.id == row.id, tasks.c.status == "queued"))
                        .values(status="running", started_at=now, attempts=tasks.c.attempts + 1)).rowcount
        if won != 1:
            return None
        return _task(c.execute(select(tasks).where(tasks.c.id == row.id)).first())


def set_progress(task_id: int, text: str) -> None:
    with engine().begin() as c:
        c.execute(update(tasks).where(tasks.c.id == task_id).values(progress=text[:200]))


def finish_task(task_id: int, result: dict | None = None) -> None:
    vals = _encode("tasks", {"result": json.dumps(result or {})})
    with engine().begin() as c:
        c.execute(update(tasks).where(tasks.c.id == task_id).values(status="done", finished_at=_now(), progress="", **vals))


def fail_task(task_id: int, error: str, retry: bool = True) -> str:
    """Retry with backoff until TASK_MAX_ATTEMPTS, then mark failed. Returns the new status."""
    with engine().begin() as c:
        attempts = c.execute(select(tasks.c.attempts).where(tasks.c.id == task_id)).scalar() or 0
        if retry and attempts < settings.TASK_MAX_ATTEMPTS:
            c.execute(update(tasks).where(tasks.c.id == task_id).values(
                status="queued", error=error[:1000], run_after=_now() + 5 * 2 ** attempts))
            return "queued"
        c.execute(update(tasks).where(tasks.c.id == task_id).values(status="failed", error=error[:1000],
                                                                    finished_at=_now(), progress=""))
        return "failed"


def requeue_stale() -> int:
    """Recover tasks whose worker died mid-run."""
    cutoff, n = _now() - settings.TASK_STALE_SECONDS, 0
    with engine().begin() as c:
        stale = c.execute(select(tasks.c.id, tasks.c.attempts).where(
            and_(tasks.c.status == "running", tasks.c.started_at < cutoff))).all()
    for t in stale:
        fail_task(t.id, "The worker stopped before finishing this task.", retry=t.attempts < settings.TASK_MAX_ATTEMPTS)
        n += 1
    return n


# ------------------------------------------------------------------- store
class Store:
    """Everything one signed-in user may read or write."""

    def __init__(self, user_id: str):
        if not user_id:
            raise ValueError("Store needs a user id")
        self.uid = user_id

    # profile
    def get_profile(self) -> dict:
        with engine().connect() as c:
            row = c.execute(select(profiles.c.data).where(profiles.c.user_id == self.uid)).first()
        return {**PROFILE_FIELDS, **(json.loads(crypto.decrypt(row.data)) if row else {})}

    def save_profile(self, data: dict) -> None:
        clean = {k: str(data.get(k, "") or "").strip() for k in PROFILE_FIELDS}
        blob = crypto.encrypt(json.dumps(clean))
        with engine().begin() as c:
            if not c.execute(update(profiles).where(profiles.c.user_id == self.uid).values(data=blob)).rowcount:
                c.execute(insert(profiles).values(user_id=self.uid, data=blob))

    # jobs
    def add_job(self, **fields) -> int:
        fields = _encode("jobs", {k: v for k, v in fields.items() if k in JOB_COLUMNS})
        now = _now()
        with engine().begin() as c:
            return c.execute(insert(jobs).values(user_id=self.uid, created_at=now, updated_at=now, **fields)).inserted_primary_key[0]

    def get_job(self, job_id: int) -> dict | None:
        with engine().connect() as c:
            row = c.execute(select(jobs).where(and_(jobs.c.id == job_id, jobs.c.user_id == self.uid))).first()
        return _decode("jobs", row) if row else None

    def list_jobs(self, status: str | None = None) -> list[dict]:
        q = select(jobs).where(jobs.c.user_id == self.uid).order_by(jobs.c.updated_at.desc())
        if status:
            q = q.where(jobs.c.status == status)
        with engine().connect() as c:
            return [_decode("jobs", r) for r in c.execute(q)]

    def update_job(self, job_id: int, **fields) -> None:
        bad = set(fields) - JOB_COLUMNS
        if bad:
            raise ValueError(f"Unknown job fields: {sorted(bad)}")
        if not fields:
            return
        with engine().begin() as c:
            c.execute(update(jobs).where(and_(jobs.c.id == job_id, jobs.c.user_id == self.uid))
                      .values(updated_at=_now(), **_encode("jobs", fields)))

    def delete_job(self, job_id: int) -> None:
        with engine().begin() as c:
            if c.execute(delete(jobs).where(and_(jobs.c.id == job_id, jobs.c.user_id == self.uid))).rowcount:
                for t in (events, tasks):
                    c.execute(delete(t).where(and_(t.c.job_id == job_id, t.c.user_id == self.uid)))

    def status_counts(self) -> dict:
        counts = {s: 0 for s in STATUSES}
        with engine().connect() as c:
            for r in c.execute(select(jobs.c.status, func.count()).where(jobs.c.user_id == self.uid).group_by(jobs.c.status)):
                counts[r[0]] = r[1]
        return counts

    # events
    def log_event(self, job_id: int, kind: str, detail: str = "") -> None:
        with engine().begin() as c:
            c.execute(insert(events).values(user_id=self.uid, job_id=job_id, ts=_now(), kind=kind, detail=detail))

    def list_events(self, job_id: int) -> list[dict]:
        with engine().connect() as c:
            return [dict(r._mapping) for r in c.execute(select(events).where(
                and_(events.c.job_id == job_id, events.c.user_id == self.uid)).order_by(events.c.ts.desc(), events.c.id.desc()))]

    # tasks
    def enqueue(self, kind: str, job_id: int | None = None, payload: dict | None = None) -> int:
        return enqueue_task(self.uid, kind, job_id, payload)

    def get_task(self, task_id: int) -> dict | None:
        with engine().connect() as c:
            row = c.execute(select(tasks).where(and_(tasks.c.id == task_id, tasks.c.user_id == self.uid))).first()
        return _task(row) if row else None

    def latest_task(self, kind: str, job_id: int | None = None) -> dict | None:
        q = select(tasks).where(and_(tasks.c.user_id == self.uid, tasks.c.kind == kind))
        if job_id is not None:
            q = q.where(tasks.c.job_id == job_id)
        with engine().connect() as c:
            row = c.execute(q.order_by(tasks.c.id.desc()).limit(1)).first()
        return _task(row) if row else None

    # account
    def usage(self) -> dict:
        return get_usage(self.uid)

    def export_all(self) -> dict:
        return {"user": get_user(self.uid), "profile": self.get_profile(), "usage": self.usage(),
                "jobs": [{**j, "events": self.list_events(j["id"])} for j in self.list_jobs()]}
