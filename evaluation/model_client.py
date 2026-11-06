"""Single-call OpenAI-compatible client for the future formal runner.

There is intentionally no Mock fallback and no automatic retry here. The runner
must apply the same preregistered retry policy to every method and record every
failed attempt. API keys are accepted in memory and never returned in metadata.
"""

from __future__ import annotations

from dataclasses import dataclass
import time
from urllib.parse import urlsplit

import httpx


@dataclass(frozen=True)
class ModelConfig:
    provider: str
    base_url: str
    model: str
    api_key: str
    temperature: float = 0.2
    max_output_tokens: int = 6000
    timeout_seconds: float = 70
    seed: int | None = None

    def validate(self) -> None:
        parsed = urlsplit(self.base_url.rstrip("/"))
        if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("base_url must be a credential-free HTTP(S) origin/path")
        if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("non-local model endpoints must use HTTPS")
        if not self.provider.strip() or not self.model.strip() or not self.api_key.strip():
            raise ValueError("provider, model and api_key are required")
        if not 0 <= self.temperature <= 2:
            raise ValueError("temperature must be between 0 and 2")
        if not 256 <= self.max_output_tokens <= 100000:
            raise ValueError("max_output_tokens must be between 256 and 100000")
        if not 5 <= self.timeout_seconds <= 300:
            raise ValueError("timeout_seconds must be between 5 and 300")


class ModelCallError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        http_status: int | None = None,
        retryable: bool = False,
        latency_ms: float | None = None,
        details: dict | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.http_status = http_status
        self.retryable = retryable
        self.latency_ms = latency_ms
        self.details = dict(details or {})

    def metadata(self) -> dict:
        metadata = {
            "code": self.code,
            "httpStatus": self.http_status,
            "retryable": self.retryable,
            "latencyMs": self.latency_ms,
        }
        if self.details:
            metadata["details"] = self.details
        return metadata


def invoke_json_prompt(prompt: str, config: ModelConfig) -> dict:
    config.validate()
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt must be a non-empty string")
    payload = {
        "model": config.model,
        "messages": [{"role": "system", "content": prompt}],
        "response_format": {"type": "json_object"},
        "stream": False,
        "temperature": config.temperature,
        "max_tokens": config.max_output_tokens,
    }
    if config.seed is not None:
        payload["seed"] = config.seed
    started = time.perf_counter()
    try:
        with httpx.Client(timeout=config.timeout_seconds, follow_redirects=False, trust_env=False) as client:
            response = client.post(
                config.base_url.rstrip("/") + "/chat/completions",
                headers={"Authorization": "Bearer " + config.api_key, "Content-Type": "application/json"},
                json=payload,
            )
    except httpx.TimeoutException as error:
        latency_ms = round((time.perf_counter() - started) * 1000, 3)
        raise ModelCallError("MODEL_TIMEOUT", "model request timed out", retryable=True, latency_ms=latency_ms) from error
    except httpx.HTTPError as error:
        latency_ms = round((time.perf_counter() - started) * 1000, 3)
        raise ModelCallError("MODEL_UNAVAILABLE", "model request failed", retryable=True, latency_ms=latency_ms) from error

    latency_ms = round((time.perf_counter() - started) * 1000, 3)
    if response.status_code in {401, 403}:
        raise ModelCallError("MODEL_AUTH_FAILED", "model authentication failed", http_status=response.status_code, latency_ms=latency_ms)
    if response.status_code == 429:
        raise ModelCallError("MODEL_RATE_LIMIT", "model rate limit reached", http_status=429, retryable=True, latency_ms=latency_ms)
    if response.status_code >= 500:
        raise ModelCallError("MODEL_PROVIDER_ERROR", "model provider returned a server error", http_status=response.status_code, retryable=True, latency_ms=latency_ms)
    if response.status_code >= 400:
        raise ModelCallError("MODEL_HTTP_ERROR", "model provider rejected the request", http_status=response.status_code, latency_ms=latency_ms)
    if len(response.content) > 1_000_000:
        raise ModelCallError("MODEL_RESPONSE_TOO_LARGE", "model response exceeded 1 MB", latency_ms=latency_ms)
    try:
        body = response.json()
        choice = body["choices"][0]
        finish_reason = choice.get("finish_reason")
        content = choice["message"]["content"]
        if finish_reason != "stop" or not isinstance(content, str) or not content:
            raise ValueError("incomplete response")
        usage = body.get("usage") or {}
        input_tokens = usage.get("prompt_tokens")
        output_tokens = usage.get("completion_tokens")
        if input_tokens is not None and (not isinstance(input_tokens, int) or input_tokens < 0):
            raise ValueError("invalid prompt token count")
        if output_tokens is not None and (not isinstance(output_tokens, int) or output_tokens < 0):
            raise ValueError("invalid completion token count")
    except (ValueError, TypeError, KeyError, IndexError) as error:
        raise ModelCallError("MODEL_INVALID_RESPONSE", "model returned an incomplete or invalid response", latency_ms=latency_ms) from error

    return {
        "text": content,
        "metadata": {
            "provider": config.provider,
            "requestedModel": config.model,
            "responseModel": body.get("model"),
            "providerRequestId": response.headers.get("x-request-id") or response.headers.get("request-id"),
            "finishReason": finish_reason,
            "latencyMs": latency_ms,
            "inputTokens": input_tokens,
            "outputTokens": output_tokens,
            "seed": config.seed,
        },
    }
