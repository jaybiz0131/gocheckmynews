#!/usr/bin/env python3
"""
site_build.py: build the public GoCheckMyNews site from committed content.

Reproducible + lossless (the GoCheckMyPet lesson D2: everything the page needs is emitted
here from the templates, so rebuilding never strips the footer, disclaimer, or schema). Reads
site/content/*.json (one file per published item; _-prefixed files are ignored) and renders a
static deploy folder site/publish/: home, archive, one page per article, plus the static
editorial pages (about / how we work / standards / how we rate sources) and a 404. No
third-party dependency; no em dashes; the no-advocacy-no-advice disclaimer baked into every
article and the footer. Every cited source renders with its outlet credibility chip (bias
lane + factual grade from site/data/credibility.json, attributed to the public charts).

CONTENT FLOW
  A story is published only after a human approves it (publish.py, Stage 6). Promote approved
  payloads into committed site content with --ingest, then rebuild:

    python3 site_build.py --ingest      # out/published/*.json -> site/content/*.json, then build
    python3 site_build.py               # build site/publish/ from committed content

USAGE
  python3 site_build.py [--ingest]
"""

import datetime
import json
import os
import re
import sys
from urllib.parse import quote

import boundary

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.join(HERE, "site")
CONTENT = os.path.join(SITE, "content")
ASSETS = os.path.join(SITE, "assets")
PUBLISH = os.path.join(SITE, "publish")
PUBLISHED = os.path.join(HERE, "out", "published")

# Brand: GoCheckMyNews is a daily general news desk in the GoCheckMy family
# (gocheckmynews.com), tied to the family hub through the "A GoCheckMy site" footer link.
# One identity everywhere: the desk and the site share the name.
NAME = "GoCheckMyNews"
SLOGAN = "Every story, sourced. Every source, rated."   # the brand tagline
DESK_LINE = "The daily news desk that checks the story, and rates the source, before it runs."   # secondary descriptor
FAMILY = "GoCheckMyNews"                       # family/domain tie: gocheckmynews.com
FAMILY_HUB = "https://gocheckmy.com/"          # the GoCheckMy family hub (canonical footer link)
ORIGIN = "https://gocheckmynews.com"           # canonical origin for canonical/og:url/sitemap
SHORT_NAME = "GoCheckMyNews"                  # home-screen label (manifest)
THEME_COLOR = "#1D4E79"                       # browser chrome + manifest
OG_IMAGE = ORIGIN + "/og-image.png"            # 1200x630 social card, generated at build time
CF_ANALYTICS_TOKEN = "426d51eabdd24778a9476f7ba8528755"  # Cloudflare Web Analytics site token for gocheckmynews.com; empty renders no beacon
DESC = ("GoCheckMyNews is an independent daily news desk built with one intention: report "
        "what actually happened and keep the facts honest. Every story is checked against "
        "its sources before it runs, and every cited outlet carries a published bias and "
        "factual rating, shown with attribution. Never advocacy, never advice.")
FAMILY_DESC = ("GoCheckMyNews is the news, checked: a daily desk that verifies every story "
               "against the official public record and outlets across the political "
               "spectrum before it runs. Every story, sourced. Every source, rated.")
NFA = ("GoCheckMyNews reports events. It does not editorialize and it does not advise. "
       "Nothing here is political advocacy, legal advice, or financial advice.")
