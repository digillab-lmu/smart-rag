import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
APP_DIR = str(REPO / "content-admin")
sys.path.insert(0, APP_DIR)

tmpdir = tempfile.mkdtemp()
env_path = Path(tmpdir) / ".env"
env_path.write_text(
    'CONTENT_ADMIN_SESSION_SECRET="test-secret-not-real"\n'
    'DOMAIN="example.com"\n'
    'LLM_PROVIDER="anthropic"\n'
    'LLM_MODEL_STRONG="claude-sonnet-5"\n'
    'LLM_API_KEY="sk-test"\n'
    'EMBEDDING_PROVIDER="openai"\n'
    'EMBEDDING_MODEL="text-embedding-3-small"\n'
    'EMBEDDING_API_KEY="sk-embed-test"\n'
    'COURSE_NAME="Testkurs"\n'
    'WEAVIATE_COLLECTION_NAME="TestChunks"\n'
    'WEAVIATE_API_KEY="wv-test"\n'
    'NEO4J_PASSWORD="neo4j-test"\n'
)
os.environ["SMARTRAG_ENV_PATH"] = str(env_path)
os.environ["SMARTRAG_SLOTS_PATH"] = str(Path(tmpdir) / "slots.json")
os.environ["SMARTRAG_TEMPLATES_DIR"] = str(Path(APP_DIR).parent / "flowise" / "agents")
os.environ["CONTENT_ADMIN_SESSION_SECRET"] = "test-secret-not-real"

import agent_templates  # noqa: E402
import app as flask_app_module  # noqa: E402
import i18n  # noqa: E402

failures = []


def check(name, cond, detail=""):
    if not cond:
        failures.append(f"{name}: {detail}")


# ── 1. Catalogue completeness (the messages.sh check_translations analogue) ──
problems = i18n.check_translations()
check("no missing/extra translation keys", not problems, "; ".join(problems))
check("both languages non-trivial", len(i18n.MSG_EN) > 100, f"only {len(i18n.MSG_EN)} keys")

# Every DE string must actually differ from EN (catches copy-paste stubs).
# A handful are legitimately identical (proper nouns, identical UI paths).
# Legitimately identical in both languages: proper nouns, an in-product
# menu path quoted verbatim, and words German borrowed unchanged.
IDENTICAL_OK = {
    "app_name", "flowise_step2_path", "dash_col_name", "upload_slot_option",
    "dash_col_status",
    # An em dash standing for "nothing here" — not text, so not translatable.
    "dash_public_no",
    # "Agent" and "Chunk" are the words German uses too, and the action
    # column has no header at all — an empty string in both languages.
    "docs_col_agent", "docs_col_chunks", "docs_col_action",
}
same = [
    k for k in i18n.MSG_EN
    if k not in IDENTICAL_OK and i18n.MSG_EN[k] == i18n.MSG_DE.get(k)
]
check("no untranslated DE strings", not same, f"identical to EN: {same}")

# ── 2. Content dicts (archetypes, field help/examples/labels) ────────────────
for name, catalog in [
    ("ARCHETYPES", agent_templates.ARCHETYPES_BY_LANG),
    ("ARCHETYPE_DESCRIPTIONS", agent_templates.ARCHETYPE_DESCRIPTIONS_BY_LANG),
    ("FIELD_HELP", agent_templates.FIELD_HELP_BY_LANG),
    ("FIELD_EXAMPLES", agent_templates.FIELD_EXAMPLES_BY_LANG),
]:
    en, de = set(catalog["en"]), set(catalog["de"])
    check(f"{name}: DE covers EN", en == de, f"missing {sorted(en - de)}, extra {sorted(de - en)}")
    identical = [k for k in en if catalog["en"][k] == catalog["de"].get(k)]
    check(f"{name}: nothing left untranslated", not identical, f"{identical}")

# Every field the forms can show must have a DE label, help text and example.
all_fields = set()
for archetype in agent_templates.ARCHETYPES:
    all_fields.update(agent_templates.placeholders_for(archetype))
for kind, getter in [
    ("label", agent_templates.field_labels_for),
    ("help", agent_templates.field_help_for),
    ("example", agent_templates.field_examples_for),
]:
    de_map = getter("de")
    missing = sorted(f for f in all_fields if not de_map.get(f))
    check(f"every form field has a DE {kind}", not missing, f"{missing}")

# ── 3. Language resolution ──────────────────────────────────────────────────
check("normalize: de", i18n.normalize_language("de") == "de")
check("normalize: de-DE", i18n.normalize_language("de-DE") == "de")
check("normalize: garbage -> default", i18n.normalize_language("klingon") == "en")
check("normalize: None -> default", i18n.normalize_language(None) == "en")
check("accept: German browser", i18n.language_from_accept_header("de-DE,de;q=0.9,en;q=0.8") == "de")
check("accept: English browser", i18n.language_from_accept_header("en-US,en;q=0.9") == "en")
check("accept: unsupported -> default", i18n.language_from_accept_header("fr-FR,fr;q=0.9") == "en")
check("accept: q-weights honoured", i18n.language_from_accept_header("en;q=0.3, de;q=0.9") == "de")
check("accept: missing header", i18n.language_from_accept_header(None) == "en")

