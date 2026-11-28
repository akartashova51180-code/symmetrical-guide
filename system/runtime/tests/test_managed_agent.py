"""Saved Platform agent adapter: offline contract tests only, zero API charges."""
import pytest
from corporate_brain import managed_agent as a


def test_saved_agent_request_has_no_tools(monkeypatch):
    observed = {}
    def fake_request(method, path, data=None):
        observed.update({"method":method,"path":path,"data":data})
        return {"id":"sess_offline_only","status":"in_progress"}
    monkeypatch.setattr(a, "_request", fake_request)
    result = a.create_session("Провести анализ кандидатов",agent_id="agent_test_123",cents=2)
    assert result["id"] == "sess_offline_only"
    assert observed["method"] == "POST"
    assert observed["path"] == "/sessions"
    assert observed["data"]["agent_id"] == "agent_test_123"
    assert observed["data"]["agent"]["tools"] == []
    assert observed["data"]["agent"]["multi_agent"]["enabled"] is False
    assert observed["data"]["spend_control"]["limit"] == 2


@pytest.mark.parametrize("id", ["", "../secrets", "a/2", "x"*129])
def test_invalid_id_rejected(id):
    with pytest.raises(ValueError):
        a.create_session("Five words of harmless instructions.",agent_id=id,cents=2)


def test_only_final_assistant_result_returned(monkeypatch):
    monkeypatch.setattr(a, "_request", lambda *args: {
        "data": [
            {"type":"message","role":"user","phase":None,"status":"completed",
             "content":[{"type":"input_text","text":"User question"}]},
            {"type":"message","role":"assistant","phase":"commentary","status":"completed",
             "content":[{"type":"output_text","text":"Thinking"}]},
            {"type":"message","role":"assistant","phase":"final_answer","status":"completed",
             "content":[{"type":"output_text","text":"Результат"}]},
        ], "has_more":False})
    assert a.session_final_text("sess_example") == "Результат"
