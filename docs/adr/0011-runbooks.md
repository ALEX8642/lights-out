# 0011 — Runbooks first, the model second

Date: 2026-11-18. Status: accepted (design; not yet implemented).

## Context

The maintainer asked whether one script could harmlessly fix several common problems at once,
or branch on what it finds. The second already exists in this network: the WAN watchdog checks
the internet, reads the modem, decides, reloads the WAN, retries with back-off and stops at a
cap, with no model involved. The ops world calls this runbook automation or auto-remediation.

Running every known fix blindly is not free (a WAN reload drops connections; a resolver restart
drops queries) and destroys the information that says which fault keeps recurring, which is what
band-aid monitoring needs (record 0009).

## Decision

Two layers.

**Layer 1: runbooks.** A runbook is a fix (record 0009) whose script is an ordered list of
checks, each paired with one fix from the table:

1. run the checks in order; the first that matches selects its fix;
2. apply that fix under its own class and preconditions (a runbook never raises a fix's class);
3. verify; on success, stop and record which branch fired;
4. if no check matches, or the fix does not verify, escalate with everything gathered.

Runbooks run without a model: on a timer, on an alert, or when the model requests one. They work
when the model's hardware is down.

**Layer 2: the model.** It is used when a runbook escalates or for faults no runbook covers. It
diagnoses, and can request a runbook or a single fix from the table, as any other fix.

Both layers are scored by the same scenarios: a scenario can declare which runbook branch should
fire, and whether the model should have been needed at all.

## Alternatives considered

- **One script that applies every known fix.** Rejected for the two reasons in the context.
- **The model for everything.** Slower, costlier in energy, unavailable when its hardware is down,
  and less predictable than a script for a fault that is already understood.

## Consequences

- The first runbook is the existing WAN watchdog, restated in this shape (checks: modem not
  registered → wait; registered → WAN reload; reloads exhausted → escalate or firewall reboot by
  its class).
- Runbook branches become data for band-aid monitoring: which branch fired, how often, and
  whether it held.
- A runbook's coverage is only as good as its checks. Anything they do not recognise must
  escalate rather than guess.
