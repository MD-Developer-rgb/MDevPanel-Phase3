"""MDev Panel - Phase 3: instance-based Minecraft server manager with a Playit tunnel integration.

Run with:  python app.py
"""

import json
import logging
import os
import signal
import sys
import threading
import time
import uuid

from flask import Flask, Response, jsonify, render_template, request, send_file
from markupsafe import escape
from werkzeug.exceptions import HTTPException

import config
import instance_manager
import os_manager
import paper_api
import playit_manager
import properties_manager
import server_manager
from instance_manager import InstanceError
from paper_api import PaperAPIError
from playit_manager import PlayitError

app = Flask(__name__)

BOOT = str(int(time.time()))  # busts phone browser cache; also busts stale SSE cursors on restart

logging.getLogger("werkzeug").setLevel(logging.ERROR)

_jobs = {}
_jobs_lock = threading.Lock()


@app.context_processor
def inject_globals():
    return {"asset_v": BOOT}


@app.before_request
def block_cross_site_posts():
    if request.method == "POST" and request.headers.get("X-Requested-With") != "MDevPanel":
        return jsonify(ok=False, message="Request blocked."), 403


def no_store(response):
    response.headers["Cache-Control"] = "no-store"
    return response


def action(result):
    result = dict(result)
    code = result.pop("http", 200)
    return no_store(jsonify(result)), code


def get_server_or_404(name):
    """The InstanceServer for name, or None + a 404 response if it isn't a
    real, currently-detected instance."""
    try:
        exists = instance_manager.instance_exists(name)
    except InstanceError:
        exists = False
    if not exists:
        return None, (jsonify(ok=False, message=f'No instance named "{name}" was found.'), 404)
    return server_manager.get(name), None


# ------------------------------------------------------------------------ pages

@app.get("/")
def instances_page():
    return render_template("instances.html", page="instances", instance=None)


@app.get("/new-instance")
def new_instance_page():
    return render_template("new_instance.html", page="new-instance", instance=None)


@app.get("/instance/<name>/")
@app.get("/instance/<name>")
def dashboard(name):
    server, err = get_server_or_404(name)
    if err:
        return err
    return render_template("dashboard.html", page="dashboard", instance=name)


@app.get("/instance/<name>/console")
def console_page(name):
    server, err = get_server_or_404(name)
    if err:
        return err
    return render_template("console.html", page="console", instance=name)


@app.get("/instance/<name>/settings")
def settings_page(name):
    server, err = get_server_or_404(name)
    if err:
        return err
    return render_template("settings.html", page="settings", instance=name)


@app.get("/playit")
def playit_page():
    names = instance_manager.list_instances()
    return render_template("playit.html", page="playit", instance=None, mc_instances=names)


@app.get("/playit/logs")
def playit_logs_page():
    return render_template("playit_logs.html", page="playit-logs", instance=None)


# -------------------------------------------------------------------- instances API

def _instance_summary(name):
    server = server_manager.get(name)
    snap = server.snapshot()
    return {
        "name": name,
        "state": snap["state"],
        "running": snap["running"],
        "restarting": snap["restarting"],
        "software": snap["software"],
        "minecraft_version": snap["minecraft_version"],
        "players_online": snap["players_online"],
        "max_players": snap["max_players"],
        "port": snap["port"],
        "ram": snap["ram"],
    }


@app.get("/api/instances")
def api_instances():
    names = instance_manager.list_instances()
    return no_store(jsonify(ok=True, instances=[_instance_summary(n) for n in names]))


@app.post("/api/instances")
def api_create_instance():
    payload = request.get_json(silent=True) or {}
    name = str(payload.get("name", ""))
    version = str(payload.get("version", ""))
    build = payload.get("build")

    try:
        name = instance_manager.validate_name(name)
    except InstanceError as exc:
        return jsonify(ok=False, message=str(exc)), 400
    if instance_manager.instance_exists(name) or __import__("os").path.exists(instance_manager.instance_dir(name)):
        return jsonify(ok=False, message=f'"{name}" already exists.'), 409
    if not version:
        return jsonify(ok=False, message="Choose a Minecraft version."), 400

    job_id = uuid.uuid4().hex
    with _jobs_lock:
        _jobs[job_id] = {"status": "starting", "percent": 0, "message": "Starting...", "name": name, "success": None}
    threading.Thread(target=_create_instance_job, args=(job_id, name, version, build), daemon=True).start()
    return jsonify(ok=True, job_id=job_id)


