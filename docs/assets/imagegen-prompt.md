# 图示生成记录

- 模式：内置 ImageGen；未使用 API/CLI fallback。
- 资产：`workflow-evolution-v1.2.png`。
- 日期：2026-09-29。
- 人工检查：署名、模型标签、四文档职责、四阶段箭头、LangWatch 观测边界和冷启动反馈。
- 图是方法说明；不代表递归 trace 覆盖、审批自动化或效率对照已经完成。

## 最终提示词

```text
Use case: scientific-educational. Create a beautiful high-resolution Chinese research-method diagram for a GitHub README, landscape 3:2, ivory white, navy text, teal for execution/evidence, amber for revision. Legible precise Simplified Chinese, professional scientific figure, generous whitespace. No fake benchmark claims.
Title "让工作流随证据演进"
Subtitle "Herdr Opus Workflow · v1.2"
A narrow provenance band under title: "衍生自白宦成（Bestony）的 herdr-dispatch"
Second line in band: "继承 Claude 调度 + Codex 执行的方法骨架，适配更新的 Opus 配置"

IMPORTANT: use a very simple 2×2 grid of FOUR large panels, clockwise flow. Use exactly FOUR main arrows with clear endpoints. Top-left panel → top-right panel → bottom-right panel → bottom-left panel → top-left panel. No extra feedback arrows. No crossing arrows. Main arrows between panels should be broad and visually unambiguous. Each panel title has a number.
TOP LEFT panel title "1 · 四份核心文档"
Inside show four compact document icons with labels "SKILL.md · 入口", "plan.md · 规划", "supervise.md · 监督与验收", "driver.md · 执行适配". Small line "保留原方法的工程标准".
TOP RIGHT panel title "2 · Agent 执行与独立验收"
Inside a small branching diagram "Opus 5.5 / max 主控" to "Worker / Worktree" to three small "原生子 Agent" icons, with small return links only to their parent. Small label "harness 原生委派 · 按需分工". Final line "独立验收 → 交付". This shows task execution.
BOTTOM RIGHT panel title "3 · LangWatch 运行证据"
Database and trace icons. Labels "run / session / 父子关系", "调用记录 · 错误 · 等待", "结合测试、提交与验收证据". Small line "仅观测，不授予权限".
BOTTOM LEFT panel title "4 · 复盘与版本化改进"
Four small steps in one vertical list: "按授权复盘", "识别重复工作与决策堵点", "小幅修改 · 验证", "Git 版本 · 可回退".
The upward main arrow from panel 4 to panel 1 is amber, labelled "下一轮冷启动加载". Other main arrows are navy/teal.
Footer in small clear text "演进对象是工作流文档，不是模型权重。原生子 Agent 的效率收益仍需实测。"
Second footer "自动审批唤醒通道：未启用"
No robot brand logos, no numerical performance data, no unbounded self-modification. Ensure 白宦成, LangWatch, Opus 5.5, harness spelled exactly.
```
