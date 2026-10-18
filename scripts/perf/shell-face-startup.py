#!/usr/bin/env python3
r"""Per-invocation startup cost of every installed shell face, by name.

niubash#377: a shell face is any PATH-reachable name that starts the niu
engine — the payload `niu.exe` itself, the `bash.exe`/`sh.exe` faces under
`winuxcmd\bin` and `winuxcmd\usr\bin`, and convention slots such as
`C:\Program Files\Git\bin\bash.exe` (niubash#398). AI agents, CI and git
hooks pay the face's fixed cost on EVERY `-c` invocation, so the contract
that matters is execution-level: spawn the face with `-c exit`, time it,
and compare it against the payload's own spawn.

Since niubash#397 the installed faces are hardlinks to `niu.exe` (same
file, one process). The shipped forwarding shims (rubash's `bash` bin)
remain the fallback wherever hardlinks are unavailable; they re-execute
`niu.exe`, which is one extra process per call. This script is the number
behind that sentence — run it before and after touching the link layout.

A row is only recorded after the face proves it executes: `<face> -c
"echo <marker>"` must print the marker, otherwise the cell says "n/a"
with the failure note instead of a broken face being reported as a fast
measurement. Structural facts (size, hardlink count, same file as the
payload) are reported per row as annotations.

Gating (niubash#377 CR round): the gate is `--check-links` and it is
DETERMINISTIC — every declared face (`winuxcmd\bin\bash.exe`,
`winuxcmd\bin\sh.exe`, `winuxcmd\usr\bin\bash.exe`, `winuxcmd\usr\bin\sh.exe`)
must be the SAME FILE as the payload `niu.exe`. The gate exists to catch a
link degenerating back into a forwarder, and inode identity is exactly that
fact, which the script computes either way. A measured-time-ratio gate was
tried first and rejected with data: shimmed faces measured 1.04-1.46x the
payload while healthy relinked faces measured 0.91-1.26x — the intervals
overlap, so no ratio threshold separates the two states without false
kills. The timing columns stay informational. The Git-bin convention slot
is never gated; it is not a declared face (niubash#398).

Usage:

    python scripts/perf/shell-face-startup.py
    python scripts/perf/shell-face-startup.py --root D:/sandbox/Niubash
    python scripts/perf/shell-face-startup.py --check-links
    python scripts/perf/shell-face-startup.py --json report.json
    python scripts/perf/shell-face-startup.py --powershell-crosscheck

`--powershell-crosscheck` re-times every verified row through PowerShell's
`Measure-Command` (median of the same N) and prints both columns, so a
number can be defended against "your timer is the cost" objections.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import platform
import statistics
import subprocess
import sys
import time

DEVNULL = subprocess.DEVNULL
MARKER = "NIU-FACE-OK"

# niubash#398: the convention slot an orphan .NET shim occupies. Measured
# when present; a missing slot is not an error.
GIT_SLOT_CANDIDATES = (
    r"C:\Program Files\Git\bin\bash.exe",
    r"D:\Git\bin\bash.exe",
)

FACE_NAMES = (
    ("winuxcmd\\bin", "bash.exe"),
    ("winuxcmd\\bin", "sh.exe"),
    ("winuxcmd\\usr\\bin", "bash.exe"),
    ("winuxcmd\\usr\\bin", "sh.exe"),
)

# The faces niubash#397 owns: `--check-links` requires each of these to be
# the same file as the payload. Declared explicitly so the gate fails on a
# face that is missing, not just on one that degenerated into a forwarder.
DECLARED_FACES = tuple(f"{subdir}\\{name}" for subdir, name in FACE_NAMES)


def default_root():
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidate = os.path.join(local, "Programs", "Niubash")
        if os.path.isfile(os.path.join(candidate, "niu.exe")):
            return candidate
    return None


def collect_faces(root, extra):
    """(label, path) rows for every face that exists under root."""
    rows = []
    if root:
        payload = os.path.join(root, "niu.exe")
        if os.path.isfile(payload):
            rows.append(("niu.exe (payload)", payload))
        for subdir, name in FACE_NAMES:
            path = os.path.join(root, subdir, name)
            if os.path.isfile(path):
                rows.append((f"{subdir}\\{name}", path))
    for path in extra:
        if os.path.isfile(path):
            rows.append((path, path))
        else:
            print(f"note: --face {path} does not exist; skipped", file=sys.stderr)
    return rows


def verify_face(path):
    """True iff the face executes and echoes the marker (execution-level)."""
    try:
        out = subprocess.run(
            [path, "-c", f"echo {MARKER}"],
            stdout=subprocess.PIPE,
            stderr=DEVNULL,
            timeout=60,
            text=True,
        ).stdout or ""
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, str(error)
    if MARKER in out:
        return True, ""
    return False, f"marker missing (stdout={out.strip()[:80]!r})"


def timed(path, n, warmup, timeout):
    """Per-call milliseconds for `<face> -c exit`. None when it broke."""
    samples = []
    for i in range(n + warmup):
        t0 = time.perf_counter()
        try:
            subprocess.run([path, "-c", "exit"], stdout=DEVNULL,
                           stderr=DEVNULL, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired):
            return None
        t1 = time.perf_counter()
        if i >= warmup:
            samples.append((t1 - t0) * 1000.0)
    if not samples:
        return None
    ordered = sorted(samples)
    return {
        "avg": statistics.mean(samples),
        "min": ordered[0],
        "median": statistics.median(samples),
        "p95": ordered[min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))],
    }


def measure_command_ms(path, n, timeout):
    """One Measure-Command sample; the PowerShell cross-check of timed()."""
    argv = [
        "powershell", "-NoProfile", "-Command",
        "(Measure-Command { & '%s' -c exit *> $null }).TotalMilliseconds"
        % path.replace("'", "''"),
    ]
    try:
        out = subprocess.run(argv, stdout=subprocess.PIPE, stderr=DEVNULL,
                             timeout=timeout, text=True).stdout or ""
        return float(out.strip())
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None


def structural(path):
    """Byte size / hardlink count / file identity, as report annotations."""
    info = {}
    try:
        st = os.stat(path)
        info["bytes"] = st.st_size
        if st.st_nlink > 0:
            info["hardlinks"] = st.st_nlink
        info["file_id"] = (st.st_dev, st.st_ino)
    except OSError:
        info["bytes"] = None
    return info


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", help="installation root (default: the machine's Niubash install)")
    parser.add_argument("--face", action="append", default=[],
                        help="extra face executable to measure (repeatable)")
    parser.add_argument("--slot", action="store_true", default=True,
                        help="include the Git bin convention slot when present (default)")
    parser.add_argument("--no-slot", dest="slot", action="store_false",
                        help="skip the Git bin convention slot")
    parser.add_argument("--n", type=int, default=20, help="timed samples per row (default 20)")
    parser.add_argument("--warmup", type=int, default=3, help="untimed warmup runs per row")
    parser.add_argument("--timeout", type=int, default=60, help="per-run timeout in seconds")
    parser.add_argument("--check-links", action="store_true",
                        help="gate: every declared face must be the same file as the "
                             "payload niu.exe (deterministic; timing ratios cannot gate)")
    parser.add_argument("--powershell-crosscheck", action="store_true",
                        help="re-time every row with Measure-Command (N samples each)")
    parser.add_argument("--json", help="write the report as JSON to this path")
    args = parser.parse_args()

    root = args.root or default_root()
    if not root:
        print("note: no installation root found; pass --root", file=sys.stderr)

    rows = collect_faces(root, args.face)
    if args.slot:
        for candidate in GIT_SLOT_CANDIDATES:
            if os.path.isfile(candidate):
                rows.append(("slot: " + candidate, candidate))
                break
    if not rows:
        print("no faces found; nothing to measure", file=sys.stderr)
        return 2

    payload_id = None
    if root:
        payload_info = structural(os.path.join(root, "niu.exe"))
        payload_id = payload_info.get("file_id")

    report = {
        "schema": "niubash:perf-shell-face-startup@1.0.0",
        "generated": _dt.datetime.now().isoformat(timespec="seconds"),
        "host": platform.node(),
        "os": platform.platform(),
        "root": root,
        "samples_per_row": args.n,
        "rows": [],
    }

    print(f"root: {root or '(none)'}   N={args.n} warmup={args.warmup} (`face -c exit`, ms)")
    header = f"{'face':44s} {'avg':>7s} {'min':>7s} {'med':>7s} {'p95':>7s}  annotation"
    print(header)
    print("-" * len(header))

    for label, path in rows:
        ok, note = verify_face(path)
        info = structural(path)
        link = "same-file-as-payload" if payload_id and info.get("file_id") == payload_id else "distinct-from-payload"
        if not ok:
            row = {"face": label, "path": path, "ok": False, "note": note,
                   "bytes": info.get("bytes")}
            print(f"{label:44s} {'n/a':>7s} {'':7s} {'':7s} {'':7s}  {note}")
            report["rows"].append(row)
            continue
        timing = timed(path, args.n, args.warmup, args.timeout)
        if timing is None:
            row = {"face": label, "path": path, "ok": False,
                   "note": "a run failed during timing", "bytes": info.get("bytes")}
            print(f"{label:44s} {'n/a':>7s} {'':7s} {'':7s} {'':7s}  run failed during timing")
            report["rows"].append(row)
            continue
        anno = [link]
        if info.get("bytes") is not None:
            anno.append(f"{info['bytes']:,} B")
        if info.get("hardlinks"):
            anno.append(f"nlink={info['hardlinks']}")
        row = {"face": label, "path": path, "ok": True, "bytes": info.get("bytes"),
               "hardlinks": info.get("hardlinks"),
               "same_file_as_payload": payload_id is not None and info.get("file_id") == payload_id,
               "avg_ms": round(timing["avg"], 2), "min_ms": round(timing["min"], 2),
               "median_ms": round(timing["median"], 2), "p95_ms": round(timing["p95"], 2)}
        if args.powershell_crosscheck:
            ps = [measure_command_ms(path, args.n, args.timeout) for _ in range(args.n)]
            ps = [v for v in ps if v is not None]
            if ps:
                row["measure_command_median_ms"] = round(statistics.median(ps), 2)
                anno.append(f"Measure-Command med {row['measure_command_median_ms']:.1f}")
        print(f"{label:44s} {timing['avg']:7.1f} {timing['min']:7.1f} "
              f"{timing['median']:7.1f} {timing['p95']:7.1f}  {', '.join(anno)}")
        report["rows"].append(row)

    gate_code = 0
    if args.check_links:
        gate_code = print_link_gate(report, root, payload_id)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)
        print(f"\nreport: {args.json}")
    return gate_code


def print_link_gate(report, root, payload_id):
    """Deterministic gate (niubash#377 CR): declared faces == payload inode.

    Returns the process exit code: 0 pass, 1 fail, 2 cannot evaluate.
    """
    if payload_id is None:
        print(
            "\n--check-links: cannot evaluate — no payload niu.exe found"
            f" under root={root or '(none)'}; pass --root pointing at an"
            " installation that contains niu.exe",
            file=sys.stderr,
        )
        return 2

    by_label = {
        os.path.normcase(row["face"]): row
        for row in report["rows"]
        if not row["face"].startswith("niu.exe") and "slot:" not in row["face"]
    }
    print(f"\n--check-links: {len(DECLARED_FACES)} declared face(s), payload"
          f" niu.exe under {root}")
    failures = 0
    for face in DECLARED_FACES:
        row = by_label.get(os.path.normcase(face))
        if row is None:
            print(f"  FAIL {face}: declared face not present under root")
            failures += 1
        elif not row.get("ok"):
            print(f"  FAIL {face}: face did not execute ({row.get('note', 'no note')})")
            failures += 1
        elif not row.get("same_file_as_payload"):
            print(f"  FAIL {face}: distinct from payload — a forwarder/copy where"
                  " the hardlink should be; repair with `niu --relink-shells --force`"
                  " (shims are sanctioned only on layouts without hardlink support)")
            failures += 1
        else:
            print(f"  PASS {face}: same file as payload (nlink={row.get('hardlinks')})")
    if failures:
        print("gate: FAIL — a declared face is missing, broken, or no longer the"
              " payload file (the degenerated-forwarder case this gate exists for)")
        return 1
    print("gate: PASS — every declared face is the payload file itself")
    return 0


if __name__ == "__main__":
    sys.exit(main())
