from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler

from .storage import create_notification, list_records, portfolio_summary, record_run
from .telegram_notifications import deliver_pending_telegram

MOSCOW = ZoneInfo("Europe/Moscow")


def run_morning_cycle(db_path: Path, now: datetime | None = None) -> dict:
    now = now or datetime.now(MOSCOW)
    started = now.isoformat(timespec="seconds")
    today = now.date()
    created = 0
    for task in list_records(db_path, "tasks"):
        data = task["data"]
        status = (data.get("статус") or "").casefold()
        due = task.get("due_date")
        is_closed = any(word in status for word in ("готово", "выполнено", "закрыто", "отменено", "завершено"))
        if not due or is_closed:
            continue
        due_day = date.fromisoformat(due)
        if due_day <= today:
            kind = "overdue_task" if due_day < today else "task_due_today"
            label = "Просрочена" if due_day < today else "Срок сегодня"
            created += int(create_notification(db_path, f"{kind}:{task['record_id']}:{today.isoformat()}", kind,
                {"task_id": task["record_id"], "project_id": task.get("project_id"), "title": task.get("title"),
                 "due_date": due, "status": label, "next_step": data.get("следующий_шаг", "")}))
    summary = portfolio_summary(db_path, today)
    created += int(create_notification(db_path, f"daily_brief:{today.isoformat()}", "daily_brief", summary))
    reviews = {
        0: ("weekly_monday", "Понедельник · план портфеля", "Выбрать результаты недели, сверить приоритеты, назначить задачи и сроки; решения утверждает Анастасия."),
        2: ("weekly_wednesday", "Среда · разбор хода работ", "Проверить блокеры, входящие сигналы, ресурсы и движение к целевым обращениям; предложить корректировки владельцу."),
        4: ("weekly_friday", "Пятница · аудит недели", "Сверить обещанное с фактом, задачи, обращения, встречи, предложения, оплаты, затраты и выводы; масштабировать или менять только по решению владелицы."),
    }
    if today.weekday() in reviews:
        kind, title, agenda = reviews[today.weekday()]
        created += int(create_notification(db_path, f"{kind}:{today.isoformat()}", kind,
            {"title": title, "agenda": agenda, "owner_decision_required": True}))
    delivery = deliver_pending_telegram(db_path)
    result = {"notifications_created": created, "summary": summary,
              "external_delivery": delivery.get("sent", 0) > 0, "telegram_delivery": delivery,
              "weekly_review": reviews.get(today.weekday(), (None,))[0]}
    record_run(db_path, "morning_cycle", started, "ok", result)
    return result


def start_scheduler(db_path: Path) -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone=MOSCOW, daemon=True)
    scheduler.add_job(run_morning_cycle, "cron", hour=9, minute=0, args=[db_path],
                      id="daily-morning-cycle", replace_existing=True, coalesce=True, misfire_grace_time=3600)
    scheduler.add_job(deliver_pending_telegram, "interval", minutes=5, args=[db_path],
                      id="telegram-notification-outbox", replace_existing=True,
                      coalesce=True, max_instances=1)
    scheduler.start()
    run_morning_cycle(db_path)
    return scheduler
