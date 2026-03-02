"""Find which of our 49 tokens have liquid perpetual contracts on each exchange."""
import ccxt
import json
import sys

OUR_TOKENS = [
    "AAVE","ADA","ALICE","APT","ARB","AVAX","BCH","BNB","BONK","BTC",
    "CAKE","CHZ","DASH","DENT","DOGE","DOT","ENA","ETH","FET","FIL",
    "FLOKI","HBAR","ICP","INJ","LINK","LTC","NEAR","OM","OP","PAXG",
    "PENDLE","PENGU","PEPE","POL","SEI","SHIB","SOL","SUI","TAO","TON",
    "TRUMP","TRX","UNI","WIF","WLD","XLM","XRP","ZEC","ZRO"
]

def check_exchange(exchange_id, quote, settle):
    """Check which tokens have perps on an exchange."""
    ex = getattr(ccxt, exchange_id)()
    ex.load_markets()
    found = {}
    for token in OUR_TOKENS:
        symbol = f"{token}/{quote}:{settle}"
        if symbol in ex.markets:
            m = ex.markets[symbol]
            found[token] = {
                "symbol": symbol,
                "id": m.get("id", ""),
                "active": m.get("active", True),
            }
    return found

if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "all"
    results = {}
    
    if target in ("binance", "all"):
        print("Checking Binance USDT-M perps...", flush=True)
        results["binance"] = check_exchange("binanceusdm", "USDT", "USDT")
        print(f"  Found: {len(results['binance'])}/{len(OUR_TOKENS)}")
    
    if target in ("kraken", "all"):
        print("Checking Kraken Futures perps...", flush=True)
        results["kraken"] = check_exchange("krakenfutures", "USD", "USD")
        print(f"  Found: {len(results['kraken'])}/{len(OUR_TOKENS)}")
    
    if target in ("hyperliquid", "all"):
        print("Checking Hyperliquid perps...", flush=True)
        results["hyperliquid"] = check_exchange("hyperliquid", "USDC", "USDC")
        print(f"  Found: {len(results['hyperliquid'])}/{len(OUR_TOKENS)}")
    
    # Save results
    with open("/workspace/crypto_backtest/data/perp/liquid_perps.json", "w") as f:
        json.dump(results, f, indent=2)
    
    # Print summary
    if len(results) > 1:
        all_exchanges = set()
        for ex_tokens in results.values():
            all_exchanges.update(ex_tokens.keys())
        print(f"\nTokens with perps on ANY exchange: {len(all_exchanges)}")
        
        # Tokens on all exchanges
        if len(results) >= 3:
            on_all = set(results.get("binance", {}).keys()) & set(results.get("kraken", {}).keys()) & set(results.get("hyperliquid", {}).keys())
            print(f"Tokens on ALL 3 exchanges: {len(on_all)}")
            missing = set(OUR_TOKENS) - all_exchanges
            if missing:
                print(f"No perps anywhere: {sorted(missing)}")
