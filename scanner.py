import hashlib
import hmac
import os
import requests
import time
import urllib.parse
from datetime import datetime

BASE_URL = "https://api.binance.com"
FUTURES_BASE = os.environ.get("BINANCE_FUTURES_BASE_URL", "https://fapi.binance.com")

def _signed_futures(method: str, path: str, params: dict | None = None) -> dict | list:
    """GET firmado a Futures. Solo lectura. Requiere BINANCE_API_KEY/SECRET en env."""
    key = os.environ.get("BINANCE_API_KEY", "")
    sec = os.environ.get("BINANCE_API_SECRET", "")
    if not key or not sec:
        raise RuntimeError("Sin BINANCE_API_KEY/SECRET: agrega el env al MCP en Hermes para ver posiciones.")
    params = dict(params or {})
    try:
        server_t = requests.get(f"{FUTURES_BASE}/fapi/v1/time", timeout=10).json()["serverTime"]
        params["timestamp"] = server_t
    except Exception:
        params["timestamp"] = int(time.time() * 1000)
    params["recvWindow"] = "30000"
    qs = urllib.parse.urlencode(params)
    params["signature"] = hmac.new(sec.encode(), qs.encode(), hashlib.sha256).hexdigest()
    r = requests.request(method, f"{FUTURES_BASE}{path}", params=params,
                         headers={"X-MBX-APIKEY": key}, timeout=15)
    r.raise_for_status()
    return r.json()

def get_open_positions() -> dict:
    """Posiciones abiertas en futuros con PnL. Vacío si no hay ninguna."""
    data = _signed_futures("GET", "/fapi/v3/positionRisk")
    rows = []
    total_pnl = 0.0
    for p in data or []:
        amt = float(p.get("positionAmt", 0) or 0)
        if amt == 0:
            continue
        entry = float(p.get("entryPrice", 0) or 0)
        mark = float(p.get("markPrice", 0) or 0)
        pnl = float(p.get("unRealizedProfit", 0) or 0)
        try:
            lev = int(float(p.get("leverage", 0) or 0))
        except (TypeError, ValueError):
            lev = 0
        notional = abs(amt) * mark
        margin_base = notional / lev if lev > 0 else notional
        roe = pnl / margin_base * 100 if margin_base > 0 else 0
        total_pnl += pnl
        rows.append({
            "symbol": p.get("symbol"),
            "side": "LONG" if amt > 0 else "SHORT",
            "amount": amt,
            "entry": entry,
            "mark": mark,
            "pnl_usdt": round(pnl, 4),
            "roe_pct": round(roe, 2),
            "roe_base": "margen" if lev > 0 else "nocional",
            "leverage": f"{lev}x" if lev > 0 else str(p.get("leverage")),
            "notional_usdt": round(notional, 2),
            "liq_price": p.get("liquidationPrice"),
            "margin_type": p.get("marginType"),
        })
    rows.sort(key=lambda x: x["pnl_usdt"])
    return {
        "count": len(rows),
        "total_pnl_usdt": round(total_pnl, 4),
        "positions": rows,
        "note": "Sin posiciones abiertas." if not rows else f"{len(rows)} posición(es) abierta(s).",
    }

