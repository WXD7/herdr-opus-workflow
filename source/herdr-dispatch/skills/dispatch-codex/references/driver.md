# Claude driver (compatibility name `dispatch-codex`) — §5a, §5c, §6a, §6c, §6d, §6g, §6h

Everything in `dispatch-codex` that depends on the agent itself. The skill keeps its codex name for
compatibility only (`../SKILL.md`); every lane here is **Claude Code**, launched as herdr
`--kind claude` with the selected group profile (default `claude-opus-5-5` / `max`). The independent halves are
`references/plan.md` (§2–§4, §5b) and `references/supervise.md` (§6b, §6e, §6f, §6i, §7, §8);
§0 and §1 are in `../SKILL.md`.

`<ROOT>` is `HERDR_WORKFLOW_ROOT` when supplied by a frozen experiment; otherwise resolve the
bundle containing `scripts/` and `observability/` from the loaded plugin. Never use an old cache.

Local launch/transcript behavior was checked on Claude Code 2.1.280; record the installed CLI
version. Historical supervision ran locally; the configurable experiment adapter still needs
live Herdr/account validation. **(unverified)** marks unestablished recovery/UI behavior.
No Codex rollout/goals database applies; native `/goal` is disabled.

---

## §5a Pre-flight the pane

With `HERDR_EXPERIMENT_CONTEXT`, validate it through `<ROOT>/scripts/experiment.py profile-env`.
Record its group/attempt, requested profile and four-document hashes. Its hash-checked
snapshot bounds cwd and pins the platform adapter; it is provenance, not an OS sandbox. The
wrapper enforces selected model/effort; unknown observed values remain unknown. Rule edits
require a new attempt. `HERDR_EXECUTOR_MODEL` / `HERDR_EXECUTOR_EFFORT` below come from that
validated context; absent a context, use Opus 5.5/max. Never infer actual usage from these flags.

**Workflow version.** On a fresh run, record `state.workflow`: actual plugin path, Git commit,
`git describe --tags --match 'workflow-v*' --always`, dirty status, and SHA-256 of
`skills/dispatch-codex/{SKILL.md,references/driver.md}` and `skills/_shared/{plan.md,supervise.md}`.
Resolve the paths from the loaded plugin,
not a similarly named cache. Never label dirty files as an unchanged release. On each resume,
compare these four hashes using a compact local check, without loading their bodies. Missing
provenance or changed hashes requires a user-approved version handoff: report and stop only the
supervisor's timer/steering, leaving workers untouched. Do not overwrite the recorded version or
reuse old acceptance blindly. A new version normally starts with a fresh supervisor/run; an
authorized handoff reloads all four files and records old/new versions and retained evidence.

Prepare ignored dependencies and secrets explicitly for each fresh worktree.

Record `HERDR_DISPATCH_SUPERVISOR_SESSION` as `state.orchestrator_session` and
`HERDR_LANGWATCH_RUN_ID` as `state.telemetry_run_id` from this supervisor's environment.
Keep the shared §2 business run_id and its telemetry mapping; every worker uses the recorded
telemetry label. On resume record the launcher's exact session; missing identity/labels blocks
dispatch. Never select a recent session as a substitute.

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

- refuses a non-Herdr pane or cwd outside the frozen group (legacy: outside `<ROOT>`);
  its frozen context pointer also configures new lane shells, which do not inherit caller env;
- exports the LangWatch labels (`HERDR_LANGWATCH_RUN_ID`, `HERDR_LANGWATCH_ROLE`) and puts the
  project's existing LangWatch wrapper first on `PATH`, then checks that `claude` resolves to it;
- exports the frozen group's child model, effort and depth/concurrency ceilings (legacy default:
  Opus 5.5/max, depth 3, concurrency 3). These are ceilings, not required fan-out. Native Agent use stays within the lane boundary; no custom
  role prompts or required fan-out. When this group explicitly disables native children, the
  wrapper denies Agent/Task tools. The original supervisor/lane workflow remains intact.

Any error it prints stops that lane: report it, and never work around it — least of all by passing
an absolute executable path to `agent start`. The Claude CLI inherits the machine's official Claude
login; never sign in, set an API key, or copy credentials on the user's behalf.

