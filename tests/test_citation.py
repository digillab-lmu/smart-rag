import io
import sys

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
APP_DIR = str(REPO / "content-admin")

sys.path.insert(0, APP_DIR)

import citation as c  # noqa: E402


failures = []


def check(name, cond, detail=""):
    if not cond:
        failures.append(f"{name}: {detail}")


# ── DOI normalisation ───────────────────────────────────────────────────────
for raw, want in [
    ("10.3389/fpsyg.2019.02364", "10.3389/fpsyg.2019.02364"),
    ("https://doi.org/10.1037/0022-0663.93.1.187", "10.1037/0022-0663.93.1.187"),
    ("http://dx.doi.org/10.1037/abc", "10.1037/abc"),
    ("doi:10.1037/abc", "10.1037/abc"),
    ("  10.1037/abc  ", "10.1037/abc"),
    ("See 10.1037/abc, page 4", "10.1037/abc"),   # copied out of running text
    ("10.1037/abc.", "10.1037/abc"),              # trailing sentence punctuation
]:
    try:
        got = c.normalize_doi(raw)
        check(f"normalize_doi({raw!r})", got == want, f"got {got!r}, want {want!r}")
    except c.CitationError as exc:
        failures.append(f"normalize_doi({raw!r}) raised: {exc}")

for bad in ["", "not-a-doi", "11.1234/xyz", "10.12/tooshort"]:
    try:
        c.normalize_doi(bad)
        failures.append(f"normalize_doi({bad!r}) should have raised")
    except c.CitationError:
        pass

# ── ISBN normalisation + checksums ──────────────────────────────────────────
for raw, want in [
    ("978-1-107-03520-1", "9781107035201"),
    ("9781107035201", "9781107035201"),
    ("ISBN 978-1-107-03520-1", "9781107035201"),
    ("0-306-40615-2", "0306406152"),
    ("043942089X", "043942089X"),                 # ISBN-10 with X check digit
]:
    try:
        got = c.normalize_isbn(raw)
        check(f"normalize_isbn({raw!r})", got == want, f"got {got!r}, want {want!r}")
    except c.CitationError as exc:
        failures.append(f"normalize_isbn({raw!r}) raised: {exc}")

for bad, why in [
    ("9781107035202", "bad ISBN-13 checksum"),
    ("0-306-40615-3", "bad ISBN-10 checksum"),
    ("12345", "too short"),
    ("", "empty"),
    ("97811070352011234", "too long"),
]:
    try:
        c.normalize_isbn(bad)
        failures.append(f"normalize_isbn({bad!r}) should have raised ({why})")
    except c.CitationError:
        pass

# ── Author formatting ───────────────────────────────────────────────────────
check(
    "_format_authors surname-first",
    c._format_authors([("Mayer", "Richard E."), ("Moreno", "Roxana")])
    == "Mayer, Richard E.; Moreno, Roxana",
    c._format_authors([("Mayer", "Richard E."), ("Moreno", "Roxana")]),
)
check("_format_authors: family only", c._format_authors([("Mayer", "")]) == "Mayer")
check("_format_authors: empty", c._format_authors([]) == "")
check("_surname_first", c._surname_first("Richard E. Mayer") == "Mayer, Richard E.",
      c._surname_first("Richard E. Mayer"))
check("_surname_first: already inverted", c._surname_first("Mayer, Richard") == "Mayer, Richard")
check("_surname_first: single word", c._surname_first("Aristotle") == "Aristotle")
check("_year_from_text", c._year_from_text("Published 2014 in Cambridge") == "2014")
check("_year_from_text: none", c._year_from_text("no date here") == "")

# ── PDF scanning ────────────────────────────────────────────────────────────
# Not a PDF at all — must degrade to "nothing found", never raise.
check("scan_pdf on garbage returns {}", c.scan_pdf(io.BytesIO(b"this is not a pdf")) == {})
check("scan_pdf on empty returns {}", c.scan_pdf(io.BytesIO(b"")) == {})

# ── Lookup dispatch + provider fallback (network stubbed) ───────────────────
real_ol, real_gb, real_get = (
    c._lookup_isbn_openlibrary, c._lookup_isbn_googlebooks, c._get,
)


def restore():
    c._lookup_isbn_openlibrary = real_ol
    c._lookup_isbn_googlebooks = real_gb
    c._get = real_get


