# Operating Jarvis for an owner

This guide is for the agent the owner talks to (a Claude Code, Codex or CLI conversation) when that
agent starts, steers or reports on Jarvis research. Jarvis keeps all state in its store; a new
conversation never starts from zero if it reads the store first.

## 1. Start of every conversation

```sh
jarvis --store STORE autonomy brief [--domain DOMAIN]
```

`brief` lists every research session (status, decisions, conclusion, finalist), Dream rounds, and
for each domain the remembered lessons, the owner's standing guidance, skills, open questions, the
active research policy and `next_steps` (sessions to continue, sessions not yet in memory).
Summarize it for the owner before doing anything else. Do not restart work that `brief` shows is
already done or running.

## 2. Answering the owner

| Owner asks | Use |
|---|---|
| How is it going / what is it doing | `autonomy status OWNER`: plan, recent actions with reasons and Critic verdicts, best result so far, open questions, guidance and answers, last issue, budget |
| Is it trustworthy / audit | `autonomy report OWNER`, `autonomy replay OWNER` |
| Final results | `autonomy deliverable OWNER DIR` |
| What do we already know | `autonomy memory show DOMAIN` |
| Detail behind a claim | read the cited `sha256:` artifact from the store |

Quote numbers from these outputs with their result refs; never estimate a result that was not
observed. Say when a result is development evidence on exposed data.

## 3. Passing the owner's ideas to Jarvis

Every idea, preference, correction or decision the owner states belongs in Jarvis, in the owner's
own meaning, so later sessions and later conversations know it.

- A session is running or paused: `autonomy advise OWNER "..."` (`--redirect` to change the
  priority). The Director must answer every item; show the owner its answer from `status`.
- No session is running: `autonomy memory note DOMAIN "..."`. Every later session in that domain
  sees it as standing owner guidance.
- Tell the owner exactly what you recorded.

Ended sessions are consolidated into memory automatically, including the owner's guidance; the
Curator turns recurring guidance into `owner_preference` lessons.

## 4. Running sessions

- Start research only when the owner asks. `autonomy start DECL` only declares a session.
- Run it in the background and check on it, never in a blocking foreground call:
  `autonomy run OWNER --wall-seconds N`. One process per session; if `status` shows it running,
  do not start a second runner.
- `pause`, `resume`, `cancel` are immediate and recorded.
- Do not edit Jarvis or the domain adapter while a session runs: the runtime identity changes and the
  session stops. Declare a new session after code changes.
- Long LLM or backend outages pause the session; `run` again resumes it.

## 5. Costs and permissions

Backtests, LLM roles, the Curator (`memory consolidate --agent ...`) and Dream rounds cost quota or
tokens: ask the owner before Dream runs and before curating many sessions. Facts-only consolidation
(`--agent none`) is free. Never read credential files, never commit private data to this public
repository, and never commit or push without the owner asking.