def get_open_orders() -> dict:
    """Todas las órdenes vivas: regulares + algo (TP/SL/trailing).
    Marca HUÉRFANAS las de símbolos SIN posición abierta (candidatas a limpieza,
    la IA a veces no las cancela). Solo lectura."""
    orders = _signed_futures("GET", "/fapi/v1/openOrders") or []
    algos_raw = _signed_futures("GET", "/fapi/v1/openAlgoOrders")
    algos = algos_raw if isinstance(algos_raw, list) else (algos_raw or {}).get("orders", [])
    pos = _signed_futures("GET", "/fapi/v3/positionRisk") or []
    pos_syms = {p.get("symbol") for p in pos if float(p.get("positionAmt", 0) or 0) != 0}

    by_symbol: dict = {}

    def bucket(sym: str) -> dict:
        return by_symbol.setdefault(sym, {"symbol": sym, "has_position": sym in pos_syms,
                                          "regular": [], "algos": []})

    for o in orders:
        b = bucket(o.get("symbol"))
        b["regular"].append({
            "orderId": o.get("orderId"),
            "type": o.get("type"),
            "side": o.get("side"),
            "price": o.get("price"),
            "stopPrice": o.get("stopPrice"),
            "activatePrice": o.get("activatePrice"),
            "callbackRate": o.get("callbackRate"),
            "origQty": o.get("origQty"),
            "status": o.get("status"),
        })
    for a in algos:
        b = bucket(a.get("symbol"))
        b["algos"].append({
            "algoId": a.get("algoId"),
            "orderType": a.get("orderType") or a.get("type"),
            "side": a.get("side"),
            "triggerPrice": a.get("triggerPrice"),
            "activatePrice": a.get("activatePrice"),
            "callbackRate": a.get("callbackRate"),
            "quantity": a.get("quantity"),
            "status": a.get("algoStatus") or a.get("status"),
        })

    symbols = []
    orphans = []
    for sym, b in sorted(by_symbol.items()):
        n = len(b["regular"]) + len(b["algos"])
        b["total"] = n
        b["orphan"] = not b["has_position"]
        if b["orphan"]:
            orphans.append(sym)
        symbols.append(b)

    return {
        "symbols_with_orders": len(symbols),
        "total_regular": sum(len(b["regular"]) for b in symbols),
        "total_algos": sum(len(b["algos"]) for b in symbols),
        "orphan_symbols": orphans,
        "cleanup_hint": ("Limpieza sugerida con cancel_all_algos del helper para: " + ", ".join(orphans)
                         if orphans else "Nada huérfano."),
        "by_symbol": symbols,
    }

def get_all_usdt_tickers(min_volume: float = 5_000_000) -> list:
    r = requests.get(f"{BASE_URL}/api/v3/ticker/24hr", timeout=15)
    r.raise_for_status()
    data = r.json()
    return [
        d for d in data
        if d["symbol"].endswith("USDT")
        and float(d["quoteVolume"]) > min_volume
        and not any(x in d["symbol"] for x in ["UP", "DOWN", "BULL", "BEAR"])
    ]

def get_klines(symbol: str, interval: str = "1d", limit: int = 14) -> list:
    r = requests.get(
        f"{BASE_URL}/api/v3/klines",
        params={"symbol": symbol, "interval": interval, "limit": limit},
        timeout=15
    )
    r.raise_for_status()
    return r.json()

def get_orderbook_bias(symbol: str, depth: int = 20) -> dict | None:
    """Lee el orderbook público y devuelve sesgo cuantificado (no narrativa).
    imbalance = (bids - asks) / (bids + asks) en top N niveles:
    > +0.05 a favor comprador, < -0.05 a favor vendedor."""
    try:
        r = requests.get(f"{BASE_URL}/api/v3/depth",
                         params={"symbol": symbol, "limit": min(max(depth, 5), 100)},
                         timeout=10)
        r.raise_for_status()
        ob = r.json()
    except Exception:
        return None
    bids = [(float(p), float(q)) for p, q in ob.get("bids", [])[:depth]]
    asks = [(float(p), float(q)) for p, q in ob.get("asks", [])[:depth]]
    if not bids or not asks:
        return None
    bid_vol = sum(p * q for p, q in bids)
    ask_vol = sum(p * q for p, q in asks)
    total = bid_vol + ask_vol
    imb = (bid_vol - ask_vol) / total if total > 0 else 0
    spread = (asks[0][0] - bids[0][0]) / bids[0][0] * 100 if bids[0][0] > 0 else 0
    bias = "bullish" if imb >= 0.05 else ("bearish" if imb <= -0.05 else "neutral")
    return {
        "bias": bias,
        "imbalance": round(imb, 3),
        "spread_pct": round(spread, 3),
        "bid_usdt": round(bid_vol, 0),
        "ask_usdt": round(ask_vol, 0),
        "depth_levels": depth,
    }

def _leverage_cap(wallet: float) -> int:
    if wallet < 25:
        return 10
    if wallet < 100:
        return 20
    if wallet < 1000:
        return 35
    return 50

