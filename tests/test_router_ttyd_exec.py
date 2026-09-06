import base64
import hashlib
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / "scripts" / "router-ttyd-exec.mjs"


def _read_exact(connection, length):
    data = b""
    while len(data) < length:
        chunk = connection.recv(length - len(data))
        if not chunk:
            raise ConnectionError("client disconnected")
        data += chunk
    return data


def _read_frame(connection):
    first, second = _read_exact(connection, 2)
    length = second & 0x7F
    if length == 126:
        length = struct.unpack("!H", _read_exact(connection, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", _read_exact(connection, 8))[0]
    mask = _read_exact(connection, 4) if second & 0x80 else None
    payload = _read_exact(connection, length)
    if mask:
        payload = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
    return first & 0x0F, payload


def _send_frame(connection, payload, opcode=2):
    header = bytes([0x80 | opcode])
    length = len(payload)
    if length < 126:
        header += bytes([length])
    elif length <= 0xFFFF:
        header += bytes([126]) + struct.pack("!H", length)
    else:
        header += bytes([127]) + struct.pack("!Q", length)
    connection.sendall(header + payload)


class FakeTtyd:
    def __init__(self):
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.error = None
        self.received = []
        self.thread = threading.Thread(target=self._serve, daemon=True)

    def start(self):
        self.thread.start()

    def finish(self):
        self.thread.join(timeout=5)
        self.listener.close()
        if self.thread.is_alive():
            raise AssertionError("fake ttyd server did not finish")
        if self.error:
            raise self.error

    def _receive_text(self, connection):
        opcode, payload = _read_frame(connection)
        if opcode == 8:
            raise ConnectionError("client closed early")
        value = payload.decode()
        self.received.append(value)
        return value

    def _serve(self):
        try:
            connection, _ = self.listener.accept()
            with connection:
                request = b""
                while b"\r\n\r\n" not in request:
                    request += connection.recv(4096)
                headers = {}
                for line in request.decode().split("\r\n")[1:]:
                    if ":" in line:
                        name, value = line.split(":", 1)
                        headers[name.lower()] = value.strip()
                accept = base64.b64encode(
                    hashlib.sha1(
                        (headers["sec-websocket-key"] + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()
                    ).digest()
                ).decode()
                connection.sendall(
                    (
                        "HTTP/1.1 101 Switching Protocols\r\n"
                        "Upgrade: websocket\r\n"
                        "Connection: Upgrade\r\n"
                        f"Sec-WebSocket-Accept: {accept}\r\n"
                        "Sec-WebSocket-Protocol: tty\r\n\r\n"
                    ).encode()
                )
                self._receive_text(connection)  # ttyd initialization JSON
                _send_frame(connection, b"0iStoreOS login: ")
                self._receive_text(connection)
                _send_frame(connection, b"0Password: ")
                self._receive_text(connection)
                _send_frame(connection, b"0root@iStoreOS:~# ")
                arming = self._receive_text(connection)
                marker = arming.split("__OCFILTER_READY_", 1)[1].split("__", 1)[0]
                _send_frame(connection, f"0\n__OCFILTER_READY_{marker}__\nroot@iStoreOS:~# ".encode())
                command = self._receive_text(connection)
                done = command.split("__OCFILTER_DONE_", 1)[1].split("__", 1)[0]
                _send_frame(connection, f"0command output\n__OCFILTER_DONE_{done}__7\n".encode())
                try:
                    _read_frame(connection)
                except (ConnectionError, OSError):
                    pass
        except Exception as error:  # pragma: no cover - surfaced by finish()
            self.error = error


@unittest.skipUnless(shutil.which("node"), "Node.js is required")
class RouterTtydExecTests(unittest.TestCase):
    def write_env(self, directory, url, password="test-password", mode=0o600):
        env_path = Path(directory) / ".router.env"
        env_path.write_text(
            f"ROUTER_TTYD_URL={url}\n"
            "ROUTER_USER=root\n"
            f"ROUTER_PASSWORD={password}\n"
            "ROUTER_COMMAND_TIMEOUT_SECONDS=5\n",
            encoding="utf-8",
        )
        env_path.chmod(mode)
        return env_path

    def test_check_config_never_prints_password(self):
        with tempfile.TemporaryDirectory() as directory:
            env_path = self.write_env(directory, "http://127.0.0.1:7681")
            result = subprocess.run(
                ["node", str(CLIENT), "--env", str(env_path), "--check-config"],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("test-password", result.stdout + result.stderr)

    def test_rejects_permissive_credential_file(self):
        if os.name == "nt":
            self.skipTest("POSIX permission check")
        with tempfile.TemporaryDirectory() as directory:
            env_path = self.write_env(directory, "http://127.0.0.1:7681", mode=0o644)
            result = subprocess.run(
                ["node", str(CLIENT), "--env", str(env_path), "--check-config"],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("chmod 600", result.stderr)

    def test_executes_through_ttyd_and_returns_remote_status(self):
        server = FakeTtyd()
        server.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                env_path = self.write_env(directory, f"http://127.0.0.1:{server.port}")
                result = subprocess.run(
                    ["node", str(CLIENT), "--env", str(env_path), "--", "printf 'hello' # comment"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
        finally:
            server.finish()

        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stdout.strip(), "command output")
        transcript = "".join(server.received)
        self.assertIn("0root\r", transcript)
        self.assertIn("0test-password\r", transcript)
        self.assertNotIn("test-password", result.stdout + result.stderr)
        self.assertIn("sh -c", transcript)


if __name__ == "__main__":
    unittest.main()