YEAR = "2026"
MONTHS = ["", "January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]

NAV = [("Home", "/index.html"), ("Latest", "/news.html"),
       ("Archive", "/archive.html"), ("Sources", "/sources.html"),
       ("About", "/about.html")]


# ---- helpers -----------------------------------------------------------------

def esc(s):
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def slugify(s):
    s = re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")
    return s or "story"


def fmt_date(iso):
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", str(iso or ""))
    if not m:
        return str(iso or "")
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    return f"{MONTHS[mo]} {d}, {y}"


def _parse_utc(item):
    """datetime for a story's publish moment: published_utc when stamped (new stories),
    else midnight of its date (legacy stories carry a date only)."""
    from datetime import datetime, timezone
    for fmt, val in (("%Y-%m-%dT%H:%M:%SZ", item.get("published_utc") or ""),
                     ("%Y-%m-%d", item.get("date") or "")):
        try:
            return datetime.strptime(val, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def fmt_when(item):
    """Dateline with the publish time when we have one: 'July 12, 2026 · 07:41 UTC'.
    News breaks around the clock; a reader needs to know 2 hours old vs 20."""
    base = esc(fmt_date(item.get("date")))
    if item.get("published_utc"):
        dt = _parse_utc(item)
        if dt:
            return f"{base} &middot; {dt.strftime('%H:%M')} UTC"
    return base


def _rfc822(item):
    dt = _parse_utc(item)
    return dt.strftime("%a, %d %b %Y %H:%M:%S +0000") if dt else ""


# Source attribution: outlet names read better (and more honestly) than raw feed URLs.
OUTLETS = {
    "npr.org": "NPR",
    "bbc.co.uk": "BBC", "bbc.com": "BBC",
    "pbs.org": "PBS NewsHour",
    "nytimes.com": "The New York Times",
    "theguardian.com": "The Guardian",
    "washingtonpost.com": "The Washington Post",
    "wsj.com": "The Wall Street Journal",
    "dowjones.io": "The Wall Street Journal",
    "foxnews.com": "Fox News",
    "nationalreview.com": "National Review",
    "washingtonexaminer.com": "Washington Examiner",
    "reason.com": "Reason",
    "thehill.com": "The Hill",
    "federalreserve.gov": "Federal Reserve (official record)",
    "supremecourt.gov": "Supreme Court (official record)",
    "congress.gov": "Congress.gov (official record)",
    "justice.gov": "Department of Justice (official record)",
}


def _host_of(url):
    from urllib.parse import urlparse
    return urlparse(url or "").netloc.lower().split(":")[0].removeprefix("www.")


def _by_domain(host, table):
    """Match a host against a domain-keyed table, walking subdomains toward the
    registrable root (rss.nytimes.com matches nytimes.com). Label-boundary only."""
    parts = [p for p in (host or "").split(".") if p]
    for i in range(len(parts) - 1):
        hit = table.get(".".join(parts[i:]))
        if hit is not None:
            return hit
    return None


def source_label(src):
    """'NPR: senate passes the stopgap' instead of a raw URL with utm cruft.
    A real title (anything that isn't just the URL) is kept as-is."""
    from urllib.parse import urlparse
    url = src.get("url") or ""
    title = (src.get("title") or "").strip()
    if title and title != url:
        return title
    p = urlparse(url)
    host = p.netloc.lower().removeprefix("www.")
    outlet = _by_domain(host, OUTLETS) or host
    slug = [s for s in p.path.split("/") if s]
    hint = re.sub(r"[-_]+", " ", re.sub(r"\.\w+$", "", slug[-1])) if slug else ""
    hint = re.sub(r"\b\d{5,}\b", "", hint).strip()
    if len(hint) > 80:
        hint = hint[:80].rsplit(" ", 1)[0] + "..."
    if hint and not hint.isdigit():
        return f"{outlet}: {hint}"
    return outlet


# ---- outlet credibility (the GoCheckMyNews differentiator) ----------------------------
# site/data/credibility.json maps every source domain the desk cites to a coarse bias lane
# and a factual-reporting grade, ATTRIBUTED to the public charts that publish them. The
# build renders a chip beside every cited source and the /sources.html methods page. If the
# table is absent, every credibility surface skips silently (never a build failure).
_CRED_PATH = os.path.join(SITE, "data", "credibility.json")
try:
    _CRED = json.load(open(_CRED_PATH, encoding="utf-8"))
except Exception:
    _CRED = {}
CRED_DOMAINS = _CRED.get("domains") or {}
CRED_ATTRIBUTION = _CRED.get("attribution") or {}
CRED_REVIEW = _CRED.get("review") or {}

# render order + display labels for the coarse lanes (deliberately coarse: a lane, not a score)
CRED_LANES = [("official-record", "Official record"), ("left", "Left"),
              ("lean-left", "Lean left"), ("center", "Center"),
              ("lean-right", "Lean right"), ("right", "Right"),
              ("libertarian", "Libertarian")]


def cred_for(url):
    """The credibility record for a source URL's outlet, or None (unrated)."""
    return _by_domain(_host_of(url), CRED_DOMAINS)


def cred_chip_label(rec):
    if rec.get("bias") == "official-record":
        return "official record"
    return f'{rec.get("bias") or "unrated"} / {rec.get("factual") or "ungraded"}'


def cred_chip(url):
    """The small credibility chip rendered beside a cited source link: the outlet's bias
    lane and factual grade ('lean-left / high'), 'official record' for primary-source
    institutions, 'unrated' for a domain absent from the table. Empty when the table
    itself is absent."""
    if not CRED_DOMAINS:
        return ""
    rec = cred_for(url)
    if rec is None:
        return '<span class="tag cred unrated">unrated</span>'
    return f'<span class="tag cred">{esc(cred_chip_label(rec))}</span>'


def spectrum_lanes(item):
    """The set of bias lanes represented in a story's cited sources."""
    lanes = set()
    for s in item.get("sources") or []:
        rec = cred_for(s.get("url") or "")
        if rec and rec.get("bias"):
            lanes.add(rec["bias"])
    return lanes


def spectrum_chip(item):
    """The balance signal: when a story's corroboration spans 3+ bias lanes, its card says
    so. Derived entirely from the credibility table; silent when the table is absent."""
    if not CRED_DOMAINS:
        return ""
    if len(spectrum_lanes(item)) >= 3:
        return '<span class="chip chip-spectrum">Corroborated across the spectrum</span>'
    return ""


# Topic tags: deterministic keyword rules over the story text, computed at build time so
# every story (old and new) gets them without touching the pipeline. Order = priority;
# a story keeps at most 3.
TAG_RULES = [
    ("government", r"\b(white house|executive order\w*|federal agenc\w*|shutdown|"
                   r"congress\w*|senate|house of representatives|house vote\w*|"
                   r"legislation|regulation\w*|cabinet|governor\w*|statehouse\w*)\b"),
    ("courts", r"\b(supreme court|scotus|certiorari|appeals court|circuit court|"
               r"district court|ruling\w*|injunction\w*|indict\w*|lawsuit\w*|"
               r"plaintiff\w*|verdict\w*|oral argument\w*)\b"),
    ("economy", r"\b(federal reserve|fomc|inflation|interest rate\w*|rate (?:cut|hike)\w*|"
                r"jobs report|unemployment|gdp|tariff\w*|cpi|recession|treasur\w*)\b"),
    ("world", r"\b(united nations|nato|ceasefire|foreign minist\w*|embassy|embassies|"
              r"sanction\w*|treaty|treaties|middle east|ukraine|taiwan|refugee\w*)\b"),
    ("politics", r"\b(election\w*|campaign\w*|midterm\w*|ballot\w*|primar(?:y|ies)|"
                 r"poll\w*|candidate\w*|voter\w*|caucus\w*)\b"),
    ("business", r"\b(earnings|merger\w*|acquisition\w*|ipo|bankruptc\w*|antitrust|"
                 r"layoff\w*|stockholder\w*|shareholder\w*|ceo)\b"),
]
_TAG_RES = [(tag, re.compile(pat, re.I)) for tag, pat in TAG_RULES]


def tags_for(item):
    body = item.get("body") or []
    text = " ".join([item.get("title") or "", item.get("dek") or "",
                     item.get("key_fact") or ""] +
                    [p if isinstance(p, str) else "" for p in body])
    scored = [(len(rx.findall(text)), i, tag)
              for i, (tag, rx) in enumerate(_TAG_RES) if rx.search(text)]
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [t[2] for t in scored[:3]]


def related_stories(item, items, n=3):
    """Stories sharing a topic tag, newest first. Turns a one-story visit into a session."""
    mine = set(tags_for(item))
    if not mine:
        return []
    scored = []
    for other in items:
        if other is item or other.get("example") or other.get("slug") == item.get("slug"):
            continue
        shared = len(mine & set(tags_for(other)))
        if shared:
            scored.append((shared, other.get("date", ""), other))
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [o for _, _, o in scored[:n]]


def render_feed(items):
    """RSS 2.0 feed of the published stories. The desk consumes RSS; now it emits it."""
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">',
           "<channel>",
           f"<title>{esc(NAME)}</title>",
           f"<link>{ORIGIN}/news.html</link>",
           f"<description>{esc(DESC)}</description>",
           "<language>en-us</language>",
           f'<atom:link href="{ORIGIN}/feed.xml" rel="self" type="application/rss+xml"/>']
    for it in [i for i in items if not i.get("example")][:30]:
        url = f"{ORIGIN}/articles/{it['slug']}.html"
        pd = _rfc822(it)
        cats = "".join(f"<category>{esc(t)}</category>" for t in tags_for(it))
        out += ["<item>",
                f"<title>{esc(it.get('title') or '')}</title>",
                f"<link>{url}</link>",
                f'<guid isPermaLink="true">{url}</guid>',
                (f"<pubDate>{pd}</pubDate>" if pd else ""),
                f"<description>{esc(it.get('dek') or '')}</description>",
                cats,
                "</item>"]
    out += ["</channel>", "</rss>", ""]
    return "\n".join(x for x in out if x)


def tracking_match(story, rx):
    """True when a story is genuinely ABOUT a tracked storyline, not merely mentioning
    it (2026-07-27: the Severe weather chip landed on a Tour de France story whose dek
    mentioned a wildfire once). A title hit qualifies alone; otherwise two or more hits
    across title+dek+key_fact. Module level so the canary can pin the regression."""
    title = story.get("title") or ""
    if rx.search(title):
        return True
    text = " ".join([title, story.get("dek") or "", story.get("key_fact") or ""])
    return len(rx.findall(text)) >= 2


def destyle(text):
    """House style: no em/en dashes in site copy (model drafts sometimes use them).

    ENTITIES COUNT. This handled the literal characters only, and the gap was not
    theoretical: three em dashes shipped on a sister site written as &mdash;, including one
    in an H2, and a lint that counted literal characters reported zero. A dash a reader sees
    is a dash whatever it is spelled as in the source, so the numeric and named entity forms
    are normalised here too, before the literal pass runs on whatever they decode to."""
    s = str(text or "")
    for ent in ("&mdash;", "&#8212;", "&#x2014;", "&#X2014;"):
        s = s.replace(" " + ent + " ", ", ").replace(ent, ", ")
    for ent in ("&ndash;", "&#8211;", "&#x2013;", "&#X2013;"):
        s = s.replace(" " + ent + " ", ", ").replace(ent, "-")
    return (s.replace(" \u2014 ", ", ").replace("\u2014", ", ")
            .replace(" \u2013 ", ", ").replace("\u2013", "-"))


def load_content():
    items = []
    if os.path.isdir(CONTENT):
        for fn in sorted(os.listdir(CONTENT)):
            if fn.startswith("_") or not fn.endswith(".json"):
                continue
            c = json.load(open(os.path.join(CONTENT, fn), encoding="utf-8"))
            c.setdefault("slug", slugify(c.get("title", "")))
            items.append(c)
    # newest first by date then id
    # newest date first; within a date, the editor's rank (1 = lead); unranked (intro,
    # example) after the day's ranked stories
    items.sort(key=lambda c: (c.get("date", ""), -(c.get("rank") or 999), c.get("id", "")),
               reverse=True)
    return items


# ---- shared chrome -----------------------------------------------------------

def masthead(active, dateline, brand="site"):
    """One identity everywhere: GoCheckMyNews is both the site and the desk. The brand
    parameter is kept for the shared call sites; every page renders the same masthead."""
    nav = "".join(
        f'<a href="{esc(href)}"{" class=active" if label == active else ""}>{esc(label)}</a>'
        for label, href in NAV)
    fam = f'<a class="mh-family" href="{FAMILY_HUB}">A GoCheckMy site</a>'
    # wordmark: "GoCheckMy" in the shared ink color, the site name ("Sports"/"News")
    # in the site color and italic (owner directive 2026-07-24)
    _base = "GoCheckMy"
    _site = NAME[len(_base):] if NAME.startswith(_base) else ""
    _wordmark = (f'<span class="mh-word-base">{esc(_base)}</span>'
                 f'<span class="mh-word-site">{esc(_site)}</span>') if _site else esc(NAME)
    brand_row = f"""<a class="mh-brand" href="/index.html" style="margin-top:8px">
    <img class="mh-mark" src="/assets/logo.svg" alt="">
    <span class="mh-word">{_wordmark}</span>
    <span class="mh-slogan">{esc(SLOGAN)}</span>
  </a>"""
    return f"""<div class="top-rule"></div>
<header class="masthead"><div class="wrap">
<script>document.addEventListener("DOMContentLoaded",function(){{var e=document.querySelector("[data-live-date]");
if(e){{e.textContent=new Date().toLocaleDateString("en-US",{{month:"long",day:"numeric",year:"numeric"}}).toUpperCase();}}}});</script>
  <div class="mh-top">
    {fam}
    <span class="mh-dateline"><span data-live-date>{esc(dateline)}</span> &middot; Independent &middot; No hype</span>
  </div>
  {brand_row}
</div></header>
<nav class="mh-nav"><div class="wrap">{nav}</div></nav>"""


def newsletter():
    return f"""<section class="news" aria-label="Newsletter signup"><div class="wrap">
  <h2>Get the brief</h2>
  <p>The day's real news, checked against the official public record and outlets across the
     political spectrum, with every source rated in the open. No advocacy, no rumor mills.
     One email, on a cadence we can actually keep.</p>
  <form name="newsletter" method="POST" data-netlify="true" netlify-honeypot="company" action="/thanks.html">
    <input type="hidden" name="form-name" value="newsletter">
    <input class="hp" type="text" name="company" tabindex="-1" autocomplete="off" aria-hidden="true">
    <input type="email" name="email" placeholder="you@email.com" required aria-label="Email address">
    <button type="submit">Subscribe</button>
  </form>
  <p class="fine">Emails are stored by Netlify Forms and used only to send the newsletter.
     Unsubscribe anytime. See our <a href="/privacy.html">privacy policy</a>. Never advocacy, never advice.</p>
</div></section>"""


def trust_block():
    return f"""<section class="trust"><div class="wrap">
  <div class="sec-head"><h2>The desk's promise</h2><span class="bar"></span></div>
  <p class="trust-line">We aggregate stories from the official public record and established
  outlets deliberately spread across the political spectrum, audit every one against its
  sources, and surface only what genuinely matters, with the spin and the hype stripped out.
  Sources are linked on every story, every outlet carries its published
  <a href="/sources.html">bias and factual rating</a>, and nothing here is ever advocacy
  or advice.</p>
</div></section>"""


def footer(brand="site"):
    """One identity everywhere; the brand parameter is kept for the shared call sites."""
    links = "".join(f'<a href="{esc(h)}">{esc(l)}</a>' for l, h in
                    [("About", "/about.html"), ("How we work", "/method.html"),
                     ("How we rate sources", "/sources.html"),
                     ("Standards & corrections", "/standards.html"), ("Archive", "/archive.html"),
                     ("Privacy", "/privacy.html"), ("Terms", "/terms.html"),
                     ("Contact", "mailto:desk@gocheckmynews.com"),
                     ("RSS", "/feed.xml")])
    who = f"{esc(NAME)}"
    note = ("GoCheckMyNews is an independent daily news desk, built with one intention: "
            "report what actually happened and keep the facts honest. Every story is "
            "sourced, and every source carries its published credibility rating.")
    return f"""<footer class="site"><div class="wrap">
  <div class="frow">
    <div class="fbrand">{who}</div>
    <div class="flinks">{links}</div>
  </div>
  <p class="fnote"><b>{esc(NFA)}</b> {note}
    {who} &middot; <a href="{FAMILY_HUB}">A GoCheckMy site</a>.<br>&copy; {YEAR} Go Check My Brands LLC</p>
</div></footer>"""


_ASSET_VER = {}


def _fingerprint_assets(html):
    """Version every /assets/ URL with a content hash (site.css?v=ab12cd34ef). netlify.toml
    caches assets in the browser for 7 days; without this, a changed stylesheet leaves
    returning visitors on week-old CSS. The HTML itself always revalidates, so a new hash
    reaches every browser on the next page load."""
    import hashlib

    def ver(path):
        if path not in _ASSET_VER:
            f = os.path.join(HERE, "site", path.lstrip("/"))
            try:
                _ASSET_VER[path] = hashlib.md5(open(f, "rb").read()).hexdigest()[:10]
            except OSError:
                _ASSET_VER[path] = "0"
        return _ASSET_VER[path]

    return re.sub(r'((?:src|href)=")(/assets/[^"?#]+)(")',
                  lambda m: f'{m.group(1)}{m.group(2)}?v={ver(m.group(2))}{m.group(3)}', html)


# The motion layer's shared guard: reduced-motion strips every video to its poster and
# freezes the micro-details; otherwise videos play only while on screen and story cards
# fade up once. Inline (one request), transform/opacity only, no layout shift.
MOTION_JS = (
    '<script>(function(){var rm=matchMedia("(prefers-reduced-motion: reduce)").matches;'
    'var vids=[].slice.call(document.querySelectorAll(".motion-video"));'
    'if(rm){vids.forEach(function(v){v.parentNode.removeChild(v)});return;}'
    'document.documentElement.classList.add("mjs");'
    'if("IntersectionObserver" in window){'
    'var vo=new IntersectionObserver(function(es){es.forEach(function(e){var v=e.target;'
    'if(e.isIntersecting&&e.intersectionRatio>=.12){if(v.paused&&!v.dataset.userPaused)v.play().catch(function(){})}'
    'else if(!v.paused)v.pause()})},{threshold:.12});'
    '[].slice.call(document.querySelectorAll(".hero-pause")).forEach(function(b){'
    'var v=b.parentNode.querySelector(".motion-video");if(!v)return;b.hidden=false;'
    'var setP=function(p){if(p){v.dataset.userPaused="1";v.pause();'
    'b.setAttribute("aria-pressed","true");'
    'b.setAttribute("aria-label","Play background animation");b.innerHTML="&#9654;"}'
    'else{delete v.dataset.userPaused;v.play().catch(function(){});'
    'b.setAttribute("aria-pressed","false");'
    'b.setAttribute("aria-label","Pause background animation");b.innerHTML="&#10074;&#10074;"}'
    'try{sessionStorage.setItem("heroPaused",p?"1":"0")}catch(e){}};'
    'try{if(sessionStorage.getItem("heroPaused")==="1")setP(true)}catch(e){}'
    'b.addEventListener("click",function(){setP(!v.paused)})});'
    'vids.forEach(function(v){if(!v.classList.contains("motion-lazy"))vo.observe(v)});'
    'var lz=vids.filter(function(v){return v.classList.contains("motion-lazy")});'
    'if(lz.length){var arm=function(){lz.forEach(function(v){vo.observe(v)});'
    'removeEventListener("scroll",arm)};addEventListener("scroll",arm,{passive:true})}'
    'var ro=new IntersectionObserver(function(es){es.forEach(function(e){'
    'if(e.isIntersecting){e.target.classList.add("in");ro.unobserve(e.target)}})},'
    '{rootMargin:"0px 0px -5% 0px"});'
    '[].slice.call(document.querySelectorAll(".reveal")).forEach(function(el){ro.observe(el)})}'
    'else{[].slice.call(document.querySelectorAll(".reveal")).forEach(function(el){el.classList.add("in")})}'
    '})()</script>')


def shell(title, desc, active, body, dateline, body_class="", path="/", noindex=False,
          brand="site", og_type="website", schema_extra="", og_image=None):
    fonts = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
             '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
             '<link href="https://fonts.googleapis.com/css2?family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,500;0,6..72,600;1,6..72,400;1,6..72,500&family=Inter:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600;700&family=Mrs+Saint+Delafield&display=swap" rel="stylesheet">')
    url = ORIGIN + path
    site_name = NAME
    # Home only: the hero band's poster is the LCP element (the video is preload="none"
    # by design), so hint the browser to fetch it first.
    lcp = ('<link rel="preload" as="image" href="/assets/hero/hero-poster.webp" '
           'fetchpriority="high">\n' if path == "/" else "")
    robots = '<meta name="robots" content="noindex">\n' if noindex else f'<link rel="canonical" href="{esc(url)}">\n'
    robots = lcp + robots
    beacon = ""
    if CF_ANALYTICS_TOKEN:
        beacon = ('\n<script defer src="https://static.cloudflareinsights.com/beacon.min.js" '
                  f'data-cf-beacon=\'{{"token": "{CF_ANALYTICS_TOKEN}"}}\'></script>')
    # accessibility: id the page's first <main> landmark as the skip-link target, and emit
    # the skip-link ONLY when such a target exists (list pages built from bare <section>s
    # get no dangling link). The .skip-link CSS lives in site.css.
    skip = ""
    if re.search(r'<main(\s|>)', body):
        body = re.sub(r'<main(\s|>)', r'<main id="main"\1', body, count=1)
        skip = '<a class="skip-link" href="#main">Skip to main content</a>\n'
    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>{esc(title)}</title>
