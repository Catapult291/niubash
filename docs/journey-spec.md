# The Expanded Golden Journey Specification (P1–P10)

Lane `wt73/journeyscope` (2026-10-02). Successor spec to
[`journey-gate.md`](journey-gate.md): the J1–J6 gate stays exactly as it is —
this document defines what comes after it and why.

Companion machine-readable manifest (consumed by the implementation lanes):
[`journey-steps.json`](journey-steps.json).

## 0. Why: the gate tests day one; the P0s lived later

The owner's verdict on the current gate (2026-10-04):

> J1–J6 tests day-ONE state only (wizard → first source → first terminals).
> Every real P0 this week lived in LATER or ADVERSARIAL state transitions.

The evidence is not rhetorical — the audit below shows the gate ran **GREEN
through both live P0s**:

- **#167 (first-key eat, P0)** — the themed prompt's per-second clock repaint
  ate the first byte of every command typed after idle (`echo` → `cho`).
  The gate's own driver *works around* the bug (the Ctrl-U wake byte), so the
  gate went green while a human lost keystrokes. The bug was found by
  raw-stream forensics in `wt67/jdrv`, **not by a red gate**. A workaround
  without a compensating product assertion is self-deception.
- **#168 (theme rebound, OPEN P0)** — sync's "selection materialized" rewrite
  clobbers the user's picked theme across `source`/new terminals. J4 spawns
  three fresh terminals but only asserts "prompt renders" and J5 asserts a
  *non-default* prompt — a rebound theme is still non-default, so every
  assertion passes while the user's selection is silently overwritten.
- The counter-proof that the phase model works: the **"6s source"** (1.3.0
  re-fetched the full clone on every terminal when the bash-it fingerprint
  rejected upstream — changelog 1.3.1 F5/F6) *is* now caught, because J4
  asserts `no 'Cloning into'` in fresh terminals. That assertion exists only
  because someone was burned first. This spec's job is to stop paying that
  tuition incident by incident.

## 1. Method

