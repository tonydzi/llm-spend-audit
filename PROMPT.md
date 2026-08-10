# Copy-paste prompt

Paste this into Claude Code, Codex, or any coding agent with shell access. It runs the
audit on the machine it is sitting on and reports back. Nothing here sends data anywhere:
every tool is local, stdlib-only, and makes no network call.

---

Run a spend audit on my own agent setup using the `llm-spend-audit` kit
(https://github.com/tonydzi/llm-spend-audit). Work in this order and do not skip the
calibration, because every later number depends on it.

**0. Get the kit.**
```
git clone https://github.com/tonydzi/llm-spend-audit.git /tmp/llm-spend-audit
cd /tmp/llm-spend-audit && python tests/test_kit.py
```
If the tests fail, stop and tell me what broke before running anything against my data.

**1. Calibrate the counter on my transcripts.**
```
python session_tax.py calibrate --days 30
```
Report the characters-per-token rate, the sample size `n`, and the spread for each script.
If it says the counter is NOT calibrated, say so plainly and treat every later estimate as
a rough guess. Do not quote an uncalibrated number as a measurement.

**2. Price the standing rent.**
```
python session_tax.py session-start --days 14
```
Tell me the median cost of starting a session and whether the per-day line is climbing. A
climbing line means my own improvements are getting more expensive.

**3. Find what I pay for on every single session.**
Find my always-loaded context files: the rules file the harness loads into every session,
any memory index, and the output of my session-start hooks. For each one:
```
python session_tax.py item-cost <file>
```
Rank them by **tokens per month**, not per run. Then tell me which single item has the
worst ratio of monthly cost to how often it actually changes my behaviour. Name it. Do not
give me a list of ten things to consider.

**4. Find who burned yesterday.**
```
python spend_audit.py
```
Give me the top three findings only, in plain language, and for each one say what to do
about it. Pay particular attention to:
- any background service opening many sessions a day, which is almost always a design
  mistake rather than a tuning problem;
- sessions with huge re-read context, which cost prefix times turns;
- sessions that never called a single tool while carrying every tool description.

If the thresholds fire on more than a third of my sessions, say so and tell me to
re-measure them from my own distribution. Do not hand me an alarm I will mute.

**5. Check whether my paid allowances are going undrawn.**
Copy `rails.example.json` to `rails.json` and fill it in from what I actually pay for. Ask
me for my plans and their monthly cost. Then:
```
python rail_utilization.py measure
python rail_utilization.py report
python rail_utilization.py audit
```
Remember: **"not measured" is not zero**. If a rail has no reading, tell me to go read the
panel; do not print it as unused. If a bucket is genuinely underdrawn, say the amount of
money that is buying nothing.

**6. Give me one page back.** Structure it exactly like this:

- **The rent**: what a session costs me before it does anything, and the trend.
- **The single worst permanent addition**: name it and give the monthly token cost.
- **The worst background consumer**: name it and say what to change.
- **Undrawn allowances**: which paid rail has the most room, and what work should move
  there.
- **What I should NOT do**: anything the numbers do not support. If the audit found
  nothing worth acting on, say that instead of manufacturing a recommendation.

For every figure you give me, say where it came from: measured from my logs, declared by a
human, or reported by a vendor. Never average numbers of different origin. If you had to
estimate something, label it an estimate.
