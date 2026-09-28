> 当前业务入口已恢复原 herdr-dispatch 系统，并适配 Claude Opus 5.5 / max worker。下面 Codex 采集示例与历史测试保留用于兼容；新 worker pane 使用 `scripts/prepare-claude-observed.zsh`。详见 [恢复记录](../../../evidence/restore-original-20260927/report.md)。

# 本项目的 LangWatch 接入

作用域固定为 `herdr-workflow-fresh-20260926`。只通过这些包装器启动的 Claude/Codex 会发送遥测。它们保留原始 `HOME`、`CODEX_HOME`、账号登录、模型和供应商设置，不执行 `langwatch instrument`，不写用户级 settings/config、全局 hook 或 AGENTS。

需要 Node.js 20+ 与 Python 3.11+（本机入口使用已安装的 `python3.13`）。CLI 版本：Claude Code 2.1.280、Codex 0.158.0。LangWatch 会话解析代码固定来自 npm `langwatch@1.18.0`。

## 私有配置

`../private/ingest-key` 是仅一行 token 的文件，权限必须是 `600`。`../private/endpoint` 是 LangWatch 根地址，当前为 `http://127.0.0.1:5560`。包装器只接受 loopback HTTP 地址。key 不写入源码、命令行参数或诊断输出。

包装器设置 `LANGWATCH_CLI_CONFIG=../private/cli.json`、`LANGWATCH_NO_DAEMON=1`，但通知适配器不需要执行 LangWatch CLI，也不读取其登录配置。完整 CLI 如另有使用，应始终使用这个项目私有配置。

先检查配置而不启动模型：

```sh
cd /Users/wangxian/Documents/ChatGPT/开发/herdr-workflow-fresh-20260926/demo
python3.13 ../observability/langwatch/instrumentation/launch.py --check claude
python3.13 ../observability/langwatch/instrumentation/launch.py --check codex exec
```

## 在 Herdr 中启动

每个新 pane 都显式放入项目包装器路径。Herdr 的新 pane 不保证继承主 Claude 的 PATH，不应依赖全局 shell 配置。

推荐从 `scripts/start-claude-observed.sh` 启动 supervisor。使用上游 `herdr agent start --kind codex` 时，不能在它的 args 中替换可执行程序：先在目标 worker pane 的当前 zsh 中 source `scripts/prepare-codex-observed.zsh RUN_ID worker:LANE`，核对该 pane 的 `command -v codex`，再运行原 agent-start 流程，并显式传 `--sandbox workspace-write --ask-for-approval on-request`。不传任何 bypass flag。

该 shell 检查只能证明解析路径；实际 Herdr launch 是否使用包装器，还须检查新生成的 launch context。可以先在 genuine Herdr pane 设置 `HERDR_LANGWATCH_PROBE=1`，再从 Herdr caller 用 `agent start ... -- --version` 做零模型探针：包装器写 `private/wrapper-probe.json` 后原 CLI 输出版本即退出。Herdr 返回未就绪是这种探针的预期结果，判断依据是新 probe 文件及其 run/role/cwd。随后 unset probe 变量。主代理已用真实 `herdr agent start --kind codex -- --version` 验证 wrapper 被执行且 run/role/cwd 正确，临时 pane 已清理；证据在 `../evidence/herdr-wrapper-probe.json`，没有启动模型。

```sh
# Supervisor pane，先 cd 到本项目 demo。
env HERDR_LANGWATCH_RUN_ID=herdr-demo-20260926 \
  HERDR_LANGWATCH_ROLE=supervisor \
  PATH="/Users/wangxian/Documents/ChatGPT/开发/herdr-workflow-fresh-20260926/observability/langwatch/instrumentation/bin:$PATH" \
  claude --plugin-dir /Users/wangxian/Documents/ChatGPT/开发/herdr-workflow-fresh-20260926/source/herdr-dispatch

# 每个 worker pane，先 cd 到该项目内对应 worktree，再用同一 run id。
env HERDR_LANGWATCH_RUN_ID=herdr-demo-20260926 \
  HERDR_LANGWATCH_ROLE=worker:slugify \
  PATH="/Users/wangxian/Documents/ChatGPT/开发/herdr-workflow-fresh-20260926/observability/langwatch/instrumentation/bin:$PATH" \
  codex --sandbox workspace-write --ask-for-approval on-request
```

