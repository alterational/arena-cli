"""One-request browser subprocess for the terminal interface."""
from __future__ import annotations
import json
import signal
import sys
from pathlib import Path


def emit(kind, **data):
    print(json.dumps({"type": kind, **data}, ensure_ascii=True), flush=True)


def main():
    from .commands.ask import resolve_chat
    from .engine import Session, SkillError, extract_chat_id
    from .errors import ArenaError
    from .url_safety import same_origin_url
    class StopWorker(BaseException):
        pass
    def stop(_signum, _frame):
        raise StopWorker()
    old = signal.signal(signal.SIGTERM, stop)
    attempted, chat_url, request = False, None, {}
    try:
        request = json.loads(sys.stdin.readline())
        cfg = request["config"]
        with Session(cfg, Path(request["profile_dir"]), Path(request["state_file"]),
                     headed=bool(request.get("headed"))) as session:
            chat = request.get("chat")
            if chat:
                chat = resolve_chat(session, chat)
                emit("resolved", chat=chat)
            attempted = True
            try:
                result = session.ask(request["prompt"], timeout=request.get("timeout"),
                                     stable=request.get("stable"), chat=chat, poll=0.5,
                                     on_update=lambda text: emit("progress", text=text))
            except Exception:
                try:
                    url = session.page.url if session.page else ""
                    if extract_chat_id(url):
                        chat_url = same_origin_url(cfg["base_url"], url)
                except Exception:
                    pass
                raise
        emit("result", answer=result.get("answer", ""), chat_id=result.get("chat_id"), url=result.get("url"))
        return 0
    except StopWorker:
        return 130
    except Exception as exc:
        if isinstance(exc, SkillError):
            message = f"{exc.code}: {exc.message}"
        elif isinstance(exc, ArenaError):
            message = str(exc) + ("\n" + exc.hint if exc.hint else "")
        else:
            message = f"{type(exc).__name__}: Could not complete the request. Run arena doctor for diagnostics."
        proxy = str(request.get("config", {}).get("proxy") or "")
        if proxy:
            message = message.replace(proxy, "[redacted proxy]")
        emit("error", message=message, chat_url=chat_url, attempted=attempted)
        return 1
    finally:
        signal.signal(signal.SIGTERM, old)


if __name__ == "__main__":
    sys.exit(main())
