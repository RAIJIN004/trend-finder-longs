from mcp.server.fastmcp import FastMCP
from scanner import (
    scan_market,
    get_ticker_detail,
    get_klines_detailed,
    get_all_usdt_tickers,
    get_intraday_momentum,
    safety_verdict,
    entry_decision,
    confluence_decision,
    approve_trade,
    get_open_positions,
    get_open_orders,
    calc_atr,
    calc_daily_changes,
    analyze_bullish_streak,
    analyze_red_streak,
    dip_entry_decision
)
from datetime import datetime

mcp = FastMCP(
    "trend-finder-longs",
    description="Binance LONG-ONLY scanner - long setups triggered when Square+orderbook align LONG and price diverges (pullback = setup change)"
)

@mcp.tool()
def scan_long_dips(
    min_volume: float = 5_000_000,
    top_n: int = 20,
    max_4h_drop_pct: float = -2.0,
    min_vol_spike: float = 1.2,
    max_24h_pct: float = -3.0,
    include_watchlist: bool = True,
    market: str = "futures",
) -> dict:
    """
    DIP-HUNTER TODO-EN-UNO en FUTUROS default: sobreextendidas a la BAJA con racha ROJA para LONGS
    de rebote (espejo del cazador alcista). Cada moneda trae: dip (4h/24h/spike/
    racha roja/neto7d) + señal ENTER/WAIT/AVOID + bounce_plan (trigger de giro +
    invalidación) + orderbook + confluencia + MI CUENTA.

    Pipeline: PUERTA capitulación (4h caído + spike) → RANKING por racha roja +
    giro 1h → orderbook + cuenta en el top. Cadáveres (-60% 24h) y caídas libres
    (1h < -1.5%) se descartan solos. Si already_involved=true, gestionar, no duplicar.

    Args:
        min_volume: Minimum 24h quote volume in USDT (default: 5M)
        top_n: Number of top results to return (default: 20)
        max_4h_drop_pct: 4h at or below this % = dumped (default: -2.0)
        min_vol_spike: Minimum volume acceleration = capitulation (default: 1.2)
        max_24h_pct: 24h at or below this % = dumped today (default: -3.0)
        include_watchlist: If True, append WATCH tier (falling, no full
            capitulation yet) for a longer list (default: True)
        market: 'futures' (default, perps + funding) o 'spot'

    Returns:
        Dictionary with dip-ranked coins. ENTER = agotamiento + giro confirmado.
        WAIT con bounce_plan = vigilar ruptura del trigger, NO entrar aún.
    """
    results = scan_market(
        min_volume=min_volume,
        top_n=top_n,
        max_4h_drop_pct=max_4h_drop_pct,
        min_vol_spike=min_vol_spike,
        max_24h_pct=max_24h_pct,
        include_watchlist=include_watchlist,
        market=market,
    )

    return {
        "scan_time": datetime.utcnow().isoformat() + "Z",
        "filter": "DIP-HUNTER: puerta capitulación (4h caído + spike) + racha roja + giro 1h",
        "scope": "SCREENER ONLY - pre-filtro de sobreextendidas a la baja, NOT a trade signal. "
                 "ENTER exige giro 1h confirmado. WAIT con bounce_plan = vigilar trigger, no entrar. "
                 "NEVER open on scanner verdict alone.",
        "disclaimer": "ESTO NO ES ASESORÍA FINANCIERA NI ANÁLISIS FINANCIERO. Haz tu "
                      "propio análisis (DYOR). Setup LONG de dip: agotamiento vendedor "
                      "(racha roja + spike) + GIRO confirmado (1h verde / ruptura del "
                      "bounce_trigger) + Square/orderbook no en contra. Entrar solo el "
                      "giro; comprar la caída libre no es setup, es atrapar cuchillos. "
                      "MÁXIMA PROBABILIDAD: orderbook + Square ALINEADOS con la tendencia "
                      "sobre-extendida + precio DIVERGENTE en contra = reversión a la "
                      "tendencia; la multitud alineada es tu LIQUIDEZ (sus stops pagan "
                      "tu entrada con límite).",
        "filters_applied": {
            "min_volume": min_volume,
            "max_4h_drop_pct": max_4h_drop_pct,
            "min_vol_spike": min_vol_spike,
            "max_24h_pct": max_24h_pct,
            "include_watchlist": include_watchlist,
        },
        "pairs_matched": len(results),
        "top_coins": results,
        "summary": {
            "enter_now": [c["symbol"] for c in results if c["entry"] == "ENTER"],
            "wait": [c["symbol"] for c in results if c["entry"] == "WAIT"],
            "avoid": [c["symbol"] for c in results if c["entry"] == "AVOID"],
            "top_pick": results[0] if results else None,
        }
    }