def approve_trade(symbol: str, side: str, entry_price: float, leverage: float,
                  stop_loss: float, wallet_usdt: float, quantity: float,
                  square_bias: str = "neutral", square_note: str = "") -> dict:
    """TODO EN UNO: puerta final antes de abrir. Corre confluencia + matemática
    de riesgo. Solo devuelve APPROVED si TODO pasa; si no, REJECTED con motivos.
    La IA no interpreta: obedece."""
    sym = symbol.upper()
    side = side.upper()
    checks = []
    fails = []

    def check(name: str, ok: bool, detail: str):
        checks.append({"check": name, "result": "PASS" if ok else "FAIL", "detail": detail})
        if not ok:
            fails.append(name)

    # 1) Confluencia (momentum + orderbook + square)
    conf = confluence_decision(sym, square_bias, square_note)
    check("confluence_ENTER",
          conf["final"] == "ENTER",
          f"confluence={conf['final']} {conf['confluence_score']} vetoes={conf['vetoes'] or 'ninguno'}")

    # 2) Lado válido
    check("side_valido", side in ("LONG", "SHORT"), f"side={side}")

    # 3) Tope de apalancamiento por wallet
    cap = _leverage_cap(wallet_usdt)
    check("leverage_cap",
          leverage <= cap,
          f"wallet ${wallet_usdt} → máx {cap}x, pedido {leverage}x")

    # 4) SL del lado correcto y con distancia real
    if side == "LONG":
        sl_ok = stop_loss < entry_price
    else:
        sl_ok = stop_loss > entry_price
    check("sl_lado_correcto", sl_ok,
          f"entry={entry_price} sl={stop_loss} side={side}")
    sl_dist_pct = abs(entry_price - stop_loss) / entry_price * 100 if entry_price > 0 else 0

    # 5) Liquidación ANTES que el SL = suicidio (rechazo duro)
    liq_dist_pct = (1 / leverage - 0.005) * 100 if leverage > 0 else 0
    check("sl_antes_que_liquidacion",
          sl_ok and sl_dist_pct < liq_dist_pct * 0.9,
          f"SL a {sl_dist_pct:.2f}% vs liquidación estimada a ~{liq_dist_pct:.2f}% "
          f"(con 10% colchón fees/slippage)")

    # 6) SL fuera del ruido (≥1.5x rango promedio 15m)
    noise_ok, noise_detail = False, "sin datos"
    try:
        ks = get_klines(sym, "15m", 17)
        ranges = [(float(k[2]) - float(k[3])) / float(k[4]) * 100 for k in ks if float(k[4]) > 0]
        avg_noise = sum(ranges) / len(ranges) if ranges else 0
        noise_ok = sl_dist_pct >= avg_noise * 1.5
        noise_detail = f"SL a {sl_dist_pct:.2f}% vs ruido 15m {avg_noise:.2f}% (mínimo 1.5x = {avg_noise*1.5:.2f}%)"
    except Exception as e:
        noise_detail = f"no se pudo medir ruido: {e}"
    check("sl_fuera_del_ruido", noise_ok, noise_detail)

    # 7) Margen usado ≤50% wallet (una operación no puede secuestrar la cuenta)
    notional = quantity * entry_price
    margin = notional / leverage if leverage > 0 else 0
    check("margen_vs_wallet",
          margin <= wallet_usdt * 0.5,
          f"margen ${margin:.2f} vs 50% wallet ${wallet_usdt*0.5:.2f} "
          f"(nocional ${notional:.2f} x{leverage})")

    # 8) Riesgo % (informativo: WARN, no rechaza)
    risk_usd = abs(entry_price - stop_loss) * quantity if sl_ok else notional
    risk_pct = risk_usd / wallet_usdt * 100 if wallet_usdt > 0 else 999
    risk_note = f"pérdida si toca SL: ${risk_usd:.2f} = {risk_pct:.1f}% wallet"
    if risk_pct > 20:
        risk_note += " (ALTO: >20%)"
    qty_2pct = (wallet_usdt * 0.02) / abs(entry_price - stop_loss) if sl_ok and entry_price != stop_loss else 0

    approved = not fails
    return {
        "symbol": sym,
        "decision": "APPROVED" if approved else "REJECTED",
        "trade": {"side": side, "entry": entry_price, "leverage": leverage,
                  "stop": stop_loss, "qty": quantity, "notional": round(notional, 2),
                  "wallet": wallet_usdt},
        "checks": checks,
        "failed": fails,
        "risk_info": risk_note + f" | qty sugerida para riesgo 2%: {qty_2pct:.1f} unidades",
        "confluence": {"final": conf["final"], "score": conf["confluence_score"],
                       "pullback_plan": conf.get("pullback_plan")},
        "scope": "APPROVED = luz verde matemática, NO garantía de ganancia. "
                 "REJECTED = no abrir bajo ningún relato ('alineación parcial' incluida).",
        "disclaimer": "NO ES ASESORÍA FINANCIERA. Revisa el trade por tu cuenta (DYOR) "
                      "aunque salga APPROVED: el mercado siempre puede invalidarlo.",
    }

