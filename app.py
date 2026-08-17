"""Prep Ghost draft posts: strip AI Unicode marks, then SEO/social excerpt."""

from __future__ import annotations

import html
import json
import logging
import os
import re
import time
import unicodedata
from datetime import datetime, timezone
from html.parser import HTMLParser
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

MAX_EXCERPT_LEN = int(_env("MAX_EXCERPT_LEN", "146"))
SKIP_COMPLETE = _env("SKIP_COMPLETE", "1") not in ("0", "false", "False")
STATE_FILE = Path(_env("STATE_FILE", "state/last-run.json"))
TAG_STATE_FILE = Path(_env("TAG_STATE_FILE", "state/current-tag.json"))

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


def read_current_tag_slug() -> str | None:
    if not TAG_STATE_FILE.exists():
        return None
    try:
        data = json.loads(TAG_STATE_FILE.read_text(encoding="utf-8"))
        slug = (data.get("currentTagSlug") or "").strip()
        return slug or None
    except (json.JSONDecodeError, OSError, TypeError):
        log.warning("invalid tag state file %s — treating as unset", TAG_STATE_FILE)
        return None


def write_current_tag_slug(slug: str) -> None:
    TAG_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {"currentTagSlug": slug}
    TAG_STATE_FILE.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def admin_tag_posts_url(slug: str) -> str:
    return f"{GHOST_URL}/ghost/#/posts?tag={slug}"


def list_tags() -> list[dict[str, Any]]:
    tags: list[dict[str, Any]] = []
    page = 1
    while True:
        data = _ghost(
            "GET",
            "tags/",
            params={"order": "name asc", "limit": 100, "page": page},
        )
        tags.extend(data["tags"])
        pagination = data.get("meta", {}).get("pagination", {})
        if page >= pagination.get("pages", 1):
            break
        page += 1
    return tags


def resolve_tag_slug(raw: str, tags: list[dict[str, Any]]) -> str | None:
    needle = raw.strip()
    if not needle:
        return None
    by_slug = {t["slug"]: t for t in tags}
    if needle in by_slug:
        return needle
    lowered = needle.casefold()
    for tag in tags:
        if tag.get("name", "").casefold() == lowered:
            return tag["slug"]
    return None


def next_tag_after(current_slug: str | None, tags: list[dict[str, Any]]) -> dict[str, Any]:
    if not tags:
        raise RuntimeError("Ghost returned no tags")
    ordered = sorted(tags, key=lambda t: t.get("name", "").casefold())
    if current_slug is None:
        return ordered[0]
    slugs = [t["slug"] for t in ordered]
    if current_slug not in slugs:
        log.warning("current tag slug %r not in Ghost — using first tag", current_slug)
        return ordered[0]
    idx = slugs.index(current_slug)
    return ordered[(idx + 1) % len(ordered)]


def run_tag_rotation(
    *,
    set_current_slug: str | None = None,
    set_only: bool = False,
) -> dict[str, Any]:
    if set_only and not set_current_slug:
        raise RuntimeError("--set-only requires --set-current-tag")

    for name, value in {"GHOST_URL": GHOST_URL, "GHOST_ADMIN_API_KEY": GHOST_KEY}.items():
        if not value:
            raise RuntimeError(f"Missing {name}")

    tags = list_tags()
    stored_slug = read_current_tag_slug()
    current_slug = stored_slug

    if set_current_slug:
        resolved = resolve_tag_slug(set_current_slug, tags)
        if not resolved:
            known = ", ".join(t["slug"] for t in tags[:20])
            raise RuntimeError(f"Unknown tag {set_current_slug!r} (sample slugs: {known})")
        current_slug = resolved
        write_current_tag_slug(resolved)
        log.info("current tag set to %s", resolved)
        if set_only:
            tag = next(t for t in tags if t["slug"] == resolved)
            return {
                "mode": "set_only",
                "current": {"name": tag["name"], "slug": tag["slug"]},
                "postsUrl": admin_tag_posts_url(tag["slug"]),
                "nextWouldBe": next_tag_after(resolved, tags),
            }

    suggested = next_tag_after(current_slug, tags)
    write_current_tag_slug(suggested["slug"])
    return {
        "mode": "rotate",
        "previousSlug": current_slug,
        "suggested": {
            "name": suggested["name"],
            "slug": suggested["slug"],
            "postsUrl": admin_tag_posts_url(suggested["slug"]),
        },
    }


