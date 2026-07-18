"""
Auto-sell rule engine: monitors open positions and fires sell rules.
Rules supported: take-profit %, stop-loss %, trailing stop-loss, timer.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, Dict, List, Optional

import config
from src.utils.logger import logger
from src.utils.database import (
    get_all_open_positions, update_position_price, close_position, record_trade
)
from src.utils.wallet_manager import decrypt_keypair, get_token_accounts
from src.trading.swaps import get_token_price_sol, execute_sell, calculate_fee

AutoSellNotify = Callable[[int, str, str], Any]

# Track trailing stop high-water marks per position id
_trailing_highs: Dict[int, float] = {}


async def run_auto_sell_engine(notify: AutoSellNotify) -> None:
    """Continuous monitoring loop for all open positions."""
    logger.info("Auto-sell engine started")
    while True:
        try:
            await _process_positions(notify)
        except Exception as e:
            logger.error("Auto-sell engine error: {}", e)
        await asyncio.sleep(15)


async def _process_positions(notify: AutoSellNotify) -> None:
    positions = await get_all_open_positions()
    if not positions:
        return

    for pos in positions:
        try:
            await _evaluate_position(pos, notify)
        except Exception as e:
            logger.error("Auto-sell: error evaluating position #{}: {}", pos["id"], e)


async def _evaluate_position(pos: dict, notify: AutoSellNotify) -> None:
    token_mint = pos["token_mint"]
    entry_price = pos.get("entry_price", 0)
    if not entry_price:
        return

    current_price = await get_token_price_sol(token_mint)
    if current_price is None:
        return

    await update_position_price(pos["id"], current_price)

    change_pct = ((current_price - entry_price) / entry_price) * 100
    rules = pos.get("sell_rules", {})
    pos_id = pos["id"]

    # ── Timer ─────────────────────────────────────────────────────────────────
    timer_minutes = rules.get("timer_minutes")
    if timer_minutes:
        age_seconds = time.time() - (pos.get("opened_at") or 0)
        if age_seconds >= timer_minutes * 60:
            await _fire_sell(pos, 100, current_price, "⏱️ Auto-sell timer", notify)
            return

    # ── Stop-loss ─────────────────────────────────────────────────────────────
    sl_pct = rules.get("sl_pct")
    if sl_pct and change_pct <= -abs(sl_pct):
        await _fire_sell(pos, 100, current_price, f"🛑 Stop-loss at {change_pct:.1f}%", notify)
        return

    # ── Trailing stop-loss ────────────────────────────────────────────────────
    trailing_pct = rules.get("trailing_pct")
    trailing_activate = rules.get("trailing_activate_pct", 0)
    if trailing_pct and change_pct >= (trailing_activate or 0):
        high = _trailing_highs.get(pos_id, current_price)
        if current_price > high:
            _trailing_highs[pos_id] = current_price
            high = current_price
        trail_drop = ((high - current_price) / high) * 100
        if trail_drop >= trailing_pct:
            await _fire_sell(
                pos, 100, current_price,
                f"📉 Trailing stop hit: -{trail_drop:.1f}% from peak",
                notify,
            )
            _trailing_highs.pop(pos_id, None)
            return

    # ── Take-profit (multi-level) ─────────────────────────────────────────────
    tp_levels: List[dict] = rules.get("tp_levels") or []
    if not tp_levels and rules.get("tp_pct"):
        tp_levels = [{"pct": rules["tp_pct"], "sell_pct": 100, "fired": False}]

    for i, level in enumerate(tp_levels):
        if level.get("fired"):
            continue
        if change_pct >= level["pct"]:
            sell_pct = level.get("sell_pct", 100)
            await _fire_sell(
                pos, sell_pct, current_price,
                f"🎯 Take-profit +{change_pct:.1f}% ({sell_pct}% sold)",
                notify,
            )
            level["fired"] = True
            # update sell_rules in db
            from src.utils.database import update_sell_rules
            await update_sell_rules(pos_id, rules)
            return


async def _fire_sell(
    pos: dict,
    sell_pct: float,
    current_price: float,
    reason: str,
    notify: AutoSellNotify,
) -> None:
    token_mint = pos["token_mint"]
    user_id = pos["user_id"]
    wallet_pubkey = pos["wallet_pubkey"]

    from src.utils.database import _fetch_one
    row = await _fetch_one(
        "SELECT encrypted_key FROM wallets WHERE public_key=? AND user_id=?",
        (wallet_pubkey, user_id),
    )
    if not row:
        logger.error("Auto-sell: no wallet key for position #{}", pos["id"])
        return

    keypair = decrypt_keypair(row["encrypted_key"])

    # Fetch current token balance
    token_accts = await get_token_accounts(wallet_pubkey)
    token_raw = 0
    decimals = 9
    for acct in token_accts:
        info = acct.get("account", {}).get("data", {}).get("parsed", {}).get("info", {})
        if info.get("mint") == token_mint:
            tok_amount = info.get("tokenAmount", {})
            token_raw = int(tok_amount.get("amount", 0))
            decimals = int(tok_amount.get("decimals", 9))
            break

    sell_raw = int(token_raw * sell_pct / 100)
    if sell_raw <= 0:
        logger.warning("Auto-sell: no tokens to sell for position #{}", pos["id"])
        return

    from src.utils.database import get_user_settings
    settings = await get_user_settings(user_id)
    slippage = settings.get("slippage", config.DEFAULT_SLIPPAGE)
    priority_fee = settings.get("priority_fee", config.DEFAULT_PRIORITY_FEE)

    ok, sig, sol_out = await execute_sell(
        keypair=keypair,
        token_mint=token_mint,
        token_amount_raw=sell_raw,
        token_decimals=decimals,
        slippage_pct=slippage,
        priority_fee_sol=priority_fee,
    )

    if ok and sig:
        entry_sol = pos.get("entry_sol", 0)
        pnl_sol = (sol_out or 0) - entry_sol * (sell_pct / 100)
        fee = calculate_fee(sol_out or 0)
        await record_trade(
            user_id=user_id,
            wallet_pubkey=wallet_pubkey,
            token_mint=token_mint,
            token_symbol=pos.get("token_symbol") or "",
            side="sell",
            sol_amount=sol_out or 0,
            token_amount=sell_raw / (10 ** decimals),
            price_sol=current_price,
            tx_sig=sig,
            fee_sol=fee,
            source="auto_sell",
        )
        if sell_pct >= 100:
            await close_position(pos["id"], pnl_sol)
        pnl_str = f"+{pnl_sol:.4f}" if pnl_sol >= 0 else f"{pnl_sol:.4f}"
        await notify(
            user_id,
            token_mint,
            (
                f"{reason}\n"
                f"💰 Received: {(sol_out or 0):.4f} SOL\n"
                f"📊 PnL: {pnl_str} SOL\n"
                f"📝 TX: {sig[:16]}..."
            ),
        )
    else:
        await notify(user_id, token_mint, f"❌ Auto-sell FAILED: {reason}")
