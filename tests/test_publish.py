"""Covers publishing an agent: the Flowise client calls, the host/URL derivation
that must match scripts/lib/common.sh, the slot page's publish/unpublish action,
the dashboard column, and the failure modes (Flowise down, chatflow deleted,
not imported yet, no DOMAIN)."""
import json
import os
import sys
from html.parser import HTMLParser
import tempfile
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
APP_DIR = str(REPO / "content-admin")
sys.path.insert(0, APP_DIR)

tmpdir = tempfile.mkdtemp()
env_path = Path(tmpdir) / ".env"
env_path.write_text(
    'CONTENT_ADMIN_SESSION_SECRET="test-secret-not-real"\n'
    'DOMAIN="example.com"\nSUBDOMAIN_PREFIX=""\n'
    'FLOWISE_API_KEY="fw-test-key"\n'
    'LLM_PROVIDER="anthropic"\nLLM_MODEL_STRONG="claude-sonnet-5"\n'
    'LLM_MODEL_FAST="claude-haiku-4-5"\nLLM_API_KEY="sk-test"\n'
    'EMBEDDING_PROVIDER="openai"\nEMBEDDING_MODEL="text-embedding-3-small"\n'
    'EMBEDDING_API_KEY="sk-embed-test"\nCOURSE_NAME="Testkurs"\n'
    'WEAVIATE_COLLECTION_NAME="TestChunks"\nWEAVIATE_API_KEY="wv-test"\n'
    'NEO4J_PASSWORD="neo4j-test"\n'
)
os.environ["SMARTRAG_ENV_PATH"] = str(env_path)
os.environ["SMARTRAG_SLOTS_PATH"] = str(Path(tmpdir) / "slots.json")
os.environ["SMARTRAG_TEMPLATES_DIR"] = str(Path(APP_DIR).parent / "flowise" / "agents")
os.environ["CONTENT_ADMIN_SESSION_SECRET"] = "test-secret-not-real"

from markupsafe import escape  # noqa: E402

import app as m  # noqa: E402
import i18n  # noqa: E402
import storage  # noqa: E402
from flowise_client import FlowiseClient, FlowiseError  # noqa: E402

failures = []
ARCH = "agent-11-expert-feedback.json"
CFID = "cf-abc-123"


def check(name, cond, detail=""):
    if not cond:
        failures.append(f"{name}: {detail}")


# ── FlowiseClient: request shape ────────────────────────────────────────────
client = FlowiseClient("http://flowise:3000/api/v1", "key")

with mock.patch("flowise_client.requests.request") as req:
    req.return_value = mock.Mock(ok=True, content=b"{}", json=lambda: {})
    client.set_chatflow_public(CFID, True)
args, kwargs = req.call_args
check("publish uses PUT", args[0] == "PUT", args)
check("publish hits the chatflow", args[1].endswith(f"/chatflows/{CFID}"), args)
# Only isPublic — Flowise merges the body into the stored entity, so sending
# flowData here would overwrite the live flow with whatever this page happens
# to hold. Verified against Flowise 3.1.3's updateChatflow (repository.merge).
check("publish sends only isPublic", kwargs["json"] == {"isPublic": True}, kwargs.get("json"))

with mock.patch("flowise_client.requests.request") as req:
    req.return_value = mock.Mock(ok=True, content=b"{}", json=lambda: {})
    client.set_chatflow_public(CFID, False)
check("unpublish sends isPublic false",
      req.call_args[1]["json"] == {"isPublic": False}, req.call_args[1].get("json"))

# A deleted chatflow reads as "gone", not as a crash.
with mock.patch("flowise_client.requests.request") as req:
    req.return_value = mock.Mock(ok=False, status_code=404, text="not found")
    check("404 -> None", client.get_chatflow(CFID) is None)

# Any other error must still surface — silently treating a 500 as "no such
# chatflow" would hide a broken Flowise behind a wrong badge.
with mock.patch("flowise_client.requests.request") as req:
    req.return_value = mock.Mock(ok=False, status_code=500, text="boom")
    try:
        client.get_chatflow(CFID)
        failures.append("500 should raise FlowiseError")
    except FlowiseError:
        pass

