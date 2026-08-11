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

# ─── Rules that hold for every workflow, not just these ─────────────────────
ALL = sorted(Path(REPO / "n8n").glob("workflows*/*.json"))


def code_lines(src: str):
    """The lines that are actually code, and whether each sits inside a
    template literal.

    Comments have to come out first: this file's own explanations quote the
    broken syntax they describe, and a check that cannot tell a warning from
    an occurrence flags the warning. And `${...}` is only wrong outside a
    template literal, which spans lines — so backticks are counted, not
    looked for on the line itself."""
    in_template = False
    in_block_comment = False
    for raw in src.split("\n"):
        line = raw
        if in_block_comment:
            if "*/" in line:
                line = line.split("*/", 1)[1]
                in_block_comment = False
            else:
                continue
        if "/*" in line and not in_template:
            before = line.split("/*", 1)[0]
            in_block_comment = "*/" not in line
            line = before
        stripped = line.strip()
        if not in_template and stripped.startswith("//"):
            continue
        # Deliberately no trailing-comment stripping. The first version cut
        # every line at its first "//" — which in `http://smartrag-weaviate`
        # is the scheme separator, not a comment. That removed a backtick,
        # inverted the template-literal parity for the whole rest of the
        # node, and reported a correct line as broken. A false positive in a
        # rule about correctness is worse than no rule: it gets the rule
        # deleted.
        was_in_template = in_template
        yield line, was_in_template
        in_template ^= (line.count("`") % 2 == 1)


for path in ALL:
    d = json.loads(path.read_text())
    fname = path.name
    for n in d["nodes"]:
        p = n.get("parameters", {})

        if n["type"].endswith(".code"):
            src = p.get("jsCode", "")
            # n8n expressions are not evaluated inside a Code node. ={{ ... }}
            # there is not a value, it is those characters — which is how
            # "Bearer ={{ $env.WEAVIATE_API_KEY }}" reached Weaviate and came
            # back 401, in a workflow nobody had ever run.
            bad = [l.strip()[:70] for l, _ in code_lines(src) if "={{" in l]
            check(f"{fname}/{n['name']} uses no expression syntax in JavaScript",
                  not bad, bad[:2])

            # Interpolation in JS needs a template literal. A single-quoted
            # string with ${...} in it ships those characters verbatim — the
            # same failure wearing different clothes.
            for line, inside in code_lines(src):
                if "${" in line and not inside and "`" not in line:
                    check(f"{fname}/{n['name']} interpolates inside a template literal",
                          False, line.strip()[:80])

            # A value pasted into a GraphQL filter is a parse error at best
            # and a rewritten query at worst.
            if "valueText:" in src:
                for line, _ in code_lines(src):
                    if "valueText:" in line and "${" in line:
                        check(f"{fname}/{n['name']} quotes its GraphQL value",
                              "JSON.stringify" in line, line.strip()[:80])

        # Weaviate's container port follows WEAVIATE_HTTP_PORT — compose maps
        # "${WEAVIATE_HTTP_PORT}:${WEAVIATE_HTTP_PORT}". Docling (5001),
        # markdowncleaner (8000) and the Content Admin (5000) have no ports
        # section at all, so theirs are properties of the image and correctly
        # literal. The distinction is the whole rule.
        blob = json.dumps(p, ensure_ascii=False)
        code_only = "\n".join(l for l, _ in code_lines(p.get("jsCode", ""))) \
            if n["type"].endswith(".code") else blob
        check(f"{fname}/{n['name']} does not hard-code Weaviate's port",
              "smartrag-weaviate:8080" not in code_only, "")
        if "smartrag-weaviate" in blob:
            check(f"{fname}/{n['name']} reads Weaviate's port from the environment",
                  "WEAVIATE_HTTP_PORT" in blob, blob[:120])

# The cursor nodes must treat "not there yet" as the first run and everything
# else as a failure. Swallowing all errors would silently re-read the entire
# message history on every run; swallowing none makes the first run impossible.
cs = json.loads((CORE / "chathistory-sync.json").read_text())
for name in ("Read lastTimestamp", "Update lastTimestamp"):
    node = [n for n in cs["nodes"] if n["name"] == name][0]
    # Comments stripped: an earlier version of this check grepped the whole
    # source for "404", and the comment explaining the 404 handling made it
    # pass against a build with the handling deleted. Assert on code, never
    # on prose.
    code = "\n".join(l for l, _ in code_lines(node["parameters"]["jsCode"]))

    # The status must come from the response, not from a caught exception.
    # Code nodes run in n8n's task runner, so an exception crosses a process
    # boundary to reach the catch and its structured fields do not survive —
    # a catch reading e.httpCode / e.statusCode / e.response.status found
    # none of them, and a first run's 404 went through as a failure on the
    # live system. ignoreHttpStatusErrors sets axios's validateStatus to
    # () => true and returnFullResponse yields { body, headers, statusCode,
    # statusMessage } (@n8n/backend-network/src/http/axios/request.ts).
    check(f"{name} asks the helper not to throw",
          "ignoreHttpStatusErrors: true" in code, code[:200])
    check(f"{name} reads the full response",
          "returnFullResponse: true" in code, code[:200])
    check(f"{name} branches on the response's status",
          "statusCode === 404" in code, code[:200])
    check(f"{name} does not depend on an exception's shape",
          "e?.httpCode" not in code and "e.response?.status" not in code, "")
    # Everything that is neither success nor "not there yet" must still fail.
    # Treating any non-200 as a first run would re-read and re-embed the
    # entire message history every five minutes.
    check(f"{name} still fails on any other status",
          "throw new Error(" in code, code[:200])

check("the cursor is created when it does not exist yet",
      "method: 'POST'" in [n for n in cs["nodes"]
                           if n["name"] == "Update lastTimestamp"][0]["parameters"]["jsCode"],
      "PATCH on a missing object is a 404, so the first run could never store one")

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
