"""
SMART RAG — LTI 1.3 Middleware
================================

Connects an LMS (Moodle, ILIAS, Canvas, ...) to Flowise AgentFlow chatbots
via the LTI 1.3 protocol.

Responsibilities:
  - Validate the LTI launch from the LMS (JWT signed by the LMS).
  - Look up the requested agent in config/agents.json.
  - Mint a short-lived session token (stored in Redis).
  - Serve the Flowise embed page (bubble or fullscreen) inside the LMS iframe.
  - Provide a /consent/check endpoint for the LMS bubble-embed integration.

Configuration:
  - LTI tool config:  config/lti.json
  - Agent mapping:    config/agents.json
  - Branding/UI text: config/branding.json
  - Keys (LTI):       config/private.key, config/public.key
                      → generate via ./generate_keys.sh

Stack: Flask + Gunicorn + PyLTI1p3 + Redis (session store).
"""

import os
import secrets
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import redis
from flask import Flask, request, make_response
from flask_caching import Cache
from flask_cors import CORS
from pylti1p3.contrib.flask import (
    FlaskRequest, FlaskOIDCLogin, FlaskMessageLaunch, FlaskCacheDataStorage
)
from pylti1p3.tool_config import ToolConfJsonFile
from pylti1p3.exception import LtiException

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
)
logger = logging.getLogger("smartrag.lti")


# ─── Configuration ────────────────────────────────────────────────────────────

CONFIG_DIR       = Path(os.getenv("LTI_CONFIG_DIR", "/app/config"))
LTI_CONFIG_PATH  = CONFIG_DIR / "lti.json"
AGENTS_PATH      = CONFIG_DIR / "agents.json"
BRANDING_PATH    = CONFIG_DIR / "branding.json"

FLOWISE_HOST     = os.getenv("FLOWISE_HOST",  "https://smart-rag.example.com")
LMS_URL          = os.getenv("LMS_URL",       "https://lms.example.com")
REDIS_URL        = os.getenv("REDIS_URL",     "redis://smartrag-redis:6379")
SESSION_SECRET   = os.getenv("LTI_SESSION_SECRET", secrets.token_hex(32))
SESSION_TTL      = int(os.getenv("SESSION_TTL_SEC", "3600"))
CONSENT_TTL      = int(os.getenv("CONSENT_TTL_SEC", "86400"))
COOKIE_NAME      = os.getenv("LTI_COOKIE_NAME", "smartrag_lti_session")


def _load_json(path: Path, required: bool = True) -> dict:
    if not path.exists():
        if required:
            raise FileNotFoundError(f"Required config file missing: {path}")
        logger.warning(f"Optional config not found, using defaults: {path}")
        return {}
    with open(path) as f:
        return json.load(f)


# ─── Agent mapping ────────────────────────────────────────────────────────────
# Loaded from config/agents.json. Chatflow IDs are resolved from env vars
# named by the `chatflow_env` field of each agent.

def _load_agent_config() -> dict[str, dict]:
    raw = _load_json(AGENTS_PATH)
    agents = {}
    for item in raw.get("agents", []):
        agent_id = item["id"]
        chatflow_env = item.get("chatflow_env", "")
        agents[agent_id] = {
            "label":        item.get("label", agent_id),
            "chatflow_id":  os.getenv(chatflow_env, "") if chatflow_env else "",
            "block":        item.get("block", 0),
            "embed_style":  item.get("embed_style", "fullscreen"),  # "bubble" | "fullscreen"
        }
    if not agents:
        logger.warning("No agents loaded — agents.json is empty or malformed.")
    return agents


AGENT_CONFIG = _load_agent_config()


# ─── Branding ─────────────────────────────────────────────────────────────────
# Loaded from config/branding.json. All UI strings, colors, and asset URLs
# come from here — there are NO hardcoded LMS or course-specific values
# anywhere below this point.

