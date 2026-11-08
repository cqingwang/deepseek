#!/usr/bin/env python3
"""Restricted HTTP CONNECT proxy for the official spark-a source fetch.

It listens on loopback only and accepts CONNECT to ports 80/443. The source
builder reaches it through an SSH reverse tunnel, so no proxy port is exposed
on the LAN.
"""

from __future__ import annotations

import select
import socket
import socketserver
import sys


MAX_HEADER = 16 * 1024


class ProxyHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        request = self.request
        request.settimeout(15)
        try:
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = request.recv(4096)
                if not chunk:
                    return
                data += chunk
                if len(data) > MAX_HEADER:
                    request.sendall(b"HTTP/1.1 431 Request Header Fields Too Large\r\n\r\n")
                    return
        except OSError:
            return

        first_line, _, _headers = data.partition(b"\r\n\r\n")
        parts = first_line.split(b"\r\n", 1)[0].split()
        if len(parts) != 3 or parts[0] != b"CONNECT":
            request.sendall(b"HTTP/1.1 405 Method Not Allowed\r\n\r\n")
            return
        host_port = parts[1].decode("ascii", errors="strict")
        host, separator, raw_port = host_port.rpartition(":")
        if not separator or not host:
            request.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
            return
        try:
            port = int(raw_port)
        except ValueError:
            request.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
            return
        if port not in (80, 443):
            request.sendall(b"HTTP/1.1 403 Forbidden\r\n\r\n")
            return

        try:
            upstream = socket.create_connection((host, port), timeout=20)
        except OSError:
            request.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            return

        try:
            request.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            request.setblocking(False)
            upstream.setblocking(False)
            sockets = [request, upstream]
            while True:
                readable, _, exceptional = select.select(sockets, [], sockets, 60)
                if exceptional or not readable:
                    return
                for source in readable:
                    target = upstream if source is request else request
                    chunk = source.recv(65536)
                    if not chunk:
                        return
                    target.sendall(chunk)
        except OSError:
            return
        finally:
            upstream.close()


class ThreadingServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} PORT")
    port = int(sys.argv[1])
    with ThreadingServer(("127.0.0.1", port), ProxyHandler) as server:
        print(f"connect proxy listening on 127.0.0.1:{port}", flush=True)
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
