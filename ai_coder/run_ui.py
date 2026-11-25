# Author: <YOUR NAME> (Student ID: <YOUR STUDENT ID>)
# SWE 270P Final Project - AI Coder: starts the web UI.
"""Start the web UI:  python run_ui.py [--port 5000]"""

import argparse

from ai_coder.ui.app import app


def main() -> None:
    """Parse host/port and run the Flask server (threaded, no reloader)."""
    parser = argparse.ArgumentParser(description="AI Coder web UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()
    print(f"AI Coder UI running at http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
