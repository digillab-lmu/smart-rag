"""
The signer, checked against AWS's own published test vector.

This module exists because Garage's admin API — which creates the buckets —
has no object operations and refuses to delete a bucket that is not empty. So
a deleted course would leave its documents behind, and the next course created
with the same id would silently adopt them, because bucket creation is
idempotent by design.

Signature Version 4 is written out rather than imported: boto3 is some ninety
megabytes for two calls, and the specification has no room for interpretation.
But "no room for interpretation" is not the same as "correct", and the failure
mode of a signer is a 403 at the moment somebody is deleting something. So the
pure signing function is checked against `get-vanilla` from AWS's own
SigV4 test suite, whose expected signature is published.

Writing this test found the value of doing it: the first run disagreed with
the published signature. Because the suite also publishes the canonical
request and the string to sign, the disagreement could be localised instead of
guessed at — both matched exactly, so only the derived key could be wrong, and
the cause was the secret. The test suite uses `…MDENG+bPx…`; the better-known
example in the S3 documentation uses `…MDENG/bPx…`. One character, in an
assumption rather than in the code.
"""

import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
APP = REPO / "content-admin"
if not APP.is_dir() and Path("/app/db.py").exists():
    APP = Path("/app")
sys.path.insert(0, str(APP))

env = Path(tempfile.mkdtemp()) / ".env"
env.write_text('GARAGE_ACCESS_KEY="GKtest"\nGARAGE_SECRET_KEY="secret"\n'
               'GARAGE_REGION="eu-central-1"\n')
os.environ["SMARTRAG_ENV_PATH"] = str(env)

import s3_client  # noqa: E402

failures = []


def check(name, ok, detail=""):
    if not ok:
        failures.append(f"{name}: {detail}")


# ─── 1. AWS's own vector ─────────────────────────────────────────────────────
# get-vanilla: GET / with two signed headers, service "service", region
# us-east-1. Nothing this application ever sends looks like this, which is the
# point — a signer that can only be exercised through its single call site is
# one whose correctness rests on a 403 not happening.
VECTOR_SECRET = "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY"
signature = s3_client.sign(
    VECTOR_SECRET, "GET", "/", "",
    {"host": "example.amazonaws.com", "x-amz-date": "20150830T123600Z"},
    s3_client._sha256(b""), "20150830T123600Z", "us-east-1", "service")
check("the signature matches AWS's published one",
      signature == "5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31",
      signature)

# The same inputs must always give the same signature, and a changed input
# must change it — a signer that ignores part of its input passes the vector
# above by accident once and fails in production for ever.
same = s3_client.sign(
    VECTOR_SECRET, "GET", "/", "",
    {"host": "example.amazonaws.com", "x-amz-date": "20150830T123600Z"},
    s3_client._sha256(b""), "20150830T123600Z", "us-east-1", "service")
check("signing is deterministic", same == signature, same)
for label, kwargs in (
    ("the method", dict(method="DELETE")),
    ("the path", dict(canonical_uri="/other")),
    ("the query", dict(canonical_query="list-type=2")),
    ("the payload", dict(payload_hash=s3_client._sha256(b"x"))),
    ("the region", dict(region="eu-central-1")),
    ("the service", dict(service="s3")),
):
    args = dict(secret_key=VECTOR_SECRET, method="GET", canonical_uri="/",
                canonical_query="",
                headers={"host": "example.amazonaws.com",
                         "x-amz-date": "20150830T123600Z"},
                payload_hash=s3_client._sha256(b""),
                amz_date="20150830T123600Z", region="us-east-1",
                service="service")
    args.update(kwargs)
    check(f"{label} is part of the signature",
          s3_client.sign(**args) != signature, label)


# ─── 2. What the client puts on the wire ─────────────────────────────────────
class Recorder:
    """Stands in for requests.request and remembers what it was asked to do."""

    def __init__(self, pages=None, status=200):
        self.pages = list(pages or [])
        self.status = status
        self.calls = []

    def __call__(self, method, url, params=None, headers=None, data=None,
                 timeout=None):
        self.calls.append({"method": method, "url": url, "params": params or {},
                           "headers": headers or {}})

        class Response:
            pass

        r = Response()
        r.status_code = self.status
        r.ok = 200 <= self.status < 300
        r.text = ""
        r.content = self.pages.pop(0).encode() if self.pages else b"<ListBucketResult/>"
        return r


def listing(keys, next_token=""):
    items = "".join(f"<Contents><Key>{k}</Key></Contents>" for k in keys)
    more = ("<IsTruncated>true</IsTruncated>"
            f"<NextContinuationToken>{next_token}</NextContinuationToken>"
            if next_token else "<IsTruncated>false</IsTruncated>")
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
            f"{items}{more}</ListBucketResult>")


