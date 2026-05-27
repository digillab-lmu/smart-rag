# ═════════════════════════════════════════════════════════════════════════════
# messages.sh — i18n strings (English + German)
# ═════════════════════════════════════════════════════════════════════════════
#
# Add new strings to BOTH arrays. If a German key is missing, t() falls back
# to English at runtime (and check_translations() warns about it).
# Use printf-style format specifiers (%s, %d) for substitutions.
# ═════════════════════════════════════════════════════════════════════════════

# Selected language — set by select_language() or --lang flag
LANG_CHOICE="${LANG_CHOICE:-}"


# ─── English ────────────────────────────────────────────────────────────────
declare -A MSG_EN=(
    # --- common -------------------------------------------------------------
    [value_required]="Value required."
    [invalid_yn]="Please answer yes or no."
    [invalid_choice]="Invalid choice — pick a number from the list."
    [enter_choice]="Choice"

    # --- language selection ------------------------------------------------
    [lang_prompt]="Choose language / Sprache wählen"
    [lang_en]="English"
    [lang_de]="Deutsch"

    # --- banner / intro -----------------------------------------------------
    [intro_welcome]="Welcome — this wizard will set up SMART RAG on this server."
    [intro_what_happens]="What will happen:"
    [intro_step1]="  1. Pre-flight checks (Ubuntu, Docker, disk space, DNS)"
    [intro_step2]="  2. Interactive configuration (course details, LLM provider, etc.)"
    [intro_step3]="  3. Generate cryptographically secure secrets"
    [intro_step4]="  4. Write .env file and substitute templates"
    [intro_step5]="  5. (next run) Start services, deploy schemas, set up APIs"
    [intro_continue]="Continue?"

    # --- preflight ----------------------------------------------------------
    [phase_preflight]="Phase 1 · Pre-flight Checks"
    [pf_ubuntu_ok]="Ubuntu %s detected"
    [pf_ubuntu_wrong]="Ubuntu 24.04 LTS required (found: %s)"
    [pf_ubuntu_not_linux]="This script requires Ubuntu Linux. Detected: %s"
    [pf_root_ok]="Running with root privileges"
    [pf_root_needed]="Please run as root: sudo bash %s"
    [pf_internet_ok]="Internet connection available"
    [pf_internet_fail]="No internet connection (cannot reach %s)"
    [pf_docker_ok]="Docker %s found"
    [pf_docker_missing]="Docker is not installed. Install it first from: https://docs.docker.com/engine/install/ubuntu/"
    [pf_compose_ok]="Docker Compose v2 plugin found"
    [pf_compose_missing]="Docker Compose v2 plugin not found. Install via: sudo apt install docker-compose-plugin"
    [pf_disk_ok]="Disk space: %s GB free on %s"
    [pf_disk_low]="Low disk space on %s: only %s GB free (recommended: 20+ GB)"
    [pf_dns_skip]="DNS check skipped — domain not yet entered"
    [pf_dns_ok]="DNS: %s resolves to %s"
    [pf_dns_mismatch]="DNS: %s resolves to %s — but server IP is %s. Certbot will fail."
    [pf_dns_nores]="DNS: %s does not resolve. Set your DNS A-record before running phase 6."

    # --- coexistence checks (extended preflight) ---------------------------
    [pf_section_coexist]="Coexistence checks (other services on this host)"
    [pf_port_check]="Checking host ports for conflicts and auto-resolving…"
    [pf_port_ok]="Port %d is free (%s)"
    [pf_port_conflict]="Port %d (%s) is in use by %s — proposing alternative: %d"
    [pf_port_no_alternative]="No free port found in range %d-%d for %s"
    [pf_port_all_free]="All requested ports are free — no reassignment needed"
    [pf_port_section_resolutions]="Port reassignments required"
    [pf_port_summary_intro]="The following ports were already in use. The wizard picked free alternatives:"
    [pf_port_summary_line]="  %-25s  %5d → %5d   (was: %s)"
    [pf_port_confirm]="Use these alternative ports? (No = cancel and free ports manually)"
    [pf_port_cancelled]="Cancelled — please stop conflicting services and re-run, or manually edit .env."
    [pf_subdom_check]="Checking nginx for subdomain conflicts…"
    [pf_subdom_ok]="No nginx site already claims our subdomains"
    [pf_subdom_conflict]="nginx site %s already serves: %s"
    [pf_subdom_abort]="One or more of our subdomains is already configured in nginx. Resolve the conflict and re-run."
    [pf_cert_check]="Checking Let's Encrypt for existing certificates…"
    [pf_cert_exists]="Existing cert found: %s (will be reused / renewed)"
    [pf_cert_clean]="No conflicting cert name in Let's Encrypt"
    [pf_data_path_check]="Checking BASE_DATA_PATH for existing data…"
    [pf_data_path_empty]="BASE_DATA_PATH %s is empty or doesn't exist (good)"
    [pf_data_path_smartrag]="BASE_DATA_PATH %s contains SMART RAG data from a previous run — reusing"
    [pf_data_path_foreign]="BASE_DATA_PATH %s already contains files NOT from SMART RAG: %s"
    [pf_data_path_confirm]="Use this path anyway? (existing files will NOT be touched)"
    [pf_nginx_running]="nginx is running — coexistence mode active"
    [pf_nginx_not_running]="nginx is installed but not running — will start it before SSL"
    [pf_nginx_config_test]="Existing nginx config has errors — fix them before we touch nginx"

    # --- config wizard ------------------------------------------------------
    [phase_config]="Phase 2 · Configuration Wizard"
    [cfg_intro]="A few questions about your deployment. Defaults shown in [brackets]."
    [cfg_env_exists_prompt]="An existing .env was found. What should we do?"
    [cfg_env_keep]="Keep existing .env (skip wizard, use saved values)"
    [cfg_env_backup_new]="Back up existing .env and create new one (recommended)"
    [cfg_env_overwrite]="Overwrite without backup (destructive)"

    # course & deployment
    [cfg_section_course]="Course & deployment"
    [cfg_course_name]="Course title (free text, e.g. \"Intro to Research Methods\")"
    [cfg_course_id]="Course ID (lowercase, hyphens only, e.g. \"intro-research\")"
    [cfg_course_id_invalid]="Course ID must be lowercase letters, digits, hyphens only."
    [cfg_domain]="Your base domain (subdomains added automatically; e.g. example.com or my-org.edu)"
    [cfg_domain_invalid]="Not a valid fully-qualified domain name."
    [cfg_admin_email]="Admin email (used for Let's Encrypt notifications)"
    [cfg_email_invalid]="Not a valid email address."
    [cfg_base_data_path]="Where should persistent data live"
    [cfg_tz]="Timezone (e.g. Europe/Berlin, America/New_York)"

    # profiles
    [cfg_section_profiles]="Components to enable"
    [cfg_enable_observability]="Enable Langfuse observability (recommended)?"
    [cfg_enable_lti]="Enable LTI 1.3 (for Moodle/ILIAS/Canvas integration)?"
    [cfg_lms_url]="Your LMS public URL (e.g. https://moodle.your-uni.edu)"

    # LLM
    [cfg_section_llm]="Language model provider"
    [cfg_llm_provider]="Choose your LLM provider"
    [cfg_llm_model_strong]="Strong model (complex reasoning)"
    [cfg_llm_model_fast]="Fast model (classification, summaries)"
    [cfg_llm_api_key]="API key for your LLM provider"
    [cfg_llm_base_url]="Base URL for your custom OpenAI-compatible endpoint"

    # Embedding
    [cfg_section_embedding]="Embedding model"
    [cfg_embed_warning_bold]="⚠ Important: cannot be changed after first ingest!"
    [cfg_embed_warning]="The embedding model defines the vector space. Changing it later requires re-ingesting all course materials. Choose carefully."
    [cfg_embed_provider]="Choose your embedding provider"
    [cfg_embed_model]="Embedding model name"
    [cfg_embed_dimensions]="Vector dimensions"
    [cfg_embed_dims_known]="Auto-filled for known model: %s"
    [cfg_embed_api_key]="API key for embedding provider (may be same as LLM key)"
    [cfg_embed_base_url]="Base URL for your custom embeddings endpoint"

    # Reranker
    [cfg_section_reranker]="Reranker (optional)"
    [cfg_reranker_intro]="The reranker improves retrieval quality. Cohere recommended."
    [cfg_reranker_provider]="Choose your reranker"
    [cfg_reranker_model]="Reranker model"
    [cfg_reranker_api_key]="API key for reranker provider"
    [cfg_reranker_base_url]="Base URL for your custom reranker endpoint"

    # confirmation
    [cfg_review_title]="Review your configuration:"
    [cfg_review_confirm]="Save this configuration?"
    [cfg_aborted]="Configuration aborted by user."

    # --- secrets ------------------------------------------------------------
    [phase_secrets]="Phase 3 · Generating Secrets"
    [secrets_intro]="Generating cryptographically secure passwords and keys…"
    [secrets_done]="%d secrets generated"
    [secrets_admin_pw_note]="Admin password for Flowise, n8n and Langfuse:"
    [secrets_creds_file]="All credentials will be saved to: %s"

    # --- templates ----------------------------------------------------------
    [phase_templates]="Phase 4 · Writing Templates"
    [tpl_writing_env]="Writing .env to %s"
    [tpl_writing_nginx]="Substituting nginx template for %s"
    [tpl_writing_weaviate]="Substituting Weaviate schema (collection: %s)"
    [tpl_copying_lti]="Copying LTI config templates"
    [tpl_done]="All templates written"

    # --- summary ------------------------------------------------------------
    [phase_complete]="Bootstrap Phase 1 Complete"
    [summary_files]="Files written:"
    [summary_next]="Next steps:"
    [summary_next_review]="  1. Review the generated .env file (especially passwords)"
    [summary_next_dns]="  2. Set DNS to resolve to this server's IP (a wildcard A-record *.%s works, or individual records for each subdomain — see list below)"
    [summary_next_ssl]="  3. Run: sudo bash scripts/get-ssl-certs.sh"
    [summary_next_start]="  4. Run: sudo bash scripts/bootstrap.sh --continue"
    [summary_creds_warn]="🔐 Keep %s safe — it contains all your credentials."
    [summary_creds_chmod]="Permissions set to 600 (owner read/write only)."

    # --- validation errors --------------------------------------------------
    [val_path_writable]="Path is not writable: %s"
    [val_dimensions_num]="Dimensions must be a positive integer."

    # --- phase 5 — system packages ------------------------------------------
    [phase_packages]="Phase 5 · Installing System Packages"
    [pkg_updating]="Updating apt package index…"
    [pkg_installing]="Installing: %s"
    [pkg_done]="System packages ready"
    [pkg_already]="Already installed: %s"

    # --- phase 6 — SSL ------------------------------------------------------
    [phase_ssl]="Phase 6 · Obtaining SSL Certificates"
    [ssl_subdomain_list]="Subdomains to certify: %s"
    [ssl_resolving_server_ip]="Detecting public IP of this server…"
    [ssl_server_ip]="Server public IP: %s"
    [ssl_checking_dns]="Verifying DNS records before requesting certificate…"
    [ssl_dns_match]="DNS: %s → %s ✓"
    [ssl_dns_fail]="DNS check failed for %s — got %s, expected %s"
    [ssl_dns_block]="Stopping here. Set DNS A-records to %s for all subdomains, then re-run."
    [ssl_writing_acme_config]="Writing temporary ACME challenge config…"
    [ssl_nginx_reload]="Reloading nginx…"
    [ssl_requesting_cert]="Requesting certificate from Let's Encrypt…"
    [ssl_cert_obtained]="Certificate obtained: %s"
    [ssl_installing_full_config]="Installing full nginx config (HTTP + HTTPS)…"
    [ssl_done]="SSL setup complete. All subdomains are now reachable via HTTPS."
    [ssl_existing_cert]="Certificate for %s already exists — skipping issuance."
    [ssl_dry_run]="Dry-run: would request certificates for %s"

    # --- phase 7 — services -------------------------------------------------
    [phase_services]="Phase 7 · Starting Docker Services"
    [svc_pulling]="Pulling Docker images (this can take a few minutes on first run)…"
    [svc_starting]="Starting services with profile: %s"
    [svc_waiting]="Waiting for services to become healthy…"
    [svc_healthy]="%s is healthy"
    [svc_unhealthy]="%s never became healthy after %d seconds"
    [svc_all_healthy]="All required services healthy"
    [svc_status]="Service status:"
    [svc_done]="Services are running."

    # --- bootstrap orchestration --------------------------------------------
    [orch_continue_intro]="Continuing bootstrap (phases 5–7)…"
    [snap_creating]="Creating safety snapshot of current system state…"
    [snap_nginx]="  nginx config (/etc/nginx)"
    [snap_docker]="  Docker container list"
    [snap_ports]="  Listening ports"
    [snap_letsencrypt]="  Let's Encrypt certs (metadata only)"
    [snap_done]="Snapshot saved at: %s"
    [snap_restore_hint]="To restore nginx if needed: sudo tar xzf %s/nginx.tar.gz -C /"
    [orch_phase1_needed]=".env not found — run phase 1 first: sudo bash scripts/bootstrap.sh"
    [orch_complete]="Bootstrap complete. Your SMART RAG instance is now running."
    [orch_next_visit]="Visit: https://smart-rag.%s"
    [orch_next_login]="Login as: admin / (see credentials.txt for password)"
    [orch_next_finalize]="Next: import agent templates + n8n workflows (phases 8–11, coming soon)"
)


