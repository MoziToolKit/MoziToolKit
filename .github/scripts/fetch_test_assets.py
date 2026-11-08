#!/usr/bin/env python3
"""
Cross-platform downloader for the temporary real Minecraft assets used by CI.

Replaces ``fetch_test_assets.sh`` so the same code path runs on Linux, macOS and
Windows. Extracted/derived locations are exported through ``$GITHUB_ENV`` for
subsequent workflow steps.

Environment:
  MTK_CI_ASSETS_BASE   Base URL hosting the assets (required).
  RUNNER_TEMP          Scratch directory (defaults to ``tempfile.gettempdir()``).
  GITHUB_ENV           When set, variables are appended for later steps.

Downloads are non-fatal: on failure the asset-dependent tests simply skip.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

# The warning output contains no emoji today, but keep stdout safe on Windows
# (legacy ANSI code page) for any future message.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

RETRIES = 5
TIMEOUT_SECONDS = 180


def _download_curl(url: str, dest: Path) -> bool:
    """Attempt download using system curl with native retry and redirect handling."""
    curl_bin = shutil.which("curl")
    if not curl_bin:
        return False

    part = dest.with_suffix(dest.suffix + ".part")
    if part.exists():
        part.unlink(missing_ok=True)

    cmd = [
        curl_bin,
        "-fL",
        "--retry", "5",
        "--retry-delay", "2",
        "-sS",
        "-o", str(part),
        url,
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_SECONDS, check=False)
        if res.returncode == 0 and part.exists() and part.stat().st_size > 0:
            part.replace(dest)
            return True
        part.unlink(missing_ok=True)
        if res.stderr:
            print(f"curl attempt failed for {url}: {res.stderr.strip()}")
    except Exception as exc:
        part.unlink(missing_ok=True)
        print(f"curl invocation error for {url}: {exc}")
    return False


def _download_urllib(url: str, dest: Path) -> bool:
    """Fallback download using urllib with backoff."""
    part = dest.with_suffix(dest.suffix + ".part")
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; MoziToolKit-CI/1.0)",
        "Accept": "*/*",
    }
    last_exc = None
    for attempt in range(1, RETRIES + 1):
        if part.exists():
            part.unlink(missing_ok=True)
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as response, \
                    open(part, "wb") as out_file:
                shutil.copyfileobj(response, out_file, length=128 * 1024)
            if part.exists() and part.stat().st_size > 0:
                part.replace(dest)
                return True
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if part.exists():
                part.unlink(missing_ok=True)
            sleep_time = min(15, 2 ** attempt)
            print(f"Download attempt ({attempt}/{RETRIES}) failed for {url}: {exc}. Retrying in {sleep_time}s...")
            time.sleep(sleep_time)

    print(f"::warning::Download failed after {RETRIES} attempts {url}: {last_exc}")
    return False


def download(url: str, dest: Path) -> bool:
    """Download *url* to *dest*; returns False (with a warning) on failure."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if _download_curl(url, dest):
        return True
    return _download_urllib(url, dest)


def export_vars(values: dict[str, str]) -> None:
    """Expose variables to the current process and to later workflow steps."""
    for key, value in values.items():
        os.environ[key] = value
        print(f"{key}={value}")

    env_file = os.environ.get("GITHUB_ENV")
    if not env_file:
        return
    with open(env_file, "a", encoding="utf-8") as fh:
        for key, value in values.items():
            fh.write(f"{key}={value}\n")


def extract_zip(archive: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(target)


def extract_tar_gz(archive: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tf:
        try:
            tf.extractall(target, filter="data")
        except TypeError:  # Python < 3.12 has no extraction filter
            tf.extractall(target)


def main() -> int:
    base = os.environ.get("MTK_CI_ASSETS_BASE")
    if not base:
        print("::error::MTK_CI_ASSETS_BASE is not set")
        return 1

    work = Path(os.environ.get("RUNNER_TEMP") or tempfile.gettempdir()) / "mtk-assets"
    work.mkdir(parents=True, exist_ok=True)

    jar = work / "mc.jar"
    if download(f"{base}/26.2-Fabric.jar", jar):
        mc_dir = work / "mc"
        shutil.rmtree(mc_dir, ignore_errors=True)
        try:
            extract_zip(jar, mc_dir)
            export_vars({
                "MTK_TEST_ASSETS": str(mc_dir),
                "MC_DIR": str(mc_dir),
                "MC_ASSETS_DIR": str(mc_dir),
                "MTK_TEST_JAR": str(jar),
            })
        except Exception as exc:  # noqa: BLE001
            print(f"::warning::Failed to extract {jar}: {exc}")
    else:
        print("::warning::Minecraft jar unavailable; real-pack tests will fall back to vendored fixtures.")

    resource_pack = work / "SPBR-21.zip"
    if download(f"{base}/SPBR-21.zip", resource_pack):
        export_vars({"MTK_TEST_RESOURCE_PACK": str(resource_pack)})
    else:
        print("::warning::External resource pack unavailable; companion-pack tests will be skipped.")

    save_archive = work / "mtk-save-world.tar.gz"
    if download(f"{base}/mtk-save-world.tar.gz", save_archive):
        try:
            extract_tar_gz(save_archive, work)
            export_vars({"MTK_TEST_SAVE": str(work / "mtk-save-world")})
        except Exception as exc:  # noqa: BLE001
            print(f"::warning::Failed to extract {save_archive}: {exc}")
    else:
        print("::warning::Sample save world unavailable; save-bridge tests will be skipped.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
