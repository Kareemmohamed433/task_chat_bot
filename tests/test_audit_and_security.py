"""Tests for security edge cases, audit logging, database rollbacks, and health endpoint."""

from unittest.mock import patch
from app.models import AuditLog, Task
from app.schemas import ChatResponse


def test_invalid_api_user_id_handled_safely(client):
    """Calling /chat with a non-existent user_id should return 404 safely with clean JSON."""
    payload = {
        "user_id": 99999,
        "message": "وش مهامي؟",
    }
    response = client.post("/chat", json=payload)
    assert response.status_code == 404
    data = response.json()
    assert data["success"] is False
    assert data["action"] == "error"
    assert "غير مسجل" in data["reply"]


def test_health_check_endpoint(client):
    """Verify /health returns operational status and database connection."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["database"] == "connected"
    assert data["timezone"] == "Asia/Riyadh"


def test_audit_log_records_authorization_and_actions(client, db_session):
    """Verify audit logs are persisted for actions and security rejections."""
    # 1. Successful action
    client.post("/chat", json={"user_id": 2, "message": "سجل مهمة لخالد بكرة الساعة 10 يراجع عرض السعر"})

    # 2. Authorization rejection
    client.post("/chat", json={"user_id": 3, "message": "حط لخالد مهمة يطبع المخططات الأحد الجاي"})

    logs = db_session.query(AuditLog).all()
    assert len(logs) >= 2

    # Check authorized log
    auth_log = [l for l in logs if l.authorized is True and l.action == "create_task"]
    assert len(auth_log) > 0
    assert auth_log[0].user_id == 2
    assert auth_log[0].target_task_id is not None

    # Check unauthorized rejection log
    unauth_log = [l for l in logs if l.authorized is False]
    assert len(unauth_log) > 0
    assert unauth_log[0].user_id == 3


def test_unknown_intent_handled_gracefully(client):
    """Unrecognized queries outside system scope return clarification response."""
    payload = {
        "user_id": 2,
        "message": "ما هو سعر صرف العملات اليوم؟",
    }
    response = client.post("/chat", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["action"] == "unknown_intent"
    assert data["success"] is False
    assert data["requires_clarification"] is True
    assert "لم أفهم" in data["reply"]


def test_greeting_handled_naturally_and_creates_no_task(client, db_session):
    """Greetings return friendly greeting without creating tasks or treating as unknown."""
    initial_task_count = db_session.query(Task).count()
    payload = {
        "user_id": 2,
        "message": "اهلا",
    }
    response = client.post("/chat", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["action"] == "greeting"
    assert data["success"] is True
    assert "أهلاً" in data["reply"] or "اهلا" in data["reply"]
    # Verify no task was created
    assert db_session.query(Task).count() == initial_task_count


def test_database_rollback_on_unexpected_error(client, db_session):
    """Verify transaction rollback occurs if an error happens during chat processing."""
    with patch("app.services.task_service.TaskService.process_chat_message", side_effect=Exception("Database lock simulate")):
        response = client.post("/chat", json={"user_id": 2, "message": "سجل مهمة لخالد بكرة الساعة 10"})
        assert response.status_code == 500
        data = response.json()
        assert data["success"] is False
        assert "حدث خطأ غير متوقع" in data["reply"]
        # Ensure no orphan tasks were created
        assert db_session.query(Task).count() == 0
