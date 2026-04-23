import json
import os
import time
from dataclasses import dataclass
from typing import Callable, Optional


DEFAULT_GEMINI_MODEL = (os.getenv("GEMINI_MODEL") or "gemini-2.5-flash").replace("models/", "")


@dataclass(frozen=True)
class GeminiErrorInfo:
    kind: str
    summary: str
    retryable: bool
    status_code: Optional[int] = None


def get_project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def normalize_model_name(model_name: Optional[str]) -> str:
    return (model_name or DEFAULT_GEMINI_MODEL).replace("models/", "")


def strip_json_fences(text: Optional[str]) -> str:
    cleaned = (text or "").strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[len("```json"):].strip()
    elif cleaned.startswith("```"):
        cleaned = cleaned[len("```"):].strip()

    if cleaned.endswith("```"):
        cleaned = cleaned[:-3].strip()

    return cleaned


def classify_gemini_error(error: Exception) -> GeminiErrorInfo:
    if isinstance(error, json.JSONDecodeError):
        return GeminiErrorInfo(
            kind="invalid_json",
            summary="Gemini returned text that was not valid JSON.",
            retryable=False,
        )

    message = str(error).strip()
    lower_message = message.lower()

    if (
        "429" in lower_message
        or "quota" in lower_message
        or "rate limit" in lower_message
        or "resource_exhausted" in lower_message
    ):
        return GeminiErrorInfo(
            kind="rate_limit",
            summary="Gemini quota or rate limit was exceeded.",
            retryable=True,
            status_code=429,
        )

    if (
        "503" in lower_message
        or "service unavailable" in lower_message
        or "temporarily unavailable" in lower_message
        or "deadline exceeded" in lower_message
        or "timeout" in lower_message
    ):
        return GeminiErrorInfo(
            kind="service_unavailable",
            summary="Gemini was temporarily unavailable.",
            retryable=True,
            status_code=503,
        )

    if ("404" in lower_message or "not found" in lower_message) and "model" in lower_message:
        return GeminiErrorInfo(
            kind="invalid_model",
            summary="The configured Gemini model name is invalid or not available for this project.",
            retryable=False,
            status_code=404,
        )

    if (
        "403" in lower_message
        or "permission denied" in lower_message
        or "api key not valid" in lower_message
        or "invalid api key" in lower_message
        or "leaked" in lower_message
    ):
        return GeminiErrorInfo(
            kind="auth",
            summary="The Gemini API key is invalid, blocked, or lacks permission.",
            retryable=False,
            status_code=403,
        )

    if "failed to process image" in lower_message:
        return GeminiErrorInfo(
            kind="image_processing",
            summary="The image could not be processed by the OCR or Gemini step.",
            retryable=False,
        )

    return GeminiErrorInfo(
        kind="general",
        summary=message or error.__class__.__name__,
        retryable=False,
    )


def format_gemini_error(error: Exception) -> str:
    info = classify_gemini_error(error)
    raw_message = str(error).strip()
    if raw_message and raw_message != info.summary:
        return f"{info.kind}: {info.summary} Raw error: {raw_message}"
    return f"{info.kind}: {info.summary}"


def retry_delay_seconds(attempt_number: int) -> int:
    return min(30, 2 ** max(0, attempt_number - 1) * 3)


def call_with_retries(
    operation: Callable[[], object],
    *,
    max_attempts: int = 4,
    on_retry: Optional[Callable[[GeminiErrorInfo, int, int], None]] = None,
):
    last_error = None

    for attempt in range(1, max_attempts + 1):
        try:
            return operation()
        except Exception as exc:  # noqa: BLE001 - preserve original library errors
            last_error = exc
            error_info = classify_gemini_error(exc)

            if not error_info.retryable or attempt >= max_attempts:
                raise

            delay = retry_delay_seconds(attempt)
            if on_retry:
                on_retry(error_info, attempt, delay)
            time.sleep(delay)

    if last_error:
        raise last_error
