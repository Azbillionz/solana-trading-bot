"""
All inline keyboard builders for the Telegram bot menus.
Every user-facing response uses inline keyboards — no text-only replies.
"""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from src.chains.registry import CHAINS, chain_label


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=data)


def _url_btn(text: str, url: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, url=url)


def _kb(*rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(list(rows))


# ── Main menu ─────────────────────────────────────────────────────────────────

def main_menu_kb(active_chain: str = "solana") -> InlineKeyboardMarkup:
    lbl = chain_label(active_chain)
    return _kb(
        [_btn("💼 Wallet", "menu:wallet"), _btn("📊 Positions", "menu:positions")],
        [_btn("🎯 Sniper", "menu:sniper"), _btn("🔁 Copy Trade", "menu:copytrade")],
        [_btn("📉 Trade", "menu:trade"), _btn("⚙️ Settings", "menu:settings")],
        [_btn("🔗 Referral", "menu:referral"), _btn("❓ Help", "menu:help")],
        [_btn(f"🌐 Chain: {lbl}", "menu:chain")],
    )


# ── Chain selector ────────────────────────────────────────────────────────────

def chain_select_kb(active_chain: str = "solana") -> InlineKeyboardMarkup:
    rows = []
    for key, info in CHAINS.items():
        tick = "✅ " if key == active_chain else ""
        rows.append([_btn(f"{tick}{info['emoji']} {info['name']} ({info['symbol']})", f"chain:select:{key}")])
    rows.append([_btn("⬅️ Back", "menu:main")])
    return _kb(*rows)


# ── Wallet menu ───────────────────────────────────────────────────────────────

def wallet_menu_kb(has_wallet: bool = True, active_chain: str = "solana") -> InlineKeyboardMarkup:
    chain_type = CHAINS.get(active_chain, {}).get("type", "solana")
    sweep_label = "🧹 Recover SOL Dust" if chain_type == "solana" else "💰 Show Balance"
    rows = [
        [_btn("➕ Create Wallet", "wallet:create"), _btn("📥 Import Wallet", "wallet:import")],
    ]
    if has_wallet:
        rows += [
            [_btn("🔄 Switch Wallet", "wallet:switch"), _btn("✏️ Rename", "wallet:rename")],
            [_btn("💰 Show Balance", "wallet:balance"), _btn(sweep_label, "wallet:sweep")],
            [_btn("🗑️ Delete Wallet", "wallet:delete")],
        ]
    rows.append([_btn("⬅️ Back", "menu:main")])
    return _kb(*rows)


def wallet_list_kb(wallets: list, action: str) -> InlineKeyboardMarkup:
    rows = []
    for w in wallets:
        wtype = w.get("wallet_type", "solana")
        icon = "◎" if wtype == "solana" else "🔷"
        label = f"{icon} {w['name']} ({w['public_key'][:6]}...)"
        rows.append([_btn(label, f"wallet:{action}:{w['public_key']}")])
    rows.append([_btn("⬅️ Back", "menu:wallet")])
    return _kb(*rows)


# ── Trade menu ────────────────────────────────────────────────────────────────

def trade_menu_kb(active_chain: str = "solana") -> InlineKeyboardMarkup:
    sym = CHAINS.get(active_chain, {}).get("symbol", "SOL")
    chain_name = CHAINS.get(active_chain, {}).get("name", "Solana")
    return _kb(
        [_btn(f"🟢 Buy Token", "trade:buy"), _btn(f"🔴 Sell Token", "trade:sell")],
        [_btn("📋 Limit Order", "trade:limit"), _btn("📊 DCA", "trade:dca")],
        [_btn(f"ℹ️ Chain: {chain_name} ({sym})", "menu:chain")],
        [_btn("⬅️ Back", "menu:main")],
    )


def confirm_trade_kb(side: str, token: str, amount: str) -> InlineKeyboardMarkup:
    data_confirm = f"trade:confirm:{side}:{token}:{amount}"
    return _kb(
        [_btn("✅ Confirm", data_confirm), _btn("❌ Cancel", "trade:cancel")],
    )


# ── Sniper menu ───────────────────────────────────────────────────────────────

def sniper_menu_kb(active: bool = False) -> InlineKeyboardMarkup:
    toggle = _btn("⏹ Stop Sniper", "sniper:stop") if active else _btn("▶️ Start Sniper", "sniper:start")
    return _kb(
        [toggle],
        [_btn("💰 Buy Amount", "sniper:set_amount"), _btn("⏱️ Delay", "sniper:set_delay")],
        [_btn("⛽ Priority Fee", "sniper:set_fee"), _btn("🛡️ Rug Filters", "sniper:filters")],
        [_btn("🎯 Auto-Sell Rules", "sniper:autosell"), _btn("🎛️ Sources", "sniper:sources")],
        [_btn("⬅️ Back", "menu:main")],
    )


def sniper_filters_kb(cfg: dict) -> InlineKeyboardMarkup:
    def tog(val: bool) -> str:
        return "✅" if val else "❌"
    return _kb(
        [_btn(f"{tog(cfg.get('check_mint_auth',1))} Mint Auth Check", "sniper:toggle:check_mint_auth")],
        [_btn(f"{tog(cfg.get('check_freeze',1))} Freeze Auth Check", "sniper:toggle:check_freeze")],
        [_btn(f"{tog(cfg.get('check_socials',0))} Socials Required", "sniper:toggle:check_socials")],
        [_btn(f"LP Burn ≥ {cfg.get('min_lp_burn_pct',80):.0f}%", "sniper:set_lp_burn")],
        [_btn(f"Max Holder Conc: {cfg.get('max_holder_conc',20):.0f}%", "sniper:set_holder_conc")],
        [_btn("⬅️ Back", "menu:sniper")],
    )


def sniper_sources_kb(cfg: dict) -> InlineKeyboardMarkup:
    def tog(val) -> str:
        return "✅" if val else "❌"
    return _kb(
        [_btn(f"{tog(cfg.get('snipe_pump',1))} pump.fun", "sniper:toggle:snipe_pump")],
        [_btn(f"{tog(cfg.get('snipe_raydium',1))} Raydium New Pools", "sniper:toggle:snipe_raydium")],
        [_btn(f"{tog(cfg.get('snipe_moonshot',0))} Moonshot", "sniper:toggle:snipe_moonshot")],
        [_btn("⬅️ Back", "menu:sniper")],
    )


# ── Copy trade menu ───────────────────────────────────────────────────────────

def copytrade_menu_kb(has_targets: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [_btn("➕ Add Target", "ct:add"), _btn("📋 List Targets", "ct:list")],
    ]
    if has_targets:
        rows.append([_btn("⚙️ Configure Target", "ct:config"), _btn("🗑️ Remove Target", "ct:remove")])
    rows.append([_btn("⬅️ Back", "menu:main")])
    return _kb(*rows)


def copytrade_target_kb(target_id: int, cfg: dict) -> InlineKeyboardMarkup:
    def tog(val) -> str:
        return "✅" if val else "❌"
    return _kb(
        [_btn(f"Fixed: {cfg.get('fixed_sol',0.1)} native", f"ct:set_amount:{target_id}")],
        [_btn(f"{tog(cfg.get('proportional',0))} Proportional", f"ct:toggle:proportional:{target_id}")],
        [_btn(f"Max Buy: {cfg.get('max_buy_sol',1)}", f"ct:set_max:{target_id}")],
        [_btn(f"Min Buy: {cfg.get('min_buy_sol',0.01)}", f"ct:set_min:{target_id}")],
        [_btn(f"{tog(cfg.get('copy_sells',1))} Copy Sells", f"ct:toggle:copy_sells:{target_id}")],
        [_btn(f"{tog(cfg.get('exclude_pump',0))} Exclude pump.fun", f"ct:toggle:exclude_pump:{target_id}")],
        [_btn(f"{tog(cfg.get('exclude_stable',1))} Exclude Stablecoins", f"ct:toggle:exclude_stable:{target_id}")],
        [_btn(f"{tog(cfg.get('dupe_buys',0))} Copy Dupe Buys", f"ct:toggle:dupe_buys:{target_id}")],
        [_btn(f"Retries: {cfg.get('retry_count',3)}", f"ct:set_retry:{target_id}")],
        [_btn("⬅️ Back", "menu:copytrade")],
    )


# ── Positions menu ────────────────────────────────────────────────────────────

def positions_menu_kb(positions: list) -> InlineKeyboardMarkup:
    rows = []
    for pos in positions:
        symbol = pos.get("token_symbol") or pos["token_mint"][:8] + "..."
        entry = pos.get("entry_sol", 0)
        chain_key = pos.get("chain", "solana")
        sym = CHAINS.get(chain_key, {}).get("symbol", "SOL")
        label = f"{symbol} | {entry:.4f} {sym}"
        rows.append([_btn(label, f"pos:detail:{pos['id']}")])
    rows.append([_btn("⬅️ Back", "menu:main")])
    return _kb(*rows)


def position_detail_kb(pos_id: int) -> InlineKeyboardMarkup:
    return _kb(
        [_btn("💰 Sell 25%", f"pos:sell:{pos_id}:25"), _btn("💰 Sell 50%", f"pos:sell:{pos_id}:50")],
        [_btn("💰 Sell 75%", f"pos:sell:{pos_id}:75"), _btn("🔴 Sell All", f"pos:sell:{pos_id}:100")],
        [_btn("🎯 Edit TP/SL Rules", f"pos:rules:{pos_id}")],
        [_btn("⬅️ Back", "menu:positions")],
    )


def position_rules_kb(pos_id: int) -> InlineKeyboardMarkup:
    return _kb(
        [_btn("🎯 Set Take-Profit", f"pos:set_tp:{pos_id}")],
        [_btn("🛑 Set Stop-Loss", f"pos:set_sl:{pos_id}")],
        [_btn("📉 Set Trailing Stop", f"pos:set_trail:{pos_id}")],
        [_btn("⏱️ Set Timer", f"pos:set_timer:{pos_id}")],
        [_btn("⬅️ Back", f"pos:detail:{pos_id}")],
    )


# ── Settings menu ─────────────────────────────────────────────────────────────

def settings_menu_kb(settings: dict) -> InlineKeyboardMarkup:
    slippage = settings.get("slippage", 10)
    pf_mode = settings.get("priority_fee_mode", "medium")
    mev = settings.get("mev_mode", "fast")
    return _kb(
        [_btn(f"📊 Slippage: {slippage}%", "settings:slippage")],
        [_btn(f"⛽ Priority Fee: {pf_mode.title()}", "settings:priority_fee")],
        [_btn(f"🛡️ MEV Mode: {mev.title()} (Solana)", "settings:mev_mode")],
        [_btn("🌐 RPC Endpoint", "settings:rpc")],
        [_btn("💱 Quote Token", "settings:quote_token")],
        [_btn("⬅️ Back", "menu:main")],
    )


def priority_fee_kb() -> InlineKeyboardMarkup:
    return _kb(
        [_btn("🐢 Low (0.001)", "settings:pf:low"), _btn("⚡ Medium (0.005)", "settings:pf:medium")],
        [_btn("🚀 High (0.01)", "settings:pf:high"), _btn("💨 Turbo (0.05)", "settings:pf:turbo")],
        [_btn("✏️ Custom", "settings:pf:custom")],
        [_btn("⬅️ Back", "menu:settings")],
    )


def mev_mode_kb() -> InlineKeyboardMarkup:
    return _kb(
        [_btn("⚡ Fast Mode (Direct RPC)", "settings:mev:fast")],
        [_btn("🛡️ Secure Mode (Jito Bundle)", "settings:mev:secure")],
        [_btn("⬅️ Back", "menu:settings")],
    )


def quote_token_kb() -> InlineKeyboardMarkup:
    return _kb(
        [_btn("◎ WSOL", "settings:quote:wsol"), _btn("💵 USDC", "settings:quote:usdc")],
        [_btn("⬅️ Back", "menu:settings")],
    )


# ── Orders (limit/DCA) menu ───────────────────────────────────────────────────

def orders_menu_kb(orders: list) -> InlineKeyboardMarkup:
    rows = []
    for o in orders:
        symbol = o.get("token_symbol") or o["token_mint"][:8] + "..."
        otype = o["order_type"].replace("_", " ").title()
        label = f"{otype} | {symbol}"
        rows.append([_btn(label, f"order:detail:{o['id']}"),
                     _btn("❌", f"order:cancel:{o['id']}")])
    rows.append([_btn("⬅️ Back", "menu:main")])
    return _kb(*rows)


# ── Referral menu ─────────────────────────────────────────────────────────────

def referral_menu_kb() -> InlineKeyboardMarkup:
    return _kb(
        [_btn("📊 My Stats", "ref:stats"), _btn("🔗 My Link", "ref:link")],
        [_btn("⬅️ Back", "menu:main")],
    )


# ── Utility ───────────────────────────────────────────────────────────────────

def back_kb(destination: str = "menu:main") -> InlineKeyboardMarkup:
    return _kb([_btn("⬅️ Back", destination)])


def confirm_cancel_kb(confirm_data: str, cancel_data: str = "menu:main") -> InlineKeyboardMarkup:
    return _kb([_btn("✅ Confirm", confirm_data), _btn("❌ Cancel", cancel_data)])
