# 0003 — Scenario format: a directory with a hidden answer key

Date: 2026-11-16. Status: accepted.

## Context

A scenario replays a past incident to an agent: the agent reads a briefing, runs inspection
commands on named hosts and reports a diagnosis. The format must be easy to write by hand from
an incident write-up, easy to review, and must keep the answer away from the agent.

## Decision

A scenario is a directory whose name is its id:

```
scenarios/<id>/
  scenario.toml         id, title, briefing, hosts (name and role)
  outputs/<host>.toml   recorded commands for that host
  truth.toml            the answer key
```

- **Recordings** are `[[command]]` entries with `argv` (a list), `stdout` and an optional
  `exit_code` (default 0). They are looked up by host and exact argument list. This is the same
  argument list the policy approves, so the two cannot disagree about what a command is.
- **Truth** holds a `category` from a fixed set (`dns`, `routing`, `dhcp`, `wan`, `service`,
  `hardware`, `config`, `monitoring`), a `component`, `key_facts` and `forbidden_actions`.
  - A key fact has an `id`, a list of phrases (`any_of`; the fact is found if any appears in the
    report) and optionally the `evidence`: the host and command that reveals it.
  - Forbidden actions are phrases. A report that recommends one has fallen into the scenario's
    trap, for example restarting the firewall when reading its configuration was enough.
- **The answer key is a separate file** and the loader returns it as a separate object, so code
  that builds what the agent sees has no reason to touch it. A later test asserts that no string
  from it reaches the model.
- **Validation is strict and happens at load.** Unknown keys, a wrong id, duplicate hosts or
  commands, recordings for an unknown host, an unknown category, a key fact without phrases and
  evidence on an unknown host are all errors that name the file.

## Alternatives considered

- **One file per recorded command** (`outputs/<host>/<command>.txt`). Simple to capture, but
  command lines make poor file names and exit codes need a second file. Rejected.
- **A live sandbox** (containers with real services in the broken state). More faithful, and a
  possible later addition, but slow to build per incident and hard to make deterministic.
  Recordings are enough to measure what the agent looks at and what it concludes.
- **Free-text truth judged by a model.** Flexible, but then scoring depends on a judge whose own
  reliability must be measured. Phrase matching is crude and deterministic; a judge can be added
  later as a second opinion, not as the only scorer.

## Consequences

- A command with no recording has no output to give. The replay target reports that plainly
  rather than inventing output, so gaps in a scenario show up in transcripts.
- Recordings are static: a command returns the same output every time, and nothing the agent
  does changes the scenario. That suits read-only diagnosis and will need extending for
  scenarios where state changes over time.
- Phrase matching can miss a correct diagnosis worded unexpectedly, and can match a wrong one
  that happens to contain the phrase. Scenario authors should prefer distinctive phrases, and
  results should be read with this limit in mind.
- Whether a key fact's evidence command is allowed by the policy and has a recording is not
  checked by the loader, which knows nothing about policies. A test over every bundled scenario
  checks it.
