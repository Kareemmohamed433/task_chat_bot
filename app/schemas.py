"""Pydantic schemas for request/response serialization and validation."""

from datetime import datetime, date
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


class IntentType(str, Enum):
    """Core intent types recognized by the assistant."""
    CREATE_TASK = "create_task"
    LIST_MY_TASKS = "list_my_tasks"
    LIST_TODAY_TASKS = "list_today_tasks"
    LIST_OVERDUE_TASKS = "list_overdue_tasks"
    LIST_EMPLOYEE_TASKS = "list_employee_tasks"
    COMPLETE_TASK = "complete_task"
    ASK_POLICY = "ask_policy"
    GREETING = "greeting"
    UNKNOWN_INTENT = "unknown_intent"


class ChatRequest(BaseModel):
    """Incoming chat request payload."""
    user_id: int = Field(..., description="ID of the authenticated sender")
    message: str = Field(..., min_length=1, description="Raw Arabic user message")
    idempotency_key: Optional[str] = Field(
        default=None, description="Client idempotency key to prevent duplicate creation"
    )


class TaskOut(BaseModel):
    """Representation of an employee task."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    description: str
    assignee_id: int
    assignee_name: Optional[str] = None
    creator_id: int
    creator_name: Optional[str] = None
    due_at: str
    status: str
    derived_status: Optional[str] = None
    is_overdue: bool = False
    is_archived: bool = False

    @field_validator("due_at", mode="before")
    @classmethod
    def coerce_due_at(cls, v):
        if isinstance(v, (datetime, date)):
            return v.strftime("%Y-%m-%d %H:%M:%S")
        return str(v) if v is not None else ""


class ChatResponse(BaseModel):
    """Standard unified response schema for all API actions."""
    action: str = Field(..., description="Action taken or detected")
    success: bool = Field(..., description="Whether the operation succeeded")
    task_id: Optional[int] = Field(default=None, description="Created or modified task ID if applicable")
    reply: str = Field(..., description="Arabic user-facing response message")
    tasks: List[TaskOut] = Field(default_factory=list, description="List of tasks if queried")
    requires_clarification: bool = Field(
        default=False, description="Flag indicating if user input requires clarification"
    )
    extractor_used: Optional[str] = Field(
        default=None, description="Extractor used to process request (llm, deterministic, fallback)"
    )


class ExtractedTaskData(BaseModel):
    """Structured data extracted for task creation."""
    assignee_name: Optional[str] = None
    task_description: Optional[str] = None
    due_date_expr: Optional[str] = None
    due_time_expr: Optional[str] = None
    is_self_assignment: bool = False
    missing_fields: List[str] = Field(default_factory=list)


class ExtractedIntent(BaseModel):
    """Validated intent and extracted payload."""
    intent: IntentType
    task_data: Optional[ExtractedTaskData] = None
    task_id: Optional[int] = None
    target_assignee: Optional[str] = None
    target_employee: Optional[str] = None
    policy_question: Optional[str] = None
    confidence: float = 1.0
    requires_clarification: bool = False
    clarification_prompt: Optional[str] = None
    extractor_used: Optional[str] = None

    def model_post_init(self, __context) -> None:
        if self.target_assignee and not self.target_employee:
            self.target_employee = self.target_assignee
        elif self.target_employee and not self.target_assignee:
            self.target_assignee = self.target_employee


class HealthResponse(BaseModel):
    """Health check response."""
    status: str
    database: str
    timezone: str
    mode: str


class EmployeeOut(BaseModel):
    """Public employee summary."""
    model_config = ConfigDict(from_attributes=True)

    user_id: int
    name: str
    department: str
    role: str

