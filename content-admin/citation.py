"""
Bibliographic lookup for the document-upload form.

Three ways to avoid retyping citation data, in the order an operator is
likely to reach for them:

  1. Read a DOI or ISBN straight out of the uploaded PDF (scan_pdf), then
     look it up — no typing at all for a normal journal article.
  2. Paste a DOI and look it up (lookup_doi, via Crossref).
  3. Paste an ISBN and look it up (lookup_isbn, via Open Library, with
     Google Books as a fallback).

All three converge on the same dict shape, so the route and the GUI treat
them identically:

    {"title", "authors", "year", "topic", "citation", "source"}

`authors` is already formatted the way the upload form documents it
("Surname, Given; Surname2, Given2"), and `citation` is a rough APA-style
line purely for the confirm-before-applying preview — the operator sees
what was found before any field is touched.

Endpoints and response shapes were verified against the live services,
not assumed:
  - Crossref:     https://api.crossref.org/works/{doi}  (free, no key;
                  a mailto in the User-Agent is their documented courtesy
                  for better rate limits)
  - Open Library: https://openlibrary.org/api/books?bibkeys=ISBN:...
  - Google Books: https://www.googleapis.com/books/v1/volumes?q=isbn:...
                  (fallback only — it enforces a per-day quota and was
                  observed returning HTTP 429 without a key, so it must
                  never be the sole source)

Known limitation, surfaced to the operator rather than hidden: ISBN
coverage for German-language academic books is patchy in both services.
A miss returns CitationNotFound, and the form falls back to typing.
"""

from __future__ import annotations

import logging
import re

import requests

# pypdf narrates malformed-but-readable PDFs at WARNING level (duplicate
# /MediaBox keys and the like). Those are not actionable here — we only
# want the text — and they'd otherwise flood the container log on every
# scan.
logging.getLogger("pypdf").setLevel(logging.ERROR)

_TIMEOUT = 15
# Crossref asks API users to identify themselves; a contact address gets
# you into their "polite" pool rather than the anonymous one.
_USER_AGENT = "smartrag-content-admin/1.0 (https://github.com/digillab-lmu/smart-rag)"

# Matches the DOI syntax registered DOIs actually use (10.<registrant>/<suffix>).
DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", re.I)
# ISBN-10/13, with or without the "ISBN" prefix and separators.
ISBN_LABELLED_RE = re.compile(
    r"ISBN(?:-1[03])?:?\s*((?:97[89][-\s]?)?(?:\d[-\s]?){9}[\dXx])", re.I
)


class CitationError(RuntimeError):
    """Lookup failed for a reason the operator can act on — shown verbatim."""


class CitationNotFound(CitationError):
    """The identifier was well-formed but nothing was found for it."""


# ─── identifier normalisation ────────────────────────────────────────────
def normalize_doi(raw: str) -> str:
    """Accepts what people actually paste: a bare DOI, a doi.org URL, or a
    'doi:' prefix."""
    value = (raw or "").strip()
    value = re.sub(r"^https?://(dx\.)?doi\.org/", "", value, flags=re.I)
    value = re.sub(r"^doi:\s*", "", value, flags=re.I)
    match = DOI_RE.search(value)
    if not match:
        raise CitationError(f"Not a valid DOI: {raw!r}")
    # Trailing punctuation is common when a DOI is copied out of running text.
    return match.group(0).rstrip(".,;)")


def normalize_isbn(raw: str) -> str:
    """Strips separators and validates the checksum, so an obvious typo is
    caught here instead of coming back as a confusing 'not found'."""
    digits = re.sub(r"[^0-9Xx]", "", raw or "").upper()
    if len(digits) == 10:
        if not _isbn10_valid(digits):
            raise CitationError(f"ISBN-10 checksum doesn't match: {raw!r}")
        return digits
    if len(digits) == 13:
        if not _isbn13_valid(digits):
            raise CitationError(f"ISBN-13 checksum doesn't match: {raw!r}")
        return digits
    raise CitationError(f"Not a valid ISBN (needs 10 or 13 digits): {raw!r}")


def _isbn10_valid(isbn: str) -> bool:
    total = 0
    for i, char in enumerate(isbn):
        value = 10 if char == "X" else (int(char) if char.isdigit() else -1)
        if value < 0 or (char == "X" and i != 9):
            return False
        total += value * (10 - i)
    return total % 11 == 0


