#!/usr/bin/env python3
"""common.py: shared helpers for the GoCheckMyNews pipeline stages."""

import json
import os
import re
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "out")
PROMPTS = os.path.join(HERE, "prompts")
CONFIG = os.path.join(HERE, "config.json")
UA = "GoCheckMyNews/1.0 (+news pipeline; +https://gocheckmynews.com)"


def gh(level, msg):
    """GitHub Actions annotation, also readable in a plain terminal."""
    print(f"::{level}::{msg}")


def load_config():
    return json.load(open(CONFIG, encoding="utf-8"))


def load_prompt(name, **subs):
    text = open(os.path.join(PROMPTS, name), encoding="utf-8").read()
    for k, v in subs.items():
        text = text.replace("{" + k + "}", str(v))
    return text


def read_out(name):
    return json.load(open(os.path.join(OUT_DIR, name), encoding="utf-8"))


def write_out(name, obj):
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, name)
    json.dump(obj, open(path, "w", encoding="utf-8"), indent=2)
    return path


def fetch_text(url, timeout=25):
    """Fetch a URL and return (http_status, plain_text_excerpt). Never raises; on failure
    returns (None, error string) so the verifier can treat unreachable as unconfirmed."""
    code, body = fetch_page(url, timeout=timeout)
    if code is None:
        return code, body
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)).strip()
    return code, text


