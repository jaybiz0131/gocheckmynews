#!/usr/bin/env python3
"""
editor.py: Stage 2, the managing-editor AI (rank + de-shill).

Reads out/items.json (Stage 1 clusters), sends the cleaned candidate set to the editor
model, and writes out/editor.json with the ranked top stories and the rejected-for-shill
list, each showing its work. Fail-closed: any parse/shape failure raises, and run.py catches
it and publishes nothing.

USAGE
  python3 editor.py                 # live (needs ANTHROPIC_API_KEY)
  DESK_LLM_MODE=replay python3 editor.py   # offline replay (tests only)
"""

import sys

import common
import llm as llmlib


EDITOR_MAX_CLUSTERS = 260


def active_elections(today=None):
    """Election windows from config election_calendar active today (owner directive
    2026-07-22: results nights are the most predictable misses, so they are staffed by
    default). Windows are approximate on purpose; they bias staffing, never facts."""
    import datetime as _dt
    cfg = common.load_config()
    today = today or _dt.datetime.now(_dt.timezone.utc).date().isoformat()
    return [e for e in cfg.get("election_calendar", [])
            if e.get("start", "") <= today <= e.get("end", "")]


def running_threads(now=None):
    """The desk's OWN active storylines with the age of their newest chapter (owner
    directive 2026-07-28: a tracker that does not track is worse than none). For every
    narrative on the watchlist, find the newest published story genuinely about it and
    report how stale that chapter is, so the editor knows which threads a development
    must UPDATE rather than pass over. Deterministic, no model calls."""
    import datetime as _dt
    import glob as _glob
    import json as _json
    import os as _os
    import re as _re
    import sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
    from site_build import tracking_match
    cfg = common.load_config()
    now = now or _dt.datetime.now(_dt.timezone.utc)
    cutoff = (now - _dt.timedelta(days=5)).isoformat()
    stories = []
    for p in _glob.glob(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                                      "site", "content", "*.json")):
        try:
            d = _json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        if (d.get("id", "").startswith("wrap-") or d.get("superseded_by")
                or (d.get("published_utc") or "") < cutoff):
            continue
        stories.append(d)
    threads = []
    for n in cfg.get("narratives", {}).get("watchlist", []):
        kws = n.get("keywords") or []
        if not kws:
            continue
        rx = _re.compile(r"\b(?:" + "|".join(_re.escape(k) for k in kws) + r")\b", _re.I)
        hits = [d for d in stories if tracking_match(d, rx)]
        if not hits:
            continue
        newest = max(hits, key=lambda d: d.get("published_utc") or "")
        try:
            when = _dt.datetime.fromisoformat(
                (newest.get("published_utc") or "").replace("Z", "+00:00"))
            age = round((now - when).total_seconds() / 3600)
        except Exception:
            age = None
        threads.append({"thread": n.get("name", ""), "title": newest.get("title", ""),
                        "hours": age})
    return threads


