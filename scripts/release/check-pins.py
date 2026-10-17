#!/usr/bin/env python3
"""Report release pins that lag the published wpm index.

`scripts/release/preinstall.json` and `scripts/release/winuxcmd-pin.json` are
release *policy*: the release workflow refuses to bundle a WinuxCmd engine or a
pre-install package whose digest does not match the pin. That check cannot
notice a pin that is simply **old** - the digest still matches the release it
names - so a stale pin quietly ships an older engine, and the only signal is
someone remembering to look.

This is the detector for that. It reads the same index a release consumes and
reports every pin that is behind (and every digest that disagrees with the
index, which catches an asset re-uploaded under a version that did not change).

    python scripts/release/check-pins.py                   # report, exit 0
    python scripts/release/check-pins.py --fail-on-stale    # exit 1 when stale
    python scripts/release/check-pins.py --index <url|path> # other index
    python scripts/release/check-pins.py --self-test        # check the checker
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import urllib.request
from pathlib import Path

DEFAULT_INDEX = "https://raw.githubusercontent.com/unixwin/wpm-source/main/index.json"
RELEASE_DIR = Path(__file__).resolve().parent
PREINSTALL_PIN = RELEASE_DIR / "preinstall.json"
WINUXCMD_PIN = RELEASE_DIR / "winuxcmd-pin.json"

# Arch keys the release matrix builds; both must stay pinned.
ARCH_KEYS = ("windows-x64", "windows-arm64")


def version_key(version: str) -> tuple:
    """Order versions the way wpm does: a dotted-numeric core plus an optional
    Debian-style packaging revision (`5.4.1-unixwin2` > `5.4.1-unixwin1` >
    `5.4.1`). Anything unparsable sorts last so it is reported rather than
    silently considered newest."""
    core, _, revision = version.partition("-")
    numbers = []
    for part in core.split("."):
        match = re.match(r"^(\d+)(.*)$", part)
        if not match:
            return (1, (), version)
        numbers.append((int(match.group(1)), match.group(2)))
    revision_key = ()
    revision_match = re.match(r"^unixwin(\d+)$", revision)
    if revision_match:
        revision_key = (int(revision_match.group(1)),)
    elif revision:
        revision_key = (version,)
    return (0, tuple(numbers), revision_key)


def compare(left: str, right: str) -> int:
    """-1 when left is older than right, 0 equal, 1 newer."""
    a, b = version_key(left), version_key(right)
    return (a > b) - (a < b)


def load_index(source: str) -> dict:
    if source.startswith(("http://", "https://")):
        request = urllib.request.Request(source, headers={"User-Agent": "niubash-pin-check"})
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read())
    return json.loads(Path(source).read_text(encoding="utf-8"))


def pinned_entries() -> list[dict]:
    """Every pin, normalized to one shape."""
    entries = []
    winuxcmd = json.loads(WINUXCMD_PIN.read_text(encoding="utf-8"))
    entries.append(
        {
            "name": "winuxcmd",
            "version": winuxcmd["winuxcmd_tag"].lstrip("v"),
            "sha256": winuxcmd.get("sha256", {}),
            "source": WINUXCMD_PIN.name,
            # The core engine is what every install runs, and the release matrix
            # builds both legs: a missing digest there is a defect.
            "all_arches_required": True,
        }
    )
    preinstall = json.loads(PREINSTALL_PIN.read_text(encoding="utf-8"))
    for package in preinstall.get("packages", []):
        entries.append(
            {
                "name": package["name"],
                "version": package["version"],
                "sha256": package.get("sha256", {}),
                "source": PREINSTALL_PIN.name,
            }
        )
    return entries


def check(entries: list[dict], index: dict) -> tuple[list[str], list[str]]:
    """(problems, report lines)."""
    published = {package["name"]: package for package in index.get("packages", [])}
    problems, lines = [], []
    for entry in entries:
        name, pinned = entry["name"], entry["version"]
        remote = published.get(name)
        if remote is None:
            problems.append(f"{name}: pinned {pinned} but not in the index at all")
            lines.append(f"  MISSING {name}: pinned {pinned}, index has no such package")
            continue
        latest = remote.get("version", "")
        if compare(pinned, latest) < 0:
            problems.append(f"{name}: pinned {pinned}, index has {latest}")
            lines.append(f"  STALE   {name}: pinned {pinned} < index {latest} ({entry['source']})")
        elif compare(pinned, latest) > 0:
            lines.append(f"  AHEAD   {name}: pinned {pinned} > index {latest}")
        else:
            lines.append(f"  ok      {name}: {pinned}")

        remote_artifacts = remote.get("artifacts", {})
        for arch in ARCH_KEYS:
            want = entry["sha256"].get(arch)
            have = remote_artifacts.get(arch, {}).get("sha256")
            if want is None:
                if entry.get("all_arches_required"):
                    problems.append(f"{name}: no {arch} digest pinned (the release matrix builds both)")
                    lines.append(f"          missing {arch} digest in {entry['source']}")
                else:
                    # The release workflow warns and skips pre-install for an
                    # arch a package does not pin (fail-open, niubash#312), so
                    # this is worth saying but not worth failing.
                    lines.append(
                        f"          note: no {arch} digest pinned in {entry['source']} "
                        f"- pre-install is skipped for that arch"
                    )
            elif have and want != have:
                problems.append(f"{name}: {arch} digest differs from the index")
                lines.append(
                    f"          {arch} digest differs from the index "
                    f"({want[:12]}… vs {have[:12]}…)"
                )
    return problems, lines


def self_test() -> int:
    """Two fixtures: a fresh set and a stale one. Keeps the checker honest."""
    fresh = {
        "packages": [
            {"name": "winuxcmd", "version": "1.1.13",
             "artifacts": {a: {"sha256": "aa"} for a in ARCH_KEYS}},
            {"name": "gawk", "version": "5.4.1-unixwin1",
             "artifacts": {"windows-x64": {"sha256": "bb"}}},
        ]
    }
    stale = {
        "packages": [
            {"name": "winuxcmd", "version": "1.1.14",
             "artifacts": {a: {"sha256": "aa"} for a in ARCH_KEYS}},
            {"name": "gawk", "version": "5.4.1-unixwin2",
             "artifacts": {"windows-x64": {"sha256": "cc"}}},
        ]
    }
    entries = [
        {"name": "winuxcmd", "version": "1.1.13", "sha256": {a: "aa" for a in ARCH_KEYS},
         "source": "fixture", "all_arches_required": True},
        {"name": "gawk", "version": "5.4.1-unixwin1", "sha256": {"windows-x64": "bb"},
         "source": "fixture"},
    ]

    fresh_problems, _ = check(entries, fresh)
    if fresh_problems:
        print("self-test: the fresh fixture reported problems:", fresh_problems)
        return 1

    stale_problems, lines = check(entries, stale)
    expected = 3  # winuxcmd behind, gawk behind, gawk digest differs
    if len(stale_problems) != expected:
        print("self-test: the stale fixture reported", len(stale_problems), "problems:")
        print("\n".join(lines))
        return 1

    # Ordering: the packaging revision must decide, not the string.
    if not (compare("5.4.1", "5.4.1-unixwin1") < 0
            and compare("5.4.1-unixwin1", "5.4.1-unixwin2") < 0
            and compare("1.1.9", "1.1.13") < 0):
        print("self-test: version ordering is wrong")
        return 1

    # A pre-install package that pins one arch is normal (fail-open for the
    # other), while the core engine must pin both.
    one_arch = [{"name": "gawk", "version": "1.0", "sha256": {"windows-x64": "bb"},
                 "source": "fixture"}]
    relaxed, _ = check(one_arch, {"packages": [
        {"name": "gawk", "version": "1.0", "artifacts": {"windows-x64": {"sha256": "bb"}}}]})
    if relaxed:
        print("self-test: a one-arch pre-install pin must not be a problem:", relaxed)
        return 1
    strict, _ = check([{"name": "winuxcmd", "version": "1.0", "sha256": {"windows-x64": "aa"},
                        "source": "fixture", "all_arches_required": True}],
                      {"packages": [{"name": "winuxcmd", "version": "1.0",
                                     "artifacts": {"windows-x64": {"sha256": "aa"}}}]})
    if len(strict) != 1:
        print("self-test: the core engine must require both arches:", strict)
        return 1

    print("self-test: ok")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--index", default=DEFAULT_INDEX, help="index URL or file path")
    parser.add_argument("--fail-on-stale", action="store_true",
                        help="exit 1 when any pin lags the index")
    parser.add_argument("--self-test", action="store_true", help="verify the checker itself")
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    entries = pinned_entries()
    index = load_index(args.index)
    problems, lines = check(entries, index)

    print(f"pins checked against {args.index}")
    print("\n".join(lines))
    if problems:
        print(f"\n{len(problems)} pin(s) need attention:")
        for problem in problems:
            print(f"  - {problem}")
        print("\nBump the pin, measure the digests from the published assets, and open a PR.")
        return 1 if args.fail_on_stale else 0
    print("\nall pins match the published index")
    return 0


if __name__ == "__main__":
    sys.exit(main())
