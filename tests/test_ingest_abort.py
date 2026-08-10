"""
A permanent embedding failure must stop the run — a transient one must not.

Live case: an OpenAI account with no credits produced 23 identical 429s, one
per chunk. Every call was doomed the moment the first one came back, and the
result was minutes of pointless requests and a wall of repeated text hiding
the single fact that mattered.

Aborting on any 429 would trade one bad behaviour for another: a plain rate
limit clears by itself, and a run that gives up on it has thrown away work it
could have finished. The distinction is the point of this test, and it is
checked by running the classifier rather than grepping for it.
"""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WF = REPO / "n8n" / "workflows-ingest" / "ingest-chunk-and-embed.json"

failures = []


def check(name, ok, detail=""):
    if not ok:
        failures.append(f"{name}: {detail}")


code = ""
for node in json.loads(WF.read_text())["nodes"]:
    js = node.get("parameters", {}).get("jsCode", "")
    if "chunks_written" in js:
        code = js
        break
check("the embedding node was found", bool(code), "no node writes chunks_written")

# ─── The classifier is extracted and actually run ────────────────────────────
m = re.search(r"function isPermanentEmbeddingFailure\(message\) \{.*?\n\}", code, re.S)
check("the classifier exists", bool(m), "no isPermanentEmbeddingFailure in the node")

node_bin = shutil.which("node")
check("node is available to run it", bool(node_bin),
      "the classifier could only be grepped, not executed")

if m and node_bin:
    PERMANENT = [
        '429 - {"error":{"message":"You have no credits remaining.",'
        '"type":"insufficient_quota","code":"credit_balance_exhausted"}}',
        '401 - {"error":{"message":"Incorrect API key provided"}}',
        '403 - {"error":{"message":"Forbidden"}}',
        '429 insufficient_quota',
    ]
    TRANSIENT = [
        # A plain rate limit: the next chunk may well succeed.
        '429 - {"error":{"message":"Rate limit reached for requests",'
        '"type":"requests","code":"rate_limit_exceeded"}}',
        '500 - internal server error',
        '503 - service unavailable',
        'connect ETIMEDOUT',
        'socket hang up',
        '',
    ]
    script = (
        m.group(0)
        + "\nconst P=" + json.dumps(PERMANENT)
        + ";\nconst T=" + json.dumps(TRANSIENT)
        + ";\nconsole.log(JSON.stringify({"
          "p:P.map(isPermanentEmbeddingFailure),"
          "t:T.map(isPermanentEmbeddingFailure)}));"
    )
    res = subprocess.run([node_bin, "-e", script], capture_output=True, text=True)
    check("the classifier runs without error", res.returncode == 0, res.stderr[:300])
    if res.returncode == 0:
        out = json.loads(res.stdout)
        for msg, verdict in zip(PERMANENT, out["p"]):
            check(f"permanent: {msg[:44]}…", verdict is True, "treated as transient")
        for msg, verdict in zip(TRANSIENT, out["t"]):
            check(f"transient: {(msg or '(empty)')[:44]}…", verdict is False,
                  "would abort a run that could have finished")

# ─── The abort is wired into the loop and reported ───────────────────────────
check("a permanent failure breaks the loop",
      re.search(r"isPermanentEmbeddingFailure\(e\.message\)[\s\S]{0,120}break;", code) is not None,
      "the classifier is computed but the loop continues")
check("the summary says it aborted", '"aborted"' in code or "aborted:" in code,
      "the caller cannot tell a stopped run from a finished one")
check("and how much was left", "chunks_remaining" in code,
      "no indication of how much of the document is missing")

# The counters must still be reported — an abort is not a reason to lose them.
for field in ("chunks_total", "chunks_written", "chunks_skipped", "errors"):
    check(f"{field} is still reported", field in code, "")

if failures:
    print("FAILURES:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print(
    "All ingest-abort checks passed: the classifier was extracted and executed "
    "against real provider replies — an exhausted quota, a rejected key and a "
    "403 stop the run, while a plain rate limit, a 5xx, a timeout and an empty "
    "message do not — the abort breaks the loop rather than only being "
    "computed, and the summary reports that it stopped, how much was left, and "
    "still carries every counter."
)
