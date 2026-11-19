import pytest

from supabase_transport import (
    SupabaseRestTransport, SupabaseDeliveryError, event_row, from_environment
)


def event(version=1, text="Synthetic transport check"):
    return {
        "event_id": "SYNC-TEST-20261119-21", "project_id": "P00",
        "source": "GitHub",
        "source_ref": "https://github.com/akartashova51180-code/symmetrical-guide/issues/2",
        "created_at": "2026-11-19T20:03:58Z", "version": version,
        "type": "test_task", "approval": "yes, test only",
        "privacy": "public_synthetic", "text": text,
    }


class FakePostgrest:
    def __init__(self):
        self.rows = {}
        self.calls = []

    def __call__(self, method, table, data):
        self.calls.append((method, table, data))
        assert table == "sync_events"
        if method == "GET":
            query = data["query"]
            key = (query["event_id"][3:], int(query["version"][3:]))
            row = self.rows.get(key)
            return ([{"event_id": row["event_id"], "version": row["version"],
                      "payload_sha256": row["payload_sha256"]}] if row else [])
        if method == "POST":
            assert data["query"]["on_conflict"] == "event_id,version"
            assert data["prefer"] == "resolution=ignore-duplicates,return=minimal"
            row = data["body"][0]
            self.rows.setdefault((row["event_id"], row["version"]), row)
            return None
        raise AssertionError(method)


def test_transport_inserts_then_detects_identical_duplicate():
    fake = FakePostgrest()
    t = SupabaseRestTransport("https://example.supabase.co", "fake-test-secret-key", request=fake)
    assert t.deliver(event()) == "delivered"
    assert t.deliver(event()) == "duplicate"
    assert [call[0] for call in fake.calls].count("POST") == 1


def test_transport_never_overwrites_same_version_conflict():
    fake = FakePostgrest()
    t = SupabaseRestTransport("https://example.supabase.co", "fake-test-secret-key", request=fake)
    t.deliver(event())
    with pytest.raises(SupabaseDeliveryError, match="version conflict"):
        t.deliver(event(text="Changed"))
    assert fake.rows[(event()["event_id"], 1)]["event_text"] == "Synthetic transport check"


def test_transport_detects_racing_insert_with_different_hash():
    fake = FakePostgrest()

    def racing_request(method, table, data):
        if method == "POST":
            row = event_row(event(text="Other concurrent content"))
            fake.rows[(row["event_id"], row["version"])] = row
            return None
        return fake(method, table, data)

    t = SupabaseRestTransport("https://example.supabase.co", "fake-test-secret-key", request=racing_request)
    with pytest.raises(SupabaseDeliveryError, match="version conflict"):
        t.deliver(event())


def test_transport_rejects_invalid_url_or_missing_key():
    for url in ("http://example.supabase.co", "https://supabase.co",
                "https://example.supabase.co.evil.test", "https://user@example.supabase.co",
                "https://example.supabase.co:8443", "https://example.supabase.co/path"):
        with pytest.raises(ValueError):
            SupabaseRestTransport(url, "fake-test-secret-key")
    with pytest.raises(ValueError, match="key"):
        SupabaseRestTransport("https://example.supabase.co", "")


def test_transport_off_by_default(monkeypatch):
    monkeypatch.delenv("CORPORATE_BRAIN_SUPABASE_SYNC_ENABLED", raising=False)
    monkeypatch.setenv("CORPORATE_BRAIN_SUPABASE_SYNC_URL", "https://example.supabase.co")
    monkeypatch.setenv("CORPORATE_BRAIN_SUPABASE_SYNC_SECRET_KEY", "fake-test-secret-key")
    with pytest.raises(RuntimeError, match="disabled"):
        from_environment(request=FakePostgrest())


def test_transport_enabled_only_with_explicit_opt_in(monkeypatch):
    monkeypatch.setenv("CORPORATE_BRAIN_SUPABASE_SYNC_ENABLED", "true")
    monkeypatch.setenv("CORPORATE_BRAIN_SUPABASE_SYNC_URL", "https://example.supabase.co")
    monkeypatch.setenv("CORPORATE_BRAIN_SUPABASE_SYNC_SECRET_KEY", "fake-test-secret-key")
    t = from_environment(request=FakePostgrest())
    assert t.deliver(event()) == "delivered"
