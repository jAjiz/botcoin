FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Build virtualenv with dependencies in an isolated location.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt /tmp/requirements.txt

# requirements.txt in this repository is UTF-16; convert to UTF-8 for pip.
RUN python -c "from pathlib import Path; p=Path('/tmp/requirements.txt'); p.write_text(p.read_text(encoding='utf-16'), encoding='utf-8')" \
    && pip install --upgrade pip \
    && pip install --no-cache-dir -r /tmp/requirements.txt

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

RUN groupadd --system appgroup \
    && useradd --system --gid appgroup --create-home appuser

COPY --from=builder /opt/venv /opt/venv
COPY . /app

RUN mkdir -p /app/data /app/logs \
    && chown -R appuser:appgroup /app

USER appuser

CMD ["python", "main.py"]
