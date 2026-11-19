import pytest

from github_transport import GitHubIssueTransport, GitHubDeliveryError, marker_for, from_environment


URL = "https://github.com/akartashova51180-code/symmetrical-guide/issues/42"


def event(**kw):
    out = {
        "event_id": "SYNC-TEST-20261119-42",
        "project_id": "P00",
        "source": "GitHub",
        "source_ref": URL,
        "created_at": "2026-11-19T20:03:58Z",
        "version": 1,
        "type": "test_task",
        "approval": "yes, test only",
        "privacy": "public_synthetic",
        "text": "synthetic data",
    }
    out.update(kw)
    return out


class MockGitHub:
    def __init__(self):
        self.comments = []
        self.writes = 0
        self.requests = []

    def __call__(self, method, path, body):
        self.requests.append((method, path))
        if method == "GET":
            assert path.startswith("issues/42/comments?per_page=100&page=")
            page = int(path.rsplit("&page=", 1)[1])
            return [{"body": text} for text in self.comments[(page-1)*100:page*100]]
        if method == "POST":
            assert path == "issues/42/comments"
            self.writes += 1
            self.comments.append(body["body"])
            return {"id": self.writes}
        raise AssertionError(method)


def make_transport(mock):
    return GitHubIssueTransport("akartashova51180-code/symmetrical-guide",
                                "fake-ci-test-token", request=mock)


def test_single_comment_and_idempotent_replay():
    mock = MockGitHub()
    t = make_transport(mock)
    assert t.deliver(event()) == "delivered"
    assert "подтверждение запуска" in mock.comments[0]
    assert t.deliver(event()) == "duplicate"
    assert mock.writes == 1


def test_comment_pagination_finds_existing_marker_on_second_page():
    mock = MockGitHub()
    mock.comments = ["other"]*100 + [marker_for(event())]
    assert make_transport(mock).deliver(event()) == "duplicate"
    assert mock.writes == 0


def test_comment_pagination_exhaustion_fails_closed():
    mock = MockGitHub()
    mock.comments = ["another"]*1000
    with pytest.raises(GitHubDeliveryError, match="pagination exhausted"):
        make_transport(mock).deliver(event())
    assert mock.writes == 0


@pytest.mark.parametrize("bad", [
    {"privacy": "internal"},
    {"type": "decision"},
    {"approval": "auto"},
    {"project_id": "P01"},
    {"source_ref": "https://github.com/another/repo/issues/42"},
    {"source_ref": "http://github.com/akartashova51180-code/symmetrical-guide/issues/42"},
    {"source_ref": URL + "/extra"},
    {"source_ref": URL + "?query=1"},
])
def test_outbound_never_posts_unsafe_or_external_data(bad):
    mock = MockGitHub()
    with pytest.raises(ValueError):
        make_transport(mock).deliver(event(**bad))
    assert mock.requests == []


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("CORPORATE_BRAIN_GITHUB_OUTBOUND_ENABLED", raising=False)
    monkeypatch.setenv("CORPORATE_BRAIN_GITHUB_REPO", "akartashova51180-code/symmetrical-guide")
    monkeypatch.setenv("CORPORATE_BRAIN_GITHUB_TOKEN", "fake-ci-test-token")
    with pytest.raises(RuntimeError, match="disabled"):
        from_environment(request=MockGitHub())