@mcp.tool()
def get_coin_analysis(symbol: str, market: str = "futures") -> dict:
    """
    Get detailed analysis of a specific coin's bullish momentum, ATR and streaks.
    
    Args:
        symbol: Trading pair symbol (e.g., 'BTCUSDT', 'NEARUSDT')
    
    Returns:
        Detailed price data, daily green/red streaks, net 7d gain, and ATR
    """
    symbol = symbol.upper()
    if not symbol.endswith("USDT"):
        symbol += "USDT"

    ticker = get_ticker_detail(symbol, market)
    klines_raw = get_klines_detailed(symbol, "1d", 14, market)
    klines_data = [[0, k["open"], k["high"], k["low"], k["close"]] for k in klines_raw]
    intraday = get_intraday_momentum(symbol, market)

    atr_info = calc_atr(klines_data, period=14)
    daily_changes = [k["change_pct"] for k in klines_raw[-8:]]
    streak_info = analyze_bullish_streak(daily_changes, klines_data[-8:])

    trend = "STRONG_BULLISH" if (streak_info["positive_streak"] >= 3 and streak_info["net_change_pct"] > 15) else (
        "BULLISH" if streak_info["net_change_pct"] > 0 else "BEARISH"
    )

    return {
        "symbol": symbol,
        "current_price": float(ticker["lastPrice"]),
        "price_change_24h": float(ticker["priceChangePercent"]),
        "range_24h": round(((float(ticker["highPrice"]) - float(ticker["lowPrice"])) / float(ticker["lowPrice"])) * 100, 2),
        "volume_24h": float(ticker["quoteVolume"]),
        "atr": atr_info["atr"],
        "atr_pct": atr_info["atr_pct"],
        "momentum": {
            "trend": trend,
            "positive_streak_days": streak_info["positive_streak"],
            "green_days": f"{streak_info['positive_days']}/{len(daily_changes)}",
            "net_7d_pct": streak_info["net_change_pct"],
            "avg_daily_change": streak_info["avg_daily_change"],
            "avg_positive_gain": streak_info["avg_positive_gain"]
        },
        "intraday_now": intraday,
        "dip_signal": dip_entry_decision(
            analyze_red_streak(daily_changes, klines_data[-8:])["red_streak"],
            analyze_red_streak(daily_changes, klines_data[-8:])["net_change_pct"],
            float(ticker["priceChangePercent"]),
            intraday,
        ) if intraday else {"entry": "WAIT", "size": "0% - esperar",
                            "reason": "sin datos intradía", "bounce_plan": None},
        "entry_signal": entry_decision(
            streak_info["positive_streak"],
            streak_info["net_change_pct"],
            float(ticker["priceChangePercent"]),
            intraday,
            safety_verdict(
                float(ticker["priceChangePercent"]),
                (intraday or {}).get("dist_from_4h_high_pct", 99.0)
            )["verdict"]
        ) if intraday else {"entry": "WAIT", "size": "0% - esperar",
                            "reason": "sin datos intradía", "wait_for_pullback": None},
        "recent_daily_candles": klines_raw[-7:]
    }

@mcp.tool()
def list_active_pairs(min_volume: float = 10_000_000) -> dict:
    """
    List all active USDT trading pairs on Binance with volume filter.
    
    Args:
        min_volume: Minimum 24h quote volume in USDT (default: 10M)
    
    Returns:
        List of active trading pairs with basic info
    """
    tickers = get_all_usdt_tickers(min_volume)

    pairs = []
    for t in tickers:
        pairs.append({
            "symbol": t["symbol"],
            "price": float(t["lastPrice"]),
            "change_24h": float(t["priceChangePercent"]),
            "volume_24h": float(t["quoteVolume"]),
            "high_24h": float(t["highPrice"]),
            "low_24h": float(t["lowPrice"])
        })

    pairs.sort(key=lambda x: x["volume_24h"], reverse=True)

    return {
        "total_pairs": len(pairs),
        "min_volume_filter": min_volume,
        "pairs": pairs
    }

