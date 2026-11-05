"""Small bounded framing for an inherited private Unix socket, not a network API."""
import json
import struct

MAX_JSON = 65_536
MAX_PCM = 1_024_000  # Covers the existing 30-second limit plus silence padding.
HEADER = struct.Struct("!II")


def encode(message, pcm=b""):
    body = json.dumps(message, allow_nan=False, separators=(",", ":")).encode()
    if not 1 <= len(body) <= MAX_JSON or len(pcm) > MAX_PCM:
        raise ValueError("Native worker message exceeds the protocol limit")
    return HEADER.pack(len(body), len(pcm)) + body + pcm


def lengths(header):
    size, pcm_size = HEADER.unpack(header)
    if not 1 <= size <= MAX_JSON or pcm_size > MAX_PCM:
        raise ValueError("Invalid native worker message size")
    return size, pcm_size


def decode(body):
    result = json.loads(body)
    if not isinstance(result, dict) or not isinstance(result.get("type"), str):
        raise ValueError("Invalid native worker message")
    return result


async def receive(reader):
    size, pcm_size = lengths(await reader.readexactly(HEADER.size))
    message = decode(await reader.readexactly(size))
    pcm = await reader.readexactly(pcm_size) if pcm_size else b""
    return message, pcm


def receive_blocking(sock):
    def exact(size):
        result = bytearray()
        while len(result) < size:
            data = sock.recv(size - len(result))
            if not data:
                raise EOFError("Native worker socket closed")
            result.extend(data)
        return bytes(result)
    size, pcm_size = lengths(exact(HEADER.size))
    return decode(exact(size)), exact(pcm_size) if pcm_size else b""
