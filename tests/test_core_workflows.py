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
    "error-handler.json": "smartrag-error-handler",
    "watchdog.json": "smartrag-watchdog",
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

# ─── The Langfuse patcher is gone, and must stay gone ───────────────────────
# It existed to write a userId onto traces that arrived without one. Both of
# the cases it could meet make it wrong now: launched through the LTI
# middleware, Flowise already sets the userId, so there is nothing to patch;
# launched without it, the only id available is Flowise's own chat id, which
# the embed keeps in a browser's local storage — writing that into a field
# called userId turns a browser into a person, which is precisely the
# confusion that makes an erasure request unanswerable.
check("the Langfuse userId patcher is not back",
      not (REPO / "n8n/workflows/langfuse-userid-patch.json").exists(), "")
for name, text in (("the deployer", DEPLOYER),
                   ("the watchdog", (REPO / "n8n/workflows/watchdog.json").read_text())):
    check(f"{name} no longer references it",
          "langfuse-userid-patch" not in text, "")
# The n8n credential existed for that workflow alone. Shipping it now would
# leave the Langfuse secret key sitting in n8n with nothing using it.
check("its Langfuse credential is not created either",
      "smartrag-langfuse-credential" not in DEPLOYER, "")

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

# ─── The expression marker belongs at the front of a parameter ──────────────
# The mirror image of the Code-node rule above, and it cost a second live
# debugging round. In a node parameter, "=" marks the WHOLE value as an
# expression, so it has to be the first character: "=Bearer {{ $env.KEY }}".
# Written as "Bearer ={{ $env.KEY }}" the "=" is just a character, the value
# is a plain string, and the service receives the literal text — the same
# 29 characters that came back 401 from the Code node, arriving by the
# opposite mistake. Six headers were written that way.
def strings_in(o, path=""):
    if isinstance(o, dict):
        for k, v in o.items():
            yield from strings_in(v, f"{path}.{k}")
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield from strings_in(v, f"{path}[{i}]")
    elif isinstance(o, str):
        yield path, o


for path in ALL:
    d = json.loads(path.read_text())
    for n in d["nodes"]:
        if n["type"].endswith(".code"):
            continue    # there {{ }} is wrong in any position, checked above
        for where, value in strings_in(n.get("parameters", {})):
            if "{{" not in value:
                continue
            check(f"{path.name}/{n['name']}{where} marks its expression at the start",
                  value.startswith("="), value[:80])
            # And exactly once: "=Bearer ={{ ... }}" is the same bug repaired
            # carelessly.
            check(f"{path.name}/{n['name']}{where} has no stray marker",
                  "={{" not in value[1:], value[:80])
            # Nor nested. A URL was written as
            #   ={{ 'http://host:{{ $env.PORT }}/objects/' + $json.id }}
            # where the inner braces sit inside a JavaScript string of the
            # outer expression: they are never evaluated, and the port went
            # into the URL as the literal text "{{ $env.PORT }}". The stray-
            # marker check above misses it, because the inner one is preceded
            # by a colon rather than an "=".
            depth = 0
            nested = False
            i = 0
            while i < len(value) - 1:
                pair = value[i:i + 2]
                if pair == "{{":
                    if depth:
                        nested = True
                    depth += 1
                    i += 2
                    continue
                if pair == "}}" and depth:
                    depth -= 1
                    i += 2
                    continue
                i += 1
            check(f"{path.name}/{n['name']}{where} has no nested expression",
                  not nested, value[:120])

# ─── The chat history belongs to the course it came from ────────────────────
# It used to be stamped with $env.COURSE_ID — the installation's one course —
# so with several courses one course's conversations were filed under
# another, every five minutes, silently. The mapping exists in the database
# (a chatflow belongs to one slot, and a slot to one course); the workflow
# now reads it instead of assuming.
cs = loaded.get("chathistory-sync.json", {})
cs_nodes = {n["name"]: n for n in cs.get("nodes", [])}
lookup = "Look up the course per chatflow"
check("the course is looked up", lookup in cs_nodes, sorted(cs_nodes))
if lookup in cs_nodes:
    q = cs_nodes[lookup]["parameters"].get("query", "")
    check("…from the slot table", "agent_slots" in q and "chatflow_id" in q, q)
    check("…against the Content Admin's database, not Flowise's",
          cs_nodes[lookup].get("credentials", {}).get("postgres", {}).get("name")
          == "smartrag-contentadmin",
          cs_nodes[lookup].get("credentials"))
    # A node name with an apostrophe cannot be referenced from a
    # single-quoted JS string — the first version was called "Look up each
    # chatflow's course" and broke the code that referenced it.
    check("…with a name a Code node can reference",
          "'" not in lookup, lookup)

