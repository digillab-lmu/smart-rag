"""
The three workflows that keep the system stateful — and were never deployed.

`n8n/workflows/` holds the memory and observability pipelines. Its README said
bootstrap imported them automatically. Nothing did: the deployer read
`workflows-ingest/` only. They were documented, present, and dead, which is
worse than absent — absent invites building, documented-and-dead invites
relying.

Deploying them turned up four more things, and each is what this file guards:

  * none had a workflow id, so every import would have created another copy,
    and a duplicate of a five-minute schedule runs twice;
  * the summariser was pinned to Anthropic — an `anthropic` node with a
    hard-coded model and a parser reading `content[0].text` — in a project
    whose whole LLM layer is provider-agnostic. On this installation, which
    uses OpenAI, it could not have run at all;
  * its system prompt contained the literal `{{COURSE_NAME}}`, a Content
    Admin placeholder n8n does not substitute, so the model would have been
    told the course was called "{{COURSE_NAME}}";
  * the Langfuse patcher hard-coded port 3001 and built SQL by pasting a
    value from Langfuse into the query string.

Three credentials also had to exist that the deployer never created.
"""

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CORE = REPO / "n8n" / "workflows"
DEPLOYER = (REPO / "scripts" / "deploy-n8n-workflows.sh").read_text()

failures = []


def check(name, ok, detail=""):
    if not ok:
        failures.append(f"{name}: {detail}")


FLOWS = {
    "chathistory-sync.json": "smartrag-chathistory-sync",
    "usermemory-summary.json": "smartrag-usermemory-summary",
    "langfuse-userid-patch.json": "smartrag-langfuse-userid-patch",
}

loaded = {}
for fname, wid in FLOWS.items():
    path = CORE / fname
    check(f"{fname} exists", path.exists())
    if not path.exists():
        continue
    d = json.loads(path.read_text())
    loaded[fname] = d

    # ─── An id, or every import leaves another copy ──────────────────────────
    check(f"{fname} has its fixed id", d.get("id") == wid, d.get("id"))
    # Imported inactive regardless; a true here only misleads the reader.
    check(f"{fname} does not claim to be active", d.get("active") is False,
          d.get("active"))

    # ─── Deployed at all ─────────────────────────────────────────────────────
    check(f"{fname} is imported by the deployer", fname in DEPLOYER)

# Every id that gets activated must belong to a workflow that gets imported —
# activating an id that was never imported fails the whole phase.
activated = set(re.findall(r'"(smartrag-[a-z0-9-]+)"', DEPLOYER))
for fname, wid in FLOWS.items():
    if wid in activated:
        check(f"{wid} is activated and imported", fname in DEPLOYER)

# ─── The summariser must not be tied to one vendor ──────────────────────────
if "usermemory-summary.json" in loaded:
    d = loaded["usermemory-summary.json"]
    types = [n["type"] for n in d["nodes"]]
    check("no vendor-specific LLM node remains",
          not any("anthropic" in t.lower() or "openAi" in t for t in types),
          [t for t in types if "anthropic" in t.lower() or "openAi" in t])

    llm = [n for n in d["nodes"] if n["name"] == "LLM: summarise session"]
    check("the summarising node is still there", len(llm) == 1)
    if llm:
        p = llm[0]["parameters"]
        body = p.get("jsonBody", "")
        check("it calls the configured provider",
              "$env.LLM_BASE_URL" in p.get("url", ""), p.get("url"))
        check("with the configured model",
              "$env.LLM_MODEL_FAST" in body, body[:120])
        check("and the configured key",
              any("$env.LLM_API_KEY" in h.get("value", "")
                  for h in p.get("headerParameters", {}).get("parameters", [])),
              p.get("headerParameters"))
        check("no model is hard-coded",
              not re.search(r"claude-[a-z0-9.-]+|gpt-4o(?!-mini')", body),
              body[:200])

        # The expression is executed below, not just grepped — the point of
        # the exercise is that it produces valid JSON, and reading it cannot
        # establish that.
        check("the request body is an expression", body.startswith("={{"), body[:40])

    # A Content Admin placeholder never gets substituted by n8n.
    whole = json.dumps(d, ensure_ascii=False)
    check("no unsubstituted GUI placeholder reaches a prompt",
          "{{COURSE_NAME}}" not in whole and "{{COURSE_ID}}" not in whole,
          re.findall(r"\{\{[A-Z_]+\}\}", whole)[:3])

    # The parser must read the shape the new call returns.
    parse = [n for n in d["nodes"] if n["name"] == "Parse and Merge"]
    if parse:
        code = parse[0]["parameters"]["jsCode"]
        check("the response is parsed in the OpenAI-compatible shape",
              "choices?.[0]?.message?.content" in code
              or "choices[0].message.content" in code, code[:200])
        check("Anthropic's shape is no longer read",
              "content?.[0]?.text" not in code, "")

