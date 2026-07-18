"""
SQLite database layer via aiosqlite.
All CRUD operations for users, wallets, positions, orders, copy trade, etc.
"""
from __future__ import annotations

import json
import time
import aiosqlite
from typing import Any, Dict, List, Optional
import config
from src.utils.logger import logger

DB_PATH = config.DATABASE_PATH


# ── Schema ────────────────────────────────────────────────────────────────────

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS users (
    user_id         INTEGER PRIMARY KEY,
    username        TEXT,
    active_wallet   TEXT,
    settings        TEXT DEFAULT '{}',
    created_at      REAL DEFAULT (unixepoch()),
    referred_by     INTEGER,
    referral_expires REAL
);

CREATE TABLE IF NOT EXISTS wallets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(user_id),
    name            TEXT NOT NULL,
    public_key      TEXT NOT NULL,
    encrypted_key   BLOB NOT NULL,
    wallet_type     TEXT DEFAULT 'solana',
    created_at      REAL DEFAULT (unixepoch()),
    UNIQUE(user_id, public_key)
);

CREATE TABLE IF NOT EXISTS positions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(user_id),
    wallet_pubkey   TEXT NOT NULL,
    token_mint      TEXT NOT NULL,
    token_symbol    TEXT,
    entry_sol       REAL NOT NULL,
    entry_price     REAL NOT NULL,
    token_amount    REAL NOT NULL,
    current_price   REAL,
    sell_rules      TEXT DEFAULT '{}',
    opened_at       REAL DEFAULT (unixepoch()),
    closed_at       REAL,
    pnl_sol         REAL,
    status          TEXT DEFAULT 'open',
    source          TEXT DEFAULT 'manual'
);

CREATE TABLE IF NOT EXISTS orders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(user_id),
    wallet_pubkey   TEXT NOT NULL,
    order_type      TEXT NOT NULL,
    token_mint      TEXT NOT NULL,
    token_symbol    TEXT,
    target_price    REAL,
    amount_sol      REAL,
    percentage      REAL,
    status          TEXT DEFAULT 'active',
    dca_interval    INTEGER,
    dca_orders_left INTEGER,
    dca_next_at     REAL,
    created_at      REAL DEFAULT (unixepoch()),
    executed_at     REAL,
    metadata        TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS copy_trade_targets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(user_id),
    target_wallet   TEXT NOT NULL,
    label           TEXT,
    fixed_sol       REAL,
    proportional    INTEGER DEFAULT 0,
    max_buy_sol     REAL DEFAULT 1.0,
    min_buy_sol     REAL DEFAULT 0.01,
    copy_sells      INTEGER DEFAULT 1,
    exclude_pump    INTEGER DEFAULT 0,
    exclude_stable  INTEGER DEFAULT 1,
    dupe_buys       INTEGER DEFAULT 0,
    retry_count     INTEGER DEFAULT 3,
    active          INTEGER DEFAULT 1,
    created_at      REAL DEFAULT (unixepoch()),
    seen_mints      TEXT DEFAULT '{}',
    UNIQUE(user_id, target_wallet)
);

CREATE TABLE IF NOT EXISTS sniper_config (
    user_id         INTEGER PRIMARY KEY REFERENCES users(user_id),
    active          INTEGER DEFAULT 0,
    buy_amount_sol  REAL DEFAULT 0.1,
    delay_seconds   REAL DEFAULT 0,
    priority_fee    REAL DEFAULT 0.005,
    tp_pct          REAL DEFAULT 100,
    sl_pct          REAL DEFAULT 40,
    trailing_pct    REAL,
    trailing_activate_pct REAL,
    min_lp_burn_pct REAL DEFAULT 80,
    max_holder_conc REAL DEFAULT 20,
    check_mint_auth INTEGER DEFAULT 1,
    check_freeze    INTEGER DEFAULT 1,
    check_socials   INTEGER DEFAULT 0,
    snipe_pump      INTEGER DEFAULT 1,
    snipe_raydium   INTEGER DEFAULT 1,
    snipe_moonshot  INTEGER DEFAULT 0,
    updated_at      REAL DEFAULT (unixepoch())
);

CREATE TABLE IF NOT EXISTS trade_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(user_id),
    wallet_pubkey   TEXT NOT NULL,
    token_mint      TEXT NOT NULL,
    token_symbol    TEXT,
    side            TEXT NOT NULL,
    sol_amount      REAL NOT NULL,
    token_amount    REAL NOT NULL,
    price_sol       REAL,
    tx_sig          TEXT,
    fee_sol         REAL DEFAULT 0,
    executed_at     REAL DEFAULT (unixepoch()),
    source          TEXT DEFAULT 'manual'
);