prep = cs_nodes.get("Prepare messages", {}).get("parameters", {}).get("jsCode", "")
check("preparation reads the mapping", f"$('{lookup}')" in prep, prep[:120])
check("a message whose chatflow is unknown is skipped, not guessed",
      "continue" in prep and "unplaceable" in prep, "")

writer = cs_nodes.get("Write to Weaviate ChatHistory", {})
body = writer.get("parameters", {}).get("jsonBody", "")
check("the written course comes from the message",
      '"course_id": $json.course_id' in body, body[:160])
check("…and no longer from the environment",
      "$env.COURSE_ID" not in body, body[:160])
# These two checks are the ends of the chain, and both were green while the
# middle dropped the field: "Prepare messages" looked the course up, skipped
# on it, and left it out of the object it emitted, so every message was
# written with course_id null. Reading node text cannot see that — it would
# have to know which fields each node passes on. tests/test_chathistory_chain.sh
# runs these Code nodes instead and asserts on what reaches the writer.
check("the chain itself is exercised, not only its two ends",
      (REPO / "tests" / "test_chathistory_chain.sh").exists(),
      "nothing checks what the nodes between them carry")

# ─── Somebody has to be told when a workflow stops ───────────────────────────
# usermemory-summary failed on every scheduled run for at least two days —
# ten red executions — and was found only because its output was being
# examined for another reason. Nothing in this system reported it.
eh = loaded.get("error-handler.json", {})
eh_nodes = {n["name"]: n for n in eh.get("nodes", [])}
check("an error workflow exists",
      any(n["type"].endswith("errorTrigger") for n in eh.get("nodes", [])),
      sorted(eh_nodes))

# n8n calls an error workflow; it never triggers itself. Activating it would
# suggest it runs, and someone asking "why no mail" would start in the wrong
# place.
check("the error handler is deployed", "error-handler.json" in DEPLOYER, "")
check("…and not activated", '"smartrag-error-handler"' not in
      DEPLOYER.split("ACTIVATE_IDS=(")[1].split(")")[0], "")
check("the watchdog is activated", '"smartrag-watchdog"' in
      DEPLOYER.split("ACTIVATE_IDS=(")[1].split(")")[0], "")

# Naming the error workflow is per workflow in n8n, and a new workflow that
# forgets to is silent in exactly the way this whole change is about. It lives
# in the file rather than being stamped on at deploy time, so the file says
# what will happen — and this check is what stops the next one forgetting.
for path in ALL:
    d = json.loads(path.read_text())
    if d.get("id") == "smartrag-error-handler":
        continue
    check(f"{path.name} names the error workflow",
          d.get("settings", {}).get("errorWorkflow") == "smartrag-error-handler",
          d.get("settings"))

# ─── An alert that arrives 288 times a day is not an alert ───────────────────
# chathistory-sync runs every five minutes. Without throttling, one broken run
# is 288 mails a day, and after the second day nobody reads any of them.
for fname in ("error-handler.json", "watchdog.json"):
    d = loaded.get(fname, {})
    code = " ".join(n.get("parameters", {}).get("jsCode", "")
                    for n in d.get("nodes", []))
    check(f"{fname} throttles", "ALERT_QUIET_MINUTES" in code, "")
    check(f"{fname} stays silent by producing nothing",
          "return [];" in code, "a mail node downstream would still send")
    check(f"{fname} counts what it suppressed",
          "suppressed" in code, "the next mail must say how many it stands for")
    # The state has to survive the execution, or every run is the first.
    check(f"{fname} keeps its state outside the run",
          "WorkflowState" in code, "")
    # An installation without a mail relay is the normal state of a test
    # machine and a real possibility on a production one. Sending anyway
    # fails the run, and a red run is what nobody looks at — which is the
    # problem this whole channel exists to solve. The report goes to the log
    # and the execution stays green.
    check(f"{fname} logs instead of failing when there is no mail relay",
          "$env.SMTP_HOST" in code, "the send would fail and redden the run")

