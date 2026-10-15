"""Tests for Streamlit client helper functions and backend integration."""

from unittest.mock import MagicMock, patch
import requests
from streamlit_app import check_backend_health, send_chat_message


def test_empty_or_whitespace_message_validation():
    """Empty or whitespace-only messages must be rejected locally before making an API call."""
    res1 = send_chat_message("http://127.0.0.1:8000", 2, "")
    assert res1["success"] is False
    assert res1["action"] == "validation_error"
    assert "غير فارغة" in res1["reply"]

    res2 = send_chat_message("http://127.0.0.1:8000", 2, "   \n\t  ")
    assert res2["success"] is False
    assert res2["action"] == "validation_error"


def test_send_chat_message_success_task_creation():
    """Verify successful task creation response parsing."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "action": "create_task",
        "success": True,
        "task_id": 10,
        "reply": "تم إنشاء المهمة وإسنادها إلى خالد.",
        "tasks": [],
        "requires_clarification": False,
    }

    with patch("requests.post", return_value=mock_resp) as mock_post:
        result = send_chat_message("http://127.0.0.1:8000", 2, "سجل مهمة لخالد بكرة")
        mock_post.assert_called_once_with(
            "http://127.0.0.1:8000/chat",
            json={"user_id": 2, "message": "سجل مهمة لخالد بكرة"},
            headers={"Content-Type": "application/json"},
            timeout=15.0,
        )
        assert result["success"] is True
        assert result["action"] == "create_task"
        assert result["task_id"] == 10
        assert "خالد" in result["reply"]


def test_send_chat_message_task_listing_structured_data():
    """Verify task listing parses structured task records."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "action": "list_my_tasks",
        "success": True,
        "task_id": None,
        "reply": "إليك قائمة المهام:",
        "tasks": [
            {
                "id": 1,
                "description": "مراجعة العرض",
                "assignee_id": 4,
                "assignee_name": "خالد",
                "creator_id": 2,
                "creator_name": "سارة",
                "due_at": "2026-10-14 10:00:00",
                "status": "معلقة",
            }
        ],
        "requires_clarification": False,
    }

    with patch("requests.post", return_value=mock_resp):
        result = send_chat_message("http://127.0.0.1:8000", 4, "وش مهامي؟")
        assert result["success"] is True
        assert result["action"] == "list_my_tasks"
        assert len(result["tasks"]) == 1
        assert result["tasks"][0]["assignee_name"] == "خالد"


def test_send_chat_message_connection_error_handled_safely():
    """Verify backend connection failure returns a clean error message without stack trace."""
    with patch("requests.post", side_effect=requests.exceptions.ConnectionError("Connection refused")):
        result = send_chat_message("http://127.0.0.1:8000", 2, "سجل مهمة")
        assert result["success"] is False
        assert result["action"] == "connection_error"
        assert "يتعذر الاتصال بالخادم" in result["reply"]
        assert "uvicorn" in result["reply"]


def test_send_chat_message_timeout_handled_safely():
    """Verify timeout exception returns an explicit timeout notification."""
    with patch("requests.post", side_effect=requests.exceptions.Timeout("Read timeout")):
        result = send_chat_message("http://127.0.0.1:8000", 2, "سجل مهمة")
        assert result["success"] is False
        assert result["action"] == "timeout"
        assert "مهلة الاتصال" in result["reply"]


def test_send_chat_message_malformed_json_response():
    """Verify invalid JSON from the server is handled gracefully."""
    mock_resp = MagicMock()
    mock_resp.status_code = 502
    mock_resp.json.side_effect = ValueError("Bad JSON")

    with patch("requests.post", return_value=mock_resp):
        result = send_chat_message("http://127.0.0.1:8000", 2, "سجل مهمة")
        assert result["success"] is False
        assert result["action"] == "error"
        assert "بيانات غير صالحة" in result["reply"]


def test_check_backend_health_connected():
    """Verify check_backend_health handles a healthy 200 response."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"status": "ok", "timezone": "Asia/Riyadh"}

    with patch("requests.get", return_value=mock_resp):
        health = check_backend_health("http://127.0.0.1:8000")
        assert health["connected"] is True
        assert health["data"]["timezone"] == "Asia/Riyadh"


def test_check_backend_health_disconnected():
    """Verify check_backend_health handles connection failure."""
    with patch("requests.get", side_effect=requests.exceptions.ConnectionError()):
        health = check_backend_health("http://127.0.0.1:8000")
        assert health["connected"] is False
        assert health["error"] == "Connection Refused"
