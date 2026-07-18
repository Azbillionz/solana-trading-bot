"""
Auto-sniper: monitors pump.fun, Raydium AMM, and Moonshot for new launches.
Applies anti-rug filters before buying and activates auto-sell rules post-snipe.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Callable, Dict, Optional, Set

import websockets
import httpx
from solders.keypair import Keypair  # type: ignore

import config
from src.utils.logger import logger
from src.utils.database import (
    get_sniper_config, record_trade, open_position
)
from src.utils.wallet_manager import get_sol_balance, decrypt_keypair
from src.trading.swaps import execute_buy, calculate_fee
from src.analysis.rugcheck import quick_rug_check


# ── Types ─────────────────────────────────────────────────────────────────────

SniperNotifyCallback = Callable[[int, str, str, bool, str], Any]


# ── State ─────────────────────────────────────────────────────────────────────

_active_snipers: Dict[int, asyncio.Task] = {}   # user_id → monitor task
_sniped_mints: Set[str] = set()                  # prevent double-snipe


# ── Rug filter ────────────────────────────────────────────────────────────────

async def _passes_filters(mint: str, cfg: dict) -> tuple[bool, str]:
    """Run anti-rug filters. Returns (passes, reason)."""
    result = await quick_rug_check(mint)
    if cfg.get("check_mint_auth") and result.get("mint_authority_enabled"):
        return False, "Mint authority not revoked"
    if cfg.get("check_freeze") and result.get("freeze_authority_enabled"):
        return False, "Freeze authority not revoked"
    lp_burn = result.get("lp_burn_pct", 0)
    min_lp = cfg.get("min_lp_burn_pct", 80)
    if lp_burn < min_lp:
        return False, f"LP burn {lp_burn:.0f}% < required {min_lp:.0f}%"
    conc = result.get("top10_holder_pct", 100)
    max_conc = cfg.get("max_holder_conc", 20)
    if conc > max_conc:
        return False, f"Top-10 holders own {conc:.0f}% (max {max_conc:.0f}%)"
    if cfg.get("check_socials") and not result.get("has_socials"):
        return False, "No social links found"
    return True, "OK"


# ── Snipe execution ───────────────────────────────────────────────────────────

async def _execute_snipe(
    user_id: int,
    wallet_pubkey: str,
    encrypted_key: bytes,
    token_mint: str,
    cfg: dict,
    notify: SniperNotifyCallback,
) -> None:
    if token_mint in _sniped_mints:
        return
    _sniped_mints.add(token_mint)

    delay = cfg.get("delay_seconds", 0)
    if delay > 0:
        await asyncio.sleep(delay)

    # Anti-rug check
    passes, reason = await _passes_filters(token_mint, cfg)
    if not passes:
        await notify(user_id, token_mint, f"❌ Snipe blocked: {reason}", False, "")
        return

    buy_sol = cfg.get("buy_amount_sol", 0.1)
    priority_fee = cfg.get("priority_fee", 0.005)
    sol_balance = await get_sol_balance(wallet_pubkey)
    if sol_balance < buy_sol + priority_fee:
        await notify(user_id, token_mint, "❌ Insufficient SOL for snipe", False, "")
        return

    keypair = decrypt_keypair(encrypted_key)
    ok, sig, token_amount = await execute_buy(
        keypair=keypair,
        token_mint=token_mint,
        sol_amount=buy_sol,
        priority_fee_sol=priority_fee,
        rpc_url=cfg.get("rpc_url", ""),
    )

    if ok and sig:
        fee_sol = calculate_fee(buy_sol, is_snipe=True)
        await record_trade(
            user_id=user_id,
            wallet_pubkey=wallet_pubkey,
            token_mint=token_mint,
            token_symbol="NEW",
            side="buy",
            sol_amount=buy_sol,
            token_amount=token_amount or 0,
            price_sol=buy_sol / (token_amount or 1),
            tx_sig=sig,
            fee_sol=fee_sol,
            source="sniper",
        )
        sell_rules = {
            "tp_pct": cfg.get("tp_pct", 100),
            "sl_pct": cfg.get("sl_pct", 40),
            "trailing_pct": cfg.get("trailing_pct"),
            "trailing_activate_pct": cfg.get("trailing_activate_pct"),
        }
        await open_position(
            user_id=user_id,
            wallet_pubkey=wallet_pubkey,
            token_mint=token_mint,
            token_symbol="NEW",
            entry_sol=buy_sol,
            entry_price=buy_sol / (token_amount or 1),
            token_amount=token_amount or 0,
            sell_rules=sell_rules,
            source="sniper",
        )
        msg = f"✅ Sniped {token_mint[:8]}...\n💰 Spent: {buy_sol} SOL\n📝 TX: {sig[:16]}..."
        await notify(user_id, token_mint, msg, True, sig)
    else:
        await notify(user_id, token_mint, f"❌ Snipe failed for {token_mint[:8]}...", False, "")


# ── WebSocket listeners ───────────────────────────────────────────────────────

async def _monitor_pump_fun(
    user_id: int,
    wallet_pubkey: str,
    encrypted_key: bytes,
    cfg: dict,
    notify: SniperNotifyCallback,
) -> None:
    """Subscribe to pump.fun program logs via WebSocket for new bonding curves."""
    ws_url = cfg.get("ws_url", config.SOLANA_WS_URL)
    subscribe_msg = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "logsSubscribe",
        "params": [
            {"mentions": [config.PUMP_FUN_PROGRAM_ID]},
            {"commitment": "confirmed"},
        ],
    }
    logger.info("Sniper[{}]: listening to pump.fun program logs", user_id)
    while True:
        try:
            async with websockets.connect(ws_url, ping_interval=20) as ws:
                await ws.send(json.dumps(subscribe_msg))
                while True:
                    raw = await ws.recv()
                    data = json.loads(raw)
                    params = data.get("params", {})
                    result = params.get("result", {})
                    value = result.get("value", {})
                    logs = value.get("logs", [])
                    # Look for "InitializeMint" or "create" in pump.fun logs
                    for log in logs:
                        if "create" in log.lower() or "initialize" in log.lower():
                            sig = value.get("signature", "")
                            # Extract new mint from logs (simplified)
                            mint = _extract_mint_from_logs(logs, sig)
                            if mint and mint not in _sniped_mints:
                                asyncio.create_task(
                                    _execute_snipe(
                                        user_id, wallet_pubkey, encrypted_key, mint, cfg, notify
                                    )
                                )
        except asyncio.CancelledError:
            logger.info("Sniper[{}]: pump.fun monitor cancelled", user_id)
            return
        except Exception as e:
            logger.error("Sniper[{}]: pump.fun WS error: {} — reconnecting in 5s", user_id, e)
            await asyncio.sleep(5)


async def _monitor_raydium(
    user_id: int,
    wallet_pubkey: str,
    encrypted_key: bytes,
    cfg: dict,
    notify: SniperNotifyCallback,
) -> None:
    """Subscribe to Raydium AMM new pool creation."""
    ws_url = cfg.get("ws_url", config.SOLANA_WS_URL)
    subscribe_msg = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "logsSubscribe",
        "params": [
            {"mentions": [config.RAYDIUM_AMM_PROGRAM_ID]},
            {"commitment": "confirmed"},
        ],
    }
    logger.info("Sniper[{}]: listening to Raydium AMM logs", user_id)
    while True:
        try:
            async with websockets.connect(ws_url, ping_interval=20) as ws:
                await ws.send(json.dumps(subscribe_msg))
                while True:
                    raw = await ws.recv()
                    data = json.loads(raw)
                    params = data.get("params", {})
                    result = params.get("result", {})
                    value = result.get("value", {})
                    logs = value.get("logs", [])
                    for log in logs:
                        if "initialize2" in log.lower() or "InitializeInstruction2" in log:
                            sig = value.get("signature", "")
                            mint = await _fetch_pool_base_mint(sig)
                            if mint and mint not in _sniped_mints:
                                asyncio.create_task(
                                    _execute_snipe(
                                        user_id, wallet_pubkey, encrypted_key, mint, cfg, notify
                                    )
                                )
        except asyncio.CancelledError:
            logger.info("Sniper[{}]: Raydium monitor cancelled", user_id)
            return
        except Exception as e:
            logger.error("Sniper[{}]: Raydium WS error: {} — reconnecting in 5s", user_id, e)
            await asyncio.sleep(5)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_mint_from_logs(logs: list, sig: str) -> Optional[str]:
    """Try to extract a token mint from program logs (heuristic)."""
    for log in logs:
        # pump.fun logs often contain the mint address as a base58 string
        parts = log.split()
        for part in parts:
            if len(part) == 44 or len(part) == 43:  # base58 pubkey length
                return part
    return None


async def _fetch_pool_base_mint(sig: str) -> Optional[str]:
    """Fetch transaction and extract the base mint of a new Raydium pool."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                config.SOLANA_RPC_URL,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTransaction",
                    "params": [
                        sig,
                        {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0},
                    ],
                },
            )
            data = resp.json()
            accs = (
                data.get("result", {})
                .get("transaction", {})
                .get("message", {})
                .get("accountKeys", [])
            )
            # The base mint is typically at a fixed index in the pool init
            for acc in accs:
                key = acc.get("pubkey", "") if isinstance(acc, dict) else str(acc)
                if key and key not in (
                    config.WSOL_MINT, config.USDC_MINT, config.RAYDIUM_AMM_PROGRAM_ID
                ):
                    return key
    except Exception as e:
        logger.warning("_fetch_pool_base_mint error for {}: {}", sig, e)
    return None


