# arena CLI Complete Reference

For people who want to write scripts or use every feature. If you only want to get started, the [README](../README.md) is enough;
`arena --help` and `arena schema <command>` are always the most accurate references for the current version.

## Installation (Choose One)

```bash
./bin/arena --help          # No installation needed; runs the launcher in the repository directly
pip install -e .            # Install as a real command (console_scripts in pyproject)
pipx install .              # Isolated installation
```

The command structure is `<command> <subcommand>`. **Global flags work either before or after the command** (`arena --json ask "…"` = `arena ask "…" --json`).

## All Commands

```
arena ask [content]              Send content to the Agent and print the answer (--json structured / --stream streaming / --no-wait background / --chat multi-turn)
arena login                      Log in and save cookies (--email / --code / --wait)
arena check                      Check login session health (--probe sends a real verification message)
arena status                     Offline overview: profile / session / config / recent background jobs (does not launch a browser)
arena cookies import <file>      Import cookies (Netscape .txt / JSON / storage_state)
arena cookies export             Export the current session (--out)
arena cookies show               List cookies (names only, no values)
arena chat list                  Recent conversations (--limit)
arena chat last                  Link to the most recent conversation
arena chat open <id|index|last>  Open a conversation (--browser uses the system browser)
arena config list|get|set|unset|path
arena profile list|show|create|use|delete    Switch between multiple login sessions
arena workspace list|show|download           Conversation workspace: list files / view contents / download (--all packages them)
arena jobs list|status|result|logs|cancel    Background jobs (created by ask --no-wait)
arena schema [command…]          Command introspection: parameters / types / defaults / risk level
arena doctor                     Environment check (dependencies / browser / network / session / skill installation)
arena version
```

## Global Options

`--json` · `--jq '<expression>'` · `--dry-run` · `--yes` · `--no-wait` · `-q/--quiet` · `-v/--verbose` ·
`--no-color` · `--profile <name>` · `--config <file>` · `--base-url` · `--timeout <seconds>` ·
`--max-chars <N>` · `--cookies <file>` · `--headed` · `--proxy <proxy>`

## Workspace (Files Produced by the Agent)

Use the command line to access the items shown in the Workspace card on the right side of the conversation page:

```bash
arena workspace list                        # List files (folders expanded automatically; defaults to the most recent conversation)
arena workspace list --chat last --json     # {files:[{path,size,href}], stats:{used,quota,files}}
arena workspace show uploads/note.txt       # Print contents (stdout contains only the content)
arena workspace download demo.txt --out ./  # Download (--out can be a file or an existing directory)
arena workspace download --all --out ws.zip # Package the entire workspace
```

Path formats: `uploads/note.txt` (full path), `note.txt` (the file name alone works when it is unique), `*/note.txt` (wildcard).
Overwriting an existing local file requires `--yes`; `show` refuses binary files and suggests using `download` instead.

