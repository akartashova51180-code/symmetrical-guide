from corporate_brain.sync_worker import deliver_pending_supabase
from sync_core import EventJournal
from supabase_transport import SupabaseDeliveryError


def event():
    return {
        "event_id": "SYNC-TEST-SUPABASE-001", "project_id": "P00",
        "source": "ChatGPT", "source_ref": "https://github.com/example/repo/issues/1",
        "created_at": "2026-11-19T20:03:58Z", "version": 1,
        "type": "test_task", "approval": "yes, test only",
        "privacy": "public_synthetic", "text": "test",
    }


class StubTransport:
    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail

    def deliver(self, value):
        self.calls += 1
        if self.fail:
            raise SupabaseDeliveryError("simulated failure")
        return "delivered"


def test_worker_marks_ack_after_success_only(tmp_path):
    db = EventJournal(tmp_path / "sync.sqlite3")
    db.put(event())
    worker = StubTransport()
    assert deliver_pending_supabase(db, worker) == {"checked": 1, "delivered": 1, "failed": 0}
    assert db.pending("supabase") == []
    assert len(db.pending("codex")) == 1
    assert deliver_pending_supabase(db, worker)["checked"] == 0
    assert worker.calls == 1


def test_worker_retains_failed_delivery_for_retry(tmp_path):
    db = EventJournal(tmp_path / "sync.sqlite3")
    db.put(event())
    assert deliver_pending_supabase(db, StubTransport(fail=True)) == {
        "checked": 1, "delivered": 0, "failed": 1}
    assert len(db.pending("supabase")) == 1
    assert db.pending("supabase")[0]["attempts"] == 1
    assert deliver_pending_supabase(db, StubTransport())["delivered"] == 1
    assert db.pending("supabase") == []


def test_worker_does_not_ack_unconfirmed_status(tmp_path):
    db = EventJournal(tmp_path / "sync.sqlite3")
    db.put(event())
    class Unconfirmed:
        def deliver(self, _):
            return "queued"
    result = deliver_pending_supabase(db, Unconfirmed())
    assert result["failed"] == 1
    assert len(db.pending("supabase")) == 1
