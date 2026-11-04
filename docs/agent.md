# Contract for Scripts and Agents

When a command-line tool is called by a script or agent, three things are most dangerous: unpredictable output, no safeguard on destructive operations, and failures that look like successes.
arena CLI implements five measures based on the lark-cli design. The following applies to `--json` / `--jq` output;
without `--json`, output is still a colored table for humans.

## 1. Unified Envelope

```json
{ "ok": true, "identity": "default",
  "data": { "answer": "…", "url": "https://arena.ai/agent/…", "elapsed_seconds": 41 },
  "meta": { "version": "2.4.0" } }
```

On failure, the exit code is nonzero and the response includes a "what to do next" card, so you don't have to guess from logs:

```json
{ "ok": false, "identity": "default",
  "error": { "type": "auth", "subtype": "login_required",
             "message": "This profile has no usable login session",
             "hint": "arena cookies import <cookies file>  (or arena login --email you@example.com)" } }
```

## 2. Risk Levels

Every command is labeled `read`, `write`, or `high-risk-write`. `high-risk-write` operations (deleting a profile, overwriting an existing login session)
are rejected outright without `--yes`: exit code 1, `confirmation_required`, and the hint offers two options, "add `--yes`" or "use a different `--profile`". An agent must ask the user first.

Run `arena schema <command>` to see each command's risk level without memorizing it.

## 3. `--dry-run` / `--jq` / `schema`

```bash
arena --dry-run --json ask "question"       # Preview the steps without executing any of them
arena --json status | jq .                  # Or use --jq '.data.session.has_auth_cookie' directly
arena schema ask                            # Parameters, types, defaults, and risk level (for agent introspection)
arena schema workspace.download             # Dotted notation is supported
arena schema                                # Overview of all commands
```

Unknown commands or subcommands exit with code `64` and list the valid options; they never silently succeed.

## 4. No False Success

If a message rendered optimistically on the page is retracted by the server (for example, due to a 429 rate limit), the tool explicitly reports `RATE_LIMITED` (exit code 7)
instead of treating a diff of the whole page text as the answer. This previously extracted control labels such as `"Agent"` as if they were answers.

## 5. Background Jobs

`arena ask --no-wait` returns a `job_id` immediately, and the task runs in the background:

```bash
arena ask --no-wait --json "Run a ten-minute research task"    # → data.job_id
arena jobs list | status last | result last | logs last | cancel last
```

Job state is stored in `~/.arena-cli/jobs/<job_id>/`. If the process dies, the job is marked `stale` (it will not show as running forever).

## Exit Codes

| Code | Meaning |
| --- | --- |
| `0` | Success |
| `1` | General error / `--yes` confirmation required / invalid input |
| `2` | Login required (`LOGIN_REQUIRED`) |
| `3` | Send failed (`SEND_FAILED`) |
| `4` | Wait timed out (`TIMEOUT`) |
| `5` | Page changed (`UI_CHANGED`; selectors need updating) |
| `6` | Blocked by human verification (`BLOCKED`) |
| `7` | Rate limited (`RATE_LIMITED`, e.g. `create-chat` returned 429) |
| `64` | Usage error (argparse's default of 2 collided with "login required", so it was changed) |

## Configuration and Precedence

Configuration lives in `~/.arena-cli/config.json` (`config.example.json` is a commented snapshot of the defaults).

Precedence: **command-line flag > environment variable > configuration file > built-in default**.

Environment variables: `ARENA_HOME` (data directory) · `ARENA_PROFILE` · `ARENA_BASE_URL` · `ARENA_PROXY` · `NO_COLOR`.

The **AGENT QUICKSTART** at the top of `arena --help` is the shortest path for agents; its examples are ordered as
"shortest path → structured output → introspection".