# ── Public URL derivation (must mirror scripts/lib/common.sh) ───────────────
# FLOWISE_PUBLIC_URL is what the wizard resolved for this deployment; it is
# read, never reassembled. Assembling it from DOMAIN applied the domain-mode
# naming rule to every mode — on a tailscale install that produced
# https://smart-rag.<machine>.<tailnet>.ts.net, a host with no certificate
# and no DNS record, shown to the operator as the students' chat address.
cases = [
    ({"FLOWISE_PUBLIC_URL": "https://smart-rag.example.com"},
     "https://smart-rag.example.com/chatbot/" + CFID, "resolved URL is used"),
    ({"FLOWISE_PUBLIC_URL": "https://kurs-smart-rag.example.com"},
     "https://kurs-smart-rag.example.com/chatbot/" + CFID, "prefix comes from .env"),
    # Tailscale mode: one MagicDNS name, no subdomain, chat on 443 via Funnel.
    ({"FLOWISE_PUBLIC_URL": "https://hp-i5.tail1234.ts.net",
      "DOMAIN": "hp-i5.tail1234.ts.net", "SUBDOMAIN_PREFIX": ""},
     "https://hp-i5.tail1234.ts.net/chatbot/" + CFID, "tailscale: no subdomain"),
    # A trailing slash in .env must not double up.
    ({"FLOWISE_PUBLIC_URL": "https://smart-rag.example.com/"},
     "https://smart-rag.example.com/chatbot/" + CFID, "trailing slash tolerated"),
    ({"FLOWISE_PUBLIC_URL": "  https://smart-rag.example.com  "},
     "https://smart-rag.example.com/chatbot/" + CFID, "values are stripped"),
    # Only when .env predates FLOWISE_PUBLIC_URL does the old rule apply.
    ({"DOMAIN": "example.com", "SUBDOMAIN_PREFIX": ""},
     "https://smart-rag.example.com/chatbot/" + CFID, "fallback: no prefix"),
    ({"DOMAIN": "example.com", "SUBDOMAIN_PREFIX": "kurs"},
     "https://kurs-smart-rag.example.com/chatbot/" + CFID, "fallback: with prefix"),
    ({"DOMAIN": "", "SUBDOMAIN_PREFIX": ""}, "", "nothing to build from -> no URL"),
]
for env, expected, label in cases:
    got = m._public_chat_url(CFID, env)
    check(f"public URL: {label}", got == expected, f"{got!r} != {expected!r}")
check("no chatflow id -> no URL",
      m._public_chat_url("", {"DOMAIN": "example.com"}) == "")

# The naming rule itself, against the shell function it mirrors.
check("host without prefix",
      m._subdomain_host("n8n", {"DOMAIN": "x.de"}) == "n8n.x.de")
check("host with prefix",
      m._subdomain_host("n8n", {"DOMAIN": "x.de", "SUBDOMAIN_PREFIX": "p"}) == "p-n8n.x.de")

# ── Slot page: state, controls, action ──────────────────────────────────────
c = m.app.test_client()
c.post("/setup", data={"username": "admin", "password": "a-strong-test-password",
                       "confirm": "a-strong-test-password"}, follow_redirects=True)

CONTENT = {"EXPERT_DOMAIN": "d", "EXPERT_KNOWLEDGE_DESCRIPTION": "d",
           "CONCEPT_LIST": "c", "RESPONSE_LANGUAGE_RULE": "r", "STUDENT_ROLE": "s"}


def confirm_arg(html_text):
    """Returns the message the publish button's confirm() actually receives,
    as the browser's HTML parser would see it, or None."""
    found = {}

    class P(HTMLParser):
        def handle_starttag(self, tag, attrs):
            a = dict(attrs)
            if tag == "button" and a.get("value") == "publish" and a.get("onclick"):
                found["js"] = a["onclick"]

    P().feed(html_text)
    js = found.get("js")
    if not js:
        return None
    inner = js[js.find("(") + 1:js.rfind(")")]
    try:
        return json.loads(inner)
    except json.JSONDecodeError:
        return f"UNPARSEABLE: {inner}"


def fake_client(is_public=False, raises=None, missing=False):
    fc = mock.Mock()
    if raises:
        fc.get_chatflow.side_effect = raises
        fc.set_chatflow_public.side_effect = raises
    else:
        fc.get_chatflow.return_value = None if missing else {
            "id": CFID, "isPublic": is_public, "name": "A"}
    return fc


