"""
.env read/write helpers.

set_env_var() is a direct port of scripts/lib/common.sh::set_env_var() — same
in-place patching, same escape order. Keep the two in sync: both write to the
SAME .env file, so a value written by one must be readable by the other (and
by `docker compose`, and by every `source .env` in the shell scripts).
"""

import os
import re
from pathlib import Path

ENV_PATH = Path(os.getenv("SMARTRAG_ENV_PATH", "/app/.env"))


def _escape(value: str) -> str:
    """
    Escape everything that's special inside a double-quoted shell string, in
    this exact order (backslash first, or the escapes added for the other
    three get re-escaped).

    Without this, a value containing e.g. $(...) would be EXECUTED the next
    time any shell script does `source .env`. Verified against the same edge
    cases as the bash original: trailing backslash, nested quotes, $(...),
    backticks.
    """
    value = value.replace("\\", "\\\\")
    value = value.replace("$", "\\$")
    value = value.replace("`", "\\`")
    value = value.replace('"', '\\"')
    return value


def set_env_var(key: str, value: str, env_path: Path | None = None) -> None:
    """
    Patch a single KEY="VALUE" line in place, preserving every other line's
    position — some values interpolate earlier ones textually (e.g.
    NEO4J_AUTH="neo4j/${NEO4J_PASSWORD}"), so reordering would silently break
    sourcing for anything after the moved line. Appends if the key is absent.
    """
    path = env_path or ENV_PATH

    # A newline has no representation both readers of this file agree on.
    # Quote escaping already stops a value from closing its own quotes, so
    # bash keeps everything after the newline inside the value — but
    # read_env() splits on lines, and a second line that looks like KEY=VALUE
    # becomes a key Python sees and bash does not. Two readers of one file
    # disagreeing is precisely the failure this project has already paid for
    # twice (REDIS_AUTH, SMTP_SENDER_EMAIL), so it is refused rather than
    # encoded: no legitimate value here is multi-line — the LTI keys live in
    # files, not in .env.
    if "\n" in value or "\r" in value:
        raise ValueError(
            f"{key}: a value written to .env may not contain a line break"
        )

    escaped = _escape(value)
    new_line = f'{key}="{escaped}"\n'

    lines = path.read_text().splitlines(keepends=True)
    out: list[str] = []
    found = False
    for line in lines:
        if line.startswith(f"{key}="):
            # First occurrence keeps its position; any later one is dropped.
            # A duplicate key is always a defect, and leaving it is worse than
            # it looks: both bash and read_env() take the LAST occurrence, so
            # updating only the first — which this used to do — changed
            # nothing that anyone would ever read.
            if not found:
                out.append(new_line)
                found = True
            continue
        out.append(line)
    if not found:
        if out and not out[-1].endswith("\n"):
            out[-1] += "\n"
        out.append(new_line)

    path.write_text("".join(out))


_UNQUOTE_RE = re.compile(r'^"(.*)"$|^\'(.*)\'$', re.DOTALL)


def read_env(env_path: Path | None = None) -> dict[str, str]:
    """
    Read .env into a dict. Deliberately does NOT expand ${VAR} references —
    callers here only need literal values, and expanding would mean
    reimplementing shell semantics. Values written by set_env_var() are
    shell-escaped; unescape the four characters it escapes.
    """
    path = env_path or ENV_PATH
    result: dict[str, str] = {}
    if not path.exists():
        return result

    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, raw = line.partition("=")
        key = key.strip()
        raw = raw.strip()

        m = _UNQUOTE_RE.match(raw)
        if m:
            raw = m.group(1) if m.group(1) is not None else m.group(2)
            raw = (
                raw.replace('\\"', '"')
                .replace("\\`", "`")
                .replace("\\$", "$")
                .replace("\\\\", "\\")
            )
        result[key] = raw
    return result
