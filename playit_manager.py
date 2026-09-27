"""Playit tunnel integration for MDev Panel (Phase 3).

MDevPanel always downloads and runs its OWN copy of the official Playit
binaries into config.PLAYIT_BIN_DIR. It never executes a `playit` found on
PATH or installed system-wide (see agent_path()/cli_path() below - every
process this module starts comes from one of those two functions, and
neither ever consults PATH or a system install location).

Architecture (as documented by the playit-agent project itself):
  - the "playit" binary is the agent/daemon that actually runs the tunnel.
    Started with no secret configured, it waits for one to be provisioned.
  - the "playit-cli" binary is a frontend used only for the one-time
    account setup ("claim") flow: it prints a URL for the user to open,
    waits for it to be approved, then hands the resulting secret to the
    running daemon.
Both binaries are needed; only the daemon keeps running afterwards.

All Playit output parsing lives in this module so it can be updated on its
own if a future Playit release changes its log format, per the phase spec.
"""

import collections
import hashlib
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime

import requests

import config
import os_manager

# ------------------------------------------------------------------- phases

NOT_INSTALLED = "not_installed"
INSTALLING = "installing"
INSTALLED = "installed"
SETUP_REQUIRED = "setup_required"
STARTING = "starting"
RUNNING = "running"
STOPPING = "stopping"
STOPPED = "stopped"
ERROR = "error"

_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]{0,31}$")
_URL_RE = re.compile(r"https://playit\.gg/(?:claim|login)/\S+")
_PUBLIC_ADDR_RE = re.compile(r"\b[\w-]+\.(?:playit\.gg|ply\.gg|gl\.at\.ply\.gg)(?::\d+)?\b", re.I)
_VERSION_OUT_RE = re.compile(r"(\d+\.\d+\.\d+(?:[.\-][A-Za-z0-9]+)?)")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


class PlayitError(Exception):
    """User-facing problem installing, starting or setting up Playit."""


def _res(ok, message, http=200):
    return {"ok": ok, "message": message, "http": http}


def _ensure_dirs():
    for path in (config.PLAYIT_DIR, config.PLAYIT_BIN_DIR, config.PLAYIT_CONFIG_DIR,
                 config.PLAYIT_LOG_DIR, config.PLAYIT_RUNTIME_DIR):
        os.makedirs(path, exist_ok=True)


# --------------------------------------------------------------- platform info

def detect_platform():
    """{"os": ..., "environment": ..., "architecture": ..., "platform_key": ...,
    "supported": bool} - never guesses across CPU families (see os_manager)."""
    arch = os_manager.detect_arch()
    family = os_manager.os_family()
    key = f"{family}-{arch}"
    supported = key in config.PLAYIT_AGENT_BINARIES and key in config.PLAYIT_CLI_BINARIES
    os_name = {"linux": "Linux", "windows": "Windows", "mac": "macOS"}.get(family, family)
    environment = "Termux" if os_manager.IS_TERMUX else None
    return {
        "os": os_name,
        "environment": environment,
        "architecture": arch,
        "platform_key": key,
        "supported": supported,
    }


# ---------------------------------------------------------------- binary paths

def _exe_name(base):
    return base + ".exe" if os_manager.IS_WINDOWS else base


def agent_path():
    return os.path.join(config.PLAYIT_BIN_DIR, _exe_name("playit"))


def cli_path():
    return os.path.join(config.PLAYIT_BIN_DIR, _exe_name("playit-cli"))


def secret_path():
    return os.path.join(config.PLAYIT_CONFIG_DIR, "playit.toml")


def socket_path():
    if hasattr(config, "PLAYIT_SOCKET_PATH"):
        return config.PLAYIT_SOCKET_PATH
    return os.path.join(config.PLAYIT_RUNTIME_DIR, "playit.sock")


def _install_meta_path():
    return os.path.join(config.PLAYIT_CONFIG_DIR, "install.json")


def _log_file_path():
    return os.path.join(config.PLAYIT_LOG_DIR, "playit-agent.log")


