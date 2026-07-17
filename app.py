"""Prep Ghost draft posts: ≤146-char SEO copy + Bonsai feature image."""

from __future__ import annotations

import html
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from typing import Any, Optional

import httpx
import jwt
from dotenv import load_dotenv
from huggingface_hub import InferenceClient

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("ghost-prep")

def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


GHOST_URL = _env("GHOST_URL").rstrip("/").removesuffix("/ghost")
GHOST_KEY = _env("GHOST_ADMIN_API_KEY")
HF_TOKEN = _env("HF_TOKEN")
HF_TEXT_MODEL = _env("HF_TEXT_MODEL", "Qwen/Qwen2.5-7B-Instruct")

BONSAI_URL = _env("BONSAI_URL", "https://prism-ml-bonsai-image-demo.hf.space").rstrip("/")
BONSAI_BACKEND = _env("BONSAI_BACKEND", "bonsai-ternary-gemlite")
BONSAI_SEED = int(_env("BONSAI_SEED", "42"))
BONSAI_STEPS = int(_env("BONSAI_STEPS", "4"))
BONSAI_WIDTH = int(_env("BONSAI_WIDTH", "1248"))
BONSAI_HEIGHT = int(_env("BONSAI_HEIGHT", "832"))
BONSAI_TOKEN = _env("BONSAI_TOKEN")

MAX_EXCERPT_LEN = int(_env("MAX_EXCERPT_LEN", "146"))
SKIP_COMPLETE = _env("SKIP_COMPLETE", "1") not in ("0", "false", "False")
STATE_FILE = Path(_env("STATE_FILE", "state/last-run.json"))

# Article body sent to the LLM — keep prompt under typical context comfort
_MAX_ARTICLE_CHARS = 6000

http = httpx.Client(timeout=httpx.Timeout(30.0, read=180.0))


def to_ghost_filter_date(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def read_last_run() -> datetime | None:
    if not STATE_FILE.exists():
        return None
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        raw = data.get("lastRunAt")
        if not raw:
            return None
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (json.JSONDecodeError, OSError, ValueError, TypeError):
        log.warning("invalid state file %s — treating as first run", STATE_FILE)
        return None


def write_last_run(when: datetime) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {"lastRunAt": to_ghost_filter_date(when)}
    STATE_FILE.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag in ("script", "style"):
            self._skip = True
        elif tag in ("p", "br", "li", "h1", "h2", "h3", "h4", "div"):
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip = False

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self._parts.append(data)

    def text(self) -> str:
        raw = html.unescape("".join(self._parts))
        return re.sub(r"[ \t]+", " ", re.sub(r"\n{2,}", "\n\n", raw)).strip()


def html_to_text(raw_html: str) -> str:
    parser = _TextExtractor()
    parser.feed(raw_html or "")
    return parser.text()


def truncate_excerpt(text: str, limit: int = MAX_EXCERPT_LEN) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip().strip("\"'"))
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(".,;:!-—") + "…"


def needs_prep(post: dict[str, Any]) -> bool:
    if not SKIP_COMPLETE:
        return True
    has_excerpt = bool((post.get("custom_excerpt") or "").strip())
    has_image = bool(post.get("feature_image"))
    return not (has_excerpt and has_image)


def _ghost_token(admin_key: str) -> str:
    key_id, secret = admin_key.split(":", 1)
    now = int(time.time())
    return jwt.encode(
        {"iat": now, "exp": now + 300, "aud": "/admin/"},
        bytes.fromhex(secret),
        algorithm="HS256",
        headers={"alg": "HS256", "typ": "JWT", "kid": key_id},
    )


