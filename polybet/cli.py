from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import typer
from rich.console import Console

from .analysis import analyze

app = typer.Typer(help="Polymarket Sports Auto-Analyst")
console = Console()


@app.command("analyze")
def analyze_cmd(text: str) -> None:
    """Analyze a Polymarket URL or match text."""
    console.print(analyze(text))


@app.command()
def chat() -> None:
    """Chat-only loop."""
    console.print("Enter market URL or match text. Type 'exit' to quit.")
    while True:
        user_input = typer.prompt("polybet>")
        if user_input.strip().lower() in {"exit", "quit"}:
            break
        try:
            console.print(analyze(user_input))
        except Exception as exc:
            console.print(f"[red]Error:[/red] {exc}")


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/analyze":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        payload = json.loads(body or b"{}")
        text = payload.get("text", "")
        markdown = analyze(text)
        encoded = json.dumps({"markdown": markdown}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


@app.command()
def serve(host: str = "0.0.0.0", port: int = 8787) -> None:
    """Optional HTTP mode: POST /analyze {text}."""
    server = ThreadingHTTPServer((host, port), _Handler)
    console.print(f"Serving on http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    app()
