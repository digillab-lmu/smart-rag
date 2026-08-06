"""
UI translations (English + German) for the content-admin GUI.

Mirrors scripts/lib/messages.sh's approach deliberately — one dict per
language, keys looked up by name, English as the fallback when a German
key is missing — so anyone who has worked on the shell side finds the
same shape here. check_translations() is the Python counterpart of the
shell function with the same name.

Language is per-user, not per-installation: it comes from a cookie the
DE|EN switch sets, falling back to the browser's Accept-Language header
on a first visit. That means two people can use the GUI in different
languages at the same time — which a single .env setting couldn't do.

Content-side strings (archetype descriptions, per-field help and worked
examples) live in agent_templates.py, not here, since they belong with
the templates they describe.
"""

from __future__ import annotations

LANGUAGES = ("en", "de")
DEFAULT_LANGUAGE = "en"
LANGUAGE_COOKIE = "smartrag_lang"
# Ten years — this is a UI preference, not session state; there's nothing
# to expire and re-picking it on every visit would be a nuisance.
LANGUAGE_COOKIE_MAX_AGE = 10 * 365 * 24 * 60 * 60


MSG_EN: dict[str, str] = {
    # --- chrome ---------------------------------------------------------
    "app_name": "SMART RAG",
    "app_subtitle": "Content Admin",
    "nav_agents": "Agents",
    "nav_documents": "Documents",
    "nav_graph": "Knowledge Graph",
    "nav_flowise": "Flowise Connection",
    "nav_logout": "Log out",
    # The chair's name is kept in German in both languages — it's a proper
    # institutional name, and inventing an English rendering we haven't
    # verified against LMU's own usage would be worse than leaving it.
    "footer_credit": 'SMART RAG is developed at the <a href="https://www.lmu.de/digillab/de/" target="_blank" rel="noopener">DigiLLab of LMU Munich</a> | <a href="https://www.psy.lmu.de/ffp/" target="_blank" rel="noopener">Lehrstuhl Empirische Pädagogik und Pädagogische Psychologie</a>, <a href="https://www.psy.lmu.de/ffp/persons/lehrstuhlinhaber/fischer-frank/index.html" target="_blank" rel="noopener">Prof. Frank Fischer</a>.',
    "footer_contact": 'Developer: <a href="mailto:benjamin.goetzinger@psy.lmu.de">Benjamin Götzinger</a>',
    "footer_repo": 'GitHub: <a href="https://github.com/digillab-lmu/smart-rag" target="_blank" rel="noopener">github.com/digillab-lmu/smart-rag</a>',
    "lang_switch_label": "Language",

    # --- first-run setup ------------------------------------------------
    "setup_title": "First-time setup",
    "setup_heading": "Welcome — create your admin account",
    "setup_intro": "This is separate from your Flowise login — it only protects this content-authoring tool.",
    "setup_username": "Username",
    "setup_password": "Password (min. 12 characters)",
    "setup_confirm": "Confirm password",
    "setup_submit": "Create account",
    "setup_err_required": "Username and password are required.",
    "setup_err_mismatch": "Passwords do not match.",
    "setup_err_too_short": "Password must be at least 12 characters.",

    # --- login ----------------------------------------------------------
    "login_title": "Log in",
    "login_heading": "Content Admin",
    "login_username": "Username",
    "login_password": "Password",
    "login_submit": "Log in",
    "login_err_invalid": "Invalid username or password.",

    # --- Flowise connection ---------------------------------------------
    "flowise_title": "Flowise Connection",
    "flowise_heading": "Connect to Flowise",
    "flowise_intro": "Flowise has no supported way to hand out an API key automatically — a one-time manual step is unavoidable:",
    "flowise_step1": "Open your Flowise instance and create your admin account, if you haven't already.",
    "flowise_step2_pre": "Go to",
    "flowise_step2_path": "Settings → API Keys → Create Key",
    "flowise_step3": "Under <strong>Permissions</strong>, check every box under <strong>Chatflows</strong>, <strong>Agentflows</strong>, <strong>Credentials</strong>, and <strong>Variables</strong> — that's everything this GUI actually calls. Leave other categories (Tools, Document Stores, Assistants, …) unchecked; nothing here uses them.",
    "flowise_step4": "Paste the key below.",
    "flowise_already_set": "A Flowise API key is already saved. Paste a new one below to replace it.",
    "flowise_key_label": "Flowise API key",
    "flowise_submit": "Save and verify",
    "flowise_err_required": "API key is required.",
    "flowise_err_connect": "Could not connect to Flowise with this key: %s",
    "flowise_saved": "Connected — Flowise API key saved.",

    # --- dashboard ------------------------------------------------------
    "dash_title": "Agents",
    "dash_heading": "Agents (up to 10)",
    "dash_not_connected": 'Flowise isn\'t connected yet. <a href="%s">Set it up first</a> — you can still fill in content below, but importing needs the connection.',
    "dash_connection_broken": 'A Flowise API key is saved, but Flowise is not accepting it right now — the key may have been deleted or had its permissions reduced, or Flowise may be down. Importing and publishing will fail until this is resolved. <a href="%s">System status</a> says which of the two it is.',
    "dash_col_slot": "Slot",
    "dash_col_name": "Name",
    "dash_col_archetype": "Archetype",
    "dash_col_status": "Status",
    "dash_status_imported": "Imported",
    "dash_status_saved": "Content saved, not imported",
    "dash_status_empty": "Empty",
    "dash_edit": "Edit",

    # --- slot / agent editing -------------------------------------------
    "slot_title": "Slot %s",
    "slot_heading": "Slot %s",
    "slot_back": "← back to agents",
    "slot_choose_archetype": "Choose an archetype",
    "slot_rag_note": 'All except Backup Assistant use RAG (retrieval over your course documents). Once this agent is set up, add its documents on the <a href="%s">Documents</a> page.',
    "slot_continue": "Continue",
    "slot_archetype_label": "Archetype",
    "slot_imported_as": "imported (chatflow id: %s)",
    "slot_name_label": "Agent Name",
    "slot_name_help": 'A unique name for this agent, so it\'s easy to tell apart from your other agents — this becomes the chatflow\'s name in Flowise. Example: "Chapter 4 Tutor" or "Sarah the Statistics Expert". Must be different from every other agent\'s name.',
    "slot_examples_note": "Each field below shows a complete worked example as gray placeholder text (it disappears once you start typing) — write your own course content following that same pattern.",
    "slot_optimize": "Optimize with AI",
    "slot_optimizing": "Optimizing…",
    "slot_suggestion": "AI suggestion",
    "slot_use_this": "Use this",
    "slot_discard": "Discard",
    "slot_close": "Close",
    "slot_save": "Save",
    "slot_import": "Save and import to Flowise",
    # --- system prompt editing -------------------------------------------
    "slot_prompt_heading": "System prompt",
    "slot_prompt_toggle_show": "Show and edit",
    "slot_prompt_toggle_hide": "Hide",
    "slot_prompt_intro": "This is the instruction the agent runs on — its role, rules, tone and task. It ships with a worked default for this agent type; edit it only if you want to change how the agent behaves pedagogically.",
    "slot_prompt_placeholders_note": "Text in double braces like {{TOPIC_NAME}} is filled in from the fields above. Add one and a matching field appears after saving; remove one and its field disappears.",
    "slot_prompt_customised": "Edited — no longer follows the default for this agent type.",
    "slot_prompt_default": "Unchanged default for this agent type.",
    "slot_prompt_reset": "Reset to default",
    "slot_prompt_reset_ok": "System prompt reset to the default for this agent type.",
    # --- onboarding guide -------------------------------------------------
    "guide_title": "System status",
    "guide_heading": "System status",
    "guide_intro": "Every line below is checked live, right now, by asking the service itself — not by reading something we noted down earlier. Reload the page after a change to see the new state.",
    "guide_ready": "Everything is set up. Your agents can answer questions and take documents.",
    "guide_blocked": "Start here: %s",
    "guide_recheck": "Check again",
    "guide_state_ok": "OK",
    "guide_state_warn": "Needs attention",
    "guide_state_fail": "Not ready",
    "guide_state_unknown": "Unclear",
    "guide_admin_needed": "This step is for whoever administers this server. It needs command-line access to the machine, which this interface deliberately does not have — and which you may not have either. If that is someone else, send them the message below.",
    "guide_admin_forward": "Message to forward:",
    "guide_admin_message": "Hi, the SMART RAG document-ingest workflows are not active on our server, so uploading course documents currently fails. Could you run this in the SMART RAG installation directory?\n\n%s\n\nIt is safe to re-run, and it checks by itself afterwards that the webhook is live. Background: docs/operations-guide.md, section \"n8n (automation)\". Thanks!",
    "guide_command_intro": "The command, on its own:",
    "guide_command_why": "Why this interface cannot just do it: it has no access to Docker or to the server's filesystem, on purpose. A web interface that could start containers would be a much bigger target than one that can only talk to other services over HTTP.",
    "guide_copy_message": "Copy message",

    "guide_llm_keys_title": "API keys for language model and embeddings",
    "guide_llm_keys_help": "Without these, no agent can answer and no document can be indexed. Both are set in the .env file on the server.",
    "guide_llm_keys_fail": "Not set yet: %s",
    "guide_llm_keys_ok": "Provider: %s",

    "guide_flowise_title": "Connection to Flowise",
    "guide_flowise_help": "Flowise runs the agents. This GUI needs an API key of its own to create and update them there.",
    "guide_flowise_link": "Set up the connection",

    "guide_agents_title": "Agents",
    "guide_agents_help": "An agent is a role your students talk to. Fill in its course content here, then import it into Flowise.",
    "guide_agents_link": "Go to agents",
    "guide_agents_fail": "No agent has been filled in yet.",
    "guide_agents_warn_unimported": "%s agent(s) filled in, none imported into Flowise yet.",
    "guide_agents_ok": "%s of %s agent(s) imported.",
    "guide_agents_warn_stale": "%s imported agent(s) no longer exist in Flowise — import them again.",

    "guide_n8n_title": "Document processing (n8n)",
    "guide_n8n_help": "n8n receives every uploaded document and runs it through conversion, cleanup, chunking and indexing. Its workflows have to be imported and switched on once.",
    "guide_n8n_ok": "The ingest workflow is active and accepting documents.",
    "guide_n8n_fail": "The ingest workflow is not registered — it is either not imported or not active.",

    "guide_ingest_services_title": "Conversion and search services",
    "guide_ingest_services_help": "Docling reads documents, markdowncleaner tidies the result, Weaviate stores it for retrieval. All three are started by Docker Compose.",
    "guide_ingest_services_fail": "Not reachable: %s",
    "guide_ingest_services_ok": "Docling, markdowncleaner and Weaviate are all reachable.",

    "guide_next_title": "What now?",
    "guide_next_upload": "Upload your first document",
    "guide_next_graph": "Set up the knowledge graph (optional)",
    # --- documents list / deletion ---------------------------------------
    "nav_doclist": "Indexed",
    "docs_title": "Indexed documents",
    "docs_heading": "What is in the index",
    "docs_intro": "Everything that has been ingested for this course, grouped by document. Removing one takes all of its chunks out of the index — the agents stop finding it immediately.",
    "docs_why_delete": "Worth doing when a document was uploaded by mistake, when a revised edition would otherwise sit next to its predecessor, or when an agent slot is reused for a different topic — the old documents keep the old slot's number and would still be found by whoever takes that slot next.",
    "docs_empty": "Nothing has been ingested for this course yet.",
    "docs_col_title": "Document",
    "docs_col_agent": "Agent",
    "docs_col_meta": "Details",
    "docs_col_chunks": "Chunks",
    "docs_col_action": "",
    "docs_delete": "Remove",
    "docs_delete_confirm": "Remove \"%s\" from the index? Its %s chunk(s) are deleted and the agents stop finding it. The original file stays in object storage; only the index entry goes.",
    "docs_deleted": "Removed %s chunk(s) of \"%s\" from the index.",
    "docs_agent_unknown": "Agent %s (slot not configured)",
    "docs_agent_none": "no agent",
    "docs_truncated": "Only part of this course is listed — it has more chunks than one page reads at a time. Remove something, or ask for a larger limit.",
    "docs_total": "%s chunk(s) in total.",
    "docs_err_not_configured": "COURSE_ID or WEAVIATE_COLLECTION_NAME is missing from .env, so there is nothing to list.",
    "docs_err_no_title": "No document was named.",
    "docs_err_list_failed": "Could not read the index: %s",
    "docs_err_delete_failed": "Could not remove the document: %s",
    # --- publishing -------------------------------------------------------
    "publish_heading": "Publish this agent",
    "publish_intro": "Publishing gives this agent a web address anyone can open — no Flowise login, no course enrolment, no password. Useful for a public demo or a link in a syllabus; not something to do casually.",
    "publish_warning": "Before you publish: every message sent through the public link is answered on your LLM budget, and anyone who has the link can send as many as they like. The agent will also answer without knowing who is asking, so nothing personalised and nothing depending on a course role will work there.",
    "publish_state_private": "Not published — reachable only through Flowise or the course integration.",
    "publish_state_public": "Published — anyone with the link can use this agent.",
    "publish_state_unknown": "Could not read the published state from Flowise.",
    "publish_state_gone": "This agent no longer exists in Flowise — import it again before publishing.",
    "publish_button": "Publish",
    "publish_unpublish_button": "Withdraw publication",
    "publish_confirm": "Publish this agent? Anyone with the link will be able to use it without logging in, and every message is answered on your LLM budget.",
    "publish_url_label": "Public link",
    "publish_embed_label": "Embed in a web page",
    "publish_embed_help": "Paste this into a page's HTML to put the agent in a box on that page.",
    "publish_copy": "Copy",
    "publish_copied": "Copied",
    "publish_ok": "Agent published. The public link is shown below.",
    "unpublish_ok": "Publication withdrawn. The public link no longer works.",
    "publish_err_not_imported": "Import this agent to Flowise first — there is nothing to publish yet.",
    "publish_err_failed": "Could not change the published state: %s",
    "publish_err_no_domain": "DOMAIN is not set in .env, so the public link cannot be built.",
    "dash_col_public": "Public",
    "dash_public_yes": "Published",
    "dash_public_no": "—",

    "slot_err_name_required": "Please give this agent a name.",
    "slot_err_name_taken": 'The name "%s" is already used by another agent — each agent needs a unique name.',
    "slot_err_not_connected": "Flowise isn't connected yet — set it up first.",
    "slot_err_import_failed": "Flowise import failed: %s",
    "slot_err_template": "Could not load agent template: %s",
    "slot_imported_ok": "Imported into Flowise.",
    "slot_err_missing_content": "Missing content for: %s — fill in the form and save first.",
    "slot_err_invalid": "Invalid slot",
    "slot_err_unknown_field": "Unknown field.",
    "slot_err_request_failed": "Something went wrong.",

    # --- document upload -------------------------------------------------
    "upload_title": "Upload Documents",
    "upload_heading": "Upload Course Documents",
    "upload_no_agents": 'No agents are configured yet. A document has to belong to a specific agent, so <a href="%s">set one up first</a> — then come back here.',
    "upload_intro": "Upload a document and it becomes searchable by the agent you assign it to. Everything after that runs automatically in the background: text and table extraction (including OCR for scanned pages), AI descriptions of any figures and diagrams so they're searchable too, then chunking and indexing.",
    "upload_timing": "Processing is <strong>not</strong> instant — a short text document takes about a minute, a long scanned one with many figures can take considerably longer. You don't need to keep this page open; you'll get an email when the document is ready.",
    "upload_slot_label": "Which agent is this document for?",
    "upload_slot_help": "Only this agent will retrieve it. Course-wide agents (Universal Assistant, Knowledge Test) search everything regardless of this setting.",
    "upload_slot_placeholder": "— choose an agent —",
    "upload_slot_option": "Slot %s — %s",
    "upload_unnamed": "unnamed",
    "upload_file_label": "Document",
    "upload_file_help": "PDF, Word, PowerPoint, Excel, HTML, Markdown, or an image (PNG/JPEG/TIFF).",
    "upload_doc_title_label": "Title",
    "upload_doc_title_help": 'How this source is cited when an agent quotes it. Leave empty to use the filename. Example: "Mayer (2009): Multimedia Learning, Chapter 3"',
    "upload_authors_label": "Authors",
    "upload_authors_help": 'Semicolon-separated, surname first. Example: "Mayer, Richard E.; Moreno, Roxana"',
    "upload_year_label": "Year",
    "upload_year_help": "Publication year. Example: 2009",
    "upload_topic_label": "Keywords",
    "upload_topic_help": "A few comma-separated terms describing what this document covers. Example: Cognitive Load, Multimedia Learning, Working Memory",
    "upload_topic_suggest": "Suggest keywords",
    "upload_topic_suggesting": "Thinking…",
    "upload_topic_err": "Could not suggest keywords: %s",
    "upload_language_label": "Language",
    "upload_language_de": "German",
    "upload_language_en": "English",
    "upload_email_label": "Notification email",
    "upload_email_help": 'Where to send the "done" message. Leave empty to use the system\'s admin address.',
    # --- citation lookup (DOI / ISBN / scan) ----------------------------
    "lookup_heading": "Fill in the details automatically",
    "lookup_intro": "For published sources you can skip the typing: scan the uploaded PDF for a DOI or ISBN, or paste one yourself. Nothing is filled in until you confirm what was found.",
    "lookup_scan_btn": "Read from the document",
    "lookup_scanning": "Reading…",
    "lookup_id_label": "DOI or ISBN",
    "lookup_id_help": "Example: 10.3389/fpsyg.2019.02364 or 978-1-107-03520-1. A doi.org link works too.",
    "lookup_btn": "Look up",
    "lookup_looking": "Looking up…",
    "lookup_found": "Found via %s",
    # Shown when the server answers with something that isn't JSON at all
    # — a proxy error page, say. The raw status and body beat a bare
    # "SyntaxError" from the browser, which names the symptom, not the cause.
    "err_http": "The server answered with an error (%s).",
    "lookup_apply": "Use these details",
    "lookup_dismiss": "Discard",
    "lookup_err_no_identifier": "Enter a DOI or ISBN first.",
    "lookup_err_nothing_in_pdf": "No DOI or ISBN found in this document's first pages. Some sources don't print one — paste it manually or fill the fields in yourself.",
    "lookup_err_no_file": "Choose a PDF above first.",
    "lookup_err_not_pdf": "Only PDF files can be scanned for a DOI or ISBN.",

    "upload_ocr_label": "This is a scanned document",
    "upload_ocr_help": "Forces optical character recognition on every page. Only tick this if the PDF is a scan or photo of pages — it's slower, and unnecessary for documents that already contain real text.",
    "upload_submit": "Upload and process",
    "upload_err_no_slot": "Please choose which agent this document belongs to.",
    "upload_err_no_file": "Please choose a file to upload.",
    "upload_err_bad_type": "'%s' isn't a supported format. Allowed: %s",
    "upload_err_bad_year": "Year must be a number (or left empty).",
    "upload_err_too_large": "That file is too large — the limit is %s MB.",
    "upload_err_failed": "Upload failed: %s",
    "upload_ok": "'%s' was handed to the ingest pipeline for %s. Processing runs in the background — a large scanned document with many figures can take a while. You'll get an email when it's searchable.",

    # --- knowledge graph -------------------------------------------------
    "graph_title": "Knowledge Graph",
    "graph_heading": "Knowledge Graph",
    "graph_intro": "Guided, manual process for now — no LLM call happens from this GUI. A fully automated builder (the GUI proposes concepts and prerequisite edges itself, with a review step before writing) is planned for a later batch.",
    "graph_h_model": "1. The data model",
    "graph_model_topic": "a high-level topic in the course (chapter / unit)",
    "graph_model_concept": "a specific learning concept (theory, method, idea)",
    "graph_model_props": "Concept properties: <code>name</code> (required, unique), <code>chapter</code> (optional), <code>section_id</code> (optional), <code>description</code> (optional).",
    "graph_h_prompt": "2. Prompt template",
    "graph_prompt_intro": "Copy this into an AI assistant of your choice, together with your course documents (or a summary of their chapter/section structure):",
    "graph_h_run": "3. Review, then run it",
    "graph_run_intro": "Read through what the AI produced before running it — this step exists so a bad suggestion doesn't quietly land in the graph the tutoring agents rely on for scaffolding. Paste the (reviewed) Cypher below.",
    "graph_cypher_label": "Cypher statements",
    "graph_submit": "Run against Neo4j",
    "graph_ok": "Executed %s statement(s) successfully.",
}


