"""Explicitly authorized single-task submission. No automatic retries."""
import json
import os
import urllib.request

import psycopg

def submit(assignment_id: str) -> str:
    agent_id = os.environ["BRAIN_AGENT_ID"]
    key = os.environ["OPENAI_API_KEY"]
    limit = int(os.environ["BRAIN_AGENT_LIMIT_CENTS"])
    if not agent_id or not key or not 1 <= limit <= 30:
        raise ValueError("Agent configuration or approved limit missing")
    with psycopg.connect(os.environ["CORPORATE_BRAIN_DATABASE_URL"]) as conn:
        with conn.cursor() as cur:
            cur.execute("""UPDATE brain_pilot.assignments
                SET status='running', updated_at=now()
                WHERE id=%s AND status='queued'
                RETURNING role_code, task""", (assignment_id,))
            row = cur.fetchone()
        if row is None:
            raise ValueError("Assignment is not queued")
    role, task = row
    payload = {
        "agent_id": agent_id,
        "environment": {"type": "none"},
        "input": f"Поручение {assignment_id}. Роль {role}. {task}\nПодготовь результат на русском. Не изменяй внешние системы, не запускай дополнительные платные операции. Отделяй факты от предположений.",
        "metadata": {"assignment_id": assignment_id},
        "spend_control": {"limit": limit},
        "stream": False,
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/agents/sessions",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {key}", "OpenAI-Beta": "agents=v1",
                 "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            session_id = json.load(response)["id"]
    except Exception:
        # Remote acceptance may be uncertain. Never resubmit automatically.
        with psycopg.connect(os.environ["CORPORATE_BRAIN_DATABASE_URL"]) as conn:
            conn.execute("""UPDATE brain_pilot.assignments SET status='blocked',
                result='Submission uncertain; reconcile remote session before retry',
                updated_at=now() WHERE id=%s AND status='running'""", (assignment_id,))
        raise RuntimeError("Submission requires reconciliation") from None
    with psycopg.connect(os.environ["CORPORATE_BRAIN_DATABASE_URL"]) as conn:
        conn.execute("""UPDATE brain_pilot.assignments SET result=%s, updated_at=now()
            WHERE id=%s AND status='running'""",
            (json.dumps({"session_id": session_id}), assignment_id))
    return session_id
