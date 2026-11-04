"""Guards that keep integration tests away from the live trading app (2026-11-04).

Integration tests run inside one process on the trading PC, next to the live
servers and their files. tests/conftest.py already points the database and the
scanner archives at a scratch folder; these two guards catch everything else,
so a route that reaches further than its test expected fails loudly instead of
touching the trader's real data.

network_guard: only loopback connections to non-live ports. Anything else -
Schwab, Alpaca, AI, news sites, ntfy, and the live :3001/:3002/:3010/:5173
servers on this PC - is refused. `redirect` maps a live port to a test server
(e.g. the scanner worker's :3010 to a running_worker() port).

write_guard: no file under the live repo's artifacts/ or database/, and not
the live .env, may be created, changed, renamed or deleted.
"""
from __future__ import annotations

import builtins
import io
import ipaddress
import os
import socket
from contextlib import contextmanager
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LIVE_PORTS = frozenset({443, 3001, 3002, 3010, 4173, 5173})
PROTECTED = (REPO / "artifacts", REPO / "database", REPO / "data", REPO / ".env")


def _loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(str(host).split("%", 1)[0]).is_loopback
    except ValueError:
        return str(host).lower() == "localhost"


@contextmanager
def network_guard(redirect: dict[int, int] | None = None):
    redirect = dict(redirect or {})
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def checked(sock, address):
        if sock.family not in (socket.AF_INET, socket.AF_INET6) or not isinstance(address, tuple):
            return address
        host, port = address[0], int(address[1])
        if not _loopback(host):
            raise ConnectionRefusedError(f"integration tests may not reach {host}:{port} - fake the call that leaves the PC")
        if port in redirect:
            return (host, redirect[port], *address[2:])
        if port in LIVE_PORTS:
            raise ConnectionRefusedError(f"integration tests may not reach the live app on port {port} - use a test server")
        return address

    socket.socket.connect = lambda sock, address: real_connect(sock, checked(sock, address))
    socket.socket.connect_ex = lambda sock, address: real_connect_ex(sock, checked(sock, address))
    try:
        yield
    finally:
        socket.socket.connect = real_connect
        socket.socket.connect_ex = real_connect_ex


def _protected(path) -> bool:
    try:
        target = Path(os.fspath(path)).resolve()
    except (TypeError, ValueError, OSError):
        return False
    return any(target == root or root in target.parents for root in PROTECTED)


def _refuse(path):
    raise PermissionError(f"integration tests may not change live files: {path}")


@contextmanager
def write_guard():
    real_open = builtins.open
    real_io_open = io.open
    real = {name: getattr(os, name) for name in ("replace", "rename", "remove", "unlink")}

    def guarded_open(file, mode="r", *args, **kwargs):
        if isinstance(file, (str, bytes, os.PathLike)) and any(flag in mode for flag in "wax+") and _protected(file):
            _refuse(file)
        return real_open(file, mode, *args, **kwargs)

    def guarded_move(name):
        def move(src, dst, *args, **kwargs):
            if _protected(src) or _protected(dst):
                _refuse(dst)
            return real[name](src, dst, *args, **kwargs)
        return move

    def guarded_delete(name):
        def delete(path, *args, **kwargs):
            if _protected(path):
                _refuse(path)
            return real[name](path, *args, **kwargs)
        return delete

    builtins.open = io.open = guarded_open
    os.replace, os.rename = guarded_move("replace"), guarded_move("rename")
    os.remove, os.unlink = guarded_delete("remove"), guarded_delete("unlink")
    try:
        yield
    finally:
        builtins.open, io.open = real_open, real_io_open
        for name, function in real.items():
            setattr(os, name, function)
