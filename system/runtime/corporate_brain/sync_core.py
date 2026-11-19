"""Small, dependency-free event journal for the symmetrical-guide sync gateway."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EVENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
PROJECT_ID = re.compile(r"^P[0-9]{2,4}$")
SOURCES = {"ChatGPT", "Codex", "GitHub", "Supabase"}
TYPES = {"decision", "task", "fact", "hypothesis", "test_task"}
PRIVACY = {"public_synthetic", "internal", "restricted"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def canonical_event(value: dict[str, Any]) -> dict[str, Any]:
    required = {"event_id", "project_id", "source", "source_ref", "created_at", "version",
                "type", "approval", "privacy", "text"}
    missing = required - value.keys()
    if missing:
        raise ValueError(f"missing fields: {', '.join(sorted(missing))}")
    event = {key: value[key] for key in required}
    if not isinstance(event["event_id"], str) or not EVENT_ID.fullmatch(event["event_id"]):
        raise ValueError("invalid event_id")
    if not isinstance(event["project_id"], str) or not PROJECT_ID.fullmatch(event["project_id"]):
        raise ValueError("invalid project_id")
    if event["source"] not in SOURCES or event["type"] not in TYPES or event["privacy"] not in PRIVACY:
        raise ValueError("invalid source, type or privacy")
    if type(event["version"]) is not int or event["version"] < 1:
        raise ValueError("version must be a positive integer")
    if not isinstance(event["approval"], str) or not isinstance(event["text"], str) or not event["text"].strip():
        raise ValueError("approval and non-empty text are required")
    if not isinstance(event["source_ref"], str) or not isinstance(event["created_at"], str):
        raise ValueError("source_ref and created_at must be strings")
    try:
        parsed = datetime.fromisoformat(event["created_at"].replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("created_at must be ISO-8601 with timezone") from exc
    if parsed.tzinfo is None:
        raise ValueError("created_at must include timezone")
    return event


class EventJournal:
    """Append-only versions; an event ID/version key can only be replayed identically."""
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sync_events (
                    event_id TEXT NOT NULL, version INTEGER NOT NULL, payload TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL, created_at TEXT NOT NULL, received_at TEXT NOT NULL,
                    PRIMARY KEY(event_id, version));
                CREATE TABLE IF NOT EXISTS sync_deliveries (
                    event_id TEXT NOT NULL, version INTEGER NOT NULL, destination TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                    ack_at TEXT, last_error TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(event_id, version, destination),
                    FOREIGN KEY(event_id, version) REFERENCES sync_events(event_id, version));
            """)

    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def put(self, raw: dict[str, Any], destinations: tuple[str, ...] = ("github", "supabase", "codex")) -> dict[str, Any]:
        event = canonical_event(raw)
        payload = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(payload.encode()).hexdigest()
        with self._db() as db:
            latest = db.execute("SELECT MAX(version) FROM sync_events WHERE event_id=?", (event["event_id"],)).fetchone()[0]
            existing = db.execute("SELECT payload_sha256 FROM sync_events WHERE event_id=? AND version=?",
                                  (event["event_id"], event["version"])).fetchone()
            if existing:
                if existing[0] != digest:
                    raise ValueError("version conflict: same event_id/version has different content")
                return {"status": "duplicate", "event_id": event["event_id"], "version": event["version"]}
            if latest is not None and event["version"] != latest + 1:
                raise ValueError(f"version conflict: expected {latest + 1}")
            if latest is None and event["version"] != 1:
                raise ValueError("first version must be 1")
            db.execute("INSERT INTO sync_events VALUES(?,?,?,?,?,?)",
                       (event["event_id"], event["version"], payload, digest, event["created_at"], utc_now()))
            for destination in sorted(set(destinations)):
                if destination not in {"github", "supabase", "codex", "chatgpt"}:
                    raise ValueError("unknown destination")
                db.execute("INSERT INTO sync_deliveries(event_id,version,destination) VALUES(?,?,?)",
                           (event["event_id"], event["version"], destination))
        return {"status": "accepted", "event_id": event["event_id"], "version": event["version"], "sha256": digest}

    def pending(self, destination: str, limit: int = 100) -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("""SELECT e.payload,d.attempts FROM sync_events e JOIN sync_deliveries d
                USING(event_id,version) WHERE d.destination=? AND d.status IN ('pending','failed')
                ORDER BY e.received_at LIMIT ?""", (destination, max(1, min(limit, 500)))).fetchall()
        return [{"event": json.loads(row["payload"]), "attempts": row["attempts"]} for row in rows]

    def ack(self, event_id: str, version: int, destination: str, status: str = "delivered", error: str = "") -> bool:
        if destination not in {"github", "supabase", "codex", "chatgpt"} or status not in {"delivered", "failed"}:
            raise ValueError("invalid destination or status")
        with self._db() as db:
            result = db.execute("""UPDATE sync_deliveries SET status=?, attempts=attempts+1,
                ack_at=?, last_error=? WHERE event_id=? AND version=? AND destination=?""",
                (status, utc_now() if status == "delivered" else None, error[:500], event_id, version, destination))
            return result.rowcount == 1

    def versions(self, event_id: str) -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT version,payload_sha256,received_at FROM sync_events WHERE event_id=? ORDER BY version",
                              (event_id,)).fetchall()
        return [dict(row) for row in rows]