# ─── The Langfuse patcher ───────────────────────────────────────────────────
if "langfuse-userid-patch.json" in loaded:
    d = loaded["langfuse-userid-patch.json"]
    whole = json.dumps(d)
    # Langfuse's container port follows LANGFUSE_PORT — unlike the Content
    # Admin's, which is fixed by its image. Hard-coding it is the same class
    # of bug as the ingest callback that went to the host's port.
    check("Langfuse's port comes from the environment",
          "$env.LANGFUSE_PORT" in whole, "")
    check("…and is not hard-coded",
          "smartrag-langfuse-web:3001" not in whole, "")

    for n in d["nodes"]:
        if n["type"].endswith("postgres"):
            q = n["parameters"].get("query", "")
            # The value comes from Langfuse's own data. Pasting it into the
            # query string is how a stored value becomes a statement.
            check(f"{n['name']} parameterises its query",
                  "{{" not in q, q[:120])
            check(f"{n['name']} passes the value separately",
                  "queryReplacement" in json.dumps(n["parameters"]),
                  n["parameters"].get("options"))

    # It only ships where Langfuse runs: an always-failing schedule every 30
    # minutes teaches people to ignore the execution list.
    check("the patcher is tied to the observability profile",
          "LANGFUSE_ENABLED" in DEPLOYER
          and "observability" in DEPLOYER, "")

# ─── Credentials the deployer must create ───────────────────────────────────
# Every credential referenced by a workflow has to be one the deployer
# creates, or the workflow imports and fails at run time with a message about
# a missing credential — which reads like a broken workflow, not a missing
# install step.
referenced = set()
for d in loaded.values():
    for n in d["nodes"]:
        for cred in (n.get("credentials") or {}).values():
            referenced.add(cred["name"])
for fname in ("ingest-document.json", "ingest-chunk-and-embed.json"):
    d = json.loads((REPO / "n8n" / "workflows-ingest" / fname).read_text())
    for n in d["nodes"]:
        for cred in (n.get("credentials") or {}).values():
            referenced.add(cred["name"])

for name in sorted(referenced):
    check(f"the deployer creates the credential {name}",
          f'"name": "{name}"' in DEPLOYER or f'"{name}"' in DEPLOYER,
          "referenced by a workflow, never provisioned")

# The Postgres credential must point at Flowise's database — that is where
# chat_message lives.
check("the Postgres credential names Flowise's database",
      '"database": "flowise"' in DEPLOYER, "")
# And at the credentials the rest of the stack uses, not new ones.
check("it uses the installation's Postgres user",
      "$pg_user" in DEPLOYER and "POSTGRES_USER" in DEPLOYER, "")
# Langfuse's API takes the project key pair as basic auth. The names matter:
# LANGFUSE_PUBLIC_KEY does not exist in this project, and reaching for it
# yields an empty credential and a 401 that looks like a Langfuse problem.
check("the Langfuse credential uses the project keys",
      "LANGFUSE_INIT_PROJECT_PUBLIC_KEY" in DEPLOYER
      and "LANGFUSE_INIT_PROJECT_SECRET_KEY" in DEPLOYER, "")

# ─── Every $env a core workflow reads must exist ────────────────────────────
env_example = (REPO / ".env.example").read_text()
for fname, d in loaded.items():
    for var in sorted(set(re.findall(r"\$env\.([A-Z_]+)", json.dumps(d)))):
        check(f"{fname} reads {var}, which .env.example declares",
              re.search(rf"^{var}=", env_example, re.M) is not None, var)

# ─── The README must not describe a system that does not exist ──────────────
readme = (CORE / "README.md").read_text()
check("the README no longer claims bootstrap imports these",
      "imported automatically by `scripts/bootstrap.sh`" not in readme, "")
check("the README describes the summariser's actual call",
      "$env.LLM_MODEL_FAST" in readme, "")
# The patcher writes a learner's name into Langfuse. Whatever is decided about
# that, the file that describes it must say so — a reader deciding whether to
# enable observability cannot see it from the workflow name.
check("the README says the patcher handles personal data",
      "personal data" in readme.lower(), "")

if failures:
    print("FAILURES:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print(
    "All core-workflow checks passed: each of the three carries a fixed id so "
    "a re-import updates rather than duplicating a schedule, each is actually "
    "imported by the deployer, the summariser calls the configured provider "
    "with the configured model and parses the shape it returns, no GUI "
    "placeholder reaches a prompt, Langfuse's port comes from the environment "
    "and its SQL takes the value as a parameter, the trace patcher ships only "
    "with the observability profile, every credential a workflow references "
    "is one the deployer creates, every $env it reads is declared, and the "
    "README no longer describes an import that never happened."
)
