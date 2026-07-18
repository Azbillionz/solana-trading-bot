# Solana Trading Bot

A production-ready Telegram-based Solana trading and sniping bot similar to Trojan Bot.

## Features

- **Manual Trading** — Buy/sell via Jupiter V6 aggregator (Raydium, Orca, Meteora, Phoenix, and all Solana DEXes)
- **Auto-Sniper** — Monitor pump.fun, Raydium new pools, and Moonshot for new launches with anti-rug filters
- **Copy Trading** — Mirror any wallet's trades proportionally or with fixed SOL amounts
- **Limit Orders** — Set price targets for automatic buy/sell execution
- **DCA** — Dollar-cost average into any token over time
- **Auto-Sell Rules** — Per-position take-profit, stop-loss, trailing stop, and timer
- **MEV Protection** — Jito bundle support for sandwich attack prevention
- **Rug Check** — Full token safety analysis (mint auth, freeze auth, LP burn, holder concentration, honeypot simulation)
- **Wallet Analysis** — P&L, win rate, risk score for any wallet
- **Multi-Wallet** — Create, import, switch, and rename wallets; non-custodial AES-256-GCM encryption
- **Referral System** — On-chain referral links; referrers earn 30% of trading fees

## Setup

1. **Copy env file and fill in your values:**
   ```bash
   cp .env.example .env
   ```

2. **Required env vars:**
   - `TELEGRAM_BOT_TOKEN` — Get from [@BotFather](https://t.me/BotFather)
   - `SOLANA_RPC_URL` — Helius, QuickNode, Triton, or public RPC
   - `ENCRYPTION_KEY` — Generate with: `python -c "import secrets; print(secrets.token_hex(32))"`

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Run the bot:**
   ```bash
   python main.py
   ```

## Commands

| Command | Description |
|---------|-------------|
| `/start` | Main menu |
| `/wallet` | Wallet management |
| `/sniper` | Auto-sniper configuration |
| `/copytrade` | Copy trading targets |
| `/positions` | Open positions & active orders |
| `/settings` | Bot settings |
| `/referral` | Referral stats & link |
| `/analyze [address]` | Wallet P&L analysis |
| `/rugcheck [mint]` | Token safety check |
| `/buy [mint] [SOL]` | Quick buy |
| `/sell [mint] [%/all]` | Quick sell |
| `/cancel` | Cancel active orders |
| `/help` | Full help guide |

## Fee Structure

- Swap fee: **1%** per trade
- Sniper fee: **1.5%** per snipe
- Referral discount: **0.5% off** for first 30 days
- Referrers earn **30%** of fees from referred users

## Architecture

```
solana-bot/
├── main.py                     # Entry point & bot bootstrap
├── config.py                   # Environment configuration
├── requirements.txt
├── src/
│   ├── bot/
│   │   ├── handlers.py         # All command handlers
│   │   ├── keyboards.py        # Inline keyboard builders
│   │   └── menus.py            # Menu state machine & callback router
│   ├── trading/
│   │   ├── swaps.py            # Jupiter V6 swap execution
│   │   ├── sniper.py           # WebSocket new-launch monitor
│   │   ├── limit_orders.py     # Limit order polling engine
│   │   ├── dca.py              # DCA order engine
│   │   └── auto_sell.py        # Auto-sell rule engine
│   ├── copy_trade/
│   │   ├── monitor.py          # Wallet snapshot poller
│   │   └── executor.py         # Copy trade execution
│   ├── analysis/
│   │   ├── rugcheck.py         # Token safety analysis
│   │   └── wallet_analyzer.py  # Wallet P&L analysis
│   └── utils/
│       ├── database.py         # SQLite via aiosqlite
│       ├── wallet_manager.py   # Keypair management & encryption
│       ├── helpers.py          # Formatting & validation
│       └── logger.py           # Loguru setup
```

## Security

- Private keys are **never stored in plaintext** — AES-256-GCM encrypted at rest
- Non-custodial: keys never leave your server
- MEV protection via Jito bundles (toggle between Fast / Secure mode)
- Anti-rug filters before every snipe
