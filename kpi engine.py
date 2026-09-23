"""
NABBAH 2.2 — Central KPI Engine
محرك KPI مركزي — يستهلك Semantic Layer و Financial Engine (Phase 2.1).
لا يُعيد تنفيذ أي صيغة مالية.

19 KPI: Decimal، معالجة القسمة على صفر، لا NaN/Infinity، نقص البيانات صريح.
"""
import sys, os
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "phase21"))
sys.path.insert(0, _HERE)

from decimal import Decimal
from nabbah_finance import (to_decimal, safe_divide, round_money, round_pct,
                            compute_sales, compute_profit, ENGINE_VERSION)
from semantic_layer import metric_envelope, get_metric_definition

KPI_ENGINE_VERSION = "1.0"


def _pct(numerator, denominator):
    """نسبة مئوية آمنة — None عند القسمة على صفر أو نقص البيانات."""
    r = safe_divide(to_decimal(numerator), to_decimal(denominator))
    if r is None:
        return None
    return round_pct(r * 100)


def compute_kpis(raw: dict, *, currency="SAR", period=None,
                 source="entry", data_quality="unknown", lang="ar"):
    """يحسب كل مؤشرات الأداء من البيانات الخام.

    raw يقبل: gross_sales, discounts, returns, vat, transactions,
              cogs, operating_expenses, payroll, rent, marketing, delivery,
              depreciation, amortization, interest, tax, previous_net_sales

    يُرجع dict من metric_id → envelope كامل.
    نقص البيانات → value=None (لا صفر مخترع).
    """
    g = to_decimal(raw.get("gross_sales"))
    disc = to_decimal(raw.get("discounts"))
    ret = to_decimal(raw.get("returns"))
    vat = to_decimal(raw.get("vat"))
    tx = to_decimal(raw.get("transactions"))
    cogs = to_decimal(raw.get("cogs"))
    opex = to_decimal(raw.get("operating_expenses"))
    payroll = to_decimal(raw.get("payroll"))
    rent = to_decimal(raw.get("rent"))
    marketing = to_decimal(raw.get("marketing"))
    delivery = to_decimal(raw.get("delivery"))
    prev_net = to_decimal(raw.get("previous_net_sales"))

    # ① المبيعات — عبر المحرك المركزي (Phase 2.1)
    sales = compute_sales(g, discounts=disc or 0, returns=ret or 0, vat=vat or 0,
                          transactions=tx, currency=currency, period=period,
                          source=source, quality=data_quality)
    net_sales_dec = to_decimal(sales["net_sales"]["value_decimal"])

    # ② الربحية — عبر المحرك المركزي (تسلسل كامل)
    profit = compute_profit(
        net_sales_dec, cogs=cogs, operating_expenses=opex,
        depreciation=raw.get("depreciation"), amortization=raw.get("amortization"),
        interest=raw.get("interest"), tax=raw.get("tax"),
        currency=currency, period=period, source=source, quality=data_quality)

    def env(mid, val, vdec=None):
        return metric_envelope(mid, val, currency=currency, period=period,
                               source=source, data_quality=data_quality,
                               value_decimal=vdec, lang=lang)

    def from_engine(mid, engine_result):
        """يلفّ نتيجة المحرك بالغلاف الدلالي (لا إعادة حساب)."""
        return env(mid, engine_result["value"], engine_result["value_decimal"])

    kpis = {}
    # المبيعات
    kpis["gross_sales"] = from_engine("gross_sales", sales["gross_sales"])
    kpis["discounts"] = from_engine("discounts", sales["discounts"])
    kpis["returns"] = from_engine("returns", sales["returns"])
    kpis["net_sales"] = from_engine("net_sales", sales["net_sales"])
    kpis["vat"] = from_engine("vat", sales["vat"])
    kpis["transactions"] = from_engine("transactions", sales["transactions"])
    kpis["aov"] = from_engine("aov", sales["avg_order_value"])
    # الربحية
    kpis["cogs"] = from_engine("cogs", profit["cogs"])
    kpis["gross_profit"] = from_engine("gross_profit", profit["gross_profit"])
    kpis["gross_margin"] = from_engine("gross_margin", profit["gross_margin"])
    kpis["operating_expenses"] = from_engine("operating_expenses", profit["operating_expenses"])
    kpis["ebit"] = from_engine("ebit", profit["operating_profit"])
    kpis["ebitda"] = from_engine("ebitda", profit["ebitda"])
    kpis["pbt"] = from_engine("pbt", profit["profit_before_tax"])
    kpis["net_profit"] = from_engine("net_profit", profit["net_profit"])
    kpis["net_margin"] = from_engine("net_margin", profit["net_margin"])

    # النِسب التشغيلية (محسوبة هنا مركزياً — صيغة واحدة)
    ns = net_sales_dec
    exp_ratio = _pct(opex, ns) if (opex is not None and ns) else None
    kpis["expense_ratio"] = env("expense_ratio", float(exp_ratio) if exp_ratio is not None else None,
                                 str(exp_ratio) if exp_ratio is not None else None)
    cogs_p = _pct(cogs, ns) if (cogs is not None and ns) else None
    kpis["cogs_pct"] = env("cogs_pct", float(cogs_p) if cogs_p is not None else None,
                            str(cogs_p) if cogs_p is not None else None)
    pay_p = _pct(payroll, ns) if (payroll is not None and ns) else None
    kpis["payroll_pct"] = env("payroll_pct", float(pay_p) if pay_p is not None else None,
                               str(pay_p) if pay_p is not None else None)
    rent_p = _pct(rent, ns) if (rent is not None and ns) else None
    kpis["rent_pct"] = env("rent_pct", float(rent_p) if rent_p is not None else None,
                            str(rent_p) if rent_p is not None else None)
    mkt_p = _pct(marketing, ns) if (marketing is not None and ns) else None
    kpis["marketing_pct"] = env("marketing_pct", float(mkt_p) if mkt_p is not None else None,
                                 str(mkt_p) if mkt_p is not None else None)
    del_p = _pct(delivery, ns) if (delivery is not None and ns) else None
    kpis["delivery_pct"] = env("delivery_pct", float(del_p) if del_p is not None else None,
                                str(del_p) if del_p is not None else None)
    ret_p = _pct(ret, g) if (ret is not None and g) else None
    kpis["return_pct"] = env("return_pct", float(ret_p) if ret_p is not None else None,
                              str(ret_p) if ret_p is not None else None)
    disc_p = _pct(disc, g) if (disc is not None and g) else None
    kpis["discount_pct"] = env("discount_pct", float(disc_p) if disc_p is not None else None,
                                str(disc_p) if disc_p is not None else None)

    # النمو
    growth = None
    if ns is not None and prev_net is not None and prev_net != 0:
        gr = safe_divide((ns - prev_net) * 100, prev_net)
        growth = round_pct(gr) if gr is not None else None
    kpis["growth"] = env("growth", float(growth) if growth is not None else None,
                          str(growth) if growth is not None else None)

    return kpis


def get_kpi(kpis: dict, metric_id: str):
    """يجلب مؤشراً واحداً من النتيجة. None إن لم يوجد."""
    return kpis.get(metric_id)


def kpi_value(kpis: dict, metric_id: str):
    """يجلب القيمة الخام (Decimal) لمؤشر — للحسابات اللاحقة."""
    k = kpis.get(metric_id)
    if not k or k.get("value_decimal") is None:
        return None
    return to_decimal(k["value_decimal"])
