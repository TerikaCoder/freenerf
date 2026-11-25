"""Command-line entry point for Freenerf."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.markdown import Markdown
from rich.prompt import Prompt
from rich.table import Table

from . import __version__
from .agents.runtime import Agent
from .agents.task_store import TaskStore
from .config import CONFIG_FILE, Config
from .llm.catalog import get_models
from .llm.providers import ProviderError, create_provider_for_config
from .sessions.store import SessionStore
from .tools.filesystem import FileSystem
from .tools.image import ImageGenerator
from .tools.terminal import Terminal
from .tui.console import console
from .tui.fonts import (  # re-exported for backwards compatibility
    apply_terminal_font,
    detect_terminal_backend,
    font_size_value,
)
from .workspace import Workspace, WorkspaceError, WorkspaceManager


def choose_workspace(config: Config) -> Workspace:
    manager = WorkspaceManager(config)
    configured = str(config.workspace_root or "").strip()

    if configured:
        try:
            return manager.current()
        except WorkspaceError as exc:
            console.print(f"[red]Configured workspace is unusable: {exc}[/red]")

    default = Path.cwd()

    if sys.stdin.isatty():
        answer = Prompt.ask(
            "Workspace directory (the folder Freenerf may edit)",
            default=str(default),
        ).strip()
    else:
        answer = str(default)

    try:
        return manager.switch(answer or default)
    except WorkspaceError as exc:
        console.print(f"[red]Cannot use workspace: {exc}[/red]")
        raise SystemExit(1)


def setup_config() -> Config | None:
    config = Config.load()

    for warning in config.validation_warnings():
        console.print(f"[yellow]{warning}[/yellow]")

    return config


def run_headless(config, workspace, prompt, auto_approve=False) -> int:
    provider = None
    try:
        provider = create_provider_for_config(config)
    except ProviderError as exc:
        console.print(f"[red]{exc}[/red]")
        return 1

    session_store = SessionStore(workspace.sessions_db)
    task_store = TaskStore(workspace.tasks_db)
    session_id = session_store.create(
        title=" ".join(prompt.split())[:40] or "Headless run",
        workspace=str(workspace.root),
    )
    session_store.add_message(session_id, "user", prompt)

    permission = "full" if auto_approve else config.permission_mode

    def approve(name, detail):
        if auto_approve:
            return True
        console.print(
            f"[yellow]Approval required for {name}: {detail}[/yellow]"
        )
        console.print(
            "[yellow]Non-interactive run denied it. "
            "Re-run with --yes to auto-approve.[/yellow]"
        )
        return False

    agent = Agent(
        provider=provider,
        filesystem=FileSystem(workspace.root),
        terminal=Terminal(workspace.root),
        console=console,
        approve=approve,
        status=lambda message: console.print(f"[dim]{message}[/dim]") if sys.stderr.isatty() else None,
        permission_mode=permission,
        workspace=workspace,
        task_store=task_store,
        session_id=session_id,
        config=config,
        image_generator=ImageGenerator(
            api_key=config.freellmapi_api_key,
            base_url=config.freellmapi_base_url,
            model=config.image_model,
            output_dir=workspace.images_dir,
        ),
    )

    try:
        answer = agent.chat(prompt)
    except ProviderError as exc:
        console.print(f"[red]Provider error: {exc}[/red]")
        return 3
    except KeyboardInterrupt:
        console.print("[yellow]Cancelled.[/yellow]")
        return 130

    outcome = agent.last_outcome
    status = outcome.status if outcome else "unknown"

    if answer:
        console.print(Markdown(answer))
        session_store.add_message(
            session_id, "assistant", answer, metadata={"outcome": status}
        )
    else:
        summary = outcome.summary if outcome else "No final answer."
        console.print(f"[yellow]{summary}[/yellow]")

    console.print(f"[dim]Status: {status}[/dim]")

    return {
        "completed": 0,
        "partial": 2,
        "awaiting_approval": 2,
        "cancelled": 130,
        "failed": 3,
        "blocked": 3,
    }.get(status, 0)


def list_models(config) -> int:
    try:
        models = get_models(
            api_key=config.active_api_key(),
            provider=config.provider,
            base_url=config.active_base_url(),
            force_refresh=True,
        )
    except Exception as exc:
        console.print(f"[red]Cannot load models: {exc}[/red]")
        return 1

    table = Table(title=f"Models · {config.provider}")
    table.add_column("Model")
    table.add_column("Context", justify="right")
    table.add_column("Price")
    table.add_column("Tools")

    for model in models[:100]:
        table.add_row(
            str(model.get("name") or model.get("id")),
            f"{int(model.get('context_length') or 0):,}",
            str(model.get("pricing_label", "Unknown")),
            (
                "yes" if model.get("supports_tools") is True
                else "no" if model.get("supports_tools") is False
                else "?"
            ),
        )

    console.print(table)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="freenerf",
        description="Autonomous multi-provider AI coding agent for the terminal",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"freenerf {__version__}",
    )
    parser.add_argument(
        "--config",
        action="store_true",
        help="Print the configuration file path and exit",
    )
    parser.add_argument(
        "--workspace",
        metavar="PATH",
        help="Use and persist a workspace directory",
    )
    parser.add_argument(
        "--provider",
        choices=["openrouter", "gemini", "freellmapi", "openai-compatible"],
        help="Override the provider for this run",
    )
    parser.add_argument(
        "--model",
        help="Override the model for this run",
    )
    parser.add_argument(
        "--permission",
        choices=["denied", "normal", "full"],
        help="Override the permission mode for this run",
    )
    parser.add_argument(
        "-p", "--print",
        dest="prompt",
        metavar="PROMPT",
        help="Run one task non-interactively and print the result",
    )
    parser.add_argument(
        "-y", "--yes",
        action="store_true",
        help="Auto-approve tool operations in non-interactive mode",
    )
    parser.add_argument(
        "--list-models",
        action="store_true",
        help="List models for the active provider and exit",
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    if args.config:
        print(CONFIG_FILE)
        return 0

    config = Config.load()

    if args.provider:
        config.select_provider(args.provider)
    if args.model:
        config.set_model(args.model)
    if args.permission:
        config.permission_mode = args.permission

    manager = WorkspaceManager(config)

    try:
        if args.workspace:
            workspace = manager.switch(args.workspace)
        else:
            workspace = choose_workspace(config)
    except WorkspaceError as exc:
        console.print(f"[red]{exc}[/red]")
        return 1

    config.workspace_root = str(workspace.root)
    config.save()

    if args.list_models:
        return list_models(config)

    if args.prompt:
        return run_headless(
            config,
            workspace,
            args.prompt,
            auto_approve=args.yes,
        )

    for warning in config.validation_warnings():
        console.print(f"[yellow]{warning}[/yellow]")
    console.print(
        f"[dim]Workspace:[/dim] {workspace.root}\n"
        f"[dim]Provider:[/dim] {config.provider} · {config.active_model()}"
    )

    from .tui.app import FreenerfApp

    try:
        FreenerfApp(config, workspace=workspace).run()
    except KeyboardInterrupt:
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