def fetch_page_meta(url, timeout=25):
    """Fetch a URL and return the WHOLE story of the fetch, never raising:
    {status, final_url, content_type, bytes, body, error}.

    WHY THIS EXISTS (owner report 2026-08-25): "0 chars" had become the desks' entire
    diagnostic. The blanket except below collapsed a 403 challenge, a paywall, a
    JS-only shell, a redirect loop and a timeout into the same two words, and nobody
    could fix what the log did not name. An HTTPError in particular carries the real
    status and usually a challenge body; both are kept now.
    """
    meta = {"status": None, "final_url": url, "content_type": "", "bytes": 0,
            "body": "", "error": ""}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(200000)
            meta.update(status=r.getcode(), final_url=r.geturl() or url,
                        content_type=r.headers.get("Content-Type", ""),
                        bytes=len(raw), body=raw.decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            raw = e.read(200000)
        except Exception:
            raw = b""
        meta.update(status=e.code, final_url=getattr(e, "url", url) or url,
                    content_type=(e.headers.get("Content-Type", "") if e.headers else ""),
                    bytes=len(raw), body=raw.decode("utf-8", "replace"),
                    error=f"HTTP {e.code}")
    except Exception as e:
        meta["error"] = f"fetch failed: {e}"
    return meta


def fetch_page(url, timeout=25):
    """Fetch a URL and return (http_status, raw_html). Never raises; on failure returns
    (None, error string). Thin wrapper over fetch_page_meta, kept for every caller."""
    m = fetch_page_meta(url, timeout=timeout)
    if m["status"] is not None:
        return m["status"], m["body"] or m["error"]
    return None, m["error"]


def guardian_api_text(url, cap=6000):
    """Full article text for a theguardian.com URL via the Guardian Open Platform
    (GUARDIAN_API_KEY, developer tier, verified 2026-07-30). Guardian pages, like most
    major outlets, serve reduced markup to non-browser fetches, so the scrape path often
    comes back thin; the outlet's own API returns the article body it published. Returns
    '' for any non-Guardian URL, a missing key, or any failure, so every caller can treat
    this as a best-effort upgrade and fall through to the normal extraction path."""
    key = os.environ.get("GUARDIAN_API_KEY", "").strip()
    if not key:
        return ""
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
        if not (host == "theguardian.com" or host.endswith(".theguardian.com")):
            return ""
        path = urllib.parse.urlparse(url).path.strip("/")
        if not path:
            return ""
        api = (f"https://content.guardianapis.com/{path}"
               f"?show-fields=bodyText&api-key={urllib.parse.quote(key)}")
        req = urllib.request.Request(api, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=25) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        body = ((data.get("response", {}) or {}).get("content", {}) or {}) \
            .get("fields", {}).get("bodyText", "")
        return re.sub(r"\s+", " ", body).strip()[:cap]
    except Exception:
        return ""


def publisher_of(url):
    """The registrable domain behind a URL, used as PUBLISHER IDENTITY. A desk can carry
    several feeds from one publisher (ESPN NFL, ESPN MLB, ESPN Top Lines; BBC News and BBC
    World), and counting those as separate sources would claim corroboration the desk does
    not have. Measured 2026-07-31: 64% of apparently corroborated clusters were one
    publisher wearing two feed names. Independence is judged by this, never by feed name."""
    from urllib.parse import urlparse
    host = (urlparse(url or "").netloc or "").lower()
    host = host[4:] if host.startswith("www.") else host
    if not host:
        return ""
    parts = host.split(".")
    if len(parts) > 2 and parts[-2] in ("co", "com", "org", "net", "gov", "ac"):
        return ".".join(parts[-3:])   # bbc.co.uk
    return ".".join(parts[-2:])


def distinct_publishers(refs):
    """How many INDEPENDENT publishers back a set of source references. Accepts URLs
    (preferred: the domain is the publisher) and bare feed/outlet names (falls back to the
    normalized name), so callers holding either shape get the same independence semantics.
    Empty entries are ignored."""
    out = set()
    for r in refs or []:
        r = (r or "").strip()
        if not r:
            continue
        dom = publisher_of(r) if "//" in r or r.startswith("www.") else ""
        out.add(dom or r.lower())
    return len(out)


def extract_article_text(html_body, cap=6000):
    """Readability-lite article extraction, stdlib only. Prefers the <article> block if the
    page has one, else collects <p> contents; strips tags/scripts, unescapes entities, and
    drops short boilerplate lines (nav crumbs, cookie banners) so the researcher gets prose,
    not nav-soup. When the markup pass comes back thin (a client-rendered shell serves
    nearly no <p> prose), falls back to the page's own JSON-LD NewsArticle.articleBody,
    which most news CMSes embed server-side even when the visible HTML is a shell (same
    honest-fetch posture as the sports desk's ESPN content-API fallback: the page the
    outlet itself served, our UA, no disguises). Returns up to `cap` chars."""
    import html as html_mod
    if not html_body:
        return ""
    body = re.sub(r"(?is)<(script|style|noscript|nav|header|footer|aside)[^>]*>.*?</\1>",
                  " ", html_body)
    m = re.search(r"(?is)<article[^>]*>(.*?)</article>", body)
    scope = m.group(1) if m else body
    paras = re.findall(r"(?is)<p[^>]*>(.*?)</p>", scope)
    if not paras and m is None:
        # No <p> tags at all (some CMSes): fall back to the naive strip of the whole page.
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)).strip()
        # PROSE HAS SENTENCES (owner report 2026-08-25): a JS/CSS shell stripped of tags
        # can yield thousands of chars of selector soup, which then defeats every
        # downstream length gate as if it were source text. A long no-<p> extraction
        # must show minimal sentence density or it is not prose.
        if len(text) > 400 and (text.count(". ") + text.count("! ") + text.count("? ")
                                ) < max(3, len(text) // 400):
            text = ""
    else:
        out = []
        for p in paras:
            t = html_mod.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", p)).strip())
            if len(t) >= 40:  # boilerplate lines (menus, "Share this", bylines) run shorter
                out.append(t)
        text = "\n".join(out)
    if len(text) < 400:
        ld = ldjson_article_body(html_body)
        if len(ld) > len(text):
            text = ld
    return text[:cap]


def ldjson_article_body(html_body):
    """The longest articleBody found in any <script type="application/ld+json"> block on the
    page (plain text, whitespace-normalized), or '' when none parses. Walks nested
    structures (@graph wrappers, arrays) because outlets nest their NewsArticle object
    differently. A malformed block is skipped, never fatal."""
    import html as html_mod
    best = ""
    for m in re.finditer(r"(?is)<script[^>]*type\s*=\s*[\"']application/ld\+json[\"'][^>]*>"
                         r"(.*?)</script>", html_body or ""):
        try:
            data = json.loads(m.group(1).strip())
        except Exception:
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                b = node.get("articleBody")
                if isinstance(b, str) and len(b) > len(best):
                    best = b
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
    if not best:
        return ""
    return html_mod.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", best))).strip()
