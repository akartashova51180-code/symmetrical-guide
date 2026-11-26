"""Copy application tables once from the designated Supabase test project to production.

The default mode is read-only planning. Pass --apply only after reviewing the counts.
The source is connected read-only, and the target must have no application rows.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import psycopg
from psycopg import sql

SOURCE_PROJECT_REF = "axtkpwqwyjfseyklsroq"
TARGET_PROJECT_REF = "tdmnqttxmuxxyamsipcq"

TABLE_ORDER = (
    "records",
    "notifications",
    "job_runs",
    "song_assets",
    "telegram_intake_state",
    "telegram_bot_runtime",
    "request_idempotency",
    "telegram_notification_outbox",
)
REQUIRED_SOURCE_TABLES = frozenset(TABLE_ORDER[:6])
PRIMARY_KEYS = {
    "records": ("entity_type", "record_id"),
    "notifications": ("id",),
    "job_runs": ("id",),
    "song_assets": ("id",),
    "telegram_intake_state": ("chat_id",),
    "telegram_bot_runtime": ("key",),
    "request_idempotency": ("idempotency_key",),
    "telegram_notification_outbox": ("event_key",),
}


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Не задана защищённая переменная {name}")
    return value


def _validate_project_url(database_url: str, expected_ref: str, label: str) -> None:
    try:
        parsed = urlsplit(database_url)
        hostname = (parsed.hostname or "").lower()
        username = unquote(parsed.username or "")
    except ValueError:
        raise RuntimeError(f"Некорректная строка подключения для {label}") from None
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise RuntimeError(f"Для {label} нужна PostgreSQL-строка подключения Supabase")
    sslmode = (parse_qs(parsed.query).get("sslmode") or [""])[0].lower()
    if sslmode not in {"require", "verify-ca", "verify-full"}:
        raise RuntimeError(f"Подключение {label} должно использовать SSL")
    identifies_project = (
        hostname == f"db.{expected_ref}.supabase.co"
        or username == f"postgres.{expected_ref}"
    )
    if not identifies_project:
        raise RuntimeError(
            f"Подключение {label} не подтверждает ожидаемый Supabase project ref; миграция остановлена"
        )


def _tables(conn: psycopg.Connection) -> set[str]:
    return {
        row[0]
        for row in conn.execute(
            "SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname = 'public'"
        ).fetchall()
    }


def _columns(conn: psycopg.Connection, table: str) -> list[tuple[str, str, str, str, str | None]]:
    return conn.execute(
        """SELECT column_name, data_type, udt_name, is_nullable, column_default
           FROM information_schema.columns
           WHERE table_schema = 'public' AND table_name = %s
           ORDER BY ordinal_position""",
        (table,),
    ).fetchall()


def _count(conn: psycopg.Connection, table: str) -> int:
    return conn.execute(
        sql.SQL("SELECT COUNT(*) FROM public.{}").format(sql.Identifier(table))
    ).fetchone()[0]


def _existing_counts(conn: psycopg.Connection) -> dict[str, int]:
    existing = _tables(conn)
    return {table: _count(conn, table) for table in TABLE_ORDER if table in existing}


def _validate_schema(source: psycopg.Connection, target: psycopg.Connection, table: str) -> list[str]:
    source_columns = _columns(source, table)
    target_columns = _columns(target, table)
    if not source_columns or not target_columns:
        raise RuntimeError(f"Таблица {table} отсутствует в источнике или цели")

    source_by_name = {column[0]: column for column in source_columns}
    target_by_name = {column[0]: column for column in target_columns}
    extra_source = set(source_by_name) - set(target_by_name)
    if extra_source:
        raise RuntimeError(f"В целевой таблице {table} не хватает полей из источника")
    for name, source_column in source_by_name.items():
        target_column = target_by_name[name]
        if source_column[1:3] != target_column[1:3]:
            raise RuntimeError(f"Тип поля {table}.{name} отличается; миграция остановлена")
    for name in set(target_by_name) - set(source_by_name):
        target_column = target_by_name[name]
        nullable, default = target_column[3], target_column[4]
        if nullable != "YES" and default is None:
            raise RuntimeError(f"Новое обязательное поле {table}.{name} нельзя заполнить автоматически")

    columns = [column[0] for column in source_columns]
    missing_key_columns = set(PRIMARY_KEYS[table]) - set(columns)
    if missing_key_columns:
        raise RuntimeError(f"У таблицы {table} не совпадает ключ записи")
    return columns


def _row_digest_update(digest, row: tuple[object, ...]) -> None:
    payload = json.dumps(row, ensure_ascii=False, default=str, separators=(",", ":"))
    digest.update(payload.encode("utf-8"))
    digest.update(b"\n")


def _ordered_query(table: str, columns: list[str]):
    return sql.SQL("SELECT {} FROM public.{} ORDER BY {}").format(
        sql.SQL(", ").join(map(sql.Identifier, columns)),
        sql.Identifier(table),
        sql.SQL(", ").join(map(sql.Identifier, PRIMARY_KEYS[table])),
    )


def _verify_assets(source: psycopg.Connection, data_dir: Path) -> int:
    if "song_assets" not in _tables(source):
        return 0
    root = data_dir.resolve()
    rows = source.execute("SELECT stored_path, sha256 FROM public.song_assets").fetchall()
    for stored_path, expected_hash in rows:
        path = (root / stored_path).resolve()
        if root not in path.parents or not path.is_file():
            raise RuntimeError(f"Не найден файл вложения CRM: {stored_path}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != expected_hash:
            raise RuntimeError(f"Контрольная сумма файла CRM не совпала: {stored_path}")
    return len(rows)


def _copy_table(source: psycopg.Connection, target: psycopg.Connection,
                table: str, columns: list[str]) -> tuple[int, str]:
    query = _ordered_query(table, columns)
    insert = sql.SQL("INSERT INTO public.{} ({}) VALUES ({})").format(
        sql.Identifier(table),
        sql.SQL(", ").join(map(sql.Identifier, columns)),
        sql.SQL(", ").join(sql.Placeholder() for _ in columns),
    )
    source_cursor = source.cursor()
    target_cursor = target.cursor()
    source_cursor.execute(query)
    count = 0
    digest = hashlib.sha256()
    while batch := source_cursor.fetchmany(500):
        target_cursor.executemany(insert, batch)
        for row in batch:
            _row_digest_update(digest, row)
        count += len(batch)
    return count, digest.hexdigest()


def _verify_target(target: psycopg.Connection, table: str, columns: list[str],
                   expected_count: int, expected_digest: str) -> None:
    cursor = target.cursor()
    cursor.execute(_ordered_query(table, columns))
    count = 0
    digest = hashlib.sha256()
    while batch := cursor.fetchmany(500):
        for row in batch:
            _row_digest_update(digest, row)
        count += len(batch)
    if count != expected_count or digest.hexdigest() != expected_digest:
        raise RuntimeError(f"Проверка после переноса не прошла для таблицы {table}; транзакция отменена")


def _reset_sequences(target: psycopg.Connection) -> None:
    for table in ("notifications", "job_runs", "song_assets"):
        target.execute(
            sql.SQL(
                "SELECT setval(pg_get_serial_sequence(%s, 'id'), COALESCE(MAX(id), 1), "
                "MAX(id) IS NOT NULL) FROM public.{}"
            ).format(sql.Identifier(table)),
            (f"public.{table}",),
        )


def migrate(*, apply: bool, source_stopped: bool, data_dir: Path) -> dict[str, int]:
    if apply and not source_stopped:
        raise RuntimeError("Перед переносом остановите приложение, затем подтвердите это флагом --confirm-source-stopped")
    source_ref = _required_env("CORPORATE_BRAIN_MIGRATION_SOURCE_PROJECT_REF")
    target_ref = _required_env("CORPORATE_BRAIN_MIGRATION_TARGET_PROJECT_REF")
    if source_ref != SOURCE_PROJECT_REF or target_ref != TARGET_PROJECT_REF:
        raise RuntimeError("Перенос разрешён только из указанного тестового проекта в основной")

    source_url = _required_env("CORPORATE_BRAIN_MIGRATION_SOURCE_URL")
    target_url = _required_env("CORPORATE_BRAIN_MIGRATION_TARGET_URL")
    _validate_project_url(source_url, source_ref, "источника")
    _validate_project_url(target_url, target_ref, "цели")
    if source_url == target_url:
        raise RuntimeError("Источник и цель совпадают; миграция остановлена")

    result: dict[str, int] = {}
    try:
        with psycopg.connect(source_url, options="-c default_transaction_read_only=on") as source:
            source.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            source_tables = _tables(source)
            missing = REQUIRED_SOURCE_TABLES - source_tables
            if missing:
                raise RuntimeError("В тестовой базе не хватает обязательных таблиц приложения")
            source_counts = {table: _count(source, table) for table in TABLE_ORDER if table in source_tables}
            if source_counts.get("records", 0) == 0:
                raise RuntimeError("В тестовой базе нет записей приложения; перенос пустого источника запрещён")
            bypasses_rls = source.execute(
                "SELECT rolbypassrls FROM pg_catalog.pg_roles WHERE rolname = current_user"
            ).fetchone()[0]
            rls_tables = source.execute(
                """SELECT c.relname FROM pg_catalog.pg_class c
                   JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                   WHERE n.nspname = 'public' AND c.relname = ANY(%s) AND c.relrowsecurity""",
                (list(source_tables & set(TABLE_ORDER)),),
            ).fetchall()
            if rls_tables and not bypasses_rls:
                raise RuntimeError("Для чтения включённых RLS-таблиц нужна учётная запись владельца Supabase")
            assets_verified = _verify_assets(source, data_dir)

            with psycopg.connect(target_url, options="-c default_transaction_read_only=on") as target_check:
                target_counts = _existing_counts(target_check)
            nonempty = [table for table, count in target_counts.items() if count]
            if nonempty:
                raise RuntimeError("Основная база уже содержит записи приложения; ничего не перенесено")

            if not apply:
                result.update(source_counts)
                result["verified_assets"] = assets_verified
                return result

            old_target_url = os.environ.get("CORPORATE_BRAIN_DATABASE_URL")
            os.environ["CORPORATE_BRAIN_DATABASE_URL"] = target_url
            try:
                from .storage import initialize

                initialize(Path("/tmp/corporate-brain-postgres-migration.sqlite3"))
            finally:
                if old_target_url is None:
                    os.environ.pop("CORPORATE_BRAIN_DATABASE_URL", None)
                else:
                    os.environ["CORPORATE_BRAIN_DATABASE_URL"] = old_target_url

            table_digests: dict[str, str] = {}
            with psycopg.connect(target_url) as target:
                with target.transaction():
                    target_tables = _tables(target)
                    columns_by_table: dict[str, list[str]] = {}
                    for table in TABLE_ORDER:
                        if table not in source_tables:
                            continue
                        if table not in target_tables:
                            raise RuntimeError(f"Таблица {table} не создана в целевой базе")
                        columns_by_table[table] = _validate_schema(source, target, table)
                    nonempty_after_init = [table for table, count in _existing_counts(target).items() if count]
                    if nonempty_after_init:
                        raise RuntimeError("После подготовки схема цели уже содержит данные; перенос отменён")

                    for table in TABLE_ORDER:
                        if table not in source_tables:
                            continue
                        count, digest = _copy_table(source, target, table, columns_by_table[table])
                        result[table] = count
                        table_digests[table] = digest
                    _reset_sequences(target)
                    for table, columns in columns_by_table.items():
                        _verify_target(target, table, columns, result[table], table_digests[table])
            result["verified_assets"] = assets_verified
    except RuntimeError:
        raise
    except Exception:
        # Driver exceptions may contain connection details; never print them with a DSN.
        phase = "применении" if apply else "проверке"
        raise RuntimeError(f"Не удалось выполнить подключение или {phase} миграции; проверьте безопасные настройки базы") from None
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true",
        help="Записать данные в пустую основную базу; без этого флага выполняется только проверка",
    )
    parser.add_argument(
        "--confirm-source-stopped", action="store_true",
        help="Подтвердить, что приложение и Telegram-приёмщик не пишут в тестовую базу",
    )
    parser.add_argument(
        "--data-dir", type=Path,
        default=Path(os.environ.get("CORPORATE_BRAIN_DATA_DIR", Path(__file__).resolve().parents[1] / "data")),
        help="Корень с файлами музыкальной CRM для проверки SHA-256",
    )
    args = parser.parse_args()
    results = migrate(apply=args.apply, source_stopped=args.confirm_source_stopped, data_dir=args.data_dir)
    print("Режим: перенос выполнен" if args.apply else "Режим: только проверка, данные не изменялись")
    for table, count in results.items():
        print(f"{table}: {count}")


if __name__ == "__main__":
    main()
