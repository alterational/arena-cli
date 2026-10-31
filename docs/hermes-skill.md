# 装成 Hermes 技能（斜杠命令）

本仓库同时是一个 **Hermes 技能**（`SKILL.md` 的 `name: arena-cli`），装进 `~/.hermes/skills/` 后
自动变成斜杠命令 `/arena-cli`，用户在 Hermes 里说人话就行。

```bash
./install.sh                               # 复制到 ~/.hermes/skills/arena-cli
./install.sh --force                       # 已存在时覆盖
./install.sh --link                        # 软链（改代码即时生效，开发用）
HERMES_HOME=~/work/.hermes ./install.sh    # 装到指定 profile
python3 scripts/verify_skill.py            # 规范自检（frontmatter / 章节 / 脚本引用 / 不打包凭证）
```

```text
/arena-cli 帮我调研三家竞品的定价策略
/arena-cli --cookies ~/uploads/arena.ai_cookies.txt 用这份 cookies 提问
```

## 入口契约

`SKILL.md` 里声明的入口用 `${HERMES_SKILL_DIR}` 模板变量，agent 拿到的是绝对路径：

```bash
python3 ${HERMES_SKILL_DIR}/scripts/arena_ask.py "<用户原话>"
```

`scripts/arena_ask.py` 的契约简单到可以直接转述给用户：

| 情况 | stdout | 退出码 |
| --- | --- | --- |
| 成功 | **只有回答正文**（无 JSON、无日志、无页面 UI 噪音） | `0` |
| 失败 | 一行 `❌ <CODE>: <下一步该做什么>` | `2/3/4/5/6` |

可选参数：`--cookies <文件>`（导入+提问一步到位）、`--json`、`--max-chars 3500`（长回答截断+附会话链接）、
`--timeout`、`--headed`、`-v`。

技能里带了 `references/troubleshooting.md`（DOM 结构 / 选择器 / 错误码），
这是给维护 agent 用的：页面改版时按它更新 `arena/engine.py` 的选择器。

## 升级

```bash
git pull && pip install -e . && ./install.sh --force
python3 scripts/verify_skill.py
```

从 `arena-agent`（2.3.x 及更早）升上来还有一步手动收尾：见 [migration.md](migration.md)。
