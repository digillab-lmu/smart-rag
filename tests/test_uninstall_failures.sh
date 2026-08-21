#!/usr/bin/env bash
# Regression for 6e-07: uninstall.sh used to print "ok" for container/network
# removal, nginx reload, and certificate deletion regardless of whether the
# underlying command actually succeeded (all four were `... || true`). Now
# each failure gets its own warning and the script exits 1 if anything failed.
#
# Runs against a sandboxed copy of uninstall.sh with docker/systemctl/certbot/
# nginx replaced by stubs — no real container, network, nginx config, or
# certificate is ever touched. This host has a live smart-rag installation
# running (real smartrag-* containers, real smart-rag-network); the whole
# point of the stubs is that uninstall.sh never sees the real docker/systemctl/
# certbot binaries at all.
set -uo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$TEST_DIR/.." && pwd)"

FAILURES=()
check() { if (( $2 != 0 )); then FAILURES+=("$1: $3"); fi; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

ROOT="$WORK/install"
STUB_DIR="$WORK/bin"
mkdir -p "$ROOT/scripts/lib" "$STUB_DIR"

cp "$REPO/scripts/uninstall.sh" "$ROOT/scripts/"
cp "$REPO/scripts/lib/messages.sh" "$ROOT/scripts/lib/"
cp "$REPO/scripts/lib/common.sh" "$ROOT/scripts/lib/"
# create_system_snapshot() writes to the real /var/backups on the host and
# shells out to tar/ss — out of scope here, and not something a test should
# leave behind on the machine it runs on. Neutralised in this copy only.
cat >> "$ROOT/scripts/lib/common.sh" <<'EOF'
create_system_snapshot() { :; }
EOF

cat > "$ROOT/.env" <<'ENVEOF'
DOMAIN="example.com"
BASE_DATA_PATH="/nonexistent-not-used-by-this-test"
ENVEOF

# ─── Stubs ────────────────────────────────────────────────────────────────────
# Each one only understands the exact subcommands uninstall.sh issues and
# fails loudly on anything else, so a code path this test doesn't expect
# can't silently fall through to a real binary elsewhere on PATH.
cat > "$STUB_DIR/docker" <<'STUB'
#!/usr/bin/env bash
[[ "$1" == "__stub_selftest__" ]] && { echo "UNINSTALL-TEST-STUB-9f3a1c2e-docker"; exit 0; }
case "$1" in
    ps)
        for n in ${FAKE_CONTAINERS:-}; do echo "$n"; done
        ;;
    rm)
        name="$3"
        if [[ -n "${FAKE_FAIL_CONTAINER:-}" && "$name" == "$FAKE_FAIL_CONTAINER" ]]; then
            echo "docker-stub: fake failure removing $name" >&2
            exit 1
        fi
        ;;
    network)
        case "$2" in
            inspect) exit 0 ;;
            rm)
                if [[ "${FAKE_FAIL_NETWORK:-0}" == "1" ]]; then
                    echo "docker-stub: fake failure removing network" >&2
                    exit 1
                fi
                ;;
            *) echo "docker-stub: unhandled network subcommand: $2" >&2; exit 9 ;;
        esac
        ;;
    *) echo "docker-stub: unhandled subcommand: $1" >&2; exit 9 ;;
esac
exit 0
STUB

cat > "$STUB_DIR/systemctl" <<'STUB'
#!/usr/bin/env bash
[[ "$1" == "__stub_selftest__" ]] && { echo "UNINSTALL-TEST-STUB-9f3a1c2e-systemctl"; exit 0; }
case "$1" in
    reload)
        if [[ "${FAKE_FAIL_NGINX_RELOAD:-0}" == "1" ]]; then
            echo "systemctl-stub: fake reload failure" >&2
            exit 1
        fi
        ;;
    *) echo "systemctl-stub: unhandled subcommand: $1" >&2; exit 9 ;;
esac
exit 0
STUB

cat > "$STUB_DIR/nginx" <<'STUB'
#!/usr/bin/env bash
[[ "$1" == "__stub_selftest__" ]] && { echo "UNINSTALL-TEST-STUB-9f3a1c2e-nginx"; exit 0; }
case "$1" in
    -t) exit 0 ;;
    *) echo "nginx-stub: unhandled arg: $1" >&2; exit 9 ;;
esac
STUB

cat > "$STUB_DIR/certbot" <<'STUB'
#!/usr/bin/env bash
[[ "$1" == "__stub_selftest__" ]] && { echo "UNINSTALL-TEST-STUB-9f3a1c2e-certbot"; exit 0; }
case "$1" in
    certificates)
        echo "  Certificate Name: smartrag-example.com"
        ;;
    delete)
        if [[ "${FAKE_FAIL_CERTBOT_DELETE:-0}" == "1" ]]; then
            echo "certbot-stub: fake delete failure" >&2
            exit 1
        fi
        ;;
    *) echo "certbot-stub: unhandled subcommand: $1" >&2; exit 9 ;;
esac
exit 0
STUB

chmod +x "$STUB_DIR"/docker "$STUB_DIR"/systemctl "$STUB_DIR"/nginx "$STUB_DIR"/certbot

# STUB_DIR goes in front of the real PATH — command -v below is what proves
# it's actually found first, not just that we intended it to be.
export PATH="$STUB_DIR:$PATH"

