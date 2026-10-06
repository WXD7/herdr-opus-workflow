# Supervision — §6b, §6e–§6f, §6i, §7, §8 (agent-independent)

Shared by every `dispatch-*` skill in this plugin. The sweep alternates between this file and the
calling skill's `references/driver.md`:

| § | Where | What |
| --- | --- | --- |
| §6a | driver | probe one lane's state from disk |
| §6b | here | poll the fleet, prove each lane is still yours |
| §6c | driver | classify each lane (the table, and its row order) |
| §6d | driver | steer a lane that is parked, blocked, or drifting |
| §6e | here | verify a lane that claims to be finished |
| §6f | here | publish a verified lane — push, open the PR |
| §6g | driver | handle a `blocked` lane / approval overlay |
| §6h | driver | compact or restart a lane |
| §6i | here | close the sweep |
| §7, §8 | here | arm the loop, report |

Apply §0 (in `SKILL.md`); reload rules only after context loss or an authorized version change.
Every sweep reads run state and proves identity before steering (§6b). For a lane whose
`state_change_seq`, `mtime`, branch HEAD and `DONE` marker all match what the last sweep recorded,
the compact probe is the whole evidence: do not re-read long files that have not changed or print
whole command outputs, and do not reload this file or the driver while their full text is still in
the session's context.

With a frozen experiment context, each group completes the same whole task independently.
Keep acceptance and the base commit fixed; never share another group's implementation or
quietly weaken its tests. Record actual model/effort separately from requested settings.
Use configured service leases for ports; do not ask a model to resolve collisions. Completion
reports trigger common checks, not automatic ranking or rule promotion. Dot notifications are
status evidence, not permission; retain the original scoped decision and recovery rules.

## The probe contract

§6c's table and everything below are written against the fields the driver's §6a promises. A driver
must report, per lane, at least:

| Field | Meaning | Missing when |
| --- | --- | --- |
| `session` | the agent's own id for this lane's live thread, as recorded in the state file | the agent exposes none — then the driver says what it uses for identity instead |
| `turn_state` | `working` (a turn is in flight) / `complete` (the last turn ended) / `unknown` | the agent's on-disk record cannot distinguish them |
| `used_pct` | percent of the context window in use, or `null` when the agent publishes no context window | — |
| `compactions` | how many times this thread has been compacted | — |
| `mtime` | last write to the lane's own on-disk record, for stall detection | — |
| `probe` | `ok` / `unavailable` with a reason | — |

Optional fields a driver may add — `goal_status`, `out_of_room`, `commits`, `todo` — are used only
by rows its own §6c table defines. **A field a driver does not report is never assumed**: a sweep
that cannot see `used_pct` does not guess one, it reports `null` and leans on `mtime` and
`turn_state` instead.

---

## §6b Poll the whole fleet in one call

`herdr agent list` returns every lane's `agent_status` and `state_change_seq` at once. Do not read
panes during a normal sweep — it costs context and tells you less than the lane's own on-disk record
does.

Names alone do not prove identity. Lane names carry no run id and are re-usable the moment an agent
exits, so a name in your state file can now belong to a later run's agent or to one the user started
by hand. Before steering any lane by name — prompt, send-keys, compact — confirm the session id
from `herdr agent get <lane>` (or the driver's own identity check, when its agent has no herdr-visible
session) still matches the one recorded in the state file (§6a). On a mismatch, stop steering and
diagnose — the three cases look alike and only one is yours to act on:

- the name resolves to an agent in some *other* workspace → a stranger re-claimed a released name.
  Never touch it (§0); surface the lane.
