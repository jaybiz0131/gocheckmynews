"""The family handoff builder, which had no tests in a 661-file pipeline.

Run: python3 tests/test_family_modules.py

WHY THIS ONE MATTERS MORE THAN ITS SIZE. Every module link carries ?src= so
the family rollup can count the handoff, and the receiving site validates that
value strictly in family.js:

    ^[a-z0-9]+_[a-z0-9-]+__[a-z0-9-]+$   and 60 characters

Anything else is SILENTLY IGNORED. The link still works, the reader still
arrives, and the handoff counts as nothing. family_modules.py already shortens
long news slugs for exactly this reason; nothing checked that the result
survives the receiver. The regex below is copied from the receiver rather than
restated, which is the point.
"""
import re
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import family_modules as fm

RECEIVER = re.compile(r"^[a-z0-9]+_[a-z0-9-]+__[a-z0-9-]+$")
RECEIVER_MAX = 60

passed = 0
failed = []


def ok(name):
    global passed
    passed += 1
    print("PASS " + name)


def bad(name, detail=""):
    failed.append(name)
    print("FAIL " + name + (("\n       " + str(detail)) if detail else ""))


def check(name, cond, detail=""):
    ok(name) if cond else bad(name, detail)


# ---- every destination in the table is a real family site ----------------
for cat, (dest, path, label) in fm.TOOL_MODULES.items():
    check(cat + ": destination '" + dest + "' is a known family site", dest in fm.FAMILY, sorted(fm.FAMILY))
    check(cat + ": the path is absolute", path.startswith("/"), path)
    check(cat + ": the link text is a reader's sentence", len(label) > 20 and not label.endswith("."), label)

# ---- the src the receiver will actually accept, for every category -------
SLUGS = [
    "deadly-storms-tornadoes-slam-midwest-causing-widespread-damage-and-outages",
    "fda-recalls-cat-food",
    "a",
    "trailing-hyphen-",
    "-leading-hyphen",
    "x" * 200,
    "india-s-youth-led-protest-movement-enters-government-talks",
]
TEXTS = {
    "recall": "FDA recall of cat food announced",
    "seniorcare": "Assisted living costs rise in nursing homes",
    "storm": "Hurricane makes landfall as evacuation orders widen",
    "estate": "Probate court fight over a will and its beneficiaries",
}
for cat, text in TEXTS.items():
    for slug in SLUGS:
        for site in ("news", "crypto"):
            mod = fm.tool_module(site, slug, text)
            if mod is None:
                bad(cat + "/" + site + ": no module for a story that should match", text)
                continue
            m = re.search(r"[?&]src=([^&]+)$", mod["url"])
            if not m:
                bad(cat + "/" + site + ": the module url carries no src", mod["url"])
                continue
            src = m.group(1)
            check(cat + "/" + site + "/" + slug[:18] + ": src is a shape the receiver accepts",
                  bool(RECEIVER.match(src)), src)
            check(cat + "/" + site + "/" + slug[:18] + ": within the receiver's 60 characters",
                  len(src) <= RECEIVER_MAX, str(len(src)) + " chars: " + src)
            check(cat + "/" + site + "/" + slug[:18] + ": names the sending site",
                  src.startswith(site + "_"), src)

# ---- the shortener's own rules -------------------------------------------
check("the budget is 30", fm.SLUG_BUDGET == 30, fm.SLUG_BUDGET)
for slug in SLUGS + ["", None]:
    short = fm._short_slug(slug)
    check("shortened '" + str(slug)[:20] + "' is never empty", bool(short), repr(short))
    check("shortened '" + str(slug)[:20] + "' is within budget", len(short) <= fm.SLUG_BUDGET, short)
    check("shortened '" + str(slug)[:20] + "' has no stray hyphen at either end",
          not short.startswith("-") and not short.endswith("-"), short)
long_one = fm._short_slug("deadly-storms-tornadoes-slam-midwest-causing-damage")
check("a long slug is cut at a word boundary, not mid-word", not long_one.endswith("-") and long_one in
      "deadly-storms-tornadoes-slam-midwest-causing-damage", long_one)

# ---- the separator respects a path that already has a query -------------
saved = dict(fm.TOOL_MODULES)
try:
    fm.TOOL_MODULES["recall"] = ("pet", "/recall-checker.html?species=cat", "Check recalls")
    mod = fm.tool_module("news", "fda-recalls-cat-food", TEXTS["recall"])
    check("a path with a query gets & rather than a second ?",
          mod and "?species=cat&src=" in mod["url"], mod and mod["url"])
finally:
    fm.TOOL_MODULES.clear()
    fm.TOOL_MODULES.update(saved)

# ---- the exclusion list is what decides, not luck ----------------------
# "Carolina Hurricanes beat the Panthers" produces no module with the
# exclusion removed as well, because nothing in it reads as a storm in the
# first place: the case passes without the guard doing any work. These pairs
# carry a REAL storm signal alongside the collision, so the exclusion is the
# only thing standing between the reader and an absurd pairing, and removing
# it turns them into modules.
for text in ["Hurricane warning issued as the Carolina Hurricanes postpone their game",
             "NFL week 3: hurricane forces evacuation of the stadium",
             "Tornado Cash developer sentenced as tornado warnings cover three states"]:
    check("the exclusion decides, not luck: " + text[:44],
          fm.tool_module("news", "a-slug", text) is None,
          fm.tool_module("news", "a-slug", text))

# ---- no match means no module, never a guess ---------------------------
for text in ["Carolina Hurricanes beat the Panthers in overtime",
             "NFL roundup: week 3 fantasy football",
             "Tornado Cash developer sentenced",
             "Morning brief: what we know so far",
             "A political firestorm engulfs the mayor",
             "An ordinary story about nothing in particular"]:
    check("no module for: " + text[:40], fm.tool_module("news", "a-slug", text) is None,
          fm.tool_module("news", "a-slug", text))

print("\n%d passed, %d failed" % (passed, len(failed)))
sys.exit(1 if failed else 0)
