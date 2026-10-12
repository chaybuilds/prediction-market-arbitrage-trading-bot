"""Usage: python3 -m oracle15 --once | --loop | --status"""
import argparse
import json
import os
from pathlib import Path
import sys
import time

from .ledger import Ledger
from .model import Settings
from .runner import Runner


def main():
    parser = argparse.ArgumentParser(description="ORACLE Polymarket US BTC 15m")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--once", action="store_true", help="Fetch one forward paper-trading observation")
    action.add_argument("--loop", action="store_true", help="Run autonomous paper feed every 30s")
    action.add_argument("--status", action="store_true", help="Local ledger status (no network)")
    parser.add_argument("--state-dir", default=os.getenv("ORACLE15_STATE_DIR", "state/oracle15"))
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--live", action="store_true", help="Unavailable: fails closed; live adapter uncertified")
    args = parser.parse_args()
    if args.live:
        parser.error("LIVE_LOCKED: no authenticated trading/preview/fill-reconciliation certification")
    if args.interval < 15 or args.interval > 300:
        parser.error("interval must be between 15 and 300 seconds")
    settings = Settings()
    state_dir = Path(args.state_dir)
    if args.status:
        db = Ledger(state_dir / "paper.sqlite3")
        try:
            print(json.dumps({"mode": "PAPER_ONLY", "risk": db.stats(settings),
                              "kill_switch": (state_dir / "KILL").exists()}, indent=2))
        finally:
            db.close()
        return 0
    # POSIX advisory lock prevents parallel paper runners after relaunch.
    state_dir.mkdir(parents=True, exist_ok=True)
    lock = (state_dir / "runner.lock").open("a+")
    try:
        import fcntl
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (ImportError, BlockingIOError):
        print("A runner is already active (or this platform does not support locks).", file=sys.stderr)
        return 2
    runner = Runner(state_dir, settings=settings)
    try:
        while True:
            try:
                print(json.dumps(runner.tick(), separators=(",", ":")), flush=True)
            except Exception as exc:
                print(json.dumps({"mode": "PAPER_ONLY", "status": "ERROR_FAIL_CLOSED",
                                  "error_type": type(exc).__name__}), flush=True)
            if not args.loop:
                return 0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0
    finally:
        runner.close()
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
