"""Covers the onboarding guide: every check's states and its degradation
behaviour, the n8n webhook probe's two distinct 404s, and the page itself."""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

import requests

REPO = Path(__file__).resolve().parent.parent
APP_DIR = str(REPO / "content-admin")
sys.path.insert(0, APP_DIR)

tmpdir = tempfile.mkdtemp()
env_path = Path(tmpdir) / ".env"
env_path.write_text(
    'CONTENT_ADMIN_SESSION_SECRET="test-secret-not-real"\n'
    'DOMAIN="example.com"\nFLOWISE_API_KEY="fw-test-key"\n'
    'LLM_PROVIDER="anthropic"\nLLM_API_KEY="sk-test"\n'
    'EMBEDDING_PROVIDER="openai"\nEMBEDDING_API_KEY="sk-embed-test"\n'
    'WEAVIATE_HTTP_PORT=8080\n'
)
os.environ["SMARTRAG_ENV_PATH"] = str(env_path)
os.environ["SMARTRAG_SLOTS_PATH"] = str(Path(tmpdir) / "slots.json")
os.environ["SMARTRAG_TEMPLATES_DIR"] = str(Path(APP_DIR).parent / "flowise" / "agents")
os.environ["CONTENT_ADMIN_SESSION_SECRET"] = "test-secret-not-real"

from markupsafe import escape  # noqa: E402

import app as m  # noqa: E402
import i18n  # noqa: E402
import setup_checks as sc  # noqa: E402
import storage  # noqa: E402
from flowise_client import FlowiseError  # noqa: E402
from setup_checks import State  # noqa: E402

failures = []
ARCH = "agent-11-expert-feedback.json"
N8N = "http://smartrag-n8n:5678"
CMD = "sudo bash scripts/deploy-n8n-workflows.sh"


def check(name, cond, detail=""):
    if not cond:
        failures.append(f"{name}: {detail}")


# ── API keys ────────────────────────────────────────────────────────────────
good = {"LLM_API_KEY": "sk-a", "EMBEDDING_API_KEY": "sk-b", "LLM_PROVIDER": "anthropic"}
c = sc.check_llm_keys(good)
check("keys present -> OK", c.state == State.OK, c)
check("provider shown", c.detail == "anthropic", c.detail)

c = sc.check_llm_keys({"LLM_API_KEY": "sk-a", "EMBEDDING_API_KEY": ""})
check("missing embedding key -> FAIL", c.state == State.FAIL, c)
check("names the missing one", c.detail == "EMBEDDING_API_KEY", c.detail)
check("missing key blocks", c.blocking)

c = sc.check_llm_keys({})
check("both missing named", "LLM_API_KEY" in c.detail and "EMBEDDING_API_KEY" in c.detail, c.detail)

# bootstrap writes literal placeholders; an untouched one is not a key.
c = sc.check_llm_keys({"LLM_API_KEY": "your-llm-api-key-here", "EMBEDDING_API_KEY": "sk-b"})
check("untouched placeholder counts as missing", c.state == State.FAIL, c)
c = sc.check_llm_keys({"LLM_API_KEY": "   ", "EMBEDDING_API_KEY": "sk-b"})
check("whitespace-only key counts as missing", c.state == State.FAIL, c)

# ── Flowise ─────────────────────────────────────────────────────────────────
check("no client -> FAIL", sc.check_flowise({}, None).state == State.FAIL)
check("no client points at the setup page",
      sc.check_flowise({}, None).link == "flowise_setup")

fc = mock.Mock()
check("reachable -> OK", sc.check_flowise({}, fc).state == State.OK)

fc = mock.Mock()
fc.check_connection.side_effect = FlowiseError("HTTP 401: Unauthorized")
c = sc.check_flowise({}, fc)
check("bad key -> FAIL", c.state == State.FAIL, c)
check("bad key surfaces Flowise's own words", "401" in c.detail, c.detail)

# A transport error is not a FlowiseError but must not escape either.
fc = mock.Mock()
fc.check_connection.side_effect = requests.ConnectionError("refused")
check("transport error handled", sc.check_flowise({}, fc).state == State.FAIL)

