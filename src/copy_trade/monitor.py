"""
Copy trade wallet monitor: polls target wallet token accounts every 30-60s,
detects new buys (new mint) and sells (disappeared mint).
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Callable, Dict, List, Optional, Set

import config
from src.utils.logger import logger
from src.utils.database import (
    get_all_copy_targets, update_seen_mints
)
from src.utils.wallet_manager import get_token_accounts
from src.copy_trade.executor import execute_copy_buy, execute_copy_sell

CopyNotify = Callable[[int, str, str], Any]

_monitor_running = False


async def run_copy_trade_monitor(notify: CopyNotify) -> None:
    """Main loop: poll all active copy targets."""
    global _monitor_running
    _monitor_running = True
    logger.info("Copy trade monitor started")
    while _monitor_running:
        try:
            await _poll_all_targets(notify)
        except Exception as e:
            logger.error("Copy trade monitor error: {}", e)
        await asyncio.sleep(config.COPY_TRADE_POLL_INTERVAL)


def stop_copy_trade_monitor() -> None:
    global _monitor_running
    _monitor_running = False


async def _poll_all_targets(notify: CopyNotify) -> None:
    targets = await get_all_copy_targets()
    if not targets:
        return

    # Group by target_wallet to avoid redundant RPC calls for the same wallet
    wallet_to_targets: Dict[str, List[dict]] = {}
    for t in targets:
        wallet_to_targets.setdefault(t["target_wallet"], []).append(t)

    tasks = [
        _poll_wallet(wallet, group, notify)
        for wallet, group in wallet_to_targets.items()
    ]
    await asyncio.gather(*tasks, return_exceptions=True)


async def _poll_wallet(
    wallet: str, targets: List[dict], notify: CopyNotify
) -> None:
    """Fetch current token accounts for `wallet` and detect changes."""
    accounts = await get_token_accounts(wallet)
    current_mints: Dict[str, float] = {}
    for acct in accounts:
        info = (
            acct.get("account", {})
            .get("data", {})
            .get("parsed", {})
            .get("info", {})
        )
        mint = info.get("mint", "")
        tok = info.get("tokenAmount", {})
        ui_amount = float(tok.get("uiAmount") or 0)
        if mint and ui_amount > 0:
            current_mints[mint] = ui_amount

    for target in targets:
        try:
            await _diff_and_act(wallet, current_mints, target, notify)
        except Exception as e:
            logger.error(
                "copy trade diff error for target {} (user {}): {}",
                wallet, target["user_id"], e,
            )


async def _diff_and_act(
    wallet: str,
    current_mints: Dict[str, float],
    target: dict,
    notify: CopyNotify,
) -> None:
    seen: Dict[str, float] = target.get("seen_mints") or {}
    user_id = target["user_id"]

    # ── New mints = buys ───────────────────────────────────────────────────────
    for mint, amount in current_mints.items():
        if mint in config.STABLE_MINTS and target.get("exclude_stable"):
            continue
        if target.get("exclude_pump") and await _is_pump_fun_token(mint):
            continue
        if mint not in seen:
            # New buy detected
            old_amount = seen.get(mint, 0)
            if amount > old_amount:
                ratio = amount / max(sum(current_mints.values()), 1)
                await execute_copy_buy(
                    user_id=user_id,
                    source_wallet=wallet,
                    token_mint=mint,
                    target_ratio=ratio,
                    target_cfg=target,
                    notify=notify,
                )
        elif target.get("dupe_buys") and amount > seen.get(mint, 0) * 1.05:
            # Repeated buy on same token
            ratio = (amount - seen.get(mint, 0)) / max(sum(current_mints.values()), 1)
            await execute_copy_buy(
                user_id=user_id,
                source_wallet=wallet,
                token_mint=mint,
                target_ratio=ratio,
                target_cfg=target,
                notify=notify,
            )

    # ── Disappeared mints = sells ─────────────────────────────────────────────
    if target.get("copy_sells"):
        for mint, old_amount in seen.items():
            if mint not in current_mints:
                # Full sell
                await execute_copy_sell(
                    user_id=user_id,
                    token_mint=mint,
                    sell_pct=100,
                    target_cfg=target,
                    notify=notify,
                )
            elif current_mints[mint] < old_amount * 0.95:
                # Partial sell
                sell_pct = (1 - current_mints[mint] / old_amount) * 100
                await execute_copy_sell(
                    user_id=user_id,
                    token_mint=mint,
                    sell_pct=sell_pct,
                    target_cfg=target,
                    notify=notify,
                )

    # Update seen mints
    await update_seen_mints(target["id"], current_mints)


async def _is_pump_fun_token(mint: str) -> bool:
    """Heuristic: check if a token was launched on pump.fun (simplified)."""
    # In production, query pump.fun API or on-chain program accounts
    return False
