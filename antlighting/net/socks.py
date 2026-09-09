"""A minimal SOCKS5 client.

Only what AntLighting needs: connect to a local SOCKS5 listener with domain or
IP addressing, send a plain HTTP request and read the response.  Implemented on
the standard library so the packaged executable has no extra dependencies.
"""

from __future__ import annotations

import socket
import struct
from typing import BinaryIO

SOCKS5_VERSION = 0x05
AUTH_NONE = 0x00
CMD_CONNECT = 0x01
ATYP_IPV4 = 0x01
ATYP_DOMAIN = 0x03
ATYP_IPV6 = 0x04


class SocksError(Exception):
    """Raised for any SOCKS handshake or transport failure."""


def _recv_exact(sock: socket.socket, count: int) -> bytes:
    chunks: list[bytes] = []
    remaining = count
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise SocksError("connection closed during handshake")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _address_bytes(host: str) -> tuple[int, bytes]:
    try:
        socket.inet_aton(host)
        return ATYP_IPV4, socket.inet_aton(host)
    except OSError:
        pass
    if ":" in host:
        try:
            packed = socket.inet_pton(socket.AF_INET6, host)
            return ATYP_IPV6, packed
        except OSError:
            pass
    encoded = host.encode("idna") if any(ord(c) > 127 for c in host) else host.encode("ascii")
    if len(encoded) > 255:
        raise SocksError("hostname too long")
    return ATYP_DOMAIN, bytes([len(encoded)]) + encoded


def connect(
    proxy_host: str,
    proxy_port: int,
    dest_host: str,
    dest_port: int,
    timeout: float = 8.0,
) -> socket.socket:
    """Open a TCP connection to ``dest_host:dest_port`` through a SOCKS5 proxy."""
    sock = socket.create_connection((proxy_host, proxy_port), timeout=timeout)
    try:
        sock.settimeout(timeout)
        sock.sendall(bytes([SOCKS5_VERSION, 1, AUTH_NONE]))
        reply = _recv_exact(sock, 2)
        if reply[0] != SOCKS5_VERSION:
            raise SocksError(f"unexpected SOCKS version {reply[0]}")
        if reply[1] != AUTH_NONE:
            raise SocksError(f"server requires auth method {reply[1]}")

        atyp, addr = _address_bytes(dest_host)
        sock.sendall(
            bytes([SOCKS5_VERSION, CMD_CONNECT, 0x00, atyp]) + addr + struct.pack("!H", dest_port)
        )
        header = _recv_exact(sock, 4)
        if header[0] != SOCKS5_VERSION:
            raise SocksError(f"unexpected reply version {header[0]}")
        if header[1] != 0x00:
            raise SocksError(_socks_error_name(header[1]))

        bound_atyp = header[3]
        if bound_atyp == ATYP_IPV4:
            _recv_exact(sock, 4 + 2)
        elif bound_atyp == ATYP_IPV6:
            _recv_exact(sock, 16 + 2)
        elif bound_atyp == ATYP_DOMAIN:
            length = _recv_exact(sock, 1)[0]
            _recv_exact(sock, length + 2)
        else:
            raise SocksError(f"unknown address type {bound_atyp}")
        return sock
    except Exception:
        sock.close()
        raise


_SOCKS_ERRORS = {
    0x01: "general failure",
    0x02: "connection not allowed",
    0x03: "network unreachable",
    0x04: "host unreachable",
    0x05: "connection refused",
    0x06: "TTL expired",
    0x07: "command not supported",
    0x08: "address type not supported",
}


def _socks_error_name(code: int) -> str:
    return _SOCKS_ERRORS.get(code, f"SOCKS error 0x{code:02x}")


def http_get_via_socks(
    proxy_host: str,
    proxy_port: int,
    url: str,
    timeout: float = 8.0,
    max_bytes: int = 4096,
) -> tuple[int, bytes]:
    """Fetch a small HTTP(S) URL through the SOCKS5 proxy, return (status, body).

    Only plain ``http://`` URLs are supported — that is enough for the
    ``generate_204`` reachability probes AntLighting uses, and it avoids
    depending on a TLS stack for the test path.
    """
    if not url.startswith("http://"):
        raise SocksError("only http:// URLs are supported for probing")
    rest = url[len("http://") :]
    hostport, _, path = rest.partition("/")
    path = "/" + path
    if ":" in hostport and not hostport.startswith("["):
        host, _, port_s = hostport.rpartition(":")
        port = int(port_s) if port_s.isdigit() else 80
    else:
        host, port = hostport, 80

    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        "User-Agent: AntLighting/1.0\r\n"
        "Accept: */*\r\n"
        "Connection: close\r\n\r\n"
    ).encode("ascii", errors="replace")

    sock = connect(proxy_host, proxy_port, host, port, timeout=timeout)
    try:
        sock.sendall(request)
        stream: BinaryIO = sock.makefile("rb")
        status_line = stream.readline(1024).decode("latin-1").strip()
        if not status_line:
            raise SocksError("empty response")
        parts = status_line.split(" ", 2)
        if len(parts) < 2 or not parts[1].isdigit():
            raise SocksError(f"malformed status line: {status_line[:60]}")
        status = int(parts[1])
        headers: dict[str, str] = {}
        while True:
            line = stream.readline(2048).decode("latin-1").strip()
            if not line:
                break
            key, _, value = line.partition(":")
            headers[key.strip().lower()] = value.strip()
        body = b""
        length = headers.get("content-length")
        if length and length.isdigit():
            body = stream.read(min(int(length), max_bytes))
        elif headers.get("transfer-encoding", "").lower() == "chunked":
            body = _read_chunked(stream, max_bytes)
        else:
            body = stream.read(max_bytes)
        return status, body
    finally:
        try:
            sock.close()
        except OSError:
            pass


def _read_chunked(stream: BinaryIO, max_bytes: int) -> bytes:
    out = bytearray()
    while len(out) < max_bytes:
        line = stream.readline(64).decode("latin-1").strip()
        if not line:
            break
        try:
            size = int(line.split(";", 1)[0], 16)
        except ValueError:
            break
        if size == 0:
            break
        out.extend(stream.read(size))
        stream.read(2)  # trailing CRLF
    return bytes(out)
