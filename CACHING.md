# Caching Strategy & Configuration

## Status
**✅ Cache Pre-loaded**: All 20 symbols cached with fresh data (90-day lookback)  
**✅ Research Cached**: Analyst consensus, earnings, quality metrics pre-calculated  
**Last Updated**: 2026-06-01  

### Cache TTL
- **Market Data (OHLCV)**: 24 hours
- **Analyst Consensus**: 24 hours (in-memory)
- **Earnings Dates**: 24 hours (in-memory)
- **Hedge Fund Holdings**: 6 hours (quarterly data)
- **Radar Scans**: 2 hours (technical signals)
- **Quality Scores**: On-demand (computed real-time)

## Why Caching?
- **Prevents Rate Limiting**: Yahoo Finance throttles requests; cache avoids re-fetching
- **Fast Dashboard**: Instant loads instead of waiting 30+ seconds per symbol
- **Resilient**: Falls back to stale cache if all APIs fail
- **Fallback Sources**: 
  - Yahoo Finance Chart API (primary)
  - yfinance library (secondary)
  - Tiingo free tier (tertiary)

## How It Works

### Automatic Caching
Every time you load the dashboard:
1. Check if cache exists and is < 24 hours old
2. Serve from cache (instant ⚡)
3. Cache is valid → Skip data fetch
4. Cache expired → Fetch fresh data and update

### Manual Pre-Load
Run to refresh cache with latest data (recommended 1x daily):
```bash
# Load market data (90 days)
python cache_loader.py --days 90

# Load research data (30 days) 
python cache_research_loader.py --days 30

# Or both with one click (Windows):
cache_preload.bat
```

### Single Symbol
Load fresh data for specific symbol:
```bash
python cache_loader.py --symbol AAPL
```

### Batch File (Windows)
Double-click for one-click cache refresh:
```
cache_preload.bat
```

## Cache Structure
```
cache/
  AAPL_62629e0628.pkl        # Market data pickle files
  MSFT_35285a8a70.pkl        # Format: {symbol}_{hash}.pkl
  GOOGL_4aa1f4e7df.pkl       # One per (symbol, date_range, interval)
  ...
  research/
    AAPL_research.json       # Research data (analyst, earnings, etc)
    MSFT_research.json
    ...
```

## Two Types of Caching

### 1. Market Data Cache (OHLCV Prices)
Pre-loader: `cache_loader.py`  
Usage: `python cache_loader.py --days 90`

### 2. Research Data Cache (NEW!)
Includes: Analyst consensus, earnings dates, quality metrics  
Pre-loader: `cache_research_loader.py`  
Usage: `python cache_research_loader.py --days 30`

Research data is in-memory cached by API with TTL checks:
- Analyst: 24 hours
- Earnings: 24 hours  
- Hedge Funds: 6 hours (quarterly data)

## Troubleshooting

### Dashboard Still Slow?
1. **Force refresh cache**:
   ```bash
   python cache_loader.py --days 90
   ```

2. **Check cache age**:
   ```bash
   ls -lah cache/
   ```

3. **Clear old cache** (if corrupted):
   ```bash
   rm cache/*.pkl
   ```
   Then run `cache_loader.py` again.

### Missing Data for Symbol?
The `cache_loader.py` automatically tries multiple sources:
- Yahoo Finance (primary)
- Tiingo API (demo token, free)
- yfinance library (fallback)

If all fail, error message suggests: `python cache_loader.py --symbol SYMBOL`

### Data Not Updating?
Cache expires after 24 hours. To force update:
```bash
python cache_loader.py --symbol <SYMBOL>
```

## Performance Targets
- **With Cache**: Dashboard loads in <2 seconds
- **First Load (no cache)**: ~30-60 seconds (depending on symbols)
- **Subsequent Loads**: <100ms (cache hit)

## Recommended Setup
1. **Daily**: Run `cache_preload.bat` before market opens (9:30 AM ET)
2. **Or**: Add Windows Task Scheduler job to auto-run `cache_loader.py`
3. **Monitor**: Check cache folder file timestamps occasionally

## Alternative Data Sources (if needed)
- **Tiingo**: `tiingo.com` (free tier, used as fallback)
- **Polygon.io**: Better historical data (requires API key)
- **Alpha Vantage**: Alternative source (requires API key)
- **IEX Cloud**: Quick quotes (free tier limited)

See `cache_loader.py` for integration examples.
