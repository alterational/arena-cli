# Login and Cookies

This tool uses a real browser, so "logging in" means giving the browser a valid login cookie.
The easiest method is to export cookies from a browser you're already logged into and import them, without re-entering your password.

## 1. Import a Cookies File (Recommended)

Three formats are supported and **detected automatically**. Only cookie names are printed, never values. After import, `state.json` permissions are automatically set to `600`:

| Format | Example |
| --- | --- |
| **Netscape `cookies.txt`** (exported by curl or a browser extension) | `arena.ai<TAB>FALSE<TAB>/<TAB>TRUE<TAB>1827972668<TAB>arena-auth-prod-v1.1<TAB>…` |
| EditThisCookie / Cookie-Editor JSON array | `[{"name":"arena-auth-prod-v1.1","value":"…","domain":".arena.ai"}]` |
| Playwright storage_state | `{"cookies":[…],"origins":[…]}` |

```bash
arena cookies import ~/Downloads/arena.ai_cookies.txt   # Import (= remember the login)
arena cookies import ./cookies.txt --profile work        # Import into a specific profile
arena check                                              # Is the session valid? Plus a cookie health check
arena cookies show                                       # List: names, domains, and expiry times only
arena cookies export --out backup.json                   # Back up
```

Output of `import`:

```json
{ "ok": true, "format": "netscape", "count": 15, "domains": [".arena.ai", "arena.ai"],
  "auth_cookie_names": ["arena-auth-prod-v1.1", "arena-auth-prod-v1.0"],
  "expired_names": [], "has_auth_cookie": true }
```

If `has_auth_cookie=false` or `check` reports `logged_in=false`, the file contains no valid login session; ask the user to export it again.
If the file does not look like cookies (for example, an HTML page or an empty file was exported), the command fails with `BAD_INPUT` and exit code 1.

## 2. Email Login (When No Cookies File Is Available)

```bash
arena login --email you@example.com --headed --wait 300   # Opens a window so you can click the link in the email
arena login --email you@example.com --code 123456         # Or supply the one-time code directly
```

Passwords never pass through this tool. The flow uses an email magic link or a one-time code. `--headed` is needed to pass Cloudflare human verification.

## 3. Multiple Accounts (Profiles)

A profile is one login session (`state.json` plus a browser user-data directory):

```
~/.arena-cli/
  state.json  prof/                 # profile "default"
  profiles/<name>/state.json,prof/  # other profiles
```

```bash
arena profile list
arena profile create work --from ~/uploads/work_cookies.txt
arena profile use work
arena --profile work ask "Run this with the work account"
arena profile delete work --yes      # high-risk-write; requires --yes
```

`--profile` / `ARENA_PROFILE` can be used to pick a profile temporarily without switching.

## 4. Where Login Data Is Stored

| Path | Contents |
| --- | --- |
| `~/.arena-cli/state.json` | Cookies and storage (**account credentials**, mode 600, never commit to git) |
| `~/.arena-cli/prof/` | Browser user-data directory (localStorage, cache) |
| `~/.arena-cli/config.json` | Configuration (proxy, timeouts, selector overrides) |

The login cookie is **httpOnly**, so `document.cookie` cannot read it. It must be saved to disk through Playwright's
`storage_state`. This is also why the JSON produced by `cookies export` works, while copying and pasting from the console does not.

The session snapshot and browser profile contain credentials. Keep them private and never share them in chat.
When restoring a snapshot, the CLI injects cookies applicable to the configured host and local storage for its exact origin.
