"""Create a transactionally consistent SQLite snapshot, including committed WAL data."""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


def create_snapshot(source_path: Path, destination_path: Path) -> None:
    if not source_path.is_file():
        raise FileNotFoundError(f"База SQLite не найдена: {source_path}")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    if source_path.resolve() == destination_path.resolve():
        raise ValueError("Путь снимка должен отличаться от исходной базы")
    with sqlite3.connect(source_path) as source, sqlite3.connect(destination_path) as destination:
        source.backup(destination)
        result = destination.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError(f"Проверка снимка SQLite завершилась ошибкой: {result}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Путь к portfolio.sqlite3")
    parser.add_argument("--output", type=Path, required=True, help="Новый файл согласованного снимка")
    args = parser.parse_args()
    create_snapshot(args.source, args.output)
    print(f"Снимок создан и прошёл integrity_check: {args.output}")


if __name__ == "__main__":
    main()
