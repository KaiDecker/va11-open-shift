from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .byok import (
    APIProtocol,
    BYOKConfig,
    BYOKConfigurationError,
    BYOKError,
    BYOKProvider,
    ResponseFormat,
    ThinkingMode,
)
from .bridge import BridgeApplication, BridgeConfig, BridgeError, serve_bridge
from .dialogue import DialogueTurnContext
from .distribution import (
    DistributionError,
    install_patch,
    uninstall_patch,
    verify_patch_output,
)
from .runtime_config import RuntimeConfigError, load_runtime_config
from .world_bridge import WorldSceneService
from .launcher import LauncherError, RuntimeSession, build_launch_config
from .game_data import (
    GameDataError,
    compare_inventories,
    inspect_game_data,
    inventory_json,
)
from .models import AgentState, DecisionContext, Goal, Memory, Relationship
from .patch_contract import (
    PatchContractError,
    load_patch_manifest,
    validate_patch_target,
)
from .paired_saves import (
    PairedSaveError,
    PairedSaveManager,
    PairedSaveMismatch,
    WorldSessionCheckpoint,
)
from .package import PackageError, build_mod_package
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
        "--thinking",
        choices=[mode.value for mode in ThinkingMode],
        default=ThinkingMode.DEFAULT.value,
    )
    probe.add_argument(
        "--response-format",
        choices=[response_format.value for response_format in ResponseFormat],
        default=ResponseFormat.JSON_OBJECT.value,
        help="use json_object for providers without strict JSON Schema support",
    )
    dialogue_probe = subparsers.add_parser(
        "probe-dialogue",
        help="make exactly one private Agent dialogue call without writing a database",
    )
    dialogue_probe.add_argument("--base-url", required=True)
    dialogue_probe.add_argument("--model", required=True)
    dialogue_probe.add_argument(
        "--protocol",
        choices=[protocol.value for protocol in APIProtocol],
        default=APIProtocol.CHAT_COMPLETIONS.value,
    )
    dialogue_probe.add_argument("--api-key-env", default="OPEN_SHIFT_API_KEY")
    dialogue_probe.add_argument("--timeout", type=float, default=30.0)
    dialogue_probe.add_argument(
        "--thinking",
        choices=[mode.value for mode in ThinkingMode],
        default=ThinkingMode.DEFAULT.value,
    )
    dialogue_probe.add_argument(
        "--response-format",
        choices=[response_format.value for response_format in ResponseFormat],
        default=ResponseFormat.JSON_OBJECT.value,
    )
    bridge = subparsers.add_parser(
        "serve-bridge",
        help="serve the loopback-only GameMaker bridge",
    )
    bridge.add_argument("--host", default="127.0.0.1")
    bridge.add_argument("--port", type=int, default=8711)
    bridge.add_argument("--token-env", default="OPEN_SHIFT_BRIDGE_TOKEN")
    bridge.add_argument("--world-db", type=Path)
    bridge.add_argument("--native-save-dir", type=Path)
    bridge.add_argument("--paired-save-dir", type=Path)
    bridge.add_argument("--seed", type=int, default=7)
    bridge.add_argument("--advance-minutes", type=int, default=1440)
    bridge.add_argument("--prefetch-days", type=int, choices=(0, 1), default=0)
    bridge.add_argument("--provider-base-url")
    bridge.add_argument("--provider-model")
    bridge.add_argument("--provider-protocol", choices=[item.value for item in APIProtocol])
    bridge.add_argument("--provider-response-format", choices=[item.value for item in ResponseFormat])
    bridge.add_argument("--provider-api-key-env", default="OPEN_SHIFT_API_KEY")
    bridge.add_argument("--provider-timeout", type=float, default=30.0)
    bridge.add_argument("--provider-max-calls", type=int, default=100000)
    bridge.add_argument("--provider-required", action="store_true")
    bridge.add_argument(
        "--provider-thinking",
        choices=[mode.value for mode in ThinkingMode],
        default=ThinkingMode.DEFAULT.value,
    )
    launch = subparsers.add_parser(
        "launch",
        help="start the local world bridge and a copied GameMaker game",
    )
    launch.add_argument("--db", type=Path, required=True)
    launch.add_argument("--native-save-dir", type=Path)
    launch.add_argument("--paired-save-dir", type=Path)
    launch.add_argument("--runtime-file", type=Path, required=True)
    launch.add_argument("--game-cwd", type=Path, required=True)
    launch.add_argument("--game-command", nargs="+", required=True)
    launch.add_argument("--steam-root", type=Path)
    launch.add_argument("--steam-app-id", type=int)
    launch.add_argument("--seed", type=int, default=7)
    launch.add_argument("--port", type=int, default=0)
    launch.add_argument("--advance-minutes", type=int, default=1440)
    launch.add_argument("--prefetch-days", type=int, choices=(0, 1), default=0)
    launch.add_argument("--config", type=Path)
    launch.add_argument("--health-timeout", type=float, default=10.0)
    launch.add_argument("--bridge-command", nargs="+")
    launch.add_argument("--provider-base-url")
    launch.add_argument("--provider-model")
    launch.add_argument("--provider-protocol", choices=[item.value for item in APIProtocol])
    launch.add_argument("--provider-response-format", choices=[item.value for item in ResponseFormat])
    launch.add_argument("--provider-api-key-env", default="OPEN_SHIFT_API_KEY")
    launch.add_argument("--provider-timeout", type=float, default=30.0)
    launch.add_argument("--provider-max-calls", type=int, default=100000)
    launch.add_argument("--provider-required", action="store_true")
    launch.add_argument("--prepare-before-game", action="store_true")
    launch.add_argument("--prepare-timeout", type=float, default=600.0)
    launch.add_argument(
        "--provider-thinking",
        choices=[mode.value for mode in ThinkingMode],
        default=ThinkingMode.DEFAULT.value,
    )
    inventory = subparsers.add_parser(
        "inspect-game-data",
        help="read a data.win and print a names-only inventory",
    )
    inventory.add_argument("--data-win", type=Path, required=True)
    inventory.add_argument("--compare", type=Path)
    patch_target = subparsers.add_parser(
        "validate-patch-target",
        help="validate a data.win against the patch manifest without modifying it",
    )
    patch_target.add_argument("--data-win", type=Path, required=True)
    patch_target.add_argument("--manifest", type=Path, required=True)
    install = subparsers.add_parser(
        "install-patch",
        help="install a verified patch into an isolated data.win copy",
    )
    install.add_argument("--original-data-win", type=Path, required=True)
    install.add_argument("--patched-data-win", type=Path, required=True)
    install.add_argument("--destination-data-win", type=Path, required=True)
    install.add_argument("--backup-dir", type=Path, required=True)
    install.add_argument("--record", type=Path, required=True)
    install.add_argument("--manifest", type=Path, required=True)
    uninstall = subparsers.add_parser(
        "uninstall-patch",
        help="restore the verified pre-install data.win backup",
    )
    uninstall.add_argument("--record", type=Path, required=True)
    config = subparsers.add_parser(
        "validate-config",
        help="validate a local non-secret Open Shift TOML configuration",
    )
    config.add_argument("--config", type=Path, required=True)
    verify = subparsers.add_parser(
        "verify-patch-output",
        help="verify a patched release candidate without installing it",
    )
    verify.add_argument("--original-data-win", type=Path, required=True)
    verify.add_argument("--patched-data-win", type=Path, required=True)
    verify.add_argument("--manifest", type=Path, required=True)
    verify.add_argument("--gml-source-dir", type=Path, required=True)
    package = subparsers.add_parser(
        "build-mod-package",
        help="build a source-only Mod zip without original game files",
    )
    package.add_argument("--project-root", type=Path, default=Path("."))
    package.add_argument("--output", type=Path, required=True)
    package.add_argument("--version", default="0.1.0")
    package.add_argument("--runtime-exe", type=Path)
    package.add_argument("--gui-exe", type=Path)
    package.add_argument("--icon", type=Path)
    package.add_argument("--utmt-cli-zip", type=Path)
    package.add_argument("--webview-dll", type=Path, action="append", default=[])
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
            thinking_mode=ThinkingMode(args.thinking),
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