def _load_install_meta():
    try:
        with open(_install_meta_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_install_meta(**updates):
    _ensure_dirs()
    data = _load_install_meta()
    data.update(updates)
    tmp = _install_meta_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, _install_meta_path())
    return data


# ------------------------------------------------------------------ Github API

def _api_get(path):
    url = f"{config.PLAYIT_API_BASE}{path}"
    try:
        resp = requests.get(url, timeout=15, headers={"Accept": "application/vnd.github+json"})
    except requests.Timeout as exc:
        raise PlayitError("GitHub took too long to respond. Try again.") from exc
    except requests.ConnectionError as exc:
        raise PlayitError("Could not reach GitHub. Check your internet connection.") from exc
    except requests.RequestException as exc:
        raise PlayitError("Could not reach GitHub.") from exc
    if resp.status_code == 404:
        raise PlayitError("That Playit release could not be found on GitHub.")
    if not resp.ok:
        raise PlayitError(f"GitHub returned an error (HTTP {resp.status_code}).")
    try:
        return resp.json()
    except ValueError as exc:
        raise PlayitError("GitHub returned a response that could not be understood.") from exc


def resolve_version():
    """The exact version string to install, honoring PLAYIT_VERSION == "latest"
    and refusing an explicitly-pinned prerelease unless allowed."""
    configured = str(config.PLAYIT_VERSION or "").strip()
    if not configured:
        raise PlayitError("No Playit version is configured.")

    if configured.lower() == "latest":
        data = _api_get("/releases/latest")  # GitHub's own "latest" already excludes prereleases/drafts
        tag = data.get("tag_name", "")
        version = tag[1:] if tag.startswith("v") else tag
        if not version:
            raise PlayitError("GitHub did not report a latest Playit version.")
        return version

    if not _VERSION_RE.match(configured):
        raise PlayitError("PLAYIT_VERSION in config.py is not a valid version string.")

    data = _api_get(f"/releases/tags/v{configured}")
    if data.get("prerelease") and not config.PLAYIT_ALLOW_PRERELEASE:
        raise PlayitError(
            f'Playit {configured} is a prerelease. Set PLAYIT_ALLOW_PRERELEASE = True in '
            f'config.py to allow installing it, or configure a stable version.')
    return configured


# -------------------------------------------------------------------- download

def _sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download_asset(version, asset_name, destination, progress_cb=None):
    url = f"{config.PLAYIT_RELEASES_BASE}/v{version}/{asset_name}"
    tmp_path = destination + ".part"
    try:
        with requests.get(url, stream=True, timeout=20) as resp:
            if resp.status_code == 404:
                raise PlayitError(f'"{asset_name}" is not available for Playit {version} on GitHub.')
            if not resp.ok:
                raise PlayitError(f"The download failed (HTTP {resp.status_code}).")
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            with open(tmp_path, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=1024 * 256):
                    if not chunk:
                        continue
                    fh.write(chunk)
                    done += len(chunk)
                    if progress_cb:
                        progress_cb(asset_name, done, total)
    except requests.Timeout as exc:
        _cleanup(tmp_path)
        raise PlayitError("The download timed out. Check your internet connection and try again.") from exc
    except requests.ConnectionError as exc:
        _cleanup(tmp_path)
        raise PlayitError("The download was interrupted. Check your internet connection and try again.") from exc
    except requests.RequestException as exc:
        _cleanup(tmp_path)
        raise PlayitError("The download failed.") from exc
    except OSError as exc:
        _cleanup(tmp_path)
        raise PlayitError("Could not write the downloaded file to storage.") from exc
    except PlayitError:
        _cleanup(tmp_path)
        raise

    if done == 0:
        _cleanup(tmp_path)
        raise PlayitError(f'"{asset_name}" downloaded as an empty file.')

    expected = config.PLAYIT_CHECKSUMS.get(version, {}).get(asset_name)
    verified = False
    if expected:
        if _sha256_of(tmp_path) != expected:
            _cleanup(tmp_path)
            raise PlayitError(f'"{asset_name}" did not match the expected checksum. Try again.')
        verified = True

    if not os_manager.IS_WINDOWS:
        os.chmod(tmp_path, 0o755)
    os.replace(tmp_path, destination)
    return verified


