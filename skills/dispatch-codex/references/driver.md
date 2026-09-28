# Claude driver (compatibility name `dispatch-codex`) — §5a, §5c, §6a, §6c, §6d, §6g, §6h

Everything in `dispatch-codex` that depends on the agent itself. The skill keeps its codex name for
compatibility only (`../SKILL.md`); every lane here is **Claude Code**, launched as herdr
`--kind claude` with model `claude-opus-5-5` at effort `max`. The agent-independent halves are
`references/plan.md` (§2–§4, §5b) and `references/supervise.md` (§6b, §6e, §6f, §6i, §7, §8);
§0 and §1 are in `../SKILL.md`.

`<ROOT>` below is the workflow project that owns the lane environment and the probe helper:
`/Users/wangxian/Documents/ChatGPT/开发/herdr-workflow-fresh-20260926`.

How each claim is marked:

- **(verified)** — confirmed on this machine for Claude Code **2.1.280**: that the §5c launch flags
  are supported, and the transcript facts §6a relies on — its location, the `sessionId` / `cwd` /
  `isSidechain` fields on its records, and `message.stop_reason: "end_turn"` ending a finished turn,
  read off real transcripts.
- **(herdr, verified)** — herdr CLI behaviour carried over from the codex-era driver; it does not
  depend on which agent runs in the pane.
- **(CLI)** — the installed `herdr agent` command lists `claude` among supported kinds; actual
  Claude startup under Herdr is not yet exercised by this adapted driver.
- **(unverified)** — not exercised end-to-end with Claude under herdr. Handle its failure; never
  assume it works.

§2 records the version it actually finds; a mismatch is a report line, not an abort. Nothing codex
owned carries over as fact: there is no rollout file and no goals database. Claude Code does have a
native `/goal` of its own, but it is not Codex's, and this driver does not use it yet (§5c).

---

## §5a Pre-flight the pane

A fresh worktree is not a working environment: no `node_modules/`, no `.env`, no `.venv`, because
those are untracked or ignored.

Once per run, record the launcher's `HERDR_DISPATCH_SUPERVISOR_SESSION` as
`state.orchestrator_session`, and its `HERDR_LANGWATCH_RUN_ID` as `state.telemetry_run_id`.
Read these exact environment variables from the supervisor shell; never choose a recent session.
The business `state.run_id` still follows the unchanged shared §2 algorithm. These two run ids
may differ: preserve their mapping, and use **telemetry_run_id** for every worker's collector
labels so supervisor and workers appear under the same LangWatch run. A resumed supervisor
updates its recorded session to the exact one the launcher supplied. Missing identity or
telemetry labels must be resolved before dispatch rather than guessed.

Local Git compatibility for shared §5b: linked worktree `.git` is a file. Resolve the original
`.dispatch/` exclusion target with `git -C <checkout> rev-parse --path-format=absolute --git-path
info/exclude`; do not concatenate `<checkout>/.git/info/exclude`. This only substitutes the correct
Git path; the original exclusion, task brief and commit standards are unchanged.

First prepare the pane's environment, in the lane's own pane (herdr started its shell in the lane's
checkout):

    herdr pane run <pane> 'source <ROOT>/scripts/prepare-claude-observed.zsh <telemetry-run-id> worker:<lane>'

