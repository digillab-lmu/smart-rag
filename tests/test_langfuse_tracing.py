"""
Observability that is switched on, not merely installed.

The observability profile runs Langfuse and ClickHouse — well over a gigabyte
of memory — and received nothing. No agent template carried tracing
configuration, no Langfuse project existed, and an n8n workflow patched traces
that were never created, every thirty minutes.

It surfaced only because a Garage evaluation checked whether Langfuse had
written any objects and found none. The MinIO bucket it had been using all
along was empty too: the store was never the problem.

Two things had to be true and neither was: Langfuse needs a project with API
keys before anything can report to it, and Flowise needs those keys per
chatflow — its env-based tracing covers LangSmith only.
"""

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "content-admin"))

from flowise_client import FlowiseClient  # noqa: E402

failures = []


def check(name, ok, detail=""):
    if not ok:
        failures.append(f"{name}: {detail}")


# ─── 1. Langfuse is initialised headlessly ───────────────────────────────────
env_example = (REPO / ".env.example").read_text()
for key in ("LANGFUSE_INIT_ORG_ID", "LANGFUSE_INIT_PROJECT_ID",
            "LANGFUSE_INIT_PROJECT_PUBLIC_KEY", "LANGFUSE_INIT_PROJECT_SECRET_KEY",
            "LANGFUSE_INIT_USER_EMAIL", "LANGFUSE_INIT_USER_PASSWORD"):
    check(f"{key} is in .env.example", re.search(rf"^{key}=", env_example, re.M) is not None,
          "without it a fresh Langfuse has no project, and no keys to report with")

# The keys must be generated, not shipped as a literal anyone could look up.
secrets = (REPO / "scripts" / "lib" / "secrets.sh").read_text()
check("the project keys are generated", "SECRET_LANGFUSE_PUBLIC_KEY" in secrets
      and "SECRET_LANGFUSE_SECRET_KEY" in secrets, "")
check("the secret key is not a fixed string",
      re.search(r'SECRET_LANGFUSE_SECRET_KEY="[^$]*"', secrets) is None,
      "a shipped literal would be identical on every installation")

# And written resolved: Langfuse reads them through env_file, which does not
# interpolate — the failure that already cost this project two incidents.
templates = (REPO / "scripts" / "lib" / "templates.sh").read_text()
for key in ("LANGFUSE_INIT_PROJECT_NAME", "LANGFUSE_INIT_USER_EMAIL",
            "LANGFUSE_INIT_USER_PASSWORD", "LANGFUSE_INIT_PROJECT_PUBLIC_KEY",
            "LANGFUSE_INIT_PROJECT_SECRET_KEY"):
    check(f"{key} is written resolved", f"REPL[{key}]" in templates,
          "env_file passes ${...} through literally")

# ─── 2. The analytic payload matches what Flowise reads ──────────────────────
payload = json.loads(FlowiseClient.langfuse_analytic("cred-123"))
check("the provider key is exactly langFuse", "langFuse" in payload,
      f"got {list(payload)} — another spelling is skipped with no error")
# .get, not [] — a wrong provider key should be reported as the one failure it
# is, not raise and take the rest of the suite's diagnostics with it.
lf = payload.get("langFuse", {})
check("status is a boolean true", lf.get("status") is True, lf)
check("the credential id is carried", lf.get("credentialId") == "cred-123", lf)

# ─── 3. It reaches the chatflow, and only when given ─────────────────────────
class Recorder(FlowiseClient):
    def __init__(self, existing=None):
        super().__init__("http://stub/api/v1", "k")
        self.existing = existing or []
        self.calls = []

    def _request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs.get("json")))
        if method == "GET" and path == "/chatflows":
            return self.existing
        return {"id": "cf-new"}


c = Recorder()
c.upsert_chatflow("A", "{}", analytic='{"langFuse":{"status":true}}')
post = [x for x in c.calls if x[0] == "POST"][0]
check("a new chatflow carries the tracing config", "analytic" in post[2], post[2])

c = Recorder(existing=[{"id": "cf-1", "name": "A"}])
c.upsert_chatflow("A", "{}", analytic='{"langFuse":{"status":true}}')
put = [x for x in c.calls if x[0] == "PUT"][0]
check("a re-imported chatflow keeps it", "analytic" in put[2], put[2])

# Omitted means omitted: Flowise merges the body, so sending analytic=null
# would clear tracing somebody configured by hand.
c = Recorder(existing=[{"id": "cf-1", "name": "A"}])
c.upsert_chatflow("A", "{}")
put = [x for x in c.calls if x[0] == "PUT"][0]
check("without tracing configured, the field is not sent at all",
      "analytic" not in put[2], f"{put[2]} would clear an existing setting")

# ─── 4. The import wires it, and degrades rather than failing ────────────────
app_src = (REPO / "content-admin" / "app.py").read_text()
check("the import creates a langfuseApi credential", '"langfuseApi"' in app_src, "")
check("using the generated project keys",
      "LANGFUSE_INIT_PROJECT_PUBLIC_KEY" in app_src, "")
check("pointing at the internal Langfuse address",
      "smartrag-langfuse-web:3001" in app_src,
      "reporting through the public URL would leave the network for an internal call")
check("only when the observability profile is on", '"observability" in env' in app_src,
      "an agent must still import without Langfuse")
check("and a tracing failure does not fail the import",
      re.search(r"Could not configure Langfuse tracing", app_src) is not None,
      "an agent that works without tracing beats no agent")

if failures:
    print("FAILURES:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print(
    "All Langfuse-tracing checks passed: the project, user and API keys are "
    "created headlessly with generated secrets written resolved rather than "
    "interpolated, the analytic payload uses the exact provider key Flowise "
    "reads, it reaches both a new and a re-imported chatflow while an omitted "
    "one is not sent at all, and the import wires it only under the "
    "observability profile and treats a tracing failure as a degradation "
    "rather than a failed import."
)
