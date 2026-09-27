# MDev Panel - Phase 2

The panel is now an **instance manager**: it can run several independent
Minecraft servers ("instances"), each in its own folder, with its own
process, console, RAM and `server.properties`. It can also create a new
instance for you by downloading Paper directly from the official PaperMC API.

Phase 1's Start/Stop/Restart/Console still work exactly as before - they now
apply to whichever instance you have open, instead of a single fixed server.

---

## One-time setup

```bash
pip install -r requirements.txt   # Flask + requests
```

Java still needs to be installed separately (`pkg install openjdk-21` on
Termux) and reachable as `JAVA_COMMAND` in `config.py`.

## 1. Where instances live

```python
# config.py
INSTANCES_DIR = "/storage/emulated/0/MinecraftServer"
```

Every subfolder directly inside `INSTANCES_DIR` that contains a server JAR is
an instance:

```
MinecraftServer/
├── Survival/server.jar      → instance "Survival"
├── SMP/server.jar           → instance "SMP"
├── RandomFolder/notes.txt   → ignored (no JAR)
```

You can drop an existing server folder in here by hand - the panel detects
`server.jar` first, and falls back to a single other `*.jar` file if that's
all a folder has. A folder with more than one `.jar` and no `server.jar` is
left alone (ambiguous) rather than guessed at.

The panel's own code and data (`config.py`, `data/`, etc.) live in a separate
folder from `INSTANCES_DIR` and are never touched by instance scanning.

## 2. Instances page

Opening the panel now shows **Instances**: a card per detected server with its
status, Minecraft version, online/max players, RAM and port. **Open** switches
the whole panel (Dashboard / Console / Settings) to that instance; **←
Instances** always brings you back.

## 3. Creating a new instance

**+ New Instance** → pick a name and a Minecraft version. Versions and builds
are fetched live from the official Paper API (`api.papermc.io`) - nothing is
hardcoded and no third-party download site is used. Leaving Build as
"Recommended" uses the latest build for that version.

The panel then creates `INSTANCES_DIR/<name>/`, downloads the chosen Paper
build straight into it as `server.jar`, and verifies it against Paper's own
checksum. Progress is shown live; the Flask UI stays responsive because the
download runs in a background thread.

Two things are always rejected before anything is created: a name that isn't a
plain folder name (no `/`, `..`, absolute paths, etc.), and a name that
already exists on disk - **an existing instance's files are never touched or
overwritten**, download or no download.

## 4. Instance dashboard, console, settings

Once you open an instance the top bar shows **Instance: `<name>`** and three
tabs:

- **Dashboard** - status, players (online from the running server, max from
  `server.properties`), Minecraft version, Java, port, RAM, uptime, and the
  same Start / Restart / Stop / Force stop controls as Phase 1.
- **Console** - the same live console as Phase 1 (pause, search, copy,
  download, command history), but scoped to this instance only. Commands you
  send here only reach this instance's process.
- **Settings** - RAM (`Xms`/`Xmx`, e.g. `1G` / `3G`) and a `server.properties`
  editor: common fields (port, max players, difficulty, gamemode, PVP, MOTD,
  whitelist, etc.) get friendly controls, and an **Advanced** box holds
  everything else. Comments and settings the friendly editor doesn't know
  about are preserved exactly - saving never deletes anything you didn't
  change.

Every instance keeps its own process, console buffer, RAM and
`server.properties`. Starting, stopping or sending a command to one instance
never touches another, even if several are online at once.

### EULA

Like Phase 1, the panel never accepts the Minecraft EULA for you. A freshly
created instance will show **⚠️ EULA not accepted yet** until you open
`eula.txt` in that instance's folder, read it, and set `eula=true` yourself.

## 5. Works on Windows, Linux and Termux

`os_manager.py` detects the environment (`WINDOWS`, `LINUX`, or `TERMUX` -
Termux is checked for explicitly, since it otherwise looks like plain Linux)
and uses that to decide how to launch and stop server processes, so the same
panel code runs on a desktop or in Termux without Linux-only commands leaking
into the Windows path or vice versa. Process start/stop uses Python's own
`subprocess`/process APIs on every platform - no `taskkill`/`pkill` shell-outs
in the normal path.

## Security (unchanged from Phase 1, extended to instances)

- No login yet. Anyone who can reach the panel's port can control every
  instance. Keep it on a trusted network, or set `PANEL_HOST = "127.0.0.1"`.
- The web UI can start/stop/restart an instance and send it Minecraft console
  commands - nothing else. There is no endpoint that runs an arbitrary OS
  command, and every instance path is validated to stay inside
  `INSTANCES_DIR` before it's ever used.
- The Paper API client never trusts a file name it's given over the network
  without checking it's a plain, local `.jar` name first.

## What's still not in Phase 2

File manager, file upload/download, plugin manager, Modrinth integration,
backups, player inventory/statistics, tunnels, advanced permissions/auth, and
deep monitoring are all left for later phases, as before.

## Project layout

