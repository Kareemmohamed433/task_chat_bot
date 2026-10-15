"""Tests for task deduplication, idempotency, categorized task listing, overdue calculation, and archival."""

from datetime import datetime, timedelta
import pytest
from app.config import settings
from app.models import Employee, Task, RoleEnum
from app.services.authorization_service import AuthorizationService
from app.services.task_service import TaskService


# ---------------------------------------------------------------------------
# 1. Tests for the specific case: 'أنا خالد وش مهامي؟' and RBAC Target Queries
# ---------------------------------------------------------------------------

def test_fahad_claiming_khalid_identity_is_rejected_with_exact_rbac_message(client, db_session):
    """
    Specific User Requirement:
    Account in Streamlit is Fahad (user_id=3, Employee).
    Message is: 'أنا خالد وش مهامي؟'
    
    1. Must extract Khalid as target employee.
    2. Must NOT return Fahad's tasks as an answer to Khalid's tasks.
    3. Must check Fahad's permission to view Khalid's tasks before query.
    4. Must show clear rejection message:
       'أنت مسجّل الدخول بحساب فهد، وليس لديك صلاحية عرض مهام خالد.'
    5. Must preserve RBAC and not change authenticated user identity.
    """
    # Seed a task for Fahad and a task for Khalid
    task_fahad = Task(
        description="إعداد تقرير فهد للمشاريع",
        assignee_id=3,  # فهد
        creator_id=2,  # سارة
        due_at=datetime(2026, 10, 16, 10, 0, 0),
        status="معلقة",
    )
    task_khalid = Task(
        description="يراجع عرض السعر",
        assignee_id=4,  # خالد
        creator_id=2,  # سارة
        due_at=datetime(2026, 10, 16, 11, 0, 0),
        status="معلقة",
    )
    db_session.add_all([task_fahad, task_khalid])
    db_session.commit()

    payload = {
        "user_id": 3,  # Authenticated as Fahad
        "message": "أنا خالد وش مهامي؟",
    }
    response = client.post("/chat", json=payload)
    assert response.status_code == 200

    data = response.json()
    assert data["action"] == "forbidden"
    assert data["success"] is False
    assert len(data["tasks"]) == 0
    assert "أنت مسجّل الدخول بحساب فهد، وليس لديك صلاحية عرض مهام خالد" in data["reply"]
    # Fahad's tasks must NOT be in the reply
    assert "تقرير فهد" not in data["reply"]


def test_manager_viewing_khalid_tasks_is_authorized(client, db_session):
    """
    Manager (user_id=1, منيرة) has global authority to view Khalid's tasks
    when asked via 'أنا خالد وش مهامي؟' or 'وش مهام خالد؟'.
    """
    task_khalid = Task(
        description="مراجعة عرض السعر مع المورد",
        assignee_id=4,  # خالد
        creator_id=2,  # سارة
        due_at=datetime(2026, 10, 20, 10, 0, 0),
        status="معلقة",
    )
    db_session.add(task_khalid)
    db_session.commit()

    payload = {
        "user_id": 1,  # Authenticated as Manager Mounira
        "message": "وش مهام خالد؟",
    }
    response = client.post("/chat", json=payload)
    assert response.status_code == 200

    data = response.json()
    assert data["action"] in ["list_employee_tasks", "list_my_tasks"]
    assert data["success"] is True
    assert len(data["tasks"]) == 1
    assert data["tasks"][0]["assignee_id"] == 4
    assert data["tasks"][0]["description"] == "مراجعة عرض السعر مع المورد"
    assert "مراجعة عرض السعر مع المورد" in data["reply"]


def test_supervisor_same_department_vs_other_department_query(client, db_session):
    """
    Supervisor Sara (user_id=2, Projects):
    - Can view Khalid's tasks (Khalid is in Projects).
    - CANNOT view Noura's tasks (Noura is in Finance).
    """
    task_khalid = Task(
        description="مهمة خالد بالمشاريع",
        assignee_id=4,  # خالد (Projects)
        creator_id=2,
        due_at=datetime(2026, 10, 18, 9, 0, 0),
        status="معلقة",
    )
    task_noura = Task(
        description="مهمة نورة بالمالية",
        assignee_id=5,  # نورة (Finance)
        creator_id=6,
        due_at=datetime(2026, 10, 18, 9, 0, 0),
        status="معلقة",
    )
    db_session.add_all([task_khalid, task_noura])
    db_session.commit()

    # 1. Sara views Khalid (Same department: Projects) -> Allowed
    resp_khalid = client.post("/chat", json={"user_id": 2, "message": "اعرض مهام خالد"})
    assert resp_khalid.status_code == 200
    data_khalid = resp_khalid.json()
    assert data_khalid["action"] in ["list_employee_tasks", "list_my_tasks"]
    assert data_khalid["success"] is True
    assert len(data_khalid["tasks"]) == 1

    # 2. Sara views Noura (Cross department: Finance) -> Forbidden
    resp_noura = client.post("/chat", json={"user_id": 2, "message": "اعرض مهام نورة"})
    assert resp_noura.status_code == 200
    data_noura = resp_noura.json()
    assert data_noura["action"] == "forbidden"
    assert data_noura["success"] is False
    assert "سارة" in data_noura["reply"]
    assert "نورة" in data_noura["reply"]


