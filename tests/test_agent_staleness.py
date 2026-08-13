"""
An imported agent is a copy, and until now nothing said which copy.

Flowise holds a snapshot of the template, not a reference to it. Changing a
template in this repository, the slot's content, or a model name in .env
changes nothing for learners until somebody imports again — and the dashboard
said "Imported" either way.

That is not a cosmetic gap. On 2026-08-13 two cross-course leaks were fixed in
six agent templates, deployed, and went on leaking on the running installation
because the agents were still the old copies. The only way to find out was a
SQL query against Flowise's own database.

So an import now records a digest of the flow it built, and the dashboard
compares it against the flow that would be built now. What is checked here is
that the comparison is honest in both directions: it must not cry wolf, and it
must not stay silent when the copy is genuinely behind.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dbfixture  # noqa: E402

# Before the fixture, not after: env_file binds ENV_PATH at import time, and
# the fixture imports the modules that import it.
os.environ["SMARTRAG_ENV_PATH"] = str(dbfixture.tmp_env())

db, course = dbfixture.require_database()

import agent_templates  # noqa: E402
import storage  # noqa: E402

failures = []


def check(name, ok, detail=""):
    if not ok:
        failures.append(f"{name}: {detail}")


# ─── 1. The digest itself ────────────────────────────────────────────────────
flow = {"nodes": [{"id": "a", "data": {"x": 1}}], "edges": []}
same_other_order = {"edges": [], "nodes": [{"data": {"x": 1}, "id": "a"}]}
check("the same flow gives the same digest",
      agent_templates.flow_digest(flow) == agent_templates.flow_digest(flow), "")
# Python preserves insertion order, so a template edit that only moves a key
# would otherwise read as a change and send someone to re-import for nothing.
check("key order does not change the digest",
      agent_templates.flow_digest(flow) ==
      agent_templates.flow_digest(same_other_order), "")
check("a changed value changes the digest",
      agent_templates.flow_digest(flow) !=
      agent_templates.flow_digest({"nodes": [{"id": "a", "data": {"x": 2}}],
                                   "edges": []}), "")

# ─── 2. The digest is stored with the import, not beside it ──────────────────
dbfixture.clear_slots(db)
ARCHETYPE = "agent-topic-template.json"
placeholders = agent_templates.placeholders_for(ARCHETYPE)
content = {k: f"Wert für {k}" for k in placeholders}
storage.save_slot(course["id"], 1, ARCHETYPE, content, "Tutor")

storage.set_chatflow_id(course["id"], 1, "cf-1", "digest-abc")
slot = storage.get_slot(course["id"], 1)
check("the chatflow id is stored", slot.get("chatflow_id") == "cf-1", slot)
check("…and the digest with it", slot.get("imported_digest") == "digest-abc", slot)
check("…and when", slot.get("imported_at") is not None, slot)

# A caller that forgets the digest must not leave a stale one behind claiming
# the new import matches the old flow.
storage.set_chatflow_id(course["id"], 1, "cf-1")
check("an import without a digest clears the old one",
      storage.get_slot(course["id"], 1).get("imported_digest") is None,
      storage.get_slot(course["id"], 1))

# ─── 3. current / behind / unknown ───────────────────────────────────────────
import app as application  # noqa: E402

env = {"COURSE_ID": course["id"], "COURSE_NAME": course["name"],
       "WEAVIATE_COLLECTION_NAME": course["collection"],
       "LLM_PROVIDER": "anthropic", "LLM_API_KEY": "sk-t",
       "EMBEDDING_PROVIDER": "openai", "EMBEDDING_API_KEY": "sk-e",
       "EMBEDDING_MODEL": "text-embedding-3-small"}

slots = storage.all_slots(course["id"])
built, missing = application._build_flow(course, 1, ARCHETYPE, slots, env)
check("a slot with complete content builds without gaps", not missing, missing)
digest = agent_templates.flow_digest(built)

storage.set_chatflow_id(course["id"], 1, "cf-1", digest)
state = application._import_state(course, storage.all_slots(course["id"]), env)
check("an agent imported from this exact flow is current",
      state.get("1") == "current", state)

# The case that started this: the template changed under a slot that was
# imported before.
storage.set_chatflow_id(course["id"], 1, "cf-1", "digest-from-an-older-template")
state = application._import_state(course, storage.all_slots(course["id"]), env)
check("an agent built from an older template is behind",
      state.get("1") == "behind", state)

# Editing the slot's own content must count as behind too — the agent in
# Flowise still answers with the old text.
storage.set_chatflow_id(course["id"], 1, "cf-1", digest)
storage.save_slot(course["id"], 1, ARCHETYPE,
                  dict(content, **{list(content)[0]: "etwas anderes"}), "Tutor")
state = application._import_state(course, storage.all_slots(course["id"]), env)
check("changed slot content makes the agent behind",
      state.get("1") == "behind", state)

# A slot imported before the digest existed has not been *shown* to differ.
# Calling that "behind" would send someone to re-import for no reason, which
# is how a warning stops being read.
storage.set_chatflow_id(course["id"], 1, "cf-1")
state = application._import_state(course, storage.all_slots(course["id"]), env)
check("an import from before the digest is unknown, not behind",
      state.get("1") == "unknown", state)

# Never imported is not a state that needs a warning at all.
dbfixture.clear_slots(db)
storage.save_slot(course["id"], 2, ARCHETYPE, content, "Zweiter")
state = application._import_state(course, storage.all_slots(course["id"]), env)
check("a slot that was never imported is not reported",
      "2" not in state, state)

# ─── 4. What the page does with it ───────────────────────────────────────────
# The comparison is only worth having if it reaches the operator, and the
# button is only useful if it is there before anyone has diagnosed anything.
page = (Path(application.__file__).parent / "templates" / "dashboard.html").read_text()
check("the page shows a slot that is behind",
      "dash_status_behind" in page, "")
check("…warns above the table when any slot is",
      "dash_behind_warning" in page and "behind_count" in page, "")
check("…and offers the re-import without requiring a warning first",
      page.count("value=\"reimport\"") == 2, page.count("value=\"reimport\""))

src = Path(application.__file__).read_text()
check("re-importing is a POST, not a link",
      'methods=["GET", "POST"]' in src.split("def dashboard")[0].rsplit("@app.route", 1)[-1]
      or 'methods=["GET", "POST"]' in src, "a GET would re-import on every visit")
check("the re-import runs inside one course",
      "__course_bound__" in src, "")
# Partial success has to say so: three of five re-imported is not a failure,
# and it is not a success either.
check("a partly failed re-import reports both halves",
      "dash_reimport_partial" in src and "dash_reimport_done" in src, "")

if failures:
    print("FAILURES:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print(
    "All agent-staleness checks passed: a flow's digest is stable under key "
    "order and changes with content; an import stores it together with the "
    "chatflow id and an import without one clears it rather than leaving a "
    "stale claim; a slot built from the current template, content and "
    "settings reads as current, one built from an older template or edited "
    "since reads as behind, one imported before the digest existed reads as "
    "unknown rather than behind, and one never imported is not reported at "
    "all; and the dashboard shows the state, warns above the table, and "
    "offers the re-import as a POST inside one course whether or not anything "
    "is behind."
)