Then, in the same pane:

    herdr pane run <pane> "claude --version"

Use `claude --version` through the wrapper; record the real CLI once per run (§2).

Read `herdr pane read <pane> --source visible`: omitting source can return empty output;
wait-output observes only future output and can miss an already completed command.

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

Persist the UUID as lane `session` before launch; §6a and §6h use that exact transcript identity. Every launch attempt gets a fresh one — a retry never reuses a uuid an earlier attempt may
have started, because an `agent start` that timed out can still have left a live Claude on it. Keep
abandoned uuids in the lane's entry for the report.

    herdr agent start <lane> --kind claude --pane <pane> --timeout 120000 -- \
      --model "${HERDR_EXECUTOR_MODEL:-claude-opus-5-5}" --effort "${HERDR_EXECUTOR_EFFORT:-max}" --permission-mode auto \
      --settings '{"ultracode":false}' --session-id <uuid>

Use the command as written: the prepared shell resolves the LangWatch wrapper, the recorded UUID
identifies the transcript, and 120 s allows for MCP startup. The selected profile and `ultracode:false`
are fixed; unavailable model means stop, not downgrade. Keep `--permission-mode auto`, whose
dialogs are handled by §6g; it is not a filesystem sandbox. No bypass flags, alternate permission
mode, or permission-widening settings/`--allowedTools`. No positional/headless prompt: verify an
empty input box before priming. No `--continue`/`--resume` on first launch, custom agent/system
prompt, absolute executable path, or Claude-owned worktree; use native Agent delegation inside
the recorded Herdr checkout.

**§3 user disclosure, three lines:**
- 执行：记录本组实际选定的 Claude 模型／effort，使用 LangWatch wrapper；兼容命令名不变。
- 权限：auto，禁止 bypass；主管按 §6g 处理已授权范围内审批，越界交给用户。
  worktree 是代码隔离而非 OS 沙箱；`--no-yolo` 保持此姿态，`--yolo` 被拒绝。
- 续跑：实验使用 §7 的确定性 DONE 事件，不建后台等待/模型定时器；非实验普通 prompt，空闲未完成读 progress 再续跑。兜底默认 15 分钟，
  正常推进可延至 30 分钟、恢复时缩至 5 分钟。账号限额共享，无可靠限流或上下文百分比，
  不启用 /goal，不按 used_pct（恒为 null）压缩。

**Then verify readiness yourself.** `agent start` returning `agent_started` with
`agent_status: idle` and `interactive_ready: true` is herdr's reading of the screen, not proof the
input box is usable — with codex it reported ready over a trust modal (herdr, verified), and how its
detection reads Claude's startup screens is unverified. Read the pane:

    herdr agent read <lane> --source visible

- A workspace-trust dialog is expected on a fresh worktree (unverified wording): accept it for the
  lane's own checkout with `send-keys` matching the option displayed — never a remembered key — then
  re-read.
- Any other dialog, a login prompt, or a model conflicting with the frozen profile → answer nothing on the
  user's behalf: stop the lane and report it with the text quoted.
- Only once Claude's input box is visible, empty and idle is the lane ready for input.

On `agent_not_ready` the name still resolves — read, resolve the dialog, continue (herdr, verified).
If `agent start` failed or timed out, read the pane before retrying: a Claude screen means the launch
happened — continue with the readiness check on the recorded uuid, never start a second one; a bare
shell means it did not — read the error it left, and retry once with a **new** uuid.

**Then prime the lane** — one call, only after the input box is visible:

    herdr agent prompt <lane> "Read .dispatch/TASK.md in this directory and work through its checklist in order. Keep .dispatch/progress.md updated after every item — assume your context may be compacted at any time and that file is all you keep. Write .dispatch/DONE only when every checklist item is done, every acceptance criterion in TASK.md verifiably holds and git status is clean, then follow the completion transport TASK.md gives you."

Record phase `implementing`. After launching all lanes, do the first landing sweep immediately.
A missing transcript means priming may not have landed: inspect the pane and resolve it (§6g)
before resending, at most once. Long turns and pending native subagents are ordinary work;
only a truly idle unfinished lane needs continuation (§6c/§6d).

