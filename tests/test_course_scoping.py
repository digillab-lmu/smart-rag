"""One installation, several courses: everything an agent retrieves must be
scoped to a course.

Two failure modes this guards against, and they are not symmetric. A filter
that is too narrow retrieves nothing — annoying, and immediately obvious. A
filter that is too wide serves one course's material to another course's
students, which nobody notices. So the checks below are written to fail
loudly on the second.

The filter format is not guessed. weaviate-client's own types (used by
@langchain/weaviate, which Flowise's Weaviate node builds on) define:

    FilterValue = { operator, target?, value, filters?: FilterValue[] }

and Filters.and() produces `{operator: 'And', filters: [...], value: null}` —
`filters`, not `operands`, with an explicit null value. Verified in
weaviate/typescript-client, src/collections/filters/{types,classes}.ts.

The value's JSON type decides the wire encoding: agent_id is an int property,
so its filter value must substitute to a bare number. A quoted placeholder
would produce a string, be encoded as valueText, and match nothing at all —
the same trap that made an earlier retrieval bug hard to see.
"""
import json
import os
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
APP_DIR = str(REPO / "content-admin")
TEMPLATES = REPO / "flowise" / "agents"
sys.path.insert(0, APP_DIR)

tmpdir = tempfile.mkdtemp()
Path(tmpdir, ".env").write_text(
    'CONTENT_ADMIN_SESSION_SECRET="t"\nDOMAIN="example.com"\n'
    'COURSE_ID="medienerziehung"\nCOURSE_NAME="Einführung"\n'
    'WEAVIATE_COLLECTION_NAME="SmartRagChunks"\n'
    'LLM_PROVIDER="anthropic"\nLLM_API_KEY="sk-t"\n'
    'EMBEDDING_PROVIDER="openai"\nEMBEDDING_API_KEY="sk-e"\n'
    'EMBEDDING_MODEL="text-embedding-3-small"\nWEAVIATE_API_KEY="wv"\n'
)
os.environ["SMARTRAG_ENV_PATH"] = str(Path(tmpdir, ".env"))
os.environ["SMARTRAG_SLOTS_PATH"] = str(Path(tmpdir, "slots.json"))
os.environ["SMARTRAG_TEMPLATES_DIR"] = str(TEMPLATES)
os.environ["CONTENT_ADMIN_SESSION_SECRET"] = "t"

import agent_templates as at  # noqa: E402

failures = []
COURSE = "medienerziehung"


def check(name, cond, detail=""):
    if not cond:
        failures.append(f"{name}: {detail}")


def vector_stores(flow):
    for node in flow.get("nodes", []):
        for vs in node.get("data", {}).get("inputs", {}).get("agentKnowledgeVSEmbeddings", []) or []:
            cfg = vs.get("vectorStoreConfig")
            if isinstance(cfg, dict):
                yield cfg


def properties_filtered(filt):
    """Every property name the filter constrains, at any nesting depth."""
    found = set()

    def walk(node):
        if not isinstance(node, dict):
            return
        target = node.get("target")
        if isinstance(target, dict) and target.get("property"):
            found.add(target["property"])
        for sub in node.get("filters") or []:
            walk(sub)

    walk(filt)
    return found


# ── The Weaviate schema carries the course ──────────────────────────────────
schema = json.loads((REPO / "weaviate" / "schema.json").read_text())
classes = schema if isinstance(schema, list) else schema.get("classes", [schema])
by_name = {c["class"]: c for c in classes}

# Everything an agent reads per student or per course needs it. WorkflowState
# is installation-wide bookkeeping and deliberately does not.
for cls in ("__COLLECTION_NAME__", "ChatHistory", "UserMemory", "TestResults"):
    check(f"schema: {cls} exists", cls in by_name, sorted(by_name))
    if cls not in by_name:
        continue
    props = {p["name"]: p for p in by_name[cls]["properties"]}
    check(f"schema: {cls} has course_id", "course_id" in props, sorted(props))
    if "course_id" in props:
        cid = props["course_id"]
        check(f"schema: {cls}.course_id is text", cid["dataType"] == ["text"], cid["dataType"])
        # Unfilterable would make the whole scheme decorative.
        check(f"schema: {cls}.course_id is filterable", cid.get("indexFilterable") is True, cid)

# agent_id stays an int — the filter's value type depends on it.
retrieval = by_name.get("__COLLECTION_NAME__", {})
agent_prop = next((p for p in retrieval.get("properties", []) if p["name"] == "agent_id"), None)
check("schema: agent_id is still int", agent_prop and agent_prop["dataType"] == ["int"], agent_prop)

