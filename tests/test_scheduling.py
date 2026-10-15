"""Tests for relative date calculations, time parsing, and Friday rescheduling."""

from datetime import datetime, time
from zoneinfo import ZoneInfo
from app.services.scheduling_service import SchedulingService

RIYADH_TZ = ZoneInfo("Asia/Riyadh")
REF_DT = datetime(2026, 10, 13, 9, 0, 0, tzinfo=RIYADH_TZ)  # Tuesday


def test_relative_date_resolution():
    """Verify standard relative date expressions resolve relative to Tuesday reference date."""
    # بكرة -> Wednesday 2026-10-14
    d_tomorrow = SchedulingService.resolve_target_date("بكرة", REF_DT)
    assert d_tomorrow.isoformat() == "2026-10-14"

    # بعد بكرة -> Thursday 2026-10-15
    d_after_tomorrow = SchedulingService.resolve_target_date("بعد بكرة", REF_DT)
    assert d_after_tomorrow.isoformat() == "2026-10-15"

    # الجمعة -> Friday 2026-10-16
    d_friday = SchedulingService.resolve_target_date("الجمعة", REF_DT)
    assert d_friday.isoformat() == "2026-10-16"

    # الأحد الجاي -> strictly next Sunday 2026-10-18
    d_next_sunday = SchedulingService.resolve_target_date("الأحد الجاي", REF_DT)
    assert d_next_sunday.isoformat() == "2026-10-18"

    # اليوم -> Tuesday 2026-10-13
    d_today = SchedulingService.resolve_target_date("اليوم", REF_DT)
    assert d_today.isoformat() == "2026-10-13"


def test_time_expression_resolution():
    """Verify explicit and cultural Arabic time expressions."""
    # الساعة 10
    t1 = SchedulingService.resolve_target_time("الساعة 10")
    assert t1 == time(10, 0)

    # 1 الظهر -> 13:00
    t2 = SchedulingService.resolve_target_time("1 الظهر")
    assert t2 == time(13, 0)

    # العصر -> default 16:00 (4:00 PM) as documented
    t3 = SchedulingService.resolve_target_time("العصر")
    assert t3 == time(16, 0)

    # الساعة 9 الصباح -> 09:00
    t4 = SchedulingService.resolve_target_time("الساعة 9 الصباح")
    assert t4 == time(9, 0)


def test_friday_scheduling_rule_enforcement():
    """Verify Friday tasks are rescheduled to Saturday while preserving the exact time."""
    result, err = SchedulingService.schedule_task("الجمعة", "الساعة 9", ref_dt=REF_DT)
    assert err is None
    assert result.was_rescheduled is True
    assert result.due_at.date().isoformat() == "2026-10-17"  # Saturday
    assert result.due_at.hour == 9
    assert result.due_at.minute == 0
    assert "الجمعة" in result.explanation
    assert "السبت" in result.explanation


def test_non_friday_scheduling_unaltered():
    """Verify non-Friday tasks remain on their original target date."""
    result, err = SchedulingService.schedule_task("بكرة", "الساعة 10", ref_dt=REF_DT)
    assert err is None
    assert result.was_rescheduled is False
    assert result.due_at.date().isoformat() == "2026-10-14"  # Wednesday
    assert result.due_at.hour == 10
    assert result.due_at.minute == 0


def test_ambiguous_date_handling():
    """Verify invalid date strings return a descriptive error instead of guessing."""
    result, err = SchedulingService.schedule_task("في وقت لاحق غير محدد", "الساعة 10", ref_dt=REF_DT)
    assert result is None
    assert err is not None
    assert "تعذر تحديد التاريخ" in err