`<telemetry-run-id>` is the recorded `state.telemetry_run_id`, and `<lane>` the lane's name. This is the only place the lane's
environment is set, and everything after it relies on the pane's shell keeping it. The script
(maintained in `<ROOT>` — not this skill's to write or patch):

- refuses to run outside a herdr pane or from a cwd outside `<ROOT>`, so lane checkouts must live
  inside that tree;
- exports the LangWatch labels (`HERDR_LANGWATCH_RUN_ID`, `HERDR_LANGWATCH_ROLE`) and puts the
  project's existing LangWatch wrapper first on `PATH`, then checks that `claude` resolves to it;
- exports the sub-agent profile — `CLAUDE_CODE_SUBAGENT_MODEL=claude-opus-5-5`,
  `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1`, `CLAUDE_CODE_EFFORT_LEVEL=max`, depth 3, 20 concurrent — so
  Claude Code's native Agent tool runs Opus 5.5 at max with no custom agent definition or role
  prompt. Depth and concurrency are ceilings, not per-task targets: the lane decides whether its
  task needs sub-agents at all; no plan or prompt demands a fan-out, and none turns them off
  globally.

Any error it prints stops that lane: report it, and never work around it — least of all by passing
an absolute executable path to `agent start`. The Claude CLI inherits the machine's official Claude
login; never sign in, set an API key, or copy credentials on the user's behalf.

Then, in the same pane:

    herdr pane run <pane> "claude --version"

Run the version, not `command -v`: it goes through the wrapper and the real CLI behind it — the path
`agent start` will take. Record it once per run (§2); this driver's (verified) claims are bound to
2.1.280.

Then poll `herdr pane read <pane> --source visible`. Two CLI traps here (herdr, verified):
`herdr pane read` with **no `--source`** returns empty output with exit code 0, and
`herdr pane wait-output` only matches output arriving *after* the call, so it times out on a command
that already finished. Poll `--source visible` instead of waiting.

- `claude` not found, or the version refuses to resolve → stop that lane and report it.
  `agent start --kind claude` launches `claude` through the pane's own shell and args cannot redirect
  it — the prepared `PATH` is what selects the wrapper.
- A login prompt, or a notice that the account cannot use the model → stop that lane and report it.
  Never fall back to another model.
- Dependencies missing → run the repo's install command in the pane and let it finish **before**
  starting the agent; `agent start` needs a pane at an idle interactive prompt.
- `.env` or other secrets missing → **ask the user** whether to symlink them. Never copy secrets.

Then write the brief (§5b in `references/plan.md`) before launching.

---

## §5c Launch Claude Code and prime the lane

**First give the lane its session uuid — before anything starts:**

    python3 -c 'import uuid; print(uuid.uuid4())'

Record it as the lane's `session` and rewrite the state file, *then* launch. The uuid is the lane's
identity for the rest of the run: it names the transcript §6a reads and is what §6h's resume path
hands back. Every launch attempt gets a fresh one — a retry never reuses a uuid an earlier attempt may
have started, because an `agent start` that timed out can still have left a live Claude on it. Keep
abandoned uuids in the lane's entry for the report.

    herdr agent start <lane> --kind claude --pane <pane> --timeout 120000 -- \
      --model claude-opus-5-5 --effort max --permission-mode auto \
      --settings '{"ultracode":false}' --session-id <uuid>

Why each part:

- `--kind claude` (CLI) — herdr's Claude agent kind; it runs `claude` through the pane's shell,
  where §5a put the LangWatch wrapper first on `PATH`. The state file's `agent_kind` is `claude`;
  only the skill's name is codex.
- `--model claude-opus-5-5 --effort max` (verified) — the fixed execution profile. Never substitute a
  model; if this one is unavailable, the lane stops and you report it — no silent downgrade.
- `--permission-mode auto` (verified) — the fixed permission mode. Claude Code's own permission
  checks stay on; which calls it clears by itself and which it raises as a prompt in the pane is
  Claude Code's decision and is not characterised here (unverified). Every prompt that surfaces is
  §6g's. This is the approval posture the user asked to keep, so pass **no** bypass on top of it —
  no `--dangerously-skip-permissions`, no `--allow-dangerously-skip-permissions`, no other
  `--permission-mode`, no permission rules through `--settings` or `--allowedTools`. It is not a
  sandbox either: the worktree plus §5b's boundaries are the containment.
- `--settings '{"ultracode":false}'` (verified) — part of the same fixed profile; pass it verbatim and
  add nothing to it.
- `--session-id <uuid>` (verified) — the uuid you just recorded, so the transcript lands at a path
  §6a computes instead of searching for.
- `--timeout 120000` — startup plus any MCP servers the checkout configures can outlast herdr's 30 s
  default.
- Nothing else. No positional prompt and no `-p`/`--print` (headless): the lane is primed through
  `herdr agent prompt` below, after the input box has been seen, so a startup dialog cannot eat the
  objective. No `--continue` or `--resume` on a first launch. No `--agent`, `--agents`,
  `--system-prompt*` or `--append-system-prompt*`: lanes use Claude Code's native Agent tool as it
  comes. No worktree option of Claude Code's own — the lane lives in the herdr-created checkout
  (`references/plan.md` §4). No absolute executable path.

**The three lines §3 owes the user, in this driver's words:**

- agent and approval posture — `Claude Code（Claude Opus 5.5，effort max，经项目 LangWatch wrapper
  启动；命令名 dispatch-codex 只是兼容保留）。审批保持开启：--permission-mode auto，不加任何权限绕过；
  需要确认的调用会在 pane 里弹出，由监督循环按 §6g 处理（lane 会停到下一轮 sweep），越出本 lane
  checkout 的一律交给你。auto 不是沙箱，worktree 是唯一隔离`（`--no-yolo` 就是这个唯一姿态，`--yolo`
  已在 §1 拒绝）;
- how the lane is driven — `以单次 prompt 启动。turn 结束时若还有后台命令／子 Agent／通知未回，结果
  到达后 Claude Code 可能自行续上；真正空闲而未完成的 lane 由监督循环（默认每 5 分钟）读 progress.md
  续跑。Claude Code 自带原生 /goal，但当前已验证的 driver 暂用普通 prompt、不设置 /goal，所以真正停下
  的未完成 lane 最长要等一个 sweep 间隔才继续`;
- limits — `所有 lane 共用本机同一个 Claude 官方登录账号，用量限额共享，可能同时卡住所有 lane；本
  driver 读不到机器可读的限流状态，被限流的 lane 表现为停止推进，由防空转计数（§6d）升级给你。上下文
  占用没有可靠数值（used_pct 恒为 null），监督循环不按百分比主动压缩`.

**Then verify readiness yourself.** `agent start` returning `agent_started` with
`agent_status: idle` and `interactive_ready: true` is herdr's reading of the screen, not proof the
input box is usable — with codex it reported ready over a trust modal (herdr, verified), and how its
detection reads Claude's startup screens is unverified. Read the pane:

    herdr agent read <lane> --source visible

- A workspace-trust dialog is expected on a fresh worktree (unverified wording): accept it for the
  lane's own checkout with `send-keys` matching the option displayed — never a remembered key — then
  re-read.
- Any other dialog, a login prompt, or a model other than Opus 5.5 on screen → answer nothing on the
  user's behalf: stop the lane and report it with the text quoted.
- Only once Claude's input box is visible, empty and idle is the lane ready for input.

On `agent_not_ready` the name still resolves — read, resolve the dialog, continue (herdr, verified).
If `agent start` failed or timed out, read the pane before retrying: a Claude screen means the launch
happened — continue with the readiness check on the recorded uuid, never start a second one; a bare
shell means it did not — read the error it left, and retry once with a **new** uuid.

**Then prime the lane** — one call, only after the input box is visible:

    herdr agent prompt <lane> "Read .dispatch/TASK.md in this directory and work through its checklist in order. Keep .dispatch/progress.md updated after every item — assume your context may be compacted at any time and that file is all you keep. Write .dispatch/DONE only when every checklist item is done, every acceptance criterion in TASK.md verifiably holds and git status is clean, then run the notify-back command TASK.md gives you."

This is the single-prompt priming this plugin already uses for opencode, verbatim. Record the lane
as phase `implementing`. This is the **nudge-driven** regime that
`references/supervise.md` §7 refers to: one Claude turn can run many tool calls and sub-agents; a
turn that ends with background work still pending can be resumed by that work's result, but a lane
that ends its turn truly idle with work unfinished stays stopped until §6d's continuation prompt. The first
sweep is also the landing check: a transcript that still does not exist then means the prompt never
reached Claude — read the pane once and resolve what is there (§6g) before re-sending it, at most
once.

**Native `/goal` — present in Claude Code, not used by this driver yet.** Claude Code 2.1.280, the
version this driver targets, has its own `/goal` (official docs:
<https://code.claude.com/docs/en/goal>): it keeps a session taking turns until a stated condition
holds. It is not Codex's — none of Codex's goals database, status names or pause/resume subcommands
apply — and the check at the end of each turn runs on Claude Code's separate small fast model
(Haiku by default on the Claude API), not on the lane's Opus 5.5. Pointing that check at another
model through `ANTHROPIC_DEFAULT_HAIKU_MODEL` also moves Claude Code's other background work, such
as conversation summarization, onto it. The user has allowed the lightweight evaluator, then asked
to stop dedicated adaptation/testing and start the business task immediately. Native goal has not
been integrated or exercised under herdr: this run uses the updated workflow with the plain-prompt
regime above. Set no `/goal` on a lane, leave that variable alone, and enable no automatic goal
loop; report this limitation and do not delay business work for another standalone smoke test.

**Relaunching an exited lane** (§6b: the name resolves to nothing) is not a fresh launch: follow
§6h's resume path — the recorded uuid back through `--resume`, on a pane confirmed to be at a bare
shell. A new uuid for a lane that already has a transcript is the user's call, never the sweep's.

Start every lane before supervising any of them.

---

## §6a Probe one lane

    python3 <ROOT>/scripts/claude_lane_state.py --session <uuid> --checkout <abs-checkout> [--transcript <abs-path>]

One python3 call instead of a shell pipeline, because Bash permission rules match per shell-operator
segment. The helper is maintained in `<ROOT>`: do not write, patch, or replace it; if it is missing or
errors, the lane's probe is `unavailable` for this sweep, with the error as the reason. `--checkout`
is the lane's checkout exactly as §4 recorded it (`.result.worktree.path`, absolute).

**Where the transcript is** (verified): `~/.claude/projects/<dir>/<uuid>.jsonl`, where `<dir>` is the
checkout's absolute path with every character other than an ASCII letter or digit replaced by `-` —
`/…/ChatGPT/开发/herdr-workflow-fresh-20260926` becomes `-…-ChatGPT----herdr-workflow-fresh-20260926`.
The helper computes it from `--session` and `--checkout`: an O(1) lookup, never a scan of other
sessions. Pass `--transcript` only when that path does not exist and one
`ls ~/.claude/projects/*/<uuid>.jsonl` finds this exact file elsewhere; record the path in the state
file. Never pick a transcript by recency or by content.

The helper reads only the **tail** (2 MiB, §0.6) and checks that what it reads belongs to this lane:
every record carrying a `sessionId` must carry this uuid, every `cwd` must be the checkout or inside
it, and only main-thread records (`isSidechain` not true) decide the turn — sub-agent records never
do. A failed check is `probe: unavailable` with the reason, never a best guess.

It reports the probe contract's fields:

- `session` — the uuid it read.
- `turn_state` — `complete` **only** when the newest main-thread message is an assistant message with
  `message.stop_reason == "end_turn"` (verified), and not when the file's last line is still being
  written. An assistant `tool_use` stop, or a trailing user prompt or tool result, is a turn in
  flight: `working`. An interruption, an API error, any other stop reason, or no readable message is
  `unknown` — surfaced, never scored as complete.
- `last_event` — the newest main-thread message as the helper names it (`assistant:end_turn`,
  `assistant:tool_use`, `user:tool_result`, `user:prompt`, `user:interrupt`, `assistant:api_error`,
  …), with `last_event_at`; §6c and §6h quote it.
- `used_pct` — **always `null`**. The transcript carries per-message token usage but no reliable
  context window to divide it by, and summing billed tokens does not measure what is in context.
  Never compute a percentage from them.
- `compactions` — `system` records with `subtype: "compact_boundary"` in the tail. Whether 2.1.280
  writes one for every compaction, manual or automatic, is unverified, and when the tail does not
  reach the start of the file the helper sets `compactions_partial` — the count is then a floor. §6h
  trusts it only as far as it says there.
- `mtime` — the transcript file's modification time, for stall detection.
- `probe` — `ok` / `unavailable`, with `reason`. It is the driver status field §6i prints (plus
  `last_event` when `turn_state` is `unknown`).
- `out_of_room` — true when the newest main-thread message is an API error saying the prompt is too
  long, or stopped with `model_context_window_exceeded`. No real context exhaustion has been run
  against it (unverified): `true` is `hard_fail`'s signal; `false` means only that no marker was
  found.
- `goal_status` — **always `null`**, kept for the probe shape only. This driver sets no native
  `/goal` (§5c), the helper reads no goal state, and no row in this driver reads the field. There is
  no `goal_tokens`: do not
  derive one from usage. For §8's token line, point to the lane's LangWatch labels (run `state.telemetry_run_id`,
  role `worker:<lane>`) instead of computing a figure here.

**Identity, for §6b.** The uuid is yours — generated and recorded before launch (§5c) — so it does not
come from herdr. Before steering, require `herdr agent get <lane>` to resolve to the recorded pane **and**
its live `agent_session` value to equal the recorded uuid, with `probe: ok` for that uuid. A matching
old transcript alone does not prove that the current pane occupant owns it. A different value is
§6b's "different id" case; if this Herdr version exposes no Claude session identity, mark identity
unverified and escalate without prompting, approving or compacting that lane.

---

## §6c Classify each lane, in this order

| Class | Test | Action |
| --- | --- | --- |
| `terminal` | phase is `published`, `failed`, user-paused, or `verified` with a recorded §6f degrade reason | Skip — report only; never re-verify, re-publish, or prompt a closed lane |
| `unpublished` | phase is `verified`, `publish_attempts` < 3, no recorded degrade reason, and `pushed_sha` is missing or behind the branch HEAD, or `pr_url` is missing with PRs enabled | Retry publish (§6f) — the work is done, only publishing is left |
| `done` | `.dispatch/DONE` exists **and** `turn_state == complete` | Verify (§6e — reuse a result only where §6e allows), deliver (§6f), mark terminal; a lane that already failed with nothing changed since goes to §6d's accounting instead |
| `blocked` | `agent_status == blocked` | Read `--source visible`, handle (§6g) |
| `blind` | `probe == unavailable` for two consecutive sweeps | The transcript is not answering for this lane — missing, or it failed the `sessionId` / `cwd` check. Read `--source visible` once, judge from git state, and escalate rather than steering a lane you cannot see |
| `hard_fail` | `out_of_room` (§6a), and no §6h recovery recorded in flight | No verified in-place restart exists here: one `/compact` per §6h if its gate allows; otherwise — or if it changes nothing — escalate |
| `stalled` | `state_change_seq` **and** `mtime` both unchanged ≥ 15 min | Read `--source visible` once: a dialog → §6g; an idle input box over unfinished work → the `idle_incomplete` action; a tool call or sub-agent still visibly running → leave it: a long build, test run or sub-agent is normal work. The same call showing for 60 min is only a point to diagnose and ask the user once in the sweep report — never a timeout: nothing stops, interrupts or fails the call on that clock, and a call still making real progress is not judged by its duration; otherwise escalate; never score as finished |
| `hot` | `used_pct ≥ 70` **and** `turn_state == complete` | Inert: `used_pct` is always `null` (§6a), so this row never matches — context is left to Claude Code, and the sweep never compacts on a guess |
| `idle_incomplete` | `agent_status` is `done`/`idle`, `turn_state == complete`, **and** no `DONE` file | **The engine.** Read `progress.md`, send a specific continuation prompt (§6d), record the nudge — unless §6b's guard read shows a background command or sub-agent still running: its result can resume the lane by itself, so send nothing this sweep |
| `working` | otherwise | Leave it alone |

An `unknown` `agent_status` is an anomaly to surface, never a completion — do not let it fall
through to `working`'s leave-it-alone. `turn_state == unknown` is surfaced the same way (with
`last_event`): never read as complete, and if it persists, `stalled` reads the pane.

**`idle_incomplete` is the common case, not the exception.** In the codex goal mode this row was a
repair path; here, as in `dispatch-opencode`, it is how a truly stopped lane moves on. Expect
unfinished lanes to match it often, and a stopped lane to wait up to one sweep interval for its next turn — that is
what §3's second line promised the user, and it is why §7's timer must stay armed. A lane with an
unanswered `escalated` record gets no continuation from this row or any other (§6d).

One known race, by design: a notify-back can arrive before the ringing lane's final turn closes (the
brief fires it right after DONE is written, mid-turn), so that lane may still read `working` with
DONE present — re-check it once at the end of the sweep, or leave it to the next tick; both are fine.

