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
#
# --no-control-socket: gunicorn 25.1.0 added a new interactive control
# interface (a Unix socket at ./gunicorn.ctl) that's ON by default. Live-hit
# a case where every single HTTP connection attempt hung until Docker's
# healthcheck timeout killed it — zero requests ever reached the app (no
# access-log line, ever), despite gunicorn's own log confirming it was
# listening. Symptom + gunicorn version + "Control socket listening at
# .../gunicorn.ctl" log line all match reports of this brand-new feature
# misbehaving in containerized environments. We don't use the control
# interface at all, so disabling it outright is the targeted fix — no need
# to downgrade off 25.1.0 (whose 23.x predecessor hasn't been patched since
# Aug 2024) or jump to 26.0.0 (drops the eventlet worker, adds unvetted
# HTTP/1.1 hardening — a bigger, unrelated version jump for this one fix).
CMD ["gunicorn", "app:app", \
     "--bind", "0.0.0.0:5000", \
     "--workers", "1", \
     "--timeout", "120", \
     "--no-control-socket", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "--forwarded-allow-ips=*"]