def test_unknown_target_employee_requests_clarification(client, db_session):
    """Querying tasks for a non-existent name requests clarification."""
    payload = {
        "user_id": 1,  # Manager
        "message": "اعرض مهام سمير",
    }
    response = client.post("/chat", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["requires_clarification"] is True
    assert "سمير" in data["reply"]


# ---------------------------------------------------------------------------
# 2. Tests for Idempotency & Retries
# ---------------------------------------------------------------------------

def test_idempotent_retry_does_not_create_duplicate_task(client, db_session):
    """
    Sending the same request twice with the same idempotency key
    must return the existing task without creating a duplicate.
    """
    payload = {
        "user_id": 2,
        "message": "سجل مهمة لخالد بكرة الساعة 10 يراجع عرض السعر",
        "idempotency_key": "idemp-khalid-review-001",
    }

    # First request
    resp1 = client.post("/chat", json=payload)
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert data1["success"] is True
    created_id = data1["task_id"]

    count_after_1 = db_session.query(Task).count()
    assert count_after_1 == 1

    # Second request (retry with identical key)
    resp2 = client.post("/chat", json=payload)
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["success"] is True
    assert data2["task_id"] == created_id
    assert "تم إنشاء المهمة مسبقاً" in data2["reply"]

    # Database count must strictly remain 1
    count_after_2 = db_session.query(Task).count()
    assert count_after_2 == 1


def test_independent_tasks_with_same_description_and_date_are_permitted(client, db_session):
    """
    Legitimate separate tasks with identical descriptions and due dates
    must be permitted if submitted with different idempotency keys.
    """
    # Submission 1
    resp1 = client.post(
        "/chat",
        json={
            "user_id": 2,
            "message": "سجل مهمة لخالد بكرة الساعة 10 يراجع عرض السعر",
            "idempotency_key": "key-task-first-part",
        },
    )
    assert resp1.status_code == 200
    id1 = resp1.json()["task_id"]

    # Submission 2 (deliberately new task, different key and distinct time)
    resp2 = client.post(
        "/chat",
        json={
            "user_id": 2,
            "message": "سجل مهمة لخالد بكرة الساعة 11 يراجع عرض السعر",
            "idempotency_key": "key-task-second-part",
        },
    )
    assert resp2.status_code == 200
    id2 = resp2.json()["task_id"]

    assert id1 != id2
    assert db_session.query(Task).count() == 2


# ---------------------------------------------------------------------------
# 3. Tests for Categorized Task Listing & Single-Pass Display
# ---------------------------------------------------------------------------

def test_categorized_task_listing_displays_each_task_once(client, db_session):
    """
    Task listing must classify tasks into Overdue, Today, Upcoming, Completed
    and display each task exactly once with ID, description, due date, and status.
    """
    ref_dt = datetime(2026, 10, 14, 12, 0, 0)

    # 1. Overdue task (past due date, not completed)
    t_overdue = Task(
        description="مهمة سابقة متأخرة",
        assignee_id=4,
        creator_id=2,
        due_at=datetime(2026, 10, 14, 9, 0, 0),  # earlier today
        status="معلقة",
    )
    # 2. Due today (later today)
    t_today = Task(
        description="مهمة مستحقة اليوم بعد الظهر",
        assignee_id=4,
        creator_id=2,
        due_at=datetime(2026, 10, 14, 15, 0, 0),
        status="معلقة",
    )
    # 3. Upcoming task (future date)
    t_upcoming = Task(
        description="مهمة قادمة الأسبوع القادم",
        assignee_id=4,
        creator_id=2,
        due_at=datetime(2026, 10, 19, 10, 0, 0),
        status="معلقة",
    )
    # 4. Completed task (past due date, but completed)
    t_completed = Task(
        description="مهمة منجزة بنجاح",
        assignee_id=4,
        creator_id=2,
        due_at=datetime(2026, 10, 13, 10, 0, 0),
        status="مكتملة",
    )

    db_session.add_all([t_overdue, t_today, t_upcoming, t_completed])
    db_session.commit()

    resp = client.post(
        "/chat",
        json={"user_id": 4, "message": "وش مهامي؟"},
        headers={"X-Reference-Datetime": "2026-10-14 12:00:00"},
    )
    assert resp.status_code == 200
    data = resp.json()

    assert data["action"] == "list_my_tasks"
    assert len(data["tasks"]) == 4

    # Verify categories in formatted text response
    reply = data["reply"]
    assert "المهام المتأخرة" in reply
    assert "مهام مستحقة اليوم" in reply
    assert "المهام القادمة" in reply
    assert "المهام المكتملة" in reply

    # Each task ID must appear exactly once in bullet items
    assert reply.count(f"#{t_overdue.id}:") == 1
    assert reply.count(f"#{t_today.id}:") == 1
    assert reply.count(f"#{t_upcoming.id}:") == 1
    assert reply.count(f"#{t_completed.id}:") == 1


def test_task_listing_queries_assigned_tasks_only_not_created(client, db_session):
    """Ensure query returns tasks where assignee_id == user_id, NOT creator_id."""
    # Sara created a task for Khalid
    task_created_by_sara = Task(
        description="مهمة لخالد أنشأتها سارة",
        assignee_id=4,  # خالد
        creator_id=2,  # سارة
        due_at=datetime(2026, 10, 18, 10, 0, 0),
        status="معلقة",
    )
    # Task assigned to Sara
    task_assigned_to_sara = Task(
        description="مهمة لسارة الشخصية",
        assignee_id=2,  # سارة
        creator_id=1,  # منيرة
        due_at=datetime(2026, 10, 18, 10, 0, 0),
        status="معلقة",
    )
    db_session.add_all([task_created_by_sara, task_assigned_to_sara])
    db_session.commit()

    resp = client.post("/chat", json={"user_id": 2, "message": "وش مهامي؟"})
    assert resp.status_code == 200
    data = resp.json()

    task_ids = [t["id"] for t in data["tasks"]]
    assert task_assigned_to_sara.id in task_ids
    assert task_created_by_sara.id not in task_ids


# ---------------------------------------------------------------------------
# 4. Tests for Overdue Logic & Reversible Archival
# ---------------------------------------------------------------------------

def test_completed_task_is_never_overdue(db_session):
    """Completed tasks with past due dates must never be marked as overdue."""
    ref_dt = datetime(2026, 10, 14, 12, 0, 0)
    task = Task(
        description="مهمة منتهية",
        assignee_id=3,
        creator_id=2,
        due_at=datetime(2026, 10, 10, 10, 0, 0),
        status="مكتملة",
    )
    db_session.add(task)
    db_session.commit()

    derived = task.get_derived_status(ref_dt)
    assert derived == "مكتملة"
    assert derived != "متأخرة"


def test_uncompleted_task_past_due_is_overdue(db_session):
    """Uncompleted task with past due date has derived status 'متأخرة' while keeping original status."""
    ref_dt = datetime(2026, 10, 14, 12, 0, 0)
    task = Task(
        description="مهمة لم تنجز",
        assignee_id=3,
        creator_id=2,
        due_at=datetime(2026, 10, 10, 10, 0, 0),
        status="معلقة",
    )
    db_session.add(task)
    db_session.commit()

    assert task.status == "معلقة"
    assert task.get_derived_status(ref_dt) == "متأخرة"


def test_soft_archival_and_reversible_unarchival(db_session):
    """
    Test configurable soft archival of old completed tasks and reversible unarchival.
    """
    ref_dt = datetime(2026, 10, 14, 12, 0, 0)

    # 1. Old completed task (45 days old -> should be archived under 30-day retention)
    t_old_completed = Task(
        description="مهمة قديمة مكتملة",
        assignee_id=4,
        creator_id=2,
        due_at=ref_dt - timedelta(days=45),
        status="مكتملة",
        is_archived=False,
    )
    # 2. Recent completed task (5 days old -> should NOT be archived)
    t_recent_completed = Task(
        description="مهمة حديثة مكتملة",
        assignee_id=4,
        creator_id=2,
        due_at=ref_dt - timedelta(days=5),
        status="مكتملة",
        is_archived=False,
    )
    # 3. Old pending task (not completed -> by default not archived)
    t_old_pending = Task(
        description="مهمة قديمة ما زالت معلقة",
        assignee_id=4,
        creator_id=2,
        due_at=ref_dt - timedelta(days=45),
        status="معلقة",
        is_archived=False,
    )

    db_session.add_all([t_old_completed, t_recent_completed, t_old_pending])
    db_session.commit()

    # Archive tasks older than 30 days
    archived_count = TaskService.archive_old_tasks(
        db_session, retention_days=30, ref_dt=ref_dt, only_completed=True
    )
    assert archived_count == 1

    db_session.refresh(t_old_completed)
    db_session.refresh(t_recent_completed)
    db_session.refresh(t_old_pending)

    assert t_old_completed.is_archived is True
    assert t_recent_completed.is_archived is False
    assert t_old_pending.is_archived is False

    # Reversible unarchival test
    restored = TaskService.unarchive_task(db_session, t_old_completed.id)
    assert restored is not None
    assert restored.is_archived is False


def test_concurrent_idempotent_creation_race_recovery(client, db_session):
    """
    Verify that if a matching key already exists or is committed in parallel,
    the system cleanly returns the existing task without duplicate creation or 500 error.
    """
    key = "race-test-key-999"
    t1 = Task(
        description="مهمة فحص التزامن",
        assignee_id=4,
        creator_id=2,
        due_at=datetime(2026, 10, 18, 10, 0, 0),
        status="معلقة",
        idempotency_key=key,
    )
    db_session.add(t1)
    db_session.commit()

    resp = client.post(
        "/chat",
        json={
            "user_id": 2,
            "message": "سجل مهمة لخالد بكرة الساعة 10 يراجع عرض السعر",
            "idempotency_key": key,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "create_task"
    assert data["success"] is True
    assert data["task_id"] == t1.id
    assert db_session.query(Task).count() == 1

