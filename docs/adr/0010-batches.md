# 0010 — Batches: rates with intervals, resumable, timed for the energy ledger

Date: 2026-11-18. Status: accepted.

## Context

Every real-model result so far is one drill: it shows the bench works, not how often the model is
right. Comparing models, temperatures or policy changes needs many drills per scenario, a rate
with an honest interval, and timestamps precise enough to attribute wall-plug energy to each
drill. Long batches on a home cluster get interrupted.

## Decision

- `lights-out batch SCENARIO... --out DIR --repeats N --temperatures 0,0.7` runs every scenario N
  times at each temperature, with a fresh model client per drill, against an endpoint or (for
  tests and demonstrations) each scenario's own `script.json`.
- One transcript per drill at `DIR/<scenario>/t<temperature>/<repeat>.jsonl`. After each drill a
  record is appended to `DIR/summary.jsonl`; `DIR/summary.csv` is rewritten at the end. The
  record holds the score's fields, the stop reason, and the first and last event timestamps.
- **Resumable.** A drill already in the summary is not run again. A complete transcript (it ends
  with an `end` event) is rescored from the file. An unfinished one is moved aside to `.partial`
  and redone.
- The summary table gives, per scenario and temperature, correct as k/n with a **Wilson 95%
  interval**, fix decisions, unsafe and unlisted refusals, and mean steps, seconds and tokens.

## Alternatives considered

- **Normal-approximation intervals.** Wrong at the small n and extreme rates this project will
  have (5 of 5 correct is not "100% ± 0").
- **Parallel drills.** Faster, but drills would share the GPU and their energy could not be told
  apart. Sequential is the default; parallelism can come later as an option for runs that do not
  measure energy.

## Consequences

- Energy per drill is attributed from the record's timestamps by a separate ledger step, so a
  batch can be re-priced later (a different idle baseline, a plug without the router) without
  rerunning it.
- Temperature 0 is not guaranteed deterministic on this hardware (judge-msa found verdicts
  differing across machines at temperature 0); repeats at 0 measure that too.

## Amendment (2026-11-19): one step budget for every temperature

Drills that stopped at the 20-step budget without a report: none at temperature 0 in any batch,
2 of 15 at 0.7 in batch 2, and 5 of 30 at 0.7 in batch 3. The budget stays at 20 for every
temperature. An investigation that wanders without concluding is a real weakness in a
responder, and a budget that changed with temperature would make batches harder to compare.
These drills are now counted on their own, as "no report" in the batch table and `out_of_steps`
in the summary, so they are not lost among the wrong diagnoses.
