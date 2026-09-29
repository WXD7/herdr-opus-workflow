# Dispatch — §2 to §5b (agent-independent)

Shared by the plugin's dispatch skills. The calling `SKILL.md` owns §0–§1, its
`references/driver.md` owns agent-specific launch and recovery, and `references/supervise.md`
owns acceptance and delivery. Section numbers are continuous; **the agent** means the caller's CLI.

---

## §2 Repo, run id, state file

Resolve `git rev-parse --show-toplevel`. Empty means "not a repo" — there is nothing to branch a
worktree from, so stop and tell the user in Chinese that dispatch needs a git repo. Do not fall back
to sharing the cwd. If `git rev-parse --git-dir` and `--git-common-dir` differ you are in a linked
worktree; stop and ask the user to re-run from the main checkout.

Run `git -C <repo> fetch origin` first when a remote exists, and prefer `origin/<branch>` as the base, so
lanes fan out from fresh upstream rather than a stale local branch. With no remote, use the local
branch and say so in the plan.

Run id: six lowercase alphanumerics derived from `date +%s`. State dir
`~/.claude/<skill-name>/<run-id>/` — `<skill-name>` is this skill's own name, so two dispatchers
running different agents over the same repo never share a state tree. `mkdir -p` it. If another run
dir for this repo still holds non-terminal lanes, do not silently start a second one — ask the user
whether to resume, archive, or abort.

Before creating lanes, record `skill`, the driver's `agent_kind`, `flags`, `repo`, `base`, and
`orchestrator_pane` from `$HERDR_PANE_ID`. Put that pane id verbatim in each brief's notify-back;
workers never discover it with `herdr agent list`. A pane id identifies a location, not its occupant.
If the supervisor session ends or moves, the session-bound timer and old notify-back can fail;
a human `--resume` restores supervision and records the current pane (§7). Existing briefs can
still target the old pane, so the timer remains necessary for missed notifications.

**Publishing preflight.** By default a lane finishes with *you* pushing its branch and opening its
PR (§6f), so settle *now* — before a single workspace exists — whether that will be possible, and
record each answer in the state file (a run the user wants delivered locally only needs none of
them — record that instead):

- does `origin` exist (`git -C <repo> remote get-url origin`);
- does `gh auth status` succeed, and does `gh repo view --json nameWithOwner -q .nameWithOwner`
  resolve the target repo;
- is the **PR base** — the `--base` ref with any `origin/` prefix stripped — an actual branch on
  origin (`git -C <repo> ls-remote --exit-code --heads origin <pr-base>`).

Do not install `gh`, do not authenticate it, and do not create the missing branch. None of these
failing is a reason to abort: they only narrow what §6f can do, and §3 must tell the user up front
which half they will be finishing by hand.

**Agent preflight.** Ask the driver's §5a what it needs verified once per run rather than per lane —
typically that the agent's executable resolves and which version it is. Record the version string:
every "(verified)" claim in a driver was verified against a specific version, and a mismatch is
worth one report line in §8, not an abort.

---

## §3 Plan the lanes

**Settle what is open first.** Before planning, ask about any unresolved requirement that would
change the business objective, the boundaries or the acceptance — a plan built around a guess only
moves the question later. In the plan you present, keep what the user has confirmed apart from what
is still pending; a pending requirement is never planned as if it were settled.

Split the task text on numbered items, newlines, or `;` — whichever the user actually used.

Then **group** them. This is the most consequential judgement in the skill:

- Same lane when tasks touch the same files or module, when one depends on another's output, or when
  a human would review them together. Inside a lane the tasks are an ordered checklist.
- Different lanes only when they can run concurrently without editing the same files. Merge cost is
  decided here, not at merge time.
- Never exceed `--lanes N` (default and hard max 16). If grouping yields more, merge the most related
  ones and say which and why. Do not pad the other way: three independent tasks make three lanes.
- When lanes > 4, warn the user that all lanes share **one account for this agent**, so its rate
  limits are shared and can throttle every lane at once. The driver names what that limit looks like
  when it is hit.

