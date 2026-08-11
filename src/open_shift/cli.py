from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .byok import (
    APIProtocol,
    BYOKConfig,
    BYOKError,
    BYOKProvider,
    ResponseFormat,
)
from .models import AgentState, DecisionContext, Goal, Relationship
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

    probe = subparsers.add_parser(
        "probe-provider",
        help="make exactly one BYOK decision call without writing a world database",
    )
    probe.add_argument("--base-url", required=True)
    probe.add_argument("--model", required=True)
    probe.add_argument(
        "--protocol",
        choices=[protocol.value for protocol in APIProtocol],
        default=APIProtocol.CHAT_COMPLETIONS.value,
    )
    probe.add_argument("--api-key-env", default="OPEN_SHIFT_API_KEY")
    probe.add_argument("--timeout", type=float, default=30.0)
    probe.add_argument(
        "--response-format",
        choices=[response_format.value for response_format in ResponseFormat],
        default=ResponseFormat.JSON_OBJECT.value,
        help="use json_object for providers without strict JSON Schema support",
    )
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


def _probe_context() -> DecisionContext:
    dana = AgentState("dana", "Dana", "home", 90, 0.2, "steady", 480)
    dorothy = AgentState("dorothy", "Dorothy", "work", 25, 0.35, "playful", 600)
    return DecisionContext(
        tick=480,
        seed=7,
        actor=dana,
        agents=(dana, dorothy),
        relationships=(Relationship("dana", "dorothy", 0.12, 0.18),),
        goals=(Goal("dana_savings", "dana", "savings", None, 150, 0.7),),
        locations=("home", "work", "va11_hall_a"),
    )


def _probe_provider(args: argparse.Namespace) -> int:
    try:
        config = BYOKConfig(
            base_url=args.base_url,
            model=args.model,
            protocol=APIProtocol(args.protocol),
            response_format=ResponseFormat(args.response_format),
            timeout_seconds=args.timeout,
            api_key_env=args.api_key_env,
            max_calls=1,
        )
        action = BYOKProvider.from_env(config).decide(_probe_context())
    except BYOKError as exc:
        print(f"Provider probe failed: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "action_type": action.action_type.value,
                "target_id": action.target_id,
                "location": action.location,
                "duration_minutes": action.duration_minutes,
                "reason_code": action.reason_code,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "simulate":
        return _simulate(args)
    if args.command == "probe-provider":
        return _probe_provider(args)
    parser.error(f"unknown command: {args.command}")
    return 2