---

## §6d Steer a lane — writing the continuation prompt

This is the most consequential thing the sweep does in this skill. Every prompt goes through
`herdr agent prompt <lane> "…"` and is subject to §6b's prompt guard: read the pane first, and if a
dialog or selection list is parked there, resolve it instead of typing. If the input box already
holds text, send nothing and escalate — the new prompt would fuse onto it, and clearing the box is
unverified for Claude (§6h). A lane whose turn is still in flight gets no prompt at all — least of
all a bare "continue". No native `/goal` is set on these lanes (§5c): never send `/goal` — in
Claude's syntax or Codex's — or any other codex command, to a Claude lane.

**Read `.dispatch/progress.md` and `git log <base>..<branch>` first.** A bare "continue" burns a turn
re-deriving state the lane already wrote down. A good continuation prompt names, in one or two
sentences:

- the next unchecked checklist item, quoted from `progress.md`;
- the file or module the §3-confirmed plan says that item touches;
- the acceptance criterion it has to satisfy;
- and the reminder to update `progress.md` and, when everything holds, to write `.dispatch/DONE` and
  run the notify-back.

When `progress.md` is missing or stale (its checklist state contradicts `git log`), say so in the
prompt and ask the lane to rewrite it from the actual tree before continuing — an out-of-date
progress file misleads every later sweep, including the one that judges §6e.

