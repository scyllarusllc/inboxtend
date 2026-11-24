"""Inbox Tend command line commands."""
from pathlib import Path

import typer

cli = typer.Typer(help='Run and maintain Inbox Tend.')


@cli.command()
def serve(port: int = typer.Option(8765, min=1, max=65535), host: str = '127.0.0.1',
          reload: bool = typer.Option(False, help='Reload Python code during development.')):
    """Run the FastAPI application with Uvicorn."""
    import uvicorn
    root = Path(__file__).resolve().parent
    options = {'reload_dirs': [str(root)],
               'reload_excludes': [str(root / name) for name in ('.venv', 'node_modules', '.tasks', 'data', 'frontend/.next')]}
    uvicorn.run('asgi:app', host=host, port=port, reload=reload, **(options if reload else {}))


@cli.command()
def backup(db: Path = typer.Option(None), dest: Path = typer.Option(None), keep: int = 14):
    """Create a compressed SQLite backup."""
    from scripts.backup import backup as create_backup
    from tasks import DEFAULT_DB
    create_backup(db or DEFAULT_DB, dest or Path('data/backups'), keep)


if __name__ == '__main__':
    cli()
