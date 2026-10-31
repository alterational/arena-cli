#!/usr/bin/env python3
"""
verify_skill.py —— 检查这个目录是否符合 Hermes 技能规范（hermes skills 的加载要求）

校验项：
  * frontmatter 能被 YAML 解析，必填字段齐全（name / description / version）
  * name 与目录名一致、kebab-case
  * description 简短（≤ 60 字符，Hermes 技能搜索会截断）
  * metadata.hermes 结构合法（tags / requires_toolsets / config 条目）
  * 正文包含 Hermes 房屋风格的章节：When to Use / Quick Reference / Procedure / Pitfalls / Verification
  * SKILL.md 里引用的脚本（${HERMES_SKILL_DIR}/...）真实存在
  * 没有把会话凭证（cookies.jsons / state.json）误打包进技能

用法：python3 scripts/verify_skill.py [技能目录]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # 允许在没有 pyyaml 的环境里跑，只做正则校验
    yaml = None

REQUIRED_SECTIONS = ["When to Use", "Quick Reference", "Procedure", "Pitfalls", "Verification"]
FORBIDDEN_FILES = {"state.json", "cookies.json", "prof"}
errors: list[str] = []
warnings: list[str] = []


def parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        raise SystemExit("SKILL.md 必须以 YAML frontmatter (`---`) 开头")
    try:
        _, fm, body = text.split("---", 2)
    except ValueError:
        raise SystemExit("frontmatter 没有正确闭合（需要两个 ---）")
    if yaml is None:
        warnings.append("没装 pyyaml，frontmatter 只做正则粗检")
        return {"_raw": fm}, body
    data = yaml.safe_load(fm) or {}
    if not isinstance(data, dict):
        raise SystemExit("frontmatter 不是 YAML 映射")
    return data, body


def git_repo_name(root: Path) -> str | None:
    """仓库名（origin 的 basename）。本地目录名可以是 clone 时的旧名字，仓库名才作数。"""
    import subprocess
    try:
        url = subprocess.run(["git", "-C", str(root), "config", "--get", "remote.origin.url"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    if not url:
        return None
    tail = url.rstrip("/").split("/")[-1].split(":")[-1]
    return tail[:-4] if tail.endswith(".git") else (tail or None)


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else
                Path(__file__).resolve().parent.parent).resolve()
    skill_md = root / "SKILL.md"
    if not skill_md.exists():
        raise SystemExit(f"找不到 {skill_md}")
    text = skill_md.read_text(encoding="utf-8")
    fm, body = parse_frontmatter(text)

    # ---- name / description / version ----
    name = str(fm.get("name", ""))
    if not name:
        errors.append("缺少 name")
    else:
        if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name):
            errors.append(f"name 必须 kebab-case（小写+连字符），当前是 {name!r}")
        if name != root.name:
            if root.parent.name == "skills":
                # 装进 skills/ 之后，目录名就是这个技能的身份，必须一致
                errors.append(f"name={name!r} 与目录名 {root.name!r} 不一致（斜杠命令名来自 name）")
            elif git_repo_name(root) == name:
                pass                      # 仓库名 = 技能名，本地 clone 的目录名无所谓
            else:
                # 源码目录可以改叫别的名字，./install.sh 会装到 skills/<name>/
                warnings.append(f"目录名 {root.name!r} 与 name={name!r} 不同；"
                                f"只是本地目录名的话没关系，./install.sh 会装到 skills/{name}/")

    desc = str(fm.get("description", "")).strip()
    if not desc:
        errors.append("缺少 description")
    elif len(desc) > 60:
        warnings.append(f"description 有 {len(desc)} 字符，建议 ≤ 60（技能搜索里会截断）")

    if not fm.get("version"):
        warnings.append("建议填写 version（如 1.0.0）")
    if not fm.get("platforms"):
        warnings.append("未声明 platforms，将在所有平台加载（本技能确实跨平台，可忽略）")

    # ---- metadata.hermes ----
    hermes = (fm.get("metadata") or {}).get("hermes") or {}
    if not hermes:
        errors.append("缺少 metadata.hermes（Hermes 用它做技能发现与条件激活）")
    else:
        if not hermes.get("tags"):
            warnings.append("建议给 metadata.hermes.tags，方便 skills_list 检索")
        toolsets = hermes.get("requires_toolsets") or []
        if "terminal" not in toolsets:
            errors.append("本技能靠 terminal 跑脚本，metadata.hermes.requires_toolsets 应包含 terminal")
        for cfg in hermes.get("config") or []:
            if not (isinstance(cfg, dict) and cfg.get("key") and cfg.get("description")):
                errors.append(f"metadata.hermes.config 条目需要 key 与 description：{cfg!r}")

    # ---- 必需章节 ----
    for sec in REQUIRED_SECTIONS:
        if not re.search(rf"^##+\s*{re.escape(sec)}", body, re.M):
            errors.append(f"正文缺少 `## {sec}` 章节")

    # ---- 引用的脚本必须存在 ----
    refs = re.findall(r"\$\{HERMES_SKILL_DIR\}/([^\s\"'`)]+)", body)
    if not refs:
        warnings.append("SKILL.md 没有用 ${HERMES_SKILL_DIR} 引用脚本（模板变量替换对 agent 更友好）")
    for ref in sorted(set(refs)):
        if not (root / ref).exists():
            errors.append(f"SKILL.md 引用了不存在的文件：{ref}")

    # ---- 别把凭证打包进去 ----
    for path in root.rglob("*"):
        if path.name in FORBIDDEN_FILES and path.is_file() and path.stat().st_size:
            errors.append(f"技能目录里混进了会话凭证：{path.relative_to(root)}")

    # ---- 脚本能被编译 ----
    for py in sorted((root / "scripts").glob("*.py")):
        try:
            compile(py.read_text(encoding="utf-8"), str(py), "exec")
        except SyntaxError as exc:
            errors.append(f"{py.name} 语法错误：{exc}")

    for w in warnings:
        print(f"  ⚠️  {w}")
    for e in errors:
        print(f"  ❌ {e}")
    if not errors:
        print(f"  ✅ SKILL.md 合规（{fm.get('name') or root.name}）：name/description/metadata/章节/脚本引用全部通过")
    else:
        print(f"\n共 {len(errors)} 个错误")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
