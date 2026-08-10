# -*- coding: utf-8 -*-
"""Tests for llm-spend-audit. Stdlib unittest, no fixtures on disk, no network.

    python -m unittest discover -s tests -v
    python tests/test_kit.py

Every test here exists because the thing it checks was wrong at some point. The ones
that matter most are the three at the top of TestCalibration: they are the difference
between a token budget you can quote and a number that is physically impossible.

Written so a test CAN go red: several cases assert the naive implementation's answer is
NOT what the tool returns. If you "simplify" the grouping away, those go red immediately.
"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import transcripts as T          # noqa: E402
import session_tax               # noqa: E402
import spend_audit               # noqa: E402
import rail_utilization          # noqa: E402

CYR = "тест длинного русского ответа про расход контекста и токены "
LAT = "a plain english sentence about context and token spend rates "
CYR_TEXT, LAT_TEXT = CYR * 8, LAT * 8
# Token counts derived from the text, never hardcoded: a fixture whose "expected" number
# is typed by hand drifts the moment someone edits the sentence, and then the test is
# asserting the typo rather than the behaviour.
CYR_TOKENS = len(CYR_TEXT) // 2          # exactly 2.0 chars/token
LAT_TOKENS = len(LAT_TEXT) // 4          # exactly 4.0 chars/token


def assistant(mid, text, tokens, kinds=("text",), ts="2026-08-06T12:00:00.000Z"):
    """One transcript record. `kinds` lets a test add thinking / tool_use blocks."""
    content = []
    for kind in kinds:
        if kind == "text":
            content.append({"type": "text", "text": text})
        else:
            content.append({"type": kind, "name": "mcp__demo__do" if kind == "tool_use" else ""})
    return {"type": "assistant", "timestamp": ts,
            "message": {"id": mid, "role": "assistant", "content": content,
                        "usage": {"output_tokens": tokens, "input_tokens": 1000,
                                  "cache_creation_input_tokens": 2000,
                                  "cache_read_input_tokens": 3000}}}


def user(text, ts="2026-08-06T12:00:00.000Z"):
    return {"type": "user", "timestamp": ts,
            "message": {"role": "user", "content": [{"type": "text", "text": text}]}}


class Fixture(unittest.TestCase):
    """A temp transcript root, pointed at by CLAUDE_PROJECTS_DIR."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="lsa-test-")
        self.project = os.path.join(self.root, "project")
        os.makedirs(self.project)
        self._env = os.environ.get("CLAUDE_PROJECTS_DIR")
        os.environ["CLAUDE_PROJECTS_DIR"] = self.root
        self.state = tempfile.mkdtemp(prefix="lsa-state-")
        self._sdir, self._cal = session_tax.STATE_DIR, session_tax.CAL_PATH
        session_tax.STATE_DIR = self.state
        session_tax.CAL_PATH = os.path.join(self.state, "calibration.json")
        self._rs, self._rd = rail_utilization.STATE, rail_utilization.STATE_DIR
        rail_utilization.STATE_DIR = self.state
        rail_utilization.STATE = os.path.join(self.state, "rails_state.json")

    def tearDown(self):
        session_tax.STATE_DIR, session_tax.CAL_PATH = self._sdir, self._cal
        rail_utilization.STATE, rail_utilization.STATE_DIR = self._rs, self._rd
        if self._env is None:
            os.environ.pop("CLAUDE_PROJECTS_DIR", None)
        else:
            os.environ["CLAUDE_PROJECTS_DIR"] = self._env
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.state, ignore_errors=True)

    def write(self, name, records, raw_lines=(), age_days=0):
        path = os.path.join(self.project, name)
        with open(path, "w", encoding="utf-8") as fh:
            for rec in records:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            for line in raw_lines:
                fh.write(line + "\n")
        if age_days:
            old = time.time() - age_days * 86400
            os.utime(path, (old, old))
        return path


