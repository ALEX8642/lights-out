# 0001 — How this repository is run

Date: 2026-11-16. Status: accepted.

## Context

The code is written with an AI coding assistant, by a maintainer whose background is quality
engineering rather than software development. A reader has no reason to trust such a project on
the author's word. Trust has to come from evidence that is visible in the repository.

## Decision

- Every change goes issue → branch → small pull request → green CI → review → squash merge.
- The assistant opens pull requests. The maintainer reviews and merges them. The assistant does
  not merge its own work into `main`. The one exception is the initial scaffold commit, which
  created the repository.
- CI gates every merge: `ruff check`, `ruff format --check`, `mypy --strict`, `pytest`, and
  `scripts/check_sanitised.py`. GitHub Actions are pinned by commit.
- Safety-critical behaviour (the command policy) has property tests as well as examples.
- Design choices are recorded in `docs/adr/`, numbered, one decision each.
- Releases are tagged, follow semantic versioning and have a changelog section.
- The README states that the project is built with AI assistance.

## Consequences

- Progress is slower than committing straight to `main`, and the history is longer. That is the
  intended trade: the history is part of what the project demonstrates.
- Work can queue behind review. Later changes are stacked on the branch of the change they depend
  on, and their pull requests are retargeted to `main` as earlier ones merge.
- Branch protection on `main` (pull request required, CI required, no force pushes) is applied as
  soon as the hosting plan allows it for this repository.
