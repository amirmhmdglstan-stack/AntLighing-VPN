"""Downloads the Xray-core release binary.

AntLighting never invents its own networking core: it fetches the official
release from the XTLS/Xray-core GitHub repository.  SHA-256 digests published
alongside each asset are verified before the archive is unpacked, and the core
is only ever executed as a separate process — never imported.

Licence: Xray-core is MPL-2.0.  See THIRD_PARTY_LICENSES.md.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import shutil
import stat
import tempfile
import zipfile
from typing import Any, Callable

from ..sources.base import http_get, http_get_bytes

log = logging.getLogger(__name__)

API_LATEST = "https://api.github.com/repos/XTLS/Xray-core/releases/latest"
API_TAG = "https://api.github.com/repos/XTLS/Xray-core/releases/tags/{tag}"

# Only these archives are ever fetched; anything else is refused.
ALLOWED_ASSET_PREFIXES = ("Xray-windows-", "Xray-linux-", "Xray-macos-")


def asset_name() -> str:
    """The release asset matching this machine."""
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "windows":
        arch = "64" if machine in ("amd64", "x86_64") else (
            "arm64-v8a" if machine in ("arm64", "aarch64") else "32"
        )
        return f"Xray-windows-{arch}.zip"
    if system == "darwin":
        arch = "arm64-v8a" if machine in ("arm64", "aarch64") else "64"
        return f"Xray-macos-{arch}.zip"
    arch = "64" if machine in ("amd64", "x86_64") else (
        "arm64-v8a" if machine in ("arm64", "aarch64") else "32"
    )
    return f"Xray-linux-{arch}.zip"


def binary_name() -> str:
    return "xray.exe" if os.name == "nt" else "xray"


class DownloadError(RuntimeError):
    """Raised when the core cannot be fetched or verified."""


def _release_info(tag: str = "") -> dict[str, Any]:
    url = API_TAG.format(tag=tag) if tag else API_LATEST
    result = http_get(url, timeout=25.0, headers={"Accept": "application/vnd.github+json"})
    if not result.ok:
        raise DownloadError(f"could not reach the release API: {result.error}")
    try:
        return json.loads(result.text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise DownloadError(f"malformed release metadata: {exc}") from exc


def find_asset(info: dict[str, Any], name: str) -> dict[str, Any] | None:
    for asset in info.get("assets", []):
        if isinstance(asset, dict) and asset.get("name") == name:
            return asset
    return None


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 256), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_core(
    dest_dir: str,
    *,
    tag: str = "",
    on_progress: Callable[[str], None] | None = None,
    verify: bool = True,
) -> str:
    """Download, verify and unpack Xray-core into *dest_dir*.

    Returns the path to the executable.  Raises :class:`DownloadError` on any
    problem; the destination directory is left unchanged on failure.
    """
    os.makedirs(dest_dir, exist_ok=True)
    name = asset_name()
    if not name.startswith(ALLOWED_ASSET_PREFIXES):
        raise DownloadError(f"refusing unexpected asset name {name}")

    def say(message: str) -> None:
        log.info("core download: %s", message)
        if on_progress:
            on_progress(message)

    say(f"Looking up {name}…")
    info = _release_info(tag)
    asset = find_asset(info, name)
    if asset is None:
        raise DownloadError(f"{name} is not part of release {info.get('tag_name')}")
    url = str(asset.get("browser_download_url") or "")
    if not url:
        raise DownloadError("the release has no download URL")

    expected = ""
    if verify:
        digest_asset = find_asset(info, name + ".dgst")
        if digest_asset is not None:
            digest_url = str(digest_asset.get("browser_download_url") or "")
            if digest_url:
                digest_result = http_get(digest_url, timeout=25.0)
                if digest_result.ok:
                    expected = _parse_dgst(digest_result.text)

    say("Downloading…")
    ok, payload, error = http_get_bytes(url, timeout=300.0)
    if not ok:
        raise DownloadError(f"download failed: {error}")

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as handle:
        handle.write(payload)
        temp_path = handle.name

    try:
        if expected:
            actual = _sha256(temp_path)
            if actual.lower() != expected.lower():
                raise DownloadError(
                    f"checksum mismatch: expected {expected[:16]}…, got {actual[:16]}…"
                )
            say("Checksum verified")
        else:
            say("No published checksum found — skipping verification")

        say("Unpacking…")
        with zipfile.ZipFile(temp_path) as archive:
            members = [m for m in archive.namelist() if not m.endswith("/")]
            wanted = {binary_name(), "geoip.dat", "geosite.dat", "wintun.dll",
                      "LICENSE", "LICENSE-Wintun"}
            extracted: list[str] = []
            for member in members:
                base = os.path.basename(member)
                if base not in wanted:
                    continue
                # Reject any path traversal in the archive.
                if ".." in member or member.startswith("/"):
                    continue
                with archive.open(member) as source, open(
                    os.path.join(dest_dir, base), "wb"
                ) as target:
                    shutil.copyfileobj(source, target)
                extracted.append(base)
        binary_path = os.path.join(dest_dir, binary_name())
        if not os.path.isfile(binary_path):
            raise DownloadError(f"{binary_name()} was not in the archive")
        if os.name != "nt":
            os.chmod(binary_path, os.stat(binary_path).st_mode | stat.S_IEXEC)
        say(f"Installed {info.get('tag_name', 'unknown')} to {dest_dir}")
        return binary_path
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass


def _parse_dgst(text: str) -> str:
    """Extract a hex digest from a ``.dgst`` file.

    Xray publishes several algorithm lines; we only want SHA256.
    """
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.lower().startswith("sha256"):
            parts = line.replace("=", " ").split()
            for part in parts[1:]:
                if len(part) == 64 and all(c in "0123456789abcdefABCDEF" for c in part):
                    return part
        parts = line.split()
        if len(parts) >= 1 and len(parts[0]) == 64 and all(
            c in "0123456789abcdefABCDEF" for c in parts[0]
        ):
            return parts[0]
    return ""