也可直接调用 `instrumentation/bin/claude` 或 `instrumentation/bin/codex` 的绝对路径。两者接受原 CLI 参数。CLI `--help`、`--version` 直接交给原程序，适合 preflight。默认角色分别为 supervisor、worker；显式 `HERDR_LANGWATCH_ROLE` 只作用于本次启动，不向下一层子进程传递。同一工作流用相同的 `HERDR_LANGWATCH_RUN_ID`。

## 采集方式及关联

- Claude：启动环境开启原生增强 telemetry，OTLP HTTP/JSON 分别导出 traces、logs、metrics。开启 user prompts、assistant responses、tool details、tool content。默认关闭 raw API bodies；`HERDR_LANGWATCH_RAW_API_BODIES=1` 才开启完整原始请求/响应。开启它可能重复记录大量上下文；关闭后仍保留上述显式内容事件，但不保留完整 API envelope。实际终端回放完整度仍需用真实会话验收。
- Codex：每次调用通过 `-c otel=...` 和 `-c notify=...` 覆盖遥测，`--no-daemon` 避免共享后台 app-server 混入项目作用域。保留原用户配置及登录。HTTP headers 由 `OTEL_EXPORTER_OTLP_HEADERS` 提供，因此 ingest key 不出现在 argv。
- 两种包装器都在现有 `NO_PROXY` / `no_proxy` 末尾补 `127.0.0.1,localhost,::1`，只确保本地接收器直连，保留已有代理与其它 bypass 规则。首次真实 Codex 烟测出现 native exporter HTTP 502 而 Node notify 成功；主代理调整后的真实复测已送达原生 spans/logs，独立验收核对两个原生 usage spans 的 input/output/cache 合计与 CLI 一致，见 `../evidence/smoke-verification.json`。原平台汇总仍有下述兼容差异。
- Codex 原 notify 从用户 config 与选定 `--profile` 文件读取，并考虑命令行 `-c notify=...`；保存在私有 launch context。适配器先启动原通知，再做遥测网络请求，采集超时不延迟原业务通知。
- Codex 通知适配器只读取收到的 `thread-id` 对应 rollout，不扫其它会话做导入。进一步检查官方解析器读出的 session id 与 cwd。使用原版算法补最后三轮，保留原 trace id 与派生 span id，使重复通知可合并；不读取或复制认证文件。

`OTEL_RESOURCE_ATTRIBUTES` 与 Codex span attributes 包含 `herdr.run_id`、`herdr.role`、`herdr.launch_id`、`project.repo`。Codex 补全还加入 `session.id` 和 JSON 格式 `langwatch.metadata`。原生 CLI session 标识继续由 CLI 自己提供，不伪造它们。

`../private/contexts/<launch_id>.json` 记录本次启动关联；`../private/notifications.jsonl` 只记 session id、trace ids、状态和 HTTP code，不记 prompt、密钥或响应内容。`rollout_missing`、`no_turns`、`capture_failed` 不能算作会话补全成功。通知未触发、CLI `--ephemeral`、rollout 格式不匹配都会影响补全。

每次 Codex notify 还会调用 `session_ledger.py`，直接复用现有 `observability/collect_run.py` 的 `load_jsonl` / `parse_codex`，生成 `../private/session-ledgers/<session_id>.json`。它按精确 session 和 response id 去重 usage，再与 cumulative delta 交叉检查；只从结构化 `event_msg.item_completed.CommandExecution.exit_code` 记录命令退出码，不把工具 RPC 成功或代码中的 `SystemExit(...)` 当作执行结果。外层 tool call id 与 command item id 没有显式关联时保持未知。

