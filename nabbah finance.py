"""
NABBAH 2.0 — Financial Calculation Engine (Phase 2.1)
محرك حسابات مالية مركزي — مصدر واحد للحقيقة (Single Source of Truth).

المبادئ:
- Decimal لكل الحسابات (لا Float غير منضبط).
- كل نتيجة موثّقة: metric, value, currency, period, source, method, quality, timestamp.
- لا NaN، لا Infinity، لا قسمة على صفر غير محميّة.
- لا يُخترع رقم — نقص البيانات يُوسم بوضوح (has_data / quality).

ملف مستقل — لا يلمس main.py. يُستورد تدريجياً لاستبدال الحسابات المكرّرة.
الإصدار: 1.0
"""
from decimal import Decimal, ROUND_HALF_UP, ROUND_HALF_EVEN, InvalidOperation, getcontext
from datetime import datetime, timezone

getcontext().prec = 28  # دقة عالية للحسابات المالية

ENGINE_VERSION = "1.1"


# ═══════════════════════════════════════════════════════════
#  أدوات مساعدة آمنة (لا NaN، لا Infinity، لا قسمة على صفر)
# ═══════════════════════════════════════════════════════════
def to_decimal(value):
    """يحوّل أي قيمة إلى Decimal بأمان. يُرجع None إن تعذّر (لا يرفع خطأ)."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        # ننظّف النصوص (فواصل، نسب، مسافات)
        s = str(value).replace(",", "").replace("%", "").replace("ريال", "").replace("SAR", "").strip()
        if s == "":
            return None
        d = Decimal(s)
        # منع Infinity و NaN
        if not d.is_finite():
            return None
        return d
    except (InvalidOperation, ValueError, TypeError):
        return None


def safe_divide(numerator, denominator):
    """قسمة آمنة — تُرجع None عند القسمة على صفر أو قيمة غير صالحة (لا Infinity)."""
    n = to_decimal(numerator)
    d = to_decimal(denominator)
    if n is None or d is None or d == 0:
        return None
    result = n / d
    if not result.is_finite():
        return None
    return result


# ═══════════════════════════════════════════════════════════
#  Rounding modes (Phase 2.2 closure — Option B approved)
#
#  LEGACY_COMPATIBLE (DEFAULT)
#    Reproduces Python round(float, n), which the legacy code used:
#    the value is taken as its exact IEEE-754 binary double, then rounded
#    half-to-even. Implemented explicitly with Decimal(float) + ROUND_HALF_EVEN,
#    never with round(). Guarantees stored values and branch scores do not change.
#    Limit: inherits float precision (~15-16 significant digits).
#
#  ACCOUNTING_HALF_UP (NOT enabled globally)
#    Exact decimal value, ties rounded away from zero (accounting convention).
#    Differs from legacy only on exact ties (e.g. 12.25 -> 12.3 vs 12.2) and on
#    values whose binary form sits just below a tie (12.35 -> 12.4 vs 12.3).
#    Enabling it requires the approved migration plan.
# ═══════════════════════════════════════════════════════════
LEGACY_COMPATIBLE = "LEGACY_COMPATIBLE"
ACCOUNTING_HALF_UP = "ACCOUNTING_HALF_UP"
ROUNDING_MODES = (LEGACY_COMPATIBLE, ACCOUNTING_HALF_UP)
DEFAULT_ROUNDING_MODE = LEGACY_COMPATIBLE


def quantize(value, places, mode=None):
    """Round a financial value with an explicit, named rounding mode."""
    d = to_decimal(value)
    if d is None:
        return None
    mode = mode or DEFAULT_ROUNDING_MODE
    quant = Decimal(10) ** -places
    if mode == LEGACY_COMPATIBLE:
        return Decimal(float(d)).quantize(quant, rounding=ROUND_HALF_EVEN)
    if mode == ACCOUNTING_HALF_UP:
        return d.quantize(quant, rounding=ROUND_HALF_UP)
    raise ValueError(f"Unknown rounding mode: {mode}")


def round_money(value, places=2, mode=None):
    """Money rounding (default mode: LEGACY_COMPATIBLE)."""
    return quantize(value, places, mode)


def round_pct(value, places=2, mode=None):
    """Percentage rounding (default mode: LEGACY_COMPATIBLE)."""
    return quantize(value, places, mode)


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


# ═══════════════════════════════════════════════════════════
#  غلاف النتيجة الموثّقة (كل رقم مالي يحمل سياقه)
# ═══════════════════════════════════════════════════════════
def _json_safe_value(value):
    """يحوّل Decimal لقيمة آمنة في JSON مع الحفاظ على الدقة.
    الأعداد الصحيحة تبقى int، والعشرية تبقى نصاً دقيقاً في value_decimal.
    value هنا للعرض السريع فقط — value_decimal هو المرجع المالي."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        # عدد صحيح؟ نُرجعه int (آمن تماماً في JSON)
        if value == value.to_integral_value():
            return int(value)
        # عشري: float للعرض (value_decimal يحمل الدقة الكاملة)
        return float(value)
    return value