**Scale to risk and reuse evidence.** Small work defaults to one lane and independent acceptance.
Each lane/subagent needs a distinct output and question. Pass known findings with revision and
environment; verify risky conclusions and reuse matching baselines. Record business cwd, exact
evidence directories, delivery defaults, diagnostic budget and authorized reviewer scope. Decide
routine implementation/recovery yourself; route only unresolved exceptions. New scope, lowered
acceptance or excess budget needs the user. Children inherit limits and report through parents.
Map original requirements without narrowing them. Record each exception's owner and actual return
channel; absent reply/channel is not approval. A reviewer is called only with explicit scope and
budget authorization; never self-invoke Astra. Reuse decisions only while their conditions hold.

Per lane derive:

- `slug` — a 2–3 word kebab-case summary of the lane's work (e.g. `add-owner-filter`,
  `fix-ws-timeout`): lowercase letters, digits and `-` only, starting with a letter (reword a
  digit-leading summary). Describe the work, not the mechanics — the slug is the readable part of
  everything the user sees: lane name, workspace label, worktree, branch. Slugs are unique within
  the run: two similar tasks get distinguishing words (`fix-login-web`, `fix-login-mobile`), never
  the same slug twice — the branch below drops `<n>`, so two lanes sharing a type and issue would
  produce the same branch name.
- `name` — `<slug>-<n>` with `<n>` the lane number, e.g. `add-owner-filter-1`. herdr requires
  `[a-z][a-z0-9_-]{0,31}`; shorten an overlong slug by dropping whole words from the end — still
  letter-leading, no trailing hyphen — until the name fits 32 chars. Names carry no run id and are
  released only when an agent exits, so check `herdr agent list` and `herdr workspace list` before
  dispatch; on a collision, shorten further and append the run id's *last* three characters
  (`<slug>-<n>-<xyz>`, still within 32 — the tail of a timestamp-derived id is its fast-moving end,
  the head barely changes between same-day runs), and re-check until the name is actually free.
- `type` — the branch-type prefix that best fits the lane's overall work, with the usual
  Conventional-Commits meanings: `feature` (or `feat` — interchangeable in the branch, but in
  commit messages and PR titles the Conventional-Commits token is always `feat`) for a user-facing
  feature, `fix` for a user-facing bug fix, `docs`, `style`, `refactor`, `test`, or `chore`. One
  type per lane; a mixed lane takes the type of its primary objective.
- `issue` — the GitHub issue number the lane addresses, read from the task text (`#123`, a full
  issue URL, or wording like "issue 123"). Never invent one: a lane whose tasks name no issue has
  none, and the plan table below is where the user can supply it before anything is created.
