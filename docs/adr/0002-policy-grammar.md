# 0002 — Policy grammar: refuse, split, match

Date: 2026-11-16. Status: accepted, amended by [0007](0007-policy-v2.md): arguments in any order, option values bound to options, and refusals classed as unsafe or unlisted.

## Context

The model proposes commands as text. The harness must decide which may run on a host. If the
decision can be argued with, or depends on escaping being done correctly, it is not a guardrail.

## Decision

A command is checked in a fixed order, and the first failure is the answer:

1. Empty, or longer than 512 characters: denied.
2. Any character outside printable ASCII (0x20–0x7E): denied. This covers tabs, newlines, NUL,
   non-breaking and zero-width spaces, and letters from other scripts that look like ASCII.
3. Any of ``; | & $ ` > < ( ) [ ] { } * ? ~ ! # \ " ' =``: denied.
4. Any `..`: denied.
5. The remainder is split on spaces. No shell lexer is involved, because nothing a lexer would
   interpret is left.
6. The first token must be a program listed for the host's role. A path (`/sbin/ip`) is not a
   listed program.
7. The remaining tokens must match one of the program's *forms*: the same number of arguments,
   each matching the whole regular expression at its position.

The policy file is TOML:

```toml
[roles.linux.programs.ping]
forms = [["-c", "[1-5]", "[a-z0-9][a-z0-9.-]*"]]
```

A malformed file (unknown key, empty forms, invalid pattern) fails at load, not at check time.

A denied command is never repaired. The model is told the reason and may propose another.

## Alternatives considered

- **Shell-style lexing (`shlex`) with quoting allowed.** Allows arguments with spaces, but then
  correctness depends on the lexer agreeing with whatever eventually runs the command. Rejected:
  inspection commands do not need quoted arguments.
- **A blocklist of dangerous commands.** Fails open on anything not thought of. Rejected.
- **Letting the model call typed tools (`get_routes()`, `read_resolver_config()`) instead of
  commands.** The safest option and a possible later direction, but it hides the question this
  project wants to measure: what does the model try when it can ask for anything?

## Consequences

- Arguments containing spaces, `=` or quotes cannot be expressed. Commands that need them
  (`journalctl --since=...`) are unavailable until a later record adds a narrower rule.
- Safety of a form rests on its patterns. A pattern for a free value must start with a letter or
  digit so the value cannot be read as an option; the default policy's header states this rule
  and tests cover option-injection cases.
- Positional forms are verbose when a program has optional arguments (see `dig`). Accepted: a
  reader can audit every allowed shape by reading the list.
- Properties tested for arbitrary input: a string containing any refused character is always
  denied; an allowed command yields exactly its space-split tokens; the check never raises.
