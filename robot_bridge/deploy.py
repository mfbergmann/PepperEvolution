#!/usr/bin/env python3
"""
Deploy the Pepper Bridge Server to the robot via SSH/SCP.

Usage:
    python robot_bridge/deploy.py [--host 10.0.100.100] [--user nao] [--password nao]
                                  [--port 8888] [--api-key SECRET]

Copies pepper_bridge.py to the robot and starts it via nohup.
"""

import argparse
import os
import sys
import time

try:
    import paramiko
except ImportError:
    print("paramiko is required: pip install paramiko")
    sys.exit(1)


BRIDGE_FILE = os.path.join(os.path.dirname(__file__), "pepper_bridge.py")
REMOTE_DIR = "/home/nao/pepper_bridge"
REMOTE_SCRIPT = REMOTE_DIR + "/pepper_bridge.py"
PID_FILE = REMOTE_DIR + "/bridge.pid"


def connect(host, user, password):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, username=user, password=password, timeout=10)
    return client


def run_cmd(client, cmd, check=False):
    stdin, stdout, stderr = client.exec_command(cmd)
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    rc = stdout.channel.recv_exit_status()
    if check and rc != 0:
        raise RuntimeError(f"Command failed ({rc}): {cmd}\n{err}")
    return out, err, rc


def deploy(host, user, password, bridge_port, api_key):
    print(f"Connecting to {user}@{host}...")
    client = connect(host, user, password)

    # Stop existing bridge
    print("Stopping existing bridge (if any)...")
    run_cmd(client, f"cat {PID_FILE} 2>/dev/null | xargs -r kill 2>/dev/null; rm -f {PID_FILE}")
    # Also kill any lingering process
    run_cmd(client, "pkill -f pepper_bridge.py 2>/dev/null")
    time.sleep(1)

    # Create remote directory
    print(f"Creating {REMOTE_DIR}...")
    run_cmd(client, f"mkdir -p {REMOTE_DIR}")

    # Upload bridge script
    print(f"Uploading {BRIDGE_FILE} -> {REMOTE_SCRIPT}...")
    sftp = client.open_sftp()
    sftp.put(BRIDGE_FILE, REMOTE_SCRIPT)
    sftp.close()

    # Start the bridge
    api_key_arg = f"--api-key={api_key}" if api_key else ""
    cmd = (
        f"cd {REMOTE_DIR} && "
        f"nohup python pepper_bridge.py --port={bridge_port} {api_key_arg} "
        f"> bridge.log 2>&1 & echo $!"
    )
    print(f"Starting bridge on port {bridge_port}...")
    pid_out, _, _ = run_cmd(client, cmd)
    pid = pid_out.strip()

    if pid:
        run_cmd(client, f"echo {pid} > {PID_FILE}")
        print(f"Bridge started with PID {pid}")
    else:
        print("Warning: could not capture PID")

    # Verify it's running
    time.sleep(2)
    out, _, rc = run_cmd(client, f"kill -0 {pid} 2>&1 && echo RUNNING || echo STOPPED")
    if "RUNNING" in out:
        print(f"Bridge is running at http://{host}:{bridge_port}/health")
    else:
        print("Warning: bridge may have failed to start. Check bridge.log on the robot.")
        # Show last lines of log
        log_out, _, _ = run_cmd(client, f"tail -20 {REMOTE_DIR}/bridge.log")
        if log_out:
            print("--- bridge.log ---")
            print(log_out)

    client.close()
    print("Deploy complete.")


def main():
    parser = argparse.ArgumentParser(description="Deploy Pepper Bridge to robot")
    parser.add_argument("--host", default=os.environ.get("PEPPER_IP", "10.0.100.100"))
    parser.add_argument("--user", default=os.environ.get("PEPPER_USER", "nao"))
    parser.add_argument("--password", default=os.environ.get("PEPPER_PASSWORD", "nao"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("BRIDGE_PORT", "8888")))
    parser.add_argument("--api-key", default=os.environ.get("BRIDGE_API_KEY", ""))
    args = parser.parse_args()

    if not os.path.exists(BRIDGE_FILE):
        print(f"Error: {BRIDGE_FILE} not found")
        sys.exit(1)

    deploy(args.host, args.user, args.password, args.port, args.api_key)


if __name__ == "__main__":
    main()
