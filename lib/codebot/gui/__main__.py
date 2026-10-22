"""python3 -m codebot.gui [--home DIR] [--port N] [--open] [--demo]: what `codebot gui` runs."""
import argparse, os, sys

def main():
    ap = argparse.ArgumentParser(prog="codebot gui", description="codebot's local web interface (127.0.0.1 only)")
    ap.add_argument("--home", help="codebot's folder (default: this checkout)"); ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--open", action="store_true", help="open it in the default browser"); ap.add_argument("--demo", action="store_true", help="fixture data, actions disabled")
    a = ap.parse_args()
    from ..config import home_dir
    from .server import serve
    home = os.path.realpath(a.home or home_dir())
    if not os.path.isdir(os.path.join(home, "config")): print(f"not a codebot folder: {home}", file=sys.stderr); return 2
    try: serve(home, a.port, a.demo, a.open)
    except OSError as e: print(f"cannot listen on 127.0.0.1:{a.port}: {e.strerror} (another GUI running? try --port)", file=sys.stderr); return 1
    return 0

if __name__ == "__main__":
    sys.exit(main())
