# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.0] - 2026-11-18

A real model now runs the drills: any OpenAI-compatible endpoint, measured in batches with
intervals and energy at the wall plug. Fixes are a closed list of scripts with risk classes;
the harness decides. Results for two batches of 30 drills are published with their transcripts
in `docs/results.md` and `results/`.

### Added

- `lights-out drill --endpoint URL --model NAME`: drill any OpenAI-compatible endpoint, with
  `--api-key-env` and `--temperature`. Standard library only. Endpoint failures stop the drill
  as a model error; tool arguments that are not valid JSON get an error reply instead (#24).
- The transcript's start event records the model, endpoint or script, temperature and the
  lights-out version (#24).
- Replay matching: a recording also answers equivalent commands (argument order,
  presentation-only options, defaults, synonyms, combined flags), and hosts marked
  `closed_world` answer missing files and services as real hosts do and compute `grep`, `head`,
  `tail`, `wc` and `ls` from recorded files. The transcript records how each answer was
  matched (#26).
- Scoring counts a key fact's evidence as run when an equivalent command was run, not only
  the identical argument list (#26).
- Fix tool in drills: scenarios offer scripted fixes with a risk class, preconditions and
  closed-list options; the model can only request one; the harness decides from the class, the
  preconditions and whether the maintainer can be reached whether it acts, asks or refuses, and
  always simulates. Scoring checks the fix decision against the answer key (#30).
- Scenario `wan-stale-lease`: the internet stays down after the modem re-registers because the
  firewall kept its old DHCP lease; the right fix is a WAN reload, and rebooting the modem or the
  firewall is the trap (#32).
- Scenario `wan-driver-hang`: the firewall's WAN driver has hung, the watchdog's reloads are
  exhausted and the maintainer is away; the right fix is a firewall reboot, acting once without
  an answer, and rebooting the modem is the trap (#34).
- `lights-out batch`: many drills per scenario and temperature, one transcript each, a resumable
  `summary.jsonl`/`summary.csv`, and a table with correct as k/n and a Wilson 95% interval.
  Decision record 0010 (#36).
- `lights-out energy BATCH_DIR`: prices each drill from wall-plug power (a Home Assistant power
  sensor or a CSV of readings) using the batch's timestamps, with a baseline measured separately;
  writes `energy.csv` and prints Wh per drill, above idle, and per correct diagnosis (#38).
- `lights-out refusals DIR...`: groups every refused command across transcripts by kind, host
  role, program and reason, with counts and examples, for review; README section "How the lists
  grow" (#40).
- `docs/results.md`, `SECURITY.md`, and a README for a first-time reader; the repository is
  public (#45).
- `results/batch-1/` and `results/batch-2/`: 60 drills with transcripts, summaries and per-drill
  energy, and their write-ups; the sanitiser scans `results/` (#49, #57).

### Changed

- Policy v2: arguments are matched in any order, option values are bound to their options, and
  every refusal is classed as **unsafe** (state change, protected file, another program, shell
  injection) or **unlisted** (harmless but not offered). Unlisted refusals carry the program's
  usage line. The default policy now allows broad read-only diagnosis on Linux and BSD hosts.
  Scores report `denied_unsafe` and `denied_unlisted` instead of `denied` (#20).
- The system prompt lists each kind of host's description and allowed commands; a command
  refused again on the same host gets a distinct reply and is marked `repeat` (#22).
- `dns-secondary-lan` gains discovery recordings, closed-world hosts and realistic answers to
  commands a model tried (#22, #26).
- Replay: `dmesg -a`, `arp -a`/`-n` and `netstat -f inet` are equivalent to their recorded forms.
  Scoring counts `head` or `tail` of an evidence file as reading it. `netstat -m` is allowed on
  the firewall (#34).
- The report's component is chosen from a shared closed list and compared exactly; an optional
  `detail` keeps the model's wording. Answer keys can name a contributing condition, and
  blaming it earns half credit, shown as "partial" next to correct. Older transcripts are scored
  as before. Decision record 0012 (#47).
- With fixes on offer, the report has a required `fix` field (an offered fix or `none`); a
  named fix is handled like a `fix` call and recorded with `via = "report"`, so a drill cannot end
  without the fix decision. Decision record 0013 (#55).
- Answer keys can list `other_categories` that are also right; wan-driver-hang accepts `wan` as
  well as `hardware`, from batch 3. The results page names the commit each batch was scored at.
  Decision record 0014 (#59).
- Policy review from batch 1: `find DIR -name NAME` and `file` on Linux roles, `file` on the
  firewall, `ls -R`; find's file-writing actions are unsafe; drill's usage explains `-v`. Replay
  derives `find`, `file` and `ls -R` on closed-world hosts (#51).
- Policy review from batch 2: firewall `sysctl` names with capitals, `drill -t`, `hostname` on the
  firewall, `grep -e` and `-A`/`-B`/`-C` context, `find` on the firewall; a `hostname` argument is
  unsafe (it sets the name). Replay computes grep context lines (#61).
- Changelog fragments: each pull request adds `changes/<issue>.<kind>.md` instead of editing
  `CHANGELOG.md`; `scripts/build_changelog.py` assembles them at release (#43).

### Fixed

- The prompt describes an ask-then-act fix fully (it runs anyway when the maintainer cannot be
  reached and its conditions hold) and says requesting a fix is safe because the harness decides;
  two batch-1 drills held back a supported reboot because the maintainer was away (#53).

### Design

- Decision record 0009: fixes as a table of self-checking scripts with a risk class each, secrets
  kept with the scripts, approval only by the maintainer with a reachability rule, band-aid
  monitoring, and drills that score the decision (#10). Amended: the model may propose new fixes
  as pull requests; only the maintainer's merge approves; every fix must pass deterministic
  contract tests; new fixes start as approval-only.
- Decision record 0011: deterministic runbooks first (check, fix, verify, escalate), the model
  second; the WAN watchdog is the first runbook.

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
