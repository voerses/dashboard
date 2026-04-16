"""
Composite Fragility Index — Gate 1
----------------------------------
Builds a funding-rate-based stress composite from 149 symbols in
data/alternative/funding_ls_proxy (daily) and ranks volume via
data/perp/binance/1h_ohlcv CSVs.

Components:
  1. Volume-weighted average funding rate across top-50 most-liquid perps,
     z-scored over 7-day rolling window (168h, but we use daily data: 7 obs).
  2. Funding dispersion: cross-symbol stdev of funding rates, z-scored 7d.
  3. Funding level stress: abs(weighted funding) z-score over 7d.

Composite: 0.4*avg_z + 0.3*disp_z + 0.3*level_z, mapped to [0,1] via sigmoid.

Outputs: research/fragility_index.parquet
"""
import os
import glob
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROXY_DIR = os.path.join(ROOT, "data/alternative/funding_ls_proxy")
OHLCV_DIR = os.path.join(ROOT, "data/perp/binance/1h_ohlcv")
OUT_PATH = os.path.join(ROOT, "research/fragility_index.parquet")


def load_funding_panel():
    """Return wide DataFrame (date x symbol) of daily funding rates."""
    files = sorted(glob.glob(os.path.join(PROXY_DIR, "*_funding_proxy.parquet")))
    series = {}
    for f in files:
        sym = os.path.basename(f).replace("_funding_proxy.parquet", "")
        if sym == "all":
            continue
        df = pd.read_parquet(f, columns=["funding_rate_daily"])
        if df.empty:
            continue
        s = df["funding_rate_daily"]
        # drop dupes, sort
        s = s[~s.index.duplicated(keep="last")].sort_index()
        series[sym] = s
    panel = pd.DataFrame(series)
    panel.index = pd.to_datetime(panel.index).normalize()
    panel = panel[~panel.index.duplicated(keep="last")].sort_index()
    return panel


def load_dollar_volume_panel(symbols, start, end):
    """Return wide DataFrame (date x symbol) of daily dollar volume."""
    vols = {}
    for sym in symbols:
        p = os.path.join(OHLCV_DIR, f"{sym}_perp_1h.csv")
        if not os.path.exists(p):
            continue
        df = pd.read_csv(p, usecols=["datetime", "close", "volume"])
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True).dt.tz_localize(None)
        df = df[(df["datetime"] >= start) & (df["datetime"] <= end)]
        if df.empty:
            continue
        df["dollar"] = df["close"] * df["volume"]
        daily = df.set_index("datetime")["dollar"].resample("1D").sum()
        vols[sym] = daily
    vdf = pd.DataFrame(vols)
    vdf.index = pd.to_datetime(vdf.index).normalize()
    return vdf


def top_n_universe(vol_panel, n=50, window=7):
    """For each date, return boolean mask (True = in top-N by 7d rolling $vol)."""
    roll = vol_panel.rolling(window, min_periods=3).sum()
    ranks = roll.rank(axis=1, ascending=False, method="first")
    mask = ranks <= n
    return mask


def compute_fragility(funding, vol_mask, vol_panel, z_window=7):
    """
    For each date compute:
      avg_raw: volume-weighted mean funding across top-50 universe
      disp_raw: cross-symbol stdev (equal-weighted) of funding in top-50
      level_raw: abs(avg_raw)

    Z-score each over rolling z_window days. Composite = weighted sum,
    mapped to [0,1] via logistic sigmoid.
    """
    # Align
    idx = funding.index.intersection(vol_mask.index)
    funding = funding.reindex(idx)
    vol_mask = vol_mask.reindex(idx)
    vol_panel = vol_panel.reindex(idx).reindex(columns=funding.columns)

    # mask funding to universe
    f_univ = funding.where(vol_mask.reindex(columns=funding.columns, fill_value=False))
    w_univ = vol_panel.where(vol_mask.reindex(columns=vol_panel.columns, fill_value=False))

    # Volume-weighted avg funding
    num = (f_univ * w_univ).sum(axis=1, skipna=True)
    den = w_univ.where(f_univ.notna()).sum(axis=1, skipna=True)
    avg_raw = num / den.replace(0, np.nan)

    # cross-symbol stdev (equal weight) across present symbols in universe
    disp_raw = f_univ.std(axis=1, skipna=True, ddof=0)

    level_raw = avg_raw.abs()

    def rolling_z(s, w):
        mu = s.rolling(w, min_periods=max(3, w // 2)).mean()
        sd = s.rolling(w, min_periods=max(3, w // 2)).std(ddof=0)
        return (s - mu) / sd.replace(0, np.nan)

    avg_z = rolling_z(avg_raw, z_window)
    disp_z = rolling_z(disp_raw, z_window)
    level_z = rolling_z(level_raw, z_window)

    raw_comp = 0.4 * avg_z.fillna(0) + 0.3 * disp_z.fillna(0) + 0.3 * level_z.fillna(0)
    # Logistic sigmoid centered at 0, scale 1.0 -> maps z~[-2,+2] to [~0.12, ~0.88]
    frag = 1.0 / (1.0 + np.exp(-raw_comp))

    out = pd.DataFrame(
        {
            "frag_idx": frag,
            "avg_z": avg_z,
            "disp_z": disp_z,
            "level_z": level_z,
            "avg_raw": avg_raw,
            "disp_raw": disp_raw,
            "universe_n": f_univ.notna().sum(axis=1),
        }
    )
    out.index.name = "date"
    return out


def main():
    print("[1/4] Loading funding panel ...")
    funding = load_funding_panel()
    print(f"  funding panel shape: {funding.shape}  range: {funding.index.min().date()} -> {funding.index.max().date()}")

    print("[2/4] Loading dollar volume panel ...")
    vol = load_dollar_volume_panel(
        funding.columns.tolist(),
        start=funding.index.min() - pd.Timedelta(days=14),
        end=funding.index.max() + pd.Timedelta(days=1),
    )
    print(f"  volume panel shape: {vol.shape}")

    print("[3/4] Building top-50 dynamic universe ...")
    mask = top_n_universe(vol, n=50, window=7)
    print(f"  mean universe size: {mask.sum(axis=1).mean():.1f}")

    print("[4/4] Computing fragility index ...")
    frag = compute_fragility(funding, mask, vol, z_window=7)
    print("  quantiles:")
    print(frag["frag_idx"].quantile([0.05, 0.25, 0.5, 0.75, 0.95]).to_string())

    frag.to_parquet(OUT_PATH)
    print(f"  -> saved to {OUT_PATH}")

    # print spike dates
    print("\nTop-20 fragility spike dates:")
    print(frag["frag_idx"].nlargest(20).to_string())


if __name__ == "__main__":
    main()
