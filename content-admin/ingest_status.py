"""
What is happening to a document between the upload and its first chunk.

Until now that window was invisible. The document list is built from what is
already in Weaviate, so an upload showed nothing at all until it was finished
— and a scanned PDF with two dozen figures legitimately takes twenty minutes.
An operator watching an unchanged page has no way to tell "working" from
"silently failed", and the difference only surfaced in n8n's execution log,
which is not somewhere this GUI's users go.

The pipeline reports its own progress instead of being polled. n8n's public
API could answer "did execution 41 succeed", but it needs an API key that
only exists once a human creates one in the browser — a fourth manual install
step and another secret that can expire — and mapping an execution back to a
document means digging through its run data, which is guesswork as soon as
two uploads overlap. The workflow, on the other hand, knows exactly which
document it is holding.

State lives in one JSON file, like slots.json: a handful of rows, each
short-lived. Nothing here is worth a database.

The honest limitation is stale rows. A callback arrives when a stage
completes, so a pipeline that dies between two stages — n8n restarted, the
container killed — leaves a row that never advances. This module does not
pretend otherwise: it does not invent a failure, it reports how long the row
has been silent and lets the reader draw the conclusion. Making that visible
is the whole point; a fabricated "failed" would be a different kind of lie
than a fabricated "running".
"""

import json
import os
import time
from pathlib import Path
from threading import Lock

_LOCK = Lock()

STATUS_PATH = Path(os.getenv("SMARTRAG_INGEST_STATUS_PATH", "/app/data/ingest-status.json"))

# The ordered stages of ingest-document.json. Order matters twice: it drives
# the progress fraction, and it is what lets a late callback be recognised as
# late rather than applied on top of a newer one — n8n's HTTP nodes do not
# guarantee arrival order, and a "converted" landing after "embedded" would
# otherwise walk the row backwards.
STAGES = [
    "accepted",     # the webhook took the file
    "converted",    # Docling produced markdown
    "described",    # figures captioned (skipped when there are none)
    "stored",       # markdown archived to object storage
    "embedded",     # chunked, embedded, written to Weaviate
    "done",         # the pipeline said so
]
TERMINAL = {"done", "failed"}

# How long a row may go without a callback before it is shown as stalled.
# Generous on purpose: "Describe Image" runs once per figure against a rate-
# limited API, and a wrongly-declared failure on a job that is merely slow
# would send someone hunting a bug that is not there.
STALE_AFTER_SECONDS = 45 * 60

# Finished rows are kept briefly so the operator sees the run complete rather
# than the row vanishing, which reads as "it disappeared" rather than "it
# worked".
KEEP_FINISHED_SECONDS = 30 * 60


def _load() -> dict:
    if not STATUS_PATH.exists():
        return {}
    try:
        return json.loads(STATUS_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        # A corrupt status file must never take the document list down with
        # it: this is a progress display, not a system of record.
        return {}


def _save(data: dict) -> None:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATUS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.replace(STATUS_PATH)


def _prune(data: dict, now: float) -> dict:
    return {
        job_id: row
        for job_id, row in data.items()
        if not (row.get("stage") in TERMINAL
                and now - row.get("updated", 0) > KEEP_FINISHED_SECONDS)
    }


def start(job_id: str, filename: str, agent_id: int | None,
          course_id: str = "") -> None:
    """Record an accepted upload. Called by the GUI, not by n8n — so a row
    exists even if the pipeline never reports anything at all, which is
    exactly the case worth seeing."""
    now = time.time()
    with _LOCK:
        data = _prune(_load(), now)
        data[job_id] = {
            "filename": filename,
            "agent_id": agent_id,
            # Which course this upload belongs to. Without it the progress
            # table was installation-wide: a document being processed in one
            # course appeared while another course was selected, next to a
            # document list that correctly showed nothing.
            "course_id": course_id,
            "stage": "accepted",
            "detail": "",
            "started": now,
            "updated": now,
        }
        _save(data)


def update(job_id: str, stage: str, detail: str = "") -> bool:
    """Apply a callback. Returns False for an unknown job or a stage that
    would move the row backwards."""
    if stage not in STAGES and stage != "failed":
        return False
    now = time.time()
    with _LOCK:
        data = _prune(_load(), now)
        row = data.get(job_id)
        if row is None:
            return False
        # A failure is always accepted; anything else must move forward. Two
        # callbacks can overtake each other on the network, and a row that
        # walks back to "converted" after "embedded" would be a bug the
        # operator cannot distinguish from a real regression.
        if stage != "failed":
            current = row.get("stage", "accepted")
            if current == "failed":
                return False
            if current in STAGES and STAGES.index(stage) <= STAGES.index(current):
                return False
        row["stage"] = stage
        row["detail"] = detail[:500]
        row["updated"] = now
        data[job_id] = row
        _save(data)
    return True


def active(course_id: str = "", now: float | None = None) -> list[dict]:
    """Every row worth showing, newest first, each annotated with what the
    template needs: how far along, whether it is finished, and whether it has
    gone quiet."""
    now = now if now is not None else time.time()
    # Reading must not write. Pruning here is in-memory only: this runs on
    # every load of the documents page, and an earlier version persisted the
    # pruned file each time — which turned a read-only or full disk into a
    # crash on the page, i.e. the progress display taking down the view it
    # exists to improve. The file is trimmed when a job starts or reports,
    # which is often enough to keep it small.
    with _LOCK:
        data = _prune(_load(), now)

    rows = []
    for job_id, row in data.items():
        # Rows written before uploads carried a course have none; they belong
        # to the installation's original course and are shown nowhere rather
        # than everywhere.
        if course_id and row.get("course_id", "") != course_id:
            continue
        stage = row.get("stage", "accepted")
        # A finished row is not "in progress". It used to linger so the
        # operator could see the run complete, but the completion is already
        # visible: the document appears in the list below. Keeping both said
        # "still working" and "nothing here" at the same time.
        if stage == "done":
            continue
        silent_for = now - row.get("updated", now)
        rows.append({
            "job_id": job_id,
            "filename": row.get("filename", ""),
            "agent_id": row.get("agent_id"),
            "course_id": row.get("course_id", ""),
            "stage": stage,
            "detail": row.get("detail", ""),
            "started": row.get("started", now),
            "updated": row.get("updated", now),
            "elapsed": int(now - row.get("started", now)),
            "silent_for": int(silent_for),
            "finished": stage in TERMINAL,
            "failed": stage == "failed",
            # Stalled is a statement about silence, not about failure.
            "stalled": stage not in TERMINAL and silent_for > STALE_AFTER_SECONDS,
            "step": (STAGES.index(stage) + 1) if stage in STAGES else len(STAGES),
            "steps": len(STAGES),
        })
    rows.sort(key=lambda r: r["started"], reverse=True)
    return rows


def forget_course(course_id: str) -> int:
    """Drop a course's progress rows. Returns how many went.

    Called when the course itself goes. Without it the table keeps rows
    pointing at a course that no longer exists — and the page that renders
    them filters by the selected course, so they would simply never be shown
    again and never be cleaned up either.
    """
    if not course_id:
        return 0
    data = _load()
    keep = {job: row for job, row in data.items()
            if row.get("course_id", "") != course_id}
    removed = len(data) - len(keep)
    if removed:
        _save(keep)
    return removed


def any_running(course_id: str = "") -> bool:
    """Whether the page has a reason to keep refreshing itself."""
    return any(not r["finished"] for r in active(course_id))
