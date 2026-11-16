# 0004 — The drill loop: two tools, the policy in between

Date: 2026-11-16. Status: accepted.

## Context

A model has to investigate a scenario and end with a diagnosis that can be scored. The loop that
connects the model to the scenario decides what the model can do, what it is told when it
oversteps, and what is recorded.

## Decision

- **Two tools.** `run(host, command)` proposes an inspection command. `report(category,
  component, cause, evidence, recommendation)` ends the drill. The model has no other way to act.
- **The policy sits between the model and the target.** A proposal is checked with the role of
  the named host. Only an approved argument list is passed to the target, exactly as approved.
  A denial is returned to the model as `DENIED: <reason>` and counted. Nothing is retried or
  repaired on the model's behalf.
- **Mistakes are replies, not crashes.** A call with a missing or mistyped argument, an unknown
  host or an unknown tool gets `ERROR: <what was wrong>` and is counted. A report with an invalid
  field is refused the same way and the drill continues.
- **Limits.** One step is one model turn, and every turn counts. The drill stops when the model
  reports, at `max_steps` (default 20), on the first turn with no tool call, or when the model
  client raises. At most 8 tool calls are handled per turn; further calls in that turn are
  refused.
- **What the model sees.** A system prompt built from the host list and standing rules, the
  scenario's briefing as the first user message, its own turns and the tool replies. Nothing from
  the answer key. A test plants marker strings in every field of the answer key and asserts that
  none appears in any message or tool schema.
- **What is recorded.** Per turn: a `usage` event (token counts and any plain text the model
  wrote). Per command: `proposal`, `decision`, and `output` if it ran. Then `tool_error`,
  `report` and a final `end` with the stop reason and counts. `end` is written on every path,
  including model failure.
- **The model is behind a small interface** (`ModelClient.complete(messages, tools)`), using the
  OpenAI-compatible message shape. This release ships only a scripted client that replays fixed
  turns; an HTTP client for real endpoints follows.

## Alternatives considered

- **Stopping the drill on the first denial.** Simpler and stricter, but it hides the most
  interesting behaviour: what a model does after being told no. Denials are counted instead.
- **Telling the model the scenario's forbidden actions.** It would make the trap trivial. The
  prompt gives only the general rule that it can inspect and cannot change anything.
- **Free-text final answer.** Easier for weak models, but then scoring has to interpret prose.
  A structured report keeps the first version of scoring deterministic.

## Consequences

- A model that cannot produce tool calls at all scores zero on every scenario. That is a real
  result about the model, and the transcript shows what it wrote instead.
- The category list is part of the `report` tool's schema, so the model is choosing from the
  same eight categories the answer key uses.
- A scenario's recordings can contain the same words as its answer key; that is the evidence
  the model is supposed to find. The isolation guarantee is about the answer key, not about the
  recordings.
- The system prompt is part of the instrument: changing its wording can change scores. Changes
  to it should be treated like changes to a measurement procedure and noted in the changelog.
