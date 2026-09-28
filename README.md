# Herdr Opus Workflow

WXD7 的私有工作流版本库，保存 **Herdr + 原 herdr-dispatch 插件 + Claude Opus 5.5/max
执行器 + LangWatch 采集与人工授权复盘**。沿用原规划、任务书、监督、恢复和独立验收体系。
命令 `/herdr-dispatch:dispatch-codex` 保留兼容名称，当前执行器是 Claude。

## 保存内容

| 路径 | 内容 |
| --- | --- |
| `source/herdr-dispatch/` | 完整原插件、四文档修订、许可证及其既有 Git 历史 |
| `scripts/` | 原启动入口、pane 环境准备、Claude 状态探针及测试 |
| `runtime/herdr.toml` | 本机 Herdr 配置快照 |
| `observability/` | 本地采集、复盘材料生成与人工授权策略；不自动调用模型 |
| `observability/langwatch/` | Docker Compose、固定镜像摘要、初始化/查询代码及采集适配器 |
| `demo/` | 合成示例源文件与测试；不含原示例仓库的 `.git` |
| `AGENTS.md`、`CLAUDE.md` | 原工程入口约定快照 |
| `SNAPSHOT.json` | 来源提交、四文档与配套文件哈希、排除范围和本机绑定说明 |

不包含业务仓库、worktree、运行报告、真实会话、数据库、Docker volumes、登录信息、
LangWatch 私密目录或下载依赖。测试中的 JSONL 是合成夹具。原说明中指向 `evidence/`
的历史验证链接保留在本机，未随本仓库上传。

## 版本与使用边界

- `main`：完整工程的版本分支；首个整体快照标签为 `bundle-v1.0.0`。
- `workflow-v1.0.0` / `workflow-v1.1.0`：原插件修改前/后的标签，提交和内容保持不变。
  这两个历史标签采用插件位于仓库根目录的旧布局，不能当作整个工程快照。
- 当前四份正文仍是 `workflow-v1.1.0`，本次归档未改变调度规则或执行配置。
- [版本比较、探索与回退](source/herdr-dispatch/WORKFLOW-VERSIONS.md)。

这是**源码与配置备份**，当前运行仍在
`/Users/wangxian/Documents/ChatGPT/开发/herdr-workflow-fresh-20260926`。
此次没有迁移、启动或重启业务 Agent、Herdr 或 LangWatch。

`driver.md` 的 `<ROOT>`、`runtime/herdr.toml` 的 worktree 目录仍指向原工程；
Herdr session、Compose project 和网页端口也保留原值。需要从新路径运行或另建并行环境时，
先明确部署目录并调整这些绑定，恢复本机依赖与私密配置，再用原入口启动全新会话/run。
不要直接用本副本的部署命令操作正在运行的同名环境，也不要把旧会话 resume 当作换版。

原始执行依赖包括本机 Herdr、官方登录的 Claude Code、zsh、Python 3.13、Node.js，
本地 LangWatch 部署另需 Docker。下载依赖和凭据不通过 Git 分发。

## 来源

插件来自 [bestony/herdr-dispatch](https://github.com/bestony/herdr-dispatch)，原 MIT
许可证保留在 `source/herdr-dispatch/LICENSE`。LangWatch 纯函数来源和哈希见
`observability/langwatch/instrumentation/upstream-source.json`，对应许可证已保留。
上游提交、原适配提交和本次副本核对依据见 `SNAPSHOT.json`。
