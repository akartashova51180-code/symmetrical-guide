import json
import pytest

from github_ingest import parse_issue_event


URL = "https://github.com/akartashova51180-code/symmetrical-guide/issues/9"


def payload(**changes):
    event = {
        "event_id": "SYNC-TEST-20261119-09", "project_id": "P00",
        "source": "GitHub", "source_ref": URL,
        "created_at": "2026-11-19T20:03:58Z",
        "version": 1, "type": "test_task",
        "approval": "yes, test only", "privacy": "public_synthetic",
        "text": "Synthetic transport check",
    }
    event.update(changes)
    return {
        "action": "opened",
        "repository": {"full_name": "akartashova51180-code/symmetrical-guide"},
        "sender": {"login": "akartashova51180-code"},
        "issue": {
            "html_url": URL,
            "user": {"login": "akartashova51180-code"},
            "body": "```sync-event\n" + json.dumps(event) + "\n```",
        },
    }


def parse(value):
    return parse_issue_event(
        value, "issues", "akartashova51180-code/symmetrical-guide",
        {"akartashova51180-code"},
    )


def test_github_ingress_parses_explicit_synthetic_event():
    assert parse(payload())["event_id"] == "SYNC-TEST-20261119-09"


@pytest.mark.parametrize("mutate", [
    lambda p: p["repository"].update(full_name="someone/other"),
    lambda p: p["sender"].update(login="attacker"),
    lambda p: p["issue"]["user"].update(login="attacker"),
    lambda p: p.update(action="deleted"),
    lambda p: p["issue"].update(body="No structured event"),
])
def test_github_ingress_ignores_irrelevant_issues(mutate):
    item = payload()
    mutate(item)
    assert parse(item) is None


@pytest.mark.parametrize("bad", [
    {"privacy": "restricted"},
    {"type": "decision"},
    {"approval": "auto"},
    {"source_ref": "https://another.example/issue"},
    {"project_id": "P01"},
])
def test_github_ingress_rejects_unsafe_payload(bad):
    item = payload(**bad)
    with pytest.raises(ValueError):
        parse(item)


def test_github_ingress_requires_explicit_allowlist():
    assert parse_issue_event(payload(), "issues", "", {"akartashova51180-code"}) is None
    assert parse_issue_event(payload(), "issues", "akartashova51180-code/symmetrical-guide", set()) is None


def test_github_ingress_requires_single_block():
    item = payload()
    item["issue"]["body"] *= 2
    with pytest.raises(ValueError, match="exactly one"):
        parse(item)
