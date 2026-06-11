from __future__ import annotations

import json
import os
from typing import Any

import httpx

from .agent import QUESTION_CATEGORIES, QuestionSeed


RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_MODEL = "gpt-5.5"


class OpenAIAgentError(RuntimeError):
    pass


def openai_configured() -> bool:
    return bool(os.getenv("OPENAI_API_KEY"))


def build_question_plan_with_openai(exam: dict[str, Any], student: dict[str, Any], submission_text: str) -> list[QuestionSeed]:
    payload = call_structured_response(
        name="twelve_question_plan",
        schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["questions"],
            "properties": {
                "questions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["category", "text", "expected_points"],
                        "properties": {
                            "category": {"type": "string", "enum": QUESTION_CATEGORIES},
                            "text": {"type": "string"},
                            "expected_points": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                    },
                }
            },
        },
        instructions=(
            "You are TWELVE, an academic viva examiner for a BTech CSE project viva. "
            "Create concise, fair, exam-ready questions. Ask exactly one thing per question. "
            "Do not reveal expected points to the student."
        ),
        user_input={
            "task": "Create a five-question viva plan.",
            "required_categories": QUESTION_CATEGORIES,
            "student": {"name": student["name"], "roll_number": student["roll_number"]},
            "exam": {
                "name": exam["name"],
                "problem_statement": trim(exam["problem_statement"], 5000),
                "curriculum": trim(exam["curriculum"], 5000),
                "rubric": trim(exam["rubric"], 5000),
            },
            "submission_excerpt": trim(submission_text, 18000),
        },
    )
    questions = payload["questions"]
    if len(questions) != 5:
        raise OpenAIAgentError(f"OpenAI returned {len(questions)} questions; expected 5")

    normalized = [
        QuestionSeed(
            category=question["category"],
            text=question["text"].strip(),
            expected_points=[point.strip() for point in question["expected_points"] if point.strip()][:5],
        )
        for question in questions
    ]
    if any(not question.text or len(question.expected_points) < 2 for question in normalized):
        raise OpenAIAgentError("OpenAI returned incomplete questions")
    return normalized


def score_answer_with_openai(question: dict[str, Any], answer_text: str, rubric: str, exam: dict[str, Any]) -> dict[str, Any]:
    payload = call_structured_response(
        name="twelve_answer_score",
        schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["score", "max_score", "reasoning"],
            "properties": {
                "score": {"type": "number"},
                "max_score": {"type": "number"},
                "reasoning": {"type": "string"},
            },
        },
        instructions=(
            "You are TWELVE's scoring specialist. Grade only the answer to the current question. "
            "Use the rubric and expected points, but do not let proctoring or behavior affect marks. "
            "Be strict, fair, and concise. Return JSON only."
        ),
        user_input={
            "task": "Score this viva answer out of 10.",
            "exam": {
                "name": exam["name"],
                "problem_statement": trim(exam["problem_statement"], 4000),
                "curriculum": trim(exam["curriculum"], 4000),
                "rubric": trim(rubric, 6000),
            },
            "question": {
                "category": question["category"],
                "text": question["text"],
                "expected_points": question.get("expected_points", []),
            },
            "student_answer": trim(answer_text, 10000),
        },
    )
    return {
        "score": max(0.0, min(10.0, round(float(payload["score"]), 1))),
        "max_score": 10.0,
        "reasoning": payload["reasoning"].strip(),
    }


def create_followup_with_openai(question: dict[str, Any], answer_text: str, rubric: str) -> str | None:
    payload = call_structured_response(
        name="twelve_followup",
        schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["should_ask", "question"],
            "properties": {
                "should_ask": {"type": "boolean"},
                "question": {"type": "string"},
            },
        },
        instructions=(
            "You are TWELVE's follow-up planner. Ask a follow-up only when the answer is vague, "
            "too short, or misses a key expected point. The follow-up must be one concise question."
        ),
        user_input={
            "task": "Decide whether one follow-up is needed.",
            "rubric": trim(rubric, 4000),
            "question": {
                "category": question["category"],
                "text": question["text"],
                "expected_points": question.get("expected_points", []),
            },
            "student_answer": trim(answer_text, 8000),
        },
    )
    followup = payload["question"].strip()
    return followup if payload["should_ask"] and followup else None


def call_structured_response(name: str, schema: dict[str, Any], instructions: str, user_input: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise OpenAIAgentError("OPENAI_API_KEY is not configured")

    request = {
        "model": os.getenv("OPENAI_VIVA_MODEL", DEFAULT_MODEL),
        "instructions": instructions,
        "input": json.dumps(user_input, ensure_ascii=True),
        "text": {
            "format": {
                "type": "json_schema",
                "name": name,
                "strict": True,
                "schema": schema,
            }
        },
    }
    try:
        with httpx.Client(timeout=float(os.getenv("OPENAI_VIVA_TIMEOUT_SECONDS", "45"))) as client:
            response = client.post(
                RESPONSES_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=request,
            )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise OpenAIAgentError(f"OpenAI request failed: {exc}") from exc

    text = extract_response_text(response.json())
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise OpenAIAgentError(f"OpenAI response was not valid JSON: {text[:300]}") from exc


def extract_response_text(data: dict[str, Any]) -> str:
    if isinstance(data.get("output_text"), str):
        return data["output_text"]
    chunks: list[str] = []
    for item in data.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if isinstance(content.get("text"), str):
                chunks.append(content["text"])
    if not chunks:
        raise OpenAIAgentError("OpenAI response did not include output text")
    return "".join(chunks)


def trim(value: str, limit: int) -> str:
    value = value or ""
    return value[:limit]