DEFAULT_BRANDING = {
    "title":          "SMART RAG · Learning Assistant",
    "html_lang":      "en",
    "institution":    "Your Institution",
    "institution_url":"https://example.com",
    "logo_url":       "",
    "bot_avatar_url": "",
    "colors": {
        "primary":           "#0066CC",
        "background":        "#FFFFFF",
        "bot_message_bg":    "#F5F5F5",
        "bot_message_text":  "#232323",
        "user_message_bg":   "#232323",
        "user_message_text": "#FFFFFF",
        "footer_text":       "#626468",
    },
    "language": {
        "welcome_fullscreen":    "Hello {given_name}! How can I help you today?",
        "welcome_bubble":        "Hi {given_name}!",
        "input_placeholder":     "Your question...",
        "footer_disclaimer":     "This assistant is an AI and may make mistakes — please verify its statements.",
        "error_title":           "Access denied",
        "error_unknown_agent":   "Agent '{agent_id}' is not configured.",
        "error_invalid_launch":  "Invalid LTI launch.",
        "error_no_user_id":      "user_id missing from JWT.",
        "error_session_invalid": "Please open the assistant from your LMS course.",
        "default_given_name":    "Student",
    },
}


def _deep_merge(default: dict, override: dict) -> dict:
    """Merge override into default, recursively (override wins)."""
    out = {**default}
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


BRANDING = _deep_merge(DEFAULT_BRANDING, _load_json(BRANDING_PATH, required=False))


# ─── Flask app ────────────────────────────────────────────────────────────────

app = Flask(__name__)
app.secret_key = SESSION_SECRET
app.config.update(
    SESSION_COOKIE_SECURE   = True,
    SESSION_COOKIE_HTTPONLY = True,
    SESSION_COOKIE_SAMESITE = "None",
    CACHE_TYPE              = "RedisCache",
    CACHE_REDIS_URL         = REDIS_URL,
    CACHE_DEFAULT_TIMEOUT   = SESSION_TTL,
)

cache = Cache(app)

# Allow the LMS to call /consent/check from its bubble-embed
CORS(app, resources={
    r"/consent/check": {"origins": [LMS_URL]},
})

redis_client = redis.from_url(REDIS_URL, decode_responses=True)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def get_tool_conf():
    return ToolConfJsonFile(str(LTI_CONFIG_PATH))

def get_launch_data_storage():
    return FlaskCacheDataStorage(cache)

def session_create(user_id: str, given_name: str, agent_id: str) -> str:
    # The full name is deliberately absent. It was stored here and read in
    # exactly one place, to be pasted into the session id; with that gone
    # there is no longer a reason to hold it at all.
    token = secrets.token_urlsafe(32)
    data = json.dumps({
        "user_id":    user_id,
        "given_name": given_name,
        "agent_id":   agent_id,
        "ts":         datetime.now(timezone.utc).isoformat(),
    })
    redis_client.setex(f"lti:session:{token}", SESSION_TTL, data)
    logger.info(f"Session created — user={user_id} agent={agent_id}")
    return token

def session_get(token):
    if not token:
        return None
    raw = redis_client.get(f"lti:session:{token}")
    if not raw:
        return None
    # Sliding window: extend TTL on each access
    redis_client.expire(f"lti:session:{token}", SESSION_TTL)
    return json.loads(raw)

def _build_launch_response(data, agent_id):
    lang = BRANDING["language"]
    if agent_id not in AGENT_CONFIG:
        return _error_page(lang["error_unknown_agent"].format(agent_id=agent_id)), 404

    user_id    = data.get("sub", "")
    given_name = data.get("given_name", lang["default_given_name"])

    if not user_id:
        return _error_page(lang["error_no_user_id"]), 403

    # The pseudonym and the agent, and nothing else. This line used to carry
    # the learner's given name and full name into a log file that is kept for
    # operational reasons and read by whoever administers the server — a
    # second copy of exactly the data the LTI pseudonym exists to avoid.
    logger.info(f"LTI launch — user={user_id} agent={agent_id}")

    # Consent flag: lets the LMS bubble-embed know the user has launched
    # the assistant at least once via the proper LTI flow.
    redis_client.setex(f"lti:consent:{user_id}", CONSENT_TTL, "yes")

    token    = session_create(user_id, given_name, agent_id)
    chat_url = f"/chat/{agent_id}?token={token}"

    # Break out of LMS iframe if we are in one (configurable in LMS-side later)
    html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body>
