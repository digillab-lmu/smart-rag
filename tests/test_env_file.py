"""
env_file.py — the code a web-facing GUI uses to patch the system's central .env.

Two consumers read that file with different rules: Python's read_env(), and
bash, which *executes* it on every `source .env` in the installer and the admin
tool. So a value typed into a form in the browser reaches a shell. If it is
not escaped exactly right, a Flowise API key field becomes remote code
execution as root the next time anyone runs the installer.

That is not a theoretical concern in this project: a backtick pair written
into a message string — not even a user-supplied value — recursed the admin
tool four thousand levels deep and left twelve thousand processes behind. The
mechanism is identical, and the only difference here is that the input comes
from outside.

These tests therefore do not re-implement shell quoting to check it. They
write adversarial values, then let real bash source the file with executable
canaries on PATH, and require that nothing ran and the variable holds the
value verbatim.
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "content-admin"))

from env_file import read_env, set_env_var  # noqa: E402

failures = []


def check(name, ok, detail=""):
    if not ok:
        failures.append(f"{name}: {detail}")


# Values a form field could carry that mean something to a shell.
ADVERSARIAL = {
    "cmd_sub": "$(touch /tmp/smartrag-canary)",
    "backtick": "`touch /tmp/smartrag-canary`",
    "nested": 'a"$(id)"b',
    "var_ref": "${HOME}",
    "backslash_tail": "ends-with-a-backslash\\",
    "quotes": 'he said "hi" and \'bye\'',
    "semicolon": "value; touch /tmp/smartrag-canary",
    "dollar_brace": "$IFS$9",
    "unicode": "Grüße — mit Bindestrich",
    "spaces": "   leading and trailing   ",
}


def fresh_env() -> Path:
    d = tempfile.mkdtemp()
    p = Path(d) / ".env"
    p.write_text(
        'DOMAIN="example.com"\n'
        "# a comment\n"
        'POSTGRES_PASSWORD="p1"\n'
        'NEO4J_AUTH="neo4j/${NEO4J_PASSWORD}"\n'
        'EXISTING="old"\n'
    )
    return p


# ─── 1. Round-trip through read_env ──────────────────────────────────────────
p = fresh_env()
for name, value in ADVERSARIAL.items():
    set_env_var(f"T_{name.upper()}", value, env_path=p)
back = read_env(p)
for name, value in ADVERSARIAL.items():
    got = back.get(f"T_{name.upper()}")
    check(f"round-trip preserves {name}", got == value, f"wrote {value!r}, read {got!r}")

# ─── 2. Real bash must not execute any of it ─────────────────────────────────
# The decisive test: bash is what actually sources this file.
tmpbin = tempfile.mkdtemp()
canary = Path(tmpbin) / "fired"
for cmdname in ("touch", "id", "sudo", "smartrag"):
    wrapper = Path(tmpbin) / cmdname
    wrapper.write_text(f'#!/usr/bin/env bash\n/usr/bin/touch "{canary}"\n')
    wrapper.chmod(0o755)

script = f"""
set -a
source "{p}"
set +a
for k in {' '.join('T_' + n.upper() for n in ADVERSARIAL)}; do
    printf '%s\\t%s\\n' "$k" "${{!k}}"
