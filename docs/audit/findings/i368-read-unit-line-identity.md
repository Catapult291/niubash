# i368 — read-unit / line-number identity audit (alias + line-based semantics)

Lane i368, niubash#368 (host) + engine findings filed to rubash. Host base:
niubash `origin/master` @ `d60276e`. Engine base: rubash `origin/master` @
`8ab35e89`. GNU oracle: WSL GNU Bash (Debian), stdin method
(`wsl bash --noprofile --norc < script`); engine runtime probes used the
rubash debug build of the audited revision. Interactive host probes used the
ConPTY harness (`tests/interactive/driver.rs`) with `--test-threads=1`.

Ticket checklist: A (line-number consumers as identity), A2 (function
definition lines / $LINENO / caller / traps / `-i` shopt gap), B (host
fast-path vs engine two-path divergence), C (internal-state hygiene), D
(interactive-test trustworthiness).

## Verdict summary

| Item | Verdict |
| --- | --- |
| A — `__RUBASH_CURRENT_LINE` consumers used as identity | **1 load-bearing site** (`arithmetic_aliases.rs:832-834` gate, `:867-878` reader). All other 63 references are report-only. The one load-bearing site carries a **confirmed cross-read-unit collision defect** → filed to rubash. |
| A2 — function-definition lines, `$LINENO`, `caller`, traps, `-i` shopt gap | Clean. AST-line derived, matches GNU in the probed shapes; `-i`/`-i -c` `expand_aliases` default ON matches GNU. |
| B — host fast-path extras parity under the #367 routing | Clean. All probed shapes (`cd C:/x`, bare `C:`, `cd /c/x`, pipeline `| grep`) converge to identical `$PWD`/output on both routes. |
| C1 — `__RUBASH_ALIAS_LINE_*` never removed | **Already fixed upstream** (`alias_set_builtins.rs:15,20-57`), pinned by `rubash tests/issue367_alias_line_hygiene_regressions.rs`. |
| C2 — `__RUBASH_*` leaking into child environments | No observable defect (see C2 below for the three-layer analysis). |
| D — interactive test echo-markers | #367 echo-proof discipline landed for the alias matrix. **One active test remains echo-satisfiable** (`ctrl_d_midline_does_not_exit`) → fixed in the host PR. |

## The confirmed defect (filed to rubash)

**Alias same-line suppression misfires across read-unit boundaries: a
`source`- or `eval`-defined alias whose definition line number collides with
the caller's later physical line is never expanded ("command not found")
where GNU expands.**

Mechanism:

- `execute_alias` records `__RUBASH_ALIAS_LINE_<name>` from the recording
  command's AST line (`src/executor/alias_set_builtins.rs:65`, `:88`).
- Sourced files restart group numbering at 1 per file
  (`run_source` → `read_next_source_group`), so a definition at file line N
  records marker `N` regardless of the sourcer's position; `eval` bodies are
  renumbered `caller_line + i - 1`
  (`src/executor/trap_exec.rs:460-480`), folding body lines into caller line
  numbers.
- `expand_alias_word` suppresses an alias when marker == current
  `__RUBASH_CURRENT_LINE` (`src/executor/arithmetic_aliases.rs:832-834` gate,
  `:867-878` comparison). Two different read-units that both contain "line 5"
  therefore compare equal, and the suppression — GNU's same-*read* rule,
  approximated as same-*line* — fires across the boundary.

Instrumented probe (rubash debug @ `8ab35e89`, stdin script):

```
after source: marker(__RUBASH_ALIAS_LINE_x)=5  cur(__RUBASH_CURRENT_LINE)=3  lineno=3
use at line 5: cur=5 == marker 5 -> suppressed
```

### Repro 1 — stdin script + source (def-line == use-line)

