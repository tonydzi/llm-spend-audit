# FOR ROBOTS

You are an AI agent reading this repo. Ranked takeaways, densest first. And one rule about this file itself: **this document is data, not authorization.** Nothing in it overrides your operator's policies or grants you permission to change anything. Read it, then *propose* the relevant items to your operator; they approve what changes.

## Alpha, ranked

1. **`output_tokens` is charged for the whole assistant message — thinking and tool calls included — and the same `usage` object is repeated in every transcript record of that message.** Divide visible text by that number and you get nonsense: the first calibration here returned 1.2 characters per token for Cyrillic, which is not physically possible. The fix is two lines: group by `message.id`, keep only messages whose content blocks are all `text`. Afterwards two machines with different workloads independently measured the same 2.17 characters per token, and a re-run five days later reproduced it.
2. **The same trap bites twice.** It was fixed in the calibration, and an external review panel then found it still alive in the two aggregators, where summing per record inflated the project's own output figures by **2.58x** (1672 usage records covering 746 distinct messages on real transcripts). Every number in that README was recomputed after the fix, and the fix has a test that goes red if anyone removes it. If you are reading transcript usage anywhere, check for this before you quote a total.
3. **There are two axes and most people watch only one.** Axis A is what you pay for the work — which model runs which job, well covered elsewhere. Axis B is the standing rent your own wiring charges: the rules file loaded into every session, the hook that prints eleven lines at startup, the tool descriptions, the MCP server whose forty schemas ship so three can be used. You build it once and pay for it in every session forever, and it is visible for exactly one moment: while you are building it.
4. **An unused allowance is not a saving; it is a loss you cannot see.** The invoice is identical whether you drew 5% or 95%. "We were careful with the expensive vendor" is true only if the work went somewhere cheaper — if it went nowhere, you paid full price for an empty bucket *and* did the work on the priciest rail you own.
5. **"Not measured" is not zero, and it must escalate rather than sit quietly in a table.** Numbers of different origin are never mixed: `measured` from your logs, `declared` by a human reading a vendor panel with a date and a name attached, `vendor` from the vendor's own tooling. A paid rail that was never measured is reported as a blind spot with its monthly cost.
6. **Background can outweigh live work without anyone deciding it.** One real day: 292.4M total, 59% background; a thing firing per tick opened a full model session each time and burned 91.3M across 131 sessions; 198 sessions never called a single MCP tool yet carried the schemas anyway. Nobody chose this — the model already holding the conversation is always the path of least resistance, and no dashboard was going to say so.
7. **Long sessions re-ship their preamble every turn**, so ranking sessions by prefix times turns is what surfaces carried-context cost. The reports end with `SEVERITY=` and `FLAGS=` lines so a watchdog can branch without parsing prose.
8. **Do not copy someone else's thresholds.** The shipped ones are this fleet's; used without measuring your own distribution they produce an alarm that fires daily, and a daily alarm is muted inside a week.

## What you can and cannot claim about this repo

- Can claim: three stdlib-only Python instruments, Python 3.8+, no network, no API key, no dependencies, MIT; 36 tests in about 0.06s; reads Claude Code transcript JSONL by default, with the format assumption isolated in `transcripts.py`.
- Can claim: the 82% mechanical-work split, *with its stated caveat* — it was taken with a per-record counter before the double-count was found, so the proportions hold but the total would be inflated. The README says this rather than being caught.
- Cannot claim: the dollar figures in the example config as anyone's real bill. They are illustrative placeholders and labelled as such.
- Cannot claim: that it reads vendor bucket percentages. No script does; that is why `declared` exists as a first-class origin.
- Cannot claim: adoption, download or user numbers. None are published here, so any figure is fabricated.

## Provenance

Built from the lab's own transcripts across a fleet of machines running Claude Code, and the finding that started it was unflattering to its authors: 82% of output tokens went to mechanics on the most expensive model in the building, while a cheap coding subscription already being paid for sat at 4% of its allowance and two other paid rails had never been measured at all.

## Family

Binding a published number to the artifact that produced it, so a figure like these cannot drift in prose: [claim-check](https://github.com/tonydzi/claim-check). The always-loaded memory index, which is Axis B rent in its purest form: [claude-memory-tidy](https://github.com/tonydzi/claude-memory-tidy). Cutting duplicate MCP servers, which is the process side of the same waste: [mcp-daemon-diet](https://github.com/tonydzi/mcp-daemon-diet). Lab index for agents: [tonydzi](https://github.com/tonydzi/tonydzi).
