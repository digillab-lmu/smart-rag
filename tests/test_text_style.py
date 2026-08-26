"""The register of every string the interface shows.

Written after the operator read the People page back to me: *"diese Texte
sind vom Charakter her so schlimm KI-Style… Es scheint, du formulierst hier
zu viel so, wie du nachdenkst."* That was right, and it names the fault
precisely. The texts explained how decisions had been reached to people who
were not present when they were reached and have no reason to care. They
addressed the reader as a person addresses a person, with a confidence
software should not have. And they let processes act: *"eine Löschung … kann
nicht behaupten"*.

What this file enforces is therefore not tone in the decorative sense. It is
four properties of ordinary software documentation:

  * **No voice.** The program is not a person and does not speak as one. No
    self-reference, no confiding, no rhetorical questions.
  * **No design history.** What a reader needs is what the control does and
    what happens next — not why it was built this way, what was rejected, or
    what was learned. That belongs in commit messages and in docs/, where it
    is written for people who are choosing how to change the software.
  * **No anthropomorphism.** A deletion does not claim, a page does not say,
    a map does not know.
  * **Facts, short.** One statement per sentence, and no sentence that exists
    only to prepare the next one.

Checked mechanically where a rule can be, because 581 keys in two languages
cannot be held to a style by memory.
"""

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
APP_DIR = REPO / "content-admin"
if not APP_DIR.is_dir() and Path("/app/db.py").exists():
    APP_DIR = Path("/app")
sys.path.insert(0, str(APP_DIR))

import i18n  # noqa: E402

failures = []


def check(name, ok, detail=""):
    if not ok:
        failures.append(f"{name}: {detail}")


CATALOGUES = {"en": i18n.MSG_EN, "de": i18n.MSG_DE}

# Keys whose value is deliberately not ordinary interface prose: a prompt
# written to be pasted into a chat window, and the text of an email.
EXEMPT = {"graph_prompt_text", "reset_mail_body", "reset_mail_subject",
          "footer_credit"}

# ─── A process is not an actor ───────────────────────────────────────────────
ANTHROPOMORPHIC = {
    "de": re.compile(
        r"\b(die|der|das|eine|ein)?\s*(Löschung|Seite|Karte|Datei|Abfrage|"
        r"Aufbau|System|Programm|Vorschlag|Graph|Antwort)\s+"
        # "kann" is left out on purpose: in German it is almost always part
        # of a passive ("die Seite kann geschlossen werden"), and including it
        # flagged nine correct sentences and no incorrect one.
        r"(will|weiß|behauptet|sagt|meint|denkt|glaubt|entscheidet sich|"
        r"hält sich|beansprucht)", re.I),
    "en": re.compile(
        r"\b(the\s+)?(deletion|erasure|page|map|file|query|build|system|program|"
        r"proposal|graph|answer)\s+"
        r"(claims|thinks|knows|believes|says|wants|decides|feels)\b", re.I),
}

# ─── Rationale belongs in the repository, not on the screen ──────────────────
RATIONALE = {
    "de": [
        "der grund", "lohnt sich zu lesen", "deshalb ist es",
        "das ist der grund", "genau deshalb", "man muss sich klarmachen",
        "wie gesagt", "ehrlich gesagt", "das heißt nicht, dass",
        "aus gutem grund", "historisch", "früher hieß", "war frueher",
        "das ist der punkt", "und zwar deshalb",
    ],
    "en": [
        "the reason is", "worth reading", "that is why it", "which is why this",
        "to be honest", "the point is", "for good reason", "historically",
        "used to be called", "it turns out",
    ],
}

# ─── Sentences that only prepare the next one ────────────────────────────────
FILLER = {
    "de": ["im grunde", "letztlich", "eigentlich ist", "bekanntlich",
           "natürlich ist", "wie man sieht", "es sei gesagt"],
    "en": ["basically", "essentially", "at the end of the day", "as you can see",
           "needless to say", "of course this"],
}

