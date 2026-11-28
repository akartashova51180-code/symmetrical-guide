"""Offline tests: no network and no paid OpenAI calls."""
import datetime as dt
from decimal import Decimal
import pytest

from corporate_brain import dispatcher as d


def settings():
    return {
        "model": "test-model",
        "input_rate": Decimal("2.00"),
        "output_rate": Decimal("8.00"),
        "max_output": 1200,
        "daily": Decimal("0.30"),
        "per_run": Decimal("0.10"),
        "max_running": 1,
    }


@pytest.mark.parametrize("task,expected", [
    ("Подобрать кандидата на вакансию", "hr"),
    ("Провести исследование целевой аудитории", "marketing"),
    ("Подготовить коммерческое предложение по продажам", "sales"),
    ("Оценить себестоимость", "finance"),
    ("Создать мини-продукт", "product"),
    ("Подготовить песню", "music"),
    ("Вынести решение об общей структуре", "00"),
])
def test_route(task, expected):
    assert d.route_role(task) == expected


def test_cost_reservation_bounded():
    payload = d._make_payload("hr", "Подбор", "test-model", 1200)
    amount = d.estimate_reservation(payload, settings())
    assert Decimal("0") < amount <= Decimal("0.10")


def test_text_extraction():
    response = {"output": [{"type": "message", "content": [
        {"type": "output_text", "text": "Результат"},
        {"type": "refusal", "refusal": "ignored"},
    ]}]}
    assert d._response_text(response) == "Результат"


def test_usage_calculation():
    response = {"usage": {"input_tokens": 100, "output_tokens": 50}}
    assert d._usage_cost(response, settings()) == Decimal("0.000600")


def test_no_usage_is_not_fabricated():
    assert d._usage_cost({}, settings()) is None


def test_dispatcher_disabled_by_default(monkeypatch):
    monkeypatch.delenv("BRAIN_DISPATCH_ENABLED", raising=False)
    with pytest.raises(RuntimeError, match="disabled"):
        d._settings()


def test_dispatcher_requires_budget(monkeypatch):
    monkeypatch.setenv("BRAIN_DISPATCH_ENABLED", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "dummy-not-real")
    monkeypatch.setenv("CORPORATE_BRAIN_DATABASE_URL", "postgresql://unused")
    monkeypatch.setenv("BRAIN_OPENAI_MODEL", "test-model")
    monkeypatch.delenv("BRAIN_DAILY_BUDGET_USD", raising=False)
    with pytest.raises(RuntimeError, match="BRAIN_DAILY_BUDGET_USD"):
        d._settings()