<script>
  if (window.top !== window.self) {{
    window.top.location.href = "{chat_url}";
  }} else {{
    window.location.href = "{chat_url}";
  }}
</script>
<noscript><a href="{chat_url}">Continue to assistant</a></noscript>
</body></html>"""
    return make_response(html, 200)


# ─── LTI endpoints ────────────────────────────────────────────────────────────

@app.route("/lti/login", methods=["GET", "POST"])
def lti_login():
    """OIDC login init — called by the LMS to start the launch flow."""
    tool_conf = get_tool_conf()
    flask_request = FlaskRequest()
    oidc_login = FlaskOIDCLogin(
        request             = flask_request,
        tool_config         = tool_conf,
        launch_data_storage = get_launch_data_storage(),
    )
    target_link_uri = request.values.get("target_link_uri")
    return oidc_login.redirect(target_link_uri)


@app.route("/lti/launch", methods=["POST"])
def lti_launch_generic():
    """
    Generic launch — agent_id comes from LTI 'custom' claim.
    Use one tool registration in the LMS and pass `custom_agent_id`.
    """
    try:
        message_launch = FlaskMessageLaunch(
            request             = FlaskRequest(),
            tool_config         = get_tool_conf(),
            launch_data_storage = get_launch_data_storage(),
        )
        data = message_launch.get_launch_data()
    except LtiException as e:
        logger.warning(f"LTI launch rejected: {e}")
        return _error_page(BRANDING["language"]["error_invalid_launch"]), 403

    custom = data.get("https://purl.imsglobal.org/spec/lti/claim/custom", {})
    agent_id = custom.get("agent_id", "agent-01")
    return _build_launch_response(data, agent_id)


@app.route("/lti/launch/<agent_id>", methods=["POST"])
def lti_launch(agent_id):
    """
    Per-agent launch — agent_id in URL path.
    Use this when the LMS registers one tool per agent.
    """
    if agent_id not in AGENT_CONFIG:
        return _error_page(
            BRANDING["language"]["error_unknown_agent"].format(agent_id=agent_id)
        ), 404

    try:
        message_launch = FlaskMessageLaunch(
            request             = FlaskRequest(),
            tool_config         = get_tool_conf(),
            launch_data_storage = get_launch_data_storage(),
        )
        data = message_launch.get_launch_data()
    except LtiException as e:
        logger.warning(f"LTI launch rejected: {e}")
        return _error_page(BRANDING["language"]["error_invalid_launch"]), 403

    return _build_launch_response(data, agent_id)


# ─── Chat & service endpoints ─────────────────────────────────────────────────

@app.route("/chat/<agent_id>", methods=["GET"])
def chat_page(agent_id):
    token        = request.args.get("token") or request.cookies.get(COOKIE_NAME)
    session_data = session_get(token)
    lang         = BRANDING["language"]

    if not session_data or session_data.get("agent_id") != agent_id:
        return _error_page(lang["error_session_invalid"]), 401
    if agent_id not in AGENT_CONFIG:
        return _error_page(lang["error_unknown_agent"].format(agent_id=agent_id)), 404

    cfg         = AGENT_CONFIG[agent_id]
    chatflow_id = cfg["chatflow_id"]
    user_id     = session_data["user_id"]
    given_name  = session_data["given_name"]
    ts          = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    # Session ID format consumed by the Flowise agent custom functions:
    #   {user_id}||{agent_id}|{timestamp}
    #
    # The learner's given name and full name used to occupy fields 2 and 5.
    # They were read by nothing except a workflow that is switched off, and
    # this string is not private: Flowise stores it on every chat message,
    # chathistory-sync copies it into Weaviate's ChatHistory, and — because
    # the launch below sends it as the Langfuse sessionId — it is stamped on
    # every trace. Three systems carrying a clear name beside the pseudonym
    # that exists so they would not have to. The name the agents actually
    # greet the learner with travels separately, as flowState.student_name.
    #
    # Field 2 stays empty rather than closing the gap. The agents read field 1
    # and chathistory-sync reads field 3, and conversations recorded before
    # this change still have the old five-field shape — renumbering would
    # make every one of them look like a different agent.
    session_id = f"{user_id}||{agent_id}|{ts}"

    if cfg["embed_style"] == "bubble":
        return _bubble_page(chatflow_id, user_id, given_name, session_id, agent_id)
    return _fullscreen_page(chatflow_id, user_id, given_name, session_id, agent_id)


@app.route("/health")
def health():
    try:
        redis_client.ping()
        return {"status": "ok", "redis": "ok", "agents_loaded": len(AGENT_CONFIG)}
    except Exception as e:
        return {"status": "degraded", "redis": str(e)}, 500


@app.route("/consent/check", methods=["GET"])
def consent_check():
    user_id = request.args.get("user_id", "").strip()
    if not user_id:
        return {"consent": False}, 400
    has_consent = redis_client.exists(f"lti:consent:{user_id}") > 0
    return {"consent": has_consent}


# ─── HTML templates ───────────────────────────────────────────────────────────
# All visual styling and copy comes from BRANDING — there are no LMS- or
# course-specific values below this point.

def _fullscreen_page(chatflow_id, user_id, given_name, session_id, agent_id):
    c    = BRANDING["colors"]
    lang = BRANDING["language"]
    welcome = lang["welcome_fullscreen"].format(given_name=given_name)
    return f"""<!DOCTYPE html>