def build_user(items, top_n):
    pool = items["clusters"]
    if len(pool) > EDITOR_MAX_CLUSTERS:
        # Newest first, keep the cap: a 180-cluster day overwhelms the editor's output
        # budget and truncates its JSON (fail-closed catches it, but we would rather rank
        # the newest 120 than fail). Timestamps are ISO strings; empties sort last.
        # CORROBORATION SURVIVES THE CAP (owner directive 2026-08-18). Sorting by recency
        # alone drops well-attested stories for fresher thin ones, which is backwards on a
        # desk whose promise is cross-outlet verification. The Lakers sale arrived in six
        # separate clusters and still missed the cut. Heavily corroborated clusters are
        # kept first, then the newest fill the rest.
        pool = sorted(pool, key=lambda c: (-len(c.get("corroboration") or []),
                                           c.get("timestamp") or "0"), reverse=False)
        pool = sorted(pool, key=lambda c: len(c.get("corroboration") or []), reverse=True)
        keep = pool[:EDITOR_MAX_CLUSTERS]
        print(f"editor: {len(items['clusters'])} clusters -> capped to "
              f"{EDITOR_MAX_CLUSTERS}, best-corroborated kept first")
        pool = sorted(keep, key=lambda c: c.get("timestamp") or "0", reverse=True)
    clusters = []
    for c in pool:
        clusters.append({
            "id": c["id"], "headline": c["headline"], "source": c["source"],
            "source_tier": c["source_tier"], "url": c["url"], "timestamp": c["timestamp"],
            "snippet": c["snippet"], "corroboration": c.get("corroboration", []),
            "shill_score": c["shill_score"], "shill_flags": c["shill_flags"],
            "shill_rejected": c["shill_rejected"],
        })
    import json
    # THE LIBRARIAN'S SHELF (charter, 2026-07-15): the editor ranks knowing what the desk
    # already ran, so a repeat only ranks as a genuine UPDATE (the deterministic rerun
    # guard remains the backstop at publish).
    import datetime as _dt
    import glob as _glob
    import os as _os
    recent = []
    cutoff = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=48)).isoformat()
    for p in _glob.glob(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                                      "site", "content", "*.json")):
        try:
            d = json.load(open(p, encoding="utf-8"))
            if (d.get("published_utc", "") >= cutoff and d.get("title")
                    and not d.get("id", "").startswith("wrap-")):
                recent.append(d["title"])
        except Exception:
            continue
    shelf = (("\n\nAlready published by this desk in the last 48 hours (a repeat of these "
              "ranks ONLY as a genuine update, and its why_it_matters must say what "
              "changed). When a story you rank is a new chapter of one of these, add "
              "\"updates\": \"<that title EXACTLY as listed>\" to its ranked entry; the "
              "site then retires the old version and stamps the new one as an update:\n" + "\n".join(f"- {t}" for t in sorted(recent)[:25]) + "\n\n")
             if recent else "\n\n")
    elections = active_elections()
    cal = ""
    if elections:
        cal = ("ELECTIONS ON TODAY'S CALENDAR (staffed by default; results and certified "
               "counts from the official record or attributed outlet calls rank ahead of "
               "commentary; never our own projection):\n"
               + "\n".join(f"- {e['name']}" for e in elections) + "\n\n")
    threads = running_threads()
    run_block = ""
    if threads:
        rows = "\n".join(
            f"- {t['thread']}: last chapter \"{t['title']}\""
            + (f" ({t['hours']}h ago)" if t["hours"] is not None else "")
            for t in threads)
        run_block = ("RUNNING STORIES THE DESK IS TELLING (thread, its newest published "
                     "chapter, and how old that chapter is). If the intake carries a "
                     "MATERIAL development on any of these (a new figure, a new decision, "
                     "a named official acting, a next step taken), rank it and set "
                     "\"updates\" to that chapter's title EXACTLY as written, so the thread "
                     "advances instead of going stale. A thread whose chapter is many hours "
                     "old while the wires carry developments is the desk falling behind:\n"
                     + rows + "\n\n")
    # THE CORROBORATION FLOOR (owner directive 2026-08-18, quality over quantity). This
    # does not tell the editor to rank more stories; it tells it which ones it may not
    # ignore. A story independently carried by several outlets is the desk's strongest
    # available quality signal, computed for free at intake, and it is exactly the signal
    # that was present and unused when the Lakers sale (six clusters), Westbrook and the
    # Leavitt departure all missed the cut on the day competitors led with them.
    # Corroboration alone is NOT a quality signal, and testing this against real intake
    # proved it: "PEPECOIN to $10 imminent, get in early" carried thirteen outlets and
    # would have been protected by a naive count. Pump content is precisely what gets
    # republished widely. The floor therefore respects the desk's own shill belt, which
    # already scored that story 9 and rejected it, and takes only clusters the belt left
    # clean.
    floor = sorted((c for c in clusters
                    if len(c.get("corroboration") or []) >= 3
                    and not c.get("shill_rejected")
                    and not (c.get("shill_flags") or [])
                    and (c.get("shill_score") or 0) == 0),
                   key=lambda c: -len(c.get("corroboration") or []))[:5]
    floor_note = ""
    if floor:
        floor_note = ("\n\nINDEPENDENTLY CORROBORATED, RANK OR EXPLAIN: each of these is "
                      "carried by three or more independent outlets, the strongest signal "
                      "this desk has that a story is real and that readers will meet it "
                      "elsewhere. Rank it, or leave it out only for a reason you would "
                      "defend to the editor-in-chief (already covered, not this desk's "
                      "beat, thin despite the outlet count):\n"
                      + "\n".join(f"- {c['id']}: {c['headline'][:110]} "
                                  f"({len(c.get('corroboration') or [])} outlets)"
                                  for c in floor) + "\n")
    return (f"Here are {len(clusters)} deduplicated story clusters from the last "
            f"{items['_meta'].get('lookback_hours', '?')} hours. Rank the top {top_n} real "
            f"stories and reject the shill." + shelf + cal + run_block + json.dumps(clusters, indent=2))


