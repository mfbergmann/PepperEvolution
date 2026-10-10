#!/usr/bin/env python3
"""
Deploy, restart, stop or inspect the Pepper Bridge Server over SSH.

Usage:
    python robot_bridge/deploy.py                 # upload + (re)start, then verify /health
    python robot_bridge/deploy.py --restart       # restart what is already on the robot
    python robot_bridge/deploy.py --stop          # stop the bridge
    python robot_bridge/deploy.py --logs [-n 50]  # tail the bridge log on the robot
    python robot_bridge/deploy.py --status        # is it running? does /health answer?

Connection details come from flags or the environment (.env is loaded):
    PEPPER_IP, PEPPER_USER, PEPPER_PASSWORD, BRIDGE_PORT, BRIDGE_API_KEY
"""

import argparse
import io
import json
import os
import shlex
import sys
import time
import zipfile
import urllib.error
import urllib.request

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # dotenv is optional for the deploy script
    pass

try:
    import paramiko
except ImportError:
    print("paramiko is required: pip install paramiko")
    sys.exit(1)


HERE = os.path.dirname(os.path.abspath(__file__))
BRIDGE_FILE = os.path.join(HERE, "pepper_bridge.py")
LAUNCH_FILE = os.path.join(HERE, "launch.sh")  # starts the bridge itself
WATCHDOG_FILE = os.path.join(HERE, "watchdog.sh")  # the one way the bridge is started (here and at boot), #15
AUTOSTART_DIR = os.path.join(HERE, "autostart")  # NAOqi package whose service runs launch.sh at boot
AUTOSTART_UUID = "pepper-bridge-autostart"
REMOTE_PKG = "/tmp/" + AUTOSTART_UUID + ".pkg"
REMOTE_DIR = "/home/nao/pepper_bridge"
# NAOqi's Python bindings are only on the path in a login shell; a plain ssh exec does not source it.
NAOQI_ENV = "PYTHONPATH=/opt/aldebaran/lib/python2.7/site-packages${PYTHONPATH:+:$PYTHONPATH}"
REMOTE_SCRIPT = REMOTE_DIR + "/pepper_bridge.py"
REMOTE_LOG = REMOTE_DIR + "/bridge.log"
PID_FILE = REMOTE_DIR + "/bridge.pid"
REMOTE_LAUNCH = REMOTE_DIR + "/launch.sh"
REMOTE_WATCHDOG = REMOTE_DIR + "/watchdog.sh"
WATCHDOG_PID = REMOTE_DIR + "/watchdog.pid"
WATCHDOG_STOP = REMOTE_DIR + "/watchdog.stop"
WATCHDOG_LOG = REMOTE_DIR + "/watchdog.log"
REMOTE_ENV = REMOTE_DIR + "/bridge.env"
HEALTH_TIMEOUT = 150.0  # bridge retries NAOqi for ~120 s after a robot boot; give it time
DEAD_AFTER = 30.0  # seconds without any /health answer before we check whether the process died
SETTLE_SECONDS = 1.0
STOP_WAIT_SECONDS = 10  # the bridge rests the robot on SIGTERM before exiting


def connect(host, user, password):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, username=user, password=password, timeout=10, look_for_keys=False, allow_agent=False)
    return client


def run_cmd(client, cmd, check=False):
    _, stdout, stderr = client.exec_command(cmd)
    out = stdout.read().decode(errors="replace").strip()
    err = stderr.read().decode(errors="replace").strip()
    rc = stdout.channel.recv_exit_status()
    if check and rc != 0:
        raise RuntimeError(f"Command failed ({rc}): {cmd}\n{err}")
    return out, err, rc


def stop_bridge(client):
    print("Stopping bridge (if running)...")
    # first the watchdog, so it does not start the bridge again (watchdog.stop also covers a watchdog mid-restart)
    run_cmd(client, f"touch {WATCHDOG_STOP}; cat {WATCHDOG_PID} 2>/dev/null | xargs -r kill 2>/dev/null")
    run_cmd(client, f"rm -f {WATCHDOG_PID}")
    run_cmd(client, f"cat {PID_FILE} 2>/dev/null | xargs -r kill 2>/dev/null; rm -f {PID_FILE}")
    run_cmd(client, "pkill -f '[p]epper_bridge.py' 2>/dev/null")  # [p] so pkill does not match this shell
    # The bridge rests the robot before it exits, which can take several seconds; starting the new one
    # earlier fails with "Address already in use". Wait, then force it.
    for _ in range(STOP_WAIT_SECONDS * 2):
        _, _, rc = run_cmd(client, "pgrep -f '[p]epper_bridge.py' >/dev/null", check=False)
        if rc != 0:
            break
        time.sleep(0.5)
    else:
        print(f"Bridge still running after {STOP_WAIT_SECONDS}s; forcing it to stop")
        run_cmd(client, "pkill -9 -f '[p]epper_bridge.py' 2>/dev/null", check=False)
    time.sleep(SETTLE_SECONDS)


