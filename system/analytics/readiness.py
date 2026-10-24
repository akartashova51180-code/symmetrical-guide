#!/usr/bin/env python3
"""Read-only audit of portfolio analytics readiness using the standard library."""

from __future__ import annotations

import argparse
import csv
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SYSTEM = ROOT / "system"


def read_csv(name: str) -> list[dict[str, str]]:
    path = SYSTEM / name
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def clean(value: str | None) -> str:
    return (value or "").strip()


def is_open(status: str) -> bool:
    closed = ("готово", "выполнено", "закрыто", "отменено", "завершено")
    return not any(word in status.casefold() for word in closed)


def build_report(today: date | None = None) -> str:
    today = today or date.today()
    projects = read_csv("projects.csv")
    tasks = read_csv("tasks.csv")
    ideas = read_csv("ideas.csv")
    evidence = read_csv("evidence.csv")
    pipeline = read_csv("pipeline.csv")
    income = read_csv("income.csv")
    products = read_csv("product-opportunities.csv")

    active_tasks = [row for row in tasks if is_open(clean(row.get("статус")))]
    unscheduled_tasks = []
    overdue_tasks = []
    for row in active_tasks:
        raw_due = clean(row.get("срок"))
        try:
            due = date.fromisoformat(raw_due)
        except ValueError:
            unscheduled_tasks.append(row)
            continue
        if due < today:
            overdue_tasks.append(row)

    nonempty_pipeline = [row for row in pipeline if any(clean(value) for value in row.values())]
    nonempty_income = [row for row in income if any(clean(value) for value in row.values())]
    top_product = next(
        (row for row in products if "текущий приоритет" in clean(row.get("приоритет")).casefold()),
        None,
    )
    evidence_with_urls = [
        row for row in evidence
        if "http://" in " ".join(row.values()).casefold()
        or "https://" in " ".join(row.values()).casefold()
    ]
    market_evidence = [
        row for row in evidence
        if any(term in clean(row.get("тип")).casefold() for term in ("внеш", "рын", "конкур"))
    ]

    lines = [
        f"# Готовность портфельной аналитики — {today.isoformat()}",
        "",
        "Аудит использует только текущие CSV-реестры. Пустая таблица означает отсутствие записи в базе, а не доказательство отсутствия деятельности вне неё.",
        "",
        "## Что уже зафиксировано",
        "",
        f"- Проектов: **{len(projects)}**.",
        f"- Записей задач: **{len(tasks)}**; открыто: **{len(active_tasks)}**; без согласованной даты: **{len(unscheduled_tasks)}**; просрочено по датированным записям: **{len(overdue_tasks)}**.",
        f"- Идей и решений: **{len(ideas)}**.",
        f"- Продуктовых гипотез: **{len(products)}**.",
        f"- Источников/доказательств: **{len(evidence)}**; записей с URL: **{len(evidence_with_urls)}**; явно отмеченных как внешние/рыночные: **{len(market_evidence)}**.",
        "",
        "## Чего не хватает для бизнес-выводов",
        "",
        f"- Воронка клиентов: **{len(nonempty_pipeline)}** заполненных записей. Конверсию каналов, количество целевых обращений и спрос вычислить нельзя.",
        f"- Финансовая история: **{len(nonempty_income)}** заполненных записей. Выручку, чистый доход, средний чек и экономику услуги вычислить нельзя.",
        "- Статистика каналов: данных по показам, откликам, квалифицированным обращениям, предложениям и оплатам пока недостаточно для сравнения.",
        "- Рыночные подтверждения: нужны датированные внешние источники с географией, цитатами/числами и ограничениями; интерес к теме сам по себе не доказывает готовность платить.",
        "- Для каждой продуктовой гипотезы нужны результат теста, трудозатраты и фактические обращения/предложения/оплаты.",
        "",
        "## Ближайшие действия с данными",
        "",
        "1. Заполнять pipeline.csv по целевым компаниям и запросам: источник, дата, проблема, этап, следующее действие, предложение и фактический результат.",
        "2. Записывать предложения и поступления в income.csv отдельно; добавлять расходы, налоги, партнёрские выплаты и часы.",
        "3. Сохранять внешние подтверждения в evidence.csv: URL, дата, география, цитата/число, ограничение и связь с покупательским вопросом.",
        "4. Для P12/PR07 передать материалы «Директора по безопасности» и согласовать с партнёром состав работ, результаты, ответственность, границы и участие.",
        "5. Не покупать BI-сервис, пока бесплатного учёта реестров не хватает для конкретного регулярно повторяющегося расчёта.",
        "",
        "## Текущие приоритеты",
        "",
        "- Коммерческие цели владелицы: запросы компаний на подбор; затем HR-администрирование и структурирование бизнеса.",
        f"- Приоритет продуктовой мастерской: **{clean(top_product.get('направление')) if top_product else 'не указан'}**. Это исследовательский приоритет, не подтверждённый спрос и не прогноз выручки.",
        "- Для оценки эффективности считать целевые обращения, квалифицированные запросы, встречи, предложения, фактические оплаты, чистый доход, затраты времени и расходов.",
        "",
        "## Ограничения отчёта",
        "",
        "Отчёт не прогнозирует доход и не оценивает вероятность успеха. Все выводы ограничены локальными реестрами и должны обновляться по мере появления проверенных данных.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Write the report to a file")
    args = parser.parse_args()
    report = build_report()
    if args.output:
        destination = args.output if args.output.is_absolute() else ROOT / args.output
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(report, encoding="utf-8")
        print(f"Отчёт записан: {destination}")
    else:
        print(report, end="")


if __name__ == "__main__":
    main()