def _set_job(job_id, **updates):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is not None:
            job.update(updates)


def _create_instance_job(job_id, name, version, build):
    try:
        if not build:
            _set_job(job_id, status="starting", message=f"Finding the latest Paper build for {version}...")
            build = paper_api.get_latest_build(version)

        directory = instance_manager.create_instance_dir(name)

        def progress(done, total):
            if total:
                percent = min(99, int(done * 100 / total))
                _set_job(job_id, status="downloading", percent=percent,
                          message=f"Downloading Paper {version} build {build}... {percent}%")
            else:
                _set_job(job_id, status="downloading", percent=None,
                          message=f"Downloading Paper {version} build {build}... {done // 1024} KB")

        _set_job(job_id, status="downloading", percent=0, message=f"Downloading Paper {version} build {build}...")
        dest = f"{directory}/{config.DEFAULT_SERVER_JAR}"
        paper_api.download_server(version, build, dest, progress_cb=progress)

        ram_min, ram_max = config.DEFAULT_RAM_MIN, config.DEFAULT_RAM_MAX
        instance_manager.record_created(name, version, ram_min, ram_max)
        server_manager.get(name)  # warm the registry so it appears immediately
        _set_job(job_id, status="done", percent=100, success=True, message=f'"{name}" was created.')
    except PaperAPIError as exc:
        instance_manager.remove_empty_instance_dir(name)
        _set_job(job_id, status="error", success=False, message=str(exc))
    except InstanceError as exc:
        _set_job(job_id, status="error", success=False, message=str(exc))
    except Exception as exc:  # pragma: no cover - unexpected, keep the UI informative
        app.logger.error("Instance creation failed: %r", exc)
        instance_manager.remove_empty_instance_dir(name)
        _set_job(job_id, status="error", success=False, message="Something went wrong creating the instance.")


@app.get("/api/instances/create/<job_id>")
def api_create_progress(job_id):
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        return jsonify(ok=False, message="Unknown job."), 404
    return no_store(jsonify(ok=True, **job))


# ----------------------------------------------------------------------- Paper API

@app.get("/api/paper/versions")
def api_paper_versions():
    try:
        versions = paper_api.get_minecraft_versions()
    except PaperAPIError as exc:
        return jsonify(ok=False, message=str(exc)), 502
    return jsonify(ok=True, versions=versions)


@app.get("/api/paper/builds")
def api_paper_builds():
    version = request.args.get("version", "")
    try:
        builds = paper_api.get_builds(version)
    except PaperAPIError as exc:
        return jsonify(ok=False, message=str(exc)), 502
    return jsonify(ok=True, builds=list(reversed(builds)), latest=builds[-1])


# --------------------------------------------------------------- per-instance API

@app.get("/api/instances/<name>/status")
def api_status(name):
    server, err = get_server_or_404(name)
    if err:
        return err
    return no_store(jsonify(server.snapshot()))


@app.post("/api/instances/<name>/start")
def api_start(name):
    server, err = get_server_or_404(name)
    if err:
        return err
    return action(server.start())


@app.post("/api/instances/<name>/stop")
def api_stop(name):
    server, err = get_server_or_404(name)
    if err:
        return err
    return action(server.stop())


@app.post("/api/instances/<name>/restart")
def api_restart(name):
    server, err = get_server_or_404(name)
    if err:
        return err
    return action(server.restart())


@app.post("/api/instances/<name>/force-stop")
def api_force_stop(name):
    server, err = get_server_or_404(name)
    if err:
        return err
    return action(server.force_stop())


@app.post("/api/instances/<name>/console/command")
def api_command(name):
    server, err = get_server_or_404(name)
    if err:
        return err
    payload = request.get_json(silent=True) or {}
    command = payload.get("command", "")
    if not isinstance(command, str):
        return jsonify(ok=False, message="Enter a command."), 400
    return action(server.send_command(command))


