# Coexistence with other services on the host

This document states what the bootstrap scripts write on the host and what
they leave alone. It applies to a server that already runs other workloads.

## Principle

The installation is additive and namespaced. Nothing that was not created by
these scripts is modified or deleted. A detected conflict stops the run with
an explanation rather than being resolved by overwriting.

---

## What is created

Everything written carries the `smartrag-` prefix or lives under an explicitly
configured path. The complete list:

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
- Containers named `smartrag-*`: 11 with the `core` profile, 14 with
  `observability` as well, 15 with `lti`
- Networks `smart-rag-network` (created by compose) and `proxy-network` (must exist or compose creates)
- Volumes named after the containers
- Images pulled from public registries

### Host ports (configurable via `.env`)
- Bound to `127.0.0.1` only — **not** exposed to the public network
- Defaults: 3000 (Flowise), 3001 (Langfuse), 3002 (Content Admin), 3900
  (Garage S3), 5678 (n8n), 7474 and 7687 (Neo4j), 8080 and 50051 (Weaviate),
  10088 (LTI)
- Each can be overridden in `.env` if it is taken, for example
  `FLOWISE_PORT=3010`

---

## What is never touched

An audited list. A bootstrap script that writes to any of these is a defect
and should be reported as one.

| Path / resource | Reason |
|-----------------|---------------------------|
| `/etc/nginx/sites-enabled/default` | Distro default, often the user's primary site |
| `/etc/nginx/sites-enabled/*` (except `smartrag-suite.conf`) | Existing virtual hosts |
| `/etc/nginx/sites-available/*` (except `smartrag-*.conf`) | Site definitions |
| `/etc/nginx/conf.d/*` | Drop-in directives |
| `/etc/nginx/nginx.conf` | Top-level nginx config |
| `/etc/letsencrypt/live/<other-name>/` | Any other certificate |
| `/etc/letsencrypt/renewal/<other-name>.conf` | Other certbot renewal configs |
| `/etc/letsencrypt/options-ssl-nginx.conf` | Shared SSL options, read only |
| `iptables` / `ufw` / `firewalld` rules | The firewall stays with the host administrator |
| `/etc/systemd/system/*.service` (except `docker.service`, which may be enabled) | Service definitions |
| `certbot.timer` / `certbot.service` | This certificate renews under the timer already present |
| Any non-`smartrag-*` Docker container, network, image, or volume | Other Docker workloads |
| Cron entries | Scheduled jobs |
| User accounts, `/etc/passwd`, sudoers | No user creation |
| `/var/lib/postgresql/`, `/var/lib/redis/`, etc. (system packages) | System services are out of scope; only Docker containers are used |

---

## Pre-flight collision checks

`bootstrap.sh` runs these before writing anything and stops on a conflict:

1. **Host port availability.** For each of the ten ports to be bound, `ss -tln`
   is parsed and the run stops if one is in use, naming the `.env` key that
   moves it.

2. **nginx `server_name` collisions.** All of `/etc/nginx/sites-enabled/*`
   except this project's own `smartrag-*` files are scanned for `server_name`
   directives overlapping the planned subdomains, such as
   `smart-rag.<DOMAIN>`. If one is already claimed, for instance by a
   standalone n8n on the same host, the wizard offers a shared prefix for all
   subdomains (`smartrag-n8n.<DOMAIN>` and so on) and retries a few times
   before stopping and leaving the conflict to be resolved by hand.

3. **nginx configuration validity.** `nginx -t` runs against the existing
   configuration. If it is already broken, the run stops rather than
   attempting a reload later, which would fail and could leave nginx in a
   worse state.

4. **Existing certificate name.** If `/etc/letsencrypt/live/smartrag-<DOMAIN>/`
   exists, that certificate is used and renewed. A certificate of the same
   name is never overwritten.

5. **`BASE_DATA_PATH` contents.** If the target directory exists and holds
   files this installation did not create, the run asks before proceeding.
   Those files are not modified; the question exists so that the directory is
   known to be shared.

---

## What happens on `uninstall.sh`

The uninstall script reverses what bootstrap installed, in reverse order,
with a confirmation for every destructive operation:

```
1. docker compose down --remove-orphans               (confirm)
2. docker volume rm $(docker volume ls -q | grep smartrag)   (confirm)
3. Remove /etc/nginx/sites-enabled/smartrag-suite.conf       (auto)
4. Remove /etc/nginx/sites-available/smartrag-suite.conf     (auto)
5. systemctl reload nginx                                    (auto)
6. certbot delete --cert-name smartrag-<DOMAIN>              (confirm)
7. rm -rf <BASE_DATA_PATH>                                   (confirm, only if it matches the pattern bootstrap created)
```

`apt`-installed packages (nginx, certbot and the rest) are never removed,
since other services on the host may be using them.

---

## Reversing the changes by hand

If a bootstrap run has to be undone manually:

```bash
# 1. Stop and remove all SMART RAG containers
docker compose -f docker/docker-compose.yml --env-file .env down --remove-orphans
docker volume ls -q | grep -E 'smartrag|smart-rag' | xargs -r docker volume rm

# 2. Remove this installation's nginx files
sudo rm -f /etc/nginx/sites-enabled/smartrag-suite.conf
sudo rm -f /etc/nginx/sites-enabled/smartrag-acme.conf      # if leftover
sudo rm -f /etc/nginx/sites-available/smartrag-suite.conf
sudo rm -f /etc/nginx/sites-available/smartrag-acme.conf
sudo nginx -t && sudo systemctl reload nginx

# 3. Remove this installation's certbot certificate
sudo certbot delete --cert-name smartrag-<domain>

# 4. Delete the data directory (check first that nothing else lives there)
sudo rm -rf <BASE_DATA_PATH>

# 5. Optionally, remove apt packages the bootstrap installed
#    (only where nothing else on the host uses them)
#    sudo apt-get remove --purge nginx certbot python3-certbot-nginx
```

After these steps the host is in the state it was in before the bootstrap
ran.

---

## Tested coexistence scenarios

| Scenario | Behaviour |
|----------|-----------|
| Existing nginx with several sites on different domains | ✓ The new subdomains are added, the others untouched |
| Existing certbot cert for `<DOMAIN>` | ✓ The new certificate is named `smartrag-<DOMAIN>` and stored separately |
| Port 3000 already used by another service | ✗ bootstrap aborts with: "Override `FLOWISE_PORT` in .env and re-run" |
| Existing `<BASE_DATA_PATH>` with foreign files | ⚠ Warning and a confirmation; the foreign files are not touched |
| nginx has broken existing config (`nginx -t` fails) | ✗ bootstrap aborts: "Fix existing nginx config first" |
| `ufw` blocks ports 80/443 | ⚠ Warning; the ports have to be opened by hand |

---

A bootstrap run that touches anything not listed under "What is created" is a
coexistence defect. Reports should name the file path.
