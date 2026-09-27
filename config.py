"""MDev Panel - configuration (Phase 2).

Edit the values below, then restart the panel. Nothing in the web UI can
change these settings, and the web UI can never supply a path or an
executable: every path used at runtime is built from INSTANCES_DIR and a
name validated by instance_manager.py.
"""

# --- Instances --------------------------------------------------------------

# Folder that holds one subfolder per Minecraft server ("instance").
# You place manually-created servers here yourself; New Instance creates one
# for you (downloading Paper into a new subfolder here).
INSTANCES_DIR = "/storage/emulated/0/MinecraftServer"

# JAR name every instance created by the panel uses. Detection also accepts
# other .jar files for instances you set up by hand (see instance_manager.py).
DEFAULT_SERVER_JAR = "server.jar"

# Java executable. "java" works if Java is on your PATH (Termux: pkg install
# openjdk-21). You can also give a full path.
JAVA_COMMAND = "java"

# Arguments placed after the JAR name for every instance.
SERVER_ARGS = ["nogui"]

# Default RAM for newly created instances. Each instance can change its own
# afterwards from Settings; existing instances keep whatever they were set to.
DEFAULT_RAM_MIN = "1G"
DEFAULT_RAM_MAX = "2G"

# Port shown when an instance's server.properties does not exist yet.
DEFAULT_SERVER_PORT = 25565

# --- Stopping ----------------------------------------------------------------

STOP_TIMEOUT = 60          # seconds to wait for a graceful "stop" per instance
FORCE_KILL_ON_TIMEOUT = True
KILL_GRACE = 15

# --- Panel web server ----------------------------------------------------------

PANEL_HOST = "0.0.0.0"     # 0.0.0.0 = reachable from other devices on your Wi-Fi
PANEL_PORT = 5000          # this is the PANEL port, never the Minecraft port

# --- Console -------------------------------------------------------------------

CONSOLE_BUFFER_LINES = 2000
HISTORY_TAIL_LINES = 200

# How often (seconds) the panel asks an ONLINE instance for its player list.
PLAYER_POLL_SECONDS = 10

# --- Network display -------------------------------------------------------

LAN_IP_OVERRIDE = None     # e.g. "192.168.1.25" if auto-detection fails

# --- Paper API ---------------------------------------------------------------

PAPER_API_BASE = "https://api.papermc.io/v2"
PAPER_API_TIMEOUT = 15      # seconds, per HTTP request (not per whole download)

# --- Panel-side storage ------------------------------------------------------

# Per-instance settings (RAM, etc.) the panel itself keeps track of, stored
# next to the panel code - never inside an instance folder.
DATA_DIR = "data"

# --- Playit tunnel integration ------------------------------------------------
#
# MDevPanel downloads and runs its OWN copy of the official Playit binaries -
# it never uses a `playit` already on PATH or installed system-wide, even if
# one exists. See playit_manager.py.
#
# Official releases : https://github.com/playit-cloud/playit-agent/releases
# Official docs     : https://playit.gg/support/

import os as _os

PLAYIT_ENABLED = True

# A specific version string (e.g. "1.0.10"), or "latest" to resolve the
# newest STABLE release from GitHub at install time (GitHub's own "latest"
# release endpoint already excludes drafts and pre-releases, so this can
# never silently pick a prerelease). If you pin a specific version here that
# turns out to be a prerelease, installation refuses unless you also set
# PLAYIT_ALLOW_PRERELEASE = True.
PLAYIT_VERSION = "1.0.10"
PLAYIT_ALLOW_PRERELEASE = False

