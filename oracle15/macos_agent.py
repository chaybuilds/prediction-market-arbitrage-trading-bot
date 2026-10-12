"""Reversible user-level macOS LaunchAgent for PAPER-only ORACLE 15M.

No secret access, no order adapter, no elevation. Run as:
python3 -m oracle15.macos_agent install|status|uninstall
"""
import argparse
import os
from pathlib import Path
import plistlib
import subprocess
import sys

LABEL = "com.chaytrades.oracle15-paper"
PLIST = Path.home() / "Library" / "LaunchAgents" / (LABEL + ".plist")
REPO = Path(__file__).resolve().parent.parent


def launchctl(*argv):
    return subprocess.run(["launchctl"] + list(argv), text=True, capture_output=True)


def install():
    if sys.platform != "darwin":
        raise RuntimeError("macOS LaunchAgent only")
    if PLIST.exists():
        raise RuntimeError("LaunchAgent already installed: refuse overwrite")
    state = REPO / "state" / "oracle15"
    state.mkdir(parents=True, exist_ok=True)
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "Label": LABEL,
        "ProgramArguments": [
            sys.executable, "-m", "oracle15", "--loop", "--interval", "30",
            "--state-dir", str(state)
        ],
        "WorkingDirectory": str(REPO),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 20,
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1"},
        "StandardOutPath": str(state / "paper-stdout.log"),
        "StandardErrorPath": str(state / "paper-stderr.log"),
        "ProcessType": "Background",
    }
    with PLIST.open("wb") as fp:
        plistlib.dump(payload, fp)
    PLIST.chmod(0o600)
    result = launchctl("bootstrap", "gui/" + str(os.getuid()), str(PLIST))
    if result.returncode:
        # A failed bootstrap must not leave an orphan LaunchAgent.
        PLIST.unlink(missing_ok=True)
        raise RuntimeError("launchctl bootstrap failed: " + result.stderr[:350])
    print("PAPER LaunchAgent installed:", PLIST)
    print("State:", state)
    print("Stop new paper entries: touch", state / "KILL")
    print("Fully uninstall: python3 -m oracle15.macos_agent uninstall")


def uninstall():
    if sys.platform != "darwin":
        raise RuntimeError("macOS LaunchAgent only")
    target = "gui/" + str(os.getuid()) + "/" + LABEL
    result = launchctl("bootout", target)
    if PLIST.exists():
        PLIST.unlink()
    print("LaunchAgent stopped/uninstalled", "launchctl=" + str(result.returncode))
    print("The paper-trade ledger is preserved.")


def status():
    target = "gui/" + str(os.getuid()) + "/" + LABEL
    if not PLIST.exists():
        print("not installed")
        return 1
    result = launchctl("print", target)
    print("installed:", PLIST)
    if result.returncode:
        print("not running:", result.stderr[:500])
        return 2
    for line in result.stdout.splitlines():
        if any(key in line for key in ("state =", "pid =", "last exit code", "program =", "path =")):
            print(line.strip())
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["install", "status", "uninstall"])
    args = parser.parse_args()
    try:
        if args.command == "install":
            install()
        elif args.command == "uninstall":
            uninstall()
        else:
            return status()
    except (OSError, RuntimeError) as exc:
        print(type(exc).__name__ + ": " + str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
