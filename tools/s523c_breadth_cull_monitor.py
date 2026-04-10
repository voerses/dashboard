"""
s523c Mission P breadth direction cull — SHADOW MODE sidecar.

Reads the live paper runner's state.json, computes whether the Mission P
breadth-cull rule WOULD fire, and logs what it would close. Does NOT actually
execute any closes.

Validated parameters (Mission P Gate 1, 2026-04-08):
  - Grace: 3 days post-entry (must be ≥3d held)
  - Trigger: ≥80% of one direction's post-grace open positions individually underwater
  - Action (would-be): close 100% of that direction's post-grace positions
  - Throttle: 14 days between culls per direction

Usage:
  /workspace/venv/bin/python tools/s523c_breadth_cull_monitor.py
  /workspace/venv/bin/python tools/s523c_breadth_cull_monitor.py --strategy s523c
  /workspace/venv/bin/python tools/s523c_breadth_cull_monitor.py --execute  # NOT IMPLEMENTED YET — shadow only

Output:
  - Console summary of the current portfolio
  - Append to state/v4_paper_s523c/breadth_cull_shadow_log.jsonl

To enable actual execution (Gate 4 paper trading), Phase 1 of the position
management CLI must be implemented first (--close TOKEN command). See:
.specs/active/position-management-cli/brief.md
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add repo root to sys.path so `from v4.position_commands import ...` works
# when run as a script (Python only adds the script's own dir to sys.path).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

REPO = Path("/workspace/crypto_backtest")

# Mission P Gate 1 validated defaults
DEFAULT_GRACE_DAYS = 3
DEFAULT_BREADTH = 0.80
DEFAULT_THROTTLE_DAYS = 14
LEVERAGE = 2.6


def load_state(state_dir: Path) -> dict:
    state_path = state_dir / "state.json"
    if not state_path.exists():
        raise FileNotFoundError(f"state.json not found at {state_path}")
    with open(state_path) as f:
        return json.load(f)


def load_throttle(state_dir: Path) -> dict:
    """Sidecar throttle file tracks last_cull per direction across runs."""
    p = state_dir / "breadth_cull_state.json"
    if not p.exists():
        return {"last_cull_long": None, "last_cull_short": None, "history": []}
    with open(p) as f:
        return json.load(f)


def save_throttle(state_dir: Path, throttle: dict) -> None:
    p = state_dir / "breadth_cull_state.json"
    with open(p, "w") as f:
        json.dump(throttle, f, indent=2)


def append_shadow_log(state_dir: Path, entry: dict) -> None:
    p = state_dir / "breadth_cull_shadow_log.jsonl"
    with open(p, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def hours_held(entry_ts_str: str, now_ts_str: str) -> float:
    if not entry_ts_str:
        return 0.0
    entry = parse_iso(entry_ts_str)
    now = parse_iso(now_ts_str)
    return (now - entry).total_seconds() / 3600


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", default="s523c", help="Strategy name (folder under state/)")
    ap.add_argument("--state-dir", default=None, help="Override state directory path")
    ap.add_argument("--grace-days", type=int, default=DEFAULT_GRACE_DAYS)
    ap.add_argument("--breadth", type=float, default=DEFAULT_BREADTH)
    ap.add_argument("--throttle-days", type=int, default=DEFAULT_THROTTLE_DAYS)
    ap.add_argument("--execute", action="store_true", help="NOT YET IMPLEMENTED — shadow mode only")
    args = ap.parse_args()

    # --execute now supported via the new position management CLI Phase 1.
    # When set, the sidecar will write close commands to the pool's commands.jsonl
    # for each token in the action list. The runner picks them up within ~1s.
    # SAFETY: requires the runner to be restarted with the new run_paper_multi.py
    # code that includes the position_commands hook in the main loop.

    state_dir = Path(args.state_dir) if args.state_dir else (REPO / f"state/v4_paper_{args.strategy}")
    if not state_dir.exists():
        print(f"ERROR: state dir not found: {state_dir}")
        return

    state = load_state(state_dir)
    throttle = load_throttle(state_dir)

    now_ts = state["last_timestamp"]
    now = parse_iso(now_ts)
    grace_hours = args.grace_days * 24

    open_positions = state["open_positions"]
    last_prices = state["last_known_prices"]

    # Filter to s523c positions only (the state may contain other strategies)
    s523_positions = [p for p in open_positions if p.get("strategy_id", "").startswith("s523")]

    print(f"\n{'=' * 80}")
    print(f"s523c Mission P Breadth Cull Monitor — SHADOW MODE")
    print(f"{'=' * 80}")
    print(f"State dir:    {state_dir}")
    print(f"Now:          {now_ts}")
    print(f"Open posns:   {len(s523_positions)} (s523c)")
    print(f"Params:       grace={args.grace_days}d  breadth={args.breadth:.0%}  throttle={args.throttle_days}d")
    print()

    # ---- Build position table with current MTM and grace status ----
    posn_rows = []
    for p in s523_positions:
        token = p["token"]
        direction = p["direction"]
        entry_price = p["entry_price"]
        margin = p["margin_usd"]
        entry_ts = p.get("entry_timestamp", "")
        cur_price = last_prices.get(token)
        if cur_price is None:
            continue
        held_h = hours_held(entry_ts, now_ts)
        held_d = held_h / 24
        post_grace = held_h >= grace_hours
        cur_ret = (cur_price - entry_price) / entry_price * direction
        cur_ret_lev = cur_ret * LEVERAGE
        underwater = cur_ret < 0

        posn_rows.append({
            "token": token,
            "direction": direction,
            "entry_price": entry_price,
            "current_price": cur_price,
            "margin_usd": margin,
            "entry_timestamp": entry_ts,
            "held_days": held_d,
            "post_grace": post_grace,
            "current_ret_unlev": cur_ret,
            "current_ret_lev_pct": cur_ret_lev * 100,
            "underwater": underwater,
        })

    # ---- Print the portfolio ----
    print(f"{'Token':<8} {'Dir':<5} {'Held':<8} {'PostGr':<7} {'Cur ret':<12} {'Lev MTM':<11} {'UW':<4}")
    print("-" * 70)
    for r in posn_rows:
        dir_str = "LONG" if r["direction"] == 1 else "SHORT"
        post_str = "✓" if r["post_grace"] else "·"
        uw_str = "↓" if r["underwater"] else "↑"
        print(f"{r['token']:<8} {dir_str:<5} {r['held_days']:>5.1f}d  {post_str:<7} {r['current_ret_unlev']:>+10.2%}  {r['current_ret_lev_pct']:>+9.2f}%  {uw_str:<4}")

    # ---- Compute breadth per direction (post-grace only) ----
    long_post = [r for r in posn_rows if r["direction"] == 1 and r["post_grace"]]
    short_post = [r for r in posn_rows if r["direction"] == -1 and r["post_grace"]]
    long_all = [r for r in posn_rows if r["direction"] == 1]
    short_all = [r for r in posn_rows if r["direction"] == -1]

    long_uw_pct = (sum(1 for r in long_post if r["underwater"]) / len(long_post) * 100) if long_post else 0
    short_uw_pct = (sum(1 for r in short_post if r["underwater"]) / len(short_post) * 100) if short_post else 0
    long_uw_pct_all = (sum(1 for r in long_all if r["underwater"]) / len(long_all) * 100) if long_all else 0
    short_uw_pct_all = (sum(1 for r in short_all if r["underwater"]) / len(short_all) * 100) if short_all else 0

    print(f"\n{'=' * 80}")
    print("BREADTH ANALYSIS (post-grace positions only)")
    print(f"{'=' * 80}")
    print(f"LONG  basket:  {len(long_post)}/{len(long_all)} post-grace, {long_uw_pct:.0f}% underwater (all-positions: {long_uw_pct_all:.0f}%)")
    print(f"SHORT basket:  {len(short_post)}/{len(short_all)} post-grace, {short_uw_pct:.0f}% underwater (all-positions: {short_uw_pct_all:.0f}%)")

    # ---- Throttle check ----
    def throttle_elapsed(direction: str) -> tuple[bool, str]:
        last = throttle.get(f"last_cull_{direction}")
        if last is None:
            return True, "never culled"
        last_dt = parse_iso(last)
        delta_d = (now - last_dt).total_seconds() / 86400
        return delta_d >= args.throttle_days, f"{delta_d:.1f}d ago"

    long_th_ok, long_th_str = throttle_elapsed("long")
    short_th_ok, short_th_str = throttle_elapsed("short")

    print(f"\nThrottle:  long {long_th_str} ({'OK' if long_th_ok else 'BLOCKED'})  short {short_th_str} ({'OK' if short_th_ok else 'BLOCKED'})")

    # ---- Decide actions ----
    print(f"\n{'=' * 80}")
    print("RULE EVALUATION")
    print(f"{'=' * 80}")

    actions = []
    rule_min_basket = 5  # need at least 5 positions in a direction to compute breadth

    for direction, label, post_list, uw_pct, throttle_ok, throttle_str in [
        (1, "LONG", long_post, long_uw_pct, long_th_ok, long_th_str),
        (-1, "SHORT", short_post, short_uw_pct, short_th_ok, short_th_str),
    ]:
        if len(post_list) < rule_min_basket:
            print(f"{label}: SKIP — only {len(post_list)} post-grace positions (need ≥{rule_min_basket})")
            continue
        if uw_pct < args.breadth * 100:
            print(f"{label}: NO ACTION — {uw_pct:.0f}% underwater < {args.breadth*100:.0f}% threshold")
            continue
        if not throttle_ok:
            print(f"{label}: BLOCKED BY THROTTLE — last cull {throttle_str}")
            continue
        # FIRE!
        tokens_to_close = [r["token"] for r in post_list]
        action = {
            "direction": label,
            "breadth_pct": uw_pct,
            "n_positions": len(post_list),
            "tokens": tokens_to_close,
            "fire_timestamp": now_ts,
        }
        actions.append(action)
        print(f"{label}: 🚨 WOULD FIRE — breadth {uw_pct:.0f}% ≥ {args.breadth*100:.0f}%, throttle OK")
        print(f"   Would close: {', '.join(tokens_to_close)}")

    # ---- Persist throttle if any actions ----
    log_entry = {
        "timestamp": now_ts,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "params": {
            "grace_days": args.grace_days,
            "breadth": args.breadth,
            "throttle_days": args.throttle_days,
        },
        "n_positions_total": len(posn_rows),
        "long_post_grace": len(long_post),
        "short_post_grace": len(short_post),
        "long_breadth_uw_pct": long_uw_pct,
        "short_breadth_uw_pct": short_uw_pct,
        "actions_would_take": actions,
        "shadow_mode": True,
    }
    append_shadow_log(state_dir, log_entry)

    if actions:
        if args.execute:
            print(f"\n🚀 EXECUTE MODE — queueing close commands via position_commands…")
            from v4.position_commands import write_command
            # Pre-execute state backup. Copy state.json + trades.jsonl to a
            # timestamped backup dir BEFORE any close commands are written.
            # This lets us roll back if the cull closes wrong positions.
            # Uses PERSISTENT storage under state/backups/, NOT /tmp (ephemeral).
            import shutil
            backup_root = REPO / "state/backups/breadth_cull_pre_execute"
            backup_root.mkdir(parents=True, exist_ok=True)
            backup_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup_dir = backup_root / f"{state_dir.name}_{backup_ts}"
            backup_dir.mkdir(exist_ok=True)
            backed_up_files: list[str] = []
            for fname in ("state.json", "equity.csv", "trades.jsonl", "heartbeat.json", "config.json"):
                src = state_dir / fname
                if src.exists():
                    shutil.copy(src, backup_dir / fname)
                    backed_up_files.append(fname)
            print(f"  ✓ Pre-execute backup: {backup_dir}")
            print(f"    Files: {', '.join(backed_up_files)}")

            # Rotate: keep last 20 backups, delete older
            all_backups = sorted([d for d in backup_root.iterdir() if d.is_dir() and d.name.startswith(state_dir.name)])
            if len(all_backups) > 20:
                for old in all_backups[:-20]:
                    shutil.rmtree(old, ignore_errors=True)
                print(f"  (rotated {len(all_backups)-20} older backups)")
            for action in actions:
                # Update throttle BEFORE issuing commands so retries don't double-fire
                throttle[f"last_cull_{action['direction'].lower()}"] = now_ts
                throttle["history"].append({
                    "timestamp": now_ts,
                    "direction": action["direction"],
                    "tokens": action["tokens"],
                    "breadth": action["breadth_pct"],
                })
                min_hours = args.grace_days * 24
                for token in action["tokens"]:
                    # force=True: override strategy's no_stop_bars (7-day) because
                    # Mission P is a portfolio-level regime signal, not a price-stop.
                    # min_hours_held=72: NEVER close a new entry (<3d old) even if it
                    # shares a token with an older position being culled. Defense in depth.
                    cmd_id = write_command(state_dir, {
                        "type": "close",
                        "token": token,
                        "force": True,
                        "min_hours_held": min_hours,
                        "reason": "breadth_cull",
                    })
                    print(f"  Queued close {token} → cmd id={cmd_id} (min_hours_held={min_hours})")
            save_throttle(state_dir, throttle)
            print(f"\n✓ {len(actions)} action(s) executed. Runner will process within ~1s.")
        else:
            print(f"\n⚠️  In SHADOW mode — no actual closes executed. {len(actions)} action(s) logged.")
            print(f"   To enable execution, re-run with --execute (requires runner restart with new code).")
    else:
        print(f"\n✓ No action required at this evaluation.")

    print(f"\nLog appended to: {state_dir}/breadth_cull_shadow_log.jsonl")


if __name__ == "__main__":
    main()
