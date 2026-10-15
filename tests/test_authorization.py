"""Tests for Role-Based Access Control (RBAC) and authorization rules."""

from datetime import datetime
from app.models import Employee, Task, AuditLog
from app.services.authorization_service import AuthorizationService


def test_employee_can_assign_to_self_only(db_session):
    """An employee can create tasks for themselves, but not for others."""
    emp_fahad = db_session.query(Employee).filter(Employee.user_id == 3).first()  # موظف
    emp_khaled = db_session.query(Employee).filter(Employee.user_id == 4).first()  # موظف
    emp_noura = db_session.query(Employee).filter(Employee.user_id == 5).first()  # موظف مالية

    # Self assignment allowed
    allowed, err = AuthorizationService.validate_task_creation(emp_fahad, emp_fahad)
    assert allowed is True
    assert err is None

    # Assignment to same-department colleague rejected
    allowed, err = AuthorizationService.validate_task_creation(emp_fahad, emp_khaled)
    assert allowed is False
    assert "غير مصرح لك كموظف" in err

    # Assignment to other department colleague rejected
    allowed, err = AuthorizationService.validate_task_creation(emp_fahad, emp_noura)
    assert allowed is False
    assert "غير مصرح لك كموظف" in err


def test_supervisor_department_boundary_enforcement(db_session):
    """A supervisor can create tasks in their own department, but not in other departments."""
    sup_sara = db_session.query(Employee).filter(Employee.user_id == 2).first()  # مشرف المشاريع
    emp_khaled = db_session.query(Employee).filter(Employee.user_id == 4).first()  # موظف المشاريع
    emp_noura = db_session.query(Employee).filter(Employee.user_id == 5).first()  # موظف المالية
    sup_abdulrahman = db_session.query(Employee).filter(Employee.user_id == 6).first()  # مشرف المالية

    # Allowed: Sara assigns to Khaled (both in Projects)
    allowed, err = AuthorizationService.validate_task_creation(sup_sara, emp_khaled)
    assert allowed is True
    assert err is None

    # Rejected: Sara assigns to Noura (Finance)
    allowed, err = AuthorizationService.validate_task_creation(sup_sara, emp_noura)
    assert allowed is False
    assert "خارج قسم" in err or "المشاريع" in err

    # Rejected: Sara assigns to Abdulrahman (Finance)
    allowed, err = AuthorizationService.validate_task_creation(sup_sara, sup_abdulrahman)
    assert allowed is False


def test_manager_cross_department_authority(db_session):
    """A manager has authority to create tasks for any employee across any department."""
    mgr_mounira = db_session.query(Employee).filter(Employee.user_id == 1).first()  # مدير
    emp_khaled = db_session.query(Employee).filter(Employee.user_id == 4).first()  # المشاريع
    emp_noura = db_session.query(Employee).filter(Employee.user_id == 5).first()  # المالية
    sup_abdulrahman = db_session.query(Employee).filter(Employee.user_id == 6).first()  # المالية

    for target in [mgr_mounira, emp_khaled, emp_noura, sup_abdulrahman]:
        allowed, err = AuthorizationService.validate_task_creation(mgr_mounira, target)
        assert allowed is True
        assert err is None


def test_task_listing_rbac_isolation(db_session):
    """Verify database query filtering strictly isolates tasks by role."""
    # Seed tasks for multiple employees
    t1 = Task(description="مهمة خالد", assignee_id=4, creator_id=2, due_at=datetime(2026, 10, 14, 10, 0), status="معلقة")
    t2 = Task(description="مهمة فهد", assignee_id=3, creator_id=2, due_at=datetime(2026, 10, 14, 11, 0), status="معلقة")
    t3 = Task(description="مهمة نورة", assignee_id=5, creator_id=6, due_at=datetime(2026, 10, 14, 12, 0), status="معلقة")
    db_session.add_all([t1, t2, t3])
    db_session.commit()

    emp_khaled = db_session.query(Employee).filter(Employee.user_id == 4).first()
    sup_sara = db_session.query(Employee).filter(Employee.user_id == 2).first()
    mgr_mounira = db_session.query(Employee).filter(Employee.user_id == 1).first()

    # Employee Khaled sees only his task
    _, q_khaled, _ = AuthorizationService.get_authorized_tasks_query(db_session, emp_khaled, "my_tasks")
    tasks_khaled = q_khaled.all()
    assert len(tasks_khaled) == 1
    assert tasks_khaled[0].id == t1.id

    # Supervisor Sara querying department tasks sees Projects tasks (t1, t2) but NOT Finance (t3)
    _, q_sara, _ = AuthorizationService.get_authorized_tasks_query(db_session, sup_sara, "department_tasks")
    tasks_sara = q_sara.all()
    assert len(tasks_sara) == 2
    dept_task_ids = [t.id for t in tasks_sara]
    assert t1.id in dept_task_ids
    assert t2.id in dept_task_ids
    assert t3.id not in dept_task_ids

    # Employee Khaled attempting company-wide tasks is blocked
    is_auth, _, err = AuthorizationService.get_authorized_tasks_query(db_session, emp_khaled, "company_tasks")
    assert is_auth is False
    assert "لا تملك صلاحية" in err

    # Manager Mounira can view company-wide tasks
    is_auth, q_mgr, _ = AuthorizationService.get_authorized_tasks_query(db_session, mgr_mounira, "company_tasks")
    assert is_auth is True
    assert len(q_mgr.all()) == 3