# ── Agents ──────────────────────────────────────────────────────────────────
live = mock.Mock()
live.list_chatflows.return_value = [{"id": "cf-1"}, {"id": "cf-2"}]

check("no slots -> FAIL", sc.check_agents({}, live).state == State.FAIL)
check("no slots blocks", sc.check_agents({}, live).blocking)

saved_only = {"1": {"archetype": ARCH, "chatflow_id": None}}
c = sc.check_agents(saved_only, live)
check("saved but not imported -> WARN", c.state == State.WARN, c)
check("saved but not imported blocks", c.blocking)
check("counts reported", c.extra == {"configured": 1, "imported": 0}, c.extra)

imported = {"1": {"archetype": ARCH, "chatflow_id": "cf-1"},
            "2": {"archetype": ARCH, "chatflow_id": None}}
c = sc.check_agents(imported, live)
check("one imported -> OK", c.state == State.OK, c)
check("counts both", c.extra["imported"] == 1 and c.extra["configured"] == 2, c.extra)
check("nothing stale", c.extra["stale"] == 0, c.extra)

# An id Flowise no longer knows: the agent is gone, so this must not be OK.
stale = {"1": {"archetype": ARCH, "chatflow_id": "cf-deleted"}}
c = sc.check_agents(stale, live)
check("deleted chatflow -> WARN", c.state == State.WARN, c)
check("stale counted", c.extra["stale"] == 1, c.extra)

# Cross-check failure must not turn a working setup into a warning.
broken = mock.Mock()
broken.list_chatflows.side_effect = FlowiseError("down")
c = sc.check_agents({"1": {"archetype": ARCH, "chatflow_id": "cf-1"}}, broken)
check("outage during cross-check still OK", c.state == State.OK, c)
check("outage reports no false staleness", c.extra["stale"] == 0, c.extra)
c = sc.check_agents({"1": {"archetype": ARCH, "chatflow_id": "cf-1"}}, None)
check("no client during cross-check still OK", c.state == State.OK, c)

# ── n8n webhook probe ───────────────────────────────────────────────────────
def n8n_reply(status, body):
    return mock.Mock(status_code=status, text=body)


# Registered for POST, asked with GET — this is the *success* signal.
# Wording verified in n8n's webhook-not-found.error.ts at tag n8n@1.123.0.
REGISTERED = ('{"code":404,"message":"This webhook is not registered for GET '
              'requests. Did you mean to make a POST request?"}')
NOT_REGISTERED = ('{"code":404,"message":"The requested webhook \\"GET '
                  'document-ingest\\" is not registered.","hint":"The workflow '
                  'must be active for a production URL to run successfully."}')

with mock.patch("setup_checks.requests.get", return_value=n8n_reply(404, REGISTERED)) as g:
    c = sc.check_n8n_webhook(N8N, CMD)
check("method-mismatch 404 means registered", c.state == State.OK, c)
# Probing with GET is the whole point: a POST would start a real ingest run.
check("probe uses GET", g.call_args[0][0].endswith("/webhook/document-ingest"), g.call_args)
check("no command offered when fine", c.command == "", c.command)

with mock.patch("setup_checks.requests.get", return_value=n8n_reply(404, NOT_REGISTERED)):
    c = sc.check_n8n_webhook(N8N, CMD)
check("unregistered 404 -> FAIL", c.state == State.FAIL, c)
check("offers the deploy command", c.command == CMD, c.command)
check("unregistered blocks", c.blocking)

with mock.patch("setup_checks.requests.get",
                side_effect=requests.ConnectionError("no route to host")):
    c = sc.check_n8n_webhook(N8N, CMD)
check("n8n unreachable -> FAIL", c.state == State.FAIL, c)
check("unreachable surfaces the reason", "no route" in c.detail, c.detail)

# Anything else: say what came back rather than inventing a verdict.
with mock.patch("setup_checks.requests.get", return_value=n8n_reply(502, "bad gateway")):
    c = sc.check_n8n_webhook(N8N, CMD)
check("unexpected reply -> UNKNOWN", c.state == State.UNKNOWN, c)
check("unexpected reply quoted", "502" in c.detail and "bad gateway" in c.detail, c.detail)

