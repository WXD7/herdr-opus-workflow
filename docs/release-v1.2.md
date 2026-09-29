# v1.2 发布记录

日期：2026-09-29。规则标签：`workflow-v1.2.0`；
整体源码包：`bundle-v1.1.0`。这是独立 workflow 仓库的发布，
不会改写旧运行目录、业务仓库或已加载文档的会话。

## 改动

- 保留 Bestony 原工作流骨架、署名和许可证；说明 Opus 5.5/max、harness 原生子 Agent
  与 LangWatch 观测如何在其基础上改进。
- 四文档细化决策归属、API 错误诊断、进展判据、有限恢复和原始需求验收。
  不增加审批绕过，不自动调用 Astra，不安装未获批准的桥接草案。
- 更新 README、部署参考、三个合成示例、ImageGen 科研解说图和对应提示词。
- 清除当前说明中的个人绝对路径与私有运行报告链接；Herdr 采用本机配置副本，
  新 session 名和 Compose 名避免沿用旧实验名字；端口仍需使用者检查。
- 修复首次 preflight 缺 evidence 目录，以及不安装旧 Codex CLI 时的无关启动依赖。
  `--check` 展示四文档实际哈希，与发布清单不符时标记 modified。

## 可复现的检查

本次通过：

| 检查 | 结果 |
| --- | --- |
| `python3.13 -m unittest discover -s scripts -p 'test_*.py' -q` | 11 项通过 |
| `python3.13 -m unittest discover -s observability/tests -p 'test_*.py' -q` | 31 项通过 |
| `python3.13 -m unittest discover -s observability/langwatch/instrumentation -p 'test_*.py' -q` | 21 项通过 |
| `node observability/langwatch/instrumentation/test_notify.cjs` | 合成采集回归通过 |
| shell 语法、`git diff --check`、Markdown 本地链接、规则 symlink | 通过 |
| `./scripts/start-claude.sh --check` | 不调用模型；Opus 5.5/max、auto、四文档哈希匹配 |
| Compose `config --quiet` | 用临时目录中的公开模板验证解析；未启动服务 |

另验证了复制目录中的四文档被修改时，入口诊断会标记 modified，而不是继续宣称原版本。
一次 Opus 5.5/max 只读发布审查触及预设轮数上限，未形成可采纳结论；没有追加调用，
也没有把它算作独立审查通过。上表报告的是实际完成的检查。

四文档 v1.1 为 71,734 字符；v1.2 为 71,435 字符，减少 299 字符。
字符数按 UTF-8 解码后的 Unicode 字符计算，不是模型 token 数。
最终文件 SHA-256 见根目录 `SNAPSHOT.json`。

公开范围检查覆盖原有全部 9 个可达提交和 90 个不同文件 blob，以及此次待发布文件：
未发现所检查的常见 token/私钥模式，也未发现被跟踪的凭据、数据库或实际会话文件。
这是有限的静态检查，不是完整安全审计。旧提交与标签未重写，保留历史署名、
本机路径和既有公开来源链接；运行凭据、真实会话和业务仓库没有随本包分发。

## 验证边界

合成测试与配置解析不证明新版多 Agent 业务运行已经成功。
本次没有部署新的 LangWatch、启动业务 run 或验证任意外部业务目录即插即用。
demo 函数故意保留 TODO，供首次运行使用；它们的初始业务测试失败是预期基线。

自动审批/唤醒桥未启用：上一轮桥接安装曾被自动审批拒绝，未完成实现与端到端验证。
本发布没有通过其它工具或路径补装该方案。
原生 /goal、自动优化候选采纳、自动回滚也未启用。
新 Opus 配置与原生子 Agent 可能降低协调阻力，但尚无受控对照支持量化收益。