# ── Every retrieving archetype filters by course ────────────────────────────
for path in sorted(TEMPLATES.glob("*.json")):
    flow = at.load_template(path.name)
    stores = list(vector_stores(flow))
    if not stores:
        # Not every archetype retrieves; the backup assistant deliberately
        # has no vector store at all, and nothing to scope.
        check(f"{path.name}: no vector store, so nothing to scope", True)
        continue

    for cfg in stores:
        raw = cfg.get("weaviateFilter") or ""
        check(f"{path.name}: has a filter at all", raw.strip(),
              "empty filter — this store would return every course's chunks")
        if not raw.strip():
            continue

        live = raw.replace("{{COURSE_ID}}", COURSE).replace("{{AGENT_NUMBER}}", "4")
        try:
            parsed = json.loads(live)
        except json.JSONDecodeError as exc:
            failures.append(f"{path.name}: filter isn't valid JSON once substituted — {exc}")
            continue

        props = properties_filtered(parsed)
        check(f"{path.name}: filters on course_id", "course_id" in props, sorted(props))

        # Structure, per weaviate-client's own definition.
        if parsed.get("operator") == "And":
            check(f"{path.name}: And uses `filters`", isinstance(parsed.get("filters"), list),
                  sorted(parsed))
            check(f"{path.name}: And has an explicit null value", parsed.get("value", "missing") is None,
                  parsed.get("value", "missing"))
            check(f"{path.name}: `operands` is not the key", "operands" not in parsed, sorted(parsed))

        # The value types that decide the wire encoding.
        def leaf_values(node, acc):
            if not isinstance(node, dict):
                return acc
            tgt = node.get("target")
            if isinstance(tgt, dict) and tgt.get("property"):
                acc[tgt["property"]] = node.get("value")
            for sub in node.get("filters") or []:
                leaf_values(sub, acc)
            return acc

        vals = leaf_values(parsed, {})
        check(f"{path.name}: course_id compares against a string",
              isinstance(vals.get("course_id"), str), repr(vals.get("course_id")))
        if "agent_id" in vals:
            check(f"{path.name}: agent_id compares against a number, not a string",
                  isinstance(vals["agent_id"], int) and not isinstance(vals["agent_id"], bool),
                  repr(vals["agent_id"]))

# ── The course reaches the filter through the import, not by hand ───────────
check("COURSE_ID is auto-filled, never asked for in the content form",
      "COURSE_ID" in at.AUTO_FILLED_FIELDS, sorted(at.AUTO_FILLED_FIELDS))

for path in sorted(TEMPLATES.glob("*.json")):
    flow = at.load_template(path.name)
    at.auto_fill_from_env(flow, {
        # The provider is required, not defaulted: it selects the Flowise node
        # type for the model, so a missing one would build the agent against
        # the wrong vendor's node.
        "LLM_PROVIDER": "anthropic", "EMBEDDING_PROVIDER": "openai",
        "COURSE_ID": COURSE, "COURSE_NAME": "Einführung",
        "WEAVIATE_COLLECTION_NAME": "SmartRagChunks",
        "EMBEDDING_MODEL": "text-embedding-3-small",
    }, slot=4)
    for cfg in vector_stores(flow):
        raw = cfg.get("weaviateFilter") or ""
        if not raw:
            continue
        check(f"{path.name}: no placeholder survives auto-fill",
              "{{" not in raw, raw[:120])
        parsed = json.loads(raw)
        vals = {}
        def collect(node):
            tgt = node.get("target")
            if isinstance(tgt, dict) and tgt.get("property"):
                vals[tgt["property"]] = node.get("value")
            for sub in node.get("filters") or []:
                collect(sub)
        collect(parsed)
        check(f"{path.name}: the real course id lands in the filter",
              vals.get("course_id") == COURSE, repr(vals.get("course_id")))

# An empty COURSE_ID must not silently produce a filter matching everything
# with an empty course — it should be visibly empty, not absent.
flow = at.load_template("agent-11-expert-feedback.json")
at.auto_fill_from_env(flow, {"LLM_PROVIDER": "anthropic",
                             "EMBEDDING_PROVIDER": "openai",
                             "COURSE_ID": "", "COURSE_NAME": "x",
                             "WEAVIATE_COLLECTION_NAME": "c", "EMBEDDING_MODEL": "m"}, slot=1)
for cfg in vector_stores(flow):
    raw = cfg.get("weaviateFilter") or ""
    if raw:
        check("an empty COURSE_ID still yields a course_id condition",
              "course_id" in raw, raw[:120])

