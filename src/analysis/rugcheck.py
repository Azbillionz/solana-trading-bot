"""
Rug check module: comprehensive token safety analysis.
Returns PASS / WARNING / FAIL with detailed bullet points.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

import httpx

import config
from src.utils.logger import logger
from src.utils.helpers import fmt_pct, risk_label


# ── Quick check (used internally by sniper) ───────────────────────────────────

async def quick_rug_check(mint: str) -> Dict[str, Any]:
    """Lightweight check for sniper pre-filter. Returns key flags."""
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(f"{config.RUGCHECK_API_URL}/tokens/{mint}/report/summary")
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "mint_authority_enabled": data.get("mintAuthority") is not None,
                    "freeze_authority_enabled": data.get("freezeAuthority") is not None,
                    "lp_burn_pct": float(data.get("lpBurnedPct", 0)),
                    "top10_holder_pct": float(data.get("top10HolderPercent", 100)),
                    "has_socials": bool(data.get("twitter") or data.get("telegram")),
                }
    except Exception:
        pass
    # Fallback: on-chain queries
    return await _on_chain_quick_check(mint)


async def _on_chain_quick_check(mint: str) -> Dict[str, Any]:
    """Fallback on-chain checks when rugcheck.xyz is unavailable."""
    result: Dict[str, Any] = {
        "mint_authority_enabled": False,
        "freeze_authority_enabled": False,
        "lp_burn_pct": 0,
        "top10_holder_pct": 0,
        "has_socials": False,
    }
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.post(
                config.SOLANA_RPC_URL,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getAccountInfo",
                    "params": [mint, {"encoding": "jsonParsed"}],
                },
            )
            data = resp.json()
            mint_info = (
                data.get("result", {})
                .get("value", {})
                .get("data", {})
                .get("parsed", {})
                .get("info", {})
            )
            result["mint_authority_enabled"] = (
                mint_info.get("mintAuthority") is not None
            )
            result["freeze_authority_enabled"] = (
                mint_info.get("freezeAuthority") is not None
            )
    except Exception as e:
        logger.warning("_on_chain_quick_check error for {}: {}", mint, e)
    return result


# ── Full rug check (public-facing /rugcheck command) ─────────────────────────

async def full_rug_check(mint: str) -> Dict[str, Any]:
    """Comprehensive token safety report."""
    findings = []
    score = 0  # 0=safe, higher=more risky
    rating = "PASS"

    # 1. Rugcheck.xyz API
    api_data = await _fetch_rugcheck_api(mint)

    # 2. Mint authority
    mint_auth = api_data.get("mintAuthority")
    if mint_auth:
        findings.append(f"⚠️ Mint authority NOT revoked: {mint_auth[:12]}...")
        score += 3
        rating = "WARNING"
    else:
        findings.append("✅ Mint authority revoked")

    # 3. Freeze authority
    freeze_auth = api_data.get("freezeAuthority")
    if freeze_auth:
        findings.append(f"⚠️ Freeze authority NOT revoked: {freeze_auth[:12]}...")
        score += 3
        rating = "WARNING"
    else:
        findings.append("✅ Freeze authority revoked")

    # 4. LP burn
    lp_burn = float(api_data.get("lpBurnedPct", 0))
    if lp_burn >= 90:
        findings.append(f"✅ LP burned: {lp_burn:.0f}%")
    elif lp_burn >= 50:
        findings.append(f"⚠️ LP burn low: {lp_burn:.0f}%")
        score += 1
        if rating == "PASS":
            rating = "WARNING"
    else:
        findings.append(f"❌ LP burn critically low: {lp_burn:.0f}%")
        score += 4
        rating = "FAIL"

    # 5. Top 10 holder concentration
    top10 = float(api_data.get("top10HolderPercent", 100))
    if top10 <= 15:
        findings.append(f"✅ Top-10 holders: {top10:.1f}%")
    elif top10 <= 30:
        findings.append(f"⚠️ Top-10 holder concentration: {top10:.1f}%")
        score += 1
        if rating == "PASS":
            rating = "WARNING"
    else:
        findings.append(f"❌ High top-10 concentration: {top10:.1f}%")
        score += 3
        rating = "FAIL"

    # 6. Honeypot simulation (sell via Jupiter quote)
    is_honeypot = await _simulate_sell(mint)
    if is_honeypot:
        findings.append("❌ HONEYPOT: sell simulation failed")
        score += 5
        rating = "FAIL"
    else:
        findings.append("✅ Sell simulation passed (not a honeypot)")

    # 7. Socials
    has_twitter = bool(api_data.get("twitter"))
    has_telegram = bool(api_data.get("telegram"))
    has_website = bool(api_data.get("website"))
    social_links = []
    if has_twitter:
        social_links.append("Twitter")
    if has_telegram:
        social_links.append("Telegram")
    if has_website:
        social_links.append("Website")
    if social_links:
        findings.append(f"✅ Socials found: {', '.join(social_links)}")
    else:
        findings.append("⚠️ No social links found")
        score += 1
        if rating == "PASS":
            rating = "WARNING"

    # 8. Deployer wallet
    deployer = api_data.get("creator", "")
    if deployer:
        findings.append(f"ℹ️ Deployer: {deployer[:12]}...")

    # 9. Known honeypot list (via rugcheck)
    if api_data.get("isHoneypot"):
        findings.append("❌ LISTED AS HONEYPOT by rugcheck.xyz")
        score += 5
        rating = "FAIL"

    # Final rating emoji
    rating_display = {
        "PASS": "🟢 PASS",
        "WARNING": "🟡 WARNING",
        "FAIL": "🔴 FAIL",
    }[rating]

    return {
        "mint": mint,
        "rating": rating,
        "rating_display": rating_display,
        "score": score,
        "findings": findings,
        "name": api_data.get("name", "Unknown"),
        "symbol": api_data.get("symbol", ""),
    }


async def _fetch_rugcheck_api(mint: str) -> dict:
    """Fetch full report from rugcheck.xyz API."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{config.RUGCHECK_API_URL}/tokens/{mint}/report")
            if resp.status_code == 200:
                return resp.json()
    except Exception as e:
        logger.warning("rugcheck API unavailable for {}: {}", mint, e)
    return {}


async def _simulate_sell(mint: str) -> bool:
    """Try to get a sell quote from Jupiter. If it fails, likely a honeypot."""
    try:
        from src.trading.swaps import get_quote
        quote = await get_quote(
            input_mint=mint,
            output_mint=config.WSOL_MINT,
            amount=1_000_000,  # small token amount
            slippage_bps=9999,
        )
        return quote is None
    except Exception:
        return True


def format_rug_report(result: dict) -> str:
    """Format a full rug check result for Telegram display."""
    name = result.get("name", "Unknown")
    symbol = result.get("symbol", "")
    mint = result.get("mint", "")
    rating_display = result.get("rating_display", "❓ UNKNOWN")
    findings = result.get("findings", [])

    lines = [
        f"🔍 <b>Rug Check: {name} ({symbol})</b>",
        f"📍 <code>{mint}</code>",
        "",
        f"Verdict: <b>{rating_display}</b>",
        "",
        "<b>Findings:</b>",
    ]
    for f in findings:
        lines.append(f"  {f}")
    return "\n".join(lines)
