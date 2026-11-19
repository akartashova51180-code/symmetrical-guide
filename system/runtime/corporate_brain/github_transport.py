"""Server-only, opt-in GitHub Issue acknowledgment delivery.

A public comment means only that the *gateway* accepted a synthetic test
event. It never claims that Codex executed or even read the issue.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

try:
    from .sync_core import canonical_event
except ImportError:  # allow standalone tests
    from sync_core import canonical_event

ISSUE_NUMBER = re.compile(r"^[1-9][0-9]{0,9}$")


class GitHubDeliveryError(RuntimeError):
    pass


def marker_for(event: dict[str, Any], receipt_secret: str) -> str:
    """Keyed, stable marker that cannot be predicted from public issue data."""
    e = canonical_event(event)
    seed = json.dumps([e["event_id"], e["version"]], separators=(",", ":"))
    if not isinstance(receipt_secret, str) or len(receipt_secret) < 16:
        raise ValueError("separate receipt signing secret required")
    digest = hmac.new(receipt_secret.encode(), seed.encode(), hashlib.sha256).hexdigest()[:32]
    return "<!-- corporate-brain-gateway-ack:" + digest + " -->"


class GitHubIssueTransport:
    def __init__(self, repo: str, token: str, receipt_secret: str, receipt_actor: str,
                 request: Callable[[str, str, Any], Any] | None = None):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo or ""):
            raise ValueError("explicit owner/repository required")
        if not token or len(token) < 10:
            raise ValueError("server-only GitHub token required")
        if not receipt_secret or len(receipt_secret) < 16:
            raise ValueError("receipt signing secret must be configured")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", receipt_actor or ""):
            raise ValueError("trusted receipt actor required")
        self.repo = repo
        self._receipt_secret = receipt_secret
        self._receipt_actor = receipt_actor
        self._token = token
        self._request_override = request

    def _request(self, method: str, path: str, body: Any = None) -> Any:
        if self._request_override is not None:
            return self._request_override(method, path, body)
        url = "https://api.github.com/repos/" + self.repo + "/" + path
        headers = {
            "Authorization": "Bearer " + self._token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "corporate-brain-sync-gateway",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = Request(url, data=json.dumps(body).encode() if body is not None else None,
                      method=method, headers=headers)
        try:
            with urlopen(req, timeout=12) as resp:
                return json.loads(resp.read())
        except HTTPError as exc:
            raise GitHubDeliveryError(f"GitHub HTTP {exc.code}") from None
        except (URLError, TimeoutError):
            raise GitHubDeliveryError("GitHub network request failed") from None

    def _issue_number(self, event: dict[str, Any]) -> int:
        e = canonical_event(event)
        if e["type"] != "test_task" or e["privacy"] != "public_synthetic" or e["project_id"] != "P00":
            raise ValueError("outbound GitHub comments allowed for synthetic tests only")
        if e["approval"] != "yes, test only":
            raise ValueError("test-only marker required")
        parts = urlsplit(e["source_ref"])
        prefix = "/" + self.repo + "/issues/"
        if (parts.scheme != "https" or parts.netloc != "github.com" or
                parts.query or parts.fragment or not parts.path.startswith(prefix)):
            raise ValueError("event does not reference a GitHub issue in allowed repo")
        number = parts.path[len(prefix):]
        if not ISSUE_NUMBER.fullmatch(number):
            raise ValueError("invalid GitHub issue number")
        return int(number)

    def _existing(self, issue_num: int, marker: str) -> bool:
        # Exhaust bounded pages so an old marker is not hidden on page 2.
        # Fail closed if the thread is too large; never risk an extra comment.
        for page in range(1, 11):
            comments = self._request(
                "GET", f"issues/{issue_num}/comments?per_page=100&page={page}")
            if not isinstance(comments, list):
                raise GitHubDeliveryError("invalid GitHub comments response")
            if any(isinstance(x, dict)
                   and isinstance(x.get("user"), dict)
                   and x["user"].get("login") == self._receipt_actor
                   and marker in str(x.get("body", "")) for x in comments):
                return True
            if len(comments) < 100:
                return False
        raise GitHubDeliveryError("comment pagination exhausted; cannot confirm idempotency")

    def deliver(self, event: dict[str, Any]) -> str:
        """One safe test-only acknowledgment, then read back the actual comment."""
        issue_num = self._issue_number(event)
        marker = marker_for(event, self._receipt_secret)
        if self._existing(issue_num, marker):
            return "duplicate"
        message = (marker + "\n"
                   "Тестовый шлюз «Мозг-администратор» зарегистрировал событие "
                   "и подтвердил запись в локальном журнале. "
                   "Это **не** подтверждение запуска или выполнения Codex.")
        self._request("POST", f"issues/{issue_num}/comments", {"body": message})
        if not self._existing(issue_num, marker):
            raise GitHubDeliveryError("GitHub did not confirm comment persistence")
        return "delivered"


def from_environment(request: Callable | None = None) -> GitHubIssueTransport:
    if os.environ.get("CORPORATE_BRAIN_GITHUB_OUTBOUND_ENABLED") != "true":
        raise RuntimeError("GitHub outbound transport disabled")
    return GitHubIssueTransport(
        os.environ.get("CORPORATE_BRAIN_GITHUB_REPO", ""),
        os.environ.get("CORPORATE_BRAIN_GITHUB_TOKEN", ""),
        os.environ.get("CORPORATE_BRAIN_GITHUB_RECEIPT_SECRET", ""),
        os.environ.get("CORPORATE_BRAIN_GITHUB_RECEIPT_ACTOR", ""),
        request=request,
    )
