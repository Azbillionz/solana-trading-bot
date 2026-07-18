"""
Multi-chain registry — every supported blockchain and its metadata.
EVM chains all share the same wallet address (one EVM key covers all).
Solana uses its own separate keypair format.
"""
from __future__ import annotations

# 1inch native token placeholder used as fromToken when spending native currency
NATIVE_TOKEN = "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE"

CHAINS: dict[str, dict] = {
    # ── Solana ────────────────────────────────────────────────────────────────
    "solana": {
        "name": "Solana",
        "emoji": "◎",
        "symbol": "SOL",
        "type": "solana",
        "chain_id": None,
        "rpc_env": "SOLANA_RPC_URL",
        "rpc_default": "https://api.mainnet-beta.solana.com",
        "explorer_tx": "https://solscan.io/tx/",
        "explorer_token": "https://solscan.io/token/",
        "dex": "jupiter",
        "oneinch_chain_id": None,
        "wrapped_native": None,
    },
    # ── EVM Chains ────────────────────────────────────────────────────────────
    "ethereum": {
        "name": "Ethereum",
        "emoji": "🔷",
        "symbol": "ETH",
        "type": "evm",
        "chain_id": 1,
        "rpc_env": "ETH_RPC_URL",
        "rpc_default": "https://eth.llamarpc.com",
        "explorer_tx": "https://etherscan.io/tx/",
        "explorer_token": "https://etherscan.io/token/",
        "dex": "1inch",
        "oneinch_chain_id": 1,
        "wrapped_native": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",  # WETH
    },
    "bsc": {
        "name": "BNB Chain",
        "emoji": "🟡",
        "symbol": "BNB",
        "type": "evm",
        "chain_id": 56,
        "rpc_env": "BSC_RPC_URL",
        "rpc_default": "https://bsc-dataseed1.binance.org",
        "explorer_tx": "https://bscscan.com/tx/",
        "explorer_token": "https://bscscan.com/token/",
        "dex": "1inch",
        "oneinch_chain_id": 56,
        "wrapped_native": "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",  # WBNB
    },
    "polygon": {
        "name": "Polygon",
        "emoji": "🟣",
        "symbol": "POL",
        "type": "evm",
        "chain_id": 137,
        "rpc_env": "POLYGON_RPC_URL",
        "rpc_default": "https://polygon-rpc.com",
        "explorer_tx": "https://polygonscan.com/tx/",
        "explorer_token": "https://polygonscan.com/token/",
        "dex": "1inch",
        "oneinch_chain_id": 137,
        "wrapped_native": "0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270",  # WMATIC/WPOL
    },
    "arbitrum": {
        "name": "Arbitrum",
        "emoji": "🔵",
        "symbol": "ETH",
        "type": "evm",
        "chain_id": 42161,
        "rpc_env": "ARBITRUM_RPC_URL",
        "rpc_default": "https://arb1.arbitrum.io/rpc",
        "explorer_tx": "https://arbiscan.io/tx/",
        "explorer_token": "https://arbiscan.io/token/",
        "dex": "1inch",
        "oneinch_chain_id": 42161,
        "wrapped_native": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1",  # WETH on Arb
    },
    "base": {
        "name": "Base",
        "emoji": "🔹",
        "symbol": "ETH",
        "type": "evm",
        "chain_id": 8453,
        "rpc_env": "BASE_RPC_URL",
        "rpc_default": "https://mainnet.base.org",
        "explorer_tx": "https://basescan.org/tx/",
        "explorer_token": "https://basescan.org/token/",
        "dex": "1inch",
        "oneinch_chain_id": 8453,
        "wrapped_native": "0x4200000000000000000000000000000000000006",  # WETH on Base
    },
    "optimism": {
        "name": "Optimism",
        "emoji": "🔴",
        "symbol": "ETH",
        "type": "evm",
        "chain_id": 10,
        "rpc_env": "OPTIMISM_RPC_URL",
        "rpc_default": "https://mainnet.optimism.io",
        "explorer_tx": "https://optimistic.etherscan.io/tx/",
        "explorer_token": "https://optimistic.etherscan.io/token/",
        "dex": "1inch",
        "oneinch_chain_id": 10,
        "wrapped_native": "0x4200000000000000000000000000000000000006",  # WETH on OP
    },
    "avalanche": {
        "name": "Avalanche",
        "emoji": "🔺",
        "symbol": "AVAX",
        "type": "evm",
        "chain_id": 43114,
        "rpc_env": "AVALANCHE_RPC_URL",
        "rpc_default": "https://api.avax.network/ext/bc/C/rpc",
        "explorer_tx": "https://snowtrace.io/tx/",
        "explorer_token": "https://snowtrace.io/token/",
        "dex": "1inch",
        "oneinch_chain_id": 43114,
        "wrapped_native": "0xB31f66AA3C1e785363F0875A1B74E27b85FD66c7",  # WAVAX
    },
    "tron": {
        "name": "Tron",
        "emoji": "🔴",
        "symbol": "TRX",
        "type": "tron",
        "chain_id": None,
        "rpc_env": "TRON_RPC_URL",
        "rpc_default": "https://api.trongrid.io",
        "explorer_tx": "https://tronscan.org/#/transaction/",
        "explorer_token": "https://tronscan.org/#/token20/",
        "dex": "sunswap",
        "oneinch_chain_id": None,
        "wrapped_native": None,
    },
}

EVM_CHAINS = {k: v for k, v in CHAINS.items() if v["type"] == "evm"}


def get_chain(key: str) -> dict:
    """Return chain info dict, defaulting to Solana if key unknown."""
    return CHAINS.get(key, CHAINS["solana"])


def get_rpc(key: str) -> str:
    """Return the configured RPC URL for a chain, falling back to public default."""
    import os
    chain = get_chain(key)
    return os.getenv(chain["rpc_env"], chain["rpc_default"])


def chain_label(key: str) -> str:
    """Return emoji + name for display in keyboards."""
    c = get_chain(key)
    return f"{c['emoji']} {c['name']}"


def is_evm(key: str) -> bool:
    return CHAINS.get(key, {}).get("type") == "evm"
