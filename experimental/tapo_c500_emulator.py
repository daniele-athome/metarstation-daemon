#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pycryptodome>=3.20", "cryptography>=42"]
# ///
"""
Tapo C500 emulator, written against pytapo 3.3.x and python-kasa 0.10.x as the
reference implementations. It speaks the three protocols a real camera speaks,
so `pytapo.Tapo` and `pytapo.media_stream.streamer.Streamer` work unmodified:

  1. Control channel, HTTPS (default port 443)
     Self-signed TLS. Implements the "secure" login (encrypt_type 3):
     probe -> nonce/device_confirm -> digest_passwd -> stok, then
     securePassthrough with AES-128-CBC using the lsk/ivb tokens.
     Mirrors pytapo/__init__.py: isSecureConnection, validateDeviceConfirm,
     generateEncryptionToken, getTag, performRequest.

  2. Media channel, raw TCP (default port 8800)
     multipart/mixed over a hand-rolled HTTP/1.1 dialogue: unauthenticated
     request -> 401 + WWW-Authenticate Digest -> authenticated request ->
     200 + Key-Exchange, then AES-128-CBC encrypted MPEG-TS chunks.
     Mirrors pytapo/media_stream/session.py and crypto.py.

  3. Discovery, UDP 20002
     Replies to kasa.Discover's probe with a 16-byte header plus a
     DiscoveryResult JSON describing a SMART.IPCAMERA over HTTPS.
     Mirrors kasa/discover.py::_get_discovery_json.

Video is generated locally by ffmpeg: a testsrc2 pattern with the wall clock,
the session number and the frame counter burnt into the image. That matters --
looking at a saved snapshot tells you immediately whether it is fresh, which is
what a staleness check on the HLS playlist can only guess at.

Failure injection (see --help) covers the interesting cases: a stream that
freezes with the socket still open, an unreachable or rejecting camera, several
TP-Link devices answering discovery at once, and a corrupt stream that makes the
consumer's ffmpeg flood stderr.

Nothing here talks to a real camera or a real cloud account. Credentials are
whatever you pass on the command line.

Usage
-----
    sudo ./tapo_c500_emulator.py --password hunter2
    ./tapo_c500_emulator.py --password hunter2 --control-port 4443   # no root

Point the client at 127.0.0.1. Port 443 needs root because pytapo hardcodes it
unless the caller passes controlPort. ffmpeg must be on PATH.

Known deviation from real hardware
----------------------------------
A real camera muxes video PES packets with an explicit packet_length, which is
the path pytapo's PES parser implements. ffmpeg writes packet_length = 0 for
video and prepends an access unit delimiter NAL, and pytapo raises
Exception("TODO IMPLEMENT, needed?") on that combination, killing the streamer on
the first chunk. neutralise_aud() below rewrites the delimiter into an
equally sized filler NAL so the stream stays on pytapo's tolerated path. The
decoded video is identical; the muxing is not byte-identical to a C500's.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import datetime
import hashlib
import json
import logging
import os
import random
import re
import secrets
import shutil
import socket
import ssl
import sys
import tempfile
from dataclasses import dataclass

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

_LOGGER = logging.getLogger("tapo-emu")

# --- protocol constants, all taken from the reference implementations ---------

DEVICE_BOUNDARY = b"--device-stream-boundary--"
"""What we send. pytapo reads Content-Type's boundary= verbatim, and falls back
to this exact value when we omit it (session.py::start)."""

CLIENT_BOUNDARY = b"--client-stream-boundary--"
"""What pytapo sends, prefixed with an extra '--' on the wire."""

STREAM_REALM = "TP-LINK IP-Camera"
DIGEST_URI = "/stream"

DISCOVERY_PORT = 20002
DISCOVERY_HEADER = b"\x02\x00\x00\x01" + b"\x00" * 12
"""kasa's _get_discovery_json only reads data[16:], so the header is cosmetic."""

QUALITY_SIZES = {"HD": (1920, 1080), "VGA": (1280, 720)}

# --- small crypto helpers ----------------------------------------------------


def md5_hex(data: bytes) -> str:
    """Lowercase, as used by the media channel's digest auth."""
    return hashlib.md5(data).hexdigest()


def md5_upper(data: bytes) -> str:
    return hashlib.md5(data).hexdigest().upper()


