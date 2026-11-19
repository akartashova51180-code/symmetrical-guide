import pytest

from sync_core import EventJournal


def event(version=1, text="hello"):
    return {"event_id": "SYNC-TEST-20261119-01", "project_id": "P00", "source": "ChatGPT",
            "source_ref": "https://github.com/akartashova51180-code/symmetrical-guide/issues/2",
            "created_at": "2026-11-19T20:03:58Z", "version": version, "type": "test_task",
            "approval": "yes, test only", "privacy": "public_synthetic", "text": text}


def test_duplicate_delivery_is_idempotent_and_versions_are_logged(tmp_path):
    journal = EventJournal(tmp_path / "sync.sqlite3")
    assert journal.put(event())["status"] == "accepted"
    assert journal.put(event())["status"] == "duplicate"
    assert journal.put(event(2, "second"))["version"] == 2
    assert [v["version"] for v in journal.versions("SYNC-TEST-20261119-01")] == [1, 2]


def test_conflicting_duplicate_and_skipped_version_are_rejected(tmp_path):
    journal = EventJournal(tmp_path / "sync.sqlite3")
    journal.put(event())
    with pytest.raises(ValueError, match="different content"):
        journal.put(event(text="tampered"))
    with pytest.raises(ValueError, match="expected 2"):
        journal.put(event(3, "gap"))


def test_ack_is_per_destination_and_replay_safe(tmp_path):
    journal = EventJournal(tmp_path / "sync.sqlite3")
    journal.put(event())
    assert len(journal.pending("codex")) == 1
    assert journal.ack("SYNC-TEST-20261119-01", 1, "codex")
    assert journal.pending("codex") == []
    assert journal.pending("supabase")
    assert not journal.ack("missing", 1, "codex")


@pytest.mark.parametrize("field,value", [("project_id", "P0"), ("version", True), ("source", "unknown"),
                                           ("created_at", "2026-11-19T00:00:00"), ("privacy", "secret")])
def test_invalid_event_is_rejected(tmp_path, field, value):
    journal = EventJournal(tmp_path / "sync.sqlite3")
    payload = event()
    payload[field] = value
    with pytest.raises(ValueError):
        journal.put(payload)