# --------------------------------------------------------------------------------------
class TestCalibration(Fixture):
    """The headline trap: output_tokens is per MESSAGE and repeated in every record."""

    def _clean_corpus(self):
        # 10 pure-text Cyrillic replies at exactly 2.0 chars/token
        return [assistant("m%d" % i, CYR_TEXT, CYR_TOKENS) for i in range(10)]

    def test_measures_the_true_rate_on_clean_data(self):
        self.write("clean.jsonl", self._clean_corpus())
        cal = session_tax.calibrate(days=30)
        self.assertEqual(cal["n_cyrillic"], 10)
        self.assertAlmostEqual(cal["cyrillic_chars_per_token"], 2.0, places=2)
        self.assertIn("measured", cal["source"])

    def test_thinking_and_tool_messages_are_excluded(self):
        """A reply with thinking blocks has a token count that is not about its text.

        Including them gave us 1.2 chars/token for Cyrillic, which cannot happen, and we
        nearly shipped a context budget built on it. The poison below is deliberately
        extreme: every poisoned reply claims 4000 tokens for the same visible text.
        """
        records = self._clean_corpus()
        records += [assistant("p%d" % i, CYR_TEXT, 4000, kinds=("thinking", "text"))
                    for i in range(20)]
        records += [assistant("q%d" % i, CYR_TEXT, 4000, kinds=("text", "tool_use"))
                    for i in range(20)]
        self.write("poisoned.jsonl", records)
        cal = session_tax.calibrate(days=30)
        self.assertEqual(cal["n_cyrillic"], 10, "poisoned messages leaked into the sample")
        self.assertAlmostEqual(cal["cyrillic_chars_per_token"], 2.0, places=2)
        # And the naive answer really is nonsense, so this test can go red for a reason.
        naive = (len(CYR_TEXT) * 50) / (CYR_TOKENS * 10 + 4000 * 40)
        self.assertLess(naive, 0.5)

    def test_repeated_usage_records_are_not_summed(self):
        """The same message appears in several records, each carrying the same usage."""
        rec = assistant("dup", CYR_TEXT, CYR_TOKENS)
        self.write("dup.jsonl", [rec, dict(rec), dict(rec)] + self._clean_corpus()[:9])
        msgs = T.assistant_messages(os.path.join(self.project, "dup.jsonl"))
        self.assertEqual(msgs["dup"]["tokens"], CYR_TOKENS,
                         "usage was summed across duplicate records")
        cal = session_tax.calibrate(days=30)
        self.assertAlmostEqual(cal["cyrillic_chars_per_token"], 2.0, places=2)

    def test_latin_rate_is_measured_separately(self):
        # A different rate from the Cyrillic one, so a single blended rate cannot pass
        self.write("lat.jsonl", [assistant("l%d" % i, LAT_TEXT, LAT_TOKENS) for i in range(10)])
        cal = session_tax.calibrate(days=30)
        self.assertEqual(cal["n_other"], 10)
        self.assertAlmostEqual(cal["other_chars_per_token"], 4.0, places=2)

    def test_too_few_samples_stays_honest(self):
        self.write("thin.jsonl", [assistant("m%d" % i, CYR_TEXT, CYR_TOKENS) for i in range(3)])
        cal = session_tax.calibrate(days=30)
        self.assertNotIn("measured", cal["source"])
        self.assertFalse(session_tax.is_measured(cal))


