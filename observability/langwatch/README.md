# 本地 LangWatch

本目录提供固定镜像摘要的 Compose、一次性本地初始化脚本和项目范围采集适配器。
新克隆中没有已部署服务、面板账号、采集密钥或历史 trace。
官方模型登录继续由 CLI 管理，不经过新的模型 API 网关。

## 准备一个独立实例

需要 Docker。部署使用 3.17.0 时期验证过的接口和固定镜像摘要（见 `images.env`）。
镜像服务及其许可证独立于本仓库的 MIT 源码许可。

1. 复制 `config/*.env.example` 到忽略的 `private/`，分别去掉 `.example` 后缀。
2. 用独立随机值替换全部 `REPLACE_*`；同一数据库在两个文件中的密码必须一致。
   建议本地用 `python3 -c 'import secrets; print(secrets.token_hex(32))'` 生成，
   不把结果放进 issue、截图或 Git。设置目录权限 700、文件权限 600。
3. 默认项目名 `herdr-opus-langwatch`，网页只监听 `127.0.0.1:5560`。
   若端口已有使用，修改 Compose 的主机端口及 server.env 的三个网页根地址；
   初始化时用一致 endpoint。不要停止或重用别的项目来腾位置。
4. 首次拉取镜像并启动：

```sh
./observability/langwatch/manage.sh config --quiet
./observability/langwatch/manage.sh pull
./observability/langwatch/manage.sh up -d
./observability/langwatch/manage.sh ps
```

确认服务已正常就绪后，再初始化：

```sh
python3 observability/langwatch/bootstrap-local.py --project herdr-opus-workflow --endpoint http://127.0.0.1:5560
```

初始化脚本使用本地已验证接口注册面板用户、建立项目和用途分离的密钥，
只接受 loopback 目标；如果部署接口不匹配会报错，而不是绕过登录。
配置模板是公开部署参考，本次发布没有重新部署容器或做新实例端到端验收。

面板账号保存在 `private/dashboard-login.json`，endpoint、ingest-key、read-key
和 bootstrap state 也只在 private 内。使用自己的本地账号打开面板项目，
URL 中的项目 slug 来自实际初始化结果，不能复制别人的历史地址。

```sh
./observability/langwatch/manage.sh stop
```

stop 保留 volumes。不要随意 `down -v`；那会删除数据。
app、workers、Postgres、Redis、ClickHouse 都会占用本机资源；本配置未部署
LangWatch 的 NLP / 在线评估引擎，也没有启用模型优化任务。

## 启动与检查采集

用 [原工作流入口](../../docs/setup.md) 启动全新主管与 worker。
兼容名称 `dispatch-codex` 现在启动 Claude Opus worker；Codex 适配器保留用于历史兼容。
采集不改用户级登录、全局 settings/config 或 hook。

[包装器技术说明](instrumentation/README.md) 记录发送内容、ID 与旁账边界。
包装器会采集 prompt、response 和工具内容，因此先使用合成任务验证目的地与 payload。
公开仓库与私有运行数据分离；脱敏规则不是“保证无敏感内容”的承诺。

单次只读查询（不调用模型、不循环）：

```sh
python3 observability/langwatch/query-trace.py TRACE_ID
```

LangWatch 面板的默认 token/cost/error 汇总不能直接当优化账本。
检查原始 usage、命令退出码、去重旁账与覆盖范围。长时间离线、强杀进程、
递归子 Agent 全链路与压缩/恢复仍需在实际 CLI 组合上验证。

官方参考：[LangWatch](https://github.com/langwatch/langwatch)、
[本地部署](https://langwatch.ai/docs/self-hosting/deployment/docker-compose)、
[Claude 接入](https://langwatch.ai/docs/coding-agents/claude-code)。
