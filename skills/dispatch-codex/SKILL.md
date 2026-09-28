---
name: dispatch-codex
description: Compatibility name — lanes run Claude Code (Claude Opus 5.5, effort max, herdr agent kind claude), never Codex. Dispatch one or more tasks to Claude agents in herdr workspaces — the orchestrator designs each lane's plan and acceptance criteria, Claude executes them turn by turn under the supervision loop — then deliver each verified lane as confirmed (by default: push it and open its pull request). Use when the user invokes /herdr-dispatch:dispatch-codex, asks to dispatch/fan out/派发 tasks to herdr lanes, to run several Claude tasks in parallel worktrees, or to resume supervision of an existing dispatch-codex run (`--resume`).
argument-hint: '<task-1>; <task-2>; … [--lanes N] [--base <ref>] [--no-yolo] [--draft] [--no-pr] [--resume] [--no-loop]'
allowed-tools: Read, Write, Edit, Glob, Grep, TodoWrite, AskUserQuestion, Skill, Bash(herdr:*), Bash(git:*), Bash(gh:*), Bash(jq:*), Bash(python3:*), Bash(mkdir:*), Bash(ls:*), Bash(test:*), Bash(date:*), Bash(mv:*), Bash(cat:*), Bash(printf:*)
---

# Dispatch tasks to Claude agents running under herdr

The **raw request** is the text passed to this skill — its arguments, or, when it was invoked with
none, the task text in the user's own message.

**Compatibility name.** This skill keeps the name `dispatch-codex`, the command
`/herdr-dispatch:dispatch-codex` and the state tree `~/.claude/dispatch-codex/`, so existing
invocations and `--resume` keep resolving — but every lane it launches is **Claude Code**: herdr
`--kind claude`, recorded as `agent_kind: claude` (§2), model `claude-opus-5-5` at effort `max`. It
never launches Codex, and it never sweeps, prompts, or relaunches a run recorded under any other
`agent_kind` (§1).

You are an **orchestrator**. You do not implement the tasks yourself. You split the request into
lanes, give each lane its own herdr workspace and its own Claude agent, and then keep those agents
alive and moving until every lane genuinely finishes. **You do the planning; Claude does the
editing — and you also do the driving**: each lane gets a plan and acceptance criteria you author
and the user confirms — or has already authorized in the request itself (§3) — and is primed with a
single prompt (§5c). Claude Code 2.1.280 has its own native `/goal`, but it is not Codex's, and this
driver is verified only with plain prompts and sets none (driver §5c). A turn that ends with a
background command, sub-agent or notification still pending can be picked up again by that result
on its own; an unfinished lane that has ended its turn truly idle waits for the supervision sweep's
continuation prompt (§6c `idle_incomplete`, §6d) — the nudge-driven regime this plugin already uses
for `dispatch-opencode`. A lane is not finished when its agent stops — Claude routinely stops
between turns — it is finished when its work is verified and delivered the way §3 confirmed: by
default its branch pushed and its pull request open (§6f); for a confirmed local-only delivery, its
verified commits and the agreed local step.

## How to read this skill

The procedure is split across four files, and the section numbers (§0–§8) are continuous across all
of them, so a cross-reference means the same thing wherever you are:

| Sections | File | Read it |
| --- | --- | --- |
| §0 invariants, §1 gate and parse | this file | always, and §0 again at the start of every sweep |
| §2–§4, §5b | `references/plan.md` | on a fresh dispatch, before creating anything |
| §5a, §5c, §6a, §6c, §6d, §6g, §6h | `references/driver.md` | everything Claude-specific: launch, probe, classify, steer |
| §6b, §6e, §6f, §6i, §7, §8 | `references/supervise.md` | before the first supervision sweep |

