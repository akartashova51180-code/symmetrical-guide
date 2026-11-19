"""Local HTTP end-to-end test: signed GitHub -> journal -> pending -> ACK.

This does not contact GitHub, Supabase or OpenAI; no live data or tokens.
"""
import hashlib
import hmac
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from corporate_brain import sync_api
from corporate_brain.sync_core import EventJournal


REPO = "akartashova51180-code/symmetrical-guide"
ISSUE_URL = f"https://github.com/{REPO}/issues/91"


def payload():
    event = {
        "event_id": "SYNC-TEST-HTTP-00091", "project_id": "P00",
        "source": "GitHub", "source_ref": ISSUE_URL,
        "created_at": "2026-11-20T10:00:00Z", "version": 1,
        "type": "test_task", "approval": "yes, test only",
        "privacy": "public_synthetic", "text": "Safe E2E synthetic test",
    }
    return {
        "action": "opened", "repository": {"full_name": REPO},
        "sender": {"login": "akartashova51180-code"},
        "issue": {"html_url": ISSUE_URL,
                  "user": {"login": "akartashova51180-code"},
                  "body": "```sync-event\n" + json.dumps(event) + "\n```"},
    }


def test_signed_webhook_to_queue_to_ack(monkeypatch, tmp_path):
    journal = EventJournal(tmp_path / "isolated-journal.sqlite3")
    monkeypatch.setattr(sync_api, "journal", journal)
    monkeypatch.setenv("CORPORATE_BRAIN_GITHUB_WEBHOOK_SECRET", "test-hmac-secret-123")
    monkeypatch.setenv("CORPORATE_BRAIN_GITHUB_REPO", REPO)
    monkeypatch.setenv("CORPORATE_BRAIN_GITHUB_ACTORS", "akartashova51180-code")
    monkeypatch.setenv("CORPORATE_BRAIN_SYNC_TOKEN", "test-api-token-123")
    app = FastAPI()
    app.include_router(sync_api.router)
    client = TestClient(app)

    raw = json.dumps(payload()).encode()
    signature = "sha256=" + hmac.new(b"test-hmac-secret-123", raw, hashlib.sha256).hexdigest()
    headers = {"X-Hub-Signature-256": signature, "X-GitHub-Event": "issues",
               "Content-Type": "application/json"}
    invalid = dict(headers, **{"X-Hub-Signature-256": "sha256=invalid"})
    assert client.post("/api/sync/github-webhook", content=raw, headers=invalid).status_code == 401
    assert not journal.versions("SYNC-TEST-HTTP-00091")

    first = client.post("/api/sync/github-webhook", content=raw, headers=headers)
    assert first.status_code == 200
    assert first.json()["journal"]["status"] == "accepted"

    replay = client.post("/api/sync/github-webhook", content=raw, headers=headers)
    assert replay.json()["journal"]["status"] == "duplicate"

    protected = "/api/sync/pending/codex"
    assert client.get(protected).status_code == 401
    auth = {"Authorization": "Bearer test-api-token-123"}
    queued = client.get(protected, headers=auth)
    assert queued.status_code == 200
    assert len(queued.json()) == 1
    assert queued.json()[0]["event"]["event_id"] == "SYNC-TEST-HTTP-00091"

    ack = "/api/sync/events/SYNC-TEST-HTTP-00091/1/ack/codex"
    assert client.post(ack, json={"status": "delivered"}).status_code == 401
    assert client.post(ack, json={"status": "delivered"}, headers=auth).status_code == 200
    assert client.get(protected, headers=auth).json() == []
    assert len(client.get("/api/sync/pending/chatgpt", headers=auth).json()) == 1

    versions = client.get("/api/sync/events/SYNC-TEST-HTTP-00091/versions", headers=auth)
    assert versions.status_code == 200
    assert len(versions.json()) == 1