- the name resolves in the lane's own recorded pane, under a different id → not necessarily a
  stranger: the id also changes when a fresh thread starts in the same agent process (the driver's
  §6h restart recipe — or the user's own hand). If the state file shows a restart in flight, finish
  that recipe; otherwise surface it to the user instead of guessing, because steering someone else's
  thread and abandoning your own look identical from here.
- the name resolves to nothing → the agent exited. A non-terminal lane is relaunched per the
  driver's §5c on its recorded pane once it is back at a shell (§5a's checks apply again), then
  re-primed. The §6h restart recipe does not apply — it prompts a live agent, and there is none
  left here.

**Prompt guard — the one sanctioned exception to the no-pane-reads rule.** Immediately before *any*
input this sweep sends a lane — a slash command, a steering or continuation prompt — do ONE
`herdr agent read <lane> --source visible`. If a selection list or modal is parked there, send
nothing: a prompt submitted at a parked list presses its highlighted default. Resolve it first
(§6d/§6g). Preserve existing input drafts. Queuing into an active turn is allowed only by the
driver's explicit non-interrupting update rule; never infer support from an empty input box.

---

## §6e Verify a finished lane

`agent_status` is not evidence, and neither is the lane's own `.dispatch/DONE`:

    git -C <checkout> status --porcelain              # must be empty
    git -C <checkout> log --oneline <base>..<branch>  # must be non-empty

**Judge original requirements as well as lane criteria.** Read diff, progress and deviations.
Rerun the core user path, changed high-risk boundaries and checks assigned to the supervisor.
Other evidence needs verified command, outcome, exact HEAD, clean tree, criteria and relevant
environment/dependencies. Reuse matching baselines; they do not prove changed code passes.
Verify meaningful UI output, failure/retry and agreed interaction quality; API success cannot
replace UI acceptance. Evaluate structure by scoped before/after evidence, not line counts.
Separate facts, inference and unresolved causes. Budget exhaustion ends investigation, not an
acceptance obligation. Only the user may lower scope, quality or acceptance. Disclose gaps;
failed/missing acceptance leaves the lane open for one focused correction.

**Record evidence once.** For each criterion store outcome, command/evidence, source (your rerun or
reviewed evidence), `checked_sha` and relevant environment. Distinguish **passed / failed this run /
not run (why) / pre-existing failure (evidence)**. A pre-existing failure is not a pass; escalate if
it blocks a required criterion. Never repeat checks just for report formatting. A later sweep can
reuse acceptance only at the same HEAD with a clean tree, unchanged criteria and environment;
always recheck tree status. Changed code or environment invalidates the overall cached pass:
rerun affected checks; retain an unaffected criterion's older evidence only after independently
checking its dependencies and recording that rationale and original revision. Unchanged failures go to the driver's §6d,
not another identical test run. Delivery after integration needs checks relevant to the combined
tree; lane results alone do not establish integration success.

**Commit shape is a report line, not a gate.** Note mixed or non-revertible changes, but do not
hold delivery or ask for rewritten history merely to improve commit shape. One coherent commit
is sufficient for a small task or a user-requested single commit.

Only a lane that passes **every** one of these becomes phase `verified`, and only a `verified` lane is
eligible for §6f. Nothing unverified is ever pushed — that is the whole reason this step runs first.

---

## §6f Publish a verified lane — push the branch, open the pull request

**You publish; the lane never does.** Delivery stays behind §6e verification. A worker asking to
push has crossed its brief's boundary; surface it under §6g rather than approving it.

Publishing is **idempotent**. It runs on every sweep until it succeeds, so record `pushed_sha`,
`pr_number`, `pr_url` and `publish_attempts` in the state file and skip whatever is already done.

**1. Preconditions — degrade with a recorded reason, never with a guess.** Re-read the §2 preflight:

- the user confirmed local-only delivery (§3 `delivery`) → push nothing and open no PR. If that
  delivery names a local step for you to run, run exactly that step — locally, never rewriting
  history. If it does not complete cleanly, leave it undone and record `escalated` with the error
  and the exact command; route recovery to §3's decision owner, leaving delivery incomplete. A step the
  user keeps for themselves is reported as agreed and printed in §8. Once the confirmed delivery is
  complete, leave the lane `verified` and record `local-only delivery (confirmed)` in the field a
  degrade reason uses: nothing degraded, but that recorded reason is what makes the lane terminal
  (step 5);
