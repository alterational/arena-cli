"""arena config list|get|set|unset|path — read and write ~/.arena-cli/config.json."""

from __future__ import annotations

import json

from ..config import SETTABLE, read_config, write_config
from ..engine import mask_proxy


def _coerce(raw: str):
    """Command-line values are strings; restore their type using JSON rules (123 / true / ["a"] / plain text)."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def run_list(ctx, args) -> int:
    data = {"config_file": str(ctx.paths.config_file),
            "values": {k: mask_proxy(str(ctx.get(k) or "")) if k == "proxy" else ctx.get(k) for k in sorted(SETTABLE)},
            "effective": {"profile": ctx.paths.profile,
                          "state_file": str(ctx.paths.state_file),
                          "profile_dir": str(ctx.paths.profile_dir)}}

    def human() -> None:
        ctx.ui.kv([(k, v) for k, v in data["values"].items()],
                  title=f"Configuration ({ctx.paths.config_file})")
        ctx.ui.kv(list(data["effective"].items()), title="\nEffective now")

    return ctx.emit(data, human=human)


def run_get(ctx, args) -> int:
    key = args.key
    if key not in SETTABLE:
        ctx.ui.fail("BAD_INPUT", f"Unknown configuration key: {key}", "Available keys: " + ", ".join(sorted(SETTABLE)))
        return 1
    value = ctx.get(key)
    if key == "proxy":
        value = mask_proxy(str(value or ""))
    return ctx.emit({key: value}, human=lambda: print(
        json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value))


def run_set(ctx, args) -> int:
    key = args.key
    if key not in SETTABLE:
        ctx.ui.fail("BAD_INPUT", f"Unknown configuration key: {key}", "Available keys: " + ", ".join(sorted(SETTABLE)))
        return 1
    value = _coerce(args.value)
    if key in ("block_analytics_scripts", "reduce_visual_effects") and not isinstance(value, bool):
        ctx.ui.fail("BAD_INPUT", f"{key} must be true or false")
        return 1
    if key == "base_url":
        from ..url_safety import validate_base_url
        try:
            validate_base_url(value)
        except (ValueError, TypeError):
            ctx.ui.fail("BAD_INPUT", "Invalid base URL", "Use an HTTP(S) origin without credentials, path, query or fragment")
            return 1
    shown = mask_proxy(str(value)) if key == "proxy" else value
    if ctx.dry_run():
        return ctx.emit({"key": key, "value": shown,
                         "config_file": str(ctx.paths.config_file)}, dry_run=True,
                        human=lambda: ctx.ui.info(
                            f"[dry-run] would set {key} to {shown} → {ctx.paths.config_file}"))

    data = read_config(ctx.paths.config_file)
    data.pop("_invalid", None)
    if key == "selectors" and not isinstance(value, dict):
        ctx.ui.fail("BAD_INPUT", "selectors must be a JSON object", 'Example: arena config set selectors \'{"editor_selectors":["..."]}\'')
        return 1
    data[key] = value
    write_config(ctx.paths.config_file, data)
    return ctx.emit({"key": key, "value": shown, "config_file": str(ctx.paths.config_file)},
                    human=lambda: ctx.ui.ok(
                        f"{key} = {json.dumps(shown, ensure_ascii=False)} → {ctx.paths.config_file}"))


def run_unset(ctx, args) -> int:
    data = read_config(ctx.paths.config_file)
    if args.key not in data:
        ctx.ui.warn(f"{args.key} is not set; nothing to remove")
        return 0
    data.pop(args.key, None)
    write_config(ctx.paths.config_file, data)
    ctx.ui.ok(f"Removed {args.key} (reverted to default)")
    return 0


def run_path(ctx, args) -> int:
    print(ctx.paths.config_file)
    return 0
