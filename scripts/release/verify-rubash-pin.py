#!/usr/bin/env python3
"""Verify a rubash checkout against scripts/release/rubash-pin.json.

`scripts/release/rubash-pin.json` is release *policy* (rubash#827, conclusion
B): every release job fetches exactly the pinned commit into ../rubash and
refuses to build anything else, so a release can no longer compile "whatever
rubash master HEAD was at build time" (the v1.4.0 defect: run 38506862578
compiled e785c3c8f610 while the era declared rubash v1.4.0 = f69e7900).

This is the standalone auditor for that pin. It checks one checkout against
the pin and reports every disagreement -- wrong revision, dirty work tree,
wrong self-declared version, not a git repo at all:

    python scripts/release/verify-rubash-pin.py                     # audit ../rubash
    python scripts/release/verify-rubash-pin.py --checkout <dir>    # audit that tree
    python scripts/release/verify-rubash-pin.py --fetch             # dry-run: fetch the
                                                                    # pinned rev into a
                                                                    # temp dir and audit it
    python scripts/release/verify-rubash-pin.py --self-test         # fixtures, no network
    python scripts/release/verify-rubash-pin.py --fetch-self-test   # the dry-run plus its
                                                                    # tamper case, needs network

The release workflow enforces the same contract inline (fail-hard steps in
release.yml); this script is the independent check a release captain runs
before cutting a tag and pin-freshness runs so a pin that rotted (e.g. a
rubash force-push) surfaces as a report, not a red release.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

RELEASE_DIR = Path(__file__).resolve().parent
RUBASH_PIN = RELEASE_DIR / "rubash-pin.json"
RUBASH_REMOTE = "https://github.com/unixwin/rubash.git"

FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


def force_remove(path: Path) -> None:
    """shutil.rmtree that wins the Windows fight against git's read-only
    object files (plain rmtree fails on them and, with ignore_errors, would
    silently strand fixture temp dirs on exactly the platform that hits it)."""

    def relax(function, target, _exc):
        try:
            os.chmod(target, stat.S_IWRITE)
            function(target)
        except OSError:
            pass

    try:
        shutil.rmtree(path, onexc=relax)
    except TypeError:  # Python < 3.12
        shutil.rmtree(path, onerror=relax)
    except OSError:
        pass


def run_git(directory: Path, *args: str) -> tuple[bool, str]:
    """(ok, combined output) of one git invocation in `directory`."""
    result = subprocess.run(
        ["git", "-C", str(directory), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = (result.stdout + result.stderr).strip()
    return result.returncode == 0, output


def load_pin(path: Path) -> tuple[dict, list[str]]:
    """(pin, problems). The pin is only trusted when the problems list is empty."""
    problems: list[str] = []
    try:
        pin = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return {}, [f"{path.name}: unreadable ({error})"]
    rev = pin.get("rubash_rev", "")
    if not FULL_SHA.match(rev):
        problems.append(
            f"{path.name}: rubash_rev must be a full 40-char commit sha, got {rev!r} "
            "(abbreviated forms cannot be fetched -- git resolves the fetch argument "
            "server-side as a ref name)"
        )
    version = pin.get("rubash_version", "")
    if not re.match(r"^\d+\.\d+\.\d+", str(version)):
        problems.append(f"{path.name}: rubash_version must be a semver string, got {version!r}")
    return pin, problems


def checkout_revision(directory: Path) -> str | None:
    ok, output = run_git(directory, "rev-parse", "HEAD")
    return output.split()[0] if ok else None


def checkout_is_clean(directory: Path) -> tuple[bool, str]:
    """Same dirty semantics as build.rs: only tracked content that differs from
    HEAD makes a tree dirty; untracked files never do."""
    ok, output = run_git(directory, "status", "--porcelain", "--untracked-files=no")
    if not ok:
        return False, output
    return not output.strip(), output.strip()


def checkout_version(directory: Path) -> str | None:
    """The Cargo.toml version the checkout claims for itself."""
    cargo_toml = directory / "Cargo.toml"
    try:
        text = cargo_toml.read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    return match.group(1) if match else None


def verify(checkout: Path, pin: dict) -> list[str]:
    """Every reason `checkout` does not honor `pin`. Empty list = verified."""
    problems: list[str] = []
    if not checkout.is_dir():
        return [f"{checkout}: checkout directory is missing"]

    rev = checkout_revision(checkout)
    if rev is None:
        return [f"{checkout}: not a git checkout (rev-parse HEAD failed)"]

    expected = pin.get("rubash_rev", "")
    if rev != expected:
        short_actual, short_expected = rev[:12], expected[:12]
        problems.append(
            f"{checkout}: HEAD {short_actual} does not match the pinned revision "
            f"{short_expected} -- this tree must not reach a release (rubash#827)"
        )

    clean, output = checkout_is_clean(checkout)
    if not clean:
        problems.append(
            f"{checkout}: work tree is dirty; a release must pin a committed revision\n{output}"
        )

    version = checkout_version(checkout)
    expected_version = str(pin.get("rubash_version", ""))
    if version is None:
        problems.append(f"{checkout}: could not read the version from Cargo.toml")
    elif version != expected_version:
        problems.append(
            f"{checkout}: Cargo.toml version {version} does not match the pinned "
            f"rubash_version {expected_version} -- rev and version drifted"
        )
    return problems


def fetch_pinned(destination: Path, rev: str) -> list[str]:
    """Materialize the pinned revision at `destination`, release.yml's recipe."""
    problems: list[str] = []
    steps = [
        ["init", "-q", str(destination)],
        ["-C", str(destination), "remote", "add", "origin", RUBASH_REMOTE],
        ["-C", str(destination), "fetch", "--depth", "1", "origin", rev],
        ["-C", str(destination), "checkout", "--quiet", "FETCH_HEAD"],
    ]
    for args in steps:
        ok, output = run_git(Path("."), *args)
        if not ok:
            problems.append(
                f"git {' '.join(args)} failed: {output}\n"
                f"  if the pinned revision is gone upstream (force-push), bump "
                f"{RUBASH_PIN.name} (see its notes for the flow)"
            )
            return problems
    return problems


