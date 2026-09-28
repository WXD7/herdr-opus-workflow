# 四文档版本管理

沿用本插件的 Git 仓库；本地稳定分支为 `workflow/opus-local`。版本标签是四份文档的
成套快照，不是上游插件版本，也不包含 Ponora 业务代码。主入口直接加载此目录，
`references/plan.md` 与 `references/supervise.md` 是共享文件的符号链接，无须另行安装副本。

| 标签 | 含义 | 验证边界 |
| --- | --- | --- |
| `workflow-v1.0.0` | 本轮修改前的 Opus 适配版，提交 `db2c27f` | 已用于 `tm173y`；运行发现的问题仍保留 |
| `workflow-v1.1.0` | 精简入口、证据交接、重点独立复验、弹性巡检、安全排队纠偏及版本记录 | 文档与入口静态检查；尚未用新版启动业务运行 |

版本范围固定为以下四份源文件；不另存日常全文副本，不在四份正文各写一套版本号：

```text
skills/dispatch-codex/SKILL.md
skills/_shared/plan.md
skills/_shared/supervise.md
skills/dispatch-codex/references/driver.md
```

## 记录与探索

- 每个完整调整做一次提交。发布可比较的版本时加不可移动的注释标签 `workflow-vX.Y.Z`。
  文字修正升补丁号；调度或验收行为变化升次版本；不兼容的状态/协议变化升主版本。
- 日常基准保留在 `workflow/opus-local`；探索从明确标签另开 `workflow/experiment-<主题>`。
  先保存未提交改动，再切分支；试验成熟后合并并打新标签，放弃时切回稳定分支。
- 新 run 在既有 `state.json` 中记录实际插件路径、Git commit/tag、dirty 状态及四文件 SHA-256。
  历史 run 的记录不改写；未打标签的实验用 commit 标识，dirty 文件不冒充正式版本。
- 标签只是本地记录；不自动 push，尤其不推向此仓库的上游作者。需要异机备份另行指定私有远端。

在本目录查看与比较：

```bash
git log --oneline --decorate -8
git tag --list 'workflow-v*'
git diff workflow-v1.0.0 workflow-v1.1.0 -- skills/dispatch-codex/SKILL.md skills/_shared/plan.md skills/_shared/supervise.md skills/dispatch-codex/references/driver.md
```

## 回退与生效

**先确认没有仍依赖当前文档的活跃 run，并保存未提交修改。** 旧会话已加载的规则不会被
Git 切换清空。不要在业务执行中切换版本，或把旧会话的 `--resume` 当作新版启动。
运行中确需换版时，由用户明确授权交接；版本不符的监督器停止自己的巡检和输入，
保留 worker 运行，等待交接，不自行杀进程。

回退采用“恢复指定四文件，再做新提交”，保留前进与回退历史；不使用 `reset --hard`
或强制移动标签。先运行 `git status --short`，只有工作区与暂存区均干净时才继续。
例如在稳定分支回到 v1.0.0 的规则：

```bash
git switch workflow/opus-local
git restore --source=workflow-v1.0.0 --worktree -- skills/dispatch-codex/SKILL.md skills/_shared/plan.md skills/_shared/supervise.md skills/dispatch-codex/references/driver.md
git diff --stat
# 查看实际 diff，确认后执行：
git add -- skills/dispatch-codex/SKILL.md skills/_shared/plan.md skills/_shared/supervise.md skills/dispatch-codex/references/driver.md
git commit -m 'revert(workflow): restore the v1.0.0 rules'
```

之后按新的发布号记录回退，不覆盖旧标签。新任务使用原入口启动**全新 Claude 会话和 run**，
读取所选版本，再核对 `state.workflow` 的版本与哈希。v1.0.0 尚无该自动记录约定，使用它时
需在既有运行证据中补记所选标签、commit 和四文件哈希。仅修改文件不代表活跃会话已换版。

v1.1.0 将正常巡检设为 15 分钟，连续健康推进可延至 30 分钟，恢复阶段可缩至 5 分钟；
完成通知仍可提前触发。这里只修改既有工作流规则，没有增设零模型触发器或启动任何业务任务。