def format_tag_rotation_summary(result: dict[str, Any]) -> str:
    if result.get("mode") == "set_only":
        current = result["current"]
        nxt = result["nextWouldBe"]
        return (
            f"## Current tag updated\n\n"
            f"**{current['name']}** (`{current['slug']}`)\n\n"
            f"[Posts]({result['postsUrl']})\n\n"
            f"Next rotation will suggest **{nxt['name']}** (`{nxt['slug']}`)."
        )
    suggested = result["suggested"]
    prev = result.get("previousSlug") or "(none)"
    return (
        f"## Suggested tag\n\n"
        f"**{suggested['name']}** (`{suggested['slug']}`)\n\n"
        f"[Open posts in Ghost Admin]({suggested['postsUrl']})\n\n"
        f"Previous current tag: `{prev}`"
    )


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


# Layer A Unicode carriers from guillaumemeyer/watermarks-remover (MIT).
# ponytail: lossless scrub only. Layer B paraphrase would rewrite the post;
# image C2PA needs Ghost re-upload. Upgrade: HF rewrite + image round-trip.
_STRIP_CPS = frozenset(
    {
        0x00AD, 0x034F, 0x061C, 0x115F, 0x1160, 0x17B4, 0x17B5,
        0x180B, 0x180C, 0x180D, 0x180E,
        0x200B, 0x200C, 0x200D, 0x200E, 0x200F,
        0x202A, 0x202B, 0x202C, 0x202D, 0x202E,
        0x2060, 0x2061, 0x2062, 0x2063, 0x2064,
        0x2066, 0x2067, 0x2068, 0x2069, 0x206A, 0x206B, 0x206C, 0x206D, 0x206E, 0x206F,
        0xFEFF,
        *range(0xFE00, 0xFE10),
        0xFFF9, 0xFFFA, 0xFFFB,
    }
)
_EMOJI_GLUE = frozenset({0x200D, 0xFE0E, 0xFE0F})
_DATA_AI_ATTR = re.compile(r"\sdata-ai[\w-]*\s*=\s*[\"'][^\"']*[\"']", re.I)


def _is_emoji_base(cp: int) -> bool:
    if 0x1F000 <= cp <= 0x1FAFF or 0x2600 <= cp <= 0x27BF or 0x2B00 <= cp <= 0x2BFF:
        return True
    if cp in (0x00A9, 0x00AE, 0x2122, 0x3030, 0x303D, 0x3297, 0x3299, 0x0023, 0x002A):
        return True
    return 0x0030 <= cp <= 0x0039


def _is_mark_cp(cp: int) -> bool:
    if cp in _STRIP_CPS:
        return True
    if 0xE0100 <= cp <= 0xE01EF or 0xE0001 <= cp <= 0xE007F:
        return True
    return unicodedata.category(chr(cp)) == "Cf"


def scrub_ai_marks(text: str) -> tuple[str, int]:
    """Strip invisible Unicode watermarks. Keep emoji ZWJ/VS and NBSP."""
    out: list[str] = []
    prev: str | None = None
    removed = 0
    for ch in text or "":
        cp = ord(ch)
        if cp in _EMOJI_GLUE and prev is not None and _is_emoji_base(ord(prev)):
            out.append(ch)
            continue
        if _is_mark_cp(cp):
            removed += 1
            continue
        out.append(ch)
        prev = ch
    return "".join(out), removed


def scrub_post_html(raw_html: str) -> tuple[str, int]:
    cleaned, removed = scrub_ai_marks(raw_html or "")
    cleaned, n_attr = _DATA_AI_ATTR.subn("", cleaned)
    return cleaned, removed + n_attr


def truncate_excerpt(text: str, limit: int = MAX_EXCERPT_LEN) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip().strip("\"'"))
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(".,;:!-—") + "…"


def needs_excerpt(post: dict[str, Any]) -> bool:
    if not SKIP_COMPLETE:
        return True
    return not bool((post.get("custom_excerpt") or "").strip())


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


def update_post(post_id: str, updated_at: str, fields: dict[str, Any]) -> dict[str, Any]:
    payload = {"posts": [{**fields, "updated_at": updated_at}]}
    kwargs: dict[str, Any] = {"json": payload}
    if "html" in fields:
        kwargs["params"] = {"source": "html"}
    return _ghost("PUT", f"posts/{post_id}/", **kwargs)["posts"][0]


def _hf_client() -> InferenceClient:
    if not HF_TOKEN:
        raise RuntimeError("Missing HF_TOKEN")
    if not HF_TOKEN.startswith("hf_"):
        raise RuntimeError("HF_TOKEN must start with hf_ (check for typos in .env / GitHub secret)")
    return InferenceClient(api_key=HF_TOKEN)


