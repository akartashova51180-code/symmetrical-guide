"""Deliver scheduled portfolio notifications to the owner's private Telegram chat."""
from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path
from typing import Any, Callable

from .storage import mark_telegram_notification, pending_telegram_notifications


def _format(item: dict[str, Any]) -> str:
    payload = item["payload"]
    kind = item["kind"]
    if kind == "daily_brief":
        return (
            f"☀️ Сводка портфеля · {payload.get('as_of', '')}\n"
            f"Проекты: {payload.get('counts', {}).get('projects', '—')}\n"
            f"Открытые задачи: {payload.get('tasks_open', '—')} · просрочено: {payload.get('tasks_overdue', '—')}\n"
            f"Срок в ближайшие 7 дней: {payload.get('tasks_due_next_7_days', '—')}\n"
            f"Воронка: {payload.get('pipeline_records', '—')} записей · доход: {payload.get('income_records', '—')} записей\n"
            f"{payload.get('financial_history_status', '')}\n"
            "Это сводка по внесённым данным; пустой реестр не означает нулевой фактический доход."
        )[:3500]
    if kind in {"overdue_task", "task_due_today"}:
        label = "⚠️ Просрочена задача" if kind == "overdue_task" else "📌 Срок задачи сегодня"
        return (f"{label}: {payload.get('title', payload.get('task_id', 'задача'))}\n"
                f"Проект: {payload.get('project_id', '—')} · срок: {payload.get('due_date', '—')}\n"
                f"Следующий шаг: {payload.get('next_step') or 'не указан'}")[:3500]
    if kind.startswith("weekly_"):
        return f"🗓 {payload.get('title', 'Недельный обзор')}\n{payload.get('agenda', '')}"[:3500]
    return f"🔔 Напоминание\n{json.dumps(payload, ensure_ascii=False)[:3300]}"


def _telegram_send(token: str, chat_id: str, message: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    request = urllib.request.Request(url, data=json.dumps({"chat_id": chat_id, "text": message}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            result = json.loads(response.read())
    except Exception as exc:
        # Never expose the request URL: it contains the bot token.
        raise RuntimeError(type(exc).__name__) from None
    if not result.get("ok"):
        raise RuntimeError("TelegramAPIError")


def deliver_pending_telegram(
    db_path: Path,
    *,
    sender: Callable[[str, str, str], None] = _telegram_send,
    environ: dict[str, str] | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    env = os.environ if environ is None else environ
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = env.get("TELEGRAM_NOTIFY_CHAT_ID", "")
    allowed = {value.strip() for value in env.get("TELEGRAM_ALLOWED_USER_IDS", "").split(",") if value.strip()}
    if not token or not chat_id:
        return {"configured": False, "attempted": 0, "sent": 0, "error": "missing_configuration"}
    if chat_id not in allowed:
        return {"configured": True, "attempted": 0, "sent": 0, "error": "chat_not_allowlisted"}

    pending = pending_telegram_notifications(db_path, limit)
    sent = 0
    for item in pending:
        try:
            sender(token, chat_id, _format(item))
        except Exception as exc:
            mark_telegram_notification(db_path, item["event_key"], sent=False, error=type(exc).__name__)
            return {"configured": True, "attempted": sent + 1, "sent": sent,
                    "pending": len(pending) - sent, "error": type(exc).__name__}
        mark_telegram_notification(db_path, item["event_key"], sent=True)
        sent += 1
    return {"configured": True, "attempted": len(pending), "sent": sent,
            "pending": len(pending) - sent, "error": ""}
