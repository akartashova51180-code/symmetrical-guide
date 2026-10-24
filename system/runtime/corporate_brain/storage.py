from __future__ import annotations

import csv
import json
import os
import re
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any

REGISTRIES = {
    "projects": "projects.csv",
    "tasks": "tasks.csv",
    "decisions": "decisions.csv",
    "evidence": "evidence.csv",
    "pipeline": "pipeline.csv",
    "income": "income.csv",
    "ideas": "ideas.csv",
    "product_opportunities": "product-opportunities.csv",
    "music_songs": "creative-catalog.csv",
    "song_campaigns": "song-campaigns.csv",
    "music_costs": "music-costs.csv",
}


class _HybridRow(dict):
    """PostgreSQL row supporting both named and positional access like sqlite3.Row."""
    def __init__(self, columns, values):
        super().__init__(zip((column.name for column in columns), values))
        self._values = values

    def __getitem__(self, key):
        return self._values[key] if isinstance(key, int) else super().__getitem__(key)


class _PostgresConnection:
    def __init__(self, connection):
        self._connection = connection

    def __enter__(self):
        self._connection.__enter__()
        return self

    def __exit__(self, exc_type, exc, traceback):
        return self._connection.__exit__(exc_type, exc, traceback)

    def execute(self, statement, parameters=()):
        ignore = "INSERT OR IGNORE INTO" in statement.upper()
        statement = re.sub(r"INSERT\s+OR\s+IGNORE\s+INTO", "INSERT INTO", statement, flags=re.I)
        statement = statement.replace("?", "%s")
        if ignore:
            statement = statement.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
        return self._connection.execute(statement, parameters)

    def executescript(self, script):
        script = script.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
        for statement in script.split(";"):
            if statement.strip():
                self.execute(statement)


