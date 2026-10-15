"""Tests for enhanced Arabic understanding, Groq LLM integration, and edge cases."""

import json
from unittest.mock import patch, MagicMock
import httpx
import pytest
from app.config import settings
from app.models import Task, Employee
from app.schemas import IntentType
from app.services.intent_service import LLMIntentExtractor, DeterministicIntentExtractor


def test_msg1_greeting_returns_natural_welcome_and_no_task(client, db_session):
    """1. 'اهلا' must return a friendly greeting without treating it as unknown or creating a task."""
    initial_tasks = db_session.query(Task).count()
    resp = client.post("/chat", json={"user_id": 2, "message": "اهلا"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "greeting"
    assert data["success"] is True
    assert "أهلاً" in data["reply"] or "اهلا" in data["reply"]
    assert "سارة" in data["reply"]  # Acknowledges sender by name
    assert data["requires_clarification"] is False
    assert db_session.query(Task).count() == initial_tasks


def test_msg2_create_task_with_tashkeel_and_relative_date(client, db_session):
    """2. 'سجّل مهمة لخالد بكرة الساعة 10 يراجع عرض السعر' creates task for Khalid at 10:00."""
    initial_tasks = db_session.query(Task).count()
    headers = {"X-Reference-Datetime": "2026-10-14 09:00:00"}  # Wednesday
    resp = client.post(
        "/chat",
        json={"user_id": 2, "message": "سجّل مهمة لخالد بكرة الساعة 10 يراجع عرض السعر"},
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "create_task"
    assert data["success"] is True
    assert data["task_id"] is not None
    assert "خالد" in data["reply"]
    assert "10:00" in data["reply"]

    # Verify task in database
    created = db_session.query(Task).filter(Task.id == data["task_id"]).first()
    assert created is not None
    assert created.assignee.name == "خالد"
    assert "يراجع عرض السعر" in created.description
    assert created.due_at.strftime("%Y-%m-%d %H:%M") == "2026-10-15 10:00"
    assert db_session.query(Task).count() == initial_tasks + 1


def test_msg3_create_task_explicit_date_preserved(client, db_session):
    """3. 'أنشئ مهمة لفهد لمراجعة التقرير يوم الخميس 15 أكتوبر 2026 الساعة 10 صباحًا' succeeds."""
    resp = client.post(
        "/chat",
        json={
            "user_id": 2,
            "message": "أنشئ مهمة لفهد لمراجعة التقرير يوم الخميس 15 أكتوبر 2026 الساعة 10 صباحًا",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "create_task"
    assert data["success"] is True
    assert "فهد" in data["reply"]

    task = db_session.query(Task).filter(Task.id == data["task_id"]).first()
    assert task is not None
    assert task.assignee.name == "فهد"
    assert task.due_at.strftime("%Y-%m-%d %H:%M") == "2026-10-15 10:00"


def test_msg4_ordinary_leave_with_typo_and_tashkeel(client):
    """4. 'كم يوم الزم أقدّم قبل الإجازة؟' returns 5 working days policy accurately without hallucinations."""
    resp = client.post(
        "/chat",
        json={"user_id": 5, "message": "كم يوم الزم أقدّم قبل الإجازة؟"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "ask_policy"
    assert data["success"] is True
    assert "خمسة أيام عمل" in data["reply"] or "5 أيام عمل" in data["reply"]
    assert "المشرف" in data["reply"]


def test_msg5_manager_creates_friday_task_rescheduled_to_saturday(client, db_session):
    """5a. 'سجّل لعبدالرحمن اجتماع مع المالية بعد بكرة 1 الظهر' by Manager Mounira (user_id=1)."""
    headers = {"X-Reference-Datetime": "2026-10-14 09:00:00"}  # Wednesday -> بعد بكرة = Friday 16 Oct
    resp = client.post(
        "/chat",
        json={"user_id": 1, "message": "سجّل لعبدالرحمن اجتماع مع المالية بعد بكرة 1 الظهر"},
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "create_task"
    assert data["success"] is True
    assert "عبدالرحمن" in data["reply"]
    assert "السبت" in data["reply"]
    assert "13:00" in data["reply"] or "01:00" in data["reply"] or "الظهر" in data["reply"]

    task = db_session.query(Task).filter(Task.id == data["task_id"]).first()
    assert task is not None
    assert task.assignee.name == "عبدالرحمن"
    assert task.due_at.strftime("%Y-%m-%d %H:%M") == "2026-10-17 13:00"


def test_msg5_supervisor_forbidden_cross_department_assignment(client):
    """5b. 'سجّل لعبدالرحمن اجتماع مع المالية بعد بكرة 1 الظهر' by Sara (user_id=2, Projects)."""
    resp = client.post(
        "/chat",
        json={"user_id": 2, "message": "سجّل لعبدالرحمن اجتماع مع المالية بعد بكرة 1 الظهر"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "forbidden"
    assert data["success"] is False
    assert "غير مصرح" in data["reply"]
    assert "المشاريع" in data["reply"]
    assert "المالية" in data["reply"]


def test_unknown_employee_triggers_clarification(client):
    """Unknown employee must request clarification and never invent an employee."""
    resp = client.post(
        "/chat",
        json={"user_id": 2, "message": "سجّل مهمة لأحمد بكرة الساعة 10"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "create_task"
    assert data["success"] is False
    assert data["requires_clarification"] is True
    assert "غير مسجل" in data["reply"]
    assert "أحمد" in data["reply"] or "احمد" in data["reply"]


def test_groq_llm_timeout_falls_back_safely(client, db_session, monkeypatch):
    """When Groq LLM times out, system executes deterministic fallback cleanly."""
    monkeypatch.setattr(settings, "intent_mode", "llm")
    monkeypatch.setattr(settings, "llm_api_key", "mock_key")

    with patch("httpx.Client.post", side_effect=httpx.TimeoutException("Read timeout")):
        resp = client.post(
            "/chat",
            json={"user_id": 2, "message": "سجل مهمة لخالد بكرة الساعة 10 يراجع عرض السعر"},
            headers={"X-Reference-Datetime": "2026-10-14 09:00:00"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "create_task"
        assert data["success"] is True
        assert data["extractor_used"] == "llm_fallback_to_deterministic"


def test_groq_llm_rate_limit_429_falls_back_safely(client, db_session, monkeypatch):
    """When Groq LLM returns 429 Rate Limit, system falls back safely."""
    monkeypatch.setattr(settings, "intent_mode", "llm")
    monkeypatch.setattr(settings, "llm_api_key", "mock_key")

    mock_resp = MagicMock()
    mock_resp.status_code = 429
    mock_resp.text = "Rate limit reached"

    with patch("httpx.Client.post", return_value=mock_resp):
        resp = client.post(
            "/chat",
            json={"user_id": 2, "message": "اهلا"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "greeting"
        assert data["success"] is True
        assert data["extractor_used"] == "llm_fallback_to_deterministic"


def test_groq_llm_auth_error_401_falls_back_safely(client, monkeypatch):
    """When Groq LLM returns 401 Unauthorized, system falls back safely."""
    monkeypatch.setattr(settings, "intent_mode", "llm")
    monkeypatch.setattr(settings, "llm_api_key", "invalid_key")

    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.text = "Invalid API key"

    with patch("httpx.Client.post", return_value=mock_resp):
        resp = client.post(
            "/chat",
            json={"user_id": 5, "message": "كم يوم لازم أقدّم قبل الإجازة؟"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "ask_policy"
        assert data["success"] is True
        assert data["extractor_used"] == "llm_fallback_to_deterministic"


def test_groq_llm_malformed_json_falls_back_safely(client, monkeypatch):
    """When Groq returns non-JSON or malformed output, system falls back safely."""
    monkeypatch.setattr(settings, "intent_mode", "llm")
    monkeypatch.setattr(settings, "llm_api_key", "mock_key")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": "This is not valid JSON {{{"}}]
    }

    with patch("httpx.Client.post", return_value=mock_resp):
        resp = client.post(
            "/chat",
            json={"user_id": 2, "message": "وش مهامي؟"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "list_my_tasks"
        assert data["success"] is True
        assert data["extractor_used"] == "llm_fallback_to_deterministic"


def test_idempotent_task_creation_prevents_duplicate(client, db_session):
    """Repeated calls with same idempotency_key must return same task without duplication."""
    key = "idem-test-arabic-12345"
    payload = {
        "user_id": 2,
        "message": "سجل مهمة لخالد بكرة الساعة 10 مراجعة العقد",
        "idempotency_key": key,
    }
    resp1 = client.post("/chat", json=payload)
    assert resp1.status_code == 200
    task_id1 = resp1.json()["task_id"]

    resp2 = client.post("/chat", json=payload)
    assert resp2.status_code == 200
    task_id2 = resp2.json()["task_id"]

    assert task_id1 == task_id2
    # Verify exactly 1 task exists with this key
    assert db_session.query(Task).filter(Task.idempotency_key == key).count() == 1


def test_colloquial_task_phrasings_supported(client, db_session):
    """Test colloquial variations: محتاج فلان يعمل, عايز أكلّف, رتّب اجتماع."""
    # 1. محتاج فلان يعمل
    resp1 = client.post(
        "/chat",
        json={"user_id": 2, "message": "محتاج خالد يعمل مراجعة العرض بكرة الساعة 10"},
    )
    assert resp1.status_code == 200
    assert resp1.json()["action"] == "create_task"

    # 2. رتّب اجتماع
    resp2 = client.post(
        "/chat",
        json={"user_id": 1, "message": "رتّب اجتماع مع المالية لعبدالرحمن بعد بكرة 1 الظهر"},
        headers={"X-Reference-Datetime": "2026-10-14 09:00:00"},
    )
    assert resp2.status_code == 200
    assert resp2.json()["action"] == "create_task"