# ── 4. t() behaviour ────────────────────────────────────────────────────────
check("t: DE lookup", i18n.t("nav_agents", lang="de") == "Agenten")
check("t: EN lookup", i18n.t("nav_agents", lang="en") == "Agents")
check("t: unknown lang falls back to EN", i18n.t("nav_agents", lang="fr") == "Agents")
check("t: unknown key returns the key", i18n.t("no_such_key_xyz", lang="de") == "no_such_key_xyz")
check("t: substitution", "%s" not in i18n.t("slot_heading", 4, lang="de"), i18n.t("slot_heading", 4, lang="de"))
check("t: wrong arg count doesn't raise", isinstance(i18n.t("slot_heading", lang="de"), str))

# ── 5. Live request behaviour ───────────────────────────────────────────────
client = flask_app_module.app.test_client()
client.post("/setup", data={
    "username": "admin", "password": "a-strong-test-password", "confirm": "a-strong-test-password",
}, follow_redirects=True)

# Browser preference drives the first visit (no cookie yet)
resp = client.get("/", headers={"Accept-Language": "de-DE,de;q=0.9"})
body = resp.get_data(as_text=True)
check("browser German -> German page", "Agenten (bis zu 10)" in body, body[:200])
check("html lang attribute follows", 'lang="de"' in body)

resp = client.get("/", headers={"Accept-Language": "en-US,en;q=0.9"})
check("browser English -> English page", "Agents (up to 10)" in resp.get_data(as_text=True))

# The switch sets a cookie and returns the operator to where they were
resp = client.get("/language/de", headers={"Referer": "/upload"}, follow_redirects=False)
check("switch redirects", resp.status_code == 302, f"got {resp.status_code}")
check("switch returns to referrer", resp.headers.get("Location", "").endswith("/upload"),
      resp.headers.get("Location"))
check("switch sets cookie", i18n.LANGUAGE_COOKIE in resp.headers.get("Set-Cookie", ""),
      resp.headers.get("Set-Cookie"))

# Cookie now wins over an opposing browser preference
resp = client.get("/", headers={"Accept-Language": "en-US,en;q=0.9"})
check("cookie beats browser header", "Agenten (bis zu 10)" in resp.get_data(as_text=True))

# An off-site Referer must not be honoured (open-redirect guard)
resp = client.get("/language/en", headers={"Referer": "https://evil.example.com/x"},
                  follow_redirects=False)
location = resp.headers.get("Location", "")
check("off-site referrer rejected", "evil.example.com" not in location, location)

# Bogus language codes fall back rather than 404 or breaking the page
resp = client.get("/language/klingon", follow_redirects=True)
check("bogus language still renders", resp.status_code == 200, f"got {resp.status_code}")

# ── 6. German content actually reaches the pages ────────────────────────────
client.get("/language/de")
resp = client.get("/slot/4")
body = resp.get_data(as_text=True)
check("archetype picker in German", "Agententyp wählen" in body, body[:300])
check("archetype names translated", "Themen-Agent" in body)
check("archetype descriptions translated", "Kursassistent" in body)

client.post("/slot/4", data={"action": "choose_archetype", "archetype": "agent-topic-template.json"})
resp = client.post("/slot/4", data={"action": "choose_archetype", "archetype": "agent-topic-template.json"})
body = resp.get_data(as_text=True)
check("field labels in German", "Name des Kapitels" in body, "TOPIC_NAME label")
check("field help in German", "Unterabschnitte innerhalb dieses Kapitels" in body)
check("field examples in German", "Kognitive Lernvoraussetzungen" in body)
check("buttons in German", "Mit KI verbessern" in body)
check("no leftover English label", "Topic Name" not in body, "English label still present")

resp = client.get("/upload")
body = resp.get_data(as_text=True)
check("upload page in German", "Kursdokumente hochladen" in body, body[:300])

resp = client.get("/graph-guidance")
check("graph page in German", "Wissensgraph" in resp.get_data(as_text=True))

# Validation messages come back translated too
resp = client.post("/upload", data={"title": "x"}, follow_redirects=True)
check("upload validation message in German", "Bitte wähle" in resp.get_data(as_text=True))

# ── 7. Switching back to English restores English ───────────────────────────
client.get("/language/en")
# POST rather than GET: slot 4 was never saved, so a GET renders the
# archetype picker instead of the content form the labels live on.
resp = client.post("/slot/4", data={"action": "choose_archetype", "archetype": "agent-topic-template.json"})
body = resp.get_data(as_text=True)
check("switch back to English", "Topic Name" in body, body[:300])
check("no German left after switching back", "Name des Kapitels" not in body)

if failures:
    print("FAILURES:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print(
    "All i18n checks passed: catalogue completeness (no missing/extra/untranslated keys in "
    "UI strings, archetypes, field help/examples/labels), language resolution (cookie > "
    "Accept-Language > default, q-weights, bogus input), t() fallbacks and substitution, "
    "live request behaviour (browser detection, switch + cookie persistence, referrer "
    "return, open-redirect guard), and German content reaching every page including "
    "validation messages — plus a clean switch back to English."
)
