"""
Thin wrapper around the n8n ingest webhook.

One call: hand an uploaded document to n8n/workflows-ingest/
ingest-document.json, which converts it (Docling), describes any figures
(the course's configured LLM_PROVIDER), archives the markdown to MinIO,
chunks it, embeds it, and writes it to Weaviate — all asynchronously.

The file is streamed straight through to n8n as multipart/form-data,
never staged on this container's own disk. Same architectural boundary
the rest of this GUI keeps: app-to-app REST calls over the internal
Docker network only, no heavy processing and no host filesystem work
here (see the module docstring in app.py).

The webhook answers immediately (responseMode: onReceived) rather than
when processing finishes — deliberate, and the reason there's no result
to return beyond "accepted". A large scanned PDF with many figures can
legitimately take tens of minutes; content-admin runs a single gunicorn
worker, so blocking on that would freeze the GUI for every other user.
The uploader gets an email when it's actually done.
"""

import requests


class N8nError(RuntimeError):
    """Raised with n8n's own error text — never swallowed silently, the
    GUI shows it to the operator verbatim so a failed upload is
    diagnosable."""


class N8nClient:
    def __init__(self, base_url: str, timeout: int = 60):
        self.base = base_url.rstrip("/")
        self.timeout = timeout

    # ─── internals ──────────────────────────────────────────────────────────
    def _request(self, method: str, path: str, **kwargs):
        url = f"{self.base}{path}"
        try:
            resp = requests.request(method, url, timeout=self.timeout, **kwargs)
        except requests.RequestException as exc:
            raise N8nError(f"{method} {url} failed: {exc}") from exc

        if not resp.ok:
            raise N8nError(
                f"{method} {path} → HTTP {resp.status_code}: {resp.text[:500]}"
            )
        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return resp.text

    # ─── document ingest ────────────────────────────────────────────────────
    def upload_document(
        self,
        file_stream,
        filename: str,
        content_type: str,
        agent_id: int,
        title: str,
        authors: str = "",
        year: str = "",
        topic: str = "",
        language: str = "de",
        force_ocr: bool = False,
        notify_email: str = "",
    ) -> None:
        """
        Forwards one uploaded file plus its metadata to the ingest webhook.
        Field names here must match what ingest-document.json reads out of
        $json.body — the file field is "file" (the Docling node's
        inputDataFieldName), everything else is a plain form field.

        Returns nothing on success: the webhook acknowledges receipt, it
        does not report the outcome of the actual conversion.
        """
        files = {"file": (filename, file_stream, content_type)}
        data = {
            "agent_id": str(agent_id),
            "title": title,
            "authors": authors,
            "year": year,
            "topic": topic,
            "language": language,
            "force_ocr": "true" if force_ocr else "false",
        }
        if notify_email:
            data["notify_email"] = notify_email

        self._request("POST", "/webhook/document-ingest", files=files, data=data)
