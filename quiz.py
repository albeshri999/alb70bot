# -*- coding: utf-8 -*-
from storage import load_questions, get_user, update_user


def get_question(index: int) -> dict | None:
    questions = load_questions()
    if 0 <= index < len(questions):
        return questions[index]
    return None


def total_questions() -> int:
    return len(load_questions())


def check_answer(q: dict, user_answer: str) -> bool:
    correct = q["answer"].strip().upper()
    given = user_answer.strip().upper()

    arabic_map = {"أ": "A", "ب": "B", "ج": "C", "د": "D"}
    given = arabic_map.get(given, given)

    return given == correct


def advance_question(user_id: int) -> dict | None:
    user = get_user(user_id)
    next_index = user["current_question"] + 1
    if next_index >= total_questions():
        update_user(user_id, completed=True, state="completed")
        return None
    update_user(user_id, current_question=next_index)
    return get_question(next_index)
