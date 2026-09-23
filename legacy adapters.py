"""
NABBAH 2.2 — Legacy Migration Adapter (Option B, approved)

Single implementation of the financial fields of main.compute_company_metrics().

LEGACY_COMPATIBLE (default, used in production):
  keeps the legacy float arithmetic ORDER (because it produced the stored values)
  and performs every rounding through the engine's explicit quantize() in
  LEGACY_COMPATIBLE mode. Output is identical to the old code by design.

ACCOUNTING_HALF_UP (not enabled; for the future migration dry-run only):
  exact Decimal arithmetic with half-up rounding.
"""
import os, sys
from decimal import Decimal
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "phase21"))
from nabbah_finance import (to_decimal, safe_divide, quantize,  # noqa: E402
                            LEGACY_COMPATIBLE, ACCOUNTING_HALF_UP)

ADAPTER_VERSION = "2.0"


def _f(q):
    return float(q) if q is not None else 0


def central_company_metrics(sales, invoices, customers, repeat_customers, expenses,
                            prev_sales=None, rounding_mode=LEGACY_COMPATIBLE):
    if rounding_mode == LEGACY_COMPATIBLE:
        profit = _f(quantize(sales - expenses, 2, LEGACY_COMPATIBLE))
        margin = _f(quantize((profit / sales) * 100, 1, LEGACY_COMPATIBLE)) if sales > 0 else 0
        avg_invoice = _f(quantize(sales / invoices, 1, LEGACY_COMPATIBLE)) if invoices > 0 else 0
        repeat_rate = _f(quantize((repeat_customers / customers) * 100, 1, LEGACY_COMPATIBLE)) if customers > 0 else 0
        has_prev = bool(prev_sales and prev_sales > 0)
        growth = _f(quantize(((sales - prev_sales) / prev_sales) * 100, 1, LEGACY_COMPATIBLE)) if has_prev else 0
    elif rounding_mode == ACCOUNTING_HALF_UP:
        s, e = to_decimal(sales) or Decimal(0), to_decimal(expenses) or Decimal(0)
        inv, cust = to_decimal(invoices) or Decimal(0), to_decimal(customers) or Decimal(0)
        rep, prev = to_decimal(repeat_customers) or Decimal(0), to_decimal(prev_sales)
        H = ACCOUNTING_HALF_UP
        profit_d = quantize(s - e, 2, H)
        profit = float(profit_d)
        margin = _f(quantize(safe_divide(profit_d * 100, s), 1, H)) if s > 0 else 0
        avg_invoice = _f(quantize(safe_divide(s, inv), 1, H)) if inv > 0 else 0
        repeat_rate = _f(quantize(safe_divide(rep * 100, cust), 1, H)) if cust > 0 else 0
        has_prev = bool(prev is not None and prev > 0)
        growth = _f(quantize(safe_divide((s - prev) * 100, prev), 1, H)) if has_prev else 0
    else:
        raise ValueError(f"Unknown rounding mode: {rounding_mode}")
    return {"profit": profit, "margin": margin, "avg_invoice": avg_invoice,
            "repeat_rate": repeat_rate, "growth": growth, "has_prev": has_prev,
            "rounding_mode": rounding_mode}
