"""Opt-in, server-only Supabase REST transport for the sync event journal.

No network calls on import. No live database writes unless explicitly invoked.
This adapter deliberately does not create tables or bypass the event version rules.
"""
from __future__ import annotations

import json
import os
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

try:
    from .sync_core import canonical_event
except ImportError:  # standalone pytest invocation
    from sync_core import canonical_event

EXPECTED_TABLES = ("sync_events", "sync_deliveries")


class SupabaseDeliveryError(RuntimeError):
    """Destination did not confirm an identical durable record."""


def event_row(event: dict[str, Any]) -> dict[str, Any]:
    """Build immutable server-side event row in the prepared Supabase schema."""
    import hashlib
    canonical = canonical_event(event)
    raw = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "event_id": canonical["event_id"],
        "version": canonical["version"],
        "project_id": canonical["project_id"],
        "source": canonical["source"],
        "source_ref": canonical["source_ref"],
        "created_at": canonical["created_at"],
        "event_type": canonical["type"],
        "approval": canonical["approval"],
        "privacy": canonical["privacy"],
        "event_text": canonical["text"],
        "payload": canonical,
        "payload_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
    }


class SupabaseRestTransport:
    """Restricted to configured HTTPS Supabase project; keys stay server-side."""

    def __init__(self, url: str, secret_key: str,
                 request: Callable[[str, str, dict[str, Any] | None], Any] | None = None):
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port
                or parsed.query or parsed.fragment or parsed.path not in ("", "/")
                or not parsed.hostname or not parsed.hostname.endswith(".supabase.co")):
            raise ValueError("Supabase URL must be a project HTTPS origin")
        if not secret_key or len(secret_key) < 10:
            raise ValueError("server-side Supabase key is required")
        self.url = url.rstrip("/")
        self._key = secret_key
        self._request_override = request

    def _request(self, method: str, table: str,
                 query: dict[str, Any] | None = None, body: Any = None,
                 preference: str = "") -> Any:
        if table not in EXPECTED_TABLES:
            raise ValueError("unsupported table")
        if self._request_override is not None:
            return self._request_override(method, table, {"query": query, "body": body, "prefer": preference})
        target = self.url + "/rest/v1/" + table
        if query:
            target += "?" + urlencode(query)
        headers = {
            "apikey": self._key, "Authorization": "Bearer " + self._key,
            "Content-Type": "application/json", "Accept": "application/json",
        }
        if preference:
            headers["Prefer"] = preference
        req = Request(target, data=json.dumps(body).encode("utf-8") if body is not None else None,
                      headers=headers, method=method)
        try:
            with urlopen(req, timeout=12) as resp:
                value = resp.read()
            return json.loads(value) if value else None
        except HTTPError as exc:
            # Never reveal credentials, body or potentially private response details.
            raise SupabaseDeliveryError(f"Supabase request failed: HTTP {exc.code}") from None
        except (URLError, TimeoutError) as exc:
            raise SupabaseDeliveryError("Supabase request failed: network error") from None

    def deliver(self, event: dict[str, Any]) -> str:
        """Insert-if-absent and verify digest; never update or overwrite history."""
        row = event_row(event)
        filters = {
            "select": "event_id,version,payload_sha256",
            "event_id": "eq." + row["event_id"],
            "version": "eq." + str(row["version"]),
            "limit": "1",
        }
        existing = self._request("GET", "sync_events", filters)
        if existing:
            if len(existing) != 1 or existing[0].get("payload_sha256") != row["payload_sha256"]:
                raise SupabaseDeliveryError("Supabase event version conflict")
            return "duplicate"
        # The primary key provides concurrency-safe insert-ignore (never merges).
        self._request("POST", "sync_events", {"on_conflict": "event_id,version"},
                      [row], "resolution=ignore-duplicates,return=minimal")
        confirmed = self._request("GET", "sync_events", filters)
        if not isinstance(confirmed, list) or len(confirmed) != 1:
            raise SupabaseDeliveryError("Supabase did not confirm event persistence")
        if confirmed[0].get("payload_sha256") != row["payload_sha256"]:
            raise SupabaseDeliveryError("Supabase event version conflict")
        return "delivered"


def from_environment(request: Callable | None = None) -> SupabaseRestTransport:
    """Explicit opt-in; no fallback to production or default project credentials."""
    if os.environ.get("CORPORATE_BRAIN_SUPABASE_SYNC_ENABLED") != "true":
        raise RuntimeError("Supabase sync transport is disabled by default")
    return SupabaseRestTransport(
        os.environ.get("CORPORATE_BRAIN_SUPABASE_SYNC_URL", ""),
        os.environ.get("CORPORATE_BRAIN_SUPABASE_SYNC_SECRET_KEY", ""),
        request=request,
    )