def connect(db_path: Path):
    database_url = os.environ.get("CORPORATE_BRAIN_DATABASE_URL", "").strip()
    if database_url:
        import psycopg

        def hybrid_row(cursor):
            return lambda values: _HybridRow(cursor.description, values)

        return _PostgresConnection(psycopg.connect(database_url, row_factory=hybrid_row))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def initialize(db_path: Path) -> None:
    with connect(db_path) as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS records (
            entity_type TEXT NOT NULL,
            record_id TEXT NOT NULL,
            project_id TEXT,
            status TEXT,
            due_date TEXT,
            title TEXT,
            payload_json TEXT NOT NULL,
            source_file TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (entity_type, record_id)
        );
        CREATE INDEX IF NOT EXISTS idx_records_project ON records(project_id);
        CREATE INDEX IF NOT EXISTS idx_records_due ON records(entity_type, due_date);
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_key TEXT NOT NULL UNIQUE,
            kind TEXT NOT NULL,
            created_at TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            seen INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS telegram_notification_outbox (
            event_key TEXT PRIMARY KEY,
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT NOT NULL DEFAULT '',
            sent_at TEXT,
            FOREIGN KEY(event_key) REFERENCES notifications(event_key) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS job_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_name TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL,
            details_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS song_assets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            song_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            original_name TEXT NOT NULL,
            stored_path TEXT NOT NULL,
            media_type TEXT,
            size_bytes INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            uploaded_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_song_assets_song ON song_assets(song_id, uploaded_at);
        CREATE TABLE IF NOT EXISTS telegram_intake_state (
            chat_id TEXT PRIMARY KEY,
            active_song_id TEXT,
            pending_action TEXT NOT NULL DEFAULT '',
            source_name TEXT NOT NULL DEFAULT '',
            project_ids TEXT NOT NULL DEFAULT 'P04',
            source_link TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS telegram_bot_runtime (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """)
    # Additive migration for existing SQLite/PostgreSQL installations.
    if os.environ.get("CORPORATE_BRAIN_DATABASE_URL", "").strip():
        with connect(db_path) as db:
            db.execute("ALTER TABLE telegram_intake_state ADD COLUMN IF NOT EXISTS pending_action TEXT NOT NULL DEFAULT ''")
    else:
        with connect(db_path) as db:
            columns = {row[1] for row in db.execute("PRAGMA table_info(telegram_intake_state)")}
            if "pending_action" not in columns:
                db.execute("ALTER TABLE telegram_intake_state ADD COLUMN pending_action TEXT NOT NULL DEFAULT ''")


def _field(row: dict[str, str], name: str, default: str = "") -> str:
    for key, value in row.items():
        if key.lstrip("\ufeff") == name:
            return (value or "").strip()
    return default


def _due_date(raw: str) -> str | None:
    try:
        return date.fromisoformat(raw[:10]).isoformat()
    except (ValueError, TypeError):
        return None


def import_registries(db_path: Path, source_dir: Path) -> dict[str, int]:
    """Import UTF-8 CSV records; retain every source field in payload_json."""
    counts: dict[str, int] = {}
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    with connect(db_path) as db:
        for entity_type, filename in REGISTRIES.items():
            path = source_dir / filename
            if not path.is_file():
                counts[entity_type] = 0
                continue
            inserted = 0
            with path.open(encoding="utf-8-sig", newline="") as stream:
                for row in csv.DictReader(stream):
                    cleaned = {str(k).lstrip("\ufeff"): (v or "").strip() for k, v in row.items() if k is not None}
                    record_id = _field(cleaned, "id") or _field(cleaned, "campaign_id")
                    if not record_id:
                        continue
                    related_projects = (_field(cleaned, "проект_id") or _field(cleaned, "связанные_проекты")
                                        or _field(cleaned, "предложенный_проект") or _field(cleaned, "источник_проект")
                                        or _field(cleaned, "проект"))
                    status = (_field(cleaned, "статус") or _field(cleaned, "этап")
                              or _field(cleaned, "статус_сведений"))
                    title = (_field(cleaned, "задача") or _field(cleaned, "идея") or _field(cleaned, "проект")
                             or _field(cleaned, "компания") or _field(cleaned, "название")
                             or _field(cleaned, "цель_кампании") or record_id)
                    db.execute("""INSERT INTO records
                        (entity_type, record_id, project_id, status, due_date, title, payload_json, source_file, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(entity_type, record_id) DO UPDATE SET
                        project_id=excluded.project_id, status=excluded.status, due_date=excluded.due_date,
                        title=excluded.title, payload_json=excluded.payload_json,
                        source_file=excluded.source_file, updated_at=excluded.updated_at""",
                        (entity_type, record_id, related_projects or None,
                         status or None,
                         _due_date(_field(cleaned, "срок")),
                         title,
                         json.dumps(cleaned, ensure_ascii=False), filename, now))
                    inserted += 1
            counts[entity_type] = inserted
    return counts


def _registry_fields(source_dir: Path, entity_type: str) -> list[str]:
    filename = REGISTRIES[entity_type]
    with (source_dir / filename).open(encoding="utf-8-sig", newline="") as stream:
        return [field.lstrip("\ufeff") for field in (csv.DictReader(stream).fieldnames or [])]


def create_music_entry(db_path: Path, source_dir: Path, entity_type: str, fields: dict[str, str]) -> dict[str, Any]:
    if entity_type not in {"music_songs", "song_campaigns", "music_costs"}:
        raise ValueError("Создавать можно только записи музыкальной CRM")
    allowed = set(_registry_fields(source_dir, entity_type))
    key_field = {"music_songs": "id", "song_campaigns": "campaign_id", "music_costs": "cost_id"}[entity_type]
    values = {key: str(value).strip() for key, value in fields.items() if key in allowed and key != key_field}
    if entity_type == "music_songs" and not values.get("название"):
        raise ValueError("Укажите название произведения")
    if entity_type == "song_campaigns":
        song_id = values.get("song_id", "")
        if not song_id or not get_record(db_path, "music_songs", song_id):
            raise ValueError("Кампания должна ссылаться на существующий song_id в общей CRM")
        if not values.get("project_id"):
            raise ValueError("Укажите проект, в рамках которого проходит кампания")
    if entity_type == "music_costs" and not values.get("сервис_или_бот"):
        raise ValueError("Укажите сервис или Telegram-бот")
    if entity_type == "music_costs" and values.get("song_id") and not get_record(db_path, "music_songs", values["song_id"]):
        raise ValueError("Расход должен ссылаться на существующий song_id или оставаться общим расходом студии")
    with connect(db_path) as db:
        prefix = {"music_songs": "C", "song_campaigns": "CMP", "music_costs": "MC"}[entity_type]
        existing_ids = [row[0] for row in db.execute("SELECT record_id FROM records WHERE entity_type=?", (entity_type,))]
        nums = [int(record_id[len(prefix):]) for record_id in existing_ids
                if record_id.startswith(prefix) and record_id[len(prefix):].isdigit()]
        record_id = f"{prefix}{max(nums, default=0)+1:02d}"
        data = {key: "" for key in allowed}
        data.update(values)
        data[key_field] = record_id
        related = data.get("связанные_проекты") or data.get("project_id") or data.get("проект") or None
        title = data.get("название") or data.get("цель_кампании") or record_id
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        db.execute("""INSERT INTO records(entity_type, record_id, project_id, status, due_date, title, payload_json, source_file, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (entity_type, record_id, related, data.get("статус") or None,
             _due_date(data.get("плановая_дата", "")), title,
             json.dumps(data, ensure_ascii=False), REGISTRIES[entity_type], now))
    return get_record(db_path, entity_type, record_id) or {}


def get_record(db_path: Path, entity_type: str, record_id: str) -> dict[str, Any] | None:
    with connect(db_path) as db:
        row = db.execute("SELECT * FROM records WHERE entity_type=? AND record_id=?", (entity_type, record_id)).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["data"] = json.loads(item.pop("payload_json"))
    return item


def update_music_entry(db_path: Path, source_dir: Path, entity_type: str, record_id: str,
                       fields: dict[str, str]) -> dict[str, Any] | None:
    if entity_type not in {"music_songs", "song_campaigns", "music_costs"}:
        raise ValueError("Изменять можно только записи музыкальной CRM")
    allowed = set(_registry_fields(source_dir, entity_type))
    key_field = {"music_songs": "id", "song_campaigns": "campaign_id", "music_costs": "cost_id"}[entity_type]
    changes = {key: str(value).strip() for key, value in fields.items() if key in allowed and key != key_field}
    current = get_record(db_path, entity_type, record_id)
    if current is None:
        return None
    data = current["data"]
    data.update(changes)
    if entity_type == "music_songs" and not data.get("название"):
        raise ValueError("Название произведения не может быть пустым")
    if entity_type == "song_campaigns":
        if not get_record(db_path, "music_songs", data.get("song_id", "")):
            raise ValueError("Кампания должна ссылаться на существующий song_id в общей CRM")
        if not data.get("project_id"):
            raise ValueError("Укажите проект, в рамках которого проходит кампания")
    if entity_type == "music_costs" and not data.get("сервис_или_бот"):
        raise ValueError("Укажите сервис или Telegram-бот")
    if entity_type == "music_costs" and data.get("song_id") and not get_record(db_path, "music_songs", data["song_id"]):
        raise ValueError("Расход должен ссылаться на существующий song_id или оставаться общим расходом студии")
    related = data.get("связанные_проекты") or data.get("project_id") or data.get("проект") or None
    title = data.get("название") or data.get("цель_кампании") or record_id
    with connect(db_path) as db:
        db.execute("""UPDATE records SET project_id=?, status=?, due_date=?, title=?, payload_json=?, updated_at=?
            WHERE entity_type=? AND record_id=?""",
            (related, data.get("статус") or None, _due_date(data.get("плановая_дата", "")), title,
             json.dumps(data, ensure_ascii=False), datetime.now().astimezone().isoformat(timespec="seconds"),
             entity_type, record_id))
    return get_record(db_path, entity_type, record_id)


def store_song_asset(db_path: Path, song_id: str, kind: str, original_name: str, stored_path: str,
                     media_type: str | None, size_bytes: int, sha256: str) -> int:
    with connect(db_path) as db:
        if db.execute("SELECT 1 FROM records WHERE entity_type='music_songs' AND record_id=?", (song_id,)).fetchone() is None:
            raise ValueError("Песня не найдена в общей CRM")
        duplicate = db.execute("SELECT id FROM song_assets WHERE song_id=? AND sha256=?", (song_id, sha256)).fetchone()
        if duplicate:
            return int(duplicate["id"])
        cursor = db.execute("""INSERT INTO song_assets(song_id, kind, original_name, stored_path, media_type, size_bytes, sha256, uploaded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING id""",
            (song_id, kind, original_name, stored_path, media_type, size_bytes, sha256,
             datetime.now().astimezone().isoformat(timespec="seconds")))
        return int(cursor.fetchone()[0])


def list_song_assets(db_path: Path, song_id: str | None = None) -> list[dict[str, Any]]:
    with connect(db_path) as db:
        if song_id:
            rows = db.execute("SELECT * FROM song_assets WHERE song_id=? ORDER BY id", (song_id,)).fetchall()
        else:
            rows = db.execute("SELECT * FROM song_assets ORDER BY id").fetchall()
    return [dict(row) for row in rows]


def get_song_asset(db_path: Path, asset_id: int) -> dict[str, Any] | None:
    with connect(db_path) as db:
        row = db.execute("SELECT * FROM song_assets WHERE id=?", (asset_id,)).fetchone()
    return dict(row) if row else None


def get_telegram_state(db_path: Path, chat_id: str) -> dict[str, str]:
    with connect(db_path) as db:
        row = db.execute("SELECT * FROM telegram_intake_state WHERE chat_id=?", (chat_id,)).fetchone()
    if row is None:
        return {"chat_id": chat_id, "active_song_id": "", "pending_action": "", "source_name": "",
                "project_ids": "P04", "source_link": ""}
    return {key: (value or "") for key, value in dict(row).items() if key != "updated_at"}


def update_telegram_state(db_path: Path, chat_id: str, **changes: str) -> dict[str, str]:
    allowed = {"active_song_id", "pending_action", "source_name", "project_ids", "source_link"}
    if set(changes) - allowed:
        raise ValueError("Недопустимое поле состояния Telegram-приёмщика")
    state = get_telegram_state(db_path, chat_id)
    state.update({key: str(value) for key, value in changes.items()})
    with connect(db_path) as db:
        db.execute("""INSERT INTO telegram_intake_state(chat_id, active_song_id, pending_action, source_name, project_ids, source_link, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(chat_id) DO UPDATE SET active_song_id=excluded.active_song_id,
            pending_action=excluded.pending_action, source_name=excluded.source_name, project_ids=excluded.project_ids, source_link=excluded.source_link,
            updated_at=excluded.updated_at""",
            (chat_id, state.get("active_song_id", ""), state.get("pending_action", ""), state.get("source_name", ""),
             state.get("project_ids", "P04"), state.get("source_link", ""),
             datetime.now().astimezone().isoformat(timespec="seconds")))
    return state


def get_telegram_offset(db_path: Path) -> int:
    with connect(db_path) as db:
        row = db.execute("SELECT value FROM telegram_bot_runtime WHERE key='offset'").fetchone()
    return int(row[0]) if row else 0


def set_telegram_offset(db_path: Path, offset: int) -> None:
    with connect(db_path) as db:
        db.execute("INSERT INTO telegram_bot_runtime(key, value) VALUES ('offset', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (str(offset),))


def list_records(db_path: Path, entity_type: str, limit: int = 500) -> list[dict[str, Any]]:
    with connect(db_path) as db:
        rows = db.execute("SELECT * FROM records WHERE entity_type=? ORDER BY record_id LIMIT ?", (entity_type, limit)).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["data"] = json.loads(item.pop("payload_json"))
        result.append(item)
    return result


def update_task(db_path: Path, record_id: str, changes: dict[str, str]) -> dict[str, Any] | None:
    allowed = {"статус", "следующий_шаг", "срок", "ответственный"}
    if not changes or set(changes) - allowed:
        raise ValueError("Разрешены только поля задачи: статус, следующий_шаг, срок, ответственный")
    with connect(db_path) as db:
        row = db.execute("SELECT payload_json FROM records WHERE entity_type='tasks' AND record_id=?", (record_id,)).fetchone()
        if row is None:
            return None
        data = json.loads(row["payload_json"])
        for key, value in changes.items():
            if key == "срок" and value and _due_date(value) is None:
                raise ValueError("Срок укажите в формате ГГГГ-ММ-ДД или оставьте пустым")
            data[key] = value
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        db.execute("""UPDATE records SET status=?, due_date=?, payload_json=?, updated_at=?
            WHERE entity_type='tasks' AND record_id=?""",
            (data.get("статус") or None, _due_date(data.get("срок", "")),
             json.dumps(data, ensure_ascii=False), now, record_id))
        updated = db.execute("SELECT * FROM records WHERE entity_type='tasks' AND record_id=?", (record_id,)).fetchone()
    item = dict(updated)
    item["data"] = json.loads(item.pop("payload_json"))
    return item


def create_task(db_path: Path, source_dir: Path, fields: dict[str, str]) -> dict[str, Any]:
    allowed = set(_registry_fields(source_dir, "tasks"))
    values = {key: str(value).strip() for key, value in fields.items() if key in allowed and key != "id"}
    if not values.get("задача"):
        raise ValueError("Опишите задачу или распоряжение")
    project_ids = [item for item in values.get("проект_id", "").split(";") if item]
    known = {row["record_id"] for row in list_records(db_path, "projects")}
    if not project_ids or set(project_ids) - known:
        raise ValueError("Укажите существующий ID проекта; для нескольких проектов используйте ;")
    due = values.get("срок", "")
    if due and _due_date(due) is None:
        raise ValueError("Срок укажите в формате ГГГГ-ММ-ДД или оставьте пустым")
    with connect(db_path) as db:
        ids = [row[0] for row in db.execute("SELECT record_id FROM records WHERE entity_type='tasks'")]
        nums = [int(value[1:]) for value in ids if value.startswith("T") and value[1:].isdigit()]
        record_id = f"T{max(nums, default=0)+1:02d}"
        data = {key: "" for key in allowed}
        data.update(values)
        data["id"] = record_id
        data["статус"] = data.get("статус") or "К выполнению"
        data["ответственный"] = data.get("ответственный") or "Анастасия / назначить"
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        db.execute("""INSERT INTO records(entity_type, record_id, project_id, status, due_date, title, payload_json, source_file, updated_at)
            VALUES ('tasks', ?, ?, ?, ?, ?, ?, 'portfolio-api', ?)""",
            (record_id, data["проект_id"], data["статус"], _due_date(due), data["задача"],
             json.dumps(data, ensure_ascii=False), now))
    return get_record(db_path, "tasks", record_id) or {}


def create_idea(db_path: Path, source_dir: Path, fields: dict[str, str]) -> dict[str, Any]:
    allowed = set(_registry_fields(source_dir, "ideas"))
    values = {key: str(value).strip() for key, value in fields.items() if key in allowed and key != "id"}
    if not values.get("идея"):
        raise ValueError("Кратко опишите идею, проблему или решение")
    values["статус"] = values.get("статус") or "На классификацию"
    values["дата"] = values.get("дата") or date.today().isoformat()
    values["источник_проект"] = values.get("источник_проект") or "P00"
    with connect(db_path) as db:
        ids = [row[0] for row in db.execute("SELECT record_id FROM records WHERE entity_type='ideas'")]
        nums = [int(value[1:]) for value in ids if value.startswith("I") and value[1:].isdigit()]
        record_id = f"I{max(nums, default=0)+1:02d}"
        values["id"] = record_id
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        related = values.get("предложенный_проект") or values.get("источник_проект") or "P00"
        db.execute("""INSERT INTO records(entity_type, record_id, project_id, status, title, payload_json, source_file, updated_at)
            VALUES ('ideas', ?, ?, ?, ?, ?, 'ideas.csv', ?)""",
            (record_id, related, values["статус"], values["идея"], json.dumps(values, ensure_ascii=False), now))
    return get_record(db_path, "ideas", record_id) or {}


def update_idea(db_path: Path, record_id: str, changes: dict[str, str]) -> dict[str, Any] | None:
    allowed = {"тип", "решение_владелицы", "факты", "гипотезы", "предложенный_проект", "роль",
               "монетизация", "срок", "основание_срока", "статус", "следующий_шаг", "блокер",
               "влияние_на_проекты", "маршрутизация", "обоснование_маршрута"}
    if not changes or set(changes) - allowed:
        raise ValueError("Недопустимые поля маршрутизации")
    with connect(db_path) as db:
        row = db.execute("SELECT payload_json FROM records WHERE entity_type='ideas' AND record_id=?", (record_id,)).fetchone()
        if row is None:
            return None
        data = json.loads(row["payload_json"])
        data.update({key: str(value).strip() for key, value in changes.items()})
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        project_id = data.get("предложенный_проект") or data.get("источник_проект") or "P00"
        db.execute("UPDATE records SET project_id=?, status=?, title=?, payload_json=?, updated_at=? WHERE entity_type='ideas' AND record_id=?",
                   (project_id, data.get("статус"), data.get("идея"), json.dumps(data, ensure_ascii=False), now, record_id))
    return get_record(db_path, "ideas", record_id)


def portfolio_summary(db_path: Path, today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    with connect(db_path) as db:
        counts = {row["entity_type"]: row["n"] for row in db.execute("SELECT entity_type, COUNT(*) n FROM records GROUP BY entity_type")}
        tasks = db.execute("SELECT status, due_date FROM records WHERE entity_type='tasks'").fetchall()
        ideas = db.execute("SELECT status FROM records WHERE entity_type='ideas'").fetchall()
    active = [r for r in tasks if not _is_closed(r["status"])]
    overdue = [r for r in active if r["due_date"] and date.fromisoformat(r["due_date"]) < today]
    due_today = [r for r in active if r["due_date"] == today.isoformat()]
    due_7d = [r for r in active if r["due_date"] and today <= date.fromisoformat(r["due_date"]) <= today.fromordinal(today.toordinal()+7)]
    return {
        "as_of": today.isoformat(),
        "counts": {kind: counts.get(kind, 0) for kind in REGISTRIES},
        "tasks_open": len(active),
        "tasks_overdue": len(overdue),
        "tasks_due_today": len(due_today),
        "tasks_due_next_7_days": len(due_7d),
        "pipeline_records": counts.get("pipeline", 0),
        "income_records": counts.get("income", 0),
        "ideas_waiting_classification": sum((r["status"] or "").casefold() in {"на классификацию", "требует классификации"} for r in ideas),
        "financial_history_status": "нет записей в импортированном реестре" if counts.get("income", 0) == 0 else "записи доступны; суммы требуют проверки качества данных",
        "connected_sources": [],
        "source_note": "Сводка основана только на локальных CSV; пустой реестр не означает нулевой фактический доход.",
    }


def portfolio_overview(db_path: Path, today: date | None = None) -> list[dict[str, Any]]:
    today = today or date.today()
    projects = list_records(db_path, "projects")
    tasks = list_records(db_path, "tasks")
    overview = []
    for project in projects:
        project_id = project["record_id"]
        related = [task for task in tasks if project_id in (task.get("project_id") or "").split(";")]
        open_tasks = [task for task in related if not _is_closed(task.get("status"))]
        due = [task for task in open_tasks if task.get("due_date")]
        overview.append({
            "project_id": project_id,
            "name": project["data"].get("проект", project["title"]),
            "goal": project["data"].get("цель", ""),
            "status": project["data"].get("статус", ""),
            "next_step": project["data"].get("следующий_шаг", ""),
            "resource_gaps": project["data"].get("неизвестное", ""),
            "open_task_count": len(open_tasks),
            "due_today": sum(task.get("due_date") == today.isoformat() for task in due),
            "overdue": sum(date.fromisoformat(task["due_date"]) < today for task in due),
            "task_ids": [task["record_id"] for task in open_tasks],
        })
    return overview


def _is_closed(status: str | None) -> bool:
    text = (status or "").casefold()
    return any(token in text for token in ("готово", "выполнено", "закрыто", "отменено", "завершено"))


def create_notification(db_path: Path, event_key: str, kind: str, payload: dict[str, Any]) -> bool:
    with connect(db_path) as db:
        cursor = db.execute("INSERT OR IGNORE INTO notifications(event_key, kind, created_at, payload_json) VALUES (?, ?, ?, ?)",
            (event_key, kind, datetime.now().astimezone().isoformat(timespec="seconds"), json.dumps(payload, ensure_ascii=False)))
        if cursor.rowcount == 1:
            db.execute("INSERT OR IGNORE INTO telegram_notification_outbox(event_key) VALUES (?)", (event_key,))
    return cursor.rowcount == 1


def pending_telegram_notifications(db_path: Path, limit: int = 20) -> list[dict[str, Any]]:
    with connect(db_path) as db:
        rows = db.execute("""SELECT n.event_key, n.kind, n.created_at, n.payload_json, o.attempts
            FROM telegram_notification_outbox o JOIN notifications n USING(event_key)
            WHERE o.sent_at IS NULL ORDER BY n.id LIMIT ?""", (max(1, min(limit, 100)),)).fetchall()
    return [{**dict(row), "payload": json.loads(row["payload_json"])} for row in rows]


def mark_telegram_notification(db_path: Path, event_key: str, *, sent: bool, error: str = "") -> None:
    sent_at = datetime.now().astimezone().isoformat(timespec="seconds") if sent else None
    # Keep only exception class/status, never request URLs or credentials.
    safe_error = re.sub(r"[^A-Za-z0-9_.:-]", "", error)[:80]
    with connect(db_path) as db:
        db.execute("""UPDATE telegram_notification_outbox
            SET attempts=attempts+1, last_error=?, sent_at=COALESCE(?, sent_at) WHERE event_key=?""",
            (safe_error, sent_at, event_key))


def list_notifications(db_path: Path, limit: int = 100) -> list[dict[str, Any]]:
    with connect(db_path) as db:
        rows = db.execute("SELECT * FROM notifications ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [{**dict(r), "payload": json.loads(r["payload_json"])} for r in rows]


def record_run(db_path: Path, job_name: str, started_at: str, status: str, details: dict[str, Any]) -> None:
    with connect(db_path) as db:
        db.execute("INSERT INTO job_runs(job_name, started_at, finished_at, status, details_json) VALUES (?, ?, ?, ?, ?)",
                   (job_name, started_at, datetime.now().astimezone().isoformat(timespec="seconds"), status,
                    json.dumps(details, ensure_ascii=False)))


def list_runs(db_path: Path, limit: int = 50) -> list[dict[str, Any]]:
    with connect(db_path) as db:
        rows = db.execute("SELECT * FROM job_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [{**dict(r), "details": json.loads(r["details_json"])} for r in rows]
