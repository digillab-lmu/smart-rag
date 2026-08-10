"""
Send the installer's hand-over message to the person who will look after the
course content.

This exists as a script inside the Content Admin image, rather than as mail
code in bootstrap.sh, for one reason: the relay configuration, the sender
fallback and the TLS handling are already solved once in mailer.py and are
covered by tests. A second implementation in bash would be a second thing to
get wrong, and it would get wrong the parts that only show up against a real
relay.

Called from the installer as:

    docker exec -i smartrag-content-admin python3 /app/send_handover.py \
        <recipient> <subject> < body.txt

The body comes in on stdin and everything else as arguments: the body is the
only multi-line part, and reading it as a stream avoids both a quoting layer
and a dependency on the host having a JSON tool to encode it with.

Exit codes are the interface: 0 sent, 1 refused (bad input or no relay), 2 the
relay rejected it or was unreachable. The installer prints the message for
copy-and-paste on anything non-zero, so a failure here costs the operator
nothing but a paste.
"""

import sys

import mailer


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: send_handover.py <recipient> <subject> < body", file=sys.stderr)
        return 1

    to = sys.argv[1].strip()
    subject = sys.argv[2].strip()
    body = sys.stdin.read()
    if not to or not subject or not body.strip():
        print("recipient, subject and body are all required", file=sys.stderr)
        return 1

    try:
        mailer.send_mail(to, subject, body)
    except mailer.MailError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    # Anything else is a bug rather than a configuration problem, but the
    # installer must not die because of it — it still has a message to print.
    except Exception as exc:  # noqa: BLE001
        print(f"unexpected failure while sending: {exc}", file=sys.stderr)
        return 2

    print(f"sent to {to}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