CREATE TABLE IF NOT EXISTS referrals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    referrer_id     INTEGER NOT NULL REFERENCES users(user_id),
    referred_id     INTEGER NOT NULL REFERENCES users(user_id),
    fees_earned_sol REAL DEFAULT 0,
    created_at      REAL DEFAULT (unixepoch()),
    UNIQUE(referred_id)
);
"""


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
        # Migrations for databases created before multi-chain support
        _migrations = [
            "ALTER TABLE wallets ADD COLUMN wallet_type TEXT DEFAULT 'solana'",
            "ALTER TABLE positions ADD COLUMN chain TEXT DEFAULT 'solana'",
            "ALTER TABLE orders ADD COLUMN chain TEXT DEFAULT 'solana'",
            "ALTER TABLE trade_history ADD COLUMN chain TEXT DEFAULT 'solana'",
        ]
        for sql in _migrations:
            try:
                await db.execute(sql)
            except Exception:
                pass  # Column already exists — SQLite has no IF NOT EXISTS for columns
        await db.commit()
    logger.info("Database initialised at {}", DB_PATH)


# ── Generic helpers ───────────────────────────────────────────────────────────

async def _fetch_one(query: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query, params) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def _fetch_all(query: str, params: tuple = ()) -> List[Dict[str, Any]]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query, params) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def _execute(query: str, params: tuple = ()) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(query, params)
        await db.commit()
        return cur.lastrowid or 0


# ── Users ─────────────────────────────────────────────────────────────────────

async def get_or_create_user(user_id: int, username: str = "") -> Dict[str, Any]:
    user = await _fetch_one("SELECT * FROM users WHERE user_id=?", (user_id,))
    if not user:
        await _execute(
            "INSERT OR IGNORE INTO users(user_id,username) VALUES(?,?)",
            (user_id, username or ""),
        )
        user = await _fetch_one("SELECT * FROM users WHERE user_id=?", (user_id,))
        # create default sniper config
        await _execute(
            "INSERT OR IGNORE INTO sniper_config(user_id) VALUES(?)", (user_id,)
        )
    return user  # type: ignore


async def update_user_setting(user_id: int, key: str, value: Any) -> None:
    user = await _fetch_one("SELECT settings FROM users WHERE user_id=?", (user_id,))
    settings: dict = json.loads(user["settings"]) if user else {}
    settings[key] = value
    await _execute(
        "UPDATE users SET settings=? WHERE user_id=?",
        (json.dumps(settings), user_id),
    )


async def get_user_settings(user_id: int) -> Dict[str, Any]:
    user = await _fetch_one("SELECT settings FROM users WHERE user_id=?", (user_id,))
    if not user:
        return {}
    return json.loads(user["settings"])


async def set_active_wallet(user_id: int, pubkey: str) -> None:
    await _execute(
        "UPDATE users SET active_wallet=? WHERE user_id=?", (pubkey, user_id)
    )


async def get_active_wallet_pubkey(user_id: int) -> Optional[str]:
    row = await _fetch_one("SELECT active_wallet FROM users WHERE user_id=?", (user_id,))
    return row["active_wallet"] if row else None


# ── Wallets ───────────────────────────────────────────────────────────────────

async def save_wallet(
    user_id: int, name: str, public_key: str, encrypted_key: bytes,
    wallet_type: str = "solana",
) -> int:
    rid = await _execute(
        "INSERT OR IGNORE INTO wallets(user_id,name,public_key,encrypted_key,wallet_type) VALUES(?,?,?,?,?)",
        (user_id, name, public_key, encrypted_key, wallet_type),
    )
    if wallet_type == "solana":
        await set_active_wallet(user_id, public_key)
    else:
        await update_user_setting(user_id, "active_evm_wallet", public_key)
    return rid


async def get_wallets(user_id: int, wallet_type: Optional[str] = None) -> List[Dict[str, Any]]:
    if wallet_type:
        return await _fetch_all(
            "SELECT * FROM wallets WHERE user_id=? AND wallet_type=? ORDER BY created_at",
            (user_id, wallet_type),
        )
    return await _fetch_all(
        "SELECT * FROM wallets WHERE user_id=? ORDER BY created_at", (user_id,)
    )


async def get_active_evm_wallet(user_id: int) -> Optional[str]:
    """Return the address of the user's currently active EVM wallet."""
    settings = await get_user_settings(user_id)
    return settings.get("active_evm_wallet")


async def get_wallet(user_id: int, public_key: str) -> Optional[Dict[str, Any]]:
    return await _fetch_one(
        "SELECT * FROM wallets WHERE user_id=? AND public_key=?",
        (user_id, public_key),
    )


async def rename_wallet(user_id: int, public_key: str, new_name: str) -> None:
    await _execute(
        "UPDATE wallets SET name=? WHERE user_id=? AND public_key=?",
        (new_name, user_id, public_key),
    )


