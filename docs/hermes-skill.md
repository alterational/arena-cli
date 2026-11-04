# Install as a Hermes Skill (Slash Command)

This repository is also a **Hermes skill** (`name: arena-cli` in `SKILL.md`). Once installed in `~/.hermes/skills/`, it
becomes the slash command `/arena-cli`, and users can simply type natural language in Hermes.

```bash
./install.sh                               # Copy to ~/.hermes/skills/arena-cli
./install.sh --force                       # Overwrite if it already exists
./install.sh --link                        # Symlink (code changes take effect immediately; for development)
HERMES_HOME=~/work/.hermes ./install.sh    # Install into a specific profile
python3 scripts/verify_skill.py            # Spec self-check (frontmatter / sections / script references / no bundled credentials)
```

```text
/arena-cli Research the pricing strategies of three competitors
/arena-cli --cookies ~/uploads/arena.ai_cookies.txt Ask using this cookies file
```

## Entry-Point Contract

The entry points declared in `SKILL.md` use the `${HERMES_SKILL_DIR}` template variable, so the agent receives an absolute path:

```bash
python3 ${HERMES_SKILL_DIR}/scripts/arena_ask.py "<user's exact request>"
```

The contract of `scripts/arena_ask.py` is simple enough to relay directly to the user:

| Case | stdout | Exit code |
| --- | --- | --- |
| Success | **Only the answer text** (no JSON, no logs, no page UI noise) | `0` |
| Failure | One line: `❌ <CODE>: <what to do next>` | `2/3/4/5/6` |

Optional arguments: `--cookies <file>` (import and ask in one step), `--json`, `--max-chars 3500` (truncate long answers and append the conversation link),
`--timeout`, `--headed`, `-v`.

The skill includes `references/troubleshooting.md` (DOM structure / selectors / error codes) for maintenance agents.
When the page layout changes, use it to update the selectors in `arena/engine.py`.

## Upgrading

```bash
git pull && pip install -e . && ./install.sh --force
python3 scripts/verify_skill.py
```

If you are upgrading from `arena-agent` (2.3.x or earlier), there is one additional manual cleanup step: see [migration.md](migration.md).
