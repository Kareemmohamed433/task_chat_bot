"""Company Policy Q&A service strictly grounded in the official company policy."""

import re
from typing import Tuple

COMPANY_POLICIES = [
    {
        "id": 1,
        "topic": "ساعات العمل",
        "keywords": [r"ساعات\s*العمل", r"وقت\s*الدوام", r"أوقات\s*العمل", r"الدوام", r"السبت\s*نصف\s*يوم", r"متى\s*يبدأ\s*الدوام", r"متى\s*ينتهي\s*الدوام"],
        "answer": "ساعات العمل الرسمية هي من الأحد إلى الخميس، من الساعة 8:00 صباحاً حتى 4:00 مساءً. ويوم السبت نصف يوم عمل حتى الساعة 12:00 ظهراً.",
    },
    {
        "id": 2,
        "topic": "الإجازة الاعتيادية",
        "keywords": [r"إجازة\s*اعتيادية", r"اجازة\s*اعتيادية", r"إجازة", r"اجازة", r"أقدم\s*قبل\s*الإجازة", r"اقدم\s*قبل\s*الاجازة", r"طلب\s*إجازة", r"طلب\s*اجازة", r"كم\s*يوم.*إجازة"],
        "answer": "تنص السياسة على أن الإجازة الاعتيادية يجب طلبها قبل خمسة أيام عمل على الأقل، وتتطلب موافقة المشرف المباشر.",
    },
    {
        "id": 3,
        "topic": "الإجازة المرضية",
        "keywords": [r"إجازة\s*مرضية", r"اجازة\s*مرضية", r"مرض", r"تقرير\s*طبي", r"مرضي"],
        "answer": "تتطلب الإجازة المرضية تقديم تقرير طبي خلال يومين من العودة إلى العمل.",
    },
    {
        "id": 4,
        "topic": "العمل عن بعد",
        "keywords": [r"العمل\s*عن\s*بعد", r"عمل\s*عن\s*بعد", r"ريموت", r"من\s*البيت", r"أونلاين"],
        "answer": "يُسمح بالعمل عن بعد يوماً واحداً في الأسبوع بموافقة المشرف المباشر.",
    },
    {
        "id": 5,
        "topic": "اعتماد المصروفات",
        "keywords": [r"مصروفات", r"مصاريف", r"فواتير", r"500", r"ريال", r"عهدة", r"موافقة\s*المدير.*مبلغ"],
        "answer": "المصروفات حتى 500 ريال سعودي يعتمدها المشرف المباشر، أما المبالغ التي تتجاوز 500 ريال سعودي فتتطلب موافقة المدير.",
    },
    {
        "id": 6,
        "topic": "تحديث حالة المهام",
        "keywords": [r"تحديث\s*حالة\s*المهام", r"تحديث\s*المهمة", r"تحديث\s*المهام", r"إغلاق\s*المهمة", r"نهاية\s*يوم\s*العمل"],
        "answer": "يجب تحديث حالة المهام المسندة يومياً قبل نهاية يوم العمل.",
    },
]

UNSUPPORTED_POLICY_REPLY = "المعلومات المطلوبة غير متوفرة في سياسة الشركة المعتمدة."


class PolicyService:
    """Answers company policy questions strictly adhering to the 6 official company rules."""

    @classmethod
    def answer_question(cls, question: str) -> Tuple[str, bool]:
        """
        Answer a policy question based strictly on the 6 official rules.
        Returns (answer_text, is_supported).
        """
        if not question or not question.strip():
            return UNSUPPORTED_POLICY_REPLY, False

        # 1. Strip Arabic tashkeel / diacritics
        clean_norm = re.sub(r"[\u064B-\u065F\u0670]", "", question.strip())

        # 2. Normalized search string for robust matching across hamzas, taa-marbuta, and typos
        norm_search = clean_norm.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").replace("ة", "ه")

        # Check explicit unsupported topics (e.g. housing allowance, bonuses, health insurance)
        unsupported_topics = [
            r"بدل\s*سكن",
            r"تامين\s*طبي",
            r"تامين\s*صحي",
            r"مكافاه",
            r"مكافات",
            r"بونص",
            r"تذاكر",
            r"قرض",
            r"سلفه",
            r"نهايه\s*خدمه",
            r"بدلات",
            r"\bسكن\b",
            r"رصيد",
        ]
        for pattern in unsupported_topics:
            if re.search(pattern, norm_search):
                return UNSUPPORTED_POLICY_REPLY, False

        # Sick leave check before generic leave check
        if re.search(r"(مرض|مرضي|مرضيه|تقرير\s*طبي)", norm_search):
            return COMPANY_POLICIES[2]["answer"], True

        # Ordinary leave check:
        # Handles "إجازة", "اجازة", "اجازتي", "أقدّم", "اقدم", "الزم أقدّم", "لازم أقدم", "كم يوم الزم"
        if (
            re.search(r"اجاز", norm_search)
            or re.search(r"(اقدم|تقديم)\s*(قبل)?\s*(الاجاز|اجاز)?", norm_search)
            or re.search(r"(الزم|لازم)\s*(اقدم|تقديم)", norm_search)
        ):
            return COMPANY_POLICIES[1]["answer"], True

        # Remote work check
        if re.search(r"(عن\s*بعد|من\s*البيت|ريموت)", norm_search):
            return COMPANY_POLICIES[3]["answer"], True

        # Expenses check
        if re.search(r"(مصروف|مصاريف|فواتير|500\s*ريال|ميزانيه|عهده)", norm_search):
            return COMPANY_POLICIES[4]["answer"], True

        # Task update check
        if re.search(r"(تحديث\s*المهام|تحديث\s*حاله|تحديث\s*المهمه|اغلاق\s*المهام)", norm_search):
            return COMPANY_POLICIES[5]["answer"], True

        # Working hours check
        if re.search(r"(ساعات\s*العمل|اوقات\s*الدوام|وقت\s*الدوام|الدوام|السبت)", norm_search):
            return COMPANY_POLICIES[0]["answer"], True

        # Default fallback for questions not covered by the 6 rules
        return UNSUPPORTED_POLICY_REPLY, False
