# 本地准备与启动

这是有运行经验的实验工作流源码包，不是复制后即开通服务的一键产品。
当前默认分支为 v1.3 开发快照。独立入口固定配置为 Opus 5.5 / max，实验入口可按组配置；
保留原审批。首次先用合成任务验证，不要把旧会话 resume 当作装载了新规则。

## 1. 环境与入口检查

需要 macOS/zsh（现有脚本的验证环境）、Herdr、官方 Claude Code CLI、
Python 3.13 和 Node.js 20+；LangWatch 本地部署另需 Docker。
保持你自己的官方登录。模型不可用时停止，不静默换模型。
历史兼容采集支持 Codex，但新版 worker 不依赖 Codex 执行。

在仓库根目录：

```sh
./scripts/start-claude.sh --check
```

该检查不启动模型、不读真实业务记录，也不验证遥测服务或账号模型权限。
脚本设置子 Agent 为同模型/max、深度上限 3、并发上限 3；这些是配置值，
不是要求开启 3 个 Agent，也不证明每个 CLI 版本都会采纳这些变量。
首次真实运行需核对会话中的实际模型与父子关系。

## 2. 配置隔离环境

`runtime/herdr.toml` 是模板。复制到忽略的本机配置并填写本仓库 worktrees 绝对路径：

```sh
python3 - <<'PY'
from pathlib import Path
import json
root = Path.cwd().resolve()
text = (root / 'runtime/herdr.toml').read_text()
text = text.replace('"/absolute/path/to/herdr-opus-workflow/worktrees"', json.dumps(str(root / 'worktrees'), ensure_ascii=False))
(root / 'runtime/herdr.local.toml').write_text(text)
(root / 'evidence').mkdir(exist_ok=True)
PY
```

先按 [LangWatch 说明](../observability/langwatch/README.md) 准备本地接收服务及私密配置。
新克隆没有凭据或已有 Docker 数据；不要套用别人的私密目录。
同一机器若已有部署，另选 Compose 项目名、端口和 Herdr session。

## 3. 用合成 demo 开启新会话

当前 `start-claude.sh` 会进入 `demo/`。为避免把整个 workflow 库当业务仓库，
先将 demo 初始化为独立实验仓库（下面只适用于首次、尚无 demo/.git 的副本）：

```sh
test ! -e demo/.git &&
git -C demo init -b main &&
git -C demo add README.md .gitignore number_utils.py text_utils.py tests &&
git -C demo commit -m "chore: synthetic dispatch baseline"
```

demo 中两个函数故意未实现；测试先失败是预期起点，不是 workflow 回归。
若本机没有 Git 提交身份，请先自行配置该实验仓库的身份。

从 workflow 根目录启动 Herdr：

```sh
HERDR_CONFIG_PATH="$PWD/runtime/herdr.local.toml" ./scripts/launch.command
```

进入 Herdr 的新空白 pane，确认是普通 shell，然后运行：

```sh
../scripts/start-claude.sh
```

在新 Claude 会话输入 [示例任务](examples.md)，沿用
`/herdr-dispatch:dispatch-codex`。主管按原工作流创建 lane/worktree，
记录 actual plugin path、Git 版本、dirty 状态、四文档哈希、run/session；
worker 通过原 driver 准备环境并执行。不会另开一套调度规范。

当前入口固定 demo，采集包装器也限制 cwd 位于本工程树内。
接入真实业务前须明确迁移位置、入口 cwd、worktree 路径、端口与验收，
完成一次入口适配；本版本不声称任意外部目录即插即用。

## 4. 版本与停止

发布新文档不代表已有 Agent 被热更新。保留旧 run 证据，用新会话/run 装载新版本。
按原 Skill 的 `--no-loop` 或已记录 timer 停止方式控制监督；
不要用关闭全局 Herdr server 的方式结束单个任务。
回退操作见 [版本说明](../source/herdr-dispatch/WORKFLOW-VERSIONS.md)。

## 多配置实验入口

v1.3 开发快照新增同一入口的 `--experiments` 模式。它支持允许范围内的外部业务仓库，
原独立入口仍从 demo 开始。配置 UI、真实 Herdr 派发与模型调用是三个不同状态，
请见 [配置实验](experiments.md) 和 [Dot 连接](dot-connection.md)。