def self_test() -> int:
    """Fixture repos, no network: the verifier must accept an honest tree and
    reject every tampered form of the contract."""
    cases_failed: list[str] = []
    scratch = Path(tempfile.mkdtemp(prefix="rubash-pin-selftest-"))
    try:
        good = scratch / "good"
        good.mkdir()
        for args in (
            ["init", "-q", str(good)],
            ["-C", str(good), "config", "user.email", "pin@example.test"],
            ["-C", str(good), "config", "user.name", "pin fixture"],
        ):
            run_git(Path("."), *args)
        (good / "Cargo.toml").write_text('[package]\nname = "rubash"\nversion = "9.9.9"\n', encoding="utf-8")
        (good / "src.txt").write_text("hello\n", encoding="utf-8")
        run_git(Path("."), "-C", str(good), "add", "-A")
        ok, _ = run_git(Path("."), "-C", str(good), "commit", "-q", "-m", "fixture")
        if not ok:
            print("self-test: could not create the fixture commit")
            return 1
        rev = checkout_revision(good)
        assert rev is not None
        pin = {"rubash_rev": rev, "rubash_version": "9.9.9"}

        if verify(good, pin):
            cases_failed.append("an honest checkout must verify clean")

        # Tampered rev: flip the first hex digit. "0" flips to "1" and any
        # other digit flips to "0", so the value always changes.
        tampered_rev = dict(pin)
        first = rev[0]
        tampered_rev["rubash_rev"] = ("0" if first != "0" else "1") + rev[1:]
        problems = verify(good, tampered_rev)
        if not any("does not match the pinned revision" in problem for problem in problems):
            cases_failed.append("a tampered pin revision must fail with a revision mismatch")

        wrong_version = dict(pin, rubash_version="0.0.1")
        if not any("version" in problem for problem in verify(good, wrong_version)):
            cases_failed.append("a wrong pinned version must fail")

        # Dirty tree: an uncommitted edit to a tracked file. Restored below so
        # the next case starts from the honest tree.
        (good / "src.txt").write_text("hello\nedited\n", encoding="utf-8")
        if not any("dirty" in problem for problem in verify(good, pin)):
            cases_failed.append("a dirty work tree must fail")
        (good / "src.txt").write_text("hello\n", encoding="utf-8")

        # Untracked files must NOT dirty the tree (build.rs semantics).
        (good / "scratch.tmp").write_text("untracked\n", encoding="utf-8")
        if verify(good, pin):
            cases_failed.append("untracked files must not make a tree dirty")
        (good / "scratch.tmp").unlink()

        if not verify(scratch / "does-not-exist", pin):
            cases_failed.append("a missing checkout must fail")
    finally:
        force_remove(scratch)

    if cases_failed:
        print("self-test failed:")
        for case in cases_failed:
            print(f"  - {case}")
        return 1
    print("self-test: ok")
    return 0


