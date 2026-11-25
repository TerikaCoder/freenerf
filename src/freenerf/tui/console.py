from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown
from rich.theme import Theme

theme = Theme({
    "info": "cyan",
    "success": "green",
    "error": "bold red",
    "muted": "dim",
})

console = Console(theme=theme)


def banner():
    console.print(
        Panel(
            "[bold cyan]FREENERF[/bold cyan]\n"
            "[dim]AI coding assistant for your terminal[/dim]",
            border_style="cyan",
        )
    )


def show_message(role: str, content: str):
    if role == "assistant":
        console.print(
            Panel(
                Markdown(content),
                title="[bold cyan]Freenerf[/bold cyan]",
                border_style="cyan",
            )
        )
    else:
        console.print(
            Panel(
                content,
                title="[bold green]You[/bold green]",
                border_style="green",
            )
        )
