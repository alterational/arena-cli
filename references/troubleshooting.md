# Maintenance Manual: Selectors, Error Codes, DOM Structure

When the page changes, **only change the configuration**, not the code: `cp config.example.json ~/.arena-cli/config.json`, then edit as needed,
or pass `--config <file>` to `arena_ask.py` / `arena_agent.py`.

## Actual Structure of arena.ai/agent (Verified 2026-10)

| Element | Selector | Notes |
| --- | --- | --- |
| Input box | `div.tiptap.ProseMirror[contenteditable="true"]` | Tiptap/ProseMirror, **not** a textarea; setting `innerText` has no effect, a `paste` event must be dispatched |
| Send button | `button[aria-label="Send message"]` | Disabled when the input is empty |
| Stop / generating | `button[aria-label*="Stop" i]` | Its presence means streaming output is still in progress |
| Conversation turn | `[data-agent-transcript-message]` | One for the user, one for the assistant |
| User turn marker | `[data-user-message-body-row]` | Appears only in user turns |
| Answer body | `[data-agent-transcript-message]:not(:has([data-user-message-body-row])) .prose` | Timestamps, `Was this task successful?`, and other noise sit outside `.prose`, so they are naturally excluded |
| Terms dialog | `[role="dialog"]:has-text("Terms of Use")` + the `Agree` button | Must be clicked on first visit |
| Login dialog | `[role="dialog"]:has-text("Log In or Create Account")` | Appears after clicking send |
| Login email field | `[role="dialog"] input[type="email"]` | Button text `Continue with email` |
| Logged-out landing page | Body text contains `Experience the frontier` | A reliable signal that you are **not** logged in |
| Logged-in markers | `a[href="/history/search"]`, `button[aria-label*="New chat" i]` | Sidebar history entry |

## How Login Data Is Stored

- Primary storage: `~/.arena-cli/prof` (the browser user-data directory; the most reliable option)
- Portable snapshot: `~/.arena-cli/state.json` (cookies + localStorage, permissions 600)
- The login cookie is **httpOnly**, so `document.cookie` cannot read it → you must use Playwright's `storage_state`
- Key credential cookie names: `arena-auth-prod-v1.1` / `arena-auth-prod-v1.0` (configuration key `auth_cookie_patterns`)

## Criteria for Detecting Completion

Generation is considered finished when **the text has stayed unchanged for `stable_seconds`** (default 2.5s) **and** **there is no Stop button**;
the hard limit is `wait_timeout` (default 900s). Sleeping for a fixed time alone would misjudge long tasks.

## Error Codes

| Code | Exit code | Action |
| --- | --- | --- |
| `LOGIN_REQUIRED` | 2 | Import/update cookies, or run `login` |
| `LOGIN_FAILED` / `LOGIN_TIMEOUT` | 2 | Use a real email domain, increase `--wait`, or use cookies directly |
| `SEND_FAILED` | 3 | Retry once; if it still fails, use `--headed` to inspect the page |
| `TIMEOUT` | 4 | Increase `--timeout`, or give the `url` to the user |
| `UI_CHANGED` | 5 | Update the selectors in this document / the configuration |
| `RATE_LIMITED` | 7 | `POST /nextjs-api/stream/create-chat` returned **429**: wait a few minutes and retry, or switch cookies / use a different `--profile` |
| `BLOCKED` | 6 | Human verification: fresh cookies / complete it manually with `--headed` / change network |
| `BAD_INPUT` | 1 | Empty prompt, or the cookies file format is not recognized |

## Send Flow (Verified 2026-10)

1. The landing page `/agent` is a Next.js SPA: at `domcontentloaded` the editor is already in the DOM, but React event handlers are not attached yet,
   so a click on send gets swallowed. Therefore `open_agent()` must be followed by `settle()` (wait for `networkidle` + 400ms).
2. When actually sending, the page issues `POST /nextjs-api/stream/create-chat`; on success the URL changes to `/agent/<chat_id>`.
3. When rate limited, this endpoint returns **429**, and the page retracts the optimistically rendered message and returns to the landing page, without showing any error.
   So "message gone + landing page" must be treated as a failure, and whole-page text diffs must never be treated as the answer (this once extracted control labels such as `"Agent"`).
   The current approach: monitor the response status of `create-chat` and detect "conversation area disappeared", then report `RATE_LIMITED` (exit code 7).

## Session URLs and Continuing Conversations