def _probe_dialogue(args: argparse.Namespace) -> int:
    decision = _probe_context()
    decision = DecisionContext(
        tick=decision.tick,
        seed=decision.seed,
        actor=decision.actor,
        agents=decision.agents,
        relationships=decision.relationships,
        goals=decision.goals,
        locations=decision.locations,
        memories=(
            Memory(
                1,
                1,
                420,
                0.7,
                "Dorothy recently checked in after a difficult shift.",
                ("social", "dorothy"),
            ),
        ),
    )
    context = DialogueTurnContext(
        scene_id="dialogue_probe",
        turn_index=0,
        turn_count=4,
        premise="第1天，Dana结束工作后在酒吧遇到了Dorothy。",
        speaker=decision,
        participant_ids=("dana", "dorothy"),
    )
    try:
        provider = BYOKProvider.from_env(
            BYOKConfig(
                base_url=args.base_url,
                model=args.model,
                protocol=APIProtocol(args.protocol),
                response_format=ResponseFormat(args.response_format),
                timeout_seconds=args.timeout,
                api_key_env=args.api_key_env,
                max_calls=1,
                thinking_mode=ThinkingMode(args.thinking),
            )
        )
        line = provider.generate_dialogue_line(context)
    except BYOKError as exc:
        print(f"Dialogue probe failed: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {"speaker_id": "dana", "expression_id": line.expression_id, "text": line.text},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _report_world_error(operation: str, error: Exception) -> None:
    detail = str(error) if isinstance(error, BYOKError) else "unexpected internal error"
    print(
        f"World {operation} failed ({type(error).__name__}): {detail}",
        file=sys.stderr,
        flush=True,
    )


def _serve_bridge(args: argparse.Namespace) -> int:
    import os

    token = os.environ.get(args.token_env)
    if token is None:
        print(
            f"Bridge token environment variable was not set: {args.token_env}",
            file=sys.stderr,
        )
        return 2
    try:
        config = BridgeConfig(token=token, host=args.host, port=args.port)
    except ValueError as exc:
        print(f"Bridge configuration failed: {exc}", file=sys.stderr)
        return 2
    del token
    try:
        print(f"Open Shift bridge listening on http://{config.host}:{config.port}")
        if args.world_db is None:
            serve_bridge(config)
        else:
            provider_factory = _provider_factory(args)
            world = WorldSceneService(
                args.world_db,
                provider_factory=provider_factory,
                error_reporter=_report_world_error,
                seed=args.seed,
                advance_minutes=args.advance_minutes,
                daily_story_mode=True,
                prefetch_days=args.prefetch_days,
                allow_provider_fallback=not args.provider_required,
            )
            local_app_data = Path(
                os.environ.get("LOCALAPPDATA", str(args.world_db.parent))
            )
            native_save_dir = args.native_save_dir or (
                local_app_data / "VA_11_Hall_A" / "saves"
            )
            paired_save_dir = args.paired_save_dir or (
                local_app_data / "VA_11_Hall_A" / "open-shift-paired-saves"
            )
            paired_saves = PairedSaveManager(
                args.world_db, native_save_dir, paired_save_dir
            )
            checkpoint_path = os.environ.get("OPEN_SHIFT_SESSION_CHECKPOINT")
            session_checkpoint = (
                WorldSessionCheckpoint(args.world_db, checkpoint_path)
                if checkpoint_path
                else None
            )

            def save_pair(request: dict[str, object]) -> dict[str, object]:
                return _paired_save_response(
                    world,
                    paired_saves,
                    request,
                    "paired",
                    session_checkpoint=session_checkpoint,
                )

            def restore_pair(request: dict[str, object]) -> dict[str, object]:
                return _paired_save_response(
                    world,
                    paired_saves,
                    request,
                    "restored",
                    session_checkpoint=session_checkpoint,
                )

            serve_bridge(
                config,
                app=BridgeApplication(
                    config,
                    scene_provider=world.open_scene,
                    scene_hint_provider=world.scene_speaker_hint,
                    ack_handler=world.ack_scene,
                    order_handler=world.resolve_order,
                    save_pair_handler=save_pair,
                    save_restore_handler=restore_pair,
                    tablet_feed_handler=world.tablet_feed,
                    story_prepare_handler=world.prepare_story_day,
                    error_reporter=_report_world_error,
                ),
            )
    except KeyboardInterrupt:
        return 0
    except (BYOKError, OSError, ValueError) as exc:
        print(f"Bridge startup failed: {exc}", file=sys.stderr)
        return 2
    return 0


def _paired_save_response(
    world: WorldSceneService,
    manager: PairedSaveManager,
    request: dict[str, object],
    status: str,
    *,
    session_checkpoint: WorldSessionCheckpoint | None = None,
) -> dict[str, object]:
    world.wait_for_background_generation()
    try:
        record = (
            manager.save_slot(
                int(request["slot"]),
                operation_id=str(request["request_id"]),
                request=request,
            )
            if status == "paired"
            else manager.restore_slot(
                int(request["slot"]),
                operation_id=str(request["request_id"]),
                request=request,
            )
        )
        if status == "paired":
            world.complete_paired_save(
                record.world_day, record.last_completed_story_day
            )
        if session_checkpoint is not None:
            session_checkpoint.capture()
    except PairedSaveMismatch as exc:
        raise BridgeError(409, exc.code, "paired save did not match") from None
    except PairedSaveError as exc:
        if exc.code == "paired_save_missing":
            status_code = 404
        elif exc.code == "operation_id_conflict":
            status_code = 409
        else:
            status_code = 503
        raise BridgeError(
            status_code, exc.code, f"paired save {exc.layer} failed"
        ) from None
    return {
        "slot": record.slot,
        "revision": record.revision,
        "status": status,
        "world_day": record.world_day,
    }


def _provider_factory(args: argparse.Namespace):
    if args.provider_base_url is None and args.provider_model is None:
        return None
    provider_model = args.provider_model
    if args.provider_base_url and provider_model is None and "deepseek.com" in args.provider_base_url:
        provider_model = "deepseek-v4-flash"
    if not args.provider_base_url or not provider_model:
        raise BYOKError(
            "provider-base-url and provider-model must be supplied together"
        )
    protocol = APIProtocol(args.provider_protocol or APIProtocol.CHAT_COMPLETIONS.value)
    response_format = ResponseFormat(
        args.provider_response_format or ResponseFormat.JSON_OBJECT.value
    )

    thinking_mode = ThinkingMode(args.provider_thinking)
    if provider_model == "deepseek-v4-flash" and thinking_mode is ThinkingMode.DEFAULT:
        thinking_mode = ThinkingMode.DISABLED
    try:
        provider = BYOKProvider.from_env(
            BYOKConfig(
                base_url=args.provider_base_url,
                model=provider_model,
                protocol=protocol,
                response_format=response_format,
                timeout_seconds=args.provider_timeout,
                api_key_env=args.provider_api_key_env,
                max_calls=args.provider_max_calls,
                thinking_mode=thinking_mode,
            )
        )
    except BYOKConfigurationError:
        if args.provider_required:
            raise
        # A missing optional key must not prevent the local bridge from
        # starting. The world remains playable with deterministic dialogue;
        # setting the key enables the configured DeepSeek provider.
        return lambda: MockProvider()

    return lambda: provider


def _launch(args: argparse.Namespace) -> int:
    try:
        if args.config is not None:
            runtime = load_runtime_config(args.config)
            args.prefetch_days = runtime.prefetch_days
            args.provider_base_url = runtime.provider_base_url
            args.provider_model = runtime.provider_model
            args.provider_protocol = runtime.provider_protocol.value
            args.provider_response_format = runtime.provider_response_format.value
            args.provider_api_key_env = runtime.provider_api_key_env
            args.provider_timeout = runtime.provider_timeout_seconds
            args.provider_max_calls = runtime.provider_max_calls
            args.provider_thinking = runtime.provider_thinking.value
        config = build_launch_config(
            db_path=args.db,
            runtime_file=args.runtime_file,
            game_command=args.game_command,
            game_cwd=args.game_cwd,
            steam_root=args.steam_root,
            steam_app_id=args.steam_app_id,
            seed=args.seed,
            port=args.port,
            advance_minutes=args.advance_minutes,
            health_timeout_seconds=args.health_timeout,
            prepare_story_before_game=args.prepare_before_game,
            story_prepare_timeout_seconds=args.prepare_timeout,
            bridge_command=tuple(args.bridge_command or ()),
            bridge_extra_args=tuple(
                item
                for pair in (
                    ("--provider-base-url", args.provider_base_url),
                    ("--provider-model", args.provider_model),
                    ("--provider-protocol", args.provider_protocol),
                    ("--provider-response-format", args.provider_response_format),
                    ("--provider-api-key-env", args.provider_api_key_env),
                    ("--provider-timeout", str(args.provider_timeout)),
                    ("--provider-max-calls", str(args.provider_max_calls)),
                    ("--provider-thinking", args.provider_thinking),
                    (
                        "--provider-required",
                        "" if args.provider_required else None,
                    ),
                    ("--prefetch-days", str(args.prefetch_days)),
                    (
                        "--native-save-dir",
                        str(args.native_save_dir) if args.native_save_dir else None,
                    ),
                    (
                        "--paired-save-dir",
                        str(args.paired_save_dir) if args.paired_save_dir else None,
                    ),
                )
                if pair[1] is not None
                for item in pair
                if item != ""
            ),
        )
        return RuntimeSession(config).run()
    except (LauncherError, OSError, ValueError) as exc:
        print(f"Launch failed: {exc}", file=sys.stderr)
        return 2


def _inspect_game_data(args: argparse.Namespace) -> int:
    try:
        baseline = inspect_game_data(args.data_win)
        if args.compare is None:
            print(inventory_json(baseline))
        else:
            print(
                json.dumps(
                    compare_inventories(
                        baseline, inspect_game_data(args.compare)
                    ),
                    ensure_ascii=False,
                    indent=2,
                )
            )
    except (OSError, GameDataError) as exc:
        print(f"Game data inspection failed: {exc}", file=sys.stderr)
        return 1
    return 0


def _validate_patch_target(args: argparse.Namespace) -> int:
    try:
        inventory = inspect_game_data(args.data_win)
        validate_patch_target(load_patch_manifest(args.manifest), inventory)
    except (OSError, json.JSONDecodeError, GameDataError, PatchContractError) as exc:
        print(f"Patch target validation failed: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "status": "supported",
                "data_win_sha256": inventory.sha256,
                "file_size": inventory.file_size,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _install_patch(args: argparse.Namespace) -> int:
    try:
        record = install_patch(
            original_data_win=args.original_data_win,
            patched_data_win=args.patched_data_win,
            destination_data_win=args.destination_data_win,
            backup_dir=args.backup_dir,
            manifest=load_patch_manifest(args.manifest),
            record_path=args.record,
        )
    except (OSError, json.JSONDecodeError, GameDataError, PatchContractError, DistributionError) as exc:
        print(f"Patch installation failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(record.to_dict(), ensure_ascii=False, indent=2))
    return 0


def _uninstall_patch(args: argparse.Namespace) -> int:
    try:
        record = uninstall_patch(record_path=args.record)
    except (OSError, json.JSONDecodeError, GameDataError, DistributionError) as exc:
        print(f"Patch uninstall failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(record.to_dict(), ensure_ascii=False, indent=2))
    return 0


def _validate_config(args: argparse.Namespace) -> int:
    try:
        config = load_runtime_config(args.config)
    except (OSError, RuntimeConfigError) as exc:
        print(f"Runtime configuration failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(config.redacted_dict(), ensure_ascii=False, indent=2))
    return 0


def _verify_patch_output(args: argparse.Namespace) -> int:
    try:
        result = verify_patch_output(
            original_data_win=args.original_data_win,
            patched_data_win=args.patched_data_win,
            manifest=load_patch_manifest(args.manifest),
            gml_source_dir=args.gml_source_dir,
        )
    except (OSError, json.JSONDecodeError, PatchContractError, DistributionError) as exc:
        print(f"Patch output verification failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _build_mod_package(args: argparse.Namespace) -> int:
    try:
        result = build_mod_package(
            project_root=args.project_root,
            output=args.output,
            version=args.version,
            runtime_exe=args.runtime_exe,
            gui_exe=args.gui_exe,
            icon=args.icon,
            utmt_cli=args.utmt_cli_zip,
            webview_dlls=tuple(args.webview_dll),
        )
    except (PackageError, OSError, ValueError) as exc:
        print(f"Package build failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "simulate":
        return _simulate(args)
    if args.command == "probe-provider":
        return _probe_provider(args)
    if args.command == "probe-dialogue":
        return _probe_dialogue(args)
    if args.command == "serve-bridge":
        return _serve_bridge(args)
    if args.command == "launch":
        return _launch(args)
    if args.command == "inspect-game-data":
        return _inspect_game_data(args)
    if args.command == "validate-patch-target":
        return _validate_patch_target(args)
    if args.command == "install-patch":
        return _install_patch(args)
    if args.command == "uninstall-patch":
        return _uninstall_patch(args)
    if args.command == "validate-config":
        return _validate_config(args)
    if args.command == "verify-patch-output":
        return _verify_patch_output(args)
    if args.command == "build-mod-package":
        return _build_mod_package(args)
    parser.error(f"unknown command: {args.command}")
    return 2
