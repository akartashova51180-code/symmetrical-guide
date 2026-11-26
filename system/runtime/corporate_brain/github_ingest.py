"""Strictly opt-in GitHub Issues ingress: only synthetic, allowlisted events."""
from __future__ import annotations

import json
import re
from typing import Any

try:
    from .sync_core import canonical_event
except ImportError:  # standalone pytest invocation
    from sync_core import canonical_event

FENCE = re.compile(r"```sync-event\s*\n(.*?)\n```", re.DOTALL)
MAX_BODY = 32768


def parse_issue_event(payload: dict[str, Any], event_type: str, expected_repo: str,
                      allowed_actors: set[str]) -> dict[str, Any] | None:
    """Return a canonical synthetic event or None for irrelevant/unauthorized hooks.

    An Issue is not an approval. The webhook is intentionally disabled unless
    the repo and actor allowlists are explicitly configured.
    """
    if event_type != "issues" or payload.get("action") not in {"opened", "edited"}:
        return None
    if not expected_repo or not allowed_actors:
        return None
    repo = payload.get("repository") or {}
    issue = payload.get("issue") or {}
    sender = payload.get("sender") or {}
    author = issue.get("user") or {}
    if repo.get("full_name") != expected_repo:
        return None
    if sender.get("login") not in allowed_actors or author.get("login") not in allowed_actors:
        return None
    body = issue.get("body")
    if not isinstance(body, str) or len(body) > MAX_BODY:
        return None
    matches = FENCE.findall(body)
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError("exactly one sync-event block is required")
    try:
        raw = json.loads(matches[0])
    except json.JSONDecodeError as exc:
        raise ValueError("invalid sync-event JSON") from exc
    if not isinstance(raw, dict):
        raise ValueError("sync-event must be an object")
    event = canonical_event(raw)
    # Public GitHub must never carry restricted or internal records.
    if event["privacy"] != "public_synthetic" or event["type"] != "test_task":
        raise ValueError("GitHub ingress permits synthetic test events only")
    if event["approval"] != "yes, test only":
        raise ValueError("test-only owner approval marker is required")
    if event["source"] not in {"GitHub", "ChatGPT"}:
        raise ValueError("unsupported GitHub event source")
    if event["source_ref"] != issue.get("html_url"):
        raise ValueError("source_ref must match the signed issue URL")
    if event["project_id"] != "P00":
        raise ValueError("synthetic ingress must use P00")
    return event
