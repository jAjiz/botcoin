FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

ARG INSTALL_DEV=false

WORKDIR /app

# Build virtualenv with dependencies in an isolated location.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt /tmp/requirements.txt
COPY requirements-dev.txt /tmp/requirements-dev.txt

RUN pip install --upgrade pip \
    && if [ "$INSTALL_DEV" = "true" ]; then \
        pip install --no-cache-dir -r /tmp/requirements-dev.txt; \
    else \
        pip install --no-cache-dir -r /tmp/requirements.txt; \
    fi

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

RUN groupadd --system appgroup \
    && useradd --system --gid appgroup --create-home appuser

COPY --from=builder /opt/venv /opt/venv
COPY . /app

RUN mkdir -p /app/logs \
    && chown -R appuser:appgroup /app \
    && chmod +x /app/scripts/entrypoint.sh

USER appuser

ENTRYPOINT ["/app/scripts/entrypoint.sh"]
