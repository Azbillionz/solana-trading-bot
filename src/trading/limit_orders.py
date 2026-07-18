"""
Limit order engine: polls Jupiter price every N seconds and executes when target is hit.
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, List, Optional

import config
from src.utils.logger import logger
from src.utils.database import get_all_active_orders, mark_order_executed
from src.utils.wallet_manager import decrypt_keypair, get_sol_balance
from src.trading.swaps import (
    get_token_price_sol, execute_buy, execute_sell, calculate_fee
)
from src.utils.database import record_trade, open_position

OrderNotify = Callable[[int, str, str], Any]


async def _get_encrypted_key_for_order(order: dict) -> Optional[bytes]:
    """Retrieve encrypted private key for order's wallet."""
    from src.utils.database import _fetch_one
    row = await _fetch_one(
        "SELECT encrypted_key FROM wallets WHERE public_key=? AND user_id=?",
        (order["wallet_pubkey"], order["user_id"]),
    )
    return row["encrypted_key"] if row else None


async def run_limit_order_engine(notify: OrderNotify) -> None:
    """Continuous loop that checks all active limit orders."""
    logger.info("Limit order engine started")
    while True:
        try:
            await _process_limit_orders(notify)
        except Exception as e:
            logger.error("Limit order engine error: {}", e)
        await asyncio.sleep(config.LIMIT_ORDER_POLL_INTERVAL)


async def _process_limit_orders(notify: OrderNotify) -> None:
    orders = await get_all_active_orders()
    limit_orders = [o for o in orders if o["order_type"] in ("limit_buy", "limit_sell")]
    if not limit_orders:
        return

    for order in limit_orders:
        try:
            await _check_and_execute_limit(order, notify)
        except Exception as e:
            logger.error("Error checking limit order #{}: {}", order["id"], e)


async def _check_and_execute_limit(order: dict, notify: OrderNotify) -> None:
    token_mint = order["token_mint"]
    target_price = order.get("target_price")
    if target_price is None:
        return

    current_price = await get_token_price_sol(token_mint)
    if current_price is None:
        return

    order_type = order["order_type"]
    triggered = False
    if order_type == "limit_buy" and current_price <= target_price:
        triggered = True
    elif order_type == "limit_sell" and current_price >= target_price:
        triggered = True

    if not triggered:
        return

    logger.info(
        "Limit order #{} triggered: {} {} at {:.8f} SOL (target {:.8f})",
        order["id"], order_type, token_mint, current_price, target_price,
    )

    enc_key = await _get_encrypted_key_for_order(order)
    if not enc_key:
        logger.error("No encrypted key for limit order #{}", order["id"])
        return

    keypair = decrypt_keypair(enc_key)
    settings = {}
    from src.utils.database import get_user_settings
    settings = await get_user_settings(order["user_id"])
    slippage = settings.get("slippage", config.DEFAULT_SLIPPAGE)
    priority_fee = settings.get("priority_fee", config.DEFAULT_PRIORITY_FEE)

    if order_type == "limit_buy":
        amount_sol = order.get("amount_sol", 0.1)
        ok, sig, token_out = await execute_buy(
            keypair=keypair,
            token_mint=token_mint,
            sol_amount=amount_sol,
            slippage_pct=slippage,
            priority_fee_sol=priority_fee,
        )
        if ok and sig:
            fee = calculate_fee(amount_sol)
            await record_trade(
                user_id=order["user_id"],
                wallet_pubkey=order["wallet_pubkey"],
                token_mint=token_mint,
                token_symbol=order.get("token_symbol") or "",
                side="buy",
                sol_amount=amount_sol,
                token_amount=token_out or 0,
                price_sol=current_price,
                tx_sig=sig,
                fee_sol=fee,
                source="limit_order",
            )
            await mark_order_executed(order["id"])
            await notify(
                order["user_id"],
                token_mint,
                f"✅ Limit buy executed!\n💰 {amount_sol} SOL → {token_mint[:8]}...\n📝 TX: {sig[:16]}...",
            )
    elif order_type == "limit_sell":
        # Get token balance
        from src.utils.wallet_manager import get_token_accounts
        accounts = await get_token_accounts(order["wallet_pubkey"])
        token_raw = 0
        decimals = 9
        for acct in accounts:
            info = (
                acct.get("account", {}).get("data", {}).get("parsed", {}).get("info", {})
            )
            if info.get("mint") == token_mint:
                tok_amount = info.get("tokenAmount", {})
                token_raw = int(tok_amount.get("amount", 0))
                decimals = int(tok_amount.get("decimals", 9))
                break

        pct = order.get("percentage", 100) or 100
        sell_raw = int(token_raw * pct / 100)
        if sell_raw <= 0:
            await mark_order_executed(order["id"])
            return

        ok, sig, sol_out = await execute_sell(
            keypair=keypair,
            token_mint=token_mint,
            token_amount_raw=sell_raw,
            token_decimals=decimals,
            slippage_pct=slippage,
            priority_fee_sol=priority_fee,
        )
        if ok and sig:
            fee = calculate_fee(sol_out or 0)
            await record_trade(
                user_id=order["user_id"],
                wallet_pubkey=order["wallet_pubkey"],
                token_mint=token_mint,
                token_symbol=order.get("token_symbol") or "",
                side="sell",
                sol_amount=sol_out or 0,
                token_amount=sell_raw / (10 ** decimals),
                price_sol=current_price,
                tx_sig=sig,
                fee_sol=fee,
                source="limit_order",
            )
            await mark_order_executed(order["id"])
            await notify(
                order["user_id"],
                token_mint,
                f"✅ Limit sell executed!\n💰 {pct:.0f}% → {(sol_out or 0):.4f} SOL\n📝 TX: {sig[:16]}...",
            )
