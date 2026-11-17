"""Receive an explicitly selected glasses photo over an Android USB tunnel."""

import argparse
import hmac
import secrets
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


MAX_BYTES = 12 * 1024 * 1024
PAGE = b"""<!doctype html>
<html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ray-Ban camera bridge</title>
<h1>Send a glasses photo</h1>
<form>
<label>Bridge token <input id="token" required autocomplete="off"></label>
<p><label>Glasses photo <input id="photo" type="file" accept="image/jpeg" required></label></p>
<button>Send photo to PC</button>
<p id="status" role="status"></p>
</form>
<script>
document.querySelector('form').onsubmit = async (event) => {
  event.preventDefault();
  const button = document.querySelector('button');
  const status = document.querySelector('#status');
  button.disabled = true;
  status.textContent = 'Sending...';
  try {
    const response = await fetch('/photo', {
      method: 'POST',
      headers: {'Authorization': 'Bearer ' + document.querySelector('#token').value.trim(),
                'Content-Type': 'image/jpeg'},
      body: document.querySelector('#photo').files[0]
    });
    status.textContent = await response.text();
  } catch (error) {
    status.textContent = error.message;
  } finally {
    button.disabled = false;
  }
};
</script></html>"""


def handler_for(token, output):
    class Bridge(BaseHTTPRequestHandler):
        def reply(self, status, body, content_type="text/plain; charset=utf-8"):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path != "/":
                return self.reply(404, b"Unknown path")
            self.reply(200, PAGE, "text/html; charset=utf-8")

        def do_POST(self):
            if self.path != "/photo":
                return self.reply(404, b"Unknown path")
            authorization = self.headers.get("Authorization", "").encode("utf-8")
            if not hmac.compare_digest(authorization, f"Bearer {token}".encode("ascii")):
                return self.reply(401, b"Enter the token printed on the PC")
            if self.headers.get_content_type() != "image/jpeg":
                return self.reply(415, b"Choose a JPEG photo")
            try:
                size = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return self.reply(400, b"Invalid content length")
            if not 4 <= size <= MAX_BYTES:
                return self.reply(413, b"Choose a JPEG photo under 12 MiB")
            self.connection.settimeout(15)
            try:
                photo = self.rfile.read(size)
            except TimeoutError:
                return self.reply(408, b"Upload timed out")
            if len(photo) != size or not photo.startswith(b"\xff\xd8\xff") or not photo.endswith(b"\xff\xd9"):
                return self.reply(400, b"Invalid JPEG")
            temporary = output.with_suffix(".upload")
            try:
                temporary.write_bytes(photo)
                temporary.replace(output)
            except OSError:
                return self.reply(500, b"Could not save photo on PC")
            self.reply(201, b"Photo received on PC")

    return Bridge


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--output", type=Path, default=Path("latest-glasses.jpg"))
    args = parser.parse_args()
    token = secrets.token_urlsafe(18)
    with HTTPServer(("127.0.0.1", args.port), handler_for(token, args.output.resolve())) as server:
        print(f"Android tunnel: adb reverse tcp:{server.server_port} tcp:{server.server_port}", flush=True)
        print(f"Open on phone: http://127.0.0.1:{server.server_port}/", flush=True)
        print(f"Token: {token}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
