"""Safe harvest resume fixtures: no network, engines, WSL or asset execution."""

import contextlib
import importlib.util
import io
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

SPEC = importlib.util.spec_from_file_location(
    "eco_test", Path(__file__).with_name("eco-test.py"))
ECO = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ECO)

SCHEMA = """
CREATE TABLE assets (
    content_hash TEXT PRIMARY KEY, hash_kind TEXT, source_url TEXT,
    repo TEXT, path TEXT, commit_sha TEXT, bytes INTEGER, category TEXT,
    tier TEXT, fetched INTEGER DEFAULT 0, verdict TEXT
);
"""


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.results = self.root / "test-results.jsonl"
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.executescript(SCHEMA)

    def asset(self, name, tier="t1", verdict=None):
        self.db.execute(
            "INSERT INTO assets (content_hash, hash_kind, source_url, repo, path, tier, verdict) "
            "VALUES (?, 'sha256', 'https://invalid.example/fixture', 'fixture', ?, ?, ?)",
            (name, name, tier, verdict))
        self.db.commit()

    def migrate(self):
        ECO.migrate_verdicts(self.db, self.results)

    def stamp(self, name, build="build-a", verdict="OK", stamp="2026-10-16T00:00:00.000001+00:00"):
        ECO.save_verdict(self.db, dict(content_hash=name, build_id=build,
                                      verdict=verdict, ts=stamp))

    def select(self, build="build-a", limit=400, stale_limit=80, redo=False):
        return ECO.select_assets(self.db, ECO.TIERS, build, limit, stale_limit, redo)

    def test_legacy_db_verdict_is_stale_not_current(self):
        self.asset("old", verdict="OK")
        self.asset("new", tier="t5")
        self.migrate()
        selected, stats = self.select()
        self.assertEqual([asset["content_hash"] for asset in selected], ["new", "old"])
        self.assertEqual((stats["unseen"], stats["stale"], stats["current"]), (1, 1, 0))
        self.assertEqual(self.db.execute("SELECT verdict FROM assets WHERE content_hash='old'").fetchone()[0], "OK")

    def test_legacy_json_last_line_wins_over_terminal_db_verdict(self):
        self.asset("retry", verdict="OK")
        self.results.write_text(
            json.dumps(dict(content_hash="retry", verdict="OK")) + "\n{partial\n" +
            json.dumps(dict(content_hash="retry", verdict="FETCH-FAILED")) + "\n", encoding="utf-8")
        self.migrate()
        selected, stats = self.select()
        self.assertEqual(len(selected), 1)
        self.assertEqual(stats["transient"], 1)
        self.stamp("retry")
        self.migrate()
        self.assertEqual(self.select()[1]["current"], 1)

    def test_same_build_skips_terminal_and_retries_transient(self):
        for name in ("ok", *sorted(ECO.TRANSIENT)):
            self.asset(name)
        self.migrate()
        self.stamp("ok")
        for name in ECO.TRANSIENT:
            self.stamp(name, verdict=name)
        selected, stats = self.select()
        self.assertEqual({asset["content_hash"] for asset in selected}, ECO.TRANSIENT)
        self.assertEqual(stats["current"], 1)
        self.assertEqual(stats["transient"], 3)

    def test_version_upgrade_preserves_history_and_retests(self):
        self.asset("old")
        self.migrate()
        self.stamp("old")
        self.assertEqual(self.select()[0], [])
        self.assertEqual(self.select(build="build-b")[1]["stale"], 1)
        self.stamp("old", build="build-b", verdict="HANG")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM eco_test_verdicts").fetchone()[0], 2)
        self.assertEqual(self.select(build="build-b")[1]["current"], 1)

    def test_unseen_and_transient_precede_bounded_stale(self):
        for index in range(12):
            self.asset(f"old-{index}", verdict="OK")
        self.asset("new", "t5")
        self.asset("retry", "t4", "HARNESS-ERROR")
        self.migrate()
        selected, stats = self.select(limit=10, stale_limit=3)
        self.assertEqual({asset["content_hash"] for asset in selected[:2]}, {"new", "retry"})
        self.assertEqual(stats["selected"], 5)
        self.assertEqual(len(self.select(limit=1)[0]), 1)

    def test_all_tiers_round_robin_even_after_upgrade(self):
        for tier in ECO.TIERS:
            for index in range(4):
                self.asset(f"{tier}-{index}", tier, "OK")
        self.migrate()
        selected, _ = self.select(limit=10, stale_limit=10)
        self.assertEqual([asset["tier"] for asset in selected], list(ECO.TIERS) * 2)

    def test_tiny_limit_rotates_tiers_across_runs(self):
        for tier in ECO.TIERS:
            for index in range(2):
                self.asset(f"{tier}-{index}", tier)
        self.migrate()
        seen = []
        for index in range(5):
            selected, _ = self.select(limit=1)
            seen.append(selected[0]["tier"])
            self.stamp(selected[0]["content_hash"], stamp=f"2026-10-16T00:00:0{index}+00:00")
        self.assertEqual(seen, list(ECO.TIERS))

    def test_retry_does_not_permanently_starve_other_unseen(self):
        self.asset("a-retry")
        self.asset("b-new")
        self.migrate()
        self.stamp("a-retry", verdict="FETCH-FAILED")
        self.assertEqual(self.select(limit=1)[0][0]["content_hash"], "b-new")

    def test_redo_includes_current_ignores_stale_cap_but_respects_limit(self):
        for name in ("a", "b", "c"):
            self.asset(name)
        self.migrate()
        self.stamp("a")
        self.stamp("b")
        selected, _ = self.select(limit=2, stale_limit=0, redo=True)
        self.assertEqual(len(selected), 2)
        self.assertEqual(len(self.select(limit=0, stale_limit=0, redo=True)[0]), 3)

    def test_stale_tier_rotation_with_limit_one(self):
        for tier in ECO.TIERS:
            for index in range(2):
                self.asset(f"{tier}-{index}", tier, "OK")
        self.migrate()
        seen = []
        for index in range(5):
            selected, _ = self.select(build=f"changing-{index}", limit=1)
            seen.append(selected[0]["tier"])
            self.stamp(selected[0]["content_hash"], build=f"changing-{index}",
                       stamp=f"2026-10-16T00:00:0{index}+00:00")
        self.assertEqual(seen, list(ECO.TIERS))

    def test_upsert_tracks_attempts(self):
        self.asset("retry")
        self.migrate()
        self.stamp("retry", verdict="FETCH-FAILED")
        self.stamp("retry", verdict="OK")
        self.assertEqual(self.db.execute("SELECT verdict, attempts FROM eco_test_verdicts").fetchone(), ("OK", 2))

    def test_reported_backlog_shape_selects_unseen_outside_t1(self):
        rows = []
        for index in range(10106):
            tier = "t1" if index < 550 else ECO.TIERS[1 + index % 4]
            rows.append((f"asset-{index:05d}", f"asset-{index:05d}", tier,
                         "OK" if index < 5921 else None))
        self.db.executemany(
            "INSERT INTO assets (content_hash, path, tier, verdict) VALUES (?, ?, ?, ?)", rows)
        self.db.commit()
        self.migrate()
        selected, stats = self.select()
        self.assertEqual((stats["total"], stats["unseen"], stats["stale"], stats["selected"]),
                         (10106, 4185, 5921, 400))
        self.assertEqual({asset["tier"] for asset in selected}, set(ECO.TIERS[1:]))
        self.assertTrue(all(int(asset["content_hash"].split("-")[1]) >= 5921 for asset in selected))

    def test_build_identity_changes_with_binaries_revisions_and_harness(self):
        niu, rubash = self.root / "niu.exe", self.root / "rubash.exe"
        niu.write_bytes(b"inert niu fixture")
        rubash.write_bytes(b"inert rubash fixture")
        def identity(niu_rev="niu-a", rubash_rev="rubash-a", slow=2.0):
            return ECO.build_identity(niu, rubash, niu_rev, rubash_rev, slow,
                                      SimpleNamespace(bin=None))[0]
        original = identity()
        self.assertEqual(original, identity())
        self.assertNotEqual(original, identity(niu_rev="niu-b"))
        self.assertNotEqual(original, identity(rubash_rev="rubash-b"))
        self.assertNotEqual(original, identity(slow=3.0))
        niu.write_bytes(b"changed niu")
        self.assertNotEqual(original, identity())
        niu.write_bytes(b"inert niu fixture")
        rubash.write_bytes(b"changed rubash")
        self.assertNotEqual(original, identity())
        rubash.write_bytes(b"inert rubash fixture")
        harness = self.root / "harness.py"
        harness.write_text("inert harness", encoding="utf-8")
        with mock.patch.object(ECO, "__file__", str(harness)):
            self.assertNotEqual(original, identity())

    def test_oracle_availability_and_version_change_identity(self):
        niu, rubash = self.root / "niu.exe", self.root / "rubash.exe"
        niu.write_bytes(b"inert niu")
        rubash.write_bytes(b"inert rubash")
        def identity(oracle):
            return ECO.build_identity(niu, rubash, "niu-a", "rubash-a", 2.0, oracle)
        missing_id, missing = identity(SimpleNamespace(bin=None))
        available_id, available = identity(SimpleNamespace(bin="/usr/bin/bash", version="GNU bash 5.3"))
        upgraded_id, _ = identity(SimpleNamespace(bin="/usr/bin/bash", version="GNU bash 5.4"))
        self.assertNotEqual(missing_id, available_id)
        self.assertNotEqual(available_id, upgraded_id)
        self.assertEqual(missing["gnu_oracle"], dict(available=False, bin=None, version=None))
        self.assertEqual(available["gnu_oracle"]["version"], "GNU bash 5.3")

    def test_oracle_recovery_retests_degraded_verdict_without_redo(self):
        self.asset("degraded", "t5")
        manifest = self.root / "manifest"
        manifest.mkdir()
        db_path = manifest / "eco-manifest.sqlite"
        with contextlib.closing(sqlite3.connect(db_path)) as disk:
            self.db.backup(disk)
        niu, rubash = self.root / "niu.exe", self.root / "rubash.exe"
        niu.write_bytes(b"inert niu")
        rubash.write_bytes(b"inert rubash")
        args = ["--niu", str(niu), "--rubash", str(rubash), "--manifest-dir", str(manifest),
                "--results-dir", str(self.root), "--tmp-root", str(self.root / "tmp"),
                "--cache-dir", str(self.root / "cache")]
        def fixture(asset, cfg):
            return dict(content_hash=asset["content_hash"],
                        verdict="HANG" if cfg.gnu.bin is None else "GNU-ALSO-FAILS")
        output = io.StringIO()
        with mock.patch.object(ECO, "load_pty", return_value=object()), \
             mock.patch.object(ECO, "GnuBash", return_value=SimpleNamespace(bin=None)) as oracle, \
             mock.patch.object(ECO, "test_one", side_effect=fixture) as tester, \
             mock.patch.object(ECO.subprocess, "run", side_effect=AssertionError("no subprocess permitted")), \
             mock.patch.dict(os.environ, GH_TOKEN="fixture-token",
                             GITHUB_STEP_SUMMARY=str(self.root / "summary.md")), contextlib.redirect_stdout(output):
            self.assertEqual(ECO.main(args), 0)
            self.assertEqual(ECO.main(args), 0)
            self.assertEqual(tester.call_count, 1)
            oracle.return_value = SimpleNamespace(bin="/usr/bin/bash", version="GNU bash 5.3")
            self.assertEqual(ECO.main(args), 0)
            self.assertEqual(tester.call_count, 2)
            self.assertEqual(ECO.main(args), 0)
            self.assertEqual(tester.call_count, 2)
        records = [json.loads(line) for line in self.results.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([record["verdict"] for record in records], ["HANG", "GNU-ALSO-FAILS"])
        self.assertNotEqual(records[0]["build_id"], records[1]["build_id"])
        self.assertFalse(records[0]["build_identity"]["gnu_oracle"]["available"])
        self.assertTrue(records[1]["build_identity"]["gnu_oracle"]["available"])
        with contextlib.closing(sqlite3.connect(db_path)) as disk:
            self.assertEqual(disk.execute("SELECT COUNT(*) FROM eco_test_verdicts").fetchone()[0], 2)

    def test_time_budget_stops_dispatch_and_drains_inflight(self):
        tested = []
        with mock.patch.object(ECO.time, "monotonic", side_effect=[0, 0, 2]):
            submitted = ECO.run_bounded([1, 2, 3], 1, 1, tested.append)
        self.assertEqual((submitted, tested), (1, [1]))

    def test_unlimited_time_still_respects_selected_assets(self):
        tested = []
        self.assertEqual(ECO.run_bounded([1, 2, 3], 2, 0, tested.append), 3)
        self.assertEqual(sorted(tested), [1, 2, 3])

    def test_summary_distinguishes_idle_from_throughput(self):
        self.asset("old", verdict="OK")
        self.migrate()
        selected, stats = self.select(stale_limit=0)
        self.assertEqual(selected, [])
        summary = self.root / "summary.md"
        with mock.patch.dict(os.environ, GITHUB_STEP_SUMMARY=str(summary)):
            ECO.write_step_summary({}, 0, self.results, idle_note="idle: eligible backlog remains",
                                   selection=stats, build_id="build-a")
        text = summary.read_text(encoding="utf-8")
        for expected in ("| unseen | 0 |", "| stale | 1 |", "| current | 0 |",
                         "| selected | 0 |", "idle: eligible backlog remains", "0 assets tested"):
            self.assertIn(expected, text)
        self.assertNotIn("assets/hour", text)

    def test_main_safe_fixture_persists_identity_resumes_and_redoes(self):
        manifest = self.root / "manifest"
        manifest.mkdir()
        db_path = manifest / "eco-manifest.sqlite"
        self.asset("safe", "t5")
        with contextlib.closing(sqlite3.connect(db_path)) as disk:
            self.db.backup(disk)
        niu, rubash = self.root / "niu.exe", self.root / "rubash.exe"
        niu.write_bytes(b"not executable")
        rubash.write_bytes(b"not executable")
        args = ["--niu", str(niu), "--rubash", str(rubash), "--manifest-dir", str(manifest),
                "--results-dir", str(self.root), "--tmp-root", str(self.root / "tmp"),
                "--cache-dir", str(self.root / "cache")]
        def fixture(asset, cfg):
            return dict(content_hash=asset["content_hash"], verdict="OK")
        output = io.StringIO()
        with mock.patch.object(ECO, "load_pty", return_value=object()), \
             mock.patch.object(ECO, "GnuBash", return_value=SimpleNamespace(bin=None)), \
             mock.patch.object(ECO, "test_one", side_effect=fixture) as tester, \
             mock.patch.object(ECO.subprocess, "run", side_effect=AssertionError("no subprocess permitted")), \
             mock.patch.dict(os.environ, GH_TOKEN="fixture-token",
                             GITHUB_STEP_SUMMARY=str(self.root / "summary.md")), contextlib.redirect_stdout(output):
            self.assertEqual(ECO.main(args), 0)
            self.assertEqual(ECO.main(args), 0)
            self.assertEqual(tester.call_count, 1)
            self.assertIn("idle: no eligible assets for this build", output.getvalue())
            self.assertEqual(ECO.main([*args, "--redo"]), 0)
            self.assertEqual(tester.call_count, 2)
            self.assertEqual(ECO.main([*args, "--rubash-revision", "new-sha"]), 0)
            self.assertEqual(tester.call_count, 3)
            tester.side_effect = lambda asset, cfg: dict(content_hash=asset["content_hash"], verdict="FETCH-FAILED")
            self.assertEqual(ECO.main([*args, "--rubash-revision", "fetch-fixture"]), 3)
            tester.side_effect = RuntimeError("synthetic harness failure")
            self.assertEqual(ECO.main([*args, "--rubash-revision", "error-fixture"]), 0)
        records = [json.loads(line) for line in self.results.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(records[0]["build_id"], records[1]["build_id"])
        self.assertNotEqual(records[1]["build_id"], records[2]["build_id"])
        self.assertIn("harness_sha256", records[0]["build_identity"])
        self.assertEqual(records[-1]["verdict"], "HARNESS-ERROR")
        self.assertIn("fetch gate failed", (self.root / "summary.md").read_text(encoding="utf-8"))
        with contextlib.closing(sqlite3.connect(db_path)) as disk:
            self.assertEqual(disk.execute("SELECT COUNT(*) FROM eco_test_verdicts").fetchone()[0], 4)
            self.assertEqual(disk.execute("SELECT COUNT(*) FROM eco_test_builds").fetchone()[0], 5)


if __name__ == "__main__":
    unittest.main()