**Anti-loop accounting is mandatory.** With prompts as the engine, a lane that has quietly stopped
responding looks exactly like a lane that is working. So with every nudge record, in the lane's state
entry: `nudges` (a count), the branch HEAD sha, the uncommitted work (`git -C <checkout> status
--porcelain` and `git -C <checkout> diff HEAD | git hash-object --stdin`), and a hash of
`progress.md`. Judge the nudge once the lane's turn has ended — the next sweep that finds it
`idle_incomplete` or `done`. A lane still inside its turn — a long build, test run or sub-agent — is
working, and its counter does not move. Then:

- progress → the nudge worked; reset the no-progress counter. Progress is a change in the work
  itself: a new commit, a changed uncommitted diff in the deliverables or business code, a new
  check result, or a blocker actually resolved — the last two as `progress.md` reports them, with
  their evidence.
- no progress → increment `no_progress`. A reworded `progress.md`, a restated checklist or message,
  or the same check re-run with the same result is not progress, however much text changed. At
  `no_progress == 1`, nudge once more, differently:
  quote the exact blocker if the pane shows one, or narrow the ask to a single file. At
  `no_progress == 2`, **stop nudging**: record `escalated` with the last two prompts and what
  `progress.md` last said, and report the lane as awaiting-user. Never fire a third identical nudge.
  From then on the lane gets no continuation and no `/compact` from any row until the user answers.

