# 0014 — An answer key can accept more than one category

Date: 2026-11-18. Status: accepted.

## Context

In batch 2, two wan-driver-hang drills diagnosed a hung WAN network driver and chose category
`wan`. The key says `hardware`. Both are fair: the fault is in the firewall's network hardware
or driver, and what it causes is a WAN outage. The categories describe areas, and some faults
sit in two areas.

## Decision

An answer key may list `other_categories`: more entries from the category list that are also
right. Each one must be a real category and different from `category`. Use it only when the
fault genuinely sits in both areas, never to rescue a wrong diagnosis. wan-driver-hang accepts
`wan` as well as `hardware`.

The change applies from batch 3. Batch 1 and batch 2 stay as published.

## Consequences

- Scoring reads the answer key as it is now. Re-scoring an old transcript with a later key can
  therefore give a different verdict from the one published. Every published result names the
  commit it was scored at. To reproduce a result, score it at that commit.
- `other_categories` widens the key, so each use is a reviewed change with its reason written
  next to it in `truth.toml`.
