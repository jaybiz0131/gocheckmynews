"""Tool-module resolver for the GoCheckMy media brands (ecosystem brief E4).

The media sites earn the audience; the consumer sites own the monetized surface.
So a media article never carries an affiliate link. It carries at most ONE link
to a consumer-site tool, and only when the story is genuinely about that thing.

Attribution is computed at BUILD time, not by client JavaScript. The label is
deterministic ({site}_{slug}__tool-module), so the media sites stay free of the
routing rail and keep shipping zero third-party script.

THIS FILE IS SHARED. It is byte-identical across the media repos. There is no
package manager between these repos, so it is copied rather than imported.
Change it in one place and copy it out; do not fork the logic per site.

Sports deliberately does not call this. Its stories are full of team names that
collide with real hazards: "Hurricanes" and "Panthers" are franchises, and the
sports archive already contains NHL stories that a naive keyword match reads as
storm coverage. A 48-hour hurricane checklist under a broadcast-rights story is
exactly the forced module this rule exists to prevent.
"""

import re

FAMILY = {
    "parents":  ("GoCheckMyParents",  "https://gocheckmyparents.com"),
    "pet":      ("GoCheckMyPet",      "https://gocheckmypet.com"),
    "home":     ("GoCheckMyHome",     "https://gocheckmyhome.com"),
    "mortgage": ("GoCheckMyMortgage", "https://gocheckmymortgage.com"),
    "storm":    ("GoCheckMyStorm",    "https://gocheckmystorm.com"),
    "estate":   ("GoCheckMyEstate",   "https://gocheckmyestate.com"),
    "crypto":   ("GoCheckMyCrypto",   "https://gocheckmycrypto.com"),
}

# category -> (destination site, path, link text)
TOOL_MODULES = {
    "recall":     ("pet",      "/recall-checker.html",
                   "Check your pet's food and medication against current FDA recalls"),
    "seniorcare": ("parents",  "/costs/",
                   "See what senior care actually costs in your state"),
    "medicare":   ("parents",  "/assessment.html",
                   "Work out what level of care fits, free"),
    "housing":    ("home",     "/",
                   "Work out what a home actually costs to own each year"),
    "rates":      ("mortgage", "/",
                   "Check whether your mortgage insurance can come off"),
    "storm":      ("storm",    "/assessment.html",
                   "Run the free storm readiness check"),
    "flood":      ("storm",    "/flood-history.html",
                   "Look up the flood history for an address"),
    "estate":     ("estate",   "/assessment.html",
                   "Run the free estate gap check"),
    "custody":    ("crypto",   "/cold-storage.html",
                   "How self-custody and cold storage actually work"),
}

# Checked FIRST. A match here means no module at all, whatever else the story
# says. These are the collisions that produce absurd pairings rather than merely
# weak ones, and every one is drawn from real archive content.
EXCLUDE = re.compile(
    # Sports franchises that share a name with a real hazard.
    r"\b(?:carolina|miami|florida)\s+(?:hurricanes|panthers)\b"
    r"|\b(?:nhl|nfl|nba|mlb|ncaa)\b"
    r"|\bfantasy football\b"
    # A crypto mixer, not weather. This one appears in headlines, so a
    # body-only guard would not have caught it.
    r"|\btornado cash\b"
    # Round-ups cover many stories at once, so no single tool is honestly "the"
    # next step. Measured against the archive: these digests were the largest
    # single source of wrong matches before the headline-only rule below.
    r"|\b(?:morning|afternoon|evening|weekly|daily)\s+brief\b"
    r"|\bthe\s+brief\b|\bround[- ]?up\b|\bwhat\s+we\s+know\b"
    # Dead metaphors. A political firestorm is not weather.
    r"|\b(?:perfect|political|media|fire|shit|brain)storm\b"
    r"|\bstorm\s+of\s+(?:criticism|protest|controversy|outrage)\b",
    re.I,
)

