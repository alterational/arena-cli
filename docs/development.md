# Development and Maintenance

## Directory Structure

```
arena-cli/
├── SKILL.md                  # Hermes skill definition (frontmatter name: arena-cli → /arena-cli)
├── install.sh                # Installs into ~/.hermes/skills/ (--force / --link)
├── README.md                 # For users (short)
├── docs/                     # For developers/maintainers (this document)
├── config.example.json       # Default values for selectors/behavior parameters; takes effect when placed at ~/.arena-cli/config.json
├── requirements.txt
├── pyproject.toml            # pip install -e . → provides the `arena` command
├── bin/arena                 # No-install launcher (python -m arena)
├── arena/                    # CLI package
│   ├── cli.py                #   Command dispatch / global flags / exit codes
│   ├── config.py             #   Configuration precedence, profiles, legacy data directory migration
│   ├── session.py            #   Session switch + error suggestions
│   ├── ui.py                 #   Colors/tables/JSON (CJK-aware alignment)
│   ├── engine.py             #   Browser engine (formerly arena_agent.py)
│   ├── errors.py             #   Error codes → {type,subtype,message,hint}
│   ├── workspace.py          #   Workspace: path assembly/lookup/text detection/stats parsing
│   ├── jq.py                 #   Minimal jq used by --jq (.a.b / [0] / [] / |)
│   ├── jobs.py               #   Background jobs (the implementation behind --no-wait)
│   ├── spec.py               #   Generates a schema from argparse (agent introspection)
│   └── commands/             #   ask/auth/cookies/chat/config/profile/
│                             #   jobs/schema/status/doctor/workspace
├── scripts/
│   ├── arena_ask.py          # Hermes skill entry point: one sentence in, answer text out (contract unchanged)
│   ├── arena_agent.py        # Legacy entry point kept for compatibility (forwards to the engine)
│   └── verify_skill.py       # Checks whether SKILL.md conforms to the Hermes skill spec
├── references/
│   └── troubleshooting.md    # Maintenance manual: DOM structure / selectors / error codes
├── snippets/                 # browser_exec (pure JS) route
│   ├── 01_fill_and_send.js
│   ├── 02_wait_and_read.js
│   ├── 03_login_email.js
│   ├── 03b_fill_code.js
│   └── 04_dump_session.js
└── tests/
    ├── mock_server.py        # Local fake site that reproduces the real DOM
    ├── proxy_server.py       # Local proxy to verify that --proxy actually takes effect
    ├── run_tests.py          # Engine + CLI (11 groups)
    ├── run_js_tests.py       # Pure JS route for snippets/*.js
    ├── run_entry_tests.py    # Skill entry point + install.sh + data directory migration (8 groups)
    ├── run_cli_tests.py      # arena CLI command system (15 groups: envelope, risk, dry-run, jq, jobs,
    │                         #   schema, workspace, proxy)
    └── run_all.sh            # Runs everything (5 stages)
```

## Self-Tests (No Real Account Required)

```bash
tests/run_all.sh
```

`mock_server.py` reproduces the real DOM of arena.ai/agent (the Tiptap editor, `data-agent-transcript-message`
turns, the Workspace panel, and 429 scenarios), so the entire test suite runs offline. Key behaviors covered:

Not logged in must report `LOGIN_REQUIRED` (no false success) → log in and save cookies → **with a brand-new browser profile,
carrying only state.json**, questions can still be asked directly (proving the cookies were really remembered) → multi-line Chinese text → long text with special characters such as `<div>` and quotes is not mangled → `check` evaluation → **uploading a Netscape cookies.txt and asking in a single command** →
real upload file parsing (including a check that "output does not contain cookie values") → a malformed file reports `BAD_INPUT` →
`--no-wait` background jobs → `--proxy` really goes through the proxy (and a bad proxy must fail explicitly) → workspace list/show/download →
legacy data directory migration (rename / merge / migrate only once / `ARENA_HOME` takes precedence).

## Page Structure (See Here When the Site Changes)

The real structure of arena.ai/agent, the selectors, the criteria for detecting completion, and the pitfalls of the send flow are all recorded in
[references/troubleshooting.md](../references/troubleshooting.md), with the date of each verification in its section heading.
The two most common pitfalls:

1. **Directly setting `innerText`** is not picked up by React/ProseMirror → the send button stays disabled. The correct approach is to dispatch a `paste` event.
2. **Waiting for completion** must not rely on a simple sleep: use two conditions, "no Stop button" and "text unchanged for N consecutive seconds" (`stable_seconds`, default 2.5s).

There are two ways to change selectors: temporarily use `arena config set selectors '{…}'` (no code changes needed),
or change the constants in `arena/engine.py` and add a matching mock DOM. The latter requires running `tests/run_all.sh` to confirm there are no regressions.

## Release

```bash
# 1. Bump the version: pyproject.toml + arena/__init__.py (both must match)
# 2. Full self-tests + spec self-check
tests/run_all.sh && python3 scripts/verify_skill.py
# 3. Commit and push
git commit -am "feat: …, vX.Y.Z" && git push origin main
```

Breaking changes (skill name, data directory, exit code meanings) must include `BREAKING CHANGE` in the commit message,
and an upgrade section must be added to [migration.md](migration.md).

## Focused regression tests

```bash
python3 -m unittest discover -s tests -p 'test_recovery.py' -v
python3 tests/run_tui_tests.py
python3 tests/run_browser_regressions.py
```

The terminal integration test uses a local mock server and a pseudo-terminal; no real credentials are used.
