"""Browser application document for the engine console."""

from pathlib import Path

from fastapi.responses import HTMLResponse

WEB_ROOT = Path(__file__).with_name("web")


def page() -> HTMLResponse:
    """Return the console shell. Styles and behavior live in static files."""
    return HTMLResponse((WEB_ROOT / "index.html").read_text())
