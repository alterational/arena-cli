#!/usr/bin/env python3
"""Hermes skill entry point (keeps the "one sentence in, answer body out" contract).

    python3 scripts/arena_ask.py "your question"
    python3 scripts/arena_ask.py --cookies ~/uploads/arena.ai_cookies.txt "your question"

Success: stdout contains only the answer body; exit code 0.
Failure: stdout has one line `❌ <CODE>: <what to do next>`; exit code 2/3/4/5/6.

Modern equivalent: `arena ask "your question"`
"""

from __future__ import annotations

import argparse
import contextlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from arena.config import arena_home, profile_paths, read_config          # noqa: E402
from arena.engine import (EXIT_CODES, Session, SkillError, cmd_import,   # noqa: E402
                          load_config)
from arena.session import explain                                        # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="arena_ask", description="Send one sentence to arena.ai/agent and print the answer.")
    p.add_argument("prompt", nargs="*", help="Content to send; `-` or omitting it reads from stdin")
    p.add_argument("--cookies", help="Cookies file; import it first, then ask")
    p.add_argument("--json", action="store_true", help="Output structured JSON")
    p.add_argument("--max-chars", type=int, default=0, help="Truncate long answers (0 = no truncation)")
    p.add_argument("--timeout", type=float)
    p.add_argument("--stable", type=float)
    p.add_argument("--profile", help="Which profile to use")
    p.add_argument("--config")
    p.add_argument("--base-url")
    p.add_argument("--state-file")
    p.add_argument("--profile-dir")
    p.add_argument("--headed", action="store_true")
    p.add_argument("--verbose", "-v", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    prompt = " ".join(args.prompt) if args.prompt and args.prompt != ["-"] else sys.stdin.read()
    prompt = (prompt or "").strip()
    if not prompt:
        print("❌ BAD_INPUT: No content was received to send to the Agent (usage: arena ask \"your question\")")
        return 1

    home = arena_home()
    cfg_file = Path(args.config).expanduser() if args.config else home / "config.json"
    file_cfg = read_config(cfg_file)
    profile = args.profile or file_cfg.get("profile") or "default"
    state_file, profile_dir = profile_paths(profile, home)
    if args.state_file:
        state_file = Path(args.state_file).expanduser()
    if args.profile_dir:
        profile_dir = Path(args.profile_dir).expanduser()

    cfg = load_config(args.config, {
        "base_url": args.base_url,
        "wait_timeout": args.timeout,
        "stable_seconds": args.stable,
    })

    captured = io.StringIO()
    try:
        if args.cookies:
            cmd_import(Path(args.cookies).expanduser(), state_file, cfg)
        with contextlib.redirect_stderr(captured):
            with Session(cfg, profile_dir, state_file, headed=args.headed) as session:
                result = session.ask(prompt, timeout=args.timeout, stable=args.stable)
    except SkillError as exc:
        if args.verbose:
            sys.stderr.write(captured.getvalue())
        if args.json:
            import json
            print(json.dumps({"ok": False, "code": exc.code, "error": exc.message,
                              "hint": explain(exc.code, exc.message)}, ensure_ascii=False, indent=2))
        else:
            print(f"❌ {exc.code}: {explain(exc.code, exc.message)}")
            print(f"  Details: {exc.message}", file=sys.stderr)
        return EXIT_CODES.get(exc.code, 1)
    finally:
        if args.verbose:
            sys.stderr.write(captured.getvalue())

    answer = result.get("answer", "")
    if args.max_chars and len(answer) > args.max_chars:
        answer = answer[: args.max_chars].rstrip() + \
            f"\n\n…(truncated; {len(result.get('answer', ''))} characters in total)"
        if result.get("url"):
            answer += f"\nFull content: {result['url']}"
    if args.json:
        import json
        result["answer"] = answer
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(answer)
    return 0


if __name__ == "__main__":
    sys.exit(main())
