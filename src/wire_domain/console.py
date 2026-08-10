"""Shared rich consoles. stdout for normal output, stderr for errors."""

from rich.console import Console

console = Console()
err_console = Console(stderr=True)