There are two implementation paths: first, read the Workspace panel DOM (fast). If the panel uses a different file-tree UI and rows cannot be recognized,
the tool automatically falls back to the "Download workspace" zip in the top-right corner of the page to list directories and read contents (`data.source = "archive"` marks this case).
For DOM details, see [references/troubleshooting.md](../references/troubleshooting.md#workspace-workspace-panel).

## Proxy

```bash
arena --proxy http://127.0.0.1:7890 ask "…"           # One-time
arena config set proxy http://127.0.0.1:7890          # Persistent (written to config.json)
arena config set proxy socks5://127.0.0.1:1080        # SOCKS5 is also supported
arena config set proxy http://user:pass@10.0.0.1:8080 # With authentication (the password is masked in output)
export ARENA_PROXY=http://127.0.0.1:7890              # Environment variable (HTTPS_PROXY/ALL_PROXY are also recognized)
```

Precedence: `--proxy` > configuration file > `ARENA_PROXY` > `HTTPS_PROXY`/`ALL_PROXY` > direct connection.
The proxy applies to both the **browser** (passed through Playwright, including `--proxy-bypass-list=<-loopback>`, because otherwise Chrome adds localhost to its bypass list by default)
and the network check in `arena doctor`. If the proxy cannot connect, the tool explicitly reports `BROWSER_ERROR` instead of appearing to succeed.

## Interactive chat tabs

Run `arena tui` in a terminal after logging in, or `arena tui --chat last` to continue an existing conversation.
This is browser UI automation, not an official Arena API. It uses the selected profile and normal browser settings.

- **Ctrl-S** sends; **Enter** adds a line, so pasted multiline text is not accidentally submitted.
- **F2** creates a tab, **Tab** or **F3** switches forward, **Shift-Tab** or **F4** switches back,
  **F6** closes a tab, and **Ctrl-Q**, **Ctrl-C**, or **F10** exits. Page Up and Page Down scroll.
- Type `/new`, `/open <chat ID|URL|last|index>`, `/next`, `/prev`, `/close`, `/help`, or `/quit`, then press Ctrl-S.
  To send a prompt beginning with `/`, start it with `//`.
- Tabs, drafts, and displayed messages exist only for the current run. Closing a tab does not delete its Arena
  conversation. Earlier messages of reopened chats are not loaded; `last` and indexes are resolved on first send.
- One request runs at a time, but tabs can be switched while it runs. Quitting during a request requires confirmation;
  the message may already have been sent. Failed or ambiguous requests are never retried automatically.

The interface requires a Unix-like terminal with Python `curses`. It does not support `--json`, `--jq`, `--dry-run`,
`--no-wait`, `--quiet`, `--verbose`, or `--max-chars`; use `arena ask` for those modes. Responses are not truncated.
The browser profile and session files contain credentials; protect them as described in [Authentication](auth.md).

## Browser performance and safety

By default, Arena-scoped visual effects are reduced and Google Analytics, Tag Manager, and Arena `/rpc` scripts are
blocked; other resource types are not blocked. Disable these separately with `arena config set reduce_visual_effects false`
and `arena config set block_analytics_scripts false` if the site misbehaves. Request routing may disable the browser
cache; service workers and content-security policies can affect these optimizations.

Conversation URLs and workspace downloads must use the configured HTTP(S) `--base-url` origin without embedded
credentials, a path, query or fragment. Cross-origin workspace and initial navigation redirects are blocked before the
destination request. The active page is checked before typing or sending, but later page-initiated navigation is not
intercepted. Job cancellation verifies the worker and reports `cancelled: false` if it cannot be verified.

## Typical Usage

```bash
# Ask a single question
arena ask "Research the pricing strategies of three competitors"

# Upload cookies → check → ask
arena cookies import ~/uploads/arena.ai_cookies.txt
arena check
arena ask --json "Organize the conclusions into a table" --max-chars 3500

# Multi-turn: continue in the conversation you just had (--chat accepts last / chat_id / the index from chat list)
arena ask --chat last "Translate the conclusions above into English"
arena ask --chat 1 "Expand a bit more on the conclusion from the first turn"
arena ask --chat 01a19037-62e2-735d-937e-b500aa740edc "Continue"

# Streaming: watch output as it is generated (progress goes to stderr; stdout still contains only the final answer)
arena ask --stream "Write an 800-word competitive analysis"

# Streaming and structured output can be combined (JSON is printed once at the end; incremental output goes to stderr)
arena ask --stream --json "…" --jq '.data.answer'

# Multiple accounts: separate login sessions for a work account and a personal account
arena profile create work --from ~/uploads/work_cookies.txt
arena profile use work
arena ask "Run this with the work account"

# Change selectors when the page is redesigned (no code changes needed)
arena config set selectors '{"editor_selectors":["div.ProseMirror[contenteditable=\"true\"]"]}'
arena config set timeout 1800
arena config list

# Long tasks without blocking: run in the background → check the result later
arena ask --no-wait --json "Run a ten-minute research task"     # → data.job_id
arena jobs status last
arena jobs result last                            # stdout is the answer text

# Only the fields, no human-readable text
arena ask --json "…" --jq '.data.answer'
arena status --jq '.data.session.has_auth_cookie'
```

## Legacy Entry Point `scripts/arena_agent.py`

The only entry point before 2.0. It is kept for compatibility (it forwards to the same engine), so existing scripts do not need to change:

```bash
python scripts/arena_agent.py login  --email you@example.com [--code 123456] [--headed] [--wait 300]
python scripts/arena_agent.py ask    "<content>" | --file p.md | -   [--timeout 900] [--format json|text|markdown] [--out f.md]
python scripts/arena_agent.py check  [--probe]
python scripts/arena_agent.py export-cookies --out cookies.json
python scripts/arena_agent.py import-cookies cookies.json

Common options: --base-url  --profile-dir  --state-file  --cookies <file>  --headed  --channel chrome  --user-agent  --config
```

For new scripts, use `arena` directly: it adds a unified envelope, risk gates, `--jq`, and background jobs.