This accounting is also this driver's only answer to rate limits: a lane held by the shared account's
limits looks like a lane that stopped advancing, and escalates the same way. Never name a limit state
the probe cannot see.

**When the lane reports a real blocker** — a missing secret, a broken upstream, a contradiction in
the task — do not improvise scope. If the answer is inside the §3-confirmed plan (a decision the
brief already made, a misread step), send the correction as a normal prompt. Otherwise escalate with
the blocker quoted (record `escalated`).

**Pausing a lane** is a user decision, not a sweep's: record
`pause: {origin: user, at: <sweep time>}` in the state file and stop nudging it. A turn already in
flight runs to its end; nothing here interrupts it. Such a lane is terminal for §7 until the user
says otherwise.

---

## §6g Handle a blocked lane

This driver never bypasses approvals (§5c), so `blocked` is an expected state here, not an anomaly:
Claude Code raises a permission prompt for a call its `auto` mode does not clear by itself, and the
lane waits until a sweep answers. It can also be the startup trust dialog (§5c) or a herdr
misclassification — read the pane before assuming which:

    herdr agent read <lane> --source visible

Claude Code's permission prompts — wording, options, keys — are **(unverified)** under herdr in this
driver, so answer only with `send-keys` matching the option actually displayed. Never type a
remembered key, and never send a prompt at a parked list — it presses the highlighted default.

