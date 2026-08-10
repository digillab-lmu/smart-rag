"""
Agent templates must carry the node versions the pinned Flowise ships.

Flowise marks a node whose stored version is older than the current
definition as outdated, and shows a badge on it. Ours said "Node version 1.1
outdated — update to latest version 1.4" on the Start node of every agent, in
an installation that was otherwise correct.

The badge is only a badge: the Start node has no version-conditional code
(packages/components/nodes/agentflow/Start/Start.ts declares this.version and
never reads nodeData.version), so behaviour comes from the current code either
way. That makes it cosmetic and worth fixing precisely because it is: a
warning that is always there, on every agent, is how people learn to skim past
warnings — the same reason the unused pdf-lib allowlist entry was removed.

The table below is the contract with a specific Flowise release. Bumping the
image without updating it is exactly the moment this should fail.
"""

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Read from packages/components/nodes/agentflow/<Node>/<Node>.ts at the tag
# the compose file pins — `this.version = …` in each node's constructor.
PINNED_FLOWISE = "3.1.3"
EXPECTED = {
    "startAgentflow": 1.4,
    "agentAgentflow": 3.2,
    "llmAgentflow": 1.1,
    "customFunctionAgentflow": 1.1,
}

failures = []


def check(name, ok, detail=""):
    if not ok:
        failures.append(f"{name}: {detail}")


# The table is worthless if it describes a different Flowise than we run.
compose = (REPO / "docker" / "docker-compose.yml").read_text()
m = re.search(r"image: flowiseai/flowise:([0-9.]+)", compose)
check("the pinned Flowise version could be read", m is not None, "")
if m:
    check(
        f"this table still describes the pinned Flowise ({PINNED_FLOWISE})",
        m.group(1) == PINNED_FLOWISE,
        f"compose pins {m.group(1)} — re-read the node versions from that tag "
        f"and update EXPECTED, rather than editing this string",
    )

templates = sorted((REPO / "flowise" / "agents").glob("*.json"))
check("templates were found", len(templates) >= 6, f"{len(templates)} files")

seen: dict[str, set] = {}
for path in templates:
    data = json.loads(path.read_text())
    for node in data.get("nodes", []):
        nd = node.get("data", {})
        name, version = nd.get("name"), nd.get("version")
        if name is None or version is None:
            continue
        seen.setdefault(name, set()).add(version)
        if name in EXPECTED:
            check(
                f"{path.name}: {name} is at the shipped version",
                version == EXPECTED[name],
                f"template has {version}, Flowise {PINNED_FLOWISE} ships "
                f"{EXPECTED[name]} — Flowise will mark it outdated",
            )

# A node type nobody listed is a node type nobody checked.
for name in sorted(seen):
    check(f"{name} is covered by the table", name in EXPECTED,
          f"used in a template at {sorted(seen[name])} and unverified here")

# One version per node type across all templates: two agents carrying
# different versions of the same node means one of them was edited by hand.
for name, versions in sorted(seen.items()):
    check(f"{name} has a single version across templates", len(versions) == 1,
          f"{sorted(versions)}")

if failures:
    print("FAILURES:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print(
    f"All node-version checks passed: every agent template carries the node "
    f"versions Flowise {PINNED_FLOWISE} ships, every node type used is covered "
    f"by the table, no two templates disagree about a version, and the table "
    f"is tied to the pinned image so an upgrade has to revisit it."
)
