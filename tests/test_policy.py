"""Tests for company policy question answering."""

from app.services.policy_service import PolicyService, UNSUPPORTED_POLICY_REPLY


def test_official_working_hours():
    """Verify working hours policy answer."""
    ans, supported = PolicyService.answer_question("ماهي ساعات العمل الرسمية؟")
    assert supported is True
    assert "8:00 صباحاً" in ans
    assert "4:00 مساءً" in ans
    assert "السبت" in ans


def test_ordinary_leave_policy():
    """Verify ordinary leave policy answer."""
    ans, supported = PolicyService.answer_question("كم يوم لازم أقدم قبل الإجازة الاعتيادية؟")
    assert supported is True
    assert "خمسة أيام عمل" in ans
    assert "المشرف المباشر" in ans


def test_sick_leave_policy():
    """Verify sick leave medical report policy answer."""
    ans, supported = PolicyService.answer_question("ماهي شروط الإجازة المرضية وتقديم التقرير الطبي؟")
    assert supported is True
    assert "تقرير طبي" in ans
    assert "يومين" in ans


def test_remote_work_policy():
    """Verify remote work allowance policy answer."""
    ans, supported = PolicyService.answer_question("هل مسموح العمل عن بعد وكم يوماً؟")
    assert supported is True
    assert "يوماً واحداً" in ans or "يوم واحد" in ans
    assert "المشرف" in ans


def test_expense_approvals_policy():
    """Verify 500 SAR expense approval thresholds."""
    ans, supported = PolicyService.answer_question("ما هي آلية اعتماد المصروفات والفواتير؟")
    assert supported is True
    assert "500 ريال" in ans
    assert "المشرف" in ans
    assert "المدير" in ans


def test_task_status_update_policy():
    """Verify daily task status update rule."""
    ans, supported = PolicyService.answer_question("متى يجب تحديث حالة المهام؟")
    assert supported is True
    assert "يومياً" in ans
    assert "نهاية يوم العمل" in ans


def test_unsupported_policies_never_fabricated():
    """Verify unsupported topics are explicitly marked unavailable without hallucinations."""
    unsupported_questions = [
        "كم بدل السكن للموظفين؟",
        "هل يوجد تأمين طبي VIP للعائلة؟",
        "كم بونص نهاية السنة؟",
        "هل تمنح الشركة قروض حسنة؟",
        "ما هو رصيد الإجازات السنوية الإضافية؟",
    ]

    for q in unsupported_questions:
        ans, supported = PolicyService.answer_question(q)
        assert supported is False
        assert ans == UNSUPPORTED_POLICY_REPLY
        # Ensure no made-up numerical figures or promises
        assert "ريال" not in ans
        assert "ألف" not in ans
