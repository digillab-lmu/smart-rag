#!/usr/bin/env bash
# The concept-map workflow's own logic, run outside n8n.
#
# The build is an n8n workflow because it is long, many-stepped and needs
# retries — but that normally means its logic can only be tested by running
# it, which costs model calls and needs a live installation. So this takes
# the Code nodes out of the committed JSON and runs them in plain Node,
# against a stubbed Weaviate answer and a stubbed model.
#
# What that buys: the slicing, merging, citation resolution and cycle
# breaking are checked on the same code that will run in production — not on
# a copy that can drift from it — for nothing, before any of it is deployed.
#
# What it does not buy: nothing here proves the wiring between nodes, the
# webhook, or that n8n's runtime provides what these nodes expect. That is
# what deploying and running it proves, and it still has to happen.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v node >/dev/null 2>&1; then
    echo "Node is not installed, and these checks execute the workflow's own"
    echo "JavaScript. Install Node to run them; nothing else in this suite"
    echo "needs it."
    exit 10
fi

# The JSON has to be valid and hold the nodes the harness expects before the
# harness can say anything useful about them.
python3 - "$REPO" <<'PY' || exit 1
import json, sys, pathlib
wf = json.loads((pathlib.Path(sys.argv[1]) / "n8n/workflows/graph-build.json").read_text())
names = {n["name"] for n in wf["nodes"]}
need = {"Plan the run", "Slice the material", "Extract concepts",
        "Merge candidates", "Propose prerequisites", "Read the failure",
        "Build Webhook", "Loop over slices", "Report: proposed",
        "Report: failed", "Report: running"}
missing = need - names
if missing:
    print("FAILURES:")
    print(f"  - the workflow is missing: {', '.join(sorted(missing))}")
    sys.exit(1)

# The loop's two outputs are not interchangeable: splitInBatches sends "done"
# on output 0 and the current batch on output 1. Crossed, the merge would run
# on the first slice and the extraction would run once at the end — and the
# workflow would still look plausible in the editor.
loop = wf["connections"]["Loop over slices"]["main"]
if loop[0][0]["node"] != "Merge candidates" or loop[1][0]["node"] != "Extract concepts":
    print("FAILURES:")
    print("  - the loop's done/batch outputs are crossed: output 0 must go to "
          "Merge candidates and output 1 to Extract concepts")
    sys.exit(1)
if wf["connections"]["Extract concepts"]["main"][0][0]["node"] != "Loop over slices":
    print("FAILURES:")
    print("  - extraction does not return to the loop, so only one slice would "
          "ever be read")
    sys.exit(1)

# Every node that can fail must route its failure to the reporting path.
#
# An Error Trigger inside a workflow whose settings.errorWorkflow names a
# different workflow never fires -- n8n calls the named one instead. That was
# the first live run: the workflow errored, the shared handler ran, and the
# Content Admin sat at "queued" for ever because nothing told it. So the
# failure path hangs off each node's error output, the way the ingest workflow
# already does it, and this checks that none was forgotten.
# The nodes the build actually passes through. "Report: running" is not one
# of them: it hangs off the slicing as a side branch, because an HTTP node in
# the path replaces the items with its response — and a progress report that
# fails must not take the build with it, which is the opposite rule and is
# checked separately below.
main_path = ["Plan the run", "Read the course material", "Slice the material",
             "Extract concepts", "Merge candidates", "Propose prerequisites"]
by_name = {n["name"]: n for n in wf["nodes"]}
for name in main_path:
    node = by_name.get(name)
    if node is None:
        print("FAILURES:"); print(f"  - the workflow has no node {name!r}"); sys.exit(1)
    if node.get("onError") != "continueErrorOutput":
        print("FAILURES:")
        print(f"  - {name} has no error output, so a failure there leaves the "
              "build saying 'running' for ever")
        sys.exit(1)
    outs = wf["connections"].get(name, {}).get("main", [])
    if len(outs) < 2 or not outs[1] or outs[1][0]["node"] != "Read the failure":
        print("FAILURES:")
        print(f"  - {name}'s error output does not reach 'Read the failure'")
        sys.exit(1)

# An HTTP node replaces the items flowing through it with the response body.
# Putting one in the middle of the path is therefore not a detour, it is a
# substitution: the progress report used to sit between the slicing and the
# loop, and the loop iterated over the report's HTTP answer. The first live
# run died with "Cannot read properties of undefined (reading 'map')".
#
# So every node that consumes real items must be fed by a node that produces
# them. Checked structurally rather than by inspection, because in the editor
# a chain through an HTTP node looks exactly like a chain that works.
http_nodes = {n["name"] for n in wf["nodes"]
              if n["type"] == "n8n-nodes-base.httpRequest"}
feeders = {}
for src, conn in wf["connections"].items():
    for out_index, outs in enumerate(conn.get("main", [])):
        for c in (outs or []):
            # index 1 is the failure path; those items are meant to be
            # whatever the failing node had.
            if out_index == 0:
                feeders.setdefault(c["node"], set()).add(src)