@app.post("/api/instances/<name>/console/clear")
def api_clear(name):
    server, err = get_server_or_404(name)
    if err:
        return err
    server.clear_console()
    return jsonify(ok=True, message="Console cleared.")


@app.get("/api/instances/<name>/console/stream")
def api_stream(name):
    server, err = get_server_or_404(name)
    if err:
        return err

    last = 0
    reset = False
    raw = request.headers.get("Last-Event-ID", "")
    if raw:
        boot, _, number = raw.partition(":")
        if boot == BOOT and number.isdigit():
            last = int(number)
        else:
            reset = True

    def generate():
        position = last
        yield "retry: 3000\n\n"
        if reset or position >= server.next_id():
            yield "event: reset\ndata: {}\n\n"
            position = 0
        while True:
            lines = server.wait_for_lines(position, 15)
            if lines:
                position = lines[-1]["id"]
                yield f"id: {BOOT}:{position}\nevent: lines\ndata: {json.dumps(lines)}\n\n"
            else:
                yield ": keepalive\n\n"

    response = Response(generate(), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache, no-transform"
    response.headers["X-Accel-Buffering"] = "no"
    return response


@app.get("/api/instances/<name>/console/download")
def api_download(name):
    server, err = get_server_or_404(name)
    if err:
        return err
    path = server.latest_log_path()
    if path:
        return send_file(path, mimetype="text/plain", as_attachment=True,
                         download_name=f"{name}-latest.log", max_age=0)
    return Response(server.console_text(), mimetype="text/plain",
                    headers={"Content-Disposition": f'attachment; filename="{name}-console.log"'})


@app.get("/api/instances/<name>/properties")
def api_get_properties(name):
    server, err = get_server_or_404(name)
    if err:
        return err
    return jsonify(ok=True, fields=properties_manager.friendly_fields(server.dir),
                    advanced=properties_manager.other_lines_text(server.dir))


@app.post("/api/instances/<name>/properties")
def api_save_properties(name):
    server, err = get_server_or_404(name)
    if err:
        return err
    payload = request.get_json(silent=True) or {}
    try:
        properties_manager.save(server.dir, payload.get("fields") or {}, payload.get("advanced") or "")
    except properties_manager.PropertiesError as exc:
        return jsonify(ok=False, message=str(exc)), 400
    return jsonify(ok=True, message="server.properties saved.")


@app.post("/api/instances/<name>/ram")
def api_save_ram(name):
    server, err = get_server_or_404(name)
    if err:
        return err
    payload = request.get_json(silent=True) or {}
    try:
        server.set_ram(payload.get("ram_min", ""), payload.get("ram_max", ""))
    except ValueError as exc:
        return jsonify(ok=False, message=str(exc)), 400
    return jsonify(ok=True, message="RAM settings saved. They take effect next start.")


# ------------------------------------------------------------------- Playit API

@app.get("/api/playit/status")
def api_playit_status():
    return no_store(jsonify(playit_manager.get_manager().status()))


@app.post("/api/playit/install")
def api_playit_install():
    try:
        return action(playit_manager.get_manager().start_install())
    except PlayitError as exc:
        return jsonify(ok=False, message=str(exc)), 400


@app.post("/api/playit/setup")
def api_playit_setup():
    return action(playit_manager.get_manager().setup())


@app.post("/api/playit/start")
def api_playit_start():
    return action(playit_manager.get_manager().start())


@app.post("/api/playit/stop")
def api_playit_stop():
    return action(playit_manager.get_manager().stop())


@app.post("/api/playit/restart")
def api_playit_restart():
    return action(playit_manager.get_manager().restart())


@app.get("/api/playit/setup-url")
def api_playit_setup_url():
    status = playit_manager.get_manager().status()
    return jsonify(ok=True, setup_url=status["setup_url"])


@app.get("/api/playit/logs/stream")
def api_playit_log_stream():
    manager = playit_manager.get_manager()

    last = 0
    reset = False
    raw = request.headers.get("Last-Event-ID", "")
    if raw:
        boot, _, number = raw.partition(":")
        if boot == BOOT and number.isdigit():
            last = int(number)
        else:
            reset = True

    def generate():
        position = last
        yield "retry: 3000\n\n"
        if reset or position >= manager.next_id():
            yield "event: reset\ndata: {}\n\n"
            position = 0
        while True:
            lines = manager.wait_for_lines(position, 15)
            if lines:
                position = lines[-1]["id"]
                yield f"id: {BOOT}:{position}\nevent: lines\ndata: {json.dumps(lines)}\n\n"
            else:
                yield ": keepalive\n\n"

    response = Response(generate(), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache, no-transform"
    response.headers["X-Accel-Buffering"] = "no"
    return response


@app.post("/api/playit/logs/clear")
def api_playit_log_clear():
    playit_manager.get_manager().clear_log()
    return jsonify(ok=True, message="Log cleared. The saved log file is untouched.")


@app.get("/api/playit/logs/download")
def api_playit_log_download():
    manager = playit_manager.get_manager()
    path = playit_manager._log_file_path()
    if os.path.isfile(path):
        return send_file(path, mimetype="text/plain", as_attachment=True,
                         download_name="playit.log", max_age=0)
    return Response(manager.log_text(), mimetype="text/plain",
                    headers={"Content-Disposition": 'attachment; filename="playit.log"'})


@app.get("/api/playit/minecraft-targets")
def api_playit_minecraft_targets():
    """Local Minecraft instances the person might want to tunnel, with their
    configured port - read-only, never changes server.properties."""
    targets = []
    for name in instance_manager.list_instances():
        server = server_manager.get(name)
        port, _ = server.port()
        targets.append({"name": name, "local_address": f"127.0.0.1:{port}"})
    return jsonify(ok=True, targets=targets)


# ---------------------------------------------------------------------- errors

@app.errorhandler(Exception)
def handle_error(exc):
    if isinstance(exc, HTTPException):
        code = exc.code or 500
        message = "Page not found." if code == 404 else (exc.description or "Request failed.")
    else:
        app.logger.error("Unhandled error: %r", exc)
        code, message = 500, "Something went wrong inside the panel. Check the terminal for details."
    if request.path.startswith("/api/"):
        return jsonify(ok=False, message=message), code
    page = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>MDev Panel</title><link rel="stylesheet" href="/static/css/style.css?v={BOOT}"></head>'
        '<body><main class="main"><section class="card">'
        f"<h1 class=\"h1\">{escape(message)}</h1>"
        '<p><a class="btn" href="/">Back to instances</a></p></section></main></body></html>'
    )
    return page, code


# ------------------------------------------------------------------------ main

def _on_signal(signum, frame):
    print("\nShutting down the panel...", flush=True)
    server_manager.shutdown_all()
    playit_manager.get_manager().shutdown()
    raise SystemExit(0)


def main():
    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    env = os_manager.ENV
    root = instance_manager.instances_root()
    names = instance_manager.list_instances()

    print("=" * 56)
    print(" MDev Panel - Phase 3 (instances + Playit)")
    print("=" * 56)
    print(f" Environment       : {env}")
    print(f" Panel (web page)  : http://127.0.0.1:{config.PANEL_PORT}   <- on this device")
    lan_ip = config.LAN_IP_OVERRIDE or os_manager.detect_lan_ip()
    if lan_ip and str(config.PANEL_HOST) not in ("127.0.0.1", "localhost"):
        print(f"                     http://{lan_ip}:{config.PANEL_PORT}   <- other devices on Wi-Fi")
    print(f" Instances folder  : {root}")
    print(f" Instances found   : {', '.join(names) if names else '(none yet)'}")
    playit_status = playit_manager.get_manager().status()
    print(f" Playit            : phase={playit_status['phase']}"
          + (f" version={playit_status['version']}" if playit_status['version'] else ""))
    print("=" * 56, flush=True)

    try:
        app.run(host=config.PANEL_HOST, port=config.PANEL_PORT, threaded=True,
                debug=False, use_reloader=False)
    except OSError:
        print(f"\nCould not open panel port {config.PANEL_PORT}: it is already in use.")
        server_manager.shutdown_all()
        sys.exit(1)


if __name__ == "__main__":
    main()
