"""Production entrypoint for stockagent.

Runs the Flask dashboard behind Waitress and starts one recurring trading worker in
the same process. Keep WEB_THREADS modest because this app has shared in-memory
dashboard state and one trading loop.
"""
from __future__ import annotations

import config
from web import app, start_background_trading


def main() -> None:
    config.validate()
    start_background_trading()

    from waitress import serve

    print(
        f"stockagent serving on {config.WEB_HOST}:{config.WEB_PORT} "
        f"| threads={config.WEB_THREADS} | dry_run={config.DRY_RUN}",
        flush=True,
    )
    serve(
        app,
        host=config.WEB_HOST,
        port=config.WEB_PORT,
        threads=config.WEB_THREADS,
        url_scheme="http",
    )


if __name__ == "__main__":
    main()
