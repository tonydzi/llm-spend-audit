# llm-spend-audit

**Your agent setup charges rent. Every session, before it does any work.** Three
deterministic instruments that show you how much, who is burning it, and whether the
subscriptions you already pay for are going undrawn.

No LLM call. No network. No API key. No dependencies. Python 3.8+, stdlib only. It reads
the transcripts your harness already wrote, which contain the real usage numbers the
vendor recorded, and does arithmetic on them.

Built and used daily at [Palo Alto AI Research Lab](https://github.com/tonydzi) across a
fleet of machines running Claude Code.

---

## The number that started this

Seven days of our own output tokens, broken down by kind of work:

| kind of work | share of output |
|---|---|
| shell commands | 54.4% |
| writing code | 15.6% |
| reading files | 12.4% |
| **pure mechanics** | **82%** |

82% of it mechanical, all of it on the most expensive model in the building. Meanwhile the
cheap coding subscription we were already paying for sat at **4%** of its allowance, and
two other paid rails had never been measured at all.

We quote the shares and not the absolute total on purpose: that split was taken with a
per-record counter, before we found the double-count described further down, so the
proportions hold but the total would be inflated. Saying so is cheaper than being caught.

Nobody decided this. The model already holding the conversation is always the path of
least resistance, and no dashboard anywhere was going to say so out loud.

## Two axes, and most people only watch one

**Axis A: what you pay for the work.** Which model runs which job. Well covered elsewhere.

**Axis B: the standing rent your own wiring charges.** The rules file loaded into every
session. The hook that prints eleven lines at startup. The tool descriptions. The MCP
server whose forty tools are all shipped into context so three of them can be used. You
build it once and pay for it in every session forever, and it is visible for exactly one
moment: while you are building it.

This kit is Axis B, plus the utilisation question. Full reasoning in [`docs/METHOD.md`](docs/METHOD.md).

## What you get

### `session_tax.py` - what a session costs before it does anything

```bash
python session_tax.py calibrate          # chars per token, measured on YOUR data
python session_tax.py session-start      # the preamble bill, per day, with the trend
python session_tax.py item-cost ~/.claude/CLAUDE.md
python session_tax.py daily              # output vs cache write vs cache read
```

Real output from our hub, on our own always-loaded rules file:

```
RENT FOR A PERMANENT CONTEXT ADDITION: CLAUDE.md
  characters      : 79 313
  tokens per run  : ~33 805
  sessions/day    : 185 (measured over 14 days)
  per month       : ~187 617 750 tokens
  share of a session start: 34.494%  (median start 98 002)
```

One file, a third of every session start. The number that changes minds is not tokens per
run, it is tokens per month, because that is what you are signing up for.

### `spend_audit.py` - who burned it, and what should not have

```bash
python spend_audit.py                    # the day that owns tonight
python spend_audit.py --date 2026-08-09 --config my.json
```

Two layers. **By source**, splitting the day between live work and each background service
or scheduled task. **Carried context**, ranking sessions by prefix times turns, because a
long session re-ships its whole preamble on every single turn.

Findings from one real day of ours:

```
total 292.4M | live 118.7M (41%) | background 173.7M (59%)
CARRIED CONTEXT over 24h: 247 sessions, 863.4M re-read
  sessions that never called a single MCP tool: 198 (301.4M, 35% of it)

FINDINGS:
  - CRITICAL: 'headless one-shot' started 131 sessions in one day and burned 91.3M
  - 'task:voice-dispatcher' fired 5 times in one day (14.0M)
  - background is 59% of all tokens, more than your live work
SEVERITY=RED
```

Something firing per tick was opening a full model session each time. 198 sessions carried
tool descriptions they never once used. Ends with `SEVERITY=` and `FLAGS=` lines so a
watchdog can branch without parsing prose.

### `rail_utilization.py` - are you drawing what you already bought?

```bash
cp rails.example.json rails.json         # then edit it
python rail_utilization.py measure       # count real calls from your own logs
python rail_utilization.py declare researcher 62
python rail_utilization.py report
python rail_utilization.py audit         # exit 1 on underuse or a blind spot
```

An unused allowance is not a saving, it is a loss you cannot see: the invoice is identical
whether you drew 5% or 95%. So "we were careful with the expensive vendor" is only true if
the work went somewhere cheaper. If it went nowhere, you paid full price for an empty
bucket **and** did the work on the priciest rail you own.

```
| rail       | $/mo | calls 7d | bucket drawn              | verdict      |
|------------|------|----------|---------------------------|--------------|
| coder      | 20   | 119      | 1.0% (2026-08-10, vendor) | UNDERUSED    |
| reviewer   | 300  | 77       | not measured              | not measured |

BLIND SPOT: reviewer ($300/mo) is paid for and never measured
unmeasured spend: $320/month
```

Numbers of different origin are never mixed: `measured` from your logs, `declared` by a
human reading a panel, `vendor` reported by the vendor's own tooling. **"Not measured" is
not zero**, and it escalates as a blind spot rather than sitting quietly in a table.

## The one line worth taking even if you use nothing else

`output_tokens` is charged for the **whole assistant message**, thinking and tool calls
included, and the same `usage` object is **repeated in every transcript record of that
message**. Divide visible text by that number and you get nonsense: our first calibration
returned 1.2 characters per token for Cyrillic, which is not physically possible.

The fix is two lines. Group by `message.id`, keep only messages whose content blocks are
all `text`. After it, two machines with different workloads independently measured the same
2.17 characters per token, and a re-run five days later reproduced it.

And it bites twice. We fixed it in the calibration, then an external review panel found
the same trap still alive in the two aggregators, where summing per record inflated our own
output figures by **2.58x** (1672 usage records covered 746 distinct messages on real
transcripts). Every number in this README was recomputed after that fix, and the fix has a
test that goes red if anyone takes it out.

That is in [`docs/GOTCHAS.md`](docs/GOTCHAS.md) along with ten more traps, including the
one where `age or 999` silently excluded today's readings, which is the only day a
same-day conflict can live in.

## Install

```bash
git clone https://github.com/tonydzi/llm-spend-audit.git
cd llm-spend-audit
python session_tax.py calibrate          # nothing else to install
python tests/test_kit.py                 # 36 tests, ~0.06s, no network
```

The tools read `~/.claude/projects/**/*.jsonl` by default. Point them elsewhere with
`--root` or `CLAUDE_PROJECTS_DIR`. Not on Claude Code? The format assumption is small and
local, in `transcripts.py`.

## The lazy path

Paste [`PROMPT.md`](PROMPT.md) into Claude Code or Codex and it will run the audit on your
setup and hand you the three findings that matter.

## Honest limits

- Reads **Claude Code transcript JSONL**. Other harnesses need `transcripts.py` adjusted.
- Bucket percentages mostly live in vendor web panels. No script reads those, which is why
  `declared` exists as a first-class origin with a date and a name attached.
- The thresholds in `spend_audit.example.json` are **ours**. Copying them without measuring
  your own distribution gives you an alarm that fires daily, and a daily alarm is one you
  mute inside a week.
- The dollar figures in `rails.example.json` are illustrative placeholders, not our bill.

## Docs

- [`docs/METHOD.md`](docs/METHOD.md) - the two axes, origin tags, measuring the ruler
- [`docs/GOTCHAS.md`](docs/GOTCHAS.md) - eleven traps, each one paid for
- [`docs/MEASUREMENTS.md`](docs/MEASUREMENTS.md) - all our numbers, as a reference for the method

<!--kits-series:start-->

## 🧰 Connector & Ops Kits

Six kits, all published 2026-08-10, each lifted out of the same live fleet after it
survived production. They are independent: take one, ignore the rest.

| kit | what it solves |
|---|---|
| [`telegram-mcp-kit`](https://github.com/tonydzi/telegram-mcp-kit) | Connect your agent to your own Telegram account in ~15 minutes, with the production patches and every gotcha |
| [`whatsapp-mcp-kit`](https://github.com/tonydzi/whatsapp-mcp-kit) | Link WhatsApp, using a live self-refreshing QR page that makes pairing actually work |
| [`mcp-daemon-diet`](https://github.com/tonydzi/mcp-daemon-diet) | One shared MCP daemon per machine instead of a stdio copy in every session, plus a watchdog that will not blind your live sessions |
| [`agent-approval-gate`](https://github.com/tonydzi/agent-approval-gate) | Your agent needs a human's OK and nobody is at the terminal: ask goes to a messenger, the answer comes back into the run |
| [`oss-publish`](https://github.com/tonydzi/oss-publish) | Open up internal work without leaking it: plausible substitutions, then a fail-closed gate over the whole tree |
| [`llm-spend-audit`](https://github.com/tonydzi/llm-spend-audit) | What your own wiring charges on every session, and which paid subscriptions are going undrawn |

<!--kits-series:end-->

## License

MIT. Take it, fork it, no attribution needed.

<!-- CONTACT-FOOTER -->
## About & contact

Built and used daily at **Palo Alto AI Research Lab** — a fleet of Claude Code machines running
24/7 as a second brain and synthetic cofounder. Every number in `docs/MEASUREMENTS.md` came off
that fleet; none was produced for the README.

Questions, or a harness whose transcripts do not parse? Open an issue, we answer within 24h — a
transcript shape we cannot read is the most useful thing anyone can send us.

- 👤 Author: **Anton Dziatkovskii** — Telegram [@tonydzi](https://t.me/tonydzi) · WhatsApp [+1 341 222 9178](https://wa.me/13412229178) · X [@Tony_Stef_](https://x.com/Tony_Stef_)
- 📣 Channels: [@ClawRus](https://t.me/ClawRus) (RU) · [@ClawEng](https://t.me/ClawEng) (EN)
- 🌐 [palo-alto.ai](https://palo-alto.ai) · [Palo Alto AI Research Lab](https://github.com/tonydzi)
- 🧪 **Engineers: want to test-drive this setup?** Message me — I hand out free starter seeds to engineers who test and report back.

---

<!--ecosystem-map:start-->

## 🧩 One piece of a working system

This repository is one piece lifted out of a live operation: one non-technical founder, an AI
cofounder, and a fleet of machines that reach consensus with each other and wake the human only
for money or the irreversible. It was extracted after it survived production, not written as a
demo — and it runs on its own: nothing here phones home to the rest.

**See how the whole thing fits together → [SYSTEM.md](https://github.com/tonydzi/tonydzi/blob/main/SYSTEM.md)**

<!--ecosystem-map:end-->

## AI contributors

This project is built by a human + AI team, and the git log says so: Claude writes most of
the code, Codex and Grok review it, Gemini feeds the research. Each is credited on a commit
**only if its output changed that commit's content** — no decorative credits. Lab-wide
policy, one source for every repo: [AI-CONTRIBUTORS.md](https://github.com/tonydzi/.github/blob/main/AI-CONTRIBUTORS.md).
