"""
How long a course's data may be kept, and the three states that are not two.

The data protection officer's green light came with the obligation: personal
data is kept for a stated period. The trap in implementing that is collapsing
three situations into two.

  * A course with **no date** has not been decided about. Somebody has to be
    asked.
  * A course with a **future date** has been decided about and needs nothing.
  * A course **past its date** needs acting on — once. After that it must stop
    warning, or the warning becomes the thing people scroll past, and the next
    real one goes with it.

Defaulting the column to anything, or treating "no date" as "not due", loses
the first. Not recording that an expiry was handled loses the third.
"""

import os
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dbfixture  # noqa: E402

os.environ["SMARTRAG_ENV_PATH"] = str(dbfixture.tmp_env())
db, course = dbfixture.require_database()

import courses as svc  # noqa: E402

CID = course["id"]
failures = []


def check(name, ok, detail=""):
    if not ok:
        failures.append(f"{name}: {detail}")


def reset():
    dbfixture._ensure_course(db)
    svc.set_retention(CID, None)


def one():
    return svc.get_course(CID)


# ─── The migration actually arrived ──────────────────────────────────────────

state = db.schema_state()
check("migration 003 is applied", 3 in state["applied"], state)
check("nothing is left pending", not state["pending"], state)

reset()
c = one()
for field in ("retention_until", "retention_note", "retention_applied_at",
              "retention_set", "retention_due", "retention_days_left"):
    check(f"a course carries {field}", field in c, sorted(c))

# ─── Three states, not two ───────────────────────────────────────────────────

check("no date means undecided, not due",
      c["retention_set"] is False and c["retention_due"] is False, c)
check("and there is no day count to show", c["retention_days_left"] is None, c)

future = date.today() + timedelta(days=90)
svc.set_retention(CID, future, "Ethikantrag 2026-03, zwei Jahre nach Erhebung")
c = one()
check("a future date is set and not due",
      c["retention_set"] and not c["retention_due"], c)
check("the day count is the days remaining",
      c["retention_days_left"] == 90, c["retention_days_left"])
check("the reason is kept with it",
      c["retention_note"].startswith("Ethikantrag"), c["retention_note"])

svc.set_retention(CID, date.today())
check("a date of today is already due", one()["retention_due"], one())

svc.set_retention(CID, date.today() - timedelta(days=1))
c = one()
check("a past date is due", c["retention_due"], c)
check("and the day count is negative rather than absent",
      c["retention_days_left"] == -1, c["retention_days_left"])

# ─── The overview separates them ─────────────────────────────────────────────

svc.set_retention(CID, date.today() - timedelta(days=5))
over = svc.retention_overview()
check("an expired course is due", CID in [x["id"] for x in over["due"]], over)
check("and is not also listed as undecided",
      CID not in [x["id"] for x in over["undecided"]], over)

svc.mark_retention_applied(CID)
over = svc.retention_overview()
check("an expiry that was acted on stops being due",
      CID not in [x["id"] for x in over["due"]], over)
check("but is still visible as handled",
      CID in [x["id"] for x in over["handled"]], over)

# A new period after a handled one is a new obligation. If the old timestamp
# survived, the warning for the new date would never appear.
svc.set_retention(CID, date.today() - timedelta(days=1), "neuer Zeitraum")
c = one()
check("setting a new date clears the earlier handling",
      c["retention_applied_at"] is None, c["retention_applied_at"])
check("so the new expiry is due again",
      CID in [x["id"] for x in svc.retention_overview()["due"]])

svc.set_retention(CID, date.today() + timedelta(days=10))
over = svc.retention_overview(warn_within_days=30)
check("a date inside the warning window is 'soon'",
      CID in [x["id"] for x in over["soon"]], over)
check("and 'soon' is not 'due'", CID not in [x["id"] for x in over["due"]], over)
over = svc.retention_overview(warn_within_days=5)
check("a shorter window leaves it out of 'soon'",
      CID not in [x["id"] for x in over["soon"]], over)

# ─── Clearing, and refusals ──────────────────────────────────────────────────

svc.set_retention(CID, None)
c = one()
check("a date can be removed again", c["retention_until"] is None, c)
check("clearing it also clears the note", c["retention_note"] is None, c)
check("and the course is undecided once more",
      CID in [x["id"] for x in svc.retention_overview()["undecided"]])

try:
    svc.set_retention(CID, date(2019, 5, 1))
    check("a date before 2021 is refused", False, "it did not raise")
except svc.CourseError as exc:
    check("a date before 2021 is refused", "slip" in str(exc).lower(), str(exc))

try:
    svc.set_retention("no-such-course", date.today() + timedelta(days=1))
    check("an unknown course is refused", False, "it did not raise")
except svc.CourseError:
    check("an unknown course is refused", True)

# The database has to refuse it too — the application is not the only writer,
# and a migration that forgot its constraint is invisible from the service.
try:
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE courses SET retention_until = %s WHERE id = %s",
                        (date(2019, 1, 1), CID))
        conn.commit()
    check("the database refuses a nonsense date too", False, "the UPDATE went through")
except Exception:  # noqa: BLE001 — psycopg's CheckViolation
    check("the database refuses a nonsense date too", True)

reset()

if failures:
    print("FAILURES:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)

print("All retention checks passed: migration 003 is applied and every course")
print("carries a retention date, a reason and a record of having been acted on;")
print("no date means undecided rather than not-due, a future date is set with")
print("the days remaining, today counts as due and a past date reports a")
print("negative count rather than none; the overview keeps due, soon, undecided")
print("and already-handled apart, an expiry stops warning once it has been")
print("acted on, and giving a course a new period clears that handling so the")
print("new expiry warns again; a date can be cleared along with its note, and")
print("a date before 2021 is refused by the service with a sentence and by the")
print("database with a constraint, so neither is the only thing standing there.")
