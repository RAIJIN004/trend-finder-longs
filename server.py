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
    analyze_bullish_streak
)
from datetime import datetime

mcp = FastMCP(
    "binance-longs",
    description="Binance LONG-ONLY scanner - long setups triggered when Square+orderbook align LONG and price diverges (pullback = setup change)"
)

@mcp.tool()
def scan_extensive_movements(
    min_volume: float = 5_000_000,
    top_n: int = 20,
    min_1h_pct: float = 1.5,
    min_4h_pct: float = 2.0,
    min_vol_spike: float = 1.0,
    min_24h_pct: float = 0.0,
    only_positive: bool = True,
    include_watchlist: bool = True,
) -> dict:
    """
    TODO-EN-UNO scan of Binance USDT pairs. Each coin in top_coins carries:
    momentum (1h/4h/spike/streak/net7d) + entry signal + pullback plan +
    orderbook bias + confluence_base (final/score/vetoes, Square assumed neutral).

    Pipeline: intraday GATE → daily-streak RANKING → orderbook + confluence +
    MY ACCOUNT (my_position/my_orders/already_involved per coin) enrichment on
    the top only. The ONLY thing the AI must add externally is Square sentiment:
    if square_hashtag is bearish, WAIT/AVOID overrides any confluence_base ENTER.
    If already_involved=true, prefer managing over opening (no duplicar posición).
    For opening: approve_trade_tool has the last word.

    Args:
        min_volume: Minimum 24h quote volume in USDT (default: 5M)
        top_n: Number of top results to return (default: 20)
        min_1h_pct: Minimum last-hour gain % (default: 1.5)
        min_4h_pct: Minimum last-4h gain % (default: 2.0, kills dead-cat bounces)
        min_vol_spike: Minimum volume acceleration vs average (default: 1.0)
        min_24h_pct: Minimum 24h change % (default: 0.0)
        only_positive: If True, only rising coins (default: True)
        include_watchlist: If True, append WATCH tier (right direction, not all
            gates passed: 1h>=0.8%, 4h>=0.5%, spike>=0.7x) for a longer list (default: True)

    Returns:
        Dictionary with hybrid-ranked movers. Every coin carries an automatic
        ENTRY SIGNAL: ENTER (operable now) / WAIT (pullback/confirmation needed)
        / AVOID (peak-chase or streakless pump). Sorted ENTER-first.
    """
    results = scan_market(
        min_volume=min_volume,
        top_n=top_n,
        min_1h_pct=min_1h_pct,
        min_4h_pct=min_4h_pct,
        min_vol_spike=min_vol_spike,
        min_24h_pct=min_24h_pct,
        only_positive=only_positive,
        include_watchlist=include_watchlist,
    )

    return {
        "scan_time": datetime.utcnow().isoformat() + "Z",
        "filter": "HYBRID: intraday gate (1h/4h/spike) + daily-streak ranking + safety verdict",
        "scope": "SCREENER ONLY - momentum pre-filter, NOT a trade signal. "
                 "Use confluence_check(symbol, square_bias) before risking capital. "
                 "NEVER open on scanner verdict alone.",
        "disclaimer": "ESTO NO ES ASESORÍA FINANCIERA NI ANÁLISIS FINANCIERO. Haz tu "
                      "propio análisis (DYOR). Setup LONG recomendado: Square + orderbook "
                      "ALINEADOS a LONG pero PRECIO DIVERGENTE en contra (pullback) = "
                      "detonante de cambio de setup (entrar en el pullback con orden "
                      "límite, nunca a mercado en el pico). Si precio y alineación van "
                      "juntos arriba sin pullback, esperar: perseguir picos no es setup.",
        "filters_applied": {
            "only_positive": only_positive,
            "min_volume": min_volume,
            "min_1h_pct": min_1h_pct,
            "min_4h_pct": min_4h_pct,
            "min_vol_spike": min_vol_spike,
            "min_24h_pct": min_24h_pct,
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
def get_coin_analysis(symbol: str) -> dict:
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

    ticker = get_ticker_detail(symbol)
    klines_raw = get_klines_detailed(symbol, "1d", 14)
    klines_data = [[0, k["open"], k["high"], k["low"], k["close"]] for k in klines_raw]
    intraday = get_intraday_momentum(symbol)

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
    square_note: str = ""
) -> dict:
    """
    DECISIÓN FINAL antes de operar: combina momentum + orderbook + Square con
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

    Returns:
        Veredicto FINAL ENTER/WAIT/AVOID con score /100, traza por fuente y vetos.
        Si hay contradicción entre fuentes NO existe 'entrada parcial': es WAIT o AVOID.
    """
    symbol = symbol.upper()
    if not symbol.endswith("USDT"):
        symbol += "USDT"
    return confluence_decision(symbol, square_bias, square_note)

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
    square_note: str = ""
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

    Returns:
        APPROVED (luz verde matemática) o REJECTED (no abrir, con motivos).
        REJECTED no admite apelación narrativa.
    """
    return approve_trade(symbol, side, entry_price, leverage, stop_loss,
                         wallet_usdt, quantity, square_bias, square_note)

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