class TestBrokenInput(Fixture):
    """Empty logs, broken JSON, sessions with no text at all. None may crash or lie."""

    def test_empty_root(self):
        self.assertEqual(T.transcript_files(), [])
        self.assertEqual(session_tax.session_starts(days=14), [])
        cal = session_tax.calibrate(days=30)
        self.assertFalse(session_tax.is_measured(cal))
        self.assertEqual(session_tax.main(["session-start"]), 0)
        self.assertEqual(spend_audit.main(["--date", "2026-08-06"]), 0)

    def test_missing_root_directory(self):
        os.environ["CLAUDE_PROJECTS_DIR"] = os.path.join(self.root, "does-not-exist")
        self.assertEqual(T.transcript_files(), [])
        self.assertEqual(session_tax.main(["daily"]), 0)

    def test_truncated_and_broken_json(self):
        good = assistant("g1", CYR_TEXT, CYR_TOKENS)
        path = self.write("broken.jsonl", [good],
                          raw_lines=['{"type": "assistant", "message": {',
                                     "not json at all",
                                     "",
                                     '{"type":"assistant","message":{"id":"g2",'])
        recs = list(T.records(path))
        self.assertEqual(len(recs), 1, "a broken line took a good record with it")

    def test_string_numbers_in_usage_do_not_drop_the_session(self):
        """One record with usage={"input_tokens": "x"} once threw TypeError, an outer
        except swallowed it, and the WHOLE session vanished from the report."""
        poisoned = assistant("p1", LAT * 4, 60)
        poisoned["message"]["usage"] = {"output_tokens": "many", "input_tokens": None,
                                        "cache_read_input_tokens": True,
                                        "cache_creation_input_tokens": 500}
        self.write("mixed.jsonl", [assistant("g1", LAT * 4, 60), poisoned])
        rows = spend_audit.scan_sessions(hours=48)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["turns"], 2, "the poisoned turn was dropped silently")
        self.assertEqual(T.num({"input_tokens": "x"}, "input_tokens"), 0)
        self.assertEqual(T.num({"a": True}, "a"), 0, "True must not count as 1 token")

    def test_session_with_no_text_messages(self):
        rec = assistant("t1", "", 90, kinds=("tool_use",))
        self.write("tools-only.jsonl", [rec, rec])
        cal = session_tax.calibrate(days=30)
        self.assertFalse(session_tax.is_measured(cal))
        rows = spend_audit.scan_sessions(hours=48)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["servers"], ["demo"])

    def test_bad_date_is_usage_error_not_crash(self):
        self.assertEqual(spend_audit.main(["--date", "yesterday"]), 2)

    def test_unknown_config_key_is_rejected(self):
        path = os.path.join(self.state, "cfg.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"huge_day_token": 5}, fh)     # typo: missing the s
        self.assertEqual(spend_audit.main(["--config", path]), 2)


class TestSpendAudit(Fixture):
    def _day(self):
        import datetime
        return datetime.date.today().isoformat()

    def test_disabled_service_still_burning_is_critical(self):
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        self.write("ghost.jsonl", [user('<scheduled-task name="retired-watchdog">go</scheduled-task>',
                                        ts=now),
                                   assistant("a1", LAT * 4, 5000, ts=now)])
        cfg = dict(spend_audit.DEFAULTS)
        cfg["should_not_run"] = ["retired-watchdog"]
        cfg["disabled_min_tokens"] = 100
        report, severity, flags, _, total = spend_audit.build_report(self._day(), 24, cfg)
        self.assertGreater(total, 0)
        self.assertEqual(severity, "RED")
        self.assertTrue(any("retired-watchdog" in f for f in flags))

    def test_live_session_alone_is_green(self):
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        self.write("live.jsonl", [user("hello", ts=now), assistant("a1", LAT * 4, 100, ts=now),
                                  user("more", ts=now), assistant("a2", LAT * 4, 100, ts=now)])
        _, severity, flags, by_source, _ = spend_audit.build_report(self._day(), 24,
                                                                    dict(spend_audit.DEFAULTS))
        self.assertEqual(severity, "GREEN", flags)
        self.assertIn("LIVE (interactive)", by_source)

    def test_carried_context_ranks_by_reread_not_output(self):
        """A cheap-looking session that re-reads a huge prefix every turn is the expensive one."""
        big = assistant("b", LAT, 10)
        big["message"]["usage"] = {"output_tokens": 10, "input_tokens": 0,
                                   "cache_creation_input_tokens": 0,
                                   "cache_read_input_tokens": 90000}
        loud = assistant("l", LAT, 9000)
        loud["message"]["usage"] = {"output_tokens": 9000, "input_tokens": 100,
                                    "cache_creation_input_tokens": 0,
                                    "cache_read_input_tokens": 100}
        self.write("quiet.jsonl", [big] * 20)
        self.write("loud.jsonl", [loud] * 2)
        rows = spend_audit.scan_sessions(hours=48)
        rows.sort(key=lambda r: r["cache_read"], reverse=True)
        self.assertEqual(rows[0]["turns"], 20)
        self.assertEqual(rows[0]["cache_read"], 1800000)

    def test_utc_window_covers_a_full_local_day(self):
        lo, hi = spend_audit.utc_window("2026-08-06")
        self.assertLess(lo, hi)
        self.assertEqual(len(lo), 19)


class TestRailUtilization(Fixture):
    def _config(self, logs):
        path = os.path.join(self.state, "rails.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"rails": [{"vendor": "coder", "plan": "p", "usd_month": 20,
                                  "engines": ["coder", "coder-cli"]},
                                 {"vendor": "researcher", "plan": "r", "usd_month": 20,
                                  "engines": ["researcher"]}],
                       "usage_logs": logs}, fh)
        return path

    def _log(self, name, rows):
        path = os.path.join(self.state, name)
        with open(path, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")
        return path

    def test_two_logs_are_merged_and_deduped(self):
        now = time.time()
        a = self._log("a.jsonl", [{"ts": now, "engine": "coder", "task": "t1"},
                                  {"ts": now + 1, "engine": "coder", "task": "t2"}])
        b = self._log("b.jsonl", [{"ts": now, "engine": "coder", "task": "t1"},
                                  {"ts": now + 2, "engine": "coder-cli", "task": "t3"}])
        cfg = rail_utilization.load_config(self._config([a, b]))
        counts = rail_utilization.count_calls(cfg, 7)
        self.assertEqual(counts["coder"], 2, "the duplicate call was counted twice")
        self.assertEqual(counts["coder-cli"], 1)

    def test_engine_aliases_roll_up_to_the_vendor(self):
        """Counting by vendor name while the log writes the CLI name printed a live rail
        as zero calls, which argues for cancelling a plan you are using every day."""
        now = time.time()
        log = self._log("c.jsonl", [{"ts": now, "engine": "coder-cli", "task": "t%d" % i}
                                    for i in range(5)])
        cfg = rail_utilization.load_config(self._config([log]))
        rail_utilization.cmd_measure(type("A", (), {"config": cfg["_path"]})())
        state = rail_utilization._load_state()
        rows, _ = rail_utilization.build_rows(cfg, state)
        coder = [r for r in rows if r["vendor"] == "coder"][0]
        self.assertEqual(coder["calls7"], 5)

    def test_skipped_and_stale_records_are_ignored(self):
        now = time.time()
        log = self._log("d.jsonl", [{"ts": now, "engine": "coder", "skipped": True},
                                    {"ts": now - 40 * 86400, "engine": "coder"},
                                    {"ts": "not a number", "engine": "coder"},
                                    {"ts": now, "engine": "coder", "task": "real"}])
        cfg = rail_utilization.load_config(self._config([log]))
        self.assertEqual(rail_utilization.count_calls(cfg, 7), {"coder": 1})

    def test_not_measured_is_not_zero(self):
        cfg = rail_utilization.load_config(self._config([]))
        rows, _ = rail_utilization.build_rows(cfg, rail_utilization._load_state())
        self.assertTrue(all(r["pct"] is None for r in rows))
        self.assertTrue(all(r["mark"] == "not measured" for r in rows))
        self.assertTrue(all(rail_utilization.is_blind(r) for r in rows))

    def test_audit_escalates_a_paid_but_unmeasured_rail(self):
        cfg_path = self._config([])
        self.assertEqual(rail_utilization.cmd_audit(type("A", (), {"config": cfg_path})()), 1)

    def test_early_month_readings_do_not_count_as_underuse(self):
        """A bucket fills over the month. A low reading on the 5th proves nothing, and
        treating it as evidence would cancel a plan on noise."""
        state = {"readings": [{"vendor": "coder", "pct": 5, "date": "2026-07-05",
                               "origin": "declared"},
                              {"vendor": "coder", "pct": 5, "date": "2026-06-05",
                               "origin": "declared"}],
                 "measurements": []}
        self.assertEqual(rail_utilization.underuse_streak(state, "coder"), 0)

    def test_late_month_readings_do_count(self):
        state = {"readings": [{"vendor": "coder", "pct": 10, "date": "2026-07-28",
                               "origin": "declared"},
                              {"vendor": "coder", "pct": 12, "date": "2026-06-25",
                               "origin": "declared"}],
                 "measurements": []}
        self.assertEqual(rail_utilization.underuse_streak(state, "coder"), 2)

    def test_vendor_figure_wins_over_a_human_one_on_the_same_day(self):
        state = {"readings": [{"vendor": "coder", "pct": 30, "date": "2026-08-05",
                               "origin": "declared"},
                              {"vendor": "coder", "pct": 3.0, "date": "2026-08-05",
                               "origin": "vendor"}],
                 "measurements": []}
        self.assertEqual(rail_utilization.latest_reading(state, "coder")["pct"], 3.0)

    def test_a_tenfold_gap_is_reported_not_averaged(self):
        import datetime
        today = datetime.date.today().isoformat()
        state = {"readings": [{"vendor": "coder", "pct": 30, "date": today, "origin": "declared"},
                              {"vendor": "coder", "pct": 3.0, "date": today, "origin": "vendor"}],
                 "measurements": []}
        clash = rail_utilization.origin_clash(state, "coder")
        self.assertIsNotNone(clash)
        self.assertIn("30", clash)
        self.assertIn("3.0", clash)

    def test_fullest_bucket_wins(self):
        """Taking the first bucket showed 3% while the binding weekly bucket was at 90%,
        i.e. it reported underuse at the exact moment we were hitting the ceiling."""
        path = os.path.join(self.state, "rollout-1.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"timestamp": "2026-08-05T10:00:00Z",
                                 "payload": {"rate_limits": {
                                     "primary": {"used_percent": 3.0, "window_minutes": 300},
                                     "secondary": {"used_percent": 90.0, "window_minutes": 10080},
                                     "plan_type": "demo", "limit_id": "x"}}}) + "\n")
        got = rail_utilization.read_vendor_rate_limit(os.path.join(self.state, "rollout-*.jsonl"))
        self.assertEqual(got["used_percent"], 90.0)
        self.assertEqual(got["slot"], "secondary")

    def test_missing_rate_limits_is_not_an_empty_bucket(self):
        self.assertIsNone(rail_utilization.read_vendor_rate_limit(
            os.path.join(self.state, "nothing-*.jsonl")))

    def test_verdicts(self):
        self.assertEqual(rail_utilization.verdict(None, None)[0], "not measured")
        self.assertEqual(rail_utilization.verdict(10, 1)[0], "UNDERUSED")
        self.assertEqual(rail_utilization.verdict(60, 1)[0], "burning it")
        self.assertEqual(rail_utilization.verdict(99, 1)[0], "AT THE CEILING")
        self.assertEqual(rail_utilization.verdict(60, 90)[0], "stale")


