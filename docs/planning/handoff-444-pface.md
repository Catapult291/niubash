# #444 交接文档 — journey P 面 #359 后适应修复（发版阻塞）

交接给：a-rookie-of-C-language
日期：2026-10-19
分支：`fix/444-pface`（worktree `D:/repo/niubash-wt-444`，基于 origin/master `a6badc4`）
状态：**未开 PR**。修复代码全部就位、单测/集成全绿、完整 journey 本地跑到只剩 **P8-S4 一条断言红**（见 §4，疑似 harness 泵线程死亡，非产品缺陷）。

---

## 0. 任务一句话

v1.4.1 Release run 39249382423 / 39223964675 死在 journey P 面五条断言（P3-S4 / P4-S1 / P7-S2 / P7-S3 / P8-S4）。根因共性：#359 rc 只读重设计后，激活面迁移到 generated loader（`~/.niubash/generated/plugins.niu`），这些步的断言/机制过时。逐步判定「过时断言 → 改断言」vs「真实缺陷 → 修产品」。

## 1. 五步定性结论（已全部完成判定）

| 步 | 失败断言（release 实录） | 定性 | 处置 |
|---|---|---|---|
| P3-S4 | `at most the documented one-line notices` — `deferred=[]`（要求 `==1`） | **过时断言**：#359 后启动根本不跑 sync——rc bootstrap 行是兼容性 no-op（`src/main.rs` `run_plugin_sync_command`：`if bootstrap { return Ok(()); }`），deferred 备忘行随启动 sync 一起退役；F5 的 wedge bound 天然更强地成立 | 改断言为 spec 自己的 "at most" 语义（`<=1`，failed/degraded 仍禁止），check 名注明 silent-boot 新常态 |
| P4-S1 | `the Skip option names the current pick` — 行是泛化 `Skip - keep my current theme` | **真实产品缺陷**：`setup_wizard.rs` `current_theme_pick` 只读 rc 找 `OSH_THEME/BASH_IT_THEME`，#359 后 theme line 在 loader 里，rc 只剩 ownership note → 向导认不出当前 pick | **修产品**：`current_theme_pick` 先扫 generated loader 再扫 rc，按加载顺序最后一个非空赋值胜（host 先 source loader 再 source rc，手写 rc 行仍胜，保住 P7-S4 unmanaged 语义）；单测改名 `current_theme_pick_reads_loader_then_rc_in_load_order` 并扩展 |
| P7-S2 | `the hand-added selection survives` — detail 为空 | **过时机制**（journey bug）：手编锚点用了 rc 拼写 `# >>> niu source oh-my-bash (managed by ...) >>>`，在 loader 文件里 replace 无效 → 手编根本没落盘；且 merge 重渲染后手编行不以原文形式保留 | 锚点改 `LOADER_BEGIN_MARK`（`# --- niu plugin source: <id> ---`，`loader.rs` `section_begin` 拼写），断言从「行原样保留」改为「'git' ∈ 物化后的 plugins=(…) 数组」（`next(spec) ∪ hand_added`，产品测试 `sync.rs:1261` 同款语义）|
| P7-S3 | `the boot printed the soft error` | **过时断言**：#359 后启动从不读 spec（loader 是 boot plane，`shell.rs` `load_generated_plugin_loader`），corrupt spec 开机静默用 last-known-good loader——不是 silent wrong shell；可读错误属于显式 sync（该断言本来就 ok） | 改断言：boot 静默且无 panic、无 failed/degraded storm；错误面归 sync（注释写明） |
| P8-S4 | (a) `add re-installed` 600s 超时；(b) `signature ['❯']` vs `['❯','PS_ALIVE …']` | (a) **真实产品缺陷**：`niu plugin add` 完成行的 `new_id` 用「最后一条合格行」扫描，#359 后 reconcile 报告把 trusted 源的 `unchanged` 物化行排在新增源 `awaiting-trust` 行之后 → 扫到旁观行（`unchanged bash-completion`），打印误导性 `Declared source 'bash-completion'`，`Installed source 'oh-my-bash'` 根本不打；(b) **journey 伪迹**：powerbash10k 右段用光标上移绘制，可能粘在 banner 行（PUA 字符 `\uf017` 在 banner 行上），signature 走查器见 "Niubash" 前缀就停 → 诚实签名被清空 | (a) **修产品**：`src/main.rs` `run_plugin_add_command` 先按本次 add 的 `(entry_id, entry_target)` 命中行取 `new_id`，旧行扫描降为 fallback；回归测试 `tests/plugin_spec_sync.rs::add_completion_line_names_the_added_source_not_a_bystander`（红→绿已本地验证：release transcript 就是红形态）。(b) 修 `themed_prompt_signature`：结构行（PUA/powerline）即使与 banner 共享物理行也入块并在该处停 |

