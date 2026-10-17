# Coding agents on Windows, and the shell they run

Your agent does not run commands in *your* shell. It picks one, and on Windows
that pick is made by a probe you can usually influence. This page lists the
mechanisms, the agent-by-agent behaviour, and what niu has to provide.

Verified against the machine in `docs/` (2026-10-16): niu 1.4.0, niu-git
2.55.0.5 (flat layout), Git for Windows 2.50.1 at `D:\Git`, a WSL2 distro
installed, opencode, codex (forked), ZCode. Rows that were read from shipped
code or vendor docs say so; rows that come from upstream issue threads are
marked as reported.

## The one thing that usually just works

Launch the agent **from a niu session**:

```
niu
$ opencode          # or codex, or any CLI agent
```

niu exports `SHELL=<path to niu.exe>`. Agents that follow the POSIX convention
read `$SHELL` and use it. That is how the agent driving this document runs: its
process tree is `powershell.exe <- niu.exe <- opencode.exe`, and opencode's
shell selector is literally

```js
function o$(e, r) { return Xc(process.env.SHELL, void 0, r, e) ?? n$(e); }
//                                                ^ which("bash") ?? "/bin/sh"
```

opencode has no niu-specific code at all (zero occurrences of `niu` in the
binary) and no config field for the shell; `$SHELL` is the whole mechanism.

The catch: `$SHELL` is **not persisted** by anything. It exists only in
processes descended from a niu session. Start the agent from the Start menu or
Explorer instead and `$SHELL` is empty, so the same agent falls through to
`which("bash")`, which on a machine with WSL resolves to
`C:\Windows\System32\bash.exe` and runs your commands in **Linux**.

## The four mechanisms

| # | Mechanism | Reads | Covered by launching from niu |
|---|---|---|---|
| 1 | `$SHELL` (POSIX convention) | the environment | yes |
| 2 | Explicit per-agent setting | config file / CLI flag | n/a, set it once |
| 3 | Git for Windows probe | hardcoded paths, or derived from `git` | no |
| 4 | Bare name, a table, or PowerShell by default | `PATH`, `ComSpec`, `PATHEXT` | partly |

Mechanism 3 is why the install places a `bash.exe` at the Git for Windows
location; mechanism 4 is why a bare `bash` cannot be redirected from the user
`PATH` alone.

### What `winuxcmd\bin\bash.exe` actually is

It is not niu. It is a launcher that starts `niu.exe` as a child process, so an
agent pointed at it runs `agent -> bash.exe -> niu.exe`. Two consequences, both
filed:

- The extra process costs about 17.6 ms per `-c` invocation (niubash#377). One
  process hop is a rounding error for an interactive session and a real tax for
  an agent that shells out per command.
- Because the child is an ordinary console program, a console-less parent used
  to get a **visible console window** per command. That is niubash#379: Claude
  Code pointed at this launcher flashed a window for every command. Fixed in
  rubash#668 (`eaa6138`) by setting `CREATE_NO_WINDOW` when all three standard
  streams are redirected; it reaches users with the next rubash bump and release
  build.

Both disappear if `bash.exe` becomes an `argv[0]` dispatch name for `niu.exe`
itself rather than a launcher, which is the same change that cheapens the hop
(niubash#377).

## Which shell is my agent actually using?

Ask the agent to run this and compare the signatures:

```
echo "SHELL=$SHELL"; uname -s; uname -a
```

| What you see | Shell | Notes |
|---|---|---|
| `uname -s` = `MSYS_NT-...`, `uname -a` ends `x86_64 Msys` | **niu** (winuxcmd shim) | what you want; `$SHELL` points at `niu.exe` |
| `uname -s` = `MINGW64_NT-...`, `MSYSTEM=MINGW64` | Git Bash | Git for Windows' own runtime |
| `uname -s` = `Linux` | **WSL** | the surprising one; the agent reached `System32\bash.exe` |
| `uname` not found, `$SHELL` empty | PowerShell or cmd | check `$PSVersionTable.PSVersion` vs `%COMSPEC%` |

The same check appears in Cursor's own support threads, where the fix is
verified by `uname -a` ending in `MINGW64_NT-...` instead of
`microsoft-standard-WSL2`.

Interpreting the result:

- `Linux` means the agent fell through to `which("bash")`. Fix the mechanism
  that agent uses, below.
- `MINGW64_NT` means it found Git Bash. If you want niu instead, set the
  agent's explicit shell (mechanism 2) or the `$SHELL` variable.
- Empty `$SHELL` with PowerShell means the agent defaults to PowerShell, which
  is safe but not bash-compatible.

## Agent reference

### opencode

- **Mechanism:** `$SHELL` first, then `which("bash")`, then `/bin/sh`. Its
  `gitbash` mode is implemented as "use `$SHELL`". No config field; the V2 CLI
  settings and core config have no shell key.
- **Set it to niu:** launch from niu, or persist `SHELL`. Nothing else is read.
- **Failure mode:** falls to `which("bash")` -> `System32\bash.exe` -> **Linux**.
- **Separate code path:** the self-updater resolves
  `git --exec-path` up three levels plus `bin\bash.exe` and refuses to update
  with "Git Bash is required to update OpenCode" when that file is missing. It
  does not consult `$SHELL`. See niubash#376.
- **Source:** shipped binary, read locally.

### Claude Code

- **Mechanism:** vendor docs. Without `CLAUDE_CODE_GIT_BASH_PATH` it looks for
  `bash.exe` in (1) `C:\Program Files\Git` and `C:\Program Files (x86)\Git`,
  then (2) the `git` on `PATH`, using `bin\bash.exe` from that installation. It
  skips a `git` sitting in the launch directory or below it, or under
  `node_modules`/`.venv`/`env`.
- **Set it to niu:** either works today. Probe (1) hits the `bash.exe` the
  installer places in `C:\Program Files\Git\bin`, or set
  `CLAUDE_CODE_GIT_BASH_PATH` in `settings.json`. Both point at the launcher
  described above, which is why this configuration is the one that exposed
  niubash#379 (a visible console window per command, fixed in rubash#668). When
  updating, verify the flash is gone and interactive use is unchanged.
- **Trap:** the variable only accepts a file named `bash.exe`, `sh.exe`, `bash`
  or `sh`. Point it at `git-bash.exe` and it is ignored with a warning. Pointing
  it at `niu.exe` is likewise ignored, so the launcher hop cannot be skipped by
  naming niu directly; that is another reason the `argv[0]` dispatch in
  niubash#377 matters for this agent specifically.
- **Failure mode:** since v2.1.120, absent Git Bash means **PowerShell**, not
  Linux.
- **Source:** vendor documentation.

### Codex

- **Mechanism:** a shell table with per-shell candidate paths. Windows entries
  include `bash -> /bin/bash`, `sh -> /bin/sh`, `pwsh`, `powershell`, `cmd`.
- **Set it to niu:** this fork adds `ShellType::Niu` with
  `C:\Program Files\Niubash\niu.exe`, `C:\Program Files (x86)\Niubash\niu.exe`,
  `%LOCALAPPDATA%\Programs\Niubash\niu.exe` and a `niu` lookup on `PATH`; the
  exec policy also whitelists `niu.exe -C`, `-c`, `-lc`, `--repl-command`. This
  is the shape the other agents need.
- **Source:** `codex-rs/shell-command/src/shell_detect.rs`, read locally.

### Cursor

Two products, both currently unhelpful:

- **IDE agent:** ignores `terminal.integrated.defaultProfile.windows` and
  `terminal.integrated.automationProfile.windows` and runs PowerShell; the
  workaround is enabling the *Legacy Terminal Tool* setting. Some versions pick
  WSL bash instead, which is why their threads recommend nulling the PowerShell
  and WSL profiles with `terminal.integrated.useWslProfiles: false`.
- **Agent CLI:** hardcoded to PowerShell. No `--shell` flag, no shell field in
  `~/.cursor/cli-config.json`, and `$SHELL`/`$COMSPEC` ignored. The shipped
  `detectShellType()` puts `isWindows && (commandExists("pwsh") ||
  commandExists("powershell"))` in the same `||` chain as the string match, so
  on Windows the PowerShell arm fires regardless of the hint and every later
  branch is unreachable. A working `NaiveTerminalExecutor` exists but nothing
  routes to it.
- **Set it to niu:** not possible today through configuration. Reported
  upstream; nothing to do on the niu side until they expose a shell path.
- **Source:** upstream issue threads quoting the shipped detection code.

### VS Code, and everything built on it

Cursor aside, the VS Code family is one shared probe:

```js
var HX = ["C:\\Program Files\\Git\\bin\\bash.exe",
          "C:\\Program Files (x86)\\Git\\bin\\bash.exe"];
// then: which("git") -> dirname(git) + "/../bin/bash.exe" and "/../../bin/bash.exe"
```

- **Applies to:** VS Code (Copilot agent), Cursor, Windsurf, Trae, ZCode, and
  extensions that use the editor's terminal or automation profile (Cline, Roo
  Code, Kilo Code, Continue).
- **Set it to niu:** probe 1 hits the installed `C:\Program Files\Git\bin\bash.exe`.
  For explicit control, point the profile at niu:
  `terminal.integrated.automationProfile.windows` = `{"path": "<niubash>\\niu.exe", "args": []}`.
  Agents that shell out through the automation profile inherit it.
- **Failure mode:** PowerShell, or cmd if neither is configured.
- **Source:** ZCode `app.asar` read locally; the rest is the same lineage.

### Gemini CLI

- **Mechanism:** `getShellConfiguration()` returns PowerShell unconditionally on
  Windows. `ComSpec` is only consulted to decide between `pwsh` and
  `powershell.exe`; there is no bash branch.
- **Set it to niu:** not possible in released code. Two upstream proposals
  exist: detect MSYS via `process.env['MSYSTEM']` and route to `bash -c`
  (issue #26567), and prefer Git Bash by searching `PATH` for a `bash.exe`
  whose path contains `git` (PR #18367).
- **Failure mode:** PowerShell, and on locked-down machines an execution-policy
  error that stops the agent entirely.
- **Source:** upstream source and issue threads.

### DSH (DeepSeek Harness)

- **Mechanism:** plugins. `dsh-niu` ships a profile bundle that enables
  `niu-sandbox` and `tool-bash`, disables the PowerShell shell/tool rows, and
  adds a settings card. A third party ships `dsh-niubash-only` in the same
  spirit.
- **Set it to niu:** `dsh plugin --profile web add @cmx666/dsh-niu-bundle@<version>`.
- **Source:** the plugin READMEs.

### Zed

- **Mechanism:** explicit. `terminal.shell.program` names the shell, and the
  agent uses that terminal.
- **Set it to niu:** point `terminal.shell.program` at `niu.exe`.
- **Source:** settings in use on this machine.

### Aider

- **Mechanism:** there is no shell selection option. `/run` uses PowerShell or
  `cmd.exe` on Windows, and the model-visible environment reports
  `Shell: COMSPEC=...`. `--suggest-shell-commands` and `--shell-completions` are
  unrelated.
- **Set it to niu:** not supported. Launching Aider from niu does not change
  the shell it uses for `/run`.
- **Failure mode:** cmd/PowerShell command syntax, not a bash failure.
- **Source:** upstream options reference and changelog.

### Not verified here

Amazon Q Developer CLI, Goose, Amp, OpenHands, Devin, and editor agents outside
the VS Code lineage were not inspected on this machine and have no entry yet.
Add them when someone can read the shipped selector.

## What niu must provide, and who owns each path

| Path | Serves | Owner | Needs admin |
|---|---|---|---|
| `SHELL=%LOCALAPPDATA%\Programs\Niubash\niu.exe` (user env) | mechanism 1 | installer | no |
| `%LOCALAPPDATA%\Programs\Niubash\bin\{bash,sh}.exe` | opencode-shaped derivation when `git` resolves from `winuxcmd\bin` | installer / `niu setup` | no |
| `C:\Program Files\Git\bin\bash.exe` | mechanism 3 probe 1 (Claude Code, VS Code family) | installer | yes |
| `C:\Program Files (x86)\Git\bin\bash.exe` | the second hardcoded candidate | installer | yes |
| `C:\Program Files\Git\bin\sh.exe` | tools that want `sh` beside the bash they found | installer | yes |
| `...\winuxcmd\opt\niugit\bin\{bash,sh}.exe` | opencode's updater probe (`git --exec-path` up three) | niugit package manifest, so wpm refresh keeps it | no |

The entry directory `...\winuxcmd\bin` must contain only names you intend to
own globally (`git`, `sh`, `bash`), because it is what goes on `PATH`.

## Why `PATH` cannot win against `System32`

Windows resolves a bare name handed to `CreateProcess` by searching the
application directory, the current directory, **the system directory**, the
Windows directory, then `PATH`. Measured with the entry directory first on
`PATH`:

| Caller | `bash` becomes |
|---|---|
| `cmd /c bash -c 'uname -s'` | winuxcmd's bash (`MSYS_NT-...`) |
| PowerShell `& bash` | winuxcmd's bash |
| Python `subprocess(['bash', ...])` | **WSL** (`Linux`) |

Shells resolve names themselves, so `PATH` order decides for them. A process
that passes the bare name to `CreateProcess` never sees `PATH` before the system
directory, so a bare `bash` cannot be fully taken over from a per-user `PATH`.
The complete answers are the Git for Windows path (`C:\Program Files\Git\bin\bash.exe`)
and telling callers to use `sh`, which has no `System32` counterpart.

## Recommended order of work

1. **Installer writes `SHELL`** to the user environment, and removes it on
   uninstall. One registry value, no elevation, and it covers the largest class
   of agents including every POSIX-flavoured CLI. Users who launch from niu
   already have it; this makes it independent of the launch method.
2. **`niu setup agents`** (idempotent) creates the non-elevated paths, prints
   what each probe resolves to, and says which entries need elevation.
3. **Installer's optional elevated step** creates the `Program Files\Git`
   entries, with the second candidate and `sh.exe`.
4. **Per-agent settings** for the agents that read neither `$SHELL` nor the Git
   probes: Zed, DSH (done), the Codex fork (done), the VS Code automation
   profile.

Two things not to do: do not add `winuxcmd\usr\bin` to `PATH` (hundreds of
names, including ones `System32` owns), and do not replace
`C:\Windows\System32\bash.exe`.

## Known traps

- `MSYSTEM` is empty in a niu session. Tools that sniff MSYS through it
  (Gemini CLI's proposed fix) will not see niu. Setting it is untested and
  changes how other MSYS-aware tools behave, so treat it as an experiment.
- `uname` in the winuxcmd shim reports `MSYS_NT-...`. Anything that branches on
  `uname` will classify the host as MSYS, which is deliberate for
  compatibility but visible to autotools and similar.
- Claude Code accepts only `bash.exe`/`sh.exe`/`bash`/`sh` in
  `CLAUDE_CODE_GIT_BASH_PATH`.
- A `bash` that resolves to Windows' WSL launcher starts Linux bash, and there
  is no error: the agent simply runs your commands on another OS.