# Who must feed whom, named rather than inferred. "Not an HTTP node" was too
# weak on its own: reading Weaviate is an HTTP node whose response really is
# the material, so it was excluded — and then feeding the loop straight from
# it, skipping the slicing entirely, passed.
expected_feed = {
    "Read the course material": {"Plan the run"},
    "Slice the material":       {"Read the course material"},
    "Loop over slices":         {"Slice the material", "Extract concepts"},
    "Merge candidates":         {"Loop over slices"},
    "Propose prerequisites":    {"Merge candidates"},
    "Report: proposed":         {"Propose prerequisites"},
}
for consumer, allowed in expected_feed.items():
    src = feeders.get(consumer, set())
    if not src:
        print("FAILURES:")
        print(f"  - nothing feeds {consumer}")
        sys.exit(1)
    if not src <= allowed:
        print("FAILURES:")
        print(f"  - {consumer} is fed by {', '.join(sorted(src - allowed))}; "
              f"it must come from {' or '.join(sorted(allowed))}. An HTTP node "
              "in the path replaces the items with its response, and a skipped "
              "step looks identical in the editor.")
        sys.exit(1)

# A progress report is a notice, not a station: if it fails, the build must
# carry on.
for name in ("Report: running",):
    if by_name[name].get("onError") != "continueRegularOutput":
        print("FAILURES:")
        print(f"  - {name} aborts the build when it fails; the display is the "
              "optional part, the build is not")
        sys.exit(1)

if any(n["type"] == "n8n-nodes-base.errorTrigger" for n in wf["nodes"]):
    print("FAILURES:")
    print("  - there is an Error Trigger in a workflow whose errorWorkflow "
          "points elsewhere; it cannot fire, and dead code here reads as a "
          "failure path that works")
    sys.exit(1)

# The report of a failure must not be able to fail into the failure path.
if by_name["Report: failed"].get("onError") != "continueRegularOutput":
    print("FAILURES:")
    print("  - Report: failed can route its own failure back into the error "
          "path")
    sys.exit(1)

# A run over a large course is minutes to hours; n8n's default execution
# timeout would cut it off with the money already spent.
if int(wf["settings"].get("executionTimeout", 0)) < 3600:
    print("FAILURES:")
    print("  - executionTimeout is under an hour, which is shorter than a "
          "build over a full course")
    sys.exit(1)

# No secret may be written into a workflow file: these are committed.
blob = json.dumps(wf)
for marker in ("sk-", "api_key\":", "Bearer ey"):
    if marker in blob:
        print("FAILURES:")
        print(f"  - the workflow contains {marker!r}, which looks like a secret")
        sys.exit(1)
if "$env.INGEST_STATUS_TOKEN" not in blob or "$env.LLM_API_KEY" not in blob:
    print("FAILURES:")
    print("  - credentials are not read from the environment")
    sys.exit(1)
PY

# A workflow file in the repository that the deploy script never mentions
# exists only for whoever reads the repository. Checked for every workflow,
# not just this one: the next person to add a file will make the same
# omission, and it fails on a server rather than here.
python3 - "$REPO" <<'PY' || exit 1
import json, pathlib, re, sys

repo = pathlib.Path(sys.argv[1])
deploy = (repo / "scripts/deploy-n8n-workflows.sh").read_text()
problems = []

for wf_file in sorted((repo / "n8n/workflows").glob("*.json")) + \
               sorted((repo / "n8n/workflows-ingest").glob("*.json")):
    if wf_file.name not in deploy:
        problems.append(f"{wf_file.name} is never imported by "
                        "scripts/deploy-n8n-workflows.sh, so it exists only in "
                        "the repository")
        continue
    wf = json.loads(wf_file.read_text())
    triggers = {n["type"] for n in wf["nodes"]}
    # A webhook or a schedule does nothing while the workflow is inactive; a
    # webhook in particular answers 404, which reaches the operator as
    # something unrelated. Sub-workflows reached by Execute Workflow, and the
    # error handler, are correctly left inactive.
    needs_active = bool(triggers & {"n8n-nodes-base.webhook",
                                    "n8n-nodes-base.scheduleTrigger",
                                    "n8n-nodes-base.cron"})
    listed = re.search(r'ACTIVATE_IDS=\((.*?)\n\)', deploy, re.S)
    active_block = listed.group(1) if listed else ""
    if needs_active and f'"{wf["id"]}"' not in active_block:
        problems.append(f"{wf_file.name} has a trigger but its id "
                        f"{wf['id']!r} is not in ACTIVATE_IDS — an inactive "
                        "webhook answers 404")

if problems:
    print("FAILURES:")
    for p in problems:
        print(f"  - {p}")
    sys.exit(1)
PY

node "$REPO/tests/graph_workflow_harness.js"
