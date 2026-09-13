#!/usr/bin/env python3
"""Small LAN-only companion server for the LanLink Android app.

The server deliberately uses only Python's standard library so it can be
started on a laptop without installing a package manager or dependencies.
It exposes a tiny JSON API and a browser UI for testing the connection.
"""

from __future__ import annotations

import argparse
import hmac
import ipaddress
import json
import os
import secrets
import socket
import threading
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit


DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8765
MAX_BODY_BYTES = 64 * 1024
MAX_TEXT_LENGTH = 4_000
MAX_MESSAGES = 500


class BadRequest(ValueError):
    """Raised when a request body or query parameter is invalid."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def new_pair_code() -> str:
    """Return a six-digit code, avoiding the easy-to-mistype all-zero value."""

    while True:
        code = f"{secrets.randbelow(1_000_000):06d}"
        if code != "000000":
            return code


def get_lan_addresses() -> list[str]:
    """Best-effort list of IPv4 addresses that another LAN device can use."""

    addresses: set[str] = set()
    try:
        for result in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = result[4][0]
            if not address.startswith("127."):
                addresses.add(address)
    except OSError:
        pass

    # The UDP connect does not send a packet; it asks the OS which interface
    # would be used to reach an outside address and is useful on Windows.
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1))
        address = probe.getsockname()[0]
        if address and not address.startswith("127."):
            addresses.add(address)
    except OSError:
        pass
    finally:
        probe.close()

    return sorted(addresses)


class LanLinkState:
    """Thread-safe in-memory state for one laptop server session."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.name = socket.gethostname()
        self.pair_code = new_pair_code()
        self.session_token = secrets.token_urlsafe(32)
        self.messages: list[dict[str, Any]] = []
        self.next_message_id = 1
        self.started_at = utc_now()

    def authenticate(self, token: str | None) -> bool:
        if not token:
            return False
        return hmac.compare_digest(token, self.session_token)

    def add_message(self, text: str, sender: str) -> dict[str, Any]:
        with self.lock:
            message = {
                "id": self.next_message_id,
                "sender": sender,
                "text": text,
                "timestamp": utc_now(),
            }
            self.next_message_id += 1
            self.messages.append(message)
            if len(self.messages) > MAX_MESSAGES:
                del self.messages[: len(self.messages) - MAX_MESSAGES]
            return message

    def messages_since(self, message_id: int) -> list[dict[str, Any]]:
        with self.lock:
            return [
                message for message in self.messages if int(message["id"]) > message_id
            ]

    def clear_messages(self) -> None:
        with self.lock:
            self.messages.clear()


class LanLinkHTTPServer(ThreadingHTTPServer):
    # Refuse a second instance on the same port instead of letting Windows
    # distribute requests between two accidentally opened servers.
    allow_reuse_address = False
    daemon_threads = True

    def __init__(self, server_address: tuple[str, int], state: LanLinkState):
        super().__init__(server_address, LanLinkHandler)
        self.state = state
        self.web_root = Path(__file__).resolve().parent / "web"


