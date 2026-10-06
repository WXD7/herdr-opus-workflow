# 项目入口

@AGENTS.md

主管调度直接调用原插件 `/herdr-dispatch:dispatch-codex`，按其 SKILL.md 与共享 plan.md / supervise.md 执行。该命令保留原名，执行器由其 driver 适配到冻结实验组配置（独立启动默认 Claude Opus 5.5 / max）；不使用额外自定义角色或另一套任务规范。

在 lane checkout 中接到 `.dispatch/TASK.md` 的执行者按原任务书工作，不把自己变成派发主管；原生子 Agent 在本 lane 的任务边界内协作。这沿用原共享 plan.md 的 worker 分工。
