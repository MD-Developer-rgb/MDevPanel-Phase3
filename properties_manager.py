"""Per-instance server.properties editor (Phase 2).

Reads and writes exactly one instance's server.properties at a time. Comments
and properties the UI doesn't know about are preserved untouched; only lines
whose key changed are rewritten, in place.
"""

import os

# key -> (label, kind, choices) shown by the friendly editor. Anything else in
# the file still round-trips through the raw/advanced editor untouched.
KNOWN_FIELDS = [
    ("server-port", "Server Port", "int", None),
    ("max-players", "Max Players", "int", None),
    ("online-mode", "Online Mode", "bool", None),
    ("difficulty", "Difficulty", "choice", ["peaceful", "easy", "normal", "hard"]),
    ("gamemode", "Gamemode", "choice", ["survival", "creative", "adventure", "spectator"]),
    ("pvp", "PVP", "bool", None),
    ("motd", "MOTD", "text", None),
    ("white-list", "Whitelist", "bool", None),
    ("spawn-protection", "Spawn Protection", "int", None),
    ("view-distance", "View Distance", "int", None),
    ("allow-flight", "Allow Flight", "bool", None),
    ("hardcore", "Hardcore", "bool", None),
]
KNOWN_KEYS = {key for key, *_ in KNOWN_FIELDS}


class PropertiesError(Exception):
    pass


def path_for(instance_dir):
    return os.path.join(instance_dir, "server.properties")


def read_raw(instance_dir):
    """Full file text, or "" if it does not exist yet (Minecraft creates it on
    first start)."""
    path = path_for(instance_dir)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def parse(text):
    """Ordered list of {"kind": "comment"|"blank"|"prop", "raw": line, "key":..,
    "value": ..} - enough to rewrite the file exactly as it was except for
    changed values."""
    rows = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            rows.append({"kind": "blank", "raw": line})
        elif stripped.startswith("#"):
            rows.append({"kind": "comment", "raw": line})
        elif "=" in line:
            key, _, value = line.partition("=")
            rows.append({"kind": "prop", "key": key.strip(), "value": value.strip(), "raw": line})
        else:
            rows.append({"kind": "comment", "raw": line})  # unrecognized line - preserve, don't touch
    return rows


def as_dict(instance_dir):
    return {r["key"]: r["value"] for r in parse(read_raw(instance_dir)) if r["kind"] == "prop"}


def friendly_fields(instance_dir):
    """Known fields plus their current value (None if the file doesn't set them yet)."""
    current = as_dict(instance_dir)
    return [
        {"key": key, "label": label, "kind": kind, "choices": choices, "value": current.get(key)}
        for key, label, kind, choices in KNOWN_FIELDS
    ]


def other_lines_text(instance_dir):
    """Raw text of every line NOT covered by the friendly editor - shown in
    the advanced/raw box, still fully editable there."""
    lines = []
    for row in parse(read_raw(instance_dir)):
        if row["kind"] == "prop" and row["key"] in KNOWN_KEYS:
            continue
        lines.append(row["raw"])
    return "\n".join(lines)


def _validate(key, kind, value, choices):
    value = "" if value is None else str(value).strip()
    if kind == "int":
        if not value.lstrip("-").isdigit():
            raise PropertiesError(f'"{key}" must be a whole number.')
    elif kind == "bool":
        if value.lower() not in ("true", "false"):
            raise PropertiesError(f'"{key}" must be true or false.')
        value = value.lower()
    elif kind == "choice" and choices and value and value not in choices:
        raise PropertiesError(f'"{key}" must be one of: {", ".join(choices)}.')
    return value


def save(instance_dir, friendly_values, raw_other_text):
    """Merge friendly-editor values and the raw/advanced text back into
    server.properties, preserving comments and any property the friendly
    editor doesn't know about, changing only what actually changed."""
    rows = parse(read_raw(instance_dir))
    field_by_key = {key: (label, kind, choices) for key, label, kind, choices in KNOWN_FIELDS}

    validated = {}
    for key, value in (friendly_values or {}).items():
        if key not in field_by_key:
            continue
        _, kind, choices = field_by_key[key]
        validated[key] = _validate(key, kind, value, choices)

    seen = set()
    out = []
    for row in rows:
        if row["kind"] != "prop" or row["key"] not in KNOWN_KEYS:
            continue  # rebuilt from raw_other_text below, in the order given there
        seen.add(row["key"])
        if row["key"] in validated:
            out.append(f"{row['key']}={validated[row['key']]}")
        else:
            out.append(row["raw"])
    for key, value in validated.items():
        if key not in seen:
            out.append(f"{key}={value}")

    other = (raw_other_text or "").replace("\r\n", "\n").split("\n")
    # trim a single trailing blank line the textarea tends to add
    if other and other[-1] == "":
        other = other[:-1]
    for line in other:
        if "=" in line and not line.strip().startswith("#"):
            key = line.split("=", 1)[0].strip()
            if key in KNOWN_KEYS:
                raise PropertiesError(
                    f'"{key}" is edited above - remove it from the advanced box.')

    final_lines = out + ([""] if out and other else []) + other
    path = path_for(instance_dir)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write("\n".join(final_lines) + "\n")
        os.replace(tmp, path)
    except OSError as exc:
        raise PropertiesError(f"Could not save server.properties: {exc.strerror or exc}.") from exc