def _ghost(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    response = http.request(
        method,
        f"{GHOST_URL}/ghost/api/admin/{path}",
        headers={
            "Authorization": f"Ghost {_ghost_token(GHOST_KEY)}",
            "Accept-Version": "v5.0",
            "Content-Type": "application/json",
        },
        **kwargs,
    )
    if response.is_error:
        log.error("ghost %s %s → %s %s", method, path, response.status_code, response.text[:500])
    response.raise_for_status()
    return response.json()


def list_drafts(since: datetime) -> list[dict[str, Any]]:
    since_iso = to_ghost_filter_date(since)
    post_filter = f"status:draft+updated_at:>'{since_iso}'"
    posts: list[dict[str, Any]] = []
    page = 1
    while True:
        data = _ghost(
            "GET",
            "posts/",
            params={
                "filter": post_filter,
                "formats": "html",
                "order": "updated_at asc",
                "limit": 50,
                "page": page,
            },
        )
        posts.extend(data["posts"])
        pagination = data.get("meta", {}).get("pagination", {})
        if page >= pagination.get("pages", 1):
            break
        page += 1
    return posts


def upload_image(png_bytes: bytes, filename: str) -> str:
    # Do not set Content-Type — httpx sets multipart boundary
    response = http.post(
        f"{GHOST_URL}/ghost/api/admin/images/upload/",
        headers={
            "Authorization": f"Ghost {_ghost_token(GHOST_KEY)}",
            "Accept-Version": "v5.0",
        },
        files={"file": (filename, BytesIO(png_bytes), "image/png")},
        data={"purpose": "image", "ref": filename},
    )
    if response.is_error:
        log.error("ghost image upload → %s %s", response.status_code, response.text[:500])
    response.raise_for_status()
    images = response.json().get("images") or []
    if not images or not images[0].get("url"):
        raise RuntimeError("Ghost image upload returned no url")
    return images[0]["url"]


def update_post(post_id: str, updated_at: str, fields: dict[str, Any]) -> dict[str, Any]:
    payload = {"posts": [{**fields, "updated_at": updated_at}]}
    return _ghost("PUT", f"posts/{post_id}/", json=payload)["posts"][0]


def _hf_client() -> InferenceClient:
    if not HF_TOKEN:
        raise RuntimeError("Missing HF_TOKEN")
    if not HF_TOKEN.startswith("hf_"):
        raise RuntimeError("HF_TOKEN must start with hf_ (check for typos in .env / GitHub secret)")
    return InferenceClient(api_key=HF_TOKEN)


def _chat(system: str, user: str, *, max_tokens: int) -> str:
    client = _hf_client()
    completion = client.chat.completions.create(
        model=HF_TEXT_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        max_tokens=max_tokens,
        temperature=0.3,
    )
    choice = completion.choices[0].message.content or ""
    return choice.strip()


def generate_excerpt(title: str, body: str) -> str:
    system = (
        "You write short SEO / social meta descriptions for blog posts. "
        f"Reply with ONE plain sentence in the same language as the article. "
        f"Hard limit: at most {MAX_EXCERPT_LEN} characters including spaces. "
        "No quotes, no hashtags, no emoji, no title prefix."
    )
    user = f"Title: {title}\n\nArticle:\n{body[:_MAX_ARTICLE_CHARS]}"
    return truncate_excerpt(_chat(system, user, max_tokens=120))


def generate_image_prompt(title: str, body: str) -> str:
    system = (
        "You write English prompts for a text-to-image model. "
        "Describe a single editorial photograph or illustration that matches the article topic. "
        "Rules: no text, letters, logos, watermarks, or UI on the image; "
        "no people with readable name tags; photorealistic or clean illustration; "
        "one coherent scene. Reply with the prompt only, under 400 characters."
    )
    user = f"Title: {title}\n\nArticle:\n{body[:_MAX_ARTICLE_CHARS]}"
    prompt = _chat(system, user, max_tokens=200)
    prompt = re.sub(r"\s+", " ", prompt.strip().strip("\"'"))
    # Reinforce no-text constraint for the image model
    if "no text" not in prompt.lower():
        prompt = f"{prompt}, no text, no letters, no watermark"
    return prompt[:500]


def generate_image(prompt: str) -> bytes:
    headers = {"Content-Type": "application/json"}
    if BONSAI_TOKEN:
        headers["Authorization"] = f"Bearer {BONSAI_TOKEN}"
    payload = {
        "prompt": prompt,
        "seed": BONSAI_SEED,
        "steps": BONSAI_STEPS,
        "backend": BONSAI_BACKEND,
        "width": BONSAI_WIDTH,
        "height": BONSAI_HEIGHT,
        "guidance": 1.0,
    }
    response = http.post(f"{BONSAI_URL}/generate", headers=headers, json=payload)
    if response.is_error:
        log.error("bonsai generate → %s %s", response.status_code, response.text[:500])
    response.raise_for_status()
    ctype = response.headers.get("content-type", "")
    if "image" not in ctype and not response.content.startswith(b"\x89PNG"):
        raise RuntimeError(f"Bonsai returned non-image content-type={ctype!r}")
    return response.content


def process_post(post: dict[str, Any]) -> dict[str, Any]:
    post_id = post["id"]
    title = post.get("title") or "Untitled"
    body = html_to_text(post.get("html") or "")
    if len(body) < 40:
        return {"id": post_id, "title": title, "skipped": True, "reason": "body too short"}

    if not needs_prep(post):
        return {"id": post_id, "title": title, "skipped": True, "reason": "already complete"}

    fields: dict[str, Any] = {}
    excerpt = (post.get("custom_excerpt") or "").strip()
    if not excerpt:
        excerpt = generate_excerpt(title, body)
        fields["custom_excerpt"] = excerpt
        fields["meta_description"] = excerpt
        fields["og_description"] = excerpt
        fields["twitter_description"] = excerpt

    image_url = post.get("feature_image")
    if not image_url:
        prompt = generate_image_prompt(title, body)
        log.info("image prompt for %s: %s", post_id, prompt[:160])
        png = generate_image(prompt)
        slug = re.sub(r"[^a-z0-9]+", "-", (post.get("slug") or post_id).lower()).strip("-") or post_id
        image_url = upload_image(png, f"prep-{slug}.png")
        fields["feature_image"] = image_url
        fields["og_image"] = image_url
        fields["twitter_image"] = image_url

    if not fields:
        return {"id": post_id, "title": title, "skipped": True, "reason": "nothing to update"}

    saved = update_post(post_id, post["updated_at"], fields)
    return {
        "id": post_id,
        "title": title,
        "updated": True,
        "excerpt": fields.get("custom_excerpt") or excerpt,
        "image": fields.get("feature_image") or image_url,
        "slug": saved.get("slug"),
    }


def run() -> dict[str, Any]:
    for name, value in {
        "GHOST_URL": GHOST_URL,
        "GHOST_ADMIN_API_KEY": GHOST_KEY,
        "HF_TOKEN": HF_TOKEN,
    }.items():
        if not value:
            raise RuntimeError(f"Missing {name}")

    run_started_at = datetime.now(timezone.utc)
    last_run_at = read_last_run()
    if last_run_at is None:
        log.info("first run — no state yet, baseline only (no drafts processed)")
        write_last_run(run_started_at)
        return {
            "since": None,
            "first_run": True,
            "drafts": 0,
            "updated": 0,
            "skipped": 0,
            "errors": 0,
            "results": [],
        }

    since_iso = to_ghost_filter_date(last_run_at)
    log.info("collecting drafts updated after %s", since_iso)
    drafts = list_drafts(last_run_at)
    log.info("found %s draft(s) in window", len(drafts))
    results: list[dict[str, Any]] = []
    for i, post in enumerate(drafts):
        try:
            result = process_post(post)
            results.append(result)
            log.info("post %s: %s", post.get("id"), result)
        except Exception as exc:
            log.exception("post %s failed", post.get("id"))
            results.append({"id": post.get("id"), "title": post.get("title"), "error": str(exc)})
        if i + 1 < len(drafts):
            time.sleep(2)

    errors = sum(1 for r in results if r.get("error"))
    if errors:
        log.warning("not updating last-run — %s error(s), will retry same window next run", errors)
    else:
        write_last_run(run_started_at)

    return {
        "since": since_iso,
        "first_run": False,
        "drafts": len(drafts),
        "updated": sum(1 for r in results if r.get("updated")),
        "skipped": sum(1 for r in results if r.get("skipped")),
        "errors": errors,
        "results": results,
    }


def _self_check() -> None:
    assert truncate_excerpt("a" * 10, 146) == "a" * 10
    assert len(truncate_excerpt("word " * 50, 146)) <= 146
    assert "…" in truncate_excerpt("alpha beta gamma delta", 12)
    assert html_to_text("<p>Hello <b>world</b></p><script>x</script>") == "Hello world"
    assert needs_prep({"custom_excerpt": "", "feature_image": None}) is True
    assert needs_prep({"custom_excerpt": "x", "feature_image": "https://x/y.png"}) is False
    when = datetime(2026, 7, 17, 6, 0, 0, tzinfo=timezone.utc)
    assert to_ghost_filter_date(when) == "2026-07-17T06:00:00.000Z"
    log.info("self-check ok")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--self-check":
        _self_check()
    else:
        _self_check()
        summary = run()
        log.info(
            "done: drafts=%s updated=%s skipped=%s errors=%s",
            summary["drafts"],
            summary["updated"],
            summary["skipped"],
            summary["errors"],
        )
        if summary["errors"]:
            sys.exit(1)
