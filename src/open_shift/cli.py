from __future__ import annotations

import argparse
import json
from pathlib import Path

from .providers import MockProvider
from .scenario import create_demo_world
from .store import WorldStore


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="open-shift")
    subparsers = parser.add_subparsers(dest="command", required=True)
    simulate = subparsers.add_parser("simulate", help="run the headless demo world")
    simulate.add_argument("--db", type=Path, required=True)
    simulate.add_argument("--days", type=int, default=30)
    simulate.add_argument("--seed", type=int, default=7)
    simulate.add_argument("--fresh", action="store_true")
    simulate.add_argument("--json", action="store_true", dest="as_json")
    return parser


def _simulate(args: argparse.Namespace) -> int:
    db_path: Path = args.db.resolve()
    if args.fresh:
        for candidate in (
            db_path,
            Path(f"{db_path}-shm"),
            Path(f"{db_path}-wal"),
        ):
            if candidate.exists():
                candidate.unlink()
    with WorldStore(db_path) as store:
        engine = create_demo_world(store, MockProvider(), seed=args.seed)
        report = engine.run_days(args.days)
        if args.as_json:
            print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        else:
            print(
                f"Simulated {report.elapsed_days:.1f} days at tick "
                f"{report.current_tick}."
            )
            print(
                f"Turns: {report.processed_turns}; rejected: "
                f"{report.rejected_actions}; provider errors: "
                f"{report.provider_errors}."
            )
            print(f"Events: {report.event_counts}")
            print(f"Completed goals: {report.completed_goals}")
            for agent in report.agents:
                print(
                    f"- {agent['display_name']}: location={agent['location']}, "
                    f"money={agent['money']}, fatigue={agent['fatigue']:.2f}"
                )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "simulate":
        return _simulate(args)
    parser.error(f"unknown command: {args.command}")
    return 2
