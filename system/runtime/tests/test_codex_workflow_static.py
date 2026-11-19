"""Validate synthetic Codex workflow safety gates without invoking model/API."""
from pathlib import Path

import yaml


def test_codex_workflow_is_owner_labeled_and_read_only():
    target = Path(__file__).resolve().parents[3] / ".github/workflows/codex-synthetic-task.yml"
    wf = yaml.load(target.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert wf["on"] == {"issues": {"types": ["labeled"]}}
    codex = wf["jobs"]["codex"]
    condition = codex["if"]
    assert "github.event.label.name == 'codex-approved'" in condition
    assert "github.event.sender.login == 'akartashova51180-code'" in condition
    assert "github.event.issue.user.login == 'akartashova51180-code'" in condition
    assert codex["permissions"] == {"contents": "read"}
    action = next(s for s in codex["steps"] if s.get("id") == "codex")
    assert action["uses"] == "openai/codex-action@v1"
    assert action["with"]["sandbox"] == "read-only"
    assert action["with"]["safety-strategy"] == "drop-sudo"
    assert "github.event.issue.body" not in str(action)
    assert "github.event.comment.body" not in str(action)
    assert "secrets.OPENAI_API_KEY" in action["with"]["openai-api-key"]
    assert wf["jobs"]["receipt"]["permissions"] == {"issues": "write"}
