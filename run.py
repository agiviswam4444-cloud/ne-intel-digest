#!/usr/bin/env python3
"""NE Intel Digest.

Usage:
  python run.py collect        # run the daily pipeline once
  python run.py serve          # start the web UI at http://127.0.0.1:8642
  python run.py serve --port N
"""
import os, sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ".")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "serve"
    if cmd == "collect":
        from digest.pipeline import run
        run("config.yaml")
    elif cmd == "serve":
        import uvicorn
        port = 8642
        host = "127.0.0.1"   # localhost only; use --host 0.0.0.0 for LAN access
        if "--port" in sys.argv:
            port = int(sys.argv[sys.argv.index("--port") + 1])
        if "--host" in sys.argv:
            host = sys.argv[sys.argv.index("--host") + 1]
        print(f"Serving on http://{host}:{port}  (LAN: python run.py serve --host 0.0.0.0)")
        uvicorn.run("digest.app:app", host=host, port=port)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
