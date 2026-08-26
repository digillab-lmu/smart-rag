#!/usr/bin/env bash
# Which .env the containers actually get.
#
# Compose resolves ${VAR} in the compose file from the ambient environment
# first and only then from --env-file. That order is the whole subject of this
# file, because it turned a correct restore into a broken installation:
#
#   The admin TUI sources .env into its own environment at startup. A restore
#   run from that menu replaced .env on disk, correctly and completely — every
#   value verified afterwards against the archive. The next action in the same
#   menu then started the containers, and Compose preferred the exports the
#   menu had made before the restore. n8n received POSTGRES_PASSWORD from
#   env_file (the archive's) and DATABASE_PASSWORD from ${POSTGRES_PASSWORD}
#   (the replaced installation's) in one container, and died with "Mismatching
#   encryption keys" — a message that points at the archive, which was fine.
#
# So compose.sh clears the keys .env defines before handing over. The checks
# below run it against a stub `docker` that reports the environment it was
# started with, which is the only place the answer is visible.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILURES=()
check() { if (( $2 != 0 )); then FAILURES+=("$1: $3"); fi; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

ROOT="$WORK/install"
mkdir -p "$ROOT/scripts" "$ROOT/docker" "$WORK/bin"
cp "$REPO/scripts/compose.sh" "$ROOT/scripts/"
: > "$ROOT/docker/docker-compose.yml"
cat > "$ROOT/.env" <<'ENVEOF'
POSTGRES_PASSWORD="from-the-file"
N8N_ENCRYPTION_KEY="key-from-the-file"
BASE_DATA_PATH="/srv/smart-rag/data"
ENVEOF

# Reports the environment it inherited, which is exactly what Compose would
# have interpolated ${VAR} from.
cat > "$WORK/bin/docker" <<'STUB'
#!/usr/bin/env bash
echo "ARGS: $*"
echo "POSTGRES_PASSWORD=${POSTGRES_PASSWORD-<unset>}"
echo "N8N_ENCRYPTION_KEY=${N8N_ENCRYPTION_KEY-<unset>}"
echo "PATH_PRESENT=$([[ -n "${PATH:-}" ]] && echo yes || echo no)"
STUB
chmod +x "$WORK/bin/docker"

# The failing situation, reproduced: a shell that read .env before it changed.
out="$(PATH="$WORK/bin:$PATH" \
      POSTGRES_PASSWORD="from-the-replaced-installation" \
      N8N_ENCRYPTION_KEY="key-from-the-replaced-installation" \
      bash "$ROOT/scripts/compose.sh" up -d 2>&1)"

grep -q "POSTGRES_PASSWORD=<unset>" <<<"$out"
check "a stale export does not reach docker compose" $? \
      "Compose prefers the environment over --env-file, so it would win"
grep -q "N8N_ENCRYPTION_KEY=<unset>" <<<"$out"
check "and neither does a stale encryption key" $? \
      "this is the value n8n refuses to start on"

# Clearing the environment must not clear the process out from under itself.
grep -q "PATH_PRESENT=yes" <<<"$out"
check "PATH survives the clearing" $? \
      "a .env that happens to define PATH would otherwise break the exec"

grep -q -- "--env-file $ROOT/.env" <<<"$out"
check "the file is still named on the command line" $? \
      "clearing the environment only helps if the file is read instead"
grep -q -- "-f $ROOT/docker/docker-compose.yml" <<<"$out"
check "and so is the compose file" $? \
      "Compose does not look for it relative to the repo root"

# Without .env there is nothing to be authoritative about, and the wrapper has
# always refused rather than starting containers with every value blank.
mv "$ROOT/.env" "$ROOT/.env.away"
PATH="$WORK/bin:$PATH" bash "$ROOT/scripts/compose.sh" ps >/dev/null 2>&1
check "a missing .env is still refused" $(( $? == 0 ? 1 : 0 )) \
      "blank values fail later, in ways that do not name the cause"
mv "$ROOT/.env.away" "$ROOT/.env"

# The admin menu re-reads .env after a restore, for the same reason.
grep -A 12 'restore\.sh" "\$archive" --replace --lang' "$REPO/scripts/admin.sh" \
    | grep -q 'source "\$REPO_ROOT/\.env"'
check "the admin menu re-reads .env after a restore" $? \
      "otherwise the rest of the session runs on the replaced installation's secrets"

if (( ${#FAILURES[@]} > 0 )); then
    echo "FAILURES:"; printf '  - %s\n' "${FAILURES[@]}"; exit 1
fi
echo "All compose-environment checks passed: the wrapper clears the keys .env"
echo "defines before handing over, so --env-file decides what the containers"
echo "get and a value exported before the file changed cannot outrank it;"
echo "PATH survives that clearing; both paths are still passed explicitly; a"
echo "missing .env is refused rather than run with blanks; and the admin menu"
echo "re-reads .env after a restore replaced it."
