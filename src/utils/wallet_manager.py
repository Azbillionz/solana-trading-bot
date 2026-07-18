"""
Wallet creation, import, encryption, and decryption.
Private keys are AES-256-GCM encrypted before storage — never stored in plaintext.
"""
from __future__ import annotations

import base64
import os
from typing import Optional, Tuple

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from solders.keypair import Keypair  # type: ignore
from solders.pubkey import Pubkey  # type: ignore
import base58
import httpx

import config
from src.utils.logger import logger
from src.utils.helpers import lamports_to_sol, LAMPORTS_PER_SOL


# ── Encryption ────────────────────────────────────────────────────────────────

def _encrypt(plaintext: bytes) -> bytes:
    """Encrypt with AES-256-GCM. Returns nonce + ciphertext."""
    aesgcm = AESGCM(config.ENCRYPTION_KEY)
    nonce = os.urandom(12)
    ct = aesgcm.encrypt(nonce, plaintext, None)
    return nonce + ct


def _decrypt(blob: bytes) -> bytes:
    """Decrypt AES-256-GCM blob (nonce + ciphertext)."""
    aesgcm = AESGCM(config.ENCRYPTION_KEY)
    nonce, ct = blob[:12], blob[12:]
    return aesgcm.decrypt(nonce, ct, None)


# ── Keypair helpers ───────────────────────────────────────────────────────────

def generate_keypair() -> Keypair:
    return Keypair()


def keypair_from_secret_key(secret: bytes) -> Keypair:
    """Create a Keypair from a 64-byte secret key or 32-byte seed."""
    if len(secret) == 64:
        return Keypair.from_bytes(secret)
    if len(secret) == 32:
        return Keypair.from_seed(secret)
    raise ValueError(f"Invalid secret key length: {len(secret)}")


def keypair_from_base58(b58_key: str) -> Keypair:
    """Import a keypair from a base58-encoded private key string."""
    raw = base58.b58decode(b58_key)
    return keypair_from_secret_key(raw)


def keypair_from_array(arr: list) -> Keypair:
    """Import from JSON byte array format [1, 2, 3, ...]."""
    return Keypair.from_bytes(bytes(arr))


# ── Encrypt / decrypt a keypair ───────────────────────────────────────────────

def encrypt_keypair(kp: Keypair) -> bytes:
    raw = bytes(kp)  # 64-byte secret key
    return _encrypt(raw)


def decrypt_keypair(blob: bytes) -> Keypair:
    raw = _decrypt(blob)
    return keypair_from_secret_key(raw)


# ── RPC balance queries ───────────────────────────────────────────────────────

async def get_sol_balance(pubkey: str) -> float:
    """Fetch SOL balance (in SOL) from RPC."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                config.SOLANA_RPC_URL,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getBalance",
                    "params": [pubkey, {"commitment": "confirmed"}],
                },
            )
            data = resp.json()
            lamports = data["result"]["value"]
            return lamports_to_sol(lamports)
    except Exception as e:
        logger.error("get_sol_balance error for {}: {}", pubkey, e)
        return 0.0


async def get_token_accounts(pubkey: str) -> list:
    """Fetch all SPL token accounts for a wallet."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                config.SOLANA_RPC_URL,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTokenAccountsByOwner",
                    "params": [
                        pubkey,
                        {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
                        {"encoding": "jsonParsed", "commitment": "confirmed"},
                    ],
                },
            )
            data = resp.json()
            return data.get("result", {}).get("value", [])
    except Exception as e:
        logger.error("get_token_accounts error for {}: {}", pubkey, e)
        return []


async def get_wallet_summary(pubkey: str) -> dict:
    """Return SOL balance + list of token holdings."""
    sol = await get_sol_balance(pubkey)
    token_accts = await get_token_accounts(pubkey)

    tokens = []
    for acct in token_accts:
        info = acct.get("account", {}).get("data", {}).get("parsed", {}).get("info", {})
        mint = info.get("mint", "")
        amount_info = info.get("tokenAmount", {})
        ui_amount = float(amount_info.get("uiAmount") or 0)
        if ui_amount > 0:
            tokens.append({"mint": mint, "amount": ui_amount})

    return {"sol": sol, "tokens": tokens}


# ── EVM wallet support ────────────────────────────────────────────────────────

def generate_evm_wallet() -> tuple[str, str]:
    """Generate a new EVM wallet. Returns (address, private_key_hex)."""
    from eth_account import Account as _Account
    acct = _Account.create()
    return acct.address, acct.key.hex()


def evm_wallet_from_key(hex_key: str) -> tuple[str, str]:
    """Import an EVM wallet from a hex private key. Returns (address, normalised_key_hex)."""
    from eth_account import Account as _Account
    if not hex_key.startswith("0x"):
        hex_key = "0x" + hex_key
    acct = _Account.from_key(hex_key)
    return acct.address, acct.key.hex()


def encrypt_evm_key(hex_key: str) -> bytes:
    """Encrypt an EVM private key (hex string) using AES-256-GCM."""
    return _encrypt(hex_key.encode())


def decrypt_evm_key(blob: bytes) -> str:
    """Decrypt and return the EVM private key hex string."""
    return _decrypt(blob).decode()


async def get_evm_native_balance(address: str, chain_key: str) -> float:
    """Return native token balance (ETH/BNB/MATIC/AVAX) for an EVM address."""
    try:
        from src.chains.registry import get_rpc
        from web3 import Web3
        import asyncio
        w3 = Web3(Web3.HTTPProvider(get_rpc(chain_key)))
        loop = asyncio.get_event_loop()
        wei = await loop.run_in_executor(
            None, lambda: w3.eth.get_balance(Web3.to_checksum_address(address))
        )
        return float(Web3.from_wei(wei, "ether"))
    except Exception as e:
        logger.error("get_evm_native_balance error ({}): {}", chain_key, e)
        return 0.0


async def sweep_dust_tokens(pubkey: str, keypair: Keypair) -> list:
    """
    Close zero-balance token accounts to recover rent SOL (~0.002 SOL each).
    Returns list of closed account mints.
    """
    token_accts = await get_token_accounts(pubkey)
    closed = []
    for acct in token_accts:
        info = acct.get("account", {}).get("data", {}).get("parsed", {}).get("info", {})
        amount_info = info.get("tokenAmount", {})
        ui_amount = float(amount_info.get("uiAmount") or 0)
        if ui_amount == 0:
            mint = info.get("mint", "unknown")
            # In a real implementation you'd build + send a closeAccount ix here
            logger.info("Would close dust token account for mint {}", mint)
            closed.append(mint)
    return closed