class LanLinkHandler(BaseHTTPRequestHandler):
    """HTTP JSON API used by both the Android client and laptop UI."""

    protocol_version = "HTTP/1.1"
    server_version = "LanLink/0.1"

    @property
    def lan_state(self) -> LanLinkState:
        return self.server.state  # type: ignore[attr-defined]

    def _is_local_request(self) -> bool:
        try:
            return ipaddress.ip_address(self.client_address[0]).is_loopback
        except ValueError:
            return False

    def _token_from(self, body: dict[str, Any] | None = None) -> str | None:
        authorization = self.headers.get("Authorization", "")
        if authorization.lower().startswith("bearer "):
            return authorization[7:].strip()
        if body is not None and isinstance(body.get("token"), str):
            return body["token"]
        return None

    def _authorized(
        self, body: dict[str, Any] | None = None, *, local_ok: bool = True
    ) -> bool:
        if local_ok and self._is_local_request():
            return True
        return self.lan_state.authenticate(self._token_from(body))

    def _read_json(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length")
        try:
            content_length = int(raw_length or "0")
        except ValueError as exc:
            raise BadRequest("Content-Length khong hop le") from exc
        if content_length < 0 or content_length > MAX_BODY_BYTES:
            raise BadRequest("Kich thuoc request qua lon")
        raw_body = self.rfile.read(content_length)
        if len(raw_body) != content_length:
            raise BadRequest("Khong doc duoc request body")
        if not raw_body:
            return {}
        try:
            body = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BadRequest("JSON khong hop le") from exc
        if not isinstance(body, dict):
            raise BadRequest("JSON phai la object")
        return body

    def _send_json(
        self,
        status: int | HTTPStatus,
        payload: dict[str, Any],
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        encoded = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(encoded)
        self.close_connection = True

    def _send_error_json(self, status: int | HTTPStatus, message: str) -> None:
        self._send_json(status, {"ok": False, "error": message})

    def _send_web_ui(self) -> None:
        page = self.server.web_root / "index.html"  # type: ignore[attr-defined]
        try:
            encoded = page.read_bytes()
        except OSError:
            self._send_error_json(HTTPStatus.NOT_FOUND, "Khong tim thay giao dien")
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)
        self.close_connection = True

    def do_OPTIONS(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Content-Length", "0")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.close_connection = True

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        parsed = urlsplit(self.path)
        path = parsed.path

        if path == "/":
            self._send_web_ui()
            return

        if path == "/api/health":
            self._send_json(HTTPStatus.OK, {"ok": True, "service": "lanlink"})
            return

        if path == "/api/info":
            payload: dict[str, Any] = {
                "ok": True,
                "protocol": 1,
                "name": self.lan_state.name,
                "port": self.server.server_port,
                "addresses": get_lan_addresses(),
                "started_at": self.lan_state.started_at,
                "requires_pair_code": True,
            }
            # Keep the pairing code out of responses sent to other LAN hosts.
            if self._is_local_request():
                payload["pair_code"] = self.lan_state.pair_code
            self._send_json(HTTPStatus.OK, payload)
            return

        if path == "/api/messages":
            query = parse_qs(parsed.query)
            token = query.get("token", [None])[0]
            since_raw = query.get("since", ["0"])[0]
            try:
                since = max(0, int(since_raw))
            except (TypeError, ValueError):
                self._send_error_json(HTTPStatus.BAD_REQUEST, "since phai la so")
                return
            if not self._authorized({"token": token}):
                self._send_error_json(HTTPStatus.UNAUTHORIZED, "Token khong hop le")
                return
            messages = self.lan_state.messages_since(since)
            last_id = messages[-1]["id"] if messages else since
            self._send_json(
                HTTPStatus.OK,
                {"ok": True, "messages": messages, "last_id": last_id},
            )
            return

        self._send_error_json(HTTPStatus.NOT_FOUND, "Endpoint khong ton tai")

    def do_POST(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        parsed = urlsplit(self.path)
        path = parsed.path
        try:
            body = self._read_json()
        except BadRequest as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            return

        if path == "/api/pair":
            code = str(body.get("code", "")).strip()
            if not hmac.compare_digest(code, self.lan_state.pair_code):
                self._send_error_json(HTTPStatus.UNAUTHORIZED, "Ma ghep doi khong dung")
                return
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "token": self.lan_state.session_token,
                    "name": self.lan_state.name,
                    "port": self.server.server_port,
                },
            )
            return

        if path == "/api/messages":
            if not self._authorized(body):
                self._send_error_json(HTTPStatus.UNAUTHORIZED, "Token khong hop le")
                return
            text = body.get("text")
            if not isinstance(text, str):
                self._send_error_json(HTTPStatus.BAD_REQUEST, "text phai la chuoi")
                return
            text = text.strip()
            if not text:
                self._send_error_json(HTTPStatus.BAD_REQUEST, "Tin nhan dang trong")
                return
            if len(text) > MAX_TEXT_LENGTH:
                self._send_error_json(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                    f"Tin nhan toi da {MAX_TEXT_LENGTH} ky tu",
                )
                return
            sender = "laptop" if self._is_local_request() else "phone"
            message = self.lan_state.add_message(text, sender)
            self._send_json(HTTPStatus.CREATED, {"ok": True, "message": message})
            return

        if path == "/api/messages/clear":
            # Clearing the shared history is a laptop UI action. The phone's
            # local "clear screen" button intentionally does not call this.
            if not self._is_local_request():
                self._send_error_json(HTTPStatus.UNAUTHORIZED, "Chi laptop moi duoc xoa")
                return
            self.lan_state.clear_messages()
            self._send_json(HTTPStatus.OK, {"ok": True})
            return

        self._send_error_json(HTTPStatus.NOT_FOUND, "Endpoint khong ton tai")

    def log_message(self, format: str, *args: Any) -> None:
        # Polling happens every second; keep routine GETs out of the console.
        if self.command == "GET" and urlsplit(self.path).path == "/api/messages":
            return
        super().log_message(format, *args)


def create_server(
    *, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, state: LanLinkState | None = None
) -> LanLinkHTTPServer:
    return LanLinkHTTPServer((host, port), state or LanLinkState())


def main() -> None:
    parser = argparse.ArgumentParser(description="LanLink LAN companion server")
    parser.add_argument(
        "--host", default=os.getenv("LANLINK_HOST", DEFAULT_HOST), help="Bind address"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("LANLINK_PORT", DEFAULT_PORT)),
        help="TCP port (default: 8765)",
    )
    args = parser.parse_args()

    try:
        server = create_server(host=args.host, port=args.port)
    except OSError as exc:
        print(f"Khong the mo cong {args.port}: {exc}")
        raise SystemExit(1) from exc
    addresses = get_lan_addresses()
    print("LanLink laptop server")
    print(f"Pair code: {server.state.pair_code}")
    print(f"Laptop UI: http://127.0.0.1:{server.server_port}/")
    if addresses:
        print("Phone address(es):")
        for address in addresses:
            print(f"  http://{address}:{server.server_port}")
    else:
        print("Khong tim thay dia chi LAN tu dong; xem ipconfig de lay IP Wi-Fi.")
    print("Chi dung tren mang tin cay. Nhan Ctrl+C de dung.")

    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\nDang dung LanLink...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
