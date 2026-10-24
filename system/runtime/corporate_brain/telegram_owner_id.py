"""Print private /start sender IDs from this bot's pending updates, without printing its token."""
from __future__ import annotations

import os
import sys

from .telegram_intake import TelegramClient


def find_private_starters(updates: list[dict]) -> list[tuple[str, str]]:
    found = set()
    for update in updates:
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        if (chat.get("type") == "private" and str(message.get("text", "")).strip().startswith("/start")
                and sender.get("id") is not None):
            found.add((str(sender["id"]), str(sender.get("username", ""))))
    return sorted(found)


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise SystemExit("Сначала задайте TELEGRAM_BOT_TOKEN в локальном deploy/.env")
    updates = TelegramClient(token).call("getUpdates", {"timeout": 0, "limit": 100, "allowed_updates": ["message"]})
    candidates = find_private_starters(updates)
    if not candidates:
        raise SystemExit("Не найден ожидающий личный /start. Откройте тестового бота, нажмите Start и запустите команду ещё раз.")
    for user_id, username in candidates:
        suffix = f" (@{username})" if username else " (без username)"
        print(f"Telegram user ID: {user_id}{suffix}")
    print("Скрипт не подтверждает пользователей: внесите только свой ID в TELEGRAM_ALLOWED_USER_IDS.", file=sys.stderr)


if __name__ == "__main__":
    main()
