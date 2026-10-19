#!/usr/bin/env python3
r"""Install/uninstall cycle probe for the installer's slot contract.

niubash#398 asked whether the niu installer put the unowned .NET shim into
`C:\Program Files\Git\bin\bash.exe` and what happens to that slot across an
install/uninstall cycle. The ruling (PR #414): the installer never elevates
(`PrivilegesRequired=lowest`) and does NOT claim the slot — nothing in the
install tree may touch it. That ruling is pinned to the repository by
`tests/installer_surface.rs` (the static leg: no foreign path, every write
surface confined to {app}, HKCU `Environment\Path` the only registry value).
This script is the runtime leg, at the weight the discipline allows on a
developer machine: no real system install, no Inno compile — the installer's
executable steps are replayed against a pure user-directory sandbox and every
slot that exists outside the sandbox root is checked byte for byte.

What is replayed, stage by stage:

  1. sandbox   — `{temp}/niubash-slot-probe-<pid>/`:
                   `app\`                        the simulated {app}: payload
                                                 `niu.exe` plus the shipped
                                                 243 KB forwarder faces (the
                                                 1.4.0 layout an upgrade finds)
                   `foreign\Program Files\Git\bin\bash.exe`
                                                 a decoy for the admin-walled
                                                 slot, seeded from the real
                                                 slot's bytes when it exists
  2. before    — `-c true` timing (min of N) of the payload and the forwarder
                 faces, plus one instrumented call whose process chain is
                 walked (the procmon substitute: forwarder = 2 processes
                 between probe and engine, hardlink = 1)
  3. install   — the [Run] step niubash#377 owns: `niu --relink-shells --force`
  4. verify    — every face is the SAME FILE as the payload; no parked
                 `.niu-old-*` name left; the decoy and the real
                 `C:\Program Files\Git\bin\bash.exe` (read-only!) are
                 byte-identical to the snapshot taken before anything ran
  5. after     — the same timing table against the hardlinked faces
  6. uninstall — the {app} tree is removed (Inno's uninstall log replay) and
                 the checks of stage 4 are repeated: the slot bytes survive a
                 full install/uninstall cycle unchanged, and no face name is
                 left outside the removed tree

Inno steps that need the real installer binary (the [Files] extraction, the
`wpm links rebuild` invocation) are outside this probe's reach; the static
guard test covers their write surface. What this probe proves at runtime is
the fact #398 turns on: nothing the installer's own engine steps do — relink,
park, replace, sweep, uninstall — ever escapes the app tree, so the Git Bash
slot is byte-identical before and after.

Usage:

    python scripts/installer-slot-probe.py --payload target/release/niu.exe
    python scripts/installer-slot-probe.py --payload niu.exe --n 30 --json report.json

Exit code 0 = every stage held; 1 = a check failed; 2 = inputs unusable.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

# niubash#398: the convention slot. Read-only for this probe — it is hashed,
# never opened for writing.
SLOT_CANDIDATES = (
    r"C:\Program Files\Git\bin\bash.exe",
    r"D:\Git\bin\bash.exe",
)

# The face layout niubash#397 relinks, relative to the installation root.
FACES = (
    (r"bin", "bash.exe"),
    (r"bin", "sh.exe"),
    (r"winuxcmd\bin", "bash.exe"),
    (r"winuxcmd\bin", "sh.exe"),
    (r"winuxcmd\usr\bin", "bash.exe"),
    (r"winuxcmd\usr\bin", "sh.exe"),
)

# The instrument for the process-chain evidence: PowerShell walks from its own
# PID up to this probe's PID, printing "pid name" per hop. A file, not an
# inline -Command, so no quoting has to survive two layers of parsing.
CHAIN_PS1 = r"""param([int]$StopPid)
$id = $PID
for ($hop = 0; $hop -lt 16; $hop++) {
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$id"
    if (-not $proc) { break }
    ("{0} {1}" -f $proc.ProcessId, $proc.Name)
    if ($id -eq $StopPid) { break }
    $id = $proc.ParentProcessId
}
"""


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def file_id(path):
    stat = os.stat(path)
    return (stat.st_dev, stat.st_ino)


class Probe:
    def __init__(self, payload, forwarder, n, warmup, keep):
        self.payload = os.path.abspath(payload)
        self.forwarder = os.path.abspath(forwarder) if forwarder else None
        self.n = n
        self.warmup = warmup
        self.keep = keep
        self.failures = []
        self.report = {
            "schema": "niubash:installer-slot-probe@1.0.0",
            "generated": _dt.datetime.now().isoformat(timespec="seconds"),
            "host": platform.node(),
            "os": platform.platform(),
            "payload": self.payload,
            "forwarder": self.forwarder,
            "stages": [],
        }

    def check(self, ok, message):
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {message}")
        if not ok:
            self.failures.append(message)
        return ok

    def stage(self, name):
        print(f"\n== {name} ==")
        self.report["stages"].append({"stage": name})

    # --- sandbox -----------------------------------------------------------

    def build_sandbox(self):
        root = tempfile.mkdtemp(prefix="niubash-slot-probe-")
        self.root = root
        self.app = os.path.join(root, "app")
        self.foreign = os.path.join(root, "foreign", "Program Files", "Git", "bin")
        os.makedirs(self.foreign)

        # The simulated {app}: payload at the root, faces seeded from the
        # forwarding shim when one exists (the shipped 1.4.0 layout an
        # upgrade walk finds), else from the payload itself.
        os.makedirs(os.path.join(self.app, "bin"), exist_ok=True)
        for directory, _ in FACES[2:]:
            os.makedirs(os.path.join(self.app, directory), exist_ok=True)
        shutil.copy2(self.payload, os.path.join(self.app, "niu.exe"))
        seed = self.forwarder or self.payload
        for directory, name in FACES:
            shutil.copy2(seed, os.path.join(self.app, directory, name))

        # The decoy slot, outside the app tree: seeded from the real slot's
        # bytes when the machine has one, else a synthetic stand-in.
        self.real_slot = next((p for p in SLOT_CANDIDATES if os.path.isfile(p)), None)
        seed = self.real_slot or os.path.join(self.app, "winuxcmd", "bin", "bash.exe")
        self.decoy = os.path.join(self.foreign, "bash.exe")
        shutil.copy2(seed, self.decoy)

        self.snapshot = {
            "decoy": sha256(self.decoy),
            "real_slot": sha256(self.real_slot) if self.real_slot else None,
        }
        print(f"sandbox: {root}")
        print(f"decoy slot: {self.decoy} ({os.path.getsize(self.decoy):,} B)")
        if self.real_slot:
            print(
                f"real slot (read-only): {self.real_slot}"
                f" ({os.path.getsize(self.real_slot):,} B)"
            )
        else:
            print("real slot: not present on this machine (decoy only)")

    def sweep(self):
        if self.keep:
            print(f"\nsandbox kept: {self.root}")
            return
        shutil.rmtree(self.root, ignore_errors=True)

    # --- checks ------------------------------------------------------------

    def slots_unchanged(self, when):
        ok = True
        decoy_now = sha256(self.decoy)
        ok &= self.check(
            decoy_now == self.snapshot["decoy"],
            f"decoy slot byte-identical {when}",
        )
        if self.real_slot:
            ok &= self.check(
                sha256(self.real_slot) == self.snapshot["real_slot"],
                f"real Git Bash slot byte-identical {when} (read-only probe)",
            )
        return ok

    def faces_same_file(self, when):
        payload_id = file_id(os.path.join(self.app, "niu.exe"))
        ok = True
        for directory, name in FACES:
            face = os.path.join(self.app, directory, name)
            if not os.path.isfile(face):
                ok &= self.check(False, f"face missing {when}: {directory}\\{name}")
                continue
            same = file_id(face) == payload_id
            nlink = os.stat(face).st_nlink
            ok &= self.check(
                same,
                f"face is the payload file {when}: {directory}\\{name} (nlink={nlink})",
            )
        return ok

    def no_parked_names(self):
        strays = []
        for directory, _ in FACES:
            parent = os.path.join(self.app, directory)
            if os.path.isdir(parent):
                strays += [
                    entry for entry in os.listdir(parent) if ".niu-old-" in entry
                ]
        return self.check(not strays, f"no parked .niu-old-* names left: {strays or 'none'}")

    def no_faces_outside_app(self):
        """After the uninstall replay, no face name survives outside {app}.

        The decoy slot is the sanctioned exception — it simulates the foreign
        file nobody's installer owns, and it must still be there.
        """
        strays = []
        for base, _dirs, files in os.walk(self.root):
            for name in files:
                path = os.path.join(base, name)
                if os.path.normcase(path) == os.path.normcase(self.decoy):
                    continue
                if name.lower() in ("bash.exe", "sh.exe"):
                    strays.append(path)
        return self.check(
            not strays,
            f"no face name left outside the app tree after uninstall: {strays or 'none'}",
        )

    # --- timing & process evidence (niubash#377) ---------------------------

    def timed_min(self, path):
        samples = []
        for index in range(self.n + self.warmup):
            start = time.perf_counter()
            try:
                subprocess.run(
                    [path, "-c", "true"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=60,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                return None
            if index >= self.warmup:
                samples.append((time.perf_counter() - start) * 1000.0)
        if not samples:
            return None
        return {"min": min(samples), "avg": statistics.mean(samples)}

    def timing_table(self, label):
        print(f"\n  `{label}` — `face -c true`, min of {self.n} (warmup {self.warmup}):")
        rows = []
        payload_row = ("niu.exe (payload)", os.path.join(self.app, "niu.exe"))
        candidates = [payload_row] + [
            (f"{directory}\\{name}", os.path.join(self.app, directory, name))
            for directory, name in FACES
        ]
        for label_face, path in candidates:
            if not os.path.isfile(path):
                print(f"    {label_face:28s}  n/a (missing)")
                continue
            timing = self.timed_min(path)
            if timing is None:
                print(f"    {label_face:28s}  n/a (a run failed — face not recorded)")
                rows.append({"face": label_face, "ok": False})
                continue
            same = "same-file-as-payload" if self._is_payload(path) else "distinct-from-payload"
            print(f"    {label_face:28s}  min {timing['min']:6.1f} ms   avg {timing['avg']:6.1f} ms   [{same}]")
            rows.append({
                "face": label_face, "ok": True,
                "min_ms": round(timing["min"], 2), "avg_ms": round(timing["avg"], 2),
                "same_file_as_payload": self._is_payload(path),
            })
        self.report.setdefault("timing", {})[label] = rows

    def _is_payload(self, path):
        try:
            return file_id(path) == file_id(os.path.join(self.app, "niu.exe"))
        except OSError:
            return False

    def process_chain(self, label, path):
        """Instrumented call: how many processes sit between probe and engine.

        The face spawns PowerShell (`-c` payload is a plain file invocation,
        nothing to quote); PowerShell walks its own ancestor chain up to this
        probe. Entries strictly between this probe and PowerShell are the
        processes one `face -c` call creates beyond the leaf instrument.
        """
        ps1 = os.path.join(self.root, "chain.ps1")
        with open(ps1, "w", encoding="ascii") as handle:
            handle.write(CHAIN_PS1)
        argv = [path, "-c",
                f'powershell -NoProfile -ExecutionPolicy Bypass -File "{ps1}" {os.getpid()}']
        try:
            out = subprocess.run(
                argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=120, text=True, check=False,
            ).stdout or ""
        except (OSError, subprocess.TimeoutExpired) as error:
            self.check(False, f"process chain ({label}): instrument failed: {error}")
            return None
        chain = []
        for line in out.splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) != 2 or not parts[0].isdigit():
                continue
            chain.append((int(parts[0]), parts[1]))
            if int(parts[0]) == os.getpid():
                break
        if not any(pid == os.getpid() for pid, _ in chain):
            self.check(False, f"process chain ({label}): walk never reached this probe: {out[:120]!r}")
            return None
        # Hop count between the probe (root) and PowerShell (leaf instrument):
        # forwarder state = forwarder + engine (2), hardlink state = engine (1).
        below = len(chain) - 1  # entries above the leaf, including this probe
        intermediates = below - 1
        pretty = " <- ".join(name for _, name in chain)
        print(f"    chain ({label}): {pretty}")
        self.report.setdefault("process_chain", {})[label] = {
            "chain": [f"{pid} {name}" for pid, name in chain],
            "intermediates": intermediates,
        }
        return intermediates


def find_forwarder(payload):
    """The shipped forwarding shim to seed the sandbox with, or None."""
    local = os.environ.get("LOCALAPPDATA")
    candidate = None
    if local:
        guess = os.path.join(local, "Programs", "Niubash", "winuxcmd", "bin", "bash.exe")
        if os.path.isfile(guess):
            candidate = guess
    if candidate and os.path.getsize(candidate) == os.path.getsize(payload):
        return None  # the live install is already hardlinked; nothing to seed
    return candidate


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--payload", required=True,
                        help="the niu.exe build to install in the sandbox "
                             "(e.g. target/release/niu.exe)")
    parser.add_argument("--forwarder",
                        help="the forwarding shim to seed the 'before' state "
                             "(default: the live install's winuxcmd\\bin\\bash.exe "
                             "when it is distinct from the payload)")
    parser.add_argument("--n", type=int, default=20,
                        help="timed samples per face, min reported (default 20)")
    parser.add_argument("--warmup", type=int, default=3, help="untimed warmup runs")
    parser.add_argument("--keep", action="store_true", help="keep the sandbox for inspection")
    parser.add_argument("--json", help="write the report as JSON to this path")
    args = parser.parse_args()

    if not os.path.isfile(args.payload):
        print(f"payload not found: {args.payload}", file=sys.stderr)
        return 2
    forwarder = args.forwarder or find_forwarder(args.payload)
    if not forwarder:
        if args.forwarder:
            print(f"forwarder not found: {args.forwarder}", file=sys.stderr)
            return 2
        print("note: no distinct forwarder found; the 'before' state will be "
              "seeded from the payload (timing A/B loses the forwarder leg, "
              "the slot-identity checks stay valid)", file=sys.stderr)

    probe = Probe(args.payload, forwarder, args.n, args.warmup, args.keep)
    try:
        probe.stage("sandbox")
        probe.build_sandbox()

        probe.stage("before: forwarder faces (niubash#377 quantification)")
        probe.timing_table("before")
        if forwarder:
            forwarder_intermediates = probe.process_chain(
                "before (forwarder)", os.path.join(probe.app, "winuxcmd", "bin", "sh.exe"))
            if forwarder_intermediates is not None:
                probe.check(
                    forwarder_intermediates == 2,
                    f"forwarder call costs 2 processes below the probe "
                    f"(measured {forwarder_intermediates})",
                )
        probe.slots_unchanged("at baseline")

        probe.stage("install replay: [Run] niu --relink-shells --force --quiet")
        relink = subprocess.run(
            [os.path.join(probe.app, "niu.exe"), "--relink-shells", "--force", "--quiet"],
            cwd=probe.app, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=300, check=False,
        )
        probe.check(relink.returncode == 0,
                    f"--relink-shells exited 0 (rc={relink.returncode} {relink.stderr.strip()[:120]})")

        probe.stage("verify: faces are the payload; slots byte-identical")
        probe.faces_same_file("after install")
        probe.no_parked_names()
        probe.slots_unchanged("after install")

        probe.stage("after: hardlinked faces (niubash#377 quantification)")
        probe.timing_table("after")
        link_intermediates = probe.process_chain(
            "after (hardlink)", os.path.join(probe.app, "winuxcmd", "bin", "sh.exe"))
        if link_intermediates is not None:
            probe.check(
                link_intermediates == 1,
                f"hardlink call costs 1 process below the probe (measured {link_intermediates})",
            )

        probe.stage("uninstall replay: {app} tree removed (Inno log replay)")
        shutil.rmtree(probe.app)
        probe.check(not os.path.exists(probe.app), "app tree removed")
        probe.no_faces_outside_app()
        probe.slots_unchanged("after uninstall")
    finally:
        if args.json:
            with open(args.json, "w", encoding="utf-8") as handle:
                json.dump(probe.report, handle, indent=2)
            print(f"\nreport: {args.json}")
        if not args.keep:
            probe.sweep()

    print()
    if probe.failures:
        print(f"probe: FAIL — {len(probe.failures)} check(s):")
        for failure in probe.failures:
            print(f"  - {failure}")
        return 1
    print("probe: PASS — install/relink/uninstall cycle leaves every slot "
          "byte-identical; the Git Bash slot is never touched (niubash#398)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
