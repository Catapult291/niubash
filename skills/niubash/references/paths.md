# Path contract

Niubash's path model in one sentence: **the Windows-native path is the
first-class internal representation; everything else is an input dialect.**
There is no translation layer, so there is nothing that can mis-convert —
and no environment variable to disable one.

## The contract

| Input spelling | Meaning | Resolves to |
|---|---|---|
| `C:/Users/me`, `C:\Users\me` | native Windows | itself — always safe |
| `/c/Users/me` | POSIX display dialect | `C:\Users\me` |
| `/mnt/c/Users/me` | WSL dialect | `C:\Users\me` |
| `/usr/bin`, `/bin`, `/etc/...`, `/var/...` | install-tree dialect | real dirs in the WinuxCmd installation root |
| `/tmp/x`, `/var/tmp/x` | per-user temp namespace | the real Windows temp dir (`TMPDIR`, else `%TEMP%`); `/var/tmp` → `<temp>/var/tmp` |
| `/home/me` | users dialect | real users directory (`C:/Users`) |
| `/dev/null` | null device | Windows `NUL` |
| `~` | home | `USERPROFILE` (kept consistent with `HOME`) |
| `D:` (bare) | drive designator | the drive's root — `D:` and `cd D:` land on `/d/` |

Key facts for agents:

- Bare drive designators are the cmd-style drive switch: `D:` or `cd D:`
  jumps to that drive's root, and the behavior is identical across
  `niu -c`, brace groups/subshells, and piped `niu -i` (the engine owns
  the dispatch on every route). `D:rel\path` drive-relative spellings are
  not a dialect — use `D:/rel/path` or `/d/rel/path`.

- `> /tmp/f` and `cat /tmp/f` agree: both hit the real Windows temp
  directory. The install tree ships **no** `tmp/` directory at all — there
  is exactly one `/tmp` and it is per-user.
- `printf '%s\n' /tmp/x` prints `/tmp/x` verbatim (GNU bash behavior):
  literal data arguments are never rewritten.
- What a child process receives is always a native Windows path when the
  argument is a shell path — `ls /etc/hosts` opens the real installed file.

## Contrast with MSYS-family environments

| | MSYS2 / Git Bash | Niubash |
|---|---|---|
| Model | POSIX emulation over a Unix-looking root | native shell, dialects as input |
| Path handling | heuristic conversion of arguments | no conversion; dialect resolution |
| Escape hatch | `MSYS_NO_PATHCONV`, `MSYS2_ARG_CONV_EXCL` required when the heuristic breaks | nothing — there is no converter to disable |
| Failure mode | silent argument mangling (e.g. `git grep "/pattern"` rewritten) | not present |

Rule of thumb: if you find yourself reaching for a `NO_PATH_CONV`-style
variable, stop — in Niubash that variable does not exist because the problem
it works around does not exist. If a path misbehaves, it is a bug: report it
to `unixwin/rubash` (shell semantics) or `unixwin/winuxcmd` (command
implementation).
