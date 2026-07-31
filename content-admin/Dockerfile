FROM python:3.13-slim

WORKDIR /app

# ─── Python deps (cached layer) ──────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ─── App code ────────────────────────────────────────────────────────────────
COPY *.py ./
COPY templates ./templates/
COPY static ./static/

# Deliberately root, unlike lti-middleware — this app's whole job is
# reading AND WRITING ../.env (bind-mounted, see docker-compose.yml), which
# is chmod 600 root:root on the host by templates.sh::write_env_file(). A
# non-root user here would get EACCES on every write attempt (FLOWISE_API_KEY,
# CHATFLOW_AGENT0X). Dropping to non-root would only protect a file this
# container is explicitly granted full read-write access to anyway — no real
# isolation benefit, just a broken write path. Same justification every other
# script that touches .env (bootstrap.sh, scripts/admin.sh, ...) already has
# for requiring root.

EXPOSE 5000

# Single worker: this is a low-traffic, single-admin-account internal tool,
# not a public-facing service — no need for gunicorn's multi-worker overhead.
CMD ["gunicorn", "app:app", \
     "--bind", "0.0.0.0:5000", \
     "--workers", "1", \
     "--timeout", "120", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "--forwarded-allow-ips=*"]
