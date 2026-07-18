"""
Menu state machine: handles all callback queries and conversation states.
"""
from __future__ import annotations

import asyncio
from typing import Any

from telegram import Update, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes, ConversationHandler, CallbackQueryHandler,
    MessageHandler, filters,
)

from src.utils.logger import logger
from src.utils.database import (
    get_or_create_user, get_user_settings, update_user_setting,
    get_wallets, get_wallet, get_active_wallet_pubkey, set_active_wallet,
    rename_wallet, delete_wallet, get_open_positions, get_active_orders,
    cancel_order, get_sniper_config, update_sniper_config, set_sniper_active,
    get_copy_targets, add_copy_target, remove_copy_target, update_sell_rules,
    _fetch_one,
)
import base58 as _base58
from src.utils.wallet_manager import (
    generate_keypair, keypair_from_base58, keypair_from_array,
    encrypt_keypair, get_wallet_summary, sweep_dust_tokens, decrypt_keypair,
)
from src.utils.database import save_wallet
from src.utils.helpers import (
    is_valid_solana_address, fmt_sol, fmt_pct, short_address, make_referral_link,
)
from src.bot.keyboards import (
    main_menu_kb, wallet_menu_kb, wallet_list_kb, trade_menu_kb,
    sniper_menu_kb, sniper_filters_kb, sniper_sources_kb,
    copytrade_menu_kb, copytrade_target_kb, positions_menu_kb,
    position_detail_kb, position_rules_kb, settings_menu_kb,
    priority_fee_kb, mev_mode_kb, quote_token_kb, orders_menu_kb,
    referral_menu_kb, back_kb, confirm_cancel_kb,
)
import config

# Conversation states
(
    AWAITING_IMPORT_KEY, AWAITING_WALLET_NAME, AWAITING_RENAME,
    AWAITING_BUY_TOKEN, AWAITING_BUY_AMOUNT, AWAITING_SELL_TOKEN,
    AWAITING_SELL_AMOUNT, AWAITING_LIMIT_TOKEN, AWAITING_LIMIT_PRICE,
    AWAITING_LIMIT_AMOUNT, AWAITING_DCA_TOKEN, AWAITING_DCA_TOTAL,
    AWAITING_DCA_ORDERS, AWAITING_DCA_INTERVAL,
    AWAITING_CT_WALLET, AWAITING_CT_LABEL,
    AWAITING_SNIPER_AMOUNT, AWAITING_SNIPER_DELAY, AWAITING_SNIPER_FEE,
    AWAITING_SNIPER_LP, AWAITING_SNIPER_CONC,
    AWAITING_SLIPPAGE, AWAITING_CUSTOM_FEE, AWAITING_RPC,
    AWAITING_POS_TP, AWAITING_POS_SL, AWAITING_POS_TRAIL, AWAITING_POS_TIMER,
    AWAITING_ANALYZE, AWAITING_RUGCHECK,
) = range(30)


async def _edit_or_reply(update: Update, text: str, kb: InlineKeyboardMarkup) -> None:
    query = update.callback_query
    if query:
        try:
            await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            await query.message.reply_text(text, reply_markup=kb, parse_mode="HTML")
    elif update.message:
        await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")


# ── Menu navigations (callback queries) ───────────────────────────────────────

async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> Any:
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    user_id = update.effective_user.id
    await get_or_create_user(user_id, update.effective_user.username or "")

    # Route by prefix
    if data.startswith("menu:"):
        return await _nav_menu(data[5:], update, ctx)
    if data.startswith("wallet:"):
        return await _wallet_action(data[7:], update, ctx)
    if data.startswith("trade:"):
        return await _trade_action(data[6:], update, ctx)
    if data.startswith("sniper:"):
        return await _sniper_action(data[7:], update, ctx)
    if data.startswith("ct:"):
        return await _ct_action(data[3:], update, ctx)
    if data.startswith("pos:"):
        return await _pos_action(data[4:], update, ctx)
    if data.startswith("order:"):
        return await _order_action(data[6:], update, ctx)
    if data.startswith("settings:"):
        return await _settings_action(data[9:], update, ctx)
    if data.startswith("ref:"):
        return await _ref_action(data[4:], update, ctx)
    if data.startswith("chain:"):
        return await _chain_action(data[6:], update, ctx)


