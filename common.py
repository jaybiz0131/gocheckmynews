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


# THE FETCH LAYER IS THE CONTENT LAYER (family audit 2026-09-02). The verifier's rule
# is absolute: a source the desk could not READ can never be VERIFIED, and a story that
# is not VERIFIED never auto-publishes. So every fetch that fails for a mechanical reason
# is a story lost, and the run logs were full of mechanical reasons: CoinDesk answering
# 429 to the sixth request in ten seconds, Google News 503 on three feeds in one run,
# 200-byte challenge stubs, a 200KB read cap that cut PBS pages before their prose
# closed. Three fixes, all honest (the desk's own UA, no disguises):
#   - per-host spacing: at least HOST_GAP seconds between requests to one host, so a
#     run reading twelve stories from one outlet is a polite reader, not a burst;
#   - retry: 429 and 5xx and timeouts get two more tries with backoff (and Retry-After
#     when the server names it); 4xx other than 429 are final, as before;
#   - the headers a browser sends with every request (Accept, Accept-Language), which
#     several CDNs require before they will serve the article markup at all.
HOST_GAP = 1.2
RETRY_STATUSES = {429, 500, 502, 503, 504, 520, 521, 522, 524}
_LAST_HIT = {}


def _polite_wait(url):
    import time as _t
    from urllib.parse import urlparse
    host = (urlparse(url or "").netloc or "").lower()
    if not host:
        return
    last = _LAST_HIT.get(host)
    now = _t.monotonic()
    if last is not None and now - last < HOST_GAP:
        _t.sleep(HOST_GAP - (now - last))
    _LAST_HIT[host] = _t.monotonic()


def fetch_page_meta(url, timeout=25, retries=2):
    """Fetch a URL and return the WHOLE story of the fetch, never raising:
    {status, final_url, content_type, bytes, body, error, attempts}.

    WHY THIS EXISTS (owner report 2026-08-25): "0 chars" had become the desks' entire
    diagnostic. The blanket except below collapsed a 403 challenge, a paywall, a
    JS-only shell, a redirect loop and a timeout into the same two words, and nobody
    could fix what the log did not name. An HTTPError in particular carries the real
    status and usually a challenge body; both are kept now.
    """
    import time as _t
    meta = {"status": None, "final_url": url, "content_type": "", "bytes": 0,
            "body": "", "error": "", "attempts": 0}
    headers = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    delay = 3.0
    for attempt in range(retries + 1):
        meta["attempts"] = attempt + 1
        _polite_wait(url)
        retry_after = None
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                # 600KB, not 200KB (Kyiv-strike audit 2026-08-31): a live PBS NewsHour
                # article runs ~262KB with its first closing </p> past byte 221,560,
                # so the old cap cut every PBS page before its prose closed.
                raw = r.read(600000)
                meta.update(status=r.getcode(), final_url=r.geturl() or url,
                            content_type=r.headers.get("Content-Type", ""),
                            bytes=len(raw), body=raw.decode("utf-8", "replace"),
                            error="")
            return meta
        except urllib.error.HTTPError as e:
            try:
                raw = e.read(200000)
            except Exception:
                raw = b""
            meta.update(status=e.code, final_url=getattr(e, "url", url) or url,
                        content_type=(e.headers.get("Content-Type", "") if e.headers else ""),
                        bytes=len(raw), body=raw.decode("utf-8", "replace"),
                        error=f"HTTP {e.code}")
            if e.code not in RETRY_STATUSES:
                return meta
            try:
                retry_after = float((e.headers or {}).get("Retry-After") or 0) or None
            except (TypeError, ValueError):
                retry_after = None
        except Exception as e:
            meta["error"] = f"fetch failed: {e}"
        if attempt < retries:
            wait = min(15.0, retry_after or delay)
            print(f"  fetch: {meta.get('error') or 'error'} on {url[:80]}; retry "
                  f"{attempt + 1}/{retries} in {wait:.0f}s")
            _t.sleep(wait)
            delay *= 2
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


