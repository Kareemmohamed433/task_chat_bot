"""SQLAlchemy ORM models for Employee, Task, and AuditLog."""

from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, ForeignKey
from sqlalchemy.orm import relationship
from app.database import Base


class RoleEnum:
    """Canonical role constants supporting both Arabic and English."""
    MANAGER = "مدير"
    SUPERVISOR = "مشرف"
    EMPLOYEE = "موظف"

    @classmethod
    def normalize(cls, role_str: str) -> str:
        """Normalize role string to standard Arabic role term."""
        mapping = {
            "manager": cls.MANAGER,
            "مدير": cls.MANAGER,
            "supervisor": cls.SUPERVISOR,
            "مشرف": cls.SUPERVISOR,
            "employee": cls.EMPLOYEE,
            "موظف": cls.EMPLOYEE,
        }
        return mapping.get(role_str.strip().lower() if role_str else "", role_str)


class Employee(Base):
    """Employee record representing staff members, supervisors, and managers."""
    __tablename__ = "employees"

    user_id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, index=True)
    department = Column(String(100), nullable=False, index=True)
    role = Column(String(50), nullable=False)

    # Relationships
    assigned_tasks = relationship(
        "Task",
        foreign_keys="Task.assignee_id",
        back_populates="assignee",
        cascade="all, delete-orphan",
    )
    created_tasks = relationship(
        "Task",
        foreign_keys="Task.creator_id",
        back_populates="creator",
        cascade="all, delete-orphan",
    )
    audit_logs = relationship(
        "AuditLog",
        back_populates="employee",
        cascade="all, delete-orphan",
    )

    @property
    def normalized_role(self) -> str:
        return RoleEnum.normalize(self.role)

    @property
    def is_manager(self) -> bool:
        return self.normalized_role == RoleEnum.MANAGER

    @property
    def is_supervisor(self) -> bool:
        return self.normalized_role == RoleEnum.SUPERVISOR

    @property
    def is_employee(self) -> bool:
        return self.normalized_role == RoleEnum.EMPLOYEE

    def __repr__(self) -> str:
        return f"<Employee(id={self.user_id}, name='{self.name}', role='{self.role}', dept='{self.department}')>"


class Task(Base):
    """Task assigned to an employee with due date and status."""
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    description = Column(Text, nullable=False)
    assignee_id = Column(Integer, ForeignKey("employees.user_id"), nullable=False, index=True)
    creator_id = Column(Integer, ForeignKey("employees.user_id"), nullable=False, index=True)
    due_at = Column(DateTime, nullable=False, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    status = Column(String(50), default="معلقة", nullable=False)
    idempotency_key = Column(String(128), unique=True, nullable=True, index=True)
    is_archived = Column(Boolean, default=False, nullable=False, index=True)

    # Relationships
    assignee = relationship("Employee", foreign_keys=[assignee_id], back_populates="assigned_tasks")
    creator = relationship("Employee", foreign_keys=[creator_id], back_populates="created_tasks")
    audit_logs = relationship("AuditLog", back_populates="target_task")

    @property
    def assignee_name(self) -> Optional[str]:
        return self.assignee.name if self.assignee else None

    @property
    def creator_name(self) -> Optional[str]:
        return self.creator.name if self.creator else None

    def get_derived_status(self, ref_dt: Optional[datetime] = None) -> str:
        """
        Compute derived status based on current due date and status.
        Completed tasks are never overdue.
        Non-completed tasks past their due_at are 'متأخرة'.
        """
        if self.status == "مكتملة":
            return "مكتملة"

        from app.config import settings
        if ref_dt is None:
            ref_dt = settings.get_reference_datetime()

        tz = settings.get_timezone()
        due = self.due_at
        if due.tzinfo is None:
            due = due.replace(tzinfo=tz)
        if ref_dt.tzinfo is None:
            ref_dt = ref_dt.replace(tzinfo=tz)

        if due < ref_dt:
            return "متأخرة"
        return self.status

    def __repr__(self) -> str:
        return f"<Task(id={self.id}, desc='{self.description[:20]}', assignee={self.assignee_id}, due={self.due_at}, archived={self.is_archived})>"


class AuditLog(Base):
    """Audit log for security actions, authorization decisions, and mutations."""
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("employees.user_id"), nullable=True, index=True)
    action = Column(String(100), nullable=False, index=True)
    target_task_id = Column(Integer, ForeignKey("tasks.id"), nullable=True)
    authorized = Column(Boolean, nullable=False, default=True)
    details = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    # Relationships
    employee = relationship("Employee", back_populates="audit_logs")
    target_task = relationship("Task", back_populates="audit_logs")

    def __repr__(self) -> str:
        return f"<AuditLog(id={self.id}, user={self.user_id}, action='{self.action}', authorized={self.authorized})>"
