# 登录与 cookies

这个工具用的是真浏览器，所以「登录」= 让浏览器带上有效的登录 cookie。
最省事的办法是把浏览器里已有的 cookies 导出来导入，不用重新输密码。

## 一、导入 cookies 文件（推荐）

支持三种格式，**自动识别**，只输出 cookie 名字、绝不打印值；导入后 `state.json` 权限自动 `600`：

| 格式 | 例子 |
| --- | --- |
| **Netscape `cookies.txt`**（curl / 浏览器扩展导出） | `arena.ai<TAB>FALSE<TAB>/<TAB>TRUE<TAB>1827972668<TAB>arena-auth-prod-v1.1<TAB>…` |
| EditThisCookie / Cookie-Editor JSON 数组 | `[{"name":"arena-auth-prod-v1.1","value":"…","domain":".arena.ai"}]` |
| Playwright storage_state | `{"cookies":[…],"origins":[…]}` |

```bash
arena cookies import ~/Downloads/arena.ai_cookies.txt   # 导入（= 记住登录）
arena cookies import ./cookies.txt --profile work        # 导入到指定 profile
arena check                                             # 会话是否有效 + cookie 体检
arena cookies show                                      # 清单：只有名字、域、过期时间
arena cookies export --out backup.json                  # 备份
```

`import` 的输出：

```json
{ "ok": true, "format": "netscape", "count": 15, "domains": [".arena.ai", "arena.ai"],
  "auth_cookie_names": ["arena-auth-prod-v1.1", "arena-auth-prod-v1.0"],
  "expired_names": [], "has_auth_cookie": true }
```

`has_auth_cookie=false` 或 `check` 报 `logged_in=false` → 这份文件没有有效登录态，让用户重新导出。
文件不像 cookies（比如导出成了 HTML 或空文件）会报 `BAD_INPUT`，退出码 1。

## 二、邮箱登录（没有 cookies 文件时）

```bash
arena login --email you@example.com --headed --wait 300   # 开窗口，方便点邮件里的链接
arena login --email you@example.com --code 123456         # 或直接给一次性验证码
```

密码不经过本工具：流程是邮箱 magic link / 一次性验证码。`--headed` 是为了过 Cloudflare 人机校验。

## 三、多账号（profile）

一个 profile = 一套登录态（`state.json` + 浏览器用户目录）：

```
~/.arena-cli/
  state.json  prof/                 # profile "default"
  profiles/<名>/state.json,prof/    # 其它 profile
```

```bash
arena profile list
arena profile create work --from ~/uploads/work_cookies.txt
arena profile use work
arena --profile work ask "用工作号跑一下"
arena profile delete work --yes      # high-risk-write，必须 --yes
```

`--profile` / `ARENA_PROFILE` 可临时指定，不用切换。

## 四、登录态放在哪

| 路径 | 内容 |
| --- | --- |
| `~/.arena-cli/state.json` | cookies + storage（**账号凭证**，600，别提交到 git） |
| `~/.arena-cli/prof/` | 浏览器用户目录（localStorage、缓存） |
| `~/.arena-cli/config.json` | 配置（代理、超时、选择器覆盖） |

登录 cookie 是 **httpOnly** 的，`document.cookie` 读不到，所以只能用 Playwright 的
`storage_state` 落盘 —— 这也是 `cookies export` 出来的 JSON 能用、而从控制台复制粘贴不行。