**Native `/goal` remains disabled.** Use plain prompts; do not introduce an evaluator or extra
model smoke test as a prerequisite.

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

The helper reads only the **tail** (2 MiB, §0.2) and checks that what it reads belongs to this lane:
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
- `mtime` — transcript write time, an activity hint; metadata writes are not substantive progress.
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

Record `last_substantive_progress_at` for evidenced code, check or blocker changes (§6d).
Missing history is unknown: establish a baseline, not an invented stall. Metadata alone never resets it.

| Class | Test | Action |
| --- | --- | --- |
| `terminal` | phase is `published`, `failed`, user-paused, or `verified` with a recorded §6f degrade reason | Skip — report only; never re-verify, re-publish, or prompt a closed lane |
| `unpublished` | phase is `verified`, `publish_attempts` < 3, no recorded degrade reason, and `pushed_sha` is missing or behind the branch HEAD, or `pr_url` is missing with PRs enabled | Retry publish (§6f) — the work is done, only publishing is left |
| `done` | `.dispatch/DONE` exists **and** `turn_state == complete` | Verify (§6e — reuse a result only where §6e allows), deliver (§6f), mark terminal; a lane that already failed with nothing changed since goes to §6d's accounting instead |
| `blocked` | `agent_status == blocked` | Read `--source visible`, handle (§6g) |
| `blind` | `probe == unavailable` for two consecutive sweeps | The transcript is not answering for this lane — missing, or it failed the `sessionId` / `cwd` check. Read `--source visible` once, judge from git state, and escalate rather than steering a lane you cannot see |
| `hard_fail` | `out_of_room` (§6a), and no §6h recovery recorded in flight | No verified in-place restart exists here: one `/compact` per §6h if its gate allows; otherwise — or if it changes nothing — escalate |
| `api_error` | newest main-thread event is `assistant:api_error`, except `out_of_room` above | Inspect once now (§6g); do not wait for an mtime stall. Record the error identity, active work and side effects before a bounded recovery |
| `stalled` | no evidenced substantive progress ≥ 15 min; metadata activity alone does not reset this | Read visible state once. Dialog → §6g; idle unfinished turn → §6d; active tool/subagent → leave working. Long work alone is not failure. At 60 min diagnose once within §3's budget; continuing progress requires no intervention. Unresolved cases use §3's decision owner, never an automatic timeout |
| `hot` | `used_pct ≥ 70` **and** `turn_state == complete` | Inert: `used_pct` is always `null` (§6a), so this row never matches — context is left to Claude Code, and the sweep never compacts on a guess |
| `idle_incomplete` | `agent_status` is `done`/`idle`, `turn_state == complete`, **and** no `DONE` file | Read `progress.md`, send a specific continuation (§6d), record the nudge — unless pending background/subagent work can resume it; then send nothing |
| `working` | otherwise | No continuation; only a necessary queued update under §6d may be sent |

An `unknown` `agent_status` is an anomaly to surface, never a completion — do not let it fall
through to `working`'s leave-it-alone. `turn_state == unknown` is surfaced the same way (with
`last_event`): never read as complete, and if it persists, `stalled` reads the pane.

**Cadence for §7:** default `15m`; after two sweeps with healthy autonomous progress on every
actionable lane, use `30m`. Use `5m` while a launch/recovery/nudge awaits confirmation or observed
idle turns need frequent continuation; return to `15m` once normal progress resumes. A changed
cadence affects the next tick, not an unseen event between ticks. Task-specific urgency may justify
a different interval, recorded with its reason. Notifications still trigger earlier checks.
Do not presume unfinished lanes are idle; check for pending tools/subagents before nudging.
User-paused or unanswered `escalated` lanes receive no steering from any row. Resolve a routine
exception under §3 before resuming; an authorization/platform denial is never overridden.

One known race, by design: a notify-back can arrive before the ringing lane's final turn closes (the
brief fires it right after DONE is written, mid-turn), so that lane may still read `working` with
DONE present — re-check it once at the end of the sweep, or leave it to the next tick; both are fine.

---

## §6d Steer a lane — writing the continuation prompt

