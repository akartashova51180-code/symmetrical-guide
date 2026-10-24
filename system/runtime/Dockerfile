FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

WORKDIR /app
RUN pip install --no-cache-dir uv==0.12.23
COPY system/runtime/pyproject.toml system/runtime/uv.lock /app/system/runtime/
WORKDIR /app/system/runtime
RUN uv sync --locked --no-dev --no-install-project
COPY system /app/system
RUN groupadd --system --gid 10001 brain \
    && useradd --system --uid 10001 --gid brain --home-dir /nonexistent --shell /usr/sbin/nologin brain
ENV PATH="/app/system/runtime/.venv/bin:${PATH}" \
    CORPORATE_BRAIN_SOURCE_DIR=/app/system \
    CORPORATE_BRAIN_DATA_DIR=/data
WORKDIR /app/system/runtime
USER 10001:10001
CMD ["uvicorn", "corporate_brain.app:app", "--host", "0.0.0.0", "--port", "8787", "--workers", "1"]