def npr_text_fallback(url, cap=6000):
    """Full article text for a www.npr.org story via NPR's own text-only site,
    https://text.npr.org/<id> (keyless; serves the desk UA, verified 2026-08-31).
    npr.org itself tarpits the desk's bot UA (the Kyiv-strike audit reproduced a 40s+
    timeout on a page a browser UA got in 3.7s), so the scrape path times out or comes
    back thin while the outlet's own text mirror carries the published prose. Same
    honest-fetch posture as guardian_api_text: the outlet's own alternate surface, our
    UA, no disguises. Returns '' for any non-NPR URL or any failure, so every caller
    can treat this as a best-effort upgrade."""
    try:
        parsed = urllib.parse.urlparse(url)
        host = parsed.netloc.lower()
        if host not in ("npr.org", "www.npr.org"):
            return ""
        m = re.match(r"^/\d{4}/\d{2}/\d{2}/([^/]+)(?:/|$)", parsed.path)
        if not m:
            return ""
        code, body = fetch_page(f"https://text.npr.org/{m.group(1)}")
        if code != 200:
            return ""
        return extract_article_text(body, cap=cap)
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



def og_description(html_body):
    """The page's own og:description / twitter:description, or ''. The publisher's own
    one-line summary of their own story, served in the same response (owner audit
    2026-08-25)."""
    import html as _h
    for pat in (r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']{40,})',
                r'<meta[^>]+content=["\']([^"\']{40,})["\'][^>]+property=["\']og:description["\']',
                r'<meta[^>]+name=["\']twitter:description["\'][^>]+content=["\']([^"\']{40,})'):
        m = re.search(pat, html_body or "", re.I)
        if m:
            return _h.unescape(m.group(1)).strip()
    return ""


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
    open_split = False
    if not paras:
        # A capped fetch can truncate a page before its first closing </p> (PBS
        # closes its prose past 220KB), leaving open <p> tags with no closed pair;
        # the text between consecutive open tags is the same prose. Each segment
        # still faces the per-line boilerplate cut, and the joined result faces
        # the sentence-density gate below, so a truncated JS shell stays empty.
        parts = re.split(r"(?is)<p\b[^>]*>", scope)
        if len(parts) > 1:
            paras = parts[1:]
            open_split = True
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
        # the open-tag segments carry whatever markup sat between paragraphs, so the
        # same sentence-density gate as the naive strip applies to them
        if open_split and len(text) > 400 and (
                text.count(". ") + text.count("! ") + text.count("? ")
                ) < max(3, len(text) // 400):
            text = ""
    if len(text) < 400:
        ld = ldjson_article_body(html_body)
        if len(ld) > len(text):
            text = ld
    if len(text) < 400:
        nd = next_data_text(html_body)
        if len(nd) > len(text):
            text = nd
    if len(text) < 200:
        og = og_description(html_body)
        if len(og) > len(text):
            text = og
    return text[:cap]


def next_data_text(html_body, cap=6000):
    """Prose embedded in a Next.js page's __NEXT_DATA__ JSON, or ''. Client-rendered
    outlets (Decrypt, and the class that returns 200 with 130 extractable chars) ship
    the article body inside this script block for hydration: the publisher's own text,
    in the same response, invisible to a <p> scan. Strings that read as prose (long,
    with sentences) are collected in document order; a malformed block yields ''."""
    m = re.search(r'(?is)<script[^>]*id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
                  html_body or "")
    if not m:
        return ""
    try:
        data = json.loads(m.group(1).strip())
    except Exception:
        return ""
    out, seen = [], set()
    stack = [data]
    while stack and sum(len(x) for x in out) < cap * 2:
        node = stack.pop(0)
        if isinstance(node, dict):
            stack.extend(v for v in node.values() if isinstance(v, (dict, list, str)))
        elif isinstance(node, list):
            stack.extend(v for v in node if isinstance(v, (dict, list, str)))
        elif isinstance(node, str) and len(node) >= 120:
            t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", node)).strip()
            if (t.count(". ") + t.count("? ") + t.count("! ") + (1 if t.endswith(".") else 0)) < 2:
                continue
            if "{" in t[:5] or t.lower().startswith(("http", "//")):
                continue
            if t in seen:
                continue
            seen.add(t)
            out.append(t)
    return "\n".join(out)[:cap]


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
