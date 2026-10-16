# 生态收割：待测推进与构建版本重测

## 默认策略

`eco-harvest-ci` 的定时任务与手动入口默认采集、测试全部 `t1`–`t5`。
测试默认每轮最多 400 个资产、2400 秒派发预算、8 个 worker。
预算耗尽后不再派发，等待在途资产按既有单阶段超时完成；不是强杀进程的总墙钟上限。
Windows 测试 job 另有 90 分钟上限，包含构建时间。

1. 优先选择从未测试的资产，以及 `FETCH-FAILED`、`HARNESS-ERROR`、`EXITED` 暂态。
2. 剩余额度用于旧构建 verdict，默认每轮最多 80 个。
3. 同一队列按 tier 轮转，tier 起点按最近测试时间轮换；tier 内未测优先，之后最久未测优先。
   即使 `--limit 1` 或连续构建升级，也不会每次重新从 t1 开始耗尽预算。
4. 当前构建已有稳定 verdict 的资产跳过。`--redo` 强制所有资产重新可选，忽略旧版本专用
   上限，但仍遵守总数量和时间预算。

命令行选项：`--tiers all`（也接受逗号列表）、`--limit 400`、`--stale-limit 80`、
`--max-seconds 2400`。`--limit 0`、`--max-seconds 0` 分别取消总数量、派发时间限制；
`--stale-limit 0` 禁用普通旧版本重测。手动 workflow 对应 `test_tiers`、`test_limit`、
`stale_limit`、`test_seconds`、`redo` 输入。

## 持久化与迁移

同一 manifest SQLite 中增加：

- `eco_test_builds`：构建身份及完整 JSON 元数据。
- `eco_test_verdicts`：以 `(content_hash, build_id)` 为键，保留每个构建的最近 verdict、
  测试时间和尝试次数；新构建不覆盖旧构建历史。
- `eco_test_meta`：一次性旧数据迁移标志。

构建身份由实际 `niu.exe`、`rubash.exe` 的 SHA256，两者构建 checkout SHA，
`eco-test.py` 的 SHA256、slow threshold，以及 GNU oracle 的可用性、已解析的命令路径
和版本共同计算。主流程先探测 oracle，再计算身份和选择资产；oracle 从不可用恢复、
路径或版本变化都会产生新身份，之前降级分类的稳定 verdict 只能作为 stale 历史，
按正常旧版本预算重测，不会永久遮盖恢复后的分类，也不绕过未测/暂态优先级。
CI 从实际构建目录读取两个
checkout 的 HEAD（rubash 为 sibling master，不误用被 path patch 替代的锁文件版本）。
本地未提供 `--niu-revision` / `--rubash-revision` 时记录 `unknown`，仍有实际二进制哈希。
每条结果 JSONL 同样带 `build_id` 和完整 `build_identity`。

首次迁移保留旧 `assets.verdict`，导入为版本未知的 `legacy` 历史；本地旧 JSONL 若存在，
以每个资产最后一条有效记录为准，避免早期 OK 永久遮蔽后来的暂态。
旧 verdict 只能算 stale 或 transient，不能被当成当前构建通过证明。
`assets.verdict` 继续作为既有 UI 的最近 badge，版本跳过决策只读取新增版本表。
表随现有 manifest artifact 和数据分支持久化流程传播，无需重建或清空数据库。

## 可观测性与安全验证

日志和 step summary 明示选择前的 `unseen` / `transient` / `stale` / `current`、
`selected`，以及实际 `completed`、因时间预算延后数和 `idle`。
零测试明确说明无可选项，或旧版本重测被限额禁用；不再把空跑投影成全库吞吐。
FETCH-FAILED 的既有比例门禁与退出码 3 保持不变。

安全验证（Python 3.11+，无第三方依赖）：

```text
python -m unittest discover -s scripts/harvest -p "test_*.py" -v
```

fixtures 使用临时/内存 SQLite、不可执行的假二进制字节和模拟 verdict；不联网，
不启动 Niubash、rubash、WSL 或 ConPTY，不执行任何收割资产。
覆盖旧 DB/JSONL 迁移、构建身份变化、版本历史、暂态重试、跨 tier 公平推进、
旧版本上限、redo、派发预算、空跑摘要、fetch 门禁，以及 10106 总数/4185 未测的合成场景。
Oracle 恢复与版本变化测试完全使用 mock；恢复场景验证相同二进制的旧 HANG 不会阻止
新身份下重新分类为 GNU-ALSO-FAILS，不启动 WSL 或真实资产。

真实资产只允许在一次性 runner 上测试。临时 HOME **不是安全隔离**；
不要在真实开发宿主为复核本次修改运行生态资产，也不需要安装或调整 WSL。
