"""
Ingest progress — the window between an upload and its first chunk.

The document list is built from Weaviate, so until this existed an upload
showed nothing at all until it finished, and a scanned PDF with figures takes
twenty minutes. What is being tested is not "does a bar move" but the four
properties that decide whether the display can be trusted:

  * the endpoint is reachable by anything on the Docker network, so an
    unauthenticated or badly-authenticated call must not be able to rewrite
    what an operator is told;
  * callbacks can arrive out of order — n8n's HTTP nodes make no promise —
    and a row that walks backwards is indistinguishable from a real
    regression to the person reading it;
  * a pipeline that dies mid-run sends nothing, and the row must say it has
    gone quiet rather than claim to be working or invent a failure;
  * none of this may be able to break the ingest, which is the part that
    matters. The workflow reports from side branches and swallows its own
    errors.
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
APP_DIR = str(REPO / "content-admin")
sys.path.insert(0, APP_DIR)

tmpdir = tempfile.mkdtemp()
env_path = Path(tmpdir) / ".env"
TOKEN = "0123456789abcdef" * 4
env_path.write_text(
    'CONTENT_ADMIN_SESSION_SECRET="test-secret-not-real"\n'
    'DOMAIN="example.com"\n'
    'ADMIN_EMAIL="admin@example.com"\n'
    'COURSE_ID="testkurs"\n'
    'WEAVIATE_COLLECTION_NAME="TestChunks"\n'
    f'INGEST_STATUS_TOKEN="{TOKEN}"\n'
)
os.environ["SMARTRAG_ENV_PATH"] = str(env_path)
os.environ["SMARTRAG_SLOTS_PATH"] = str(Path(tmpdir) / "slots.json")
os.environ["SMARTRAG_INGEST_STATUS_PATH"] = str(Path(tmpdir) / "ingest-status.json")
os.environ["SMARTRAG_TEMPLATES_DIR"] = str(Path(APP_DIR).parent / "flowise" / "agents")
os.environ["CONTENT_ADMIN_SESSION_SECRET"] = "test-secret-not-real"

# ─── A database, because agent slots live in one now ─────────────────────────
# Slots moved out of slots.json into Postgres, so this suite needs a database
# and a course for the slots to belong to. dbfixture arranges both, or exits
# 10 — "could not run" rather than a pass that covered nothing.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import dbfixture  # noqa: E402
_db, COURSE = dbfixture.require_database()
dbfixture.clear_slots(_db)
COURSE_ID = COURSE["id"]

import app as flask_app_module  # noqa: E402
import ingest_status  # noqa: E402

client = flask_app_module.app.test_client()
failures = []


def check(name, ok, detail=""):
    if not ok:
        failures.append(f"{name}: {detail}")


def reset():
    Path(os.environ["SMARTRAG_INGEST_STATUS_PATH"]).unlink(missing_ok=True)


def post(payload, token=TOKEN):
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["X-Ingest-Token"] = token
    return client.post("/api/ingest-status", data=json.dumps(payload), headers=headers)


# ─── 1. The store's own rules ────────────────────────────────────────────────
reset()
ingest_status.start("job1", "skript.pdf", 1)
rows = ingest_status.active()
check("an accepted upload is visible immediately", len(rows) == 1, rows)
check("…and starts at 'accepted'", rows[0]["stage"] == "accepted", rows[0])
check("…and is not yet finished", not rows[0]["finished"], rows[0])

check("a stage moves the row on", ingest_status.update("job1", "converted"))
check("the row shows the new stage",
      ingest_status.active()[0]["stage"] == "converted")

# Out-of-order delivery: the earlier stage must not undo the later one.
ingest_status.update("job1", "embedded")
check("a late callback is refused", not ingest_status.update("job1", "converted"))
check("…and leaves the row where it was",
      ingest_status.active()[0]["stage"] == "embedded",
      ingest_status.active()[0])
# The same stage twice is not progress either.
check("a repeated stage is refused", not ingest_status.update("job1", "embedded"))

check("an unknown job is refused", not ingest_status.update("nosuchjob", "done"))
check("an invented stage is refused", not ingest_status.update("job1", "teleported"))

# A failure is the one report that may arrive at any point.
check("a failure is always accepted", ingest_status.update("job1", "failed", "429 from the embedding API"))
row = ingest_status.active()[0]
check("the row reads as failed", row["failed"] and row["finished"], row)
check("the reason is kept", "429" in row["detail"], row)
check("nothing revives a failed row", not ingest_status.update("job1", "done"))

# ─── 2. Silence is reported as silence ───────────────────────────────────────
reset()
ingest_status.start("job2", "gross.pdf", 2)
ingest_status.update("job2", "converted")
row = ingest_status.active()[0]
check("a fresh row is not stalled", not row["stalled"], row)

# Reach past the threshold without waiting for it.
data = json.loads(Path(os.environ["SMARTRAG_INGEST_STATUS_PATH"]).read_text())
data["job2"]["updated"] = time.time() - (ingest_status.STALE_AFTER_SECONDS + 60)
Path(os.environ["SMARTRAG_INGEST_STATUS_PATH"]).write_text(json.dumps(data))
row = ingest_status.active()[0]
check("a silent row is marked stalled", row["stalled"], row)
# Stalled is a statement about silence. Calling it failed would be inventing
# an outcome nobody reported.
check("…but not as failed", not row["failed"], row)
check("…and not as finished", not row["finished"], row)
check("the silence is quantified", row["silent_for"] >= ingest_status.STALE_AFTER_SECONDS,
      row["silent_for"])

# ─── 3. Finished rows go away, but not instantly ─────────────────────────────
reset()
ingest_status.start("job3", "fertig.pdf", 1)
ingest_status.update("job3", "done")
# A finished row is not "in progress". It used to linger so the operator
# could watch the run complete — until a finished row sat under "being
# processed" while the document list below said the course had nothing,
# which is two contradictory statements about the same upload. The
# completion is already visible: the document appears in the list.
check("a finished row leaves the in-progress list at once",
      len(ingest_status.active()) == 0, ingest_status.active())
data = json.loads(Path(os.environ["SMARTRAG_INGEST_STATUS_PATH"]).read_text())
check("…but is kept in the store for a while", "job3" in data, list(data))
data["job3"]["updated"] = time.time() - (ingest_status.KEEP_FINISHED_SECONDS + 60)
Path(os.environ["SMARTRAG_INGEST_STATUS_PATH"]).write_text(json.dumps(data))
ingest_status.start("job3b", "next.pdf", 1)   # a write, which is what prunes
data = json.loads(Path(os.environ["SMARTRAG_INGEST_STATUS_PATH"]).read_text())
check("…and is eventually pruned from it", "job3" not in data, list(data))

# A failure stays visible: nobody else is going to mention it.
reset()
ingest_status.start("job3c", "kaputt.pdf", 1)
ingest_status.update("job3c", "failed", "429 from the embedding API")
check("a failed row stays in the list", len(ingest_status.active()) == 1,
      ingest_status.active())

# Rows belong to a course. Without this the table was installation-wide: an
# upload in one course appeared while another was selected, beside a document
# list that correctly showed nothing.
reset()
ingest_status.start("job-a", "a.pdf", 1, "kurs-a")
ingest_status.start("job-b", "b.pdf", 1, "kurs-b")
check("a course sees only its own uploads",
      [r["filename"] for r in ingest_status.active("kurs-a")] == ["a.pdf"],
      ingest_status.active("kurs-a"))
check("…and the other course sees only its own",
      [r["filename"] for r in ingest_status.active("kurs-b")] == ["b.pdf"],
      ingest_status.active("kurs-b"))
# A row from before uploads carried a course belongs to no course, and is
# shown in none rather than in all of them.
ingest_status.start("job-old", "alt.pdf", 1)
check("a row with no course appears in no course",
      "alt.pdf" not in [r["filename"] for r in ingest_status.active("kurs-a")],
      ingest_status.active("kurs-a"))

# An unfinished row is never pruned, however old — that is the row that
# matters most.
reset()
ingest_status.start("job4", "haengt.pdf", 1)
data = json.loads(Path(os.environ["SMARTRAG_INGEST_STATUS_PATH"]).read_text())
data["job4"]["updated"] = time.time() - (10 * ingest_status.KEEP_FINISHED_SECONDS)
data["job4"]["started"] = data["job4"]["updated"]
Path(os.environ["SMARTRAG_INGEST_STATUS_PATH"]).write_text(json.dumps(data))
check("an old unfinished row is kept", len(ingest_status.active()) == 1)

# Listing must not write. This runs on every load of the documents page, and
# writing there turned a read-only or full disk into a crash of the page
# itself — the progress display breaking the view it exists to improve.
reset()
ingest_status.start("job4b", "lesen.pdf", 1)
status_file = Path(os.environ["SMARTRAG_INGEST_STATUS_PATH"])
before = status_file.stat().st_mtime_ns
ingest_status.active()
check("listing does not write the status file",
      status_file.stat().st_mtime_ns == before, "active() rewrote the file")
status_file.chmod(0o444)
try:
    rows = ingest_status.active()
    check("listing works when the file cannot be written", len(rows) == 1, rows)
except OSError as exc:
    check("listing works when the file cannot be written", False, str(exc))
finally:
    status_file.chmod(0o644)

# A corrupt file must not take the documents page down with it.
Path(os.environ["SMARTRAG_INGEST_STATUS_PATH"]).write_text("{not json")
check("a corrupt status file degrades to empty", ingest_status.active() == [])

# ─── 4. The endpoint ─────────────────────────────────────────────────────────
reset()
ingest_status.start("job5", "endpoint.pdf", 3)

r = post({"job_id": "job5", "stage": "converted"}, token=None)
check("no token is rejected", r.status_code == 401, r.status_code)
r = post({"job_id": "job5", "stage": "converted"}, token="wrong-token")
check("a wrong token is rejected", r.status_code == 401, r.status_code)
check("…and neither one moved the row",
      ingest_status.active()[0]["stage"] == "accepted")

r = post({"job_id": "job5", "stage": "converted"})
check("the right token is accepted", r.status_code == 200, r.data)
check("…and the row moved", ingest_status.active()[0]["stage"] == "converted")

r = post({"job_id": "", "stage": "converted"})
check("a missing job id is a 400", r.status_code == 400, r.status_code)
r = post({"job_id": "job5"})
check("a missing stage is a 400", r.status_code == 400, r.status_code)

# An unknown job answers 200, not an error: n8n retries on failure, and no
# number of retries can make an unknown job known. The ingest must not be
# disturbed by the display.
r = post({"job_id": "ghost", "stage": "done"})
check("an unknown job still answers 200", r.status_code == 200, r.status_code)
check("…and says it applied nothing", r.get_json() == {"applied": False}, r.get_json())

# The dangerous configuration: no token set at all must lock the endpoint,
# not open it.
saved = env_path.read_text()
env_path.write_text(saved.replace(f'INGEST_STATUS_TOKEN="{TOKEN}"', 'INGEST_STATUS_TOKEN=""'))
r = post({"job_id": "job5", "stage": "stored"}, token="")
check("an unset token refuses everyone", r.status_code == 401, r.status_code)
r = post({"job_id": "job5", "stage": "stored"})
check("…including a caller with a plausible token", r.status_code == 401, r.status_code)
env_path.write_text(saved)

# An unwritable store must not fail the upload: by the time the row is written
# the document is already with n8n, so a 500 here would report a failure that
# did not happen and invite a second upload of the same file.
import inspect  # noqa: E402
upload_src = inspect.getsource(flask_app_module.upload)
start_call = upload_src[upload_src.index("ingest_status.start"):]
check("recording progress cannot fail the upload",
      "except OSError" in upload_src
      and upload_src.index("try:", upload_src.index("ingest_status.start") - 200)
          < upload_src.index("ingest_status.start"),
      "ingest_status.start is not guarded")

# ─── 5. The workflow reports, and cannot break the ingest doing it ───────────
wf = json.load(open(REPO / "n8n" / "workflows-ingest" / "ingest-document.json"))
nodes = {n["name"]: n for n in wf["nodes"]}
reports = {name: n for name, n in nodes.items() if name.startswith("Report: ")}
check("the workflow reports its progress", len(reports) >= 4, sorted(reports))

for name, node in reports.items():
    p = node.get("parameters", {})
    check(f"{name} posts", p.get("method") == "POST", p.get("method"))
    check(f"{name} authenticates",
          any(h.get("name") == "X-Ingest-Token" and "INGEST_STATUS_TOKEN" in h.get("value", "")
              for h in p.get("headerParameters", {}).get("parameters", [])),
          p.get("headerParameters"))
    # 5000, literally, and not CONTENT_ADMIN_PORT. That variable is the port
    # on the HOST — compose maps "127.0.0.1:${CONTENT_ADMIN_PORT}:5000" — and
    # inside the Docker network the container always listens on 5000, which
    # is a property of the image (Dockerfile: gunicorn --bind 0.0.0.0:5000).
    # The first version of this test demanded the variable, on the reasoning
    # that a hard-coded port breaks an installation that moved it. It is the
    # other way round here, and the test enshrined the bug: every report
    # would have gone to a port nothing listens on inside the network. The
    # workflow's other internal calls use literal ports for the same reason
    # (smartrag-docling:5001).
    check(f"{name} posts to the container's own port",
          "smartrag-content-admin:5000" in p.get("url", ""), p.get("url"))
    check(f"{name} does not use the host-side port variable",
          "CONTENT_ADMIN_PORT" not in p.get("url", ""), p.get("url"))
    check(f"{name} carries the job id from the webhook",
          "$('Upload Webhook')" in p.get("jsonBody", ""), p.get("jsonBody"))
    # The one property that protects the ingest itself.
    check(f"{name} cannot fail the run",
          node.get("onError") == "continueRegularOutput", node.get("onError"))
    check(f"{name} does not hang the run",
          p.get("options", {}).get("timeout", 0) <= 10000, p.get("options"))

# Every reported stage must be one the store accepts, and vice versa — a
# typo on either side is silent, and shows as a row that never advances.
reported = set()
for node in reports.values():
    body = node["parameters"]["jsonBody"]
    stage = body.split("stage: '")[1].split("'")[0]
    reported.add(stage)
# "failed" is a report but not a stage in the ordered list: it can
# arrive at any point and ends the row.
unknown = reported - set(ingest_status.STAGES) - {"failed"}
check("every reported stage is known to the store", not unknown, unknown)

# Reports hang off existing nodes as side branches. Spliced into the chain,
# an HTTP node replaces the items the next node reads — the progress display
# would break the pipeline it reports on.
for source, conns in wf["connections"].items():
    targets = [c["node"] for c in conns.get("main", [[]])[0]]
    report_targets = [t for t in targets if t.startswith("Report: ")]
    if report_targets:
        check(f"{source} still reaches its real successor",
              len([t for t in targets if not t.startswith("Report: ")]) >= 1
              or source == "Send Success Email",
              targets)
        check(f"{source} reports last",
              targets.index(report_targets[0]) == len(targets) - len(report_targets),
              targets)
# And nothing downstream may depend on a report's output.
for name in reports:
    check(f"nothing runs after {name}", name not in wf["connections"], wf["connections"].get(name))

# ─── 6. The token is provisioned, and the strings exist ──────────────────────
check("the token is declared in .env.example",
      'INGEST_STATUS_TOKEN=' in (REPO / ".env.example").read_text())
check("bootstrap generates it",
      "SECRET_INGEST_STATUS_TOKEN" in (REPO / "scripts" / "lib" / "secrets.sh").read_text())
check("bootstrap writes it into .env",
      "REPL[INGEST_STATUS_TOKEN]" in (REPO / "scripts" / "lib" / "templates.sh").read_text())
# An existing installation upgrading into this feature must get a real value,
# not the published placeholder string.
check("an upgrade fills it in",
      "INGEST_STATUS_TOKEN)" in (REPO / "scripts" / "admin.sh").read_text())

i18n_src = (REPO / "content-admin" / "i18n.py").read_text()
for key in ["docs_running_heading", "docs_running_intro", "docs_col_stage",
            "docs_col_elapsed", "docs_stage_failed", "docs_stage_stalled"] + \
           [f"docs_stage_{s}" for s in ingest_status.STAGES]:
    check(f"{key} exists in both languages", i18n_src.count(f'"{key}":') == 2,
          f'{i18n_src.count(chr(34) + key + chr(34) + ":")} definition(s)')

# ─── A failure has to reach the page ─────────────────────────────────────────
# Observed live: Docling ran out of memory, n8n stopped the run, and the
# table went on showing the upload as in progress. A step that dies between
# two reports sends nothing, so the risky steps now report their own failure
# from an error output — which, unlike n8n's Error Trigger, runs inside the
# same execution and can still read the job id.
wf = json.loads((REPO / "n8n" / "workflows-ingest" / "ingest-document.json").read_text())
nodes = {n["name"]: n for n in wf["nodes"]}
check("there is a node that reports a failure", "Report: failed" in nodes,
      sorted(nodes))
fail_node = nodes.get("Report: failed", {})
body = fail_node.get("parameters", {}).get("jsonBody", "")
check("it reports the failed stage", "'failed'" in body, body[:120])
check("it carries the job id", "$('Upload Webhook')" in body, body[:120])
check("it passes on what went wrong", "detail" in body, body[:120])

for risky in ("Docling Conversion", "Upload to object storage", "Chunk + Embed"):
    node = nodes.get(risky, {})
    check(f"{risky} has an error output", node.get("onError") == "continueErrorOutput",
          node.get("onError"))
    branches = wf["connections"].get(risky, {}).get("main", [])
    check(f"{risky}'s error output reaches the report",
          len(branches) > 1 and any(c["node"] == "Report: failed" for c in branches[1]),
          branches)
    # The success branch must still go where it went.
    check(f"{risky} still continues on success",
          branches and len(branches[0]) >= 1, branches[:1])

tpl = (REPO / "content-admin" / "templates" / "documents.html").read_text()
check("a row in progress shows it is moving", 'class="spinner"' in tpl, "")
check("…and the page refreshes while it is",
      "location.replace" in tpl, "")

if failures:
    print("FAILURES:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print(
    "All ingest-progress checks passed: an accepted upload is visible before "
    "it has any chunks, a callback can only move a row forward, a failure is "
    "accepted at any point and is final, silence is reported as silence "
    "rather than as failure or as work, finished rows linger and then go "
    "while unfinished ones stay, a corrupt status file degrades to empty, the "
    "endpoint refuses an absent, wrong or unconfigured token and answers 200 "
    "to an unknown job so n8n never retries against the display, and the "
    "workflow reports from side branches with errors swallowed and a short "
    "timeout, so nothing about the progress display can fail the ingest."
)
