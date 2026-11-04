# Upgrade Notes

## 2.4.0: `arena-agent` → `arena-cli`

Before 2.4.0, the project's naming was inconsistent: the repository was called `arena-cli`, the Hermes skill was called `arena-agent`
(slash command `/arena-agent`), and the data directory was `~/.arena-agent`. Everything now uses one name:

| Item | 2.3.x | 2.4.0 |
| --- | --- | --- |
| Repository / package | `arena-cli` | `arena-cli` |
| Command | `arena` | `arena` |
| Hermes skill / slash command | `arena-agent` / `/arena-agent` | **`arena-cli` / `/arena-cli`** |
| Data directory | `~/.arena-agent` | **`~/.arena-cli`** |

### Data Directory: Automatic Migration, No Lost Login Sessions

The first time `arena` runs (any subcommand triggers it), it moves `~/.arena-agent` to `~/.arena-cli`:

- **New directory does not exist** → the whole directory is renamed; state files, configuration, and browser profiles are carried over unchanged.
- **New directory already exists** (for example, `arena status` was run first and `prof/` and `jobs/` were already created) → merge item by item:
  files missing from the target are moved over; for files with the same name, the one with the newer mtime is kept. Existing login sessions are therefore never overwritten.
- After migration, a marker is written to `~/.arena-cli/.migrated-from-arena-agent`, so the large chromium directories are not rescanned later.
- Once emptied, the old directory is deleted; items that cannot be moved (permissions or in use) are left in place and can be deleted manually.
- If `ARENA_HOME` is set explicitly, **no migration happens** (that is your own directory, and the tool does not touch it).

To verify:

```bash
arena status        # Check that the "session file" is ~/.arena-cli/state.json
arena check         # Check that you are still logged in
```

### Skill Directory: Manual Cleanup

Because the skill name changed, the old skill directory is not deleted automatically (doing so could remove files the user has modified):

```bash
git pull && pip install -e .
./install.sh --force                 # Installs to ~/.hermes/skills/arena-cli
rm -rf ~/.hermes/skills/arena-agent  # Remove the old one, otherwise two slash commands will exist side by side
```

`./install.sh` prints a notice when it finds the old directory, but it does not delete it for you.

### Compatibility

- The `arena` **commands, flags, exit codes, and JSON envelope** have not changed, so scripts do not need to be updated.
- The legacy entry point `scripts/arena_agent.py` still exists (it forwards to the same engine).
- Only the "names" changed: the slash command, the data directory, and the skill installation path. Update any hard-coded `~/.arena-agent` in your scripts.

## 2.3.0: Workspace and Proxy

- Added `arena workspace list|show|download` (`--all` packages everything into a zip).
- Added `--proxy` (per invocation / `config set proxy` / `ARENA_PROXY` / `HTTPS_PROXY`),
  which applies to both the browser and the network check in `arena doctor`.

## 2.2.0: Multi-turn and Streaming

- `arena ask --chat last|<id>|<index>` continues an existing conversation.
- `arena ask --stream` shows output as it is generated (progress goes to stderr; stdout still contains only the final answer).
