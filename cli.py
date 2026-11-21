"""Inbox Tend command line commands."""
from pathlib import Path

import typer

cli = typer.Typer(help='Run and maintain Inbox Tend.')


@cli.command()
def serve(port: int = typer.Option(8765, min=1, max=65535), host: str = '127.0.0.1'):
    """Run the FastAPI application with Uvicorn."""
    import uvicorn
    uvicorn.run('asgi:app', host=host, port=port)


@cli.command()
def backup(db: Path = typer.Option(None), dest: Path = typer.Option(None), keep: int = 14):
    """Create a compressed SQLite backup."""
    from scripts.backup import backup as create_backup
    from tasks import DEFAULT_DB
    create_backup(db or DEFAULT_DB, dest or Path('data/backups'), keep)


if __name__ == '__main__':
    cli()
