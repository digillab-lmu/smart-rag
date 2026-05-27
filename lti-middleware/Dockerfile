FROM python:3.13-slim

WORKDIR /app

# ─── Python deps (cached layer) ──────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ─── App code + config templates ────────────────────────────────────────────
COPY main.py ./
COPY config  ./config/

# Run as non-root for least privilege
RUN useradd --create-home --shell /bin/bash smartrag && \
    chown -R smartrag:smartrag /app
USER smartrag

EXPOSE 10088

# Gunicorn — 2 workers is enough for ~300 concurrent learners; bump if needed.
# --forwarded-allow-ips=* trusts X-Forwarded-* from any IP (we sit behind nginx).
CMD ["gunicorn", "main:app", \
     "--bind", "0.0.0.0:10088", \
     "--workers", "2", \
     "--timeout", "60", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "--forwarded-allow-ips=*"]
