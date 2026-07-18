"""
EVM multi-chain trading via 1inch Swap API + web3.py.
Supports: Ethereum, BNB Chain, Polygon, Arbitrum, Base, Optimism, Avalanche.

Swap flow:
  1. Get swap calldata from 1inch API (quote + tx data in one call)
  2. For sells: approve 1inch router to spend tokens if needed
  3. Sign & broadcast via web3 RPC
  4. Wait for confirmation
"""
from __future__ import annotations

import asyncio
from typing import Optional

import httpx
from web3 import Web3
from web3.exceptions import TransactionNotFound
from eth_account import Account

import config
from src.chains.registry import get_chain, get_rpc, NATIVE_TOKEN
from src.utils.logger import logger

# 1inch v5.2 router (same address across all supported EVM chains)
ONEINCH_ROUTER = "0x1111111254EEB25477B68fb85Ed929f73A960582"

# Minimal ERC-20 ABI — only what the bot needs
ERC20_ABI = [
    {
        "name": "approve", "type": "function",
        "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "nonpayable",
    },
    {
        "name": "allowance", "type": "function",
        "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
    },
    {
        "name": "balanceOf", "type": "function",
        "inputs": [{"name": "account", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
    },
    {
        "name": "decimals", "type": "function",
        "inputs": [],
        "outputs": [{"name": "", "type": "uint8"}],
        "stateMutability": "view",
    },
]


def _get_w3(chain_key: str) -> Web3:
    """Return a connected Web3 instance for the given chain."""
    return Web3(Web3.HTTPProvider(get_rpc(chain_key)))


def _oneinch_base_url(chain_key: str) -> str:
    chain = get_chain(chain_key)
    cid = chain["oneinch_chain_id"]
    if config.ONEINCH_API_KEY:
        return f"https://api.1inch.dev/swap/v6.0/{cid}"
    return f"https://api.1inch.io/v5.2/{cid}"


def _oneinch_headers() -> dict:
    if config.ONEINCH_API_KEY:
        return {"Authorization": f"Bearer {config.ONEINCH_API_KEY}"}
    return {}


async def evm_get_native_balance(address: str, chain_key: str) -> float:
    """Return native token balance (ETH/BNB/MATIC/AVAX) in human-readable units."""
    try:
        w3 = _get_w3(chain_key)
        loop = asyncio.get_event_loop()
        wei = await loop.run_in_executor(None, lambda: w3.eth.get_balance(Web3.to_checksum_address(address)))
        return float(Web3.from_wei(wei, "ether"))
    except Exception as e:
        logger.error("evm_get_native_balance error {}: {}", chain_key, e)
        return 0.0


async def evm_get_token_balance(address: str, token_address: str, chain_key: str) -> tuple[float, int]:
    """Return (human_balance, decimals) for an ERC-20 token."""
    try:
        w3 = _get_w3(chain_key)
        contract = w3.eth.contract(
            address=Web3.to_checksum_address(token_address), abi=ERC20_ABI
        )
        loop = asyncio.get_event_loop()
        decimals = await loop.run_in_executor(None, contract.functions.decimals().call)
        raw = await loop.run_in_executor(
            None, lambda: contract.functions.balanceOf(Web3.to_checksum_address(address)).call()
        )
        return raw / (10 ** decimals), decimals
    except Exception as e:
        logger.error("evm_get_token_balance error: {}", e)
        return 0.0, 18


async def evm_get_token_price_native(token_address: str, chain_key: str) -> float:
    """Get approximate token price in native currency via 1inch quote."""
    chain = get_chain(chain_key)
    try:
        amount_wei = Web3.to_wei(1, "ether")  # 1 native token worth of token
        base = _oneinch_base_url(chain_key)
        async with httpx.AsyncClient(timeout=10, headers=_oneinch_headers()) as client:
            r = await client.get(f"{base}/quote", params={
                "fromTokenAddress": NATIVE_TOKEN,
                "toTokenAddress": token_address,
                "amount": str(amount_wei),
            })
            data = r.json()
            to_amount = int(data.get("toTokenAmount", 0))
            to_decimals = data.get("toToken", {}).get("decimals", 18)
            if to_amount > 0:
                return 1.0 / (to_amount / (10 ** to_decimals))
    except Exception as e:
        logger.warning("evm_get_token_price error: {}", e)
    return 0.0


async def _ensure_approval(
    w3: Web3,
    private_key: str,
    owner_address: str,
    token_address: str,
    amount_wei: int,
    chain_key: str,
) -> bool:
    """Approve 1inch router to spend tokens if allowance is insufficient."""
    try:
        contract = w3.eth.contract(
            address=Web3.to_checksum_address(token_address), abi=ERC20_ABI
        )
        loop = asyncio.get_event_loop()
        allowance = await loop.run_in_executor(
            None,
            lambda: contract.functions.allowance(
                Web3.to_checksum_address(owner_address),
                Web3.to_checksum_address(ONEINCH_ROUTER),
            ).call(),
        )
        if allowance >= amount_wei:
            return True  # Already approved

        chain = get_chain(chain_key)
        nonce = await loop.run_in_executor(
            None,
            lambda: w3.eth.get_transaction_count(Web3.to_checksum_address(owner_address)),
        )
        gas_price = await loop.run_in_executor(None, lambda: w3.eth.gas_price)
        approve_tx = contract.functions.approve(
            Web3.to_checksum_address(ONEINCH_ROUTER), 2**256 - 1  # max approval
        ).build_transaction({
            "from": Web3.to_checksum_address(owner_address),
            "nonce": nonce,
            "gasPrice": gas_price,
            "chainId": chain["chain_id"],
        })
        estimated = await loop.run_in_executor(None, lambda: w3.eth.estimate_gas(approve_tx))
        approve_tx["gas"] = int(estimated * 1.2)

        signed = w3.eth.account.sign_transaction(approve_tx, private_key=private_key)
        tx_hash = await loop.run_in_executor(None, lambda: w3.eth.send_raw_transaction(signed.raw_transaction))
        await loop.run_in_executor(None, lambda: w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60))
        logger.info("Approval tx confirmed: {}", tx_hash.hex())
        return True
    except Exception as e:
        logger.error("Approval failed: {}", e)
        return False


async def evm_execute_buy(
    private_key: str,
    token_address: str,
    native_amount: float,
    chain_key: str,
    slippage_pct: float = 1.0,
) -> tuple[bool, Optional[str], Optional[float]]:
    """
    Buy a token using native currency (ETH/BNB/MATIC/AVAX) via 1inch.
    Returns (success, tx_hash, token_amount_received).
    """
    chain = get_chain(chain_key)
    try:
        account = Account.from_key(private_key)
        address = account.address
        w3 = _get_w3(chain_key)
        amount_wei = Web3.to_wei(native_amount, "ether")

        # Get swap calldata from 1inch
        base = _oneinch_base_url(chain_key)
        async with httpx.AsyncClient(timeout=20, headers=_oneinch_headers()) as client:
            r = await client.get(f"{base}/swap", params={
                "fromTokenAddress": NATIVE_TOKEN,
                "toTokenAddress": Web3.to_checksum_address(token_address),
                "amount": str(amount_wei),
                "fromAddress": address,
                "slippage": slippage_pct,
                "disableEstimate": "true",
            })
            if r.status_code != 200:
                logger.error("1inch buy error {}: {}", r.status_code, r.text[:200])
                return False, None, None
            swap_data = r.json()

        tx_data = swap_data["tx"]
        token_out_wei = int(swap_data.get("toTokenAmount", 0))
        token_decimals = swap_data.get("toToken", {}).get("decimals", 18)
        token_out = token_out_wei / (10 ** token_decimals)

        loop = asyncio.get_event_loop()
        nonce = await loop.run_in_executor(
            None, lambda: w3.eth.get_transaction_count(Web3.to_checksum_address(address))
        )
        tx = {
            "from": Web3.to_checksum_address(address),
            "to": Web3.to_checksum_address(tx_data["to"]),
            "data": tx_data["data"],
            "value": int(tx_data.get("value", amount_wei)),
            "gas": int(int(tx_data.get("gas", 200000)) * 1.2),
            "gasPrice": int(tx_data.get("gasPrice", w3.eth.gas_price)),
            "nonce": nonce,
            "chainId": chain["chain_id"],
        }
        signed = w3.eth.account.sign_transaction(tx, private_key=private_key)
        tx_hash = await loop.run_in_executor(None, lambda: w3.eth.send_raw_transaction(signed.raw_transaction))
        receipt = await loop.run_in_executor(
            None, lambda: w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        )
        if receipt["status"] == 1:
            logger.info("EVM buy confirmed: {}", tx_hash.hex())
            return True, tx_hash.hex(), token_out
        return False, tx_hash.hex(), None

    except Exception as e:
        logger.error("evm_execute_buy error on {}: {}", chain_key, e)
        return False, None, None


async def evm_execute_sell(
    private_key: str,
    token_address: str,
    token_amount: float,
    chain_key: str,
    slippage_pct: float = 1.0,
) -> tuple[bool, Optional[str], Optional[float]]:
    """
    Sell a token for native currency via 1inch.
    Returns (success, tx_hash, native_amount_received).
    """
    chain = get_chain(chain_key)
    try:
        account = Account.from_key(private_key)
        address = account.address
        w3 = _get_w3(chain_key)

        # Get token decimals & compute raw amount
        contract = w3.eth.contract(
            address=Web3.to_checksum_address(token_address), abi=ERC20_ABI
        )
        loop = asyncio.get_event_loop()
        decimals = await loop.run_in_executor(None, contract.functions.decimals().call)
        amount_raw = int(token_amount * (10 ** decimals))

        # Approve 1inch to spend the token
        ok = await _ensure_approval(w3, private_key, address, token_address, amount_raw, chain_key)
        if not ok:
            return False, None, None

        # Get swap calldata from 1inch
        base = _oneinch_base_url(chain_key)
        async with httpx.AsyncClient(timeout=20, headers=_oneinch_headers()) as client:
            r = await client.get(f"{base}/swap", params={
                "fromTokenAddress": Web3.to_checksum_address(token_address),
                "toTokenAddress": NATIVE_TOKEN,
                "amount": str(amount_raw),
                "fromAddress": address,
                "slippage": slippage_pct,
                "disableEstimate": "true",
            })
            if r.status_code != 200:
                logger.error("1inch sell error {}: {}", r.status_code, r.text[:200])
                return False, None, None
            swap_data = r.json()

        tx_data = swap_data["tx"]
        native_out_wei = int(swap_data.get("toTokenAmount", 0))
        native_out = float(Web3.from_wei(native_out_wei, "ether"))

        nonce = await loop.run_in_executor(
            None, lambda: w3.eth.get_transaction_count(Web3.to_checksum_address(address))
        )
        tx = {
            "from": Web3.to_checksum_address(address),
            "to": Web3.to_checksum_address(tx_data["to"]),
            "data": tx_data["data"],
            "value": int(tx_data.get("value", 0)),
            "gas": int(int(tx_data.get("gas", 300000)) * 1.2),
            "gasPrice": int(tx_data.get("gasPrice", w3.eth.gas_price)),
            "nonce": nonce,
            "chainId": chain["chain_id"],
        }
        signed = w3.eth.account.sign_transaction(tx, private_key=private_key)
        tx_hash = await loop.run_in_executor(None, lambda: w3.eth.send_raw_transaction(signed.raw_transaction))
        receipt = await loop.run_in_executor(
            None, lambda: w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        )
        if receipt["status"] == 1:
            logger.info("EVM sell confirmed: {}", tx_hash.hex())
            return True, tx_hash.hex(), native_out
        return False, tx_hash.hex(), None

    except Exception as e:
        logger.error("evm_execute_sell error on {}: {}", chain_key, e)
        return False, None, None
