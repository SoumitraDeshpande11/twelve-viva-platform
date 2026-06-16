"""Local-LLM provider backed by Ollama (cross-platform: Linux + macOS/Metal).

Mirrors gemini_agent / openai_agent: each function raises OllamaAgentError on failure so
the dispatcher in main.py can fall back to the deterministic local heuristic. Uses the
Ollama HTTP API's structured-output `format` field (a JSON schema) to get parseable JSON.
No model runs in-process — it talks to a local `ollama serve` (default :11434).
"""
from __future__ import annotations

import json
import os
from typing import Any

import httpx

from .agent import QUESTION_CATEGORIES, QuestionSeed

DEFAULT_MODEL = "llama3.2:3b"


class OllamaAgentError(RuntimeError):
    pass


def ollama_host() -> str:
    return os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")


def ollama_model() -> str:
    return os.getenv("OLLAMA_MODEL", DEFAULT_MODEL)


def ollama_configured() -> bool:
    # The model server is local; assume available when selected. A down/unreachable server
    # surfaces as an OllamaAgentError at call time, which the dispatcher falls back from.
    return True


def build_question_plan_with_ollama(exam: dict[str, Any], student: dict[str, Any], submission_text: str) -> list[QuestionSeed]:
    payload = call_structured_response(
        schema={
            "type": "object",
            "required": ["questions"],
            "properties": {
                "questions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["category", "text", "expected_points"],
                        "properties": {
                            "category": {"type": "string", "enum": QUESTION_CATEGORIES},
                            "text": {"type": "string"},
                            "expected_points": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                }
            },
        },
        instructions=(
            "You are TWELVE, an academic viva examiner for a BTech CSE project viva. "
            "Create exactly five concise, fair, exam-ready questions. Ask exactly one thing per question. "
            "Use each of the required categories exactly once. Each question needs 3-5 expected_points. "
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
            "submission_excerpt": trim(submission_text, 12000),
        },
    )
    questions = payload.get("questions", [])
    if len(questions) != 5:
        raise OllamaAgentError(f"Ollama returned {len(questions)} questions; expected 5")
    normalized = [
        QuestionSeed(
            category=question["category"],
            text=str(question["text"]).strip(),
            expected_points=[str(point).strip() for point in question.get("expected_points", []) if str(point).strip()][:5],
        )
        for question in questions
    ]
    if any(not question.text or len(question.expected_points) < 2 for question in normalized):
        raise OllamaAgentError("Ollama returned incomplete questions")
    return normalized


def score_answer_with_ollama(question: dict[str, Any], answer_text: str, rubric: str, exam: dict[str, Any]) -> dict[str, Any]:
    payload = call_structured_response(
        schema={
            "type": "object",
            "required": ["score", "max_score", "reasoning"],
            "properties": {
                "score": {"type": "number"},
                "max_score": {"type": "number"},
                "reasoning": {"type": "string"},
            },
        },
        instructions=(
            "You are TWELVE's scoring specialist. Grade only the answer to the current question, out of 10. "
            "Use the rubric and expected points. Do not let proctoring or behavior affect marks. "
            "Be strict, fair, and concise."
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
        "reasoning": str(payload["reasoning"]).strip(),
    }


def create_followup_with_ollama(question: dict[str, Any], answer_text: str, rubric: str) -> str | None:
    payload = call_structured_response(
        schema={
            "type": "object",
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
    followup = str(payload.get("question", "")).strip()
    return followup if payload.get("should_ask") and followup else None


def call_structured_response(schema: dict[str, Any], instructions: str, user_input: dict[str, Any]) -> dict[str, Any]:
    # Use Ollama's OpenAI-compatible endpoint (/v1/chat/completions): it is the most
    # portable surface across Ollama builds/platforms (incl. macOS). JSON mode + an
    # explicit schema in the prompt give us parseable structured output.
    system = f"{instructions}\nRespond with ONLY a JSON object matching this schema:\n{json.dumps(schema)}"
    body = {
        "model": ollama_model(),
        "stream": False,
        "temperature": float(os.getenv("OLLAMA_TEMPERATURE", "0.2")),
        # Cap output length so a chatty model can't run long — bounds latency.
        "max_tokens": int(os.getenv("OLLAMA_MAX_TOKENS", "1024")),
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user_input)},
        ],
    }
    try:
        with httpx.Client(timeout=float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "180"))) as client:
            response = client.post(f"{ollama_host()}/v1/chat/completions", json=body)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise OllamaAgentError(f"Ollama request failed: {exc}") from exc
    try:
        content = response.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise OllamaAgentError("Ollama response was malformed") from exc
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise OllamaAgentError(f"Ollama response was not valid JSON: {content[:300]}") from exc


def trim(value: str, limit: int) -> str:
    value = value or ""
    return value[:limit]
