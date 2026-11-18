# Security

## What counts as a security bug here

The point of this project is that a language model cannot change a system it is diagnosing.
These count as security bugs:

- A command that changes state, runs another program, reads a protected file or uses shell
  syntax, and that `lights-out policy check` **allows**.
- A fix request with options outside its closed list that is accepted, or one that acts when
  its risk class says it should ask or refuse.
- Anything that leads the harness to pass model text to a shell. It never should.
- A secret, such as an API key read through `--api-key-env`, being written to a transcript.

A harmless read-only command that is refused is not a security bug. Open an ordinary issue for
it. "How the lists grow" in the README describes how those are reviewed.

## Reporting

Please report privately through GitHub's **Report a vulnerability** button on this repository's
Security tab, not in a public issue. Include the command or fix request, the host role, and what
`lights-out policy check` printed. A failing test is ideal.

You should get a reply within a week. Fixed issues are credited in the changelog unless you
would rather not be named.

## Scope and current limits

Drills run against **recorded** incidents. No version so far connects to a live host. When a
live read-only mode arrives (planned for 0.5), this file will describe its trust boundary. The
policy is an allowlist written for a specific set of host roles. Read
`policies/default.toml` before trusting it on your own systems.