# Primary answers -> fallback must not be called at all
calls = []
c._lookup_isbn_openlibrary = lambda i: (calls.append("ol") or {"source": "Open Library"})
c._lookup_isbn_googlebooks = lambda i: (_ for _ in ()).throw(AssertionError("must not run"))
check("ISBN: primary wins", c.lookup_isbn("9781107035201")["source"] == "Open Library")
check("ISBN: fallback skipped when primary answers", calls == ["ol"], calls)

# Primary finds nothing -> fallback gets a turn
c._lookup_isbn_openlibrary = lambda i: None
c._lookup_isbn_googlebooks = lambda i: {"source": "Google Books"}
check("ISBN: falls back on no-match", c.lookup_isbn("9781107035201")["source"] == "Google Books")

# Primary *errors* (timeout/HTTP) -> fallback must still get a turn. This
# was a real bug: only no-match was treated as fallback-worthy, so an
# Open Library timeout skipped Google Books entirely.
c._lookup_isbn_openlibrary = lambda i: (_ for _ in ()).throw(c.CitationError("timeout"))
c._lookup_isbn_googlebooks = lambda i: {"source": "Google Books"}
check("ISBN: falls back on provider error", c.lookup_isbn("9781107035201")["source"] == "Google Books")

# Both fail -> CitationNotFound, and the reasons are surfaced for diagnosis
c._lookup_isbn_googlebooks = lambda i: (_ for _ in ()).throw(c.CitationError("429 quota"))
try:
    c.lookup_isbn("9781107035201")
    failures.append("ISBN: both failing should raise CitationNotFound")
except c.CitationNotFound as exc:
    check("ISBN: not-found mentions both failures",
          "timeout" in str(exc) and "429 quota" in str(exc), str(exc))
except c.CitationError as exc:
    failures.append(f"ISBN: wrong exception type: {type(exc).__name__}: {exc}")
restore()


# ── Crossref response parsing (stubbed HTTP) ────────────────────────────────
class FakeResp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self.ok = 200 <= status < 300
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


CROSSREF = {
    "message": {
        "title": ["Cognitive constraints on multimedia learning"],
        "author": [
            {"given": "Richard E.", "family": "Mayer"},
            {"given": "Julie", "family": "Heiser"},
        ],
        "issued": {"date-parts": [[2001, 3]]},
        "container-title": ["Journal of Educational Psychology"],
        "volume": "93",
        "issue": "1",
        "page": "187-198",
        "DOI": "10.1037/0022-0663.93.1.187",
        "subject": ["Education", "Developmental Psychology"],
    }
}

c._get = lambda url, **kw: FakeResp(200, CROSSREF)
r = c.lookup_doi("10.1037/0022-0663.93.1.187")
check("DOI: title", r["title"] == "Cognitive constraints on multimedia learning", r["title"])
check("DOI: authors surname-first", r["authors"] == "Mayer, Richard E.; Heiser, Julie", r["authors"])
check("DOI: year from date-parts", r["year"] == "2001", r["year"])
check("DOI: subjects become topic", "Education" in r["topic"], r["topic"])
check("DOI: source label", r["source"] == "Crossref")
for part in ["Mayer, Richard E.", "(2001)", "Journal of Educational Psychology", "93(1)", "187-198", "doi.org"]:
    check(f"DOI citation contains {part!r}", part in r["citation"], r["citation"])

c._get = lambda url, **kw: FakeResp(404)
try:
    c.lookup_doi("10.1037/nope")
    failures.append("DOI 404 should raise CitationNotFound")
except c.CitationNotFound:
    pass

c._get = lambda url, **kw: FakeResp(500)
try:
    c.lookup_doi("10.1037/abc")
    failures.append("DOI 500 should raise CitationError")
except c.CitationNotFound:
    failures.append("DOI 500 raised NotFound, should be a plain CitationError")
except c.CitationError:
    pass

# Missing optional fields must not break parsing
c._get = lambda url, **kw: FakeResp(200, {"message": {"title": ["Bare"]}})
r = c.lookup_doi("10.1037/bare")
check("DOI: tolerates missing author/issued/container",
      r["title"] == "Bare" and r["authors"] == "" and r["year"] == "", r)
restore()

if failures:
    print("FAILURES:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print(
    "All citation.py checks passed: DOI normalisation (bare/URL/doi:/in-prose/trailing "
    "punctuation, plus rejections), ISBN normalisation with ISBN-10 and ISBN-13 checksum "
    "validation, author/year formatting helpers, scan_pdf degrading safely on non-PDF input, "
    "ISBN provider fallback (primary wins, falls back on both no-match and provider error, "
    "both-failed surfaces reasons), and Crossref parsing including 404/500 handling and "
    "records missing optional fields."
)