async def delete_wallet(user_id: int, public_key: str) -> None:
    await _execute(
        "DELETE FROM wallets WHERE user_id=? AND public_key=?",
        (user_id, public_key),
    )


# ── Positions ─────────────────────────────────────────────────────────────────

async def open_position(
    user_id: int,
    wallet_pubkey: str,
    token_mint: str,
    token_symbol: str,
    entry_sol: float,
    entry_price: float,
    token_amount: float,
    sell_rules: dict,
    source: str = "manual",
) -> int:
    return await _execute(
        """INSERT INTO positions
           (user_id,wallet_pubkey,token_mint,token_symbol,entry_sol,entry_price,
            token_amount,sell_rules,source)
           VALUES(?,?,?,?,?,?,?,?,?)""",
        (
            user_id, wallet_pubkey, token_mint, token_symbol,
            entry_sol, entry_price, token_amount,
            json.dumps(sell_rules), source,
        ),
    )


async def get_open_positions(user_id: int) -> List[Dict[str, Any]]:
    rows = await _fetch_all(
        "SELECT * FROM positions WHERE user_id=? AND status='open' ORDER BY opened_at DESC",
        (user_id,),
    )
    for r in rows:
        r["sell_rules"] = json.loads(r["sell_rules"])
    return rows


async def get_all_open_positions() -> List[Dict[str, Any]]:
    rows = await _fetch_all(
        "SELECT * FROM positions WHERE status='open'",
    )
    for r in rows:
        r["sell_rules"] = json.loads(r["sell_rules"])
    return rows


async def update_position_price(position_id: int, current_price: float) -> None:
    await _execute(
        "UPDATE positions SET current_price=? WHERE id=?",
        (current_price, position_id),
    )


async def close_position(position_id: int, pnl_sol: float) -> None:
    await _execute(
        "UPDATE positions SET status='closed',closed_at=?,pnl_sol=? WHERE id=?",
        (time.time(), pnl_sol, position_id),
    )


async def update_sell_rules(position_id: int, sell_rules: dict) -> None:
    await _execute(
        "UPDATE positions SET sell_rules=? WHERE id=?",
        (json.dumps(sell_rules), position_id),
    )


# ── Orders ────────────────────────────────────────────────────────────────────

async def create_order(
    user_id: int,
    wallet_pubkey: str,
    order_type: str,
    token_mint: str,
    token_symbol: str,
    target_price: Optional[float] = None,
    amount_sol: Optional[float] = None,
    percentage: Optional[float] = None,
    dca_interval: Optional[int] = None,
    dca_orders_left: Optional[int] = None,
    dca_next_at: Optional[float] = None,
    metadata: Optional[dict] = None,
) -> int:
    return await _execute(
        """INSERT INTO orders
           (user_id,wallet_pubkey,order_type,token_mint,token_symbol,
            target_price,amount_sol,percentage,dca_interval,dca_orders_left,
            dca_next_at,metadata)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            user_id, wallet_pubkey, order_type, token_mint, token_symbol,
            target_price, amount_sol, percentage,
            dca_interval, dca_orders_left, dca_next_at,
            json.dumps(metadata or {}),
        ),
    )


async def get_active_orders(user_id: int) -> List[Dict[str, Any]]:
    rows = await _fetch_all(
        "SELECT * FROM orders WHERE user_id=? AND status='active' ORDER BY created_at DESC",
        (user_id,),
    )
    for r in rows:
        r["metadata"] = json.loads(r.get("metadata") or "{}")
    return rows


async def get_all_active_orders() -> List[Dict[str, Any]]:
    rows = await _fetch_all("SELECT * FROM orders WHERE status='active'")
    for r in rows:
        r["metadata"] = json.loads(r.get("metadata") or "{}")
    return rows


async def cancel_order(order_id: int, user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "UPDATE orders SET status='cancelled' WHERE id=? AND user_id=? AND status='active'",
            (order_id, user_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def mark_order_executed(order_id: int) -> None:
    await _execute(
        "UPDATE orders SET status='executed',executed_at=? WHERE id=?",
        (time.time(), order_id),
    )


async def update_dca_order(order_id: int, dca_orders_left: int, dca_next_at: float) -> None:
    if dca_orders_left <= 0:
        await _execute(
            "UPDATE orders SET status='executed',executed_at=?,dca_orders_left=0 WHERE id=?",
            (time.time(), order_id),
        )
    else:
        await _execute(
            "UPDATE orders SET dca_orders_left=?,dca_next_at=? WHERE id=?",
            (dca_orders_left, dca_next_at, order_id),
        )


# ── Copy trade ────────────────────────────────────────────────────────────────

async def add_copy_target(
    user_id: int,
    target_wallet: str,
    label: str = "",
    fixed_sol: float = 0.1,
    proportional: bool = False,
    max_buy_sol: float = 1.0,
    min_buy_sol: float = 0.01,
    copy_sells: bool = True,
    exclude_pump: bool = False,
    exclude_stable: bool = True,
    dupe_buys: bool = False,
    retry_count: int = 3,
) -> int:
    return await _execute(
        """INSERT OR REPLACE INTO copy_trade_targets
           (user_id,target_wallet,label,fixed_sol,proportional,max_buy_sol,
            min_buy_sol,copy_sells,exclude_pump,exclude_stable,dupe_buys,retry_count)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            user_id, target_wallet, label, fixed_sol, int(proportional),
            max_buy_sol, min_buy_sol, int(copy_sells), int(exclude_pump),
            int(exclude_stable), int(dupe_buys), retry_count,
        ),
    )


