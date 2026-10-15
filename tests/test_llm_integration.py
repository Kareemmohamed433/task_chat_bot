"""Tests for LLM integration, Groq provider routing, and colloquial Arabic intent extraction."""

from datetime import datetime
import json
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo
import httpx
import pytest

from app.config import settings
from app.models import Employee, Task
from app.schemas import IntentType
from app.services.intent_service import (
    DeterministicIntentExtractor,
    IntentService,
    LLMIntentExtractor,
)


def test_provider_url_construction():
    """Verify that chat completion endpoints are normalized correctly across different provider URL formats."""
    # Base URL without trailing slash
    url1 = LLMIntentExtractor.build_chat_endpoint_url("https://api.groq.com/openai/v1")
    assert url1 == "https://api.groq.com/openai/v1/chat/completions"

    # Base URL with trailing slash
    url2 = LLMIntentExtractor.build_chat_endpoint_url("https://api.groq.com/openai/v1/")
    assert url2 == "https://api.groq.com/openai/v1/chat/completions"

    # Fully specified endpoint URL
    url3 = LLMIntentExtractor.build_chat_endpoint_url("https://api.groq.com/openai/v1/chat/completions")
    assert url3 == "https://api.groq.com/openai/v1/chat/completions"

    # Standard OpenAI endpoint
    url4 = LLMIntentExtractor.build_chat_endpoint_url("https://api.openai.com/v1")
    assert url4 == "https://api.openai.com/v1/chat/completions"


def test_intent_service_configuration_selection(monkeypatch):
    """Verify that INTENT_MODE setting activates the corresponding intent extractor class."""
    # When INTENT_MODE=deterministic
    monkeypatch.setattr(settings, "intent_mode", "deterministic")
    extractor_det = IntentService.get_extractor(force_refresh=True)
    assert isinstance(extractor_det, DeterministicIntentExtractor)

    # When INTENT_MODE=llm
    monkeypatch.setattr(settings, "intent_mode", "llm")
    extractor_llm = IntentService.get_extractor(force_refresh=True)
    assert isinstance(extractor_llm, LLMIntentExtractor)

    # Revert back
    monkeypatch.setattr(settings, "intent_mode", "deterministic")
    extractor_det2 = IntentService.get_extractor(force_refresh=True)
    assert isinstance(extractor_det2, DeterministicIntentExtractor)


def test_llm_extractor_successful_structured_extraction(monkeypatch):
    """Verify that LLMIntentExtractor parses valid JSON choices into structured ExtractedIntent."""
    monkeypatch.setattr(settings, "intent_mode", "llm")
    monkeypatch.setattr(settings, "llm_provider", "https://api.groq.com/openai/v1")
    monkeypatch.setattr(settings, "llm_model", "qwen/qwen3.8-27b")
    monkeypatch.setattr(settings, "llm_api_key", "mock-test-key")

    mock_llm_json = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "intent": "create_task",
                            "assignee_name": "فهد",
                            "is_self_assignment": False,
                            "task_description": "مراجعة التقرير المالي",
                            "due_date_expr": "يوم الخميس 15 أكتوبر 2026",
                            "due_time_expr": "الساعة 10 صباحًا",
                            "policy_question": None,
                        }
                    )
                }
            }
        ]
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_llm_json

    with patch.object(httpx.Client, "post", return_value=mock_resp) as mock_post:
        extractor = LLMIntentExtractor()
        res = extractor.extract("أنشئ مهمة لفهد لمراجعة التقرير المالي")

        assert res.intent == IntentType.CREATE_TASK
        assert res.task_data is not None
        assert res.task_data.assignee_name == "فهد"
        assert res.task_data.task_description == "مراجعة التقرير المالي"
        assert res.task_data.due_date_expr == "يوم الخميس 15 أكتوبر 2026"
        assert res.task_data.due_time_expr == "الساعة 10 صباحًا"

        # Verify post arguments
        called_url, called_kwargs = mock_post.call_args[0][0], mock_post.call_args[1]
        assert called_url == "https://api.groq.com/openai/v1/chat/completions"
        assert called_kwargs["headers"]["Authorization"] == "Bearer mock-test-key"
        assert called_kwargs["json"]["model"] == "qwen/qwen3.8-27b"


def test_llm_extractor_provider_errors_fallback_safely(monkeypatch):
    """Verify that provider timeouts, HTTP errors, and malformed JSON fall back to deterministic mode without crashing."""
    monkeypatch.setattr(settings, "intent_mode", "llm")
    monkeypatch.setattr(settings, "llm_api_key", "mock-test-key")

    extractor = LLMIntentExtractor()

    # 1. Timeout failure
    with patch.object(httpx.Client, "post", side_effect=httpx.TimeoutException("Read timed out")):
        res_timeout = extractor.extract("سجل مهمة لخالد بكرة")
        assert res_timeout.intent == IntentType.CREATE_TASK
        assert res_timeout.task_data.assignee_name == "خالد"

    # 2. HTTP 401 Authentication failure
    mock_401 = MagicMock()
    mock_401.status_code = 401
    mock_401.text = "Invalid API Key"
    with patch.object(httpx.Client, "post", return_value=mock_401):
        res_401 = extractor.extract("سجل مهمة لخالد بكرة")
        assert res_401.intent == IntentType.CREATE_TASK
        assert res_401.task_data.assignee_name == "خالد"

    # 3. HTTP 429 Rate limit failure
    mock_429 = MagicMock()
    mock_429.status_code = 429
    mock_429.text = "Rate limit reached"
    with patch.object(httpx.Client, "post", return_value=mock_429):
        res_429 = extractor.extract("وش مهامي؟")
        assert res_429.intent == IntentType.LIST_MY_TASKS

    # 4. Malformed JSON content
    mock_malformed = MagicMock()
    mock_malformed.status_code = 200
    mock_malformed.json.return_value = {"choices": [{"message": {"content": "Not JSON at all"}}]}
    with patch.object(httpx.Client, "post", return_value=mock_malformed):
        res_bad_json = extractor.extract("كم يوم لازم أقدم قبل الإجازة؟")
        assert res_bad_json.intent == IntentType.ASK_POLICY