<meta name="description" content="{esc(desc)}">
{robots}<link rel="alternate" type="application/rss+xml" title="{esc(NAME)} feed" href="/feed.xml">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(desc)}">
<meta property="og:type" content="{esc(og_type)}">{schema_extra}
<meta property="og:url" content="{esc(url)}">
<meta property="og:site_name" content="{esc(site_name)}">
<meta property="og:image" content="{og_image or OG_IMAGE}">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:image" content="{og_image or OG_IMAGE}">
<link rel="icon" type="image/svg+xml" href="/assets/favicon.svg">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<link rel="manifest" href="/site.webmanifest">
<meta name="theme-color" content="{THEME_COLOR}">
{fonts}
<link rel="stylesheet" href="/assets/site.css">
</head>
<body class="{esc(body_class)}">
{skip}{masthead(active, dateline, brand)}
{body}
{footer(brand)}{beacon}
{MOTION_JS}
</body>
</html>"""
    return _fingerprint_assets(page)


# ---- article ----------------------------------------------------------------

def render_body(body):
    out = []
    for b in body or []:
        if isinstance(b, dict) and "h2" in b:
            out.append(f"<h2>{esc(b['h2'])}</h2>")
        else:
            out.append(f"<p>{esc(b)}</p>")
    return "\n".join(out)


def verdict_badge(verdict):
    if verdict == "VERIFIED":
        return '<span class="badge verified">Verified</span>'
    if verdict in ("NEEDS-HUMAN-REVIEW", "REVIEW"):
        return '<span class="badge review">Editor reviewed</span>'
    return ""


def sig_block():
    """The desk's closing mark. It states what was DONE to the story, never how the desk is
    built. Owner's call, 2026-07-30: the process is competitive advantage, so the caption no
    longer says "automated newsroom" and the attestation no longer points at a page
    explaining the pipeline. Signature reads Charles Independence, the desk's byline.

    Anything added here must describe the story, not the machinery. The AI disclosure lives
    once on /standards.html, which is what keeps a named byline honest."""
    return """<div class="sigrow">
  <div class="sig">
    <span class="sig-script">Charles Independence</span>
    <span class="sig-cap">The GoCheckMyNews Desk</span>
    <span class="sig-attest">Ranked, source-checked, and verified by the desk's
      <a href="/method.html">independent review pass</a>.</span>
  </div>
  <div class="stamp" role="img" aria-label="Sources verified, on the record stamp">
    <svg viewBox="0 0 120 120" aria-hidden="true">
      <circle cx="60" cy="60" r="56" fill="none" stroke="currentColor" stroke-width="2"/>
      <!-- Stamp J (owner's pick, 2026-07-30): a solid accent disc fills the ring and the
           GoCheckMy check is knocked out of it, so the mark IS the seal rather than a small
           device sitting inside one. The knockout uses --card so it inverts with the theme;
           a hardcoded white would vanish into the dark-mode disc. This replaces the dashed
           inner ring, which the disc now occupies. -->
      <circle cx="60" cy="60" r="45" fill="currentColor"/>
      <defs><path id="stamparc" d="M60,60 m-51,0 a51,51 0 1,1 102,0 a51,51 0 1,1 -102,0"/></defs>
      <text font-size="9.4" letter-spacing="2.2" fill="currentColor"
        font-family="IBM Plex Mono,monospace" font-weight="600">
        <textPath href="#stamparc" startOffset="2%">SOURCES VERIFIED</textPath>
        <textPath href="#stamparc" startOffset="55%">ON THE RECORD</textPath>
      </text>
      <path d="M27 62 L49 85 L93 32" fill="none" stroke="var(--card)" stroke-width="11"
        stroke-linecap="round" stroke-linejoin="round"/>
    </svg>
  </div>
</div>"""


def share_row(url, title):
    """Share buttons for growing the audience: LinkedIn and X get the story with one click,
    copy-link covers everything else. Plain links, no tracking scripts."""
    u, t = quote(url, safe=""), quote(title, safe="")
    return f"""<div class="sharerow">
  <span class="share-lab">Share this story</span>
  <a class="share-btn" href="https://www.linkedin.com/sharing/share-offsite/?url={u}"
     target="_blank" rel="noopener">LinkedIn</a>
  <a class="share-btn" href="https://twitter.com/intent/tweet?text={t}&amp;url={u}"
     target="_blank" rel="noopener">X</a>
  <button class="share-btn" type="button" data-url="{esc(url)}">Copy link</button>
</div>
<script>
(function(){{
  var b=document.querySelector('.sharerow button');if(!b)return;
  b.addEventListener('click',function(){{
    var u=b.getAttribute('data-url');
    function ok(){{b.textContent='Copied';setTimeout(function(){{b.textContent='Copy link';}},1600);}}
    function fb(){{var t=document.createElement('textarea');t.value=u;t.style.position='fixed';t.style.opacity='0';
      document.body.appendChild(t);t.select();try{{document.execCommand('copy');ok();}}catch(e){{}}
      document.body.removeChild(t);}}
    if(navigator.clipboard&&window.isSecureContext){{navigator.clipboard.writeText(u).then(ok,fb);}}else{{fb();}}
  }});
}})();
</script>"""


def render_article(item, all_items=None):
    dateline = fmt_date(item.get("date"))
    badge = verdict_badge(item.get("verdict"))
    tag = f'<span class="tag">{esc(item.get("category","news"))}</span>' if item.get("category") else ""
    topic_chips = "".join(f'<span class="tag topic">{esc(t)}</span>' for t in tags_for(item))
    ribbon = ""
    if item.get("example"):
        ribbon = ('<div class="callout"><b>Example, not a real story.</b> This page shows the '
                  'format GoCheckMyNews publishes in. The content is illustrative only.</div>')
    if item.get("superseded_by"):
        newer = next((i for i in (all_items or [])
                      if i.get("slug") == item["superseded_by"]), None)
        newer_title = newer.get("title") if newer else "the newest version"
        ribbon += (f'<div class="callout"><b>This story has been updated.</b> Read the '
                   f'latest version: <a href="/articles/{esc(item["superseded_by"])}.html">'
                   f'{esc(newer_title)}</a>.</div>')
    if item.get("update_of"):
        prev = next((i for i in (all_items or []) if i.get("slug") == item["update_of"]), None)
        prev_title = prev.get("title") if prev else "our earlier story"
        ribbon += (f'<div class="callout"><b>Update.</b> This story develops our earlier '
                   f'reporting: <a href="/articles/{esc(item["update_of"])}.html">'
                   f'{esc(prev_title)}</a>.</div>')
    # THE BOUNDARY PANEL sits ABOVE the prose, unlike every other panel on the page, because
    # it answers the only question a reader of a security advisory has before they will read
    # anything else: am I affected. Values are the vendor's own words, copied through the
    # pipeline by assignment (writer._carry_boundary) and never restated in prose, so there
    # is no sentence here that can come back inverted. See boundary.py.
    bnd = ""
    brows = boundary.rows(item.get("boundary") or {})
    if brows:
        lis = ""
        for label, value in brows:
            cell = (f'<a href="{esc(value)}" rel="nofollow noopener">{esc(value)}</a>'
                    if label == "Advisory" else esc(value))
            lis += f'<div class="brow"><dt>{esc(label)}</dt><dd>{cell}</dd></div>'
        bnd = (f'<section class="boundary" aria-labelledby="bnd-h">'
               f'<h2 id="bnd-h">Who this affects</h2>'
               f'<p class="mut">Quoted from the advisory linked below. The desk does not '
               f'restate it.</p><dl>{lis}</dl></section>')
    key = ""
    if item.get("key_fact"):
        key = (f'<div class="keyfact"><span class="lab">The key fact</span>'
               f'<p>{esc(item["key_fact"])}</p></div>')
    bottom = ""
    if (item.get("bottom_line") or "").strip():
        bottom = (f'<div class="bottomline"><span class="lab">The Bottom Line</span>'
                  f'<p>{esc(item["bottom_line"])}</p></div>')
    take = ""
    if (item.get("human_take") or "").strip():
        take = (f'<div class="take"><span class="lab">The take</span>'
                f'<p>{esc(item["human_take"])}</p></div>')
    srcs = item.get("sources") or []
    src_html = ""
    if srcs:
        # every cited source carries its credibility chip: the outlet's bias lane and
        # factual grade from the public charts (the differentiator; see /sources.html)
        lis = "".join(
            f'<li><a href="{esc(s.get("url",""))}" rel="nofollow">{esc(source_label(s))}</a>'
            f' {cred_chip(s.get("url") or "")}</li>'
            for s in srcs)
        legend = ""
        if CRED_DOMAINS:
            legend = ('<p class="cred-note">Outlet ratings are the public AllSides and '
                      'Media Bias/Fact Check charts\' calls, not ours. '
                      '<a href="/sources.html">How we rate sources</a>.</p>')
        # SINGLE-SOURCE DISCLOSURE (owner directive 2026-07-31): when only one outlet has
        # carried a development, say so plainly beside the sourcing. This is a factual note
        # about THIS story, not process talk: a reader deserves to know a claim rests on one
        # outlet, and stating it is a credibility signal rather than an admission.
        import common as _cmn
        if _cmn.distinct_publishers([s.get("url", "") for s in srcs]) <= 1:
            legend = (f'<p class="single-source"><b>Single-source report.</b> As published, '
                      f'only {esc(source_label(srcs[0]))} had reported this development. '
                      f'No independent outlet had corroborated it.</p>') + legend
        also = item.get("also_reported_by") or []
        if also:
            names = ", ".join(esc(a.get("outlet", "")) for a in also[:4] if a.get("outlet"))
            legend = (f'<p class="also-reported"><b>Also reported by</b> {names}. '
                      f'Independent coverage of the same development, found by search; '
                      f'not this story\'s sources.</p>') + legend
        src_html = f'<div class="sources"><h2>Sources</h2><ol>{lis}</ol>{legend}</div>'
    rel_html = ""
    for rel in related_stories(item, all_items or []):
        rel_html += (f'<li><a href="/articles/{esc(rel["slug"])}.html">{esc(rel.get("title"))}</a>'
                     f'<span class="mut"> &middot; {fmt_when(rel)}</span></li>')
    if rel_html:
        rel_html = f'<div class="related"><h2>Related stories</h2><ul>{rel_html}</ul></div>'
    # NO PER-STORY CHECK TRAIL. This used to render a "How this story was checked" block on
    # every article, naming the desk's automated verifier, its independent approver, and how
    # many cited pages were fetched live. That is the process, published once per story, and
    # it is removed for the same reason the method-page walkthrough was (owner's call,
    # 2026-07-30). The corrections path survives in the footer's standards link, which is
    # what that block's "Report an error" was for.
    trail = ""
    author = esc(item.get("author", "The GoCheckMyNews Desk"))
    body = f"""<main class="wrap narrow">
  <article class="article">
    <div class="ey">{badge}{'<span class="badge update">Update</span>' if item.get("update_of") else ''}{tag}{topic_chips}<span class="dateline">{fmt_when(item)}</span></div>
    <h1>{esc(item.get("title"))}</h1>
    {f'<p class="dek">{esc(item["dek"])}</p>' if item.get("dek") else ""}
    <div class="byline">By {author}</div>
    {ribbon}
    {bnd}
    <div class="prose">{render_body(item.get("body"))}</div>
    {key}
    {take}
    {bottom}
    <p class="signoff">{esc(SLOGAN)}</p>
    {sig_block()}
    {share_row(ORIGIN + f"/articles/{item['slug']}.html", item.get("title") or "")}
    {trail}
    {src_html}
    {rel_html}
    <p class="nfa">{esc(NFA)}</p>
  </article>
</main>"""
    title = f'{item.get("title")} - {NAME}'
    desc = item.get("dek") or (item.get("body", [""])[0] if item.get("body") else DESC)
    url = f"{ORIGIN}/articles/{item['slug']}.html"
    # Per-article share card, written earlier in build() by _render_og_card. Checked on
    # disk rather than assumed, because that render is fail-open: if it was skipped the
    # article falls back to the site-wide card instead of pointing at a 404.
    card_out = os.path.join(PUBLISH, "og", f"{item['slug']}.png")
    og_image = f"{ORIGIN}/og/{item['slug']}.png" if os.path.exists(card_out) else OG_IMAGE
    schema = json.dumps({"@context": "https://schema.org", "@graph": [
        {"@type": "NewsArticle", "headline": item.get("title"),
         "description": item.get("dek") or "", "url": url, "mainEntityOfPage": url,
         "image": og_image,
         "datePublished": item.get("published_utc") or item.get("date"),
         "dateModified": item.get("published_utc") or item.get("date"),
         "author": {"@type": "Organization", "name": NAME, "url": ORIGIN + "/news.html"},
         "publisher": {"@type": "Organization", "name": FAMILY, "url": ORIGIN + "/"}},
        {"@type": "BreadcrumbList", "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Latest", "item": ORIGIN + "/news.html"},
            {"@type": "ListItem", "position": 2, "name": item.get("title"), "item": url}]}
    ]}, ensure_ascii=False)
    return shell(title, desc if isinstance(desc, str) else DESC, "Latest", body, dateline.upper(),
                 path=f"/articles/{item['slug']}.html", noindex=bool(item.get("example")),
                 og_type="article", og_image=og_image,
                 schema_extra=f'\n<script type="application/ld+json">{schema}</script>')


# ---- cards / index / archive -------------------------------------------------

def card(item):
    badge = verdict_badge(item.get("verdict"))
    tag = f'<span class="tag">{esc(item.get("category","news"))}</span>' if item.get("category") else ""
    tag += "".join(f'<span class="tag topic">{esc(t)}</span>' for t in tags_for(item)[:2])
    href = f'/articles/{esc(item["slug"])}.html'
    summ = item.get("dek") or (item.get("body", [""])[0] if item.get("body") else "")
    if isinstance(summ, dict):
        summ = summ.get("h2", "")
    nsrc = len(item.get("sources") or [])
    return f"""<article class="card reveal">
  <div class="row">{badge}{tag}{spectrum_chip(item)}</div>
  <h3><a href="{href}">{esc(item.get("title"))}</a></h3>
  <p class="summary">{esc(summ[:180])}</p>
  <div class="foot"><span class="dateline">{fmt_when(item)}</span>
    <span class="src">{nsrc} source{"s" if nsrc != 1 else ""}</span></div>
</article>"""


def desk_strip():
    # Desk strip: the desk line beneath the masthead, text only (the chassis this repo was
    # cloned from put an anchor-portrait video here; this desk runs a single brand, no mascot).
    return f"""<section class="desk" aria-label="About this desk"><div class="wrap">
  <div class="desk-copy">
    <span class="kicker">From the desk</span>
    <p>{esc(DESK_LINE)}</p>
  </div>
</div></section>"""


def _blink_when(item):
    """Edition timestamp with a clock-style blinking colon (CSS animates .tick-colon)."""
    t = fmt_when(item)
    if ":" in t:
        head, rest = t.split(":", 1)
        return f'{head}<span class="tick-colon">:</span>{rest}'
    return t


def _is_wrap(item):
    return str(item.get("id", "")).startswith("wrap-")


# ---- daypart stacking (owner directive 2026-07-20) --------------------------------
# The front page re-stacks like a broadcast rundown. The build clock decides stacking
# and badge decay ONLY; datelines stay content-derived (the house rule is untouched).
# SITE_BUILD_NOW pins the clock for deterministic replays and the canary.

BREAKING_HOURS = 3
_DAYPART_WRAP = {"morning": "wrap-am-", "midday": "wrap-md-", "evening": "wrap-pm-"}
_NOW_CACHE = None


def _build_now():
    global _NOW_CACHE
    if _NOW_CACHE is None:
        env = os.environ.get("SITE_BUILD_NOW", "")
        try:
            _NOW_CACHE = datetime.datetime.fromisoformat(env.replace("Z", "+00:00"))
        except ValueError:
            _NOW_CACHE = datetime.datetime.now(datetime.timezone.utc)
    return _NOW_CACHE


def _daypart(now):
    return "morning" if now.hour < 14 else "midday" if now.hour < 20 else "evening"


def _fresh_hours(item, now):
    try:
        ts = (item.get("published_utc") or "").replace("Z", "+00:00")
        return (now - datetime.datetime.fromisoformat(ts)).total_seconds() / 3600.0
    except (ValueError, TypeError):
        return 1e9


def home_stack(items, now=None):
    """One deterministic rule for the hero lead and The Bottom Line anchor, shared by
    render_home and bottom_line_card so the front page and /news never disagree.
      1. A story under BREAKING_HOURS old takes the lead with the Breaking badge (a
         breaking publish triggers its own build; the next slot or refresh build
         retires the badge).
      2. The Bottom Line anchors today's edition matching the build daypart.
      3. Otherwise the editor's rank of the newest date leads (unchanged behavior).
      4. No matching edition -> the newest edition, exactly as before. Cron drift's
         worst case is the status quo; nothing ever renders empty."""
    now = now or _build_now()
    stories = [i for i in (items or []) if not i.get("example") and not _is_wrap(i)
               and not i.get("superseded_by")]
    breaking = False
    if stories:
        freshest = min(stories, key=lambda i: _fresh_hours(i, now))
        # BREAKING requires the EVENT to be fresh, not just the publish (owner
        # directive 2026-07-27: a Friday ruling posted Monday is never Breaking).
        # event_utc is stamped at ingest from the story's wire timestamp; a story
        # without one can never carry the label.
        def _event_fresh(story):
            try:
                ev = datetime.datetime.fromisoformat(
                    (story.get("event_utc") or "").replace("Z", "+00:00"))
                return (now - ev).total_seconds() <= 12 * 3600
            except Exception:
                return False
        if _fresh_hours(freshest, now) <= BREAKING_HOURS and _event_fresh(freshest):
            breaking = True
            stories = [freshest] + [s for s in stories if s is not freshest]
    wraps = [i for i in (items or [])
             if _is_wrap(i) and i.get("bottom_line") and not i.get("example")]
    prefix = _DAYPART_WRAP[_daypart(now)]
    today = now.strftime("%Y-%m-%d")
    anchor = next((w for w in wraps
                   if str(w.get("id", "")).startswith(prefix) and w.get("date") == today),
                  None)
    if anchor is None:
        anchor = wraps[0] if wraps else None
    # EDITIONS STALENESS (owner directive 2026-07-27): an edition older than 24 hours
    # is never featured; the module collapses instead of presenting old synthesis as
    # current. The /bottom-line archive keeps every edition.
    if anchor is not None and _fresh_hours(anchor, now) > 24:
        anchor = None
    return stories, breaking, anchor


# STALENESS GUARD (owner directive 2026-07-22: the Bottom Line is a powerful piece or
# it is not on the page). Build time: an edition from a previous day is labeled
# honestly. View time: the band carries its timestamp and the reader's browser retires
# it into an archive pointer past 20 hours, so a stalled pipeline can never showcase
# an old read as current.
_BL_GUARD_SCRIPT = (
    '<script>(function(){var b=document.querySelector("[data-bl-published]");'
    'if(!b)return;var t=Date.parse(b.getAttribute("data-bl-published"));'
    'if(isNaN(t)||Date.now()-t<=864e5)return;'
    'b.setAttribute("href","/bottom-line.html");'
    'var s=b.querySelector(".hero-bl-src");if(s)s.textContent="The daily reads";'
    'var r=b.querySelector(".hero-bl-read");if(r)r.textContent='
    '"The next edition is on the way. Catch up on the archive of the desk\u2019s daily reads.";'
    'var m=b.querySelector(".hero-bl-more");if(m)m.innerHTML="The Bottom Line archive &rarr;";'
    '})();</script>')


def _bl_fresh_label(ed, name):
    """Honest label when the band shows a previous day's edition."""
    ed_date = (ed.get("published_utc") or "")[:10]
    build_date = _build_now().date().isoformat()
    if ed_date and ed_date < build_date:
        import datetime as _dt
        days = (_dt.date.fromisoformat(build_date) - _dt.date.fromisoformat(ed_date)).days
        return ("Yesterday's " + name) if days == 1 else (name + " &middot; " + esc(fmt_date(ed_date)))
    return name


def bottom_line_card(items):
    """THE BOTTOM LINE (owner directive 2026-07-15): the desk's signature element, the
    newest edition's 3-5 sentence read, refreshed every slot (and by breaking runs).
    Rendered as the compact card that rides beside the lead story (owner directive
    2026-07-17: lead first, Bottom Line to its right, same arrangement as the front
    page), reusing the home hero's card styling."""
    _, _, ed = home_stack(items)  # daypart anchor; falls back to newest edition
    if ed is None:
        return ""
    name = _bl_fresh_label(ed, esc((ed.get("title") or "").split(":")[0].strip()
                                   or "The Daily Edition"))
    return (f'<a class="hero-bl news-bl" data-bl-published="{esc(ed.get("published_utc") or "")}" '
            f'href="/articles/{esc(ed["slug"])}.html">'
            f'<span class="hero-kick"><span class="kicker">The Bottom Line</span></span>'
            f'<span class="hero-bl-src">{name} &middot; {_blink_when(ed)}</span>'
            f'<span class="hero-bl-read">{esc(ed["bottom_line"])}</span>'
            f'<span class="hero-bl-more">Read the full edition &rarr;</span></a>'
            + _BL_GUARD_SCRIPT)


def render_bottom_line_history(items, dateline):
    """/bottom-line.html: the browsable history of the daily reads, one entry per
    edition, newest first. Each edition's read is preserved with its edition forever."""
    wraps = [i for i in items if _is_wrap(i) and i.get("bottom_line") and not i.get("example")]
    rows = []
    for ed in wraps:
        name = esc((ed.get("title") or "").split(":")[0].strip())
        rows.append(f"""<div class="bl-hist">
      <div class="bl-head"><span class="bl-label">{name}</span>
        <span class="dateline">{fmt_when(ed)}</span></div>
      <p class="bl-read">{esc(ed["bottom_line"])}</p>
      <p style="margin:6px 0 0"><a href="/articles/{esc(ed['slug'])}.html">The full edition &rarr;</a></p>
    </div>""")
    body = f"""<main class="wrap narrow"><section class="page">
  <span class="kicker">The Bottom Line</span>
  <h1>The daily reads</h1>
  <p class="lede">Three times a day the desk closes its edition with The Bottom Line: what
     happened, why it mattered, and what the calendar says comes next. Synthesis of the
     desk's verified reporting, never a prediction and never advice. Every read is kept.</p>
  {"".join(rows) if rows else '<p class="lede">The first edition lands soon.</p>'}
  <p class="nfa">{esc(NFA)}</p>
</section></main>"""
    return shell(f"The Bottom Line - {NAME}", "The desk's daily reads: what happened, why it "
                 "mattered, and what comes next. Synthesis, never advice.",
                 "The Bottom Line", body, dateline, path="/bottom-line.html")


def render_news(items, dateline):
    # superseded chapters live on only through their own page's forward pointer; the
    # news list and archive show one entry per story, exactly like the homepage
    live = [i for i in items if not i.get("example") and not i.get("superseded_by")]
    bl = bottom_line_card(live)
    live = [i for i in live if not _is_wrap(i)]  # editions speak through the Bottom Line card
    if live:
        lead = live[0]
        rest = live[1:]
        badge = verdict_badge(lead.get("verdict"))
        lead_tags = tags_for(lead)
        tag = (f'<span class="tag topic">{esc(lead_tags[0])}</span>' if lead_tags
               else f'<span class="tag">{esc(lead.get("category", "news"))}</span>')
        # freshness at a glance (owner directive 2026-07-24): a lead that is the head of
        # an update chain shows "Updated <time>" so a running story reads as current.
        lead_when = f'Updated {fmt_when(lead)}' if lead.get("update_of") else fmt_when(lead)
        lead_inner = f"""<span class="kicker">Lead story</span> {tag}{spectrum_chip(lead)}
    <h1><a href="/articles/{esc(lead["slug"])}.html" style="color:inherit">{esc(lead.get("title"))}</a></h1>
    {f'<p class="dek">{esc(lead["dek"])}</p>' if lead.get("dek") else ""}
    <div class="meta">{badge}<span class="dateline">{lead_when}</span>
      <a href="/articles/{esc(lead["slug"])}.html">Read the story &rarr;</a></div>"""
        # Lead first, The Bottom Line beside it (front-page arrangement); without an
        # edition the lead simply spans the row.
        lead_html = (f"""<section class="lead"><div class="wrap"><div class="news-grid">
    <div class="news-lead">{lead_inner}</div>
    {bl}
  </div></div></section>""" if bl else f"""<section class="lead"><div class="wrap">
    {lead_inner}
  </div></section>""")
        grid = ""
        if rest:
            grid = (f'<section class="sec"><div class="wrap"><div class="sec-head" id="latest">'
                    f'<h2>More from the desk</h2><span class="bar"></span></div>'
                    f'<div class="grid">{"".join(card(i) for i in rest)}</div></div></section>')
    else:
        lead_html = f"""<section class="lead"><div class="wrap">
    <span class="kicker">The desk is live</span>
    <h1>Honest news, on a cadence we can keep.</h1>
    <p class="dek">{esc(DESK_LINE)} The first published brief lands here. In the meantime, read how
       the desk works and why you can trust the byline.</p>
    <div class="meta"><a href="/method.html">How we work &rarr;</a>
      <a href="/about.html">Why this exists &rarr;</a></div>
  </div></section>"""
        grid = ('<section class="sec"><div class="wrap"><div class="empty">'
                '<span class="k">No brief published yet</span>'
                '<p style="margin:.6em 0 0">Every story here will have been ranked by an AI editor, '
                'checked against its sources by an independent AI verifier, and approved by a human. '
                'That gate is the whole point, so we would rather publish nothing than publish junk.</p>'
                '</div></div></section>')
    # news first: lead story (Bottom Line beside it), then the rest of the day's stories;
    # the promise strip and the newsletter read as the footer beats, never above the
    # journalism; the desk strip is secondary chrome; the news itself is the main landmark
    body = (desk_strip() + '<main class="news-main">' + lead_html + grid
            + trust_block() + newsletter() + '</main>')
    return shell(f"Latest news - {NAME}", DESC, "Latest", body, dateline, path="/news.html")


def render_home(items, dateline):
    """The GoCheckMyNews front door, built for the RETURNING reader: today's headlines,
    the editions, and the storylines the desk is tracking. The brand pitch lives below the
    information, not above it."""
    live = [i for i in (items or []) if not i.get("example") and not _is_wrap(i)]

    # The front page (owner directive 2026-07-16): a network-style hero mosaic. Several
    # lead stories visible at once with explicit hierarchy (the editor's rank orders them),
    # editions in their own strip below. No carousel: every ranked story is on screen.
    # Daypart re-stack (2026-07-20): home_stack may promote a breaking story to the lead
    # and picks the edition that anchors The Bottom Line square.
    stories, breaking, bl_anchor = home_stack(items)

    def _hero_tag(item):
        tags = tags_for(item)
        return f'<span class="tag topic">{esc(tags[0])}</span>' if tags else ""

    desk_html = ""
    if stories:
        lead = stories[0]
        dek_html = f'<p class="hero-dek">{esc(lead["dek"])}</p>' if lead.get("dek") else ""
        # The desk set: an ambient video loop behind the lead card. It is scenery for
        # WHATEVER story leads, never an illustration of it (no caption, no linkage), and
        # the scrim guarantees the headline always beats the motion. Reduced-motion
        # readers get the poster still only (script below removes the video pre-load).
        hero_video = (
            '<video class="hero-video motion-video" autoplay muted loop playsinline preload="none" '
            'poster="/assets/hero/hero-poster.jpg" aria-hidden="true" tabindex="-1">'
            '<source src="/assets/hero/hero-loop.webm" type="video/webm">'
            '<source src="/assets/hero/hero-loop.mp4" type="video/mp4"></video>'
            '<span class="hero-scrim" aria-hidden="true"></span>'
            '<button class="hero-pause" type="button" hidden aria-pressed="false" '
            'aria-label="Pause background animation">&#10074;&#10074;</button>')
        lead_mark = ('<span class="badge breaking">Breaking</span>' if breaking
                     else _hero_tag(lead))
        lead_html = (f'<a class="hero-lead" href="/articles/{esc(lead["slug"])}.html">'
                     f'<span class="hero-kick"><span class="kicker">Lead story</span>{lead_mark}'
                     f'{spectrum_chip(lead)}</span>'
                     f'<h3>{esc(lead.get("title"))}</h3>{dek_html}'
                     f'<span class="hl-meta">{verdict_badge(lead.get("verdict"))}'
                     f'<span class="dateline">{fmt_when(lead)}</span></span></a>')
        # The Bottom Line rides shotgun: the day's summary as the hero square beside the
        # lead, replacing the standalone band lower on the page.
        bl_card = ""
        if bl_anchor is not None:
            ed = bl_anchor
            ed_name = esc((ed.get("title") or "").split(":")[0].strip() or "The Daily Edition")
            bl_card = (f'<a class="hero-bl" data-bl-published="{esc(ed.get("published_utc") or "")}" '
                       f'href="/articles/{esc(ed["slug"])}.html">'
                       f'<span class="hero-kick"><span class="kicker">The Bottom Line</span></span>'
                       f'<span class="hero-bl-src">{_bl_fresh_label(ed, ed_name)} &middot; {_blink_when(ed)}</span>'
                       f'<span class="hero-bl-read">{esc(ed["bottom_line"])}</span>'
                       f'<span class="hero-bl-more">Read the full edition &rarr;</span></a>'
                       + _BL_GUARD_SCRIPT)
        more = "".join(
            f'<a class="hero-item" href="/articles/{esc(i["slug"])}.html">'
            f'<span class="hero-num">{n:02d}</span><span class="hero-body">'
            f'<span class="hero-kick">{_hero_tag(i)}</span>'
            f'<span class="hl-title">{esc(i.get("title"))}</span>'
            f'<span class="dateline">{fmt_when(i)}</span></span></a>'
            for n, i in enumerate(stories[1:6], start=2))
        more += ('<a class="hero-item more" href="/news.html">'
                 '<span class="hero-body"><span class="hl-title">All stories &rarr;</span></span></a>')
        desk_html = f"""<div class="sec-head"><h2>Today at the desk</h2><span class="bar"></span></div>
  <div class="hero-band">{hero_video}<div class="hero-band-inner">
    <div class="hero-grid">{lead_html}{bl_card}</div>
    <div class="hero-more-lab">More from the desk</div>
    <div class="hero-more">{more}</div>
  </div></div>"""

    # The Editions: the desk's daily synthesis as its own strip, one card per slot
    # (morning / midday / evening), newest first, never older than the current news cycle.
    # EDITIONS STALENESS (owner directive 2026-07-27, same rule as the featured band):
    # a card older than 24 hours never renders and the strip collapses entirely when
    # nothing fresh exists; the live-dot only ever sits on a fresh card. A July 23
    # brief wearing a live-dot on July 27 slipped through this module.
    _now = _build_now()
    wraps = [i for i in items if _is_wrap(i) and not i.get("example")
             and _fresh_hours(i, _now) <= 24]
    ed_cards, seen_slots = [], set()
    if wraps:
        recent = sorted({w.get("date", "") for w in wraps}, reverse=True)[:2]
        for w in wraps:
            if len(ed_cards) >= 3:
                break
            if (w.get("date") or "") not in recent:
                continue
            title = w.get("title") or ""
            kick, _, hook = title.partition(":")
            if not hook:
                kick, hook = "The Daily Edition", title
            if kick in seen_slots:
                continue
            seen_slots.add(kick)
            fact = w.get("key_fact") or w.get("dek") or ""
            dot = '<span class="live-dot"></span>' if not ed_cards else ''
            ed_cards.append(
                f'<a class="edition-card reveal" href="/articles/{esc(w["slug"])}.html">'
                f'<span class="ed-kick">{esc(kick)}{dot}</span>'
                f'<span class="ed-title">{esc(hook.strip())}</span>'
                f'<span class="ed-fact">{esc(fact)}</span>'
                f'<span class="dateline">{_blink_when(w)}</span></a>')
    editions_html = ""
    if ed_cards:
        editions_html = (f'<div class="sec-head" style="margin-top:26px"><h2>The Editions</h2>'
                         f'<span class="bar"></span></div>'
                         f'<p class="pc-note" style="margin:0 0 10px">The desk\'s daily synthesis: '
                         f'morning, midday, and evening reads over everything published.</p>'
                         f'<div class="edition-strip">{"".join(ed_cards)}</div>')

    # Tracking: the narratives watchlist, each chip linking to its latest published chapter.
    track_html = ""
    chips = []
    try:
        watch = json.load(open(os.path.join(HERE, "config.json"),
                               encoding="utf-8")).get("narratives", {}).get("watchlist", [])
    except Exception:
        watch = []
    _tracking_match = tracking_match
    for n in watch:
        kws = n.get("keywords") or []
        if not kws:
            continue
        rx = re.compile(r"\b(?:" + "|".join(re.escape(k) for k in kws) + r")\b", re.I)
        cands = [i for i in live if not i.get("superseded_by") and _tracking_match(i, rx)]
        cands.sort(key=lambda i: i.get("published_utc") or "", reverse=True)
        hit = cands[0] if cands else None
        if hit:
            # BUILD GATE (owner directive 2026-07-27): a chip whose target does not
            # carry its storyline fails the build rather than shipping a mislink.
            if not _tracking_match(hit, rx) or hit.get("superseded_by"):
                raise SystemExit(f"tracking chip mismatch: {n.get('name')} -> {hit.get('slug')}")
            chips.append(f'<a class="chip" href="/articles/{esc(hit["slug"])}.html">'
                         f'{esc(n.get("name", ""))}</a>')
    if chips:
        track_html = (f'<div class="tracking"><span class="lab">Tracking</span>{"".join(chips)}'
                      f'<span class="mut">the storylines the desk is following</span></div>')

    # The Bottom Line lives in the hero square beside the lead (owner call 2026-07-16);
    # the standalone band below is retired on home. /bottom-line.html keeps the history.
    body = f"""<main class="wrap"><h1 class="sr-only">GoCheckMyNews: the latest verified news</h1><section class="page">
  {desk_html}
  {editions_html}
  {track_html}
  <p class="lede home-lede" style="margin-top:22px">Built with one intention: report what
     actually happened and keep the facts honest. Every story is checked against the official
     public record and outlets deliberately spread across the political spectrum, with the
     spin and the hype stripped out. Every cited outlet carries its published
     <a href="/sources.html">bias and factual rating</a>, shown with attribution. No advocacy,
     no paid promotion, and never advice. Everything here is free, and every source is
     linked.</p>
</section></main>""" + newsletter()
    return shell(f"{FAMILY} - The news, checked.", FAMILY_DESC, "Home", body, dateline, path="/")


def render_archive(items, dateline):
    live = [i for i in items if not i.get("example") and not i.get("superseded_by")]
    if live:
        # group by day, newest first (items are already sorted): a researcher scans by date
        days = []
        for i in live:
            if not days or days[-1][0] != i.get("date"):
                days.append((i.get("date"), []))
            days[-1][1].append(i)
        inner = "".join(
            f'<div class="sec-head" style="margin-top:22px"><h2>{esc(fmt_date(d))}</h2>'
            f'<span class="bar"></span></div><div class="grid">'
            + "".join(card(i) for i in group) + "</div>"
            for d, group in days)
    else:
        inner = ('<div class="empty"><span class="k">Archive is empty</span>'
                 '<p style="margin:.6em 0 0">No stories have been approved and published yet.</p></div>')
    body = f"""<main class="wrap"><h1 class="sr-only">GoCheckMyNews archive</h1><section class="sec">
    <div class="sec-head"><h2>Archive</h2><span class="bar"></span></div>
    {inner}
  </section></main>"""
    return shell(f"Archive - {NAME}", "Every published GoCheckMyNews story.", "Archive", body,
                 dateline, path="/archive.html")


# ---- static editorial pages --------------------------------------------------

def render_method(items, dateline):
    example = next((i for i in items if i.get("example")), None)
    ex_html = ""
    if example:
        ex_html = (f'<h2>What a finished story looks like</h2>'
                   f'<p>Here is the format, using an illustrative example (not a real story):</p>'
                   f'<div style="margin:18px 0">{card(example)}</div>'
                   f'<p><a href="/articles/{esc(example["slug"])}.html">Open the example story &rarr;</a></p>')
    body = f"""<main class="wrap narrow"><section class="page">
  <span class="kicker">Method</span>
  <h1>How we work</h1>
  <p class="lede">The standards this desk holds itself to, and the things it does not do.</p>

  <h2>What we aim for</h2>
  <ul>
    <li><b>Sources you can check.</b> Stories link the material they draw on, and the public
        record such as court rulings, central bank releases and legislation carries more weight
        here than anyone's account of it.</li>
    <li><b>More than one side of the story.</b> Our intake is deliberately spread across the
        political spectrum, so the desk is not reading one side alone. Outlets carry a published
        bias and factual rating, shown with attribution;
        <a href="/sources.html">how we rate sources</a> explains whose ratings those are. News
        corroborated across several lanes gets more prominence than news carried by one.</li>
    <li><b>A second look before publication.</b> Stories are checked against the sources they
        cite, by a pass separate from the one that assembled them. Work that does not hold up
        is held back or dropped rather than smoothed over.</li>
    <li><b>Labels on unsettled news.</b> Where something is reported rather than confirmed, we
        try to say so plainly and name who reported it.</li>
    <li><b>Human oversight.</b> A human editor-in-chief oversees the desk, can hold or remove
        anything, and owns the opinion and analysis published here.</li>
  </ul>

  {ex_html}

  <h2>What we do not do</h2>
  <ul>
    <li>We do not advocate. We report events and explain what they may mean, never what
        to believe, whom to vote for, or what to buy.</li>
    <li>We do not endorse candidates, parties, policies or products.</li>
    <li>We do not present a single-source claim as settled fact.</li>
    <li>We do not run paid coverage as news.</li>
    <li>We will not let the machine speak in a human voice. Takes, analysis, and corrections
        are human work, always.</li>
  </ul>
  <p class="nfa">{esc(NFA)}</p>
</section></main>"""
    return shell(f"How we work - {NAME}", "How GoCheckMyNews ranks, verifies, and approves every story.",
                 "How we work", body, dateline, path="/method.html")


def render_about(dateline):
    body = f"""<main class="wrap narrow"><section class="page">
  <span class="kicker">About</span>
  <h1>Why GoCheckMyNews exists</h1>
  <p class="lede">The news is drowning in advocacy, aggregation, and outrage engineering.
     The scarce thing is a desk that checks the story and shows you the source's track
     record. That is the entire product.</p>

  <p>Too much "news" is noise wearing a press badge: claims sourced to nobody, aggregation
     of aggregation until the original quote is unrecognizable, one side's telling presented
     as the whole story, and opinion dressed as reporting. It is exhausting, and it is how
     readers get misled.</p>

  <p>GoCheckMyNews is built on one idea: every story, sourced; every source, rated. We
     report what actually happened, verify every claim against the official public record
     and on-record sources before it runs, and show you each cited outlet's published bias
     lane and factual grade, with attribution, right next to the link. We deliberately read
     across the political spectrum so the desk never sees one side's telling alone, and a
     story corroborated across that spectrum says so on its card.</p>

  <h2>The machine does the grind. A human owns the judgment.</h2>
  <p>An AI newsroom does the reading, the triage, the fact-checking, and the first draft, every day,
     without getting tired. But the machine is the staff, not the editor. A story runs only when an
     independent verification pass confirms it against its sources; anything flagged waits for the
     human editor-in-chief, who oversees the desk, overrides the machine where judgment differs, and
     owns every take: no opinion ever goes out in a human voice unless a human wrote it. If that
     standard ever slips, we drop the cadence before we drop the standard.</p>

  <h2>Our bias</h2>
  <p>We are biased toward the reader and against the spin cycle. We weight the official
     public record and on-record sources most, we read outlets across the spectrum and show
     you their published ratings, we link every source, and we would rather publish nothing
     on a given day than publish something we cannot stand behind. We do not endorse
     candidates, parties, policies, or products, and we do not tell you what to think.</p>

  <h2>What we are not</h2>
  <p>We are not an advocacy shop, a partisan outlet, or an advice column, and nothing here
     is political advocacy, legal advice, or financial advice. We report what happened and,
     carefully, what it may mean. What you do with that is yours.</p>

  <h2>Contact the desk</h2>
  <p class="operator">This website is operated by Go Check My Brands LLC, a South Carolina limited liability company. Contact: hello@gocheckmynews.com.</p>
  <p>Tips, corrections, and questions: <a href="mailto:desk@gocheckmynews.com">desk@gocheckmynews.com</a>.</p>
  <p>Sponsorship inquiries: <a href="mailto:desk@gocheckmynews.com">desk@gocheckmynews.com</a>.
     Sponsorship never buys coverage; see <a href="/method.html">how we work</a>.</p>

  <div class="callout"><b>Read next:</b> <a href="/method.html">How we work</a>, the
    the standards every story has to clear. <a href="/sources.html">How we rate
    sources</a>, whose ratings the credibility chips carry. Or <a href="/standards.html">our
    standards and corrections policy</a>.</div>
  <p class="nfa">{esc(NFA)}</p>
</section></main>"""
    return shell(f"About - {NAME}", "Why GoCheckMyNews exists: an honest daily news desk "
                 "that checks every story against its sources and rates every source in the open.",
                 "About", body, dateline, path="/about.html")


def render_standards(dateline):
    body = f"""<main class="wrap narrow"><section class="page">
  <span class="kicker">Standards</span>
  <h1>Standards and corrections</h1>
  <p class="lede">What you can hold us to.</p>

  <h2>Sourcing</h2>
  <p>Stories link the sources they draw on. We give most weight to the official public
     record: rulings, releases and legislation straight from the courts, the central bank and
     Congress carry more weight than any outlet's retelling. Below that sit established
     outlets deliberately spread across the political spectrum, so the desk is not reading one
     side alone. A claim resting on a single source below that primary tier is labelled as
     unconfirmed or left out.</p>

  <h2>Source ratings, attributed</h2>
  <p>Cited outlets render with a credibility chip where we have a rating for them: its coarse bias lane and factual
     grade per the public AllSides and Media Bias/Fact Check charts. Those ratings belong to
     those organizations, not to us; we transcribe them, attribute them, and re-check them
     periodically. A domain we have not seeded renders as unrated, honestly. The full table and
     the reasoning live at <a href="/sources.html">how we rate sources</a>. A story whose
     corroboration spans three or more bias lanes carries a "corroborated across the
     spectrum" marker on its card.</p>

  <h2>Verification</h2>
  <p>Stories are checked against the sources they cite by a pass separate from the one that
     assembled them. Work that does not hold up is labelled clearly for the reader or held
     back. We would rather be slow than wrong.</p>

  <h2>Oversight</h2>
  <p>A human editor-in-chief oversees the desk, can hold or remove anything, and owns the
     opinion and analysis published here.</p>

  <h2>Never advocacy, never advice</h2>
  <p>We report events and explain what they may mean. We do not editorialize, we do not
     endorse candidates, parties, policies, or products, and we do not advise. Nothing on
     this site is political advocacy, legal advice, or financial advice.</p>

  <h2>Corrections</h2>
  <p>When we get something wrong, we fix it promptly and note the correction on the story
     itself. To report an error, email
     <a href="mailto:desk@gocheckmynews.com">desk@gocheckmynews.com</a> with a link to the
     story and, if you have it, the source that shows the error; we check every report
     against the sources. A correction is a feature of an honest desk, not a failure.</p>

  <h2>AI disclosure</h2>
  <p>Stories on this site are produced with AI assistance and reviewed before publication,
     under a human editor-in-chief who oversees the desk. Opinion, analysis and corrections
     are human work. We say so because you should know what you are reading.</p>
  <p class="nfa">{esc(NFA)}</p>
</section></main>"""
    return shell(f"Standards - {NAME}", "GoCheckMyNews standards, verification, and corrections policy.",
                 "Standards", body, dateline, path="/standards.html")


def render_sources_page(dateline):
    """/sources.html: the methods page behind the credibility chips. Every rated outlet,
    grouped by bias lane, with its factual grade, the attribution line from
    site/data/credibility.json verbatim, and an honest account of whose ratings these are
    and why the lanes are deliberately coarse."""
    attr_line = CRED_ATTRIBUTION.get("line") or ""
    seeded = CRED_REVIEW.get("seeded") or ""
    next_due = CRED_REVIEW.get("next_review_due") or ""
    cadence = CRED_REVIEW.get("cadence") or ""
    chart_links = "".join(
        f'<li><a href="{esc(u)}" rel="nofollow noopener">{esc(lbl)}</a></li>'
        for lbl, u in [("The AllSides Media Bias Chart", CRED_ATTRIBUTION.get("allsides")),
                       ("Media Bias/Fact Check", CRED_ATTRIBUTION.get("mbfc")),
                       ("The Ad Fontes Media Bias Chart", CRED_ATTRIBUTION.get("adfontes"))]
        if u)
    groups = {}
    for dom, rec in sorted(CRED_DOMAINS.items()):
        groups.setdefault(rec.get("bias") or "unlisted", []).append((dom, rec))
    lane_html = ""
    for lane, lane_label in CRED_LANES:
        if lane not in groups:
            continue
        rows = ""
        for dom, rec in groups[lane]:
            name = _by_domain(dom, OUTLETS) or dom
            side = f'<span class="cred-side">{esc(rec["note"])}</span>' if rec.get("note") else ""
            rows += (f'<li><b>{esc(name)}</b> <span class="mut">({esc(dom)})</span> '
                     f'<span class="tag cred">{esc(cred_chip_label(rec))}</span>{side}</li>')
        lane_html += (f'<h2>{esc(lane_label)}</h2>'
                      f'<ul class="cred-list">{rows}</ul>')
    review_line = ""
    if seeded:
        review_line = (f'<p>The table was seeded on {esc(seeded)}'
                       + (f' and is re-verified against the current published charts on a '
                          f'{esc(cadence)} cadence; the next review is due {esc(next_due)}.'
                          if next_due else '.') + '</p>')
    body = f"""<main class="wrap narrow"><section class="page">
  <span class="kicker">Sources</span>
  <h1>How we rate sources</h1>
  <p class="lede">Every source this desk cites renders with a small credibility chip: the
     outlet's bias lane and factual-reporting grade. This page is the honest fine print
     behind those chips: whose ratings they are, how coarse they are on purpose, and the
     full table.</p>

  <h2>Whose ratings these are (not ours)</h2>
  <p>{esc(attr_line)}</p>
  <p>The ratings on this site belong to those public charts, not to this desk. We transcribe
     them at a deliberately coarse granularity, attribute them every place they appear, and
     re-check them against the published charts on a schedule, because chart ratings move.
     We claim no media-bias methodology of our own, and a chip is never this desk's opinion
     of an outlet. The charts we transcribe from and cross-reference:</p>
  <ul>{chart_links}</ul>
  {review_line}

  <h2>Why the lanes are coarse</h2>
  <p>Bias rating is judgment, not measurement, and false precision is its own kind of spin.
     So the desk keeps whole lanes (left, lean-left, center, lean-right, right, libertarian)
     instead of scores, and three factual grades (high, mostly-high, mixed) instead of
     percentages. The chip is meant to answer one reader question fast: roughly where does
     this outlet sit, and how reliable is its reporting? For anything finer, follow the
     chart links above.</p>
  <p>"Official record" is not a bias lane; it marks a primary source: the institution's own
     rulings, releases, or legislative record rather than journalism about them. A domain we
     have not seeded renders as <span class="tag cred unrated">unrated</span>, which means
     exactly that and nothing more.</p>

  <h2>How the desk uses the lanes</h2>
  <p>The intake is deliberately spread across the spectrum so the desk never sees one side's
     telling alone, and a story whose corroboration spans three or more lanes carries a
     "corroborated across the spectrum" marker on its card. Reading an outlet is not an
     endorsement of it, and a rating is not a verdict on any single story: every story is
     still verified against its cited sources individually, per
     <a href="/method.html">how we work</a>.</p>

  <h2>The table</h2>
  {lane_html}
  <p class="nfa">{esc(NFA)}</p>
</section></main>"""
    return shell(f"How we rate sources - {NAME}",
                 "Every outlet GoCheckMyNews cites, with its bias lane and factual grade per "
                 "the public AllSides and Media Bias/Fact Check charts, attributed.",
                 "Sources", body, dateline, path="/sources.html")


def render_privacy(dateline):
    body = f"""<main class="wrap narrow"><section class="page">
  <span class="kicker">Privacy</span>
  <h1>Privacy policy</h1>
  <p class="lede">What this site actually collects, which is very little, and where the little
     goes. No accounts, no ads, no cookies set by us.</p>

  <p class="operator">This website is operated by Go Check My Brands LLC, a South Carolina limited liability company. Contact: hello@gocheckmynews.com.</p>

  <h2>The newsletter</h2>
  <p>If you sign up for the daily brief, the email address you submit is stored by Netlify Forms,
     the form service of our hosting provider. We use it only to send the newsletter. We do not
     sell your email address, and we do not share it with anyone else. Every issue includes an
     unsubscribe option, and unsubscribing removes you from the list.</p>

  <h2>Analytics</h2>
  <p>We measure traffic with Cloudflare Web Analytics. It is a cookieless beacon: it counts page
     views and referrers in aggregate and does not build profiles or track you across other
     sites. Cloudflare processes those requests under its own privacy policy.</p>

  <h2>Hosting and server logs</h2>
  <p>The site is served by Netlify. Like any web host, Netlify's infrastructure sees standard
     request data (your IP address and browser user agent) and keeps its own server logs under
     its own privacy policy. We do not receive or store that data ourselves.</p>

  <h2>Fonts</h2>
  <p>Pages load their typefaces from Google Fonts (fonts.googleapis.com and fonts.gstatic.com),
     so your browser makes a request to Google when a page loads. Google processes font requests
     under its own privacy policy.</p>

  <h2>Links out</h2>
  <p>Stories link their sources. Once you leave this site, the site you land on operates
     under its own privacy policy.</p>

  <h2>Contact</h2>
  <p>Questions about this policy, your data, or the newsletter, including unsubscribe requests:
     <a href="mailto:desk@gocheckmynews.com">desk@gocheckmynews.com</a>. A human reads it.</p>

  <h2>Changes</h2>
  <p>This policy changes only when the site's behavior changes, and the date below moves when it
     does. Last updated July 19, 2026.</p>
</section></main>"""
    return shell(f"Privacy - {NAME}",
                 "What GoCheckMyNews collects and where it goes: newsletter emails via Netlify Forms, "
                 "cookieless Cloudflare analytics, and nothing else.",
                 "Privacy", body, dateline, path="/privacy.html")


def render_terms(dateline):
    body = f"""<main class="wrap narrow"><section class="page">
  <span class="kicker">Terms</span>
  <h1>Terms of use</h1>

  <p class="operator">This website is operated by Go Check My Brands LLC, a South Carolina limited liability company. Contact: hello@gocheckmynews.com.</p>

  <h2>Never advocacy, never advice</h2>
  <p>GoCheckMyNews publishes news reporting and plain-language context for education and
     information only. GoCheckMyNews reports events; it does not editorialize and it does
     not advise. Nothing on this site is political advocacy, an endorsement of any
     candidate, party, policy, or product, or legal, financial, medical, or investment
     advice, and nothing here is a recommendation to act.</p>

  <h2>Informational purposes only</h2>
  <p>Stories and context are assembled from public third-party sources (the official public
     record, public feeds, news outlets). Data can be delayed, revised, or wrong at the
     source, and outlet credibility ratings are third-party judgments that change over time.
     Verify anything that matters against primary sources before you act on it. If you spot
     an error in our reporting, our <a href="/standards.html">standards and corrections
     policy</a> explains how to report it and how we fix it.</p>

  <h2>Our content and copyright</h2>
  <p>The original text on this site (story summaries, context, and the editorial pages) is
     &copy; {YEAR} Go Check My Brands LLC. You may quote it with attribution and a link. The
     underlying reporting belongs to the outlets we cite: stories here summarize their
     reporting in our own words and link to the source rather than reproduce it, and
     headlines, quotations, and outlet ratings remain the property of their publishers.</p>

  <h2>Links to other sites</h2>
  <p>Every story links out to its sources. Those sites are not ours: we do not control their
     content, and a link is neither an endorsement of an outlet nor responsibility for what
     it publishes. Once you leave this site, the destination's own terms and policies
     apply.</p>

  <h2>No warranty</h2>
  <p>The site and its data are provided "as is" and "as available," without warranties of any
     kind, express or implied, to the maximum extent permitted by law. We do not warrant that the
     site is accurate, complete, current, or uninterrupted.</p>

  <h2>Limitation of liability</h2>
  <p>To the fullest extent permitted by law, GoCheckMyNews and its operators are not liable for
     any loss or damage arising from your use of this site or reliance on its content, including
     indirect, incidental, or consequential damages.</p>

  <h2>Governing law</h2>
  <p>These terms are governed by the laws of the State of South Carolina, without regard to
     conflict-of-law rules. If you do not agree with these terms, please do not use the site.</p>

  <p class="nfa">Last updated July 21, 2026.</p>
</section></main>"""
    return shell(f"Terms of Use - {NAME}",
                 "What GoCheckMyNews is and is not: news reporting for education and information, "
                 "never advocacy or advice, with no warranty.",
                 "Terms", body, dateline, path="/terms.html")


def render_404(dateline):
    body = """<main class="wrap narrow"><section class="page" style="text-align:center;padding-top:60px">
  <span class="kicker">404</span>
  <h1>That page moved on.</h1>
  <p class="lede" style="margin-left:auto;margin-right:auto">The story you were looking for is not
     here. Try the <a href="/index.html">front page</a> or the <a href="/archive.html">archive</a>.</p>
</section></main>"""
    return shell(f"Not found - {NAME}", "Page not found.", None, body, dateline,
                 path="/404.html", noindex=True)


def render_thanks(dateline):
    body = """<main class="wrap narrow"><section class="page" style="text-align:center;padding-top:60px">
  <span class="kicker">Subscribed</span>
  <h1>You are on the list.</h1>
  <p class="lede" style="margin-left:auto;margin-right:auto">Thanks for subscribing to the brief.
     We will not sell your email, and you can unsubscribe anytime. Back to the
     <a href="/index.html">front page</a>.</p>
</section></main>"""
    return shell(f"Subscribed - {NAME}", "Thanks for subscribing.", None, body, dateline,
                 path="/thanks.html", noindex=True)



# ---- publish-door helpers ----------------------------------------------------

OUTLET_NAMES = {
    "federalreserve.gov": "Federal Reserve",
    "justice.gov": "U.S. Department of Justice",
    "congress.gov": "Congress.gov",
    "supremecourt.gov": "Supreme Court of the United States",
    "npr.org": "NPR",
    "bbc.co.uk": "BBC News", "bbc.com": "BBC News",
    "pbs.org": "PBS NewsHour",
    "nytimes.com": "The New York Times",
    "theguardian.com": "Guardian US",
    "washingtonpost.com": "The Washington Post",
    "wsj.com": "The Wall Street Journal",
    "foxnews.com": "Fox News",
    "nationalreview.com": "National Review",
    "washingtonexaminer.com": "Washington Examiner",
    "reason.com": "Reason",
    "thehill.com": "The Hill",
}


def outlet_name(url):
    """Display name for a cited URL (the writer hands ingest bare URLs; a Sources list
    of raw URLs is unreadable and hides the outlet). Falls back to the domain."""
    from urllib.parse import urlparse
    host = (urlparse(url).netloc or "").lower()
    host = host[4:] if host.startswith("www.") else host
    for dom, name in OUTLET_NAMES.items():
        if host == dom or host.endswith("." + dom):
            return name
    return host or url


def story_shape_problems(item):
    """Deterministic publish-door belt: a malformed story is held with a named reason,
    never silently published. Soft gaps (key_fact, bottom_line) only warn: a thin but
    sound story still ships."""
    hold, warn = [], []
    for k in ("title", "dek"):
        if not str(item.get(k) or "").strip():
            hold.append(f"empty {k}")
    if item.get("title") == "Untitled":
        hold.append("placeholder title")
    if not [x for x in (item.get("body") or []) if str(x).strip()]:
        hold.append("empty body")
    if not item.get("sources"):
        hold.append("no sources")
    import re as _re
    if not _re.match(r"^\d{4}-\d{2}-\d{2}$", str(item.get("date") or "")):
        hold.append(f"malformed date {item.get('date')!r}")
    if not _re.match(r"^[a-z0-9-]{8,}$", str(item.get("slug") or "")):
        hold.append(f"malformed slug {item.get('slug')!r}")
    for k in ("key_fact", "bottom_line"):
        if not str(item.get(k) or "").strip():
            warn.append(f"empty {k}")
    return hold, warn


def _title_words(t):
    import re as _re
    # crude singular merge: "strike"/"strikes" and "test"/"tests" must count as the
    # same word (2026-07-22: a same-day Iran rewrite scored 0.625 against its twin
    # because plural variants split the overlap)
    return {w[:-1] if w.endswith("s") and not w.endswith("ss") else w
            for w in _re.findall(r"[a-z]{4,}", (t or "").lower())}


def find_superseded(title, declared_title, content_dir, hours=96):
    """The slug of an existing story this new one supersedes, or None. Two detectors:
    the editor's explicit declaration (exact title match from its shelf: catches the
    same event under a rewritten angle), and a deterministic word-overlap backstop
    (>=70% of meaningful title words: catches near-identical republication that slips
    past every model). Editions and examples are never superseded."""
    import datetime as _dt
    cutoff = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=hours)).isoformat()
    nw = _title_words(title)
    best = None
    for fn in sorted(os.listdir(content_dir)) if os.path.isdir(content_dir) else []:
        if not fn.endswith(".json") or fn.startswith("_"):
            continue
        try:
            d = json.load(open(os.path.join(content_dir, fn), encoding="utf-8"))
        except Exception:
            continue
        if d.get("example") or "brief" in (d.get("kind") or "") and d.get("id", "").startswith("wrap-"):
            continue
        if d.get("id", "").startswith("wrap-") or d.get("category") == "daily edition":
            continue
        if (d.get("published_utc") or "") < cutoff or d.get("superseded_by"):
            continue
        t = d.get("title") or ""
        if declared_title and t.strip() == declared_title.strip():
            return d.get("slug")
        tw = _title_words(t)
        if nw and tw:
            overlap = len(nw & tw) / min(len(nw), len(tw))
            # same-day rewording is almost always the same event; a cross-day match
            # must clear the stricter bar (day N of a running story is a genuinely
            # different chapter and belongs to the editor's declared-update path)
            import datetime as _dt
            same_day = (d.get("published_utc") or "")[:10] ==                 _dt.datetime.now(_dt.timezone.utc).date().isoformat()
            if overlap >= (0.5 if same_day else 0.7):
                best = d.get("slug")
    return best


