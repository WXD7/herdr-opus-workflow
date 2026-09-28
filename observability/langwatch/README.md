> **当前方案：原 herdr-dispatch 系统 + Opus 5.5 Max 执行器 + LangWatch。** 入口恢复为 `scripts/start-claude.sh`（`start-claude-observed.sh` 转发同入口）；命令仍为 `/herdr-dispatch:dispatch-codex`。原规划和监督共享规范保留，执行器通过原 driver 适配。此前独立 Opus 启动器方案已撤销。Astra 仍逐次确认后才复盘。

# 本机 LangWatch 接入

LangWatch 已部署在 **http://127.0.0.1:5560**。项目是 `herdr-workflow-fresh-20260926`，只接收本工程包装器启动的 Claude/Codex。官方登录继续由各 CLI 使用；这里没有配置模型 API 网关。

- [会话列表](http://127.0.0.1:5560/herdr-workflow-fresh-20260926-wnci3d/sessions)
- [Trace Explorer](http://127.0.0.1:5560/herdr-workflow-fresh-20260926-wnci3d/traces)
- [真实试跑与对账](evidence/smoke-verification.md)（[JSON](evidence/smoke-verification.json)）
- [启动适配器说明及测试](instrumentation/README.md)
- [Herdr 实际启动入口探针](evidence/herdr-wrapper-probe.json)

## 已验证与边界

Claude Code 和 Codex GPT-6 Astra 各执行了一个有意返回退出码 7 的小命令。Codex 首次原生 OTLP 请求被本机系统代理路径返回 502；项目启动器补充 loopback `NO_PROXY` 后第二次成功。此前失败的尝试仍保留在证据中，未当作成功或从成本统计中删除。没有增加任何模型心跳。

Claude 的输入、输出、缓存读取、缓存写入分别为 **34 / 163 / 6,505 / 6,725**，平台记录与 CLI 相符，工具失败和最终回复可回放。CLI 的模型是原配置的 `claude-fable-5-1`。

Codex 修复后的两个请求合计 **33,038 输入 / 80 输出 / 29,312 缓存输入**；缓存输入是输入的子集。入库的原始 span 按 ID 去重后与 CLI 相符。Codex 首次尝试另为 **33,113 输入 / 79 输出 / 29,312 缓存输入**，其原生遥测未成功入库，不能因此把它的模型用量当零。

**LangWatch 3.17.0 的默认面板汇总尚不能作为 Codex 优化决策的可靠账本。** 本次发现：默认汇总仅计到第一条请求；Session 页面可能选择没有终端内容的 `session_loop` trace；工具 RPC 的 `success=true` 不能代表 shell 退出码为零。Claude 的平台费用估价也与 CLI 原生费用存在差异。实际复盘应读取校正记录与原始证据，未知费用保留为空，不把订阅配额或估价写成账单。

接入适配器自动保存 `private/session-ledgers/<session-id>.json`，复用之前本地采集器的响应 ID 去重逻辑。LangWatch 内同一主 trace 会附上 `Herdr observed session snapshot (no model)`，以 `herdr.observed.*` 显示校正信息。其 ID 来自会话 ID 与快照哈希，重复发送同一快照不会新增一个观察记录。这是整会话快照，不能跨快照相加。

这样做是因为 3.17.0 对已经存在的 span 重发时没有更新新增属性；独立快照保留原始数据，也不再写一份 `gen_ai.usage` 造成双计。具体完整性与校验状态以试跑验证文件为准。一次短 Codex 任务就产生了数百条内部 span，它们不等于数百次模型请求；Astra 应先读去重后的响应计量与退出码，只在需要时取原始片段。

本次验证覆盖正常回合结束、真实命令失败、会话关联、末轮补全、用量对账与入口隔离；尚未验证强制杀进程、长时间离线补传、原生子代理全链路、长任务压缩/恢复。既有 Claude/worker 会话不会被事后自动注入新环境，旧试跑也没有批量导入。

## 启动和停止服务

从工程根目录运行：

```sh
./observability/langwatch/manage.sh up -d --pull never
./observability/langwatch/manage.sh ps
./observability/langwatch/manage.sh stop
```

`stop` 保留数据；重新 `up` 即可恢复。镜像已固定到 `images.env` 的 digest，不会因 `latest` 标签变化自动升级。独立 Compose 项目名为 `herdr-fresh-langwatch`，数据库、Redis、ClickHouse 无宿主机端口；只有网页绑定 `127.0.0.1:5560`。数据位于这个项目独立的 Docker volumes，不与其它工程共用。

已重建这五个容器并确认三条真实试跑 trace、校正用量、失败退出码与快照哈希均保留，见 [持久化验证](evidence/persistence-check.json)。每个容器的日志轮转限制为 10 MB × 3。一次空闲附近快照中，本套服务约占 2.4 GiB 内存；这是实测快照，不是长期容量保证。

当前只部署 app、workers、Postgres、Redis、ClickHouse。网页关于 `LANGWATCH_NLP_SERVICE`、`LANGEVALS_ENDPOINT` 的提示表示尚未部署工作流/在线评估引擎，不代表采集服务未启动。本次也没有配置外部模型评估器或自动优化任务。

## 新的业务运行如何被采集

先用原有 `scripts/launch.command` 打开 Herdr。在一个新的空闲主管 pane 中，从 `demo/` 运行：

```sh
../scripts/start-claude-observed.sh
```

这个入口现在直接加载原 `source/herdr-dispatch` 插件。`dispatch-codex` 是兼容命令名，worker 的实际 kind 为 `claude`、模型为 Opus 5.5 / max。原 driver 要求每个 worker pane 先 source `scripts/prepare-claude-observed.zsh`，再由 Herdr 启动 Claude；主管和 worker 共享 run ID，并记录各自 session 与角色。业务规划、任务书、监督及独立验收继续来自原 Skill。

此前 Codex 的真实 Herdr wrapper 探针仅证明历史 Codex 链路；当前适配检查与未实测范围见 [恢复原系统报告](../../evidence/restore-original-20260927/report.md)。`start-claude.sh` 与 `start-claude-observed.sh` 现在是同一条有采集的入口，不再作为两个不同执行体系。

## 本地凭据与复盘数据

本地面板账号保存在 `private/dashboard-login.json`。采集密钥仅有当前项目 `traces:create` 权限；读取密钥与管理密钥分开保存。已验证采集密钥读 trace 返回 403、读取密钥写 collector 返回 403。`private/` 为 700、凭据文件为 600，已加入忽略规则。

后续 Astra 观察器可在阶段结束或出现新异常时读取聚合校正记录，再按 trace ID 取必要片段。它不应周期性重新阅读全部会话，也不应仅凭面板 `success`、token 总数或费用估价自行改工作流。原来的 [受控复盘契约](../astra-review-contract.md) 继续适用；本次没有启用自动改写或无限巡检。

已经提供一次性只读查询入口，直接使用权限受限的读取 key，不调用模型、不轮询、不输出会话全文：

```sh
python3 observability/langwatch/query-trace.py 824af644f40361abbcf8545bef4d343f
```

返回示例见 [observer-query-example.json](evidence/observer-query-example.json)。它把平台默认汇总标为未验证，并单独返回最新校正快照；同一会话的多个快照不可相加。

## 版本与出处

- 部署镜像：LangWatch **3.17.0**，digest 及依赖镜像见 [images.json](evidence/images.json)。
- 查阅源码：`88566e6991a9326cd2b61b3ac4ac722a2f2eb2fc`；镜像与源码存在小版本差异，初始化脚本按实际镜像接口适配。
- Codex 补全解析：npm `langwatch@1.18.0` 官方纯函数，出处及哈希见 `instrumentation/upstream-source.json`。
- [官方本地部署](https://langwatch.ai/docs/self-hosting/deployment/docker-compose)、[Claude 接入](https://langwatch.ai/docs/coding-agents/claude-code)、[Codex 接入](https://langwatch.ai/docs/coding-agents/openai-codex)。
