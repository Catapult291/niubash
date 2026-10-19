# The Golden User Journey Gate

> **Successor spec:** [journey-spec.md](journey-spec.md) (+ the
> machine-readable [journey-steps.json](journey-steps.json)) — the phased
> P1–P10 expansion covering the blind spots J1–J7 cannot see (state
> persistence, setup re-runs, network failure, trust/spec lifecycle,
> upgrade, drift). J1–J7 below stay the release gate; the P-phases extend
> it lane by lane.

The owner's directive (2026-10-04, verbatim intent):

> 你现在的测试逻辑完完全全不是按照用户使用来的!交互式你根本不测!…必须改革测试方式
> ("Your current test logic is completely not how users use it! You don't
> test interactive at all! … the testing method must be reformed.")

The answer is this gate: **the owner's own transcripts become the automated
release blocker.** `scripts/journey/golden-journey.py` walks the exact
journey a real user walked on 2026-10-03/04 — not a synthetic shape — over
a real ConPTY (pywinpty + pyte, the `scripts/smoke-wizard-journey.py`
pattern), in a sandboxed HOME (`USERPROFILE` and `HOME` both overridden;
`USERPROFILE` wins in this product, a known pitfall, so both point at the
sandbox and the real `~/.niubash` is never touched).

## The journey (J1–J7)

| Step | The user's move | The gate asserts |
| --- | --- | --- |
| J1 fresh-install first contact | install, run `niu`, no `~/.niubash` anything | the v-banner + wizard appear: welcome block, environment panel, "No external themes installed yet" |
| J2 wizard full run | collection **full** → Apply → watch the clones → **Trust now (现在信任)** → gallery → pick `powerline-multiline` | clone progress lines appear; the trust question appears; gallery lists >60 themes; `~/.niubashrc` written with the theme, `~/.niubash/plugins.toml` written, undo receipts listed on the finish screen; the REPL prompt is alive afterwards |
| J3 activation | `source ~/.niubashrc` in the live session | ZERO syntax errors anywhere |
| J4 new terminal ×3 | open three fresh `niu` sessions | no `Cloning into` (no re-downloads), no "not declared" nags, at most the documented per-source `awaiting-trust` notices, prompt renders |
| J5 daily battery | `ls \| wc -l`, `echo hi \| cat -n`, `cd ~ && pwd`, `[[ a != b ]] && echo ok` | each produces output, not errors; prompt still alive after each |
| J6 trust + activate bash-completion | `niu plugin trust bash-completion` → `source ~/.niubashrc` → new terminal | trust reports success; ZERO syntax errors — bash_completion included |
| J7 gallery live preview (niubash#170) | re-run `niu setup`, walk the theme gallery | the pane below the highlight renders that theme's real PS1 (header follows the highlight; the old static sentence is gone; Esc fast-forwards, Cancel writes nothing) |

### The wave-1 persistence group (P3-S1…S4 + P8-S4, lane wt79/jw1-persistence)

Registered as phases `P3` and `P8` in `PHASE_RUNNERS` (the unified
mechanism — wt86/jmerge folded wt79's single `--phase` into `--phases`):
like every phase they compose AFTER the base gate on the same sandbox,
walking on the installed state J1–J7 leave (the theme-bearing source is
still installed — J6 only trusts bash-completion — and P8-S4 restores
what it removes). The wt79 vocabulary survives as `--phases` aliases:
`full` = all, `gate` = base, `persist` = P3,P8.

| Step | The user's move | The gate asserts |
| --- | --- | --- |
| P3-S1 reopen sandbox session | fresh terminal after J5; write a marker file through the shell | prompt renders; the wizard's theme line still in the rc; spec still declares oh-my-bash + the theme; the marker round-trips through the shell (previous-session state detection) |
| P3-S2 theme-block byte-stability + theme identity (niu#168) | `source ~/.niubashrc` in a live session; two fresh terminals; byte snapshots after every open | (a) the THEME block bytes (the oh-my-bash managed guard region) identical after every source/terminal, present exactly once, no activated/deactivated flip of the theme source — the whole-rc comparison stays informational (a spec-declared block may legally materialize once trusted, see the wt87 calibration below); (b) the picked theme's variable unchanged, same framework; (c) `selection materialized` never printed during an unchanged-spec source/startup; (d) terminal 2's prompt block equals terminal 1's modulo clock digits |
| P3-S3 first-key integrity (niu#167 anti-masking) | idle the themed prompt ≥1.2 s (≥1 clock repaint), then `echo NIU167KEY` with the Ctrl-U wake DISABLED, no retry | the full word executes (bare marker output); never `cho: command not found`. On a pass the run FLIPS `WAKE_ENABLED` off — every later send goes wake-free (the gate stops masking) |
| P3-S4 aged state (F5) | seed between sessions: rename `oh-my-bash.sh` inside the trusted tree, hand-declare a missing origin + seed its `bootstrap-failures.toml` memo | prompt ≤10 s; exactly one `deferred` row + the documented awaiting-trust lines — one per still-untrusted source, the bound derived from the sandbox registry (wt87 calibration; a literal cap went stale when wt78 expanded `full`); guarded loader no-ops (no error storm); explicit `niu plugin sync` retries the missing origin readably; `niu plugin restore` rebuilds the tree; after healing the spec, the next terminal is silent |
| P8-S4 remove the source with the ACTIVE theme (F4) | `niu plugin source remove oh-my-bash` while its theme is applied, then re-add + trust + re-enable | spec declaration, registry record and tree drop; no resurrection at the fresh terminal (no re-clone); floor prompt, zero syntax-error storm; re-add restores the theme; exactly one oh-my-bash managed block at steady state (no orphan blocks) |

Driver capabilities this lane landed (journey-steps.json `driver_capabilities`,
owner W1): **wake-flag** — `send_line(..., wake=False)` (no sacrificial
Ctrl-U, no retry: a retry would mask the first-key behavior the probe
exists to observe) plus the previous-session marker file; **seed** — the
`SandboxSeed` helper (rc byte snapshots, spec stanza add/drop, the
product-format F5 ledger write, tree-file damage), all under the sandbox
home between sessions.

Observed verdicts (first landing runs, 2026-10-04, release 1.3.3):
P3-S2 went **green** — the #168 rebound does not fire for the
oh-my-bash/`powerline-multiline` wizard shape. P3-S3 passed and the run
continued wake-free. One product observation recorded, not yet a ticket:
after `niu plugin source remove` + a fresh terminal the removed source's
managed rc block REMAINS (inert — its guarded loader no-ops on the
missing tree; `remove_source` also will not delete a tree whose layout
no longer fingerprints); the block is only replaced once the source is
re-added. F4-adjacent orphan-block sweep is a candidate follow-up.

### Gate calibration to the product (wt87/gatecal, post-1.3.4)

Release run 37218948432 (v1.3.4) failed its two newest gates. Both were
written against the pre-1.3.4 product; the product legitimately changed,
so the gates recalibrate to it — the gate calibrates to the product, the
product does not calibrate to the gate. Every change below names the
legitimate product behavior the old form mislabeled.

**F1 — P3-S2 whole-rc byte equality → theme-block byte equality.**
The old (a) asserted the WHOLE rc byte-identical across sessions. The
diff the run shows is the `# >>> niu source bash-completion …` managed
block materializing legally: J6 trusts bash-completion, the spec
declares it, and a later session's sync materializes its source block —
that is the spec being truth and materialization being sync's job, not
a stability break. What P3-S2 actually guards (the #168 class) is the
THEME state: the oh-my-bash managed guard region (where
`OSH_THEME='powerline-multiline'` lives) must stay byte-identical
across every open and present exactly once, no
`activated`/`deactivated` flip of the theme source in any P3 session
stream, plus the kept (b) theme-variable identity (verified holding —
wt72/themeback landed) and (c) rewrite-tell checks. The whole-rc
comparison remains as an informational note. The
`wt73-168-theme-rebound` KNOWN-FAIL registration is retired: wt72
landed in 1.3.4, and keeping the label would relabel a future #168
regression as expected-red. A P3-S2 red is now a plain regression red.

**F2 — P3-S4 awaiting-trust bound: literal ≤2 → registry-derived.**
The old bound "≤2 awaiting-trust notices" predates wt78 expanding the
`full` collection to nine entries — after J6 trusts bash-completion,
five still-untrusted sources legitimately name themselves
(`bash-preexec`, `bash-sensible`, `complete-alias`, `fzf-git.sh`,
`git-flow-completion`), and each honest release run will show exactly
that. Both P3-S4 assertions now derive the bound the way J4 has since
jw2's lane: every noticed id must be a real still-untrusted source in
the sandbox `registry.toml`, and the unique count may not exceed the
registry's untrusted count. A product regression (a nag for a trusted
source, a failed/degraded row dressed as awaiting-trust) still fails.

**P8-S4's two unlabeled reds in the same run — adjudicated, NOT
recalibrated here (assertions untouched; follow-up gate work, not
product bugs):**

> **Resolved by the wt87 follow-up (see "Follow-up: P8-S4 state-robustness"
> below)** — both reds were gate artifacts; the follow-up rewrites the
> precondition and the render comparison. The text below is the original
> adjudication, kept for the record.

- `precondition: oh-my-bash installed with the picked theme active —
  theme line present: False` — the precondition demands the J2 pick
  (`powerline-multiline`) still be active in the rc, but the journey's
  own P4 phase deliberately re-picks themes (P4-S4 picks
  `powerbash10k` for dual-framework routing, and P7/P8 run on that
  state), and the wt80 known-fail left P4-S3's undo half-applied. The
  product is provably correct at every hop (P4-S2/P4-S4/P7 all green).
  The precondition's assumption is stale gate sequencing: the step
  should establish or re-derive "the currently-active theme's source"
  instead of pinning the J2 pick.
- `the themed prompt renders again — row now: 'completion` …'` — the
  themed prompt DID render: the terminal snapshot
  (`transcripts/P8-S4-restored-terminal.txt` in the run artifacts)
  carries the themed rows (` ~ ` clock row + `❯`), the rc carries the
  active theme line, exactly one managed block, spec declares, zero
  syntax errors. The red is a fingerprint artifact:
  `await_first_prompt_row` compares the last 4 non-empty viewport rows,
  and with wt78's nine-entry collection the awaiting-trust notices wrap
  (`… then `niu plugin trust git-flow-` / `completion``), so a wrapped
  fragment lands inside the compared block and the fingerprints differ.
  A follow-up should compare the prompt-shaped rows only.

**P4-S3's reds stay KNOWN-FAIL `wt80-undo-receipt-ambiguous-theme`:**
that is a real, still-open product issue (the wizard's undo receipt
`niu plugin disable <theme>` fails when the theme name exists in more
than one installed source) owned by lane wt80/jw2-wizardspec — labeled,
never waived.

Local validation (wt87, fresh `cargo build --release` + full-journey
runs): P3-S2 went **PASS** — the whole-rc notes recorded exactly the
release run's diff (`# >>> niu source bash-completion …` materializing)
as informational while all theme-block/identity/tell/prompt assertions
held; P3-S4's recalibrated assertions read `5 source(s) vs 5 untrusted
in the registry` — the same five sources that red'd the release run,
now derived instead of capped. Remaining local reds were the
`schannel` TLS-reset family on the network-bound restore/re-add legs
(environment, the documented five-TLS-burned-release-runs class; retry
in a clean window) plus P8-S4's two adjudicated gate reds above, which
reproduced exactly as predicted.

### Follow-up: P8-S4 state-robustness + the full-matrix standard
(wt87 continuation, post-37224044818)

The re-dispatched release failed on exactly the two adjudicated P8-S4
classes — both now FIXED in the gate (they were gate artifacts; the
product was correct in the artifacts):

- **The precondition is derived from the live journey state.** Whatever
  theme P4 left active IS the state under test: at step start the gate
  reads the rc's active theme variable and the spec's oh-my-bash entry,
  requires them to agree with the assignment inside the oh-my-bash
  managed block (a mismatch stays an honest red — an rc/spec
  disagreement is a #168-class bug, never tolerated), and runs the
  removal-then-restore contract against THAT theme — the re-enable leg
  re-enables `oh-my-bash/<active-theme>`, restoring the state the step
  found instead of silently flipping back to the J2 pick (the old form
  "restored" `powerline-multiline` over a live `powerbash10k` and its
  own check passed against the wrong line). No active theme is a legal
  state: the contract then runs themeless (re-add + trust only; nothing
  may resurrect on disk or screen).
- **The render comparison is structural, not a fingerprint.** The new
  `themed_prompt_signature(session)` extracts the prompt's OWN rows
  (bottom-up from the input-glyph row while rows carry prompt
  structure — powerline glyphs or the prompt-ish row — stopping at the
  first notice/banner/output row; digits stripped). The restored
  terminal must match a reference captured from the SAME theme in the
  SAME run (the pre-removal session), never a cross-theme fingerprint;
  the wrapped-awaiting-trust fragment (`completion``) can no longer
  enter the comparison (that was run 37224044818's red: the themed
  prompt provably rendered in its own snapshot).

Matrix standard (owner escalation — the gate must hold in every cell,
not the one we happened to run):

- `{A→B→A, A→B (no return), A→A re-pick}` × theme axis: P8-S4 is
  **robust by construction** — the precondition derives the live state,
  so any consistent post-P4 theme satisfies it (validated against the
  real run-37224044818 facts: active `powerbash10k`, spec-agreed,
  J2 pick `powerline-multiline`). The P4-S2 hop-shape variants
  themselves (A→B-no-return, A→A) remain P4-lane follow-up work: they
  change the wizard-flow legs, not P8-S4's contract.
- `{HISTCONTROL}` axis: **inapplicable to this gate's semantics** — the
  product reads no HISTCONTROL anywhere (`grep -rn HISTCONTROL crates/`
  is empty; only HISTFILE appears, in a comment). Recorded instead of
  faked.
- `{1 vs 3 post-remove terminals}` axis: implemented as the
  `NIU_JOURNEY_P8S4_FLOOR_TERMINALS` knob (default 1; matrix cell 3):
  resurrection can first appear on a LATER startup (the memoized-defer
  path differs from the first one), so the floor contract asserts on
  every consecutive fresh terminal.

### Follow-up: the perfbudget gate ships now (wt87 continuation)

Release run 37224044818's perfbudget job died BEFORE timing:
`git fetch --depth 1 origin abf8461` → `couldn't find remote ref` — a
fetch REFSPEC is resolved server-side as a ref NAME, so the abbreviated
pins could never fetch. The checkout step now:

- pins the FULL 40-char object ids
  (`abf846186ab0a8a41ec5888e827ece6277dfe446`,
  `4725d29db8c0ac8c21df47664b28539f3b8fce94` — verified against the
  local baseline trees and the GitHub API);
- retries 5× with backoff, honoring `GIT_PERF_FALLBACK_PROXY` on the
  final attempt (local operators with a dead global proxy; CI runs
  direct like every other job);
- **fails open with a loud label** on total transport failure: the
  missing source is skipped (empty `--omb`/`--bash-it` = source
  disabled in asset-timing.py) and the verdict artifact carries
  `SKIPPED-<source>.txt` plus a workflow warning — a flap must not kill
  the gate before it measures, and a skip must never look like a
  measurement;
- **records per-source fetch latency** into
  `perfbudget-artifacts/fetch-latency.json` (seconds, attempts, ok) —
  the fetch feeding the measurement was historically invisible, and
  that invisibility is how the 6s incident shipped.

### Ship evidence: the 6s source fix is in the 1.3.4 binary

- Source-level: `STARTUP_FETCH_BUDGET` (3s, `NIU_STARTUP_FETCH_BUDGET_MS`
  overridable) + `run_git_bounded` (kill-on-close job over the whole git
  process tree; credential guards) bound the startup bootstrap fetch —
  `crates/niubash-runtime/src/plugins/sources.rs:67,669`; the product's
  own unit test `run_git_bounded_kills_a_hung_child_at_the_deadline`
  covers the hung-child kill.
- E2E: a SYN-drop origin (`https://10.255.255.1/...`, the dropped-SYN
  failure mode the budget exists for) declared in an otherwise
  wizard-shaped sandbox: first prompt at **3.2s**, REPL alive — the
  bounded fetch did not stall startup. Caveat stated plainly: the
  host's startup floor is ~3.5s (bootstrap-off control: 3.6s), so the
  E2E probe shows the budget path does not REGRESS startup and the
  journey's own P3-S4 deferred-install cell renders at 3.4s; the
  budget-vs-hang kill itself is evidenced by the unit test and source,
  not by the floor-dominated E2E numbers.

Exit code `0` only if every assertion holds. The run writes:

- `verdict.json` / `verdict.txt` — the per-step, per-assertion verdict;
- `transcripts/*.txt` — full screen captures (scrollback included) at
  every notable moment;
- `run.json` + `sandbox-kept.txt` — where the sandbox lived (kept for
  diagnosis when the gate is red, deleted when green).

### Known-fails are told, not hidden

The `KNOWN_FAILS` table at the top of the script registers failures with
their owning ticket. A match still makes the gate **RED** — the gate's
job is to tell the truth — but the verdict names the ticket so the red is
*expected-red until that lane lands*, not a mystery. When the fix lands,
the pattern simply stops matching and the gate goes green without edits.
Currently registered:

- `wt56-bash-completion-syntax` (lane wt56, alias family) — the
  `bash_completion: line 1376: syntax error in conditional expression`
  the owner hit on 2026-10-03; the gate observed it again on its first
  full run at J6 (fresh terminal after `niu plugin trust
  bash-completion`).
- `bash-preexec-recipe-entry` (recipe seed) — the `full` collection's
  bash-preexec recipe names entry `bash-preexec`, but upstream
  rcaloras/bash-preexec ships `bash-preexec.sh`, so the apply reports
  `1 entries failed`.
- `wt80-undo-receipt-ambiguous-theme` (lane wt80/jw2-wizardspec) — the
  wizard's undo receipt `niu plugin disable <theme>` fails when the
  theme name exists in more than one installed source (observed at
  P4-S3 in the 1.3.4 release run; `full` creates exactly that
  dual-framework state). Still open; labeled, never waived.

(The `wt73-168-theme-rebound` registration was retired with the
wt72/themeback fix in 1.3.4 — see the wt87 calibration section above.)

Unregistered failures stay plain RED. A red gate blocks the release
until each red is either fixed or registered — registering is labeling,
never waiving.

### The gallery live-preview golden (J7 + the offline ConPTY probe)

J7 asserts the user moment on the REAL gallery (>60 oh-my-bash themes):
the pane below the highlight renders that theme's actual prompt and
follows the highlight. Its deterministic sibling is
`scripts/smoke-theme-gallery-preview.py` — an offline ConPTY golden over
a fixture source carrying every representative theme class (single-line,
two-line, colored, right-aligned, powerline glyph, loads-but-no-PS1,
hung-forever). It snapshots the preview block per theme into the journey
manifest format (`verdict.json` / `verdict.txt` / `transcripts/`), runs
as leg `d3` of `scripts/smoke-test-1.3.0.sh`, and pins the niubash#170
degradation contract: a theme whose render fails or hangs shows
"(preview unavailable: …)" within the 1.5s bound — the gallery never
freezes, and the browse leaves no OSH_THEME anywhere.

### Network reality

The journey clones the real origins, so it inherits the network's truth:
a flaky path to github.com (TLS handshake resets, `curl 56 schannel`)
fails the gate honestly with the git error lines in the transcripts.
That is not a product bug and not a waiver candidate — re-run the gate
when the network recovers, exactly like a user would.

### Input delivery is verified, not assumed

Slow runners widen every window where niu is still drawing and the
ConPTY bridge eats keystrokes (release run 37149660449, J6: the typed
`niu plugin trust bash-completion` never echoed and the 90s wait timed
out on a screen still showing the startup banner; same family as wt61's
"menus drain input queued while they draw"). Local diagnosis of the
same gate added a second, sharper mode: **the themed prompt's
per-second clock repaint eats the FIRST input byte typed after idle**
(`echo` executes as `cho`, `niu` as `iu` — `niu: cho: command not
found` in the transcripts). So the driver hardens INPUT DELIVERY only —
four layers, all bounded — plus one sequencing rule (the output anchor
below, which owns the space *between* two sends):

- **settle** — before every send (`send_line`, `answer`, the gallery
  walk's first arrow), wait for a quiet screen (two polls, no new
  bytes, no viewport change) or a prompt-ish last non-empty row, at
  most 15s. A session that has never emitted a byte never counts as
  settled (that window is the startup-drain loss itself).
- **wake** — every REPL line is preceded by a kill-line (Ctrl-U): if
  the editor is about to eat a first byte, it eats the Ctrl-U; if not,
  the Ctrl-U harmlessly clears the input line. It also wipes any stale
  input before a retry's retype, so a resend can never concatenate two
  half-lines. Verified against the release build: Ctrl-U + line (and
  double Ctrl-U + line) execute clean.
- **confirm** — after a REPL line, wait up to 10s for its text to
  appear NEW in the rendered viewport (the editor syntax-highlights
  input, so the raw bytes never hold the word contiguously —
  `\x1b[36mecho\x1b[0m ...` — and pre-existing screen text does not
  count: the awaiting-trust nag literally spells the trust command);
  after a menu key, wait up to 10s for ANY viewport change (menus read
  raw and never echo); after Enter, wait 3s for the scroll.
- **resend once** — when the signal never came, resend the keystroke
  once and record it. This retries *delivery* only: the
  expected-output waits (`wait_for`) never retry, so a real product
  failure still fails the gate.

### Output-anchored sequencing

Release run 37153503706 (journey job, J6) exposed a hole the four
delivery layers cannot close: `echo J6_ALIVE` and the line typed right
after it executed as **one glued command**
(`❯ echo J6_ALIVEniu plugin trust bash-completion`). The second send
was delivered while the first was still being processed — the wake
Ctrl-U was the clock-repaint's eaten byte, and the text appended to the
unsubmitted input line. The settle check cannot see that state: the
typed input line *itself* starts with the prompt glyph, so the
prompt-ish matcher fires **during** execution, before the prompt has
returned.

So between two consecutive REPL sends in the same session, `send_line`
now returns only when the command it typed has **completed** — an
*output anchor*: a NEW prompt-ish last row that is neither the typed
input line nor any row carrying the command text (the command-text
guard also survives the theme clock's repaints). Bounded at 30s by
default; the known-long journey commands carry their own bound (90s for
`niu plugin trust bash-completion`, 60s for `source ~/.niubashrc`,
matching each step's expected-output wait). Every anchor outcome lands
in the `delivery` ledger as kind `anchor-wait` — one line per REPL
send, with its elapsed time: the sequencing trace a future CI flake
needs, never silent. The hidden `--stress-delay-ms N` harness
(randomized 0–N ms pause before every settle check, simulating runner
slowness — the shape that broke two release runs) validates it: normal
PASS plus three stress runs at N=800, all PASS.

The gallery's DOWN walk deliberately does not resend per key: repaint
can lag a delivered arrow, a resent arrow can overshoot the verified
row, and the walk already self-heals by polling the highlight.

Every resend (and every give-up) lands in a `delivery` ledger inside
`verdict.json` and as an `INPUT DELIVERY EVENTS` section in
`verdict.txt` — session, kind (`send_line` / `send_line-enter` /
`answer` / `anchor-wait`), attempt, keys (the waited marker, for
anchors), reason (including the settle outcome), and action (`resend` /
`undelivered` / `anchored` / `anchor-timeout`). The raw-stream and
transcript artifact formats are unchanged.

(The first-byte eat itself is product behavior worth its own ticket —
a human typing at the themed prompt after a clock tick would lose their
first keypress the same way. The gate works around it in the driver; it
does not fix it.)

## How to run locally

```sh
cargo build --release
python scripts/journey/golden-journey.py target/release/niu.exe
```

Requirements: Windows (ConPTY), `python -m pip install pywinpty pyte`,
git on PATH (the journey makes the same real clones the user's terminal
did — no offline mirror, no seeded fixtures), `ls`/`cat` on PATH for the
battery (WinuxCmd on a user machine; Git for Windows on a CI runner),
network access to github.com.

Options: `--artifacts DIR` (default
`target/journey-results/<timestamp>`), `--keep-sandbox DIR` (create the
sandbox under a directory you choose; it is kept on red, removed on
green), `--phases IDS` (comma-separated: the base J1–J7 gate always runs
first, then the named registered phases; `all` = every registered phase —
the DEFAULT, the gate exercises everything; `base` = the bare release
gate; legacy `--phase` words accepted: `full`, `gate`, `persist`),
`--cleanup-sandbox DIR` (standalone verb, no `niu.exe` needed: delete a
kept sandbox directory in the journey's own context — DACLs and the
read-only attribute are restored to host-clearable first — and exit 0
when the path is gone).

### Sandbox landing + host-clearable teardown (niubash#428)

A bare Python script has no git-bash-style path translation, so the
repo's conventional `/d/tmp/x` is read as DRIVE-RELATIVE: with the cwd
on D: the sandbox used to land at `D:\d\tmp\x` — the literal `/d`
became a junk first directory (the `D:\d` junk tree). The `--keep-sandbox`,
`--artifacts` and `--cleanup-sandbox` landing points therefore rewrite
the single-letter-drive form (`/d/...` → `D:/...`, with a printed
normalization note) and print the actual sandbox landing on every run;
an explicit `D:/...` form never regresses. Teardown makes the whole
sandbox tree host-clearable BEFORE it is deleted or kept (icacls
`/reset` back to parent inheritance + read-only attribute cleared), so
a kept-for-diagnosis tree deletes with one host command
(`Remove-Item -Recurse`), and debris from older runs is removed by the
`--cleanup-sandbox` verb (icacls → attrib → rmtree, escalating through
takeown and `rd /s /q` while the tree survives).

### Phase composition (--phases)

The phased expansion ([journey-spec.md](journey-spec.md)) lands lane by
lane: each wave lane appends clearly-separated phase runner functions to
`scripts/journey/golden-journey.py`, registered under the spec's phase id
in `PHASE_RUNNERS` (`wt79/jw1-persistence` → `P3` + `P8-S4`,
`wt80/jw2-wizardspec` → `P4` + `P7`). The registry + the `--phases` flag
are the only shared surface, so lanes cannot collide in step code.

Selected phases run AFTER the base gate on the same sandbox — every phase
walks on the installed state J1–J7 leave — so a lane's local run is
`--phases base,P4,P7`. The DEFAULT run is the base gate followed by EVERY
registered phase in spec order (P3, P4, P7, P8): the gate exercises
everything unless a subset is asked for. A base-gate failure blocks the
phases (nothing to walk on), exactly like it blocks J2–J7 today. Phase
verdicts appear in the same
`verdict.{json,txt}` as steps; expected-red phase steps carry their
registered KNOWN-FAIL labels (e.g. `wt80-undo-receipt-ambiguous-theme`)
and never pass silently.

## How the gate blocks release

`.github/workflows/release.yml` has a `journey` job that runs on
`windows-2025`: it resolves the release tag, **preserves the current
`scripts/journey/` from master, checks out the tag's own source, builds
`niu.exe` fresh inside the job** (stale-binary discipline — the artifact
is never reused), installs `pywinpty`/`pyte`, and runs the journey. The
`release` job declares `needs: [build-windows, journey]`, so no GitHub
Release is published while the journey is red. The verdict and the full
transcripts upload as the `journey-verdict` artifact (always, even on
failure — 14-day retention) so a red gate can be diagnosed from the run
page alone.

The "preserve current gate, checkout old tag" step mirrors the packager
preservation in `build-windows`: a `workflow_dispatch` release of an old
tag must be gated by the *current* journey while building that tag's own
source. (A genuinely old tag may lack strings the current gate waits for;
the gate failing on an ancient tag is honest — release it only if you
mean it.)

## How to add a step

A step is a **real user transcript, never a synthetic shape**:

1. Record what the user actually did — the exact commands, the exact
   menu answers, the exact terminal observations (screenshots/transcripts
   beat memory; date them).
2. Append a `J7`… block in `journey()` following the existing shape: one
   `verdict.step(...)`, `step.check(...)` for every user-visible fact,
   `verdict.capture(...)` at the moments a human would look at.
3. Assert what the USER sees (screen text, files under the sandbox
   `~/.niubash`), never internals. If the honest expectation is red
   today, register a KNOWN-FAIL with the owning ticket instead of
   weakening the assertion.
4. Re-run the journey end-to-end and paste the verdict into the PR.

If your step can be probed offline (no network, no ConPTY), it belongs in
`scripts/smoke-wizard-journey.py` or the Rust tests instead — the golden
journey is for exactly the interactive, online, whole-product class.

## The standing rule

**Every interactive-class fix ships with a journey step or an `-i` e2e
test.** If a change touches the wizard, menus, prompt rendering, rc
activation, trust flow, plugin sync startup behavior, or anything a user
watches happen in a terminal, the PR carries either a new step in this
journey or an interactive end-to-end test exercising the real flow.
"Green on synthetic shapes" is not evidence for this class — that is the
directive.
