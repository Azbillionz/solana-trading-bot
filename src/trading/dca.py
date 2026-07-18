"""
DCA (Dollar-Cost Averaging) order engine.
Splits a total SOL amount into N equal buys spaced by a time interval.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Callable

import config
from src.utils.logger import logger
from src.utils.database import (
    get_all_active_orders, update_dca_order, record_trade, open_position
)
from src.utils.wallet_manager import decrypt_keypair
from src.trading.swaps import execute_buy, calculate_fee

DCANotify = Callable[[int, str, str], Any]


async def run_dca_engine(notify: DCANotify) -> None:
    """Continuous loop that checks all active DCA orders."""
    logger.info("DCA engine started")
    while True:
        try:
            await _process_dca_orders(notify)
        except Exception as e:
            logger.error("DCA engine error: {}", e)
        await asyncio.sleep(30)   # check every 30s, actual execution gated by dca_next_at


async def _process_dca_orders(notify: DCANotify) -> None:
    orders = await get_all_active_orders()
    dca_orders = [o for o in orders if o["order_type"] == "dca"]
    now = time.time()
    for order in dca_orders:
        try:
            next_at = order.get("dca_next_at") or 0
            if now >= next_at:
                await _execute_dca_slice(order, notify)
        except Exception as e:
            logger.error("DCA order #{} error: {}", order["id"], e)


async def _execute_dca_slice(order: dict, notify: DCANotify) -> None:
    from src.utils.database import _fetch_one, get_user_settings
    row = await _fetch_one(
        "SELECT encrypted_key FROM wallets WHERE public_key=? AND user_id=?",
        (order["wallet_pubkey"], order["user_id"]),
    )
    if not row:
        return

    keypair = decrypt_keypair(row["encrypted_key"])
    settings = await get_user_settings(order["user_id"])
    slippage = settings.get("slippage", config.DEFAULT_SLIPPAGE)
    priority_fee = settings.get("priority_fee", config.DEFAULT_PRIORITY_FEE)

    token_mint = order["token_mint"]
    amount_sol = order.get("amount_sol", 0.01)
    interval_secs = (order.get("dca_interval") or 60) * 60  # interval stored in minutes
    orders_left = order.get("dca_orders_left", 0)

    ok, sig, token_out = await execute_buy(
        keypair=keypair,
        token_mint=token_mint,
        sol_amount=amount_sol,
        slippage_pct=slippage,
        priority_fee_sol=priority_fee,
    )

    symbol = order.get("token_symbol") or ""
    if ok and sig:
        fee = calculate_fee(amount_sol)
        await record_trade(
            user_id=order["user_id"],
            wallet_pubkey=order["wallet_pubkey"],
            token_mint=token_mint,
            token_symbol=symbol,
            side="buy",
            sol_amount=amount_sol,
            token_amount=token_out or 0,
            price_sol=amount_sol / (token_out or 1),
            tx_sig=sig,
            fee_sol=fee,
            source="dca",
        )
        remaining = orders_left - 1
        next_at = time.time() + interval_secs
        await update_dca_order(order["id"], remaining, next_at)
        total_done = (order.get("dca_orders_left", 0) - remaining)
        await notify(
            order["user_id"],
            token_mint,
            (
                f"📊 DCA slice #{total_done} executed\n"
                f"💰 {amount_sol} SOL → {symbol or token_mint[:8]}...\n"
                f"📝 TX: {sig[:16]}...\n"
                f"🔁 {remaining} slices remaining"
            ),
        )
    else:
        await notify(
            order["user_id"],
            token_mint,
            f"❌ DCA slice failed for {symbol or token_mint[:8]}...",
        )