# Pre-flight: uninstall.sh's nginx-config removal (step 2) is NOT behind a
# --purge-* flag, only behind `[[ -e "$f" || -L "$f" ]]` for four fixed
# /etc/nginx/... paths — a real, unstubbed `rm -f`. Confirmed absent on this
# host right now; abort rather than assume that stays true.
REAL_NGINX_FILES=(
    /etc/nginx/sites-enabled/smartrag-suite.conf
    /etc/nginx/sites-available/smartrag-suite.conf
    /etc/nginx/sites-enabled/smartrag-acme.conf
    /etc/nginx/sites-available/smartrag-acme.conf
)
for f in "${REAL_NGINX_FILES[@]}"; do
    if [[ -e "$f" || -L "$f" ]]; then
        echo "ABORT: $f exists on this host — uninstall.sh's real (unstubbed) rm -f would remove it. Refusing to run." >&2
        exit 1
    fi
done

# Safety check, run again before every single invocation below (not just
# once here) — see verify_stubs_or_abort(), called from run_uninstall().
# Two layers: (1) command -v resolves inside STUB_DIR, not the real binary
# on this host; (2) a behavioural self-test — each stub answers a
# `__stub_selftest__` argument with a fixed signature no real docker/
# systemctl/certbot/nginx could ever produce. Path resolution alone only
# proves what *would* run; the signature proves what actually did.
verify_stubs_or_abort() {
    local bin resolved reply
    for bin in docker systemctl certbot nginx; do
        resolved="$(command -v "$bin")"
        if [[ "$resolved" != "$STUB_DIR/$bin" ]]; then
            echo "ABORT: $bin resolves to $resolved, not the stub — refusing to run uninstall.sh" >&2
            exit 1
        fi
        reply="$("$bin" __stub_selftest__ 2>/dev/null)"
        if [[ "$reply" != "UNINSTALL-TEST-STUB-9f3a1c2e-$bin" ]]; then
            echo "ABORT: $bin did not answer the stub self-test (got: '$reply') — refusing to run uninstall.sh" >&2
            exit 1
        fi
    done
}

run_uninstall() {   # sets $out and $rc
    verify_stubs_or_abort
    out="$(FAKE_CONTAINERS="${FAKE_CONTAINERS:-}" \
           FAKE_FAIL_CONTAINER="${FAKE_FAIL_CONTAINER:-}" \
           FAKE_FAIL_NETWORK="${FAKE_FAIL_NETWORK:-0}" \
           FAKE_FAIL_NGINX_RELOAD="${FAKE_FAIL_NGINX_RELOAD:-0}" \
           FAKE_FAIL_CERTBOT_DELETE="${FAKE_FAIL_CERTBOT_DELETE:-0}" \
           bash "$ROOT/scripts/uninstall.sh" --lang en --yes --purge-certs 2>&1)"
    rc=$?
}

reset_env() {
    unset FAKE_CONTAINERS FAKE_FAIL_CONTAINER FAKE_FAIL_NETWORK \
          FAKE_FAIL_NGINX_RELOAD FAKE_FAIL_CERTBOT_DELETE
}

echo "Everything succeeds → exit 0, all three ok() messages, no warnings:"
reset_env
FAKE_CONTAINERS="smartrag-fixture-a"
run_uninstall
check "exit code 0" $(( rc == 0 ? 0 : 1 )) "got $rc"
check "containers_done printed" $([[ "$out" == *"Containers and network removed"* ]] && echo 0 || echo 1) "$out"
check "nginx_done printed" $([[ "$out" == *"nginx configs removed"* ]] && echo 0 || echo 1) "$out"
check "certs_done printed" $([[ "$out" == *"Certificate smartrag-example.com deleted"* ]] && echo 0 || echo 1) "$out"

echo
echo "Container removal fails → named in the warning, exit 1, containers_done suppressed:"
reset_env
FAKE_CONTAINERS="smartrag-fixture-a"
FAKE_FAIL_CONTAINER="smartrag-fixture-a"
run_uninstall
check "exit code 1" $(( rc == 1 ? 0 : 1 )) "got $rc"
check "names the failed container" $([[ "$out" == *"Could not remove container(s): smartrag-fixture-a"* ]] && echo 0 || echo 1) "$out"
check "containers_done NOT printed" $([[ "$out" != *"Containers and network removed"* ]] && echo 0 || echo 1) "$out"

echo
echo "Network removal fails → own warning, exit 1:"
reset_env
FAKE_FAIL_NETWORK=1
run_uninstall
check "exit code 1" $(( rc == 1 ? 0 : 1 )) "got $rc"
check "network warning printed" $([[ "$out" == *"Could not remove smart-rag-network"* ]] && echo 0 || echo 1) "$out"
check "containers_done NOT printed" $([[ "$out" != *"Containers and network removed"* ]] && echo 0 || echo 1) "$out"

echo
echo "nginx reload fails → own warning, exit 1, but nginx_done still prints (configs really were removed):"
reset_env
FAKE_FAIL_NGINX_RELOAD=1
run_uninstall
check "exit code 1" $(( rc == 1 ? 0 : 1 )) "got $rc"
check "reload warning printed" $([[ "$out" == *"reload failed"* ]] && echo 0 || echo 1) "$out"
check "nginx_done still printed" $([[ "$out" == *"nginx configs removed"* ]] && echo 0 || echo 1) "$out"

echo
echo "certbot delete fails → named in the warning, exit 1, certs_done suppressed:"
reset_env
FAKE_FAIL_CERTBOT_DELETE=1
run_uninstall
check "exit code 1" $(( rc == 1 ? 0 : 1 )) "got $rc"
check "certs warning printed" $([[ "$out" == *"Certificate smartrag-example.com could not be deleted"* ]] && echo 0 || echo 1) "$out"
check "certs_done NOT printed" $([[ "$out" != *"Certificate smartrag-example.com deleted"* ]] && echo 0 || echo 1) "$out"

echo
if (( ${#FAILURES[@]} == 0 )); then
    echo "✓ all checks passed"
    exit 0
else
    echo "✗ ${#FAILURES[@]} check(s) failed:"
    for f in "${FAILURES[@]}"; do echo "  - $f"; done
    exit 1
fi
