# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:0.5-python3.13-bookworm-slim AS builder

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable


FROM python:3.13-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    DB_PATH=/data/swipes.db \
    NAMES_DIR=/app/data/names \
    UPLOAD_DIR=/data/uploads

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY src/ /app/src/
COPY data/names/ /app/data/names/

RUN mkdir -p /data/uploads

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/healthz').status==200 else 1)"

CMD ["uvicorn", "baby_names_swiper.app:app", "--host", "0.0.0.0", "--port", "8000"]
