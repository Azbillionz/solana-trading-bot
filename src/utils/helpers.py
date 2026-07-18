"""
Shared helper utilities: formatting, validation, address parsing.
"""
from __future__ import annotations

import re
from typing import Optional
import base58
from solders.pubkey import Pubkey  # type: ignore


# ── Address validation ────────────────────────────────────────────────────────

def is_valid_solana_address(address: str) -> bool:
    """Return True if address is a valid base58-encoded 32-byte public key."""
    try:
        raw = base58.b58decode(address)
        return len(raw) == 32
    except Exception:
        return False


def to_pubkey(address: str) -> Optional[Pubkey]:
    try:
        return Pubkey.from_string(address)
    except Exception:
        return None


# ── SOL / lamport conversion ──────────────────────────────────────────────────

LAMPORTS_PER_SOL = 1_000_000_000


def sol_to_lamports(sol: float) -> int:
    return int(sol * LAMPORTS_PER_SOL)


def lamports_to_sol(lamports: int) -> float:
    return lamports / LAMPORTS_PER_SOL


# ── Number formatting ─────────────────────────────────────────────────────────

def fmt_sol(amount: float, decimals: int = 4) -> str:
    return f"{amount:.{decimals}f} SOL"


def fmt_usd(amount: float) -> str:
    if abs(amount) >= 1_000_000:
        return f"${amount/1_000_000:.2f}M"
    if abs(amount) >= 1_000:
        return f"${amount/1_000:.2f}K"
    return f"${amount:.2f}"


def fmt_pct(value: float, sign: bool = True) -> str:
    prefix = "+" if sign and value >= 0 else ""
    return f"{prefix}{value:.2f}%"


def fmt_tokens(amount: float) -> str:
    if amount >= 1_000_000_000:
        return f"{amount/1_000_000_000:.2f}B"
    if amount >= 1_000_000:
        return f"{amount/1_000_000:.2f}M"
    if amount >= 1_000:
        return f"{amount/1_000:.2f}K"
    return f"{amount:.4f}"


def short_address(address: str, chars: int = 6) -> str:
    if len(address) <= chars * 2 + 3:
        return address
    return f"{address[:chars]}...{address[-chars:]}"


# ── Time formatting ───────────────────────────────────────────────────────────

def fmt_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        m, s = divmod(int(seconds), 60)
        return f"{m}m {s}s"
    h, rem = divmod(int(seconds), 3600)
    m = rem // 60
    return f"{h}h {m}m"


# ── Input parsing ─────────────────────────────────────────────────────────────

def parse_float(text: str) -> Optional[float]:
    try:
        return float(text.strip().replace(",", ""))
    except ValueError:
        return None


def parse_int(text: str) -> Optional[int]:
    try:
        return int(text.strip().replace(",", ""))
    except ValueError:
        return None


# ── Token amount from raw ─────────────────────────────────────────────────────

def raw_to_ui(raw: int, decimals: int) -> float:
    return raw / (10 ** decimals)


def ui_to_raw(amount: float, decimals: int) -> int:
    return int(amount * (10 ** decimals))


# ── Risk score label ──────────────────────────────────────────────────────────

def risk_label(score: int) -> str:
    if score <= 3:
        return "🟢 Low"
    if score <= 6:
        return "🟡 Medium"
    return "🔴 High"


# ── Referral link ─────────────────────────────────────────────────────────────

def make_referral_link(bot_username: str, user_id: int) -> str:
    return f"https://t.me/{bot_username}?start=ref_{user_id}"
