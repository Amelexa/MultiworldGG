"""Shared, reloadable IP access policy for the WebHost and hosted game servers."""

import ipaddress
import json
import logging
import os
from pathlib import Path
import threading


def parse_address(value):
    address = ipaddress.ip_address(value)
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return address.ipv4_mapped
    return address


class IPWhitelist:
    def __init__(self, path=None):
        self.path = Path(path or os.environ.get(
            "MWGG_IP_WHITELIST", Path(__file__).resolve().parent / "access-control" / "ip-whitelist.json"
        ))
        self._stamp = None
        self._networks = ()
        self._lock = threading.Lock()

    def _reload(self):
        try:
            info = self.path.stat()
            stamp = (info.st_mtime_ns, info.st_size, info.st_ino)
        except OSError:
            stamp = ("missing",)
        with self._lock:
            if stamp == self._stamp:
                return self._networks
            networks = []
            try:
                if stamp == ("missing",):
                    raise ValueError("whitelist file is missing or unreadable")
                if info.st_size > 64 * 1024:
                    raise ValueError("whitelist file exceeds 64 KiB")
                data = json.loads(self.path.read_text(encoding="utf-8-sig"))
                if not isinstance(data, dict) or set(data) != {"allowed_ips", "lan_networks"}:
                    raise ValueError("expected only allowed_ips and lan_networks")
                private_ranges = tuple(ipaddress.ip_network(cidr) for cidr in (
                    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7"
                ))
                for field in ("allowed_ips", "lan_networks"):
                    entries = data[field]
                    if not isinstance(entries, list) or any(not isinstance(entry, str) for entry in entries):
                        raise ValueError(f"{field} must be a list of IPs or CIDR subnets")
                    for entry in entries:
                        network = ipaddress.ip_network(entry, strict=True)
                        if network.prefixlen == 0:
                            raise ValueError("allow-all networks are not permitted")
                        if field == "lan_networks" and not any(
                            network.version == private.version and network.subnet_of(private)
                            for private in private_ranges
                        ):
                            raise ValueError(f"LAN network is not RFC1918 or IPv6 ULA: {entry}")
                        networks.append(network)
            except (ValueError, OSError) as exc:
                logging.error("IP whitelist invalid; denying non-loopback access: %s", exc)
                networks = []
            self._networks = tuple(networks)
            self._stamp = stamp
            return self._networks

    def allows(self, value):
        try:
            address = parse_address(value)
        except (ValueError, TypeError):
            return False
        if address.is_loopback:
            return True
        return any(address.version == network.version and address in network for network in self._reload())

    def allows_http(self, environ):
        peer = environ.get("REMOTE_ADDR", "")
        try:
            # Only the local reverse proxy may supply a forwarded client address.
            # The rightmost address is the client recorded by that proxy.
            if parse_address(peer).is_loopback and "HTTP_X_FORWARDED_FOR" in environ:
                peer = environ["HTTP_X_FORWARDED_FOR"].split(",")[-1].strip()
        except (ValueError, TypeError, AttributeError):
            return False
        return self.allows(peer)


whitelist = IPWhitelist()


class IPWhitelistMiddleware:
    def __init__(self, application, policy=whitelist):
        self.application = application
        self.policy = policy

    def __call__(self, environ, start_response):
        if not self.policy.allows_http(environ):
            start_response("403 Forbidden", [("Content-Length", "0"), ("Cache-Control", "no-store")])
            return []
        return self.application(environ, start_response)


def websocket_protocol(policy=whitelist):
    from http import HTTPStatus
    from websockets.legacy.server import WebSocketServerProtocol

    class WhitelistedProtocol(WebSocketServerProtocol):
        async def process_request(self, path, request_headers):
            peer = self.remote_address
            if not peer or not policy.allows(peer[0]):
                # Abort before sending the WebSocket handshake or room information.
                self.transport.abort()
                return HTTPStatus.FORBIDDEN, [], b""
            return await super().process_request(path, request_headers)

    return WhitelistedProtocol
