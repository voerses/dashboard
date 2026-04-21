"""v5 logging infrastructure.

Currently hosts the M10 AC #22 ``LogRotator`` — a uniform size-based
rotation + gzip policy applicable to every JSONL sink under
``v5/logs/``. Individual sinks (``arbitration.jsonl``,
``sizing_fills.jsonl``, ``orders_log.jsonl``, ``funding_accruals.jsonl``,
``risk_decisions.jsonl``, ``clock_drift.jsonl``) share this policy.
"""
from v5.logs.log_rotator import LogRotator

__all__ = ["LogRotator"]