def confluence_decision(symbol: str, square_bias: str = "neutral",
                        square_note: str = "") -> dict:
    """Combina 3 fuentes con reglas FIJAS y vetos. Ninguna fuente decide sola:
    - Momentum (scanner propio): 40 pts
    - Orderbook (medido aquí, no interpretado): 35 pts
    - Square/sentiment (lo aporta la IA, única entrada externa): 25 pts
    Regla anti-narrativa: CUALQUIER contradicción fuerte = WAIT o AVOID.
    No existen 'entradas por alineación parcial'."""
    sym = symbol.upper()
    problems = []
    if not sym.replace("USDT", "").isalnum() or not sym.isascii():
        problems.append("SÍMBOLO NO ESTÁNDAR: verifícalo en el exchange antes de operar")

    # 1) Momentum (scanner propio, determinista)
    intra = get_intraday_momentum(sym)
    mom_entry, mom_score, mom_detail = "AVOID", 0, None
    if intra is None:
        mom_detail = "sin datos intradía"
    else:
        try:
            ks = get_klines(sym, "1d", 8)
            daily = calc_daily_changes(ks)
            streak = analyze_bullish_streak(daily, ks)
            t = get_ticker_detail(sym)
            chg24 = float(t["priceChangePercent"])
            sv = safety_verdict(chg24, intra["dist_from_4h_high_pct"])
            ed = entry_decision(streak["positive_streak"], streak["net_change_pct"],
                                chg24, intra, sv["verdict"])
            mom_entry = ed["entry"]
            mom_detail = {
                "entry": ed["entry"], "size": ed["size"], "reason": ed["reason"],
                "wait_for_pullback": ed["wait_for_pullback"],
                "racha_d": streak["positive_streak"], "net_7d": streak["net_change_pct"],
                "chg_1h": intra["chg_1h"], "chg_4h": intra["chg_4h"],
                "spike": intra["vol_spike"], "chg_24h": round(chg24, 2),
            }
            mom_score = {"ENTER": 40, "WAIT": 20, "AVOID": 0}[ed["entry"]]
        except Exception as e:
            mom_detail = f"error momentum: {e}"

    # 2) Orderbook (medido aquí)
    ob = get_orderbook_bias(sym)
    ob_score = 0
    if ob is None:
        ob_detail = "orderbook no disponible"
    else:
        imb = ob["imbalance"]
        ob_score = 35 if imb >= 0.15 else (25 if imb >= 0.05 else (12 if imb > -0.05 else (5 if imb > -0.15 else 0)))
        ob_detail = ob

    # 3) Square (externo, lo trae la IA)
    sq = (square_bias or "neutral").lower()
    if sq not in ("bullish", "bearish", "neutral"):
        sq = "neutral"
    sq_score = {"bullish": 25, "neutral": 12, "bearish": 0}[sq]

    score = mom_score + ob_score + sq_score

    # VETOS (pisan el puntaje, sin excepción)
    vetoes = []
    if mom_entry == "AVOID":
        vetoes.append("momentum AVOID: el scanner descarta la moneda")
    if sq == "bearish":
        vetoes.append("Square bearish: sentimiento en contra, máximo WAIT")
    if isinstance(ob_detail, dict) and ob_detail["bias"] == "bearish" and mom_entry == "ENTER":
        vetoes.append(f"orderbook en contra (imb={ob_detail['imbalance']}): momentum dice ENTER pero no hay bids que lo sostengan")
    if isinstance(ob_detail, dict) and ob_detail["spread_pct"] > 0.30:
        vetoes.append(f"spread {ob_detail['spread_pct']}%: impuesto microcap, no entrada a mercado")

    if mom_entry == "WAIT":
        vetoes.append("momentum WAIT (sin ENTER): orderbook y Square no pueden autorizar solas")
    if vetoes or score < 70:
        if mom_entry == "AVOID" or (sq == "bearish" and mom_entry != "ENTER"):
            final, size = "AVOID", "0% - descartar"
        else:
            final, size = "WAIT", "0% - esperar"
    else:
        final, size = "ENTER", "100% - operar"

    pullback_plan = None
    if isinstance(mom_detail, dict):
        pullback_plan = mom_detail.get("wait_for_pullback")

    return {
        "symbol": sym,
        "final": final,
        "suggested_size": size,
        "confluence_score": f"{score}/100 (ENTER exige >=70 SIN vetos)",
        "pullback_plan": pullback_plan,
        "trace": {
            "momentum_40": {"score": mom_score, "detail": mom_detail},
            "orderbook_35": {"score": ob_score, "detail": ob_detail},
            "square_25": {"bias": sq, "score": sq_score, "note": square_note},
        },
        "vetoes": vetoes,
        "symbol_warnings": problems,
        "scope": "Esta tool es la ÚNICA que autoriza entradas. El scanner solo filtra momentum; "
                 "el orderbook solo mide liquidez; Square solo mide sentimiento. "
                 "PROHIBIDO abrir posición por 'alineación parcial' si aquí sale WAIT/AVOID.",
        "disclaimer": "NO ES ASESORÍA FINANCIERA. Verifica por tu cuenta (DYOR): Square y "
                      "confluencia alineados al mismo lado o no hay trade.",
    }