# ─── German ─────────────────────────────────────────────────────────────────
declare -A MSG_DE=(
    # --- common -------------------------------------------------------------
    [value_required]="Eingabe erforderlich."
    [invalid_yn]="Bitte mit ja oder nein antworten."
    [invalid_choice]="Ungültige Auswahl — bitte eine Nummer aus der Liste wählen."
    [enter_choice]="Auswahl"

    # --- language selection ------------------------------------------------
    [lang_prompt]="Sprache wählen / Choose language"
    [lang_en]="English"
    [lang_de]="Deutsch"

    # --- banner / intro -----------------------------------------------------
    [intro_welcome]="Willkommen — dieser Assistent richtet SMART RAG auf diesem Server ein."
    [intro_what_happens]="Was passiert:"
    [intro_step1]="  1. Vorab-Prüfungen (Ubuntu, Docker, Speicherplatz, DNS)"
    [intro_step2]="  2. Interaktive Konfiguration (Kursdaten, LLM-Anbieter, etc.)"
    [intro_step3]="  3. Kryptografisch sichere Secrets erzeugen"
    [intro_step4]="  4. .env-Datei schreiben und Templates ersetzen"
    [intro_step5]="  5. (nächster Lauf) Services starten, Schemas deployen, APIs einrichten"
    [intro_continue]="Fortfahren?"

    # --- preflight ----------------------------------------------------------
    [phase_preflight]="Phase 1 · Vorab-Prüfungen"
    [pf_ubuntu_ok]="Ubuntu %s erkannt"
    [pf_ubuntu_wrong]="Ubuntu 24.04 LTS wird benötigt (gefunden: %s)"
    [pf_ubuntu_not_linux]="Dieses Skript benötigt Ubuntu Linux. Erkannt: %s"
    [pf_root_ok]="Root-Rechte vorhanden"
    [pf_root_needed]="Bitte als root ausführen: sudo bash %s"
    [pf_internet_ok]="Internet-Verbindung vorhanden"
    [pf_internet_fail]="Keine Internet-Verbindung (%s nicht erreichbar)"
    [pf_docker_ok]="Docker %s gefunden"
    [pf_docker_missing]="Docker ist nicht installiert. Bitte zuerst installieren: https://docs.docker.com/engine/install/ubuntu/"
    [pf_compose_ok]="Docker Compose v2 Plugin gefunden"
    [pf_compose_missing]="Docker Compose v2 Plugin fehlt. Installation: sudo apt install docker-compose-plugin"
    [pf_disk_ok]="Speicherplatz: %s GB frei auf %s"
    [pf_disk_low]="Wenig Speicherplatz auf %s: nur %s GB frei (empfohlen: 20+ GB)"
    [pf_dns_skip]="DNS-Prüfung übersprungen — Domain noch nicht eingegeben"
    [pf_dns_ok]="DNS: %s → %s"
    [pf_dns_mismatch]="DNS: %s zeigt auf %s — aber Server-IP ist %s. Certbot wird scheitern."
    [pf_dns_nores]="DNS: %s ist nicht auflösbar. DNS-A-Record setzen, bevor Phase 6 läuft."

    # --- coexistence checks (extended preflight) ---------------------------
    [pf_section_coexist]="Koexistenz-Prüfungen (andere Services auf diesem Host)"
    [pf_port_check]="Host-Ports auf Konflikte prüfen und Alternativen ermitteln…"
    [pf_port_ok]="Port %d ist frei (%s)"
    [pf_port_conflict]="Port %d (%s) belegt durch %s — Vorschlag: %d"
    [pf_port_no_alternative]="Kein freier Port im Bereich %d-%d für %s gefunden"
    [pf_port_all_free]="Alle gewünschten Ports sind frei — keine Anpassung nötig"
    [pf_port_section_resolutions]="Port-Anpassungen erforderlich"
    [pf_port_summary_intro]="Folgende Ports waren bereits belegt. Der Wizard hat freie Alternativen gewählt:"
    [pf_port_summary_line]="  %-25s  %5d → %5d   (war: %s)"
    [pf_port_confirm]="Diese Alternativ-Ports verwenden? (Nein = abbrechen und Ports manuell freigeben)"
    [pf_port_cancelled]="Abgebrochen — bitte konfliktierende Services stoppen und neu starten, oder .env manuell anpassen."
    [pf_subdom_check]="nginx auf Subdomain-Konflikte prüfen…"
    [pf_subdom_ok]="Keine bestehende nginx-Site beansprucht unsere Subdomains"
    [pf_subdom_conflict]="nginx-Site %s liefert bereits: %s"
    [pf_subdom_abort]="Eine oder mehrere unserer Subdomains ist bereits in nginx konfiguriert. Konflikt auflösen und neu starten."
    [pf_cert_check]="Let's Encrypt auf bestehende Zertifikate prüfen…"
    [pf_cert_exists]="Bestehendes Zertifikat gefunden: %s (wird wiederverwendet/erneuert)"
    [pf_cert_clean]="Kein Cert-Namens-Konflikt in Let's Encrypt"
    [pf_data_path_check]="BASE_DATA_PATH auf bestehende Daten prüfen…"
    [pf_data_path_empty]="BASE_DATA_PATH %s ist leer oder existiert nicht (gut)"
    [pf_data_path_smartrag]="BASE_DATA_PATH %s enthält SMART RAG-Daten vom letzten Lauf — werden wiederverwendet"
    [pf_data_path_foreign]="BASE_DATA_PATH %s enthält Dateien, die NICHT von SMART RAG sind: %s"
    [pf_data_path_confirm]="Pfad trotzdem verwenden? (bestehende Dateien werden NICHT angetastet)"
    [pf_nginx_running]="nginx läuft — Koexistenz-Modus aktiv"
    [pf_nginx_not_running]="nginx ist installiert aber nicht aktiv — wird vor SSL gestartet"
    [pf_nginx_config_test]="Bestehende nginx-Config hat Fehler — bitte erst beheben, bevor wir nginx anfassen"

    # --- config wizard ------------------------------------------------------
    [phase_config]="Phase 2 · Konfigurations-Assistent"
    [cfg_intro]="Ein paar Fragen zu deinem Deployment. Vorschläge in [eckigen Klammern]."
    [cfg_env_exists_prompt]="Eine bestehende .env wurde gefunden. Was tun?"
    [cfg_env_keep]="Bestehende .env behalten (Assistent überspringen)"
    [cfg_env_backup_new]="Bestehende .env sichern und neu anlegen (empfohlen)"
    [cfg_env_overwrite]="Ohne Backup überschreiben (destruktiv)"

    # course & deployment
    [cfg_section_course]="Kurs & Deployment"
    [cfg_course_name]="Kurstitel (Freitext, z.B. \"Einführung in Forschungsmethoden\")"
    [cfg_course_id]="Kurs-ID (Kleinbuchstaben, nur Bindestriche, z.B. \"forschungsmethoden\")"
    [cfg_course_id_invalid]="Kurs-ID darf nur aus Kleinbuchstaben, Ziffern und Bindestrichen bestehen."
    [cfg_domain]="Deine Basis-Domain (Subdomains werden automatisch ergänzt; z.B. example.com oder meine-uni.de)"
    [cfg_domain_invalid]="Keine gültige Domain."
    [cfg_admin_email]="Admin-E-Mail (für Let's Encrypt-Benachrichtigungen)"
    [cfg_email_invalid]="Keine gültige E-Mail-Adresse."
    [cfg_base_data_path]="Wo sollen Daten persistiert werden"
    [cfg_tz]="Zeitzone (z.B. Europe/Berlin, America/New_York)"

    # profiles
    [cfg_section_profiles]="Komponenten aktivieren"
    [cfg_enable_observability]="Langfuse Observability aktivieren (empfohlen)?"
    [cfg_enable_lti]="LTI 1.3 aktivieren (für Moodle/ILIAS/Canvas-Anbindung)?"
    [cfg_lms_url]="Öffentliche URL deines LMS (z.B. https://moodle.deine-uni.de)"

    # LLM
    [cfg_section_llm]="Sprachmodell-Anbieter"
    [cfg_llm_provider]="LLM-Anbieter wählen"
    [cfg_llm_model_strong]="Starkes Modell (komplexes Reasoning)"
    [cfg_llm_model_fast]="Schnelles Modell (Klassifikation, Zusammenfassungen)"
    [cfg_llm_api_key]="API-Key deines LLM-Anbieters"
    [cfg_llm_base_url]="Basis-URL deines OpenAI-kompatiblen Endpoints"

    # Embedding
    [cfg_section_embedding]="Embedding-Modell"
    [cfg_embed_warning_bold]="⚠ Wichtig: nach erstem Ingest nicht mehr änderbar!"
    [cfg_embed_warning]="Das Embedding-Modell definiert den Vektorraum. Änderungen später erfordern komplettes Neu-Indexieren aller Kursmaterialien. Sorgfältig wählen."
    [cfg_embed_provider]="Embedding-Anbieter wählen"
    [cfg_embed_model]="Embedding-Modell-Name"
    [cfg_embed_dimensions]="Vektor-Dimensionen"
    [cfg_embed_dims_known]="Auto-ausgefüllt für bekanntes Modell: %s"
    [cfg_embed_api_key]="API-Key für Embedding-Anbieter (kann derselbe wie LLM-Key sein)"
    [cfg_embed_base_url]="Basis-URL deines Embedding-Endpoints"

    # Reranker
    [cfg_section_reranker]="Reranker (optional)"
    [cfg_reranker_intro]="Der Reranker verbessert die Retrieval-Qualität. Cohere empfohlen."
    [cfg_reranker_provider]="Reranker wählen"
    [cfg_reranker_model]="Reranker-Modell"
    [cfg_reranker_api_key]="API-Key für Reranker-Anbieter"
    [cfg_reranker_base_url]="Basis-URL deines Reranker-Endpoints"

    # confirmation
    [cfg_review_title]="Konfiguration prüfen:"
    [cfg_review_confirm]="Konfiguration speichern?"
    [cfg_aborted]="Konfiguration abgebrochen."

    # --- secrets ------------------------------------------------------------
    [phase_secrets]="Phase 3 · Secrets erzeugen"
    [secrets_intro]="Kryptografisch sichere Passwörter und Keys werden erzeugt…"
    [secrets_done]="%d Secrets erzeugt"
    [secrets_admin_pw_note]="Admin-Passwort für Flowise, n8n und Langfuse:"
    [secrets_creds_file]="Alle Zugangsdaten werden gespeichert in: %s"

    # --- templates ----------------------------------------------------------
    [phase_templates]="Phase 4 · Templates schreiben"
    [tpl_writing_env]=".env wird geschrieben nach %s"
    [tpl_writing_nginx]="nginx-Template wird für %s ersetzt"
    [tpl_writing_weaviate]="Weaviate-Schema wird ersetzt (Collection: %s)"
    [tpl_copying_lti]="LTI-Config-Templates werden kopiert"
    [tpl_done]="Alle Templates geschrieben"

    # --- summary ------------------------------------------------------------
    [phase_complete]="Bootstrap-Phase 1 abgeschlossen"
    [summary_files]="Geschriebene Dateien:"
    [summary_next]="Nächste Schritte:"
    [summary_next_review]="  1. .env-Datei prüfen (besonders die Passwörter)"
    [summary_next_dns]="  2. DNS auf Server-IP zeigen lassen (ein Wildcard-A-Record *.%s reicht, oder einzelne Records pro Subdomain — Liste unten)"
    [summary_next_ssl]="  3. Ausführen: sudo bash scripts/get-ssl-certs.sh"
    [summary_next_start]="  4. Ausführen: sudo bash scripts/bootstrap.sh --continue"
    [summary_creds_warn]="🔐 %s sicher aufbewahren — enthält alle Zugangsdaten."
    [summary_creds_chmod]="Berechtigungen auf 600 gesetzt (nur Besitzer kann lesen/schreiben)."

    # --- validation errors --------------------------------------------------
    [val_path_writable]="Pfad ist nicht beschreibbar: %s"
    [val_dimensions_num]="Dimensionen müssen eine positive Ganzzahl sein."

    # --- phase 5 — system packages ------------------------------------------
    [phase_packages]="Phase 5 · System-Pakete installieren"
    [pkg_updating]="apt-Paketindex wird aktualisiert…"
    [pkg_installing]="Installiere: %s"
    [pkg_done]="System-Pakete bereit"
    [pkg_already]="Bereits installiert: %s"

    # --- phase 6 — SSL ------------------------------------------------------
    [phase_ssl]="Phase 6 · SSL-Zertifikate beziehen"
    [ssl_subdomain_list]="Zu zertifizierende Subdomains: %s"
    [ssl_resolving_server_ip]="Öffentliche IP dieses Servers ermitteln…"
    [ssl_server_ip]="Öffentliche Server-IP: %s"
    [ssl_checking_dns]="DNS-Einträge vor Zertifikatsanfrage prüfen…"
    [ssl_dns_match]="DNS: %s → %s ✓"
    [ssl_dns_fail]="DNS-Prüfung fehlgeschlagen für %s — erhalten: %s, erwartet: %s"
    [ssl_dns_block]="Abbruch. DNS-A-Records für alle Subdomains auf %s setzen, dann erneut ausführen."
    [ssl_writing_acme_config]="Temporäre ACME-Challenge-Config wird geschrieben…"
    [ssl_nginx_reload]="nginx neu laden…"
    [ssl_requesting_cert]="Zertifikat bei Let's Encrypt anfordern…"
    [ssl_cert_obtained]="Zertifikat erhalten: %s"
    [ssl_installing_full_config]="Vollständige nginx-Config installieren (HTTP + HTTPS)…"
    [ssl_done]="SSL-Setup abgeschlossen. Alle Subdomains sind jetzt per HTTPS erreichbar."
    [ssl_existing_cert]="Zertifikat für %s existiert bereits — wird übersprungen."
    [ssl_dry_run]="Dry-run: würde Zertifikate anfordern für %s"

    # --- phase 7 — services -------------------------------------------------
    [phase_services]="Phase 7 · Docker-Services starten"
    [svc_pulling]="Docker-Images werden geladen (beim ersten Lauf einige Minuten)…"
    [svc_starting]="Services starten mit Profil: %s"
    [svc_waiting]="Warte bis Services healthy sind…"
    [svc_healthy]="%s ist healthy"
    [svc_unhealthy]="%s wurde nach %d Sekunden nicht healthy"
    [svc_all_healthy]="Alle benötigten Services sind healthy"
    [svc_status]="Service-Status:"
    [svc_done]="Services laufen."

    # --- bootstrap orchestration --------------------------------------------
    [orch_continue_intro]="Bootstrap wird fortgesetzt (Phasen 5–7)…"
    [snap_creating]="Sicherheits-Snapshot des aktuellen System-Zustands wird erstellt…"
    [snap_nginx]="  nginx-Config (/etc/nginx)"
    [snap_docker]="  Docker-Container-Liste"
    [snap_ports]="  Lauschende Ports"
    [snap_letsencrypt]="  Let's Encrypt Zertifikate (nur Metadaten)"
    [snap_done]="Snapshot gespeichert: %s"
    [snap_restore_hint]="nginx wiederherstellen falls nötig: sudo tar xzf %s/nginx.tar.gz -C /"
    [orch_phase1_needed]=".env nicht gefunden — zuerst Phase 1 ausführen: sudo bash scripts/bootstrap.sh"
    [orch_complete]="Bootstrap abgeschlossen. Deine SMART RAG Instanz läuft jetzt."
    [orch_next_visit]="Öffne: https://smart-rag.%s"
    [orch_next_login]="Login als: admin / (Passwort in credentials.txt)"
    [orch_next_finalize]="Nächster Schritt: Agent-Templates + n8n-Workflows importieren (Phasen 8–11, demnächst)"
)


