"""06-AUTO-003: opt-in, durable, budget-gated, single-worker dispatcher.

Uses the documented OpenAI Responses API, not the unverified /v1/agents/sessions
prototype. This is an explicitly separate API executor: it does NOT remotely run
a saved ChatGPT/Platform agent or write to other GPT chats.

Run using: python -m corporate_brain.dispatcher --once
Production deployment and spend must be separately approved. No tools, browsing,
external writes or outbound messages are granted to the model.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path

from . import managed_agent

import psycopg

ROLES = {
    "00": ("Мозг-администратор", "Учитывай иерархию холдинга. Координируй, не утверждай решения собственника."),
    "coo": ("Штаб / COO", "Координация, ресурсы, ответственность и ограничения собственника 40 часов в неделю."),
    "marketing": ("Маркетинг", "Исследуй целевую аудиторию до стратегии, отделяй данные от гипотез."),
    "sales": ("Продажи", "Предлагай коммерческие действия без самостоятельной отправки клиентам."),
    "hr": ("HR", "Не раскрывай персональные данные кандидатов. Разделяй проверенные сведения и выводы."),
    "product": ("Продуктовый центр", "Проверяй проблему и покупателя до разработки продукта."),
    "content": ("Департамент позиционирования, PR и SEO", "Не публикуй материалы без решения собственника. Проверяй соответствие титульному бренду."),
    "legal": ("Юридический департамент", "Учитывай авторские и смежные права, договоры, защиту ИИ-контента; не выдавай экспертное заключение без источников."),
    "finance": ("Финансы", "Расчёты только на проверяемых исходных данных."),
    "security": ("Корпоративная безопасность", "Не выдавай гипотезы за установленные угрозы."),
    "music": ("Продюсерский центр", "Учитывай права авторов и лицензии. Не публикуй работы."),
    "audit": ("Матушка-гусыня", "Независимо оцени результаты, финансовый, временной и репутационный риск."),
}
SCHEMA = "brain_ops"
BASE_INSTRUCTIONS = (
    "Ты действуешь в рамках холдинга Анастасии Карташовой. "
    "Собственник принимает окончательные решения. Не утверждай, что "
    "действия совершены, если ты только предложил их. Не придумывай факты. "
    "Тон: ясный русский язык без сложного жаргона. "
    "Проверяй четыре точки перед предложением запуска: идея и хук, "
    "юридическая чистота, упаковка бренда и монетизация. "
    "Учитывай синергию продюсерского, HR и защитного направлений. "
    "Не отправляй сообщения, не публикуй контент и не совершай покупки. "
    "Указывай: результат, подтверждения, ограничения, следующий шаг."
)
REVIEW_INSTRUCTIONS = (
    "Ты независимый ревизор Матушка-гусыня, а не исполнитель. "
    "Проверяй соответствие заданию, неподтверждённые факты, "
    "ненужные расходы, риски репутации и времени собственника. "
    "Не ставь статус 'принято' без доказательств. "
    "Первая строка строго: ВЕРДИКТ: ПРИНЯТЬ, ВЕРДИКТ: ДОРАБОТАТЬ или "
    "ВЕРДИКТ: НЕДОСТАТОЧНО ДАННЫХ. "
    "Верни конкретный вердикт ПРИНЯТЬ / ДОРАБОТАТЬ / НЕДОСТАТОЧНО ДАННЫХ."
)
API_URL = "https://api.openai.com/v1/responses"


def _usd(name: str) -> Decimal:
    raw = os.environ.get(name, "").strip()
    try:
        amount = Decimal(raw)
    except (InvalidOperation, ValueError):
        raise RuntimeError(f"Required positive numeric setting: {name}") from None
    if not amount.is_finite() or amount <= 0:
        raise RuntimeError(f"Required positive numeric setting: {name}")
    return amount


def _settings() -> dict:
    if os.environ.get("BRAIN_DISPATCH_ENABLED") != "1":
        raise RuntimeError("Dispatcher disabled: set BRAIN_DISPATCH_ENABLED=1 after approval")
    for name in ("OPENAI_API_KEY", "CORPORATE_BRAIN_DATABASE_URL", "BRAIN_OPENAI_MODEL"):
        if not os.environ.get(name, "").strip():
            raise RuntimeError(f"Missing environment setting: {name}")
    return {
        "model": os.environ["BRAIN_OPENAI_MODEL"],
        "daily": _usd("BRAIN_DAILY_BUDGET_USD"),
        "per_run": _usd("BRAIN_MAX_CALL_USD"),
        "input_rate": _usd("BRAIN_INPUT_USD_PER_MILLION"),
        "output_rate": _usd("BRAIN_OUTPUT_USD_PER_MILLION"),
        "max_output": int(os.environ.get("BRAIN_MAX_OUTPUT_TOKENS", "1200")),
        "max_running": int(os.environ.get("BRAIN_MAX_RUNNING", "1")),
        "backend": os.environ.get("BRAIN_EXECUTION_BACKEND", "responses"),
    }


def estimate_reservation(payload: dict, settings: dict) -> Decimal:
    # Intentionally pessimistic input-token bound: UTF-8 byte length.
    # Model usage can vary. This is a preflight estimate, NOT a provider cap.
    input_bytes = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    cost = (Decimal(input_bytes) * settings["input_rate"]
            + Decimal(settings["max_output"]) * settings["output_rate"]) / Decimal(1_000_000)
    return cost.quantize(Decimal("0.000001"), rounding="ROUND_UP")


def _response_text(response: dict) -> str:
    pieces = []
    for item in response.get("output", []):
        if item.get("type") == "message":
            for part in item.get("content", []):
                if part.get("type") == "output_text":
                    pieces.append(part.get("text", ""))
    return "\n".join(pieces).strip()


def _remote(payload: dict) -> dict:
    request = urllib.request.Request(
        API_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": "Bearer " + os.environ["OPENAI_API_KEY"],
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)


def _make_payload(role: str, task: str, model: str, max_output: int, *, review: bool = False) -> dict:
    if review:
        instructions = BASE_INSTRUCTIONS + "\n" + REVIEW_INSTRUCTIONS
    else:
        name, rules = ROLES[role]
        instructions = BASE_INSTRUCTIONS + "\nРоль: " + name + ". " + rules
    return {
        "model": model,
        "instructions": instructions,
        "input": task,
        "max_output_tokens": max_output,
        "store": False,
    }


def route_role(instruction: str) -> str:
    """Conservative free routing: unclassified instructions go to HQ."""
    low = instruction.lower()
    keywords = (
        ("hr", ("ваканси", "кандидат", "подбор персонал", "резюме", "рекрут")),
        ("marketing", ("целевая аудитори", "спрос", "вордстат", "маркетинг")),
        ("sales", ("продаж", "клиент", "воронк", "коммерческ")),
        ("finance", ("бюджет", "себестоим", "прибыл", "финанс")),
        ("content", ("стать", "пост ", "контент", "публикац", "seo", "позиционирован", "пиар", "pr ")),
        ("legal", ("договор", "авторск", "лицензи", "юридическ", "правооблад")),
        ("product", ("мини-продукт", "прототип", "продукт", "сервис")),
        ("security", ("корпоративн", "безопасност", "угроз")),
        ("music", ("песн", "музык", "гимн", "трек")),
    )
    for role, markers in keywords:
        if any(marker in low for marker in markers):
            return role
    return "00"


def queue_task(conn, project_id: str, role_code: str, instruction: str, source_key: str) -> str:
    """Explicit, idempotent local enqueue. Does not invoke a paid API."""
    if role_code == "auto":
        role_code = route_role(instruction)
    if role_code not in ROLES or role_code == "audit":
        raise ValueError("Unknown executor role")
    if not project_id.strip() or not source_key.strip():
        raise ValueError("Project and unique source key are required")
    if not 5 <= len(instruction) <= 16000:
        raise ValueError("Instruction must be between 5 and 16000 characters")
    with conn.transaction():
        row = conn.execute("""
            INSERT INTO brain_ops.assignments(project_id,role_code,instruction,source_key)
            VALUES (%s,%s,%s,%s) ON CONFLICT(source_key) DO NOTHING
            RETURNING id
        """, (project_id, role_code, instruction, source_key)).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT id FROM brain_ops.assignments WHERE source_key=%s", (source_key,)
            ).fetchone()
        return str(row[0])


def _reserve_and_claim(conn, settings: dict):
    """Serialize budget checking and reservation across worker processes."""
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(6003, 1)")
        active = conn.execute("""
            SELECT COUNT(*) FROM brain_ops.assignments WHERE status='running'
        """).fetchone()[0]
        if active >= settings["max_running"]:
            return None
        row = conn.execute("""
            SELECT id,project_id,role_code,instruction
            FROM brain_ops.assignments WHERE status='queued'
            ORDER BY created_at,id FOR UPDATE SKIP LOCKED LIMIT 1
        """).fetchone()
        if not row:
            return None
        assignment_id, project_id, role, task = row
        if role not in ROLES or role == "audit":
            conn.execute("UPDATE brain_ops.assignments SET status='blocked',error_code='invalid_role',updated_at=now() WHERE id=%s", (assignment_id,))
            return None
        payload = _make_payload(role, "Проект: " + project_id + "\nПоручение: " + task,
                                settings["model"], settings["max_output"])
        reserved = estimate_reservation(payload, settings)
        if settings["backend"] == "managed":
            # Reserve the ENTIRE requested session cap, not a 1-cent estimate.
            # Otherwise multiple managed sessions could exceed the daily cap.
            reserved = settings["per_run"]
        today = dt.datetime.now(dt.timezone.utc).date()
        spent = conn.execute("""
            SELECT COALESCE(SUM(GREATEST(reserved_usd,COALESCE(actual_usd,0))),0)
            FROM brain_ops.budget_ledger WHERE day=%s
        """, (today,)).fetchone()[0]
        if reserved > settings["per_run"] or spent + reserved > settings["daily"]:
            # Keep queued; no paid call if budget is insufficient.
            return None
        conn.execute("""
            INSERT INTO brain_ops.budget_ledger(assignment_id,purpose,day,reserved_usd)
            VALUES (%s,'execute',%s,%s)
        """, (assignment_id, today, reserved))
        conn.execute("""
            UPDATE brain_ops.assignments
            SET status='running',backend=%s,attempts=attempts+1,started_at=now(),updated_at=now()
            WHERE id=%s
        """, (settings["backend"],assignment_id))
        return str(assignment_id), payload


def _usage_cost(response: dict, settings: dict) -> Decimal | None:
    usage = response.get("usage") or {}
    if not isinstance(usage.get("input_tokens"), int) or not isinstance(usage.get("output_tokens"), int):
        return None
    cost = (Decimal(usage["input_tokens"]) * settings["input_rate"]
            + Decimal(usage["output_tokens"]) * settings["output_rate"]) / Decimal(1_000_000)
    return cost.quantize(Decimal("0.000001"), rounding="ROUND_UP")


def _mark_uncertain(conn, assignment_id: str, phase: str):
    # Never auto-retry requests whose network outcome is uncertain.
    with conn.transaction():
        conn.execute("""
            UPDATE brain_ops.assignments
            SET status='blocked',error_code=%s,updated_at=now()
            WHERE id=%s AND status IN ('running','awaiting_review')
        """, (phase + "_unknown_remote_outcome", assignment_id))


def _execute_one(conn, settings: dict) -> bool:
    claimed = _reserve_and_claim(conn, settings)
    if claimed is None:
        return False
    assignment_id, payload = claimed
    if settings["backend"] == "managed":
        agent_id = os.environ.get("BRAIN_AGENT_ID", "")
        max_cents = int((settings["per_run"] * 100).to_integral_value(rounding="ROUND_FLOOR"))
        try:
            session = managed_agent.create_session(
                payload["input"], agent_id=agent_id, cents=max_cents)
        except Exception:
            _mark_uncertain(conn, assignment_id, "managed_submission")
            raise RuntimeError("Managed session creation uncertain; do not retry") from None
        sid = session.get("id")
        if not isinstance(sid, str) or not sid:
            _mark_uncertain(conn, assignment_id, "managed_id_missing")
            raise RuntimeError("Managed session ID missing; manual reconciliation") from None
        with conn.transaction():
            conn.execute("""
                UPDATE brain_ops.assignments SET response_id=%s,updated_at=now()
                WHERE id=%s AND status='running'
            """,(sid,assignment_id))
            conn.execute("""
                UPDATE brain_ops.budget_ledger SET response_id=%s
                WHERE assignment_id=%s AND purpose='execute'
            """,(sid,assignment_id))
        return True
    try:
        response = _remote(payload)
    except Exception:
        _mark_uncertain(conn, assignment_id, "execute")
        raise RuntimeError("OpenAI submission/response uncertain; manual reconciliation required") from None
    response_id = str(response.get("id", ""))
    text = _response_text(response)
    usage_cost = _usage_cost(response, settings)
    valid = bool(text) and response.get("status") == "completed" and bool(response_id)
    with conn.transaction():
        conn.execute("""
            UPDATE brain_ops.budget_ledger SET response_id=%s,actual_usd=%s
            WHERE assignment_id=%s AND purpose='execute'
        """, (response_id or None, usage_cost, assignment_id))
        conn.execute("""
            UPDATE brain_ops.assignments SET response_id=%s,result=%s,
              status=%s,error_code=%s,updated_at=now()
            WHERE id=%s AND status='running'
        """, (response_id or None, text or None,
              "awaiting_review" if valid else "blocked",
              None if valid else "incomplete_response", assignment_id))
    return True


def _resume_managed(conn, settings: dict) -> bool:
    """Reconcile one saved session, never re-submit the original paid task."""
    row = conn.execute("""
        SELECT id,response_id FROM brain_ops.assignments
        WHERE backend='managed' AND status='running'
          AND response_id IS NOT NULL AND result IS NULL
        ORDER BY started_at LIMIT 1
    """).fetchone()
    if row is None:
        return False
    aid,sid = row
    try:
        state = managed_agent.session_status(sid)
    except Exception:
        # Read error: leave running, try status read again later (not a paid retry).
        return False
    status = state.get("status")
    if status in ("failed", "budget_reached", "requires_action"):
        with conn.transaction():
            conn.execute("""
                UPDATE brain_ops.assignments SET status='blocked',error_code=%s,updated_at=now()
                WHERE id=%s AND status='running'
            """,("managed_"+status,aid))
        return True
    if status != "idle":
        return False
    try:
        text = managed_agent.session_final_text(sid)
    except Exception:
        return False
    if not text:
        return False
    usage = state.get("usage")
    # Model-specific pricing must be configured, and returned usage is provisional.
    cost = _usage_cost({"usage": usage}, settings) if usage else None
    with conn.transaction():
        conn.execute("""
            UPDATE brain_ops.assignments SET result=%s,status='awaiting_review',updated_at=now()
            WHERE id=%s AND status='running' AND result IS NULL
        """,(text,aid))
        conn.execute("""
            UPDATE brain_ops.budget_ledger SET actual_usd=%s
            WHERE assignment_id=%s AND purpose='execute'
        """,(cost,aid))
    return True


def _reserve_review(conn, settings: dict):
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(6003,1)")
        row = conn.execute("""
            SELECT id,project_id,role_code,instruction,result
            FROM brain_ops.assignments WHERE status='awaiting_review'
              AND review_response_id IS NULL
            ORDER BY updated_at,id FOR UPDATE SKIP LOCKED LIMIT 1
        """).fetchone()
        if not row:
            return None
        aid, project, role, task, result = row
        payload = _make_payload("audit",
            "Проект: "+project+"\nИсходное поручение: "+task+
            "\nРоль исполнителя: "+role+"\nРезультат на проверку:\n"+(result or ""),
            settings["model"], settings["max_output"], review=True)
        reserved = estimate_reservation(payload, settings)
        today = dt.datetime.now(dt.timezone.utc).date()
        spent = conn.execute("""
            SELECT COALESCE(SUM(GREATEST(reserved_usd,COALESCE(actual_usd,0))),0)
            FROM brain_ops.budget_ledger WHERE day=%s
        """, (today,)).fetchone()[0]
        if reserved > settings["per_run"] or spent + reserved > settings["daily"]:
            return None
        cur = conn.execute("""
            INSERT INTO brain_ops.budget_ledger(assignment_id,purpose,day,reserved_usd)
            VALUES (%s,'review',%s,%s) ON CONFLICT DO NOTHING RETURNING id
        """, (aid,today,reserved))
        if not cur.fetchone():
            # Previous review may have uncertain remote outcome: never resend.
            return None
        conn.execute("""
            UPDATE brain_ops.assignments SET status='running',updated_at=now()
            WHERE id=%s AND status='awaiting_review'
        """,(aid,))
        return str(aid),payload


def _review_one(conn, settings: dict) -> bool:
    prepared = _reserve_review(conn, settings)
    if prepared is None:
        return False
    aid, payload = prepared
    try:
        response = _remote(payload)
    except Exception:
        _mark_uncertain(conn, aid, "review")
        raise RuntimeError("OpenAI review uncertain; manual reconciliation required") from None
    rid = str(response.get("id", ""))
    text = _response_text(response)
    valid = bool(rid and text and response.get("status") == "completed")
    cost = _usage_cost(response,settings)
    # Verdict is advisory, never pretend it is a formally independent person.
    with conn.transaction():
        conn.execute("""
            UPDATE brain_ops.budget_ledger SET response_id=%s,actual_usd=%s
            WHERE assignment_id=%s AND purpose='review'
        """,(rid or None,cost,aid))
        conn.execute("""
            UPDATE brain_ops.assignments SET review_response_id=%s,review=%s,
             status=%s,error_code=%s,updated_at=now() WHERE id=%s AND status='running'
        """,(rid or None,text or None,
             ("completed" if text.splitlines()[0].strip() == "ВЕРДИКТ: ПРИНЯТЬ" else "rejected") if valid else "blocked",
             None if valid else "incomplete_review",aid))
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Private budget-gated task dispatcher")
    parser.add_argument("--once", action="store_true", help="Process at most one execution and one review")
    parser.add_argument("--enqueue", action="store_true", help="Store task without calling OpenAI")
    parser.add_argument("--project", default="")
    parser.add_argument("--role", default="")
    parser.add_argument("--source-key", default="")
    parser.add_argument("--instruction", default="")
    args = parser.parse_args()
    if args.enqueue:
        url = os.environ.get("CORPORATE_BRAIN_DATABASE_URL")
        if not url:
            raise RuntimeError("Missing database URL")
        with psycopg.connect(url) as conn:
            print(queue_task(conn,args.project,args.role,args.instruction,args.source_key))
        return 0
    if not args.once:
        parser.error("Use --enqueue or --once; automatic looping is intentionally not enabled")
    settings = _settings()
    if settings["backend"] not in ("responses", "managed"):
        raise RuntimeError("Unrecognized backend")
    if settings["backend"] == "managed" and not os.environ.get("BRAIN_AGENT_ID", ""):
        raise RuntimeError("Saved Platform agent ID required")
    if settings["backend"] == "managed" and not (Decimal("0.01") <= settings["per_run"] <= Decimal("0.30") and (settings["per_run"] * 100) % 1 == 0):
        raise RuntimeError("Managed agent requires a whole-cent per-run limit between $0.01 and $0.30")
    if not 1 <= settings["max_running"] <= 2 or not 128 <= settings["max_output"] <= 4096:
        raise RuntimeError("Unsafe concurrency/output configuration")
    with psycopg.connect(os.environ["CORPORATE_BRAIN_DATABASE_URL"]) as conn:
        resumed = _resume_managed(conn,settings) if settings["backend"] == "managed" else False
        execution = _execute_one(conn,settings)
        review = _review_one(conn,settings)
    print(json.dumps({"executed":execution,"reconciled":resumed,"reviewed":review},ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
