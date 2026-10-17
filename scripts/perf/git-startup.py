#!/usr/bin/env python3
"""Per-invocation startup cost of `git`, grouped by caller.

This measures the fixed cost of *starting* git, which is what a prompt
render, an editor polling git, or a script loop pays on every call. Long
operations (clone, fetch, pack) absorb that fixed cost, so this is not a
throughput benchmark.

Numbers from different callers are not comparable. The caller's
process-creation cost is usually the largest term:

    per-call cost  ~=  caller spawn/fork cost  +  git's own init cost

niu-git only shrinks the second term. Inside Git Bash the first term is
MSYS's fork emulation, which swamps the difference, so swapping only the
git binary there shows nothing.

Usage:

    python scripts/perf/git-startup.py
    python scripts/perf/git-startup.py --repo D:/repo/rubash
    python scripts/perf/git-startup.py --git-bash D:/Git/bin/bash.exe --json

Every shell row is verified before it is recorded: if the shell cannot run
the command (wrong path dialect, missing binary), the cell says "n/a" with a
note instead of a fast failure being reported as a fast measurement.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

DEVNULL = subprocess.DEVNULL

# D:\Git is a plain Git for Windows install. C:\Program Files\Git is
# routinely replaced by a winuxcmd/MSYS bundle whose bash reports
# MSYSTEM empty and whose `git` is not Git for Windows, so it is only a
# fallback and never trusted without the identity check below.
GIT_BASH_CANDIDATES = (
    r"D:\Git\bin\bash.exe",
    r"C:\Program Files\Git\bin\bash.exe",
)
GFW_GIT_CANDIDATES = (
    r"D:\Git\mingw64\bin\git.exe",
    r"C:\Program Files\Git\mingw64\bin\git.exe",
)
GFW_CMD_GIT_CANDIDATES = (
    r"D:\Git\cmd\git.exe",
    r"C:\Program Files\Git\cmd\git.exe",
)
GIT_PROMPT_SH_CANDIDATES = (
    "/etc/profile.d/git-prompt.sh",
    "/mingw64/share/git/completion/git-prompt.sh",
    "/usr/share/git-core/contrib/completion/git-prompt.sh",
)


def first_existing(paths):
    for p in paths:
        if p and os.path.exists(p):
            return p
    return None


def to_posix(path):
    p = path.replace("\\", "/")
    if len(p) > 1 and p[1] == ":":
        return "/" + p[0].lower() + p[2:]
    return p


def run(argv, timeout=180, text=False):
    return subprocess.run(argv, stdout=DEVNULL if not text else subprocess.PIPE,
                          stderr=DEVNULL if not text else subprocess.PIPE,
                          timeout=timeout, text=text)


def timed(argv, n, warmup=2, timeout=180):
    """Per-call milliseconds for `argv`. Returns dict or None."""
    samples = []
    for i in range(n + warmup):
        t0 = time.perf_counter()
        try:
            run(argv, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired):
            return None
        t1 = time.perf_counter()
        if i >= warmup:
            samples.append((t1 - t0) * 1000.0)
    if not samples:
        return None
    return {"avg": statistics.mean(samples), "min": min(samples)}


def shell_startup(shell, args, iterations=7):
    return timed([shell] + args, iterations, warmup=2)


def shell_runs(shell, args, inner):
    """True when `inner` actually executes something that prints a version."""
    try:
        out = run([shell] + args + [inner], text=True).stdout or ""
    except (OSError, subprocess.TimeoutExpired):
        return False
    return "git version" in out


def per_call_in_shell(shell, args, inner, n, iterations=7, probe=None):
    """Per-call cost of `inner` inside one long-lived shell invocation.

    The shell's own startup is measured separately and subtracted. A
    literal loop list is used so nothing but the loop body spawns.
    `probe` overrides the readiness check for commands that print nothing.
    """
    if not shell_runs(shell, args, probe or inner):
        return None
    base = shell_startup(shell, args, iterations)
    if base is None:
        return None
    items = " ".join(str(i) for i in range(n))
    loop = "for i in %s; do %s; done" % (items, inner)
    total = timed([shell] + args + [loop], iterations, warmup=1)
    if total is None:
        return None
    per_call = total["avg"] - base["avg"]
    if per_call <= 0:
        return None
    return per_call / n


def detect_git_bash(explicit):
    """Return (path, identity) for a real Git for Windows bash."""
    candidates = [explicit] if explicit else list(GIT_BASH_CANDIDATES) + [shutil.which("bash")]
    fallback = None
    for cand in candidates:
        if not cand or not os.path.exists(cand):
            continue
        try:
            out = run([cand, "-c", "echo ${MSYSTEM:-none}; command -v git"], text=True).stdout
        except (OSError, subprocess.TimeoutExpired):
            continue
        parts = out.split()
        msystem = parts[0] if parts else "none"
        gitpath = parts[1] if len(parts) > 1 else "none"
        identity = "MSYSTEM=%s git=%s" % (msystem, gitpath)
        if msystem.startswith("MINGW") or msystem.startswith("MSYS"):
            return cand, identity
        if fallback is None:
            fallback = (cand, identity)
    if fallback is not None:
        return fallback
    return None, "not found"


def count_git_children_per_prompt(bash, repo_posix, prompt_sh):
    """How many git child processes one Git Bash prompt render spawns."""
    real_git = first_existing(GFW_GIT_CANDIDATES) or shutil.which("git")
    if not real_git:
        return None
    shim = tempfile.mkdtemp(prefix="gitstartup-")
    log = os.path.join(shim, "calls.log")
    shim_git = os.path.join(shim, "git")
    with open(shim_git, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("#!/usr/bin/env bash\necho \"$*\" >> \"%s\"\nexec \"%s\" \"$@\"\n"
                 % (to_posix(log), to_posix(real_git)))
    os.chmod(shim_git, 0o755)
    script = ('cd "%s"; export PATH="%s:$PATH"; export GIT_PS1_SHOWDIRTYSTATE=1; '
              'source "%s"; __git_ps1 >/dev/null'
              % (repo_posix, to_posix(shim), prompt_sh))
    try:
        run([bash, "-c", script])
        with open(log, encoding="utf-8") as fh:
            calls = [line.strip() for line in fh if line.strip()]
    except (OSError, subprocess.TimeoutExpired):
        return None
    finally:
        shutil.rmtree(shim, ignore_errors=True)
    return calls


def prompt_render_cost(bash, repo_posix, prompt_sh, n, iterations=5):
    """Milliseconds for one Git Bash prompt render, git children included."""
    inner = ('cd "%s"; export GIT_PS1_SHOWDIRTYSTATE=1; source "%s"; __git_ps1 >/dev/null'
             % (repo_posix, prompt_sh))
    probe = ('source "%s"; type __git_ps1 >/dev/null 2>&1 && echo "git version"' % prompt_sh)
    return per_call_in_shell(bash, ["-c"], inner, n, iterations, probe=probe)


def fmt(stat):
    if stat is None:
        return "n/a"
    avg = stat.get("avg") if isinstance(stat, dict) else stat
    return "n/a" if avg is None else "%.1f" % avg


def render_table(result):
    names = result["binary_rows"]
    lines = ["| caller | " + " | ".join(names) + " |",
             "|---" * (len(names) + 1) + "|"]
    for key, label in (
        ("native_spawn", "native spawn (GUI app, editor, script)"),
        ("via_cmd", "`cmd.exe /c`"),
        ("warm_niu", "warm `niu -c` (per call)"),
        ("warm_gitbash", "warm Git Bash (`bash -c`, per call)"),
    ):
        row = result["rows"].get(key, {})
        lines.append("| %s | %s |" % (label, " | ".join(fmt(row.get(n)) for n in names)))
    lines += ["", "| shell / prompt | ms |", "|---|---|"]
    for label, stat in result["rows"].get("new_terminal", {}).items():
        lines.append("| %s | %s |" % (label, fmt(stat)))
    for label, stat in result["rows"].get("work", {}).items():
        lines.append("| %s | %s |" % (label, fmt(stat)))
    if result.get("prompt_children"):
        lines += ["", "Git children per Git Bash prompt render: %d" % len(result["prompt_children"])]
        lines += ["  git %s" % c for c in result["prompt_children"]]
    if result.get("notes"):
        lines += ["", "Notes:"] + ["  %s" % n for n in result["notes"]]
    p = result["provenance"]
    lines += ["", "Measured %s on %s, python %s." % (p["date"], p["os"], p["python"]),
              "Git Bash: %s (%s)" % (p["git_bash"], p["git_bash_identity"]),
              "niu: %s" % p["niu"], "Repo: %s" % p["repo"]]
    for name, path in p["binaries"].items():
        lines.append("%s: %s" % (name, path))
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=None,
                    help="repository for the status and prompt rows (default: cwd)")
    ap.add_argument("--n", type=int, default=20, help="iterations for native rows")
    ap.add_argument("--shell-n", type=int, default=10,
                    help="git calls per measurement inside a long-lived shell")
    ap.add_argument("--git-bash", default=None, help="path to a real Git for Windows bash.exe")
    ap.add_argument("--niu", default=None, help="path to niu.exe")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    repo = os.path.abspath(args.repo or os.getcwd())
    repo_posix = to_posix(repo)

    niu = args.niu or shutil.which("niu") or shutil.which("niu.exe")
    git = shutil.which("git")
    gfw = first_existing(GFW_GIT_CANDIDATES)
    gfw_cmd = first_existing(GFW_CMD_GIT_CANDIDATES)
    bash, bash_identity = detect_git_bash(args.git_bash)
    notes = []
    if bash and not bash_identity.startswith("MSYSTEM=MINGW"):
        notes.append("the bash found is not a Git for Windows install (%s); "
                     "pass --git-bash pointing at D:\\Git\\bin\\bash.exe" % bash_identity)

    binaries = []
    if git:
        binaries.append(("niu-git (git on PATH)", git))
    if gfw:
        binaries.append(("Git for Windows", gfw))
    if gfw_cmd and gfw_cmd != gfw:
        binaries.append(("Git for Windows (cmd launcher)", gfw_cmd))

    result = {"repo": repo, "binary_rows": [n for n, _ in binaries],
              "notes": notes, "prompt_children": None}

    result["rows"] = {
        "native_spawn": {n: timed([p, "--version"], args.n) for n, p in binaries},
        "warm_niu": {}, "warm_gitbash": {}, "new_terminal": {}, "work": {},
    }
    comspec = os.environ.get("COMSPEC") or r"C:\Windows\System32\cmd.exe"
    result["rows"]["via_cmd"] = {n: timed([comspec, "/c", p, "--version"],
                                          max(args.n // 2, 5)) for n, p in binaries}
    if niu:
        result["rows"]["warm_niu"] = {
            n: per_call_in_shell(niu, ["-c"], "%s --version" % p, args.shell_n)
            for n, p in binaries}
    if bash:
        result["rows"]["warm_gitbash"] = {
            n: per_call_in_shell(bash, ["-c"], "%s --version" % to_posix(p), args.shell_n)
            for n, p in binaries}
    if niu:
        result["rows"]["new_terminal"]["niu -c exit"] = shell_startup(niu, ["-c", "exit"])
    if bash:
        result["rows"]["new_terminal"]["Git Bash -c exit (non-login)"] = shell_startup(bash, ["-c", "exit"])
        result["rows"]["new_terminal"]["Git Bash -lc exit (login)"] = shell_startup(bash, ["-lc", "exit"])

    if bash and os.path.isdir(repo):
        prompt_sh = None
        for cand in GIT_PROMPT_SH_CANDIDATES:
            if shell_runs(bash, ["-c"], '[ -f %s ] && echo git version' % cand):
                prompt_sh = cand
                break
        if prompt_sh:
            cost = prompt_render_cost(bash, repo_posix, prompt_sh, max(args.shell_n // 2, 5))
            result["rows"]["work"]["Git Bash prompt render (`__git_ps1`, per render)"] = cost
            try:
                result["prompt_children"] = count_git_children_per_prompt(bash, repo_posix, prompt_sh)
            except Exception:  # noqa: BLE001
                result["prompt_children"] = None
    if os.path.isdir(repo):
        for n, p in binaries:
            stat = timed([p, "status", "--porcelain"], 5)
            if stat:
                result["rows"]["work"]["%s, `status --porcelain` (per call)" % n] = stat

    result["provenance"] = {
        "date": _dt.date.today().isoformat(),
        "os": platform.platform(),
        "python": platform.python_version(),
        "repo": repo,
        "niu": niu,
        "git_bash": bash,
        "git_bash_identity": bash_identity,
        "binaries": {n: p for n, p in binaries},
    }

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(render_table(result))
    if not binaries and not niu and not bash:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