- Benign and inside the lane's own checkout (edit its files, run its tests, read files) → approve the
  narrowest option offered, a one-time yes. Never an "always" / "don't ask again" option: it writes a
  permission rule that outlives this call — the opposite of the approvals the user asked to keep.
- The one sanctioned exception to the bullet below: a finishing lane's notify-back (§5b) may surface
  *here*, as a permission prompt for a `herdr agent prompt` command. If the quoted command is
  **exactly** the notify-back — aimed at the recorded `orchestrator_pane`, carrying this run's id and
  the lane's own name, with nothing chained after it — approve it once: you briefed it, and the sweep
  reading this prompt is already the sweep it was trying to summon. Any variation — another pane,
  another run id, an extra `;`/`&&` command — is not the notify-back and falls through to the rule
  below. When it does surface here, the doorbell rings late by design: the prompt holds the ring
  until a timer sweep approves it — a cost of keeping approvals, not a malfunction.
- Anything leaving the lane's blast radius — `git push`, `gh pr create`, force operations, `sudo`,
  deleting outside the checkout, reading credentials, writing to a network target → **do not answer**.
  Leave the prompt standing, record it (`escalated`, command quoted), stop steering the lane, and
  surface it to the user. Publishing being a normal part of this run (§6f) does not make it
  approvable *here*: §6f runs after verification, on your side of the fence. A lane asking to push is
  a lane that misread its brief.

`herdr agent prompt` returns `agent_blocked` while a dialog is up, so clear it with `send-keys` first
(herdr, verified).

---

## §6h Compact or recover a lane

