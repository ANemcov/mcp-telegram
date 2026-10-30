"""MTProto proxy secrets and the FakeTLS ("ee") transport for Telethon.

Telethon only implements plain and "dd" MTProxy secrets. "ee" secrets need the
obfuscated2 stream to be wrapped into TLS records after a fake TLS handshake whose
client and server randoms are HMAC-SHA256 signatures keyed by the proxy secret.
"""

import asyncio
import base64
import binascii
import hashlib
import hmac
import os
import re
import struct
import time
from typing import Type

from telethon.network import (  # type: ignore
    ConnectionTcpMTProxyRandomizedIntermediate,
    Connection,
)

_MAX_RECORD = 16384
_HELLO_SIZE = 517
_CHANGE_CIPHER_SPEC = b"\x14\x03\x03\x00\x01\x01"


def parse_secret(secret: str) -> tuple[bytes, str | None]:
    """Parse a hex or base64(url) proxy secret into (16-byte key, FakeTLS domain or None)."""
    secret = secret.strip()
    if re.fullmatch(r"(?:[0-9a-fA-F]{2})+", secret):
        raw = bytes.fromhex(secret)
    else:
        try:
            raw = base64.urlsafe_b64decode(
                secret.replace("+", "-").replace("/", "_") + "=" * (-len(secret) % 4)
            )
        except (binascii.Error, ValueError):
            raise ValueError("MTProxy secret is neither hex nor base64") from None

    if len(raw) > 17 and raw[0] == 0xEE:
        return raw[1:17], raw[17:].decode("ascii")
    if len(raw) == 17 and raw[0] == 0xDD:
        return raw[1:], None
    if len(raw) == 16:
        return raw, None
    raise ValueError("MTProxy secret must contain a 16-byte key")


def proxy_connection(secret: str) -> tuple[Type[Connection], str]:
    """Pick the Telethon connection class and the secret value to pass with it."""
    key, domain = parse_secret(secret)
    if domain is not None:
        return ConnectionTcpMTProxyFakeTLS, secret
    return ConnectionTcpMTProxyRandomizedIntermediate, key.hex()


def _grease() -> bytes:
    b = (os.urandom(1)[0] & 0xF0) | 0x0A
    return bytes((b, b))


def _ext(ext_type: int, data: bytes) -> bytes:
    return struct.pack(">HH", ext_type, len(data)) + data


def _client_hello(domain: str) -> bytes:
    """Chrome-like ClientHello with a zeroed random, padded to 517 bytes."""
    host = domain.encode("ascii")
    sni = struct.pack(">HBH", len(host) + 3, 0, len(host)) + host
    group = _grease()
    alpn = b"\x02h2\x08http/1.1"
    sig_algs = bytes.fromhex("04030804040105030805050108060601")
    key_share = group + b"\x00\x01\x00" + b"\x00\x1d\x00\x20" + os.urandom(32)
    extensions = b"".join(
        [
            _ext(int.from_bytes(_grease(), "big"), b""),
            _ext(0x0000, sni),
            _ext(0x0017, b""),
            _ext(0xFF01, b"\x00"),
            _ext(0x000A, b"\x00\x08" + group + b"\x00\x1d\x00\x17\x00\x18"),
            _ext(0x000B, b"\x01\x00"),
            _ext(0x0023, b""),
            _ext(0x0010, struct.pack(">H", len(alpn)) + alpn),
            _ext(0x0005, b"\x01\x00\x00\x00\x00"),
            _ext(0x000D, struct.pack(">H", len(sig_algs)) + sig_algs),
            _ext(0x0012, b""),
            _ext(0x0033, struct.pack(">H", len(key_share)) + key_share),
            _ext(0x002D, b"\x01\x01"),
            _ext(0x002B, b"\x06" + _grease() + b"\x03\x04\x03\x03"),
            _ext(0x001B, b"\x02\x00\x02"),
            _ext(int.from_bytes(_grease(), "big"), b"\x00"),
        ]
    )
    ciphers = _grease() + bytes.fromhex(
        "130113021303c02bc02fc02cc030cca9cca8c013c014009c009d002f0035"
    )
    body_head = (
        b"\x03\x03"
        + b"\x00" * 32
        + b"\x20"
        + os.urandom(32)
        + struct.pack(">H", len(ciphers))
        + ciphers
        + b"\x01\x00"
    )
    # record header (5) + handshake header (4) + body + extensions length (2) + padding ext (4)
    pad = _HELLO_SIZE - (5 + 4 + len(body_head) + 2 + len(extensions) + 4)
    if pad < 0:
        raise ValueError("FakeTLS domain is too long")
    extensions += _ext(0x0015, b"\x00" * pad)
    body = body_head + struct.pack(">H", len(extensions)) + extensions
    handshake = b"\x01" + len(body).to_bytes(3, "big") + body
    return b"\x16\x03\x01" + struct.pack(">H", len(handshake)) + handshake


