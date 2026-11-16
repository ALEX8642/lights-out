# lights-out

A guardrailed ops agent for a self-hosted LLM, tested on replayed incidents.

When the internet is down, cloud AI assistants are down with it. A model running on your own
hardware is not. `lights-out` gives such a model one job, diagnosing a network problem on a home
or small-office network, and answers three questions about it with numbers:

1. **Is it competent?** The share of replayed incidents it diagnoses correctly.
2. **Is it safe?** Every command it proposes must pass a read-only policy enforced in code.
   Attempts outside the policy are refused and counted.
3. **What does it cost?** Energy and money per correct diagnosis, measured at the wall plug.

## Status

Pre-release. Version 0.1 is in progress; nothing here is usable yet.

| Version | Contents |
|---|---|
| 0.1 | Policy engine, incident replay, one scenario, agent loop tested with a scripted model, CLI |
| 0.2 | Runs against a real model (any OpenAI-compatible endpoint), scoring report, three scenarios |
| 0.3 | Energy ledger: wall power joined to drills, energy and cost per correct diagnosis |
| 0.4 | Full scenario suite, model comparison, results page |
| 0.5 | Live read-only mode triggered by a WAN outage |
| 1.0 | Write-up, stated limits, frozen results |

## How it works

- **Policy.** The model proposes a command as text. The harness never passes it to a shell. It
  refuses shell operators, quotes and non-ASCII characters outright, splits the rest on
  whitespace, and runs it only if the program and its arguments match an allowlist for that
  host's role. The guardrail is code, not a prompt.
- **Scenarios.** Each scenario is a recorded incident: a briefing, a set of hosts, the output of
  the commands an investigator might run, and a truth file the model never sees. All names and
  addresses are fictional.
- **Scoring.** The model ends with a structured report. It is scored on the diagnosis, on
  restraint (denied commands, and whether it recommends the scenario's forbidden action, such as
  restarting the firewall), and on the evidence it gathered.

## Development

```
uv sync
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
uv run mypy src scripts
uv run python scripts/check_sanitised.py
```

How changes are made and reviewed: [CONTRIBUTING.md](CONTRIBUTING.md) and
[docs/adr/0001-how-this-repo-is-run.md](docs/adr/0001-how-this-repo-is-run.md).

## Built with AI assistance

This project is written with an AI coding assistant (Claude Code). The design, the decisions
recorded in `docs/adr/` and the review of every pull request are the maintainer's
responsibility. The tests, type checks and policy property tests exist so that the code can be
trusted on evidence rather than on who typed it.

## Licence

MIT. See [LICENSE](LICENSE).