旁账包含 source 文件摘要与 `source_timestamp`、response ids、tool call ids、明确退出码、完整性状态与 `cost: null`。补全 span 只增加 `herdr.observed.*` 属性和 `langwatch.metadata.observed`，完全不写 `gen_ai.usage`，不替换平台原汇总。数值是**整个精确 session 的快照**，不可跨 span 累加，也不能据此推断订阅实际账单。相同源文件重放得到相同 ledger hash；每次原子覆盖同一 session 文件，避免旁账自己累加。

独立 API 验收发现 LangWatch 3.17 虽对同 span 重放返回 HTTP 200，却保留已有 span 的旧属性，见 `../evidence/fixed-replay-verification.json`。因此每次 notify 同时在最新已知 trace 中加入明确名为 `Herdr observed session snapshot (no model)` 的兼容 span，`langwatch.span.type=span`，不含模型调用、`gen_ai.usage` 或 cost 计量。span id 从 session id 与 ledger hash 确定生成，相同快照始终同一 id。它是无自造 parent 的旁账观察，时间取源文件最后记录的 `source_timestamp`，start=end，不声称有模型执行时长。所有用量字段都位于 `herdr.observed.*` 和独立 metadata 中。

该兼容 span 已通过真实完成会话的独立 API 重放验收：首次仅新增 `18cdb4582de2a9bb`，随后重复回放仍为 583 个唯一 span，snapshot 仅一个、`type=span`、`metrics=null`，API 可见旁账 input 33038 / output 80 / cached 29312 / exit `[7]`。两个原生 usage spans、平台原 summary 与六个 session 事件均保持不变；ledger hash 也稳定。证据在 `../evidence/ledger-replay-verification.json`。这次验证没有调用模型。

首次真实完整采集的 Codex 0.158 / LangWatch 3.17 组合中，两个原生 usage spans 合计为 input 33038 / output 80 / cached input 29312，而平台 summary 只呈现首请求；shell exit 7 也没有成为平台 summary error。旁账明确保留这项兼容差异，不把更丰富的独立观察伪装成平台已修复。`capture(context, payload)` 可做无模型、无原 notify 链的补全重放；审计时可在 context 指定精确 `rollout_path`，避免任何目录枚举。

## run 标签格式与生效时机

`herdr.run_id` 只由 `run_id.py` 规定。新默认值是 `run-<带连字符的 UUID>`；`scripts/start-claude.sh` 用主管 session UUID 生成，精确 `--resume` 同一 session 得到同一标签。旧格式 `run-<32位hex>` 是一整段十六进制，LangWatch 3.17 的 CRYPTO 规则会把其中形似比特币地址的（以 1 或 3 开头且其后没有 0，约 2%）存成 `run-[CRYPTO]`，例如 tm0ivs 的 `run-18b2d22b75ce44448e9b89e8735e951f`。入口、两个 `prepare-*-observed.zsh` 和 `launch.py` 都把旧格式确定性地改写为同一 UUID 的标准写法 `run-18b2d22b-75ce-4444-8e9b-89e8735e951f`：不同旧 ID 不会合并，已标准化的标签不变，`tm0ivs`、`herdr-demo-20260926` 这类自定义标签原样保留。发生改写时 prepare 脚本打印对应关系，launch context 另记 `legacy_run_id`。隐私规则不变。

生效时机：

