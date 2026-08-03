"""
Thin FastAPI wrapper around the `markdowncleaner` PyPI package
(https://github.com/josk0/markdowncleaner, MIT), used by
n8n/workflows-ingest/ingest-document.json to strip references/footnotes/
copyright-notice sections and fix PDF-to-markdown conversion artifacts
(mojibake, broken linebreaks) before an uploaded document is chunked.

Verified against the original VHB deployment's actual running container
(docker inspect + `docker exec markdowncleaner cat /app/app.py`) rather
than reconstructed from guesswork — this is a faithful copy of that
service, not a reinterpretation. Default option values below are exactly
what that container used; the original ingest workflow never sent
anything but `markdown` in its request body, so those defaults are what
it always ran with in practice.
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from markdowncleaner import MarkdownCleaner, CleanerOptions

app = FastAPI(title="MarkdownCleaner Service")


class CleanRequest(BaseModel):
    markdown: str
    remove_short_lines: bool = False  # Default OFF -- otherwise short headlines get lost
    remove_references: bool = True
    remove_sections: bool = True      # Strips Acknowledgements, References, etc.
    fix_encoding: bool = True
    crimp_linebreaks: bool = True


class CleanResponse(BaseModel):
    cleaned: str
    original_length: int
    cleaned_length: int


@app.post("/clean", response_model=CleanResponse)
def clean_markdown(req: CleanRequest):
    try:
        options = CleanerOptions()
        options.remove_short_lines = req.remove_short_lines
        options.remove_sections = req.remove_sections
        options.remove_references_heuristically = req.remove_references
        options.fix_encoding_mojibake = req.fix_encoding
        options.crimp_linebreaks = req.crimp_linebreaks
        options.contract_empty_lines = True
        options.remove_footnotes_in_text = True
        options.remove_duplicate_headlines = True
        cleaner = MarkdownCleaner(options=options)
        cleaned = cleaner.clean_markdown_string(req.markdown)
        return CleanResponse(
            cleaned=cleaned,
            original_length=len(req.markdown),
            cleaned_length=len(cleaned),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
def health():
    return {"status": "ok"}