# ─── The address is configuration, not a name in a file ──────────────────────
# The vhb installation's version had one person's address and one provider's
# credential compiled into the nodes.
for fname in ("error-handler.json", "watchdog.json"):
    blob = json.dumps(loaded.get(fname, {}), ensure_ascii=False)
    check(f"{fname} sends to the configured address",
          "$env.ADMIN_EMAIL" in blob, "")
    check(f"{fname} has no address written into it",
          not re.search(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", blob, re.I),
          re.findall(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", blob, re.I))

# A failure from an HTTP node carries the request body, and here that body is
# a learner's message. The mail says where to look instead of quoting it.
error_blob = json.dumps(loaded.get("error-handler.json", {}), ensure_ascii=False)
# ".stack", not "stack": the node's own comment explains why the stack is
# left out, and a check that cannot tell an explanation from an occurrence
# flags the explanation.
check("the alert does not mail the stack trace",
      ".stack" not in error_blob, "")
check("…and caps the message it does include",
      ".slice(0, 300)" in error_blob, "")

# ─── The watchdog watches something that exists ──────────────────────────────
# The vhb watchdog has an entry reading workflowId: 'BITTE_PRUEFEN'. That id
# matches nothing, so it reports "no successful execution found" every hour,
# for ever — which is how a watchdog stops being read.
wd = loaded.get("watchdog.json", {})
wd_code = " ".join(n.get("parameters", {}).get("jsCode", "")
                   for n in wd.get("nodes", []))
watched = set(re.findall(r"id:\s*'(smartrag-[a-z0-9-]+)'", wd_code))
known = {json.loads(p.read_text()).get("id") for p in ALL}
check("the watchdog watches at least the two scheduled workflows",
      {"smartrag-chathistory-sync", "smartrag-usermemory-summary"} <= watched,
      watched)
for wid in sorted(watched):
    check(f"the watchdog's {wid} is a workflow that exists", wid in known,
          sorted(known))

# The failure that has no error: green executions that write nothing. Neither
# an error trigger nor a liveness check sees it, and it is what chathistory-sync
# did for hours on 2026-08-13.
check("the watchdog also checks that work is actually being done",
      "Settled messages" in wd_code and "Aggregate" in wd_code,
      "a workflow can succeed and do nothing")
check("…and reads n8n's own database rather than its API",
      "smartrag-n8ndb" in json.dumps(wd, ensure_ascii=False),
      "an API key would be a credential with full access, for two counts")

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

# Referenced by id, not only by name. n8n links credentials by id; a
# name-only reference falls back to a lookup by type, and with two Postgres
# credentials in the same workflow it bound to the wrong one — the course
# lookup ran against Flowise's database and failed with "relation
# agent_slots does not exist", while the same query worked by hand.
deployer_ids = dict(re.findall(
    r'"id":\s*"([^"]+)",\s*\n\s*"name":\s*"([^"]+)"', DEPLOYER))
name_to_id = {n: i for i, n in deployer_ids.items()}
for path in ALL:
    d = json.loads(path.read_text())
    for n in d["nodes"]:
        for typ, cred in (n.get("credentials") or {}).items():
            label = f"{path.name}/{n['name']}"
            check(f"{label} references its credential by id",
                  bool(cred.get("id")), cred)
            check(f"{label}'s credential id is the one the deployer creates",
                  cred.get("id") == name_to_id.get(cred.get("name")),
                  f"{cred} vs {name_to_id.get(cred.get('name'))}")

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
# Both remaining workflows copy what learners wrote, keyed to their
# pseudonym. Whatever is decided about that, the file describing them has to
# say so — it cannot be seen from a workflow called "chathistory-sync", and
# whoever reads this file is deciding what to run. The check outlived the
# workflow it was written for: it originally guarded the Langfuse patcher's
# row, and the concern was never that workflow's alone.
check("the README says which workflows handle personal data",
      readme.lower().count("personal data") >= 2, "")

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
    "README no longer describes an import that never happened. And a failure "
    "now reaches a person: every workflow names the error handler, which "
    "mails the configured address rather than one written into a file and "
    "quotes no learner text; both alert paths go quiet after the first mail "
    "and count what they suppressed, so one broken five-minute schedule is "
    "not 288 mails a day; and an hourly watchdog covers what neither can see "
    "— a workflow switched off, one that stopped being triggered, and one "
    "that reports success while writing nothing — watching only ids that "
    "exist."
)