for lang, catalogue in CATALOGUES.items():
    for key, text in sorted(catalogue.items()):
        if key in EXEMPT:
            continue
        low = text.lower()

        # An escape that reached the screen. Twice now a rewrite has been
        # applied through a JSON batch and escaped a second time on the way
        # in, so the interface displayed \u201esub\u201c where it meant to
        # show quotation marks. Only a screenshot caught the first one.
        if re.search(r"\\u[0-9a-fA-F]{4}", text) or "\\n" in text.replace("\n", ""):
            check(f"{lang}/{key}: no escape sequence shown as text", False,
                  text[:110])

        if ANTHROPOMORPHIC[lang].search(text):
            check(f"{lang}/{key}: no process is given a will", False,
                  ANTHROPOMORPHIC[lang].search(text).group(0))

        for phrase in RATIONALE[lang]:
            if phrase in low:
                check(f"{lang}/{key}: no design rationale on screen", False,
                      f"{phrase!r} in {text[:90]!r}")
                break

        for phrase in FILLER[lang]:
            if phrase in low:
                check(f"{lang}/{key}: no filler", False, f"{phrase!r}")
                break

        # An em dash chaining one explanation onto another is the shape of
        # thinking aloud. One is a legitimate aside; two is an argument. Not
        # applied to short strings, where a pair of dashes is decoration
        # around a placeholder rather than an argument.
        if len(text) > 60 and text.count("—") > 1:
            check(f"{lang}/{key}: at most one aside per string", False,
                  text[:120])

        # Rhetorical questions address the reader as a conversation partner.
        # A confirmation dialog is the exception: it asks, then states the
        # consequences after a blank line, which is what a dialog is.
        # A confirmation reads "Delete X? Consequences." — the question is
        # the first sentence and the rest states what follows. Only a question
        # buried later in the text is addressing the reader rhetorically.
        head, mark, rest = text.partition("?")
        tail = rest if mark and len(head) < 120 else text
        if "?" in tail and not tail.rstrip().endswith("?"):
            check(f"{lang}/{key}: no rhetorical question mid-sentence", False,
                  text[:120])

        # Length. Help text that runs past this is explaining rather than
        # telling; the limit is generous and the intros need their own.
        # Markup is not reading: <strong> and an anchor's href add characters
        # the reader never sees, and measuring them made three correct texts
        # look long.
        visible = re.sub(r"<[^>]+>", "", text)
        limit = 600 if key.endswith(("_intro", "_howto_1", "_howto_2",
                                     "_howto_3", "_howto_4", "_howto_5",
                                     "_message", "_warning", "_step3")) else 320
        if len(visible) > limit:
            check(f"{lang}/{key}: shorter than {limit} characters", False,
                  f"{len(visible)} characters")

# ─── German interface texts do not address the reader personally ─────────────
# Ordinary German software is impersonal. "Du" gives the program a voice and
# a relationship with the reader, which is the thing being removed here.
DU = re.compile(r"\b(du|dir|dich|dein|deine|deinen|deinem|deiner|deines)\b", re.I)
for key, text in sorted(i18n.MSG_DE.items()):
    if key in EXEMPT:
        continue
    m = DU.search(text)
    check(f"de/{key}: impersonal, not second person", m is None,
          m.group(0) if m else "")

# ─── The installer speaks to the same reader ────────────────────────────────
# scripts/lib/messages.sh is the other half of the interface: 845 keys that an
# operator reads once, at the point of least context they will ever have. The
# same rules apply, checked the same way.
import subprocess  # noqa: E402

dump = subprocess.run(
    ["bash", "-c",
     'source scripts/lib/messages.sh 2>/dev/null; '
     'for k in "${!MSG_EN[@]}"; do printf "en\t%s\t%s\n" "$k" "${MSG_EN[$k]}"; done; '
     'for k in "${!MSG_DE[@]}"; do printf "de\t%s\t%s\n" "$k" "${MSG_DE[$k]}"; done'],
    cwd=REPO, capture_output=True, text=True)

rows = [line.split("\t", 2) for line in dump.stdout.splitlines()
        if line.count("\t") >= 2]
check("the installer catalogue could be read", len(rows) > 500, len(rows))

# The handover message is a letter to a colleague and the prompt is meant to
# be pasted; neither is interface prose.
SH_EXEMPT_PREFIX = ("handover_body", "handover_mail", "handover_subject")

for lang, key, text in rows:
    if key.startswith(SH_EXEMPT_PREFIX):
        continue
    if ANTHROPOMORPHIC[lang].search(text):
        check(f"sh/{lang}/{key}: no process is given a will", False, text[:100])
    for phrase in RATIONALE[lang] + FILLER[lang]:
        if phrase in text.lower():
            check(f"sh/{lang}/{key}: no rationale or filler", False, phrase)
            break
    if len(text) > 60 and text.count("—") > 1:
        check(f"sh/{lang}/{key}: at most one aside per string", False, text[:110])
    head, mark, rest = text.partition("?")
    tail = rest if mark and len(head) < 120 else text
    if "?" in tail and not tail.rstrip().endswith("?"):
        check(f"sh/{lang}/{key}: no rhetorical question mid-sentence", False,
              text[:110])
    if len(text) > 340:
        check(f"sh/{lang}/{key}: shorter than 340 characters", False,
              f"{len(text)} characters")
    if lang == "de" and DU.search(text):
        check(f"sh/de/{key}: impersonal, not second person", False,
              DU.search(text).group(0))

if failures:
    print("FAILURES:")
    for f in failures[:60]:
        print(f"  - {f}")
    if len(failures) > 60:
        print(f"  … and {len(failures) - 60} more")
    sys.exit(1)

print("All interface-text checks passed: no string gives a process a will, "
      "explains why the software was built as it was, pads with filler, "
      "chains two asides onto one sentence, asks the reader a rhetorical "
      "question, runs past its length limit, or addresses the reader as du.")