@mcp.tool()
def confluence_check(
    symbol: str,
    square_bias: str = "neutral",
    square_note: str = "",
    market: str = "futures"
) -> dict:
    """
    DECISIÓN FINAL antes de operar: combina momentum + orderbook + funding + Square con
    reglas fijas y vetos. Esta es la ÚNICA tool que autoriza entradas.

    Cómo usarla (la IA debe seguir este orden):
    1. scan_extensive_movements para filtrar candidatas (screener, no señal).
    2. Orderbook lo mide ESTA tool sola (imbalance cuantificado, sin narrativa).
    3. Square/sentiment lo trae la IA con binance-square (square_hashtag) y lo
       pasa como square_bias + square_note. Si no hay datos de Square, dejar neutral.

    Args:
        symbol: Par, ej. 'RAYUSDT'
        square_bias: 'bullish' | 'bearish' | 'neutral' según posts recientes de Square
        square_note: Resumen de 1 línea de lo visto en Square (ej. '3 posts whale accumulation')
        market: 'futures' (default, +funding) o 'spot'

    Returns:
        Veredicto FINAL ENTER/WAIT/AVOID con score /100, traza por fuente y vetos.
        Si hay contradicción entre fuentes NO existe 'entrada parcial': es WAIT o AVOID.
    """
    symbol = symbol.upper()
    if not symbol.endswith("USDT"):
        symbol += "USDT"
    return confluence_decision(symbol, square_bias, square_note, market)

@mcp.tool()
def approve_trade_tool(
    symbol: str,
    side: str,
    entry_price: float,
    leverage: float,
    stop_loss: float,
    wallet_usdt: float,
    quantity: float,
    square_bias: str = "neutral",
    square_note: str = "",
    market: str = "futures"
) -> dict:
    """
    PUERTA FINAL TODO-EN-UNO antes de abrir CUALQUIER posición. La IA debe
    llamarla con el trade EXACTO que pretende abrir y OBEDECER el resultado.

    Valida en orden: confluencia ENTER + lado + tope de apalancamiento por
    wallet + SL del lado correcto + SL antes que liquidación + SL fuera del
    ruido + margen <=50% wallet. Además informa % de riesgo y qty para 2%.

    Args:
        symbol: Par, ej. 'RUNEUSDT'
        side: 'LONG' o 'SHORT'
        entry_price: Precio de entrada pretendido
        leverage: Apalancamiento pretendido (tope automático según wallet)
        stop_loss: Stop loss pretendido
        wallet_usdt: Balance disponible en USDT (futuros)
        quantity: Cantidad en unidades base
        square_bias: 'bullish' | 'bearish' | 'neutral' (de binance-square)
        square_note: Nota de 1 línea de Square
        market: 'futures' (default) o 'spot'

    Returns:
        APPROVED (luz verde matemática) o REJECTED (no abrir, con motivos).
        REJECTED no admite apelación narrativa.
    """
    return approve_trade(symbol, side, entry_price, leverage, stop_loss,
                         wallet_usdt, quantity, square_bias, square_note, market)

@mcp.tool()
def show_positions() -> dict:
    """
    Muestra las posiciones ABIERTAS en futuros REAL con PnL no realizado, ROE%,
    entrada, mark, apalancamiento y liquidación. Si no hay ninguna, lo dice.
    Solo lectura (no abre/cierra nada). Requiere BINANCE_API_KEY/SECRET en el env.
    """
    return get_open_positions()

@mcp.tool()
def show_orders() -> dict:
    """
    Muestra TODAS las órdenes vivas en futuros REAL: regulares + algo (TP/SL/trailing),
    agrupadas por símbolo. Marca HUÉRFANAS las de símbolos SIN posición abierta
    (la IA a veces no limpia: estas son candidatas a cancel_all_algos del helper).
    Solo lectura. Requiere BINANCE_API_KEY/SECRET en el env.
    """
    return get_open_orders()

if __name__ == "__main__":
    mcp.run()