- Each agent conversation has its own address: `https://arena.ai/agent/<chat_id>`, where chat_id is the ID shown by `arena chat list` (UUID format). `scripts/arena_agent.py` **does not support --chat**; it always opens a new landing page (a new conversation). To continue with context, you must use `arena ask --chat <chat_id|last|index>`, and the engine will actually `goto /agent/<chat_id>` before sending the message.
- Using the agent script to "ask a follow-up" does not carry history; the symptom is that it repeats the answer to the previous question.

## Multi-turn Conversations (`--chat`)

`arena ask --chat <chat_id|URL|last|index>` first runs `goto /agent/<id>`, then sends the new message.

**Pitfall**: in a conversation that has already finished, the bottom of the page is not the input box but the "**Was this task successful?**" feedback card
(`Yes / No / Keep working` plus an `×` / `Esc` in the top right). It pushes the composer out of view, so `wait_editor()`
keeps failing to find the input box → reports `UI_CHANGED`. Fix: `dismiss_feedback()` — it recognizes the `feedback_text` copy,
prefers clicking `button[aria-label*="Close"]`, falls back to pressing `Esc`, and then waits for the input box again.

In addition, `last` / indexes (`#1` in `arena chat list`) are converted to real chat_ids by `commands/ask.resolve_chat()`.
`Session.open_chat()` only accepts chat_ids / URLs, so do not pass `last` directly (it would produce `/agent/last`).

## Streaming Output (`--stream`)

On every polling round the engine calls `extract_answer()`, and `Session.ask(on_update=...)` invokes the callback whenever the text grows.
`commands/ask.Streamer` writes the **increments** to stderr, while stdout still contains only the final answer (`--json` is unaffected).
Note that when not in verbose mode, `open_session()` redirects `sys.stderr` into a StringIO to swallow Playwright noise,
so the Streamer must write to `sys.__stderr__`. During streaming the polling interval is reduced to 0.5s.

## Workspace (Workspace Panel)

The card on the right side of `/agent/<chat_id>`. Two UI variants have been observed in practice:

1. **Classic list** (common): rows are `div[role="button"][aria-label="<file name> file"]`, with hierarchy indicated by
   `style="padding-left: 8/24/40px"` (8 = root, +16 per level). Each row embeds
   `<a download aria-label="Download <file name>" href="/api/chat/workspace/cas/user/<hash>">`.
   Collapsed folders are `button[aria-label="Collapse|Expand <name> folder"]`; **child items are not in the DOM** until expanded.
2. **File tree** (large workspaces / newer version): `aria-label="Open file"` + `Expand` + `Copy`, with no per-file `a[download]`; rows are identified by text, and paths are uncertain.

So the strategy in `arena/engine.py` is: read variant (1) first; when rows cannot be read but the panel statistics show files, **fall back to the "Download workspace" zip in the top right of the page**
(`a[aria-label="Download workspace"]` → `/api/chat/<chat>/workspace/<uuid>/download`): `workspace_zip_entries()` lists directories, and `workspace_zip_read()` reads contents.
`arena workspace list --json` marks `data.source` as either `dom` or `archive`.

Pitfalls:
- The panel header sometimes renders a skeleton first (`animate-pulse` placeholders), so the stats appear before the rows → polling is required.
- The stats text may be `47B/128.0MB` + `2/10K files`, or abbreviated thousands such as `1.7K/10K files`;
  `workspace.parse_stats()` uses `re.search` rather than matching the whole line.
- For large workspaces (29MB / 1.7K files) the page never reaches `networkidle` → open with
  `domcontentloaded` + `wait_loaded()`, not `wait_editor()` (viewing files does not need the input box).

## Proxy

- Browser: `launch_persistent_context(proxy=...)`; addresses support `http/https/socks5`, and `user:pass@` is split into
  `username/password`. `--proxy-bypass-list=<-loopback>` **must** also be added, otherwise Chrome by default puts
  localhost on its direct-connection allowlist (test scripts running against the mock would wrongly conclude the proxy had no effect).
- The network check in `arena doctor` uses `urllib` + `ProxyHandler` and is labeled "(via proxy)".
- Passwords are masked with `mask_proxy()` in output and logs.
- If the proxy cannot connect → navigation raises `PWError` → converted to `BROWSER_ERROR` (exit code 1), with a hint to verify the proxy first using `curl -x`.
- Testing: `tests/proxy_server.py` is a forward proxy of a few dozen lines; the assertion checks that requests actually went through the proxy, not just that the configuration was written.

## Self-Tests

```bash
python3 tests/run_tests.py      # Engine + CLI (local mock, 12 groups, including 429 rate limiting)
python3 tests/run_js_tests.py   # snippets/*.js (pure JS route)
python3 scripts/verify_skill.py # Checks whether SKILL.md conforms to the Hermes skill spec
```
