# IP whitelist

Edit `ip-whitelist.json` here. The same policy protects all WebHost pages, APIs,
static files, and hosted game WebSocket handshakes. No Caddy or firewall changes
are required.

```json
{
  "allowed_ips": ["203.0.113.8", "2001:db8::8"],
  "lan_networks": ["192.168.1.0/24"]
}
```

The example public IPs above are documentation addresses: replace them with your
friends' actual public IPv4/IPv6 addresses. CIDR subnets are also accepted.
The installed default has no allowed public IPs and allows Nova's actual LAN,
`192.168.1.0/24`. VPN addresses are not automatically considered LAN; add their
addresses or subnet explicitly if wanted. A LAN client reaching Nova via its
public address may appear as the router's public IP and need an explicit entry.

Loopback is always allowed for local health checks. Requests forwarded by the
local proxy use its recorded client address. Headers from direct remote clients
are never trusted. Keep the proxy local; a different proxy needs an explicit
trusted-proxy configuration change.

Compose mounts this directory read-only into the container. Edits, including
atomic file replacement by editors, apply to new HTTP requests and game
connections without a restart or image rebuild. Existing game sessions stay
connected until disconnected. Invalid or missing JSON denies all non-loopback
clients and logs an error, so typos do not accidentally make the service public.

Denied HTTP requests receive an empty `403` before Flask handles any routes or
creates sessions. Denied game connections are closed before the WebSocket
handshake and room data. This is application access control, not a firewall:
TCP/TLS connections can still be established. The game policy checks the socket
peer directly, not forwarded HTTP headers.

Run the focused tests with:

```sh
python test/webhost/test_ip_access.py
```
