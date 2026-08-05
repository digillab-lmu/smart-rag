"""Guards the custom-function code inside the agent templates against the
Flowise sandbox's actual shape.

A live agent failed with:

    NodeVM Execution Error: TypeError: Cannot read properties of undefined
    (reading 'env')

The templates read secrets as `$vars?.X || process.env.X || 'default'`.
Flowise builds the sandbox with `process` explicitly set to undefined
(packages/components/src/utils.ts, createCodeExecutionSandbox at
flowise@3.1.3), so the moment `$vars?.X` is falsy the next term throws
instead of falling through to the default. And one of these variables —
EMBEDDING_BASE_URL — is legitimately empty in every deployment that uses a
standard provider, so this was not an edge case but the normal path.
"""
import json
import os
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
APP_DIR = str(REPO / "content-admin")
TEMPLATES = Path(APP_DIR).parent / "flowise" / "agents"
sys.path.insert(0, APP_DIR)

tmpdir = tempfile.mkdtemp()
Path(tmpdir, ".env").write_text(
    'CONTENT_ADMIN_SESSION_SECRET="t"\nDOMAIN="example.com"\n'
    'LLM_PROVIDER="anthropic"\nLLM_API_KEY="sk-t"\n'
    'EMBEDDING_PROVIDER="openai"\nEMBEDDING_API_KEY="sk-e"\n'
    'EMBEDDING_MODEL="text-embedding-3-small"\nEMBEDDING_BASE_URL=""\n'
    'WEAVIATE_API_KEY="wv"\nNEO4J_PASSWORD="n4j"\nCOURSE_NAME="K"\n'
    'WEAVIATE_COLLECTION_NAME="C"\n'
)
os.environ["SMARTRAG_ENV_PATH"] = str(Path(tmpdir, ".env"))
os.environ["SMARTRAG_SLOTS_PATH"] = str(Path(tmpdir, "slots.json"))
os.environ["SMARTRAG_TEMPLATES_DIR"] = str(TEMPLATES)
os.environ["CONTENT_ADMIN_SESSION_SECRET"] = "t"

import agent_templates as at  # noqa: E402

failures = []


def check(name, cond, detail=""):
    if not cond:
        failures.append(f"{name}: {detail}")


# Everything createCodeExecutionSandbox blanks out. Referencing any of these
# in template code throws at runtime, and the error names the property being
# read ("reading 'env'"), not the missing object — which is what made the
# original failure hard to place.
BLANKED = ("process", "util", "Symbol", "child_process", "fs")

for path in sorted(TEMPLATES.glob("*.json")):
    raw = path.read_text()
    flow = json.loads(raw)
    name = path.name

    for banned in BLANKED:
        hits = re.findall(rf"\b{banned}\s*\.", raw)
        check(f"{name}: no `{banned}.` in template code", not hits, f"{len(hits)} hit(s)")

    # Every secret must come from $vars, the accessor Flowise actually
    # populates (sandbox['$vars'] = prepareSandboxVars(variables)).
    secrets = set(re.findall(r"\$vars\?\.([A-Z0-9_]+)", raw))
    check(f"{name}: reads its secrets from $vars", secrets, "none found")

    # Anything read from $vars must be a variable the import creates, or it
    # will silently be undefined at runtime.
    IMPORT_CREATES = {
        "EMBEDDING_API_KEY", "EMBEDDING_BASE_URL", "EMBEDDING_MODEL",
        "WEAVIATE_API_KEY", "NEO4J_PASSWORD",
    }
    unknown = secrets - IMPORT_CREATES
    check(f"{name}: every $vars name is one the import creates", not unknown, sorted(unknown))

    # A falsy $vars value must reach a literal default, not another lookup.
    # This is the actual bug: `|| process.env.X || 'default'` never got to
    # the default because the middle term threw first.
    for var in secrets:
        pattern = re.compile(
            r"\$vars\?\." + var + r"\s*\|\|\s*([^;\\\n]{0,40})"
        )
        for tail in pattern.findall(raw):
            tail = tail.strip()
            check(f"{name}: {var} falls back to a literal",
                  tail.startswith("'") or tail.startswith('"'),
                  f"falls back to {tail!r}")


# ── The chain behaves correctly when a variable is empty ────────────────────
# EMBEDDING_BASE_URL is empty in every deployment using a standard provider,
# so this is the normal path, not a corner case. Evaluated the way the
# sandbox would: $vars present but empty, `process` undefined.
def evaluate(expr, variables):
    """Mimics `a || b || c` over the template's own fallback chain."""
    for term in [t.strip() for t in expr.split("||")]:
        if term.startswith(("'", '"')):
            return term.strip("'\"")
        key = term.replace("$vars?.", "")
        if term.startswith("process."):
            raise TypeError("Cannot read properties of undefined (reading 'env')")
        if variables.get(key):
            return variables[key]
    return ""


VARS = {"EMBEDDING_API_KEY": "sk-e", "EMBEDDING_MODEL": "text-embedding-3-small",
        "EMBEDDING_BASE_URL": "", "WEAVIATE_API_KEY": "wv", "NEO4J_PASSWORD": "n4j"}

for path in sorted(TEMPLATES.glob("*.json")):
    raw = path.read_text()
    for chain in re.findall(r"(\$vars\?\.[A-Z0-9_]+(?:\s*\|\|\s*[^;\\\n]+?)?)(?=;|\\n)", raw):
        try:
            evaluate(chain, VARS)
        except TypeError as exc:
            failures.append(f"{path.name}: chain {chain!r} throws — {exc}")

# The empty one must resolve to the documented default, not to "".
base_url_chains = [
    c for p in TEMPLATES.glob("*.json")
    for c in re.findall(r"\$vars\?\.EMBEDDING_BASE_URL\s*\|\|\s*'[^']+'", p.read_text())
]
check("EMBEDDING_BASE_URL has a real default", base_url_chains, "no default found")
for chain in base_url_chains:
    check("empty EMBEDDING_BASE_URL resolves to the default",
          evaluate(chain, VARS).startswith("http"), evaluate(chain, VARS))

# ── The import still produces valid, loadable templates ─────────────────────
for arch in at.ARCHETYPES:
    flow = at.load_template(arch)
    check(f"{arch}: still loads", isinstance(flow, dict) and flow.get("nodes"))

if failures:
    print("FAILURES:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print(
    "All sandbox-code checks passed: no template references process, util, Symbol, "
    "child_process or fs — all of which Flowise's createCodeExecutionSandbox sets to "
    "undefined, so touching them throws at the first message; every secret is read via "
    "$vars, every $vars name is one the import actually creates as a Flowise Variable, and "
    "every fallback chain ends in a literal so a legitimately empty variable "
    "(EMBEDDING_BASE_URL, empty for every standard provider) reaches its default instead "
    "of throwing."
)
