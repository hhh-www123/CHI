"""Anonymous visitor ownership for a small, single-worker public alpha."""
from __future__ import annotations

from collections import OrderedDict, deque
import hashlib
import json
import re
import secrets
import threading
import time
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from .config import get_settings

COOKIE = "travel_visitor_v2"
SESSION_PATH = re.compile(r"^/api/sessions/([a-f0-9]{32})(?:/|$)")
_token_pattern = re.compile(r"^[A-Za-z0-9_-]{43}$")
_buckets: OrderedDict[str, deque[float]] = OrderedDict()
_bucket_lock = threading.Lock()
_generation_slots = threading.BoundedSemaphore(4)


def _allow_rate(key: str, limit: int) -> bool:
    now = time.monotonic()
    with _bucket_lock:
        bucket = _buckets.setdefault(key, deque())
        _buckets.move_to_end(key)
        while bucket and bucket[0] <= now - 60:
            bucket.popleft()
        if len(_buckets) > 10000:
            _buckets.popitem(last=False)
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        return True


def install_access(app, get_store):
    @app.middleware("http")
    async def protect_sessions(request: Request, call_next):
        if not request.url.path.startswith("/api/"):
            return await call_next(request)
        settings = get_settings()
        cookie = request.cookies.get(COOKIE, "")
        is_new = _token_pattern.fullmatch(cookie) is None
        if is_new:
            cookie = secrets.token_urlsafe(32)
        owner = hashlib.sha256(cookie.encode("ascii")).hexdigest()
        request.state.visitor_owner = owner
        mutation = request.method in {"POST", "PUT", "PATCH", "DELETE"}
        if mutation:
            origin = request.headers.get("origin")
            if origin:
                parsed = urlsplit(str(request.url))
                same_origin = f"{parsed.scheme}://{parsed.netloc}"
                if origin.rstrip("/") not in {*settings.allowed_origins, same_origin}:
                    return JSONResponse({"detail": "请求来源与当前站点不一致。"}, status_code=403)
            size = request.headers.get("content-length", "0")
            if not size.isdigit() or int(size) > 65536:
                return JSONResponse({"detail": "请求内容过大。"}, status_code=413)
            chunks, received = [], 0
            async for chunk in request.stream():
                received += len(chunk)
                if received > 65536:
                    return JSONResponse({"detail": "请求内容过大。"}, status_code=413)
                chunks.append(chunk)
            # Starlette's cached request replays this body to the route handler.
            request._body = b"".join(chunks)
        target = SESSION_PATH.match(request.url.path)
        if target:
            try:
                storage = get_store()
                with storage.lock:
                    session = storage.get(target.group(1))
                    if session.get("_owner") is None:
                        # Existing desktop files are adopted only from the local machine.
                        local_request = request.client and request.client.host in {"127.0.0.1", "::1", "testclient"}
                        local_host = request.url.hostname in {"127.0.0.1", "localhost", "::1"} or (request.client and request.client.host == "testclient")
                        if local_request and local_host:
                            storage.claim_legacy(target.group(1), owner)
                        else:
                            raise HTTPException(404, "未找到这个行程，或你没有访问权限。")
                    elif session["_owner"] != owner:
                        raise HTTPException(404, "未找到这个行程，或你没有访问权限。")
            except HTTPException as exc:
                return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        generation = mutation and request.url.path.endswith(("/generate", "/routes", "/repair", "/copy-style", "/edits/candidates"))
        creation = mutation and request.url.path == "/api/sessions"
        if creation:
            try:
                setup = json.loads(getattr(request, "_body", b"{}") or b"{}")
            except (ValueError, TypeError):
                setup = {}
            if isinstance(setup, dict) and "city" in setup:
                generation = True
        client_address = request.client.host if request.client else "unknown"
        if (generation or creation) and not _allow_rate(f"{client_address}:{'generation' if generation else 'creation'}", settings.requests_per_minute if generation else 60):
            return JSONResponse({"detail": "操作较频繁，请稍等一分钟再试。"}, status_code=429, headers={"Retry-After": "60"})
        acquired = False
        try:
            if generation:
                acquired = _generation_slots.acquire(blocking=False)
                if not acquired:
                    return JSONResponse({"detail": "Agent 正忙，请稍后重试。"}, status_code=429, headers={"Retry-After": "10"})
            response = await call_next(request)
            if is_new:
                response.set_cookie(COOKIE, cookie, max_age=60 * 60 * 24 * 180, httponly=True, samesite="lax", secure=settings.cookie_secure, path="/")
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            return response
        finally:
            if acquired:
                _generation_slots.release()
