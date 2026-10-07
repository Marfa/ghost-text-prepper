#!/usr/bin/env python3
"""Fetch upstream Layer A text_unicode.py into vendor/ and report if it changed.

Exit codes:
  0 — vendor updated (or --check with changes pending)
  1 — error
  2 — already up to date (--check: no changes)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

log = logging.getLogger("sync-watermarks")

REPO = "guillaumemeyer/watermarks-remover"
UPSTREAM_PATH = "service/scripts/text_unicode.py"
RAW_URL = f"https://raw.githubusercontent.com/{REPO}/{{ref}}/{UPSTREAM_PATH}"
COMMITS_API = (
    f"https://api.github.com/repos/{REPO}/commits?path={UPSTREAM_PATH}&per_page=1"
)

ROOT = Path(__file__).resolve().parents[1]
VENDOR_DIR = ROOT / "vendor" / "watermarks_remover"
VENDOR_FILE = VENDOR_DIR / "text_unicode.py"
SOURCE_FILE = VENDOR_DIR / "SOURCE.txt"


def _github_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "ghost-text-prepper-sync-watermarks",
    }
    token = (os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _http_get(url: str, *, accept: str | None = None) -> bytes:
    headers = _github_headers()
    if accept:
        headers["Accept"] = accept
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        body = exc.read()[:300].decode("utf-8", errors="replace")
        raise RuntimeError(f"GET {url} → {exc.code}: {body}") from exc


def _latest_commit() -> tuple[str, str]:
    raw = _http_get(COMMITS_API)
    data = json.loads(raw.decode("utf-8"))
    if not data:
        raise RuntimeError(f"no commits for {UPSTREAM_PATH}")
    sha = data[0]["sha"]
    date = data[0]["commit"]["committer"]["date"]
    return sha, date


def _read_source_commit() -> str | None:
    if not SOURCE_FILE.is_file():
        return None
    for line in SOURCE_FILE.read_text(encoding="utf-8").splitlines():
        if line.startswith("commit="):
            return line.split("=", 1)[1].strip()
    return None


def _write_source(commit: str, date: str) -> None:
    SOURCE_FILE.write_text(
        "\n".join(
            [
                f"repo=https://github.com/{REPO}",
                f"path={UPSTREAM_PATH}",
                f"commit={commit}",
                f"date={date}",
                "license=MIT",
                "",
            ]
        ),
        encoding="utf-8",
        newline="\n",
    )


def sync(*, check_only: bool) -> int:
    sha, date = _latest_commit()
    current = _read_source_commit()
    remote = _http_get(RAW_URL.format(ref=sha))
    if not remote.endswith(b"\n"):
        remote += b"\n"

    local = VENDOR_FILE.read_bytes() if VENDOR_FILE.is_file() else b""
    same_bytes = local == remote
    same_commit = current == sha

    if same_bytes and same_commit:
        log.info("up to date at %s", sha[:12])
        return 2

    if check_only:
        log.info(
            "update available: local_commit=%s remote=%s bytes_differ=%s",
            current or "(none)",
            sha[:12],
            not same_bytes,
        )
        return 0

    VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    VENDOR_FILE.write_bytes(remote)
    _write_source(sha, date)
    log.info("updated vendor to %s (%s)", sha[:12], date)
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="report whether an update is available; do not write files",
    )
    args = parser.parse_args()
    try:
        return sync(check_only=args.check)
    except Exception as exc:  # noqa: BLE001 — CLI boundary
        log.error("%s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