# ── Ingest services ─────────────────────────────────────────────────────────
def probe_map(mapping):
    """Answers per URL so one service can fail while the others pass."""
    def _get(url, timeout=None):
        for fragment, resp in mapping.items():
            if fragment in url:
                if isinstance(resp, Exception):
                    raise resp
                return resp
        raise AssertionError(f"unexpected probe: {url}")
    return _get


ok_resp = mock.Mock(ok=True, status_code=200, text="")
with mock.patch("setup_checks.requests.get", side_effect=probe_map(
        {"docling": ok_resp, "markdowncleaner": ok_resp, "weaviate": ok_resp})):
    c = sc.check_ingest_services({"WEAVIATE_HTTP_PORT": "8080"})
check("all three up -> OK", c.state == State.OK, c)
check("each service reported", set(c.extra) == {"docling", "markdowncleaner", "weaviate"}, c.extra)

with mock.patch("setup_checks.requests.get", side_effect=probe_map({
        "docling": ok_resp,
        "markdowncleaner": requests.ConnectionError("refused"),
        "weaviate": mock.Mock(ok=False, status_code=503, text="not ready")})):
    c = sc.check_ingest_services({})
check("partial outage -> FAIL", c.state == State.FAIL, c)
check("names exactly the down ones",
      "markdowncleaner" in c.detail and "weaviate" in c.detail and "docling" not in c.detail,
      c.detail)
check("healthy one still marked ok", c.extra["docling"]["ok"] is True, c.extra)
check("503 detail kept", "503" in c.extra["weaviate"]["detail"], c.extra["weaviate"])

# The Weaviate port must follow .env, not a hard-coded 8080.
seen = []
with mock.patch("setup_checks.requests.get",
                side_effect=lambda url, timeout=None: (seen.append(url), ok_resp)[1]):
    sc.check_ingest_services({"WEAVIATE_HTTP_PORT": "9999"})
check("weaviate port comes from .env", any(":9999/" in u for u in seen), seen)
seen.clear()
with mock.patch("setup_checks.requests.get",
                side_effect=lambda url, timeout=None: (seen.append(url), ok_resp)[1]):
    sc.check_ingest_services({})
check("weaviate port defaults to 8080", any(":8080/" in u for u in seen), seen)

# ── run_all ─────────────────────────────────────────────────────────────────
with mock.patch("setup_checks.requests.get", return_value=n8n_reply(404, NOT_REGISTERED)):
    checks = sc.run_all({}, {}, None, N8N, CMD)
check("run_all returns every check", len(checks) == 5, len(checks))
# One failure must never suppress the checks after it — seeing everything at
# once is the point of the page.
check("all checks ran despite failures", [c.key for c in checks] == [
    "llm_keys", "flowise", "agents", "n8n", "ingest_services"], [c.key for c in checks])

# ── The page ────────────────────────────────────────────────────────────────
client = m.app.test_client()
resp = client.get("/getting-started")
check("guide requires login", resp.status_code in (302, 401), resp.status_code)

client.post("/setup", data={"username": "admin", "password": "a-strong-test-password",
                            "confirm": "a-strong-test-password"}, follow_redirects=True)


def render(checks_result):
    with mock.patch.object(sc, "run_all", return_value=checks_result):
        return client.get("/getting-started")


ALL_OK = [sc.Check(k, State.OK) for k in
          ("llm_keys", "flowise", "agents", "n8n", "ingest_services")]
body = render(ALL_OK).get_data(as_text=True)
check("ready banner shown", i18n.t("guide_ready") in body, body[:200])
check("next steps offered when ready", i18n.t("guide_next_upload") in body)

# A WARN is not a pass: agents saved-but-never-imported is not a working
# system, and a green banner would claim otherwise.
warned = [sc.Check("llm_keys", State.OK), sc.Check("flowise", State.OK),
          sc.Check("agents", State.WARN, extra={"configured": 2, "imported": 0},
                   link="dashboard", link_key="agents_link", blocking=True),
          sc.Check("n8n", State.OK), sc.Check("ingest_services", State.OK)]
