"""
Jupiter V6 API integration for swap execution.
Handles quote → swap transaction → sign → send → confirm.
"""
from __future__ import annotations

import base64
import asyncio
from typing import Any, Dict, Optional, Tuple

import httpx
from solders.keypair import Keypair  # type: ignore
from solders.transaction import VersionedTransaction  # type: ignore
from solders.signature import Signature  # type: ignore
import base58

import config
from src.utils.logger import logger
from src.utils.helpers import sol_to_lamports, lamports_to_sol, LAMPORTS_PER_SOL


JUPITER_QUOTE_URL = f"{config.JUPITER_API_URL}/quote"
JUPITER_SWAP_URL = f"{config.JUPITER_API_URL}/swap"
JUPITER_PRICE_URL = "https://price.jup.ag/v6/price"


# ── Price ─────────────────────────────────────────────────────────────────────

async def get_token_price_sol(token_mint: str) -> Optional[float]:
    """Return token price in SOL using Jupiter Price API."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                JUPITER_PRICE_URL,
                params={"ids": token_mint, "vsToken": config.WSOL_MINT},
            )
            data = resp.json()
            price_info = data.get("data", {}).get(token_mint)
            if price_info:
                return float(price_info["price"])
    except Exception as e:
        logger.warning("get_token_price_sol failed for {}: {}", token_mint, e)
    return None


async def get_token_price_via_quote(
    token_mint: str,
    amount_sol: float = 0.001,
) -> Optional[float]:
    """Estimate price by getting a Jupiter quote for a tiny amount."""
    try:
        lamports = sol_to_lamports(amount_sol)
        quote = await get_quote(
            input_mint=config.WSOL_MINT,
            output_mint=token_mint,
            amount=lamports,
            slippage_bps=9999,
        )
        if not quote:
            return None
        out_amount = int(quote["outAmount"])
        # price = SOL per 1 token unit (normalized)
        decimals = int(quote.get("outputMint", {}) if False else 0) or 9
        token_units = out_amount / (10 ** decimals)
        if token_units == 0:
            return None
        return amount_sol / token_units
    except Exception as e:
        logger.warning("get_token_price_via_quote failed for {}: {}", token_mint, e)
        return None


# ── Quote ─────────────────────────────────────────────────────────────────────

async def get_quote(
    input_mint: str,
    output_mint: str,
    amount: int,
    slippage_bps: int = 1000,
    only_direct_routes: bool = False,
) -> Optional[Dict[str, Any]]:
    """Fetch a swap quote from Jupiter V6."""
    try:
        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": str(amount),
            "slippageBps": str(slippage_bps),
            "onlyDirectRoutes": str(only_direct_routes).lower(),
        }
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(JUPITER_QUOTE_URL, params=params)
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.error("get_quote error ({} → {}): {}", input_mint, output_mint, e)
        return None


# ── Build swap transaction ────────────────────────────────────────────────────

async def get_swap_transaction(
    quote: Dict[str, Any],
    user_pubkey: str,
    priority_fee_lamports: int = 5000,
    use_jito: bool = False,
    jito_tip_lamports: int = 1000,
) -> Optional[str]:
    """Call Jupiter /swap to get a base64-encoded transaction."""
    try:
        payload: Dict[str, Any] = {
            "quoteResponse": quote,
            "userPublicKey": user_pubkey,
            "wrapAndUnwrapSol": True,
            "computeUnitPriceMicroLamports": priority_fee_lamports,
            "dynamicComputeUnitLimit": True,
        }
        if use_jito:
            payload["useJitoBundle"] = True
            payload["jitoTipLamports"] = jito_tip_lamports

        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(JUPITER_SWAP_URL, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data.get("swapTransaction")
    except Exception as e:
        logger.error("get_swap_transaction error: {}", e)
        return None


# ── Sign & send ───────────────────────────────────────────────────────────────

async def sign_and_send_transaction(
    tx_base64: str,
    keypair: Keypair,
    rpc_url: str = "",
    commitment: str = "confirmed",
) -> Optional[str]:
    """Sign a versioned transaction and send it to the RPC. Returns tx signature."""
    rpc = rpc_url or config.SOLANA_RPC_URL
    try:
        raw_tx = base64.b64decode(tx_base64)
        tx = VersionedTransaction.from_bytes(raw_tx)
        signed_tx = VersionedTransaction(tx.message, [keypair])
        signed_bytes = bytes(signed_tx)
        encoded = base64.b64encode(signed_bytes).decode()

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                rpc,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "sendTransaction",
                    "params": [
                        encoded,
                        {
                            "encoding": "base64",
                            "skipPreflight": False,
                            "preflightCommitment": commitment,
                            "maxRetries": 3,
                        },
                    ],
                },
            )
            data = resp.json()
            if "error" in data:
                logger.error("sendTransaction RPC error: {}", data["error"])
                return None
            return data.get("result")
    except Exception as e:
        logger.error("sign_and_send_transaction error: {}", e)
        return None


async def confirm_transaction(
    sig: str, rpc_url: str = "", max_wait: int = 60
) -> bool:
    """Poll for transaction confirmation."""
    rpc = rpc_url or config.SOLANA_RPC_URL
    deadline = asyncio.get_event_loop().time() + max_wait
    async with httpx.AsyncClient(timeout=10) as client:
        while asyncio.get_event_loop().time() < deadline:
            try:
                resp = await client.post(
                    rpc,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getSignatureStatuses",
                        "params": [[sig], {"searchTransactionHistory": True}],
                    },
                )
                data = resp.json()
                statuses = data.get("result", {}).get("value", [])
                if statuses and statuses[0]:
                    status = statuses[0]
                    if status.get("err"):
                        logger.error("Tx {} failed on-chain: {}", sig, status["err"])
                        return False
                    if status.get("confirmationStatus") in ("confirmed", "finalized"):
                        return True
            except Exception as e:
                logger.warning("confirm_transaction poll error: {}", e)
            await asyncio.sleep(2)
    logger.warning("Transaction {} not confirmed within {}s", sig, max_wait)
    return False


# ── High-level buy / sell ─────────────────────────────────────────────────────

async def execute_buy(
    keypair: Keypair,
    token_mint: str,
    sol_amount: float,
    slippage_pct: float = 10.0,
    priority_fee_sol: float = 0.005,
    use_jito: bool = False,
    jito_tip_sol: float = 0.001,
    rpc_url: str = "",
) -> Tuple[bool, Optional[str], Optional[float]]:
    """
    Buy a token using SOL via Jupiter.
    Returns (success, tx_signature, output_token_amount).
    """
    lamports = sol_to_lamports(sol_amount)
    slippage_bps = int(slippage_pct * 100)
    pf_lamports = sol_to_lamports(priority_fee_sol)
    jito_lamports = sol_to_lamports(jito_tip_sol)
    pubkey = str(keypair.pubkey())

    quote = await get_quote(config.WSOL_MINT, token_mint, lamports, slippage_bps)
    if not quote:
        return False, None, None

    out_amount = int(quote.get("outAmount", 0))

    tx_b64 = await get_swap_transaction(
        quote, pubkey, pf_lamports, use_jito, jito_lamports
    )
    if not tx_b64:
        return False, None, None

    sig = await sign_and_send_transaction(tx_b64, keypair, rpc_url)
    if not sig:
        return False, None, None

    confirmed = await confirm_transaction(sig, rpc_url)
    return confirmed, sig, out_amount / 1e9 if out_amount else None


async def execute_sell(
    keypair: Keypair,
    token_mint: str,
    token_amount_raw: int,
    token_decimals: int = 9,
    slippage_pct: float = 10.0,
    priority_fee_sol: float = 0.005,
    use_jito: bool = False,
    jito_tip_sol: float = 0.001,
    rpc_url: str = "",
) -> Tuple[bool, Optional[str], Optional[float]]:
    """
    Sell a token amount (in raw units) for SOL via Jupiter.
    Returns (success, tx_signature, sol_received).
    """
    slippage_bps = int(slippage_pct * 100)
    pf_lamports = sol_to_lamports(priority_fee_sol)
    jito_lamports = sol_to_lamports(jito_tip_sol)
    pubkey = str(keypair.pubkey())

    quote = await get_quote(token_mint, config.WSOL_MINT, token_amount_raw, slippage_bps)
    if not quote:
        return False, None, None

    sol_out_lamports = int(quote.get("outAmount", 0))

    tx_b64 = await get_swap_transaction(
        quote, pubkey, pf_lamports, use_jito, jito_lamports
    )
    if not tx_b64:
        return False, None, None

    sig = await sign_and_send_transaction(tx_b64, keypair, rpc_url)
    if not sig:
        return False, None, None

    confirmed = await confirm_transaction(sig, rpc_url)
    return confirmed, sig, lamports_to_sol(sol_out_lamports) if sol_out_lamports else None


# ── Fee calculation ───────────────────────────────────────────────────────────

def calculate_fee(sol_amount: float, is_snipe: bool = False) -> float:
    fee_pct = config.SNIPER_FEE_PCT if is_snipe else config.TRADING_FEE_PCT
    return sol_amount * fee_pct / 100
