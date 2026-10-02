"""Updating the Pi's operating system (apt update + upgrade) in the
background, with the log available to the page while it runs."""
import os
import subprocess
import threading
import time

_state = {"running": False, "ok": None, "started": 0, "finished": 0, "log": [], "reboot": False}
_lock = threading.Lock()


def status():
    with _lock:
        return {**_state, "log": _state["log"][-40:],
                "reboot": os.path.exists("/var/run/reboot-required")}


def start():
    with _lock:
        if _state["running"]:
            return False
        _state.update(running=True, ok=None, started=time.time(), finished=0, log=[])
    threading.Thread(target=_run, daemon=True, name="apt").start()
    return True


def _append(line):
    with _lock:
        _state["log"].append(line.rstrip())
        del _state["log"][:-400]


def _run():
    env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive"}
    ok = True
    for cmd in (["sudo", "-n", "apt-get", "update"],
                ["sudo", "-n", "apt-get", "-y", "-o", "Dpkg::Options::=--force-confdef",
                 "-o", "Dpkg::Options::=--force-confold", "upgrade"],
                ["sudo", "-n", "apt-get", "-y", "autoremove"]):
        _append("$ " + " ".join(cmd[2:]))
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
            for line in p.stdout:
                _append(line)
            p.wait()
            if p.returncode != 0:
                _append(f"(exit code {p.returncode})")
                ok = False
                break
        except OSError as e:
            _append(f"could not run apt-get: {e}")
            ok = False
            break
    _append("Done." if ok else "Update failed — see above.")
    with _lock:
        _state.update(running=False, ok=ok, finished=time.time())