MSG_DE: dict[str, str] = {
    # --- chrome ---------------------------------------------------------
    "app_name": "SMART RAG",
    "app_subtitle": "Inhaltsverwaltung",
    "nav_agents": "Agenten",
    "nav_documents": "Dokumente",
    "nav_graph": "Wissensgraph",
    "nav_flowise": "Flowise-Verbindung",
    "nav_logout": "Abmelden",
    "footer_credit": 'SMART RAG ist eine Entwicklung des <a href="https://www.lmu.de/digillab/de/" target="_blank" rel="noopener">DigiLLab der LMU München</a> | <a href="https://www.psy.lmu.de/ffp/" target="_blank" rel="noopener">Lehrstuhl Empirische Pädagogik und Pädagogische Psychologie</a>, <a href="https://www.psy.lmu.de/ffp/persons/lehrstuhlinhaber/fischer-frank/index.html" target="_blank" rel="noopener">Prof. Frank Fischer</a>.',
    "footer_contact": 'Entwickler: <a href="mailto:benjamin.goetzinger@psy.lmu.de">Benjamin Götzinger</a>',
    "footer_repo": 'GitHub: <a href="https://github.com/digillab-lmu/smart-rag" target="_blank" rel="noopener">github.com/digillab-lmu/smart-rag</a>',
    "lang_switch_label": "Sprache",

    # --- first-run setup ------------------------------------------------
    "setup_title": "Ersteinrichtung",
    "setup_heading": "Willkommen — Administrationskonto anlegen",
    "setup_intro": "Dieses Konto ist unabhängig von deinem Flowise-Login — es schützt ausschließlich diese Oberfläche zur Inhaltspflege.",
    "setup_username": "Benutzername",
    "setup_password": "Passwort (mind. 12 Zeichen)",
    "setup_confirm": "Passwort bestätigen",
    "setup_submit": "Konto anlegen",
    "setup_err_required": "Benutzername und Passwort sind erforderlich.",
    "setup_err_mismatch": "Die Passwörter stimmen nicht überein.",
    "setup_err_too_short": "Das Passwort muss mindestens 12 Zeichen lang sein.",

    # --- login ----------------------------------------------------------
    "login_title": "Anmelden",
    "login_heading": "Inhaltsverwaltung",
    "login_username": "Benutzername",
    "login_password": "Passwort",
    "login_submit": "Anmelden",
    "login_err_invalid": "Benutzername oder Passwort ist falsch.",

    # --- Flowise connection ---------------------------------------------
    "flowise_title": "Flowise-Verbindung",
    "flowise_heading": "Mit Flowise verbinden",
    "flowise_intro": "Flowise bietet keine Möglichkeit, einen API-Schlüssel automatisch bereitzustellen — ein einmaliger manueller Schritt lässt sich nicht vermeiden:",
    "flowise_step1": "Öffne deine Flowise-Instanz und lege dort dein Administrationskonto an, falls noch nicht geschehen.",
    "flowise_step2_pre": "Gehe zu",
    "flowise_step2_path": "Settings → API Keys → Create Key",
    "flowise_step3": "Setze unter <strong>Permissions</strong> in den Kategorien <strong>Chatflows</strong>, <strong>Agentflows</strong>, <strong>Credentials</strong> und <strong>Variables</strong> jeweils alle Häkchen — mehr ruft diese Oberfläche nicht auf. Die übrigen Kategorien (Tools, Document Stores, Assistants, …) bleiben leer, sie werden hier nicht verwendet.",
    "flowise_step4": "Füge den Schlüssel unten ein.",
    "flowise_already_set": "Es ist bereits ein Flowise-API-Schlüssel gespeichert. Zum Ersetzen unten einen neuen einfügen.",
    "flowise_key_label": "Flowise-API-Schlüssel",
    "flowise_submit": "Speichern und prüfen",
    "flowise_err_required": "Der API-Schlüssel ist erforderlich.",
    "flowise_err_connect": "Verbindung zu Flowise mit diesem Schlüssel fehlgeschlagen: %s",
    "flowise_saved": "Verbunden — Flowise-API-Schlüssel gespeichert.",

    # --- dashboard ------------------------------------------------------
    "dash_title": "Agenten",
    "dash_heading": "Agenten (bis zu 10)",
    "dash_not_connected": 'Flowise ist noch nicht verbunden. <a href="%s">Zuerst einrichten</a> — Inhalte kannst du auch jetzt schon eintragen, für den Import wird die Verbindung aber benötigt.',
    "dash_connection_broken": 'Es ist ein Flowise-API-Schlüssel gespeichert, aber Flowise akzeptiert ihn gerade nicht — der Schlüssel wurde möglicherweise gelöscht oder in seinen Rechten beschnitten, oder Flowise läuft nicht. Import und Veröffentlichen schlagen fehl, solange das so ist. <a href="%s">Systemstatus</a> sagt, welcher der beiden Fälle vorliegt.',
    "dash_col_slot": "Platz",
    "dash_col_name": "Name",
    "dash_col_archetype": "Agententyp",
    "dash_col_status": "Status",
    "dash_status_imported": "Importiert",
    "dash_status_saved": "Inhalte gespeichert, nicht importiert",
    "dash_status_empty": "Leer",
    "dash_edit": "Bearbeiten",

    # --- slot / agent editing -------------------------------------------
    "slot_title": "Platz %s",
    "slot_heading": "Platz %s",
    "slot_back": "← zurück zur Übersicht",
    "slot_choose_archetype": "Agententyp wählen",
    "slot_rag_note": 'Alle Typen außer dem Ausweich-Assistenten nutzen RAG (Recherche in deinen Kursdokumenten). Sobald dieser Agent eingerichtet ist, kannst du seine Dokumente unter <a href="%s">Dokumente</a> hochladen.',
    "slot_continue": "Weiter",
    "slot_archetype_label": "Agententyp",
    "slot_imported_as": "importiert (Chatflow-ID: %s)",
    "slot_name_label": "Name des Agenten",
    "slot_name_help": 'Ein eindeutiger Name, an dem du diesen Agenten von deinen anderen unterscheiden kannst — er wird zugleich der Name des Chatflows in Flowise. Beispiel: "Tutor Kapitel 4" oder "Sarah, Statistik-Expertin". Muss sich von allen anderen Agentennamen unterscheiden.',
    "slot_examples_note": "Jedes Feld unten zeigt ein vollständig ausformuliertes Beispiel als grauen Platzhaltertext (er verschwindet, sobald du zu tippen beginnst) — schreibe deine eigenen Kursinhalte nach demselben Muster.",
    "slot_optimize": "Mit KI verbessern",
    "slot_optimizing": "Wird verbessert…",
    "slot_suggestion": "KI-Vorschlag",
    "slot_use_this": "Übernehmen",
    "slot_discard": "Verwerfen",
    "slot_close": "Schließen",
    "slot_save": "Speichern",
    "slot_import": "Speichern und nach Flowise importieren",
    # --- system prompt editing -------------------------------------------
    "slot_prompt_heading": "Systemprompt",
    "slot_prompt_toggle_show": "Anzeigen und bearbeiten",
    "slot_prompt_toggle_hide": "Ausblenden",
    "slot_prompt_intro": "Das ist die Anweisung, nach der dieser Agent arbeitet — seine Rolle, seine Regeln, sein Ton und seine Aufgabe. Für jeden Agententyp ist ein ausgearbeiteter Standard hinterlegt; ändere ihn nur, wenn du das pädagogische Verhalten des Agenten anpassen willst.",
    "slot_prompt_placeholders_note": "Text in doppelten geschweiften Klammern wie {{TOPIC_NAME}} wird aus den Feldern oben eingesetzt. Fügst du einen hinzu, erscheint nach dem Speichern ein passendes Feld; entfernst du einen, verschwindet sein Feld.",
    "slot_prompt_customised": "Bearbeitet — folgt nicht mehr dem Standard dieses Agententyps.",
    "slot_prompt_default": "Unveränderter Standard dieses Agententyps.",
    "slot_prompt_reset": "Auf Standard zurücksetzen",
    "slot_prompt_reset_ok": "Systemprompt auf den Standard dieses Agententyps zurückgesetzt.",
    # --- onboarding guide -------------------------------------------------
    "guide_title": "Systemstatus",
    "guide_heading": "Systemstatus",
    "guide_intro": "Jede Zeile unten wird jetzt gerade live geprüft, indem der jeweilige Dienst selbst gefragt wird — nicht, indem wir uns früher etwas notiert haben. Nach einer Änderung die Seite neu laden, dann steht hier der neue Stand.",
    "guide_ready": "Alles eingerichtet. Deine Agenten können antworten und Dokumente aufnehmen.",
    "guide_blocked": "Fang hier an: %s",
    "guide_recheck": "Erneut prüfen",
    "guide_state_ok": "In Ordnung",
    "guide_state_warn": "Braucht Aufmerksamkeit",
    "guide_state_fail": "Noch nicht bereit",
    "guide_state_unknown": "Unklar",
    "guide_admin_needed": "Dieser Schritt gehört zur Server-Administration. Er braucht Kommandozeilen-Zugriff auf die Maschine — den diese Oberfläche bewusst nicht hat und den du möglicherweise auch nicht hast. Wenn das jemand anderes macht, schick ihr oder ihm einfach die Nachricht unten.",
    "guide_admin_forward": "Nachricht zum Weiterleiten:",
    "guide_admin_message": "Hallo, die Ingest-Workflows von SMART RAG sind auf unserem Server nicht aktiv, deshalb schlägt das Hochladen von Kursdokumenten gerade fehl. Könntest du das im SMART-RAG-Installationsverzeichnis ausführen?\n\n%s\n\nDer Befehl kann gefahrlos wiederholt werden und prüft anschließend selbst, ob der Webhook läuft. Hintergrund: docs/operations-guide.md, Abschnitt \"n8n (automation)\". Danke!",
    "guide_command_intro": "Der Befehl allein:",
    "guide_command_why": "Warum diese Oberfläche das nicht einfach selbst macht: Sie hat bewusst keinen Zugriff auf Docker und auf das Dateisystem des Servers. Eine Weboberfläche, die Container starten könnte, wäre ein deutlich größeres Angriffsziel als eine, die nur per HTTP mit anderen Diensten spricht.",
    "guide_copy_message": "Nachricht kopieren",

    "guide_llm_keys_title": "API-Schlüssel für Sprachmodell und Embeddings",
    "guide_llm_keys_help": "Ohne diese kann kein Agent antworten und kein Dokument indexiert werden. Beide werden in der .env-Datei auf dem Server gesetzt.",
    "guide_llm_keys_fail": "Noch nicht gesetzt: %s",
    "guide_llm_keys_ok": "Anbieter: %s",

    "guide_flowise_title": "Verbindung zu Flowise",
    "guide_flowise_help": "Flowise führt die Agenten aus. Diese Oberfläche braucht einen eigenen API-Schlüssel, um sie dort anzulegen und zu aktualisieren.",
    "guide_flowise_link": "Verbindung einrichten",

    "guide_agents_title": "Agenten",
    "guide_agents_help": "Ein Agent ist eine Rolle, mit der deine Studierenden sprechen. Hier trägst du seine Kursinhalte ein und importierst ihn anschließend nach Flowise.",
    "guide_agents_link": "Zu den Agenten",
    "guide_agents_fail": "Es ist noch kein Agent ausgefüllt.",
    "guide_agents_warn_unimported": "%s Agent(en) ausgefüllt, aber noch keiner nach Flowise importiert.",
    "guide_agents_ok": "%s von %s Agent(en) importiert.",
    "guide_agents_warn_stale": "%s importierte(r) Agent(en) existieren in Flowise nicht mehr — bitte erneut importieren.",

    "guide_n8n_title": "Dokumentverarbeitung (n8n)",
    "guide_n8n_help": "n8n nimmt jedes hochgeladene Dokument entgegen und schickt es durch Umwandlung, Bereinigung, Zerlegung und Indexierung. Seine Workflows müssen einmal importiert und eingeschaltet werden.",
    "guide_n8n_ok": "Der Ingest-Workflow ist aktiv und nimmt Dokumente an.",
    "guide_n8n_fail": "Der Ingest-Workflow ist nicht registriert — er ist entweder nicht importiert oder nicht aktiv.",

    "guide_ingest_services_title": "Umwandlungs- und Suchdienste",
    "guide_ingest_services_help": "Docling liest Dokumente, markdowncleaner räumt das Ergebnis auf, Weaviate speichert es für die Suche. Alle drei werden von Docker Compose gestartet.",
    "guide_ingest_services_fail": "Nicht erreichbar: %s",
    "guide_ingest_services_ok": "Docling, markdowncleaner und Weaviate sind alle erreichbar.",

    "guide_next_title": "Und jetzt?",
    "guide_next_upload": "Erstes Dokument hochladen",
    "guide_next_graph": "Wissensgraph einrichten (optional)",
    # --- documents list / deletion ---------------------------------------
    "nav_doclist": "Indexiert",
    "docs_title": "Indexierte Dokumente",
    "docs_heading": "Was im Index liegt",
    "docs_intro": "Alles, was für diesen Kurs eingelesen wurde, nach Dokument gruppiert. Wer eines entfernt, nimmt alle seine Chunks aus dem Index — die Agenten finden es sofort nicht mehr.",
    "docs_why_delete": "Sinnvoll, wenn ein Dokument versehentlich hochgeladen wurde, wenn sonst eine überarbeitete Fassung neben ihrer Vorgängerin läge, oder wenn ein Agenten-Slot für ein anderes Thema weiterverwendet wird — die alten Dokumente behalten die alte Slot-Nummer und würden von der nächsten Belegung weiterhin gefunden.",
    "docs_empty": "Für diesen Kurs wurde noch nichts eingelesen.",
    "docs_col_title": "Dokument",
    "docs_col_agent": "Agent",
    "docs_col_meta": "Angaben",
    "docs_col_chunks": "Chunks",
    "docs_col_action": "",
    "docs_delete": "Entfernen",
    "docs_delete_confirm": "\"%s\" aus dem Index entfernen? Die %s Chunk(s) werden gelöscht und die Agenten finden das Dokument nicht mehr. Die Originaldatei bleibt im Objektspeicher; nur der Indexeintrag verschwindet.",
    "docs_deleted": "%s Chunk(s) von \"%s\" aus dem Index entfernt.",
    "docs_agent_unknown": "Agent %s (Slot nicht eingerichtet)",
    "docs_agent_none": "kein Agent",
    "docs_truncated": "Es wird nur ein Teil dieses Kurses aufgelistet — er hat mehr Chunks, als auf einmal gelesen werden. Entferne etwas oder lass das Limit erhöhen.",
    "docs_total": "Insgesamt %s Chunk(s).",
    "docs_err_not_configured": "In der .env fehlt COURSE_ID oder WEAVIATE_COLLECTION_NAME — es gibt nichts aufzulisten.",
    "docs_err_no_title": "Es wurde kein Dokument benannt.",
    "docs_err_list_failed": "Der Index konnte nicht gelesen werden: %s",
    "docs_err_delete_failed": "Das Dokument konnte nicht entfernt werden: %s",
    # --- publishing -------------------------------------------------------
    "publish_heading": "Diesen Agenten veröffentlichen",
    "publish_intro": "Beim Veröffentlichen bekommt dieser Agent eine Webadresse, die alle öffnen können — ohne Flowise-Login, ohne Kurszugehörigkeit, ohne Passwort. Praktisch für eine öffentliche Demo oder einen Link im Seminarplan; nichts, was man nebenbei tun sollte.",
    "publish_warning": "Vor dem Veröffentlichen: Jede Nachricht über den öffentlichen Link wird auf deinem LLM-Budget beantwortet, und wer den Link hat, kann beliebig viele senden. Außerdem antwortet der Agent, ohne zu wissen, wer fragt — alles Personalisierte und alles, was von einer Kursrolle abhängt, funktioniert dort nicht.",
    "publish_state_private": "Nicht veröffentlicht — erreichbar nur über Flowise oder die Kurseinbindung.",
    "publish_state_public": "Veröffentlicht — alle mit dem Link können diesen Agenten nutzen.",
    "publish_state_unknown": "Der Veröffentlichungsstatus konnte nicht von Flowise gelesen werden.",
    "publish_state_gone": "Diesen Agenten gibt es in Flowise nicht mehr — importiere ihn erneut, bevor du ihn veröffentlichst.",
    "publish_button": "Veröffentlichen",
    "publish_unpublish_button": "Veröffentlichung zurückziehen",
    "publish_confirm": "Diesen Agenten veröffentlichen? Alle mit dem Link können ihn dann ohne Anmeldung nutzen, und jede Nachricht wird auf deinem LLM-Budget beantwortet.",
    "publish_url_label": "Öffentlicher Link",
    "publish_embed_label": "In eine Webseite einbetten",
    "publish_embed_help": "Füge das in den HTML-Code einer Seite ein, um den Agenten dort in einem Kasten anzuzeigen.",
    "publish_copy": "Kopieren",
    "publish_copied": "Kopiert",
    "publish_ok": "Agent veröffentlicht. Der öffentliche Link steht unten.",
    "unpublish_ok": "Veröffentlichung zurückgezogen. Der öffentliche Link funktioniert nicht mehr.",
    "publish_err_not_imported": "Importiere diesen Agenten zuerst nach Flowise — es gibt noch nichts zu veröffentlichen.",
    "publish_err_failed": "Der Veröffentlichungsstatus konnte nicht geändert werden: %s",
    "publish_err_no_domain": "In der .env ist DOMAIN nicht gesetzt, deshalb lässt sich der öffentliche Link nicht bilden.",
    "dash_col_public": "Öffentlich",
    "dash_public_yes": "Veröffentlicht",
    "dash_public_no": "—",

    "slot_err_name_required": "Bitte gib diesem Agenten einen Namen.",
    "slot_err_name_taken": 'Der Name "%s" wird bereits von einem anderen Agenten verwendet — jeder Agent braucht einen eindeutigen Namen.',
    "slot_err_not_connected": "Flowise ist noch nicht verbunden — bitte zuerst einrichten.",
    "slot_err_import_failed": "Flowise-Import fehlgeschlagen: %s",
    "slot_err_template": "Agentenvorlage konnte nicht geladen werden: %s",
    "slot_imported_ok": "Nach Flowise importiert.",
    "slot_err_missing_content": "Es fehlen noch Inhalte für: %s — bitte im Formular ergänzen und speichern.",
    "slot_err_invalid": "Ungültiger Platz",
    "slot_err_unknown_field": "Unbekanntes Feld.",
    "slot_err_request_failed": "Da ist etwas schiefgegangen.",

    # --- document upload -------------------------------------------------
    "upload_title": "Dokumente hochladen",
    "upload_heading": "Kursdokumente hochladen",
    "upload_no_agents": 'Es ist noch kein Agent eingerichtet. Ein Dokument gehört immer zu einem bestimmten Agenten — <a href="%s">richte also zuerst einen ein</a> und komm dann hierher zurück.',
    "upload_intro": "Lade ein Dokument hoch, und es wird für den Agenten durchsuchbar, dem du es zuordnest. Alles Weitere läuft automatisch im Hintergrund: Text- und Tabellenerkennung (bei Scans einschließlich Texterkennung), KI-Beschreibungen von Abbildungen und Diagrammen, damit auch diese auffindbar werden, danach Zerlegung und Indexierung.",
    "upload_timing": "Die Verarbeitung ist <strong>nicht</strong> sofort fertig — ein kurzes Textdokument dauert etwa eine Minute, ein langes eingescanntes mit vielen Abbildungen deutlich länger. Du musst diese Seite nicht offen lassen; du bekommst eine E-Mail, sobald das Dokument bereitsteht.",
    "upload_slot_label": "Für welchen Agenten ist dieses Dokument?",
    "upload_slot_help": "Nur dieser Agent greift darauf zu. Kursweite Agenten (Universal-Assistent, Wissenstest) durchsuchen unabhängig von dieser Einstellung alle Dokumente.",
    "upload_slot_placeholder": "— Agent auswählen —",
    "upload_slot_option": "Platz %s — %s",
    "upload_unnamed": "ohne Namen",
    "upload_file_label": "Dokument",
    "upload_file_help": "PDF, Word, PowerPoint, Excel, HTML, Markdown oder ein Bild (PNG/JPEG/TIFF).",
    "upload_doc_title_label": "Titel",
    "upload_doc_title_help": 'So wird diese Quelle zitiert, wenn ein Agent daraus antwortet. Leer lassen, um den Dateinamen zu verwenden. Beispiel: "Mayer (2009): Multimedia Learning, Kapitel 3"',
    "upload_authors_label": "Autorinnen und Autoren",
    "upload_authors_help": 'Mit Semikolon getrennt, Nachname zuerst. Beispiel: "Mayer, Richard E.; Moreno, Roxana"',
    "upload_year_label": "Jahr",
    "upload_year_help": "Erscheinungsjahr. Beispiel: 2009",
    "upload_topic_label": "Schlagwörter",
    "upload_topic_help": "Einige durch Komma getrennte Begriffe, die den Inhalt beschreiben. Beispiel: Cognitive Load, Multimediales Lernen, Arbeitsgedächtnis",
    "upload_topic_suggest": "Schlagwörter vorschlagen",
    "upload_topic_suggesting": "Wird überlegt…",
    "upload_topic_err": "Schlagwörter konnten nicht vorgeschlagen werden: %s",
    "upload_language_label": "Sprache",
    "upload_language_de": "Deutsch",
    "upload_language_en": "Englisch",
    "upload_email_label": "E-Mail für Benachrichtigung",
    "upload_email_help": "Wohin die Fertigmeldung geschickt wird. Leer lassen, um die Administrationsadresse des Systems zu verwenden.",
    # --- citation lookup (DOI / ISBN / scan) ----------------------------
    "lookup_heading": "Angaben automatisch übernehmen",
    "lookup_intro": "Bei veröffentlichten Quellen sparst du dir das Abtippen: Das hochgeladene PDF nach DOI oder ISBN durchsuchen lassen oder selbst eine eingeben. Übernommen wird erst, wenn du den Fund bestätigst.",
    "lookup_scan_btn": "Aus dem Dokument auslesen",
    "lookup_scanning": "Wird ausgelesen…",
    "lookup_id_label": "DOI oder ISBN",
    "lookup_id_help": "Beispiel: 10.3389/fpsyg.2019.02364 oder 978-1-107-03520-1. Ein doi.org-Link funktioniert ebenfalls.",
    "lookup_btn": "Nachschlagen",
    "lookup_looking": "Wird nachgeschlagen…",
    "lookup_found": "Gefunden über %s",
    "err_http": "Der Server hat mit einem Fehler geantwortet (%s).",
    "lookup_apply": "Angaben übernehmen",
    "lookup_dismiss": "Verwerfen",
    "lookup_err_no_identifier": "Bitte zuerst eine DOI oder ISBN eingeben.",
    "lookup_err_nothing_in_pdf": "Auf den ersten Seiten dieses Dokuments wurde keine DOI und keine ISBN gefunden. Nicht jede Quelle enthält eine — bitte selbst eingeben oder die Felder von Hand ausfüllen.",
    "lookup_err_no_file": "Bitte zuerst oben ein PDF auswählen.",
    "lookup_err_not_pdf": "Nur PDF-Dateien können nach DOI oder ISBN durchsucht werden.",

    "upload_ocr_label": "Dies ist ein eingescanntes Dokument",
    "upload_ocr_help": "Erzwingt die Texterkennung auf jeder Seite. Nur ankreuzen, wenn das PDF ein Scan oder Foto von Seiten ist — es dauert länger und ist bei Dokumenten mit echtem Text unnötig.",
    "upload_submit": "Hochladen und verarbeiten",
    "upload_err_no_slot": "Bitte wähle aus, zu welchem Agenten dieses Dokument gehört.",
    "upload_err_no_file": "Bitte wähle eine Datei zum Hochladen aus.",
    "upload_err_bad_type": "'%s' ist kein unterstütztes Format. Erlaubt sind: %s",
    "upload_err_bad_year": "Das Jahr muss eine Zahl sein (oder leer bleiben).",
    "upload_err_too_large": "Diese Datei ist zu groß — das Limit liegt bei %s MB.",
    "upload_err_failed": "Hochladen fehlgeschlagen: %s",
    "upload_ok": "'%s' wurde an die Verarbeitung für %s übergeben. Sie läuft im Hintergrund — ein großes eingescanntes Dokument mit vielen Abbildungen kann eine Weile dauern. Du bekommst eine E-Mail, sobald es durchsuchbar ist.",

    # --- knowledge graph -------------------------------------------------
    "graph_title": "Wissensgraph",
    "graph_heading": "Wissensgraph",
    "graph_intro": "Vorerst ein angeleiteter, manueller Ablauf — diese Oberfläche ruft selbst keine KI auf. Ein vollautomatischer Aufbau (die Oberfläche schlägt Konzepte und Voraussetzungsbeziehungen selbst vor, mit Prüfschritt vor dem Schreiben) ist für einen späteren Ausbau vorgesehen.",
    "graph_h_model": "1. Das Datenmodell",
    "graph_model_topic": "ein übergeordnetes Thema des Kurses (Kapitel / Einheit)",
    "graph_model_concept": "ein einzelnes Lernkonzept (Theorie, Methode, Idee)",
    "graph_model_props": "Eigenschaften von Concept: <code>name</code> (erforderlich, eindeutig), <code>chapter</code> (optional), <code>section_id</code> (optional), <code>description</code> (optional).",
    "graph_h_prompt": "2. Prompt-Vorlage",
    "graph_prompt_intro": "Kopiere den folgenden Text in eine KI deiner Wahl, zusammen mit deinen Kursdokumenten (oder einer Übersicht ihrer Kapitel- und Abschnittsstruktur):",
    "graph_h_run": "3. Prüfen, dann ausführen",
    "graph_run_intro": "Lies das Ergebnis der KI durch, bevor du es ausführst — dieser Schritt sorgt dafür, dass kein fehlerhafter Vorschlag unbemerkt in dem Graphen landet, auf den sich die Tutor-Agenten beim Lernaufbau stützen. Füge das (geprüfte) Cypher unten ein.",
    "graph_cypher_label": "Cypher-Anweisungen",
    "graph_submit": "In Neo4j ausführen",
    "graph_ok": "%s Anweisung(en) erfolgreich ausgeführt.",
}