# ── The ingest writes it ────────────────────────────────────────────────────
ingest = (REPO / "n8n" / "workflows-ingest" / "ingest-chunk-and-embed.json").read_text()
ingest_json = json.loads(ingest)
code_nodes = {n["name"]: n.get("parameters", {}).get("jsCode", "") for n in ingest_json["nodes"]}

writing = [n for n, c in code_nodes.items() if "course_id" in c]
check("the ingest sets course_id on chunks", writing, "no node writes course_id")
check("it comes from the environment, not a literal",
      any("$env.COURSE_ID" in c for c in code_nodes.values()),
      "no node reads $env.COURSE_ID")

# A chunk written without it is invisible to every agent, so the write path
# and the filter have to agree on the property name — a typo on either side
# is silent.
for name, code in code_nodes.items():
    if "agent_id:" in code:
        check(f"ingest/{name}: carries course_id alongside agent_id",
              "course_id" in code, "agent_id is set but course_id is not")

# ── The background workflows write it too ───────────────────────────────────
# This block used to require the bug. It read "takes it from the environment:
# $env.COURSE_ID is present" — the installation's single course, stamped on
# every row, which is exactly what stopped there being more than one. Worse,
# it kept passing after chathistory-sync was fixed, because a comment inside
# the workflow still contained the words. Prose is not behaviour, so the token
# may not appear anywhere in these files now, comments included.
for wf, cls in (("chathistory-sync.json", "ChatHistory"),
                ("usermemory-summary.json", "UserMemory")):
    blob = (REPO / "n8n" / "workflows" / wf).read_text()
    check(f"{wf}: writes course_id", "course_id" in blob,
          f"{cls} rows would be shared across courses")
    check(f"{wf}: does not take the course from the environment",
          "COURSE_ID" not in blob,
          "every row would carry the installation's single course")

# ── The learning record is per learner AND per course ───────────────────────
# A learner can be in several courses, and what they have mastered in one says
# nothing about another. The summary workflow used to loop over learners
# alone: one record per learner, both courses' concepts merged into it, and
# the course stamped from the environment. Now the loop runs over courses
# first, and over the learners inside each course.
um = json.loads((REPO / "n8n" / "workflows" / "usermemory-summary.json").read_text())
um_nodes = {n["name"]: n for n in um["nodes"]}


def um_code(node):
    return um_nodes.get(node, {}).get("parameters", {}).get("jsCode", "")


check("usermemory: the loop starts from the courses",
      "List courses" in um_nodes, sorted(um_nodes))
check("usermemory: learners are listed within one course",
      "Get users per course" in um_nodes, sorted(um_nodes))
check("usermemory: a course with no id is skipped rather than merged",
      "if (!course_id) continue" in um_code("List courses"), um_code("List courses")[:200])

extract = um_code("Extract user_ids")
check("usermemory: the record is looked up by learner and course",
      'path: ["user_id"]' in extract and 'path: ["course_id"]' in extract,
      "limit: 1 on the learner alone returns whichever course Weaviate finds first")

process = um_code("Process Memory")
check("usermemory: the chat history it summarises is the course's",
      'path: ["course_id"]' in process, process[:200])
# The cursor lives in the record, so it is per course as well. Shared, a
# learner active in one course would move the other course's cursor past
# messages that are then never summarised.
check("usermemory: the cursor comes from that course's record",
      "last_updated" in process and 'path: ["timestamp"]' in process, process[:200])

merge = um_code("Parse and Merge")
check("usermemory: the record is written with the loop's course",
      "course_id," in merge, merge[-400:])
check("usermemory: a record with no course is refused, not written empty",
      "no_course" in merge, merge[:400])

# ── And the agents read it within their own course ──────────────────────────
for path in sorted(TEMPLATES.glob("*.json")):
    flow = json.loads(path.read_text())
    for node in flow.get("nodes", []):
        code = (node.get("data", {}).get("inputs") or {}).get(
            "customFunctionJavascriptFunction") or ""
        if "Get { UserMemory" not in code:
            continue
        label = f"{path.name}/{node.get('data', {}).get('label', '?')}"
        check(f"{label}: reads the record of its own course",
              'path: ["course_id"]' in code,
              "another course's learning record would be read as this one's")
        check(f"{label}: takes the course from the import substitution",
              "{{COURSE_ID}}" in code, "")
        # The learner id comes out of the session and went into the query
        # unquoted. JSON.stringify is what the rest of this project uses for
        # the same reason: a value with a quote in it would rewrite the query.
        check(f"{label}: does not interpolate the learner id raw",
              '"${userId}"' not in code, code[:200])