def sha256_upper(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def hex_nonce(nbytes: int) -> str:
    """Hex only: pytapo parses these headers by splitting on '=' with no
    maxsplit, so any base64 padding would corrupt the parse."""
    return secrets.token_hex(nbytes).upper()


def aes_encrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    """A fresh cipher per message -- pytapo builds a new AES object for every
    encrypt and decrypt, so CBC state must not carry over."""
    return AES.new(key, AES.MODE_CBC, iv=iv).encrypt(pad(data, 16, style="pkcs7"))


def aes_decrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    return unpad(AES.new(key, AES.MODE_CBC, iv=iv).decrypt(data), 16, style="pkcs7")


# --- configuration -----------------------------------------------------------


@dataclass
class Config:
    host: str = "0.0.0.0"
    control_port: int = 443
    stream_port: int = 8800
    user: str = "admin"
    password: str = "cloud_account_password"
    cloud_password: str | None = None

    # video
    quality: str | None = None  # None => honour whatever the client asks for
    fps: int = 15
    bitrate: str = "2M"
    ts_packets: int = 26  # 26 * 188 = 4888 bytes per chunk
    font: str | None = None

    # discovery
    discovery: bool = True
    discovery_bind: str = "0.0.0.0"
    advertise_ip: str | None = None
    extra_devices: int = 0
    decoys_first: bool = False

    # failure injection
    fail_control: bool = False
    fail_stream: bool = False
    fail_auth: bool = False
    freeze_after: float | None = None
    freeze_after_chunks: int | None = None
    drop_after: float | None = None
    corrupt_ts: float = 0.0
    corrupt_depth: int = 8
    corrupt_after: float = 3.0
    slow_stream: float = 0.0
    stall_handshake: float = 0.0

    def __post_init__(self) -> None:
        if self.cloud_password is None:
            self.cloud_password = self.password

    @property
    def hashed_password(self) -> str:
        """SHA256 of the control password, uppercase hex. This emulator always
        negotiates the SHA256 variant, so pytapo's validateDeviceConfirm picks
        EncryptionMethod.SHA256 and every later digest follows suit."""
        return sha256_upper(self.password.encode())

    @property
    def hashed_cloud_password(self) -> str:
        """pwd_digest(cloud_password, SHA256) from media_stream/_utils.py."""
        return sha256_upper(self.cloud_password.encode())

    def size_for(self, requested: str | None) -> tuple[int, int]:
        quality = self.quality or requested or "HD"
        return QUALITY_SIZES.get(quality.upper(), QUALITY_SIZES["HD"])


# --- TLS certificate ---------------------------------------------------------


def make_self_signed_cert(directory: str) -> tuple[str, str]:
    """A real C500 also presents a self-signed cert; pytapo's TlsAdapter sets
    CERT_NONE and ALL:@SECLEVEL=0, so anything valid is accepted."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "TP-LINK IP-Camera"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "TP-LINK"),
        ]
    )
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False
        )
        .sign(key, hashes.SHA256())
    )

    cert_path = os.path.join(directory, "cert.pem")
    key_path = os.path.join(directory, "key.pem")
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(key_path, "wb") as f:
        f.write(
            key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
    return cert_path, key_path


# --- HTTP helpers ------------------------------------------------------------


def parse_header_block(block: bytes) -> dict[str, str]:
    """Same shape as pytapo's parse_http_headers, but case-insensitive keys."""
    out: dict[str, str] = {}
    for line in block.decode("latin-1").strip().split("\r\n"):
        if ":" in line:
            key, value = line.split(":", 1)
            out[key.strip().lower()] = value.strip()
    return out


_AUTH_PAIR = re.compile(r'(\w+)\s*=\s*(?:"([^"]*)"|([^,\s]+))')


def parse_digest_auth(value: str) -> dict[str, str]:
    return {m[1]: (m[2] or m[3]) for m in _AUTH_PAIR.finditer(value)}


# --- control channel ---------------------------------------------------------


@dataclass
class ControlSession:
    stok: str
    lsk: bytes
    ivb: bytes
    seq: int
    cnonce: str


class ControlServer:
    """HTTPS control channel: login, then AES-wrapped securePassthrough."""

    def __init__(self, config: Config):
        self.config = config
        self._nonces: dict[str, str] = {}  # cnonce -> nonce, from login stage 1
        self._sessions: dict[str, ControlSession] = {}

    async def handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info("peername")
        try:
            while True:
                try:
                    head = await reader.readuntil(b"\r\n\r\n")
                except (asyncio.IncompleteReadError, ConnectionError):
                    return

                request_line, _, header_block = head.partition(b"\r\n")
                try:
                    method, path, _ = request_line.decode("latin-1").split(" ", 2)
                except ValueError:
                    return
                headers = parse_header_block(header_block)

                body = b""
                length = int(headers.get("content-length", "0") or 0)
                if length > 0:
                    body = await reader.readexactly(length)

                _LOGGER.debug("control %s %s %s", peer, method, path)
                status, payload = self._route(path, headers, body)
                await self._respond(writer, status, payload)

                if headers.get("connection", "").lower() == "close":
                    return
        finally:
            with contextlib.suppress(ConnectionError):
                writer.close()
                await writer.wait_closed()

    async def _respond(
        self, writer: asyncio.StreamWriter, status: int, payload: dict
    ) -> None:
        body = json.dumps(payload).encode()
        reason = {200: "OK", 401: "Unauthorized", 404: "Not Found"}.get(status, "OK")
        head = (
            f"HTTP/1.1 {status} {reason}\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n"
        )
        writer.write(head.encode("latin-1") + body)
        await writer.drain()

    def _route(self, path: str, headers: dict[str, str], body: bytes) -> tuple[int, dict]:
        if path.startswith("/stok="):
            return self._secure_passthrough(path, headers, body)

        try:
            request = json.loads(body or b"{}")
        except json.JSONDecodeError:
            return 200, {"error_code": -40101}

        if request.get("method") == "login":
            return self._login(request.get("params") or {})
        return 200, {"error_code": -40105}

    # -- login, the encrypt_type 3 flow --------------------------------------

    def _login(self, params: dict) -> tuple[int, dict]:
        if "digest_passwd" in params:
            return self._login_finish(params)
        if "cnonce" in params:
            return self._login_challenge(params)
        return self._login_probe()

    def _login_probe(self) -> tuple[int, dict]:
        """isSecureConnection() looks for error_code -40413 plus a "3" inside
        result.data.encrypt_type; that is what puts pytapo on the secure path."""
        return 200, {
            "error_code": -40413,
            "result": {"data": {"code": -40413, "encrypt_type": ["3"], "nonce": ""}},
        }

    def _login_challenge(self, params: dict) -> tuple[int, dict]:
        cnonce = params["cnonce"]
        nonce = hex_nonce(8)
        self._nonces[cnonce] = nonce

        # validateDeviceConfirm rebuilds exactly this and compares.
        confirm = (
            sha256_upper((cnonce + self.config.hashed_password + nonce).encode())
            + nonce
            + cnonce
        )
        if self.config.fail_auth:
            # A wrong password shows up here, not at stage 2: the client derives
            # device_confirm from its own password and finds it does not match.
            # pytapo then retries twice and raises "Invalid authentication data".
            _LOGGER.warning("--fail-auth: returning a device_confirm that will not match")
            confirm = hex_nonce(32) + nonce + cnonce
        _LOGGER.debug("login challenge cnonce=%s nonce=%s", cnonce, nonce)
        return 200, {
            "error_code": -40413,
            "result": {
                "data": {
                    "code": -40413,
                    "encrypt_type": ["3"],
                    "nonce": nonce,
                    "device_confirm": confirm,
                }
            },
        }

    def _login_finish(self, params: dict) -> tuple[int, dict]:
        cnonce = params.get("cnonce", "")
        nonce = self._nonces.get(cnonce)
        if nonce is None:
            _LOGGER.warning("login stage 2 with an unknown cnonce, asking to restart")
            return 200, {"error_code": -40413, "result": {"data": {"code": -40413}}}

        expected_digest = sha256_upper(
            (self.config.hashed_password + cnonce + nonce).encode()
        )
        expected = expected_digest + cnonce + nonce

        if params.get("digest_passwd") != expected:
            _LOGGER.warning("rejecting login: digest_passwd does not match")
            # -40411 at HTTP 401 is what pytapo turns into
            # "Invalid authentication data".
            return 401, {
                "error_code": -40411,
                "result": {"data": {"code": -40411, "sec_left": 0}},
            }

        # generateEncryptionToken("lsk"/"ivb", nonce)
        hashed_key = sha256_upper(
            (cnonce + self.config.hashed_password + nonce).encode()
        )
        lsk = hashlib.sha256(("lsk" + cnonce + nonce + hashed_key).encode()).digest()[:16]
        ivb = hashlib.sha256(("ivb" + cnonce + nonce + hashed_key).encode()).digest()[:16]

        stok = secrets.token_hex(16)
        start_seq = random.randint(100, 5000)
        self._sessions[stok] = ControlSession(
            stok=stok, lsk=lsk, ivb=ivb, seq=start_seq, cnonce=cnonce
        )
        self._nonces.pop(cnonce, None)

        _LOGGER.info("login ok, stok=%s start_seq=%d", stok, start_seq)
        return 200, {
            "error_code": 0,
            "result": {"stok": stok, "start_seq": start_seq, "user_group": "root"},
        }

    # -- securePassthrough ---------------------------------------------------

    def _secure_passthrough(
        self, path: str, headers: dict[str, str], body: bytes
    ) -> tuple[int, dict]:
        stok = path.split("/")[1].removeprefix("stok=")
        session = self._sessions.get(stok)
        if session is None:
            _LOGGER.warning("unknown stok %s, forcing a re-login", stok)
            return 200, {"error_code": -40401}

        try:
            outer = json.loads(body)
            ciphertext = base64.b64decode(outer["params"]["request"])
            inner = json.loads(aes_decrypt(session.lsk, session.ivb, ciphertext))
        except Exception:
            _LOGGER.warning("could not decrypt a passthrough request", exc_info=True)
            return 200, {"error_code": -40401}

        # getTag() hashes the raw outer body plus the Seq header, so we can
        # verify it byte for byte. A mismatch means the client's view of the
        # sequence number or the password has drifted.
        seq_header = headers.get("seq")
        tag = headers.get("tapo_tag")
        if tag and seq_header:
            base = sha256_upper(
                (self.config.hashed_password + session.cnonce).encode()
            )
            expected = sha256_upper(base.encode() + body + seq_header.encode())
            if expected != tag.upper():
                _LOGGER.warning(
                    "Tapo_tag mismatch (seq=%s): the client's password or sequence "
                    "number has drifted",
                    seq_header,
                )
            else:
                _LOGGER.debug("Tapo_tag ok (seq=%s)", seq_header)

        response = self._dispatch(inner)
        encrypted = aes_encrypt(
            session.lsk, session.ivb, json.dumps(response).encode()
        )
        return 200, {
            "error_code": 0,
            "result": {"response": base64.b64encode(encrypted).decode()},
        }

    def _dispatch(self, request: dict) -> dict:
        if request.get("method") != "multipleRequest":
            return {"error_code": -40105}

        requests = (request.get("params") or {}).get("requests") or []
        responses = [self._dispatch_one(r) for r in requests]
        return {"error_code": 0, "result": {"responses": responses}}

    def _dispatch_one(self, request: dict) -> dict:
        method = request.get("method")
        handler = getattr(self, f"_do_{method}", None)
        if handler is None:
            _LOGGER.info("unimplemented control method: %s", method)
            return {"method": method, "error_code": -40210}
        return {"method": method, "result": handler(request.get("params") or {}), "error_code": 0}

    def _do_getDeviceInfo(self, params: dict) -> dict:
        return {
            "device_info": {
                "basic_info": {
                    "device_type": "SMART.IPCAMERA",
                    "device_model": "C500",
                    "device_name": "C500 4.0",
                    "device_info": "C500 4.0 IPC",
                    "device_alias": "Aviosuperficie",
                    "hw_version": "4.0",
                    "sw_version": "1.3.9 Build 240424 Rel.52192n",
                    "mac": "A8-42-A1-00-00-01",
                    "dev_id": secrets.token_hex(20).upper(),
                    "oem_id": secrets.token_hex(16).upper(),
                    "hw_desc": "00000000000000000000000000000000",
                    "features": "3",
                    "barcode": "",
                    "region": "Europe/Rome",
                    "has_set_location_info": 1,
                    "latitude": 0,
                    "longitude": 0,
                    "avatar": "camera",
                    "is_cal": 1,
                }
            }
        }

    def _do_getAudioConfig(self, params: dict) -> dict:
        return {
            "audio_config": {
                "microphone": {
                    "encode_type": "G711alaw",
                    "sampling_rate": "8",
                    "volume": "80",
                    "mute": "off",
                },
                "speaker": {"volume": "80", "mute": "off"},
                "record_audio": {"enabled": "on"},
            }
        }

    def _do_getPresetConfig(self, params: dict) -> dict:
        # processPresetsResponse zips id and name, so both lists must line up.
        return {"preset": {"preset": {"id": ["1", "2"], "name": ["Pista", "Cielo"]}}}

    def _do_getClockStatus(self, params: dict) -> dict:
        now = datetime.datetime.now()
        return {
            "system": {
                "clock_status": {
                    "seconds_from_1970": int(now.timestamp()),
                    "local_time": now.strftime("%Y-%m-%d %H:%M:%S"),
                }
            }
        }


# --- video source ------------------------------------------------------------

_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf",
    "/System/Library/Fonts/Menlo.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
)


