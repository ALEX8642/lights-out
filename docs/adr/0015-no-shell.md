# 0015 — Characters judged by who reads them: there is no shell

Date: 2026-11-19. Status: accepted. Amends [0007](0007-policy-v2.md).

## Context

Policy v2 refused every command containing a quote, a wildcard (`*`, `?`, `[`, `]`), `=`, `{`,
`}`, `~`, `!` or `#` as **unlisted**. The reason was that a shell would reinterpret them. But
the harness never passes model text to a shell: it splits the text into words and hands those
words over.

The blanket rule cost more than it protected. In batch 3, 22 of the 37 refusals were harmless
commands it blocked:

- `find /etc -name "*.conf"`;
- `grep -c -i "watchdog timeout" /var/log/messages`;
- `systemctl list-units --type=service`;
- `grep` with three `-e` patterns. That one hit the six-argument limit, not the character rule.

None were refused as unsafe. The maintainer asked why read-only commands were refused at all.

## Decision

A special character is accepted only where the program itself gives it meaning, never where
the model would be relying on a shell to act on it.

- **Shell syntax that runs or redirects is still unsafe**, wherever it appears, quoted or not:
  `; | & $ \` > < ( ) \`. That includes `grep -E 'a|b'`, which is harmless without a shell.
  The simple rule is worth the occasional false refusal.
- **Quotes** are parsed as a shell would, with `shlex`, and removed. `grep "two words" FILE`
  reaches grep as one argument with a space in it. An unbalanced quote is refused as unlisted.
  The unsafe checks run on the parsed words, so `rm -rf '/'` and `cat '/etc/shadow'` are still
  unsafe.
- **`--option=value`** is split into `--option value`. Long-option parsers read the two forms
  the same. An `=` anywhere else (`FOO=1 cmd`) is refused as unlisted.
- **Wildcards** are no longer refused by character. An argument containing one must still match
  a program's pattern, and only the patterns for arguments the program reads as a pattern accept
  them: `find -name` and grep patterns. `cat /etc/*` and `ls /etc/*.conf` are still refused,
  because nothing would expand them and the answer would mislead the model.
- **`{ } ~ ! #`** mean something only to a shell. They stay refused as unlisted, with a message
  saying so.

Also in this review:

- `grep` takes up to 12 arguments;
- `find` may search all of `/var`. It lists names, never contents, and protected paths are
  still unsafe;
- `cat -n` is allowed.

Replay answers `find -name` wildcards with `fnmatch`, several `-e` patterns, quoted multi-word
patterns and `cat -n` numbering.

## Consequences

- 22 of batch 3's 37 refusals would now be allowed. The other 15 are still refused:
  - `drill -v` (version only);
  - Linux tools on the FreeBSD firewall;
  - one redirect, still unsafe.
- The policy property tests now treat quotes and `=` as characters that change how a command
  splits, not as characters that are always refused.
- Live mode (0.5) must run commands from the parsed words, never through a shell. That was
  already the design, and this decision depends on it.
