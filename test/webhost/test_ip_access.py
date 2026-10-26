import asyncio
import importlib.util
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ip_access import IPWhitelist, IPWhitelistMiddleware, websocket_protocol


class TestIPWhitelist(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "whitelist.json"
        self.write(["203.0.113.8", "2001:db8::8"])
        self.policy = IPWhitelist(self.path)

    def write(self, allowed, lan=None):
        self.path.write_text(json.dumps({
            "allowed_ips": allowed,
            "lan_networks": ["192.168.1.0/24"] if lan is None else lan,
        }))

    def test_explicit_ips_lan_and_loopback(self):
        for address in ("203.0.113.8", "2001:db8::8", "192.168.1.42", "127.0.0.1", "::1",
                        "::ffff:192.168.1.42"):
            self.assertTrue(self.policy.allows(address), address)
        for address in ("203.0.113.9", "2001:db8::9", "192.168.2.42", "10.2.3.4", "100.88.1.5", "bad"):
            self.assertFalse(self.policy.allows(address), address)

    def test_direct_clients_cannot_spoof_forwarded_headers(self):
        self.assertFalse(self.policy.allows_http({
            "REMOTE_ADDR": "203.0.113.9", "HTTP_X_FORWARDED_FOR": "192.168.1.42"
        }))

    def test_local_proxy_uses_rightmost_forwarded_client(self):
        self.assertFalse(self.policy.allows_http({
            "REMOTE_ADDR": "127.0.0.1", "HTTP_X_FORWARDED_FOR": "192.168.1.42, 203.0.113.9"
        }))
        self.assertTrue(self.policy.allows_http({
            "REMOTE_ADDR": "127.0.0.1", "HTTP_X_FORWARDED_FOR": "203.0.113.8"
        }))
        self.assertFalse(self.policy.allows_http({"REMOTE_ADDR": "127.0.0.1", "HTTP_X_FORWARDED_FOR": ""}))

    def test_atomic_edits_reload_and_invalid_files_fail_closed(self):
        self.assertTrue(self.policy.allows("203.0.113.8"))
        replacement = self.path.with_suffix(".tmp")
        replacement.write_text(json.dumps({"allowed_ips": ["203.0.113.9"], "lan_networks": []}))
        replacement.replace(self.path)
        self.assertFalse(self.policy.allows("203.0.113.8"))
        self.assertTrue(self.policy.allows("203.0.113.9"))
        self.path.write_text("broken JSON")
        with self.assertLogs(level="ERROR"):
            self.assertFalse(self.policy.allows("203.0.113.9"))
        self.assertTrue(self.policy.allows("127.0.0.1"))
        self.path.unlink()
        with self.assertLogs(level="ERROR"):
            self.assertFalse(self.policy.allows("203.0.113.9"))

    def test_invalid_networks_and_unknown_fields_fail_closed(self):
        invalid = (
            {"allowed_ips": ["0.0.0.0/0"], "lan_networks": []},
            {"allowed_ips": [], "lan_networks": ["203.0.113.0/24"]},
            {"allowed_ips": ["not an IP"], "lan_networks": []},
            {"allowed_ips": [], "lan_networks": [], "enable": False},
        )
        for document in invalid:
            self.path.write_text(json.dumps(document))
            with self.assertLogs(level="ERROR"):
                self.assertFalse(self.policy.allows("203.0.113.8"))

    def test_denied_wsgi_requests_never_reach_application(self):
        called = []
        middleware = IPWhitelistMiddleware(lambda *args: called.append(True), self.policy)
        response = []
        body = middleware({"REMOTE_ADDR": "203.0.113.9"}, lambda *args: response.append(args))
        self.assertEqual(body, [])
        self.assertEqual(response[0][0], "403 Forbidden")
        self.assertEqual(called, [])


@unittest.skipUnless(importlib.util.find_spec("waitress"), "waitress not installed")
class TestWebIPWhitelist(unittest.TestCase):
    def test_waitress_preserves_real_forwarded_client_for_policy(self):
        from waitress import create_server

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "whitelist.json"
            path.write_text(json.dumps({"allowed_ips": ["203.0.113.8"], "lan_networks": []}))

            def application(environ, start_response):
                start_response("200 OK", [("Content-Length", "2")])
                return [b"OK"]

            server = create_server(
                IPWhitelistMiddleware(application, IPWhitelist(path)),
                host="127.0.0.1", port=0, threads=1, map={},
                trusted_proxy="127.0.0.1", trusted_proxy_count=1,
                trusted_proxy_headers={"x-forwarded-for"},
            )
            thread = threading.Thread(target=server.run, daemon=True)
            thread.start()
            try:
                for forwarded, expected in (
                    (None, 200), ("203.0.113.8", 200), ("203.0.113.9", 403),
                    ("127.0.0.1, 203.0.113.9", 403),
                ):
                    connection = http.client.HTTPConnection("127.0.0.1", server.effective_port, timeout=3)
                    headers = {"X-Forwarded-For": forwarded} if forwarded is not None else {}
                    connection.request("GET", "/", headers=headers)
                    response = connection.getresponse()
                    self.assertEqual(response.status, expected)
                    self.assertEqual(response.read(), b"OK" if expected == 200 else b"")
                    connection.close()
            finally:
                server.task_dispatcher.shutdown()
                server.close()
                thread.join(timeout=2)


@unittest.skipUnless(importlib.util.find_spec("websockets"), "websockets not installed")
class TestGameIPWhitelist(unittest.IsolatedAsyncioTestCase):
    async def test_denied_game_handshake_sends_no_bytes(self):
        from websockets.legacy.server import serve

        class Deny:
            def allows(self, address):
                return False

        reached = []

        async def handler(websocket, path):
            reached.append(True)

        async with serve(handler, "127.0.0.1", 0, create_protocol=websocket_protocol(Deny())) as server:
            port = server.sockets[0].getsockname()[1]
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write((
                "GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\n"
                "Connection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
                "Sec-WebSocket-Version: 13\r\n\r\n"
            ).encode())
            await writer.drain()
            self.assertEqual(await asyncio.wait_for(reader.read(), 3), b"")
            writer.close()
            await writer.wait_closed()
        self.assertEqual(reached, [])

    async def test_allowed_game_handshake_reaches_server(self):
        from websockets.legacy.client import connect
        from websockets.legacy.server import serve

        class Allow:
            def allows(self, address):
                return True

        async def handler(websocket, path):
            await websocket.send("room information")

        async with serve(handler, "127.0.0.1", 0, create_protocol=websocket_protocol(Allow())) as server:
            port = server.sockets[0].getsockname()[1]
            async with connect(f"ws://127.0.0.1:{port}") as client:
                self.assertEqual(await client.recv(), "room information")


if __name__ == "__main__":
    unittest.main()
