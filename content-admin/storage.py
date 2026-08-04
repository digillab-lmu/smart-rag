"""
Agent-slot state (which archetype + content values are assigned to slots
1-10, and the Flowise chatflow id once imported).

A plain JSON file on a persistent volume — this is a small, simple config
blob (max 10 slots of form data), not relational data; a database schema/
migration story would be over-engineering for what this actually is.
Mirrors how lti-middleware/config/agents.json already does the same kind
of thing for this project.
"""

import json
import os
from pathlib import Path
from threading import Lock

MAX_SLOTS = 10
_LOCK = Lock()

STORAGE_PATH = Path(os.getenv("SMARTRAG_SLOTS_PATH", "/app/data/slots.json"))


def _load() -> dict:
    if not STORAGE_PATH.exists():
        return {}
    return json.loads(STORAGE_PATH.read_text())


def _save(data: dict) -> None:
    STORAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORAGE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.replace(STORAGE_PATH)


def all_slots() -> dict[str, dict]:
    """Returns a dict for slots "1".."10", each either {} (unconfigured) or
    {"archetype": ..., "name": ..., "content": {...},
     "system_prompt": ... | None, "chatflow_id": ... | None}."""
    data = _load()
    return {str(i): data.get(str(i), {}) for i in range(1, MAX_SLOTS + 1)}


def get_slot(slot: int) -> dict:
    return _load().get(str(slot), {})


def name_taken(name: str, exclude_slot: int) -> bool:
    """Case-insensitive check whether another slot already uses this name."""
    normalized = name.strip().casefold()
    if not normalized:
        return False
    data = _load()
    for key, slot_data in data.items():
        if int(key) == exclude_slot:
            continue
        existing_name = (slot_data.get("name") or "").strip().casefold()
        if existing_name and existing_name == normalized:
            return True
    return False


def save_slot(
    slot: int,
    archetype: str,
    content: dict[str, str],
    name: str,
    system_prompt: str | None = None,
) -> None:
    """
    `system_prompt` is the operator's edited version of the archetype's
    system prompt, or None to keep using the shipped default. Stored as an
    override rather than a copy of the default: a slot that was never
    edited keeps tracking the template, so template improvements reach it,
    and slots saved before this field existed simply read as None.
    """
    if not (1 <= slot <= MAX_SLOTS):
        raise ValueError(f"slot must be 1..{MAX_SLOTS}, got {slot}")
    with _LOCK:
        data = _load()
        existing = data.get(str(slot), {})
        data[str(slot)] = {
            "archetype": archetype,
            "name": name,
            "content": content,
            "system_prompt": system_prompt,
            "chatflow_id": existing.get("chatflow_id"),
        }
        _save(data)


def set_chatflow_id(slot: int, chatflow_id: str) -> None:
    with _LOCK:
        data = _load()
        if str(slot) not in data:
            raise ValueError(f"slot {slot} has no saved content yet")
        data[str(slot)]["chatflow_id"] = chatflow_id
        _save(data)
