import abc
import json
import logging
import re
from typing import Optional
import httpx
from app.config import settings
from app.models import Employee
from app.schemas import ExtractedIntent, ExtractedTaskData, IntentType

logger = logging.getLogger(__name__)


class BaseIntentExtractor(abc.ABC):
    """Abstract interface for intent extraction providers."""

    @abc.abstractmethod
    def extract(self, message: str, sender: Optional[Employee] = None) -> ExtractedIntent:
        """Extract intent and structured entities from user message."""
        pass


class DeterministicIntentExtractor(BaseIntentExtractor):
    """
    High-accuracy, rule-based deterministic intent extractor supporting
    Saudi dialect and Modern Standard Arabic. Runs completely offline without API keys.
    """

    KNOWN_NAMES = ["منيرة", "سارة", "فهد", "خالد", "نورة", "عبدالرحمن", "أحمد", "احمد", "محمد", "علي"]

    def extract(self, message: str, sender: Optional[Employee] = None) -> ExtractedIntent:
        clean = message.strip()
        # 0. Strip Arabic tashkeel / diacritics for uniform matching
        clean_norm = re.sub(r"[\u064B-\u065F\u0670]", "", clean)

        # 1. Check for task completion triggers
        # Matches: "أنا أنجزت المهمة رقم 8", "أنجزت المهمة 8", "خلصت المهمة رقم 8", "علّم المهمة 8 كمكتملة", "تم إنجاز المهمة 8", etc.
        completion_match = re.search(
            r"\b(?:أنا\s*|انا\s*)?(?:أنجزت|انجزت|خلصت|علّم|علم|تم\s*إنجاز|تم\s*انجاز|تم\s*إنهاء|تم\s*انهاء|أنهيت|انهيت)\s+(?:المهمة\s*(?:رقم\s*)?|مهمة\s*(?:رقم\s*)?|رقم\s*)?(\d+)\b",
            clean_norm,
        )
        if not completion_match:
            completion_match = re.search(
                r"\b(?:المهمة\s*(?:رقم\s*)?|مهمة\s*(?:رقم\s*)?)(\d+)\s+(?:أنجزتها|انجزتها|خلصتها|تم\s*إنجازها|تم\s*انجازها|كمكتملة|مكتملة)\b",
                clean_norm,
            )

        if completion_match:
            task_id = int(completion_match.group(1))
            return ExtractedIntent(
                intent=IntentType.COMPLETE_TASK,
                task_id=task_id,
                confidence=1.0,
                extractor_used="deterministic",
            )

        # 2. Check for prompt injection / company-wide listing intent
        if re.search(r"\b(كل\s*مهام\s*الشركة|مهام\s*الشركة|جميع\s*مهام\s*الشركة)\b", clean_norm):
            return ExtractedIntent(
                intent=IntentType.LIST_MY_TASKS,
                confidence=1.0,
                extractor_used="deterministic",
            )

        # 3. Check for Today's tasks triggers (list_today_tasks)
        # Check if querying another employee's today tasks: "خالد عنده إيه النهارده؟", "وش عند خالد اليوم؟"
        other_today = re.search(
            r"\b([^\s\?،,]+)\s+عنده\s*(?:إيه|ايه|وش|ماذا)\s*(?:النهارده|اليوم|هذا اليوم)\b",
            clean_norm,
        ) or re.search(
            r"\b(?:وش\s*عند|ماذا\s*لدى|وش\s*مهام|اعرض\s*مهام)\s+([^\s\?،,]+)\s*(?:النهارده|اليوم|هذا اليوم)\b",
            clean_norm,
        )
        if other_today and other_today.group(1).strip() not in ["أنا", "انا", "كل", "الشركة", "القسم"]:
            target_name = other_today.group(1).strip()
            return ExtractedIntent(
                intent=IntentType.LIST_TODAY_TASKS,
                target_assignee=target_name,
                target_employee=target_name,
                confidence=1.0,
                extractor_used="deterministic",
            )

        # Check own today tasks: "عندي إيه النهارده؟", "وش مهامي اليوم؟", "مهامي اليوم"
        if re.search(
            r"\b(عندي\s*(?:إيه|ايه)\s*(?:النهارده|اليوم)|وش\s*عندي\s*(?:النهارده|اليوم)|وش\s*مهامي\s*(?:النهارده|اليوم)|مهامي\s*(?:النهارده|اليوم)|اعرض\s*مهامي\s*(?:النهارده|اليوم))\b",
            clean_norm,
        ):
            return ExtractedIntent(
                intent=IntentType.LIST_TODAY_TASKS,
                target_assignee=None,
                target_employee=None,
                confidence=1.0,
                extractor_used="deterministic",
            )

        # 4. Check for Overdue tasks triggers (list_overdue_tasks)
        other_overdue = re.search(r"\b(?:مهام|المهام)\s+([^\s\?،,]+)\s+المتأخرة\b", clean_norm)
        if other_overdue and other_overdue.group(1).strip() not in ["كل", "الشركة", "القسم"]:
            return ExtractedIntent(
                intent=IntentType.LIST_OVERDUE_TASKS,
                target_assignee=other_overdue.group(1).strip(),
                target_employee=other_overdue.group(1).strip(),
                confidence=1.0,
                extractor_used="deterministic",
            )

        if re.search(r"\b(مهامي\s*المتأخرة|المهام\s*المتأخرة|وش\s*المتأخر|المهام\s*المتاخرة|مهام\s*متأخرة)\b", clean_norm):
            return ExtractedIntent(
                intent=IntentType.LIST_OVERDUE_TASKS,
                target_assignee=None,
                target_employee=None,
                confidence=1.0,
                extractor_used="deterministic",
            )

        # 5. Check for task listing targeting another employee or claiming an identity (list_employee_tasks)
        # Pattern A: Claims identity: e.g. "أنا خالد وش مهامي؟", "أنا خالد اعرض مهامي", "أنا خالد وش عندي"
        identity_match = re.search(
            r"^\s*أنا\s+([^\s\?،,]+)(?:\s+(?:وش\s*مهامي|اعرض\s*مهامي|قائمة\s*مهامي|وش\s*عندي|ماهي\s*مهامي|ما\s*هي\s*مهامي|مهامي))?",
            clean_norm,
        )
        if identity_match:
            claimed_name = identity_match.group(1).strip()
            if re.search(r"\b(وش\s*مهامي|اعرض\s*مهامي|قائمة\s*مهامي|وش\s*عندي|ماهي\s*مهامي|ما\s*هي\s*مهامي|مهامي)\b", clean_norm):
                return ExtractedIntent(
                    intent=IntentType.LIST_EMPLOYEE_TASKS,
                    target_assignee=claimed_name,
                    target_employee=claimed_name,
                    confidence=1.0,
                    extractor_used="deterministic",
                )

        # Pattern B: Queries asking about another employee's tasks: e.g. "وش مهام خالد؟", "اعرض مهام نورة", "مهام سارة"
        other_tasks_match = re.search(
            r"\b(?:وش|اعرض|استعرض|قائمة|ماهي|ما\s*هي|أريد\s*معرفة|ابي\s*اشوف)?\s*مهام\s+([^\s\?،,]+)\b",
            clean_norm,
        )
        if other_tasks_match:
            target_name = other_tasks_match.group(1).strip()
            if target_name not in ["الشركة", "القسم", "كل", "العمل", "المشروع", "اليوم", "النهارده", "المتأخرة"]:
                return ExtractedIntent(
                    intent=IntentType.LIST_EMPLOYEE_TASKS,
                    target_assignee=target_name,
                    target_employee=target_name,
                    confidence=1.0,
                    extractor_used="deterministic",
                )

        # 6. Standard own task listing intent (list_my_tasks)
        if re.search(r"\b(وش\s*مهامي|مهامي|اعرض\s*مهامي|قائمة\s*مهامي|وش\s*عندي|اعرض\s*المهام|ماهي\s*مهامي|ما\s*هي\s*مهامي)\b", clean_norm):
            return ExtractedIntent(
                intent=IntentType.LIST_MY_TASKS,
                target_assignee=None,
                target_employee=None,
                confidence=1.0,
                extractor_used="deterministic",
            )

        # 3. Detect task creation triggers
        is_task_creation_prompt = bool(
            re.search(
                r"\b(سجل(?:ها|ه)?|سجّل(?:ها|ه)?|حط(?:ها|ه)?|ذكرني|ذكّرني|أبي|ابي|كلف|كلّف|أنشئ(?:ها|ه)?|انشئ|محتاج|عايز|عاوز|اعمل|سوّي|سوي|رتّب|رتب|اجتماع)\b",
                clean_norm,
            )
            or re.search(r"(سجّلها|سجلها)\s*(له|لها)?\s*كمهمة", clean_norm)
            or re.search(r"\bمحتاج\s+\S+\s+(يعمل|يراجع|يسوي|يجهز|يطبع|يرسل|يحضر)\b", clean_norm)
            or re.search(r"\bعايز\s+(أكلّف|أكلف|اكلف|كلّف|كلف)\b", clean_norm)
        )

        # 4. Check for greetings (if not task creation or policy)
        greeting_pattern = r"^(أهلا|اهلا|مرحبا|مرحباً|السلام\s*عليكم|سلام\s*عليكم|صباح\s*الخير|مساء\s*الخير|هلا|حياك|هاي|اهلين|أهلين|تحياتي|صباح\s*النور|مساء\s*الورد)\b"
        if not is_task_creation_prompt and re.search(greeting_pattern, clean_norm):
            return ExtractedIntent(
                intent=IntentType.GREETING,
                confidence=1.0,
                extractor_used="deterministic",
            )

        # 5. Check for policy questions (must NOT be task creation)
        if not is_task_creation_prompt and (
            re.search(
                r"\b(كم\s*يوم|كم\s*بدل|إجازة|اجازة|اجازتي|سياسة|العمل\s*عن\s*بعد|مصروف|500\s*ريال|ساعات\s*العمل|الدوام|تقرير\s*طبي|تحديث\s*المهام)\b",
                clean_norm,
            )
            or re.search(r"\b(الزم\s*أقدّم|الزم\s*اقدم|لازم\s*أقدّم|لازم\s*اقدم|أقدّم\s*قبل|اقدم\s*قبل)\b", clean_norm)
            or clean_norm.startswith("كم ")
            or clean_norm.startswith("هل ")
            or clean_norm.startswith("ما هي ")
            or clean_norm.startswith("ماهي ")
        ):
            return ExtractedIntent(
                intent=IntentType.ASK_POLICY,
                policy_question=clean,
                confidence=1.0,
                extractor_used="deterministic",
            )

        # 6. Handle task creation
        if is_task_creation_prompt:
            task_data = self._extract_task_data(clean_norm, sender)
            requires_clarification = False
            clarification_prompt = None

            if not task_data.assignee_name and not task_data.is_self_assignment:
                requires_clarification = True
                clarification_prompt = "يرجى تحديد اسم الموظف المراد إسناد المهمة إليه."
            elif not task_data.task_description and not task_data.due_date_expr:
                requires_clarification = True
                clarification_prompt = "يرجى توضيح تفاصيل المهمة وتاريخ استحقاقها."

            return ExtractedIntent(
                intent=IntentType.CREATE_TASK,
                task_data=task_data,
                requires_clarification=requires_clarification,
                clarification_prompt=clarification_prompt,
                confidence=1.0,
                extractor_used="deterministic",
            )

        # Fallback
        return ExtractedIntent(
            intent=IntentType.UNKNOWN_INTENT,
            confidence=0.0,
            requires_clarification=True,
            clarification_prompt="عذراً، لم أفهم طلبك. يمكنك تسجيل مهمة، أو الاستفسار عن مهامك، أو السؤال عن سياسة الشركة.",
            extractor_used="deterministic",
        )

    def _extract_task_data(self, text: str, sender: Optional[Employee] = None) -> ExtractedTaskData:
        norm_text = re.sub(r"[\u064B-\u065F\u0670]", "", text)
        is_self = False
        assignee_name = None
        missing_fields = []

        # Self-assignment triggers
        if re.search(r"\b(ذكرني|ذكّرني|حط\s*لي|سجل\s*لي|سجلني|محتاج\s*أسوي|محتاج\s*اسوي)\b", norm_text):
            is_self = True
            if sender:
                assignee_name = sender.name

        # Extract explicit assignee
        if not is_self:
            name_match = re.search(
                r"\b(?:سجل\s*(?:مهمة\s*)?ل|سجّل\s*(?:مهمة\s*)?ل|حط\s*(?:مهمة\s*)?ل|كلف\s*ل?|كلّف\s*ل?|أبي\s+|ابي\s+|محتاج\s+|عايز\s+|عاوز\s+|رتّب\s*ل?|رتب\s*ل?)([^\s،,]+)",
                norm_text,
            )
            if name_match:
                raw_name = name_match.group(1).strip()
                if raw_name.startswith("ل") and len(raw_name) > 2:
                    assignee_name = raw_name[1:]
                else:
                    assignee_name = raw_name
                if assignee_name == "عبد" and "عبدالرحمن" in norm_text:
                    assignee_name = "عبدالرحمن"
            else:
                for name in self.KNOWN_NAMES:
                    if re.search(rf"\b(ل)?{name}\b", norm_text):
                        assignee_name = name
                        break

        # Extract relative date expression
        date_expr = None

        if re.search(r"\b(بعد\s*بكرة|بعد\s*بكره|بعد\s*غد|بعد\s*غدا)\b", norm_text):
            date_expr = "بعد بكرة"
        elif re.search(r"\b(بكرة|بكره|غدا)\b", norm_text):
            date_expr = "بكرة"
        elif re.search(r"\b(اليوم|هذا اليوم)\b", norm_text):
            date_expr = "اليوم"
        else:
            # Check explicit dates with months or weekdays
            day_map = {
                "الجمعة": "الجمعة",
                "السبت": "السبت",
                "الخميس": "الخميس",
                "الأربعاء": "الأربعاء",
                "الاربعاء": "الأربعاء",
                "الثلاثاء": "الثلاثاء",
                "الاثنين": "الاثنين",
                "الإثنين": "الاثنين",
                "الأحد": "الأحد",
                "الاحد": "الأحد",
            }
            for day_raw, day_canonical in day_map.items():
                day_norm = re.sub(r"[\u064B-\u065F\u0670]", "", day_raw)
                day_match = re.search(
                    rf"\b(يوم\s*)?{day_norm}(\s+(الجاي|القادم|الجاية|القادمة))?\b",
                    norm_text,
                )
                if day_match:
                    suffix = day_match.group(2) or ""
                    date_expr = f"{day_canonical}{suffix}".strip()
                    break

            if not date_expr:
                # Check for explicit Arabic month e.g. "15 أكتوبر 2026"
                months_pat = r"(يناير|فبراير|مارس|أبريل|ابريل|مايو|يونيو|يوليو|أغسطس|اغسطس|سبتمبر|أكتوبر|اكتوبر|نوفمبر|ديسمبر)"
                m_match = re.search(rf"\b(?:يوم\s*)?(\S+\s*)?(\d{{1,2}}\s*{months_pat}(?:\s*\d{{4}})?)\b", norm_text)
                if m_match:
                    date_expr = m_match.group(0).strip()

        # Default to today if time mentioned but no date
        if date_expr is None and re.search(r"\b(العصر|الظهر|المغرب|الساعة|الصباح|الصبح)\b", norm_text):
            date_expr = "اليوم"

        # Extract time expression
        time_expr = None

        arabic_numbers = {
            "واحدة": 1, "اثنين": 2, "ثلاثة": 3, "أربعة": 4, "اربعة": 4,
            "خمسة": 5, "ستة": 6, "سبعة": 7, "ثمانية": 8, "تسعة": 9,
            "عشرة": 10, "أحد عشر": 11, "احد عشر": 11, "اثنا عشر": 12, "اثني عشر": 12,
        }

        # "1 الظهر" or "الساعة 1 الظهر"
        dhuhr_match = re.search(r"\b(?:الساعة\s*)?(\d{1,2})\s*(الظهر|الظهرية)\b", norm_text)
        if dhuhr_match:
            time_expr = f"{dhuhr_match.group(1)} الظهر"
        else:
            arabic_time_match = re.search(
                r"\b(?:الساعة\s*)?(واحدة|اثنين|ثلاثة|أربعة|اربعة|خمسة|ستة|سبعة|ثمانية|تسعة|عشرة|أحد\s*عشر|احد\s*عشر|اثنا\s*عشر|اثني\s*عشر)"
                r"(?:\s*(الصبح|الصباح|صباحاً|صباح|مساءً|مساء|بالليل))?\b",
                norm_text,
            )
            if arabic_time_match:
                num_word = arabic_time_match.group(1).strip()
                period = arabic_time_match.group(2) or ""
                num = arabic_numbers.get(num_word, 0)
                if num:
                    time_expr = f"الساعة {num:02d}:00" + (f" {period}" if period else "")
            else:
                # "الساعة 10" or "الساعة 10 صباحا"
                sa3a_match = re.search(
                    r"\b(الساعة\s*\d{1,2}(?::\d{2})?(?:\s*(?:الصبح|الصباح|صباحاً|صباح|مساءً|مساء|بالليل))?)\b",
                    norm_text,
                )
                if sa3a_match:
                    time_expr = sa3a_match.group(1)
                elif re.search(r"\b(العصر|عصراً)\b", norm_text):
                    time_expr = "العصر"
                elif re.search(r"\b(الظهر)\b", norm_text):
                    time_expr = "الظهر"
                elif re.search(r"\b(المغرب)\b", norm_text):
                    time_expr = "المغرب"
                elif re.search(r"\b(الصباح|صباحاً|الصبح)\b", norm_text):
                    time_expr = "الصباح"

        # Extract description
        desc = norm_text
        # Remove trigger prefixes
        desc = re.sub(
            r"^(?:سجل\s*(?:مهمة\s*)?(?:ل[^\s]+)?|سجّل\s*(?:مهمة\s*)?(?:ل[^\s]+)?|حط\s*(?:مهمة\s*)?(?:ل[^\s]+)?|حط\s*لي\s*(?:مهمة)?|ذكرني|ذكّرني|أبي\s+[^\s]+|ابي\s+[^\s]+|محتاج\s+[^\s]+|عايز\s+[^\s]+|رتّب\s*(?:اجتماع\s*)?(?:ل[^\s]+)?|رتب\s*(?:اجتماع\s*)?(?:ل[^\s]+)?|أنشئ\s*(?:مهمة\s*)?(?:ل[^\s]+)?|انشئ\s*(?:مهمة\s*)?(?:ل[^\s]+)?)\s*",
            "",
            desc,
        )
        # Remove suffix directive "سجّلها له كمهمة"
        desc = re.sub(r"[,،]?\s*(?:سجلها|سجّلها)\s*(?:له|لها)?\s*كمهمة\.?", "", desc)
        # Remove date expression
        if date_expr:
            desc = re.sub(rf"\b{re.escape(date_expr)}\b", "", desc)
        # Remove time expression
        if time_expr:
            desc = re.sub(rf"\b{re.escape(time_expr)}\b", "", desc)
        # Remove common fillers
        desc = re.sub(
            r"\b(مهمة|يوم|الساعة\s*\d+|الساعة|بكرة|بكره|غدا|غداً|الجمعة|السبت|الأحد|الاحد|الخميس|الأربعاء|الثلاثاء|الاثنين|الجاي|القادم|الجاية|القادمة|الصبح|الصباح|صباحاً|صباح)\b",
            "",
            desc,
        )
        # If there is a trailing/leading preposition to assignee like "لخالد" or "لعبدالرحمن"
        if assignee_name:
            desc = re.sub(rf"\bل?{re.escape(assignee_name)}\b", "", desc)
        desc = re.sub(r"\s+", " ", desc).strip()

        # Check missing fields
        if not assignee_name and not is_self:
            missing_fields.append("assignee")
        if not date_expr:
            missing_fields.append("due_date")
        if not desc:
            missing_fields.append("description")

        return ExtractedTaskData(
            assignee_name=assignee_name,
            task_description=desc if desc else None,
            due_date_expr=date_expr,
            due_time_expr=time_expr,
            is_self_assignment=is_self,
            missing_fields=missing_fields,
        )


