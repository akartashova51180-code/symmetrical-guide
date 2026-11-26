"""Run the guarded Supabase transfer with passwords entered privately at the prompt.

The default mode is a read-only plan. Passwords are not echoed or written to disk.
"""
from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path
from urllib.parse import quote

from .migrate_postgres_to_postgres import migrate

SOURCE_PROJECT_REF = "axtkpwqwyjfseyklsroq"
TARGET_PROJECT_REF = "tdmnqttxmuxxyamsipcq"
SOURCE_USER = f"postgres.{SOURCE_PROJECT_REF}"
TARGET_USER = f"postgres.{TARGET_PROJECT_REF}"
SOURCE_HOST = "aws-1-eu-west-3.pooler.supabase.com"
TARGET_HOST = "aws-0-eu-west-1.pooler.supabase.com"


def _database_url(user: str, password: str, host: str) -> str:
    encoded_password = quote(password, safe="")
    return f"postgresql://{user}:{encoded_password}@{host}:5432/postgres?sslmode=require"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true",
        help="Write to the empty main database after reviewing the read-only plan",
    )
    parser.add_argument(
        "--confirm-source-stopped", action="store_true",
        help="Confirm the test app and Telegram intake are stopped",
    )
    parser.add_argument(
        "--data-dir", type=Path,
        default=Path(os.environ.get("CORPORATE_BRAIN_DATA_DIR", Path(__file__).resolve().parents[1] / "data")),
        help="Folder containing music CRM files for checksum verification",
    )
    args = parser.parse_args()

    if args.apply and not args.confirm_source_stopped:
        parser.error("Before --apply, stop the test app and Telegram intake and add --confirm-source-stopped")
    if args.confirm_source_stopped and not args.apply:
        parser.error("--confirm-source-stopped is used together with --apply")

    source_password = getpass.getpass("Password for TEST Supabase database: ")
    target_password = getpass.getpass("Password for MAIN Supabase database: ")
    if not source_password or not target_password:
        parser.error("Both database passwords are required")

    names = (
        "CORPORATE_BRAIN_MIGRATION_SOURCE_URL",
        "CORPORATE_BRAIN_MIGRATION_TARGET_URL",
        "CORPORATE_BRAIN_MIGRATION_SOURCE_PROJECT_REF",
        "CORPORATE_BRAIN_MIGRATION_TARGET_PROJECT_REF",
    )
    old_values = {name: os.environ.get(name) for name in names}
    os.environ.update({
        names[0]: _database_url(SOURCE_USER, source_password, SOURCE_HOST),
        names[1]: _database_url(TARGET_USER, target_password, TARGET_HOST),
        names[2]: SOURCE_PROJECT_REF,
        names[3]: TARGET_PROJECT_REF,
    })
    del source_password, target_password

    try:
        results = migrate(
            apply=args.apply,
            source_stopped=args.confirm_source_stopped,
            data_dir=args.data_dir,
        )
    finally:
        for name, value in old_values.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    print("Transfer completed." if args.apply else "Read-only plan completed; no database data was changed.")
    for table, count in results.items():
        print(f"{table}: {count}")


if __name__ == "__main__":
    main()