def calc_daily_changes(klines: list) -> list:
    changes = []
    for k in klines:
        o, c = float(k[1]), float(k[4])
        pct = ((c - o) / o) * 100
        changes.append(round(pct, 2))
    return changes

def get_intraday_momentum(symbol: str) -> dict | None:
    """Momentum intradía real: cambio 1h y 4h + spike de volumen (velas 15m).
    Filtro principal: detecta lo que se mueve AHORA, no ayer."""
    try:
        ks = get_klines(symbol, "15m", 17)
    except Exception:
        return None
    if len(ks) < 17:
        return None
    closes = [float(k[4]) for k in ks]
    highs = [float(k[2]) for k in ks]
    lows = [float(k[3]) for k in ks]
    quote_vols = [float(k[5]) * float(k[4]) for k in ks]
    chg_1h = (closes[-1] / closes[-5] - 1) * 100 if closes[-5] > 0 else 0
    chg_4h = (closes[-1] / closes[0] - 1) * 100 if closes[0] > 0 else 0
    base_vol = sum(quote_vols[:-4]) / 13
    spike = (sum(quote_vols[-4:]) / 4) / base_vol if base_vol > 0 else 0
    green_1h = sum(1 for i in range(-4, 0) if closes[i] > closes[i - 1])
    high_4h = max(highs)
    price = closes[-1]
    dist_high = (high_4h - price) / high_4h * 100 if high_4h > 0 else 0
    # Plan de pullback: profundidad = mitad del rango de 1h (clamp 1%-5%).
    range_1h = (max(highs[-4:]) - min(lows[-4:])) / price * 100 if price > 0 else 0
    depth = min(5.0, max(1.0, range_1h / 2))
    pullback_px = high_4h * (1 - depth / 100)
    return {
        "chg_1h": round(chg_1h, 2),
        "chg_4h": round(chg_4h, 2),
        "vol_spike": round(spike, 1),
        "green_candles_1h": f"{green_1h}/4",
        "dist_from_4h_high_pct": round(dist_high, 2),
        "high_4h": high_4h,
        "pullback_entry": round(pullback_px, 8),
        "pullback_depth_pct": round(depth, 2),
        "invalidate_above": round(high_4h * 1.005, 8),
        "price": price,
    }

def safety_verdict(chg_24h: float, dist_high: float) -> dict:
    """Capa de seguridad: marca chase-riesgo y compras en el pico exacto."""
    reasons = []
    if chg_24h > 80:
        reasons.append(f"sobre-extendida 24h={chg_24h:.0f}% (riesgo de chase)")
    elif chg_24h > 35:
        reasons.append(f"extendida 24h={chg_24h:.0f}% (entrar con mitad de tamaño)")
    if dist_high < 0.5:
        reasons.append("en el pico de 4h (esperar retroceso, no entrada a mercado)")
    verdict = "EVITAR" if chg_24h > 80 else ("PRECAUCION" if reasons else "OK")
    return {"verdict": verdict, "reasons": reasons}

