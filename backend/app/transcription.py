from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any

import httpx

from .gemini_agent import extract_response_text as extract_gemini_text


class TranscriptionError(RuntimeError):
    pass


def transcription_provider() -> str:
    provider = os.getenv("TWELVE_TRANSCRIPTION_PROVIDER", "auto").strip().lower()
    if provider == "local":
        return "local"
    if provider == "openai":
        return "openai" if openai_transcription_configured() else "local"
    if provider == "gemini":
        return "gemini" if gemini_transcription_configured() else "local"
    if openai_transcription_configured():
        return "openai"
    if gemini_transcription_configured():
        return "gemini"
    return "local"


def openai_transcription_configured() -> bool:
    return bool(os.getenv("OPENAI_API_KEY"))


def gemini_transcription_configured() -> bool:
    return bool(os.getenv("GEMINI_API_KEY"))


def transcribe_audio(path: Path, mime_type: str | None, draft_transcript: str | None = None) -> dict[str, Any]:
    provider = transcription_provider()
    if provider == "openai":
        return transcribe_with_openai(path, mime_type)
    if provider == "gemini":
        return transcribe_with_gemini(path, mime_type)
    if draft_transcript and os.getenv("TWELVE_ENV", "local").strip().lower() in {"local", "development", "test"}:
        return {
            "status": "draft_used",
            "text": draft_transcript.strip(),
            "provider": "browser-draft-local",
            "model": "browser-speech-recognition",
            "error": None,
        }
    raise TranscriptionError("No server transcription provider is configured.")


def transcribe_with_openai(path: Path, mime_type: str | None) -> dict[str, Any]:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise TranscriptionError("OPENAI_API_KEY is not configured")
    model = os.getenv("OPENAI_TRANSCRIPTION_MODEL", "gpt-4o-mini-transcribe")
    try:
        with path.open("rb") as file:
            files = {"file": (path.name, file, mime_type or "application/octet-stream")}
            data = {
                "model": model,
                "language": os.getenv("OPENAI_TRANSCRIPTION_LANGUAGE", "en"),
                "response_format": "json",
            }
            with httpx.Client(timeout=float(os.getenv("OPENAI_TRANSCRIPTION_TIMEOUT_SECONDS", "60"))) as client:
                response = client.post(
                    "https://api.openai.com/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    data=data,
                    files=files,
                )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise TranscriptionError(f"OpenAI transcription failed: {exc}") from exc

    payload = response.json()
    text = str(payload.get("text", "")).strip()
    if not text:
        raise TranscriptionError("OpenAI transcription returned empty text")
    return {"status": "transcribed", "text": text, "provider": "openai", "model": model, "error": None}


def transcribe_with_gemini(path: Path, mime_type: str | None) -> dict[str, Any]:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise TranscriptionError("GEMINI_API_KEY is not configured")
    model = os.getenv("GEMINI_TRANSCRIPTION_MODEL", os.getenv("GEMINI_VIVA_MODEL", "gemini-3.5-flash"))
    request = {
        "contents": [
            {
                "parts": [
                    {
                        "inlineData": {
                            "mimeType": mime_type or "audio/webm",
                            "data": base64.b64encode(path.read_bytes()).decode("ascii"),
                        }
                    },
                    {
                        "text": (
                            "Transcribe this student viva answer exactly. Return only the spoken words. "
                            "Do not summarize, correct, score, or add commentary."
                        )
                    },
                ]
            }
        ]
    }
    try:
        with httpx.Client(timeout=float(os.getenv("GEMINI_TRANSCRIPTION_TIMEOUT_SECONDS", "60"))) as client:
            response = client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                json=request,
            )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise TranscriptionError(f"Gemini transcription failed: {exc}") from exc

    text = extract_gemini_text(response.json()).strip()
    if not text:
        raise TranscriptionError("Gemini transcription returned empty text")
    return {"status": "transcribed", "text": text, "provider": "gemini", "model": model, "error": None}
