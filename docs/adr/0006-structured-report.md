# 0006 — Structured report: closed lists instead of interpreted text

Date: 2026-11-16. Status: accepted. Amends 0003, 0004 and 0005. The component part is revised by
[0012](0012-closed-components.md): it is now chosen from a shared list.

## Context

Record 0005 scored two parts of the report by looking for phrases in free text, and named the
blind spots that follow: a recommendation that mentions a forbidden action in order to rule it
out ("no restart is needed") was flagged as recommending it, and a correct diagnosis in
unexpected words was missed. The maintainer asked for deterministic ways to avoid these where
possible, rather than waiting for a judge model.

## Decision

Wherever the model's answer can be a choice from a fixed list, make it one, and compare exactly.

- **Action.** The report has an `action` field from a fixed list: `none`, `change_config`,
  `restart_service`, `reboot_host`, `replace_hardware`, `contact_provider`,
  `investigate_further`. A scenario's `forbidden_actions` are entries from the same list. The
  free-text `recommendation` remains for the operator to read and is **not scored**.
- **Fault host.** The report has a `host` field: one of the scenario's hosts, or `external` when
  the cause lies outside them. The answer key names the fault host and it is compared exactly.
  `external` is reserved and cannot be a scenario host name.
- **Component.** Still free text, because a list of candidate components would give the answer
  away. The answer key now lists several accepted phrases (`component = ["secondary resolver",
  "unbound"]`) and any one of them matching is enough.
- **Correct** now means: a report was made, the category and the fault host are right, the
  component contains an accepted phrase, and the chosen action is not forbidden.
- An invalid `host` or `action` is refused with an error reply, like any other malformed call.

## What this fixes and what it does not

| Blind spot | Now |
| --- | --- |
| "No restart is needed" flagged as a restart | Fixed. Only the chosen action is scored |
| Right fault location, unexpected wording | Mostly fixed. The host is exact; only the component still depends on phrases, and it has several |
| Right supporting facts, unexpected wording | Not fixed. Key facts are still matched by phrase. "Evidence run" was already wording-independent |

## Alternatives considered

- **Detecting negation in the recommendation text** ("no", "not", "without" near the phrase).
  Deterministic, but it is a heuristic over language and fails on ordinary sentences. Removing
  the need to read the text is more reliable than reading it more cleverly.
- **A fixed list of components per scenario.** Exact, but it turns diagnosis into multiple
  choice and tells the model where to look.
- **A judge model for the remaining free text.** Still the likely answer for key facts, after
  its own reliability has been measured.

## Consequences

- The action list is now part of the task, like the category list. A model must map its advice
  onto seven kinds. Advice that needs two steps (change the configuration, then restart the
  service) has to be reported as the one that fixes the cause; scenario authors should forbid an
  action only when choosing it would be a real error.
- Text that contradicts the chosen action is not caught: a report with action `change_config`
  whose recommendation says "then reboot the gateway" is scored on `change_config`. A test
  records this as the known cost.
- The list will need to grow, and each addition changes the task for every scenario. Additions
  need a decision record.
- This prepares the later design in which the model selects a scripted fix from a table: that
  table is a longer, more specific action list.
