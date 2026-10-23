"""xdog.site Flask application factory.

Mirrors the depins app-factory pattern (``create_app()`` + ``init_bp``) but with
no i18n, auth, or DB — content is authored in :mod:`xdog.site.content`.
"""

from __future__ import annotations

import os
from pathlib import Path

from flask import Flask, render_template, url_for
from xdog.site.blueprints import init_bp
from xdog.site.content.packages import Package


def create_app() -> Flask:
    """Build and configure the xdog site Flask app.

    Set ``XDOG_SITE_DEV=1`` to auto-reload templates and stop caching static
    files, so template/CSS edits take effect on the next request without a
    restart — without enabling full Flask debug (which is unsafe behind nginx).
    """
    app = Flask(__name__)
    dev = bool(os.getenv("XDOG_SITE_DEV") or os.getenv("XDOG_SITE_DEBUG"))
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0 if dev else 3600
    app.config["TEMPLATES_AUTO_RELOAD"] = dev
    if not app.config.get("SECRET_KEY"):
        app.config["SECRET_KEY"] = os.getenv("XDOG_SITE_SECRET") or "dev-insecure-change-me"
    init_bp(app)

    static_dir = Path(app.static_folder) if app.static_folder else None

    @app.template_global()
    def static_v(filename: str) -> str:
        """Static URL with a cache-busting ``?v=<mtime>`` so browsers refetch
        a file the moment its contents change — no manual hard-refresh."""
        url = url_for("static", filename=filename)
        if static_dir is not None:
            fpath = static_dir / filename
            try:
                return f"{url}?v={int(fpath.stat().st_mtime)}"
            except OSError:
                pass
        return url

    @app.template_global()
    def site_packages() -> tuple[Package, ...]:
        """Navigation uses the same inert catalog as package routes and sitemap."""
        from xdog.site.content.packages import PACKAGES

        return PACKAGES

    @app.template_global()
    def havefun_packages() -> tuple[str, ...]:
        """Packages with a runnable HaveFun page, for the nav's HaveFun section.

        Read from the route's own allowlist so a nav child can never point at a
        name that would 404.
        """
        from xdog.site.blueprints.main import HAVEFUN_PACKAGES

        return HAVEFUN_PACKAGES

    @app.errorhandler(404)
    def not_found(_e: object) -> tuple[str, int]:
        return render_template("errors/404.html"), 404

    return app


def main() -> None:
    """Run the development server (``xdog-site``)."""
    host = os.getenv("XDOG_SITE_HOST", "127.0.0.1")
    port = int(os.getenv("XDOG_SITE_PORT", "8080"))
    create_app().run(host=host, port=port, debug=bool(os.getenv("XDOG_SITE_DEBUG")))


if __name__ == "__main__":
    main()
