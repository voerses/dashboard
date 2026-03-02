# Data Acquisition Playbook

Patterns for fetching historical crypto data. Covers rate limits, proxies,
bot protection bypass, and exchange-specific quirks.

## Environment Setup

```
Proxy:    http://10.100.2.10:3128 (required — Python DNS fails without it)
Platform: aarch64 Linux, glibc 2.36 (Debian bookworm)
Python:   /workspace/venv/bin/python
```

### Proxy Configuration

| Library | Auto-detects proxy? | Fix |
|---------|-------------------|-----|
| curl | Yes (env vars) | Nothing needed |
| requests | Yes (env vars) | Nothing needed |
| ccxt | **No** | `ccxt.exchange({'proxies': {'https': proxy, 'http': proxy}})` |
| node fetch | **No** | Use HTTP CONNECT tunnel or `--proxy-server` flag |

## Rate Limit Patterns

### Global Thread-Safe Rate Limiter

All multi-threaded fetchers MUST use a shared lock, not per-thread delays:

```python
_rate_lock = threading.Lock()
_last_request_time = 0.0

def global_rate_limit(min_interval):
    global _last_request_time
    with _rate_lock:
        elapsed = time.monotonic() - _last_request_time
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        _last_request_time = time.monotonic()
```

### Exchange Rate Limits

| Exchange | Budget | Interval | Workers | Notes |
|----------|--------|----------|---------|-------|
| Binance USDT-M | 2,400 weight/min | 0.167s | 4 | fetch_ohlcv=5 weight |
| Kraken Futures | Unlimited (public) | 0.1s polite | 6 | Charts API, no limit |
| Hyperliquid | 1,200 req/min | 2.5s | 4 | POST to /info, 429 detection |

### ccxt Shared Markets Cache

Load markets once, share across threads to avoid N redundant API calls:

```python
_shared_markets = None  # set in main()
# In each thread:
ex = create_exchange()
ex.markets = _shared_markets
```

## Cloudflare Bot Protection Bypass

When a data source is behind Cloudflare managed challenge (403 + JS challenge):

### Step 1: Install Playwright + Chromium

```bash
/workspace/venv/bin/pip install playwright
PLAYWRIGHT_BROWSERS_PATH=/workspace/.playwright playwright install chromium
```

### Step 2: Provision Missing Shared Libraries (no root)

On aarch64 Debian bookworm, Chromium needs ~16 `.so` files not installed by default.
Download `.deb` packages and extract manually:

```bash
# Determine arch and glibc version
uname -m                                    # aarch64
/lib/aarch64-linux-gnu/libc.so.6 --version  # glibc 2.36

# Download from Debian bookworm (matches glibc)
BASE="http://deb.debian.org/debian/pool/main"
PORTS="http://ports.ubuntu.com/ubuntu-ports/pool/main"  # arm64 fallback
curl --proxy $HTTP_PROXY -sL "$URL" -o /tmp/lib.deb
dpkg-deb -x /tmp/lib.deb /workspace/.libs/

# Create SONAME symlinks
cd /workspace/.libs/usr/lib/aarch64-linux-gnu/
for f in *.so.*; do
    soname=$(echo "$f" | grep -oP '^.*?\.so\.\d+')
    [ -n "$soname" ] && [ ! -e "$soname" ] && ln -sf "$f" "$soname"
done
```

Required packages (arm64, Debian bookworm compatible):
`libnspr4, libnss3, libatk1.0-0, libatk-bridge2.0-0, libatspi2.0-0,
libdbus-1-3, libcups2, libxkbcommon0, libxcomposite1, libxdamage1,
libxfixes3, libxrandr2, libgbm1, libasound2, libdrm2, libwayland-server0,
libavahi-common3, libavahi-client3, libxi6`

### Step 3: Run Playwright with LD_LIBRARY_PATH

```python
# Launch with libs and proxy
env LD_LIBRARY_PATH="/workspace/.libs/usr/lib/aarch64-linux-gnu:..." \
    PLAYWRIGHT_BROWSERS_PATH=/workspace/.playwright \
    python script.py

# In script:
browser = await p.chromium.launch(
    headless=True,
    args=['--no-sandbox', '--disable-gpu',
          '--proxy-server=http://10.100.2.10:3128']
)
page = await browser.new_page()
await page.goto(url, wait_until='commit', timeout=30000)
await page.wait_for_timeout(5000)  # let CF challenge execute
content = await page.content()
```

Key: use `wait_until='commit'` (not `networkidle` — CF keeps polling).
Wait 5s after initial load for challenge resolution.

## Exchange-Specific Quirks

### Kraken Futures
- **ccxt fetch_ohlcv returns empty** — use native Charts API instead:
  `GET https://futures.kraken.com/api/charts/v1/trade/{symbol}/1h?from=&to=`
- **Funding API caps at ~1 year** — use bulk CSV export from support page:
  `https://assets-cms.kraken.com/files/51n36hrp/facade/4b70936c...zip`
  (Behind Cloudflare — use Playwright approach above)
- Symbol format: `PF_XBTUSD` (linear), `PI_XBTUSD` (inverse). XBT = BTC.

### Binance USDT-M
- OI endpoint: `startTime` param fails for newer tokens — catch and skip
- Funding: 8h intervals, data from Jan 2020
- Symbol suffix: always `/USDT:USDT` for perpetuals

### Hyperliquid
- POST-based API: `{"type": "fundingHistory", "coin": "BTC", ...}`
- Funding paginated 500/page, OHLCV capped at 5000 candles per call
- S3 archive (requester-pays): needs AWS creds, ~$0.09/GB
- Thread-local `requests.Session` required (not thread-safe otherwise)

## Data Storage

```
data/perp/
  binance/{funding,1h_ohlcv,oi}/   → {TOKEN}_funding.csv, etc.
  kraken/{funding,1h_ohlcv}/        → {TOKEN}_funding.csv, etc.
  hyperliquid/{funding,ohlcv}/      → {TOKEN}_funding.csv, etc.
```

CSV schema (funding): `timestamp,datetime,funding_rate`
CSV schema (OHLCV): `timestamp,open,high,low,close,volume`

## Scripts

| Script | Exchange | Data | Status |
|--------|----------|------|--------|
| `tools/fetch_binance_perp.py` | Binance | funding + ohlcv + oi | Working |
| `tools/fetch_kraken_perp.py` | Kraken | funding only (via ccxt) | Working (1yr limit) |
| `tools/fetch_kraken_ohlcv.py` | Kraken | ohlcv via Charts API | Working |
| `tools/fetch_hyperliquid_perp.py` | Hyperliquid | funding + ohlcv | Working (slow) |
| `tools/fetch_hyperliquid_s3.py` | Hyperliquid | S3 archive | Needs AWS creds |