_CATALOG: dict[str, dict[str, str]] = {"en": MSG_EN, "de": MSG_DE}


def normalize_language(value: str | None) -> str:
    """Maps anything user- or browser-supplied onto a language we have."""
    if not value:
        return DEFAULT_LANGUAGE
    candidate = value.strip().lower()[:2]
    return candidate if candidate in LANGUAGES else DEFAULT_LANGUAGE


def language_from_accept_header(header: str | None) -> str:
    """
    Picks the best supported language out of an Accept-Language header,
    honouring its q-weights ("de;q=0.9, en;q=0.8" → de).

    Hand-parsed rather than using Werkzeug's accept_languages so this
    function stays testable without a request context — and so the
    fallback is our DEFAULT_LANGUAGE rather than whatever the header's
    first unsupported entry happens to be.
    """
    if not header:
        return DEFAULT_LANGUAGE

    best: tuple[float, str] | None = None
    for part in header.split(","):
        piece = part.strip()
        if not piece:
            continue
        tag, _, params = piece.partition(";")
        quality = 1.0
        if params.strip().startswith("q="):
            try:
                quality = float(params.strip()[2:])
            except ValueError:
                quality = 0.0
        code = tag.strip().lower()[:2]
        if code not in LANGUAGES:
            continue
        if best is None or quality > best[0]:
            best = (quality, code)

    return best[1] if best else DEFAULT_LANGUAGE


def t(key: str, *args, lang: str = DEFAULT_LANGUAGE) -> str:
    """
    Looks up `key`, falling back to English and then to the key itself so a
    missing translation degrades to something readable rather than raising
    mid-render. %s substitution mirrors messages.sh's printf-style usage.
    """
    catalog = _CATALOG.get(lang, MSG_EN)
    text = catalog.get(key) or MSG_EN.get(key) or key
    if args:
        try:
            return text % args
        except TypeError:
            # Wrong number of placeholders — show the unsubstituted string
            # rather than blowing up the whole page.
            return text
    return text


def check_translations() -> list[str]:
    """
    Python counterpart of messages.sh's check_translations — reports keys
    present in English but missing in another language (which would
    silently fall back and look like an untranslated page). Used by the
    test suite; returns an empty list when everything is covered.
    """
    problems: list[str] = []
    for lang in LANGUAGES:
        if lang == "en":
            continue
        catalog = _CATALOG[lang]
        for key in MSG_EN:
            if key not in catalog:
                problems.append(f"{lang}: missing key {key!r}")
        for key in catalog:
            if key not in MSG_EN:
                problems.append(f"{lang}: key {key!r} has no English original")
    return problems
