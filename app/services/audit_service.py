"""Audit logging service for tracking actions and security events."""

import logging
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy.orm import Session
from app.models import AuditLog

logger = logging.getLogger(__name__)


class AuditService:
    """Service for logging security decisions, mutations, and authorization events."""

    @staticmethod
    def log(
        db: Session,
        user_id: Optional[int],
        action: str,
        authorized: bool = True,
        target_task_id: Optional[int] = None,
        details: Optional[str] = None,
    ) -> AuditLog:
        """Create and persist an audit log entry within the active transaction."""
        audit_entry = AuditLog(
            user_id=user_id,
            action=action,
            authorized=authorized,
            target_task_id=target_task_id,
            details=details,
            created_at=datetime.now(timezone.utc),
        )
        db.add(audit_entry)
        try:
            db.flush()
        except Exception as e:
            logger.warning("Failed to flush audit log: %s", e)
        return audit_entry