Every prompt uses `herdr agent prompt <lane> "…"`, after live identity (§6a) and input guards
(§6b). A draft means no input and a report; a dialog goes to §6g. Never clear the composer, interrupt a running tool,
or send `/goal`/Codex commands. Ordinary continuations require an idle turn with no pending work.

**Necessary updates may queue during work.** For a user-authorized scope change or concrete
correctness evidence, persist the change in the existing brief/state and send one concise update
without interrupting the turn, only when the
current client is known to support queued prompts and the visible input is empty. A prior local
run established this for its client; other clients need their own evidence. If support is
unknown, retain the update in state for the next safe idle point and report the delay. Before
sending record update id, content and attempt; afterward record queued/failed/uncertain, and mark
absorbed only from transcript/progress evidence. Do not equate delivery with uptake or retry an
uncertain send. Combine unsent facts, preserve sent content; no duplicate reminders or worker-wide
broadcast. Check uptake at the next scheduled/event sweep, never poll for it. A completed turn
that ignored a required update gets one focused correction under the nudge accounting below.

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
  `progress.md` last said, and route to §3's decision owner. Never fire a third identical nudge.
  No continuation or `/compact` until a recorded resolution changes the blocked condition.

Use this accounting for stalled progress under shared limits too; never claim a machine-readable
rate-limit state the probe cannot see.

**When the lane reports a real blocker** — a missing secret, a broken upstream, a contradiction in
the task — do not improvise scope. If the answer is inside the §3-confirmed plan (a decision the
brief already made, a misread step), send the correction as a normal prompt. Otherwise record the
quoted blocker, evidence and decision owner (§3); supervisors decide routine recovery, delegated
reviewers only their authorized exceptions, and users any changed scope, authority or budget.

**Pausing a lane** is a user decision, not a sweep's: record
`pause: {origin: user, at: <sweep time>}` in the state file and stop nudging it. A turn already in
flight runs to its end; nothing here interrupts it. Such a lane is terminal for §7 until the user
says otherwise.

---

## §6g Handle a blocked lane

Prove identity (§6b), then read the visible pane once. Distinguish permission, task decision,
startup dialog and runtime error. Preserve real drafts; grey suggestions alone prove nothing.
Do not guess keys or send a prompt into a dialog (`agent_blocked`); use its displayed option.

Benign work inside the checkout may receive one-time approval, not a persistent rule. Notify-back
must exactly target the recorded supervisor pane with this run/lane and no chained operation.
Worker publishing, force operations, sudo, credentials and outside-scope actions stay blocked;
route them under §3. Supervisor publishing remains after independent acceptance. A platform denial
or unavailable approval verdict cannot be retried through another tool, path or permission mode.

Routine decisions follow confirmed defaults; new scope, weaker acceptance or extra budget needs
the authorized owner. Children inherit no supervisor approval; never infer permission from telemetry.

<!-- experiment-decisions: parent-v1 -->
In new frozen experiments, the existing dispatcher routes registered lane requests to this exact
group supervisor. On a decision wake, run `python3 "$HERDR_WORKFLOW_ROOT/scripts/experiment.py"
decision inbox`; it records receipt. For `routine`, decide within §3 and use `decision answer
--id <id> --request-hash <hash> --text '<choice and reason>'`. Do not call AskUserQuestion for an
already-authorized engineering choice. Then end the turn if only waiting for lanes; the dispatcher
delivers the answer to that child, which must `decision consume` once before proceeding.
`authority` cannot be answered through this transport: surface the exact request to the authorized
owner/native permission interface. No automatic permission approval or Dot cloud wake is claimed.
Changed/expired requests, changed recipients and uncertain sends stop automatic delivery; preserve
the recorded failure. Sent, parent-received, answered and child-consumed are separate evidence.
Native Agent descendants report to their native caller, not directly to the group supervisor;
their caller records the result and resumes/delegates follow-up via the native harness. No extra
model timer, role prompt framework or recursive observer. Historical snapshots keep their transport.

Explicit API errors deserve prompt diagnosis after proving no operation is still running; verify
side effects before one justified continuation. Do not replay blindly or treat normal long work
as stalled. Mtime/away_summary changes are not progress. Duplicate events reuse recorded handling.


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
never `worktree remove` (§0.4), and a relaunch needs the pane back at a bare shell first (step 1).
