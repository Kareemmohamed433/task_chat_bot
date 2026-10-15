"""Role-Based Access Control (RBAC) and authorization service."""

import logging
from typing import Optional, Tuple
from sqlalchemy.orm import Query, Session
from app.models import Employee, Task, RoleEnum

logger = logging.getLogger(__name__)


class AuthorizationService:
    """Enforces authorization rules independently of LLM outputs or user message claims."""

    @staticmethod
    def validate_task_creation(
        sender: Employee, assignee: Employee
    ) -> Tuple[bool, Optional[str]]:
        """
        Validate whether the sender is authorized to assign a task to the assignee.
        
        Rules:
        - Employee: Can assign tasks to self only.
        - Supervisor: Can assign tasks to employees within their department.
        - Manager: Can assign tasks to any employee.
        """
        if sender.is_employee:
            if sender.user_id == assignee.user_id:
                return True, None
            return (
                False,
                f"غير مصرح لك كموظف بإسناد مهام لموظفين آخرين ({assignee.name}). يمكنك فقط تسجيل مهام لنفسك.",
            )

        if sender.is_supervisor:
            if sender.department == assignee.department:
                return True, None
            return (
                False,
                f"غير مصرح لك كمشرف في قسم '{sender.department}' بإسناد مهام لموظف في قسم '{assignee.department}' ({assignee.name}).",
            )

        if sender.is_manager:
            return True, None

        return False, "غير مصرح لك بإنشاء أو إسناد المهام في النظام."

    @staticmethod
    def validate_task_view(
        sender: Employee, target_employee: Employee
    ) -> Tuple[bool, Optional[str]]:
        """
        Validate whether sender is authorized to view tasks of target_employee.

        Rules:
        - Self-view: Always permitted.
        - Employee: Can view their own tasks only.
        - Supervisor: Can view tasks of employees within their department.
        - Manager: Can view tasks of any employee.
        """
        if sender.user_id == target_employee.user_id:
            return True, None

        if sender.is_employee:
            return (
                False,
                f"أنت مسجّل الدخول بحساب {sender.name}، وليس لديك صلاحية عرض مهام {target_employee.name}.",
            )

        if sender.is_supervisor:
            if sender.department == target_employee.department:
                return True, None
            return (
                False,
                f"أنت مسجّل الدخول بحساب {sender.name} (مشرف {sender.department})، وليس لديك صلاحية عرض مهام {target_employee.name} في قسم {target_employee.department}.",
            )

        if sender.is_manager:
            return True, None

        return (
            False,
            f"أنت مسجّل الدخول بحساب {sender.name}، وليس لديك صلاحية عرض مهام {target_employee.name}.",
        )

    @staticmethod
    def get_authorized_tasks_query(
        db: Session, sender: Employee, request_scope: str = "my_tasks"
    ) -> Tuple[bool, Optional[Query], Optional[str]]:
        """
        Build an authorized SQLAlchemy query for retrieving tasks based on sender's role.
        
        request_scope:
        - 'my_tasks': The sender is asking for their own tasks.
        - 'department_tasks': The sender is asking for their department tasks.
        - 'company_tasks': The sender is asking for all company tasks.
        """
        base_query = db.query(Task).filter(Task.is_archived == False)

        # Handling broad company-wide query requests (e.g., prompt injection or admin queries)
        if request_scope == "company_tasks":
            if not sender.is_manager:
                return (
                    False,
                    None,
                    f"عذراً {sender.name}، لا تملك صلاحية الاطلاع على كافة مهام الشركة. صلاحياتك الحالية: {sender.role}.",
                )
            return True, base_query.order_by(Task.due_at.asc()), None

        if request_scope == "department_tasks":
            if sender.is_employee:
                return (
                    False,
                    None,
                    f"عذراً {sender.name}، لا تملك صلاحية استعراض مهام القسم كاملة. يمكنك فقط استعراض مهامك الشخصية.",
                )
            if sender.is_supervisor:
                query = base_query.join(Task.assignee).filter(
                    Employee.department == sender.department
                )
                return True, query.order_by(Task.due_at.asc()), None
            if sender.is_manager:
                return True, base_query.order_by(Task.due_at.asc()), None

        # Standard 'my_tasks' scope (or fallback)
        # An employee only gets their assigned tasks
        if sender.is_employee:
            query = base_query.filter(Task.assignee_id == sender.user_id)
            return True, query.order_by(Task.due_at.asc()), None

        # A supervisor asking for my_tasks gets tasks assigned to them (or dept tasks if supervisor requests)
        # By default 'my_tasks' returns tasks assigned to sender
        query = base_query.filter(Task.assignee_id == sender.user_id)
        return True, query.order_by(Task.due_at.asc()), None

    @staticmethod
    def validate_task_completion(
        sender: Employee, task: Task
    ) -> Tuple[bool, Optional[str]]:
        """
        Validate whether sender is authorized to mark the given task as completed.

        Rules:
        - Assignee: Can complete their own assigned tasks.
        - Manager: Can complete any task in the company.
        - Supervisor: Can complete tasks assigned to employees within their department.
        - Employee: Cannot complete tasks assigned to other employees.
        """
        # 1. Assignee can always complete their own task
        if task.assignee_id == sender.user_id:
            return True, None

        # 2. Manager can complete any task
        if sender.is_manager:
            return True, None

        # 3. Supervisor can complete tasks in their department
        if sender.is_supervisor:
            assignee = task.assignee
            if assignee and assignee.department == sender.department:
                return True, None
            dept_name = assignee.department if assignee else "آخر"
            return (
                False,
                f"أنت مسجّل الدخول بحساب {sender.name} (مشرف {sender.department})، وليس لديك صلاحية تعديل مهمة لموظف في قسم {dept_name}.",
            )

        # 4. Regular employee cannot complete another employee's task
        assignee_name = task.assignee.name if task.assignee else f"مستخدم #{task.assignee_id}"
        return (
            False,
            f"أنت مسجّل الدخول بحساب {sender.name}، وليس لديك صلاحية تعديل أو إنجاز المهمة #{task.id} المسندة إلى {assignee_name}.",
        )