def upload(client):
    run_cmd(client, f"mkdir -p {REMOTE_DIR}")
    # Keep one copy of whatever bridge was there before (e.g. an earlier version) instead of losing it.
    run_cmd(client, f"test -f {REMOTE_SCRIPT} && cp -p {REMOTE_SCRIPT} {REMOTE_SCRIPT}.previous || true")
    print(f"Uploading {os.path.basename(BRIDGE_FILE)} -> {REMOTE_SCRIPT}")
    sftp = client.open_sftp()
    try:
        sftp.put(BRIDGE_FILE, REMOTE_SCRIPT)
        sftp.put(LAUNCH_FILE, REMOTE_LAUNCH)
        sftp.put(WATCHDOG_FILE, REMOTE_WATCHDOG)
    finally:
        sftp.close()


def write_settings(client, port, api_key):
    """bridge.env: what launch.sh starts the bridge with, now and at the next boot."""
    lines = [f"PORT={int(port)}"]
    if api_key:
        lines.append(f"API_KEY={shlex.quote(api_key)}")
    body = "\n".join(lines) + "\n"
    run_cmd(client, f"umask 077; printf %s {shlex.quote(body)} > {REMOTE_ENV}")


def build_autostart_package():
    """The NAOqi package (a zip) whose autorun service starts the bridge at boot."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as pkg:
        for name in ("manifest.xml", "service.sh"):
            pkg.write(os.path.join(AUTOSTART_DIR, name), name)
    return buf.getvalue()


def install_autostart(client):
    print("Installing the autostart package (ALServiceManager starts the bridge at boot)...")
    sftp = client.open_sftp()
    try:
        with sftp.open(REMOTE_PKG, "wb") as fh:
            fh.write(build_autostart_package())
    finally:
        sftp.close()
    out, err, rc = run_cmd(client, f"{NAOQI_ENV} qicli call PackageManager.install {REMOTE_PKG} 2>&1")
    run_cmd(client, f"rm -f {REMOTE_PKG}")
    print(out or err)
    if rc != 0 or "false" in out.lower() or "error" in out.lower():
        print("Autostart package NOT installed")
        return False
    print(f"Installed {AUTOSTART_UUID}; the bridge will start by itself after the next boot")
    return True


def remove_autostart(client):
    out, err, _ = run_cmd(client, f"{NAOQI_ENV} qicli call PackageManager.removePkg {AUTOSTART_UUID} 2>&1")
    print(out or err)
    return "true" in out.lower()


def start_bridge(client, port, api_key):
    write_settings(client, port, api_key)
    # watchdog.sh runs launch.sh (which writes the bridge's PID and log) and starts it again if it dies (#15).
    # "cd DIR; cmd &" (not "cd && cmd &"): with && the job is a subshell and $! is not the launcher's PID.
    cmd = f"cd {REMOTE_DIR}; nohup /bin/sh watchdog.sh > /dev/null 2>&1 < /dev/null & echo $!"
    print(f"Starting bridge on port {port}...")
    pid_out, _, _ = run_cmd(client, cmd)
    pid = pid_out.strip().splitlines()[-1] if pid_out.strip() else ""
    if pid:
        print(f"Bridge started with PID {pid}")
    else:
        print("Warning: could not capture PID")
    return pid


def is_running(client):
    out, _, _ = run_cmd(client, "pgrep -f '[p]epper_bridge.py' || true")  # [p] excludes this shell
    return bool(out.strip())


def tail_log(client, lines):
    out, _, _ = run_cmd(client, f"tail -n {int(lines)} {REMOTE_LOG} 2>/dev/null")
    return out


def wait_for_health(host, port, api_key, timeout=None, client=None):
    """Poll /health until the bridge reports NAOqi connected.

    The bridge answers 503 "naoqi_connecting" while NAOqi is still booting (up to
    ~2 minutes after power-on); no answer at all for DEAD_AFTER seconds means the
    process is probably dead, which we confirm over SSH when ``client`` is given.
    """
    timeout = HEALTH_TIMEOUT if timeout is None else timeout
    url = f"http://{host}:{port}/health"
    headers = {"X-API-Key": api_key} if api_key else {}
    started = time.time()
    deadline = started + timeout
    last_error = None
    last_note = 0.0
    answered = False
    while time.time() < deadline:
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode())
                if data.get("ok"):
                    return data
                last_error = data
                answered = True
        except urllib.error.HTTPError as exc:
            answered = True
            try:
                last_error = json.loads(exc.read().decode())
            except ValueError:
                last_error = f"HTTP {exc.code}"
            if exc.code == 401:
                print("Bridge answered 401: BRIDGE_API_KEY does not match the running bridge")
                return None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            last_error = exc
        elapsed = time.time() - started
        if elapsed - last_note >= 10:
            last_note = elapsed
            state = "waiting for NAOqi" if answered else "waiting for the bridge to listen"
            print(f"  ... {state} ({elapsed:.0f}s)")
        if not answered and client is not None and elapsed > DEAD_AFTER and not is_running(client):
            print("Bridge process is not running on the robot")
            return None
        time.sleep(1.5)
    print(f"Bridge did not become healthy within {timeout:.0f}s (last: {last_error})")
    return None


def deploy(args):
    print(f"Connecting to {args.user}@{args.host}...")
    client = connect(args.host, args.user, args.password)
    try:
        if args.logs:
            print(tail_log(client, args.lines) or "(no log yet)")
            return 0
        if args.status:
            running = is_running(client)
            print(f"Bridge process: {'running' if running else 'not running'}")
            _, _, rc = run_cmd(client, f"kill -0 $(cat {WATCHDOG_PID} 2>/dev/null) 2>/dev/null", check=False)
            print(f"Watchdog: {'running' if rc == 0 else 'not running'}")
            restarts, _, _ = run_cmd(client, f"tail -n 3 {WATCHDOG_LOG} 2>/dev/null", check=False)
            if restarts:
                print("Watchdog log (latest):\n  " + restarts.replace("\n", "\n  "))
            health = wait_for_health(args.host, args.port, args.api_key, timeout=4)
            print(f"/health: {json.dumps(health) if health else 'no answer'}")
            return 0 if running and health else 1
        if args.install_autostart:
            if not run_cmd(client, f"test -f {REMOTE_LAUNCH}", check=False)[2] == 0:
                print("Deploy the bridge first (launch.sh is not on the robot yet)")
                return 1
            return 0 if install_autostart(client) else 1
        if args.remove_autostart:
            return 0 if remove_autostart(client) else 1
        if args.stop:
            stop_bridge(client)
            print("Bridge stopped." if not is_running(client) else "Warning: a bridge process is still alive")
            return 0

        stop_bridge(client)
        if not args.restart or run_cmd(client, f"test -f {REMOTE_WATCHDOG}", check=False)[2] != 0:
            upload(client)
        start_bridge(client, args.port, args.api_key)

        health = wait_for_health(args.host, args.port, args.api_key, client=client)
        if health:
            print(
                f"Bridge is healthy: robot={health.get('robot_name')} naoqi={health.get('naoqi')} "
                f"version={health.get('version')}"
            )
            print(f"  http://{args.host}:{args.port}/health")
            return 0
        print("--- last lines of bridge.log ---")
        print(tail_log(client, 30) or "(empty)")
        return 1
    finally:
        client.close()


def main():
    parser = argparse.ArgumentParser(description="Deploy the Pepper Bridge to the robot")
    parser.add_argument("--host", default=os.environ.get("PEPPER_IP", "10.0.100.100"))
    parser.add_argument("--user", default=os.environ.get("PEPPER_USER", "nao"))
    parser.add_argument("--password", default=os.environ.get("PEPPER_PASSWORD", "nao"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("BRIDGE_PORT", "8888")))
    parser.add_argument("--api-key", default=os.environ.get("BRIDGE_API_KEY", ""))
    parser.add_argument("--restart", action="store_true", help="restart without uploading")
    parser.add_argument("--stop", action="store_true", help="stop the bridge and exit")
    parser.add_argument("--logs", action="store_true", help="print the bridge log and exit")
    parser.add_argument("--status", action="store_true", help="report process + /health state")
    parser.add_argument(
        "--install-autostart", action="store_true", help="install the package that starts the bridge at boot"
    )
    parser.add_argument("--remove-autostart", action="store_true", help="remove the autostart package")
    parser.add_argument("-n", "--lines", type=int, default=50, help="log lines for --logs")
    args = parser.parse_args()

    only_manage = (
        args.restart or args.stop or args.logs or args.status or args.install_autostart or args.remove_autostart
    )
    if not only_manage and not os.path.exists(BRIDGE_FILE):
        print(f"Error: {BRIDGE_FILE} not found")
        sys.exit(1)
    try:
        sys.exit(deploy(args))
    except (paramiko.SSHException, OSError) as exc:
        print(f"SSH error talking to {args.host}: {exc}")
        sys.exit(2)


if __name__ == "__main__":
    main()