# First match wins, so specific patterns sit above general ones.
#
# Every pattern here was tightened against the real archive rather than written
# from imagination. Two lessons are baked in:
#   - a bare verb is not a topic. "evacuat*" matched wildfires in France, a
#     landfill collapse in Guinea, and a helicopter crash, none of which a storm
#     readiness check serves. Storm words now have to be storm words.
#
# WILDFIRE IS DELIBERATELY ABSENT, and this is a content gap rather than a
# trigger gap. Do not "fix" it by adding evacuat* or wildfire here.
# Hurricane preparation and wildfire evacuation are opposite problems. Hurricane
# readiness assumes days of warning: shelter in place, harden the house, stock
# up. Wildfire evacuation is minutes to hours: go-bag, leave now. Storm's
# flagship destination is a 14-question "is your home storm-ready" assessment,
# and handing that to someone whose neighbourhood is evacuating is a trust break
# at the moment trust matters most. Partial overlap in the supply calculator and
# the FEMA risk data does not rescue it, because the module points at the
# hurricane assessment.
# US wildfire stories therefore earn no module today. The fix is a real wildfire
# path on Storm (defensible space, go-bag, PSPS and generator, air quality,
# insurance non-renewal), which is a Q4 content decision. When that exists, add
# a "wildfire" category here pointing at it, not at the storm assessment.
# (Owner ruling, 2026-08-28.)
#   - a mention is not a subject. "recalled" is how baseball moves a player up
#     from Triple-A, so a recall needs product or safety context to count.
CATEGORY_PATTERNS = [
    ("recall",     r"\brecall(?:ed|s)?\b(?=[^.]{0,70}\b(?:fda|usda|cpsc|food|drug|product|"
                   r"contaminat\w+|salmonella|listeria|e\.?\s?coli|vehicle|safety|lot\b)\b)"
                   r"|\b(?:fda|usda|cpsc)\b[^.]{0,40}\brecall"),
    ("flood",      r"\bflood(?:ing|plain|water|s)?\b|\bstorm surge\b|\bNFIP\b"),
    ("storm",      r"\bhurricane\b|\btropical storm\b|\btropical depression\b|\btyphoon\b"
                   r"|\bstorms?\b(?!\s+of\b)"
                   r"|\btornado\b|\bstorm surge\b|\bblizzard\b|\bnor'?easter\b"),
    ("medicare",   r"\bmedicare\b(?=[^.]{0,70}\b(?:coverage|benefit|premium|part [abcd]\b|"
                   r"enroll\w*|advantage|supplement)\b)|\blong[- ]term care\b"),
    ("seniorcare", r"\bassisted living\b|\bnursing home\b|\bmemory care\b|\bhome health\b"),
    ("rates",      r"\bmortgage insurance\b|\bPMI\b|\bmortgage rate\b"),
    ("housing",    r"\bproperty tax\b|\bhomeowners?\s+insurance\b|\bclosing cost\b"),
    ("estate",     r"\bprobate\b|\bpower of attorney\b|\bliving will\b"
                   r"|\bbeneficiar\w+\b(?=[^.]{0,50}\b(?:will|estate|trust|inherit\w*|heir)\b)"),
    ("custody",    r"\bcold storage\b|\bself[- ]custody\b|\bhardware wallet\b|\bseed phrase\b"
                   r"|\btrezor\b|\bledger (?:nano|wallet|device)\b"
                   r"|\bexchange (?:hack|breach|collapse)\b"),
]

# The consumer tools are UNITED STATES tools: FEMA flood maps, Medicare data,
# state-by-state care costs. A reader of a story about France, Japan, or China
# is not served by any of them, however well the keywords match, so the whole
# module is withheld rather than offered and wasted.
NON_US = re.compile(
    r"\b(?:france|french|spain|spanish|portugal|greece|crete|italy|germany|"
    r"japan|japanese|china|chinese|taiwan|korea|india|pakistan|philippines|"
    r"indonesia|australia|canada|canadian|british columbia|alberta|ontario|"
    r"quebec|mexico|brazil|guinea|nigeria|kenya|israel|gaza|ukraine|russia|"
    r"iran|iraq|turkey|syria|europe|european|uk|britain|british|scotland|"
    r"ireland|wales)\b",
    re.I,
)

def detect_category(text):
    """Category for a story, or None. None is a valid and common outcome: most
    stories earn no module, and that is the rule working rather than failing."""
    text = text or ""
    if EXCLUDE.search(text):
        return None
    if NON_US.search(text):
        return None
    for name, pat in CATEGORY_PATTERNS:
        if re.search(pat, text, re.I):
            return name
    return None


# News slugs run long ("deadly-storms-tornadoes-slam-midwest-causing-widespread-
# power-outages" is 66 characters), and the src label has to survive TWO caps
# further down the chain, neither of which is ours to widen:
#   - family.js on the destination site rejects an inbound src over 60 chars
#   - the origin is later copied into the network's second click reference,
#     which Awin caps at 50 ASCII characters
# An over-long label is not truncated at either boundary, it is DROPPED, so the
# whole handoff would vanish silently. The slug is shortened here instead, on a
# word boundary, which keeps the label readable in a report. Collisions between
# two articles that share a 30-character prefix are acceptable: the rollup
# groups by route, and the article is a detail within it.
SLUG_BUDGET = 30


def _short_slug(slug):
    slug = (slug or "article")[:SLUG_BUDGET]
    if "-" in slug and len(slug) == SLUG_BUDGET:
        slug = slug.rsplit("-", 1)[0]          # do not end mid-word
    return slug.strip("-") or "article"


def tool_module(from_site, slug, text):
    """Return {'url','text','site_name','category'} or None.

    from_site is the media site key ('news', 'crypto'). Every module from a
    media site is cross-site unless it points at that site's own tools, and
    carries ?src= either way so the family rollup counts the handoff."""
    cat = detect_category(text)
    if not cat:
        return None
    dest_key, path, label = TOOL_MODULES[cat]
    dest = FAMILY.get(dest_key)
    if not dest:
        return None
    name, base = dest
    src = "%s_%s__tool-module" % (from_site, _short_slug(slug))
    sep = "&" if "?" in path else "?"
    return {
        "url": base + path + sep + "src=" + src,
        "text": label,
        "site_name": name,
        "category": cat,
    }


def story_text(item):
    """TITLE-ONLY text for matching.

    This narrowed twice, each time against the real archive rather than by
    guesswork, and each narrowing removed wrong pairings:

    1. Dropping the body removed almost every bad match. A digest that mentioned
       a tropical storm among six other stories, a baseball player "recalled
       from Triple-A", a golf league's "emergency refinancing", a company called
       "the largest beneficiary" of a market recovery. None was ABOUT the thing.

    2. Dropping the dek removed the rest. A story headlined "Coinbase Launches
       Tokenized Stocks" mentioned self-custody wallets in its second sentence;
       a heat-record story mentioned flooding. Still not what those stories are
       about.

    A headline is the closest thing a story has to a statement of its subject.
    The rule is "no category match, no module", so a miss costs nothing and a
    wrong match costs the reader's trust.
    """
    return item.get("title", "") or ""
