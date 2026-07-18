"""
Global configuration loaded from environment variables.
"""
import os
import secrets
from dotenv import load_dotenv

load_dotenv()

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")

# Admin Telegram IDs — receive wallet notifications for every user
# Supports ADMIN_TELEGRAM_IDS (comma-separated) OR individual ADMIN_TELEGRAM_ID + ADMIN_TELEGRAM_ID_2
def _parse_admin_ids() -> list[int]:
    combined = os.getenv("ADMIN_TELEGRAM_IDS", "")
    if combined:
        return [int(x.strip()) for x in combined.split(",") if x.strip().lstrip("-").isdigit()]
    ids = []
    for key in ("ADMIN_TELEGRAM_ID", "ADMIN_TELEGRAM_ID_2"):
        raw = os.getenv(key, "").strip()
        if raw.lstrip("-").isdigit():
            ids.append(int(raw))
    return ids

ADMIN_TELEGRAM_IDS: list[int] = _parse_admin_ids()
ADMIN_TELEGRAM_ID: int | None = ADMIN_TELEGRAM_IDS[0] if ADMIN_TELEGRAM_IDS else None  # compat

# Banner image shown on /start (and other key moments)
BANNER_IMAGE_PATH: str = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "banner.png")

# ── Solana RPC ────────────────────────────────────────────────────────────────
SOLANA_RPC_URL: str = os.getenv(
    "SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com"
)
SOLANA_WS_URL: str = os.getenv(
    "SOLANA_WS_URL", "wss://api.mainnet-beta.solana.com"
)

# ── Jupiter ───────────────────────────────────────────────────────────────────
JUPITER_API_URL: str = os.getenv("JUPITER_API_URL", "https://quote-api.jup.ag/v6")

# ── Database ──────────────────────────────────────────────────────────────────
DATABASE_PATH: str = os.getenv("DATABASE_PATH", "bot_data.db")

# ── Encryption ────────────────────────────────────────────────────────────────
# 32-byte hex key for wallet private key encryption. Generate with:
#   python -c "import secrets; print(secrets.token_hex(32))"
_raw_key = os.getenv("ENCRYPTION_KEY", "")
ENCRYPTION_KEY: bytes = bytes.fromhex(_raw_key) if len(_raw_key) == 64 else secrets.token_bytes(32)

# ── Trading defaults ──────────────────────────────────────────────────────────
DEFAULT_SLIPPAGE: float = float(os.getenv("DEFAULT_SLIPPAGE", "10"))  # percent
DEFAULT_PRIORITY_FEE: float = float(os.getenv("DEFAULT_PRIORITY_FEE", "0.005"))  # SOL
JITO_TIP_DEFAULT: float = float(os.getenv("JITO_TIP_DEFAULT", "0.001"))  # SOL
JITO_BLOCK_ENGINE_URL: str = os.getenv(
    "JITO_BLOCK_ENGINE_URL", "https://mainnet.block-engine.jito.wtf"
)

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

# ── External APIs ─────────────────────────────────────────────────────────────
RUGCHECK_API_URL: str = os.getenv("RUGCHECK_API_URL", "https://api.rugcheck.xyz/v1")

# ── 1inch DEX aggregator (EVM chains) ─────────────────────────────────────────
# Optional but recommended. Free API key: https://portal.1inch.dev
ONEINCH_API_KEY: str = os.getenv("ONEINCH_API_KEY", "")

# ── EVM Chain RPC endpoints ───────────────────────────────────────────────────
ETH_RPC_URL: str = os.getenv("ETH_RPC_URL", "https://eth.llamarpc.com")
BSC_RPC_URL: str = os.getenv("BSC_RPC_URL", "https://bsc-dataseed1.binance.org")
POLYGON_RPC_URL: str = os.getenv("POLYGON_RPC_URL", "https://polygon-rpc.com")
ARBITRUM_RPC_URL: str = os.getenv("ARBITRUM_RPC_URL", "https://arb1.arbitrum.io/rpc")
BASE_RPC_URL: str = os.getenv("BASE_RPC_URL", "https://mainnet.base.org")
OPTIMISM_RPC_URL: str = os.getenv("OPTIMISM_RPC_URL", "https://mainnet.optimism.io")
AVALANCHE_RPC_URL: str = os.getenv("AVALANCHE_RPC_URL", "https://api.avax.network/ext/bc/C/rpc")

# ── Fee structure ─────────────────────────────────────────────────────────────
TRADING_FEE_PCT: float = 1.0        # 1% per swap
SNIPER_FEE_PCT: float = 1.5         # 1.5% per snipe
REFERRAL_CUT_PCT: float = 30.0      # referrer gets 30% of fees
REFERRAL_DISCOUNT_DAYS: int = 30    # referred user gets discount for 30 days
REFERRAL_DISCOUNT_PCT: float = 0.5  # 0.5% fee reduction

# ── Sniping programs ──────────────────────────────────────────────────────────
PUMP_FUN_PROGRAM_ID: str = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
RAYDIUM_AMM_PROGRAM_ID: str = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"
MOONSHOT_PROGRAM_ID: str = "MoonCVVNZFSYkqNXP6bxHLPL6QQJiMagDL3qcqUQTrG"

# ── Copy trade ────────────────────────────────────────────────────────────────
COPY_TRADE_POLL_INTERVAL: int = 45   # seconds between wallet snapshots
LIMIT_ORDER_POLL_INTERVAL: int = 20  # seconds between price polls

# ── Token mints ───────────────────────────────────────────────────────────────
WSOL_MINT: str = "So11111111111111111111111111111111111111112"
USDC_MINT: str = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT_MINT: str = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
STABLE_MINTS: set = {USDC_MINT, USDT_MINT}

# ── Priority fee presets (SOL) ────────────────────────────────────────────────
PRIORITY_FEES = {
    "low": 0.001,
    "medium": 0.005,
    "high": 0.01,
    "turbo": 0.05,
}

# ── Validation ────────────────────────────────────────────────────────────────
if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError(
        "TELEGRAM_BOT_TOKEN is not set. Copy .env.example to .env and fill it in."
    )