def _cleanup(path):
    try:
        os.remove(path)
    except OSError:
        pass


# ---------------------------------------------------------------- install state

def binary_ok(path):
    return os.path.isfile(path) and (os_manager.IS_WINDOWS or os.access(path, os.X_OK))


def _looks_like_android_shared_storage(path):
    """/storage/emulated/0/..., /sdcard/..., /storage/self/... etc. are backed
    by sdcardfs/FUSE on Android: chmod +x on files there is silently ignored,
    so a binary downloaded here can never be marked executable. This is the
    single most common reason installation fails on Termux."""
    real = os.path.realpath(path)
    return bool(re.match(r"^/storage/|^/sdcard(/|$)|^/mnt/(sdcard|media_rw)/", real))


def diagnose_exec_problem(path):
    """After a chmod+os.access(X_OK) failure, work out *why* as precisely as
    possible instead of a single generic message."""
    if not os.path.isfile(path):
        return f"{os.path.basename(path)} was not written to disk."

    if _looks_like_android_shared_storage(path):
        return (
            f"{os.path.basename(path)} is on Android shared storage "
            f"({path}), which does not support marking files as executable. "
            "Move MDevPanel's own folder into Termux's private storage "
            "(for example ~/MDevPanel, i.e. NOT under /storage/emulated/0) "
            "and restart the panel from there. Your Minecraft instances can "
            "stay on shared storage - only the panel + Playit installation "
            "need to be on Termux's own filesystem.")

    try:
        os.chmod(path, 0o755)
    except OSError as exc:
        return f"Could not set the executable permission on {os.path.basename(path)}: {exc.strerror or exc}."

    if os.access(path, os.X_OK):
        return None  # transient - it's fine now

    try:
        import subprocess
        subprocess.run([path, "--version"], capture_output=True, timeout=5)
        return None
    except PermissionError:
        return (
            f"The filesystem holding {path} does not allow executing files "
            "(often a restricted mount or noexec option). Try installing "
            "Playit with MDevPanel's folder on a different filesystem, such "
            "as Termux's own home directory.")
    except OSError as exc:
        if getattr(exc, "errno", None) == 8:  # Exec format error
            return (f"{os.path.basename(path)} is not built for this device's CPU "
                    f"architecture ({os_manager.detect_arch()}).")
        return f"{os.path.basename(path)} could not be executed: {exc.strerror or exc}."
    except Exception:
        return f"{os.path.basename(path)} could not be executed."


def is_installed():
    return binary_ok(agent_path()) and binary_ok(cli_path())


def installed_version(force=False):
    """Best-effort version string: prefer what we recorded at install time;
    fall back to asking the binary itself; never fails hard."""
    meta = _load_install_meta()
    if not force and meta.get("version"):
        return meta["version"]
    if not binary_ok(agent_path()):
        return None
    try:
        import subprocess
        out = subprocess.run([agent_path(), "--version"], capture_output=True, text=True,
                             timeout=10, errors="replace")
        text = (out.stdout or out.stderr or "").strip()
        match = _VERSION_OUT_RE.search(text)
        version = match.group(1) if match else (text[:32] or None)
        if version:
            _save_install_meta(version=version)
        return version
    except Exception:
        return meta.get("version")


# ============================================================================

