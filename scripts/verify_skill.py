#!/usr/bin/env python3
"""
verify_skill.py — checks whether this directory conforms to the Hermes skill spec (the requirements for loading Hermes skills)

Checks:
  * frontmatter can be parsed as YAML and required fields are present (name / description / version)
  * name matches the directory name and is kebab-case
  * description is short (≤ 60 characters; Hermes skill search truncates longer ones)
  * metadata.hermes structure is valid (tags / requires_toolsets / config entries)
  * Body contains the Hermes house-style sections: When to Use / Quick Reference / Procedure / Pitfalls / Verification
  * Scripts referenced in SKILL.md (${HERMES_SKILL_DIR}/...) actually exist
  * No session credentials (cookies.json / state.json) were accidentally bundled into the skill

Usage: python3 scripts/verify_skill.py [skill directory]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # Allow running without pyyaml; only do regex-based validation
    yaml = None

REQUIRED_SECTIONS = ["When to Use", "Quick Reference", "Procedure", "Pitfalls", "Verification"]
FORBIDDEN_FILES = {"state.json", "cookies.json", "prof"}
errors: list[str] = []
warnings: list[str] = []


def parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        raise SystemExit("SKILL.md must start with a YAML frontmatter block (`---`)")
    try:
        _, fm, body = text.split("---", 2)
    except ValueError:
        raise SystemExit("frontmatter is not properly closed (two --- lines are required)")
    if yaml is None:
        warnings.append("pyyaml is not installed; frontmatter was only checked with a rough regex")
        return {"_raw": fm}, body
    data = yaml.safe_load(fm) or {}
    if not isinstance(data, dict):
        raise SystemExit("frontmatter is not a YAML mapping")
    return data, body


def git_repo_name(root: Path) -> str | None:
    """Repository name (basename of origin). The local directory name may be an old name from clone time; only the repository name counts."""
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
        raise SystemExit(f"Cannot find {skill_md}")
    text = skill_md.read_text(encoding="utf-8")
    fm, body = parse_frontmatter(text)

    # ---- name / description / version ----
    name = str(fm.get("name", ""))
    if not name:
        errors.append("missing name")
    else:
        if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name):
            errors.append(f"name must be kebab-case (lowercase + hyphens); current value is {name!r}")
        if name != root.name:
            if root.parent.name == "skills":
                # Once installed into skills/, the directory name is the skill's identity and must match
                errors.append(f"name={name!r} does not match the directory name {root.name!r} (the slash command name comes from name)")
            elif git_repo_name(root) == name:
                pass                      # Repository name = skill name; the local clone's directory name does not matter
            else:
                # The source directory may have a different name; ./install.sh installs to skills/<name>/
                warnings.append(f"Directory name {root.name!r} differs from name={name!r}; "
                                f"this is fine if it is only the local directory name; ./install.sh installs to skills/{name}/")

    desc = str(fm.get("description", "")).strip()
    if not desc:
        errors.append("missing description")
    elif len(desc) > 60:
        warnings.append(f"description has {len(desc)} characters; ≤ 60 is recommended (skill search truncates it)")

    if not fm.get("version"):
        warnings.append("version is recommended (e.g. 1.0.0)")
    if not fm.get("platforms"):
        warnings.append("platforms is not declared; the skill will load on all platforms (this skill is cross-platform, so this can be ignored)")

    # ---- metadata.hermes ----
    hermes = (fm.get("metadata") or {}).get("hermes") or {}
    if not hermes:
        errors.append("missing metadata.hermes (Hermes uses it for skill discovery and conditional activation)")
    else:
        if not hermes.get("tags"):
            warnings.append("metadata.hermes.tags is recommended to make skills_list searches easier")
        toolsets = hermes.get("requires_toolsets") or []
        if "terminal" not in toolsets:
            errors.append("This skill runs its scripts through terminal, so metadata.hermes.requires_toolsets must include terminal")
        for cfg in hermes.get("config") or []:
            if not (isinstance(cfg, dict) and cfg.get("key") and cfg.get("description")):
                errors.append(f"metadata.hermes.config entries require key and description: {cfg!r}")

    # ---- Required sections ----
    for sec in REQUIRED_SECTIONS:
        if not re.search(rf"^##+\s*{re.escape(sec)}", body, re.M):
            errors.append(f"Body is missing the `## {sec}` section")

    # ---- Referenced scripts must exist ----
    refs = re.findall(r"\$\{HERMES_SKILL_DIR\}/([^\s\"'`)]+)", body)
    if not refs:
        warnings.append("SKILL.md does not reference scripts via ${HERMES_SKILL_DIR} (template variable substitution is friendlier to agents)")
    for ref in sorted(set(refs)):
        if not (root / ref).exists():
            errors.append(f"SKILL.md references a file that does not exist: {ref}")

    # ---- Do not bundle credentials ----
    for path in root.rglob("*"):
        if path.name in FORBIDDEN_FILES and path.is_file() and path.stat().st_size:
            errors.append(f"Session credentials found in the skill directory: {path.relative_to(root)}")

    # ---- Scripts must compile ----
    for py in sorted((root / "scripts").glob("*.py")):
        try:
            compile(py.read_text(encoding="utf-8"), str(py), "exec")
        except SyntaxError as exc:
            errors.append(f"{py.name} syntax error: {exc}")

    for w in warnings:
        print(f"  ⚠️  {w}")
    for e in errors:
        print(f"  ❌ {e}")
    if not errors:
        print(f"  ✅ SKILL.md compliant ({fm.get('name') or root.name}): name/description/metadata/sections/script references all passed")
    else:
        print(f"\n{len(errors)} error(s) found")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