def find_font(explicit: str | None) -> str | None:
    if explicit:
        return explicit if os.path.exists(explicit) else None
    for candidate in _FONT_CANDIDATES:
        if os.path.exists(candidate):
            return candidate
    return None


def build_ffmpeg_command(
    config: Config, width: int, height: int, session_id: int, font: str | None
) -> list[str]:
    """testsrc2 with the wall clock, session number and frame counter drawn on
    top. A snapshot taken from this stream carries its own timestamp, so a stale
    image is obvious rather than inferred."""
    source = f"testsrc2=size={width}x{height}:rate={config.fps}"
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-re",
        "-f", "lavfi",
        "-i", source,
    ]

    if font:
        size = max(24, height // 20)
        common = (
            f"fontfile={font}:fontsize={size}:fontcolor=white"
            ":box=1:boxcolor=black@0.6:borderw=0"
        )
        clock = r"%{localtime\:%Y-%m-%d %H\\\:%M\\\:%S}"
        overlay = (
            f"drawtext={common}:text='{clock}':x=24:y=24,"
            f"drawtext={common}:text='sessione {session_id}  frame %{{n}}'"
            f":x=24:y={24 + size + 14}"
        )
        cmd += ["-vf", overlay]
    else:
        _LOGGER.warning(
            "no TrueType font found, falling back to the plain test pattern "
            "(pass --font to get a burnt-in clock)"
        )

    cmd += [
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-tune", "zerolatency",
        "-g", str(config.fps),  # one keyframe per second, so HLS can segment
        "-pix_fmt", "yuv420p",
        "-b:v", config.bitrate,
        "-f", "mpegts",
        "-mpegts_flags", "+resend_headers",
        "-pat_period", "0.2",
        "pipe:1",
    ]
    return cmd


# A real camera muxes video PES packets with an explicit packet_length, so
# pytapo's PES parser takes its ModeSize path. ffmpeg's mpegts muxer instead
# writes packet_length = 0 for video (legal, and unavoidable: the muxer also
# prepends an access unit delimiter NAL that no bitstream filter can suppress,
# because it is re-added after filtering). pytapo's PES.SetBuffer reads that
# combination -- zero length plus a 4-byte-start-code AUD -- as ModeStream, and
# PES.GetPacket raises Exception("TODO IMPLEMENT, needed?") on it, killing the
# streamer task on the first chunk.
#
# So we rewrite the AUD into an equally sized filler-data NAL. The access unit
# delimiter is optional in H.264 and filler data is ignored by every decoder, so
# the stream still decodes identically, the TS stays byte-for-byte 188-aligned,
# and pytapo leaves Mode unset and simply returns no RTP packet -- which is all
# the streamer needs, since it only uses those packets for audio.
_AUD_PREFIX = b"\x00\x00\x00\x01\x09"
_FILLER_NAL = b"\x00\x00\x00\x01\x0c\x80"  # NAL type 12, rbsp stop bit


def neutralise_aud(chunk: bytearray) -> int:
    """Patch access unit delimiters in place. Returns how many were rewritten."""
    patched = 0
    for offset in range(0, len(chunk) - 187, 188):
        if chunk[offset] != 0x47:
            continue
        if not chunk[offset + 1] & 0x40:  # payload_unit_start_indicator
            continue
        cursor = offset + 4
        if chunk[offset + 3] & 0x20:  # adaptation field present
            cursor += 1 + chunk[cursor]
        if cursor + 9 > offset + 188:
            continue
        if bytes(chunk[cursor : cursor + 3]) != b"\x00\x00\x01":
            continue
        if not 0xE0 <= chunk[cursor + 3] <= 0xEF:  # video stream_id
            continue
        payload = cursor + 9 + chunk[cursor + 8]  # after the optional PES fields
        if payload + 6 > offset + 188:
            continue
        if bytes(chunk[payload : payload + 5]) == _AUD_PREFIX:
            chunk[payload : payload + 6] = _FILLER_NAL
            patched += 1
    return patched


# --- media channel -----------------------------------------------------------


@dataclass
class StreamKeys:
    username: str
    nonce: str
    key: bytes
    iv: bytes

    @classmethod
    def create(cls, username: str, hashed_password: str) -> StreamKeys:
        """AESHelper.__init__: key = MD5(nonce:hashed_pwd), iv = MD5(user:nonce)."""
        nonce = hex_nonce(16)
        key = hashlib.md5(f"{nonce}:{hashed_password}".encode()).digest()
        iv = hashlib.md5(f"{username}:{nonce}".encode()).digest()
        return cls(username=username, nonce=nonce, key=key, iv=iv)

    def encrypt(self, data: bytes) -> bytes:
        return aes_encrypt(self.key, self.iv, data)


class StreamServer:
    """The proprietary media channel on port 8800."""

    def __init__(self, config: Config, font: str | None):
        self.config = config
        self.font = font
        self._next_session = 1

    async def handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info("peername")
        _LOGGER.info("stream connection from %s", peer)
        keys: StreamKeys | None = None
        try:
            keys = await self._handshake(reader, writer)
            if keys is None:
                return
            await self._serve(reader, writer, keys)
        except (asyncio.IncompleteReadError, ConnectionError):
            _LOGGER.info("stream client %s disconnected", peer)
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.error("stream session failed", exc_info=True)
        finally:
            with contextlib.suppress(ConnectionError):
                writer.close()
                await writer.wait_closed()

    async def _handshake(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> StreamKeys | None:
        if self.config.stall_handshake:
            await asyncio.sleep(self.config.stall_handshake)

        head = await reader.readuntil(b"\r\n\r\n")
        headers = parse_header_block(head.partition(b"\r\n")[2])

        nonce = hex_nonce(16)
        opaque = hex_nonce(8)

        if "authorization" not in headers:
            # No spaces after the commas and hex-only values: pytapo parses this
            # header by splitting on ' ' once, then ',', then '=' with no
            # maxsplit, so anything fancier corrupts the parse.
            challenge = (
                f'Digest realm="{STREAM_REALM}",nonce="{nonce}",'
                f'qop="auth",opaque="{opaque}"'
            )
            writer.write(
                b"HTTP/1.1 401 Unauthorized\r\n"
                + f"WWW-Authenticate: {challenge}\r\n".encode("latin-1")
                + b"Content-Length: 0\r\n\r\n"
            )
            await writer.drain()

            head = await reader.readuntil(b"\r\n\r\n")
            headers = parse_header_block(head.partition(b"\r\n")[2])

        if not self._check_digest(headers.get("authorization", ""), nonce):
            _LOGGER.warning("stream authentication failed")
            writer.write(b"HTTP/1.1 401 Unauthorized\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            return None

        keys = StreamKeys.create(self.config.user, self.config.hashed_cloud_password)
        writer.write(
            b"HTTP/1.1 200 OK\r\n"
            + (
                "Content-Type: multipart/mixed;boundary="
                f"{DEVICE_BOUNDARY.decode()}\r\n"
            ).encode("latin-1")
            + (
                f'Key-Exchange: username="{keys.username}" nonce="{keys.nonce}"\r\n'
            ).encode("latin-1")
            + b"Transfer-Encoding: chunked\r\n"
            + b"Connection: keep-alive\r\n\r\n"
        )
        await writer.drain()
        _LOGGER.debug("key exchange done, nonce=%s", keys.nonce)
        return keys

    def _check_digest(self, authorization: str, nonce: str) -> bool:
        if not authorization:
            return False
        auth = parse_digest_auth(authorization)
        if auth.get("nonce") != nonce:
            _LOGGER.warning("digest nonce mismatch")
            return False

        username = auth.get("username", "")
        challenge1 = md5_hex(
            f"{username}:{STREAM_REALM}:{self.config.hashed_cloud_password}".encode()
        )
        challenge2 = md5_hex(f"POST:{DIGEST_URI}".encode())
        expected = md5_hex(
            ":".join(
                (
                    challenge1,
                    nonce,
                    auth.get("nc", ""),
                    auth.get("cnonce", ""),
                    auth.get("qop", ""),
                    challenge2,
                )
            ).encode()
        )
        if expected != auth.get("response"):
            _LOGGER.warning(
                "digest response mismatch: the cloud password does not match"
            )
            return False
        return True

    # -- framing -------------------------------------------------------------

    def _frame(
        self,
        keys: StreamKeys,
        body: bytes,
        mimetype: str,
        *,
        session: int | None = None,
        sequence: int | None = None,
        encrypt: bool = True,
    ) -> bytes:
        if encrypt:
            body = keys.encrypt(body)
        lines = [
            f"Content-Type: {mimetype}",
            f"Content-Length: {len(body)}",
            f"X-If-Encrypt: {int(encrypt)}",
        ]
        if session is not None:
            lines.append(f"X-Session-Id: {session}")
        if sequence is not None:
            lines.append(f"X-Data-Sequence: {sequence}")
        head = ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")
        return DEVICE_BOUNDARY + b"\r\n" + head + body + b"\r\n"

    async def _serve(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        keys: StreamKeys,
    ) -> None:
        """Read client chunks; the preview request starts the video pump."""
        video_task: asyncio.Task | None = None
        try:
            while True:
                await reader.readuntil(CLIENT_BOUNDARY)
                head = await reader.readuntil(b"\r\n\r\n")
                headers = parse_header_block(head)
                length = int(headers.get("content-length", "0") or 0)
                body = await reader.readexactly(length) if length else b""

                if headers.get("content-type") != "application/json":
                    # Window acknowledgements carry no Content-Type.
                    _LOGGER.debug(
                        "client ack, received=%s", headers.get("x-data-received")
                    )
                    continue

                try:
                    request = json.loads(body)
                except json.JSONDecodeError:
                    _LOGGER.warning("unparseable client request: %r", body[:200])
                    continue

                if request.get("type") != "request":
                    continue

                session_id = self._next_session
                self._next_session += 1

                preview = (request.get("params") or {}).get("preview") or {}
                requested = (preview.get("resolutions") or [None])[0]
                width, height = self.config.size_for(requested)
                _LOGGER.info(
                    "starting session %d at %dx%d (client asked for %s)",
                    session_id,
                    width,
                    height,
                    requested,
                )

                # This response both answers the client's sequence number and
                # hands out the session id; session.py needs both to bind its
                # queue before any video arrives.
                writer.write(
                    self._frame(
                        keys,
                        json.dumps(
                            {
                                "type": "response",
                                "seq": request.get("seq"),
                                "params": {"session_id": session_id},
                                "error_code": 0,
                            }
                        ).encode(),
                        "application/json",
                    )
                )
                await writer.drain()

                if video_task is not None:
                    video_task.cancel()
                video_task = asyncio.create_task(
                    self._pump_video(writer, keys, session_id, width, height)
                )
        finally:
            if video_task is not None:
                video_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await video_task

    async def _pump_video(
        self,
        writer: asyncio.StreamWriter,
        keys: StreamKeys,
        session_id: int,
        width: int,
        height: int,
    ) -> None:
        cmd = build_ffmpeg_command(self.config, width, height, session_id, self.font)
        _LOGGER.debug("ffmpeg: %s", " ".join(cmd))
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stderr_task = asyncio.create_task(self._drain_ffmpeg(process.stderr))

        chunk_size = self.config.ts_packets * 188
        buffer = bytearray()
        sequence = 0
        started = asyncio.get_running_loop().time()
        frozen = False

        try:
            while True:
                data = await process.stdout.read(65536)
                if not data:
                    _LOGGER.warning("ffmpeg stopped producing for session %d", session_id)
                    break
                buffer += data

                while len(buffer) >= chunk_size:
                    raw = bytearray(buffer[:chunk_size])
                    del buffer[:chunk_size]
                    neutralise_aud(raw)
                    chunk = bytes(raw)

                    elapsed = asyncio.get_running_loop().time() - started
                    if not frozen and self._should_freeze(elapsed, sequence):
                        frozen = True
                        _LOGGER.warning(
                            "freezing session %d: socket stays open, no more TS "
                            "(the playlist mtime will stop moving)",
                            session_id,
                        )
                    if frozen:
                        continue

                    if self.config.drop_after and elapsed >= self.config.drop_after:
                        _LOGGER.warning(
                            "dropping session %d after %.1fs", session_id, elapsed
                        )
                        writer.close()
                        return

                    # Leave the opening seconds clean: the consumer needs a
                    # valid SPS/PPS and one good keyframe to lock on, and a
                    # stream that is corrupt from the first byte is simply
                    # rejected instead of decoded-and-complained-about.
                    if (
                        self.config.corrupt_ts
                        and elapsed >= self.config.corrupt_after
                        and random.random() < self.config.corrupt_ts
                    ):
                        chunk = self._corrupt(chunk)

                    writer.write(
                        self._frame(
                            keys,
                            chunk,
                            "video/mp2t",
                            session=session_id,
                            sequence=sequence,
                        )
                    )
                    await writer.drain()
                    sequence += 1

                    if self.config.slow_stream:
                        await asyncio.sleep(self.config.slow_stream)
        except (ConnectionError, asyncio.CancelledError):
            raise
        finally:
            stderr_task.cancel()
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            with contextlib.suppress(asyncio.CancelledError, ConnectionError):
                await process.wait()
            _LOGGER.info(
                "session %d ended after %d chunks", session_id, sequence
            )

    def _should_freeze(self, elapsed: float, sequence: int) -> bool:
        if self.config.freeze_after is not None and elapsed >= self.config.freeze_after:
            return True
        return (
            self.config.freeze_after_chunks is not None
            and sequence >= self.config.freeze_after_chunks
        )

    def _corrupt(self, chunk: bytes) -> bytes:
        """Scramble elementary-stream payload only, leaving the TS header and the
        adaptation field intact. Corrupting those instead makes ffmpeg give up on
        the stream ("wrong adaptation size") rather than keep decoding and
        complaining, and it is the per-frame complaining that floods stderr."""
        out = bytearray(chunk)
        for offset in range(0, len(out) - 187, 188):
            if out[offset] != 0x47:
                continue
            pid = ((out[offset + 1] & 0x1F) << 8) | out[offset + 2]
            if pid in (0x0000, 0x1000, 0x1FFF):  # PAT, PMT, null: keep parseable
                continue
            start = offset + 4
            if out[offset + 3] & 0x20:  # adaptation field
                start += 1 + out[start]
            if start >= offset + 188:
                continue
            for _ in range(self.config.corrupt_depth):
                position = random.randint(start, offset + 187)
                out[position] ^= random.randint(1, 255)
        return bytes(out)

    async def _drain_ffmpeg(self, stderr: asyncio.StreamReader | None) -> None:
        if stderr is None:
            return
        while True:
            line = await stderr.readline()
            if not line:
                return
            _LOGGER.debug("ffmpeg: %s", line.decode(errors="replace").strip())


# --- discovery ---------------------------------------------------------------


def camera_discovery_result(ip: str) -> dict:
    return {
        "device_id": secrets.token_hex(20).upper(),
        "owner": secrets.token_hex(16).upper(),
        "device_type": "SMART.IPCAMERA",
        "device_model": "C500",
        "device_name": "C500",
        "ip": ip,
        "mac": "A8-42-A1-00-00-01",
        "is_support_iot_cloud": True,
        "obd_src": "tplink",
        "factory_default": False,
        "firmware_version": "1.3.9",
        "hardware_version": "4.0",
        # is_support_https drives kasa's lookup key SMART.IPCAMERA.HTTPS.
        "mgt_encrypt_schm": {
            "is_support_https": True,
            "encrypt_type": "AES",
            "http_port": 443,
            "lv": 3,
        },
    }


def decoy_discovery_result(ip: str, index: int) -> dict:
    """A plug and a bulb, so `next(iter(devices))` has something wrong to pick."""
    decoys = [
        ("SMART.TAPOPLUG", "P110", "Presa hangar"),
        ("SMART.TAPOBULB", "L530", "Lampada ufficio"),
        ("SMART.TAPOPLUG", "P100", "Presa radio"),
    ]
    device_type, model, name = decoys[index % len(decoys)]
    return {
        "device_id": secrets.token_hex(20).upper(),
        "owner": secrets.token_hex(16).upper(),
        "device_type": device_type,
        "device_model": model,
        "device_name": name,
        "ip": ip,
        "mac": f"A8-42-A1-00-00-{index + 2:02X}",
        "is_support_iot_cloud": True,
        "obd_src": "tplink",
        "factory_default": False,
        "mgt_encrypt_schm": {
            "is_support_https": False,
            "encrypt_type": "KLAP",
            "http_port": 80,
            "lv": 2,
        },
    }


@dataclass
class FakeDevice:
    label: str
    result: dict
    responder: socket.socket | None = None
    """A socket bound to this device's own address, so the reply carries a
    distinct source IP. kasa keys discovered devices by the reply's source
    address and ignores anything it has already seen, so decoys that answer from
    one address collapse into a single entry. None means "reply from the
    listening socket" and lets the kernel pick the address."""

    def frame(self) -> bytes:
        payload = json.dumps({"error_code": 0, "result": self.result}).encode()
        return DISCOVERY_HEADER + payload


class DiscoveryProtocol(asyncio.DatagramProtocol):
    """One listener answers on behalf of every fake device."""

    def __init__(self, devices: list[FakeDevice]):
        self.devices = devices
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport) -> None:  # type: ignore[override]
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:  # type: ignore[override]
        if self.transport is None:
            return
        _LOGGER.info(
            "discovery probe from %s, answering as %s",
            addr[0],
            ", ".join(d.label for d in self.devices),
        )
        for device in self.devices:
            frame = device.frame()
            if device.responder is None:
                self.transport.sendto(frame, addr)
            else:
                device.responder.sendto(frame, addr)


# --- wiring ------------------------------------------------------------------


def bind_udp(ip: str) -> socket.socket:
    """SO_REUSEADDR everywhere, because the wildcard listener and the decoys'
    per-address responders all want port 20002. kasa drops any reply whose
    source port is not the discovery port, so the responders cannot use an
    ephemeral one."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind((ip, DISCOVERY_PORT))
    sock.setblocking(False)
    return sock


async def start_discovery(
    config: Config,
) -> tuple[asyncio.DatagramTransport, list[socket.socket]]:
    advertised = config.advertise_ip or "127.0.0.1"
    devices = [FakeDevice(label="C500", result=camera_discovery_result(advertised))]

    # The wildcard listener has to be bound before the per-address responders,
    # otherwise the specific binds make the wildcard bind fail with EADDRINUSE.
    listener = bind_udp(config.discovery_bind)

    responders: list[socket.socket] = []
    for index in range(config.extra_devices):
        ip = f"127.0.0.{index + 2}"
        result = decoy_discovery_result(ip, index)
        responder = bind_udp(ip)
        responders.append(responder)
        devices.append(
            FakeDevice(
                label=f"{result['device_name']} @ {ip}",
                result=result,
                responder=responder,
            )
        )

    if config.decoys_first:
        # Discovery order is otherwise whatever the network delivers first;
        # answering as the decoys before the camera makes the outcome repeatable.
        devices = devices[1:] + devices[:1]

    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: DiscoveryProtocol(devices), sock=listener
    )
    _LOGGER.info(
        "discovery listening on %s:%d as %d device(s)",
        config.discovery_bind,
        DISCOVERY_PORT,
        len(devices),
    )
    if config.extra_devices:
        _LOGGER.info(
            'decoys answer from 127.0.0.2+, so discover with target="127.0.0.255" '
            "to see them all"
        )
    return transport, responders


def _suppress_tls_noise(loop: asyncio.AbstractEventLoop) -> None:
    """pytapo's _isKLAP() opens a plaintext HTTP request against the TLS port on
    purpose; the resulting handshake error is expected, not a problem."""

    def handler(_loop, context):
        exception = context.get("exception")
        if isinstance(exception, ssl.SSLError):
            _LOGGER.debug("ignored TLS handshake error: %s", exception)
            return
        message = context.get("message", "")
        if "SSL" in message or "ssl" in message:
            _LOGGER.debug("ignored TLS error: %s", message)
            return
        _loop.default_exception_handler(context)

    loop.set_exception_handler(handler)


async def run(config: Config) -> None:
    if shutil.which("ffmpeg") is None:
        _LOGGER.error("ffmpeg is not on PATH; the emulator needs it to make video")
        raise SystemExit(1)

    _suppress_tls_noise(asyncio.get_running_loop())
    font = find_font(config.font)
    servers: list[asyncio.Server] = []
    transports: list[asyncio.DatagramTransport] = []
    responders: list[socket.socket] = []
    tempdir = tempfile.mkdtemp(prefix="tapo-emu-")

    try:
        if config.fail_control:
            _LOGGER.warning(
                "--fail-control: not listening on the control port, clients will "
                "get ECONNREFUSED"
            )
        else:
            cert_path, key_path = make_self_signed_cert(tempdir)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(cert_path, key_path)
            control = ControlServer(config)
            servers.append(
                await asyncio.start_server(
                    control.handle, config.host, config.control_port, ssl=context
                )
            )
            _LOGGER.info(
                "control channel on https://%s:%d", config.host, config.control_port
            )

        stream_server: StreamServer | None = None
        if config.fail_stream:
            _LOGGER.warning("--fail-stream: not listening on the media port")
        else:
            stream_server = StreamServer(config, font)
            servers.append(
                await asyncio.start_server(
                    stream_server.handle, config.host, config.stream_port
                )
            )
            _LOGGER.info(
                "media channel on tcp://%s:%d", config.host, config.stream_port
            )

        if config.discovery:
            transport, responders = await start_discovery(config)
            transports.append(transport)

        _LOGGER.info(
            "ready: user=%s password=%s cloud_password=%s",
            config.user,
            config.password,
            config.cloud_password,
        )
        if font:
            _LOGGER.info("burning the clock into the picture with %s", font)

        await asyncio.Event().wait()
    finally:
        for transport in transports:
            transport.close()
        for responder in responders:
            responder.close()
        for server in servers:
            server.close()
            with contextlib.suppress(Exception):
                await server.wait_closed()
        shutil.rmtree(tempdir, ignore_errors=True)


def parse_args(argv: list[str]) -> Config:
    parser = argparse.ArgumentParser(
        prog="tapo_c500_emulator",
        description="Emulates a Tapo C500 for pytapo and python-kasa clients.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument("--host", default="0.0.0.0", help="address to bind")
    parser.add_argument("--control-port", type=int, default=443,
                        help="HTTPS control port; pytapo defaults to 443, which needs root")
    parser.add_argument("--stream-port", type=int, default=8800, help="media channel port")
    parser.add_argument("--user", default="admin", help="control username")
    parser.add_argument("--password", default="cloud_account_password",
                        help="control password (pytapo's password= argument)")
    parser.add_argument("--cloud-password", default=None,
                        help="cloud password used by the media channel; defaults to --password")

    video = parser.add_argument_group("video")
    video.add_argument("--quality", choices=sorted(QUALITY_SIZES),
                       help="force a resolution instead of honouring the client's request")
    video.add_argument("--fps", type=int, default=15)
    video.add_argument("--bitrate", default="2M")
    video.add_argument("--ts-packets", type=int, default=26,
                       help="188-byte TS packets per multipart chunk")
    video.add_argument("--font", help="TrueType font for the burnt-in clock")

    discovery = parser.add_argument_group("discovery")
    discovery.add_argument("--no-discovery", dest="discovery", action="store_false",
                           help="do not answer kasa discovery probes")
    discovery.add_argument("--discovery-bind", default="0.0.0.0")
    discovery.add_argument("--advertise-ip",
                           help="the ip reported in the discovery result")
    discovery.add_argument("--extra-devices", type=int, default=0,
                           help="also answer as N decoy TP-Link devices on 127.0.0.2+")
    discovery.add_argument("--decoys-first", action="store_true",
                           help="let the decoys answer before the camera, so the first "
                                "discovered device is reliably the wrong one")

    faults = parser.add_argument_group("failure injection")
    faults.add_argument("--fail-control", action="store_true",
                        help="refuse connections on the control port")
    faults.add_argument("--fail-stream", action="store_true",
                        help="refuse connections on the media port")
    faults.add_argument("--fail-auth", action="store_true",
                        help="reject every login with -40411")
    faults.add_argument("--freeze-after", type=float,
                        help="after N seconds per session, stop sending TS but keep the socket open")
    faults.add_argument("--freeze-after-chunks", type=int,
                        help="same, counted in chunks instead of seconds")
    faults.add_argument("--drop-after", type=float,
                        help="close the media connection abruptly after N seconds")
    faults.add_argument("--corrupt-ts", type=float, default=0.0,
                        help="corrupt this fraction of chunks (0..1) to make the consumer's ffmpeg noisy")
    faults.add_argument("--corrupt-depth", type=int, default=8,
                        help="payload bytes to scramble per TS packet when corrupting")
    faults.add_argument("--corrupt-after", type=float, default=3.0,
                        help="seconds of clean stream before corruption starts, so the "
                             "consumer can lock on first")
    faults.add_argument("--slow-stream", type=float, default=0.0,
                        help="extra delay in seconds between chunks")
    faults.add_argument("--stall-handshake", type=float, default=0.0,
                        help="delay before answering on the media port")

    parser.add_argument("-v", "--verbose", action="store_true")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="[%(asctime)s] %(name)s %(levelname)s - %(message)s",
    )

    fields = {f for f in Config.__dataclass_fields__}
    return Config(**{k: v for k, v in vars(args).items() if k in fields})


def main() -> None:
    config = parse_args(sys.argv[1:])
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run(config))


if __name__ == "__main__":
    main()
