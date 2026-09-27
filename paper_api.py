"""Official PaperMC API client (Phase 2).

Only talks to api.papermc.io. Never trusts a file name returned by the API
without validating it first (see _safe_jar_name).
"""

import hashlib
import os

import requests

import config

TIMEOUT = config.PAPER_API_TIMEOUT
BASE = config.PAPER_API_BASE.rstrip("/")


class PaperAPIError(Exception):
    """A user-facing problem talking to the Paper API or downloading a build."""


def _get_json(path):
    url = f"{BASE}{path}"
    try:
        resp = requests.get(url, timeout=TIMEOUT)
    except requests.Timeout as exc:
        raise PaperAPIError("The Paper API took too long to respond. Try again.") from exc
    except requests.ConnectionError as exc:
        raise PaperAPIError("Could not reach the Paper API. Check your internet connection.") from exc
    except requests.RequestException as exc:
        raise PaperAPIError("Could not reach the Paper API.") from exc

    if resp.status_code == 404:
        raise PaperAPIError("Paper has no data for that request (404).")
    if not resp.ok:
        raise PaperAPIError(f"The Paper API returned an error (HTTP {resp.status_code}).")
    try:
        return resp.json()
    except ValueError as exc:
        raise PaperAPIError("The Paper API returned a response that could not be understood.") from exc


def get_minecraft_versions():
    """Minecraft versions with a Paper build, newest first."""
    data = _get_json("/projects/paper")
    versions = data.get("versions")
    if not isinstance(versions, list) or not versions:
        raise PaperAPIError("The Paper API did not list any versions.")
    return list(reversed(versions))


def get_builds(version):
    """Build numbers (ints) available for a Minecraft version, oldest first."""
    if not version or "/" in version or ".." in version:
        raise PaperAPIError("That is not a valid Minecraft version.")
    data = _get_json(f"/projects/paper/versions/{version}")
    builds = data.get("builds")
    if not isinstance(builds, list) or not builds:
        raise PaperAPIError(f"No Paper builds are available for Minecraft {version}.")
    return builds


def get_latest_build(version):
    return get_builds(version)[-1]


def _build_info(version, build):
    data = _get_json(f"/projects/paper/versions/{version}/builds/{build}")
    try:
        application = data["downloads"]["application"]
        name = application["name"]
    except (KeyError, TypeError) as exc:
        raise PaperAPIError("The Paper API response was missing the download details.") from exc
    return {"name": _safe_jar_name(name), "sha256": application.get("sha256")}


def _safe_jar_name(name):
    """Never trust a file name from an external API: it must be a plain, local
    .jar file name with no path separators or traversal sequences."""
    if not isinstance(name, str) or not name.lower().endswith(".jar"):
        raise PaperAPIError("The Paper API returned an unexpected file name.")
    if os.path.basename(name) != name or name in (".", ".."):
        raise PaperAPIError("The Paper API returned an unsafe file name.")
    return name


def _sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_server(version, build, destination_path, progress_cb=None, cancel_event=None):
    """Download a Paper build to destination_path (atomically via a .part file).

    progress_cb(bytes_done, bytes_total_or_0) is called periodically.
    cancel_event, if given and set, aborts the download and raises PaperAPIError.
    """
    info = _build_info(version, build)
    url = f"{BASE}/projects/paper/versions/{version}/builds/{build}/downloads/{info['name']}"
    tmp_path = destination_path + ".part"

    try:
        with requests.get(url, stream=True, timeout=TIMEOUT) as resp:
            if resp.status_code == 404:
                raise PaperAPIError("That Paper build is no longer available for download.")
            if not resp.ok:
                raise PaperAPIError(f"The download failed (HTTP {resp.status_code}).")
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            with open(tmp_path, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=1024 * 256):
                    if cancel_event is not None and cancel_event.is_set():
                        raise PaperAPIError("Download cancelled.")
                    if not chunk:
                        continue
                    fh.write(chunk)
                    done += len(chunk)
                    if progress_cb:
                        progress_cb(done, total)
    except requests.Timeout as exc:
        _cleanup(tmp_path)
        raise PaperAPIError("The download timed out. Check your internet connection and try again.") from exc
    except requests.ConnectionError as exc:
        _cleanup(tmp_path)
        raise PaperAPIError("The download was interrupted. Check your internet connection and try again.") from exc
    except requests.RequestException as exc:
        _cleanup(tmp_path)
        raise PaperAPIError("The download failed.") from exc
    except OSError as exc:
        _cleanup(tmp_path)
        raise PaperAPIError("Could not write the downloaded file to storage (disk full or permission denied?).") from exc
    except PaperAPIError:
        _cleanup(tmp_path)
        raise

    if done == 0:
        _cleanup(tmp_path)
        raise PaperAPIError("The download completed but the file was empty.")

    if info.get("sha256"):
        try:
            if _sha256_of(tmp_path) != info["sha256"]:
                _cleanup(tmp_path)
                raise PaperAPIError("The downloaded file did not match Paper's checksum. Try again.")
        except OSError as exc:
            _cleanup(tmp_path)
            raise PaperAPIError("Could not verify the downloaded file.") from exc

    os.replace(tmp_path, destination_path)


def _cleanup(path):
    try:
        os.remove(path)
    except OSError:
        pass