None of this has been exercised with Claude Code under herdr. The codex recipes — `/compact` on a hot
lane, composer-clearing keys, the `/new` restart — do **not** carry over as fact, and this driver
never clears a lane's conversation or screen on its own.

**Compaction — a probe on first use (unverified).**

    herdr agent prompt <lane> "/compact" --wait --until idle --timeout 120000

Claude Code has a built-in `/compact` and can also compact on its own as its context fills. Because
`used_pct` is always `null`, the sweep never compacts on a percentage (`hot` is inert, §6c); the only
row that sends this is `hard_fail`.

- **Only between turns.** Gate on `turn_state == complete` and on §6b's guard read showing an idle,
  empty input box. Anything already typed there → send nothing and escalate.
- **Judge it by effect.** The next probe's `compactions` must be higher, and on a `hard_fail` lane
  `out_of_room` must be gone once a new turn has run. Anything else — herdr reports the prompt
  stalled, the pane shows `/compact` answered as an ordinary message or as an unknown command, or the
  count does not move — record `compact_unavailable` once, stop sending `/compact` for the rest of the
  run, and escalate the lane. Never retry a command that did not work, sweep after sweep.
- **After compacting, re-prime by prompt.** A compacted Claude lane is idle; with no `/goal` set
  (§5c) and nothing pending in the background, it stays that way until prompted: send §6d's
  continuation prompt in the same sweep, built from `progress.md` — which is exactly why §5b insists
  that file stays current.
- **At most 3 compactions before this driver stops sending `/compact` — a local, conservative
  recovery limit.** It is this driver's own cap on the sweep's `/compact` recovery, kept from the
  codex driver as caution; it is not verified as a limit of Opus 5.5 or Claude Code. Count the larger
  of the helper's `compactions` (a floor when `compactions_partial`, §6a) and the effective
  `/compact`s recorded in the state file. At 3, send that lane no more `/compact`; its next
  `hard_fail` escalates. Claude Code's automatic compactions are normal: the count is never by
  itself a quality failure, nor a reason to interrupt or escalate a lane that is still working.
  While neither source has registered a compaction in this run, the rule has nothing to count and
  stays dormant — never fill the gap with a guess.

**No in-place restart.** Codex's restart recipe has no verified Claude equivalent: `/clear` is untested
here, and a fresh conversation would move the lane onto a session uuid this run neither generated nor
recorded (§5c). So every path that ended in a restart in the codex driver — `hard_fail` that
compaction did not fix, the 3-compaction cap — escalates instead: record `escalated`, quoting
`last_event` and what `progress.md` last said. What happens to the Claude process next is the user's
call. A fresh session, if they ask for one, is §5c's launch with a newly generated, recorded uuid and
§5c's priming prompt, on a pane back at a bare shell.

**The resume path — an exited lane gets its own session back.** Reached from §6b's "the name resolves
to nothing" (via §5c). In order:

1. **Confirm a bare shell.** `herdr pane read <pane> --source visible` must show the shell prompt with
   nothing typed and no Claude screen, and the helper's `mtime` must not have moved since the last
   sweep — no live process may still be writing this uuid. Anything else — a half-typed line, a
   running process, a Claude screen — do not clear, kill, or type over it: escalate.
2. Record `restarting: true` on the lane and rewrite the state file, so the next sweep does not fire
   the same row again while the resume is mid-flight.
3. Re-run §5a on the pane — the prepare script, then the version.
4. Relaunch with §5c's exact command, `--resume <recorded uuid>` in place of `--session-id <uuid>`:
   the exact recorded uuid — never `--continue`, never a session picker, never a new uuid. If the
   uuid has no transcript at all, the lane never got its first prompt in: that is a failed launch —
   §5c's launch with a new uuid, not a resume.
5. Check readiness as §5c does, then send §6d's continuation prompt (it counts as a nudge).
6. On the next sweep require `probe: ok` on that same uuid and a moved `mtime`; only then clear
   `restarting`. Whether a resumed Claude keeps appending to the same `<uuid>.jsonl` is unverified: if
   the pane shows the lane working while that file stays still, stop steering and escalate. Never
   adopt "the newest transcript" in its place — recording an id you have not seen belong to this
   lane is how a sweep ends up steering a thread that is not the lane's.

If the pane does not respond at all, the Claude process is gone or wedged: escalate to the user;
never `worktree remove` (§0.5), and a relaunch needs the pane back at a bare shell first (step 1).