client = s3_client.S3Client(endpoint="http://smartrag-garage:3900")

# Pagination: a bucket with more than a thousand objects answers truncated,
# and a client that ignores the continuation token empties the first page and
# reports success — leaving a bucket that cannot be deleted and a course that
# looks gone.
rec = Recorder([listing(["a.md", "b.md"], next_token="TOKEN"),
                listing(["c.md"])])
s3_client.requests.request = rec
keys = client.list_keys("mathe-1-rag")
check("every page is read", keys == ["a.md", "b.md", "c.md"], keys)
check("the continuation token is sent back",
      rec.calls[1]["params"].get("continuation-token") == "TOKEN",
      rec.calls[1]["params"])
check("the listing is a v2 listing",
      rec.calls[0]["params"].get("list-type") == "2", rec.calls[0]["params"])

# Path style: the bucket is in the path, not in the host. Virtual-host style
# would need DNS for every bucket, and the rest of this stack (n8n's
# credential) is path style too.
check("the bucket is addressed path-style",
      rec.calls[0]["url"] == "http://smartrag-garage:3900/mathe-1-rag",
      rec.calls[0]["url"])

# Every request carries the three headers the signature covers.
for header in ("Authorization", "x-amz-date", "x-amz-content-sha256"):
    check(f"{header} is sent", header in rec.calls[0]["headers"],
          sorted(rec.calls[0]["headers"]))
check("the credential names the configured region",
      "/eu-central-1/s3/aws4_request" in rec.calls[0]["headers"]["Authorization"],
      rec.calls[0]["headers"]["Authorization"])

# A key with a slash in it is one key, and the slash is a path separator in
# the URL — encoding it as %2F would delete nothing and report success.
rec = Recorder()
s3_client.requests.request = rec
client.delete_key("mathe-1-rag", "agent_1/Kapitel 2.md")
check("a key with a slash keeps its separator",
      rec.calls[0]["url"].endswith("/mathe-1-rag/agent_1/Kapitel%202.md"),
      rec.calls[0]["url"])
check("deleting uses DELETE", rec.calls[0]["method"] == "DELETE", rec.calls[0])

# ─── 3. Emptying, and the states that are not failures ───────────────────────
rec = Recorder([listing(["one.md", "two.md"])])
s3_client.requests.request = rec
removed = client.empty_bucket("mathe-1-rag")
check("emptying deletes every key it listed", removed == 2, removed)
check("…one request each",
      [c["method"] for c in rec.calls] == ["GET", "DELETE", "DELETE"],
      [c["method"] for c in rec.calls])

# A bucket that does not exist is nothing to empty. Treating the 404 as an
# error would make deleting a course whose bucket was never created fail.
rec = Recorder(status=404)
s3_client.requests.request = rec
check("a bucket that is not there lists as empty",
      client.list_keys("never-created-rag") == [], "")

# 403 is the one that needs its own sentence: the key exists but was never
# granted on this bucket, which is what a bucket created by hand looks like.
rec = Recorder(status=403)
s3_client.requests.request = rec
try:
    client.list_keys("mathe-1-rag")
    check("a refused request raises", False, "it returned normally")
except s3_client.S3Error as exc:
    check("a refused request explains itself", "not be allowed" in str(exc), str(exc))

# ─── 4. Refusing to start without credentials ────────────────────────────────
# Empty strings would produce a valid-looking signature that is rejected, and
# the operator would be told the object store refused them rather than that
# nothing was configured.
env.write_text('GARAGE_ACCESS_KEY=""\nGARAGE_SECRET_KEY=""\n')
try:
    s3_client.S3Client(endpoint="http://smartrag-garage:3900")
    check("missing credentials are refused", False, "it constructed anyway")
except s3_client.S3Error as exc:
    check("missing credentials are named", "GARAGE_ACCESS_KEY" in str(exc), str(exc))

if failures:
    print("FAILURES:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print(
    "All S3 signing checks passed: the signature reproduces AWS's published "
    "get-vanilla vector and changes when the method, path, query, payload, "
    "region or service changes; listing follows continuation tokens so a "
    "bucket over one page is emptied completely; requests are path-style and "
    "carry the signed date and payload headers; a key containing a slash "
    "keeps it as a path separator; a bucket that does not exist lists as "
    "empty rather than failing; a refusal explains that the key may not be "
    "granted on that bucket; and missing credentials are refused at "
    "construction rather than becoming a rejected signature later."
)
