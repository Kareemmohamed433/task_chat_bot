"""Core task management workflow orchestrator."""

import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Tuple
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.config import settings
from app.models import Employee, Task
from app.schemas import ChatResponse, IntentType, TaskOut
from app.services.audit_service import AuditService
from app.services.authorization_service import AuthorizationService
from app.services.intent_service import IntentService
from app.services.policy_service import PolicyService
from app.services.scheduling_service import SchedulingService
from app.services.scope import is_company_scope

logger = logging.getLogger(__name__)


class TaskService:
    """Orchestrates intent extraction, RBAC authorization, scheduling, and database persistence."""

    @classmethod
    def process_chat_message(
        cls,
        db: Session,
        user_id: int,
        message: str,
        ref_dt: Optional[datetime] = None,
        idempotency_key: Optional[str] = None,
    ) -> ChatResponse:
        """
        Process an incoming chat message from an authenticated user.
        Derives role from the database, extracts intent, validates RBAC, and applies business rules.
        Supports request idempotency via idempotency_key.
        """
        # 1. Retrieve sender from database
        sender = db.query(Employee).filter(Employee.user_id == user_id).first()
        if not sender:
            AuditService.log(
                db,
                user_id=user_id,
                action="auth_failure",
                authorized=False,
                details=f"Sender user_id {user_id} not found in database.",
            )
            logger.warning("[TASK-FLOW: 1. AUTH-FAIL] Sender user_id=%s not in database.", user_id)
            return ChatResponse(
                action="error",
                success=False,
                reply="المستخدم غير مسجل في قاعدة بيانات النظام.",
                requires_clarification=False,
            )

        logger.info(
            "[TASK-FLOW: 1. AUTH-OK] Sender: user_id=%d, name=%s, role=%s, dept=%s",
            sender.user_id,
            sender.name,
            sender.role,
            sender.department,
        )

        # 1b. Idempotency: check BEFORE calling the LLM,
        # so retried requests return instantly without another Groq round-trip.
        if idempotency_key:
            scoped_key = f"{user_id}:{idempotency_key}"
            existing_task = (
                db.query(Task)
                .filter(
                    (Task.idempotency_key == idempotency_key)
                    | (Task.idempotency_key == scoped_key)
                )
                .first()
            )
            if existing_task:
                logger.info(
                    "[TASK-FLOW: IDEMPOTENT-MATCH] Matched key: %s (Task ID %d)",
                    idempotency_key,
                    existing_task.id,
                )
                return ChatResponse(
                    action="create_task",
                    success=True,
                    task_id=existing_task.id,
                    reply=f"تم إنشاء المهمة مسبقاً بنجاح وإسنادها إلى {existing_task.assignee.name if existing_task.assignee else 'الموظف'}.",
                    tasks=[TaskOut.model_validate(existing_task)],
                    requires_clarification=False,
                )

        # 2. Extract intent and structured entities
        extracted = IntentService.extract_intent(message, sender=sender)
        logger.info(
            "[TASK-FLOW: 2. INTENT-EXTRACTED] intent=%s, extractor=%s, confidence=%.2f",
            extracted.intent.value,
            extracted.extractor_used,
            extracted.confidence,
        )

        # 3. Handle Greeting intent
        if extracted.intent == IntentType.GREETING:
            AuditService.log(
                db,
                user_id=sender.user_id,
                action="greeting",
                authorized=True,
                details=f"Greeting from {sender.name}",
            )
            logger.info("[TASK-FLOW: 3. GREETING-REPLY] Responded with friendly welcome to %s", sender.name)
            return ChatResponse(
                action="greeting",
                success=True,
                reply=f"أهلاً وسهلاً بك يا {sender.name}! 👋 كيف يمكنني مساعدتك اليوم؟ يمكنك إسناد مهمة جديدة، أو استعراض مهامك الحالية، أو السؤال عن سياسة الشركة.",
                tasks=[],
                requires_clarification=False,
                extractor_used=extracted.extractor_used,
            )

        # 4. Handle Policy Q&A intent
        if extracted.intent == IntentType.ASK_POLICY:
            reply_text, is_supported = PolicyService.answer_question(message)
            AuditService.log(
                db,
                user_id=sender.user_id,
                action="ask_policy",
                authorized=True,
                details=f"Policy question asked (supported={is_supported})",
            )
            logger.info("[TASK-FLOW: 4. POLICY-REPLY] Policy question answered (supported=%s)", is_supported)
            return ChatResponse(
                action="ask_policy",
                success=True,
                reply=reply_text,
                tasks=[],
                requires_clarification=False,
                extractor_used=extracted.extractor_used,
            )

        # 5. Handle Task Completion intent
        if extracted.intent == IntentType.COMPLETE_TASK:
            if not extracted.task_id:
                return ChatResponse(
                    action="complete_task",
                    success=False,
                    reply="يرجى تحديد رقم المهمة المطلوب إنجازها (مثال: أنجزت المهمة 8).",
                    tasks=[],
                    requires_clarification=True,
                    extractor_used=extracted.extractor_used,
                )

            task = db.query(Task).filter(Task.id == extracted.task_id).first()
            if not task:
                AuditService.log(
                    db,
                    user_id=sender.user_id,
                    action="complete_task_not_found",
                    authorized=False,
                    details=f"Task #{extracted.task_id} not found in database.",
                )
                return ChatResponse(
                    action="complete_task",
                    success=False,
                    task_id=extracted.task_id,
                    reply=f"لم يتم العثور على المهمة رقم {extracted.task_id} في النظام. يرجى التأكد من رقم المهمة.",
                    tasks=[],
                    requires_clarification=False,
                    extractor_used=extracted.extractor_used,
                )

            is_auth, auth_err = AuthorizationService.validate_task_completion(sender, task)
            if not is_auth:
                AuditService.log(
                    db,
                    user_id=sender.user_id,
                    action="complete_task_unauthorized",
                    authorized=False,
                    target_task_id=task.id,
                    details=auth_err,
                )
                return ChatResponse(
                    action="forbidden",
                    success=False,
                    task_id=task.id,
                    reply=auth_err or "غير مصرح لك بتعديل أو إنجاز هذه المهمة.",
                    tasks=[],
                    requires_clarification=False,
                    extractor_used=extracted.extractor_used,
                )

            # Check if already completed
            if task.status == "مكتملة":
                t_out = TaskOut.model_validate(task)
                return ChatResponse(
                    action="complete_task",
                    success=True,
                    task_id=task.id,
                    reply=f"المهمة رقم {task.id} ({task.description}) مكتملة بالفعل مسبقاً.",
                    tasks=[t_out],
                    requires_clarification=False,
                    extractor_used=extracted.extractor_used,
                )

            # Mark task as completed
            task.status = "مكتملة"
            db.commit()
            db.refresh(task)

            AuditService.log(
                db,
                user_id=sender.user_id,
                action="complete_task",
                authorized=True,
                target_task_id=task.id,
                details=f"Task #{task.id} marked as completed by {sender.name}",
            )
            t_out = TaskOut.model_validate(task)
            return ChatResponse(
                action="complete_task",
                success=True,
                task_id=task.id,
                reply=f"تم إنجاز المهمة رقم {task.id} ({task.description}) بنجاح وتحديث حالتها إلى مكتملة.",
                tasks=[t_out],
                requires_clarification=False,
                extractor_used=extracted.extractor_used,
            )

        # 6. Handle Today's Tasks Listing intent (list_today_tasks)
        if extracted.intent == IntentType.LIST_TODAY_TASKS:
            if ref_dt is None:
                ref_dt = settings.get_reference_datetime()
            tz = settings.get_timezone()
            if ref_dt.tzinfo is None:
                ref_dt = ref_dt.replace(tzinfo=tz)
            today_date = ref_dt.date()

            target_name = extracted.target_employee or extracted.target_assignee
            if target_name:
                target_emp = cls._resolve_employee_by_name(db, target_name)
                if not target_emp:
                    return ChatResponse(
                        action="list_today_tasks",
                        success=False,
                        reply=f"لم يتم العثور على موظف باسم '{target_name}' في النظام. يرجى التحقق من الاسم وتحديده بوضوح.",
                        tasks=[],
                        requires_clarification=True,
                        extractor_used=extracted.extractor_used,
                    )
                is_auth, err_msg = AuthorizationService.validate_task_view(sender, target_emp)
                if not is_auth:
                    AuditService.log(
                        db,
                        user_id=sender.user_id,
                        action="view_tasks_unauthorized",
                        authorized=False,
                        details=f"User {sender.name} denied viewing today tasks of {target_emp.name}",
                    )
                    return ChatResponse(
                        action="forbidden",
                        success=False,
                        reply=err_msg or f"أنت مسجّل الدخول بحساب {sender.name}، وليس لديك صلاحية عرض مهام {target_emp.name}.",
                        tasks=[],
                        requires_clarification=False,
                        extractor_used=extracted.extractor_used,
                    )
                query_assignee_id = target_emp.user_id
                subject_name = target_emp.name
            else:
                query_assignee_id = sender.user_id
                subject_name = None

            all_user_tasks = (
                db.query(Task)
                .filter(
                    Task.assignee_id == query_assignee_id,
                    Task.is_archived == False,
                )
                .order_by(Task.due_at.asc())
                .all()
            )
            today_tasks = [t for t in all_user_tasks if t.due_at.date() == today_date]
            reply_text, task_out_list = cls.build_today_tasks_reply(today_tasks, ref_dt=ref_dt, subject_name=subject_name)
            AuditService.log(
                db,
                user_id=sender.user_id,
                action="list_today_tasks",
                authorized=True,
                details=f"Retrieved {len(today_tasks)} today tasks for {subject_name or sender.name}",
            )
            return ChatResponse(
                action="list_today_tasks",
                success=True,
                reply=reply_text,
                tasks=task_out_list,
                requires_clarification=False,
                extractor_used=extracted.extractor_used,
            )

        # 7. Handle Overdue Tasks Listing intent (list_overdue_tasks)
        if extracted.intent == IntentType.LIST_OVERDUE_TASKS:
            if ref_dt is None:
                ref_dt = settings.get_reference_datetime()
            tz = settings.get_timezone()
            if ref_dt.tzinfo is None:
                ref_dt = ref_dt.replace(tzinfo=tz)

            target_name = extracted.target_employee or extracted.target_assignee
            if target_name:
                target_emp = cls._resolve_employee_by_name(db, target_name)
                if not target_emp:
                    return ChatResponse(
                        action="list_overdue_tasks",
                        success=False,
                        reply=f"لم يتم العثور على موظف باسم '{target_name}' في النظام. يرجى التحقق من الاسم وتحديده بوضوح.",
                        tasks=[],
                        requires_clarification=True,
                        extractor_used=extracted.extractor_used,
                    )
                is_auth, err_msg = AuthorizationService.validate_task_view(sender, target_emp)
                if not is_auth:
                    AuditService.log(
                        db,
                        user_id=sender.user_id,
                        action="view_tasks_unauthorized",
                        authorized=False,
                        details=f"User {sender.name} denied viewing overdue tasks of {target_emp.name}",
                    )
                    return ChatResponse(
                        action="forbidden",
                        success=False,
                        reply=err_msg or f"أنت مسجّل الدخول بحساب {sender.name}، وليس لديك صلاحية عرض مهام {target_emp.name}.",
                        tasks=[],
                        requires_clarification=False,
                        extractor_used=extracted.extractor_used,
                    )
                query_assignee_id = target_emp.user_id
                subject_name = target_emp.name
            else:
                query_assignee_id = sender.user_id
                subject_name = None

            all_user_tasks = (
                db.query(Task)
                .filter(
                    Task.assignee_id == query_assignee_id,
                    Task.is_archived == False,
                )
                .order_by(Task.due_at.asc())
                .all()
            )
            overdue_tasks = [t for t in all_user_tasks if t.get_derived_status(ref_dt) == "متأخرة"]
            reply_text, task_out_list = cls.build_overdue_tasks_reply(overdue_tasks, ref_dt=ref_dt, subject_name=subject_name)
            AuditService.log(
                db,
                user_id=sender.user_id,
                action="list_overdue_tasks",
                authorized=True,
                details=f"Retrieved {len(overdue_tasks)} overdue tasks for {subject_name or sender.name}",
            )
            return ChatResponse(
                action="list_overdue_tasks",
                success=True,
                reply=reply_text,
                tasks=task_out_list,
                requires_clarification=False,
                extractor_used=extracted.extractor_used,
            )

        # 8. Handle Task Listing intents (list_employee_tasks, list_my_tasks) or company scope queries
        is_company_query = is_company_scope(message)
        if (
            extracted.intent in [
                IntentType.LIST_EMPLOYEE_TASKS,
                IntentType.LIST_MY_TASKS,
                IntentType.LIST_TODAY_TASKS,
                IntentType.LIST_OVERDUE_TASKS,
            ]
            or (extracted.intent == IntentType.UNKNOWN_INTENT and is_company_query)
            or is_company_query
        ):
            if is_company_query:
                is_auth, query, err_msg = AuthorizationService.get_authorized_tasks_query(
                    db, sender=sender, request_scope="company_tasks"
                )
                if not is_auth:
                    AuditService.log(
                        db,
                        user_id=sender.user_id,
                        action="list_tasks_rejected",
                        authorized=False,
                        details=f"Unauthorized company tasks query attempt by {sender.name}",
                    )
                    logger.warning("[TASK-FLOW: 8. LIST-REJECTED] User %s forbidden for company tasks", sender.name)
                    return ChatResponse(
                        action="forbidden",
                        success=False,
                        reply=err_msg or "غير مصرح لك باستعراض هذه المهام.",
                        tasks=[],
                        requires_clarification=False,
                        extractor_used=extracted.extractor_used,
                    )
                tasks = query.filter(Task.is_archived == False).all()
                reply_text, task_out_list = cls.build_categorized_tasks_reply(tasks, ref_dt=ref_dt, subject_name="الشركة")
                return ChatResponse(
                    action="list_my_tasks",
                    success=True,
                    reply=reply_text,
                    tasks=task_out_list,
                    requires_clarification=False,
                    extractor_used=extracted.extractor_used,
                )

            # Check if querying another employee's tasks or explicit identity claimed in message
            target_name = extracted.target_employee or extracted.target_assignee
            if target_name:
                target_emp = cls._resolve_employee_by_name(db, target_name)
                if not target_emp:
                    logger.info("[TASK-FLOW: 8. LIST-UNKNOWN-TARGET] Target employee '%s' not found.", target_name)
                    return ChatResponse(
                        action="list_employee_tasks" if extracted.intent == IntentType.LIST_EMPLOYEE_TASKS else "list_my_tasks",
                        success=False,
                        reply=f"لم يتم العثور على موظف باسم '{target_name}' في النظام. يرجى التحقق من الاسم وتحديده بوضوح.",
                        tasks=[],
                        requires_clarification=True,
                        extractor_used=extracted.extractor_used,
                    )

                # Validate viewing permission
                is_auth, err_msg = AuthorizationService.validate_task_view(sender, target_emp)
                if not is_auth:
                    AuditService.log(
                        db,
                        user_id=sender.user_id,
                        action="view_tasks_unauthorized",
                        authorized=False,
                        details=f"User {sender.name} denied viewing tasks of {target_emp.name}",
                    )
                    logger.warning("[TASK-FLOW: 8. LIST-FORBIDDEN] %s", err_msg)
                    return ChatResponse(
                        action="forbidden",
                        success=False,
                        reply=err_msg or f"أنت مسجّل الدخول بحساب {sender.name}، وليس لديك صلاحية عرض مهام {target_emp.name}.",
                        tasks=[],
                        requires_clarification=False,
                        extractor_used=extracted.extractor_used,
                    )

                # Authorized query for target_emp
                tasks = (
                    db.query(Task)
                    .filter(
                        Task.assignee_id == target_emp.user_id,
                        Task.is_archived == False,
                    )
                    .order_by(Task.due_at.asc())
                    .all()
                )
                AuditService.log(
                    db,
                    user_id=sender.user_id,
                    action="list_tasks",
                    authorized=True,
                    details=f"Retrieved {len(tasks)} tasks of {target_emp.name} for {sender.name}",
                )
                logger.info("[TASK-FLOW: 8. LIST-SUCCESS] Retrieved %d tasks of %s for %s", len(tasks), target_emp.name, sender.name)
                reply_text, task_out_list = cls.build_categorized_tasks_reply(tasks, ref_dt=ref_dt, subject_name=target_emp.name)
                return ChatResponse(
                    action="list_employee_tasks" if extracted.intent == IntentType.LIST_EMPLOYEE_TASKS else "list_my_tasks",
                    success=True,
                    reply=reply_text,
                    tasks=task_out_list,
                    requires_clarification=False,
                    extractor_used=extracted.extractor_used,
                )

            # Standard own tasks query
            tasks = (
                db.query(Task)
                .filter(
                    Task.assignee_id == sender.user_id,
                    Task.is_archived == False,
                )
                .order_by(Task.due_at.asc())
                .all()
            )
            AuditService.log(
                db,
                user_id=sender.user_id,
                action="list_tasks",
                authorized=True,
                details=f"Retrieved {len(tasks)} personal tasks for {sender.name}.",
            )
            logger.info("[TASK-FLOW: 8. LIST-SUCCESS] Retrieved %d tasks for %s", len(tasks), sender.name)
            reply_text, task_out_list = cls.build_categorized_tasks_reply(tasks, ref_dt=ref_dt)
            return ChatResponse(
                action="list_my_tasks",
                success=True,
                reply=reply_text,
                tasks=task_out_list,
                requires_clarification=False,
                extractor_used=extracted.extractor_used,
            )

        # 9. Handle Task Creation intent
        if extracted.intent == IntentType.CREATE_TASK:
            task_data = extracted.task_data
            if not task_data:
                return ChatResponse(
                    action="create_task",
                    success=False,
                    reply="يرجى توضيح تفاصيل المهمة وتاريخ استحقاقها والموظف المعني.",
                    requires_clarification=True,
                    extractor_used=extracted.extractor_used,
                )

            # Resolve assignee
            assignee: Optional[Employee] = None
            if task_data.is_self_assignment:
                assignee = sender
            elif task_data.assignee_name:
                assignee = cls._resolve_employee_by_name(db, task_data.assignee_name)
                # If assignee name was provided but not found in DB
                if not assignee:
                    AuditService.log(
                        db,
                        user_id=sender.user_id,
                        action="create_task_unknown_employee",
                        authorized=False,
                        details=f"Assignee '{task_data.assignee_name}' not found in database.",
                    )
                    logger.warning(
                        "[TASK-FLOW: ASSIGNEE-UNKNOWN] Assignee '%s' not registered in database.",
                        task_data.assignee_name,
                    )
                    return ChatResponse(
                        action="create_task",
                        success=False,
                        reply=f"الموظف '{task_data.assignee_name}' غير مسجل في قاعدة بيانات موظفي الشركة. يرجى التحقق من الاسم.",
                        requires_clarification=True,
                        extractor_used=extracted.extractor_used,
                    )

            # If assignee is identified, validate RBAC authorization immediately
            # before checking missing dates/descriptions (e.g. T04: cross-department assignment must be rejected upfront)
            if assignee:
                logger.info(
                    "[TASK-FLOW: ASSIGNEE-FOUND] ID=%d, Name=%s, Dept=%s, Role=%s",
                    assignee.user_id,
                    assignee.name,
                    assignee.department,
                    assignee.role,
                )
                is_auth, auth_err = AuthorizationService.validate_task_creation(
                    sender=sender, assignee=assignee
                )
                if not is_auth:
                    AuditService.log(
                        db,
                        user_id=sender.user_id,
                        action="create_task_rejected",
                        authorized=False,
                        details=f"Forbidden task assignment from {sender.name} ({sender.role}) to {assignee.name} ({assignee.role})",
                    )
                    logger.warning(
                        "[TASK-FLOW: RBAC-FORBIDDEN] Sender %s (%s, %s) forbidden to assign to %s (%s, %s)",
                        sender.name,
                        sender.role,
                        sender.department,
                        assignee.name,
                        assignee.role,
                        assignee.department,
                    )
                    return ChatResponse(
                        action="forbidden",
                        success=False,
                        reply=auth_err or "غير مصرح لك بإسناد هذه المهمة.",
                        requires_clarification=False,
                        extractor_used=extracted.extractor_used,
                    )

            # Check for missing essential fields to request clarification in one cohesive prompt
            missing_fields = []
            if not assignee:
                missing_fields.append("الموظف المعني بالمهمة (أو تحديد أنها مهمة شخصية لك)")

            raw_desc = (task_data.task_description or "").strip()
            vague_phrases = {
                "المهمة", "مهمة", "المهمة اللي تكلمنا عنها", "الموضوع اللي تكلمنا عنه",
                "الموضوع اللي اتفقنا عليه", "الموضوع", "المهمة السابقة"
            }
            if not raw_desc or raw_desc in vague_phrases:
                missing_fields.append("وصف أو عنوان واضح للمهمة")

            if not task_data.due_date_expr:
                missing_fields.append("تاريخ ووقت استحقاق المهمة")

            if missing_fields:
                if len(missing_fields) == 1:
                    reply_msg = f"يرجى تحديد {missing_fields[0]} لإتمام تسجيل المهمة."
                else:
                    items_str = "\n".join(f"• {item}" for item in missing_fields)
                    reply_msg = f"يرجى توضيح البيانات الأساسية التالية لإتمام تسجيل المهمة:\n{items_str}"
                return ChatResponse(
                    action="create_task",
                    success=False,
                    reply=reply_msg,
                    requires_clarification=True,
                    extractor_used=extracted.extractor_used,
                )

            # Resolve schedule & Friday rule
            sched_result, sched_err = SchedulingService.schedule_task(
                date_expr=task_data.due_date_expr,
                time_expr=task_data.due_time_expr,
                ref_dt=ref_dt,
            )
            if sched_err or not sched_result:
                logger.warning("[TASK-FLOW: SCHED-FAILED] Reason: %s", sched_err)
                return ChatResponse(
                    action="create_task",
                    success=False,
                    reply=sched_err or "يرجى تحديد تاريخ ووقت واضحين للمهمة.",
                    requires_clarification=True,
                    extractor_used=extracted.extractor_used,
                )

            # Strip leaked command words (e.g. "سجّل لخالد ...") from the description
            clean_description = cls._clean_description(task_data.task_description, assignee.name)
            due_naive = sched_result.due_at.replace(tzinfo=None)

            # Conflict rule: an employee cannot have two open tasks at the exact same minute
            conflict = cls._find_conflict(db, assignee.user_id, due_naive)
            if conflict:
                AuditService.log(
                    db,
                    user_id=sender.user_id,
                    action="create_task_conflict",
                    authorized=True,
                    target_task_id=conflict.id,
                    details=f"Conflict with task #{conflict.id} for {assignee.name} at {conflict.due_at}",
                )
                logger.info(
                    "[TASK-FLOW: CONFLICT] %s already has task #%d at %s",
                    assignee.name,
                    conflict.id,
                    due_naive.isoformat(),
                )
                return ChatResponse(
                    action="create_task",
                    success=False,
                    task_id=conflict.id,
                    reply=(
                        f"{assignee.name} لديه مهمة بالفعل الساعة {due_naive.strftime('%H:%M')} "
                        f"يوم {due_naive.strftime('%Y-%m-%d')} "
                        f"(مهمة #{conflict.id}: {conflict.description}، أنشأها {conflict.creator_name}). "
                        f"يرجى اختيار وقت آخر."
                    ),
                    tasks=[TaskOut.model_validate(conflict)],
                    requires_clarification=True,
                    extractor_used=extracted.extractor_used,
                )

            # Create Task entity and commit
            new_task = Task(
                description=clean_description,
                assignee_id=assignee.user_id,
                creator_id=sender.user_id,
                due_at=due_naive,  # Persist naive local datetime
                created_at=datetime.now(timezone.utc),
                status="معلقة",
                idempotency_key=idempotency_key,
                is_archived=False,
            )
            try:
                db.add(new_task)
                db.flush()

                # Audit log success
                AuditService.log(
                    db,
                    user_id=sender.user_id,
                    action="create_task",
                    authorized=True,
                    target_task_id=new_task.id,
                    details=f"Task #{new_task.id} created for {assignee.name} due {sched_result.due_at.isoformat()}",
                )
                db.commit()
            except IntegrityError:
                db.rollback()
                if idempotency_key:
                    scoped_key = f"{user_id}:{idempotency_key}"
                    existing = (
                        db.query(Task)
                        .filter(
                            (Task.idempotency_key == idempotency_key)
                            | (Task.idempotency_key == scoped_key)
                        )
                        .first()
                    )
                    if existing:
                        logger.info(
                            "[TASK-FLOW: CONCURRENT-IDEMPOTENT-RACE] Recovered from concurrent race for key %s (Task ID %d)",
                            idempotency_key,
                            existing.id,
                        )
                        t_out = TaskOut.model_validate(existing)
                        return ChatResponse(
                            action="create_task",
                            success=True,
                            task_id=existing.id,
                            reply=f"تم إنشاء المهمة مسبقاً بنجاح وإسنادها إلى {existing.assignee.name if existing.assignee else 'الموظف'}.",
                            tasks=[t_out],
                            requires_clarification=False,
                            extractor_used=extracted.extractor_used,
                        )
                raise

            logger.info(
                "[TASK-FLOW: TASK-PERSISTED] Task #%d created: '%s' for %s due %s (rescheduled=%s)",
                new_task.id,
                new_task.description,
                assignee.name,
                sched_result.due_at.isoformat(),
                sched_result.was_rescheduled,
            )

            # Compose confirmation response using single source of truth for date & weekday
            weekday_name = SchedulingService.get_arabic_weekday_name(sched_result.due_at)
            due_date_str = sched_result.due_at.strftime("%Y-%m-%d")
            due_time_str = sched_result.due_at.strftime("%H:%M")

            if sched_result.was_rescheduled:
                reply_msg = (
                    f"تم إنشاء المهمة بنجاح وإسنادها إلى {assignee.name}.\n"
                    f"{sched_result.explanation}"
                )
            else:
                reply_msg = (
                    f"تم إنشاء المهمة وإسنادها إلى {assignee.name} "
                    f"ليوم {weekday_name} الموافق {due_date_str} في تمام الساعة {due_time_str}."
                )

            t_out = TaskOut.model_validate(new_task)
            return ChatResponse(
                action="create_task",
                success=True,
                task_id=new_task.id,
                reply=reply_msg,
                tasks=[t_out],
                requires_clarification=False,
                extractor_used=extracted.extractor_used,
            )

        # 10. Fallback Unknown Intent
        logger.info("[TASK-FLOW: UNKNOWN-INTENT] Clarification prompt returned to user %s", sender.name)
        return ChatResponse(
            action="unknown_intent",
            success=False,
            reply=extracted.clarification_prompt
            or "عذراً، لم أفهم طلبك. يمكنك تسجيل مهمة، أو الاستفسار عن مهامك، أو السؤال عن سياسة الشركة.",
            tasks=[],
            requires_clarification=True,
            extractor_used=extracted.extractor_used,
        )

    @staticmethod
    def _normalize_arabic_name(name: str) -> str:
        return name.strip().replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")

    @classmethod
    def _resolve_employee_by_name(cls, db: Session, name_str: str) -> Optional[Employee]:
        """Resolve employee by name with alef normalization.

        Tries the full name first, then with a leading 'ل' preposition removed
        (e.g. 'لخالد' -> 'خالد'), so names that genuinely start with 'ل' still match.
        """
        if not name_str:
            return None
        name_clean = name_str.strip()
        candidates = [name_clean]
        if name_clean.startswith("ل") and len(name_clean) > 2:
            candidates.append(name_clean[1:])

        all_employees = db.query(Employee).all()
        for cand in candidates:
            cand_norm = cls._normalize_arabic_name(cand)
            for emp in all_employees:
                if emp.name == cand or cls._normalize_arabic_name(emp.name) == cand_norm:
                    return emp
        return None

    @classmethod
    def _find_conflict(cls, db: Session, assignee_id: int, due: datetime) -> Optional[Task]:
        """Return an open task for this assignee at exactly the same due datetime."""
        return (
            db.query(Task)
            .filter(
                Task.assignee_id == assignee_id,
                Task.due_at == due,
                Task.is_archived == False,
                Task.status != "مكتملة",
            )
            .order_by(Task.id.asc())
            .first()
        )

    @staticmethod
    def _clean_description(desc: str, assignee_name: str) -> str:
        """Remove a leaked leading command such as 'سجّل لخالد' from the task description."""
        original = desc.strip()
        stripped = re.sub(r"[\u064B-\u065F\u0670\u0640]", "", original)  # drop tashkeel/tatweel
        pattern = (
            r"^(?:سجل|اضف|أضف|اسند|أسند|كلف)\s*(?:مهمة\s*)?"
            rf"(?:ل\s*{re.escape(assignee_name)}\s+)?"
        )
        cleaned = re.sub(pattern, "", stripped).strip()
        return cleaned if cleaned and cleaned != stripped else original

    @classmethod
    def build_today_tasks_reply(
        cls,
        tasks: List[Task],
        ref_dt: Optional[datetime] = None,
        subject_name: Optional[str] = None,
    ) -> Tuple[str, List[TaskOut]]:
        """Build single-pass output for today's tasks only without duplication."""
        if not tasks:
            reply = (
                f"لا توجد مهام مستحقة اليوم لـ {subject_name}."
                if subject_name
                else "لا توجد مهام مستحقة لك اليوم."
            )
            return reply, []

        task_out_list = [TaskOut.model_validate(t) for t in tasks]
        header = (
            f"إليك مهام اليوم لـ {subject_name} ({len(tasks)}):"
            if subject_name
            else f"إليك مهامك المستحقة اليوم ({len(tasks)}):"
        )
        lines = [header]
        for t in tasks:
            assignee_str = f" • المسؤول: {t.assignee.name}" if subject_name and t.assignee else ""
            lines.append(
                f"• مهمة #{t.id}: {t.description}{assignee_str} (الموعد: {t.due_at.strftime('%Y-%m-%d %H:%M:%S')} - الحالة: {t.status})"
            )
        return "\n".join(lines), task_out_list

    @classmethod
    def build_overdue_tasks_reply(
        cls,
        tasks: List[Task],
        ref_dt: Optional[datetime] = None,
        subject_name: Optional[str] = None,
    ) -> Tuple[str, List[TaskOut]]:
        """Build single-pass output for overdue tasks only without duplication."""
        if not tasks:
            reply = (
                f"لا توجد مهام متأخرة لـ {subject_name} حالياً."
                if subject_name
                else "لا توجد مهام متأخرة لديك حالياً."
            )
            return reply, []

        task_out_list = [TaskOut.model_validate(t) for t in tasks]
        header = (
            f"إليك المهام المتأخرة لـ {subject_name} ({len(tasks)}):"
            if subject_name
            else f"إليك مهامك المتأخرة ({len(tasks)}):"
        )
        lines = [header]
        for t in tasks:
            assignee_str = f" • المسؤول: {t.assignee.name}" if subject_name and t.assignee else ""
            lines.append(
                f"• مهمة #{t.id}: {t.description}{assignee_str} (الموعد: {t.due_at.strftime('%Y-%m-%d %H:%M:%S')} - الحالة: متأخرة)"
            )
        return "\n".join(lines), task_out_list

    @classmethod
    def build_categorized_tasks_reply(
        cls,
        tasks: List[Task],
        ref_dt: Optional[datetime] = None,
        subject_name: Optional[str] = None,
    ) -> Tuple[str, List[TaskOut]]:
        """
        Build an organized, single-pass task listing categorized into:
        - Overdue (متأخرة)
        - Due Today (مستحقة اليوم)
        - Upcoming (قادمة)
        - Completed (مكتملة)
        Displays each task once with ID, description, due date, and status.
        """
        if not tasks:
            reply = (
                f"لا توجد مهام مسجلة لـ {subject_name} حالياً."
                if subject_name
                else "لا توجد مهام مسجلة لك حالياً."
            )
            return reply, []

        if ref_dt is None:
            ref_dt = settings.get_reference_datetime()
        tz = settings.get_timezone()
        if ref_dt.tzinfo is None:
            ref_dt = ref_dt.replace(tzinfo=tz)

        ref_date = ref_dt.date()

        overdue_tasks = []
        today_tasks = []
        upcoming_tasks = []
        completed_tasks = []
        task_out_list: List[TaskOut] = []

        for t in tasks:
            derived = t.get_derived_status(ref_dt)
            is_over = (derived == "متأخرة")

            t_out = TaskOut(
                id=t.id,
                description=t.description,
                assignee_id=t.assignee_id,
                assignee_name=t.assignee.name if t.assignee else None,
                creator_id=t.creator_id,
                creator_name=t.creator.name if t.creator else None,
                due_at=t.due_at.strftime("%Y-%m-%d %H:%M:%S"),
                status=t.status,
                derived_status=derived,
                is_overdue=is_over,
                is_archived=bool(getattr(t, "is_archived", False)),
            )
            task_out_list.append(t_out)

            due_dt = t.due_at
            if due_dt.tzinfo is None:
                due_dt = due_dt.replace(tzinfo=tz)

            if t.status == "مكتملة":
                completed_tasks.append(t)
            elif is_over:
                overdue_tasks.append(t)
            elif due_dt.date() == ref_date:
                today_tasks.append(t)
            else:
                upcoming_tasks.append(t)

        header = (
            f"إليك قائمة المهام لـ {subject_name}:"
            if subject_name
            else "إليك قائمة المهام:"
        )
        sections = [header]

        if overdue_tasks:
            sections.append(f"\n🔴 المهام المتأخرة ({len(overdue_tasks)}):")
            for t in overdue_tasks:
                sections.append(
                    f"• مهمة #{t.id}: {t.description} (الموعد: {t.due_at.strftime('%Y-%m-%d %H:%M:%S')} - الحالة: متأخرة)"
                )

        if today_tasks:
            sections.append(f"\n🟡 مهام مستحقة اليوم ({len(today_tasks)}):")
            for t in today_tasks:
                sections.append(
                    f"• مهمة #{t.id}: {t.description} (الموعد: {t.due_at.strftime('%Y-%m-%d %H:%M:%S')} - الحالة: {t.status})"
                )

        if upcoming_tasks:
            sections.append(f"\n🟢 المهام القادمة ({len(upcoming_tasks)}):")
            for t in upcoming_tasks:
                sections.append(
                    f"• مهمة #{t.id}: {t.description} (الموعد: {t.due_at.strftime('%Y-%m-%d %H:%M:%S')} - الحالة: {t.status})"
                )

        if completed_tasks:
            sections.append(f"\n⚪ المهام المكتملة ({len(completed_tasks)}):")
            for t in completed_tasks:
                sections.append(
                    f"• مهمة #{t.id}: {t.description} (الموعد: {t.due_at.strftime('%Y-%m-%d %H:%M:%S')} - الحالة: مكتملة)"
                )

        reply_text = "\n".join(sections)
        return reply_text, task_out_list

    @classmethod
    def archive_old_tasks(
        cls,
        db: Session,
        retention_days: Optional[int] = None,
        ref_dt: Optional[datetime] = None,
        only_completed: bool = True,
    ) -> int:
        """
        Soft-archive tasks older than retention days without deleting them permanently.
        Configurable via settings.task_archive_retention_days (default 30 days).
        Reversible through unarchive_task.
        """
        if retention_days is None:
            retention_days = settings.task_archive_retention_days
        if ref_dt is None:
            ref_dt = settings.get_reference_datetime()

        cutoff_date = ref_dt - timedelta(days=retention_days)
        cutoff_naive = cutoff_date.replace(tzinfo=None)

        query = db.query(Task).filter(
            Task.is_archived == False,
            Task.due_at <= cutoff_naive,
        )
        if only_completed:
            query = query.filter(Task.status == "مكتملة")

        tasks_to_archive = query.all()
        count = len(tasks_to_archive)
        for t in tasks_to_archive:
            t.is_archived = True
        db.commit()
        logger.info("Archived %d tasks older than %d days.", count, retention_days)
        return count

    @classmethod
    def unarchive_task(cls, db: Session, task_id: int) -> Optional[Task]:
        """
        Restore a soft-archived task back to active status (fully reversible).
        """
        task = db.query(Task).filter(Task.id == task_id).first()
        if task and task.is_archived:
            task.is_archived = False
            db.commit()
            db.refresh(task)
            logger.info("Unarchived task ID %d.", task_id)
        return task