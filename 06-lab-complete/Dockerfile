# ============================================================
# Production Dockerfile — multi-stage (tiêu chí 1)
#   Stage 1 (builder): cài dependencies vào /install
#   Stage 2 (runtime): base slim, chỉ copy thư viện đã cài + code, chạy bằng user non-root
# Build:  docker build -t my-agent:prod .
# ============================================================

# ── Stage 1: builder ─────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# Copy requirements TRƯỚC code để tận dụng cache: sửa code không phải cài lại thư viện
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Stage 2: runtime ─────────────────────────────────────────
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

# User không phải root: lỡ bị tấn công thì kẻ xấu không có quyền root trong container
RUN groupadd --system app && useradd --system --gid app --home-dir /app app

WORKDIR /app

# Chỉ lấy thư viện đã cài từ builder (không mang theo pip cache, file build...)
COPY --from=builder /install /usr/local
COPY --chown=app:app app/ ./app/
COPY --chown=app:app utils/ ./utils/

USER app

EXPOSE 8000

# Docker tự gọi /health mỗi 30s; lỗi 3 lần liên tiếp → container "unhealthy"
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8000') + '/health', timeout=3)" || exit 1

# `exec` thay shell bằng uvicorn → uvicorn là PID 1, nhận SIGTERM trực tiếp khi `docker stop`
# ${PORT:-8000}: lắng nghe theo cổng nền tảng cloud cấp, mặc định 8000
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 2 --timeout-graceful-shutdown 8"]