async def get_copy_targets(user_id: int) -> List[Dict[str, Any]]:
    rows = await _fetch_all(
        "SELECT * FROM copy_trade_targets WHERE user_id=? AND active=1",
        (user_id,),
    )
    for r in rows:
        r["seen_mints"] = json.loads(r.get("seen_mints") or "{}")
    return rows


async def get_all_copy_targets() -> List[Dict[str, Any]]:
    rows = await _fetch_all("SELECT * FROM copy_trade_targets WHERE active=1")
    for r in rows:
        r["seen_mints"] = json.loads(r.get("seen_mints") or "{}")
    return rows


async def remove_copy_target(user_id: int, target_wallet: str) -> None:
    await _execute(
        "UPDATE copy_trade_targets SET active=0 WHERE user_id=? AND target_wallet=?",
        (user_id, target_wallet),
    )


async def update_seen_mints(target_id: int, seen_mints: dict) -> None:
    await _execute(
        "UPDATE copy_trade_targets SET seen_mints=? WHERE id=?",
        (json.dumps(seen_mints), target_id),
    )


# ── Sniper config ─────────────────────────────────────────────────────────────

async def get_sniper_config(user_id: int) -> Optional[Dict[str, Any]]:
    return await _fetch_one(
        "SELECT * FROM sniper_config WHERE user_id=?", (user_id,)
    )


async def update_sniper_config(user_id: int, **kwargs: Any) -> None:
    if not kwargs:
        return
    sets = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [user_id]
    await _execute(
        f"UPDATE sniper_config SET {sets}, updated_at=unixepoch() WHERE user_id=?",
        tuple(vals),
    )


async def set_sniper_active(user_id: int, active: bool) -> None:
    await _execute(
        "UPDATE sniper_config SET active=? WHERE user_id=?",
        (int(active), user_id),
    )


# ── Trade history ─────────────────────────────────────────────────────────────

async def record_trade(
    user_id: int,
    wallet_pubkey: str,
    token_mint: str,
    token_symbol: str,
    side: str,
    sol_amount: float,
    token_amount: float,
    price_sol: float,
    tx_sig: str,
    fee_sol: float = 0.0,
    source: str = "manual",
) -> None:
    await _execute(
        """INSERT INTO trade_history
           (user_id,wallet_pubkey,token_mint,token_symbol,side,sol_amount,
            token_amount,price_sol,tx_sig,fee_sol,source)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (
            user_id, wallet_pubkey, token_mint, token_symbol, side,
            sol_amount, token_amount, price_sol, tx_sig, fee_sol, source,
        ),
    )


async def get_trade_history(user_id: int, limit: int = 100) -> List[Dict[str, Any]]:
    return await _fetch_all(
        "SELECT * FROM trade_history WHERE user_id=? ORDER BY executed_at DESC LIMIT ?",
        (user_id, limit),
    )


# ── Referrals ─────────────────────────────────────────────────────────────────

async def register_referral(referrer_id: int, referred_id: int, expires_at: float) -> None:
    await _execute(
        "INSERT OR IGNORE INTO referrals(referrer_id,referred_id) VALUES(?,?)",
        (referrer_id, referred_id),
    )
    await _execute(
        "UPDATE users SET referred_by=?,referral_expires=? WHERE user_id=?",
        (referrer_id, expires_at, referred_id),
    )


async def get_referral_stats(user_id: int) -> Dict[str, Any]:
    row = await _fetch_one(
        "SELECT COUNT(*) as count, SUM(fees_earned_sol) as total FROM referrals WHERE referrer_id=?",
        (user_id,),
    )
    return {"count": row["count"] or 0, "total_sol": row["total"] or 0.0}


async def credit_referral_fee(referrer_id: int, fee_sol: float) -> None:
    await _execute(
        "UPDATE referrals SET fees_earned_sol=fees_earned_sol+? WHERE referrer_id=?",
        (fee_sol, referrer_id),
    )
