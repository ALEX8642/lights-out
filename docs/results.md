# Results

Every number here comes from a transcript of a real model run. Nothing here is from the
scripted stand-in used in the tests. Results are added as batches finish. A result counts only
if the run used the answer key as committed before it started. The key is never changed to fit
what a model did.

## Set-up

- **Model:** Qwen3.8-Flash-Next (NVFP4), served by vLLM with tensor parallelism across two NVIDIA
  GB10 (DGX Spark) nodes. The harness reaches it as an OpenAI-compatible endpoint.
- **Harness:** `lights-out drill` / `lights-out batch`, `policies/default.toml`, 20-step budget.
- **Energy:** wall-plug power of both nodes from a smart plug, read from Home Assistant's
  history and integrated over each drill's timestamps (`lights-out energy`). The plug also
  carries a network router (about 10 W), so these figures are a slight overestimate. Idle for
  the pair is about 121 W.

## Single drills (2026-11-17)

These are the first runs of each scenario. Each one led to fixes in the bench (the policy,
replay or scoring), never in the answer key. The failures are kept on purpose, because they
show what had to change.

| Scenario | Run | Result | Time | Wh at plug |
|---|---|---|---|---|
| dns-secondary-lan | 1 | no report. Policy v1 refused 33 of 40 commands, nearly all harmless variants | 161 s | 11.3 |
| dns-secondary-lan | 2 | no report. Nothing was refused, but 35 of 43 commands had no recorded output | 171 s | 11.9 |
| dns-secondary-lan | 3 | **correct** in 16 steps; 5 refused, all unlisted, 0 unsafe | 261 s | 18.2 |
| wan-stale-lease | 1 | **correct** in 19 steps. It asked for the right fix (`wan_reload`) and did not touch the modem | 216 s | 15.0 |
| wan-driver-hang | 1 | not correct. Right fix (`firewall_reboot`, acted alone because the maintainer was away), wrong component: the bench could not answer `dmesg -a` | 166 s | 11.6 |
| wan-driver-hang | 2 | **correct**. Right component and fix; it rebooted once and did not touch the modem | 178 s | 12.4 |

What changed after these runs, all on the bench side:

- policy v2, with options in any order, a command menu, and unsafe refusals kept separate from unlisted ones ([ADR 0007](adr/0007-policy-v2.md));
- replay matching for equivalent commands ([ADR 0008](adr/0008-replay-matching.md));
- more recordings;
- scoring that counts `head`/`tail` of an evidence file as reading it.

No run so far has proposed a command refused as unsafe.

## Batch 1 (2026-11-17, in progress)

Three scenarios, five repeats each, at temperatures 0 and 0.7. Intervals are Wilson 95%.

| Scenario | Temperature | Correct | 95% interval |
|---|---|---|---|
| dns-secondary-lan | 0 | 0 / 5 | 0.00 – 0.43 |
| dns-secondary-lan | 0.7 | 4 / 5 | 0.38 – 0.96 |
| wan-stale-lease | 0 | running | |
| wan-stale-lease | 0.7 | queued | |
| wan-driver-hang | 0 | queued | |
| wan-driver-hang | 0.7 | queued | |

**What the DNS result shows so far.** At temperature 0 the model produced the same 13-step
investigation five times and named the same wrong host. The incident has two causes: a
resolver that drops private answers, and a rate limit on the primary DNS server. The model
blamed the rate limit. That reading is defensible but it is not the answer key. Whether
two-cause incidents should accept either cause, or score the root cause and the contributing
condition separately, is an open question. It will be decided before batch 2, and the batch 1
numbers will stay as scored under the current key. At 0.7 the model explores more and usually
finds the root cause.

Energy per correct diagnosis and the refusal review for this batch will be added when it
finishes.
