#!/usr/bin/env python3
"""Register Ghost webhooks that hit the Cloudflare Worker for Telegram OG fixes.

Needs: GHOST_URL, GHOST_ADMIN_API_KEY, WEBHOOK_TARGET_URL
Optional: WEBHOOK_SECRET

  WEBHOOK_TARGET_URL=https://ghost-telegram-og-webhook.<you>.workers.dev/ \\
    python scripts/register-telegram-og-webhooks.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

import app  # noqa: E402

EVENTS = (
    "post.published",
    "post.published.edited",
    "post.scheduled",
    "post.edited",
)


def main() -> None:
    target = (os.getenv("WEBHOOK_TARGET_URL") or "").strip().rstrip("/") + "/"
    if not target.startswith("https://"):
        raise SystemExit("Set WEBHOOK_TARGET_URL to the Worker https URL")
    secret = (os.getenv("WEBHOOK_SECRET") or "").strip() or None

    for event in EVENTS:
        payload = {
            "webhooks": [
                {
                    "event": event,
                    "target_url": target,
                    "name": f"telegram-og:{event}",
                    **({"secret": secret} if secret else {}),
                }
            ]
        }
        try:
            data = app._ghost("POST", "webhooks/", json=payload)
            wh = (data.get("webhooks") or [{}])[0]
            print(f"ok {event} → {wh.get('id')} {wh.get('target_url')}")
        except Exception as exc:
            # Duplicate target/event often 422 — print and continue
            print(f"fail {event}: {exc}")


if __name__ == "__main__":
    main()
