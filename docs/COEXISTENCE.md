# SMART RAG · Coexistence Guarantees

This document is the *contract* between the bootstrap scripts and any other
service already running on the host. Read this before deploying on a server
that hosts other workloads.

## Guiding principle

> **Additive, namespaced, opt-in.** We install *alongside*, never *instead of*.
> We never modify or delete anything we didn't create. If a conflict is
> detected, we abort with a clear explanation — we never silently overwrite.

---

## What we create (and only these)

Everything we write is namespaced with the `smartrag-` prefix or lives under
explicitly-configured paths. The complete list:

### File system
| Path | Created by | Owned by |
|------|------------|----------|
| `/etc/nginx/sites-available/smartrag-suite.conf` | `bootstrap.sh` phase 4 | this repo |
| `/etc/nginx/sites-enabled/smartrag-suite.conf` (symlink) | `get-ssl-certs.sh` | this repo |
| `/etc/nginx/sites-available/smartrag-acme.conf` (temp) | `get-ssl-certs.sh` | this repo, deleted after SSL setup |
| `/etc/letsencrypt/live/smartrag-<DOMAIN>/` | certbot, via this repo | this repo's cert |
| `/etc/letsencrypt/renewal/smartrag-<DOMAIN>.conf` | certbot, via this repo | this repo's renewal config |
| `<BASE_DATA_PATH>/postgres/`, `/redis/`, `/neo4j/`, ... | `start-services.sh` | Docker volumes |
| `<BASE_DATA_PATH>/staging/` | `bootstrap.sh` phase 4 | staged config files |
| `<repo>/.env`, `<repo>/credentials.txt`, `<repo>/bootstrap.log` | bootstrap | this repo |
| `<repo>/lti-middleware/config/{lti,agents,branding}.json` | bootstrap phase 4 (LTI only) | this repo |

### apt packages (only installed if missing)
- nginx
- certbot, python3-certbot-nginx
- jq, dnsutils, openssl, curl, ca-certificates

### Docker
- Containers named `smartrag-*` (10–14 of them, depending on profiles)
- Networks `smart-rag-network` (created by compose) and `proxy-network` (must exist or compose creates)
- Volumes named after the containers
- Images pulled from public registries

### Host ports (configurable via `.env`)
- Bound to `127.0.0.1` only — **not** exposed to the public network
- Defaults: 3000, 5678, 8080, 50051, 7474, 7687, 3001, 9000, 9001, 10088
- Override per-port if any are taken (e.g., `FLOWISE_PORT=3001`)

---

## What we never touch

A hard, audited list. If you see the bootstrap scripts touching any of these,
**that is a bug** — please open an issue.

| Path / resource | Why we keep our hands off |
|-----------------|---------------------------|
| `/etc/nginx/sites-enabled/default` | Distro default, often the user's primary site |
| `/etc/nginx/sites-enabled/*` (except `smartrag-suite.conf`) | Existing virtual hosts |
| `/etc/nginx/sites-available/*` (except `smartrag-*.conf`) | Site definitions |
| `/etc/nginx/conf.d/*` | Drop-in directives |
| `/etc/nginx/nginx.conf` | Top-level nginx config |
| `/etc/letsencrypt/live/<other-name>/` | Any other certificate |
| `/etc/letsencrypt/renewal/<other-name>.conf` | Other certbot renewal configs |
| `/etc/letsencrypt/options-ssl-nginx.conf` | Shared SSL options (we only read it) |
| `iptables` / `ufw` / `firewalld` rules | Firewall is your responsibility |
| `/etc/systemd/system/*.service` (except docker.service which we may `enable`) | Service definitions |
| `certbot.timer` / `certbot.service` | Auto-renewal — our cert renews under your existing timer |
| Any non-`smartrag-*` Docker container, network, image, or volume | Other Docker workloads |
| Cron entries | Scheduled jobs |
| User accounts, `/etc/passwd`, sudoers | No user creation |
| `/var/lib/postgresql/`, `/var/lib/redis/`, etc. (system packages) | We never touch system services — only Docker containers |

---

## Pre-flight collision checks

`bootstrap.sh` runs these **before** writing anything, and aborts on conflict:

