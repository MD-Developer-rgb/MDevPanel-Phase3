"""Instance discovery, name validation and per-instance metadata (Phase 2).

An "instance" is a subfolder directly inside INSTANCES_DIR that contains a
server JAR. This module never lets a name escape INSTANCES_DIR, and never
touches the contents of a folder that already looks like an instance.
"""

import json
import os
import re
import threading
import time

import config

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.\-]{0,63}$")
_META_LOCK = threading.RLock()


class InstanceError(Exception):
    """A user-facing problem with an instance name or path."""


# --------------------------------------------------------------- name safety

def validate_name(name):
    """Raise InstanceError for anything but a plain folder-name-safe string
    that stays inside INSTANCES_DIR. Returns the trimmed name."""
    name = (name or "").strip()
    if not name:
        raise InstanceError("Enter an instance name.")
    if len(name) > 64:
        raise InstanceError("Instance name is too long (64 characters max).")
    if name in (".", "..") or not _NAME_RE.match(name):
        raise InstanceError(
            "Use only letters, numbers, spaces, - . or _ (no / \\ or leading dot).")
    # Belt and suspenders: resolve and confirm it lands directly inside INSTANCES_DIR.
    root = os.path.realpath(instances_root())
    candidate = os.path.realpath(os.path.join(root, name))
    if os.path.dirname(candidate) != root:
        raise InstanceError("That name is not allowed.")
    return name


def instances_root():
    return os.path.abspath(os.path.expanduser(str(config.INSTANCES_DIR)))


def instance_dir(name):
    """Validated absolute path to an instance's directory. Raises InstanceError
    for an unsafe name; does not check the directory actually exists."""
    name = validate_name(name)
    return os.path.join(instances_root(), name)


# ----------------------------------------------------------------- detection

def _find_jar(directory):
    """Server JAR for an instance directory: prefer the configured default
    name; otherwise fall back to the only *.jar file directly inside, if
    there is exactly one (for manually created instances)."""
    default = str(config.DEFAULT_SERVER_JAR)
    if os.path.isfile(os.path.join(directory, default)):
        return default
    try:
        entries = os.listdir(directory)
    except OSError:
        return None
    jars = [e for e in entries if e.lower().endswith(".jar") and os.path.isfile(os.path.join(directory, e))]
    return jars[0] if len(jars) == 1 else None


def list_instances():
    """Names of every valid instance directly inside INSTANCES_DIR, sorted."""
    root = instances_root()
    try:
        entries = os.listdir(root)
    except OSError:
        return []
    found = []
    for entry in sorted(entries, key=str.lower):
        path = os.path.join(root, entry)
        if not os.path.isdir(path) or entry.startswith("."):
            continue
        if not _NAME_RE.match(entry):
            continue  # not a name the panel could have created or would manage
        if _find_jar(path):
            found.append(entry)
    return found


def instance_exists(name):
    directory = instance_dir(name)
    return os.path.isdir(directory) and _find_jar(directory) is not None


def jar_name(name):
    return _find_jar(instance_dir(name))


# ------------------------------------------------------------------ creation

def create_instance_dir(name):
    """Create a brand-new, empty instance folder. Refuses if anything is
    already there (existing file, folder, or instance) so nothing already
    on disk is ever touched or overwritten."""
    directory = instance_dir(name)
    if os.path.exists(directory):
        raise InstanceError(f'"{name}" already exists.')
    try:
        os.makedirs(directory)
    except OSError as exc:
        raise InstanceError(f"Could not create the instance folder: {exc.strerror or exc}.") from exc
    return directory


def remove_empty_instance_dir(name):
    """Best-effort cleanup if creation failed right after making the folder
    (e.g. the download failed) - only removes it if it's still empty."""
    directory = instance_dir(name)
    try:
        if os.path.isdir(directory) and not os.listdir(directory):
            os.rmdir(directory)
    except OSError:
        pass


# ------------------------------------------------------------- metadata (RAM)

def _meta_path(name):
    data_dir = os.path.abspath(str(config.DATA_DIR))
    os.makedirs(os.path.join(data_dir, "instances"), exist_ok=True)
    return os.path.join(data_dir, "instances", f"{validate_name(name)}.json")


def load_metadata(name):
    path = _meta_path(name)
    defaults = {
        "ram_min": config.DEFAULT_RAM_MIN,
        "ram_max": config.DEFAULT_RAM_MAX,
        "created": None,
        "minecraft_version": None,
        "software": None,
    }
    with _META_LOCK:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                defaults.update(data)
        except (OSError, ValueError):
            pass
    return defaults


def save_metadata(name, **updates):
    path = _meta_path(name)
    with _META_LOCK:
        data = load_metadata(name)
        data.update(updates)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp, path)
    return data


def record_created(name, minecraft_version, ram_min, ram_max):
    save_metadata(
        name,
        created=time.time(),
        minecraft_version=minecraft_version,
        software="Paper",
        ram_min=ram_min,
        ram_max=ram_max,
    )


def delete_metadata(name):
    path = _meta_path(name)
    try:
        os.remove(path)
    except OSError:
        pass
