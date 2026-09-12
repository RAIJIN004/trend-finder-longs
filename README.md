# Binance Longs (MCP Server) — LONG ONLY

Copia LONG-only de binance-trend-finder (sin doctrina contrarian/short).
Doctrina: **Square + orderbook alineados a LONG + precio divergente en contra
(pullback) = detonante de cambio de setup.** Se entra en el pullback con orden
límite (`wait_for_pullback`), nunca a mercado en el pico.

> NO ES ASESORÍA NI ANÁLISIS FINANCIERO. DYOR siempre.

MCP server for scanning Binance markets to detect coins **moving RIGHT NOW with consistency**: hybrid filter (intraday gate + daily-streak ranking + safety verdict).

## Filtro HIBRIDO (reemplaza al ATR diario)

1. **Puerta intradía** — 1h≥1.5% + 4h≥2% + spike volumen≥1x (velas 15m): solo pasa lo que se mueve AHORA. El ATR diario quedó como dato informativo.
2. **Ranking por racha diaria** — consistencia primero (racha > 1h > neto7d > spike). El neto 7d negativo hunde los rebotes de desplome al fondo.
3. **Veredicto de seguridad** — `OK` / `PRECAUCION` (sobre-extendida +35%, en el pico de 4h) / `EVITAR` (+80% en 24h).

## Installation

```bash
pip install -e .
```

Or install dependencies directly:

```bash
pip install mcp requests
```

## Usage with Claude Desktop / OpenCode / Hermes

### OpenCode (`opencode.json`):
```json
{
  "mcp": {
    "binance-trend-finder": {
      "type": "local",
      "command": [
        "C:\\Python313\\python.exe",
        "C:\\Users\\jhonv\\Downloads\\binance-mcp-server\\server.py"
      ],
      "timeout": 120,
      "enabled": true
    }
  }
}
```

### Hermes Agent (`config.yaml`):
```yaml
mcp_servers:
  binance-trend-finder:
    command: C:\Python313\python.exe
    args:
      - C:\Users\jhonv\Downloads\binance-mcp-server\server.py
    connect_timeout: 60
    enabled: true
```

## Available MCP Tools

### `scan_extensive_movements`
Scans all Binance USDT pairs for sustained bullish momentum.

**Parameters:**
- `only_positive` (bool, default: `True`): Solo devuelve monedas con racha positiva y rendimiento neto semanal positivo.
- `min_positive_days` (int, default: `4`): Mínimo de días verdes en los últimos 8 días.
- `min_atr_pct` (float, default: `2.0`): Filtro de ATR % para descartar monedas sin rango ni volatilidad.
- `min_24h_pct` (float, default: `0.0`): Cambio mínimo positivo en las últimas 24h.
- `min_net_7d_pct` (float, default: `0.0`): Rendimiento neto mínimo en 7 días.
- `min_volume` (float, default: `5000000`): Volumen mínimo 24h en USDT.
- `top_n` (int, default: `20`): Número de resultados a retornar.

### `get_coin_analysis`
Detailed analysis of a specific symbol (`BTCUSDT`, `NEARUSDT`, etc.) including green streaks, net 7d gain, ATR %, and trend verdict.

### `list_active_pairs`
Quick overview of active USDT pairs filtered by volume.

## License

MIT
