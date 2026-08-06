#!/usr/bin/env bash
# ═════════════════════════════════════════════════════════════════════════════
# Regression: "no healthcheck" must be distinguishable from "still starting"
# ═════════════════════════════════════════════════════════════════════════════
#
# `docker inspect --format='{{.State.Health.Status}}'` is a partial template:
# on a container whose image ships no HEALTHCHECK it fails outright ("map has
# no entry for key Health") instead of reporting anything. Callers that fold
# that failure into an empty string treat it as "still booting" and wait out
# their entire timeout for a status that can never arrive.
#
# That happened for real on smartrag-langfuse-web: 180s of waiting, and the
# unrelated container logs printed next to the timeout pointed the diagnosis
# at Redis twice before the actual cause surfaced.
#
# These tests use a stub `docker` that reproduces the template engine's
# behaviour, so they need no daemon.
# ═════════════════════════════════════════════════════════════════════════════

set -uo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$TEST_DIR/.." && pwd)"

PASS=0; FAIL=0
check() {
    local desc="$1" expected="$2" actual="$3"
    if [[ "$expected" == "$actual" ]]; then
        echo "  ✓ $desc"; PASS=$((PASS+1))
    else
        echo "  ✗ $desc"; echo "      erwartet: [$expected]"; echo "      erhalten: [$actual]"
        FAIL=$((FAIL+1))
    fi
}

STUB_DIR="$(mktemp -d)"
trap 'rm -rf "$STUB_DIR"' EXIT

# ─── Stub docker ─────────────────────────────────────────────────────────────
# Behaves like the real Go template engine for the two formats we care about:
#   with-health    → has a healthcheck, currently "starting"
#   without-health → no healthcheck, running   (the langfuse-web case)
#   ghost          → does not exist
cat > "$STUB_DIR/docker" <<'STUB'
#!/usr/bin/env bash
fmt=""; name=""
for arg in "$@"; do
    case "$arg" in
        --format=*) fmt="${arg#--format=}" ;;
        with-health|without-health|ghost) name="$arg" ;;
    esac
done
[[ "$name" == "ghost" ]] && { echo "Error: No such object: ghost" >&2; exit 1; }

case "$fmt" in
    '{{.State.Status}}')
        echo running ;;
    '{{.State.Health.Status}}')
        # The naive template: fails hard when there is no Health key.
        if [[ "$name" == "without-health" ]]; then
            echo "template parsing error: map has no entry for key \"Health\"" >&2
            exit 1
        fi
        echo starting ;;
    *'{{if .State.Health}}'*)
        # The guarded template: total, never fails.
        if [[ "$name" == "without-health" ]]; then echo none; else echo starting; fi ;;
esac
exit 0
STUB
chmod +x "$STUB_DIR/docker"
PATH="$STUB_DIR:$PATH"

# shellcheck source=../scripts/lib/common.sh
source "$REPO_ROOT/scripts/lib/common.sh"

echo "container_health() unterscheidet alle Zustände:"
check "Healthcheck vorhanden, startet noch" "starting" "$(container_health with-health)"
check "kein Healthcheck definiert"          "none"     "$(container_health without-health)"
check "Container existiert nicht"           "absent"   "$(container_health ghost)"

echo
echo "container_ready() wertet 'kein Healthcheck' als bereit, wenn der Container läuft:"
container_ready without-health && r=ready || r=notready
check "ohne Healthcheck, laufend → bereit" "ready" "$r"
container_ready with-health && r=ready || r=notready
check "mit Healthcheck, noch startend → nicht bereit" "notready" "$r"
container_ready ghost && r=ready || r=notready
check "nicht existent → nicht bereit" "notready" "$r"

echo
echo "Kein Skript verwendet mehr das ungeschützte Template:"
# This is the durable assertion: the bug returns the moment someone writes
# {{.State.Health.Status}} without an {{if .State.Health}} guard again.
naive="$(grep -rn '{{\.State\.Health\.Status}}' "$REPO_ROOT/scripts" \
         | grep -v '{{if \.State\.Health}}' \
         | grep -v ':[0-9]*:[[:space:]]*#' || true)"
check "keine ungeschützten Vorkommen in scripts/" "" "$naive"

echo
echo "Jeder Dienst in der Warteliste ist abgedeckt:"
# Either compose defines a healthcheck, or the image ships one, or the wait
# loop must be able to fall back — which it now can, for every service.
if grep -q 'smartrag-langfuse-web' "$REPO_ROOT/scripts/start-services.sh"; then
    block="$(awk '/^  smartrag-langfuse-web:/{f=1} f&&/healthcheck:/{print "yes"; exit}' \
             "$REPO_ROOT/docker/docker-compose.yml")"
    check "langfuse-web hat einen Healthcheck in compose" "yes" "$block"
fi

echo
if (( FAIL == 0 )); then
    echo "✓ $PASS Prüfungen bestanden"
    exit 0
else
    echo "✗ $FAIL von $((PASS+FAIL)) Prüfungen fehlgeschlagen"
    exit 1
fi
