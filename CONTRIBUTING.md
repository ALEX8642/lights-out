# Contributing

This is a single-maintainer project run as if it had a team, so that every change can be traced
and reviewed.

## Workflow

1. **Issue first.** Every change starts as an issue that states what will change and how it will
   be verified.
2. **Branch.** `feat/<topic>`, `fix/<topic>`, `docs/<topic>` or `chore/<topic>` from `main`.
3. **Small pull request.** One issue, one pull request. Tests come with the code that they test.
4. **CI must be green.** Lint, format, type check, tests and the sanitising check.
5. **Review, then squash merge.** The maintainer reviews and merges. The pull request title
   becomes the commit on `main`.

`main` always passes CI. Nothing is pushed to it directly after the initial scaffold commit.

## Commit and pull request titles

[Conventional Commits](https://www.conventionalcommits.org/): `type(scope): summary`, with types
`feat`, `fix`, `docs`, `test`, `refactor`, `chore`, `ci`. Example: `feat(policy): deny-by-default
command policy`.

## Rules for the code

- The policy is enforced in code and denies by default. Nothing the model writes reaches a shell.
- A scenario's `truth.toml` never reaches the model.
- No real IP addresses, hostnames, domains or credentials. `scripts/check_sanitised.py` enforces
  the address part; see its allowlist.
- Write the failing test first.
- A design choice that someone could reasonably have made differently gets a decision record in
  `docs/adr/`.

## Releases

Semantic versioning. Each release has a section in `CHANGELOG.md`, a `vX.Y.Z` tag and a GitHub
release.