```
app.py                   Flask routes: instances list, creation, per-instance API
config.py                All settings
os_manager.py            Windows / Linux / Termux environment + process abstraction
paper_api.py             Official PaperMC API client (versions, builds, download)
instance_manager.py      Instance discovery, name validation, per-instance metadata
properties_manager.py    server.properties reader/writer (preserves comments)
server_manager.py        Per-instance process manager + registry (InstanceServer)
templates/                instances.html, new_instance.html, dashboard.html,
                          console.html, settings.html, base.html
static/css/style.css
static/js/               instances.js, new_instance.js, dashboard.js,
                          console.js, settings.js
data/instances/          per-instance RAM settings (JSON) - not Minecraft data
start_panel.sh / stop_panel.sh
```

---

## Phase 3: Playit Integration

The panel can open a public tunnel to one of your Minecraft instances using
[Playit](https://playit.gg), via the new **Playit** tab.

### MDevPanel uses its own private Playit install

MDevPanel downloads Playit's own official standalone release binaries into
`data/playit/bin/` and only ever runs those copies:

```
data/playit/
├── bin/       playit, playit-cli   (downloaded from GitHub Releases)
├── config/    playit.toml (the account secret) and install.json (version info)
├── logs/      playit-agent.log
└── runtime/   agent.pid while running
```

It **never** runs a `playit` found on your PATH or installed system-wide
(via `apt`, `pkg`, Homebrew, a manual install, etc.), even if one already
exists on the machine - only the private copy in `data/playit/bin/` is ever
executed. If you already have Playit installed globally, that installation
is completely irrelevant to MDevPanel; it downloads and manages its own.

Binaries and their download URLs come from the official release page only:

- Releases: https://github.com/playit-cloud/playit-agent/releases
- Documentation: https://playit.gg/support/

### Installing

Open **Playit** → the panel detects your OS, environment (Linux/Termux) and
CPU architecture, and shows a matching **Install Playit** button. Installing
downloads two official binaries for your platform - the `playit` agent and
the `playit-cli` setup tool - verifies them against the checksums published
on the release page when available, and marks them executable. Nothing is
installed through `apt`/`pkg`/a package manager.

`PLAYIT_VERSION` in `config.py` can be a specific version (e.g. `"1.0.10"`)
or `"latest"`, which always resolves to the newest **stable** release -
GitHub's own "latest release" API excludes prereleases and drafts, so a
prerelease is never installed silently. Pinning an exact version that turns
out to be a prerelease is refused unless you also set
`PLAYIT_ALLOW_PRERELEASE = True`.

### Connecting your account (setup/claim)

Press **Setup Playit** once installed. This starts the private agent if it
isn't running yet, then runs the official `playit-cli` setup flow, which
generates a one-time claim link. The panel shows that link (never a made-up
or hardcoded one) with **Open** and **Copy** buttons. Once you approve it in
your browser, `playit-cli` hands the resulting secret to the running agent
automatically - the panel detects this by watching for the secret file to
appear, not by assuming the process finished successfully.

The secret itself is never shown in the UI, never sent to the browser, and
never included in any API response - only the boolean `secret_configured`.

### Status and tunnel info

The Playit page shows the agent's real state - phase, secret configured,
version, platform, and, when the running agent has actually reported one, a
public tunnel address. If no tunnel address is currently available the panel
says so plainly rather than guessing or reusing your regular internet IP.

The **Local Minecraft Server** field lets you pick which of your instances'
local address to tunnel; it's informational only and never modifies that
instance's `server.properties`.

**Start / Stop / Restart** control only MDevPanel's own Playit process -
completely independent from every Minecraft instance's process manager.
Stopping or restarting Playit never touches a running Minecraft server, and
vice versa.

### Logs

**Playit → Logs** shows the private agent's live output (search, pause,
copy, download). **Clear** only empties what's displayed in the panel; the
saved log file on disk is untouched.

### A note on Playit's own architecture

As of the versions covered here, Playit's release splits into two binaries:
`playit` is the agent/daemon that actually runs the tunnel, and `playit-cli`
is used only for the one-time account setup. `playit_manager.py` isolates
all of this (including the log-line parsing used to find a claim link or a
public tunnel address) in one place, so it can be updated independently if a
future Playit release changes its output format.

### Troubleshooting: "The downloaded files are not executable"

This means the download itself worked, but the file couldn't be marked
executable - almost always because **MDevPanel's own folder is on Android
shared storage** (anywhere under `/storage/emulated/0`, `/sdcard`, etc.).
That storage is backed by a filesystem that ignores `chmod`, so nothing
downloaded there can ever be run directly, on any app.

Fix: move MDevPanel's whole folder into Termux's own private storage - for
example `~/MDevPanel` - and run `start_panel.sh` from there instead. Your
`INSTANCES_DIR` (Minecraft worlds) is unaffected and can stay on shared
storage, since Minecraft's JAR is launched via `java -jar`, not executed
directly; only Playit's own binaries need real executable permission.