# Slot 1: saved but never imported — no publish box at all.
storage.save_slot(1, ARCH, CONTENT, "Not Imported", None)
body = c.get("/slot/1").get_data(as_text=True)
check("no publish box before import", 'value="publish"' not in body)

# Publishing a slot that was never imported must be refused even if the
# request is forged by hand — the missing button is not the only guard.
with mock.patch.object(m, "_flowise_client", return_value=fake_client()):
    body = c.post("/slot/1", data={"archetype": ARCH, "action": "publish"},
                  follow_redirects=True).get_data(as_text=True)
check("publish without import is refused",
      i18n.t("publish_err_not_imported") in body, body[:300])

# Slot 2: imported, currently private.
storage.save_slot(2, ARCH, CONTENT, "Imported Agent", None)
storage.set_chatflow_id(2, CFID)

with mock.patch.object(m, "_flowise_client", return_value=fake_client(is_public=False)):
    body = c.get("/slot/2").get_data(as_text=True)
check("private state shown", i18n.t("publish_state_private") in body)
check("publish button offered", 'value="publish"' in body)
check("warning shown before publishing", i18n.t("publish_warning") in body)
# Parsed, not grepped: the earlier version only looked for "confirm(" and
# so passed while the attribute was actually truncated at the first double
# quote of the |tojson output — the dialog never appeared and the button
# published anyway. The whole message must survive into the attribute value.
check("confirm dialog wired", confirm_arg(body) == i18n.t("publish_confirm"),
      repr(confirm_arg(body))[:160])
# The URL must not be advertised while the agent is private.
check("no public link while private", "/chatbot/" not in body, body[body.find("/chatbot/") - 80:][:160])

fc = fake_client(is_public=False)
with mock.patch.object(m, "_flowise_client", return_value=fc):
    body = c.post("/slot/2", data={"archetype": ARCH, "action": "publish"},
                  follow_redirects=True).get_data(as_text=True)
check("publish calls Flowise with True",
      fc.set_chatflow_public.call_args[0] == (CFID, True), fc.set_chatflow_public.call_args)
check("publish confirms", i18n.t("publish_ok") in body, body[:300])

# Now public: link, embed snippet and the withdraw button.
with mock.patch.object(m, "_flowise_client", return_value=fake_client(is_public=True)):
    body = c.get("/slot/2").get_data(as_text=True)
check("public state shown", i18n.t("publish_state_public") in body)
check("public URL shown", f"https://smart-rag.example.com/chatbot/{CFID}" in body)
check("embed snippet offered", "&lt;iframe" in body or "<iframe" in body.replace("&lt;", "<"))
check("withdraw button offered", 'value="unpublish"' in body)
check("publish button gone while public", 'value="publish"' not in body)
# No second confirm-and-publish path hiding in the page.
check("warning not repeated once public", i18n.t("publish_warning") not in body)

fc = fake_client(is_public=True)
with mock.patch.object(m, "_flowise_client", return_value=fc):
    body = c.post("/slot/2", data={"archetype": ARCH, "action": "unpublish"},
                  follow_redirects=True).get_data(as_text=True)
check("unpublish calls Flowise with False",
      fc.set_chatflow_public.call_args[0] == (CFID, False), fc.set_chatflow_public.call_args)
check("unpublish confirms", i18n.t("unpublish_ok") in body, body[:300])

# Publishing must not touch the stored content or the prompt.
before = storage.get_slot(2)
with mock.patch.object(m, "_flowise_client", return_value=fake_client()):
    c.post("/slot/2", data={"archetype": ARCH, "action": "publish"}, follow_redirects=True)
check("publish leaves the slot untouched", storage.get_slot(2) == before,
      f"{storage.get_slot(2)} != {before}")

# ── Failure modes must degrade, not 500 ─────────────────────────────────────
with mock.patch.object(m, "_flowise_client",
                       return_value=fake_client(raises=FlowiseError("connection refused"))):
    resp = c.get("/slot/2")
check("Flowise outage still renders the page", resp.status_code == 200, resp.status_code)
check("unknown state shown on outage",
      i18n.t("publish_state_unknown") in resp.get_data(as_text=True))

with mock.patch.object(m, "_flowise_client",
                       return_value=fake_client(raises=FlowiseError("boom"))):
    body = c.post("/slot/2", data={"archetype": ARCH, "action": "publish"},
                  follow_redirects=True).get_data(as_text=True)
