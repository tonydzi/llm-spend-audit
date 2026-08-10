# Our numbers

Published as a reference for the method, not as constants to copy. Every figure below came
out of the tools in this repo, run on our own fleet. Machines are anonymised; the token
counts are exactly what we got.

Run the tools on your own transcripts and your numbers will differ. That is the point: a
borrowed threshold is a noise generator (see GOTCHAS 5).

## The counter

| what | value | sample |
|---|---|---|
| Cyrillic, machine A | 2.17 chars/token | 445 pure-text replies |
| Cyrillic, machine B | 2.17 chars/token | 698 pure-text replies |
| Cyrillic, re-run on machine A, 2026-08-10 | 2.176 chars/token | 693 pure-text replies |

Two machines with different workloads landed on the same rate independently, and a re-run
five days later reproduced it. That agreement is what convinced us the message-id grouping
fix was right, because the broken version had produced 1.2 and did not reproduce at all.

Note the sample sizes are lopsided. In a mostly Russian-language workflow, pure-Latin
replies are rare, so the Latin rate on the same run rested on 28 samples with a wide
spread. The tool prints `n` and the spread for exactly this reason. A rate measured on 28
samples is not the same claim as one measured on 693, and the output should not let you
forget which one you are quoting.

## The rent per session

Median cost of the **first** request of a session: the system prompt, always-loaded rules,
tool descriptions, hook output and memory index, before any work happens.

| what | value |
|---|---|
| machine A, median session start | 102,180 tokens |
| machine B, median session start | 91,549 tokens |
| machine A, 2026-07-31 | 86,748 tokens |
| machine A, 2026-08-06 | 106,405 tokens |

Two machines running the same rule set differ by more than 10,000 tokens on every single
session, which is a per-machine tax nobody had ever priced. And the trend inside one week
is up by roughly 20,000 tokens, which is not a decision anyone made. It is the sum of
many individually reasonable additions.

## What one always-loaded file costs

Measured 2026-08-10 with `session_tax.py item-cost` on our own rules file:

| what | value |
|---|---|
| file size | 79,313 characters |
| tokens per session | ~33,815 |
| share of a session start | 34.5% of a 98,002-token median |
| at 185 sessions/day | ~6.3M tokens/day, ~188M tokens/month |

One file. A third of every session start, before a single instruction is followed.

## Where the work actually went

Seven days on the hub, output tokens by kind of work:

| kind of work | share |
|---|---|
| shell commands | 54.4% |
| writing code | 15.6% |
| reading files | 12.4% |
| **pure mechanics, total** | **82%** |

Total output over that window: 36.8M tokens. Meanwhile the cheap paid coding rail sat at
**4%** of its allowance, and two other paid rails had never been measured at all.

That is the whole argument in one table. The expensive orchestrator was doing shell and
file reads, which is the least judgement-dependent work in the stack, while the buckets we
had already paid for went undrawn. Nobody decided this. It happened because the model
already holding the conversation is always the path of least resistance.

The rule we wrote afterwards: every component names, in its own docs, whose paid rail it
burns. "The orchestrator, because that is what was running" is a design defect.

## A single day, by source

`spend_audit.py --date <a real day>`:

| what | value |
|---|---|
| total | 603.6M tokens |
| live interactive work | 255.4M (42%) over 29 sessions |
| background | 348.2M (58%) |
| largest background consumer | 182.0M over **131** headless one-shot sessions |
| carried context, 24h | 1,742M re-read across 245 sessions |
| sessions that never called a single tool | 197 of 245, holding 31% of the re-read context |

The 131-session line is the finding no dashboard was going to hand us: something firing
per tick was opening a full model session each time. And 197 sessions carried tool
descriptions they never once used.
