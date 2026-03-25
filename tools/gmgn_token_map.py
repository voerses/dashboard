#!/usr/bin/env python3
"""
Token Address Mapping — Binance Futures symbols to DEX contract addresses.

Maps meme/volatile tokens traded on Binance Futures to their on-chain
contract addresses for use with GMGN.AI and DexScreener APIs.

Usage:
    from tools.gmgn_token_map import load_token_map, get_address

    token_map = load_token_map()
    chain, address = get_address(token_map, "BONK")
"""

import json
import os
from pathlib import Path

TOKEN_MAP_PATH = Path(__file__).resolve().parent.parent / "data" / "alternative" / "gmgn_ai" / "token_map.json"


def load_token_map() -> dict:
    """Load the token address mapping from JSON.

    Returns:
        dict: {TOKEN: {"chain": str, "address": str}, ...}
    """
    if not TOKEN_MAP_PATH.exists():
        raise FileNotFoundError(f"Token map not found: {TOKEN_MAP_PATH}")
    with open(TOKEN_MAP_PATH) as f:
        return json.load(f)


def get_address(token_map: dict, token: str) -> tuple:
    """Get (chain, address) for a token.

    Returns:
        (chain, address) or (None, None) if not found.
    """
    entry = token_map.get(token)
    if entry is None:
        return None, None
    return entry["chain"], entry["address"]


def get_tokens_by_chain(token_map: dict, chain: str) -> list:
    """Get all tokens for a given chain.

    Returns:
        list of (token, address) tuples.
    """
    return [
        (token, entry["address"])
        for token, entry in token_map.items()
        if entry["chain"] == chain
    ]


if __name__ == "__main__":
    tm = load_token_map()
    print(f"Loaded {len(tm)} tokens:")
    for chain in sorted(set(e["chain"] for e in tm.values())):
        tokens = get_tokens_by_chain(tm, chain)
        print(f"\n  {chain.upper()} ({len(tokens)} tokens):")
        for token, addr in tokens:
            print(f"    {token:10s} {addr[:20]}...")