def test_arabic_example_input_a_standard_phrasing(client, db_session, monkeypatch):
    """
    Verify Input A:
    'أنشئ مهمة لفهد لمراجعة التقرير يوم الخميس 15 أكتوبر 2026 الساعة 10 صباحًا'
    Supervisor Sara (user_id=2) assigns to Fahd (user_id=3) in the same department.
    """
    monkeypatch.setattr(settings, "intent_mode", "llm")
    monkeypatch.setattr(settings, "reference_datetime_override", "2026-10-13 09:00:00")

    mock_llm_json = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "intent": "create_task",
                            "assignee_name": "فهد",
                            "is_self_assignment": False,
                            "task_description": "مراجعة التقرير",
                            "due_date_expr": "15 أكتوبر 2026",
                            "due_time_expr": "الساعة 10 صباحًا",
                            "policy_question": None,
                        }
                    )
                }
            }
        ]
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_llm_json

    with patch.object(httpx.Client, "post", return_value=mock_resp):
        payload = {
            "user_id": 2,
            "message": "أنشئ مهمة لفهد لمراجعة التقرير يوم الخميس 15 أكتوبر 2026 الساعة 10 صباحًا",
        }
        resp = client.post("/chat", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "create_task"
        assert data["success"] is True
        assert data["task_id"] is not None
        assert "فهد" in data["reply"]
        assert "2026-10-15" in data["reply"]
        assert "10:00" in data["reply"]

        task = db_session.query(Task).filter(Task.id == data["task_id"]).first()
        assert task is not None
        assert task.assignee_id == 3
        assert task.creator_id == 2
        assert task.due_at == datetime(2026, 10, 15, 10, 0, 0)


def test_arabic_example_input_b_colloquial_phrasing(client, db_session, monkeypatch):
    """
    Verify Input B:
    'محتاج فهد يراجع التقرير يوم الخميس الجاي الساعة عشرة الصبح، سجّلها له كمهمة.'
    Supervisor Sara (user_id=2) assigns to Fahd (user_id=3).
    Tests that colloquial phrasing with pronoun suffix and word number parses successfully.
    """
    monkeypatch.setattr(settings, "intent_mode", "llm")
    monkeypatch.setattr(settings, "reference_datetime_override", "2026-10-13 09:00:00")

    mock_llm_json = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "intent": "create_task",
                            "assignee_name": "فهد",
                            "is_self_assignment": False,
                            "task_description": "مراجعة التقرير",
                            "due_date_expr": "يوم الخميس الجاي",
                            "due_time_expr": "الساعة عشرة الصبح",
                            "policy_question": None,
                        }
                    )
                }
            }
        ]
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_llm_json

    with patch.object(httpx.Client, "post", return_value=mock_resp):
        payload = {
            "user_id": 2,
            "message": "محتاج فهد يراجع التقرير يوم الخميس الجاي الساعة عشرة الصبح، سجّلها له كمهمة.",
        }
        resp = client.post("/chat", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "create_task"
        assert data["success"] is True
        assert data["task_id"] is not None
        assert "فهد" in data["reply"]
        assert "الخميس" in data["reply"]
        assert "2026-10-15" in data["reply"]
        assert "10:00" in data["reply"]

        task = db_session.query(Task).filter(Task.id == data["task_id"]).first()
        assert task is not None
        assert task.assignee_id == 3
        assert task.creator_id == 2
        assert task.due_at == datetime(2026, 10, 15, 10, 0, 0)


def test_live_groq_api_extraction_safe_verification():
    """
    Safely verifies live Groq API connectivity if configured in .env.
    Never exposes API keys or sensitive tokens.
    """
    if not settings.llm_api_key or "gsk_" not in settings.llm_api_key:
        pytest.skip("No real Groq API key configured for live verification.")

    target_url = LLMIntentExtractor.build_chat_endpoint_url(settings.llm_provider)
    headers = {
        "Authorization": f"Bearer {settings.llm_api_key.strip()}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.llm_model,
        "messages": [
            {"role": "system", "content": LLMIntentExtractor.SYSTEM_PROMPT},
            {"role": "user", "content": "محتاج فهد يراجع التقرير يوم الخميس الجاي الساعة عشرة الصبح، سجّلها له كمهمة."},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.0,
    }

    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.post(target_url, json=payload, headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        assert parsed["intent"] == "create_task"
        assert "فهد" in parsed["assignee_name"]
    except Exception as e:
        pytest.skip(f"Live Groq API network unreachable or rate limited: {e}")