def metric_result(name, value, *, currency="SAR", period=None, source=None,
                  method=None, quality="unknown", unit=""):
    """يُغلّف نتيجة مالية بكل سياقها الإلزامي.
    القيمة None تعني «بيانات ناقصة» — لا صفر مخترع.
    - value: للعرض السريع (int للصحيح، float للعشري)
    - value_decimal: المرجع المالي الدقيق (نص) — يُستخدم لأي حساب لاحق"""
    return {
        "metric": name,
        "value": _json_safe_value(value),                        # للعرض
        "value_decimal": str(value) if value is not None else None,  # المرجع الدقيق
        "currency": currency,
        "unit": unit,
        "period": period,
        "source": source or "calculated",
        "method": f"nabbah-finance-v{ENGINE_VERSION}",
        "rounding_mode": DEFAULT_ROUNDING_MODE,
        "data_quality": quality,
        "has_data": value is not None,
        "calculated_at": _now_iso(),
    }


# ═══════════════════════════════════════════════════════════
#  محرك المبيعات (Sales)
# ═══════════════════════════════════════════════════════════
def compute_sales(gross_sales, discounts=0, returns=0, vat=0, transactions=0,
                  *, currency="SAR", period=None, source="entry", quality="unknown"):
    """يحسب مؤشرات المبيعات الكاملة.
    Net Sales = Gross - Discounts - Returns (VAT منفصل)."""
    g = to_decimal(gross_sales)
    disc = to_decimal(discounts) or Decimal(0)
    ret = to_decimal(returns) or Decimal(0)
    v = to_decimal(vat) or Decimal(0)
    tx = to_decimal(transactions)

    net = None
    if g is not None:
        net = g - disc - ret

    aov = safe_divide(net, tx) if (net is not None and tx and tx > 0) else None

    mk = lambda name, val, unit="": metric_result(name, val, currency=currency,
                                                   period=period, source=source,
                                                   quality=quality, unit=unit)
    return {
        "gross_sales": mk("إجمالي المبيعات", round_money(g)),
        "discounts": mk("الخصومات", round_money(disc)),
        "returns": mk("المرتجعات", round_money(ret)),
        "net_sales": mk("صافي المبيعات", round_money(net)),
        "vat": mk("ضريبة القيمة المضافة", round_money(v)),
        "transactions": mk("عدد المعاملات", int(tx) if tx is not None else None),
        "avg_order_value": mk("متوسط قيمة الطلب", round_money(aov)),
    }


# ═══════════════════════════════════════════════════════════
#  محرك الربحية (Profit)
# ═══════════════════════════════════════════════════════════
def compute_profit(net_sales, cogs=None, operating_expenses=None, depreciation=None,
                   amortization=None, interest=None, tax=None,
                   *, currency="SAR", period=None, source="entry", quality="unknown"):
    """يحسب تسلسل الربح الكامل بفصل واضح للبنود.

    التسلسل المحاسبي الصحيح:
      Gross Profit    = Net Sales − COGS
      Operating Profit (EBIT) = Gross Profit − OpEx
      EBITDA          = EBIT + Depreciation + Amortization
      Profit Before Tax = EBIT − Interest
      Net Profit      = Profit Before Tax − Tax

    قاعدة صارمة: لا يُعرض مؤشر باسمه إلا إذا اكتملت مكوّناته.
    - EBITDA يتطلب depreciation أو amortization (وإلا None).
    - Net Profit يتطلب معرفة الفائدة والضريبة (وإلا يبقى None ويُعرض EBIT بدلاً منه).
    """
    ns = to_decimal(net_sales)
    c = to_decimal(cogs)
    opex = to_decimal(operating_expenses)
    dep = to_decimal(depreciation) or Decimal(0)
    amort = to_decimal(amortization) or Decimal(0)
    intr = to_decimal(interest)
    tx = to_decimal(tax)

    # ① Gross Profit
    gross_profit = (ns - c) if (ns is not None and c is not None) else None
    gross_margin = None
    if gross_profit is not None and ns and ns != 0:
        gm = safe_divide(gross_profit * 100, ns)
        gross_margin = round_pct(gm) if gm is not None else None

    # ② Operating Profit (EBIT) = Gross Profit − OpEx
    operating_profit = None
    if gross_profit is not None and opex is not None:
        operating_profit = gross_profit - opex
    elif ns is not None and opex is not None and c is None:
        # لا COGS منفصل → الربح التشغيلي = المبيعات − المصروفات (تقدير)
        operating_profit = ns - opex
    operating_margin = None
    if operating_profit is not None and ns and ns != 0:
        om = safe_divide(operating_profit * 100, ns)
        operating_margin = round_pct(om) if om is not None else None

    # ③ EBITDA = EBIT + Depreciation + Amortization (فقط إن توفّر الإهلاك)
    ebitda = None
    has_dep_data = (to_decimal(depreciation) is not None) or (to_decimal(amortization) is not None)
    if operating_profit is not None and has_dep_data:
        ebitda = operating_profit + dep + amort

    # ④ Profit Before Tax = EBIT − Interest
    # نميّز: interest=0 صريح (معروف) عن None (غير معروف)
    interest_known = to_decimal(interest) is not None
    tax_known = to_decimal(tax) is not None
    intr_val = to_decimal(interest) or Decimal(0)
    tx_val = to_decimal(tax) or Decimal(0)

    pbt = None
    if operating_profit is not None and interest_known:
        pbt = operating_profit - intr_val

    # ⑤ Net Profit = PBT − Tax (فقط إن عُرفت الفائدة والضريبة معاً)
    net_profit = None
    if pbt is not None and tax_known:
        net_profit = pbt - tx_val
    net_margin = None
    if net_profit is not None and ns and ns != 0:
        nm = safe_divide(net_profit * 100, ns)
        net_margin = round_pct(nm) if nm is not None else None

    mk = lambda name, val, unit="": metric_result(name, val, currency=currency,
                                                   period=period, source=source,
                                                   quality=quality, unit=unit)
    return {
        "cogs": mk("تكلفة البضاعة المباعة", round_money(c)),
        "gross_profit": mk("الربح الإجمالي", round_money(gross_profit)),
        "gross_margin": mk("هامش الربح الإجمالي", gross_margin, unit="%"),
        "operating_expenses": mk("المصروفات التشغيلية", round_money(opex)),
        "operating_profit": mk("الربح التشغيلي (EBIT)", round_money(operating_profit)),
        "operating_margin": mk("هامش الربح التشغيلي", operating_margin, unit="%"),
        "ebitda": mk("EBITDA", round_money(ebitda)),
        "profit_before_tax": mk("الربح قبل الضريبة", round_money(pbt)),
        "net_profit": mk("صافي الربح", round_money(net_profit)),
        "net_margin": mk("هامش صافي الربح", net_margin, unit="%"),
    }