BASE_DIR = _os.path.dirname(_os.path.abspath(__file__))
# IMPORTANT (Android/Termux): this must resolve to somewhere on Termux's own
# filesystem (e.g. MDevPanel kept in ~/MDevPanel), NOT under shared storage
# such as /storage/emulated/0. Shared storage cannot mark a downloaded file
# as executable, so Playit installation will fail there even though the
# download itself succeeds. Your Minecraft INSTANCES_DIR is unaffected by
# this - only the panel's own folder (and therefore PLAYIT_DIR) needs to be
# on Termux's private storage.
PLAYIT_DIR = _os.path.join(BASE_DIR, "data", "playit")
PLAYIT_BIN_DIR = _os.path.join(PLAYIT_DIR, "bin")
PLAYIT_CONFIG_DIR = _os.path.join(PLAYIT_DIR, "config")
PLAYIT_LOG_DIR = _os.path.join(PLAYIT_DIR, "logs")
PLAYIT_RUNTIME_DIR = _os.path.join(PLAYIT_DIR, "runtime")

# Official GitHub Releases only - never a mirror.
PLAYIT_RELEASES_BASE = "https://github.com/playit-cloud/playit-agent/releases/download"
PLAYIT_API_BASE = "https://api.github.com/repos/playit-cloud/playit-agent"

# The panel needs two official binaries: the agent/daemon ("playit") that
# actually runs the tunnel, and the CLI ("playit-cli") used only to run the
# one-time account setup/claim flow. Add a platform key here (and to both
# dicts) to support another OS/CPU combination - nothing else needs to
# change. Only Linux/Termux architectures are populated by default per this
# phase's scope; Windows/macOS asset names can be added the same way.
PLAYIT_AGENT_BINARIES = {
    "linux-aarch64": "playit-linux-aarch64",
    "linux-amd64":   "playit-linux-amd64",
    "linux-armv7":   "playit-linux-armv7",
    "linux-i686":    "playit-linux-i686",
}
PLAYIT_CLI_BINARIES = {
    "linux-aarch64": "playit-cli-linux-aarch64",
    "linux-amd64":   "playit-cli-linux-amd64",
    "linux-armv7":   "playit-cli-linux-armv7",
    "linux-i686":    "playit-cli-linux-i686",
}

# Known-good sha256 checksums, copied from the official release page, keyed
# by version then asset file name. When the configured version/asset isn't
# listed here the download still proceeds (nothing else provided a checksum
# to check it against), but the UI says so rather than pretending it verified
# something it didn't.
PLAYIT_CHECKSUMS = {
    "1.0.10": {
        "playit-linux-aarch64": "4c0db3e7b3a8158e249441c2f0b73f54e83429395890c7b1ca45fd7a6303d763",
        "playit-linux-amd64": "2df7d9f10227ab312b1ad341853db4e8a8243df5cfcdbae58713a4271711c339",
        "playit-linux-armv7": "92ec60988b1246e07ac090c663128bd04bdc0d7ff388db520e1ff7bb4e5003e0",
        "playit-linux-i686": "d7215f3995e486bc231b3b542aa5f1ac6b0d604f8dae97bb14a9a64b49b3ed50",
        "playit-cli-linux-aarch64": "b126b4164c03838598c8f33f209d76f6acf1c257d07900c0af2d461b9647099f",
        "playit-cli-linux-amd64": "6fd54d147ae1d3232b22c1c1f4aa3d13cf16d889e840ca2d3f90b4f50a2e7301",
        "playit-cli-linux-armv7": "2e1140a838b42f00233065432ed36fbfe8af34e9aa22585bcb2e01fcdad282a6",
        "playit-cli-linux-i686": "e8e4bd663d0781e3d168be2a4e45d3642a38bc7946f507ba6116e8687b8a678f",
    },
}

# CLI flag playit-manager passes to both binaries to point them at MDevPanel's
# own private secret file instead of the OS-default location (observed as
# `secret_path=...` in the agent's own startup log). If a future Playit
# release renames this flag, change it here only.
PLAYIT_SECRET_PATH_FLAG = "--secret_path"

PLAYIT_STOP_TIMEOUT = 15
PLAYIT_KILL_GRACE = 5
PLAYIT_LOG_BUFFER_LINES = 2000

# How long the panel waits, after the setup flow starts, for the user to open
# the claim link and approve it before giving up.
PLAYIT_SETUP_TIMEOUT = 300
