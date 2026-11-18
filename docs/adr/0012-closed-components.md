# 0012 — The component is chosen from a list, and a contributing condition earns partial credit

Date: 2026-11-18. Status: accepted. Revises the component part of
[0006](0006-structured-report.md).

## Context

Batch 1 (`docs/results.md`) turned up two scoring problems.

**Phrase matching on the component misses correct answers.** 0006 kept the component as free
text matched against accepted phrases. It rejected a list of components for each scenario,
because such a list hints at the answer. In the first seven WAN stale-lease drills the model
named the right host, gave the right timeline and asked for the right fix. It called the part
at fault the "DHCP session", and the answer key accepts "DHCP lease". At temperature 0 it used
the same wording five times, so it scored 0 of 5. Adding "session" to the key after seeing the
results would fix this one case. It would also be the post-hoc key change the results page
promises not to make, and the next wording would slip through again.

**Incidents with two causes score all or nothing.** In dns-secondary-lan the primary resolver's
rate limit sends queries to the secondary, and the secondary's rebind filter empties the
answers. The filter is the root cause. At temperature 0 the model blamed the rate limit five
times. That is a reasonable reading and only half right, yet it scored the same as a wrong
answer.

## Decision

1. **One shared, closed list of components** (`COMPONENTS` in `scenario.py`), offered in the
   report tool's schema and compared exactly, the same way as the host and the action. The list
   is generic and the same for every scenario, so it gives nothing away about any one incident.
   That is the difference from the per-scenario list 0006 rejected. It has about two dozen
   entries, from `modem` and `wan_dhcp_lease` to `dns_rate_limit`, `client_config` and `other`.
   Adding one is a reviewed change, like a policy change.
2. The report gets an optional free-text `detail` field for the model's own wording. It is not
   scored.
3. An answer key can name a **contributing condition** (`[contributing]`: a host and accepted
   component ids). The report can name one too (`contributing`, optional). Scoring:
   - The root cause named, with everything else right: **correct**, credit 1. Whether the
     contributing condition was also named is recorded but does not change this.
   - The contributing condition blamed as the root cause, with the category right, no forbidden
     action, and the fix decision not wrong: **partial**, credit 0.5.
   - Anything else: credit 0.
   Success rates and their intervals count only correct drills. Partial drills are reported
   next to them, never folded in.
4. **Older transcripts** carry free text in `component`. They are still scored against the
   answer key's `component_phrases`, unchanged. No contributing phrases exist for them, so they
   can never earn partial credit. **Batch 1 stays exactly as scored.** The new rules apply from
   batch 2, with the answer keys committed before it runs.

## Consequences

- The component is no longer a test of wording. A model that understands the fault but
  describes it unusually is no longer marked wrong.
- A list is easier to guess from than free text, but the list is shared and has about two dozen
  entries. The host and category must also be right. Guessing still needs the diagnosis.
- `other` is a legitimate choice. A drill whose answer is `other` should be rare and is a sign
  that the list needs a new entry.
- Key facts are still matched by phrase. They measure how well a diagnosis is supported, not
  whether it is correct, so their brittleness does not decide a result.
