"""One-time import from a stopped SQLite snapshot into an empty PostgreSQL database."""
from __future__ import annotations

import argparse
import hashlib
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .storage import initialize, connect

TABLES = (
    "records", "notifications", "job_runs",
    "song_assets", "telegram_intake_state", "telegram_bot_runtime",
)
SERIAL_TABLES = ("notifications", "job_runs", "song_assets")


def migrate(source_path: Path, data_dir: Path) -> dict[str, int]:
    if not source_path.is_file():
        raise FileNotFoundError(f"SQLite-файл не найден: {source_path}")
    if not os.environ.get("CORPORATE_BRAIN_DATABASE_URL"):
        raise RuntimeError("Задайте CORPORATE_BRAIN_DATABASE_URL для целевой PostgreSQL базы")

    with sqlite3.connect(source_path) as source:
        source.row_factory = sqlite3.Row
        source_tables = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing = set(TABLES) - source_tables
        if missing:
            raise RuntimeError(f"В SQLite-снимке отсутствуют таблицы: {', '.join(sorted(missing))}")

        initialize(source_path)
        copied: dict[str, int] = {}
        with connect(source_path) as target:
            nonempty = [table for table in TABLES
                        if target.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] != 0]
            if nonempty:
                raise RuntimeError(f"Целевая PostgreSQL база должна быть пустой; заполнены таблицы: {', '.join(nonempty)}")

            for table in (*TABLES, "telegram_notification_outbox"):
                if table not in source_tables:
                    copied[table] = 0
                    continue
                rows = source.execute(f"SELECT * FROM {table}").fetchall()
                if not rows:
                    copied[table] = 0
                    continue
                columns = [item[1] for item in source.execute(f"PRAGMA table_info({table})")]
                column_sql = ", ".join(columns)
                placeholders = ", ".join("%s" for _ in columns)
                statement = f"INSERT INTO {table} ({column_sql}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"
                for row in rows:
                    target.execute(statement, tuple(row))
                copied[table] = len(rows)

            for table in SERIAL_TABLES:
                target.execute(
                    f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                    f"COALESCE(MAX(id), 1), MAX(id) IS NOT NULL) FROM {table}"
                )
            today = datetime.now(ZoneInfo("Europe/Moscow")).date().isoformat()
            latest = target.execute("SELECT event_key, kind FROM notifications").fetchall()
            current_kinds = {"daily_brief", "overdue_task", "task_due_today", "weekly_monday",
                             "weekly_wednesday", "weekly_friday"}
            for item in latest:
                key, kind = item["event_key"], item["kind"]
                if kind in current_kinds and (key == f"daily_brief:{today}" or key.endswith(f":{today}")):
                    target.execute("INSERT INTO telegram_notification_outbox(event_key) VALUES (%s) ON CONFLICT DO NOTHING",
                                   (key,))

        with connect(source_path) as target:
            assets = target.execute("SELECT stored_path, sha256 FROM song_assets").fetchall()
        for asset in assets:
            file_path = (data_dir / asset["stored_path"]).resolve()
            if data_dir.resolve() not in file_path.parents or not file_path.is_file():
                raise RuntimeError(f"Файл из музыкальной CRM отсутствует или вне каталога данных: {asset['stored_path']}")
            digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
            if digest != asset["sha256"]:
                raise RuntimeError(f"SHA-256 не совпадает для файла: {asset['stored_path']}")
        copied["verified_assets"] = len(assets)
        return copied


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Путь к остановленному SQLite-снимку")
    parser.add_argument("--data-dir", type=Path,
                        default=Path(os.environ.get("CORPORATE_BRAIN_DATA_DIR", "system/runtime/data")),
                        help="Корень каталога с вложениями музыкальной CRM")
    args = parser.parse_args()
    results = migrate(args.source, args.data_dir)
    for table, count in results.items():
        print(f"{table}: {count}")


if __name__ == "__main__":
    main()
