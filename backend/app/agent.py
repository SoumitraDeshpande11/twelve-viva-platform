from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any


QUESTION_CATEGORIES = [
    "problem-statement-verification",
    "project-deep-dive",
    "cross-questioning",
    "core-subject",
    "rubric-defense",
]


@dataclass
class QuestionSeed:
    category: str
    text: str
    expected_points: list[str]


def build_question_plan(exam: dict[str, Any], student: dict[str, Any], submission_text: str) -> list[QuestionSeed]:
    project_terms = keywords(submission_text or exam["problem_statement"], limit=8)
    curriculum_terms = keywords(exam["curriculum"], limit=8)
    rubric_terms = keywords(exam["rubric"], limit=6)

    project_focus = ", ".join(project_terms[:3]) or "your implementation"
    subject_focus = ", ".join(curriculum_terms[:3]) or "core CSE concepts"
    rubric_focus = ", ".join(rubric_terms[:3]) or "correctness, clarity, and depth"

    return [
        QuestionSeed(
            category=QUESTION_CATEGORIES[0],
            text=f"{student['name']}, explain the exact problem statement your project solves and the main users affected by it.",
            expected_points=["Clear problem framing", "Identifies user or stakeholder", "Connects answer to submitted project"],
        ),
        QuestionSeed(
            category=QUESTION_CATEGORIES[1],
            text=f"Walk through the most important technical component of your project, especially around {project_focus}.",
            expected_points=["Names implementation components", "Explains data/control flow", "Mentions tradeoffs or constraints"],
        ),
        QuestionSeed(
            category=QUESTION_CATEGORIES[2],
            text="If your current design failed under double the expected load or usage, what would you change first and why?",
            expected_points=["Recognizes likely bottleneck", "Proposes concrete mitigation", "Justifies priority"],
        ),
        QuestionSeed(
            category=QUESTION_CATEGORIES[3],
            text=f"Relate your project to one core subject concept from the curriculum, such as {subject_focus}.",
            expected_points=["Maps project to subject concept", "Uses correct terminology", "Gives a project-specific example"],
        ),
        QuestionSeed(
            category=QUESTION_CATEGORIES[4],
            text=f"Using the rubric focus areas ({rubric_focus}), what part of your work deserves the strongest marks and what remains weak?",
            expected_points=["Self-evaluates honestly", "Uses rubric language", "Identifies improvement area"],
        ),
    ]


def score_answer(question: dict[str, Any], answer_text: str, rubric: str) -> dict[str, Any]:
    answer = answer_text.strip()
    expected = question.get("expected_points", [])
    if isinstance(expected, str):
        expected = []

    if not answer:
        return {
            "score": 0.0,
            "max_score": 10.0,
            "reasoning": "No answer was submitted.",
        }

    normalized = answer.lower()
    word_count = len(re.findall(r"\b\w+\b", normalized))
    coverage = sum(1 for point in expected if keyword_overlap(point, normalized))
    coverage_score = (coverage / max(len(expected), 1)) * 5.0
    depth_score = min(word_count / 45, 1.0) * 3.0
    specificity_score = min(len(set(re.findall(r"\b[a-z]{5,}\b", normalized))) / 18, 1.0) * 2.0
    score = round(min(10.0, coverage_score + depth_score + specificity_score), 1)

    if score >= 8:
        verdict = "Strong answer with specific detail and good alignment to expected points."
    elif score >= 5:
        verdict = "Adequate answer, but it needs more direct coverage of expected points or implementation detail."
    else:
        verdict = "Weak answer with limited specificity or incomplete coverage of the question."

    rubric_hint = first_sentence(rubric) or "Rubric alignment checked."
    return {
        "score": score,
        "max_score": 10.0,
        "reasoning": f"{verdict} {rubric_hint}",
    }


def create_followup(question: dict[str, Any], answer_text: str) -> str | None:
    if len(answer_text.split()) >= 35:
        return None
    digest = hashlib.sha1((question["text"] + answer_text).encode()).hexdigest()
    prompts = [
        "Give one concrete example from your code or design that supports that answer.",
        "What assumption are you making there, and how would you test it?",
        "Which alternative did you reject, and why was your chosen approach better for this project?",
    ]
    return prompts[int(digest[:2], 16) % len(prompts)]


def keywords(text: str, limit: int) -> list[str]:
    stop = {
        "about",
        "after",
        "again",
        "also",
        "because",
        "before",
        "between",
        "could",
        "every",
        "from",
        "have",
        "into",
        "more",
        "that",
        "their",
        "there",
        "these",
        "this",
        "through",
        "using",
        "with",
        "would",
    }
    words = re.findall(r"\b[a-zA-Z][a-zA-Z0-9_-]{3,}\b", text.lower())
    counts: dict[str, int] = {}
    for word in words:
        if word in stop:
            continue
        counts[word] = counts.get(word, 0) + 1
    return [word for word, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]]


def keyword_overlap(point: str, answer: str) -> bool:
    terms = keywords(point, limit=4)
    return any(term in answer for term in terms)


def first_sentence(text: str) -> str:
    match = re.search(r"[^.!?]+[.!?]", text.strip())
    return match.group(0).strip() if match else text.strip()[:120]
