"""
Sending mail from the Content Admin.

Only one thing needs this: the password-reset link. That is also why it does
not go through n8n, which is how every other mail in this system is sent —
the person who needs a reset link is locked out, and making their recovery
depend on a second service being up would be the wrong dependency to add.
Python's smtplib against the same relay n8n uses is enough.

Configuration comes from the same SMTP_* variables the rest of the stack
uses, so an installation that can already send ingest notifications can send
this without any further setup.

Note SMTP_SENDER_EMAIL: .env.example writes it as "noreply@${DOMAIN}", and
read_env() deliberately does not expand shell interpolation. The wizard now
resolves it, but an .env written by an older version still carries the
literal — hence the fallback below, rather than a sender address with a
visible ${DOMAIN} in it.
"""

import logging
import smtplib
import ssl
from email.message import EmailMessage

from env_file import read_env

logger = logging.getLogger(__name__)

TIMEOUT = 15


class MailError(RuntimeError):
    """Raised when a message could not be handed to the relay."""


def mail_configured(env: dict | None = None) -> bool:
    """True when there is somewhere to send to and something to send with.

    Both halves matter: a relay with no admin address to send to is as
    useless here as an address with no relay.
    """
    env = env if env is not None else read_env()
    return bool(env.get("SMTP_HOST", "").strip()) and bool(
        env.get("ADMIN_EMAIL", "").strip()
    )


def _sender(env: dict) -> str:
    raw = env.get("SMTP_SENDER_EMAIL", "").strip()
    # An unresolved "${DOMAIN}" is worse than no value: it produces a sender
    # that looks deliberate and is not a valid address.
    if raw and "${" not in raw:
        return raw
    domain = env.get("DOMAIN", "").strip()
    return f"noreply@{domain}" if domain else "noreply@localhost"


def send_mail(to: str, subject: str, body: str) -> None:
    """Send one plain-text message. Raises MailError on any failure."""
    env = read_env()
    host = env.get("SMTP_HOST", "").strip()
    if not host:
        raise MailError("SMTP_HOST is empty — no mail relay configured.")

    try:
        port = int(env.get("SMTP_PORT", "25") or 25)
    except ValueError:
        port = 25

    msg = EmailMessage()
    msg["From"] = _sender(env)
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    secure = env.get("SMTP_SECURE", "false").strip().lower() in ("true", "1", "yes")
    user = env.get("SMTP_USER", "").strip()
    password = env.get("SMTP_PASSWORD", "")

    try:
        if secure and port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=TIMEOUT,
                                  context=ssl.create_default_context()) as smtp:
                if user:
                    smtp.login(user, password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=TIMEOUT) as smtp:
                if secure:
                    smtp.starttls(context=ssl.create_default_context())
                # The local Postfix relay this project can install accepts
                # unauthenticated mail from the Docker network by design —
                # the provider password lives in Postfix's config, not here.
                if user:
                    smtp.login(user, password)
                smtp.send_message(msg)
    except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
        # The relay's own words are almost always the fix, so they are kept.
        raise MailError(str(exc)) from exc

    logger.info("Password-reset mail sent to %s via %s:%s", to, host, port)
