"""Per-instance Minecraft server process manager (Phase 2).

Each instance gets its own InstanceServer: its own process, console buffer,
uptime and status. Starting or stopping one instance can never affect
another - there is no shared/global process state.
"""

import collections
import os
import re
import threading
import time
from datetime import datetime

import config
import instance_manager
import os_manager

OFFLINE = "OFFLINE"
STARTING = "STARTING"
ONLINE = "ONLINE"
STOPPING = "STOPPING"
CRASHED = "CRASHED"
ERROR = "ERROR"

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
DONE_RE = re.compile(r'Done \([^)]*\)! For help, type "help"')
MC_START_RE = re.compile(r"Starting minecraft server version (\S+)", re.I)
RUNNING_RE = re.compile(r"This server is running (\S+) version (.+)")
MC_PAREN_RE = re.compile(r"\(MC: ([^)]+)\)")
MC_LEADING_RE = re.compile(r"(\d+\.\d+(?:\.\d+)?)")
PLAYER_LIST_RE = re.compile(r"There are (\d+) of a max(?:imum)? of (\d+) players online", re.I)

RAM_RE = re.compile(r"^\d+[MG]$", re.I)

ERROR_HINTS = (
    (re.compile(r"You need to agree to the EULA", re.I),
     "You need to accept the Minecraft EULA. Set eula=true in eula.txt, then start again."),
    (re.compile(r"FAILED TO BIND|Address already in use", re.I),
     "Minecraft could not open its port. Another program may be using it."),
    (re.compile(r"UnsupportedClassVersionError|class file version"),
     "This server JAR needs a newer Java version."),
    (re.compile(r"OutOfMemoryError|Could not reserve enough space"),
     "Java ran out of memory. Lower the maximum RAM in this instance's Settings."),
    (re.compile(r"Unable to access jarfile|Invalid or corrupt jarfile"),
     "The server JAR could not be opened. Check that it is a valid server JAR."),
)


def validate_ram(value, label="RAM"):
    value = (value or "").strip().upper()
    if not RAM_RE.match(value):
        raise ValueError(f'{label} must look like 512M, 1G or 4G.')
    return value


def _res(ok, message, http=200):
    return {"ok": ok, "message": message, "http": http}


