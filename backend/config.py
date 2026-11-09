"""Server-only settings. Secrets are never returned by the public status endpoint."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import threading
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
_RUNTIME_KEYS = {"AGENT_MODE", "CANDIDATE_AGENT_MODE", "LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "LLM_MODEL_LABEL", "AMAP_KEY"}
_RUNTIME_LOCK = threading.RLock()


def runtime_config_path() -> Path:
    data_dir = Path(os.environ.get("TRAVEL_GENUI_DATA_DIR", ROOT / "backend" / ".runtime"))
    return data_dir / "integrations.json"


def runtime_values() -> dict[str, str]:
    path = runtime_config_path()
    with _RUNTIME_LOCK:
        if not path.is_file():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {}
    if not isinstance(raw, dict):
        return {}
    return {key: value for key, value in raw.items() if key in _RUNTIME_KEYS and isinstance(value, str)}


def update_runtime_values(updates: dict[str, str]) -> None:
    if not updates or any(key not in _RUNTIME_KEYS or not isinstance(value, str) for key, value in updates.items()):
        raise ValueError("运行时配置包含不支持的字段。")
    path = runtime_config_path()
    with _RUNTIME_LOCK:
        current = runtime_values()
        current.update(updates)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        temporary.replace(path)


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
    result.update(runtime_values())
    return result


def _number(values: dict[str, str], key: str, default: float, minimum: float, maximum: float) -> float:
    try:
        return max(minimum, min(maximum, float(values.get(key, str(default)))))
    except ValueError:
        return default


def normalize_llm_base_url(value: str) -> str:
    base = value.strip().rstrip("/")
    if base.endswith("/chat/completions"):
        base = base[:-len("/chat/completions")].rstrip("/")
    parsed = urlsplit(base)
    if (not base or len(base) > 2048 or parsed.scheme not in {"https", "http"} or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or any(ord(character) < 32 for character in base)):
        raise ValueError("模型请求地址必须是有效的 HTTP(S) 基础地址。")
    return base


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
    base = normalize_llm_base_url(values.get("LLM_BASE_URL", "https://api.deepseek.com"))
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
