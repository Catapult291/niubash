#!/usr/bin/env python3
"""Sandbox path + hygiene regression tests for golden-journey.py
(issue #428, both root causes):

  root cause 1 — the repo's git-bash-style `/d/tmp/x` is DRIVE-RELATIVE
  to a bare Python script on Windows (landed at `<cwd-drive>:\\d\\tmp\\x`,
  the literal `/d` becoming a junk directory): the normalizer must
  rewrite the single-letter-drive form and leave every explicit or
  genuinely-relative form untouched.

  root cause 2 — teardown must leave the tree HOST-CLEARABLE (the kept
  debris used to deny even enumeration; takeown/icacls/rd all failed on
  the host side), and the --cleanup-sandbox verb must refuse to be a
  foot-gun.

Runs against the real module (importlib, no niu.exe, no ConPTY
sessions). Needs pywinpty + pyte importable because the module itself
imports them at top level. Exit 0 = contract holds, 1 = broken.
"""

import importlib.util
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODULE_PATH = HERE / "golden-journey.py"


def load_module():
    spec = importlib.util.spec_from_file_location(
        "golden_journey_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_readonly_tree(root: Path) -> None:
    (root / "home" / ".niubash" / "sources" / "oh-my-bash" / ".git"
     / "objects").mkdir(parents=True)
    loose = root / "home" / ".niubash" / "sources" / "oh-my-bash" / ".git" \
        / "objects" / "ab" / "cdef"
    loose.parent.mkdir(parents=True)
    loose.write_text("loose git object")
    os.chmod(loose, stat.S_IREAD)  # the 444 Windows read-only attribute
    (root / "home" / "marker.txt").write_text("x")


def main() -> int:
    gj = load_module()
    failures = []

    def expect(name, got, want):
        if got != want:
            failures.append(f"{name}: got {got!r}, want {want!r}")

    if os.name == "nt":  # the guard is Windows-shaped by definition
        # ── Root cause 1: the POSIX drive form ──────────────────────────
        expect("/d/tmp/x normalizes",
               str(gj.normalize_posix_drive_form(Path("/d/tmp/x"))),
               "D:\\tmp\\x")
        expect("/D/TMP normalizes case-insensitively",
               str(gj.normalize_posix_drive_form(Path("/D/TMP"))),
               "D:\\TMP")
        expect("/d alone normalizes",
               str(gj.normalize_posix_drive_form(Path("/d"))), "D:\\")
        expect("explicit D:/ form never regresses",
               str(gj.normalize_posix_drive_form(Path("D:/tmp/x"))),
               "D:\\tmp\\x")
        expect("genuinely drive-relative /tmp/x is untouched",
               str(gj.normalize_posix_drive_form(Path("/tmp/x"))),
               str(Path("/tmp/x")))
        expect("multi-letter first segment is untouched",
               str(gj.normalize_posix_drive_form(Path("/ab/tmp"))),
               str(Path("/ab/tmp")))
        expect("plain relative path is untouched",
               str(gj.normalize_posix_drive_form(Path("tmp/x"))),
               str(Path("tmp/x")))

    # ── Root cause 2: host-clearable teardown ────────────────────────────
    with tempfile.TemporaryDirectory() as tmp:
        tree = Path(tmp) / "journey-probe"
        make_readonly_tree(tree)
        gj._rmtree_clearing_readonly(tree)
        expect("read-only tree fully removed", tree.exists(), False)

    with tempfile.TemporaryDirectory() as tmp:
        parent = Path(tmp)
        (parent / "journey-alive").mkdir()
        expect("_tree_present sees an entry", gj._tree_present(
            parent / "journey-alive"), True)
        expect("_tree_present honest about absence", gj._tree_present(
            parent / "journey-gone"), False)
        expect("_tree_present falls back for a missing parent",
               gj._tree_present(parent / "no-such-parent" / "child"),
               False)

    with tempfile.TemporaryDirectory() as tmp:
        tree = Path(tmp) / "journey-probe"
        make_readonly_tree(tree)
        ok, detail = gj.force_rmtree(tree)
        expect("force_rmtree removes a read-only tree", ok, True)
        expect("force_rmtree tree gone", tree.exists(), False)
        if "survived" in detail:
            failures.append(f"force_rmtree claimed survival on a soft "
                            f"tree: {detail}")

    # The verb is a blade: it refuses relative paths and drive roots.
    expect("cleanup verb refuses a relative path",
           gj.cleanup_sandbox_verb(Path("tmp/relative")), 2)
    expect("cleanup verb refuses a drive root",
           gj.cleanup_sandbox_verb(Path("D:\\")), 2)

    # The verb end to end on a real (soft) tree.
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "journey-local-325" / "journey-ab12cd"
        make_readonly_tree(target)
        rc = gj.cleanup_sandbox_verb(target)
        expect("cleanup verb removes a real tree", rc, 0)
        expect("cleanup verb leaves nothing", target.exists(), False)
        expect("cleanup verb leaves the parent alone",
               (Path(tmp) / "journey-local-325").exists(), True)

    # An absent path is already "gone" — the verb must not traceback.
    with tempfile.TemporaryDirectory() as tmp:
        rc = gj.cleanup_sandbox_verb(Path(tmp) / "journey-never-was")
        expect("cleanup verb on an absent path exits 0", rc, 0)

    # Main-contract plumbing: --cleanup-sandbox works WITHOUT a niu.exe,
    # and the niu.exe requirement survives the nargs='?' change. Every
    # child spawn carries CREATE_NO_WINDOW (0x08000000): a probe run must
    # never flash a visible console on the owner's desktop.
    CREATE_NO_WINDOW = {"creationflags": 0x08000000} if os.name == "nt" else {}
    probe_parent = Path(tempfile.mkdtemp(prefix="gj428-verb-"))
    try:
        sandbox = probe_parent / "journey-probe-dead"
        sandbox.mkdir()
        done = subprocess.run(
            [sys.executable, str(MODULE_PATH), "--cleanup-sandbox",
             str(sandbox)],
            capture_output=True, text=True, timeout=300, **CREATE_NO_WINDOW)
        expect("bare --cleanup-sandbox (no niu.exe) exits 0",
               done.returncode, 0)
        expect("bare --cleanup-sandbox removed the tree",
               sandbox.exists(), False)
        if str(sandbox) not in done.stdout:
            failures.append("cleanup verb stdout never names the target")

        done = subprocess.run(
            [sys.executable, str(MODULE_PATH)],
            capture_output=True, text=True, timeout=300, **CREATE_NO_WINDOW)
        expect("no niu.exe and no verb is a usage error (exit 2)",
               done.returncode, 2)
    finally:
        gj._rmtree_clearing_readonly(probe_parent)

    if failures:
        print("FAIL:")
        for line in failures:
            print(f"  - {line}")
        return 1
    print("ok: sandbox path + hygiene contract holds (#428)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