body = render(warned).get_data(as_text=True)
check("WARN is not ready", i18n.t("guide_ready") not in body)
check("warn text shown", i18n.t("guide_agents_warn_unimported", 2) in body, body[:200])
check("warn links onward", i18n.t("guide_agents_link") in body)
check("blocker named at the top",
      i18n.t("guide_blocked", i18n.t("guide_agents_title")) in body, body[:400])

# The deploy command must be shown to copy, and only while it's needed.
failing_n8n = [sc.Check("llm_keys", State.OK), sc.Check("flowise", State.OK),
               sc.Check("agents", State.OK, extra={"configured": 1, "imported": 1, "stale": 0}),
               sc.Check("n8n", State.FAIL, "not registered", command=CMD, blocking=True),
               sc.Check("ingest_services", State.OK)]
body = render(failing_n8n).get_data(as_text=True)
check("deploy command shown", CMD in body, body[:200])
check("explains why the GUI can't run it",
      str(escape(i18n.t("guide_command_why"))) in body, body[:200])

# The person using this GUI authors course content; they typically have no
# shell on the server at all. So the step must be addressed to whoever
# administers it, with something they can actually forward — the bare
# command alone would be an instruction they cannot follow.
check("says this is the administrator's step",
      str(escape(i18n.t("guide_admin_needed"))) in body, body[:200])
check("offers a forwardable message",
      str(escape(i18n.t("guide_admin_message", CMD))) in body,
      i18n.t("guide_admin_message", CMD)[:120])
check("the message contains the command", CMD in body, "")
check("the message is copyable", 'id="msg-n8n"' in body and 'data-copy="msg-n8n"' in body)
check("message copy button is labelled as such",
      i18n.t("guide_copy_message") in body, i18n.t("guide_copy_message"))
check("n8n failure text shown", i18n.t("guide_n8n_fail") in body)
check("raw detail surfaced", "not registered" in body)

body = render(ALL_OK).get_data(as_text=True)
check("no command once n8n is fine", CMD not in body)

# Per-service breakdown for the three ingest services.
detailed = [sc.Check("llm_keys", State.OK), sc.Check("flowise", State.OK),
            sc.Check("agents", State.OK, extra={"configured": 1, "imported": 1, "stale": 0}),
            sc.Check("n8n", State.OK),
            sc.Check("ingest_services", State.FAIL, "weaviate", extra={
                "docling": {"ok": True, "detail": ""},
                "markdowncleaner": {"ok": True, "detail": ""},
                "weaviate": {"ok": False, "detail": "HTTP 503: not ready"}})]
body = render(detailed).get_data(as_text=True)
check("each service listed", "docling" in body and "weaviate" in body)
check("failing service's detail shown", "503" in body, body[:200])

# The page must survive every check failing at once, and be reachable from
# the nav on every page.
ALL_FAIL = [sc.Check(k, State.FAIL, "boom", blocking=True) for k in
            ("llm_keys", "flowise", "agents", "n8n", "ingest_services")]
resp = render(ALL_FAIL)
check("page renders with everything broken", resp.status_code == 200, resp.status_code)
nav_body = client.get("/").get_data(as_text=True)
check("guide is in the nav", "/getting-started" in nav_body)
# Renamed from "Getting started": the page is consulted long after setup,
# whenever something stops working, so it is named for what it shows.
check("nav uses the status name", i18n.t("guide_title") in nav_body, i18n.t("guide_title"))
check("nav no longer says 'Getting started'", "Getting started" not in nav_body)

# Real run: no service reachable in the test environment, so this exercises
# the actual probes and their error handling end to end.
resp = client.get("/getting-started")
check("unmocked run renders", resp.status_code == 200, resp.status_code)

# ── Both languages ──────────────────────────────────────────────────────────
for lang in ("en", "de"):
    client.get(f"/language/{lang}")
    body = render(failing_n8n).get_data(as_text=True)
    for key in ("guide_heading", "guide_n8n_title", "guide_n8n_fail",
                "guide_command_why", "guide_admin_needed"):
        check(f"[{lang}] {key} localised",
              str(escape(i18n.t(key, lang=lang))) in body, i18n.t(key, lang=lang)[:60])
    check(f"[{lang}] copy label injected as JSON",
          json.dumps(i18n.t("publish_copy", lang=lang)) in body)
