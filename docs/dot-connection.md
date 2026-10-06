# Dot 连接同一工作流

配置入口、队列和结果统一；Dot 不负责重写四文档。当前已经实现本地 stdio / HTTP MCP
工具，以及持久化的 MCP Events 订阅与签名回调。协议测试不等于实际账号连接。

## 本机连接

```sh
./scripts/start-claude.sh --experiments --allow-repo /absolute/business-repo connection --write-plugin
```

输出的 `local_plugin_path` 包含 `plugin.json` 和 `mcp.json`，绑定当前 Python、仓库、状态目录。
这是本机生成文件，含本机路径，默认不提交。按当前客户端的本地插件流程添加／安装，
在新对话里确认能调用 `workflow_status`。本地任务可用 stdio；Dot 能否通过所连接的电脑
访问该工具，以客户端实际连接结果为准。此项目不修改用户全局 Codex 配置、不冒充已安装。

工具顺序：`workflow_create` → `workflow_get` → `workflow_update` →
用户确认范围及配置后 `workflow_start`。每次更新带 revision；启动重试复用 request_id。
`workflow_compare` 返回包括失败组的结果；`workflow_instruction` 受一次发送和频率限制。
复杂自然语言交给 Dot 转成相同操作，不加一个后台配置模型。

### 用已连接的电脑试跑

Dot 须先在其 Computers 设置中获得本机访问；云端电脑不能直接读取本机路径。
参见 [OpenAI：Dot 的电脑与应用](https://learn.chatgpt.com/docs/dots/computers-and-apps)。
连接后可由本机任务调用上述 MCP，或直接调用相同 CLI；不必先部署公网服务。

```sh
./scripts/start-claude.sh --experiments --state-dir /absolute/private-trial --allow-repo /absolute/business-repo prepare-launch --capacity 2
```

此命令只生成本次专属的 `launch-herdr.command`、`dashboard.command` 和 Herdr 配置。
打开前者会启动独立命名的新 Herdr 会话，由原生 `terminal.default_shell` 在协调 pane
启动现有 dispatcher；业务 pane 仍为普通 zsh，全部沿用原 workflow。没有伪造 pane 环境、
恢复旧会话或改全局配置。两种服务均有进程生命周期锁，重复打开不会出现第二个调度器。
这只是便捷接线，不是另一套 Agent 提示词或调度框架。

使用者在页面或 Dot 中确认同一草稿后才调用 `workflow_start`；重试复用 request_id。
先确认 `workflow_status.dispatcher_connected`，再核对各组实际会话与配置。
生成文件与通过单元测试不代表实际 Herdr 启动、模型可用或 Dot 账号连接已验证。
若客户端无本机访问／操作能力，报告缺失条件；勿改用云端另做一份业务实现。

## 云端事件

[OpenAI MCP Events 文档](https://developers.openai.com/plugins/build/mcp-events)说明 Dot
通过 MCP 2.0 订阅并接收 HTTPS 回调。[插件接线](https://developers.openai.com/plugins/build/plugins)
需要真实服务连接。当前只监听本机，不能把 localhost 地址直接当作云端已连接端点。
云端使用需部署鉴权 HTTPS 入口，将请求代理到 `/mcp`，保留 Bearer 凭据并重写 Host
为本机监听 Host；禁止把此开发服务直接暴露公网。也可按客户端支持的本机连接能力接入。

事件只有完成、失败、阻塞等实质状态变化，不发送模型巡检。订阅前校验签名挑战，
持久化订阅、有效期、稳定 event ID、接收结果；最多三次有限退避重试。回调只接受公开 HTTPS
地址，并防 DNS 解析后转向私网。2xx 仅表示送达，不能当作 Dot 已处理或修复完成。

本机须运行 `serve` 才会投递持久化事件（stdio 工具进程不另起常驻线程）。
现阶段未验证真实 Dot 注册／唤醒、HTTPS 部署和多人账号隔离。这个端点按单一本机所有者设计。
请勿把自测签名接收器或工具列表查询称作真实 Dot 演示。
