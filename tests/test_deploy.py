"""
Tests for robot_bridge/deploy.py using a fake paramiko client and a local HTTP server.
"""

import importlib.util
import json
import sys
import threading
import types
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

DEPLOY_PATH = Path(__file__).resolve().parents[1] / "robot_bridge" / "deploy.py"
REMOTE_DIR = "/home/nao/pepper_bridge"


class FakeChannel:
    def __init__(self, rc=0):
        self.rc = rc

    def recv_exit_status(self):
        return self.rc


class FakeStream:
    def __init__(self, data=b"", rc=0):
        self._data = data
        self.channel = FakeChannel(rc)

    def read(self):
        return self._data


class FakeSSHClient:
    """Records commands; answers a few of them like the robot would."""

    instances = []

    def __init__(self):
        self.commands = []
        self.uploaded = []
        self.closed = False
        self.running = False
        FakeSSHClient.instances.append(self)

    def set_missing_host_key_policy(self, policy):
        pass

    def connect(self, host, username=None, password=None, timeout=None, look_for_keys=None, allow_agent=None):
        self.host = host

    def exec_command(self, cmd):
        self.commands.append(cmd)
        out = b""
        if "nohup python pepper_bridge.py" in cmd:
            assert cmd.startswith(f"cd {REMOTE_DIR}; "), "must not use 'cd && ... &' (wrong $!)"
            self.running = True
            out = b"4242\n"
        elif cmd.startswith("pgrep -f '[p]epper_bridge.py'"):
            out = b"4242\n" if self.running else b""
        elif cmd.startswith("pgrep -f pepper_bridge.py"):  # a bare pattern matches the shell itself
            out = b"999\n"
        elif cmd.startswith("pkill"):
            self.running = False
        elif cmd.startswith("tail"):
            out = b"INFO Bridge listening on http://0.0.0.0:8888\n"
        return FakeStream(), FakeStream(out), FakeStream()

    def open_sftp(self):
        client = self

        class Sftp:
            def put(self, local, remote):
                client.uploaded.append((local, remote))

            def close(self):
                pass

        return Sftp()

    def close(self):
        self.closed = True


@pytest.fixture(scope="module")
def deploy():
    fake_paramiko = types.ModuleType("paramiko")
    fake_paramiko.SSHClient = FakeSSHClient
    fake_paramiko.AutoAddPolicy = object
    fake_paramiko.SSHException = Exception
    saved = sys.modules.get("paramiko")
    sys.modules["paramiko"] = fake_paramiko
    try:
        spec = importlib.util.spec_from_file_location("deploy_under_test", DEPLOY_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        if saved is not None:
            sys.modules["paramiko"] = saved
        else:
            sys.modules.pop("paramiko", None)
    module.HEALTH_TIMEOUT = 2.0
    module.SETTLE_SECONDS = 0.01
    module.DEAD_AFTER = 0.5
    return module


@pytest.fixture
def health_server():
    """A local /health endpoint that requires the right API key."""

    connecting = {"left": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/health":
                self.send_response(404)
                self.end_headers()
                return
            if connecting["left"] > 0:
                connecting["left"] -= 1
                body = json.dumps({"ok": False, "error": "naoqi_connecting", "naoqi_connected": False}).encode()
                self.send_response(503)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
                return
            if self.headers.get("X-API-Key") != "k":
                body = json.dumps({"ok": False, "error": "unauthorized"}).encode()
                self.send_response(401)
            else:
                body = json.dumps(
                    {"ok": True, "robot_name": "Pepper", "naoqi": "2.5.10.7", "version": "2.1.0"}
                ).encode()
                self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    server.connecting = connecting
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()


def make_args(deploy, port, **overrides):
    ns = types.SimpleNamespace(
        host="127.0.0.1",
        user="nao",
        password="nao",
        port=port,
        api_key="k",
        restart=False,
        stop=False,
        logs=False,
        status=False,
        lines=20,
    )
    for k, v in overrides.items():
        setattr(ns, k, v)
    return ns


class TestWaitForHealth:

    def test_returns_health_json(self, deploy, health_server):
        data = deploy.wait_for_health("127.0.0.1", health_server.server_address[1], "k", timeout=5)
        assert data["ok"] and data["robot_name"] == "Pepper"

    def test_wrong_key_times_out(self, deploy, health_server, capsys):
        assert deploy.wait_for_health("127.0.0.1", health_server.server_address[1], "wrong", timeout=2) is None
        assert "401" in capsys.readouterr().out

    def test_no_server(self, deploy):
        assert deploy.wait_for_health("127.0.0.1", 1, "", timeout=1.5) is None

    def test_waits_while_naoqi_is_connecting(self, deploy, health_server, capsys):
        health_server.connecting["left"] = 2
        data = deploy.wait_for_health("127.0.0.1", health_server.server_address[1], "k", timeout=10)
        assert data and data["ok"]

    def test_gives_up_quickly_when_process_died(self, deploy, capsys):
        FakeSSHClient.instances.clear()
        client = FakeSSHClient()  # running=False -> "not running"
        started = __import__("time").time()
        assert deploy.wait_for_health("127.0.0.1", 1, "", timeout=30, client=client) is None
        assert __import__("time").time() - started < 10
        assert "not running" in capsys.readouterr().out


class TestDeployFlow:

    def test_full_deploy(self, deploy, health_server, capsys):
        FakeSSHClient.instances.clear()
        rc = deploy.deploy(make_args(deploy, health_server.server_address[1]))
        client = FakeSSHClient.instances[-1]
        assert rc == 0
        assert client.uploaded == [(deploy.BRIDGE_FILE, deploy.REMOTE_SCRIPT)]
        joined = "\n".join(client.commands)
        assert "pkill -f '[p]epper_bridge.py'" in joined
        assert f"--port={health_server.server_address[1]} --api-key=k" in joined
        assert f"echo 4242 > {deploy.PID_FILE}" in joined
        assert client.closed
        assert "Bridge is healthy: robot=Pepper" in capsys.readouterr().out

    def test_restart_does_not_upload(self, deploy, health_server):
        FakeSSHClient.instances.clear()
        assert deploy.deploy(make_args(deploy, health_server.server_address[1], restart=True)) == 0
        assert FakeSSHClient.instances[-1].uploaded == []

    def test_failed_start_prints_log(self, deploy, capsys):
        FakeSSHClient.instances.clear()
        rc = deploy.deploy(make_args(deploy, 1))  # nothing listens on port 1
        assert rc == 1
        out = capsys.readouterr().out
        assert "bridge.log" in out and "Bridge listening" in out

    def test_stop_status_logs(self, deploy, health_server, capsys):
        FakeSSHClient.instances.clear()
        assert deploy.deploy(make_args(deploy, health_server.server_address[1], logs=True)) == 0
        assert "Bridge listening" in capsys.readouterr().out
        assert deploy.deploy(make_args(deploy, health_server.server_address[1], stop=True)) == 0
        assert "Bridge stopped" in capsys.readouterr().out
        rc = deploy.deploy(make_args(deploy, health_server.server_address[1], status=True))
        out = capsys.readouterr().out
        assert "not running" in out and rc == 1
