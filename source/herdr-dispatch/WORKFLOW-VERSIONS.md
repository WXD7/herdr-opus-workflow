# Workflow 与完整工程版本

本公开衍生项目的主分支为 `main`，插件位于 `source/herdr-dispatch/`。
归档前插件仓库的提交历史原样保留；原运行目录和原插件 Git 仓库未被迁移。

| 标签 | 内容 | 布局与验证边界 |
| --- | --- | --- |
| `workflow-v1.0.0` | 修改前基线，`db2c27f` | 插件在仓库根；曾用于本地任务 |
| `workflow-v1.1.0` | 精简与改进版，`4c3ba9b` | 插件在仓库根；发布后用于本地实验，私有运行证据不分发 |
| `bundle-v1.0.0` | 插件、workflow、入口、采集与部署配置完整快照 | 本仓库当前目录布局；四份正文与 v1.1.0 一致 |
| `workflow-v1.2.0` | 决策归属、错误诊断、原始需求验收与证据复用 | 完整工程布局；规则已写入，合成回归与静态检查；未做新版业务 E2E |
| `bundle-v1.1.0` | v1.2 规则、公开说明、示例、图示及可移植配置模板 | 与 workflow-v1.2.0 同一发布提交；没有自动审批桥 |

开发分支 `feat/multi-config-experiments`：v1.3 参数化组配置与共同验收、确定性服务资源；
尚未创建发布标签。旧标签保持原字节；需要回退时新建任务选择旧 ref，不恢复旧会话。

`workflow-v*` 记录四文档行为版本；`bundle-v*` 记录整体工程快照。标签不移动或覆盖。
文字修正升补丁号，调度/验收变化升次版本，不兼容协议变化升主版本；配套文件归档不冒充规则更新。
新任务仍在既有 state 中记录实际插件路径、Git commit/tag、dirty 状态及四文件 SHA-256。

插件 manifest 的 0.0.1 是保留的上游包版本；本项目规则版本由 workflow 标签与四文件哈希
识别，不把插件包号当规则号。归档清单见仓库根 SNAPSHOT.json，发布检查见 docs/release-v1.2.md。

## v1.2 的范围

在原四文档内替换和合并约束：常规决定由主管负责；获授权复盘者只接指定例外；
新范围、权限、预算或降低验收要求交用户。原生子 Agent 继承边界、经父 Agent 汇总。
API 错误与正常长任务分开处理，恢复前核对活动与副作用；元数据更新不冒充业务进展。
验收同时对照原始需求和 lane 标准，明确事实、推断与未知，不为排版重复测试。

该版本发布的是规则和公开工程包，没有安装上一轮未获批准、未测试的审批 hook 草案。
不会自动唤醒本对话、自动批准权限或热更新既有 Agent。文档更完整不等于桥接能力已经实现。
原生子 Agent 降低沟通成本是待比较的设计预期，不是发布性能结论。

## 探索

从明确的整体标签创建 `experiment/<主题>` 分支；完成一个完整调整就提交一次。
合适的试验再合并回 `main` 并打新标签。切分支前保存未提交改动，不重写既有历史。
未来 Git 操作在这个独立仓库进行；原运行目录不会自动同步，也不会热更新已加载的 Agent。

## 回退

先确认没有正在使用待切换规则的活跃 run，并保存修改。回退四文件后做新提交，保留回退历史；
不使用 `reset --hard` 或强制移动标签。全新 Claude 会话/run 加载所选规则，旧会话不能自动换版。
运行中需要交接版本时须有用户授权；不要为了回退擅自停止业务进程。

从 `bundle-v*` 恢复时，文件路径是本仓库根目录下的：

```text
source/herdr-dispatch/skills/dispatch-codex/SKILL.md
source/herdr-dispatch/skills/_shared/plan.md
source/herdr-dispatch/skills/_shared/supervise.md
source/herdr-dispatch/skills/dispatch-codex/references/driver.md
```

**旧两个 workflow 标签的路径没有 `source/herdr-dispatch/` 前缀。** 需从旧提交读取内容，
写入当前对应位置，避免把文件恢复到错误目录。例如在工作区干净时，从仓库根运行：

```bash
python3 - <<'PY'
from pathlib import Path
import subprocess
version = 'workflow-v1.0.0'
for rel in ('skills/dispatch-codex/SKILL.md', 'skills/_shared/plan.md',
            'skills/_shared/supervise.md', 'skills/dispatch-codex/references/driver.md'):
    content = subprocess.check_output(['git', 'show', f'{version}:{rel}'])
    Path('source/herdr-dispatch', rel).write_bytes(content)
PY
git diff -- source/herdr-dispatch/skills
```

检查 diff 后，只暂存这四文件并提交一次回退。以后新建的 `workflow-v*` 标签采用完整工程布局；
选择回退方式以目标标签的实际文件树为准。原文件版本说明仍能在旧标签中找到。
