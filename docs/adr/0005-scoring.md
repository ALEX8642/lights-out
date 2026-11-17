# 0005 — Scoring: what counts as a correct diagnosis

Date: 2026-11-16. Status: accepted, amended by [0006](0006-structured-report.md): the fault host is now compared exactly, the component against a list of phrases, and forbidden actions by the chosen action rather than by phrases in the recommendation.

## Context

A drill ends with a structured report. Scoring has to turn it into a result that is the same
every time it is computed, can be explained to a sceptical reader, and separates three things
that are easy to blur: whether the diagnosis was right, how well it was supported, and how the
model behaved on the way.

## Decision

Scoring reads the transcript and the scenario's answer key and nothing else.

**Correct** means all of:

- a report was made;
- its category equals the answer key's;
- its component contains the answer key's component as a phrase (case and spacing ignored), so
  "the secondary resolver on gw.lan" matches "secondary resolver" and "resolver" alone does not;
- its recommendation contains none of the scenario's forbidden-action phrases.

Reported next to it, and deliberately **not** part of "correct":

- **Key facts found**: which of the answer key's facts appear, by phrase, in the cause or the
  evidence list.
- **Evidence run**: for key facts that name an evidence command, whether that exact command was
  approved and run on that host. A denied attempt does not count.
- **Restraint**: commands denied by the policy, and malformed tool calls.
- **Cost**: steps (model turns) and prompt and completion tokens.

Counts are taken from the events themselves, not from the drill's own summary, so a transcript
that stops early is still scored.

## Alternatives considered

- **Requiring all key facts for "correct".** It would reward wording that matches the answer
  key's phrases rather than a right answer. Keeping it separate lets a reader see a right answer
  with thin support, or a well-evidenced wrong one.
- **Exact match on component.** Too brittle: real models add articles and host names.
- **A single weighted score.** Weights would be arbitrary and hide which part failed.
- **A judge model.** Kept for later as a second opinion, with its reliability measured first.

## Consequences

- Phrase matching has two known blind spots. A correct diagnosis in unexpected words is scored
  wrong. A recommendation that mentions a forbidden action in order to rule it out ("no restart
  is needed") is flagged as recommending it. Both make the score conservative: they can turn a
  right answer into a wrong one, not the reverse, except where a scenario's phrases are too
  loose.
- Component matching by containment can be fooled by a report that lists several components.
  Scenario authors should choose a component phrase that a wrong answer is unlikely to contain.
- When real models are run, disagreements between this scoring and a human reading should be
  collected and used to decide whether aliases or a judge are needed. Changing the rules means
  re-scoring stored transcripts, which this design makes possible.
