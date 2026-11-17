# 0009 — Fixes: a table of scripts, a risk class each, and one approver

Date: 2026-11-17. Status: accepted (design; not yet implemented). Closes the design question in #10.

## Context

So far the agent only inspects. The incidents it is meant for sometimes need an action: on
2026-11-03 the internet stayed down for four hours after a modem reboot until someone reloaded
the firewall's WAN interface by hand. When the internet is down, the cloud assistant that
normally does this work cannot help, and the maintainer may not be reachable either.

Letting a model type state-changing commands is not acceptable: a wrong diagnosis, a confused
model or an injected instruction would go straight to a host. The maintainer's direction
(2026-11-16 and 2026-11-17): dangerous actions should be deterministic scripts with at most a few
safe optional arguments; anything outside them comes to him for approval; that only works when
he can be reached, which during an internet outage means he is at home on the LAN; and the
system needs a risk-based rule for when to act, ask or stop. Only he approves.

## Decision

### The model selects; it never commands

The model gets one more tool, `fix(id, reason, options)`. `id` must be an entry of a fixed table
of fixes. `options` may only contain values from closed lists that the entry declares (for
example `mode: dry-run | apply`, `wait_minutes: 5 | 15 | 30`). There is no free text that reaches
a host: no command, path, host name or argument the model writes. The host a fix acts on is fixed
by the fix itself.

### Each fix is a script that checks itself

Every entry is a script, reviewed like any other code, that:

- **checks its preconditions in code** before acting, from the host's own state, and refuses if
  any fails, whatever the model said (a firewall reboot requires, among others: WAN down for
  60 minutes or more, the WAN watchdog at its reload cap, the modem reporting itself registered,
  no firewall reboot in the last 24 hours);
- **supports a dry run** that reports what it would do and changes nothing; drills only ever use
  it;
- **is rate-limited and safe to repeat**, with its own record of past runs;
- **verifies its result** and reports success or failure in a line it writes itself;
- **runs under its own narrow identity** on the target host: an SSH key restricted by a forced
  command to that one script (the pattern of the existing read-only backup key), so the harness
  cannot run anything else on the host even if the harness itself had a bug.

### Secrets stay with the scripts

A credential a fix needs (the modem's login, a push token) lives on the target host, readable
only by root, next to the script. The model never sends one, never receives one, and cannot read
one: the command policy refuses protected files (record 0007). What a fix returns to the model
is the script's own result line, never raw tool output. A test plants a fake secret in a fix's
fixture and asserts it appears in no transcript.

### A risk class per fix, set in advance by the maintainer

Each fix is scored once, FMEA-style, on three things:

| Factor | Low | Medium | High |
| --- | --- | --- | --- |
| Blast radius if wrong | one service | one host | the whole network |
| Reversibility | undoes itself | scripted undo | none |
| Verifiability | script confirms and stops if not | partly | cannot tell |

The score maps to a class:

| Class | Rule | Example |
| --- | --- | --- |
| Observe | read-only; always allowed | the inspection commands |
| Act alone | low on all three: act, then notify | reload the WAN interface; restart the secondary DNS resolver |
| Ask, then act if unreachable | medium: ask first; act without an answer only under the conditions below | reboot the firewall after the watchdog's reloads are exhausted; turn off a DNS filter whose upstream is unreachable |
| Approval only | high or no undo: wait for an answer however long | anything touching firewall rules or storage |
| Never | not in the table | rebooting the hypervisor; anything destructive |

The class of every fix lives in a file the maintainer approves by pull request. It is decided
before any outage, never during one, and never by the model.

### Who is asked, how, and what silence means

Only the maintainer approves. How he is asked depends on whether he can be reached, and that
changes the channel, not what is allowed:

| Situation | How | No answer |
| --- | --- | --- |
| Internet up | phone notification | wait |
| Internet down, maintainer on the home network | local push through Home Assistant, approve or deny | deny |
| Internet down, maintainer away | no channel | see below |

"Ask, then act if unreachable" proceeds without an answer only if **all** of these hold: the
outage is what prevents contact; the fix's stated waiting time has passed since the question was
first attempted; the cost of waiting is high by the fix's own measure (for example, nobody in the
house has a working internet connection); the fix has not already run in this incident; and its
preconditions still hold at the moment it runs. It runs once. Everything else is queued and
reported when contact returns.

Whether to ask is decided by the harness from the class and the reachability state. The model
cannot pass "skip approval"; there is no such option.

### Watching the band-aids

Automatic fixes hide the problem they fix, so the agent also watches them: for each one, did it
fire, did the symptom clear, and is it firing more often than its baseline? The firing rate is
kept on a control chart; a point beyond the limits or a rising run is escalated with the trend. A
fix at its retry cap is reported as "automatic fixes exhausted". A fix that should have fired and
did not is reported as broken. This is arithmetic over the fixes' own records; the model is used
to explain a trend, not to compute it.

### Drills score the decision

Scenarios declare the fixes they offer, the state each fix's preconditions would see, the
reachability state, and the expected decision. In a drill the harness simulates the fix's
precondition check against that state and records whether the model acted, asked or stopped, and
whether that matched. A model that reboots when it should have waited fails, even if the reboot
would have worked.

## Alternatives considered

- **Allow state-changing commands behind the unsafe list.** The unsafe list is a classifier, not
  a boundary (record 0007); one omission would reach a host.
- **Let the model decide when to ask.** It moves the most important judgement to the least
  trustworthy component.
- **Approval always, for everything.** Safe, but the agent then cannot help in the case it exists
  for: an outage at night with nobody reachable.
- **A judge model to approve fixes.** Adds a second fallible component in the same failure mode.

## Consequences

- The fix table, not the model, bounds what can happen. Adding a fix is a reviewed change with
  a risk class, tests and a dry run.
- The agent's usefulness is limited to what the table covers. A fault without a fix in the table
  is diagnosed and reported, never acted on.
- Reachability detection (is the maintainer home?) and Home Assistant's local approve/deny
  notification are both unverified; live mode cannot start until they are tested.
- Live mode also needs the narrow per-fix SSH identities installed on real hosts, which is a
  separate change with its own review.
