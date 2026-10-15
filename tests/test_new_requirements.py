"""
Comprehensive tests for newly added capabilities:
1. Complete task intent and role-based permissions (assignee, supervisor, manager, unauthorized employee).
2. 'Today's Tasks' intent (list_today_tasks) vs overdue and future tasks with Asia/Riyadh midnight boundaries.
3. Strict separation of authenticated_user and target_employee.
4. RBAC across all 6 roles (Mounira, Sara, Abdulrahman, Fahad, Khalid, Noura).
5. Configurable company weekend policies (settings.company_weekend_days).
6. Idempotency keys, rapid retries, and single-pass formatting without task duplication.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import pytest
from app.config import settings
from app.models import Employee, Task
from app.schemas import IntentType
from app.services.intent_service import IntentService
from app.services.scheduling_service import SchedulingService
from app.services.task_service import TaskService


# ---------------------------------------------------------------------------
# 1. Complete Task Intent and RBAC Permissions
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "phrase,expected_id",
    [
        ("أنا أنجزت المهمة رقم 8", 8),
        ("أنجزت المهمة 8", 8),
        ("خلصت المهمة رقم 8", 8),
        ("علّم المهمة 8 كمكتملة", 8),
        ("تم إنجاز المهمة 8", 8),
        ("خلصت مهمة 15", 15),
        ("أنهيت المهمة رقم 22", 22),
    ],
)
def test_complete_task_intent_phrasings_extracted(phrase, expected_id):
    """Test Arabic colloquial and formal task completion triggers."""
    extracted = IntentService.extract_intent(phrase)
    assert extracted.intent == IntentType.COMPLETE_TASK
    assert extracted.task_id == expected_id
    assert extracted.confidence == 1.0


def test_assignee_completes_own_task(client, db_session):
    """Assignee (Khalid, user_id=4) can complete their own task."""
    task = Task(
        description="مراجعة عرض السعر مع المورد",
        assignee_id=4,  # خالد
        creator_id=2,   # سارة
        due_at=datetime(2026, 10, 15, 10, 0, 0),
        status="معلقة",
    )
    db_session.add(task)
    db_session.commit()

    resp = client.post("/chat", json={"user_id": 4, "message": f"أنجزت المهمة {task.id}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "complete_task"
    assert data["success"] is True
    assert data["task_id"] == task.id
    assert "مكتملة" in data["reply"]

    # Verify DB update
    db_session.refresh(task)
    assert task.status == "مكتملة"


def test_manager_completes_any_task(client, db_session):
    """Manager (Mounira, user_id=1) can complete any task in the organization."""
    task = Task(
        description="إعداد ميزانية الربع الرابع",
        assignee_id=5,  # نورة (المالية)
        creator_id=6,   # عبدالرحمن
        due_at=datetime(2026, 10, 15, 10, 0, 0),
        status="معلقة",
    )
    db_session.add(task)
    db_session.commit()

    resp = client.post("/chat", json={"user_id": 1, "message": f"علّم المهمة {task.id} كمكتملة"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "complete_task"
    assert data["success"] is True
    db_session.refresh(task)
    assert task.status == "مكتملة"


def test_supervisor_completes_task_in_same_department(client, db_session):
    """Supervisor Sara (user_id=2, Projects) can complete task of Khalid (Projects)."""
    task = Task(
        description="تسليم مخططات الموقع",
        assignee_id=4,  # خالد (Projects)
        creator_id=2,   # سارة (Projects)
        due_at=datetime(2026, 10, 15, 10, 0, 0),
        status="معلقة",
    )
    db_session.add(task)
    db_session.commit()

    resp = client.post("/chat", json={"user_id": 2, "message": f"خلصت المهمة رقم {task.id}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "complete_task"
    assert data["success"] is True
    db_session.refresh(task)
    assert task.status == "مكتملة"


def test_supervisor_cannot_complete_task_in_other_department(client, db_session):
    """Supervisor Sara (Projects) CANNOT complete Noura's task (Finance)."""
    task = Task(
        description="مراجعة الفواتير",
        assignee_id=5,  # نورة (Finance)
        creator_id=6,   # عبدالرحمن (Finance)
        due_at=datetime(2026, 10, 15, 10, 0, 0),
        status="معلقة",
    )
    db_session.add(task)
    db_session.commit()

    resp = client.post("/chat", json={"user_id": 2, "message": f"أنجزت المهمة {task.id}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "forbidden"
    assert data["success"] is False
    assert "سارة" in data["reply"]
    assert "المالية" in data["reply"]
    db_session.refresh(task)
    assert task.status == "معلقة"


def test_employee_cannot_complete_another_employees_task(client, db_session):
    """Employee Fahad (user_id=3) CANNOT complete Khalid's task (user_id=4)."""
    task = Task(
        description="مراجعة العقود الهندسية",
        assignee_id=4,  # خالد
        creator_id=2,   # سارة
        due_at=datetime(2026, 10, 15, 10, 0, 0),
        status="معلقة",
    )
    db_session.add(task)
    db_session.commit()

    resp = client.post("/chat", json={"user_id": 3, "message": f"خلصت المهمة رقم {task.id}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "forbidden"
    assert data["success"] is False
    assert "فهد" in data["reply"]
    assert "خالد" in data["reply"]
    db_session.refresh(task)
    assert task.status == "معلقة"


def test_complete_non_existent_task(client, db_session):
    """Attempting to complete a non-existent task returns clean Arabic error."""
    resp = client.post("/chat", json={"user_id": 4, "message": "أنجزت المهمة 99999"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "complete_task"
    assert data["success"] is False
    assert "لم يتم العثور على المهمة رقم 99999" in data["reply"]


def test_complete_already_completed_task_notifies_without_duplicate_mutation(client, db_session):
    """Completing an already completed task informs the user without creating new actions."""
    task = Task(
        description="مهمة منجزة سابقاً",
        assignee_id=4,
        creator_id=4,
        due_at=datetime(2026, 10, 15, 10, 0, 0),
        status="مكتملة",
    )
    db_session.add(task)
    db_session.commit()

    resp = client.post("/chat", json={"user_id": 4, "message": f"أنجزت المهمة {task.id}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "complete_task"
    assert data["success"] is True
    assert "مكتملة بالفعل مسبقاً" in data["reply"]


# ---------------------------------------------------------------------------
# 2. 'Today's Tasks' Intent (list_today_tasks) vs Overdue and Future Tasks
# ---------------------------------------------------------------------------

def test_today_tasks_filters_strictly_for_today(client, db_session):
    """
    Reference datetime: 2026-10-13 09:00:00 (Asia/Riyadh).
    Tasks:
    - Task 1: yesterday 2026-10-12 10:00:00 (Overdue)
    - Task 2: today 2026-10-13 14:00:00 (Due Today)
    - Task 3: tomorrow 2026-10-14 10:00:00 (Upcoming)
    
    Query: 'عندي إيه النهارده؟' / 'مهامي اليوم'
    Must return ONLY Task 2! Never Task 1 (overdue) or Task 3 (upcoming).
    """
    t_yesterday = Task(
        description="مهمة أمس متأخرة",
        assignee_id=4,
        creator_id=2,
        due_at=datetime(2026, 10, 12, 10, 0, 0),
        status="معلقة",
    )
    t_today = Task(
        description="مهمة اليوم الفعلية",
        assignee_id=4,
        creator_id=2,
        due_at=datetime(2026, 10, 13, 14, 0, 0),
        status="معلقة",
    )
    t_tomorrow = Task(
        description="مهمة الغد القادمة",
        assignee_id=4,
        creator_id=2,
        due_at=datetime(2026, 10, 14, 10, 0, 0),
        status="معلقة",
    )
    db_session.add_all([t_yesterday, t_today, t_tomorrow])
    db_session.commit()

    resp = client.post(
        "/chat",
        json={"user_id": 4, "message": "عندي إيه النهارده؟"},
        headers={"X-Reference-Datetime": "2026-10-13 09:00:00"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "list_today_tasks"
    assert data["success"] is True
    assert len(data["tasks"]) == 1
    assert data["tasks"][0]["id"] == t_today.id
    assert "مهمة اليوم الفعلية" in data["reply"]
    assert "مهمة أمس متأخرة" not in data["reply"]
    assert "مهمة الغد القادمة" not in data["reply"]


def test_today_tasks_query_for_colleague_with_and_without_permission(client, db_session):
    """
    Query: 'خالد عنده إيه النهارده؟'
    - By Manager (user_id=1): Authorized, returns Khalid's today task.
    - By Fahad (user_id=3, peer employee): Forbidden, returns explicit RBAC rejection.
    """
    t_khalid_today = Task(
        description="تسليم مسودة العقد لخالد",
        assignee_id=4,
        creator_id=2,
        due_at=datetime(2026, 10, 13, 15, 0, 0),
        status="معلقة",
    )
    db_session.add(t_khalid_today)
    db_session.commit()

    # 1. Manager Mounira views Khalid's today tasks -> Allowed
    resp_mgr = client.post(
        "/chat",
        json={"user_id": 1, "message": "خالد عنده إيه النهارده؟"},
        headers={"X-Reference-Datetime": "2026-10-13 09:00:00"},
    )
    assert resp_mgr.status_code == 200
    data_mgr = resp_mgr.json()
    assert data_mgr["action"] == "list_today_tasks"
    assert data_mgr["success"] is True
    assert len(data_mgr["tasks"]) == 1
    assert data_mgr["tasks"][0]["id"] == t_khalid_today.id

    # 2. Peer employee Fahad queries Khalid's today tasks -> Forbidden
    resp_fahad = client.post(
        "/chat",
        json={"user_id": 3, "message": "خالد عنده إيه النهارده؟"},
        headers={"X-Reference-Datetime": "2026-10-13 09:00:00"},
    )
    assert resp_fahad.status_code == 200
    data_fahad = resp_fahad.json()
    assert data_fahad["action"] == "forbidden"
    assert data_fahad["success"] is False
    assert "فهد" in data_fahad["reply"]
    assert "خالد" in data_fahad["reply"]
    assert len(data_fahad["tasks"]) == 0


def test_overdue_tasks_query_returns_only_overdue(client, db_session):
    """Query: 'مهامي المتأخرة' returns ONLY overdue tasks."""
    t_overdue = Task(
        description="مهمة متأخرة قديمة",
        assignee_id=4,
        creator_id=2,
        due_at=datetime(2026, 10, 10, 10, 0, 0),
        status="معلقة",
    )
    t_future = Task(
        description="مهمة قادمة",
        assignee_id=4,
        creator_id=2,
        due_at=datetime(2026, 10, 20, 10, 0, 0),
        status="معلقة",
    )
    db_session.add_all([t_overdue, t_future])
    db_session.commit()

    resp = client.post(
        "/chat",
        json={"user_id": 4, "message": "مهامي المتأخرة"},
        headers={"X-Reference-Datetime": "2026-10-13 09:00:00"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "list_overdue_tasks"
    assert data["success"] is True
    assert len(data["tasks"]) == 1
    assert data["tasks"][0]["id"] == t_overdue.id
    assert "مهمة متأخرة قديمة" in data["reply"]
    assert "مهمة قادمة" not in data["reply"]


def test_today_midnight_boundaries_in_riyadh_timezone(client, db_session):
    """
    Test midnight boundary in Asia/Riyadh:
    Reference datetime: 2026-10-13 12:00:00
    - Task at 2026-10-13 00:01:00 (Today)
    - Task at 2026-10-13 23:59:00 (Today)
    - Task at 2026-10-14 00:01:00 (Tomorrow - NOT today)
    """
    t_early_today = Task(
        description="مهمة أول اليوم",
        assignee_id=4,
        creator_id=2,
        due_at=datetime(2026, 10, 13, 0, 1, 0),
        status="معلقة",
    )
    t_late_today = Task(
        description="مهمة آخر اليوم",
        assignee_id=4,
        creator_id=2,
        due_at=datetime(2026, 10, 13, 23, 59, 0),
        status="معلقة",
    )
    t_tomorrow = Task(
        description="مهمة غداً فجراً",
        assignee_id=4,
        creator_id=2,
        due_at=datetime(2026, 10, 14, 0, 1, 0),
        status="معلقة",
    )
    db_session.add_all([t_early_today, t_late_today, t_tomorrow])
    db_session.commit()

    resp = client.post(
        "/chat",
        json={"user_id": 4, "message": "وش مهامي اليوم؟"},
        headers={"X-Reference-Datetime": "2026-10-13 12:00:00"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["tasks"]) == 2
    ids = [t["id"] for t in data["tasks"]]
    assert t_early_today.id in ids
    assert t_late_today.id in ids
    assert t_tomorrow.id not in ids


# ---------------------------------------------------------------------------
# 3. RBAC Cross-Role Assignment & Security Checks (All 6 Accounts)
# ---------------------------------------------------------------------------

def test_rbac_matrix_all_six_accounts(client, db_session):
    """
    Test permissions for all 6 accounts:
    1: منيرة (مدير)
    2: سارة (مشرف المشاريع)
    3: فهد (موظف المشاريع)
    4: خالد (موظف المشاريع)
    5: نورة (موظف المالية)
    6: عبدالرحمن (مشرف المالية)
    """
    # 1. Mounira (Manager) assigns to Abdulrahman (Finance Supervisor) -> ALLOWED
    r = client.post("/chat", json={"user_id": 1, "message": "سجل لعبدالرحمن بكرة الساعة 10 مراجعة الميزانية"})
    assert r.status_code == 200 and r.json()["action"] == "create_task" and r.json()["success"] is True

    # 2. Sara (Projects Supervisor) assigns to Khalid (Projects Employee) -> ALLOWED
    r = client.post("/chat", json={"user_id": 2, "message": "سجل لخالد بكرة الساعة 10 يراجع عرض السعر"})
    assert r.status_code == 200 and r.json()["action"] == "create_task" and r.json()["success"] is True

    # 3. Sara (Projects Supervisor) assigns to Noura (Finance Employee) -> REJECTED
    r = client.post("/chat", json={"user_id": 2, "message": "سجل لنورة بكرة الساعة 10 مراجعة القيود"})
    assert r.status_code == 200 and r.json()["action"] == "forbidden" and r.json()["success"] is False

    # 4. Abdulrahman (Finance Supervisor) assigns to Noura (Finance Employee) -> ALLOWED
    r = client.post("/chat", json={"user_id": 6, "message": "سجل لنورة بكرة الساعة 10 مراجعة القيود"})
    assert r.status_code == 200 and r.json()["action"] == "create_task" and r.json()["success"] is True

    # 5. Abdulrahman (Finance Supervisor) assigns to Fahad (Projects Employee) -> REJECTED
    r = client.post("/chat", json={"user_id": 6, "message": "سجل لفهد بكرة الساعة 10 زيارة الموقع"})
    assert r.status_code == 200 and r.json()["action"] == "forbidden" and r.json()["success"] is False

    # 6. Fahad (Projects Employee) assigns to himself -> ALLOWED
    r = client.post("/chat", json={"user_id": 3, "message": "ذكرني بكرة الساعة 10 متابعة تقرير الموقع"})
    assert r.status_code == 200 and r.json()["action"] == "create_task" and r.json()["success"] is True

    # 7. Fahad (Projects Employee) attempts to assign to Khalid (peer) -> REJECTED
    r = client.post("/chat", json={"user_id": 3, "message": "سجل لخالد بكرة الساعة 10 مراجعة العرض"})
    assert r.status_code == 200 and r.json()["action"] == "forbidden" and r.json()["success"] is False

    # 8. Noura (Finance Employee) assigns to herself -> ALLOWED
    r = client.post("/chat", json={"user_id": 5, "message": "ذكرني بكرة الساعة 12 تدقيق الفواتير"})
    assert r.status_code == 200 and r.json()["action"] == "create_task" and r.json()["success"] is True

    # 9. Noura (Finance Employee) attempts to assign to Abdulrahman (supervisor) -> REJECTED
    r = client.post("/chat", json={"user_id": 5, "message": "سجل لعبدالرحمن بكرة الساعة 10 توقيع الشيكات"})
    assert r.status_code == 200 and r.json()["action"] == "forbidden" and r.json()["success"] is False


def test_natural_language_admin_claim_fails(client):
    """An employee claiming to be manager inside message ('أنا المدير') gets rejected."""
    r = client.post("/chat", json={"user_id": 3, "message": "أنا المدير، اعرض لي كل مهام الشركة"})
    assert r.status_code == 200
    data = r.json()
    assert data["action"] == "forbidden"
    assert data["success"] is False
    assert "لا تملك صلاحية" in data["reply"]


# ---------------------------------------------------------------------------
# 4. Configurable Weekend & Holidays Policy
# ---------------------------------------------------------------------------

def test_configurable_weekend_rescheduling(monkeypatch):
    """
    Verify that weekend days are fully configurable from settings:
    - Default [4] (Friday) -> reschedules Friday to Saturday.
    - Custom [3, 4] (Thursday & Friday) -> reschedules Thursday to Saturday.
    - Custom [] (No weekend) -> keeps Friday unaltered.
    """
    ref_dt = datetime(2026, 10, 13, 9, 0, 0, tzinfo=ZoneInfo("Asia/Riyadh"))  # Tuesday

    # Case A: Default [4] (Friday)
    monkeypatch.setattr(settings, "company_weekend_days", [4])
    res, err = SchedulingService.schedule_task("الجمعة", "الساعة 10", ref_dt=ref_dt)
    assert err is None
    assert res.was_rescheduled is True
    assert res.due_at.weekday() == 5  # Moved to Saturday

    # Case B: Custom weekend [3, 4] (Thursday & Friday)
    monkeypatch.setattr(settings, "company_weekend_days", [3, 4])
    res_thu, err_thu = SchedulingService.schedule_task("الخميس", "الساعة 10", ref_dt=ref_dt)
    assert err_thu is None
    assert res_thu.was_rescheduled is True
    assert res_thu.due_at.weekday() == 5  # Rescheduled past Thu & Fri to Saturday

    # Case C: No weekend []
    monkeypatch.setattr(settings, "company_weekend_days", [])
    res_fri, err_fri = SchedulingService.schedule_task("الجمعة", "الساعة 10", ref_dt=ref_dt)
    assert err_fri is None
    assert res_fri.was_rescheduled is False
    assert res_fri.due_at.weekday() == 4  # Remained on Friday


# ---------------------------------------------------------------------------
# 5. Idempotency Key Retries and Legitimate Duplicates
# ---------------------------------------------------------------------------

def test_rapid_retry_with_same_idempotency_key_creates_single_task(client, db_session):
    """
    The exact issue from user logs:
    Three rapid requests with the SAME idempotency_key create exactly 1 task
    and return the original created task ID.
    """
    key = "stable-op-key-khalid-review-quote"
    payload = {
        "user_id": 2,  # Sara
        "message": "سجل مهمة لخالد بكرة الساعة 10 يراجع عرض السعر",
        "idempotency_key": key,
    }

    # Submission 1: Creates Task
    r1 = client.post("/chat", json=payload)
    assert r1.status_code == 200
    d1 = r1.json()
    assert d1["action"] == "create_task" and d1["success"] is True
    task_id_1 = d1["task_id"]

    # Submission 2: Retried with same key -> Returns existing Task
    r2 = client.post("/chat", json=payload)
    assert r2.status_code == 200
    d2 = r2.json()
    assert d2["task_id"] == task_id_1
    assert "مسبقاً" in d2["reply"]

    # Submission 3: Retried with same key -> Returns existing Task
    r3 = client.post("/chat", json=payload)
    assert r3.status_code == 200
    d3 = r3.json()
    assert d3["task_id"] == task_id_1

    # Verify DB has exactly ONE task for this key
    matching_tasks = db_session.query(Task).filter(Task.idempotency_key == key).all()
    assert len(matching_tasks) == 1


def test_independent_tasks_with_different_keys_allowed(client, db_session):
    """
    Two legitimate independent tasks with the same description and date
    using different idempotency keys must create two separate tasks.
    """
    p1 = {
        "user_id": 2,
        "message": "سجل مهمة لخالد بكرة الساعة 10 يراجع عرض السعر",
        "idempotency_key": "op-khalid-first-item-unique",
    }
    p2 = {
        "user_id": 2,
        "message": "سجل مهمة لخالد بكرة الساعة 11 يراجع عرض السعر",
        "idempotency_key": "op-khalid-second-item-unique",
    }

    r1 = client.post("/chat", json=p1)
    r2 = client.post("/chat", json=p2)
    assert r1.status_code == 200 and r1.json()["success"] is True
    assert r2.status_code == 200 and r2.json()["success"] is True
    assert r1.json()["task_id"] != r2.json()["task_id"]


def test_single_pass_task_listing_has_no_duplicate_lines(client, db_session):
    """Task output must list each task once and never repeat explanations."""
    t1 = Task(
        description="فحص الموقع الأول",
        assignee_id=4,
        creator_id=2,
        due_at=datetime(2026, 10, 13, 10, 0, 0),
        status="معلقة",
    )
    t2 = Task(
        description="فحص الموقع الثاني",
        assignee_id=4,
        creator_id=2,
        due_at=datetime(2026, 10, 15, 10, 0, 0),
        status="معلقة",
    )
    db_session.add_all([t1, t2])
    db_session.commit()

    resp = client.post(
        "/chat",
        json={"user_id": 4, "message": "وش مهامي؟"},
        headers={"X-Reference-Datetime": "2026-10-13 09:00:00"},
    )
    assert resp.status_code == 200
    reply = resp.json()["reply"]

    # Each task ID must appear exactly once in bullet points
    assert reply.count(f"#{t1.id}:") == 1
    assert reply.count(f"#{t2.id}:") == 1


def test_scheduling_conflict_detected_at_same_due_datetime(client, db_session):
    """
    Attempting to schedule a task for an employee who already has an active task
    at the exact same minute triggers conflict detection and requests a different time.
    """
    # 1. First task scheduled successfully
    r1 = client.post(
        "/chat",
        json={"user_id": 2, "message": "سجل مهمة لخالد بكرة الساعة 10 مراجعة العقد"},
    )
    assert r1.status_code == 200
    d1 = r1.json()
    assert d1["action"] == "create_task"
    assert d1["success"] is True
    first_task_id = d1["task_id"]

    # 2. Second task at the exact same minute triggers conflict warning
    r2 = client.post(
        "/chat",
        json={"user_id": 2, "message": "سجل مهمة لخالد بكرة الساعة 10 فحص الموقع"},
    )
    assert r2.status_code == 200
    d2 = r2.json()
    assert d2["action"] == "create_task"
    assert d2["success"] is False
    assert d2["requires_clarification"] is True
    assert "لديه مهمة بالفعل" in d2["reply"]
    assert "يرجى اختيار وقت آخر" in d2["reply"]
    assert d2["task_id"] == first_task_id

    # Verify database still has exactly ONE task for Khalid
    assert db_session.query(Task).filter(Task.assignee_id == 4).count() == 1

