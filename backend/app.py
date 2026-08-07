"""Placeholder API process for Compose wiring.

Serves a bare health endpoint so `docker compose up` can prove the api
service is reachable before EXT-003 replaces this with the real Django
project.
"""

from http.server import BaseHTTPRequestHandler, HTTPServer


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health/":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 8000), HealthHandler).serve_forever()