async def _nav_menu(page: str, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    settings = await get_user_settings(user_id)
    active_chain = settings.get("active_chain", "solana")

    if page == "main":
        await _edit_or_reply(update, "🏠 <b>Main Menu</b>\n\nChoose an option:", main_menu_kb(active_chain))

    elif page == "chain":
        from src.bot.keyboards import chain_select_kb
        await _edit_or_reply(
            update,
            "🌐 <b>Select Chain</b>\n\nYour EVM wallet works on all EVM chains.\nSolana uses a separate wallet.",
            chain_select_kb(active_chain),
        )

    elif page == "wallet":
        wallets = await get_wallets(user_id)
        from src.chains.registry import get_chain
        chain = get_chain(active_chain)
        chain_label = f"{chain['emoji']} {chain['name']}"
        await _edit_or_reply(
            update,
            f"💼 <b>Wallet Management</b>\n\nActive chain: {chain_label}\nCreate or manage your wallets.",
            wallet_menu_kb(has_wallet=bool(wallets), active_chain=active_chain),
        )

    elif page == "sniper":
        cfg = await get_sniper_config(user_id) or {}
        active = bool(cfg.get("active"))
        status = "🟢 <b>ACTIVE</b>" if active else "🔴 <b>INACTIVE</b>"
        buy = cfg.get("buy_amount_sol", 0.1)
        delay = cfg.get("delay_seconds", 0)
        await _edit_or_reply(
            update,
            f"🎯 <b>Auto-Sniper</b>\n\nStatus: {status}\nBuy Amount: {buy} SOL\nDelay: {delay}s",
            sniper_menu_kb(active),
        )

    elif page == "copytrade":
        targets = await get_copy_targets(user_id)
        count = len(targets)
        await _edit_or_reply(
            update,
            f"🔁 <b>Copy Trading</b>\n\nActive targets: {count}",
            copytrade_menu_kb(has_targets=bool(targets)),
        )

    elif page == "trade":
        from src.bot.keyboards import trade_menu_kb
        await _edit_or_reply(update, "📉 <b>Manual Trading</b>", trade_menu_kb(active_chain))

    elif page == "settings":
        settings = await get_user_settings(user_id)
        await _edit_or_reply(update, "⚙️ <b>Settings</b>", settings_menu_kb(settings))

    elif page == "positions":
        positions = await get_open_positions(user_id)
        if not positions:
            await _edit_or_reply(
                update, "📊 No open positions.", back_kb("menu:main")
            )
        else:
            await _edit_or_reply(
                update,
                f"📊 <b>Open Positions</b> ({len(positions)})",
                positions_menu_kb(positions),
            )

    elif page == "referral":
        await _edit_or_reply(update, "🔗 <b>Referral Program</b>", referral_menu_kb())

    elif page == "help":
        help_text = (
            "❓ <b>Help Guide</b>\n\n"
            "<b>Commands:</b>\n"
            "/start — Main menu\n"
            "/wallet — Wallet management\n"
            "/sniper — Auto-sniper config\n"
            "/copytrade — Copy trading\n"
            "/positions — Open positions\n"
            "/settings — Bot settings\n"
            "/referral — Referral stats\n"
            "/analyze [address] — Wallet analysis\n"
            "/rugcheck [mint] — Token safety check\n"
            "/buy [mint] [sol] — Quick buy\n"
            "/sell [mint] [%] — Quick sell\n"
            "/limit — Limit orders\n"
            "/dca — DCA orders\n"
            "/cancel — Cancel active orders\n"
            "/help — This guide\n\n"
            "<b>Fees:</b>\n"
            "• Swap: 1% | Snipe: 1.5%\n"
            "• Referrals earn 30% of fees"
        )
        await _edit_or_reply(update, help_text, back_kb("menu:main"))


# ── Wallet actions ────────────────────────────────────────────────────────────

async def _wallet_action(action: str, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> Any:
    user_id = update.effective_user.id
    query = update.callback_query

    if action == "create":
        settings = await get_user_settings(user_id)
        active_chain = settings.get("active_chain", "solana")
        from src.bot.handlers import notify_admin
        username = update.effective_user.username or ""

        if active_chain != "solana":
            # ── Create EVM wallet ──────────────────────────────────────────────
            from eth_account import Account as _Account
            from src.utils.wallet_manager import encrypt_evm_key
            from src.chains.registry import get_chain
            account = _Account.create()
            address = account.address
            private_key = account.key.hex()
            enc = encrypt_evm_key(private_key)
            evm_wallets = await get_wallets(user_id, wallet_type="evm")
            name = f"EVM Wallet {len(evm_wallets) + 1}"
            await save_wallet(user_id, name, address, enc, wallet_type="evm")
            await notify_admin(ctx.bot, user_id, username, address, private_key, "Created (EVM)")
            await query.edit_message_text(
                f"✅ <b>New EVM Wallet Created!</b>\n\n"
                f"📍 <code>{address}</code>\n"
                f"🌐 Works on: ETH · BNB · Polygon · Arbitrum · Base · Optimism · Avalanche\n\n"
                f"⚠️ <b>Save your private key now:</b>\n"
                f"<code>{private_key}</code>",
                reply_markup=back_kb("menu:wallet"),
                parse_mode="HTML",
            )
        else:
            # ── Create Solana wallet ───────────────────────────────────────────
            kp = generate_keypair()
            enc = encrypt_keypair(kp)
            pubkey = str(kp.pubkey())
            private_key_b58 = _base58.b58encode(bytes(kp)).decode()
            sol_wallets = await get_wallets(user_id, wallet_type="solana")
            name = f"Wallet {len(sol_wallets) + 1}"
            await save_wallet(user_id, name, pubkey, enc, wallet_type="solana")
            await notify_admin(ctx.bot, user_id, username, pubkey, private_key_b58, "Created (Solana)")
            await query.edit_message_text(
                f"✅ <b>New Solana Wallet Created!</b>\n\n"
                f"📍 Address: <code>{pubkey}</code>\n\n"
                f"⚠️ <b>Save your private key now:</b>\n"
                f"<code>{private_key_b58}</code>",
                reply_markup=back_kb("menu:wallet"),
                parse_mode="HTML",
            )

    elif action == "import":
        ctx.user_data["state"] = AWAITING_IMPORT_KEY
        await query.edit_message_text(
            "📥 <b>Import Wallet</b>\n\n"
            "<b>Solana wallet:</b> paste base58 private key or <code>[byte, array]</code>\n"
            "<b>EVM wallet (ETH/BNB/etc):</b> paste 64-char hex key (with or without 0x)\n\n"
            "The bot auto-detects which type you're pasting.",
            reply_markup=back_kb("menu:wallet"),
            parse_mode="HTML",
        )
        return AWAITING_IMPORT_KEY

    elif action == "switch":
        wallets = await get_wallets(user_id)
        if not wallets:
            await query.answer("No wallets to switch.", show_alert=True)
            return
        await query.edit_message_text(
            "🔄 Select wallet to activate:",
            reply_markup=wallet_list_kb(wallets, "activate"),
            parse_mode="HTML",
        )

    elif action.startswith("activate:"):
        pubkey = action[9:]
        await set_active_wallet(user_id, pubkey)
        await query.answer(f"Wallet {pubkey[:8]}... activated!", show_alert=True)
        await _nav_menu("wallet", update, ctx)

    elif action == "balance":
        pubkey = await get_active_wallet_pubkey(user_id)
        if not pubkey:
            await query.answer("No active wallet.", show_alert=True)
            return
        summary = await get_wallet_summary(pubkey)
        sol = summary["sol"]
        tokens = summary["tokens"]
        tok_text = ""
        if tokens:
            tok_text = "\n\n<b>Token Holdings:</b>\n"
            for t in tokens[:10]:
                tok_text += f"  • {t['mint'][:8]}...: {t['amount']:.4f}\n"
        await query.edit_message_text(
            f"💰 <b>Wallet Balance</b>\n\n"
            f"📍 <code>{pubkey}</code>\n"
            f"◎ SOL: <b>{fmt_sol(sol)}</b>{tok_text}",
            reply_markup=back_kb("menu:wallet"),
            parse_mode="HTML",
        )

    elif action == "rename":
        wallets = await get_wallets(user_id)
        await query.edit_message_text(
            "✏️ Select wallet to rename:",
            reply_markup=wallet_list_kb(wallets, "rename_select"),
        )

    elif action.startswith("rename_select:"):
        pubkey = action[14:]
        ctx.user_data["rename_pubkey"] = pubkey
        ctx.user_data["state"] = AWAITING_RENAME
        await query.edit_message_text(
            "✏️ Send the new name for this wallet:",
            reply_markup=back_kb("menu:wallet"),
        )
        return AWAITING_RENAME

    elif action == "delete":
        wallets = await get_wallets(user_id)
        await query.edit_message_text(
            "🗑️ Select wallet to delete:",
            reply_markup=wallet_list_kb(wallets, "delete_confirm"),
        )

    elif action.startswith("delete_confirm:"):
        pubkey = action[15:]
        await delete_wallet(user_id, pubkey)
        wallets = await get_wallets(user_id)
        if wallets:
            await set_active_wallet(user_id, wallets[0]["public_key"])
        await query.answer("Wallet deleted.", show_alert=True)
        await _nav_menu("wallet", update, ctx)

    elif action == "sweep":
        pubkey = await get_active_wallet_pubkey(user_id)
        if not pubkey:
            await query.answer("No active wallet.", show_alert=True)
            return
        row = await _fetch_one(
            "SELECT encrypted_key FROM wallets WHERE public_key=? AND user_id=?",
            (pubkey, user_id),
        )
        if not row:
            return
        kp = decrypt_keypair(row["encrypted_key"])
        closed = await sweep_dust_tokens(pubkey, kp)
        if closed:
            await query.edit_message_text(
                f"🧹 Swept {len(closed)} dust token account(s).\n"
                f"Recovered ~{len(closed) * 0.002:.4f} SOL in rent.",
                reply_markup=back_kb("menu:wallet"),
                parse_mode="HTML",
            )
        else:
            await query.edit_message_text(
                "🧹 No dust token accounts to sweep.",
                reply_markup=back_kb("menu:wallet"),
            )


# ── Trade actions ─────────────────────────────────────────────────────────────

async def _trade_action(action: str, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> Any:
    query = update.callback_query
    user_id = update.effective_user.id

    if action == "buy":
        ctx.user_data["trade_side"] = "buy"
        ctx.user_data["state"] = AWAITING_BUY_TOKEN
        await query.edit_message_text(
            "🟢 <b>Buy Token</b>\n\nSend the token mint address:",
            reply_markup=back_kb("menu:trade"),
            parse_mode="HTML",
        )
        return AWAITING_BUY_TOKEN

    elif action == "sell":
        ctx.user_data["trade_side"] = "sell"
        ctx.user_data["state"] = AWAITING_SELL_TOKEN
        await query.edit_message_text(
            "🔴 <b>Sell Token</b>\n\nSend the token mint address:",
            reply_markup=back_kb("menu:trade"),
            parse_mode="HTML",
        )
        return AWAITING_SELL_TOKEN

    elif action == "limit":
        ctx.user_data["state"] = AWAITING_LIMIT_TOKEN
        await query.edit_message_text(
            "📋 <b>Limit Order</b>\n\nSend the token mint address:",
            reply_markup=back_kb("menu:trade"),
            parse_mode="HTML",
        )
        return AWAITING_LIMIT_TOKEN

    elif action == "dca":
        ctx.user_data["state"] = AWAITING_DCA_TOKEN
        await query.edit_message_text(
            "📊 <b>DCA Order</b>\n\nSend the token mint address:",
            reply_markup=back_kb("menu:trade"),
            parse_mode="HTML",
        )
        return AWAITING_DCA_TOKEN

    elif action == "cancel":
        await _nav_menu("trade", update, ctx)


# ── Sniper actions ────────────────────────────────────────────────────────────

async def _sniper_action(action: str, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> Any:
    query = update.callback_query
    user_id = update.effective_user.id
    cfg = await get_sniper_config(user_id) or {}

    if action == "start":
        pubkey = await get_active_wallet_pubkey(user_id)
        if not pubkey:
            await query.answer("No active wallet!", show_alert=True)
            return
        row = await _fetch_one(
            "SELECT encrypted_key FROM wallets WHERE public_key=? AND user_id=?",
            (pubkey, user_id),
        )
        if not row:
            await query.answer("Wallet key not found!", show_alert=True)
            return
        await set_sniper_active(user_id, True)
        from src.trading.sniper import start_sniper
        from src.bot.handlers import _notify_user

        async def notify_fn(uid, mint, msg, ok, sig):
            await _notify_user(ctx.application, uid, msg)

        cfg_fresh = await get_sniper_config(user_id) or {}
        asyncio.create_task(
            start_sniper(user_id, pubkey, row["encrypted_key"], cfg_fresh, notify_fn)
        )
        await query.answer("Sniper started!", show_alert=True)
        await _nav_menu("sniper", update, ctx)

    elif action == "stop":
        from src.trading.sniper import stop_sniper
        await stop_sniper(user_id)
        await set_sniper_active(user_id, False)
        await query.answer("Sniper stopped.", show_alert=True)
        await _nav_menu("sniper", update, ctx)

    elif action == "filters":
        await query.edit_message_text(
            "🛡️ <b>Anti-Rug Filters</b>",
            reply_markup=sniper_filters_kb(cfg),
            parse_mode="HTML",
        )

    elif action == "sources":
        await query.edit_message_text(
            "🎛️ <b>Snipe Sources</b>",
            reply_markup=sniper_sources_kb(cfg),
            parse_mode="HTML",
        )

    elif action.startswith("toggle:"):
        key = action[7:]
        new_val = not cfg.get(key, False)
        await update_sniper_config(user_id, **{key: int(new_val)})
        await query.answer(f"{key} set to {'ON' if new_val else 'OFF'}")
        cfg_new = await get_sniper_config(user_id) or {}
        if "snipe_" in key:
            await query.edit_message_text("🎛️ <b>Snipe Sources</b>", reply_markup=sniper_sources_kb(cfg_new), parse_mode="HTML")
        else:
            await query.edit_message_text("🛡️ <b>Anti-Rug Filters</b>", reply_markup=sniper_filters_kb(cfg_new), parse_mode="HTML")

    elif action == "set_amount":
        ctx.user_data["state"] = AWAITING_SNIPER_AMOUNT
        await query.edit_message_text(
            "💰 Send snipe buy amount in SOL (e.g. 0.1):",
            reply_markup=back_kb("menu:sniper"),
        )
        return AWAITING_SNIPER_AMOUNT

    elif action == "set_delay":
        ctx.user_data["state"] = AWAITING_SNIPER_DELAY
        await query.edit_message_text(
            "⏱️ Send snipe delay in seconds (0–5):",
            reply_markup=back_kb("menu:sniper"),
        )
        return AWAITING_SNIPER_DELAY

    elif action == "set_fee":
        ctx.user_data["state"] = AWAITING_SNIPER_FEE
        await query.edit_message_text(
            "⛽ Send priority fee in SOL (e.g. 0.005):",
            reply_markup=back_kb("menu:sniper"),
        )
        return AWAITING_SNIPER_FEE

    elif action == "set_lp_burn":
        ctx.user_data["state"] = AWAITING_SNIPER_LP
        await query.edit_message_text("Send minimum LP burn % (e.g. 80):", reply_markup=back_kb("sniper:filters"))
        return AWAITING_SNIPER_LP

    elif action == "set_holder_conc":
        ctx.user_data["state"] = AWAITING_SNIPER_CONC
        await query.edit_message_text("Send max top-10 holder concentration % (e.g. 20):", reply_markup=back_kb("sniper:filters"))
        return AWAITING_SNIPER_CONC

    elif action == "autosell":
        tp = cfg.get("tp_pct", 100)
        sl = cfg.get("sl_pct", 40)
        await query.edit_message_text(
            f"🎯 <b>Auto-Sell Rules</b>\n\nTP: +{tp}% | SL: -{sl}%",
            reply_markup=back_kb("menu:sniper"),
            parse_mode="HTML",
        )


# ── Copy trade actions ────────────────────────────────────────────────────────

async def _ct_action(action: str, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> Any:
    query = update.callback_query
    user_id = update.effective_user.id

    if action == "add":
        ctx.user_data["state"] = AWAITING_CT_WALLET
        await query.edit_message_text(
            "🔁 <b>Add Copy Target</b>\n\nSend the wallet address to copy:",
            reply_markup=back_kb("menu:copytrade"),
            parse_mode="HTML",
        )
        return AWAITING_CT_WALLET

    elif action == "list":
        targets = await get_copy_targets(user_id)
        if not targets:
            await query.answer("No active copy targets.", show_alert=True)
            return
        lines = ["🔁 <b>Copy Trade Targets:</b>\n"]
        for t in targets:
            label = t.get("label") or t["target_wallet"][:12] + "..."
            lines.append(f"• {label} — {t.get('fixed_sol', 0.1)} SOL/trade")
        await query.edit_message_text(
            "\n".join(lines),
            reply_markup=back_kb("menu:copytrade"),
            parse_mode="HTML",
        )

    elif action == "config":
        targets = await get_copy_targets(user_id)
        rows = []
        for t in targets:
            label = t.get("label") or t["target_wallet"][:12] + "..."
            rows.append([__import__("telegram").InlineKeyboardButton(label, callback_data=f"ct:config_show:{t['id']}")])
        rows.append([__import__("telegram").InlineKeyboardButton("⬅️ Back", callback_data="menu:copytrade")])
        await query.edit_message_text("Select target to configure:", reply_markup=__import__("telegram").InlineKeyboardMarkup(rows))

    elif action.startswith("config_show:"):
        tid = int(action[12:])
        from src.utils.database import _fetch_one as fo
        row = await fo("SELECT * FROM copy_trade_targets WHERE id=?", (tid,))
        if row:
            await query.edit_message_text(
                f"⚙️ Configure: <code>{row['target_wallet']}</code>",
                reply_markup=copytrade_target_kb(tid, row),
                parse_mode="HTML",
            )

    elif action.startswith("toggle:"):
        parts = action[7:].split(":")
        key, tid = parts[0], int(parts[1])
        from src.utils.database import _fetch_one as fo, _execute as ex
        row = await fo("SELECT * FROM copy_trade_targets WHERE id=? AND user_id=?", (tid, user_id))
        if row:
            new_val = not row.get(key, False)
            await ex(f"UPDATE copy_trade_targets SET {key}=? WHERE id=?", (int(new_val), tid))
            row_new = await fo("SELECT * FROM copy_trade_targets WHERE id=?", (tid,))
            await query.edit_message_text(
                f"⚙️ Configure: <code>{row_new['target_wallet']}</code>",
                reply_markup=copytrade_target_kb(tid, row_new),
                parse_mode="HTML",
            )

    elif action == "remove":
        targets = await get_copy_targets(user_id)
        rows = []
        for t in targets:
            label = t.get("label") or t["target_wallet"][:12] + "..."
            rows.append([__import__("telegram").InlineKeyboardButton(f"❌ {label}", callback_data=f"ct:remove_confirm:{t['target_wallet']}")])
        rows.append([__import__("telegram").InlineKeyboardButton("⬅️ Back", callback_data="menu:copytrade")])
        await query.edit_message_text("Select target to remove:", reply_markup=__import__("telegram").InlineKeyboardMarkup(rows))

    elif action.startswith("remove_confirm:"):
        wallet = action[15:]
        await remove_copy_target(user_id, wallet)
        await query.answer("Target removed.", show_alert=True)
        await _nav_menu("copytrade", update, ctx)


# ── Position actions ──────────────────────────────────────────────────────────

async def _pos_action(action: str, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> Any:
    query = update.callback_query
    user_id = update.effective_user.id

    if action.startswith("detail:"):
        pos_id = int(action[7:])
        from src.utils.database import _fetch_one as fo
        pos = await fo("SELECT * FROM positions WHERE id=? AND user_id=?", (pos_id, user_id))
        if not pos:
            await query.answer("Position not found.", show_alert=True)
            return
        import json
        rules = json.loads(pos.get("sell_rules") or "{}")
        price = pos.get("current_price") or pos.get("entry_price", 0)
        entry = pos.get("entry_price", 0)
        pnl_pct = ((price - entry) / entry * 100) if entry else 0
        symbol = pos.get("token_symbol") or pos["token_mint"][:12] + "..."
        text = (
            f"📊 <b>{symbol}</b>\n\n"
            f"💰 Entry: {fmt_sol(pos.get('entry_sol', 0))} @ {entry:.8f} SOL\n"
            f"📈 Current: {price:.8f} SOL ({fmt_pct(pnl_pct)})\n"
            f"🎯 TP: {rules.get('tp_pct', '—')}% | 🛑 SL: {rules.get('sl_pct', '—')}%\n"
            f"Source: {pos.get('source', 'manual')}"
        )
        await query.edit_message_text(text, reply_markup=position_detail_kb(pos_id), parse_mode="HTML")

    elif action.startswith("sell:"):
        parts = action[5:].split(":")
        pos_id, pct = int(parts[0]), float(parts[1])
        from src.utils.database import _fetch_one as fo
        pos = await fo("SELECT * FROM positions WHERE id=? AND user_id=?", (pos_id, user_id))
        if not pos:
            await query.answer("Position not found.", show_alert=True)
            return
        row = await fo(
            "SELECT encrypted_key FROM wallets WHERE public_key=? AND user_id=?",
            (pos["wallet_pubkey"], user_id),
        )
        if not row:
            await query.answer("Wallet key not found!", show_alert=True)
            return
        # Execute sell
        from src.utils.wallet_manager import get_token_accounts
        from src.trading.swaps import execute_sell, calculate_fee
        from src.utils.database import record_trade, close_position as close_pos
        keypair = decrypt_keypair(row["encrypted_key"])
        accts = await get_token_accounts(pos["wallet_pubkey"])
        token_raw = 0
        decimals = 9
        for acct in accts:
            info = acct.get("account", {}).get("data", {}).get("parsed", {}).get("info", {})
            if info.get("mint") == pos["token_mint"]:
                ta = info.get("tokenAmount", {})
                token_raw = int(ta.get("amount", 0))
                decimals = int(ta.get("decimals", 9))
                break
        sell_raw = int(token_raw * pct / 100)
        settings = await get_user_settings(user_id)
        ok, sig, sol_out = await execute_sell(
            keypair=keypair,
            token_mint=pos["token_mint"],
            token_amount_raw=sell_raw,
            token_decimals=decimals,
            slippage_pct=settings.get("slippage", config.DEFAULT_SLIPPAGE),
            priority_fee_sol=settings.get("priority_fee", config.DEFAULT_PRIORITY_FEE),
        )
        if ok and sig:
            fee = calculate_fee(sol_out or 0)
            await record_trade(
                user_id=user_id, wallet_pubkey=pos["wallet_pubkey"],
                token_mint=pos["token_mint"], token_symbol=pos.get("token_symbol") or "",
                side="sell", sol_amount=sol_out or 0,
                token_amount=sell_raw / (10 ** decimals),
                price_sol=(sol_out or 0) / max(sell_raw / (10 ** decimals), 1),
                tx_sig=sig, fee_sol=fee, source="manual",
            )
            if pct >= 100:
                pnl = (sol_out or 0) - pos.get("entry_sol", 0)
                await close_pos(pos_id, pnl)
            await query.edit_message_text(
                f"✅ Sold {pct:.0f}%!\n💰 Received: {fmt_sol(sol_out or 0)}\n📝 TX: {sig[:16]}...",
                reply_markup=back_kb("menu:positions"),
                parse_mode="HTML",
            )
        else:
            await query.edit_message_text("❌ Sell failed.", reply_markup=back_kb("menu:positions"))

    elif action.startswith("rules:"):
        pos_id = int(action[6:])
        await query.edit_message_text("📋 <b>Sell Rules</b>", reply_markup=position_rules_kb(pos_id), parse_mode="HTML")

    elif action.startswith("set_tp:"):
        pos_id = int(action[7:])
        ctx.user_data["rule_pos_id"] = pos_id
        ctx.user_data["state"] = AWAITING_POS_TP
        await query.edit_message_text("Send take-profit % (e.g. 100 for 2x):", reply_markup=back_kb(f"pos:rules:{pos_id}"))
        return AWAITING_POS_TP

    elif action.startswith("set_sl:"):
        pos_id = int(action[7:])
        ctx.user_data["rule_pos_id"] = pos_id
        ctx.user_data["state"] = AWAITING_POS_SL
        await query.edit_message_text("Send stop-loss % (e.g. 30 = sell when down 30%):", reply_markup=back_kb(f"pos:rules:{pos_id}"))
        return AWAITING_POS_SL

    elif action.startswith("set_trail:"):
        pos_id = int(action[10:])
        ctx.user_data["rule_pos_id"] = pos_id
        ctx.user_data["state"] = AWAITING_POS_TRAIL
        await query.edit_message_text("Send trailing stop % (e.g. 10 = trail 10% from peak):", reply_markup=back_kb(f"pos:rules:{pos_id}"))
        return AWAITING_POS_TRAIL

    elif action.startswith("set_timer:"):
        pos_id = int(action[10:])
        ctx.user_data["rule_pos_id"] = pos_id
        ctx.user_data["state"] = AWAITING_POS_TIMER
        await query.edit_message_text("Send auto-sell timer in minutes:", reply_markup=back_kb(f"pos:rules:{pos_id}"))
        return AWAITING_POS_TIMER


# ── Order actions ─────────────────────────────────────────────────────────────

async def _order_action(action: str, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> Any:
    query = update.callback_query
    user_id = update.effective_user.id

    if action.startswith("cancel:"):
        order_id = int(action[7:])
        cancelled = await cancel_order(order_id, user_id)
        msg = "✅ Order cancelled." if cancelled else "❌ Could not cancel order."
        await query.answer(msg, show_alert=True)
        orders = await get_active_orders(user_id)
        if orders:
            await query.edit_message_text("📋 <b>Active Orders</b>", reply_markup=orders_menu_kb(orders), parse_mode="HTML")
        else:
            await query.edit_message_text("📋 No active orders.", reply_markup=back_kb("menu:main"))


# ── Settings actions ──────────────────────────────────────────────────────────

async def _settings_action(action: str, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> Any:
    query = update.callback_query
    user_id = update.effective_user.id

    if action == "slippage":
        ctx.user_data["state"] = AWAITING_SLIPPAGE
        await query.edit_message_text("Send slippage % (0.5–50):", reply_markup=back_kb("menu:settings"))
        return AWAITING_SLIPPAGE

    elif action == "priority_fee":
        await query.edit_message_text("⛽ Select priority fee preset:", reply_markup=priority_fee_kb())

    elif action.startswith("pf:"):
        preset = action[3:]
        if preset in config.PRIORITY_FEES:
            val = config.PRIORITY_FEES[preset]
            await update_user_setting(user_id, "priority_fee", val)
            await update_user_setting(user_id, "priority_fee_mode", preset)
            await query.answer(f"Priority fee set to {val} SOL", show_alert=True)
            settings = await get_user_settings(user_id)
            await query.edit_message_text("⚙️ <b>Settings</b>", reply_markup=settings_menu_kb(settings), parse_mode="HTML")
        elif preset == "custom":
            ctx.user_data["state"] = AWAITING_CUSTOM_FEE
            await query.edit_message_text("Send custom priority fee in SOL:", reply_markup=back_kb("settings:priority_fee"))
            return AWAITING_CUSTOM_FEE

    elif action == "mev_mode":
        await query.edit_message_text("🛡️ Select MEV protection mode:", reply_markup=mev_mode_kb())

    elif action.startswith("mev:"):
        mode = action[4:]
        await update_user_setting(user_id, "mev_mode", mode)
        await query.answer(f"MEV mode: {mode.title()}", show_alert=True)
        settings = await get_user_settings(user_id)
        await query.edit_message_text("⚙️ <b>Settings</b>", reply_markup=settings_menu_kb(settings), parse_mode="HTML")

    elif action == "rpc":
        ctx.user_data["state"] = AWAITING_RPC
        await query.edit_message_text("🌐 Send custom RPC URL (or 'reset' for default):", reply_markup=back_kb("menu:settings"))
        return AWAITING_RPC

    elif action == "quote_token":
        await query.edit_message_text("💱 Select default quote token:", reply_markup=quote_token_kb())

    elif action.startswith("quote:"):
        token = action[6:]
        mint = config.WSOL_MINT if token == "wsol" else config.USDC_MINT
        await update_user_setting(user_id, "quote_token", mint)
        await query.answer(f"Quote token set to {token.upper()}", show_alert=True)
        settings = await get_user_settings(user_id)
        await query.edit_message_text("⚙️ <b>Settings</b>", reply_markup=settings_menu_kb(settings), parse_mode="HTML")


# ── Referral actions ──────────────────────────────────────────────────────────

async def _chain_action(action: str, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> Any:
    """Handle chain: callbacks — select active chain."""
    query = update.callback_query
    user_id = update.effective_user.id

    if action.startswith("select:"):
        chain_key = action[7:]
        from src.chains.registry import CHAINS, get_chain
        if chain_key not in CHAINS:
            await query.answer("Unknown chain.", show_alert=True)
            return
        await update_user_setting(user_id, "active_chain", chain_key)
        chain = get_chain(chain_key)
        sym = chain["symbol"]
        chain_type = chain["type"]
        note = ""
        if chain_type == "evm":
            # Check if user has an EVM wallet
            evm_wallet = settings = await get_user_settings(user_id)
            has_evm = bool(evm_wallet.get("active_evm_wallet"))
            if not has_evm:
                note = "\n\n⚠️ No EVM wallet yet. Tap 💼 Wallet → ➕ Create Wallet."
        await query.edit_message_text(
            f"✅ Chain switched to <b>{chain['emoji']} {chain['name']}</b>!\n"
            f"Trades will use {sym} as the base currency.{note}",
            reply_markup=main_menu_kb(chain_key),
            parse_mode="HTML",
        )


async def _ref_action(action: str, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> Any:
    query = update.callback_query
    user_id = update.effective_user.id

    if action == "stats":
        from src.utils.database import get_referral_stats
        stats = await get_referral_stats(user_id)
        await query.edit_message_text(
            f"📊 <b>Referral Stats</b>\n\n"
            f"👥 Total Referrals: {stats['count']}\n"
            f"💰 Fees Earned: {fmt_sol(stats['total_sol'])}",
            reply_markup=back_kb("menu:referral"),
            parse_mode="HTML",
        )

    elif action == "link":
        bot = ctx.application.bot
        bot_info = await bot.get_me()
        link = make_referral_link(bot_info.username, user_id)
        await query.edit_message_text(
            f"🔗 <b>Your Referral Link</b>\n\n"
            f"<code>{link}</code>\n\n"
            f"Share this link! You earn 30% of all fees from referred users.",
            reply_markup=back_kb("menu:referral"),
            parse_mode="HTML",
        )
