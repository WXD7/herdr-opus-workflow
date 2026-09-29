# 项目范围的 LangWatch 适配器

只经本目录包装器新启动、且 cwd 位于工程树内的 Claude/Codex 会发送遥测。
保留原始 HOME、CODEX_HOME、官方登录及供应商配置；不执行全局 instrument，
不写用户级 settings、config 或 hook。入口依赖 Python 3.13 与 Node.js 20+。

## 配置与启动

`../private/ingest-key` 是权限 600 的单行 token，`../private/endpoint` 是 loopback HTTP 根地址。
用 [初始化步骤](../README.md) 准备；不复制别人的密钥。
主入口 [start-claude.sh](../../../scripts/start-claude.sh) 固定模型配置；
每个 worker pane 先 source `scripts/prepare-claude-observed.zsh RUN_ID worker:LANE`，
再按原 driver 执行 Herdr agent start。PATH、角色与 run ID 必须来自该次运行，
不能依赖其它 pane 的环境或选择“最近的 session”。

在根目录检查配置（需先准备 private 配置，不启动模型）：

```sh
python3.13 observability/langwatch/instrumentation/launch.py --check claude
```

实际模型及原生子 Agent 配置由主入口和 driver 约束。旧 Codex 包装器仅保留兼容；
它的旁账/notify 行为不代表 Claude 已有相同的自动通知或审批能力。

## 采集、关联与计量

- Claude：原生增强 telemetry 导出 traces/logs/metrics，包含 user prompts、
  assistant responses 和工具详情。默认不发送 raw API bodies；
  `HERDR_LANGWATCH_RAW_API_BODIES=1` 才开启，可能大量重复上下文。
- Codex：进程级 otel/notify 覆盖，保留原 notify 链；按精确 thread ID 读 rollout，
  用固定上游纯函数补最后三轮，核对 session 与 cwd，不扫描其它项目历史。
- 两者补充 loopback NO_PROXY，避免本地遥测误走系统代理。key 放在 header 环境中，
  不放命令行 argv；私密 launch context 仍须按敏感文件保管。
- `herdr.run_id`、`herdr.role`、`herdr.launch_id`、`project.repo` 记录关联；
  原生 session 标识不伪造。Herdr 独立 worker 与原生子 Agent 是两类父子关系，
  只认实际证据，缺边时标未知。
- Codex `session_ledger.py` 按 session/response ID 去重，读取明确 shell exit_code；
  工具 RPC success 不能代替命令成功。整会话快照不能跨快照累加。
- `Herdr observed session snapshot (no model)` 是兼容旁账 span，
  ID 由 session 与 ledger hash 派生，不含模型调用或 gen_ai.usage，也不是原生父子边。
  重发同一快照不应新增计量。平台默认汇总可能和旁账不一致；费用未知保留 null。

私密 context、notification 状态日志和 session ledger 均在 `../private/`；
`rollout_missing`、`no_turns`、`capture_failed` 不算补全成功。
缺失尾部、强杀进程、长时间离线补传、递归链和恢复均应单独验证，
不能用普通回合成功代替全链路保证。

## run ID

`run_id.py` 是标签规则单一来源。默认 `run-<带连字符的 UUID>`；
入口用 supervisor session UUID 生成，精确 resume 保留映射。
旧 `run-<32 hex>` 可能被平台 CRYPTO 脱敏误匹配，包装器把它确定性地改为同一 UUID
的连字符写法，不关闭隐私规则。自定义标签保持不变。

修复只影响新启动进程，历史被替换的原值无法凭空恢复。历史核对使用 session/trace ID、
launch context 与本地 state；本工具不批量重发或回填历史 trace。
collector 中的规范化候选是本地推导，不是平台已更新的证明。

## 来源与验证

`official-rollout.cjs` 的纯函数来自 npm `langwatch@1.18.0`，保留
[来源与哈希](upstream-source.json)、[MIT 许可](LICENSE.langwatch)。
`extract-official.py` 在下载对应 npm 包到忽略的 vendor 目录后可重现提取，
不会运行其全局安装或 CLI main。

```sh
python3.13 -m unittest discover -s observability/langwatch/instrumentation -p 'test_*.py' -v
node observability/langwatch/instrumentation/test_notify.cjs
```

这些合成回归检查与真实模型/Herdr/服务端验收分开。
[本次发布结果](../../../docs/release-v1.2.md)不包含新增模型业务实跑。