# ═══════════════════════════════════════════════════════════
#  محرك المصروفات (Expenses breakdown)
# ═══════════════════════════════════════════════════════════
EXPENSE_CATEGORIES = ["payroll", "rent", "marketing", "delivery",
                      "software", "maintenance", "g_and_a", "other"]
EXPENSE_LABELS = {
    "payroll": "الرواتب", "rent": "الإيجار", "marketing": "التسويق",
    "delivery": "التوصيل", "software": "البرمجيات", "maintenance": "الصيانة",
    "g_and_a": "مصاريف عمومية وإدارية", "other": "أخرى",
}


def compute_expenses(breakdown: dict, *, currency="SAR", period=None,
                     source="entry", quality="unknown"):
    """يحسب تفصيل المصروفات + الإجمالي."""
    result = {}
    total = Decimal(0)
    any_data = False
    for cat in EXPENSE_CATEGORIES:
        val = to_decimal(breakdown.get(cat))
        if val is not None:
            total += val
            any_data = True
        result[cat] = metric_result(EXPENSE_LABELS[cat], round_money(val),
                                     currency=currency, period=period,
                                     source=source, quality=quality)
    result["total_expenses"] = metric_result(
        "إجمالي المصروفات", round_money(total) if any_data else None,
        currency=currency, period=period, source=source, quality=quality)
    return result


# ═══════════════════════════════════════════════════════════
#  محرك الانحراف (Variance — Budget vs Actual)
# ═══════════════════════════════════════════════════════════
def compute_variance(budget, actual, *, metric_type="revenue",
                     currency="SAR", period=None, quality="unknown"):
    """يحسب الانحراف مع تفسير الإشارة الصحيح حسب نوع المؤشر.
    revenue/profit: actual > budget = إيجابي (جيد)
    expense/cost: actual < budget = إيجابي (جيد)"""
    b = to_decimal(budget)
    a = to_decimal(actual)
    if b is None or a is None:
        return {
            "budget": metric_result("الموازنة", round_money(b), currency=currency, period=period, quality=quality),
            "actual": metric_result("الفعلي", round_money(a), currency=currency, period=period, quality=quality),
            "variance_amount": metric_result("مبلغ الانحراف", None, currency=currency, period=period, quality=quality),
            "variance_pct": metric_result("نسبة الانحراف", None, unit="%", currency=currency, period=period, quality=quality),
            "interpretation": "بيانات ناقصة",
        }
    variance = a - b
    variance_pct = safe_divide(variance * 100, b)
    variance_pct = round_pct(variance_pct) if variance_pct is not None else None

    # التفسير الصحيح حسب نوع المؤشر
    is_expense = metric_type in ("expense", "cost", "cogs")
    if is_expense:
        favorable = variance <= 0  # صرف أقل = جيد
    else:
        favorable = variance >= 0  # إيراد أكثر = جيد
    interpretation = "مواتٍ (favorable)" if favorable else "غير مواتٍ (unfavorable)"

    return {
        "budget": metric_result("الموازنة", round_money(b), currency=currency, period=period, quality=quality),
        "actual": metric_result("الفعلي", round_money(a), currency=currency, period=period, quality=quality),
        "variance_amount": metric_result("مبلغ الانحراف", round_money(variance), currency=currency, period=period, quality=quality),
        "variance_pct": metric_result("نسبة الانحراف", variance_pct, unit="%", currency=currency, period=period, quality=quality),
        "favorable": favorable,
        "interpretation": interpretation,
    }