# ─── Language helpers ────────────────────────────────────────────────────────

# t KEY [ARG1 ARG2 ...] — translate (with printf-style substitution)
t() {
    local key="$1"; shift || true
    local fmt
    if [[ "$LANG_CHOICE" == "de" && -n "${MSG_DE[$key]:-}" ]]; then
        fmt="${MSG_DE[$key]}"
    else
        fmt="${MSG_EN[$key]:-MISSING:$key}"
    fi
    # shellcheck disable=SC2059
    printf "$fmt" "$@"
}

# auto-detect language from $LANG env var
detect_default_language() {
    if [[ "${LANG:-}" == de* ]]; then
        echo "de"
    else
        echo "en"
    fi
}

# Interactive language selection
select_language() {
    # already set by --lang flag?
    if [[ -n "$LANG_CHOICE" ]]; then return 0; fi

    local default; default="$(detect_default_language)"
    local default_num
    if [[ "$default" == "de" ]]; then default_num=2; else default_num=1; fi

    printf "${BOLD}%s${RESET}\n" "$(t lang_prompt)"
    printf "    ${BOLD}[1]${RESET}  %s\n" "$(t lang_en)"
    printf "    ${BOLD}[2]${RESET}  %s\n" "$(t lang_de)"
    printf "  ${DIM}%s${RESET} ${BOLD}[%d]${RESET}: " "$(t enter_choice)" "$default_num"

    local input
    IFS= read -r input
    input="${input:-$default_num}"
    case "$input" in
        2) LANG_CHOICE="de" ;;
        *) LANG_CHOICE="en" ;;
    esac
    export LANG_CHOICE
}

# Lint: report any DE keys missing from MSG_EN, or vice versa
check_translations() {
    local key missing_de=() missing_en=()
    for key in "${!MSG_EN[@]}"; do
        [[ -z "${MSG_DE[$key]:-}" ]] && missing_de+=("$key")
    done
    for key in "${!MSG_DE[@]}"; do
        [[ -z "${MSG_EN[$key]:-}" ]] && missing_en+=("$key")
    done
    if (( ${#missing_de[@]} > 0 )); then
        warn "Missing German translations: ${missing_de[*]}"
    fi
    if (( ${#missing_en[@]} > 0 )); then
        warn "Missing English translations: ${missing_en[*]}"
    fi
    return 0
}
