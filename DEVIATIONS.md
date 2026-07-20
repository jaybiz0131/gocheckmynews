# GoCheckMyNews: DEVIATIONS.md

Places where the build diverged from, or must flag a tension in, its instructions.
House rule (inherited from the GoCheckMy family): surface tensions, do not resolve them silently.

---

## Provenance

This repo was cloned from the family's sports chassis on 2026-07-19 and adapted into
the GoCheckMyNews daily general news desk. The chassis's own deviation history stays with
that repo; it documents that desk's decisions, not this one's. Chassis facts that still
matter here live in the adapted docs (README.md, CHARTER.md, NEWS_VERIFY.md,
LAUNCH_CHECKLIST.md), not re-listed as deviations.

Added at cloning: the outlet credibility layer (site/data/credibility.json, the chips
beside every cited source, /sources.html, and the cross-spectrum corroboration marker).
Kept: the full fail-closed pipeline, the verdict-badge honesty UI, the trusted-newsroom
design system (masthead rule retinted to press ink blue, #1D4E79), and the human gate.

## Deviations

### D1 (2026-07-20): the build clock, scoped

House rule: "dateline reflects the newest content, never a wall clock." The daypart
front (home_stack in site_build.py) reads the build-time UTC clock to pick the hero
lead, decay the Breaking badge (3 hours), and anchor The Bottom Line to the current
slot's edition. The clock decides STACKING ONLY; every rendered dateline stays
content-derived. SITE_BUILD_NOW pins the clock for deterministic replays. Tension
surfaced here rather than resolved silently: a static page can present a stale stack
between builds, and the accepted bound is the existing rebuild rhythm (slot publishes,
breaking runs, the 12:00 UTC refresh); no extra builds were added to tighten it.
