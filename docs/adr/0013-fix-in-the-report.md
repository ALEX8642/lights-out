# 0013 — The report names the fix, so a drill cannot end without the fix decision

Date: 2026-11-18. Status: accepted. Amends [0009](0009-fix-table-and-approval.md).

## Context

Under 0009 a fix is requested with its own `fix` tool, and the drill ends when the model calls
`report`. Nothing made the model decide about a fix before reporting.

In batch 1, two wan-driver-hang drills at temperature 0.7 diagnosed the hung driver and
recommended a reboot without requesting one. They wrote that the maintainer was away, so the
reboot needed approval. #54 made the prompt describe the fix class fully. In batch 2 the
temperature-0 drills then requested their fixes every time. At 0.7, four drills still put the
fix only in the recommendation text: two wan-driver-hang and two wan-stale-lease. One even wrote
"request the reboot anyway and let the harness decide", then called `report` without calling
`fix`.

That is a model forgetting a step, not misunderstanding the rules. More prompt wording would be
another hope. A field in the report is a structure.

## Decision

When a scenario offers fixes, the report tool has a required `fix` field: one of the offered fix
ids, or `none`.

- A report without a valid `fix` is a tool error. The model is told the choices and can report
  again.
- A named fix that was not already requested is handled exactly as a `fix` call would be: same
  classes, preconditions, reachability and outcome. Its reason is the report's cause, and its
  options are the defaults, which are the first and least disruptive value of each. It is
  recorded as a `fix` event with `via = "report"`, before the report event.
- A fix already requested with the `fix` tool is not requested again. `none` requests nothing.
- The `fix` tool stays, for a fix requested mid-investigation whose outcome the model wants to
  see. Scoring is unchanged: it reads the `fix` events, whichever way they came.

Scenarios without fixes are unchanged.

## Consequences

- Every drill in a scenario with fixes ends with an explicit fix decision, which is what a
  responder must do. Forgetting is no longer possible. Choosing wrongly still is, and it is
  scored as before: a forbidden fix, a fix the harness refuses, or `none` when a fix was
  expected.
- Choosing a fix in the report hides its outcome from the model, because the drill ends. That
  is acceptable: the report is the model's final word. A model that wants to see the outcome
  can use the `fix` tool first.
- Batch 1 and batch 2 transcripts were produced without this field and stay as scored.