client.get("/language/en")

# ─── The services' own addresses ─────────────────────────────────────────────
# Opening Flowise or n8n is an ordinary thing to want, and the address is
# otherwise only in .env or in an email from the day the system was installed.
# So it rides on the check, in every state — including OK, where a
# failure-only link would be missing exactly when the system works.
env_urls = {
    "FLOWISE_PUBLIC_URL": "https://smart-rag.example.com",
    "N8N_WEBHOOK_URL": "https://n8n.example.com",
    "LLM_PROVIDER": "openai",
    "LLM_API_KEY": "sk-test",
    "EMBEDDING_API_KEY": "sk-test",
}

class _OkFlowise:
    def check_connection(self):
        return True

c = sc.check_flowise(env_urls, _OkFlowise())
check("a working Flowise still offers its address",
      c.state == State.OK
      and c.external_url == "https://smart-rag.example.com", c)
c = sc.check_flowise(env_urls, None)
check("…and so does an unreachable one",
      c.external_url == "https://smart-rag.example.com", c)

# The probe goes to the container over the Docker network; the link has to
# work in a browser. Confusing the two would hand the operator an address
# that only resolves inside Docker.
class _Resp:
    def __init__(self, body, code=404):
        self.text = body
        self.status_code = code


_saved = sc.requests.get
try:
    sc.requests.get = lambda *a, **k: _Resp(
        "This webhook is not registered for GET requests.")
    c = sc.check_n8n_webhook("http://smartrag-n8n:5678", "cmd", env_urls)
    check("a working n8n offers its public address",
          c.state == State.OK
          and c.external_url == "https://n8n.example.com", c)
    check("…and not the internal one",
          "smartrag-n8n" not in (c.external_url or ""), c.external_url)

    sc.requests.get = lambda *a, **k: _Resp(
        'The requested webhook "GET document-ingest" is not registered.')
    c = sc.check_n8n_webhook("http://smartrag-n8n:5678", "cmd", env_urls)
    check("a broken n8n still offers its address", c.external_url == "https://n8n.example.com", c)
finally:
    sc.requests.get = _saved

# An installation with no public URL configured must show no link at all
# rather than an empty one, which renders as a link to the current page.
c = sc.check_flowise({}, _OkFlowise())
check("no address configured means no link", c.external_url == "", c)

# run_all must pass the environment down — without it the n8n link is silently
# empty on every installation.
import inspect as _inspect  # noqa: E402
src = _inspect.getsource(sc.run_all)
check("run_all hands the environment to the n8n check",
      "check_n8n_webhook(n8n_base_url, deploy_command, env)" in src, src)

for key in ["guide_flowise_open", "guide_n8n_open", "guide_open_note"]:
    i18n_src = (REPO / "content-admin" / "i18n.py").read_text()
    check(f"{key} exists in both languages", i18n_src.count(f'"{key}":') == 2,
          i18n_src.count(f'"{key}":'))

tpl = (REPO / "content-admin" / "templates" / "getting_started.html").read_text()
check("the link is not hidden behind a failing state",
      "c.external_url %}" in tpl and "external_url and c.state" not in tpl, "")
check("the link opens safely in a new tab",
      'rel="noopener noreferrer"' in tpl, "")

if failures:
    print("FAILURES:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print(
    "All onboarding-guide checks passed: key check treats blanks and untouched bootstrap "
    "placeholders as missing and names them; Flowise check separates 'never connected' from "
    "a rejected key and survives a transport error; agent check distinguishes none/saved/"
    "imported, spots chatflows deleted in Flowise, and does not invent staleness when the "
    "cross-check itself fails; the n8n probe uses a side-effect-free GET and reads n8n's two "
    "distinct 404 messages the right way round — method-mismatch means the webhook IS "
    "registered — falling back to UNKNOWN with the raw reply for anything else; ingest "
    "services are probed individually with the Weaviate port taken from .env; run_all runs "
    "every check even when earlier ones fail; and the page names the first blocker, refuses "
    "to call a WARN ready, shows the deploy command only while needed with the reason the "
    "GUI cannot run it, breaks the three services out individually, renders with everything "
    "broken, and is localised in both languages."
)