# ── The migration exists and is honest about being needed ───────────────────
mig = REPO / "scripts" / "migrate-add-course-id.sh"
check("a migration script exists", mig.exists(), str(mig))
if mig.exists():
    text = mig.read_text()
    check("the migration supports a dry run", "--dry-run" in text)
    # deploy-schemas.sh skips existing classes on purpose, so an upgrade
    # would otherwise never gain the property.
    check("it adds the property to existing classes", "/properties" in text)
    check("it backfills existing objects", "PATCH" in text)
    check("it is idempotent about objects that already have one",
          "already had one" in text or "course_id // \"\"" in text)

schemas = (REPO / "scripts" / "deploy-schemas.sh").read_text()
check("deploy-schemas.sh still leaves existing classes alone",
      "exists" in schemas.lower(), "if this changed, re-check the migration's premise")

# ─── The ingest takes the course from the request, not the environment ──────
# Until this held, every upload landed in whichever course .env named — the
# selected course affected only what was displayed. It was found the way such
# things are found: a document uploaded into a new course arrived in the old
# one, and the new course's list correctly showed nothing.
import json as _json  # noqa: E402

doc = _json.load(open(REPO / "n8n" / "workflows-ingest" / "ingest-document.json"))
sub = _json.load(open(REPO / "n8n" / "workflows-ingest" / "ingest-chunk-and-embed.json"))
by_doc = {n["name"]: n for n in doc["nodes"]}
by_sub = {n["name"]: n for n in sub["nodes"]}

bucket_param = by_doc["Upload to object storage"]["parameters"]["bucketName"]
check("the bucket comes from the upload",
      "body.bucket" in bucket_param, bucket_param)

frontmatter = by_doc["Build Frontmatter"]["parameters"]["jsCode"]
for field in ("course_id", "collection", "bucket"):
    line = [l for l in frontmatter.split("\n")
            if l.strip().startswith(f"{field}:")]
    check(f"the metadata's {field} comes from the upload",
          line and "trigger." in line[0], line[:1])

chunking = by_sub["Chunking"]["parameters"]["jsCode"]
check("a chunk's course comes from the document's metadata",
      "meta.course_id" in chunking, "")

embed = by_sub["Embed + Write to Weaviate"]["parameters"]["jsCode"]
check("the collection written to comes from the document's metadata",
      "_meta.collection" in embed, "")
# The metadata is not on the item by then — Chunking emits chunk properties —
# so it has to be read from the trigger. Getting this wrong is silent: the
# fallback is the environment, which writes into another course's collection
# and reports success.
check("…and is read from the trigger, where it still exists",
      "$('When Called by Document Ingest')" in embed, embed[:200])

# Every one of these keeps an environment fallback so a single-course
# installation is unaffected — but the fallback must be second, not first.
for name, code in (("Build Frontmatter", frontmatter),
                   ("Chunking", chunking),
                   ("Embed + Write to Weaviate", embed)):
    for line in code.split("\n"):
        if "$env.COURSE_ID" in line or "$env.WEAVIATE_COLLECTION_NAME" in line:
            check(f"{name}: the environment is only a fallback",
                  "||" in line and line.index("$env") > line.index("||"),
                  line.strip()[:90])

# And the Content Admin has to send them at all.
client_src = (REPO / "content-admin" / "n8n_client.py").read_text()
for field in ("course_id", "collection", "bucket"):
    check(f"the upload sends {field}", f'data["{field}"]' in client_src, "")
app_src = (REPO / "content-admin" / "app.py").read_text()
check("the upload route sends the selected course",
      'course_id=g.course["id"]' in app_src
      and 'collection=g.course["collection"]' in app_src
      and 'bucket=g.course["bucket"]' in app_src, "")

if failures:
    print("FAILURES:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print(
    "All course-scoping checks passed: course_id is a filterable text property on the "
    "retrieval collection, ChatHistory, UserMemory and TestResults; every archetype that "
    "retrieves filters on it (the one without a vector store has nothing to scope), using "
    "weaviate-client's own And shape — `filters`, not `operands`, with an explicit null "
    "value — and comparing course_id as a string while agent_id stays a bare number so it "
    "is not encoded as text against an int property; the id reaches the filter through "
    "auto-fill rather than the content form, leaving no placeholder behind; the ingest "
    "writes course_id wherever it writes agent_id, and neither background workflow takes "
    "the course from the environment any more — the chat history from the chatflow it came "
    "from, the learning record from a loop that runs over courses and then over the "
    "learners inside each one, refusing to write a record with no course; every agent that "
    "reads a learning record matches its own course and no longer interpolates the learner "
    "id into the query; and a dry-runnable, idempotent migration exists for deployments "
    "created before this, since deploy-schemas.sh deliberately never touches an existing "
    "class."
)
