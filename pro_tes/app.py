"""Entry point to start service."""
from pathlib import Path

from connexion import App
from foca.foca import foca


def init_app() -> App:
    app = foca(Path(__file__).resolve().parent / "config.yaml")
    return app


def run_app(app: App) -> None:
    app.run(port=app.port)


if __name__ == '__main__':
    app = init_app()
    run_app(app)