class TestItemCost(Fixture):
    def test_item_cost_reports_monthly_rent(self):
        self.write("s.jsonl", [assistant("m%d" % i, CYR_TEXT, CYR_TOKENS) for i in range(10)])
        session_tax.calibrate(days=30)
        hook = os.path.join(self.state, "hook.txt")
        with open(hook, "w", encoding="utf-8") as fh:
            fh.write(CYR * 20)                       # a chatty startup hook, 1160 chars
        args = type("A", (), {"path": hook, "sessions_per_day": 10.0, "json": True,
                              "root": None})()
        session_tax.cmd_item_cost(args)              # must not raise
        # 20 repetitions: the Cyrillic letters at 2.0 chars/token, the spaces and
        # punctuation at the Latin rate. Computed, not guessed, from the calibration above.
        cal = session_tax.load_calibration()
        cyr = sum(1 for ch in CYR * 20 if session_tax.CYRILLIC.match(ch))
        expected = cyr / cal["cyrillic_chars_per_token"]             + (len(CYR * 20) - cyr) / cal["other_chars_per_token"]
        tokens = session_tax.estimate_tokens(CYR * 20)
        self.assertAlmostEqual(tokens, expected, delta=1)
        self.assertGreater(tokens, 400)

    def test_missing_file_is_usage_error(self):
        self.assertEqual(session_tax.main(["item-cost", os.path.join(self.state, "nope.txt")]), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