- no `origin` → nothing to push to; leave the lane `verified`, report it as local-only;
- `gh` missing or unauthenticated, or `--no-pr` → do step 2, then stop and print the exact
  `gh pr create` command for the user;
- the PR base is not a branch on origin → do step 2, but do **not** open a PR against a substituted
  base. A PR aimed at a branch the lane did not fork from shows a diff that is not the lane's work.
  Report it and print the command with the base left for the user to fill in.

**2. Push exactly one branch, by explicit refspec:**

    git -C <checkout> push -u origin refs/heads/<branch>:refs/heads/<branch>

The refspec is spelled out on purpose: never `--all`, never `--force` or `--force-with-lease`, never
the base branch, never a branch that is not in the state file. A rejected non-fast-forward push means
something else moved that branch — stop, record it, surface it; force-pushing here would destroy
whatever moved it. No `--no-verify`: pre-push hooks are part of the repo's checks.

**3. Reuse an existing PR before creating one:**

    gh pr list --repo <owner/repo> --head <branch> --state all --json number,url,state

Non-empty → record it and stop; the push in step 2 already updated it. `gh pr create` errors out on a
head branch that already has a PR, and a sweep that treats that error as failure will retry forever.

**4. Write the body to a file, then create:**

    gh pr create --repo <owner/repo> --base <pr-base> --head <branch> \
      --title '<conventional-commit title>' \
      --body-file ~/.claude/<skill-name>/<run-id>/<lane>-pr.md

- Supply all shown arguments, including `--repo` from §2, to avoid an interactive prompt hanging
  a non-interactive call. Use `--body-file` for literal multiline text.
- **Ready for review is the default** and may trigger CI and reviewer notifications, as disclosed
  in §3. Use `--draft` only when requested; it does not change the verification requirement.
- **Title:** Conventional-Commits shaped, derived from the lane's objective — the commit subject when
  the lane produced exactly one commit, otherwise `<type>(<scope>): <objective>`, with the type
  agreeing with the branch's type prefix (a `feature` branch normalizes to `feat`). No run ids, lane
  names or raw branch strings in the title; those belong in the body.
- **Body, in English:** the checklist with its final tick state; a `Closes #<issue>` line when the
  lane carries an issue number, so the merge closes it; a short summary distilled from
  `.dispatch/progress.md` (that file is git-excluded per §5b, so the reviewer cannot open it — carry
  it over, do not link to it); the acceptance criteria with each one's verified outcome (§6e);
  any recorded deviation from the §3-confirmed plan, stated as such; and a provenance line naming
  the run id and the lane, and stating that **an agent wrote the code** while the orchestrator
  authored the plan and verified the result before push. Do not name the model, its version, or the
  approval posture the lane ran under — that detail belongs in the run's own state, not in a PR the
  whole repo reads. The reviewer should know what they are reading before they start reading it.

**5. Close the lane.** Record `pr_url` and set the phase to `published`. On failure, record the
stderr and bump `publish_attempts`; after 3 failed attempts stop retrying, record that as the
lane's degrade reason, leave it `verified`, and surface it with the exact command for the user to
run by hand. The recorded reason — from here or from step 1 — is what makes a `verified` lane
terminal for §7 and keeps §6c's `unpublished` row from re-matching it forever. A publish loop that
retries forever is worse than one that hands the command back.

---

## §6i Close the sweep

Rewrite the state file atomically (write `.tmp`, then `mv`). Emit **one line per lane** — lane,
phase, the driver's own status field, `used_pct`, compactions, turn state, PR number or `—` — never
raw JSON. This sweep runs many times; verbose output is what makes a long supervision run
unaffordable.

