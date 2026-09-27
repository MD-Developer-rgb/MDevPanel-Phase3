"""OS / environment abstraction layer for MDev Panel (Phase 2).

Everything that differs between Windows, plain Linux and Termux (Android)
lives here, so the rest of the app never checks platform.system() itself.
"""

import os
import platform
import shutil
import signal
import socket
import struct
import subprocess

WINDOWS = "WINDOWS"
LINUX = "LINUX"
TERMUX = "TERMUX"

try:  # POSIX only - used for LAN interface addresses and /proc scans
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None


def detect_environment():
    """Best-effort OS classification. Termux reports platform.system() == 'Linux',
    so it needs its own checks rather than relying on that alone."""
    system = platform.system()
    if system == "Windows":
        return WINDOWS
    if system == "Darwin":
        return LINUX  # treated like desktop Linux for process handling purposes
    if (
        os.environ.get("TERMUX_VERSION")
        or "com.termux" in os.environ.get("PREFIX", "")
        or os.path.isdir("/data/data/com.termux/files/usr")
    ):
        return TERMUX
    return LINUX


ENV = detect_environment()
IS_WINDOWS = ENV == WINDOWS
IS_TERMUX = ENV == TERMUX
IS_POSIX = ENV in (LINUX, TERMUX)


# ------------------------------------------------------------- CPU architecture

# Raw platform.machine() values -> playit's own arch naming. Deliberately does
# NOT guess across families (e.g. armv6l is left unmapped rather than treated
# as armv7) - an unmapped arch is reported as unsupported rather than run.
_ARCH_ALIASES = {
    "aarch64": "aarch64", "arm64": "aarch64",
    "x86_64": "amd64", "amd64": "amd64",
    "armv7l": "armv7", "armv7": "armv7",
    "i686": "i686", "i386": "i686", "x86": "i686",
}


def detect_arch():
    """Normalized CPU architecture (aarch64/amd64/armv7/i686), or the raw
    platform.machine() string if it isn't one of the known aliases."""
    machine = platform.machine().lower()
    return _ARCH_ALIASES.get(machine, machine)


def os_family():
    """'linux', 'windows' or 'mac' - independent of the Termux/Linux split
    above, which only concerns process handling, not binary selection."""
    system = platform.system()
    if system == "Windows":
        return "windows"
    if system == "Darwin":
        return "mac"
    return "linux"


def platform_key():
    """e.g. 'linux-amd64', 'linux-aarch64' - matches how third-party binary
    releases (such as Paper or Playit) usually name their platform assets."""
    return f"{os_family()}-{detect_arch()}"


# ------------------------------------------------------------------- Java

def find_java(configured_command):
    """Resolve the configured Java command/path to a runnable executable, or None."""
    command = str(configured_command)
    looks_like_path = ("/" in command) or ("\\" in command) or os.path.isabs(command)
    if looks_like_path:
        candidates = [command]
        if IS_WINDOWS and not command.lower().endswith(".exe"):
            candidates.append(command + ".exe")
        for candidate in candidates:
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
        return None
    return shutil.which(command)


# -------------------------------------------------------- process lifecycle

def popen_kwargs():
    """Extra Popen kwargs so a server process (a) survives this request and
    (b) can be signaled as a whole tree later, per OS."""
    if IS_WINDOWS:
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}  # own session == own process group (pgid == pid)


def is_alive(proc):
    return proc is not None and proc.poll() is None


def terminate(proc, force=False):
    """End a server process using Python's process APIs (no taskkill/pkill shell-out
    on the paths that run on every platform)."""
    if proc is None:
        return
    if IS_WINDOWS:
        try:
            proc.kill() if force else proc.terminate()  # both map to TerminateProcess on Windows
        except OSError:
            pass
        return
    sig = signal.SIGKILL if force else signal.SIGTERM
    try:
        os.killpg(proc.pid, sig)  # process was started with start_new_session=True, so pgid == pid
    except (OSError, AttributeError):
        try:
            proc.send_signal(sig)
        except OSError:
            pass


# ------------------------------------------------------------------- network

LAN_IFACE_PREFIXES = ("wlan", "swlan", "ap", "eth", "rndis", "usb", "br", "en", "wi")


def port_available(port):
    """True if nothing is listening on the TCP port. Works the same on every OS."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if not IS_WINDOWS:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", int(port)))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _valid_lan_ip(ip):
    import ipaddress
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return addr.version == 4 and addr.is_private and not addr.is_loopback and not addr.is_link_local


def _iface_ipv4(name):
    if fcntl is None:
        return None
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        packed = fcntl.ioctl(sock.fileno(), 0x8915, struct.pack("256s", name.encode()[:15]))  # SIOCGIFADDR
        return socket.inet_ntoa(packed[20:24])
    except OSError:
        return None
    finally:
        sock.close()


def detect_lan_ip():
    """Best-effort LAN IPv4 address, without sending any traffic where possible."""
    try:
        names = [n for _, n in socket.if_nameindex()]
    except (OSError, AttributeError):
        names = []
    ranked = []
    for name in names:
        for rank, prefix in enumerate(LAN_IFACE_PREFIXES):
            if name.lower().startswith(prefix):
                ranked.append((rank, name))
                break
    for _, name in sorted(ranked):
        ip = _iface_ipv4(name)
        if ip and _valid_lan_ip(ip):
            return ip
    for target in ("192.168.0.1", "10.0.0.1"):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.settimeout(0.5)
            sock.connect((target, 9))  # UDP connect: selects a route, sends nothing
            ip = sock.getsockname()[0]
            if _valid_lan_ip(ip):
                return ip
        except OSError:
            pass
        finally:
            sock.close()
    return None


# --------------------------------------------------------- external process scan

def find_process_using_jar(jar_name, instance_dir, exclude_pids):
    """PID of a Java process running jar_name from instance_dir, not started by us.
    Best-effort: only implemented where /proc is available (Linux/Termux)."""
    if not IS_POSIX or not os.path.isdir("/proc"):
        return None
    try:
        entries = os.listdir("/proc")
    except OSError:
        return None
    target_dir = os.path.realpath(instance_dir)
    for entry in entries:
        if not entry.isdigit() or int(entry) in exclude_pids:
            continue
        try:
            with open(f"/proc/{entry}/cmdline", "rb") as fh:
                args = fh.read().split(b"\0")
        except OSError:
            continue
        args = [a.decode("utf-8", "replace") for a in args if a]
        if len(args) < 3 or "java" not in os.path.basename(args[0]).lower() or "-jar" not in args:
            continue
        idx = args.index("-jar")
        jar_arg = args[idx + 1] if idx + 1 < len(args) else ""
        if os.path.basename(jar_arg) != jar_name:
            continue
        try:
            same_dir = os.path.realpath(os.readlink(f"/proc/{entry}/cwd")) == target_dir
        except OSError:
            same_dir = os.path.isabs(jar_arg) and os.path.realpath(jar_arg) == os.path.realpath(
                os.path.join(instance_dir, jar_name))
        if same_dir:
            return int(entry)
    return None