1. **Host port availability** — for each of the ~10 ports we want to bind,
   parse `ss -tln` and abort if any is in use. Suggests overriding the port
   in `.env`.

2. **nginx server_name collisions** — scan all `/etc/nginx/sites-enabled/*`
   (except our own `smartrag-*` files) for `server_name` directives that
   overlap with our planned subdomains (e.g. `smart-rag.<DOMAIN>`). If any of
   our subdomains is already claimed — e.g. a standalone `n8n` already
   running on this host — the wizard offers a shared prefix for all our
   subdomains instead (`smartrag-n8n.<DOMAIN>` etc.) and retries, up to a
   few attempts, before giving up and asking you to resolve it manually.

3. **nginx config validity** — runs `nginx -t` against the existing
   configuration. If it's already broken, we refuse to reload it later (which
   would also fail and possibly leave nginx in a worse state).

4. **Existing certificate name** — if `/etc/letsencrypt/live/smartrag-<DOMAIN>/`
   already exists, we use the existing cert (renew only). We never overwrite
   an existing cert with the same name.

5. **`BASE_DATA_PATH` contents** — if the target data directory exists and
   contains files we didn't create, ask the user before proceeding. We never
   modify those files; the warning is just so you know we're sharing a
   directory.

---

## What happens on `uninstall.sh`

The uninstall script reverses **exactly** what bootstrap installed, in
reverse order, with confirmations for any destructive operation:

```
1. docker compose down --remove-orphans               (confirm)
2. docker volume rm $(docker volume ls -q | grep smartrag)   (confirm)
3. Remove /etc/nginx/sites-enabled/smartrag-suite.conf       (auto)
4. Remove /etc/nginx/sites-available/smartrag-suite.conf     (auto)
5. systemctl reload nginx                                    (auto)
6. certbot delete --cert-name smartrag-<DOMAIN>              (confirm)
7. rm -rf <BASE_DATA_PATH>                                   (confirm, only if it matches the pattern we created)
```

`apt`-installed packages (nginx, certbot, ...) are NEVER removed —
they may be in use by other services on the host.

---

## How to reverse changes manually (worst-case recovery)

If a bootstrap run goes sideways, here are the manual steps to undo everything:

```bash
# 1. Stop and remove all SMART RAG containers
docker compose -f docker/docker-compose.yml --env-file .env down --remove-orphans
docker volume ls -q | grep -E 'smartrag|smart-rag' | xargs -r docker volume rm

# 2. Remove our nginx files
sudo rm -f /etc/nginx/sites-enabled/smartrag-suite.conf
sudo rm -f /etc/nginx/sites-enabled/smartrag-acme.conf      # if leftover
sudo rm -f /etc/nginx/sites-available/smartrag-suite.conf
sudo rm -f /etc/nginx/sites-available/smartrag-acme.conf
sudo nginx -t && sudo systemctl reload nginx

# 3. Remove our certbot certificate
sudo certbot delete --cert-name smartrag-<your-domain>

# 4. Delete the data directory (NUKE — make sure nothing else is there)
sudo rm -rf <BASE_DATA_PATH>

# 5. Optionally: remove apt packages we may have installed
#    (only if you're sure nothing else uses them)
#    sudo apt-get remove --purge nginx certbot python3-certbot-nginx
```

After these steps, the host should be in the exact state it was before
running bootstrap.

---

## Tested coexistence scenarios

| Scenario | Behaviour |
|----------|-----------|
| Existing nginx with several sites on different domains | ✓ Our subdomains are added, others untouched |
| Existing certbot cert for `<DOMAIN>` | ✓ Our cert is named `smartrag-<DOMAIN>` and stored separately |
| Port 3000 already used by another service | ✗ bootstrap aborts with: "Override `FLOWISE_PORT` in .env and re-run" |
| Existing `<BASE_DATA_PATH>` with foreign files | ⚠ Warning + interactive confirmation; we never touch the foreign files |
| nginx has broken existing config (`nginx -t` fails) | ✗ bootstrap aborts: "Fix existing nginx config first" |
| `ufw` blocks ports 80/443 | ⚠ Warning; you must open them manually |

---

If you find a case where bootstrap touches something not listed under "What we
create" above, that's a coexistence bug. Please open an issue with the file
path and a short description.