<html lang="{BRANDING['html_lang']}">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{BRANDING['title']}</title>
  <style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    html, body {{ height: 100%; overflow: hidden; background: {c['background']}; }}
    flowise-fullchatbot {{ display: block; height: 100vh; }}
  </style>
</head>
<body>
  <flowise-fullchatbot></flowise-fullchatbot>
  <script type="module">
    import Chatbot from 'https://cdn.jsdelivr.net/npm/flowise-embed/dist/web.js';
    Chatbot.initFull({{
      chatflowid: '{chatflow_id}',
      apiHost:    '{FLOWISE_HOST}',
      chatflowConfig: {{
        sessionId: '{session_id}',
        streaming: true,
        vars: {{
          userId:   '{user_id}',
          userName: '{given_name}',
          agentId:  '{agent_id}'
        }},
        overrideConfig: {{
          analytics: {{ langFuse: {{ userId: '{user_id}', sessionId: '{session_id}' }} }},
          flowState: {{ student_name: '{given_name}' }}
        }}
      }},
      theme: {{
        chatWindow: {{
          showTitle: true,
          title:                '{BRANDING['title']}',
          titleAvatarSrc:       '{BRANDING['logo_url']}',
          titleBackgroundColor: '{c['primary']}',
          titleTextColor:       '{c['user_message_text']}',
          welcomeMessage:       {json.dumps(welcome)},
          backgroundColor:      '{c['background']}',
          fontSize: 15, renderHTML: true, clearChatOnReload: false,
          botMessage: {{
            backgroundColor: '{c['bot_message_bg']}',
            textColor:       '{c['bot_message_text']}',
            showAvatar: {str(bool(BRANDING['bot_avatar_url'])).lower()},
            avatarSrc:       '{BRANDING['bot_avatar_url']}'
          }},
          userMessage: {{
            backgroundColor: '{c['user_message_bg']}',
            textColor:       '{c['user_message_text']}',
            showAvatar: false
          }},
          textInput: {{
            placeholder:      {json.dumps(lang['input_placeholder'])},
            backgroundColor: '{c['background']}',
            textColor:       '{c['bot_message_text']}',
            sendButtonColor: '{c['primary']}',
            autoFocus: true
          }},
          footer: {{
            textColor:   '{c['footer_text']}',
            text:        {json.dumps(lang['footer_disclaimer'])},
            company:     '{BRANDING['institution']}',
            companyLink: '{BRANDING['institution_url']}'
          }}
        }}
      }}
    }});
  </script>
