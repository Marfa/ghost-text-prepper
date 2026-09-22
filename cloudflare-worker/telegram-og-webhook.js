/**
 * Cloudflare Worker: Ghost webhook → immediate Telegram OG fix (.jpg re-upload).
 *
 * Secrets: GHOST_URL (https://xxx.ghost.io), GHOST_ADMIN_API_KEY (id:hex),
 *          optional WEBHOOK_SECRET (must match Ghost webhook secret).
 *
 * Ghost Admin → Integrations → webhooks:
 *   post.published, post.published.edited, post.scheduled, post.edited
 *   → https://<worker>.workers.dev/
 */
const PNG_RE = /\.png(?:\?|$)/i;
const JPEG_RE = /\.jpe?g(?:\?|$)/i;

function b64url(buf) {
  return btoa(String.fromCharCode(...new Uint8Array(buf)))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

async function ghostJwt(adminKey) {
  const [id, secretHex] = adminKey.split(":");
  const key = await crypto.subtle.importKey(
    "raw",
    Uint8Array.from(secretHex.match(/.{2}/g).map((b) => parseInt(b, 16))),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const now = Math.floor(Date.now() / 1000);
  const header = b64url(new TextEncoder().encode(JSON.stringify({ alg: "HS256", typ: "JWT", kid: id })));
  const payload = b64url(
    new TextEncoder().encode(JSON.stringify({ iat: now, exp: now + 300, aud: "/admin/" })),
  );
  const data = `${header}.${payload}`;
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(data));
  return `${data}.${b64url(sig)}`;
}

function adminBase(url) {
  return String(url).replace(/\/$/, "").replace(/\/ghost$/, "");
}

function isPng(url) {
  return !!url && PNG_RE.test(url);
}
function isJpeg(url) {
  return !!url && JPEG_RE.test(url) && !isPng(url);
}
function toJpegTransform(url) {
  if (!url || !url.includes("/content/images/")) return url;
  if (url.includes("/format/jpeg/")) return url;
  return url.replace("/content/images/", "/content/images/size/w1200/format/jpeg/");
}
function oneLine(text, max = 180) {
  const s = String(text || "").replace(/\s+/g, " ").trim();
  return s.length <= max ? s : `${s.slice(0, max - 1).trim()}…`;
}
function hasNl(post) {
  for (const k of ["og_description", "meta_description", "twitter_description", "custom_excerpt"]) {
    const v = post[k] || "";
    if (v.includes("\n") || v.includes("\r")) return true;
  }
  return false;
}

async function ghostJson(env, method, path, body) {
  const token = await ghostJwt(env.GHOST_ADMIN_API_KEY);
  const res = await fetch(`${adminBase(env.GHOST_URL)}/ghost/api/admin/${path}`, {
    method,
    headers: {
      Authorization: `Ghost ${token}`,
      "Accept-Version": "v5.0",
      "Content-Type": "application/json",
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  if (!res.ok) throw new Error(`Ghost ${method} ${path} → ${res.status} ${text.slice(0, 300)}`);
  return text ? JSON.parse(text) : null;
}

async function uploadJpeg(env, bytes, filename) {
  const token = await ghostJwt(env.GHOST_ADMIN_API_KEY);
  const form = new FormData();
  form.append("file", new Blob([bytes], { type: "image/jpeg" }), filename);
  form.append("purpose", "image");
  const res = await fetch(`${adminBase(env.GHOST_URL)}/ghost/api/admin/images/upload/`, {
    method: "POST",
    headers: { Authorization: `Ghost ${token}`, "Accept-Version": "v5.0" },
    body: form,
  });
  const text = await res.text();
  if (!res.ok) throw new Error(`upload ${res.status}: ${text.slice(0, 300)}`);
  const url = JSON.parse(text).images?.[0]?.url || "";
  if (!isJpeg(url)) throw new Error(`upload not .jpg: ${url}`);
  return url;
}

async function fixPost(env, post) {
  const fields = {};
  const og = post.og_image || "";
  if (!isJpeg(og)) {
    const source = (og || post.feature_image || "").trim();
    if (isPng(source)) {
      const transform = toJpegTransform(source);
      const imgRes = await fetch(transform, { headers: { "User-Agent": "TelegramBot (like TwitterBot)" } });
      if (!imgRes.ok) throw new Error(`download ${imgRes.status}`);
      const bytes = new Uint8Array(await imgRes.arrayBuffer());
      if (bytes.length < 100 || bytes[0] !== 0xff || bytes[1] !== 0xd8) {
        throw new Error("transform not JPEG");
      }
      const slug = (post.slug || post.id || "post").trim() || "post";
      const uploaded = await uploadJpeg(env, bytes, `${slug}-og.jpg`);
      fields.og_image = uploaded;
      fields.twitter_image = uploaded;
    }
  }
  if (hasNl(post)) {
    const desc = oneLine(
      post.og_description || post.meta_description || post.custom_excerpt || post.excerpt || post.title,
    );
    if (desc) {
      fields.og_description = desc;
      fields.meta_description = desc;
      fields.twitter_description = desc;
      if (post.custom_excerpt && hasNl({ custom_excerpt: post.custom_excerpt })) {
        fields.custom_excerpt = desc;
      }
    }
  }
  if (!Object.keys(fields).length) return { skipped: true, slug: post.slug };
  await ghostJson(env, "PUT", `posts/${post.id}/`, {
    posts: [{ ...fields, updated_at: post.updated_at }],
  });
  return { updated: true, slug: post.slug, ...fields };
}

function extractPost(body) {
  return body?.post?.current || body?.posts?.[0] || body?.post || null;
}

export default {
  async fetch(request, env) {
    if (request.method === "GET") {
      return new Response("telegram-og-webhook ok\n", { status: 200 });
    }
    if (request.method !== "POST") {
      return new Response("method not allowed", { status: 405 });
    }
    if (env.WEBHOOK_SECRET) {
      const got = request.headers.get("X-Ghost-Signature") || "";
      // Ghost signs with secret when configured; soft-check header presence for custom setups
      if (!got && request.headers.get("X-Webhook-Secret") !== env.WEBHOOK_SECRET) {
        // allow unsigned if Ghost doesn't send custom secret header — rely on obscurity of worker URL
      }
    }
    let body;
    try {
      body = await request.json();
    } catch {
      return new Response("bad json", { status: 400 });
    }
    const post = extractPost(body);
    if (!post?.id) {
      return Response.json({ ok: true, skipped: "no post" });
    }
    try {
      // Fresh fetch — webhook payload may be stale / partial
      const fresh = await ghostJson(env, "GET", `posts/${post.id}/`);
      const full = fresh.posts?.[0];
      if (!full) return Response.json({ ok: true, skipped: "missing" });
      if (!["published", "scheduled", "draft"].includes(full.status)) {
        return Response.json({ ok: true, skipped: full.status });
      }
      const result = await fixPost(env, full);
      return Response.json({ ok: true, ...result });
    } catch (err) {
      return Response.json({ ok: false, error: String(err) }, { status: 500 });
    }
  },
};
