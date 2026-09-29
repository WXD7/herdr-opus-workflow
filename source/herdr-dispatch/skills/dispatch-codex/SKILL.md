---
name: dispatch-codex
description: Dispatch tasks to Claude Opus 5.5/max agents in Herdr worktrees, with supervisor-authored plans, independent acceptance and confirmed delivery. The name dispatch-codex is retained for compatibility; it never launches Codex. Use for /herdr-dispatch:dispatch-codex, explicit Herdr task dispatch or parallel lanes, and --resume of an existing Claude run.
argument-hint: '<task-1>; <task-2>; … [--lanes N] [--base <ref>] [--no-yolo] [--draft] [--no-pr] [--resume] [--no-loop]'
allowed-tools: Read, Write, Edit, Glob, Grep, TodoWrite, AskUserQuestion, Skill, Bash(herdr:*), Bash(git:*), Bash(gh:*), Bash(jq:*), Bash(python3:*), Bash(mkdir:*), Bash(ls:*), Bash(test:*), Bash(date:*), Bash(mv:*), Bash(cat:*), Bash(printf:*)
---

# Dispatch tasks to Claude agents under Herdr

The supervisor plans, delegates, verifies and delivers; Claude Opus 5.5/max lanes implement in
worktrees. Compatibility names do not launch Codex. Native /goal remains disabled (§5c).
Routine decisions belong to the supervisor; delegated exceptions follow §3. Descendants inherit
its boundaries and report via parents. Completion requires acceptance and agreed delivery.

## How to read this skill

Section numbers are continuous. Read only the route needed; retain loaded rules while they remain
in context. A fresh or compacted session reloads the relevant files, not on every unchanged sweep.

| Sections | File | When |
| --- | --- | --- |
| §0–§1 | this file | entry and routing |
| §2–§4, §5b | `references/plan.md` | fresh dispatch, before creating anything |
| §5a, §5c, §6a, §6c, §6d, §6g, §6h | `references/driver.md` | launch, probe, steer or resume |
| §6b, §6e–§6f, §6i, §7–§8 | `references/supervise.md` | supervision and delivery |

Plan and supervise are symlinks to `skills/_shared/`; read them by path. Keep those shared rules
single-sourced. Version identification and mismatch handling are in driver §5a; maintenance and
rollback instructions are in the plugin's `WORKFLOW-VERSIONS.md`, not part of a routine sweep.

## §0 Invariants

1. **Read run state each sweep.** `~/.claude/dispatch-codex/<run-id>/state.json` records ownership,
   progress and prior actions; verify claims against disk. Update it atomically (`.tmp`, then `mv`).
2. **Use recorded identity and disk evidence.** Probe only a lane's recorded UUID and checkout
   (driver §6a); read at most the transcript's last 2 MiB. Never search other sessions by recency.
   Herdr `done`/`idle`, a notify-back, and `.dispatch/DONE` trigger inspection, not acceptance (§6e).
3. **Act only on this run's recorded targets.** Verify live session and pane before any input.
   Never `herdr server stop` or agent/workspace `focus`: focus changes the user's UI and can change
   status. Preserve drafts and permission dialogs (§6b/§6g); never touch another run's agents.
4. **Preserve work and delivery boundaries.** Only the supervisor delivers verified work as §3
   confirmed. Local-only means no push or PR. Otherwise publish only the recorded lane branch;
   never force-push, push the base, rewrite history, or merge except an explicitly confirmed local
   integration. Never run `worktree remove`; show cleanup commands for the user instead.
5. **Keep the execution profile.** Claude Opus 5.5/max, native subagents within the task's scope,
   no permission bypass or silent model substitution. Follow driver §5a/§5c for exact settings.

## §1 Gate and parse

Require `test "${HERDR_ENV:-}" = 1`, `test -n "${HERDR_PANE_ID:-}"` and a successful
`herdr agent list`. If any fails, stop and explain in Chinese that this is not a reachable Herdr
pane. Do not install or launch Herdr or an agent to bypass the gate; an exported environment flag
alone does not prove a pane or provide working notify-back coordinates.

The raw request is the skill's arguments, or the user's task text when there are no arguments.
Parse these flags; the rest is task text.

| Flag | Meaning | Default |
| --- | --- | --- |
| `--lanes N` | concurrent lane ceiling, 1–16; not a target | 16 |
| `--base <ref>` | lane base | `origin/<current>` if present, else current branch |
| `--no-yolo` | normal Claude checks via `--permission-mode auto` | on, the only posture |
| `--draft` | draft PR rather than ready for review | off |
| `--no-pr` | push verified branch, print PR command | off |
| `--resume` | one sweep of an existing run; skip §2–§5 | off |
| `--no-loop` | omit the recurring supervision timer | off |

Reject `--yolo` and `--no-worktree` before creating anything; explain in Chinese and ask for a
corrected invocation. This adapter retains approvals and a separate worktree for every lane.
Permission mode is not a filesystem sandbox; worktrees separate edits, with §5b's boundaries.

**Resume:** locate non-terminal runs whose recorded repo matches the cwd and whose `agent_kind`
is `claude`. One match selects it; several require a user choice; none means report and stop.
An explicitly named run must also match; never substitute another run. Other or missing agent
kinds are historical and left untouched. Read driver and supervise if absent from context, verify
the recorded workflow version (driver §5a), then do one §6 sweep. Do not repeat planning or launch.

**Fresh dispatch:** with no task, ask in Chinese what to dispatch and stop. Otherwise read
`references/plan.md` and continue at §2. Delivery and acceptance follow the current authorization,
never a previous run's defaults.
