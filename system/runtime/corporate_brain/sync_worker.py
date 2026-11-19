"""One-shot opt-in queue processor. Not scheduled and never runs automatically."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .sync_core import EventJournal
from .supabase_transport import SupabaseDeliveryError, from_environment


def deliver_pending_supabase(journal: EventJournal, transport: Any, limit: int = 20) -> dict[str, int]:
    """ACK only after a matching durable remote hash was read back.

    The worker runs one batch per explicit invocation. Any retryable failure
    leaves the local event journal intact and marks the delivery failed.
    """
    counts = {"checked": 0, "delivered": 0, "failed": 0}
    for item in journal.pending("supabase", limit):
        event = item["event"]
        counts["checked"] += 1
        try:
            result = transport.deliver(event)
            if result not in {"delivered", "duplicate"}:
                raise SupabaseDeliveryError("remote persistence was not confirmed")
            journal.ack(event["event_id"], event["version"], "supabase", "delivered")
            counts["delivered"] += 1
        except (SupabaseDeliveryError, ValueError, OSError):
            # Do not expose event bodies or credentials in the journal.
            journal.ack(event["event_id"], event["version"], "supabase", "failed",
                        "Supabase delivery failed; inspect private worker logs")
            counts["failed"] += 1
    return counts


def main() -> None:
    if os.environ.get("CORPORATE_BRAIN_SUPABASE_SYNC_ENABLED") != "true":
        raise SystemExit("Supabase transport disabled; no network calls made")
    # Fail closed before touching a local SQLite journal.
    transport = from_environment()
    db_path = Path(os.environ.get("CORPORATE_BRAIN_SYNC_DB", "data/sync-journal.sqlite3"))
    if not db_path.is_file():
        raise SystemExit("Existing sync journal required; refusing to create an empty journal")
    journal = EventJournal(db_path)
    print(deliver_pending_supabase(journal, transport))


if __name__ == "__main__":
    main()
