# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

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
