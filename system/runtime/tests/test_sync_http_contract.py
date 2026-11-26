"""Exercise urllib Request construction without opening any external connection."""
import json
from urllib.error import HTTPError

import pytest
import github_transport
import supabase_transport


class FakeResponse:
    def __init__(self, data):
        self.data = json.dumps(data).encode()
    def __enter__(self):
        return self
    def __exit__(self, *_):
        return False
    def read(self):
        return self.data


def test_supabase_http_request_uses_private_auth_and_encoded_filter(monkeypatch):
    observed = []
    def fake_urlopen(request, timeout):
        observed.append((request, timeout))
        return FakeResponse([{"event_id": "E-1", "version": 1, "payload_sha256": "abc"}])
    monkeypatch.setattr(supabase_transport, "urlopen", fake_urlopen)
    client = supabase_transport.SupabaseRestTransport(
        "https://example.supabase.co", "supabase-fake-server-secret-123")
    data = client._request("GET", "sync_events", {"event_id": "eq.E-1", "version": "eq.1"})
    assert data[0]["event_id"] == "E-1"
    req, timeout = observed[0]
    assert timeout == 12
    assert req.full_url.startswith("https://example.supabase.co/rest/v1/sync_events?")
    assert "event_id=eq.E-1" in req.full_url
    assert req.get_header("Authorization") == "Bearer supabase-fake-server-secret-123"
    assert req.get_header("Apikey") == "supabase-fake-server-secret-123"


def test_github_http_request_uses_token_only_in_header(monkeypatch):
    seen = []
    def fake_urlopen(request, timeout):
        seen.append(request)
        return FakeResponse({"id": 101, "body": "test"})
    monkeypatch.setattr(github_transport, "urlopen", fake_urlopen)
    client = github_transport.GitHubIssueTransport(
        "akartashova51180-code/symmetrical-guide",
        "fake-github-server-token",
        "fake-long-receipt-signature-secret",
        "trusted-gateway")
    response = client._request("POST", "issues/42/comments", {"body": "test"})
    assert response["id"] == 101
    req = seen[0]
    assert req.full_url == "https://api.github.com/repos/akartashova51180-code/symmetrical-guide/issues/42/comments"
    assert req.get_method() == "POST"
    assert req.get_header("Authorization") == "Bearer fake-github-server-token"
    assert "fake-github-server-token" not in req.full_url
    assert json.loads(req.data)["body"] == "test"


def test_supabase_http_error_redacts_body_and_key(monkeypatch):
    def fake_urlopen(request, timeout):
        raise HTTPError(request.full_url, 403, "secret-error", {}, None)
    monkeypatch.setattr(supabase_transport, "urlopen", fake_urlopen)
    client = supabase_transport.SupabaseRestTransport(
        "https://example.supabase.co", "fake-supabase-secret-123")
    with pytest.raises(supabase_transport.SupabaseDeliveryError) as exc:
        client._request("GET", "sync_events")
    assert "HTTP 403" in str(exc.value)
    assert "fake-supabase-secret-123" not in str(exc.value)
