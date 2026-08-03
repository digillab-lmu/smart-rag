FROM python:3.12-slim

WORKDIR /app

# ─── Python deps (cached layer) ──────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ─── App code ────────────────────────────────────────────────────────────────
COPY app.py .

# Run as non-root for least privilege — this service is stateless (no
# volumes, no writable paths needed).
RUN useradd --create-home --shell /bin/bash smartrag && \
    chown -R smartrag:smartrag /app
USER smartrag

EXPOSE 8000

# Same command the original (verified) deployment ran — plain uvicorn, no
# gunicorn wrapper: this is a low-traffic internal-only service called by
# one n8n workflow, not a public-facing endpoint.
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