class PlayitManager:
    """Owns MDevPanel's private Playit agent process, the one-time setup
    flow, its log buffer, and status. Entirely separate from Minecraft
    instance management - nothing here can start, stop or touch a Minecraft
    process, and nothing in server_manager.py can touch this."""

    def __init__(self):
        self._lock = threading.RLock()
        self._cond = threading.Condition()

        self._daemon_proc = None
        self._daemon_exited = threading.Event()
        self._daemon_exited.set()
        self._starting = False
        self._stopping = False
        self._last_error = None

        self._setup_proc = None
        self._setup_url = None
        self._setup_active = False
        self._setup_error = None
        self._setup_started = None

        self._public_address = None

        self._buf = collections.deque(maxlen=max(200, int(config.PLAYIT_LOG_BUFFER_LINES)))
        self._next_id = 1

        self._install_job = None  # {"status", "message", "percent"} while installing

        _ensure_dirs()
        self._load_log_tail()

    # ------------------------------------------------------------- log buffer

    def _add(self, source, text):
        with self._cond:
            self._buf.append({"id": self._next_id, "s": source, "t": text})
            self._next_id += 1
            self._cond.notify_all()
        try:
            with open(_log_file_path(), "a", encoding="utf-8") as fh:
                fh.write(text + "\n")
        except OSError:
            pass
        self._scan_for_public_address(text)

    def _panel(self, message):
        self._add("panel", f"[{datetime.now():%H:%M:%S}] [Panel] {message}")

    def next_id(self):
        with self._cond:
            return self._next_id

    def wait_for_lines(self, last_id, timeout):
        with self._cond:
            if self._next_id - 1 <= last_id:
                self._cond.wait(timeout)
            out = []
            for entry in reversed(self._buf):
                if entry["id"] <= last_id:
                    break
                out.append(entry)
            out.reverse()
            return out

    def log_text(self):
        with self._cond:
            return "\n".join(e["t"] for e in self._buf) + "\n"

    def clear_log(self):
        with self._cond:
            self._buf.clear()

    def _load_log_tail(self, max_bytes=65536, max_lines=200):
        path = _log_file_path()
        if not os.path.isfile(path):
            return
        try:
            with open(path, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                fh.seek(max(0, size - max_bytes))
                data = fh.read().decode("utf-8", "replace")
        except OSError:
            return
        lines = data.splitlines()
        if size > max_bytes:
            lines = lines[1:]
        lines = [ln for ln in lines if ln.strip()][-max_lines:]
        if not lines:
            return
        with self._cond:
            for line in lines:
                self._buf.append({"id": self._next_id, "s": "history", "t": line})
                self._next_id += 1

    def _scan_for_public_address(self, text):
        match = _PUBLIC_ADDR_RE.search(text)
        if match:
            self._public_address = match.group(0)

    # ----------------------------------------------------------------- install

    def install_job_status(self):
        with self._lock:
            return dict(self._install_job) if self._install_job else None

    def start_install(self):
        with self._lock:
            if self._install_job and self._install_job["status"] == "installing":
                return _res(False, "Installation is already in progress.", 409)
            self._install_job = {"status": "installing", "percent": 0, "message": "Starting..."}
        threading.Thread(target=self._install_worker, daemon=True, name="playit-install").start()
        return _res(True, "Installing Playit...")

    def _set_job(self, **updates):
        with self._lock:
            if self._install_job is not None:
                self._install_job.update(updates)

    def _install_worker(self):
        try:
            _ensure_dirs()
            platform_info = detect_platform()
            if not platform_info["supported"]:
                raise PlayitError(
                    f"No official Playit binary is configured for {platform_info['os']} "
                    f"{platform_info['architecture']} ({platform_info['platform_key']}). "
                    "Add a binary URL for it in config.py to enable installation.")

            self._set_job(message="Checking the latest Playit release...")
            version = resolve_version()

            key = platform_info["platform_key"]
            agent_asset = config.PLAYIT_AGENT_BINARIES[key]
            cli_asset = config.PLAYIT_CLI_BINARIES[key]

            if _looks_like_android_shared_storage(config.PLAYIT_BIN_DIR):
                raise PlayitError(
                    f"MDevPanel's data folder ({config.PLAYIT_BIN_DIR}) is on Android shared "
                    "storage, which cannot mark downloaded files as executable. Move MDevPanel's "
                    "own folder into Termux's private storage (e.g. ~/MDevPanel, not anywhere "
                    "under /storage/emulated/0) and restart the panel before installing Playit. "
                    "Your Minecraft instances folder can stay where it is.")

            def progress(asset, done, total):
                pct = int(done * 50 / total) if total else 0
                base = 0 if asset == agent_asset else 50
                self._set_job(percent=min(99, base + pct),
                               message=f"Downloading {asset}... {done // 1024} KB" +
                                       (f" / {total // 1024} KB" if total else ""))

            self._panel(f"Installing Playit {version} for {key}...")
            self._set_job(percent=1, message=f"Downloading {agent_asset}...")
            agent_verified = _download_asset(version, agent_asset, agent_path(), progress)
            self._set_job(percent=50, message=f"Downloading {cli_asset}...")
            cli_verified = _download_asset(version, cli_asset, cli_path(), progress)

            self._set_job(percent=95, message="Verifying the installed binary runs...")
            problem = diagnose_exec_problem(agent_path()) or diagnose_exec_problem(cli_path())
            if problem:
                raise PlayitError(problem)
            resolved_version = installed_version(force=True) or version

            _save_install_meta(
                version=resolved_version, platform_key=key,
                installed_at=time.time(),
                checksum_verified=bool(agent_verified and cli_verified),
            )
            self._panel(f"Playit {resolved_version} installed.")
            self._set_job(status="done", percent=100,
                          message=f"Playit {resolved_version} installed.")
        except PlayitError as exc:
            self._panel(f"Install failed: {exc}")
            self._set_job(status="error", message=str(exc))
        except Exception as exc:  # pragma: no cover
            self._panel(f"Install failed: unexpected error ({exc})")
            self._set_job(status="error", message="Something went wrong installing Playit.")

    # ------------------------------------------------------------------ status

    def _daemon_alive(self):
        return os_manager.is_alive(self._daemon_proc)

    def secret_configured(self):
        path = secret_path()
        try:
            return os.path.isfile(path) and os.path.getsize(path) > 0
        except OSError:
            return False

    def phase(self):
        if not is_installed():
            return NOT_INSTALLED
        job = self.install_job_status()
        if job and job["status"] == "installing":
            return INSTALLING
        with self._lock:
            if self._stopping:
                return STOPPING
            if self._starting:
                return STARTING
            daemon_alive = self._daemon_alive()
            secret_ok = self.secret_configured()
            setup_active = self._setup_active
            last_error = self._last_error
        if daemon_alive and secret_ok:
            return RUNNING
        if setup_active or (daemon_alive and not secret_ok):
            return SETUP_REQUIRED
        if last_error:
            return ERROR
        if secret_ok and not daemon_alive:
            return STOPPED
        return INSTALLED

    def status(self):
        platform_info = detect_platform()
        phase = self.phase()
        with self._lock:
            setup_url = self._setup_url if self._setup_active else None
            setup_error = self._setup_error
            last_error = self._last_error
            public_address = self._public_address
        return {
            "ok": True,
            "installed": is_installed(),
            "version": installed_version(),
            "configured_version": str(config.PLAYIT_VERSION),
            "phase": phase,
            "secret_configured": self.secret_configured(),
            "agent_running": self._daemon_alive(),
            "platform": platform_info["os"],
            "environment": platform_info["environment"],
            "architecture": platform_info["architecture"],
            "supported": platform_info["supported"],
            "public_address": public_address,
            "setup_url": setup_url,
            "message": setup_error or last_error,
            "install_job": self.install_job_status(),
        }

    # ------------------------------------------------------------------- start

    def start(self):
        with self._lock:
            if self._daemon_alive():
                return _res(False, "Playit agent is already running.", 409)
            if self._stopping:
                return _res(False, "Playit is still stopping. Wait a moment.", 409)
            if not is_installed():
                return _res(False, "Playit is not installed yet.", 400)
            return self._start_locked()

    def _start_locked(self):
        _ensure_dirs()
        flag = getattr(config, "PLAYIT_SECRET_PATH_FLAG", "--secret-path")
        if flag == "--secret_path":
            flag = "--secret-path"
        
        command = [
            agent_path(),
            flag, secret_path(),
            "--socket-path", socket_path()
        ]
        try:
            import subprocess
            proc = subprocess.Popen(
                command, cwd=config.PLAYIT_RUNTIME_DIR,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                **os_manager.popen_kwargs(),
            )
        except OSError as exc:
            self._last_error = "Could not launch the Playit agent binary."
            self._panel(f"Failed to start Playit: {exc}")
            return _res(False, "Unable to start Playit.", 500)

        self._daemon_proc = proc
        self._daemon_exited = threading.Event()
        self._starting = True
        self._last_error = None
        self._public_address = None
        try:
            with open(os.path.join(config.PLAYIT_RUNTIME_DIR, "agent.pid"), "w") as fh:
                fh.write(str(proc.pid))
        except OSError:
            pass
        self._panel("Starting Playit agent...")
        threading.Thread(target=self._reader, args=(proc, self._daemon_exited),
                          daemon=True, name="playit-reader").start()
        threading.Timer(2.0, self._clear_starting_flag, args=(proc,)).start()
        return _res(True, "Starting Playit agent...")

    def _clear_starting_flag(self, proc):
        with self._lock:
            if self._daemon_proc is proc:
                self._starting = False

    def _reader(self, proc, exited):
        try:
            for raw in proc.stdout:
                line = ANSI_RE.sub("", raw.rstrip("\r\n"))
                if line.strip():
                    self._add("agent", line)
        except (ValueError, OSError):
            pass
        finally:
            try:
                code = proc.wait()
            except Exception:
                code = -1
            self._on_exit(proc, code, exited)

    def _on_exit(self, proc, code, exited):
        with self._lock:
            if proc is self._daemon_proc:
                self._daemon_proc = None
                self._starting = False
                if not self._stopping and code not in (0, None):
                    self._last_error = f"Playit agent exited unexpectedly (exit code {code})."
                    self._panel(f"Playit agent crashed (exit code {code}).")
                else:
                    self._panel("Playit agent stopped.")
        exited.set()

    # -------------------------------------------------------------------- stop

    def stop(self):
        with self._lock:
            if not self._daemon_alive():
                return _res(False, "Playit agent is not running.", 409)
            proc, exited = self._daemon_proc, self._daemon_exited
            self._stopping = True
        self._panel("Stopping Playit agent...")
        threading.Thread(target=self._stop_worker, args=(proc, exited),
                          daemon=True, name="playit-stop").start()
        return _res(True, "Stopping Playit agent...")

    def _stop_worker(self, proc, exited):
        os_manager.terminate(proc, force=False)  # graceful signal first
        if not exited.wait(max(1, int(config.PLAYIT_STOP_TIMEOUT))):
            self._panel("Playit agent did not stop in time. Forcing it to close.")
            os_manager.terminate(proc, force=True)
            exited.wait(max(1, int(config.PLAYIT_KILL_GRACE)))
        with self._lock:
            self._stopping = False

    def restart(self):
        with self._lock:
            if not self._daemon_alive():
                return _res(False, "Playit agent is not running. Use Start instead.", 409)
        self._panel("Restarting Playit agent...")
        threading.Thread(target=self._restart_worker, daemon=True, name="playit-restart").start()
        return _res(True, "Restarting Playit agent...")

    def _restart_worker(self):
        result = self.stop()
        if not result["ok"]:
            return
        with self._lock:
            exited = self._daemon_exited
        exited.wait(int(config.PLAYIT_STOP_TIMEOUT) + int(config.PLAYIT_KILL_GRACE) + 5)
        time.sleep(0.5)
        with self._lock:
            self._start_locked()

    # ------------------------------------------------------------------- setup

    def setup(self):
        """Run the one-time claim flow: start the agent if needed, then run
        playit-cli's default ("auto") command, which - per the project's own
        documented behaviour - notices the agent is waiting for a secret,
        generates a claim code, prints a URL for the user, and once approved
        hands the resulting secret to the running agent over IPC."""
        with self._lock:
            if self._setup_active:
                return _res(False, "Setup is already in progress.", 409)
            if not is_installed():
                return _res(False, "Playit is not installed yet.", 400)
            if self.secret_configured():
                return _res(False, "Playit is already set up.", 409)

        if not self._daemon_alive():
            start_result = self.start()
            if not start_result["ok"]:
                return start_result
            time.sleep(1)

        with self._lock:
            self._setup_active = True
            self._setup_url = None
            self._setup_error = None
            self._setup_started = time.monotonic()

        command = [cli_path(), "--socket-path", socket_path()]
        try:
            import subprocess
            proc = subprocess.Popen(
                command, cwd=config.PLAYIT_RUNTIME_DIR,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                **os_manager.popen_kwargs(),
            )
        except OSError as exc:
            with self._lock:
                self._setup_active = False
                self._setup_error = "Could not launch the Playit setup process."
            self._panel(f"Failed to start Playit setup: {exc}")
            return _res(False, "Unable to start Playit setup.", 500)

        self._setup_proc = proc
        self._panel("Playit setup started - waiting for a claim link...")
        threading.Thread(target=self._setup_reader, args=(proc,), daemon=True,
                          name="playit-setup-reader").start()
        threading.Thread(target=self._setup_watchdog, args=(proc,), daemon=True,
                          name="playit-setup-watchdog").start()
        return _res(True, "Setting up Playit...")

    def _setup_reader(self, proc):
        try:
            for raw in proc.stdout:
                line = ANSI_RE.sub("", raw.rstrip("\r\n"))
                if not line.strip():
                    continue
                self._add("setup", line)
                match = _URL_RE.search(line)
                if match:
                    with self._lock:
                        self._setup_url = match.group(0)
                    self._panel("Setup link ready - open it to connect this agent.")
        except (ValueError, OSError):
            pass
        finally:
            try:
                code = proc.wait()
            except Exception:
                code = -1
            self._on_setup_exit(proc, code)

    def _on_setup_exit(self, proc, code):
        with self._lock:
            if proc is not self._setup_proc:
                return
            self._setup_proc = None
            self._setup_active = False
            if self.secret_configured():
                self._panel("Playit setup complete.")
            elif code not in (0, None):
                self._setup_error = "Playit setup did not complete. Check the log for details."
                self._panel(f"Playit setup exited without finishing (exit code {code}).")
            self._setup_url = None

    def _setup_watchdog(self, proc):
        timeout = max(10, int(config.PLAYIT_SETUP_TIMEOUT))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not os_manager.is_alive(proc):
                return
            if self.secret_configured():
                return
            time.sleep(1)
        if os_manager.is_alive(proc):
            self._panel(f"Setup timed out after {timeout}s. Cancelling.")
            os_manager.terminate(proc, force=False)
            if not proc_wait(proc, 5):
                os_manager.terminate(proc, force=True)
            with self._lock:
                self._setup_active = False
                self._setup_url = None
                if not self.secret_configured():
                    self._setup_error = "Setup timed out. The claim link was not opened in time."

    # ---------------------------------------------------------------- shutdown

    def shutdown(self):
        with self._lock:
            setup_proc = self._setup_proc
            daemon_proc = self._daemon_proc
            self._stopping = True
        try:
            if setup_proc is not None and os_manager.is_alive(setup_proc):
                os_manager.terminate(setup_proc, force=True)
            if daemon_proc is not None and os_manager.is_alive(daemon_proc):
                os_manager.terminate(daemon_proc, force=False)
                if not proc_wait(daemon_proc, int(config.PLAYIT_STOP_TIMEOUT)):
                    os_manager.terminate(daemon_proc, force=True)
        finally:
            with self._lock:
                self._stopping = False


def proc_wait(proc, timeout):
    """True if proc exits within timeout seconds, without needing the
    threading.Event bookkeeping the class above uses for its own processes."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not os_manager.is_alive(proc):
            return True
        time.sleep(0.2)
    return not os_manager.is_alive(proc)


# --------------------------------------------------------------------- singleton

_manager = None
_manager_lock = threading.Lock()


def get_manager():
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = PlayitManager()
        return _manager