## 2. 已落盘的修改（本分支全部 diff，223+/38-）

产品（2 处）：
- `crates/niubash-runtime/src/setup_wizard.rs` — `current_theme_pick` 读 loader+rc（含 doc 注释与单测重写）。
- `src/main.rs` — `run_plugin_add_command` 完成行 `new_id` 命中本次 entry（含注释），新增回归测试在 `tests/plugin_spec_sync.rs` 尾部。

journey（`scripts/journey/golden-journey.py`）：
- P3-S4 deferred 上界改 `<=1`（约 L1793-1810）。
- P7-S2 锚点 `LOADER_BEGIN_MARK` + 选择成员断言 + 手编未落盘的显式 AssertionError（约 L3768-3805）。
- P7-S3 boot 静默断言（约 L3903-3922）。
- P8-S4 `themed_prompt_signature` banner 粘行入块（约 L1122-1145，docstring L1099-1104）。
- 交付加固（本地实锤 2026-10-19：Enter 落进菜单自绘窗口会被吞）：J2 walk 与 `wizard_rerun` 每次菜单作答前 `wait_quiescent()`；`wizard_rerun` 的 niu-git 问句条件化（#230 同款，bundle 带 opt/niugit 时跳过——J2 walk 原本就有此形状，run6 在本机因此 crash）。
- `Session.seal()`：**blocked 从此不等绿**（gate_exit_code 契约本来就说 blocked→exit 1；run6 暴露该洞：P4 crash 后 verdict 仍 PASS）。`scripts/journey/test_verdict_exit.py` 自检通过。
- `Session._pump`/`text`/`transcript`：`stream.feed` 移入 `_lock`（run4 实锤 `RuntimeError: deque mutated during iteration` 打穿整个 gate）。**注意：这也是 §4 疑点的首要嫌疑，交接后优先复核（见 §4.3）。**

验证状态：
- `cargo test --release -p niubash-runtime --lib`：561 passed。
- `--test plugin_spec_sync / plugin_anyplug / plugin_assets / plugin_sources / smoke_1_3_0`：全绿（含新回归测试）。
- `python scripts/journey/test_verdict_exit.py`：ok。

## 3. 复用点速查（#443 引入的 loader 帮手，golden-journey.py）

- `loader_text_for(home)`（L3029 附近）→ `~/.niubash/generated/plugins.niu` 全文。
- `loader_managed_block(text, sid)` / `loader_managed_block_count` / `loader_without_block`（L3043-3072）→ loader 段落级断言（含 marker）。
- `LOADER_BEGIN_MARK = "# --- niu plugin source: {sid} ---"`、`LOADER_END_MARK = "# --- end niu plugin source: {sid} ---"`。**与 rc 侧拼写不同**：`RC_BEGIN_MARK = "# >>> niu source {sid} (managed by `niu plugin enable/disable`) >>>"`。所有「managed 块」断言一律走 loader 拼写；rc 现在只允许断言 ownership note（`# Prompt owned by the <source> theme '<pick>'`）+ 无 theme line。
- `rc_theme_vars(text)`（L3074）对 loader/rc 都能用（找 OSH_THEME/BASH_IT_THEME）。
- 产品侧对应物：`plugins/loader.rs` `section_begin/section_end`、`startup_decision`；sync 行动作 `installed|awaiting-trust|activated|unchanged|deactivated|degraded|drift|failed|removed|unsupported|deferred|merged`。

## 4. 唯一剩余红：run9 P8-S4 单断言（交接后的活）

### 4.1 现象（D:/tmp/jt444-run9，2026-10-19 23:35，完整 journey 其余全绿）

```
[FAIL] `niu plugin source remove oh-my-bash` reported removal
  || timed out (90s) waiting for ("Removed source 'oh-my-bash'",)
```

