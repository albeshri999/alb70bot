# -*- coding: utf-8 -*-
ARABIC_ORDINALS = [
    "الأولى", "الثانية", "الثالثة", "الرابعة", "الخامسة",
    "السادسة", "السابعة", "الثامنة", "التاسعة", "العاشرة",
    "الحادية عشرة", "الثانية عشرة", "الثالثة عشرة", "الرابعة عشرة", "الخامسة عشرة",
    "السادسة عشرة", "السابعة عشرة", "الثامنة عشرة", "التاسعة عشرة", "العشرون",
]


def default_question(step: int) -> str:
    if step < len(ARABIC_ORDINALS):
        return f"❓ ما هي كلمة السر {ARABIC_ORDINALS[step]}؟"
    return f"❓ ما هي كلمة السر رقم {step + 1}؟"


def stage_ordinal(idx: int) -> str:
    return ARABIC_ORDINALS[idx] if idx < len(ARABIC_ORDINALS) else str(idx + 1)


def get_stage_question(day_data: dict, step: int) -> str:
    stages = day_data.get("stages", [])
    if step < len(stages):
        q = stages[step].get("question", "")
        return q if q else default_question(step)
    return default_question(step)


def get_stage_answer(day_data: dict, step: int) -> str:
    stages = day_data.get("stages", [])
    if step < len(stages):
        return stages[step].get("answer", "")
    return ""


def get_stage_meaning(day_data: dict, step: int) -> str:
    stages = day_data.get("stages", [])
    if step < len(stages):
        return stages[step].get("meaning", "")
    return ""