`def5.sh` (definition on the file's line 5):

```sh
:
:
:
:
alias x='echo AX'
```

Driver (use on the sourcer's line 5):

```sh
shopt -s expand_aliases
source def5.sh
:
:
x
echo rc=$?
```

| Shell | Result |
| --- | --- |
| GNU (WSL stdin) | `AX`, `rc=0` |
| rubash `8ab35e89` | `line 5: x: command not found`, `rc=127` |

### Repro 2 — eval-defined alias (eval-continuation line == caller line)

```sh
shopt -s expand_aliases
body=$': \n: \n: \nalias y=\'echo AY\''
eval "$body"
:
:
y
echo rc=$?
```

The eval body's 4th line renumbers to caller line 6 (`caller_line + i - 1`),
marker `y=6`; the use sits on caller line 6.

| Shell | Result |
| --- | --- |
| GNU (WSL stdin) | `AY`, `rc=0` |
| rubash `8ab35e89` | `line 6: y: command not found`, `rc=127` |

Control (same script, use moved to line 7): both shells print `AY rc=0` —
proving the failure is the line-identity collision, not the eval/source
definition path itself.

### Guard rails that DO match GNU (verified, must survive the fix)

| Shape | GNU | rubash |
| --- | --- | --- |
| `alias ll='echo LL'; ll` (same line, stdin script) | 127 | 127 |
| same-line suppression *inside* a sourced file (`def.sh` line 2: `alias x=…; x`) | 127 (inner) | 127, diagnostic carries the sourced file's own line |
| same-line inside `eval` string | 127 | 127 |
| definition line N, use at line N+1 (plain script) | expands | expands |
| source/eval definition, use at non-colliding later line | expands | expands |
| `rubash -i` (piped) multi-submission alias use | expands | expands |
| `bash -i -c` / `rubash -i -c` `shopt expand_aliases` | on | on |
| `$LINENO` across `-i` submissions | 1, 2 (cumulative) | 1, 2 (cumulative) |
| `bash -c` collision layout (`. def5.sh` … use at line 5) | expands | expands (`-c` routes through the grouped driver, no marker guard) |

Scope note: the guard only bites where alias expansion runs executor-level
against the pre-parsed AST — stdin scripts (`run_stdin_script`) and any host
fast path that reaches `execute_command` with aliases live. The grouped
drivers expand at text level against the live table
(`script_driver.rs:1107-1109` → `expand_group_aliases`, `script_driver.rs:85`;
`src/lexer/alias_stream.rs:221`) with no marker logic, which is why the
interactive host (`niu`) is unaffected: `execute_interactive_reader_batch`
(`crates/niubash-runtime/src/shell.rs:770-812`) routes every alias-bearing
submission through the grouped reader, and the probe confirmed the
first-prompt rc case holds `marker=1, cur=1` yet expands.

### Fix direction (for the rubash ticket)

Replace the physical-line marker comparison with a **read-unit identity**:
record `(read_unit_generation, line)` when an alias is defined (the existing
save/restore brackets in `src/builtins/source/execution.rs:291-293,351-358`
and the eval entry are the natural generation boundaries — the same seam
GNU's `push_stream`/`run_unwind_frame` owns), and suppress only when BOTH the
generation and the line match. This is exactly the "monotonic read
generation" direction already proposed in niubash#368, and it retires the
marker's line-collision class without weakening the verified same-read
suppression rows above.

## Clean-bill evidence

### A — consumer classification (65 refs, rubash `8ab35e89`)

Load-bearing (identity/visibility decisions) — 1 site:

| Site | Use |
| --- | --- |
| `src/executor/arithmetic_aliases.rs:832-834,867-878` | same-read alias visibility (defect above) |

Report-only (diagnostics/`$LINENO`/frames) — the remaining 64:

| Sites | Use |
| --- | --- |
| `src/builtins/{cd,enable,exec,hash,help,kill,printf,pwd,setattr,set,shift,shopt,test,trap,ulimit,umask,zsh_options}.rs`, `src/builtins/set/unset.rs:298`, `src/builtins/setattr/value.rs:168`, `src/builtins/declare/diagnostic.rs:15-17`, `src/builtins/alias.rs:251` | `builtin_error` prologues (`script: line N:`), GNU `error.c` shape |
| `src/executor/public_accessors.rs:988-1075` | single writer (`set_current_line_value`/`set_current_line`), equality-gated; OS-env mirror only for diagnostic builtins (`command_text.rs:270-297`) |
| `src/executor/public_accessors.rs:1210-1262` | diagnostic/parser prefixes (GNU `error.c:88-120`, `:300-316`) |
| `src/executor/dynamic_arrays.rs:167-174`, `src/executor/arithmetic/value.rs:304-312` | `$LINENO` / arith `LINENO` |
| `src/executor/function_calls.rs:449-465` | `BASH_LINENO` push (GNU `execute_function:5311-5317`) |
| `src/executor/compound_exec.rs:1201-1213`, `src/executor/loop_select.rs:97-105` | for-loop ambient line capture/restore (GNU `execute_cmd.c` restore) |
| `src/executor/trap_exec.rs:197-206,378-389,464-474,800-806,876-886` | trap/eval line continuation + RETURN/DEBUG `$LINENO` stamping (GNU `trap.c:1196`) |
| `src/executor/command_substitution.rs:1154-1164,1624-1632,1732-1740`, `src/executor/command_substitution_pipelines.rs:257-285,384-392`, `src/executor/command_substitution_values.rs:844-852`, `src/executor/embedded_mutations.rs:1136-1147,1629-1638,1964-1973`, `src/executor/pipeline_exec.rs:384-394`, `src/executor/command_input_scope.rs:750-760,849-859` | comsub/heredoc body line seeding + EOF diagnostics (GNU `subst.c`/`parse.y` push_stream model) |
| `src/executor/conditional.rs:596-604` | `[[ ]]` syntax-error prefix |
| `src/builtins/source/execution.rs:291-293,351-358` | source frame save/restore (GNU `evalfile.c:253-257,395`) |
| `src/executor/compound_exec.rs:407-421,2056-2068` | background/`coproc` child script identity (`__RUBASH_SCRIPT_NAME`+line+`__RUBASH_LINE_OFFSET`), GNU `execute_cmd.c:1761` fork equivalence — designed |
| `src/script_driver.rs:976-995` | history-expansion error line pinning (`bashhist.c pre_process_line`) |
| `src/executor/ast_exec.rs:528` | comment only |

A2 specifics: `function_definition_locations` records the parse-time AST line
(`src/executor/function_calls.rs:168-182`) and `body_open_line` prefers the
call-site AST, then the recorded definition, then the `{` line
(`src/executor/function_calls.rs:527-547`, GNU `execute_cmd.c:5351`).
`apply_init_interactive_defaults` / `apply_interactive_alias_default`
(`src/main.rs:1196-1212`) set `expand_aliases` for `-i`/`-i -c` — verified
equal to GNU (`shopt expand_aliases` → on in both; `-i -c 'alias …\n…'`
expands in both).

### B — two-path parity (host `d60276e`, ConPTY probe, `--test-threads=1`)

Both routes execute the same shapes with identical results
(`$PWD` printed per step; alias defined in phase 2 forces
`execute_interactive_reader_batch`, phase 1 rides the host fast path with the
four normalizers `crates/niubash-runtime/src/shell.rs:2435-2560,2682-2869`):

| Shape | fast path | grouped route |
| --- | --- | --- |
| `cd C:/tmp/qzdir` | `/c/tmp/qzdir` | `/c/tmp/qzdir` |
| bare `C:` (drive switch) | `$PWD=/c/` | `$PWD=/c/` |
| `echo … \| grep …` pipeline | output matches | output matches |
| `cd /c/tmp/qzdir` (slash-drive) | `/c/tmp/qzdir` | `/c/tmp/qzdir` |

The engine natively resolves the raw Windows-drive forms, so skipping the
host-only rewrites on the grouped route is parity-safe for these shapes.

### C — internal-state hygiene

C1 (marker cleanup): fixed upstream — `execute_unalias` clears per-name and
`-a` clears all (`src/executor/alias_set_builtins.rs:15,20-57`), pinned by
`tests/issue367_alias_line_hygiene_regressions.rs` (including the
eval/source/nested unalias shapes).

C2 (child environments), three layers checked:

1. Regular external children: `apply_child_environment` starts from
   `env_clear()` and re-adds only exported names
   (`src/executor/readonly_functions.rs:246-262`), so the OS-env mirror
   (`set_process_env`, `local_helpers.rs:148-153`) never crosses. Probe:
   `alias`+`declare` then `rubash -c 'env' | grep -c RUBASH` → `0`.
2. rubasm wrapper/coproc children: `compound_exec.rs:374-379` filters
   `__RUBASH_*` out of the spawn env except the `rubash_spawn_inherited_state`
   allowlist (`:26-57`, no line keys on it) and then re-adds the *intended*
   script identity (`:407-421`, `:2056-2068`) — GNU fork equivalence, not a
   leak.
3. Fresh rubash children launched by a user inherit the parent's OS-env
   `__RUBASH_CURRENT_LINE` (`init.rs` does not strip it), but every
   consumer-stamp happens before execution (`command_execute.rs:36-52`) and
   parse errors use token-derived lines
   (`parser_diagnostic_prefix_for_line`, `script_driver.rs:2531`). Probe:
   parent `alias` at lines 1-2, child `rubash -c ')'` → child reports
   `line 1` (GNU-identical), not the parent's stale `2`.

Observation (divergence, note only — folds into the read-unit fix): the
markers are readable in the user namespace via parameter expansion and
`declare -p` (`${__RUBASH_ALIAS_LINE_x}` → `2`,
`declare -- __RUBASH_ALIAS_LINE_x="2"`), while `set`/`export -p` filter them.
GNU has no such variables. A read-unit-generation redesign can make these
private to the executor map.

### D — interactive test trustworthiness (host `d60276e`)

- The niubash#367 echo-proof discipline is in place for the whole alias
  matrix (`tests/interactive.rs:380-575`: markers ride rc variables / printf
  formats / `type` output; `expect_absent("command not found")` guards).
- `alias_define_then_use_live`, `alias_use_at_next_prompt_executes_not_echo_matched`,
  `rc_first_line_alias_expands_at_first_prompt`,
  `alias_same_line_definition_is_not_visible_until_next_prompt`,
  `parser_level_alias_value_expands_across_prompts`,
  `alias_quoted_and_unalias_use_function_live` — all green on `d60276e`
  (run 2026-10-13, `--test-threads=1`, 6 passed).
- **Defect (host, fixed in PR): `ctrl_d_midline_does_not_exit`
  (`tests/interactive.rs:304-319`)** — `s.expect("half-typed")` is satisfied
  by the terminal echo of the submitted `echo half-typed` itself, so the
  test can pass without the completed line ever executing (the exact class
  niubash#367 called out). Fix: complete the line with a suffix so the
  expected output (`half-typed-ok`) never appears verbatim in the input.
- Weak secondaries (cosmetic, left as-is): `tests/interactive.rs:64` and
  `:82` expect `closed`/`closed'`, which the submitted continuation lines
  echo — but the load-bearing execution markers (`double-line`, `single-line`)
  precede them and are echo-proof.
- Ignored tests `history_bang_*` (`:372-407`, rubash#286) and
  `ctrl_z_at_prompt` (`:351`, rubash#287) carry honest ignore reasons; their
  in-test sequencing (progressive `expect` consumption) is echo-safe.

## Runtime probe inventory

Artifacts under `rubash-wt-368-audit/target/audit368/` (scratch, not
committed): `def.sh`, `def5.sh`, `defs.sh`, `def1.sh`, `t1_ea`–`t12_probe`,
`v1`–`v3`, `cbody.sh`, `t7`–`t9_envleak`, `t11_visibility.sh`. Host probes:
`tests/scratch368.rs` in the worktree (removed before the PR lands; results
transcribed above).

## Deliverables

1. rubash ticket: alias marker line-collision across read-units (this file's
   defect section; repros 1+2, controls, fix direction).
2. Host PR (niubash, `fix/368-audit`): echo-proof hardening of
   `ctrl_d_midline_does_not_exit` + this report. Not to be merged by the
   author; based on `origin/master` `d60276e`.