Record exceptions once (`escalated`, reason, decision owner, evidence, requested action and status).
Routine decisions belong to the supervisor (§3), not an automatic human queue. A delegated reviewer
must have a real authorized return channel; otherwise report the unavailable route. Reuse the
record until new evidence arrives; never treat notification as an answer or repeat identical asks.

---

## §7 Arm the recurring loop

Unless `--no-loop`, arm one `loop` job with this skill's resolving `--resume` invocation, e.g.
`/herdr-dispatch:dispatch-<agent> --resume`. Use the driver's cadence; absent one, use `5m`.
Record job id, interval and reason. Adjust only when the driver's condition changes: update the
existing job if supported, otherwise cancel that recorded job before replacing it. Never stack
timers; if cancellation is uncertain, report it instead of creating another.

Notify-back triggers timely completion checks. The timer recovers missed notifications, silent
blocks, stalls and idle unfinished work; preserve that fallback. Each event/tick performs one
sweep with compact state, reads only changed evidence, and reuses valid acceptance (§6e). A
duplicate wake with no new evidence or due recovery only records the no-op. These rules reduce
work after wake-up, not model calls already made; Markdown does not implement zero-model gating.

Tell the user in Chinese the interval, possible recovery delay and how to stop the loop. On a
resumed supervisor, record its current pane and re-arm only if no loop exists and actionable lanes
remain. `--no-loop` keeps notify-back but a missed ring or silently idle worker then requires a
manual `--resume`. Add no second model-driven heartbeat. Do not busy-wait inside a sweep.

Stop the loop once every lane is terminal or externally waiting with no action left for you;
record who must decide and why. Resolve routine supervisor decisions before declaring that wait.
Terminal means `published`, `failed`, user-paused, or `verified` with a recorded reason why §6f
did not publish it — a confirmed local-only delivery included. External waiting needs a recorded
unanswered escalation to its authorized owner. A lane whose work is done but whose branch is still
unpushed is **not** terminal unless its confirmed delivery is local-only, and the loop is what
eventually gets it out.

---

## §8 Report

Per lane: 分支, checkout 路径, 状态, 已完成/剩余清单项, commit 数（§6e 判定提交混杂、无法整体回退的，在这里标注一下）,
**验收标准逐条结果**（每条：通过 / 本次未通过 / 未运行（写明原因）/ 本 lane 之前已失败（给依据）；无法机械验证时给依据）,
该 agent 的运行情况（driver 的 §6a 报的状态字段、token/用量、有无暂停 / 限流 / 阻塞经历；驱动方式降级过的写明原因）,
是否偏离已确认的实施计划（有则一句话说明偏在哪、为什么）, compaction 次数,
**PR 链接**（未开成的写明原因，别留空；本地交付的写明已完成的本地步骤）.

Name the agent and version once at the top, and say plainly which side of the line each thing is on:
you **did** push the verified branches and open their PRs (§6f) — or, for a confirmed local-only
delivery, pushed nothing and ran only the confirmed local step; you did **not** merge anything
beyond that step and did not remove any workspace. Then print — do not run — the follow-up commands
that apply to each lane's delivery:

    herdr worktree remove --workspace <ws>             # destroys the checkout AND kills its agent process
    gh pr merge <pr-number> --squash --delete-branch   # per lane, after you have reviewed it
    git -C <repo> branch -d <branch>                   # only if the branch outlived the PR merge

The block is in that order on purpose: `worktree remove` runs **before** `gh pr merge
--delete-branch`, because git refuses to delete a local branch that is still checked out in a
worktree, and the merge command will report a partial success. Warn that `worktree remove` discards
uncommitted work in that checkout, and remember each lane may have **two** workspace ids to clean up
(§4). Only when the run used `--draft`, print `gh pr ready <pr-number>` ahead of the merge command:
GitHub refuses to merge a draft outright (`Pull request is not mergeable: it is in draft state`).