def generate_excerpt(title: str, body: str) -> str:
    system = (
        "You write short SEO / social meta descriptions for blog posts. "
        f"Reply with ONE plain sentence in the same language as the article. "
        f"Hard limit: at most {MAX_EXCERPT_LEN} characters including spaces. "
        "No quotes, no hashtags, no emoji, no title prefix."
    )
    user = f"Title: {title}\n\nArticle:\n{body[:_MAX_ARTICLE_CHARS]}"
    client = _hf_client()
    completion = client.chat.completions.create(
        model=HF_TEXT_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        max_tokens=120,
        temperature=0.3,
    )
    text = (completion.choices[0].message.content or "").strip()
    return truncate_excerpt(text)


def process_post(post: dict[str, Any]) -> dict[str, Any]:
    post_id = post["id"]
    title = post.get("title") or "Untitled"
    html_raw = post.get("html") or ""
    html_clean, html_marks = scrub_post_html(html_raw)
    title_clean, title_marks = scrub_ai_marks(title)
    marks_removed = html_marks + title_marks
    body = html_to_text(html_clean)
    excerpt_needed = needs_excerpt(post)

    if len(body) < 40 and not marks_removed:
        return {"id": post_id, "title": title, "skipped": True, "reason": "body too short"}
    if not excerpt_needed and not marks_removed:
        return {"id": post_id, "title": title, "skipped": True, "reason": "already complete"}

    fields: dict[str, Any] = {}
    excerpt = ""
    if excerpt_needed and len(body) >= 40:
        excerpt = generate_excerpt(title_clean, body)
        fields.update(
            {
                "custom_excerpt": excerpt,
                "meta_description": excerpt,
                "og_description": excerpt,
                "twitter_description": excerpt,
            }
        )
    if html_clean != html_raw:
        fields["html"] = html_clean
    if title_clean != title:
        fields["title"] = title_clean
    if not fields:
        return {"id": post_id, "title": title, "skipped": True, "reason": "already complete"}

    saved = update_post(post_id, post["updated_at"], fields)
    result: dict[str, Any] = {
        "id": post_id,
        "title": title_clean,
        "updated": True,
        "slug": saved.get("slug"),
    }
    if excerpt:
        result["excerpt"] = excerpt
    if marks_removed:
        result["marks_removed"] = marks_removed
    return result


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
            time.sleep(1)

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
    assert needs_excerpt({"custom_excerpt": ""}) is True
    assert needs_excerpt({"custom_excerpt": "x"}) is False
    when = datetime(2026, 7, 17, 6, 0, 0, tzinfo=timezone.utc)
    assert to_ghost_filter_date(when) == "2026-07-17T06:00:00.000Z"
    assert scrub_ai_marks("Hello\u200b world") == ("Hello world", 1)
    assert scrub_ai_marks("a\u00a0b") == ("a\u00a0b", 0)
    family = "👨‍👩‍👧"
    assert scrub_ai_marks(family) == (family, 0)
    html_out, n = scrub_post_html('<p data-ai-generated="yes">Hi\u200b</p>')
    assert n == 2 and "data-ai" not in html_out and "\u200b" not in html_out
    sample_tags = [
        {"name": "Actiondesk", "slug": "actiondesk"},
        {"name": "Active@ Partition Manager", "slug": "active-partition-manager"},
        {"name": "Zeta", "slug": "zeta"},
    ]
    nxt = next_tag_after("actiondesk", sample_tags)
    assert nxt["slug"] == "active-partition-manager"
    assert next_tag_after("zeta", sample_tags)["slug"] == "actiondesk"
    assert resolve_tag_slug("Active@ Partition Manager", sample_tags) == "active-partition-manager"
    log.info("self-check ok")


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Ghost draft prep and tag rotation")
    parser.add_argument("--self-check", action="store_true", help="run helper self-check only")
    parser.add_argument("--tag-rotate", action="store_true", help="suggest next Ghost tag")
    parser.add_argument(
        "--set-current-tag",
        metavar="SLUG_OR_NAME",
        help="set current tag (slug or exact name); use with --set-only to skip rotation",
    )
    parser.add_argument(
        "--set-only",
        action="store_true",
        help="with --set-current-tag, update state without advancing to the next tag",
    )
    args = parser.parse_args()

    if args.self_check:
        _self_check()
        sys.exit(0)

    if args.tag_rotate or args.set_current_tag:
        _self_check()
        result = run_tag_rotation(
            set_current_slug=args.set_current_tag,
            set_only=args.set_only,
        )
        summary_md = format_tag_rotation_summary(result)
        log.info("%s", summary_md.replace("## ", "").replace("**", "").replace("\n\n", "\n"))
        summary_path = os.getenv("GITHUB_STEP_SUMMARY")
        if summary_path:
            Path(summary_path).write_text(summary_md + "\n", encoding="utf-8")
        sys.exit(0)

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