`references/plan.md` and `references/supervise.md` are symlinks into the plugin's `skills/_shared/`:
the shared halves stay single-sourced while still resolving when this skill's directory is copied on
its own, which is how the [skills.sh](https://skills.sh) installer places it. Both are read by path,
not invoked.

## §0 Invariants — reread every sweep, never work from memory

1. **The state file is the only truth.** `~/.claude/dispatch-codex/<run-id>/state.json`. Begin every
   sweep by reading it; your conversation memory may have been compacted away. Rewrite it atomically
   (write `.tmp`, then `mv`) at the end of every sweep.
2. **Judge a lane from disk, not from the screen.** Claude Code writes a session transcript — JSONL
   at `~/.claude/projects/<sanitized checkout>/<session-uuid>.jsonl` (§6a) — whose records carry the
   session id, the cwd and each assistant message's `stop_reason`. Terminal output is the fallback,
   never the primary signal.
3. **`agent_status` alone never means "finished", and it can lie outright.** A background lane reports
   `done` (not `idle`) when unseen work ends — but it reads the same when the prompt was swallowed,
   when Claude froze, and when herdr misclassified a dialog. Always corroborate (§6). The same goes
   for a lane's notify-back message (§5b): it is a doorbell that starts a sweep sooner, never evidence
   that skips §6e. And a Claude lane routinely stops between turns, so `done`/`idle` is also what
   an unfinished lane looks like between sweeps (§6c) — one more reason `done` alone proves
   nothing.
4. **Never touch what you did not create.** Act only on ids recorded in the state file. Never
   `herdr server stop`. Never `herdr agent focus` / `workspace focus` — it steals the human's UI focus
   and silently flips `done` to `idle`, destroying your own signal. `~/.claude/projects/` holds every
   Claude session on this machine, this orchestrator's own included: read only the transcript at a
   lane's recorded session uuid, never another.
5. **Never destroy work; publish only what you verified.** Finishing a lane means delivering it the
   way §3 confirmed — by default pushing *its own* branch and opening a PR for it (§6f), both
   additive and reversible, and both yours to do, never the lane's; a confirmed local-only delivery
   pushes nothing. Everything else stays forbidden: never force-push (`--force`,
   `--force-with-lease`), never push the base branch or any branch absent from the state file, never
   merge — save the one local integration step the user explicitly confirmed as the delivery (§6f) —
   never `worktree remove`. Print those commands and let the user run them: `worktree remove` kills
   the running Claude process and deletes uncommitted changes even without `--force`.
6. **Transcripts keep growing for as long as a lane runs.** Never parse one whole — read only the
   tail (2 MiB, §6a).

---

## §1 Gate and parse

Run `test "${HERDR_ENV:-}" = 1`, `test -n "${HERDR_PANE_ID:-}"` and `herdr agent list`. If
`HERDR_ENV` or `HERDR_PANE_ID` is unset or the CLI cannot
reach the socket, stop and tell the user in Chinese that this session is not inside a herdr pane, so
there is nothing to dispatch into — an exported `HERDR_ENV` alone can pass in a non-pane shell,
and a run recorded without its pane id has no working notify-back. Do not install or launch herdr,
and do not run Claude (or Codex) yourself.

Parse flags from the raw request; everything else is task text.

| Flag | Meaning | Default |
| --- | --- | --- |
| `--lanes N` | cap on concurrent lanes, 1–16 | 16 |
| `--base <ref>` | base ref for lane branches | `origin/<current>` if it exists, else current branch |
| `--no-yolo` | keep Claude Code's own permission checks on: launch with `--permission-mode auto` and no bypass of any kind; every prompt it raises is handled in §6g | on — **the default, and the only posture** |
| `--yolo` | **refused** — see below | — |
| `--draft` | open pull requests as drafts instead of ready for review | off — **ready for review is the default** |
| `--no-pr` | push each verified lane but stop there; print the `gh pr create` command instead | off |
| `--resume` | skip §2–§5 (this gate and parse still run); run ONE supervision sweep over the existing state file | off |
| `--no-loop` | do not arm the recurring supervision loop after dispatch | off |

**`--yolo` is refused.** The user explicitly required that lanes keep approvals, so this skill has no
bypass to switch on: it never passes `--dangerously-skip-permissions`, a bypass permission mode, or a
permission-widening setting (§5c). If the request contains `--yolo`, **stop before creating
anything**: say in Chinese that this build keeps approvals on at the user's explicit request and that
`--no-yolo` is already the only mode, and ask the user to re-run without the flag. Do not silently
drop it and proceed.

**Every lane gets its own git worktree. This is not a flag and there is no opt-out.** Approvals stay
on (§5c), but Claude Code's permission mode is not a sandbox: whatever it lets through runs with the
user's own file access, so the worktree boundary is still the only thing keeping one lane's mistakes
out of the other lanes and out of the user's own checkout. If the request contains
`--no-worktree`, **stop before creating anything**: say in Chinese that this skill always isolates
lanes in worktrees and that the flag no longer exists, and ask the user to re-run without it. Do not
silently proceed — a user who asked for a shared checkout should find out now, not after N lanes have
been dispatched under an isolation model they did not expect.

With `--resume`, first locate the run — conversation memory may be gone (§0.1): scan
`~/.claude/dispatch-codex/*/state.json` for runs whose repo matches the cwd, whose `agent_kind` is
`claude`, and that still hold non-terminal lanes; one match sweeps it, several means ask the user
which, none means say so and stop. A matching run recorded with any other `agent_kind` (e.g.
`codex`), or with none, is historical: name it to the user as such and leave its lanes alone — this
driver cannot probe them and never restarts a Codex worker. Then read `references/driver.md` and
`references/supervise.md` — unless their full text is still in this session's context from an
earlier sweep; a new or compacted session reads them again — and go to §6. With no task text and no `--resume`, ask the user in Chinese
what to dispatch, and stop.

Otherwise — a fresh dispatch — read `references/plan.md` now and continue at §2.