def _isbn13_valid(isbn: str) -> bool:
    if not isbn.isdigit():
        return False
    total = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(isbn))
    return total % 10 == 0


# ─── PDF scanning ────────────────────────────────────────────────────────
def scan_pdf(file_stream, max_pages: int = 3) -> dict[str, str]:
    """
    Pulls a DOI and/or ISBN out of the front matter of a PDF. Only the
    first few pages are read: identifiers live on the title page or the
    imprint page, and parsing a 900-page scan to find them would be a
    waste of the request's time.

    Returns {"doi": ..., "isbn": ..., "text": ...} with only the keys
    actually found; "text" is the extracted front matter, kept so a caller
    can reuse it (keyword suggestions) without making the operator upload
    the same file twice. Never raises for an unreadable file — a scan that
    finds nothing is a normal outcome, not an error.
    """
    try:
        from pypdf import PdfReader

        reader = PdfReader(file_stream)
        text = ""
        for page in reader.pages[:max_pages]:
            text += (page.extract_text() or "") + "\n"
    except Exception:  # noqa: BLE001 — encrypted, corrupt, or not a PDF
        return {}

    found: dict[str, str] = {}
    if text.strip():
        found["text"] = text

    doi_match = DOI_RE.search(text)
    if doi_match:
        try:
            found["doi"] = normalize_doi(doi_match.group(0))
        except CitationError:
            pass

    isbn_match = ISBN_LABELLED_RE.search(text)
    if isbn_match:
        try:
            found["isbn"] = normalize_isbn(isbn_match.group(1))
        except CitationError:
            # A mis-OCR'd ISBN is common; treat it as "not found" rather
            # than surfacing a checksum complaint about text the operator
            # never typed.
            pass

    return found


# ─── lookups ─────────────────────────────────────────────────────────────
def _get(url: str, **kwargs):
    try:
        return requests.get(
            url, headers={"User-Agent": _USER_AGENT}, timeout=_TIMEOUT, **kwargs
        )
    except requests.RequestException as exc:
        raise CitationError(f"Lookup service unreachable: {exc}") from exc


def lookup_doi(raw_doi: str) -> dict[str, str]:
    doi = normalize_doi(raw_doi)
    resp = _get(f"https://api.crossref.org/works/{doi}")
    if resp.status_code == 404:
        raise CitationNotFound(f"No record found for DOI {doi}")
    if not resp.ok:
        raise CitationError(f"Crossref returned HTTP {resp.status_code}")

    try:
        message = resp.json()["message"]
    except (ValueError, KeyError) as exc:
        raise CitationError("Unexpected response from Crossref") from exc

    title = (message.get("title") or [""])[0]
    authors = _format_authors(
        (a.get("family", ""), a.get("given", "")) for a in message.get("author") or []
    )
    year = _first_year(message.get("issued"))
    container = (message.get("container-title") or [""])[0]
    topic = ", ".join(
        s for s in (message.get("subject") or [])[:6] if isinstance(s, str)
    )

    return {
        "title": title,
        "authors": authors,
        "year": year,
        "topic": topic,
        "citation": _apa_line(
            authors,
            year,
            title,
            container,
            message.get("volume", ""),
            message.get("issue", ""),
            message.get("page", ""),
            doi,
        ),
        "source": "Crossref",
    }


def lookup_isbn(raw_isbn: str) -> dict[str, str]:
    isbn = normalize_isbn(raw_isbn)

    # Each provider is tried in turn and any failure — no match, an HTTP
    # error, or the service being unreachable — moves on to the next.
    # Treating only "no match" as fallback-worthy was a real bug caught in
    # testing: Open Library timed out, the error propagated, and Google
    # Books never got a turn even though it could have answered.
    errors: list[str] = []
    for provider in (_lookup_isbn_openlibrary, _lookup_isbn_googlebooks):
        try:
            result = provider(isbn)
        except CitationError as exc:
            errors.append(str(exc))
            continue
        if result is not None:
            return result

    detail = f" ({'; '.join(errors)})" if errors else ""
    raise CitationNotFound(
        f"No record found for ISBN {isbn}. Coverage for German-language "
        f"academic titles is incomplete — please enter the details manually.{detail}"
    )


