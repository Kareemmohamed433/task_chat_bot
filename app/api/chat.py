"""Chat endpoint for Arabic task management assistant."""

import logging
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, Header, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from app.config import settings
from app.database import get_db
from app.models import Employee
from app.schemas import ChatRequest, ChatResponse
from app.services.task_service import TaskService

router = APIRouter(tags=["Chat"])
logger = logging.getLogger(__name__)


@router.post(
    "/chat",
    response_model=ChatResponse,
    responses={
        200: {"model": ChatResponse, "description": "Processed chat response"},
        404: {"model": ChatResponse, "description": "Sender employee not found"},
        500: {"model": ChatResponse, "description": "Internal server error"},
    },
)
def process_chat(
    request: ChatRequest,
    db: Session = Depends(get_db),
    x_reference_datetime: Optional[str] = Header(None, alias="X-Reference-Datetime"),
    x_idempotency_key: Optional[str] = Header(None, alias="X-Idempotency-Key"),
    idempotency_key_header: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    """
    Process incoming chat requests for task creation, task listing, or policy inquiries.
    Enforces RBAC authorization, resolves relative dates, and ensures Friday rescheduling.
    Supports request idempotency via headers or body to prevent duplicate task creation.
    """
    try:
        # Determine idempotency key
        idempotency_key = request.idempotency_key or x_idempotency_key or idempotency_key_header
        if idempotency_key:
            idempotency_key = idempotency_key.strip()

        logger.info(
            "[CHAT-API] Incoming request: user_id=%d, msg_len=%d, idempotency_key=%s",
            request.user_id,
            len(request.message),
            idempotency_key,
        )

        # Determine reference datetime if passed via header or fallback to settings
        ref_dt = None
        if x_reference_datetime:
            try:
                clean_str = x_reference_datetime.strip()
                if "T" in clean_str:
                    ref_dt = datetime.fromisoformat(clean_str)
                else:
                    ref_dt = datetime.strptime(clean_str, "%Y-%m-%d %H:%M:%S")
                if ref_dt.tzinfo is None:
                    ref_dt = ref_dt.replace(tzinfo=settings.get_timezone())
            except Exception as e:
                logger.warning("Could not parse X-Reference-Datetime header: %s", e)

        # Verify sender existence
        sender = db.query(Employee).filter(Employee.user_id == request.user_id).first()
        if not sender:
            logger.warning("Rejected chat request from non-existent user_id: %s", request.user_id)
            err_response = ChatResponse(
                action="error",
                success=False,
                reply="المستخدم غير مسجل في قاعدة بيانات النظام.",
                tasks=[],
                requires_clarification=False,
            )
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content=err_response.model_dump(),
            )

        # Process message through TaskService
        response = TaskService.process_chat_message(
            db=db,
            user_id=request.user_id,
            message=request.message,
            ref_dt=ref_dt,
            idempotency_key=idempotency_key,
        )
        return response

    except Exception as e:
        logger.exception("Unexpected error in /chat endpoint: %s", e)
        db.rollback()
        # Controlled error response without leaking stack traces or credentials
        err_response = ChatResponse(
            action="error",
            success=False,
            reply="حدث خطأ غير متوقع أثناء معالجة طلبك. يرجى المحاولة لاحقاً.",
            tasks=[],
            requires_clarification=False,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=err_response.model_dump(),
        )