def mark_superseded(content_dir, old_slug, new_slug):
    """Rewrite the superseded story's JSON so the homepage retires it and its page
    points forward. Content files are pipeline-owned; this is the one sanctioned
    mutation (the update chain)."""
    for fn in os.listdir(content_dir):
        if not fn.endswith(".json"):
            continue
        path = os.path.join(content_dir, fn)
        try:
            d = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        if d.get("slug") == old_slug:
            d["superseded_by"] = new_slug
            json.dump(d, open(path, "w", encoding="utf-8"), indent=2)
            return True
    return False


# ---- ingest approved payloads -----------------------------------------------

def ingest():
    """Promote approved payloads (out/published/*.json from publish.py) into committed content."""
    if not os.path.isdir(PUBLISHED):
        print("ingest: no out/published/ (nothing approved yet); building from committed content only.")
        return 0
    # date/time from the run, not a wall clock, so builds stay reproducible
    date, published_utc = "undated", ""
    try:
        published_utc = json.load(open(os.path.join(HERE, "out", "items.json"),
                                       encoding="utf-8"))["_meta"]["generated"]
        date = published_utc[:10]
    except Exception:
        pass
    os.makedirs(CONTENT, exist_ok=True)
    # editor rank (1 = lead) so the day's page keeps the desk's editorial order
    rank_map, updates_map = {}, {}
    try:
        ranked = json.load(open(os.path.join(HERE, "out", "editor.json"), encoding="utf-8"))["ranked"]
        rank_map = {r["id"]: i + 1 for i, r in enumerate(ranked)}
        updates_map = {r["id"]: r["updates"] for r in ranked if r.get("updates")}
    except Exception:
        pass
    # the wire timestamp of the story's cluster: the closest thing the desk holds to
    # WHEN THE EVENT HAPPENED; the Breaking label gate depends on it
    event_map = {}
    try:
        event_map = {c["id"]: c.get("timestamp") or "" for c in json.load(
            open(os.path.join(HERE, "out", "items.json"), encoding="utf-8"))["clusters"]}
    except Exception:
        pass
    # verification inputs for the per-story trail (same run, already on disk)
    verifier_reasons, source_checks = {}, {}
    try:
        verifier_reasons = {v["id"]: v for v in json.load(
            open(os.path.join(HERE, "out", "verifier.json"), encoding="utf-8"))["verdicts"]}
    except Exception:
        pass
    try:
        source_checks = json.load(open(os.path.join(HERE, "out", "source_texts.json"),
                                       encoding="utf-8"))
    except Exception:
        pass
    n = 0
    for fn in sorted(os.listdir(PUBLISHED)):
        if not fn.endswith(".json"):
            continue
        rec = json.load(open(os.path.join(PUBLISHED, fn), encoding="utf-8"))
        payload = rec.get("payload", {})
        art = payload.get("article", {})
        title = art.get("title") or "Untitled"
        slug = slugify(title)
        body = art.get("body", "")
        paras = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()] or [body]
        srcs = [{"title": outlet_name(u), "url": u} for u in art.get("sources", [])]
        title = destyle(title)
        # the writer model sometimes slips a process note about the review status into the
        # copy ("Note: flagged for human review."); the article is the finished story only,
        # so any such sentence is stripped from every published field at the door
        note = re.compile(r"(?:Note:\s*)?[^.!?]*(?:flagged for|pending)\s+human\s+review[^.!?]*[.!?]?\s*"
                          r"|[^.!?]*human review before publication[^.!?]*[.!?]?\s*", re.I)
        def scrub(text):
            return note.sub("", destyle(text)).strip()
        paras = [scrub(p) for p in paras]
        paras = [p for p in paras if p]
        item = {
            "id": rec.get("id"), "slug": slug, "kind": "brief",
            "title": title, "dek": scrub((payload.get("script", {}) or {}).get("summary", "")),
            "date": date, "published_utc": published_utc,
            "category": "news", "verdict": rec.get("verdict"),
            "rank": rank_map.get(rec.get("id")),
            "author": "The GoCheckMyNews Desk",
            "key_fact": scrub((payload.get("script", {}) or {}).get("key_fact", "")),
            "bottom_line": scrub(art.get("bottom_line", "")),
            "human_take": destyle(art.get("human_take", "")), "body": paras, "sources": srcs,
        }
        checks = source_checks.get(rec.get("id")) or []
        live = sum(1 for c in checks
                   if c.get("http_status") == 200 and (c.get("source_text") or "").strip())
        if event_map.get(rec.get("id")):
            item["event_utc"] = event_map[rec.get("id")]
        item["verification"] = {
            "verdict": rec.get("verdict"),
            "checked_utc": published_utc,
            "sources_cited": len(srcs),
            "sources_live_checked": live,
        }
        old_slug = find_superseded(title, updates_map.get(rec.get("id")), CONTENT)
        if old_slug and old_slug != slug:
            item["update_of"] = old_slug
        # WHO ELSE REPORTED THIS (owner directive 2026-08-01). Recorded as coverage that
        # exists, never as this story's sources: Google News links are redirects, so they
        # never enter the sources list. Fail-soft and bounded to the handful of stories
        # that actually publish.
        try:
            import corroborate
            also = corroborate.also_reported_by(
                title, (srcs[0].get("title") if srcs else "") or "")
            if also:
                item["also_reported_by"] = [{"outlet": o, "headline": h} for o, h in also]
        except Exception as e:
            print(f"::warning::corroboration lookup failed for {rec.get('id')}: {e}")
        hold, warn = story_shape_problems(item)
        if hold:
            print(f"::warning::ingest: story {rec.get('id')} HELD, not published: "
                  + "; ".join(hold))
            continue
        if warn:
            print(f"::warning::ingest: story {rec.get('id')} shipped with gaps: "
                  + "; ".join(warn))
        # The boundary block passes through UNSCRUBBED and UNDESTYLED, alone among the
        # fields here. Every other value is the desk's own prose and gets house style; these
        # four are the vendor's words, checked character-for-character against the advisory
        # upstream (boundary.check_against_sources) and labeled on the page as quoted. Running
        # destyle over a quotation would edit a version range the desk promised not to touch,
        # for a house-style rule that has never applied to quoted material anyway.
        if boundary.is_complete(art.get("boundary")):
            item["boundary"] = {f: str(art["boundary"][f]) for f in boundary.FIELDS}
        out = os.path.join(CONTENT, f"{date}-{slug}.json")
        json.dump(item, open(out, "w", encoding="utf-8"), indent=2)
        if item.get("update_of"):
            mark_superseded(CONTENT, item["update_of"], slug)
            print(f"  ingested {rec.get('id')} as UPDATE of {item['update_of']} "
                  f"-> {os.path.relpath(out)}")
        else:
            print(f"  ingested {rec.get('id')} -> {os.path.relpath(out)}")
        n += 1
    print(f"ingest: promoted {n} approved item(s) into site content.")
    return n