def validate(obj, top_n):
    if not isinstance(obj, dict) or "ranked" not in obj or "rejected" not in obj:
        import json as _json
        raise llmlib.LLMError("editor output missing 'ranked'/'rejected' -- got: "
                              + _json.dumps(obj)[:300])
    if not isinstance(obj["ranked"], list) or not isinstance(obj["rejected"], list):
        raise llmlib.LLMError("editor 'ranked'/'rejected' must be lists")
    if len(obj["ranked"]) > top_n:
        obj["ranked"] = obj["ranked"][:top_n]
    for r in obj["ranked"]:
        for f in ("id", "headline", "why_it_matters"):
            if not r.get(f):
                raise llmlib.LLMError(f"editor ranked item missing '{f}': {r}")
        r.setdefault("source_urls", [])
        r.setdefault("confidence", "medium")
        r.setdefault("category", "other")
    return obj


MAX_SOURCE_URLS = 6


def thread_corroboration(ranked, clusters):
    """Deterministically extend every ranked story's source_urls with its cluster's primary
    URL and corroboration URLs (audit 2026-07-21: the model typically returned only the
    primary URL, so published stories rendered a single credibility chip and the spectrum
    chip never fired). The model's own picks stay first, then the cluster primary, then
    corroborating outlets in tier order; deduped, capped. Downstream this gives the
    verifier cross-outlet pages to confirm against, the researcher more source text for
    the depth gate, and the site multiple chips per story."""
    by_id = {c["id"]: c for c in clusters}
    for r in ranked:
        c = by_id.get(r["id"]) or {}
        urls = [u for u in (r.get("source_urls") or []) if u]
        for u in [c.get("url")] + [x.get("url") for x in (c.get("corroboration") or [])]:
            if u and u not in urls:
                urls.append(u)
        r["source_urls"] = urls[:MAX_SOURCE_URLS]


def run(client=None):
    cfg = common.load_config()
    top_n = cfg["top_n"]
    items = common.read_out("items.json")
    client = client or llmlib.Client(cfg)
    system = common.load_prompt("editor.md", TOP_N=top_n)
    user = build_user(items, top_n)

    obj = client.call_json("editor", system, user,
                           validate=lambda o: validate(o, top_n))
    thread_corroboration(obj["ranked"], items["clusters"])

    obj["_meta"] = {"stage": "2-editor", "mode": client.mode,
                    "candidates": len(items["clusters"]),
                    "ranked": len(obj["ranked"]), "rejected": len(obj["rejected"]),
                    "budget": client.budget.summary()}
    path = common.write_out("editor.json", obj)
    print(f"editor: ranked {len(obj['ranked'])} / rejected {len(obj['rejected'])} "
          f"-> {path} [mode={client.mode}]")
    return obj


def main():
    try:
        run()
    except llmlib.LLMError as e:
        common.gh("error", f"editor: {e}")
        sys.exit(2)


if __name__ == "__main__":
    main()
