from __future__ import annotations

import base64
import os
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from google import genai


DEFAULT_TTS_MODEL = "gemini-3.1-flash-tts-preview"
DEFAULT_LIVE_MODEL = "gemini-3.1-flash-live-preview"
DEFAULT_VOICE = "Kore"


class GeminiVoiceError(RuntimeError):
    pass


def gemini_tts_configured() -> bool:
    return bool(os.getenv("GEMINI_API_KEY"))


def synthesize_question_wav(text: str, output_path: Path) -> None:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise GeminiVoiceError("GEMINI_API_KEY is not configured")

    model = os.getenv("GEMINI_TTS_MODEL", DEFAULT_TTS_MODEL)
    voice_name = os.getenv("GEMINI_TTS_VOICE", DEFAULT_VOICE)
    prompt = (
        "Say clearly, calmly, and professionally as an academic viva examiner. "
        "Read the question exactly once, with a short natural pause at the end:\n"
        f"{text}"
    )
    request = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {
                        "voiceName": voice_name,
                    }
                }
            },
        },
        "model": model,
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    try:
        with httpx.Client(timeout=float(os.getenv("GEMINI_TTS_TIMEOUT_SECONDS", "45"))) as client:
            response = client.post(
                url,
                headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                json=request,
            )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise GeminiVoiceError(f"Gemini TTS request failed: {exc}") from exc

    inline = extract_inline_audio(response.json())
    pcm = base64.b64decode(inline["data"])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_wav(output_path, pcm)


def create_live_ephemeral_token() -> dict[str, Any]:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise GeminiVoiceError("GEMINI_API_KEY is not configured")

    model = os.getenv("GEMINI_LIVE_MODEL", DEFAULT_LIVE_MODEL)
    now = datetime.now(timezone.utc)
    expire_at = now + timedelta(minutes=int(os.getenv("GEMINI_LIVE_TOKEN_MINUTES", "30")))
    new_session_expire_at = now + timedelta(seconds=int(os.getenv("GEMINI_LIVE_NEW_SESSION_SECONDS", "60")))

    client = genai.Client(api_key=api_key, http_options={"api_version": "v1alpha"})
    token = client.auth_tokens.create(
        config={
            "uses": 1,
            "expire_time": expire_at.isoformat().replace("+00:00", "Z"),
            "new_session_expire_time": new_session_expire_at.isoformat().replace("+00:00", "Z"),
            "live_connect_constraints": {
                "model": model,
                "config": {
                    "response_modalities": ["AUDIO"],
                    "session_resumption": {},
                    "system_instruction": {
                        "parts": [
                            {
                                "text": (
                                    "You are TWELVE, an AI viva examiner. Ask concise academic viva "
                                    "questions and wait for the student's answer. Do not score aloud."
                                )
                            }
                        ]
                    },
                },
            },
            "http_options": {"api_version": "v1alpha"},
        }
    )
    token_name = getattr(token, "name", None)
    if not token_name:
        raise GeminiVoiceError("Gemini did not return an ephemeral token")
    return {
        "configured": True,
        "token": token_name,
        "model": model,
        "websocket_url": (
            "wss://generativelanguage.googleapis.com/ws/"
            "google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContentConstrained"
        ),
        "access_token_query": "access_token",
        "expires_at": expire_at.isoformat(),
        "new_session_expires_at": new_session_expire_at.isoformat(),
    }


def extract_inline_audio(data: dict[str, Any]) -> dict[str, str]:
    try:
        inline = data["candidates"][0]["content"]["parts"][0]["inlineData"]
    except (KeyError, IndexError, TypeError) as exc:
        raise GeminiVoiceError("Gemini TTS response did not include inline audio") from exc
    if not inline.get("data"):
        raise GeminiVoiceError("Gemini TTS response contained empty audio data")
    return inline


def write_wav(path: Path, pcm: bytes) -> None:
    with wave.open(str(path), "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(24000)
        file.writeframes(pcm)