class LLMIntentExtractor(BaseIntentExtractor):
    """
    LLM-based intent extractor using OpenAI/Groq compatible chat completions API.
    Falls back to DeterministicIntentExtractor on any failure.
    """

    SYSTEM_PROMPT = (
        "You are an expert Arabic intent extraction engine for an enterprise task management assistant.\n"
        "Analyze the user message (Modern Standard Arabic or Saudi/Gulf dialect).\n\n"
        "Return ONLY a valid JSON object with this exact schema — no markdown, no explanation:\n"
        "{\n"
        '  "intent": "create_task" | "list_my_tasks" | "list_today_tasks" | "list_overdue_tasks" | "list_employee_tasks" | "complete_task" | "ask_policy" | "greeting" | "unknown_intent",\n'
        '  "task_id": integer | null,\n'
        '  "assignee_name": string | null,\n'
        '  "target_employee": string | null,\n'
        '  "target_assignee": string | null,\n'
        '  "is_self_assignment": boolean,\n'
        '  "task_description": string | null,\n'
        '  "due_date_expr": string | null,\n'
        '  "due_time_expr": string | null,\n'
        '  "policy_question": string | null\n'
        "}\n\n"
        "Classification rules:\n"
        "- intent='complete_task' when user reports completing or finishing a task.\n"
        "  Triggers: أنجزت المهمة 8، خلصت المهمة رقم 8، علّم المهمة 8 كمكتملة، تم إنجاز المهمة 8، أنهيت مهمة 8.\n"
        "  Always extract the task ID as an integer in task_id.\n"
        "- intent='list_today_tasks' when user asks about tasks due today (عندي إيه النهارده، وش مهامي اليوم، خالد عنده إيه النهارده).\n"
        "- intent='list_overdue_tasks' when user asks about overdue tasks (مهامي المتأخرة، وش المتأخر، مهام خالد المتأخرة).\n"
        "- intent='list_employee_tasks' when user asks about another employee's tasks (وش مهام خالد، اعرض مهام سارة) or claims another identity (أنا خالد وش مهامي).\n"
        "- intent='list_my_tasks' when user asks about their own general tasks without a specific date (وش مهامي، اعرض مهامي، وش عندي).\n"
        "- intent='create_task' when user wants to create, assign, schedule, or register a task.\n"
        "  Triggers: سجل، سجّل، حط، أبي، ابي، محتاج، عايز، ذكرني، سجلها له كمهمة.\n"
        "- intent='ask_policy' when user asks about company rules, leave, expenses, working hours.\n"
        "- intent='greeting' for friendly welcomes (أهلا، مرحبا، السلام عليكم).\n"
        "- intent='unknown_intent' only when nothing above applies.\n\n"
        "For task_id: integer representing the task number (e.g. 8).\n"
        "For assignee_name: strip preposition prefixes (لفهد → فهد, لخالد → خالد).\n"
        "For target_employee: when intent is list_employee_tasks or list_today_tasks for another person, extract that person's clean name (e.g. 'خالد'). Otherwise null.\n"
        "For is_self_assignment: true when ذكرني / حط لي / سجل لي / محتاج أسوي.\n"
        "For due_date_expr: preserve the original Arabic phrase (بكرة، الخميس الجاي، 15 أكتوبر 2026).\n"
        "For due_time_expr: preserve the original Arabic phrase (الساعة 10 الصبح، 1 الظهر).\n"
        "For task_description: extract the core action cleanly (يراجع التقرير، يطبع المخططات).\n"
    )

    def __init__(self, fallback: Optional[BaseIntentExtractor] = None):
        self.fallback = fallback or DeterministicIntentExtractor()

    def _fallback_extract(self, message: str, sender: Optional[Employee] = None) -> ExtractedIntent:
        res = self.fallback.extract(message, sender)
        res.extractor_used = "llm_fallback_to_deterministic"
        return res

    @classmethod
    def build_chat_endpoint_url(cls, provider: str) -> str:
        base = provider.strip().rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    def extract(self, message: str, sender: Optional[Employee] = None) -> ExtractedIntent:
        if not settings.llm_api_key or not settings.llm_api_key.strip():
            logger.warning("LLM API key not configured. Falling back to deterministic extractor.")
            return self._fallback_extract(message, sender)

        target_url = self.build_chat_endpoint_url(settings.llm_provider)
        headers = {
            "Authorization": f"Bearer {settings.llm_api_key.strip()}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": settings.llm_model,
            "messages": [
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": message},
            ],
            "temperature": 0.0,
        }

        # FIX: Only add response_format for models that support it (not all Groq models do)
        supported_json_models = ["llama", "gemma", "mixtral", "qwen"]
        if any(m in settings.llm_model.lower() for m in supported_json_models):
            payload["response_format"] = {"type": "json_object"}

        try:
            logger.debug("Sending request to LLM: url=%s model=%s message=%r", target_url, settings.llm_model, message)

            with httpx.Client(timeout=20.0) as client:
                resp = client.post(target_url, json=payload, headers=headers)

            logger.debug("LLM response status: %d", resp.status_code)

            if resp.status_code != 200:
                logger.warning(
                    "LLM returned %d (model: %s). Preview: %s. Falling back.",
                    resp.status_code,
                    settings.llm_model,
                    resp.text[:300],
                )
                return self._fallback_extract(message, sender)

            data = resp.json()
            raw_content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            logger.debug("LLM raw response: %r", raw_content)

            if not raw_content:
                logger.warning("Empty LLM response. Falling back.")
                return self._fallback_extract(message, sender)

            # Strip markdown fences if present
            cleaned = raw_content.strip()
            cleaned = re.sub(r"^```json\s*", "", cleaned)
            cleaned = re.sub(r"^```\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
            cleaned = cleaned.strip()

            parsed = json.loads(cleaned)
            logger.debug("LLM parsed JSON: %s", parsed)

            # Map intent
            raw_intent = parsed.get("intent", "unknown_intent")
            try:
                parsed_intent = IntentType(raw_intent)
            except ValueError:
                logger.warning("Unknown intent value from LLM: %r. Using unknown_intent.", raw_intent)
                parsed_intent = IntentType.UNKNOWN_INTENT

            # Extract fields
            raw_task_id = parsed.get("task_id")
            parsed_task_id = None
            if raw_task_id is not None:
                try:
                    parsed_task_id = int(raw_task_id)
                except (ValueError, TypeError):
                    parsed_task_id = None

            if parsed_intent == IntentType.COMPLETE_TASK and parsed_task_id is None:
                m = re.search(r"\b(\d+)\b", message)
                if m:
                    parsed_task_id = int(m.group(1))

            assignee = parsed.get("assignee_name")
            if assignee:
                assignee = str(assignee).strip()
                if assignee.startswith("ل") and len(assignee) > 2:
                    assignee = assignee[1:]

            target_employee = parsed.get("target_employee") or parsed.get("target_assignee")
            if target_employee:
                target_employee = str(target_employee).strip()
                if target_employee.startswith("ل") and len(target_employee) > 2:
                    target_employee = target_employee[1:]

            desc = parsed.get("task_description")
            date_expr = parsed.get("due_date_expr")
            time_expr = parsed.get("due_time_expr")
            is_self = bool(parsed.get("is_self_assignment", False))

            if is_self and not assignee and sender:
                assignee = sender.name

            missing_fields = []
            if not assignee and not is_self:
                missing_fields.append("assignee")
            if not date_expr:
                missing_fields.append("due_date")
            if not desc:
                missing_fields.append("description")

            task_data = ExtractedTaskData(
                assignee_name=assignee,
                task_description=desc,
                due_date_expr=date_expr,
                due_time_expr=time_expr,
                is_self_assignment=is_self,
                missing_fields=missing_fields,
            )

            requires_clarification = False
            clarification_prompt = None
            if parsed_intent == IntentType.CREATE_TASK:
                if not task_data.assignee_name and not task_data.is_self_assignment:
                    requires_clarification = True
                    clarification_prompt = "يرجى تحديد اسم الموظف المراد إسناد المهمة إليه."
                elif not task_data.due_date_expr and not task_data.task_description:
                    requires_clarification = True
                    clarification_prompt = "يرجى توضيح تفاصيل المهمة وتاريخ استحقاقها."

            return ExtractedIntent(
                intent=parsed_intent,
                task_data=task_data,
                task_id=parsed_task_id,
                target_assignee=target_employee,
                target_employee=target_employee,
                policy_question=parsed.get("policy_question"),
                confidence=0.95,
                requires_clarification=requires_clarification,
                clarification_prompt=clarification_prompt,
                extractor_used="llm",
            )

        except httpx.TimeoutException:
            logger.warning("LLM timed out (20s). endpoint=%s model=%s. Falling back.", target_url, settings.llm_model)
            return self._fallback_extract(message, sender)
        except json.JSONDecodeError as e:
            logger.warning("JSON decode error from LLM: %s. Falling back.", e)
            return self._fallback_extract(message, sender)
        except Exception as e:
            logger.warning("Unexpected LLM error: %s. Falling back.", e)
            return self._fallback_extract(message, sender)


class IntentService:
    """Entry point for intent extraction based on system configuration."""

    _extractor: Optional[BaseIntentExtractor] = None
    _current_mode: Optional[str] = None

    @classmethod
    def get_extractor(cls, force_refresh: bool = False) -> BaseIntentExtractor:
        mode = settings.intent_mode.lower()
        if cls._extractor is None or force_refresh or cls._current_mode != mode:
            if mode == "llm":
                logger.info(
                    "Activating LLMIntentExtractor (provider: %s, model: %s)",
                    settings.llm_provider,
                    settings.llm_model,
                )
                cls._extractor = LLMIntentExtractor()
            else:
                logger.info("Activating DeterministicIntentExtractor")
                cls._extractor = DeterministicIntentExtractor()
            cls._current_mode = mode
        return cls._extractor

    @classmethod
    def extract_intent(cls, message: str, sender: Optional[Employee] = None) -> ExtractedIntent:
        return cls.get_extractor().extract(message, sender)