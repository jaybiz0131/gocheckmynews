# GoCheckMyNews: the News Desk charter

This desk is a GoCheckMy family news desk, cloned from the family's sports chassis
(2026-07-19; that chassis itself descends from the crypto news desk) and operated under the
family newsroom charter. The family master charter lives in the gcm-newsroom repo's
CHARTER.md; this file is this desk's operating half.

Mission: report what actually happened, stripped of spin and advocacy, for a reader
drowning in outrage engineering. Every story, sourced; every source, rated. Speed matters;
accuracy outranks it. A held story costs a slot, a wrong one costs the brand. Fail-closed
everywhere: nothing wrong ships to make a deadline.

THE FOUR STAGES
1. RESEARCHER: feed aggregation (tiered sources: the official public record primary, that
   is rulings, releases, and legislation straight from the courts, the central bank, and
   Congress; outlet RSS major, DELIBERATELY spread across the political spectrum so the
   desk never sees one side's telling alone; aggregators mixed; deduplicated into clusters)
   plus the breaking-news watcher (deterministic thresholds, no model). Draftable stories
   get a structured research brief built from the FULL fetched source pages: data points
   with per-claim confidence labels (official-record / reported / announced-not-verified /
   unconfirmed), a deliberately pulled other-side case, open questions. The institution's
   own record is ground truth that outranks any outlet's retelling.
2. VERIFIER-EDITOR: the editor ranks and strips the hype over the deterministic belt, with
   the desk's last-48h published titles in view (the librarian's shelf: repeats rank only
   as genuine updates). An independent adversarial verifier live-fetches every cited source
   and issues VERIFIED / NEEDS-HUMAN-REVIEW / REJECT. BREAKING-AS-FACT needs two
   INDEPENDENT sources; wire rewrites are not independence; single-source breaking
   publishes only labeled unconfirmed, or holds a slot. Corroboration ACROSS the bias
   spectrum is what earns top billing; every cited outlet carries a published bias/factual
   rating (site/data/credibility.json) shown to readers with attribution: the ratings
   belong to the public charts, never to this desk.
3. WRITER: the desk voice: straight, sourced, no hype, no advocacy, epistemics in the
   prose, the take slot always empty for a human. Brief-bound: no fact outside the brief.
   Across the day's slots, update and extend, never repeat. Never an endorsement, a
   prediction, or a piece of advice; never advocacy dressed as analysis.
4. GATE: deterministic classes (source liveness, dedup/rerun, compliance lint,
   persona/credential, em-dash, depth, template completeness, no-advocacy-no-advice,
   approver sign-off, breaking two-source), the post-draft approver tracing every fact to
   its brief, and the categorized editorial log.

CADENCE: the daily brief on schedule, watcher-triggered breaking runs, slot recovery on
cron drift, contract ladder on small-model output, monthly aging review WITH the correction
loop (corrections.py: premise-flagged stories re-run through research -> write -> approve
and update in place only with the approver's signature, stamped with a correction note),
and a quarterly re-verification of the credibility table against the current published
charts (chart ratings move; stale ratings are their own kind of wrong).

THE BOTTOM LINE: each edition closes with the desk's signature 3-5 sentence read: synthesis
of what happened and why it mattered, never a prediction, never an endorsement, never
advice, never causation beyond sources. It renders beside the lead story on the homepage
and the news desk, refreshed every slot (breaking runs regenerate the current slot's
edition), archived forever at /bottom-line.html, and guarded by its own deterministic gate
on top of the prompt lane.

The reader-facing version of this charter is the site's method page, and the honest fine
print behind the credibility chips is the site's /sources.html page.
