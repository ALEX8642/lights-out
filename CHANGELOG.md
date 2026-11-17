# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-11-16

First release. The guardrail, incident replay and scoring work end to end with a scripted
stand-in for the model. No real model has been run yet.

### Added

- Repository scaffold: CI (lint, format, type check, tests), sanitising check for IPv4
  addresses, contribution workflow and the first decision record.
- Command policy: deny-by-default check of model-proposed commands against a per-role allowlist,
  with a default read-only policy for `linux`, `firewall` and `dns` hosts (#1).
- Scenario format and loader: a recorded incident as a directory with a briefing, hosts, command
  recordings and a hidden answer key, validated at load (#2).
- Replay target: answers approved commands from a scenario's recordings, reports commands with
  no recording as such, and truncates long output (#3).
- Transcript: append-only JSONL record of a drill, written event by event, with a reader that
  tolerates a line cut off by a crash (#4).
- Drill loop: a model investigates a scenario through `run` and `report` tools with the command
  policy enforced in between, limits on steps and calls, and a scripted model client (#5).
- Scoring: a drill is scored from its transcript against the answer key for correctness,
  supporting facts, evidence gathered, restraint and cost (#6).
- Structured report: the fault host and the kind of action are chosen from fixed lists and
  compared exactly; the answer key accepts several component phrases; recommendation text is no
  longer scored (#16).
- Command line: `lights-out policy check`, `drill` and `score`.
- First bundled scenario, `dns-secondary-lan`: local names fail when clients fall back to a
  second resolver that strips private addresses. Tests check that every bundled scenario is
  solvable under the default policy (#7).

### Changed

- The `config` category was removed: a misconfiguration can sit in any area, and the kind of fix
  is already captured by the report's action.
- `lights-out --version`.
