"""
Copy trade executor: translates detected trades into swap calls on the user's wallet.
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable, Optional

import config
from src.utils.logger import logger
from src.utils.database import (
    get_active_wallet_pubkey, record_trade, open_position, _fetch_one, get_user_settings
)
from src.utils.wallet_manager import decrypt_keypair, get_sol_balance, get_token_accounts
from src.trading.swaps import execute_buy, execute_sell, calculate_fee

CopyNotify = Callable[[int, str, str], Any]


async def execute_copy_buy(
    user_id: int,
    source_wallet: str,
    token_mint: str,
    target_ratio: float,
    target_cfg: dict,
    notify: CopyNotify,
    retry: int = 0,
) -> None:
    """Buy a token on behalf of user, mirroring a detected buy."""
    max_retries = target_cfg.get("retry_count", 3)
    fixed_sol = target_cfg.get("fixed_sol")
    proportional = target_cfg.get("proportional", False)
    max_buy = target_cfg.get("max_buy_sol", 1.0)
    min_buy = target_cfg.get("min_buy_sol", 0.01)

    wallet_pubkey = await get_active_wallet_pubkey(user_id)
    if not wallet_pubkey:
        await notify(user_id, token_mint, "❌ No active wallet for copy trade")
        return

    sol_balance = await get_sol_balance(wallet_pubkey)

    if proportional:
        buy_sol = sol_balance * target_ratio
    else:
        buy_sol = fixed_sol or 0.1

    buy_sol = min(buy_sol, max_buy)
    if buy_sol < min_buy:
        logger.info(
            "Copy trade: skipping buy of {} for user {} — amount {:.4f} < min {:.4f}",
            token_mint, user_id, buy_sol, min_buy,
        )
        return

    if sol_balance < buy_sol + config.DEFAULT_PRIORITY_FEE:
        await notify(user_id, token_mint, "❌ Insufficient SOL for copy trade buy")
        return

    row = await _fetch_one(
        "SELECT encrypted_key FROM wallets WHERE public_key=? AND user_id=?",
        (wallet_pubkey, user_id),
    )
    if not row:
        return

    keypair = decrypt_keypair(row["encrypted_key"])
    settings = await get_user_settings(user_id)
    slippage = settings.get("slippage", config.DEFAULT_SLIPPAGE)
    priority_fee = settings.get("priority_fee", config.DEFAULT_PRIORITY_FEE)

    ok, sig, token_out = await execute_buy(
        keypair=keypair,
        token_mint=token_mint,
        sol_amount=buy_sol,
        slippage_pct=slippage,
        priority_fee_sol=priority_fee,
    )

    if ok and sig:
        fee = calculate_fee(buy_sol)
        await record_trade(
            user_id=user_id,
            wallet_pubkey=wallet_pubkey,
            token_mint=token_mint,
            token_symbol="",
            side="buy",
            sol_amount=buy_sol,
            token_amount=token_out or 0,
            price_sol=buy_sol / (token_out or 1),
            tx_sig=sig,
            fee_sol=fee,
            source="copy_trade",
        )
        await open_position(
            user_id=user_id,
            wallet_pubkey=wallet_pubkey,
            token_mint=token_mint,
            token_symbol="",
            entry_sol=buy_sol,
            entry_price=buy_sol / (token_out or 1),
            token_amount=token_out or 0,
            sell_rules={},
            source="copy_trade",
        )
        await notify(
            user_id,
            token_mint,
            (
                f"🔁 Copy buy executed!\n"
                f"📥 Mirroring {source_wallet[:8]}...\n"
                f"💰 Spent: {buy_sol:.4f} SOL → {token_mint[:8]}...\n"
                f"📝 TX: {sig[:16]}..."
            ),
        )
    else:
        if retry < max_retries:
            logger.warning(
                "Copy buy failed for user {} mint {} — retry {}/{}",
                user_id, token_mint, retry + 1, max_retries,
            )
            await asyncio.sleep(2 ** retry)
            await execute_copy_buy(
                user_id, source_wallet, token_mint, target_ratio, target_cfg,
                notify, retry + 1,
            )
        else:
            await notify(
                user_id, token_mint,
                f"❌ Copy buy failed after {max_retries} retries for {token_mint[:8]}...",
            )


async def execute_copy_sell(
    user_id: int,
    token_mint: str,
    sell_pct: float,
    target_cfg: dict,
    notify: CopyNotify,
    retry: int = 0,
) -> None:
    """Sell a percentage of a token position, mirroring a detected sell."""
    max_retries = target_cfg.get("retry_count", 3)
    wallet_pubkey = await get_active_wallet_pubkey(user_id)
    if not wallet_pubkey:
        return

    # Get token balance
    accounts = await get_token_accounts(wallet_pubkey)
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

    sell_raw = int(token_raw * sell_pct / 100)
    if sell_raw <= 0:
        return

    row = await _fetch_one(
        "SELECT encrypted_key FROM wallets WHERE public_key=? AND user_id=?",
        (wallet_pubkey, user_id),
    )
    if not row:
        return

    keypair = decrypt_keypair(row["encrypted_key"])
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
        fee = calculate_fee(sol_out or 0)
        await record_trade(
            user_id=user_id,
            wallet_pubkey=wallet_pubkey,
            token_mint=token_mint,
            token_symbol="",
            side="sell",
            sol_amount=sol_out or 0,
            token_amount=sell_raw / (10 ** decimals),
            price_sol=(sol_out or 0) / max(sell_raw / (10 ** decimals), 1),
            tx_sig=sig,
            fee_sol=fee,
            source="copy_trade",
        )
        await notify(
            user_id,
            token_mint,
            (
                f"🔁 Copy sell executed!\n"
                f"💰 Sold {sell_pct:.0f}% → {(sol_out or 0):.4f} SOL\n"
                f"📝 TX: {sig[:16]}..."
            ),
        )
    else:
        if retry < max_retries:
            await asyncio.sleep(2 ** retry)
            await execute_copy_sell(
                user_id, token_mint, sell_pct, target_cfg, notify, retry + 1
            )
        else:
            await notify(
                user_id, token_mint,
                f"❌ Copy sell failed after {max_retries} retries",
            )
