"""Web OS entry point (IDEA.md §6.1).

Usage:
    uv run main.py [--host HOST] [--port PORT] [--debug]

Default bind 127.0.0.1:11686. Use --host 0.0.0.0 to allow access from all
addresses. The one-time login password is printed to this console at startup
and is never persisted: restarting the service invalidates sessions and
generates a new password.
"""
import argparse
import os
import sys

from app import create_app
from app import sockets
from config import BOOT_ID, PASSWORD

app = create_app()

_banner_done = False


def print_banner(host="127.0.0.1", port=11686):
    global _banner_done
    if _banner_done:
        return
    _banner_done = True
    shown_host = host if host not in ("0.0.0.0", "::") else \
        f"<this machine's address> (bound {host})"
    print("==============================================================", flush=True)
    print("  Web OS — single-user server operations workbench", flush=True)
    print("--------------------------------------------------------------", flush=True)
    print(f"  Password  : {PASSWORD}", flush=True)
    print(f"  Boot ID   : {BOOT_ID}", flush=True)
    print(f"  Web UI    : http://{shown_host}:{port}", flush=True)
    print("  Password is printed in the server console at startup.", flush=True)
    print("  Restart the service if you forgot it.", flush=True)
    print("==============================================================", flush=True)


def main():
    parser = argparse.ArgumentParser(description="Web OS server")
    parser.add_argument("--host", default="127.0.0.1",
                        help="bind address (0.0.0.0 = all interfaces)")
    parser.add_argument("--port", type=int, default=11686,
                        help="port (default 11686)")
    parser.add_argument("--debug", action="store_true",
                        help="development mode with auto-reload")
    args = parser.parse_args()

    # With the reloader active, background threads belong to the child.
    if not args.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        from app import start_background
        start_background()

    print_banner(args.host, args.port)
    sockets.socketio.run(app, host=args.host, port=args.port, debug=args.debug,
                          allow_unsafe_werkzeug=True)


if __name__ == "__main__":
    sys.exit(main())
