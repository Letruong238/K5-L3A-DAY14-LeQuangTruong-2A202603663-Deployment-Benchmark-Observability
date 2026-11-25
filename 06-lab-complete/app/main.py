"""
Production AI Agent — Part 6 Final Project

Kiểm tra:
    python check_production_ready.py

Luồng xử lý POST /ask:
    X-API-Key (401) → rate limit (429) → budget (402) → gọi LLM → ghi chi phí → 200
"""
import json
import logging
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import redis
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.auth import verify_api_key
from app.config import settings
from app.cost_guard import check_budget, estimate_tokens, record_cost
from app.rate_limiter import check_rate_limit
from app.redis_client import r, redis_healthy
from utils.mock_llm import ask as llm_ask


# ─────────────────────────────────────────────────────────
# Structured logging (tiêu chí 10): mỗi dòng log là 1 JSON object
# ─────────────────────────────────────────────────────────
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "lvl": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Các field thêm qua logger.info(..., extra={"fields": {...}})
        entry.update(getattr(record, "fields", {}))
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)   # traceback nằm gọn trong 1 dòng JSON
        return json.dumps(entry, ensure_ascii=False)


def _setup_logging() -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level)
    return logging.getLogger("agent")


logger = _setup_logging()


def log_event(event: str, level: int = logging.INFO, **fields):
    logger.log(level, event, extra={"fields": {"event": event, **fields}})


# ─────────────────────────────────────────────────────────
# Lifespan: khởi động + graceful shutdown (tiêu chí 11)
# uvicorn tự bắt SIGTERM: ngừng nhận kết nối mới, chờ request đang chạy xong,
# rồi chạy phần sau `yield` bên dưới và thoát với exit code 0.
# Vì vậy KHÔNG tự đăng ký signal handler (sẽ đè mất handler của uvicorn).
# ─────────────────────────────────────────────────────────
_state = {"ready": False, "started_at": time.time()}


@asynccontextmanager
async def lifespan(app: FastAPI):
    log_event("startup", environment=settings.environment, port=settings.port,
              rate_limit_per_minute=settings.rate_limit_per_minute,
              daily_budget_usd=settings.daily_budget_usd,
              api_key_configured=bool(settings.agent_api_key))
    if not settings.agent_api_key:
        log_event("missing_api_key", logging.WARNING,
                  detail="AGENT_API_KEY chưa đặt — mọi request /ask sẽ bị 401")
    _state["ready"] = True
    yield
    # ── Shutdown: chỉ chạy sau khi uvicorn đã xử lý xong request đang chạy ──
    _state["ready"] = False
    log_event("shutdown", detail="graceful shutdown — đóng kết nối Redis")
    try:
        r.close()
    except Exception:
        pass
    log_event("shutdown_complete")


app = FastAPI(title="Production AI Agent", version="1.0.0", lifespan=lifespan)


# ─────────────────────────────────────────────────────────
# Middleware: log mỗi request (không log API key / nội dung câu hỏi)
# ─────────────────────────────────────────────────────────
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        log_event("request", method=request.method, path=request.url.path, status=status,
                  ms=round((time.perf_counter() - start) * 1000, 1))


# ─────────────────────────────────────────────────────────
# Xử lý lỗi: trả mã lỗi rõ ràng, KHÔNG lộ stack trace ra client
# ─────────────────────────────────────────────────────────
@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={
        "error": "Invalid request body",
        "detail": [{"field": ".".join(str(p) for p in e["loc"][1:]), "msg": e["msg"]}
                   for e in exc.errors()],
    })


@app.exception_handler(redis.exceptions.RedisError)
async def redis_error_handler(request: Request, exc: redis.exceptions.RedisError):
    log_event("redis_error", logging.ERROR, path=request.url.path, error=type(exc).__name__)
    return JSONResponse(status_code=503, content={"error": "Storage temporarily unavailable"})


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception):
    logger.error("unhandled_error", exc_info=exc,
                 extra={"fields": {"event": "unhandled_error", "path": request.url.path}})
    return JSONResponse(status_code=500, content={"error": "Internal server error"})


# ─────────────────────────────────────────────────────────
# Health & readiness (tiêu chí 4, 5)
# ─────────────────────────────────────────────────────────
@app.get("/health")
def health():
    """Liveness: process còn sống là 200 — KHÔNG phụ thuộc Redis."""
    return {
        "status": "ok",
        "uptime_seconds": round(time.time() - _state["started_at"], 1),
        "environment": settings.environment,
        "version": app.version,
    }


@app.get("/ready")
def ready():
    """Readiness: chỉ 200 khi app đã khởi động xong VÀ kết nối được Redis, ngược lại 503."""
    if not _state["ready"]:
        return JSONResponse(status_code=503, content={"ready": False, "reason": "starting or shutting down"})
    if not redis_healthy():
        return JSONResponse(status_code=503, content={"ready": False, "reason": "redis unavailable"})
    return {"ready": True}


# ─────────────────────────────────────────────────────────
# Agent endpoint
# ─────────────────────────────────────────────────────────
class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    user_id: str | None = Field(default=None, max_length=128)


@app.post("/ask")
def ask(body: AskRequest, api_key: str = Depends(verify_api_key)):   # tiêu chí 6 → 401
    check_rate_limit(api_key)                                         # tiêu chí 7 → 429
    check_budget()                                                    # tiêu chí 8 → 402

    answer = llm_ask(body.question)

    input_tokens = estimate_tokens(body.question)
    output_tokens = estimate_tokens(answer)
    cost = record_cost(input_tokens, output_tokens)
    log_event("llm_call", input_tokens=input_tokens, output_tokens=output_tokens,
              cost_usd=round(cost, 8))

    return {
        "question": body.question,
        "answer": answer,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens,
                  "cost_usd": round(cost, 8)},
    }


@app.get("/")
def root():
    return {"app": "Production AI Agent", "docs": "/docs", "health": "/health", "ready": "/ready"}
