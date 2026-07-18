"""
Wallet performance analysis: P&L, win rate, trade stats, risk score.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

import config
from src.utils.logger import logger
from src.utils.helpers import fmt_sol, fmt_pct, fmt_duration, risk_label, short_address
from src.utils.wallet_manager import get_wallet_summary


async def analyze_wallet(wallet_address: str) -> Dict[str, Any]:
    """
    Return a comprehensive performance report for any wallet address.
    Uses trade history from our DB (for users) or on-chain data for external wallets.
    """
    # Fetch trades from DB for this wallet
    from src.utils.database import _fetch_all
    trades = await _fetch_all(
        "SELECT * FROM trade_history WHERE wallet_pubkey=? ORDER BY executed_at",
        (wallet_address,),
    )

    if not trades:
        # Try to get on-chain data
        return await _analyze_from_chain(wallet_address)

    return _compute_stats(wallet_address, trades)


def _compute_stats(wallet: str, trades: List[dict]) -> Dict[str, Any]:
    """Compute performance stats from trade history rows."""
    buys: Dict[str, Dict] = {}
    total_pnl = 0.0
    wins = 0
    losses = 0
    total_closed = 0
    hold_times = []
    token_counts: Dict[str, int] = {}
    rugged: List[str] = []

    for trade in trades:
        mint = trade["token_mint"]
        token_counts[mint] = token_counts.get(mint, 0) + 1
        side = trade["side"]

        if side == "buy":
            if mint not in buys:
                buys[mint] = {
                    "sol_in": trade["sol_amount"],
                    "buy_time": trade["executed_at"],
                    "symbol": trade.get("token_symbol") or "",
                }
            else:
                buys[mint]["sol_in"] += trade["sol_amount"]
        elif side == "sell":
            if mint in buys:
                sol_in = buys[mint]["sol_in"]
                sol_out = trade["sol_amount"]
                pnl = sol_out - sol_in
                total_pnl += pnl
                hold_time = trade["executed_at"] - buys[mint]["buy_time"]
                hold_times.append(hold_time)
                total_closed += 1
                if pnl > 0:
                    wins += 1
                else:
                    losses += 1
                # Rough rug heuristic: sold for < 10% of buy
                if sol_out < sol_in * 0.1:
                    rugged.append(mint)
                del buys[mint]

    win_rate = (wins / total_closed * 100) if total_closed > 0 else 0.0
    avg_hold = sum(hold_times) / len(hold_times) if hold_times else 0.0

    # Top traded tokens
    top_tokens = sorted(token_counts.items(), key=lambda x: x[1], reverse=True)[:5]

    # Risk score (1-10)
    risk = 1
    if win_rate < 30:
        risk += 3
    elif win_rate < 50:
        risk += 1
    if len(rugged) >= 3:
        risk += 3
    elif len(rugged) >= 1:
        risk += 1
    if total_pnl < -5:
        risk += 2
    elif total_pnl < 0:
        risk += 1
    risk = min(10, risk)

    return {
        "wallet": wallet,
        "total_pnl_sol": total_pnl,
        "win_rate": win_rate,
        "total_trades": len(trades),
        "total_closed": total_closed,
        "wins": wins,
        "losses": losses,
        "avg_hold_seconds": avg_hold,
        "top_tokens": top_tokens,
        "rugged_count": len(rugged),
        "risk_score": risk,
        "open_positions": len(buys),
    }


async def _analyze_from_chain(wallet: str) -> Dict[str, Any]:
    """
    Analyze a wallet that isn't in our DB by fetching recent signatures from RPC.
    Limited to last 100 confirmed transactions.
    """
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                config.SOLANA_RPC_URL,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getSignaturesForAddress",
                    "params": [wallet, {"limit": 100, "commitment": "confirmed"}],
                },
            )
            sigs = resp.json().get("result", [])
    except Exception as e:
        logger.error("_analyze_from_chain error for {}: {}", wallet, e)
        sigs = []

    return {
        "wallet": wallet,
        "total_pnl_sol": 0.0,
        "win_rate": 0.0,
        "total_trades": len(sigs),
        "total_closed": 0,
        "wins": 0,
        "losses": 0,
        "avg_hold_seconds": 0.0,
        "top_tokens": [],
        "rugged_count": 0,
        "risk_score": 5,
        "open_positions": 0,
        "note": "External wallet — limited on-chain data only",
    }


def format_wallet_report(result: dict) -> str:
    """Format analysis result for Telegram display."""
    wallet = result.get("wallet", "")
    note = result.get("note", "")

    lines = [
        f"📊 <b>Wallet Analysis</b>",
        f"📍 <code>{wallet}</code>",
        "",
    ]

    if note:
        lines += [f"ℹ️ {note}", ""]

    lines += [
        f"💰 <b>Total PnL:</b> {fmt_sol(result['total_pnl_sol'])}",
        f"🎯 <b>Win Rate:</b> {result['win_rate']:.1f}% ({result['wins']}W / {result['losses']}L)",
        f"📈 <b>Total Trades:</b> {result['total_trades']} ({result['total_closed']} closed)",
        f"⏱️ <b>Avg Hold Time:</b> {fmt_duration(result['avg_hold_seconds'])}",
        f"💀 <b>Rugged Tokens:</b> {result['rugged_count']}",
        f"⚠️ <b>Risk Score:</b> {result['risk_score']}/10 — {risk_label(result['risk_score'])}",
    ]

    if result.get("top_tokens"):
        lines += ["", "<b>Top Traded:</b>"]
        for mint, count in result["top_tokens"]:
            lines.append(f"  • {short_address(mint)} — {count} trades")

    return "\n".join(lines)