1. Mine every issue from the last two weeks (unixwin/niubash #138–#168, plus
   the rubash-side theme/prompt family #415–#423) and the journey-gate
   KNOWN_FAILS registry.
2. For each incident classify: **would a J1–J6 walk have caught it?** Almost
   every answer is NO. That gap list is this spec's skeleton.
3. Enumerate the real user's first WEEK as phases P1–P10; each step traces to
   a numbered incident or a documented product contract — never a synthetic
   shape.
4. Feasibility-mark every step against today's ConPTY driver
   (`scripts/journey/golden-journey.py`) and name the driver capability it
   needs, if any.
5. Implementation lanes consume `journey-steps.json`; each landed step
   becomes a `J7…` block in the gate per the existing "How to add a step"
   rules (real transcripts, assertions on what the USER sees, known-fails
   registered not waived).

## 2. The incident audit (the gap-list skeleton)

Verdicts: **CAUGHT** (a J1–J6 assertion holds or fails on it), **MISSED**
(gate green while the bug shipped), **MASKED** (gate green *because a driver
workaround hides the product behavior*), **OUT** (correctly outside this
gate's class).

| Incident | Journey phase | J1–J6 verdict | Why |
| --- | --- | --- | --- |
| niu#159 setup rc backslash syntax error (1.2.4) | P1 | CAUGHT (today) | J3's zero-syntax-errors on `source ~/.niubashrc` — closed only after the burn |
| niu#157 setup rc unclosed `${…//\}` — every alias dead (1.2.1) | P1 | CAUGHT (today) | same J3 assertion, same story |
| niu#85 wizard Enter skips next question; arrows move 2 rows; ESC ignores default | P1 | MISSED | J2 drives one happy path; per-question input integrity never asserted |
| niu#143 user hand-migrates a theme (custom PS1 + PROMPT_COMMAND) | P4 | MISSED | wizard re-run + hand-edited rc coexistence never walked |
| KNOWN_FAIL `bash-preexec-recipe-entry` (apply reports `1 entries failed`) | P1 | CAUGHT | J2 asserts "no failed lines" (registered red) |
| niu#146 `niu -i` with non-tty stdin skips rc (GNU sources it) | P2 | OUT (sibling) | ConPTY is a tty; needs the piped-stdin probe as a plain-subprocess leg |
| niu#145 interactive startup hang (async git-status thread) | P3 | CAUGHT (today) | J4 "prompt renders" — the incident that produced the gate |
| niu#117 inherited Git Bash PS1 → `__git_ps1: command not found` every prompt | P2/P10 | MISSED | the gate scrubs the environment; no real user's terminal is scrubbed |
| niu#141 MSYS-parent spawn failures (rc=126, silent empty pipes) | P2/P10 | MISSED (partial) | gate's parent is python.exe; env-level approximation only |
| niu#160 `niu -c` unclosed quote exits silently rc=1 (GNU: message + rc 2) | P2 | MISSED | the journey never invokes `-c` mode |
| niu#148 `niu --norc -C '…'` argument-order misdispatch | P2 | MISSED | the journey never passes flags |
| niu#138 `~/.niubash_profile` (non-interactive/login env, mise) | P2/P9 | MISSED | feature documented as requested; no journey leg |
| **niu#167 first-key eat after clock repaint (P0)** | P3 | **MASKED** | Ctrl-U wake hides it; gate green through a live P0 |
| **niu#168 theme rebound across source/new terminal (P0, OPEN)** | P3/P4 | **MISSED** | J4/J5 assert non-default prompt; rebound theme is non-default; no rc byte-stability or theme-identity assertion |
| "6s source" — 1.3.0 bootstrap re-fetched clones every terminal (F5/F6) | P3/P5 | CAUGHT (today) | J4 "no Cloning into" — added post-incident; the model works |
| niu#147 cd into a git repo → `unexpected EOF … matching ')'` + stray `)` at prompt | P5 | MISSED | the battery never enters a git repository |
| rubash#423 `cd dir && ls` lists the OLD dir (children keep stale cwd) | P5 | MISSED | battery uses builtin `pwd` only |
| niu#155 `seq 200000 \| wc -l` silently loses 75%+ of the data | P5 | MISSED | battery is tiny-data; no volume probe |
| niu#158 `seq 20000 \| cat` hangs forever past ~64 KB | P5 | MISSED | same — no volume probe |
| niu#144 merged `2>&1` stderr reordered + withheld until exit | P5 | MISSED | battery has no merged-redirect stage |
| niu#166 bare `tr` in a pipe stage silently idles | P5 | MISSED | battery has no `tr` stage |
| niu#154/#163 `${x:?}`/`set -u` failure rc=127 (GNU 1) | P5 | MISSED | battery never fails an expansion |
| niu#162 opencode TUI fails to launch (env/DLL path pollution; cmd/PS fine) | P5/P10 | MISSED | battery spawns no env-sensitive child |
| five release runs burned by TLS resets (2026-10-03 22:11–23:26) | P5 | RED-noise only | the gate *inherits* network truth but never tests the PRODUCT's failure behavior (deferred ledger, bounded startup) |
| J6 release gluing / input-delivery losses (runs 37149660449, 37153503706) | driver | CAUGHT | input-delivery hardening — driver-side, done |
| KNOWN_FAIL `wt56-bash-completion-syntax` (bash_completion:1376) | P6 | CAUGHT | J6 (registered red) |
| niu#168 family: trust a SECOND source while one is active | P6 | MISSED | J6 trusts exactly one source, once |
| F4 (changelog 1.3.1): uninstall must drop the spec declaration or the source resurrects | P8 | MISSED | remove/remove-with-active-theme never walked |
| F5 ledger: failed startup install memoized, retried only on explicit sync | P3/P5 | MISSED | aged state never seeded; every terminal is born just-written |
| F1–F4 (changelog 1.3.1): imperative-mode dead end — nag on every terminal, `--adopt` migration | P9 | MISSED | the gate always ends spec-managed; upgrading an OLD install is never walked |
| rubash#416 OMB iterate: single-quoted `${var/pat/repl}` in `$()` → theme loses all segments | P4 | MISSED | exactly ONE theme (powerline-multiline) is ever activated; 60+ gallery themes never sourced |
| rubash#417 hawaii50 / #418 powerbash10k+brainy right-prompt render family | P4 | MISSED | same |
| rubash#420/#421/#422 PROMPT_DIRTRIM, `\u` empty USER, `\[ \]` markers on 22/82 themes | P4 | MISSED | same |
| niu#150 WinGet portable install loses the winuxcmd tree → STATUS_DLL_NOT_FOUND | P1/P9 | OUT | packaging CI owns the installer; but the *startup validation* behavior (degrade with a message, never a silent wrong shell) is journey-testable |
| niu#161 release stages the retired oh-my-niu bundle | P1 | OUT | packaging gate, not a terminal journey |
| niu#162 follow-on: PATH without winuxcmd at startup | P10 | MISSED | env-per-session variant never driven |

**Score: of the 30+ in-scope incidents, J1–J6 as shipped catches 6 — and 5 of
those 6 were added only after the incident burned a release or a P0.**

## 3. The phased specification

Conventions: every step below is phrased as the user's moves plus the
assertions the gate makes on what the user sees (screen text, files under the
sandbox `~`), per the standing rule in journey-gate.md §"How to add a step".
`Feasibility` marks and `Priority` (1 = wave 1 / P0-class, 2 = wave 2,
3 = wave 3) feed `journey-steps.json`.

### P1 — install + wizard first contact  *(base exists: J1–J2)*

Guards: #157, #159, #85, #143, KNOWN_FAIL `bash-preexec-recipe-entry`, #161 (out-of-scope pointer).

| Step | The user's move | Assertions | Feasibility | Priority |
| --- | --- | --- | --- | --- |
| P1-S1 *(=J1)* | fresh HOME → run `niu` | `Niubash <version>` welcome line + the dim first-run hint naming `niu setup`; REPL prompt alive; the wizard never auto-starts (#359 stage 1) | `conpty` | — |
| P1-S2 *(=J2)* | at the J1 prompt: `niu setup` → collection `full` → Apply → watch clones → Trust now → gallery → pick `powerline-multiline` | wizard welcome block (v-banner, environment panel, "No external themes installed yet"); clone lines; rc (the pick's ownership note) + the generated loader (theme activation) + spec + journal written; undo receipts listed; >60 themes on disk; REPL alive | `conpty` | — |
| P1-S3 | same walk but **Decline** the trust question | no trust flip; next startup shows the documented per-source `awaiting-trust` notice and prints the exact `niu plugin trust <id>` (plugins-guide contract: "Declining changes nothing"); enable refuses while untrusted | `conpty` | 2 |
| P1-S4 | per-question input integrity: one DOWN = exactly one row (highlight row-number delta 1); ESC takes the documented default; Enter never double-advances to the question after next | guards #85's three symptoms as a class (menu single-key semantics), not one republish of each | `conpty` | 3 |
| P1-S5 | apply honesty: a collection entry that fails (`failed` lines, e.g. the bash-preexec seed) keeps the step RED until its ticket lands | stays a registered KNOWN_FAIL; never absorbed as green | `conpty` (exists) | — |

### P2 — first session & first source beyond the just-written state  *(base exists: J3)*

Guards: #146, #117, #141, #160, #148, #138.

| Step | The user's move | Assertions | Feasibility | Priority |
| --- | --- | --- | --- | --- |
| P2-S1 *(=J3)* | `source ~/.niubashrc` in a live session | completes; zero syntax errors | `conpty` | — |
| P2-S2 | day-one flag invocations from the same sandbox: `niu -c 'echo ok'`, `niu --norc -c 'echo ok'`, `niu -c $'echo \\"'` (unclosed quote) | `-c` prints `ok` with the theme's aliases NOT required; `--norc` order-independent (#148); unclosed quote prints the EOF diagnostic and exits rc 2 (#160, GNU parity) | `subproc` | 3 |
| P2-S3 | spawn a session carrying a real terminal's baggage: inherited `PS1` (Git Bash shape), `MSYSTEM=MINGW64`, `SHELL=/usr/bin/bash` | prompt renders; zero `__git_ps1: command not found`; zero foreign-PS1 glyphs at prompt #1 (guards #117/#141 class: inherited env must be discarded or honored, never leaked) | `conpty+env` | 2 |
| P2-S4 | non-interactive login contract (`~/.niubash_profile`, #138) | once the feature lands: `niu -c` login path sources it (mise activation visible); until then do NOT add the step — feature-pending, tracked by #138 | `feature-pending` | 3 |

### P3 — second session, state persistence, state aging  *(base exists: J4; the P0 phase)*

Guards: **#168**, **#167**, the 6s-source family (kept green by J4), F5 ledger.

| Step | The user's move | Assertions | Feasibility | Priority |
| --- | --- | --- | --- | --- |
| P3-S1 *(=J4)* | three fresh terminals | no `Cloning into`, no `not declared`, ≤ documented `awaiting-trust` lines, prompt renders | `conpty` | — |
| P3-S2 | **theme-block byte-stability + theme identity across terminals** (#168): snapshot `~/.niubashrc` bytes after J2; `source ~/.niubashrc` in a live session; open two fresh terminals; snapshot bytes after each | (a) the theme block bytes (the oh-my-bash managed guard region) identical after every source/terminal, present exactly once, no `activated`/`deactivated` flip of the theme source — the whole-rc comparison is an informational note only (a spec-declared block may legally materialize once trusted, e.g. the bash-completion block J6 trusts; wt87 calibration); (b) the picked theme's variable (`OSH_THEME`/`BASH_IT_THEME` = `powerline-multiline`) still present and unchanged; (c) the string `selection materialized` NEVER appears during an unchanged-spec source/startup (it is the #168 rewrite tell); (d) terminal 2's first prompt row equals terminal 1's modulo clock digits | `conpty` | **1** |
| P3-S3 | **first-key integrity as product behavior** (the #167 anti-masking step): after the themed prompt idles ≥1.2 s (≥1 clock repaint), send `echo` with the driver's Ctrl-U wake DISABLED for this one probe | the full word executes (`echo: usage`-class output or silent success) — never `cho: command not found`; one labeled probe per session; the general wake stays for everything else | `conpty+wake-flag` | **1** |
| P3-S4 | **aged state**: between sessions, damage one installed tree (rename a file inside a trusted source) and seed a `bootstrap-failures.toml` entry for a declared-but-missing origin | next terminal reaches a prompt bounded ≤10 s with at most the documented one-line notices (`deferred`, `tree missing — repair with niu plugin restore <id>`; awaiting-trust notices name only still-untrusted sources, at most one per — the bound derived from the sandbox registry, wt78 expanded `full` to nine entries so no literal cap); the guarded loader no-ops silently (no error storm); `niu plugin sync` (explicit) retries and repairs; the terminal after that is silent | `conpty+seed` | **1** |

### P4 — re-running setup + switching themes  *(nothing exists today)*

Guards: **#168** (owner's entry door: "向导重选后"), #157/#159 (the rc writer is the most-burned code path), #143, rubash#416–#422 theme family.

| Step | The user's move | Assertions | Feasibility | Priority |
| --- | --- | --- | --- | --- |
| P4-S1 | re-run `niu setup` on the P3-state sandbox (`rerun_wizard()` is a supported flow: "Re-run the setup wizard even if the user already has a startup rc") | wizard completes; ends with exactly one theme state: the freshly picked theme active in rc AND spec AND rendered prompt — never a third state; zero syntax errors | `conpty` | **1** |
| P4-S2 | theme switch A→B→A through the wizard gallery: `powerline-multiline` → a second theme (e.g. `edsonarios`) → back to A | after each pick: new terminal renders THAT theme (prompt shape + rc variable agree); returning to A restores the managed block byte-identically to its original | `conpty` | **1** |
| P4-S3 | execute the undo receipts the finish screen printed (per-entry undo commands; `niu plugin rollback` family — read the exact verbs from `~/.niubash/setup-journal.toml`) | after undo: rc/spec/registry return to the pre-wizard state (files restored or absent); re-running the wizard afterwards succeeds; nothing resurrects on the next sync (F4) | `conpty` | 2 |
| P4-S4 | dual-framework same-name theme: with `full` (OMB + bash-it both installed) pick `powerbash10k` (exists in both frameworks — #168's exact shape) | exactly ONE framework's block activates it; spec's framework attribution agrees with the rc guard block; a second terminal does not flip the framework; green since wt72/themeback landed (1.3.4) — a red is a plain #168 regression | `conpty` | **1** |
| P4-S5 | theme breadth smoke: source ~8 gallery themes (one per family: `edsonarios`, `hawaii50`, `brainy`, `iterate`, `powerbash10k`, …), one throwaway session each | zero syntax errors; prompt renders per theme; known-bad themes register as KNOWN-FAILs with their rubash issue numbers (guards the rubash#416/#417/#418/#420/#421/#422 family instead of one theme forever) | `conpty` | 2 |

### P5 — the daily battery under adversarial conditions  *(base exists: J5)*

Guards: #155, #158, #144, #166, #147, #423, #154/#163, #162, the five TLS-burned release runs.

| Step | The user's move | Assertions | Feasibility | Priority |
| --- | --- | --- | --- | --- |
| P5-S1 *(=J5)* | `ls \| wc -l`, `echo hi \| cat -n`, `cd ~ && pwd`, `[[ a != b ]] && echo ok` | each produces output; theme owns the prompt; alive after each | `conpty` | — |
| P5-S2 | volume battery: `seq 200000 \| wc -l` → `200000`; `seq 20000 \| cat` returns | exact count (no silent EPIPE loss, #155); no permanent hang (#158) | `conpty` | 2 |
| P5-S3 | merged-redirect battery: `ls nofile 2>&1 \| wc -l` → `1`; `echo out 2>&1 \| grep out` | stderr reaches the pipe once, ordered, not withheld (#144) | `conpty` | 3 |
| P5-S4 | git-repo cd: `mkdir r && cd r && git init -q`, then a new prompt, then `cd r && ls` | prompt renders with no `unexpected EOF` and no stray `)` (#147); `ls` lists r's contents (children see the current cwd, #423) | `conpty` | 2 |
| P5-S5 | failing expansions: `niu -c 'set -u; echo $x'` | diagnostic printed, exit code 1 (GNU parity; #154/#163) | `subproc` | 3 |
| P5-S6 | env-sensitive child: `niu -c 'env'` carries no mangled drive-paths (the `B:/~BUN` shape of #162); if `less` is on PATH, page a file and quit | child env sane; TUI exits, prompt alive | `conpty` (best-effort, skip-if-absent) | 3 |
| P5-S7 | **startup under broken network**: the driver installs a dead git transport for the sandbox (sandbox gitconfig `insteadOf` rewrite of `https://github.com/` → `http://127.0.0.1:1/`) with a declared-but-unfetched source | fresh terminal reaches a prompt bounded ≤10 s with exactly the one-line deferred/failed notices — no hang, no re-fetch storm (the five TLS release runs; F5); `niu plugin sync` while still dead reports per-source failure readably; remove the rewrite → `niu plugin sync` repairs and clears the ledger → next terminal silent | `conpty+netfail` | **1** |

### P6 — trust lifecycle  *(base exists: J6)*

Guards: KNOWN_FAIL `wt56-bash-completion-syntax`, #168's dual-active world, F4, the trust-tier contracts in plugins-guide.

| Step | The user's move | Assertions | Feasibility | Priority |
| --- | --- | --- | --- | --- |
| P6-S1 *(=J6)* | `niu plugin trust bash-completion` → `source` → fresh terminal | trusted; zero syntax errors; `_init_completion` loaded | `conpty` | — |
| P6-S2 | trust a SECOND source while one is active: `niu plugin trust bash-preexec` (it has sat untrusted since J2) → `source` → fresh terminal | both active; zero errors; both awaiting-trust notices gone | `conpty` | 2 |
| P6-S3 | remove a source with active assets: `niu plugin source remove bash-completion` | rc re-materialized without it (its loader line gone); spec declaration dropped (F4: no resurrection at the next sync); fresh terminal clean | `conpty` | **1** |
| P6-S4 | checksum tier: rename a file inside a trusted tree, new terminal, then `niu plugin restore <id>` | startup stays silent (guarded loader no-op, documented `tree missing` contract) or prints the documented repair line — never an error storm; restore rebuilds from the pin; next terminal loads again | `conpty+seed` | 2 |

### P7 — spec hand-editing  *(nothing exists today)*

Guards: the documented contracts in plugins-guide ("you can equally hand-edit
the spec and run `niu plugin sync`"; the merge semantics; unmanaged themes) —
and #168's inverse invariant (sync claims only what the spec declares).

| Step | The user's move | Assertions | Feasibility | Priority |
| --- | --- | --- | --- | --- |
| P7-S1 | hand-add an `enable` entry to `~/.niubash/plugins.toml` → `niu plugin sync` → `niu plugin sync` again | first sync materializes it into the rc; second sync is a byte-identical no-op | `conpty` | **1** |
| P7-S2 | hand-add a marker line inside the managed rc block → `niu plugin sync` | the hand line survives every sync (documented `hand_added ∪ next(spec)` rule); the spec's own set is intact | `conpty` | **1** |
| P7-S3 | corrupt the spec (invalid TOML) → `niu plugin sync`, then a fresh terminal | sync fails with a readable error and does NOT destructively rewrite the rc; the fresh terminal still reaches a prompt bounded ≤10 s (a corrupt spec must never wedge startup — hang class) | `conpty` | **1** |
| P7-S4 | unmanaged theme: drop the `theme` key from the spec, hand-set `OSH_THEME` in the rc → `niu plugin sync` → fresh terminal | the hand theme survives (spec declares none → "keeps the current theme"); sync never rewrites it — #168's inverse invariant | `conpty` | **1** |

### P8 — plugin add/remove/update cycles with live themes  *(nothing exists today)*

Guards: F4 (resurrection), F6 fingerprint family, #161 (out-of-scope pointer), the lockfile verb contracts.

| Step | The user's move | Assertions | Feasibility | Priority |
| --- | --- | --- | --- | --- |
| P8-S1 | `niu plugin add <small-wild-repo>` while themes are active → trust → enable one file → `source` | add adopts/installs untrusted; trust question names the id; enabled file sources clean alongside the active theme | `conpty` (network) | 2 |
| P8-S2 | `niu plugin update <id>` on a source with an active theme | pin moves to the ref tip; theme still active; rc block unchanged byte-for-byte | `conpty` (network) | 2 |
| P8-S3 | `niu plugin rollback` / `restore` round-trip on that source | tree returns to the prior pin; enablement unchanged; fresh terminal clean | `conpty` | 2 |
| P8-S4 | **remove the source providing the ACTIVE theme** (`niu plugin source remove` of the theme-bearing framework) | documented degradation: guarded loader no-ops, prompt falls back cleanly, no syntax-error storm, spec declaration dropped (F4); re-add + re-enable restores the theme | `conpty` | **1** |

### P9 — upgrade / migration first run  *(nothing exists today)*

Guards: changelog 1.3.1 F1–F6 (every one of them shipped in this phase), #150 (startup-validation behavior; installer itself is packaging CI), #103/#106/#139/#140 (the version-regression family).

| Step | The user's move | Assertions | Feasibility | Priority |
| --- | --- | --- | --- | --- |
| P9-S1 | imperative-mode machine: seed the sandbox with `registry.toml` + installed sources but NO `plugins.toml` (the documented pre-1.3.1 state) → first terminal → `niu plugin sync` → `niu plugin sync --adopt` → `niu plugin sync` again | first run: NO "installed but not declared" nag (F1); sync prints the migration one-liner (F3); `--adopt` snapshots enablement + theme; the following plain sync is a byte-stable no-op (F2) | `conpty+seed` | **1** |
| P9-S2 | old rc + new binary: seed a 1.2.x-era `~/.niubashrc` (the #159-era generator shape, pre-spec) → fresh terminal | rc loads with zero syntax errors; aliases work; the new binary never "fixes" the file silently behind the user's back | `conpty+seed` | 2 |
| P9-S3 | seed a `bootstrap-failures.toml` from a previous version with a stale origin | startup defers with one line (F5); no fetch churn; explicit `niu plugin sync` retries | `conpty+seed` | 2 |

### P10 — multi-day drift  *(nothing exists today)*

Guards: #167 across sessions, #150's startup-validation behavior, #117/#141 environment classes, long-lived terminal reality.

| Step | The user's move | Assertions | Feasibility | Priority |
| --- | --- | --- | --- | --- |
| P10-S1 | timezone drift: spawn a session with `TZ=Pacific/Auckland` | clock segment renders; prompt alive; the P3-S3 first-key probe still passes under the other TZ | `conpty+env` | 3 |
| P10-S2 | degraded PATH: spawn with the winuxcmd directory stripped from PATH | startup degrades with the documented validation message (the #150-era behavior) — never a silent wrong-shell or a bare crash | `conpty+env` | 2 |
| P10-S3 | long-lived terminal: ≥30 s idle against the per-second clock (≥30 repaints; the overnight-terminal shape), plus a few hundred scrolled lines | prompt alive; scrollback sane; first-key probe passes (#167 across long idle) | `conpty` | 3 |
| P10-S4 | real MSYS parent (full #141 shape: spawn niu from an actual MSYS/Cygwin parent) | external applets spawn; pipes not silently empty | partial via `conpty+env` (`MSYSTEM`); full shape needs a parent-process driver capability — mark explicitly partial | 3 |

## 4. Feasibility legend + driver capability requests

| Mark | Meaning | New driver capability needed |
| --- | --- | --- |
| `conpty` | drivable with today's `golden-journey.py` as-is | none |
| `conpty+env` | same driver, a second env variant per phase (PATH/TZ/PS1/MSYSTEM) | trivial: parameterize `build_env` (env dict is already per-Session) |
| `conpty+wake-flag` | a `send_line(..., wake=False)` option so ONE probe asserts the product's first-key behavior instead of masking it (#167) | small, driver-only |
| `conpty+seed` | write/modify files under the sandbox home between sessions (aged state, old-version state, damaged trees) | small: a `seed_sandbox()` helper; no product change |
| `conpty+netfail` | break the git transport deterministically: sandbox gitconfig `insteadOf` → `http://127.0.0.1:1/` (or a dead proxy env) | small: a `break_network()/heal_network()` verb |
| `subproc` | plain child spawn (no ConPTY) for `-c`/flag-mode probes; reuses the same sandbox env | small: a `run_subproc()` leg |
| `sibling` | belongs in Rust tests / `smoke-wizard-journey.py` (offline), per journey-gate.md's standing rule | — |
| `out-of-scope` | packaging/installer CI (WinGet manifest #150, bundle staging #161); referenced for completeness, never a journey step | — |
| `feature-pending` | the behavior is requested/documented but unimplemented (#138); do not add until the feature lands | — |

No step requires a product code change to *drive*; steps whose honest
expectation is red today (P4-S4 until #168 lands) register as KNOWN-FAILs —
labeling, never waiving.

## 5. Coverage matrix (phase × current gate status × the gap)

| Phase | Current gate status | The gap in one line |
| --- | --- | --- |
| P1 install/wizard | COVERED (J1–J2) + gaps | happy path only: decline path, per-question input integrity (#85), undo execution untested |
| P2 first session/source | COVERED (J3) + gaps | only the scrubbed-env, tty, no-flags shape; real-terminal env (#117/#141), `-c`/flags (#160/#148) uncovered |
| P3 second session/persistence | PARTIAL (J4) | sessions exercised but state STABILITY not asserted (#168 green-through); #167 actively MASKED; aged state never seeded |
| P4 setup re-run + theme switch | NOT COVERED | the #168 entry door; the rc writer (burned by #157/#159) has no regression walk; 60+ themes never sourced |
| P5 daily battery | PARTIAL (J5) | 4 tiny commands; no volume (#155/#158), no 2>&1 (#144), no git-repo cd (#147), no failing expansion, no TUI child, no broken network (five burned runs) |
| P6 trust lifecycle | PARTIAL (J6) | one source trusted once; second trust, removal-with-active-assets, checksum mismatch untested |
| P7 spec hand-editing | NOT COVERED | the documented workflow (hand-edit + sync) and its corruption behavior have zero assertions |
| P8 plugin lifecycle | NOT COVERED | add/update/rollback/remove-with-active-theme never walked (F4 resurrection shipped here) |
| P9 upgrade path | NOT COVERED | every 1.3.1 hotfix (F1–F6) lived here; the gate can only see day-one spec-managed state |
| P10 multi-day drift | NOT COVERED | TZ/PATH/idle/parent-env drift never varied |

## 6. Top-10 gaps by blast radius

1. **P3-S2** theme identity + rc byte-stability across terminals — #168 (OPEN P0): silent preference loss for every multi-terminal user; the gate structurally cannot see it today.
2. **P4-S1/P4-S2** setup re-run + theme A→B→A — the #168 entry door, and the rc writer's first regression walk since #157/#159 burned it twice.
3. **P5-S7** startup under broken network — burned five release runs in one evening; unbounded startup is the #145 hang class recurring via the plugin path.
4. **P3-S3** first-key-after-idle as a product assertion — #167 proved the gate can go green through a live P0; an anti-masking assertion is the single cheapest honesty fix.
5. **P7-S1/P7-S3** spec hand-edit + corrupt-TOML startup — the documented power-user path; a wedged startup bricks every terminal (hang class).
6. **P6-S3/P8-S4** removal of a source with active assets — F4's resurrection bug shipped exactly here; dangling loader lines are the #157 family's shape.
7. **P4-S4** dual-framework same-name theme routing — #168's suspected mechanism; wt61-G2's neighborhood; one step pins the whole attribution model.
8. **P9-S1** imperative-mode migration first run — F1–F4 all shipped in this transition; every pre-1.3 install hits it on upgrade.
9. **P5-S2** volume battery — #155 (silent 75% data loss) and #158 (permanent hang) were P0-adjacent and invisible to tiny-data probes.
10. **P2-S3** real-terminal environment inheritance — #117's every-prompt nag shipped to real users because the gate's env is cleaner than anyone's terminal.

## 7. Recommended lane split

Four lanes; capabilities land inside the lane that needs them (no separate
driver lane — each capability is small and driver-only). Waves ordered by the
top-10 blast radii.

| Lane | Phases | Steps | Capabilities it lands | Why together |
| --- | --- | --- | --- | --- |
| **W1 persistence** (wave 1) | P3, P8-S4 | P3-S2, P3-S3, P3-S4, P8-S4 | `wake-flag`, `seed` | one theme: state across terminals; both P0s live here |
| **W2 wizard+spec** (wave 1) | P4, P7 | P4-S1…S5, P7-S1…S4 | none (heaviest ConPTY scripting: gallery walks) | both mutate selection state through wizard/sync; shares the gallery-walk machinery |
| **W3 network+trust** (wave 2) | P5-S7, P6 | P5-S7, P6-S2…S4 | `netfail` | trust and fetch-failure share the sources/registry subsystem |
| **W4 envelope** (wave 2) | P2, P5-S2…S6, P9, P10 | P2-S2/S3, P5-S2…S6, P9-S1…S3, P10-S1…S4 | `env-per-session`, `subproc`, `seed` (reuses W1's) | everything that varies the *environment* rather than the state files |

Wave 1 = W1 + W2 (the P0 class: #168, #167, setup re-run). Wave 2 = W3 + W4.
Each lane lands its steps as J7… gate blocks per journey-gate.md's rules, one
verdict paste per PR; P4-S4's `wt73-168-theme-rebound` KNOWN-FAIL was
retired when wt72/themeback landed (1.3.4) — the #168 assertions (P3-S2,
P4-S4) are plain regression reds now, per the wt87 calibration in
journey-gate.md.

## 8. Standing rules inherited unchanged

- Real user transcripts only — every step above cites its incident or
  documented contract; a step without a traceable origin does not belong in
  the gate.
- Known-fails are told, not hidden: registered red blocks the release until
  the owning lane lands.
- The gate asserts what the USER sees; internals stay in the Rust tests and
  the offline smoke journey.
- Every interactive-class fix ships with a journey step or an `-i` e2e test —
  this spec doubles as the checklist for that rule.