def entry_decision(streak_days: int, net_7d: float, chg_24h: float,
                   intra: dict, verdict: str) -> dict:
    """Señal de entrada AUTOMATICA: impide entrar en picos y en monedas sin racha.
    ENTER = operable ahora. WAIT = esperar (con nivel de pullback concreto si el
    motivo es el pico). AVOID = descartar (rebote de desplome, sobre-extendida,
    sin racha + extendida)."""
    dist_high = (intra or {}).get("dist_from_4h_high_pct", 99.0)
    if verdict == "EVITAR" or net_7d < -20 or (streak_days <= 1 and chg_24h > 35):
        why = []
        if verdict == "EVITAR":
            why.append("sobre-extendida (+80% 24h)")
        if net_7d < -20:
            why.append(f"rebote dentro de desplome semanal ({net_7d:+.1f}% 7d)")
        if streak_days <= 1 and chg_24h > 35:
            why.append("sin racha diaria + extendida (pump de una vela)")
        return {"entry": "AVOID", "size": "0% - descartar", "reason": "; ".join(why),
                "wait_for_pullback": None}
    if verdict == "PRECAUCION" or streak_days <= 1 or dist_high < 0.5:
        why = []
        pullback = None
        if dist_high < 0.5 and intra:
            why.append(f"en el pico de 4h: esperar pullback a {intra['pullback_entry']} "
                       f"(-{intra['pullback_depth_pct']}%)")
            pullback = {
                "pullback_entry": intra["pullback_entry"],
                "depth_pct": intra["pullback_depth_pct"],
                "high_4h": intra["high_4h"],
                "invalidate_above": intra["invalidate_above"],
                "note": "Orden límite en pullback_entry. Si rompe invalidate_above, "
                        "el plan se cancela: re-evaluar (aplican reglas de chase).",
            }
        if streak_days <= 1:
            why.append("sin racha diaria todavía (esperar confirmación)")
        if verdict == "PRECAUCION" and not why:
            why.append("veredicto PRECAUCION")
        return {"entry": "WAIT", "size": "0% - esperar", "reason": "; ".join(why),
                "wait_for_pullback": pullback}
    size = "50% - mitad de tamaño" if chg_24h > 35 else "100% - tamaño completo"
    return {"entry": "ENTER", "size": size,
            "reason": f"racha {streak_days}d + moviéndose ahora + fuera del pico",
            "wait_for_pullback": None}

def calc_atr(klines: list, period: int = 14) -> dict:
    if len(klines) < 2:
        return {"atr": 0, "atr_pct": 0, "tr_values": []}

    tr_values = []
    for i in range(1, len(klines)):
        high = float(klines[i][2])
        low = float(klines[i][3])
        prev_close = float(klines[i-1][4])

        tr1 = high - low
        tr2 = abs(high - prev_close)
        tr3 = abs(low - prev_close)
        tr = max(tr1, tr2, tr3)
        tr_values.append(tr)

    if not tr_values:
        return {"atr": 0, "atr_pct": 0, "tr_values": []}

    atr_period = min(period, len(tr_values))
    atr = sum(tr_values[-atr_period:]) / atr_period

    current_price = float(klines[-1][4])
    atr_pct = (atr / current_price * 100) if current_price > 0 else 0

    return {
        "atr": round(atr, 6),
        "atr_pct": round(atr_pct, 2),
        "tr_values": [round(v, 6) for v in tr_values]
    }

def analyze_bullish_streak(daily_changes: list, klines: list) -> dict:
    if not daily_changes or len(klines) < 2:
        return {
            "positive_streak": 0,
            "positive_days": 0,
            "negative_days": 0,
            "net_change_pct": 0,
            "avg_daily_change": 0,
            "avg_positive_gain": 0
        }

    # Consecutive positive days from newest to oldest
    pos_streak = 0
    for c in reversed(daily_changes):
        if c > 0:
            pos_streak += 1
        else:
            break

    pos_days = sum(1 for c in daily_changes if c > 0)
    neg_days = sum(1 for c in daily_changes if c < 0)

    first_open = float(klines[0][1])
    last_close = float(klines[-1][4])
    net_change = round(((last_close - first_open) / first_open) * 100, 2) if first_open > 0 else 0

    avg_change = round(sum(daily_changes) / len(daily_changes), 2)
    pos_gains = [c for c in daily_changes if c > 0]
    avg_pos_gain = round(sum(pos_gains) / len(pos_gains), 2) if pos_gains else 0

    return {
        "positive_streak": pos_streak,
        "positive_days": pos_days,
        "negative_days": neg_days,
        "net_change_pct": net_change,
        "avg_daily_change": avg_change,
        "avg_positive_gain": avg_pos_gain
    }