但（同 step 内，其后的断言全 ok）：spec 声明已删、registry 已删、树已删、floor 终端干净、re-add/trust/enable 全过、恢复后 loader/spec 断言全过——**remove 在磁盘上完全成功，只是那条输出行没被 wait_for 看见**。

### 4.2 实锤证据（读文件即得，无需重跑）

- `D:/tmp/jt444-run9/transcripts/P8-S4-remove.raw.ansi` 末尾：`Removed source 'oh-my-bash' (D:\tmp\jt444-sandbox9\journey-bvh_1slk\home\.niubash\sources\oh-my-bash)` —— **needle 是该行子串，输出在发送后 1s 内（23:35:40）已落到 raw 流**。
- 而 wait_for 90s（至 23:37:10）从未命中，异常里带的屏幕快照冻结在 23:35:39；raw 文件 mtime = 23:37:10（close 时 flush），即 **23:35:40 之后再无任何字节进入 pyte/raw_chunks，且 `self.dead` 仍为 False**。
- verdict delivery events（`D:/tmp/jt444-run9/verdict.json`）：send_line `anchor after 0.2s` 正常返回，无 resend/undelivered。

### 4.3 首要嫌疑与三步复核法

**嫌疑 #1：Session._pump 的 feed 异常使泵线程静默死亡。** `_pump` 只有 `proc.read()` 包在 try/except 里；`stream.feed(data)` 抛异常会直接杀死 daemon 线程——之后 dead 永远 False、raw_chunks/屏幕冻结、所有 wait_for 只能靠超时。append+raw 文件写在 feed **之前**，所以「文件有行、内存冻结」完全自洽。这与我的 lock 修改无关（改前 feed 同样裸奔），但**改前从没在这个路径上被踩过**（release runner 上 remove 通过）。
复核法（不重跑全程）：
1. 给 `_pump` 的 feed 段包 try/except，异常时 `self._delivery_event("pump", ..., str(err), "pump-dead")` 并 `self.dead = True`——让真实异常浮出到 verdict 而不是 90s 静默超时；这是无论如何都该落的无窗加固。
2. 单独复跑该步即可定位：`--phases all` 或写个 30 行的最小 ConPTY 脚本进 sandbox9 复现 remove（sandbox9 保留在 `D:/tmp/jt444-sandbox9`，未被清理，ACL 已是宿主可清）。
3. 若真是 pyte 对某字节序列抛异常，转投 pyte 层过滤或降级为 raw-only 匹配；若不是，再疑 ConPTY read 假死（`isalive()` 真、read 永挂）——加 read 心跳/看门狗即可。

次要嫌疑：本机 ConPTY 在长会话后偶发 read 假死（J3-P8 同会话存活约 20 分钟）。release runner 上五步全过、本机 9 轮仅此一次，低概率单发也可能。**不要为此改产品**——remove 的磁盘契约在 run9 里全绿。

### 4.4 完成判据（接力后）

`--phases all` 全绿 + `--phases all` 第二轮确认非 flake → 开 PR 到 master（标题注明 #444 + 发版阻塞 run 39249382423），正文用本文件 §1 表格逐条回报处置。

## 5. 本机跑 journey 的环境三坑（接力前必读）