def _lookup_isbn_openlibrary(isbn: str) -> dict[str, str] | None:
    resp = _get(
        "https://openlibrary.org/api/books",
        params={"bibkeys": f"ISBN:{isbn}", "format": "json", "jscmd": "data"},
    )
    if not resp.ok:
        return None
    try:
        payload = resp.json()
    except ValueError:
        return None
    entry = payload.get(f"ISBN:{isbn}")
    if not entry:
        return None

    title = entry.get("title", "")
    if entry.get("subtitle"):
        title = f"{title}: {entry['subtitle']}"
    # Open Library gives one display name per author, not split name parts.
    authors = "; ".join(
        _surname_first(a.get("name", "")) for a in entry.get("authors") or []
    )
    year = _year_from_text(entry.get("publish_date", ""))
    publisher = "; ".join(p.get("name", "") for p in entry.get("publishers") or [])
    topic = ", ".join(
        s.get("name", "") for s in (entry.get("subjects") or [])[:6]
    )

    return {
        "title": title,
        "authors": authors,
        "year": year,
        "topic": topic,
        "citation": _apa_book_line(authors, year, title, publisher),
        "source": "Open Library",
    }


def _lookup_isbn_googlebooks(isbn: str) -> dict[str, str] | None:
    resp = _get(
        "https://www.googleapis.com/books/v1/volumes", params={"q": f"isbn:{isbn}"}
    )
    # 429 (daily quota without an API key) is expected often enough that it
    # must degrade to "no result" rather than an error — Open Library has
    # already had its turn by this point.
    if not resp.ok:
        return None
    try:
        payload = resp.json()
    except ValueError:
        return None
    items = payload.get("items") or []
    if not items:
        return None

    info = items[0].get("volumeInfo", {})
    title = info.get("title", "")
    if info.get("subtitle"):
        title = f"{title}: {info['subtitle']}"
    authors = "; ".join(_surname_first(a) for a in info.get("authors") or [])
    year = _year_from_text(info.get("publishedDate", ""))

    return {
        "title": title,
        "authors": authors,
        "year": year,
        "topic": ", ".join((info.get("categories") or [])[:6]),
        "citation": _apa_book_line(authors, year, title, info.get("publisher", "")),
        "source": "Google Books",
    }


# ─── formatting helpers ──────────────────────────────────────────────────
def _format_authors(pairs) -> str:
    """Matches the format the upload form asks for: surname first,
    semicolon-separated."""
    out = []
    for family, given in pairs:
        family, given = (family or "").strip(), (given or "").strip()
        if family and given:
            out.append(f"{family}, {given}")
        elif family or given:
            out.append(family or given)
    return "; ".join(out)


def _surname_first(display_name: str) -> str:
    """"Richard E. Mayer" -> "Mayer, Richard E." — for services that only
    return a display name."""
    name = (display_name or "").strip()
    if not name or "," in name:
        return name
    parts = name.split()
    if len(parts) < 2:
        return name
    return f"{parts[-1]}, {' '.join(parts[:-1])}"


def _first_year(issued) -> str:
    try:
        return str(issued["date-parts"][0][0])
    except (TypeError, KeyError, IndexError):
        return ""


def _year_from_text(text: str) -> str:
    match = re.search(r"\b(1[5-9]\d{2}|20\d{2})\b", text or "")
    return match.group(1) if match else ""


def _apa_line(authors, year, title, container, volume, issue, page, doi) -> str:
    bits = [f"{authors} ({year or 'n.d.'})." if authors else f"({year or 'n.d.'})."]
    if title:
        bits.append(f"{title.rstrip('.')}.")
    if container:
        locator = container
        if volume:
            locator += f", {volume}"
            if issue:
                locator += f"({issue})"
        if page:
            locator += f", {page}"
        bits.append(f"{locator}.")
    if doi:
        bits.append(f"https://doi.org/{doi}")
    return " ".join(bits)


def _apa_book_line(authors, year, title, publisher) -> str:
    bits = [f"{authors} ({year or 'n.d.'})." if authors else f"({year or 'n.d.'})."]
    if title:
        bits.append(f"{title.rstrip('.')}.")
    if publisher:
        bits.append(f"{publisher}.")
    return " ".join(bits)