- `branch` — `<type>/<issue>-<slug>`, the slug serving as the alias (the issue's keyword) — e.g.
  `feature/1-init`, `fix/42-ws-timeout`. When the lane has no issue number, drop that segment:
  `<type>/<slug>`.
  Nothing in this name embeds the run id, so it can collide with a branch that already exists —
  locally, on origin, or checked out in another worktree. Check
  `git -C <repo> branch --list <branch>`, `git -C <repo> ls-remote --heads origin <branch>` and
  `git -C <repo> worktree list` before dispatch; on a collision, append the run id's last three
  characters (`<type>/<issue>-<slug>-<xyz>`).
- `tasks` — the ordered checklist.
- `plan` — the lane's implementation plan, authored by **you**. The lane's agent does not plan for
  itself in this skill — it executes the plan you hand it — so this must be executable as written:
  ordered steps naming the files or modules each one touches, key decisions with a one-line
  rationale each, and where a route is genuinely open, the fork named with the default you chose, so
  the user is confirming a direction, not discovering one later. Ground every step in the repo — a
  quick Glob/Grep/Read of the code it names, never guesswork. Steps, not prose: the lane executes
  this top to bottom and records any forced deviation in `.dispatch/progress.md` (§5b).
- `acceptance` — **your** explicit, checkable definition of done (验收标准). Use actual repo
  commands and expected outcomes, selected by impact; preserve user-required and business-safety
  checks. Name the critical criteria you will independently rerun (§6e), and evidence eligible for
  reuse. No automated checks means tree/diff criteria plus that disclosed limitation. For a
  user-facing change, cover the intended user path, meaningful output, failure/retry behavior and
  any agreed interaction quality. An API shortcut proves only that layer, not UI completion;
  an empty result needs a justified business reason, not just a successful status. Keep concrete
  product rules in this task's acceptance, not the generic workflow.

When structural simplification is in scope, name the duplication, coupling or responsibility to
improve and the before/after evidence. Reuse existing abstractions only where their failure
semantics fit; explain a necessary new entity. Neither line-count reduction nor more shared
components proves a better design. Record remaining structural debt without widening into a
whole-repo rewrite.

Present the plan in Chinese: the lane table (lane / 分支 / 包含的任务), and under each lane its
实施计划 (the ordered steps) and 验收标准 bullets. Below that, state in **three** lines, so the user
sees all of them before anything is created:

- **which agent and which approval posture** the lanes will run in — the driver's §5c supplies the
  exact wording, including what `--no-yolo` does and does not change for this agent. Never describe
  a safety boundary this agent does not actually have.
- **how each lane is driven** — the driver's §5c supplies this too: an autonomous objective the
  agent continues on its own, or a single prompt that the supervision sweep re-nudges (§6c
  `idle_incomplete`). Say which, because it changes how long a silent lane may legitimately stay
  silent.
- **what happens when a lane finishes** — by default
  `验证通过后自动 push 到 origin，并对 <pr-base> 开 PR（ready for review，会触发 CI 和 reviewer 通知）`,
  or the degraded form the §2 preflight actually found (`--draft` 开草稿 PR / `--no-pr` 只 push，PR
  命令会打印出来 / `gh 未登录，只 push，PR 命令会打印出来` / `PR base 不在 origin 上，只 push，PR 命令
  留待你补 base` / `无 origin，只能本地提交`). When the user asked for local-only delivery, say
  `只做本地交付：验收通过并完成约定的本地提交／整合即结束，不 push、不开 PR`, naming that local step
  and who runs it. Record the mode as the run's `delivery` in the state file — it is this run's
  choice, never a default carried over from an earlier run. Never promise a PR the preflight says
  you cannot open.

Then ask once with `AskUserQuestion`: 按此派发 / 计划或验收标准要改（在补充里说明改哪里） / 合并成
更少的 lane / 我来调整. Create nothing before that answer. The confirmation covers the lane split,
the plan **and** the acceptance criteria together — the confirmed `plan` and `acceptance` go
verbatim into each lane's brief (§5b), so dispatching without this answer would dispatch a direction
nobody agreed to. If the user asks for changes, rework the plan and ask again; do not start a
partial dispatch of the lanes they did not question.

One case skips the question: the user has already authorized this exact work with a clear scope —
typically a small task whose objective, boundaries, acceptance and delivery the request itself
settles, with nothing pending. Then present the plan as the record of what will run, say you are
proceeding on that authorization, and dispatch; the plan you presented is what §5b quotes. Asking
again for an authorization already given only stalls the run. It covers that scope and nothing
wider: a pending requirement, or anything the plan adds beyond what the user wrote, still goes
through the question.

---

## §4 One workspace per lane

**`herdr worktree create --cwd <repo>` creates TWO workspaces**, not one: the linked worktree *and* a
workspace for the base repo itself. So snapshot ids before and after and record every new one, or
cleanup will leak:

    herdr workspace list | jq -r '.result.workspaces[].workspace_id'   # before
    herdr worktree create --cwd <repo> --branch <branch> --base <base> --label <lane> --no-focus
    herdr workspace list | jq -r '.result.workspaces[].workspace_id'   # after — diff for new ids

`herdr workspace create --cwd <cwd>` is **not** an alternative here — it would point a lane at an
existing checkout, which §1 forbids. `worktree create` is the only way a lane is born. Nor is the
agent's *own* worktree feature (some of these CLIs have one): a lane must live in the worktree herdr
created and recorded, because that path is what §6e verifies and §6f pushes. The driver says so
explicitly where its agent offers one.

Read ids from the JSON, never guess — workspace ids are opaque handles like `w4B`, not `w1`:
`.result.workspace.workspace_id`, `.result.root_pane.pane_id`, `.result.worktree.path` (the lane's
checkout).

If a create fails because the name, label or worktree path is already taken — a §3 collision
surfacing at claim time, or a stale checkout directory neither list could show — rename per §3's
collision rule and retry once. On any other failure, record the lane as `failed` with the error,
continue with the rest, and report it at the end — one broken lane must not abort the dispatch.
Append each lane to the state file as it is created so a crash mid-fan-out is recoverable.

---

## §5b Write the brief into the lane's own checkout

(§5a pre-flights the pane and §5c launches the agent — both in the driver. Write the brief between
them: the file must exist before the agent is primed.)

`<checkout>/.dispatch/TASK.md`, kept out of git:

    mkdir -p <checkout>/.dispatch
    printf '.dispatch/\n' >> <checkout>/.git/info/exclude

Every lane has its own checkout, so `.dispatch/` never collides between lanes and needs no per-lane
subdirectory.

The English brief contains: **objective**, the exact confirmed **plan** and **acceptance**, a
`- [ ]` **checklist**, reusable **findings/baselines** (source commit, environment, command/result
and evidence path), and remaining questions. Execute in order; record justified implementation
deviations in `progress.md`. A change to scope or acceptance goes back to the supervisor before
proceeding; recording a deviation alone does not authorize weaker delivery. DONE requires every
criterion, not merely checked boxes. **Boundaries:** work only in this checkout, never `cd` to
the main checkout or touch another lane's files; commit but never push, merge or open a PR.
The supervisor verifies and delivers. Include the following commit and progress policies.

> **Commit policy.** Commit coherent, reviewable and independently revertible changes with
> Conventional Commits: `<type>(<scope>): <description>`. Separate unrelated fixes and substantive
> refactors from behavior changes when they are independent units; do not split a small change
> merely to match checklist items. Honor a user-requested single commit. Stage explicit paths,
> never `git add -A`; correct committed work with follow-up commits, never amend or rebase history
> the supervisor may already have read.
>
> **`.gitlock` protocol**, pinned to the **main checkout's absolute path**
> `<repo>` — each worktree has its own root, so a per-worktree lock would serialize nothing: before
> each commit, create `<repo>/.gitlock`; commit; delete it. If it already exists, another lane is
> committing — wait 30 s, re-check, and only commit once it is gone. Never delete a `.gitlock` you did
> not create.

Then the load-bearing part:

> **Progress protocol.** Keep `.dispatch/progress.md` current: after each checklist item, rewrite it
> with checklist state, meaningful changes, next action, decisions and check evidence (command,
> result, commit, relevant environment). Pass the brief's relevant findings and delegation rule
> (§3) to any native descendants. Keep this file concise enough to restore after compaction.
> When all items and criteria hold and `git status` is clean, write `.dispatch/DONE` with a summary.

`.dispatch/progress.md` carries more weight the less autonomous the agent is: for a lane driven by
re-nudges (§6c `idle_incomplete`) it is the only thing that tells the next nudge where to resume, so
the driver may strengthen this paragraph — never weaken it.

Then the notify-back, so a finished lane rings the orchestrator instead of sitting undiscovered
until the next §7 tick. Write it into the brief with `<orch-pane>` (the §2 `orchestrator_pane`),
`<run-id>`, `<lane>` and `<skill>` already substituted — the lane runs it verbatim:

> **Completion notify-back.** Immediately after writing `.dispatch/DONE`, run this exact command
> once:
>
>     herdr agent prompt <orch-pane> "[<skill> <run-id>] lane <lane> wrote DONE — run one <skill> --resume sweep now."
>
> If it errors or is rejected (e.g. `agent_blocked`), do not retry and do not investigate — the
> orchestrator also polls on a timer and will find `.dispatch/DONE` regardless. This is the only
> herdr command in your job: it targets only the pane named here, and you never read, prompt, or
> send keys to any other pane or agent.

(The herdr skill warns against asking for file output in an initial prompt. That guidance is about
*retrieving long answers*; here the files are a state protocol that must survive compaction.)

---

Now launch the lanes: the driver's §5c. Start every lane before supervising any of them, then read
`references/supervise.md` and continue at §6.