# ---- build -------------------------------------------------------------------

def _copytree(src, dst):
    os.makedirs(dst, exist_ok=True)
    for root, _dirs, files in os.walk(src):
        rel = os.path.relpath(root, src)
        target = os.path.join(dst, rel) if rel != "." else dst
        os.makedirs(target, exist_ok=True)
        for f in files:
            data = open(os.path.join(root, f), "rb").read()
            open(os.path.join(target, f), "wb").write(data)


def _render_og_card(item):
    """Render this article's OG card into PUBLISH/og/<slug>.png at build time. FAIL-OPEN:
    any problem (Pillow missing, font issue, bad headline) is swallowed so the card simply
    does not exist and the article falls back to the site-wide og-image. A card is a
    nice-to-have; it must never break the site build."""
    try:
        import sys as _sys
        _sys.path.insert(0, os.path.join(HERE, "scripts"))
        import og_render
        cat = (item.get("category") or "").strip().lower()
        # Just "News" on this desk. The family template is "<site word> News", which reads
        # as "News News" here.
        kicker = ("News" if cat in ("", "news")
                  else "Daily Edition" if cat == "daily edition" else item["category"].title())
        og_render.render_card(item.get("title", ""), kicker,
                              os.path.join(PUBLISH, "og", f"{item['slug']}.png"))
        return True
    except Exception as e:
        print(f"::warning::og card skipped for {item.get('slug','?')}: {e}")
        return False