def scan_market(
    min_volume: float = 5_000_000,
    top_n: int = 20,
    min_1h_pct: float = 1.5,
    min_4h_pct: float = 2.0,
    min_vol_spike: float = 1.0,
    min_24h_pct: float = 0.0,
    only_positive: bool = True,
    include_watchlist: bool = True,
) -> list:
    """HIBRIDO en 2 etapas + nivel WATCH para lista extensa:
    1. PUERTA intradía (1 request 15m x17 por moneda): solo lo que se mueve AHORA.
    2. RANKING por racha diaria (1 request 1d x8 solo para las que pasan):
       consistencia primero, velocidad después. Neto 7d negativo hunde rebotes
       de desplome al fondo. Incluye veredicto de seguridad por moneda."""
    tickers = get_all_usdt_tickers(min_volume)
    gated = []

    for i, t in enumerate(tickers):
        sym = t["symbol"]
        pct_24h = float(t["priceChangePercent"])
        vol = float(t["quoteVolume"])

        if only_positive and pct_24h < min_24h_pct:
            continue

        intra = get_intraday_momentum(sym)
        if intra is None:
            continue

        passes = True
        if only_positive:
            if intra["chg_1h"] < min_1h_pct:
                passes = False
            if intra["chg_4h"] < min_4h_pct:
                passes = False
        if intra["vol_spike"] < min_vol_spike:
            passes = False

        tier = "PASS" if passes else None
        if tier is None and include_watchlist:
            # WATCH: dirección correcta pero sin pasar todo (para lista extensa)
            if (intra["chg_1h"] >= 0.8 and intra["chg_4h"] >= 0.5
                    and intra["vol_spike"] >= 0.7):
                tier = "WATCH"
        if tier is None:
            continue

        gated.append((sym, pct_24h, vol, intra, tier))

        if (i + 1) % 50 == 0:
            time.sleep(0.5)

    # Capar WATCH para no disparar requests: los de mejor 1h primero
    gated.sort(key=lambda g: (0 if g[4] == "PASS" else 1, -g[3]["chg_1h"]))
    gated = [g for g in gated if g[4] == "PASS"] + \
            [g for g in gated if g[4] == "WATCH"][:max(top_n, 10)]

    results = []
    for sym, pct_24h, vol, intra, tier in gated:
        try:
            ks = get_klines(sym, "1d", 8)
            if len(ks) < 8:
                continue
            daily = calc_daily_changes(ks)
            streak = analyze_bullish_streak(daily, ks)
            safety = safety_verdict(pct_24h, intra["dist_from_4h_high_pct"])
            entry = entry_decision(streak["positive_streak"], streak["net_change_pct"],
                                   pct_24h, intra, safety["verdict"])
            results.append({
                "symbol": sym,
                "tier": tier,
                "price": intra["price"],
                "chg_1h": intra["chg_1h"],
                "chg_4h": intra["chg_4h"],
                "vol_spike": intra["vol_spike"],
                "green_candles_1h": intra["green_candles_1h"],
                "dist_from_4h_high_pct": intra["dist_from_4h_high_pct"],
                "pct_24h": round(pct_24h, 2),
                "volume_24h": round(vol, 0),
                "positive_streak_days": streak["positive_streak"],
                "green_days_8": f"{streak['positive_days']}/8",
                "net_7d_pct": streak["net_change_pct"],
                "verdict": safety["verdict"],
                "warnings": safety["reasons"],
                "entry": entry["entry"],
                "suggested_size": entry["size"],
                "entry_reason": entry["reason"],
                "wait_for_pullback": entry["wait_for_pullback"],
            })
        except Exception:
            continue
        time.sleep(0.03)

    # Orden: tier PASS primero, luego señal de entrada, luego híbrido clásico
    rank = {"ENTER": 2, "WAIT": 1, "AVOID": 0}
    tier_rank = {"PASS": 1, "WATCH": 0}
    results.sort(
        key=lambda x: (tier_rank.get(x["tier"], 0), rank.get(x["entry"], 0),
                       x["positive_streak_days"],
                       x["chg_1h"], x["net_7d_pct"], x["vol_spike"]),
        reverse=True
    )
    top = results[:top_n]

    # TODO-EN-UNO: enriquecer SOLO el top con orderbook + confluencia base
    # + MI CUENTA (posición y órdenes vivas por moneda). Square queda neutral
    # por defecto: la IA debe verificar square_hashtag y, si es bearish,
    # degradar (regla en scope).
    for c in top:
        ob = get_orderbook_bias(c["symbol"])
        if ob is None:
            c["orderbook"] = {"bias": "unknown", "note": "orderbook no disponible"}
            c["confluence_base"] = {"final": c["entry"], "score": "n/a",
                                    "note": "sin orderbook: vale la señal momentum"}
            continue
        imb = ob["imbalance"]
        ob_pts = 35 if imb >= 0.15 else (25 if imb >= 0.05 else (12 if imb > -0.05 else (5 if imb > -0.15 else 0)))
        mom_pts = {"ENTER": 40, "WAIT": 20, "AVOID": 0}.get(c["entry"], 0)
        score = mom_pts + ob_pts + 12  # Square neutral = 12
        vetoes = []
        if c["entry"] == "AVOID":
            vetoes.append("momentum AVOID")
        if c["entry"] == "WAIT":
            vetoes.append("momentum WAIT: falta ENTER de momentum")
        if ob["bias"] == "bearish" and c["entry"] == "ENTER":
            vetoes.append(f"orderbook en contra (imb={imb})")
        if ob["spread_pct"] > 0.30:
            vetoes.append(f"spread {ob['spread_pct']}%")
        if vetoes or score < 70:
            final = "AVOID" if c["entry"] == "AVOID" else "WAIT"
        else:
            final = "ENTER"
        c["orderbook"] = ob
        c["confluence_base"] = {
            "final": final, "score": f"{score}/100",
            "vetoes": vetoes,
            "square_assumed": "neutral (IA debe verificar square_hashtag; si bearish → WAIT/AVOID)",
        }
        time.sleep(0.03)

    # MI CUENTA: posición + órdenes vivas por cada moneda del top (3 requests
    # firmados en total, no por moneda). Sin keys → campos en null, sin error.
    try:
        pos = _signed_futures("GET", "/fapi/v3/positionRisk") or []
        ords = _signed_futures("GET", "/fapi/v1/openOrders") or []
        algos_raw = _signed_futures("GET", "/fapi/v1/openAlgoOrders")
        algos = algos_raw if isinstance(algos_raw, list) else (algos_raw or {}).get("orders", [])
        pos_by = {p.get("symbol"): p for p in pos if float(p.get("positionAmt", 0) or 0) != 0}
        ord_by: dict = {}
        for o in ords:
            ord_by.setdefault(o.get("symbol"), {"regular": [], "algos": []})["regular"].append({
                "id": o.get("orderId"), "type": o.get("type"), "side": o.get("side"),
                "qty": o.get("origQty"), "stop": o.get("stopPrice") or o.get("activatePrice"),
            })
        for a in algos:
            ord_by.setdefault(a.get("symbol"), {"regular": [], "algos": []})["algos"].append({
                "id": a.get("algoId"), "type": a.get("orderType") or a.get("type"),
                "side": a.get("side"), "trigger": a.get("triggerPrice"),
                "activate": a.get("activatePrice"), "cb": a.get("callbackRate"),
                "qty": a.get("quantity"),
            })
        for c in top:
            p = pos_by.get(c["symbol"])
            o = ord_by.get(c["symbol"], {"regular": [], "algos": []})
            n_ord = len(o["regular"]) + len(o["algos"])
            c["my_position"] = None if not p else {
                "side": "LONG" if float(p["positionAmt"]) > 0 else "SHORT",
                "amount": float(p["positionAmt"]),
                "entry": float(p.get("entryPrice", 0) or 0),
                "mark": float(p.get("markPrice", 0) or 0),
                "pnl_usdt": round(float(p.get("unRealizedProfit", 0) or 0), 4),
            }
            c["my_orders"] = {"total": n_ord, **o}
            c["already_involved"] = bool(p) or n_ord > 0
    except Exception:
        for c in top:
            c["my_position"] = None
            c["my_orders"] = {"total": 0, "regular": [], "algos": []}
            c["already_involved"] = False

    return top

def get_ticker_detail(symbol: str) -> dict:
    r = requests.get(f"{BASE_URL}/api/v3/ticker/24hr", params={"symbol": symbol}, timeout=10)
    r.raise_for_status()
    return r.json()

def get_klines_detailed(symbol: str, interval: str = "1d", limit: int = 14) -> list:
    klines = get_klines(symbol, interval, limit)
    result = []
    for k in klines:
        o, h, l, c, v = float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])
        result.append({
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "volume": v,
            "change_pct": round(((c - o) / o) * 100, 2) if o > 0 else 0
        })
    return result
