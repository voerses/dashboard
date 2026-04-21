# TokenBarArrays ↔ DataEngine Field-Inventory Gap Table

Per M10 design §A1 (Cluster A Q2 gate). Documents every 
`TokenBarArrays` dataclass field and its source classification:

- **data_engine_sourced**: populated directly from `ctx.data._arrays[token][...]`
- **derived**: computed from data_engine_sourced arrays inside the bridge builder
- **strategy_provided**: filled from `Strategy.generate(ctx, bar)` output or `Strategy.spec`

| field | source | classification |
| --- | --- | --- |
| `armed_direction` | Strategy.generate / Strategy.spec | strategy_provided |
| `armed_levels` | Strategy.generate / Strategy.spec | strategy_provided |
| `atr` | ctx.data._arrays[tok][field] | data_engine_sourced |
| `breakeven_atr` | Strategy.generate / Strategy.spec | strategy_provided |
| `capital_split` | Strategy.generate / Strategy.spec | strategy_provided |
| `chandelier_lookback` | Strategy.generate / Strategy.spec | strategy_provided |
| `close` | ctx.data._arrays[tok][field] | data_engine_sourced |
| `convex_bar_thresholds` | Strategy.generate / Strategy.spec | strategy_provided |
| `convex_exit` | Strategy.generate / Strategy.spec | strategy_provided |
| `convex_multipliers` | Strategy.generate / Strategy.spec | strategy_provided |
| `direction` | Strategy.generate / Strategy.spec | strategy_provided |
| `edge` | Strategy.generate / Strategy.spec | strategy_provided |
| `entry_delay` | Strategy.generate / Strategy.spec | strategy_provided |
| `entry_limit_price` | Strategy.generate / Strategy.spec | strategy_provided |
| `entry_mask` | Strategy.generate / Strategy.spec | strategy_provided |
| `funding_1h` | ctx.data._arrays[tok][field] | data_engine_sourced |
| `funding_exit_threshold` | Strategy.generate / Strategy.spec | strategy_provided |
| `funding_zscore` | derived in _build_token_bar_arrays_from_generate | derived |
| `high` | ctx.data._arrays[tok][field] | data_engine_sourced |
| `is_combined` | Strategy.generate / Strategy.spec | strategy_provided |
| `is_perp_primary` | Strategy.generate / Strategy.spec | strategy_provided |
| `is_perp_secondary` | Strategy.generate / Strategy.spec | strategy_provided |
| `leverage` | Strategy.generate / Strategy.spec | strategy_provided |
| `low` | ctx.data._arrays[tok][field] | data_engine_sourced |
| `max_hold` | Strategy.generate / Strategy.spec | strategy_provided |
| `max_trail_mult` | Strategy.generate / Strategy.spec | strategy_provided |
| `mean_target_vals` | Strategy.generate / Strategy.spec | strategy_provided |
| `min_hold` | Strategy.generate / Strategy.spec | strategy_provided |
| `n_bars` | Strategy.generate / Strategy.spec | strategy_provided |
| `no_stop_bars` | Strategy.generate / Strategy.spec | strategy_provided |
| `per_bar_is_perp` | Strategy.generate / Strategy.spec | strategy_provided |
| `perp_atr` | derived in _build_token_bar_arrays_from_generate | derived |
| `perp_close` | derived in _build_token_bar_arrays_from_generate | derived |
| `perp_funding_1h` | derived in _build_token_bar_arrays_from_generate | derived |
| `perp_high` | derived in _build_token_bar_arrays_from_generate | derived |
| `perp_low` | derived in _build_token_bar_arrays_from_generate | derived |
| `perp_rolling_adv` | derived in _build_token_bar_arrays_from_generate | derived |
| `post_liquidity_count` | derived in _build_token_bar_arrays_from_generate | derived |
| `post_walkforward_count` | derived in _build_token_bar_arrays_from_generate | derived |
| `priority` | Strategy.generate / Strategy.spec | strategy_provided |
| `raw_entry_count` | Strategy.generate / Strategy.spec | strategy_provided |
| `ret_1h` | derived in _build_token_bar_arrays_from_generate | derived |
| `rolling_adv` | ctx.data._arrays[tok][field] | data_engine_sourced |
| `rsi` | Strategy.generate / Strategy.spec | strategy_provided |
| `rsi_exit_level` | Strategy.generate / Strategy.spec | strategy_provided |
| `sec_max_hold` | Strategy.generate / Strategy.spec | strategy_provided |
| `sec_min_hold` | Strategy.generate / Strategy.spec | strategy_provided |
| `sec_no_stop_bars` | Strategy.generate / Strategy.spec | strategy_provided |
| `sec_stop_mult` | Strategy.generate / Strategy.spec | strategy_provided |
| `sec_target_mult` | Strategy.generate / Strategy.spec | strategy_provided |
| `sec_trail_mult` | Strategy.generate / Strategy.spec | strategy_provided |
| `secondary_direction` | Strategy.generate / Strategy.spec | strategy_provided |
| `secondary_entry_mask` | Strategy.generate / Strategy.spec | strategy_provided |
| `secondary_leverage` | Strategy.generate / Strategy.spec | strategy_provided |
| `sma_trail_vals` | Strategy.generate / Strategy.spec | strategy_provided |
| `stop_mult` | Strategy.generate / Strategy.spec | strategy_provided |
| `strategy_id` | Strategy.generate / Strategy.spec | strategy_provided |
| `target_mult` | Strategy.generate / Strategy.spec | strategy_provided |
| `time_trail_schedule` | Strategy.generate / Strategy.spec | strategy_provided |
| `timestamps` | ctx.data._arrays[tok][field] | data_engine_sourced |
| `token` | Strategy.generate / Strategy.spec | strategy_provided |
| `trail_mult` | Strategy.generate / Strategy.spec | strategy_provided |
| `trail_schedule` | Strategy.generate / Strategy.spec | strategy_provided |
| `vol_20` | derived in _build_token_bar_arrays_from_generate | derived |
| `volume` | ctx.data._arrays[tok][field] | data_engine_sourced |