done
"""
res = subprocess.run(
    ["bash", "-c", script],
    capture_output=True, text=True,
    env={**os.environ, "PATH": f"{tmpbin}:{os.environ['PATH']}"},
)
check("bash can source the file at all", res.returncode == 0, res.stderr[:300])
check("sourcing .env executed nothing", not canary.exists(),
      "a canary command ran — a form value reached the shell")

seen = dict(
    line.split("\t", 1) for line in res.stdout.splitlines() if "\t" in line
)
for name, value in ADVERSARIAL.items():
    key = f"T_{name.upper()}"
    # Bash strips nothing inside double quotes, so the value must come back
    # byte for byte — including the leading/trailing spaces.
    check(f"bash reads {name} verbatim", seen.get(key) == value,
          f"wrote {value!r}, bash saw {seen.get(key)!r}")

# ─── 3. Python and bash must agree ───────────────────────────────────────────
# Two readers of one file that disagree is the failure mode that has already
# cost this project two separate incidents.
for name in ADVERSARIAL:
    key = f"T_{name.upper()}"
    check(f"python and bash agree on {name}", back.get(key) == seen.get(key),
          f"python {back.get(key)!r} vs bash {seen.get(key)!r}")

# ─── 4. In-place patching, not appending ─────────────────────────────────────
p2 = fresh_env()
before = p2.read_text().splitlines()
set_env_var("EXISTING", "new", env_path=p2)
after = p2.read_text().splitlines()
check("an existing key is replaced, not duplicated", len(before) == len(after),
      f"{len(before)} -> {len(after)} lines")
check("the replaced key keeps its position",
      after.index('EXISTING="new"') == before.index('EXISTING="old"'),
      f"{after}")
check("the new value is readable", read_env(p2).get("EXISTING") == "new", "")
check("an interpolating line is untouched",
      'NEO4J_AUTH="neo4j/${NEO4J_PASSWORD}"' in after,
      "a line that references an earlier one moved or changed")

set_env_var("BRAND_NEW", "x", env_path=p2)
check("a new key is appended", p2.read_text().splitlines()[-1] == 'BRAND_NEW="x"',
      p2.read_text().splitlines()[-1])

# A file with no trailing newline must not have its last line corrupted.
p3 = fresh_env()
p3.write_text('LAST="value-without-newline"')
set_env_var("ADDED", "y", env_path=p3)
env3 = read_env(p3)
check("a missing trailing newline is handled",
      env3.get("LAST") == "value-without-newline" and env3.get("ADDED") == "y",
      p3.read_text())

# ─── 5. Key matching must be exact ───────────────────────────────────────────
p4 = fresh_env()
set_env_var("FLOWISE_API_KEY", "aaa", env_path=p4)
set_env_var("FLOWISE_API_KEY_OLD", "bbb", env_path=p4)
set_env_var("FLOWISE_API_KEY", "ccc", env_path=p4)
e4 = read_env(p4)
check("a longer key with the same prefix is not overwritten",
      e4.get("FLOWISE_API_KEY_OLD") == "bbb", e4.get("FLOWISE_API_KEY_OLD"))
check("the exact key is updated", e4.get("FLOWISE_API_KEY") == "ccc",
      e4.get("FLOWISE_API_KEY"))
# A commented-out line must not be treated as the key.
p5 = fresh_env()
p5.write_text('#SECRET="commented"\n')
set_env_var("SECRET", "real", env_path=p5)
check("a commented line is not patched in place",
      read_env(p5).get("SECRET") == "real" and '#SECRET="commented"' in p5.read_text(),
      p5.read_text())

# ─── 6. A newline in a value must not forge a second key ─────────────────────
# Quote escaping stops a value from closing its own quotes, so bash is safe.
# read_env splits on lines, though — so an unescaped newline can make Python
# see a key that bash does not. Two readers, one file, different answers.
p6 = fresh_env()
try:
    set_env_var("NOTES", 'harmless\nINJECTED="gotcha"', env_path=p6)
    refused = False
except ValueError:
    refused = True
check("a value with a line break is refused", refused,
      "it was written — read_env would then see a key bash does not")
check("and nothing was written", "INJECTED" not in read_env(p6), p6.read_text())
for bad in ("a\rb", "a\r\nb", "\n"):
    try:
        set_env_var("NOTES", bad, env_path=p6)
        ok_refused = False
    except ValueError:
        ok_refused = True
    check(f"carriage returns are refused too ({bad!r})", ok_refused, "")

res6 = subprocess.run(
    ["bash", "-c", f'set -a; source "{p6}"; set +a; echo "${{INJECTED-UNSET}}"'],
    capture_output=True, text=True,
)
check("bash does not see the forged key either", res6.stdout.strip() == "UNSET",
      res6.stdout.strip())

# ─── 7. The bash port must not replace the file's inode ──────────────────────
# Docker bind-mounts a single file by inode. A writer that does tmp+mv gives
# the destination a new one, and every container mounting .env then keeps
# reading the file it started with — silently, for as long as it lives.
#
# Observed: a rotated LLM key kept failing with the old key's billing error,
# while the very same key worked when tested from the shell. Host inode
# 6553919, container inode 6553909, two different keys.
#
# Python's set_env_var uses write_text(), which truncates in place. The bash
# port is the one that had to change, and both write this same file.
import shutil  # noqa: E402

bash = shutil.which("bash")
p7 = fresh_env()
before_inode = p7.stat().st_ino
res7 = subprocess.run(
    [bash, "-c",
     f'source "{REPO}/scripts/lib/common.sh" >/dev/null 2>&1; '
     f'backup_file() {{ :; }}; '
     f'set_env_var "{p7}" NEW_KEY "new-value"'],
    capture_output=True, text=True,
)
check("bash set_env_var runs", res7.returncode == 0, res7.stderr[-300:])
check("the bash port keeps the inode", p7.stat().st_ino == before_inode,
      f"{before_inode} -> {p7.stat().st_ino} — a container's mount would go stale")
check("and the value was written", 'NEW_KEY="new-value"' in p7.read_text(), p7.read_text())

# Python's port, same requirement, same file.
p8 = fresh_env()
before8 = p8.stat().st_ino
set_env_var("ANOTHER", "v", env_path=p8)
check("the Python port keeps the inode", p8.stat().st_ino == before8,
      f"{before8} -> {p8.stat().st_ino}")

# No writer anywhere may replace a bind-mounted file by renaming over it. The
# rule was written for .env and holds for every file the compose mounts: a mv
# swaps the inode, and a container that already has the old one mounted keeps
# reading it while the host shows the new content. It earned its keep on a
# second file on 2026-08-24 — write_garage_config wrote garage.toml, which is
# mounted at /etc/garage.toml, through a temp file and a mv.
for script in (REPO / "scripts").rglob("*.sh"):
    body = "\n".join(
        l for l in script.read_text().splitlines() if not l.strip().startswith("#")
    )
    if "mv " in body and ".env" in body:
        import re as _re
        bad = _re.findall(r'^\s*mv\s+.*\$\{?(?:tmp|TMP)\b.*$', body, _re.M)
        check(f"{script.name} does not rename over a mounted file", not bad, str(bad))

# ─── 8. Repeated writes must not bury the output in backup notices ───────────
# The upgrade entry and the Garage spike each write many keys in a row.
# backup_file announced every one, so a single change produced eighteen
# "Backed up" lines — which hides whatever else the operation reported and
# makes a routine action look drastic. Within one second the later calls
# overwrote the same file anyway.
p9 = fresh_env()
res9 = subprocess.run(
    [bash, "-c",
     f'source "{REPO}/scripts/lib/common.sh" >/dev/null 2>&1; '
     f'for k in A B C D E F; do set_env_var "{p9}" "K$k" "v"; done'],
    capture_output=True, text=True,
)
notices = res9.stdout.count("Backed up")
check("six writes announce at most one backup per second", notices <= 2,
      f"{notices} notices for six writes")
backups = list(p9.parent.glob(".env.backup-*"))
check("and leave at most one backup file per second", len(backups) <= 2,
      f"{len(backups)} files: {[b.name for b in backups]}")
env9 = read_env(p9)
check("every key was still written", all(env9.get(f"K{k}") == "v" for k in "ABCDEF"),
      "quieter must not mean fewer writes")

# ─── Result ──────────────────────────────────────────────────────────────────
if failures:
    print("FAILURES:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print(
    "All env_file checks passed: adversarial values survive a round trip "
    "unchanged, real bash sources the file without executing any of them "
    "(verified with canaries named touch, id, sudo and smartrag on PATH), "
    "Python and bash read every value identically, an existing key is patched "
    "in place without moving the lines that interpolate earlier ones, a key "
    "sharing a prefix with a longer one is left alone, a commented line is not "
    "mistaken for the key, and a line break is refused outright rather than "
    "encoded into something the two readers would disagree about."
)