check("publish failure surfaces the reason", "boom" in body, body[:300])

# Chatflow deleted in Flowise: say so plainly. Not "unknown" (that would send
# the operator hunting a connection problem) and not "private" (that would
# imply publishing is one click away, when it needs a re-import first).
with mock.patch.object(m, "_flowise_client", return_value=fake_client(missing=True)):
    body = c.get("/slot/2").get_data(as_text=True)
check("deleted chatflow is named as such", i18n.t("publish_state_gone") in body, body[:200])
check("deleted chatflow is not shown as unknown", i18n.t("publish_state_unknown") not in body)
check("deleted chatflow is not shown as merely private",
      i18n.t("publish_state_private") not in body)

# Flowise not configured at all.
with mock.patch.object(m, "_flowise_client", return_value=None):
    resp = c.get("/slot/2")
    check("unconfigured Flowise still renders", resp.status_code == 200, resp.status_code)
    body = c.post("/slot/2", data={"archetype": ARCH, "action": "publish"},
                  follow_redirects=True).get_data(as_text=True)
# Jinja HTML-escapes the apostrophe in "isn\'t", so compare escaped.
check("publish without Flowise is refused",
      str(escape(i18n.t("slot_err_not_connected"))) in body, body[:300])

# ── Dashboard column ────────────────────────────────────────────────────────
dash = mock.Mock()
dash.list_chatflows.return_value = [{"id": CFID, "isPublic": True}]
with mock.patch.object(m, "_flowise_client", return_value=dash):
    body = c.get("/").get_data(as_text=True)
check("dashboard has a public column", i18n.t("dash_col_public") in body)
check("published slot is marked", i18n.t("dash_public_yes") in body)
check("published slot links out", f"/chatbot/{CFID}" in body)
check("one list call for all slots", dash.list_chatflows.call_count == 1,
      dash.list_chatflows.call_count)

dash = mock.Mock()
dash.list_chatflows.return_value = [{"id": CFID, "isPublic": False}]
with mock.patch.object(m, "_flowise_client", return_value=dash):
    body = c.get("/").get_data(as_text=True)
check("private slot is not marked published", i18n.t("dash_public_yes") not in body)

dash = mock.Mock()
dash.list_chatflows.side_effect = FlowiseError("down")
with mock.patch.object(m, "_flowise_client", return_value=dash):
    resp = c.get("/")
check("dashboard survives a Flowise outage", resp.status_code == 200, resp.status_code)

# ── Both languages ──────────────────────────────────────────────────────────
for lang in ("en", "de"):
    c.get(f"/language/{lang}")
    with mock.patch.object(m, "_flowise_client", return_value=fake_client(is_public=True)):
        body = c.get("/slot/2").get_data(as_text=True)
    check(f"[{lang}] publish heading localised", i18n.t("publish_heading", lang=lang) in body)
    check(f"[{lang}] withdraw button localised",
          i18n.t("publish_unpublish_button", lang=lang) in body)
    with mock.patch.object(m, "_flowise_client", return_value=fake_client(is_public=False)):
        body = c.get("/slot/2").get_data(as_text=True)
    check(f"[{lang}] warning localised", i18n.t("publish_warning", lang=lang) in body)
    check(f"[{lang}] confirm message survives into the attribute",
          confirm_arg(body) == i18n.t("publish_confirm", lang=lang),
          repr(confirm_arg(body))[:160])
c.get("/language/en")

if failures:
    print("FAILURES:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print(
    "All publishing checks passed: set_chatflow_public PUTs only isPublic (so Flowise's "
    "merge semantics preserve the live flowData), get_chatflow maps 404 to None but still "
    "raises on other errors; the public URL mirrors subdomain_host() from "
    "scripts/lib/common.sh with and without SUBDOMAIN_PREFIX, strips whitespace and yields "
    "nothing without DOMAIN or a chatflow id; the slot page hides the box before import, "
    "refuses a forged publish anyway, shows the warning and a confirm dialog only while "
    "private, reveals link and embed snippet only once public, offers exactly one of "
    "publish/withdraw, and leaves stored content untouched; a Flowise outage, a deleted "
    "chatflow and an unconfigured Flowise all degrade to a rendered page with an honest "
    "state instead of a 500; the dashboard marks and links published agents from a single "
    "list call and survives an outage; and everything is localised in both languages."
)