- 只对修复后新启动的进程生效。进程环境在启动时固定、不会热更新；修复前已启动的主管和 worker 在退出前继续发送原标签，旧格式在平台上仍可能是 `run-[CRYPTO]`。
- state 已记录旧格式时，之后新启动的 worker 和以 `--resume <同一 session>` 重新进入的主管发送同一个标准标签，state 原值不改写。`collect_run.py` 仍按原值输出 `telemetry_run_id`，另给 `telemetry_run_id_mapping`，注明规范候选值只是本地推导，未读取、也未回填平台。
- 修复前 source 过旧版 prepare 脚本的 pane 仍保留旧值；之后经 wrapper 启动时 `launch.py` 已发送标准值，重新 source 当前脚本可让 pane 输出一致。
- `run-[CRYPTO]` 在入库时替换，原值不在平台里，不能凭空恢复或回填。历史 trace 用 session / trace ID、`herdr.launch_id` 与本地 state、launch context 对应；本修复不重发、不改写历史 trace。某个旧标签是否被替换，以实际查询为准。

回归测试：`python3.13 -m unittest observability/langwatch/instrumentation/test_run_id.py observability/langwatch/instrumentation/test_launch.py`。修复记录见 [run 标签修复报告](../../../evidence/langwatch-run-id-fix-20260927/report.md)。

## 官方代码复用与差异

LangWatch 一键接入会修改用户级 Claude settings、Codex config、hook，并可能启动自己的后台进程。原版 `ingest codex --notify` 必须从 `CODEX_HOME/config.toml` 的 LangWatch marker block 读取 endpoint/token，不能直接读取当前进程的 `-c` 覆盖。因此这里提取 npm 包里的纯 rollout parser 与 OTLP builder，逐字保存为 `official-rollout.cjs`，由薄适配器调用；不是另写完整 trace parser。

`extract-official.py` 可重现提取过程，`upstream-source.json` 记录 npm URL、版本、原 bundle 与所有提取片段的 SHA-256。适配器的差异仅为项目内定位和验证、本地 HTTP POST、关联 metadata、状态日志、原 notify 链接；不复用上游全局安装、会话上下文 spool、历史导入或 daemon。npm 包及缓存位于本目录 vendor 并已忽略。

官方依据：

- [LangWatch Claude 原生 OpenTelemetry 与手动环境变量](https://langwatch.ai/docs/coding-agents/claude-code)
- [LangWatch headless ingest key](https://langwatch.ai/docs/coding-agents/headless-and-ci)
- [Codex config 参考；项目 config 不允许 otel/notify](https://developers.openai.com/codex/config-reference)
- [Codex OTLP HTTP exporter 调用](https://github.com/openai/codex/blob/main/codex-rs/otel/src/provider.rs)
- [Codex 锁定的 opentelemetry-otlp 0.31.0](https://github.com/openai/codex/blob/main/codex-rs/Cargo.lock)
- [OpenTelemetry Rust 0.31.0 HTTP 实现](https://docs.rs/opentelemetry-otlp/0.31.0/src/opentelemetry_otlp/exporter/http/mod.rs.html)：配置 headers 后，读取 signal headers 或 `OTEL_EXPORTER_OTLP_HEADERS` 合并（源码第 212–216 行）。

## 已执行的验证

```sh
python3.13 -m unittest observability/langwatch/instrumentation/test_launch.py
node observability/langwatch/instrumentation/test_notify.cjs
python3.13 -m unittest observability/langwatch/instrumentation/test_session_ledger.py
```

六项 Python 检查和临时 loopback HTTP 接收器测试通过：账号目录/供应商变量保留、key 不进入 argv、原 notify 保留、角色不泄漏、权限检查、其它项目拒绝采集、官方 parser、末三轮行为、工具失败内容、trace/session metadata、response-id 去重、稳定旁账 hash、非模型 snapshot 身份。真实 Codex `features list` 带相同 `otel` 内联设置和 span attributes 能加载成功，无模型请求。主代理也完成了 18 项原观测基础测试。配置/合成测试与真实会话验收分别记录，真实证据见 `../evidence/smoke-verification.json`，Herdr 入口证据见 `../evidence/herdr-wrapper-probe.json`，汇总见 `../evidence/instrumentation-tests.json`。