class InstanceServer:
    def __init__(self, name):
        self.name = name

        self._lock = threading.RLock()
        self._cond = threading.Condition()

        self._state = OFFLINE
        self._proc = None
        self._exited = threading.Event()
        self._exited.set()
        self._started_mono = None
        self._stop_requested = False
        self._restarting = False
        self._shutting_down = False
        self._last_error = None
        self._pending_error = None

        self._software = None
        self._mc_version = None
        self._players_online = None
        self._players_max = None
        self._last_poll = 0.0

        self._buf = collections.deque(maxlen=max(100, int(config.CONSOLE_BUFFER_LINES)))
        self._next_id = 1

        self._env_cache = (0.0, None)
        self._java_cache = (0.0, None)
        self._ext_cache = (0.0, None)

        self._load_history()

    # ------------------------------------------------------------------ paths

    @property
    def dir(self):
        return instance_manager.instance_dir(self.name)

    def jar(self):
        return instance_manager.jar_name(self.name) or config.DEFAULT_SERVER_JAR

    def jar_path(self):
        return os.path.join(self.dir, self.jar())

    def latest_log_path(self):
        path = os.path.join(self.dir, "logs", "latest.log")
        return path if os.path.isfile(path) else None

    def ram(self):
        meta = instance_manager.load_metadata(self.name)
        return meta.get("ram_min") or config.DEFAULT_RAM_MIN, meta.get("ram_max") or config.DEFAULT_RAM_MAX

    def set_ram(self, ram_min, ram_max):
        ram_min = validate_ram(ram_min, "Minimum RAM")
        ram_max = validate_ram(ram_max, "Maximum RAM")
        instance_manager.save_metadata(self.name, ram_min=ram_min, ram_max=ram_max)

    # ------------------------------------------------------------ server.properties

    def port(self):
        path = os.path.join(self.dir, "server.properties")
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    if key.strip() == "server-port":
                        value = value.strip()
                        if value.isdigit() and 1 <= int(value) <= 65535:
                            return int(value), "server.properties"
                        break
        except OSError:
            pass
        return int(config.DEFAULT_SERVER_PORT), "default"

    def max_players(self):
        path = os.path.join(self.dir, "server.properties")
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if line.strip().startswith("max-players="):
                        value = line.split("=", 1)[1].strip()
                        return int(value) if value.isdigit() else None
        except OSError:
            pass
        return None

    # ------------------------------------------------------------------ Java

    def java_info(self, force=False):
        now = time.time()
        ts, cached = self._java_cache
        if not force and cached is not None and now - ts < 60:
            return cached
        info = self._detect_java()
        self._java_cache = (time.time(), info)
        return info

    @staticmethod
    def _detect_java():
        path = os_manager.find_java(config.JAVA_COMMAND)
        if not path:
            return {"ok": False, "path": None, "display": None, "error": "Java was not found. Install Java and check JAVA_COMMAND in config.py."}
        try:
            import subprocess
            out = subprocess.run([path, "-version"], capture_output=True, text=True, timeout=20, errors="replace")
        except (OSError, Exception):
            return {"ok": False, "path": path, "display": None, "error": "Java was found but could not be executed."}
        text = (out.stderr or out.stdout or "").strip()
        if out.returncode != 0 or not text:
            return {"ok": False, "path": path, "display": None, "error": "Java was found but could not be executed."}
        first = text.splitlines()[0]
        match = re.search(r'(\S+) version "([^"]+)"', first)
        if not match:
            return {"ok": True, "path": path, "display": first[:60], "error": None}
        vendor = "OpenJDK" if match.group(1).lower().startswith("openjdk") else "Java"
        parts = match.group(2).split(".")
        major = parts[1] if match.group(2).startswith("1.") and len(parts) > 1 else parts[0]
        major = re.match(r"\d+", major)
        major = major.group(0) if major else match.group(2)
        return {"ok": True, "path": path, "display": f"{vendor} {major}", "error": None}

    # ------------------------------------------------------------ environment

    def environment_check(self, force=False):
        now = time.time()
        ts, cached = self._env_cache
        if not force and cached is not None and now - ts < 3:
            return cached
        checks = self._run_checks()
        self._env_cache = (time.time(), checks)
        return checks

    def _run_checks(self):
        checks = []

        def add(cid, ok, title, detail="", blocking=True):
            checks.append({"id": cid, "ok": ok, "title": title, "detail": detail,
                           "level": "error" if blocking else "warning", "blocking": blocking})

        directory = self.dir
        port, _ = self.port()

        dir_ok = os.path.isdir(directory)
        add("dir", dir_ok, "⚠️ Instance folder not found", f"Expected:\n {directory}")

        if dir_ok:
            jar = instance_manager.jar_name(self.name)
            add("jar", jar is not None, f"⚠️ No server JAR found",
                f"Expected {config.DEFAULT_SERVER_JAR} inside:\n {directory}")

        java = self.java_info()
        add("java", java["ok"], "❌ " + (java["error"] or "Java problem"),
            "Install Java, or set JAVA_COMMAND in config.py." if not java["ok"] else "")

        running = self._alive()
        external = None if running else self._find_external()
        add("external", external is None,
            "⚠️ A Minecraft server is already running outside this panel for this instance",
            f"Found a Java process (PID {external}) already running this instance's JAR.\n"
            "Stop it first, or the panel cannot control it." if external else "")

        port_ok = True if (running or external is not None) else os_manager.port_available(port)
        add("port", port_ok, f"❌ Minecraft port {port} is already in use.",
            "Another program is using this port. Stop it, or change server-port in server.properties." if not port_ok else "")

        if dir_ok:
            eula_ok, detail = True, ""
            eula_path = os.path.join(directory, "eula.txt")
            try:
                with open(eula_path, "r", encoding="utf-8", errors="replace") as fh:
                    match = re.search(r"^\s*eula\s*=\s*(\w+)", fh.read(), re.M | re.I)
                if not match or match.group(1).lower() != "true":
                    eula_ok = False
                    detail = "eula.txt says eula=false. Read the Minecraft EULA (https://aka.ms/MinecraftEULA), then set eula=true."
            except OSError:
                eula_ok = False
                detail = ("There is no eula.txt yet. The first start creates it and then stops. "
                          "Open it, read the EULA, set eula=true and start again.")
            add("eula", eula_ok, "⚠️ EULA not accepted yet", detail, blocking=False)

        return checks

    def _find_external(self):
        now = time.time()
        ts, cached = self._ext_cache
        if now - ts < 3:
            return cached
        exclude = {os.getpid()}
        if self._proc is not None:
            exclude.add(self._proc.pid)
        found = os_manager.find_process_using_jar(self.jar(), self.dir, exclude)
        self._ext_cache = (time.time(), found)
        return found

    # ------------------------------------------------------------------ status

    def _alive(self):
        return os_manager.is_alive(self._proc)

    def snapshot(self):
        with self._lock:
            alive = self._alive()
            state = self._state
            uptime = int(time.monotonic() - self._started_mono) if alive and self._started_mono else None
            data = {
                "ok": True,
                "name": self.name,
                "state": state,
                "running": alive,
                "restarting": self._restarting,
                "message": self._last_error if state in (ERROR, CRASHED) else None,
                "uptime_seconds": uptime,
                "software": self._software or "Unknown",
                "minecraft_version": self._mc_version or "Unknown",
                "players_online": self._players_online,
            }
        checks = self.environment_check()
        java = self.java_info()
        port, port_source = self.port()
        ram_min, ram_max = self.ram()
        data.update({
            "server_time": time.time(),
            "instance": {
                "dir": self.dir,
                "jar": self.jar(),
                "jar_exists": os.path.isfile(self.jar_path()),
            },
            "java": {"ok": java["ok"], "display": java["display"], "error": java["error"]},
            "port": port,
            "port_source": port_source,
            "max_players": self.max_players() or self._players_max,
            "ram": {"min": ram_min, "max": ram_max},
            "problems": [c for c in checks if not c["ok"]],
            "ready": all(c["ok"] for c in checks if c["blocking"]),
        })
        return data

    # ----------------------------------------------------------------- console

    def _add(self, src, text):
        with self._cond:
            self._buf.append({"id": self._next_id, "s": src, "t": text})
            self._next_id += 1
            self._cond.notify_all()

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

    def console_text(self):
        with self._cond:
            return "\n".join(e["t"] for e in self._buf) + "\n"

    def clear_console(self):
        with self._cond:
            self._buf.clear()

    def _load_history(self):
        path = self.latest_log_path()
        if not path:
            return
        try:
            with open(path, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                fh.seek(max(0, size - 65536))
                data = fh.read().decode("utf-8", "replace")
        except OSError:
            return
        lines = data.splitlines()
        if size > 65536:
            lines = lines[1:]
        lines = [ANSI_RE.sub("", ln) for ln in lines if ln.strip()][-int(config.HISTORY_TAIL_LINES):]
        if not lines:
            return
        self._panel("Showing the end of logs/latest.log from the previous run.")
        for line in lines:
            self._parse_info(line)
            self._add("history", line)

    def _parse_info(self, line):
        match = MC_START_RE.search(line)
        if match:
            self._mc_version = match.group(1)
        match = RUNNING_RE.search(line)
        if match:
            name, rest = match.group(1), match.group(2)
            if name == "CraftBukkit" and "Spigot" in rest:
                name = "Spigot"
            self._software = name
            paren = MC_PAREN_RE.search(rest)
            if paren:
                self._mc_version = paren.group(1)
            elif not self._mc_version:
                lead = MC_LEADING_RE.match(rest)
                if lead:
                    self._mc_version = lead.group(1)
        match = PLAYER_LIST_RE.search(line)
        if match:
            self._players_online = int(match.group(1))
            self._players_max = int(match.group(2))

    # ------------------------------------------------------------------- start

    def start(self):
        with self._lock:
            if self._restarting:
                return _res(False, "⚠️ A restart is already in progress.", 409)
            if self._alive():
                if self._state == STOPPING:
                    return _res(False, "⚠️ Server is stopping. Wait until it is offline.", 409)
                return _res(False, "⚠️ Server is already running.", 409)
            return self._start_locked()

    def _start_locked(self):
        checks = self.environment_check(force=True)
        blocking = [c for c in checks if not c["ok"] and c["blocking"]]
        if blocking:
            return _res(False, blocking[0]["title"], 400)

        java = self.java_info()
        ram_min, ram_max = self.ram()
        command = [java["path"], f"-Xms{ram_min}", f"-Xmx{ram_max}", "-jar", self.jar()] \
            + [str(a) for a in config.SERVER_ARGS]
        try:
            import subprocess
            proc = subprocess.Popen(
                command,
                cwd=self.dir,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                **os_manager.popen_kwargs(),
            )
        except OSError:
            self._state = ERROR
            self._last_error = "Minecraft failed to start."
            self._panel("Could not launch Java. Check JAVA_COMMAND in config.py.")
            return _res(False, "❌ Minecraft failed to start.", 500)

        self._proc = proc
        self._exited = threading.Event()
        self._started_mono = time.monotonic()
        self._stop_requested = False
        self._last_error = None
        self._pending_error = None
        self._players_online = None
        self._state = STARTING
        self._panel("Starting: " + " ".join([os.path.basename(command[0])] + command[1:]))
        threading.Thread(target=self._reader, args=(proc, self._exited), name=f"mc-reader-{self.name}", daemon=True).start()
        threading.Thread(target=self._player_poll_loop, args=(proc,), name=f"mc-poll-{self.name}", daemon=True).start()
        return _res(True, "Starting server...")

    def _reader(self, proc, exited):
        try:
            for raw in proc.stdout:
                line = ANSI_RE.sub("", raw.rstrip("\r\n")).replace("\r", "")
                if not line.strip():
                    continue
                self._add("server", line)
                self._inspect(proc, line)
        except (ValueError, OSError):
            pass
        finally:
            try:
                code = proc.wait()
            except Exception:
                code = -1
            self._on_exit(proc, code, exited)

    def _inspect(self, proc, line):
        self._parse_info(line)
        with self._lock:
            if proc is not self._proc or self._state != STARTING:
                return
            if DONE_RE.search(line):
                self._state = ONLINE
                self._restarting = False
                return
            if not self._pending_error:
                for pattern, message in ERROR_HINTS:
                    if pattern.search(line):
                        self._pending_error = message
                        break

    def _player_poll_loop(self, proc):
        """While this process is ONLINE, periodically ask for the player list
        so the dashboard has a real online count (server.properties only gives
        the configured maximum, not who is actually connected)."""
        interval = max(3, int(config.PLAYER_POLL_SECONDS))
        while os_manager.is_alive(proc):
            time.sleep(interval)
            with self._lock:
                if proc is not self._proc or self._state != ONLINE:
                    return
            self._write_stdin("list")

    def _on_exit(self, proc, code, exited):
        with self._lock:
            if proc is not self._proc:
                exited.set()
                return
            previous = self._state
            self._proc = None
            self._started_mono = None
            self._players_online = None
            if self._stop_requested or previous == STOPPING:
                self._state = OFFLINE
                self._panel("Server stopped.")
            elif previous == STARTING:
                self._state = ERROR
                self._restarting = False
                self._last_error = self._pending_error or "Minecraft failed to start."
                self._panel(f"Server exited during startup (exit code {code}).")
            elif code == 0:
                self._state = OFFLINE
                self._panel("Server stopped.")
            else:
                self._state = CRASHED
                self._restarting = False
                self._last_error = f"Minecraft stopped unexpectedly (exit code {code})."
                self._panel(f"Server crashed (exit code {code}).")
        exited.set()

    # -------------------------------------------------------------------- stop

    def _write_stdin(self, text):
        proc = self._proc
        if proc is None or proc.stdin is None:
            return False
        try:
            proc.stdin.write(text + "\n")
            proc.stdin.flush()
            return True
        except (OSError, ValueError):
            return False

    def _begin_stop_locked(self):
        proc, exited = self._proc, self._exited
        self._stop_requested = True
        self._state = STOPPING
        self._panel("Stopping server...")
        if not self._write_stdin("stop"):
            self._panel("Could not send 'stop' to the server. Forcing it to close.")
            threading.Thread(target=self._terminate, args=(proc, exited), daemon=True).start()
            return
        threading.Thread(target=self._stop_watchdog, args=(proc, exited), name=f"mc-watchdog-{self.name}", daemon=True).start()

    def _stop_watchdog(self, proc, exited):
        timeout = max(1, int(config.STOP_TIMEOUT))
        if exited.wait(timeout):
            return
        if not config.FORCE_KILL_ON_TIMEOUT:
            self._panel(f"Server is still stopping after {timeout}s. Use Force stop if it is stuck.")
            return
        self._panel(f"Server did not stop within {timeout}s. Forcing it to close.")
        self._terminate(proc, exited)

    def _terminate(self, proc, exited):
        os_manager.terminate(proc, force=False)
        if exited.wait(max(1, int(config.KILL_GRACE))):
            return
        self._panel("Server is still running. Killing the process.")
        os_manager.terminate(proc, force=True)
        exited.wait(5)

    def stop(self):
        with self._lock:
            if not self._alive():
                return _res(False, "⚠️ Server is not running.", 409)
            if self._state == STOPPING:
                return _res(True, "Stopping server...")
            self._restarting = False
            self._begin_stop_locked()
            return _res(True, "Stopping server...")

    def force_stop(self):
        with self._lock:
            if not self._alive():
                return _res(False, "⚠️ Server is not running.", 409)
            proc, exited = self._proc, self._exited
            self._restarting = False
            self._stop_requested = True
            self._state = STOPPING
            self._panel("Force stop requested.")
        threading.Thread(target=self._terminate, args=(proc, exited), name=f"mc-force-{self.name}", daemon=True).start()
        return _res(True, "Force stopping server...")

    # ----------------------------------------------------------------- restart

    def restart(self):
        with self._lock:
            if self._restarting:
                return _res(False, "⚠️ A restart is already in progress.", 409)
            if not self._alive():
                return _res(False, "⚠️ Server is not running. Use Start instead.", 409)
            if self._state == STOPPING:
                return _res(False, "⚠️ Server is already stopping.", 409)
            self._restarting = True
            exited = self._exited
            self._begin_stop_locked()
        threading.Thread(target=self._restart_worker, args=(exited,), name=f"mc-restart-{self.name}", daemon=True).start()
        return _res(True, "Restarting server...")

    def _restart_worker(self, exited):
        limit = int(config.STOP_TIMEOUT) + int(config.KILL_GRACE) + 20
        if not exited.wait(limit):
            with self._lock:
                self._restarting = False
                self._panel("Restart cancelled: the server did not stop in time.")
            return
        time.sleep(1)
        deadline = time.time() + 15
        port, _ = self.port()
        while time.time() < deadline and not os_manager.port_available(port):
            time.sleep(1)
        with self._lock:
            if not self._restarting or self._shutting_down:
                return
            self._panel("Restarting server...")
            result = self._start_locked()
            if not result["ok"]:
                self._restarting = False
                self._state = ERROR
                self._last_error = result["message"].lstrip("⚠️❌ ").strip() or "Minecraft failed to start."

    # ---------------------------------------------------------------- commands

    def send_command(self, text):
        cmd = (text or "").strip().lstrip("/").strip()
        if not cmd:
            return _res(False, "Enter a command.", 400)
        if len(cmd) > 1000 or any(ord(c) < 32 or ord(c) == 127 for c in cmd):
            return _res(False, "That command contains characters the console cannot send.", 400)
        with self._lock:
            if not self._alive() or self._state == STOPPING:
                return _res(False, "⚠️ Server is not running.", 409)
            if cmd.lower() == "stop":
                self._add("cmd", "> stop")
                return self.stop()
            if not self._write_stdin(cmd):
                return _res(False, "❌ Could not send the command to the server.", 500)
            self._add("cmd", "> " + cmd)
        return _res(True, "Command sent.")

    # ---------------------------------------------------------------- shutdown

    def shutdown(self):
        with self._lock:
            if self._shutting_down:
                return
            self._shutting_down = True
            if not self._alive():
                return
            proc, exited = self._proc, self._exited
            self._restarting = False
            if not self._stop_requested:
                self._begin_stop_locked()
        if not exited.wait(int(config.STOP_TIMEOUT) + int(config.KILL_GRACE) + 10):
            self._terminate(proc, exited)


# --------------------------------------------------------------------- registry

_instances = {}
_registry_lock = threading.RLock()


def get(name):
    """The single InstanceServer for this instance name, created on first use."""
    name = instance_manager.validate_name(name)
    with _registry_lock:
        server = _instances.get(name)
        if server is None:
            server = InstanceServer(name)
            _instances[name] = server
        return server


def running_names():
    with _registry_lock:
        return [name for name, server in _instances.items() if server._alive()]


def forget(name):
    """Drop a stopped instance's in-memory state (e.g. after it's deleted)."""
    with _registry_lock:
        _instances.pop(name, None)


def shutdown_all():
    with _registry_lock:
        servers = list(_instances.values())
    running = [s for s in servers if s._alive()]
    if running:
        names = ", ".join(s.name for s in running)
        print(f"Stopping running instance(s) before exiting: {names} (this can take a moment)...", flush=True)
    for server in servers:
        server.shutdown()