1. **rubash pin**：Cargo.toml `[patch]` 把 rubash 指到 `../rubash`。`D:/repo/rubash` 必须 ≥ pin `cd456ca3`（run7 实锤：停在旧 pin `45f537ea` 时，向导选主题后的会话在 `command_prepare.rs:3919` panic——`index out of bounds: the len is 132 but the index is 132`，正是 `scripts/release/rubash-pin.json` notes 里记录的 P4-S4/P7 panic 面）。本机已 ff 到 origin/master `78952f5`（含 pin）。release CI 自己 fetch pin，与此无关。
2. **GitHub 直连烂**：本机直连 clone 大 repo 必超时（`early EOF`）；`~/.gitconfig` 的 `http.proxy=127.0.0.1:65532` 走 HOME，journey 沙箱重定向 HOME 后 git 读不到 → 沙箱内直连 → 全挂。已建本地镜像缓存 `D:/tmp/ghcache/*.git`（8 个 collection 源的 --mirror clone），跑 journey 前导出（可原样抄）：
   ```bash
   export GIT_CONFIG_COUNT=8 \
     GIT_CONFIG_KEY_0='url.file:///D:/tmp/ghcache/oh-my-bash.git.insteadOf'      GIT_CONFIG_VALUE_0='https://github.com/ohmybash/oh-my-bash.git' \
     GIT_CONFIG_KEY_1='url.file:///D:/tmp/ghcache/bash-it.git.insteadOf'         GIT_CONFIG_VALUE_1='https://github.com/Bash-it/bash-it.git' \
     GIT_CONFIG_KEY_2='url.file:///D:/tmp/ghcache/bash-completion.git.insteadOf' GIT_CONFIG_VALUE_2='https://github.com/scop/bash-completion.git' \
     GIT_CONFIG_KEY_3='url.file:///D:/tmp/ghcache/bash-preexec.git.insteadOf'    GIT_CONFIG_VALUE_3='https://github.com/rcaloras/bash-preexec.git' \
     GIT_CONFIG_KEY_4='url.file:///D:/tmp/ghcache/complete-alias.git.insteadOf'  GIT_CONFIG_VALUE_4='https://github.com/cykerway/complete-alias.git' \
     GIT_CONFIG_KEY_5='url.file:///D:/tmp/ghcache/fzf-git.sh.git.insteadOf'      GIT_CONFIG_VALUE_5='https://github.com/junegunn/fzf-git.sh.git' \
     GIT_CONFIG_KEY_6='url.file:///D:/tmp/ghcache/bash-sensible.git.insteadOf'   GIT_CONFIG_VALUE_6='https://github.com/mrzool/bash-sensible.git' \
     GIT_CONFIG_KEY_7='url.file:///D:/tmp/ghcache/git-flow-completion.git.insteadOf' GIT_CONFIG_VALUE_7='https://github.com/bobthecow/git-flow-completion.git'
   ```
   （env 经 os.environ → niu → git 传递，无视 HOME 重定向；CI 不受影响。本地镜像只为验证，PR 判绿以 CI 为准。）
   走代理直连 GitHub 也试过：schannel 握手随机失败，不稳。
3. **后台 cargo 必须落盘日志、别用管道吞退出码**：`cargo build … | tail` 把 cargo 的退出码换成 tail 的，且 10 分钟 timeout 杀编译后 pipeline 仍报 0 → run8 拿旧二进制跑了一整轮（panic 复现假象）。正确姿势：`cargo build … > build.log 2>&1; echo exit=$? >> build.log`（本机 release 全量 ≈5 分钟，增量也要 3-5 分钟）。

## 6. 现场遗留物

- worktree：`D:/repo/niubash-wt-444`（分支 `fix/444-pface`，已 commit + push，见 §7）。
- 保留沙箱（诊断用，宿主可清）：`D:/tmp/jt444-sandbox9`（run9 P8-S4 现场）。清理命令：`python scripts/journey/golden-journey.py --cleanup-sandbox D:/tmp/jt444-sandbox9`。
- artifacts（verdict+transcripts，全可读）：`D:/tmp/jt444-baseline`（J2 本机首曝 stall）、`-run2/-run3`（clone 超时轮）、`-run4`（pyte deque crash 轮）、`-run5`（lock 修复后，代理不稳轮）、`-run6`（P4 niu-git crash 轮，其 seal 洞实锤）、`-run7`（rubash 旧 pin panic 轮）、`-run8`（无效轮：旧二进制）、**`-run9`（最终态：仅 P8-S4 一红）**。
- release 失败实录：`D:/tmp/jt444-artifacts`（run 39249382423 的 verdict+transcripts）。
- 镜像缓存：`D:/tmp/ghcache`（8 repo，68M）。
- 主 worktree `D:/repo/niubash` 有他人未提交改动（repl.rs/interactive.rs），未动。

## 7. 提交

分支 `fix/444-pface`（已 push）：
1. `fix(#444): journey P-face #359 adaptations — wizard current pick, add completion line, loader-era assertions`（产品 2 + journey 断言 4 + 测试 1）。
2. `fix(#444): journey harness hardening — seal holds blocked, feed under lock, quiesce before wizard menus, conditional niu-git ask`。
3. handoff 文档本尊（即本文件）。

未做（接力清单）：§4.3 三步复核 → 全绿两轮 → PR（base master，正文注明 #444 + 发版阻塞）→ 沙箱清理收尾。
