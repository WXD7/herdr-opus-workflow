# 运行证据与受控改进

执行层沿用 Bestony/herdr-dispatch 的规划、监督、独立验收，默认适配 Opus 5.5 / max，
实验组按冻结配置执行。
观测层收集本任务的数据；复盘层在明确授权后提出改进。三个职责分开，
不会因为有 LangWatch 就自动拍板或改写规则。

运行时的消息机制负责让管理会话知道“谁在等谁、决定是否生效”；LangWatch 与本地证据
负责支持过程核查和事后复盘。二者用相同身份关联，但遥测送达不等于决策已处理。
Dot 可作为可选消息网关，不是观测或四文档改进的必要依赖。[目标架构](../docs/architecture.md)

## 现有工具

| 文件 | 能做什么 | 边界 |
| --- | --- | --- |
| `collect_run.py` | 按 state 中的精确 session 和父子关系收集本地记录，去重 usage | 一次性快照；不是常驻增量服务 |
| `langwatch/instrumentation/` | 项目内 Claude/Codex 包装器、遥测关联、Codex 旁账 | 只采集包装器新启动的会话；保留官方登录 |
| `make_review_packet.py` | 从摘要生成有限证据包 | 不调用模型；超出大小限制会失败 |
| `gate.py` | 合成事件的确定性影子门控 | 不唤醒 Astra，不批准权限，不实施预算硬限额 |
| `astra-review-contract.md` | 一次复盘的输入、输出、质量门槛与预算约定 | 契约不是自动执行器 |

## 如何评价

先核对覆盖范围、run/session 对应和父子边，再读汇总。不要把“最近一个会话”当目标。
Claude 的 cache read / cache write 与普通输入分开；Codex cached input 是 input 的子集，
不能再加一次。相同 response/message ID 去重；整会话快照不可跨次相加。
缺失记录记作未知，不能当零成本。平台估价也不等于订阅账单。

测试、提交和验收结果需保留来源与版本，日志中的“我已完成”只是声明。
`away_summary`、文件 mtime 或高频状态消息不等于实际进展；
审批状态字段不等于现场仍有一个可回答的权限弹窗。

比较改进时同时看验收通过率、缺陷、总消耗、完成时间和人工介入。
将失败、重试、监督、复盘与实验成本纳入相应账本；没有受控对照就不宣称节省百分比。
原生子 Agent 的上下文隔离、重复工作和关联缺失也要计入观察，而不是只看 worker 数量。

## 使用

在工程根目录执行：

```sh
python3 observability/collect_run.py --state /absolute/path/to/run/state.json --out observability/runs/example
python3 observability/make_review_packet.py observability/runs/example/run-summary.json --out observability/runs/example/review-packet.json
python3 observability/gate.py observability/tests/fixtures/shadow-events.jsonl --out observability/runs/shadow-demo/decisions.json
python3 -m unittest discover -s observability/tests -v
```

state 必须来自本次任务并指向真实身份；仓库不分发真实会话。
后两个命令可用随附合成夹具运行，不消耗模型。

[LangWatch 配置](langwatch/README.md) · [复盘契约](astra-review-contract.md) ·
[一轮文档改进示例](../docs/examples.md)

## 从证据到下一版规则

复盘先定位问题属于业务实现、工作流规则还是消息基础设施，再提出有限修改。
将规则候选提交到 Git，记录四文档哈希，在新会话中与基线隔离比较后决定采纳或回退。
业务方案、模型配置、规则版本是不同实验轴；多项同时改时不能把效果归因于单项。
改进的目标是更好的业务质量和总成本，不是更多 Agent、更多文档或更多巡检。

## 当前实现边界

v1.3 开发快照已有局部 lane 决策往返和持久化事件投递代码，但顶层 Codex 管理关系、
完整状态识别、真实 Dot 订阅／唤醒与恢复闭环尚未完成验证，实跑仍有已知阻断。
持久化、去重和超时的局部实现不能证明所有会话可被可靠管理。
没有无人值守规则修改器、自动采纳／回退执行器或模型费用硬限额。
不得把合成门控测试说成真实 token 节省，也不得把 Markdown 规则说成运行时强制机制。
[开发版验证与已知缺口](../docs/release-v1.3.md)
