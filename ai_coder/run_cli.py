# Author: <YOUR NAME> (Student ID: <YOUR STUDENT ID>)
# SWE 270P Final Project - AI Coder: command-line runner for the pipeline.
"""Run the multi-agent pipeline from the terminal.

Examples:
    python run_cli.py --spec specs/customer_feedback_tracker.md
    LLM_PROVIDER=mock python run_cli.py --spec specs/customer_feedback_tracker.md
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ai_coder.orchestrator import run_sync
from ai_coder.parsing import split_spec


def print_event(event: dict) -> None:
    """Print one progress event."""
    print(f"[{event['time']}] {event['agent']:>12} | {event['message']}", flush=True)


def main() -> int:
    """Parse arguments, run the pipeline and print a summary."""
    parser = argparse.ArgumentParser(description="AI Coder - multi-agent MCP code generator")
    parser.add_argument("--spec", help="markdown file with '## Description' and requirements")
    parser.add_argument("--description", help="description text (instead of --spec)")
    parser.add_argument("--requirements", default="", help="requirements text (with --description)")
    args = parser.parse_args()
    if args.spec:
        description, requirements = split_spec(Path(args.spec).read_text(encoding="utf-8"))
    elif args.description:
        description, requirements = args.description, args.requirements
    else:
        parser.error("give --spec or --description")

    result = run_sync(description, requirements, on_event=print_event)
    print("\n=== Result ===")
    print(f"Project folder : {result.project_dir}")
    print(f"Tests          : {result.test_report.get('summary', 'not run')}")
    print(f"Fix rounds     : {result.fix_rounds}")
    print("LLM usage      :", json.dumps({k: result.usage[k] for k in (
        "total_api_calls", "total_prompt_tokens", "total_completion_tokens", "total_tokens")}))
    for agent, row in result.usage["by_agent"].items():
        print(f"  {agent:>10}: {row['calls']} calls, {row['total_tokens']} tokens")
    if result.error:
        print(f"Error          : {result.error}")
    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
