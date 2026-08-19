#!/usr/bin/env bash
# Backing up an installation, and refusing to restore one that would not work.
#
# The copying is the easy half. What this file holds is the four refusals,
# because each of them prevents a failure that does not look like a failure:
#
#   * **Postgres major version.** A data directory is not portable across
#     them. Restored anyway, Postgres refuses to start, and the operator is
#     looking at a restart loop rather than at a sentence saying why.
#
#   * **The two halves belong together.** Postgres, Neo4j and ClickHouse read
#     their password once, at initdb; N8N_ENCRYPTION_KEY decrypts every
#     credential n8n holds. A data directory beside a different .env produces
#     databases nobody can open — and it surfaces days later as "wrong
#     password", which reads as a configuration mistake, not as a bad restore.
#
#   * **An occupied target.** A restore is not a merge. Overwriting a running
#     installation because somebody typed the wrong archive name has to take
#     more than one command.
#
#   * **A torn archive.** --running produces a copy of the databases taken
#     mid-write. It may restore and it may not, and the only honest thing is
#     to record that in the archive and say it at restore time.
#
# Everything runs against a small fake installation in a temp directory, with
# compose.sh stubbed — the scripts must not need Docker to be testable, or
# they only get tested on the day they are needed.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAILURES=()
check() { if (( $2 != 0 )); then FAILURES+=("$1: $3"); fi; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# ─── A fake installation ─────────────────────────────────────────────────────
# A repo copy with the two scripts, the libraries they source, a stub
# compose.sh, and a data directory with a PG_VERSION in the place the real one
# has it.
make_install() {   # $1 = root, $2 = pg major, $3 = domain
    local root="$1" pg="$2" domain="$3"
    mkdir -p "$root/scripts/lib" "$root/docker" "$root/data/postgres/data" \
             "$root/data/weaviate/data" "$root/data/garage/meta"
    cp "$REPO/scripts/backup.sh" "$REPO/scripts/restore.sh" "$root/scripts/"
    cp "$REPO/scripts/lib/common.sh" "$REPO/scripts/lib/messages.sh" "$root/scripts/lib/"
    # The stub records what it was asked, so "were the services stopped" is
    # answerable without Docker.
    cat > "$root/scripts/compose.sh" <<'STUB'
#!/usr/bin/env bash
echo "compose $*" >> "${COMPOSE_LOG:-/dev/null}"
exit 0
STUB
    chmod +x "$root/scripts/compose.sh"
    printf 'image: postgres:%s.10-alpine\n' "$pg" > "$root/docker/docker-compose.yml"
    printf '%s' "$pg" > "$root/data/postgres/data/PG_VERSION"
    echo "chunk data" > "$root/data/weaviate/data/store.db"
    echo "node key" > "$root/data/garage/meta/node_key"
    cat > "$root/.env" <<ENVEOF
BASE_DATA_PATH="$root/data"
DOMAIN="$domain"
TAILSCALE_HOSTNAME=""
COMPOSE_PROFILES="core"
POSTGRES_PASSWORD="pg-secret-$domain"
N8N_ENCRYPTION_KEY="n8n-secret-$domain"
NEO4J_PASSWORD="neo-secret"
CLICKHOUSE_PASSWORD="ch-secret"
ENCRYPTION_KEY="enc-secret"
ENVEOF
}

A="$WORK/a"
make_install "$A" 17 "example.edu"

# ─── Backing up ──────────────────────────────────────────────────────────────

export COMPOSE_LOG="$WORK/compose.log"
out="$(cd "$A" && bash scripts/backup.sh --lang en --to "$WORK/backups" 2>&1)"
rc=$?
check "a backup runs to completion" $rc "$out"

ARCHIVE="$(ls -1 "$WORK"/backups/smartrag-*.tar.gz 2>/dev/null | head -1)"
[[ -n "$ARCHIVE" && -f "$ARCHIVE" ]]
check "an archive is produced" $? "$(ls -la "$WORK/backups" 2>&1)"

[[ -f "$ARCHIVE.sha256" ]]
check "a checksum is written beside it" $?
[[ "$(cat "$ARCHIVE.sha256")" == "$(sha256sum "$ARCHIVE" | awk '{print $1}')" ]]
check "and the checksum matches" $?

grep -q 'compose stop' "$COMPOSE_LOG"
check "the services are stopped before the copy" $? "$(cat "$COMPOSE_LOG")"
grep -q 'compose start' "$COMPOSE_LOG"
check "and started again afterwards" $? "$(cat "$COMPOSE_LOG")"

# The manifest is what every refusal below reads.
tar -xzf "$ARCHIVE" -C "$WORK" manifest.toml env
check "the archive carries a manifest and an .env" $?
grep -q 'consistent = true' "$WORK/manifest.toml"
check "a stopped backup records itself as consistent" $? "$(cat "$WORK/manifest.toml")"
grep -q 'postgres_major = "17"' "$WORK/manifest.toml"
check "the Postgres major comes from the data directory" $? "$(cat "$WORK/manifest.toml")"

# Fingerprints, never the secrets: the manifest sits unencrypted next to the
# archive, and the whole point of hashing is that reading it teaches nobody
# the password.
grep -q 'fp_postgres_password' "$WORK/manifest.toml"
check "the manifest fingerprints the identity secrets" $?
grep -q 'pg-secret-example.edu' "$WORK/manifest.toml"
check "and contains no secret in the clear" $(( $? == 0 ? 1 : 0 )) "$(cat "$WORK/manifest.toml")"

# The .env travels inside, because a data directory without it is unreadable.
grep -q 'POSTGRES_PASSWORD="pg-secret-example.edu"' "$WORK/env"
check "the .env travels with the data" $?

# ─── --running says so ───────────────────────────────────────────────────────
rm -f "$COMPOSE_LOG"
out="$(cd "$A" && bash scripts/backup.sh --lang en --to "$WORK/torn" --running 2>&1)"
TORN="$(ls -1 "$WORK"/torn/smartrag-*.tar.gz | head -1)"
grep -q 'compose stop' "${COMPOSE_LOG}" 2>/dev/null
check "--running does not stop the services" $(( $? == 0 ? 1 : 0 )) "$(cat "$COMPOSE_LOG" 2>/dev/null)"
rm -rf "$WORK/tornman"; mkdir -p "$WORK/tornman"
tar -xzf "$TORN" -C "$WORK/tornman" manifest.toml
grep -q 'consistent = false' "$WORK/tornman/manifest.toml"
check "and the archive records itself as torn" $? "$(cat "$WORK/tornman/manifest.toml")"

# ─── Refusing without an .env ────────────────────────────────────────────────
NOENV="$WORK/noenv"; make_install "$NOENV" 17 "x.edu"; rm "$NOENV/.env"
out="$(cd "$NOENV" && bash scripts/backup.sh --lang en --to "$WORK/nope" 2>&1)"
check "a backup without an .env is refused" $(( $? == 0 ? 1 : 0 )) "$out"
grep -qi 'unreadable' <<<"$out"
check "and says why an .env-less backup is useless" $? "$out"

# ─── Restoring: the happy path ───────────────────────────────────────────────
B="$WORK/b"; make_install "$B" 17 "example.edu"
rm -rf "$B/data"        # an empty target
export COMPOSE_LOG="$WORK/compose-b.log"

out="$(cd "$B" && bash scripts/restore.sh "$ARCHIVE" --lang en --dry-run 2>&1)"
check "a dry run passes every check" $? "$out"
[[ ! -d "$B/data" ]]
check "and touches nothing" $? "the dry run created $B/data"

out="$(cd "$B" && bash scripts/restore.sh "$ARCHIVE" --lang en 2>&1)"
check "a restore onto an empty target succeeds" $? "$out"
[[ -f "$B/data/weaviate/data/store.db" ]]
check "the data is in place" $? "$(find "$B/data" -maxdepth 2 2>&1 | head)"
grep -q 'pg-secret-example.edu' "$B/.env"
check "the archive's .env is in place" $? ""
grep -q "BASE_DATA_PATH=\"$B/data\"" "$B/.env"
check "BASE_DATA_PATH follows the target host, not the archive" $? "$(grep BASE_DATA "$B/.env")"

# ─── Refusal 1: a different Postgres major ───────────────────────────────────
C="$WORK/c"; make_install "$C" 16 "example.edu"; rm -rf "$C/data"
out="$(cd "$C" && bash scripts/restore.sh "$ARCHIVE" --lang en 2>&1)"
check "restoring across Postgres majors is refused" $(( $? == 0 ? 1 : 0 )) "$out"
grep -qi 'major' <<<"$out"
check "and the refusal names the versions" $? "$out"
[[ ! -d "$C/data" ]]
check "nothing was unpacked before refusing" $? "$C/data exists"

# ─── Refusal 2: halves that do not belong together ───────────────────────────
# The archive is rebuilt with an .env from a different installation, which is
# what a hand-assembled archive looks like.
FRANK="$WORK/frank"; mkdir -p "$FRANK"
tar -xzf "$ARCHIVE" -C "$FRANK"
sed -i.bak 's/pg-secret-example.edu/pg-secret-somewhere-else/' "$FRANK/env"
(cd "$FRANK" && tar -czf "$WORK/frankenstein.tar.gz" manifest.toml env data.tar)
D="$WORK/d"; make_install "$D" 17 "example.edu"; rm -rf "$D/data"
out="$(cd "$D" && bash scripts/restore.sh "$WORK/frankenstein.tar.gz" --lang en 2>&1)"
check "an archive whose halves disagree is refused" $(( $? == 0 ? 1 : 0 )) "$out"
grep -qi 'POSTGRES_PASSWORD' <<<"$out"
check "and the refusal names which secret disagrees" $? "$out"
[[ ! -d "$D/data" ]]
check "nothing was unpacked before that refusal either" $? "$D/data exists"

# ─── Refusal 3: an occupied target ───────────────────────────────────────────
E="$WORK/e"; make_install "$E" 17 "example.edu"
echo "live data" > "$E/data/weaviate/data/precious.db"
out="$(cd "$E" && bash scripts/restore.sh "$ARCHIVE" --lang en 2>&1)"
check "restoring over an existing installation is refused" $(( $? == 0 ? 1 : 0 )) "$out"
[[ -f "$E/data/weaviate/data/precious.db" ]]
check "and the existing data is untouched" $? ""

out="$(cd "$E" && bash scripts/restore.sh "$ARCHIVE" --lang en --force 2>&1)"
check "--force proceeds" $? "$out"
aside="$(ls -d "$E"/data.replaced-* 2>/dev/null | head -1)"
[[ -n "$aside" && -f "$aside/weaviate/data/precious.db" ]]
check "the replaced installation is moved aside, not deleted" $? "$(ls -d "$E"/data* 2>&1)"

# ─── Refusal 4: a damaged archive ────────────────────────────────────────────
cp "$ARCHIVE" "$WORK/damaged.tar.gz"; cp "$ARCHIVE.sha256" "$WORK/damaged.tar.gz.sha256"
printf 'rot' >> "$WORK/damaged.tar.gz"
F="$WORK/f"; make_install "$F" 17 "example.edu"; rm -rf "$F/data"
out="$(cd "$F" && bash scripts/restore.sh "$WORK/damaged.tar.gz" --lang en 2>&1)"
check "an archive that fails its checksum is refused" $(( $? == 0 ? 1 : 0 )) "$out"
[[ ! -d "$F/data" ]]
check "and nothing was unpacked" $? "$F/data exists"

# ─── The rename is never silent ──────────────────────────────────────────────
G="$WORK/g"; make_install "$G" 17 "example.edu"; rm -rf "$G/data"
out="$(cd "$G" && bash scripts/restore.sh "$ARCHIVE" --lang en --dry-run 2>&1)"
grep -qi 'example.edu' <<<"$out"
check "a restore states the address it is bringing" $? "$out"

out="$(cd "$G" && bash scripts/restore.sh "$ARCHIVE" --lang en --rename "new.edu" --force 2>&1)"
check "a rename restores" $? "$out"
grep -q 'DOMAIN="new.edu"' "$G/.env"
check "and DOMAIN is rewritten" $? "$(grep DOMAIN "$G/.env")"
grep -q 'pg-secret-example.edu' "$G/.env"
check "while the secrets are kept — a rename is not a reinstall" $? ""
grep -qi 'certificate' <<<"$out"
check "the rename says the certificates are not covered" $? "$out"
grep -qi 'LTI' <<<"$out"
check "…and that the LMS registration has to be redone" $? "$out"

# ─── A message beginning with a hyphen is text, not an option ────────────────
# printf reads its first argument as the format, and "--dry-run: every check
# runs…" begins with two of them. On the first real run that printed
# "printf: --: invalid option" followed by an empty warning — the worst shape
# a bug can take, because the operator sees that something was not said and
# cannot tell what.
for lang in en de; do
    out="$(cd "$G" && bash scripts/restore.sh "$ARCHIVE" --lang "$lang" --dry-run 2>&1)"
    grep -q 'invalid option' <<<"$out"
    check "no printf option error in $lang" $(( $? == 0 ? 1 : 0 )) "$out"
    grep -q 'dry-run' <<<"$out"
    check "the leading-hyphen message is printed in full in $lang" $? "$out"
done

# Every catalogue message that begins with a hyphen, not just this one.
hyphenated="$(grep -cE '^\s*\[[a-z0-9_]+\]="-' "$REPO/scripts/lib/messages.sh")"
(( hyphenated > 0 ))
check "there are messages beginning with a hyphen to protect" $? "$hyphenated"
grep -q 'printf -- "\$fmt"' "$REPO/scripts/lib/messages.sh"
check "t() passes -- before the format" $? \
      "$(grep -n 'printf .*fmt' "$REPO/scripts/lib/messages.sh")"

# ─── The address is printed once ─────────────────────────────────────────────
# On a Tailscale deployment DOMAIN is the MagicDNS name, so the archive's two
# address fields hold the same string and printing both stutters.
TS="$WORK/ts"; make_install "$TS" 17 "host.example.ts.net"
sed -i.bak 's/^TAILSCALE_HOSTNAME=""/TAILSCALE_HOSTNAME="host.example.ts.net"/' "$TS/.env"
COMPOSE_LOG="$WORK/ts.log" bash -c "cd '$TS' && bash scripts/backup.sh --lang en --to '$WORK/tsb'" >/dev/null 2>&1
TSA="$(ls -1 "$WORK"/tsb/smartrag-*.tar.gz | head -1)"
out="$(cd "$TS" && bash scripts/restore.sh "$TSA" --lang en --dry-run 2>&1)"
n="$(grep -o 'host.example.ts.net' <<<"$(grep 'Archive taken' <<<"$out")" | wc -l | tr -d ' ')"
[[ "$n" == "1" ]]
check "an address that is both DOMAIN and MagicDNS is printed once" $? "$n in: $(grep 'Archive taken' <<<"$out")"

# ─── A dry run names what a real run would replace ───────────────────────────
out="$(cd "$E" && bash scripts/restore.sh "$ARCHIVE" --lang en --dry-run --force 2>&1)"
check "a dry run over an occupied target still passes" $? "$out"
grep -qi 'aside' <<<"$out"
check "…but says the live installation would be moved aside" $? "$out"

# ─── Both languages, because an operator restoring at 3am reads their own ────
for lang in en de; do
    out="$(cd "$G" && bash scripts/restore.sh "$WORK/definitely-not-here.tar.gz" --lang "$lang" 2>&1)"
    [[ -n "$out" ]] && ! grep -qE '^\s*restore_[a-z_]+\s*$' <<<"$out"
    check "a missing archive is reported in $lang" $? "$out"
done

# ─── The closing text must not deny what now exists ──────────────────────────
# The installer's reference section told every new operator there was no
# backup command. It was true when written and false the moment backup.sh
# landed, and a sentence like that is read once, at the only moment somebody
# is deciding how to look after the installation.
for lang in EN DE; do
    line="$(grep -A1 "\[ref_admin_items\]" "$REPO/scripts/lib/messages.sh" \
            | grep -m1 "$lang" || true)"
done
catalog="$(cat "$REPO/scripts/lib/messages.sh")"
grep -q 'no backup command yet' <<<"$catalog"
check "the reference no longer says there is no backup command" $(( $? == 0 ? 1 : 0 )) ""
grep -q 'Ein Backup-Befehl fehlt noch' <<<"$catalog"
check "…in German either" $(( $? == 0 ? 1 : 0 )) ""
grep -qE '\[ref_admin_items\]=.*[Bb]ackup' <<<"$catalog"
check "and mentions the backup command that exists" $? ""

if (( ${#FAILURES[@]} > 0 )); then
    echo "FAILURES:"; printf '  - %s\n' "${FAILURES[@]}"; exit 1
fi
echo "All backup/restore checks passed: a backup stops the services, copies"
echo "BASE_DATA_PATH and .env into one archive with a checksum, records the"
echo "Postgres major from the data directory itself and fingerprints — never"
echo "stores — the secrets a data directory is unreadable without; --running"
echo "keeps the services up and marks the archive torn; and a backup with no"
echo ".env is refused, because it could never be restored. A restore refuses,"
echo "before unpacking anything, an archive from a different Postgres major,"
echo "one whose .env and data were not taken together, one that fails its"
echo "checksum, and a target that already holds data — and with --force moves"
echo "the existing installation aside rather than deleting it. A dry run does"
echo "all of that and touches nothing. The address is always stated, and a"
echo "rename rewrites DOMAIN, keeps every secret, and says out loud that the"
echo "certificates and the LMS registration are not covered by it."