# ── Public start / stop ───────────────────────────────────────────────────────

async def start_sniper(
    user_id: int,
    wallet_pubkey: str,
    encrypted_key: bytes,
    cfg: dict,
    notify: SniperNotifyCallback,
) -> None:
    if user_id in _active_snipers:
        await stop_sniper(user_id)

    tasks = []
    if cfg.get("snipe_pump", True):
        tasks.append(
            asyncio.create_task(
                _monitor_pump_fun(user_id, wallet_pubkey, encrypted_key, cfg, notify)
            )
        )
    if cfg.get("snipe_raydium", True):
        tasks.append(
            asyncio.create_task(
                _monitor_raydium(user_id, wallet_pubkey, encrypted_key, cfg, notify)
            )
        )

    if tasks:
        combined = asyncio.create_task(_gather(*tasks))
        _active_snipers[user_id] = combined
        logger.info("Sniper started for user {}", user_id)


async def _gather(*tasks):
    await asyncio.gather(*tasks, return_exceptions=True)


async def stop_sniper(user_id: int) -> None:
    task = _active_snipers.pop(user_id, None)
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    logger.info("Sniper stopped for user {}", user_id)


def is_sniper_active(user_id: int) -> bool:
    task = _active_snipers.get(user_id)
    return task is not None and not task.done()