</body>
</html>"""


def _bubble_page(chatflow_id, user_id, given_name, session_id, agent_id):
    c    = BRANDING["colors"]
    lang = BRANDING["language"]
    welcome = lang["welcome_bubble"].format(given_name=given_name)
    return f"""<!DOCTYPE html>
<html lang="{BRANDING['html_lang']}">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{BRANDING['title']}</title>
  <style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    html, body {{ height: 100%; background: {c['background']}; }}
  </style>
</head>
<body>
  <script type="module">
    import Chatbot from 'https://cdn.jsdelivr.net/npm/flowise-embed/dist/web.js';
    Chatbot.init({{
      chatflowid: '{chatflow_id}',
      apiHost:    '{FLOWISE_HOST}',
      chatflowConfig: {{
        sessionId: '{session_id}',
        streaming: true,
        vars: {{
          userId:   '{user_id}',
          userName: '{given_name}',
          agentId:  '{agent_id}'
        }},
        overrideConfig: {{
          analytics: {{ langFuse: {{ userId: '{user_id}', sessionId: '{session_id}' }} }},
          flowState: {{ student_name: '{given_name}' }}
        }}
      }},
      theme: {{
        button: {{
          backgroundColor: '{c['primary']}',
          iconColor: 'white',
          autoWindowOpen: {{ autoOpen: true, openDelay: 1, autoOpenOnMobile: true }}
        }},
        chatWindow: {{
          showTitle: true,
          title:                '{BRANDING['title']}',
          titleAvatarSrc:       '{BRANDING['logo_url']}',
          titleBackgroundColor: '{c['primary']}',
          titleTextColor:       '{c['user_message_text']}',
          welcomeMessage:       {json.dumps(welcome)},
          backgroundColor:      '{c['background']}',
          fontSize: 15, renderHTML: true, clearChatOnReload: false,
          botMessage: {{
            backgroundColor: '{c['bot_message_bg']}',
            textColor:       '{c['bot_message_text']}',
            showAvatar: {str(bool(BRANDING['bot_avatar_url'])).lower()},
            avatarSrc:       '{BRANDING['bot_avatar_url']}'
          }},
          userMessage: {{
            backgroundColor: '{c['user_message_bg']}',
            textColor:       '{c['user_message_text']}',
            showAvatar: false
          }},
          textInput: {{
            placeholder:      {json.dumps(lang['input_placeholder'])},
            backgroundColor: '{c['background']}',
            textColor:       '{c['bot_message_text']}',
            sendButtonColor: '{c['primary']}',
            autoFocus: true
          }},
          footer: {{
            textColor:   '{c['footer_text']}',
            text:        {json.dumps(lang['footer_disclaimer'])},
            company:     '{BRANDING['institution']}',
            companyLink: '{BRANDING['institution_url']}'
          }}
        }}
      }}
    }});
  </script>
</body>
</html>"""


def _error_page(message):
    lang = BRANDING["language"]
    c    = BRANDING["colors"]
    return f"""<!DOCTYPE html>
<html lang="{BRANDING['html_lang']}">
<head>
  <meta charset="UTF-8"><title>{lang['error_title']}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; display: flex; align-items: center;
           justify-content: center; height: 100vh; background: #F5F5F5; margin: 0; }}
    .box {{ background: white; padding: 2rem; border-radius: 8px; text-align: center;
            box-shadow: 0 2px 8px rgba(0,0,0,.1); max-width: 420px; }}
    h2 {{ color: #dc2626; margin-bottom: 1rem; }}
    p  {{ color: {c['footer_text']}; line-height: 1.5; }}
  </style>
</head>
<body>
  <div class="box">
    <h2>{lang['error_title']}</h2>
    <p>{message}</p>
  </div>
</body>
</html>"""
