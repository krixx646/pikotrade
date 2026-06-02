"""
TradingView Automation Loop

Runs the trading agent on a schedule and updates TradingView
when the generated Pine Script changes.

Usage:
    python scripts/run_tradingview_loop.py [--interval 300] [--use-ai] [--use-gemma] [--ai-limit 3]

Press Ctrl+C to stop.
"""
import argparse
import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PINE_FILE = PROJECT_ROOT / "outputs" / "tradingview" / "market_agent_zones.pine"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the trading agent loop and update TradingView Pine Editor."
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=300,
        help="Seconds between agent runs (default: 300 = 5 minutes)",
    )
    parser.add_argument(
        "--use-ai",
        action="store_true",
        default=False,
        help="Enable DeepSeek AI analysis (higher API cost)",
    )
    parser.add_argument("--use-gemma", action="store_true", default=False)
    parser.add_argument(
        "--ai-limit",
        type=int,
        default=0,
        help="Max instruments to send to AI per cycle (default: 0 = no AI)",
    )
    parser.add_argument(
        "--symbol",
        default="EURJPY",
        help="Default TradingView symbol (e.g., EURUSD, GBPJPY, XAUUSD)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        default=False,
        help="Run one cycle and exit (no loop)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    print("=" * 60)
    print("TradingView Automation Loop")
    print(f"  Interval: {args.interval}s ({args.interval // 60} min)")
    print(f"  AI: {'DeepSeek' if args.use_ai else 'No'} | Gemma: {'Yes' if args.use_gemma else 'No'} | Limit: {args.ai_limit}")
    print(f"  Symbol: {args.symbol}")
    print(f"  Mode: {'Single cycle' if args.once else 'Continuous loop'}")
    print("=" * 60)

    cycle = 0
    last_hash = _read_hash()

    while True:
        cycle += 1
        print(f"\n--- Cycle {cycle} ({_time_now()}) ---")

        # Step 1: Run the agent
        print("[1/5] Running trading agent...")
        agent_ok = _run_agent(args.use_ai, args.use_gemma, args.ai_limit)
        if not agent_ok:
            print("[WARN] Agent command returned non-zero. Will still try to update.")
            pass

        # Step 2: Verify Pine file exists
        print("[2/5] Checking Pine Script...")
        if not PINE_FILE.exists():
            print("[SKIP] No Pine Script generated. Skipping TradingView update.")
            if args.once:
                break
            time.sleep(args.interval)
            continue

        # Step 3: Hash comparison
        current_hash = _hash_file(PINE_FILE)
        if current_hash and current_hash == last_hash:
            print(f"[3/5] Pine Script unchanged (hash: {current_hash[:12]}...). Skipping update.")
            if args.once:
                break
            time.sleep(args.interval)
            continue

        if current_hash:
            print(f"[3/5] Pine Script changed (new hash: {current_hash[:12]}...). Updating TradingView.")
        else:
            print("[3/5] First run or hash unavailable. Updating TradingView.")

        # Step 4: Update TradingView
        print(f"[4/5] Updating TradingView for {args.symbol}...")
        tv_ok = _update_tradingview(args.symbol)
        if tv_ok:
            last_hash = current_hash
            _write_hash(current_hash or "")
            print("[5/5] TradingView updated successfully.")
        else:
            print("[5/5] TradingView update failed or was blocked.")
            print("[INFO] Check TradingView manually. Press Ctrl+C to stop loop.")

        if args.once:
            break

        print(f"\n[WAIT] Sleeping for {args.interval}s ({args.interval // 60} min)...")
        print("[TIP] Press Ctrl+C to stop monitoring.\n")
        time.sleep(args.interval)

    return 0


def _run_agent(use_ai: bool, use_gemma: bool, ai_limit: int) -> bool:
    """Run the one-cycle agent command."""
    py = sys.executable
    args = [py, "scripts/run_always_on.py", "--once"]
    if use_ai:
        args.append("--use-ai")
    if use_gemma:
        args.append("--use-gemma")
    args.extend(["--ai-limit", str(ai_limit)])

    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT / "src")

    result = subprocess.run(
        args,
        cwd=str(PROJECT_ROOT),
        env=env,
        capture_output=True,
        text=False,
        timeout=300,
    )

    # Print key output lines
    for line in result.stdout.decode("utf-8", errors="replace").splitlines():
        if any(kw in line for kw in ["Pine script:", "Pine zones:", "alert(s)", "ERROR"]):
            print(f"  {line}")

    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace")[:500]
        if stderr.strip():
            print(f"  [STDERR] {stderr}")

    return result.returncode == 0


def _update_tradingview(symbol: str) -> bool:
    """Run the TradingView browser update script."""
    py = sys.executable
    args = [
        py,
        "scripts/update_tradingview_pine.py",
        "--symbol", symbol,
        "--force",
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT / "src")

    result = subprocess.run(
        args,
        cwd=str(PROJECT_ROOT),
        env=env,
        capture_output=True,
        text=False,
        timeout=120,
    )

    output = result.stdout.decode("utf-8", errors="replace")
    for line in output.splitlines():
        print(f"  {line}")

    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace")[:500]
        if stderr.strip():
            print(f"  [STDERR] {stderr}")
        return False

    return "[OK]" in output or "successfully" in output.lower()


def _hash_file(path: Path) -> str | None:
    """Return SHA-256 hash of file contents."""
    try:
        content = path.read_bytes()
        return hashlib.sha256(content).hexdigest()
    except Exception:
        return None


def _read_hash() -> str | None:
    """Read previously saved hash."""
    hash_path = PROJECT_ROOT / "outputs" / "tradingview" / ".pine_hash"
    try:
        return hash_path.read_text(encoding="utf-8").strip()
    except Exception:
        return None


def _write_hash(hash_value: str) -> None:
    """Save current hash for next comparison."""
    hash_path = PROJECT_ROOT / "outputs" / "tradingview" / ".pine_hash"
    try:
        hash_path.parent.mkdir(parents=True, exist_ok=True)
        hash_path.write_text(hash_value, encoding="utf-8")
    except Exception as e:
        print(f"[WARN] Could not save hash: {e}")


def _time_now() -> str:
    return time.strftime("%H:%M:%S")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n[STOP] Monitoring stopped by user.")
        raise SystemExit(0)
