"""Date and time parsing with Asia/Riyadh timezone and Friday rescheduling."""

import re
from datetime import datetime, date, time, timedelta
from typing import Optional, Tuple
from zoneinfo import ZoneInfo
from app.config import settings


class SchedulingResult:
    """Encapsulates the resolved due date/time and rescheduling metadata."""

    def __init__(
        self,
        due_at: datetime,
        was_rescheduled: bool = False,
        original_due_at: Optional[datetime] = None,
        explanation: Optional[str] = None,
        weekday_name: Optional[str] = None,
    ):
        self.due_at = due_at
        self.was_rescheduled = was_rescheduled
        self.original_due_at = original_due_at
        self.explanation = explanation
        self.weekday_name = weekday_name or SchedulingService.get_arabic_weekday_name(due_at)


class SchedulingService:
    """Service for resolving Arabic relative dates and times with business rules."""

    ARABIC_WEEKDAYS = {
        "الاثنين": 0,
        "الإثنين": 0,
        "الثلاثاء": 1,
        "الاربعاء": 2,
        "الأربعاء": 2,
        "الخميس": 3,
        "الجمعة": 4,
        "السبت": 5,
        "الاحد": 6,
        "الأحد": 6,
    }

    ARABIC_WEEKDAYS_BY_INDEX = {
        0: "الإثنين",
        1: "الثلاثاء",
        2: "الأربعاء",
        3: "الخميس",
        4: "الجمعة",
        5: "السبت",
        6: "الأحد",
    }

    ARABIC_MONTHS = {
        "يناير": 1, "فبراير": 2, "مارس": 3, "أبريل": 4, "ابريل": 4, "مايو": 5,
        "يونيو": 6, "يوليو": 7, "أغسطس": 8, "اغسطس": 8, "سبتمبر": 9,
        "أكتوبر": 10, "اكتوبر": 10, "نوفمبر": 11, "ديسمبر": 12,
    }

    ARABIC_HOUR_WORDS = {
        "الحادية عشرة": 11, "حادي عشر": 11, "إحدى عشرة": 11, "احدى عشرة": 11, "أحد عشر": 11,
        "الثانية عشرة": 12, "ثاني عشر": 12, "اثنتا عشرة": 12, "اثنا عشر": 12,
        "العاشرة": 10, "عشرة": 10, "عشر": 10,
        "التاسعة": 9, "تسعة": 9, "تسع": 9,
        "الثامنة": 8, "ثمانية": 8, "ثمان": 8,
        "السابعة": 7, "سبعة": 7, "سبع": 7,
        "السادسة": 6, "ستة": 6, "ست": 6,
        "الخامسة": 5, "خمسة": 5, "خمس": 5,
        "الرابعة": 4, "أربعة": 4, "اربعة": 4, "أربع": 4, "اربع": 4,
        "الثالثة": 3, "ثلاثة": 3, "ثلاث": 3,
        "الثانية": 2, "اثنتين": 2, "اثنين": 2,
        "الواحدة": 1, "واحدة": 1, "الوحدة": 1, "واحد": 1,
    }

    @classmethod
    def get_arabic_weekday_name(cls, dt_or_d: date | datetime) -> str:
        """Return the exact Arabic weekday name derived directly from the date object."""
        idx = dt_or_d.weekday()
        return cls.ARABIC_WEEKDAYS_BY_INDEX[idx]

    @classmethod
    def resolve_target_date(
        cls,
        date_expr: Optional[str],
        ref_dt: Optional[datetime] = None,
    ) -> Optional[date]:
        """Resolve an Arabic relative date expression relative to ref_dt."""
        if not date_expr:
            return None

        tz = settings.get_timezone()
        if ref_dt is None:
            ref_dt = settings.get_reference_datetime()
        elif ref_dt.tzinfo is None:
            ref_dt = ref_dt.replace(tzinfo=tz)
        else:
            ref_dt = ref_dt.astimezone(tz)

        ref_date = ref_dt.date()
        clean = date_expr.strip()
        # Strip Arabic diacritics (tashkeel: fathatan, dammatan, kasratan, fatha, damma, kasra, etc.)
        clean_norm = re.sub(r"[\u064B-\u065F\u0670]", "", clean)

        # Handle explicit date with Arabic month (e.g. '15 أكتوبر 2026' or '15 اكتوبر')
        months_pat = "|".join(cls.ARABIC_MONTHS.keys())
        month_match = re.search(rf"\b(\d{{1,2}})\s*(?:من\s*)?({months_pat})(?:\s*(\d{{4}}))?\b", clean_norm)
        if month_match:
            try:
                day = int(month_match.group(1))
                m_name = month_match.group(2)
                month = cls.ARABIC_MONTHS[m_name]
                year = int(month_match.group(3)) if month_match.group(3) else ref_date.year
                return date(year, month, day)
            except ValueError:
                pass

        # Handle 'اليوم'
        if re.search(r"\b(اليوم|هذا اليوم)\b", clean_norm):
            return ref_date

        # Handle 'بعد بكرة' / 'بعد غد' (must be before 'بكرة')
        if re.search(r"\b(بعد\s*بكرة|بعد\s*بكره|بعد\s*غد|بعد\s*غدا)\b", clean_norm):
            return ref_date + timedelta(days=2)

        # Handle 'بكرة' / 'غداً' / 'غدا'
        if re.search(r"\b(بكرة|بكره|غدا)\b", clean_norm):
            return ref_date + timedelta(days=1)

        # Handle specific weekdays (including with القادم / الجاي / القادمة / الجاية)
        for day_name, day_idx in cls.ARABIC_WEEKDAYS.items():
            pattern = rf"\b(يوم\s*)?{day_name}(\s+(الجاي|القادم|الجاية|القادمة))?\b"
            if re.search(pattern, clean_norm):
                days_ahead = (day_idx - ref_date.weekday()) % 7
                if days_ahead == 0:
                    days_ahead = 7
                return ref_date + timedelta(days=days_ahead)

        # Try parsing DD-MM-YYYY or DD/MM/YYYY
        dmy_match = re.search(r"\b(\d{1,2})[-/](\d{1,2})[-/](\d{4})\b", clean_norm)
        if dmy_match:
            try:
                day = int(dmy_match.group(1))
                month = int(dmy_match.group(2))
                year = int(dmy_match.group(3))
                return date(year, month, day)
            except ValueError:
                pass

        # Try parsing ISO format YYYY-MM-DD or YYYY/MM/DD
        iso_match = re.search(r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b", clean_norm)
        if iso_match:
            try:
                year = int(iso_match.group(1))
                month = int(iso_match.group(2))
                day = int(iso_match.group(3))
                return date(year, month, day)
            except ValueError:
                pass

        return None

    @classmethod
    def resolve_target_time(cls, time_expr: Optional[str]) -> Optional[time]:
        """Resolve Arabic time expressions into Python time."""
        if not time_expr:
            return None

        clean = time_expr.strip()
        # Strip Arabic tashkeel/diacritics
        clean = re.sub(r"[\u064B-\u065F\u0670]", "", clean)

        # Replace Arabic word numbers with digits (e.g. 'عشرة' -> '10')
        for word, val in cls.ARABIC_HOUR_WORDS.items():
            pattern = rf"\b{word}\b"
            if re.search(pattern, clean):
                clean = re.sub(pattern, str(val), clean)
                break

        # Check explicit formats like 14:30 or 10:00
        colon_match = re.search(r"\b(\d{1,2}):(\d{2})\b", clean)
        if colon_match:
            hour = int(colon_match.group(1))
            minute = int(colon_match.group(2))
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                return time(hour=hour, minute=minute)

        # Check standalone single number (e.g. '10', '9', '14')
        standalone_num = re.match(r"^\s*(\d{1,2})\s*$", clean)
        if standalone_num:
            hour = int(standalone_num.group(1))
            if 0 <= hour <= 23:
                return time(hour=hour, minute=0)

        # Check afternoon / evening hour: e.g., '1 الظهر' or 'الساعة 1 الظهر' or '2 الظهر'
        dhuhr_match = re.search(r"\b(?:الساعة\s*)?(\d{1,2})\s*(?:الظهر|الظهرية)\b", clean)
        if dhuhr_match:
            hour = int(dhuhr_match.group(1))
            if hour == 12:
                return time(hour=12, minute=0)
            elif 1 <= hour <= 11:
                return time(hour=hour + 12, minute=0)

        # Check night/evening hour: e.g. '8 بالليل' or '8 مساء' or '10 مساء' or '10 م'
        night_match = re.search(r"\b(?:الساعة\s*)?(\d{1,2})\s*(?:مساء|مساءً|بالليل|ليلا|ليلاً|\(م\)|م)\b", clean)
        if night_match:
            hour = int(night_match.group(1))
            if hour < 12:
                return time(hour=hour + 12, minute=0)
            return time(hour=hour, minute=0)

        # Check morning hour: e.g. 'الساعة 10' or '10 صباحا' or '10 صباحاً' or 'الساعة 9 الصباح'
        morning_num_match = re.search(
            r"\b(?:الساعة\s*)?(\d{1,2})\s*(?:الصبح|الصباح|صباحا|صباحاً|صباح|\(ص\)|ص)\b",
            clean,
        )
        if morning_num_match:
            hour = int(morning_num_match.group(1))
            if 0 <= hour <= 23:
                return time(hour=hour, minute=0)

        hour_match = re.search(r"\bالساعة\s*(\d{1,2})\b", clean)
        if hour_match:
            hour = int(hour_match.group(1))
            if 0 <= hour <= 23:
                return time(hour=hour, minute=0)

        # Descriptive periods without explicit hour
        # As documented in Section 9: 'العصر' defaults to 16:00 (4:00 PM)
        if re.search(r"\b(العصر|عصراً)\b", clean):
            return time(hour=16, minute=0)

        if re.search(r"\b(الظهر)\b", clean):
            return time(hour=12, minute=0)

        if re.search(r"\b(المغرب)\b", clean):
            return time(hour=18, minute=0)

        if re.search(r"\b(العشاء|العشا)\b", clean):
            return time(hour=20, minute=0)

        if re.search(r"\b(الصباح|صباحاً)\b", clean):
            return time(hour=9, minute=0)

        return None

    @classmethod
    def schedule_task(
        cls,
        date_expr: Optional[str],
        time_expr: Optional[str],
        ref_dt: Optional[datetime] = None,
        default_time: time = time(hour=9, minute=0),
    ) -> Tuple[Optional[SchedulingResult], Optional[str]]:
        """
        Resolve date and time, apply Friday rescheduling rule, and return SchedulingResult.
        Derives all weekday names dynamically from the exact resolved date objects.
        Returns (SchedulingResult, error_message).
        """
        tz = settings.get_timezone()
        target_date = cls.resolve_target_date(date_expr, ref_dt)
        if target_date is None:
            return None, "تعذر تحديد التاريخ المطلوب للمهمة. يرجى تحديد التاريخ بشكل واضح."

        target_time = cls.resolve_target_time(time_expr)
        if target_time is None:
            # If time was not explicitly supplied, use default working hour (09:00)
            target_time = default_time

        resolved_dt = datetime.combine(target_date, target_time, tzinfo=tz)

        # Weekend / Non-working day rescheduling rule:
        # Configurable via settings.company_weekend_days (default: [4] for Friday)
        if target_date.weekday() in settings.company_weekend_days:
            current_date = target_date + timedelta(days=1)
            while current_date.weekday() in settings.company_weekend_days:
                current_date = current_date + timedelta(days=1)
            rescheduled_date = current_date
            rescheduled_dt = datetime.combine(rescheduled_date, target_time, tzinfo=tz)
            orig_weekday = cls.get_arabic_weekday_name(target_date)
            new_weekday = cls.get_arabic_weekday_name(rescheduled_date)
            explanation = (
                f"ملاحظة: تنص سياسة العمل على عدم جدولة المهام يوم {orig_weekday} (عطلة نهاية الأسبوع)، "
                f"لذا تم ترحيل موعد المهمة تلقائياً إلى يوم {new_weekday} الموافق {rescheduled_date.strftime('%Y-%m-%d')} "
                f"في تمام الساعة {target_time.strftime('%H:%M')}."
            )
            return (
                SchedulingResult(
                    due_at=rescheduled_dt,
                    was_rescheduled=True,
                    original_due_at=resolved_dt,
                    explanation=explanation,
                    weekday_name=new_weekday,
                ),
                None,
            )

        target_weekday = cls.get_arabic_weekday_name(target_date)
        return SchedulingResult(due_at=resolved_dt, weekday_name=target_weekday), None
