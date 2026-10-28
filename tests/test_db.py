import pytest

from config import settings
from src import crypto, db


def two_users():
    db.upsert_user("alice", "a@x.com"); db.upsert_user("bob", "b@x.com")
    return db.Store("alice"), db.Store("bob")


def test_tenant_isolation():
    a, b = two_users()
    jid = a.add_job(title="Secret", resume_md="alice resume")
    a.log_event(jid, "x"); tid = a.enqueue("process_job", jid, {"p": 1})
    a.save_profile({"full_name": "Alice"})
    assert b.get_job(jid) is None and b.list_jobs() == [] and b.list_events(jid) == []
    assert b.get_task(tid) is None and b.latest_task("process_job", jid) is None
    assert b.get_profile()["full_name"] == ""
    b.update_job(jid, title="HACKED"); b.delete_job(jid)             # silently no-ops on someone else's row
    assert a.get_job(jid)["title"] == "Secret"
    assert a.status_counts()["Saved"] == 1 and b.status_counts()["Saved"] == 0


def test_encryption_at_rest(monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setattr(settings, "ENCRYPTION_KEY", Fernet.generate_key().decode())
    a, _ = two_users()
    jid = a.add_job(title="T", resume_md="MY SECRET RESUME", cover_letter="dear x")
    a.save_profile({"full_name": "Alice", "phone": "555-1234"})
    tid = a.enqueue("run_application", jid, {"answers": {"q1": "555-1234"}})
    with db.engine().connect() as c:
        raw = " ".join(str(v) for t in (db.jobs, db.profiles, db.tasks) for r in c.execute(db.select(t)) for v in r)
    assert "SECRET RESUME" not in raw and "555-1234" not in raw and "dear x" not in raw
    assert a.get_job(jid)["resume_md"] == "MY SECRET RESUME" and a.get_profile()["phone"] == "555-1234"
    assert a.get_task(tid)["payload"]["answers"]["q1"] == "555-1234"


def test_queue_claim_retry_and_stale(monkeypatch):
    a, _ = two_users()
    t1, t2 = a.enqueue("k"), a.enqueue("k")
    c1, c2 = db.claim_task(), db.claim_task()
    assert (c1["id"], c2["id"]) == (t1, t2) and db.claim_task() is None             # each task claimed once
    assert c1["status"] == "running" and c1["attempts"] == 1
    assert db.fail_task(t1, "boom") == "queued"                                      # retry scheduled with backoff
    assert db.claim_task() is None                                                   # not runnable until run_after
    clock = iter(range(10**12, 10**13, 1000))                                        # time moves on between calls
    monkeypatch.setattr(db, "_now", lambda: next(clock))
    again = db.claim_task(); assert again["id"] == t1 and again["attempts"] == 2
    db.fail_task(t1, "boom"); db.claim_task(); assert db.fail_task(t1, "boom") == "failed"   # attempts exhausted
    db.finish_task(t2, {"ok": 1}); assert a.get_task(t2)["status"] == "done" and a.get_task(t2)["result"] == {"ok": 1}


def test_stale_running_task_is_recovered(monkeypatch):
    a, _ = two_users()
    tid = a.enqueue("k"); db.claim_task()
    assert db.requeue_stale() == 0
    monkeypatch.setattr(db, "_now", lambda: 10**12)
    assert db.requeue_stale() == 1 and a.get_task(tid)["status"] == "queued"


def test_usage_users_export_and_delete():
    a, b = two_users()
    db.add_usage("alice", "jobs"); db.add_usage("alice", "jobs", 2); db.add_usage("bob", "jobs")
    assert a.usage() == {"jobs": 3} and b.usage() == {"jobs": 1}
    jid = a.add_job(title="T"); a.log_event(jid, "created"); a.save_profile({"full_name": "Alice"})
    dump = a.export_all()
    assert dump["profile"]["full_name"] == "Alice" and dump["jobs"][0]["events"][0]["kind"] == "created"
    db.delete_account("alice")
    assert db.get_user("alice") is None and db.get_user("bob") is not None and b.usage() == {"jobs": 1}
    with db.engine().connect() as c:
        assert c.execute(db.select(db.jobs)).first() is None


def test_store_requires_user_and_whitelists_columns():
    with pytest.raises(ValueError):
        db.Store("")
    a, _ = two_users()
    with pytest.raises(ValueError):
        a.update_job(a.add_job(title="x"), user_id="bob")


def test_webhook_idempotency_marker():
    assert db.seen_webhook("evt_1") is False and db.seen_webhook("evt_1") is True


def test_first_use_from_many_threads_does_not_race(tmp_path, monkeypatch):
    """Regression: the embedded worker and the page both hit the DB on first launch, and both tried to
    create the tables ("table users already exists") or queried before they existed."""
    import threading
    import time

    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite:///{tmp_path / 'race.db'}")
    db._engine = None                                           # fresh process: nothing configured yet
    real = db.metadata.create_all
    monkeypatch.setattr(db.metadata, "create_all", lambda e, **k: (time.sleep(0.15), real(e, **k))[1])  # widen the window

    errors, gate = [], threading.Barrier(6)
    def first_touch(i):
        try:
            gate.wait()
            db.upsert_user(f"user{i}")
            db.claim_task()
        except Exception as e:
            errors.append(repr(e)[:120])
    threads = [threading.Thread(target=first_touch, args=(i,)) for i in range(6)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert errors == []
    assert all(db.get_user(f"user{i}") for i in range(6))