class _TLSReader:
    """Unwraps TLS application data records into a plain byte stream."""

    def __init__(self, reader: asyncio.StreamReader):
        self._reader = reader
        self._buffer = bytearray()

    async def readexactly(self, n: int) -> bytes:
        while len(self._buffer) < n:
            header = await self._reader.readexactly(5)
            payload = await self._reader.readexactly(struct.unpack(">H", header[3:])[0])
            if header[0] == 0x17:
                self._buffer += payload
            elif header[0] != 0x14:
                raise ConnectionError(f"Unexpected TLS record type {header[0]:#x}")
        data = bytes(self._buffer[:n])
        del self._buffer[:n]
        return data


class _TLSWriter:
    """Wraps outgoing bytes into TLS application data records."""

    def __init__(self, writer: asyncio.StreamWriter):
        self._writer = writer
        self._prefix = _CHANGE_CIPHER_SPEC

    def write(self, data: bytes) -> None:
        out = bytearray(self._prefix)
        self._prefix = b""
        for i in range(0, len(data), _MAX_RECORD):
            chunk = data[i : i + _MAX_RECORD]
            out += b"\x17\x03\x03" + struct.pack(">H", len(chunk)) + chunk
        self._writer.write(bytes(out))

    async def drain(self) -> None:
        await self._writer.drain()

    def close(self) -> None:
        self._writer.close()

    async def wait_closed(self) -> None:
        await self._writer.wait_closed()


class ConnectionTcpMTProxyFakeTLS(ConnectionTcpMTProxyRandomizedIntermediate):
    """MTProxy connection for "ee" (FakeTLS) secrets."""

    def __init__(self, ip, port, dc_id, *, loggers, proxy=None, local_addr=None):
        super().__init__(ip, port, dc_id, loggers=loggers, proxy=proxy, local_addr=local_addr)
        self._tls_domain = parse_secret(proxy[2])[1]
        if self._tls_domain is None:
            raise ValueError("FakeTLS connection requires an ee-secret")

    @staticmethod
    def normalize_secret(secret):
        return parse_secret(secret)[0]

    async def _connect(self, timeout=None, ssl=None):
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(self._ip, self._port), timeout=timeout
        )
        try:
            await asyncio.wait_for(self._handshake(reader, writer), timeout=timeout)
        except BaseException:
            writer.close()
            raise

        self._reader = _TLSReader(reader)
        self._writer = _TLSWriter(writer)
        self._codec = self.packet_codec(self)
        self._init_conn()
        await self._writer.drain()

    async def _handshake(self, reader, writer):
        hello = bytearray(_client_hello(self._tls_domain))
        digest = bytearray(hmac.new(self._secret, hello, hashlib.sha256).digest())
        stamp = struct.unpack("<I", digest[28:32])[0] ^ int(time.time())
        digest[28:32] = struct.pack("<I", stamp & 0xFFFFFFFF)
        hello[11:43] = digest
        writer.write(bytes(hello))
        await writer.drain()

        # ServerHello, ChangeCipherSpec and the first application data record
        response = bytearray()
        while True:
            header = await reader.readexactly(5)
            if header[1:3] != b"\x03\x03":
                raise ConnectionError("FakeTLS proxy sent a non-TLS response")
            payload = await reader.readexactly(struct.unpack(">H", header[3:])[0])
            response += header + payload
            if header[0] == 0x17:
                break

        if response[0] != 0x16 or len(response) < 43:
            raise ConnectionError("FakeTLS proxy did not send ServerHello")
        server_random = bytes(response[11:43])
        response[11:43] = b"\x00" * 32
        expected = hmac.new(self._secret, bytes(digest) + response, hashlib.sha256).digest()
        if not hmac.compare_digest(server_random, expected):
            raise ConnectionError("FakeTLS handshake failed: wrong secret or not an MTProxy")
