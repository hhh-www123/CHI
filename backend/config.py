"""Server-only settings. Secrets are never returned by the public status endpoint."""
from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent


def _values() -> dict[str, str]:
    result: dict[str, str] = {}
    path = Path(os.environ.get("TRAVEL_ENV_FILE", ROOT / ".env"))
    if path.is_file():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            result[key] = value
    result.update(os.environ)
    return result


def _number(values: dict[str, str], key: str, default: float, minimum: float, maximum: float) -> float:
    try:
        return max(minimum, min(maximum, float(values.get(key, str(default)))))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    agent_mode: str = "llm"
    candidate_agent_mode: str = "mock"
    candidate_ttl_seconds: int = 600
    llm_api_key: str = field(default="", repr=False)
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-flash"
    llm_model_label: str = "DeepSeek-V4.1-Flash-1017"
    llm_timeout: float = 70
    llm_max_tokens: int = 6000
    amap_key: str = field(default="", repr=False)
    map_timeout: float = 8
    cookie_secure: bool = False
    allowed_origins: tuple[str, ...] = ()
    requests_per_minute: int = 12
    session_secret: str = field(default="", repr=False)


def get_settings() -> Settings:
    values = _values()
    base = values.get("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("LLM_BASE_URL 必须是服务端配置的有效 HTTP(S) 接口地址。")
    mode = values.get("AGENT_MODE", "llm").strip().lower()
    if mode not in {"demo", "llm"}:
        raise ValueError("AGENT_MODE 只支持 llm 或 demo。")
    candidate_mode = values.get("CANDIDATE_AGENT_MODE", "mock").strip().lower()
    if candidate_mode not in {"mock", "llm"}:
        raise ValueError("CANDIDATE_AGENT_MODE 只支持 mock 或 llm。")
    return Settings(
        candidate_agent_mode=candidate_mode,
        candidate_ttl_seconds=int(_number(values, "CANDIDATE_TTL_SECONDS", 600, 30, 3600)),
        agent_mode=mode,
        llm_api_key=values.get("LLM_API_KEY", values.get("DEEPSEEK_API_KEY", "")).strip(),
        llm_base_url=base,
        llm_model=values.get("LLM_MODEL", "deepseek-flash").strip(),
        llm_model_label=values.get("LLM_MODEL_LABEL", "DeepSeek-V4.1-Flash-1017").strip(),
        llm_timeout=_number(values, "LLM_TIMEOUT", 70, 5, 180),
        llm_max_tokens=int(_number(values, "LLM_MAX_TOKENS", 6000, 512, 16000)),
        amap_key=values.get("AMAP_KEY", "").strip(),
        map_timeout=_number(values, "MAP_TIMEOUT", 8, 2, 30),
        cookie_secure=values.get("COOKIE_SECURE", "false").lower() == "true",
        allowed_origins=tuple(x.strip().rstrip("/") for x in values.get("ALLOWED_ORIGINS", "").split(",") if x.strip()),
        requests_per_minute=int(_number(values, "AGENT_REQUESTS_PER_MINUTE", 12, 1, 120)),
        session_secret=values.get("SESSION_SECRET", ""),
    )