def build():
    items = load_content()
    # dateline reflects the newest content (or a neutral standing line), never a wall clock
    newest = next((i.get("date") for i in items if not i.get("example") and i.get("date")), None)
    dateline = fmt_date(newest).upper() if newest else "AN HONEST NEWS DESK"

    import shutil
    if os.path.isdir(PUBLISH):
        shutil.rmtree(PUBLISH)
    os.makedirs(os.path.join(PUBLISH, "articles"), exist_ok=True)
    _copytree(ASSETS, os.path.join(PUBLISH, "assets"))

    def w(rel, html):
        path = os.path.join(PUBLISH, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w", encoding="utf-8").write(html)

    w("index.html", render_home(items, dateline))
    w("news.html", render_news(items, dateline))
    w("archive.html", render_archive(items, dateline))
    w("method.html", render_method(items, dateline))
    w("about.html", render_about(dateline))
    w("standards.html", render_standards(dateline))
    w("sources.html", render_sources_page(dateline))
    w("privacy.html", render_privacy(dateline))
    w("terms.html", render_terms(dateline))
    w("404.html", render_404(dateline))
    w("thanks.html", render_thanks(dateline))
    for it in items:
        _render_og_card(it)  # build-time per-article share card (fail-open) -> PUBLISH/og/
        w(os.path.join("articles", f"{it['slug']}.html"), render_article(it, all_items=items))
    w("bottom-line.html", render_bottom_line_history(items, dateline))
    w("feed.xml", render_feed(items))

    # The site card and the home-screen icon are GENERATED here, not copied from assets.
    # They used to be copied, and all three desks shipped the same committed file: News and
    # Sports were serving the Crypto card, so the one surface a stranger sees carried the
    # wrong brand. Generating removes the class of bug rather than the instance.
    # Fail-open, and fall back to the committed asset, because a missing og:image is worse
    # than a stale one.
    _share_generated = False
    try:
        import sys as _sys
        _sys.path.insert(0, os.path.join(HERE, "scripts"))
        import og_render
        og_render.render_site_card(os.path.join(PUBLISH, "og-image.png"))
        og_render.render_icon(os.path.join(PUBLISH, "apple-touch-icon.png"), 180)
        _share_generated = True
    except Exception as e:
        print(f"::warning::share surfaces not generated, falling back to committed: {e}")
    if not _share_generated:
        for _name in ("apple-touch-icon.png", "og-image.png"):
            _src = os.path.join(ASSETS, _name)
            if os.path.exists(_src):
                open(os.path.join(PUBLISH, _name), "wb").write(open(_src, "rb").read())
    # A web app manifest, so saving to a home screen picks up the desk's own name and colour
    # instead of the page title and a browser default.
    with open(os.path.join(PUBLISH, "site.webmanifest"), "w", encoding="utf-8") as _mf:
        # FAMILY, not NAME. NAME is the byline persona on this desk (Crypto Cronkite), and
        # a home-screen label is a brand surface: the wordmark is the hero there too.
        json.dump({"name": FAMILY, "short_name": SHORT_NAME, "start_url": "/",
                   "display": "standalone", "background_color": "#FBFAF6",
                   "theme_color": THEME_COLOR,
                   "icons": [{"src": "/apple-touch-icon.png", "sizes": "180x180",
                              "type": "image/png"}]}, _mf, ensure_ascii=False, indent=1)

    # sitemap (indexable pages only; 404/thanks are noindex), robots, netlify 404 redirect
    locs = ["/", "/news.html",
            "/archive.html", "/bottom-line.html", "/method.html", "/sources.html",
            "/about.html", "/standards.html", "/privacy.html", "/terms.html"]
    locs += [f"/articles/{it['slug']}.html" for it in items if not it.get("example")]
    urls = "\n".join(f"  <url><loc>{ORIGIN}{esc(p)}</loc></url>" for p in locs)
    w("sitemap.xml", '<?xml version="1.0" encoding="UTF-8"?>\n'
      '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + urls + "\n</urlset>\n")

    # GOOGLE NEWS SITEMAP (2026-07-31): a standard sitemap tells crawlers a page exists;
    # this tells Google News a page is FRESH NEWS worth crawling now, and it is the entry
    # requirement for News/Discover surfacing. Spec: articles from the last 48 hours only,
    # nothing static, max 1000 URLs, W3C publication_date. An empty news sitemap is valid
    # and correct on a quiet day, so this never fabricates freshness.
    _news_now = _build_now()
    news_rows = []
    for it in items:
        if it.get("example") or it.get("superseded_by"):
            continue
        try:
            when = datetime.datetime.fromisoformat(
                (it.get("published_utc") or "").replace("Z", "+00:00"))
        except Exception:
            continue
        if (_news_now - when).total_seconds() > 48 * 3600:
            continue
        news_rows.append(
            f"  <url>\n"
            f"    <loc>{ORIGIN}/articles/{esc(it['slug'])}.html</loc>\n"
            f"    <news:news>\n"
            f"      <news:publication>\n"
            f"        <news:name>{esc(NAME)}</news:name>\n"
            f"        <news:language>en</news:language>\n"
            f"      </news:publication>\n"
            f"      <news:publication_date>{esc(when.strftime('%Y-%m-%dT%H:%M:%SZ'))}"
            f"</news:publication_date>\n"
            f"      <news:title>{esc(it.get('title') or '')}</news:title>\n"
            f"    </news:news>\n"
            f"  </url>")
    news_rows = news_rows[:1000]
    w("news-sitemap.xml",
      '<?xml version="1.0" encoding="UTF-8"?>\n'
      '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"\n'
      '        xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">\n'
      + ("\n".join(news_rows) + "\n" if news_rows else "") + "</urlset>\n")

    w("robots.txt", f"User-agent: *\nAllow: /\n\nSitemap: {ORIGIN}/sitemap.xml\n"
                    f"Sitemap: {ORIGIN}/news-sitemap.xml\n")
    w("_redirects", "/*  /404.html  404\n")
    n_live = sum(1 for i in items if not i.get("example"))
    print(f"site: built {PUBLISH} - {n_live} published stor{'y' if n_live == 1 else 'ies'} "
          f"+ {len(items) - n_live} example, plus home/archive/method/sources/about/standards/404.")
    return 0


def main():
    if "--ingest" in sys.argv:
        ingest()
    build()


if __name__ == "__main__":
    main()
