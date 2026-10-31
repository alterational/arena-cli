# 给脚本和 agent 的契约

命令行工具被脚本/agent 调用时最怕三件事：输出不可预测、危险操作没有闸门、失败装成功。
arena CLI 按 lark-cli 那套体系做了五件事。以下内容对 `--json` / `--jq` 生效；
不加 `--json` 时输出仍是给人看的彩色表格。

## 1. 统一信封

```json
{ "ok": true, "identity": "default",
  "data": { "answer": "…", "url": "https://arena.ai/agent/…", "elapsed_seconds": 41 },
  "meta": { "version": "2.4.0" } }
```

失败时退出码非 0，并带一张「下一步该干什么」的名片（不用去猜日志）：

```json
{ "ok": false, "identity": "default",
  "error": { "type": "auth", "subtype": "login_required",
             "message": "这个 profile 没有可用登录态",
             "hint": "arena cookies import <cookies 文件>  （或 arena login --email 你@邮箱）" } }
```

## 2. 风险分级

每个命令标 `read` / `write` / `high-risk-write`。`high-risk-write`（删除 profile、覆盖已有登录态）
不带 `--yes` 直接拒绝：退出码 1、`confirmation_required`，hint 里同时给「加 `--yes`」
和「换个 `--profile`」两条路 —— agent 必须先去问用户。

用 `arena schema <命令>` 能看到每个命令的风险等级，不用背。

## 3. `--dry-run` / `--jq` / `schema` 三件套

```bash
arena --dry-run --json ask "问题"           # 先看清要做哪几步，一条都不执行
arena --json status | jq .                 # 或直接 --jq '.data.session.has_auth_cookie'
arena schema ask                           # 参数/类型/默认值/风险（agent 自省用）
arena schema workspace.download            # 支持点分写法
arena schema                               # 全部命令一览
```

未知命令 / 未知子命令退出码 `64` 并列出可用项，不会静默当成功。

## 4. 不假装成功

页面乐观渲染的消息被服务端撤回（比如限流 429）会明确报 `RATE_LIMITED`（退出码 7），
而不是把整页文本差分当成回答 —— 曾经真的抽出过 `"Agent"` 这种控件文案。

## 5. 后台任务

`arena ask --no-wait` 立刻返回 `job_id`，任务在后台跑：

```bash
arena ask --no-wait --json "跑一个十分钟的调研"    # → data.job_id
arena jobs list | status last | result last | logs last | cancel last
```

状态落在 `~/.arena-cli/jobs/<job_id>/`，进程死掉会被识别为 `stale`（不会永远 running）。

## 退出码

| 码 | 含义 |
| --- | --- |
| `0` | 成功 |
| `1` | 一般错误 / 需要 `--yes` 确认 / 输入有问题 |
| `2` | 需要登录（`LOGIN_REQUIRED`） |
| `3` | 发送失败（`SEND_FAILED`） |
| `4` | 等待超时（`TIMEOUT`） |
| `5` | 页面改版（`UI_CHANGED`，选择器要更新） |
| `6` | 被人机校验拦截（`BLOCKED`） |
| `7` | 被限流（`RATE_LIMITED`，如 `create-chat` 429） |
| `64` | 用法错误（argparse 默认的 2 会和「需要登录」撞车，已改掉） |

## 配置与优先级

配置在 `~/.arena-cli/config.json`（`config.example.json` 是带注释的默认值快照）。

优先级：**命令行 flag > 环境变量 > 配置文件 > 内置默认**。

环境变量：`ARENA_HOME`（数据目录）· `ARENA_PROFILE` · `ARENA_BASE_URL` · `ARENA_PROXY` · `NO_COLOR`。

`arena --help` 顶部的 **AGENT QUICKSTART** 就是给 agent 的最短路径，示例按
「最短路径 → 结构化 → 自省」排。