def fetch_self_test() -> int:
    """The dry-run, end to end: fetch the real pinned rev into a temp dir and
    verify it passes, then prove a tampered rev fails against that same tree."""
    pin, problems = load_pin(RUBASH_PIN)
    if problems:
        for problem in problems:
            print(problem)
        return 1

    scratch = Path(tempfile.mkdtemp(prefix="rubash-pin-fetch-"))
    try:
        checkout = scratch / "rubash"
        if fetch_problems := fetch_pinned(checkout, pin["rubash_rev"]):
            for problem in fetch_problems:
                print(problem)
            return 1
        if problems := verify(checkout, pin):
            print("the pinned revision fetched fresh failed verification:")
            for problem in problems:
                print(f"  {problem}")
            return 1
        print(f"fetch dry-run: rubash {pin['rubash_rev'][:12]} (v{pin['rubash_version']}) fetched and verified")

        # Tamper case: the same tree against a pin whose rev was flipped.
        tampered = dict(pin)
        first = pin["rubash_rev"][0]
        tampered["rubash_rev"] = ("0" if first != "0" else "1") + pin["rubash_rev"][1:]
        if not verify(checkout, tampered):
            print("self-test: a tampered rev must fail against a real checkout")
            return 1
        print("tamper case: flipped rev correctly rejected")
        return 0
    finally:
        force_remove(scratch)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pin", type=Path, default=RUBASH_PIN, help="pin file (default: scripts/release/rubash-pin.json)")
    parser.add_argument("--checkout", type=Path, default=None, help="rubash tree to audit (default: ../rubash beside this repo)")
    parser.add_argument("--fetch", action="store_true", help="dry-run: fetch the pinned rev into a temp dir and verify it")
    parser.add_argument("--self-test", action="store_true", help="verify the verifier (local fixtures, no network)")
    parser.add_argument("--fetch-self-test", action="store_true", help="--fetch plus its tamper case (needs network)")
    args = parser.parse_args()

    if args.self_test:
        return self_test()
    if args.fetch_self_test:
        return fetch_self_test()

    pin, problems = load_pin(args.pin)
    if problems:
        for problem in problems:
            print(problem)
        return 1

    if args.fetch:
        checkout = Path(tempfile.mkdtemp(prefix="rubash-pin-fetch-")) / "rubash"
        if fetch_problems := fetch_pinned(checkout, pin["rubash_rev"]):
            for problem in fetch_problems:
                print(problem)
            return 1
    else:
        if args.checkout is not None:
            checkout = args.checkout
        else:
            checkout = Path(__file__).resolve().parents[2] / ".." / "rubash"
            checkout = checkout.resolve()

    if problems := verify(checkout, pin):
        print(f"rubash checkout does NOT honor {args.pin.name}:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(
        f"ok: {checkout} = {pin['rubash_rev'][:12]} (v{pin['rubash_version']}), "
        "clean, matches the pin"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
