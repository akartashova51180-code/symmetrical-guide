"""Authenticated local HTTP adapter; include `router` from corporate_brain.app."""
from __future__ import annotations

import hmac
import json
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

try:
    from .sync_core import EventJournal
    from .github_ingest import parse_issue_event
except ImportError:  # standalone tests/import from the module directory
    from sync_core import EventJournal
    from github_ingest import parse_issue_event

router = APIRouter(prefix="/api/sync", tags=["sync"])
DB_PATH = Path(os.environ.get("CORPORATE_BRAIN_SYNC_DB", "data/sync-journal.sqlite3"))
journal = EventJournal(DB_PATH)


def _authorize(authorization: str | None) -> None:
    expected = os.environ.get("CORPORATE_BRAIN_SYNC_TOKEN", "")
    if not expected or not authorization or not hmac.compare_digest(authorization, f"Bearer {expected}"):
        raise HTTPException(401, "sync authentication failed")


@router.post("/events")
def put_event(body: dict[str, Any], authorization: str | None = Header(default=None)):
    _authorize(authorization)
    try:
        return journal.put(body)
    except ValueError as exc:
        raise HTTPException(409 if "conflict" in str(exc) or "version" in str(exc) else 422, str(exc)) from exc


@router.get("/pending/{destination}")
def pending(destination: str, limit: int = 100, authorization: str | None = Header(default=None)):
    _authorize(authorization)
    if destination not in {"github", "supabase", "codex", "chatgpt"}:
        raise HTTPException(404, "unknown destination")
    return journal.pending(destination, limit)


@router.post("/events/{event_id}/{version}/ack/{destination}")
def acknowledge(event_id: str, version: int, destination: str, body: dict[str, Any],
                authorization: str | None = Header(default=None)):
    _authorize(authorization)
    try:
        changed = journal.ack(event_id, version, destination, body.get("status", "delivered"), body.get("error", ""))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if not changed:
        raise HTTPException(404, "delivery not found")
    return {"status": "acknowledged"}


@router.get("/events/{event_id}/versions")
def versions(event_id: str, authorization: str | None = Header(default=None)):
    _authorize(authorization)
    return journal.versions(event_id)


@router.post("/github-webhook")
async def github_webhook(request: Request, x_hub_signature_256: str | None = Header(default=None),
                         x_github_event: str | None = Header(default=None)):
    secret = os.environ.get("CORPORATE_BRAIN_GITHUB_WEBHOOK_SECRET", "")
    if not secret or not x_hub_signature_256:
        raise HTTPException(401, "webhook authentication is not configured")
    raw = await request.body()
    expected = "sha256=" + hmac.new(secret.encode(), raw, "sha256").hexdigest()
    if not hmac.compare_digest(expected, x_hub_signature_256):
        raise HTTPException(401, "invalid webhook signature")
    if len(raw) > 65536:
        raise HTTPException(413, "webhook payload too large")
    if not x_github_event:
        raise HTTPException(422, "missing X-GitHub-Event")
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("webhook JSON must be an object")
        event = parse_issue_event(
            payload, x_github_event,
            os.environ.get("CORPORATE_BRAIN_GITHUB_REPO", ""),
            {name.strip() for name in os.environ.get("CORPORATE_BRAIN_GITHUB_ACTORS", "").split(",") if name.strip()},
        )
        if event is None:
            return {"status": "ignored", "action": "no_event_created"}
        # Ingress records a synthetic event, not approval of any real-world action.
        result = journal.put(event, destinations=("codex", "chatgpt"))
        return {"status": "accepted", "journal": result}
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(422, str(exc)) from exc

