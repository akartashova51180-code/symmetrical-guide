"""Managed OpenAI agent adapter for a SAVED Platform agent.

Explicitly uses the documented beta Agents API:
POST /v1/agents/sessions; GET /v1/agents/sessions/{id};
GET /v1/agents/sessions/{id}/items.
No automatic retries of session creation. Tools are cleared to prevent
unapproved external actions. Session limit is a provider best-effort limit.
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request

AGENTS_BASE = "https://api.openai.com/v1/agents"
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _request(method: str, path: str, data: dict | None = None) -> dict:
    key = os.environ.get("OPENAI_API_KEY", "")
    if not key:
        raise RuntimeError("OPENAI_API_KEY missing")
    headers = {"Authorization": "Bearer " + key, "OpenAI-Beta": "agents=v1"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        AGENTS_BASE + path,
        data=None if data is None else json.dumps(data,ensure_ascii=False).encode("utf-8"),
        headers=headers, method=method,
    )
    with urllib.request.urlopen(req, timeout=45) as response:
        return json.load(response)


def create_session(instruction: str, *, agent_id: str, cents: int) -> dict:
    if not agent_id or not SESSION_ID_PATTERN.fullmatch(agent_id):
        raise ValueError("Valid saved Platform agent ID required")
    if not 1 <= cents <= 30:
        raise ValueError("Session limit must be between 1 and 30 USD cents")
    if not instruction.strip() or len(instruction) > 16000:
        raise ValueError("Invalid assignment length")
    payload = {
        "agent_id": agent_id,
        "environment": {"type": "none"},
        "input": instruction,
        "stream": False,
        "spend_control": {"limit": cents},
        "agent": {
            "multi_agent": {"enabled": False},
            "tools": [],
            "instructions": (
                "Для этого поручения работай ТОЛЬКО с переданным текстом. "
                "Не меняй внешние системы, не запускай дополнительные агенты, "
                "не публикуй материалы и не утверждай непроверенные действия."
            ),
        },
    }
    return _request("POST", "/sessions", payload)


def session_status(session_id: str) -> dict:
    if not SESSION_ID_PATTERN.fullmatch(session_id):
        raise ValueError("Invalid session id")
    return _request("GET", "/sessions/" + urllib.parse.quote(session_id, safe=""))


def session_final_text(session_id: str) -> str | None:
    """Returns completed root final answer only; otherwise None."""
    if not SESSION_ID_PATTERN.fullmatch(session_id):
        raise ValueError("Invalid session id")
    page = _request("GET", "/sessions/" + urllib.parse.quote(session_id, safe="") + "/items")
    messages = []
    for item in page.get("data", []):
        if item.get("type") == "message" and item.get("role") == "assistant" and (
            item.get("phase") == "final_answer" and item.get("status") == "completed"
        ):
            text = "\n".join(c.get("text","") for c in item.get("content",[])
                             if c.get("type") == "output_text")
            if text.strip():
                messages.append(text.strip())
    # Fail closed on pagination rather than pretending to have the final output.
    if page.get("has_more"):
        return None
    return messages[-1] if messages else None
