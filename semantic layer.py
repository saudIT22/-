"""
NABBAH 2.2 — Business Semantic Layer
تعريف مرجعي واحد (canonical) لكل مؤشر أعمال.

المبدأ: مؤشر واحد = تعريف واحد = صيغة واحدة، مهما كانت الوحدة الطالبة.
يبني على Phase 2.1 (nabbah_finance) — لا يكرّر الصيغ.
"""
from decimal import Decimal
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "phase21"))

SEMANTIC_VERSION = "1.0"

# ═══════════════════════════════════════════════════════════
#  السجل المرجعي للمؤشرات (Canonical Metric Registry)
# ═══════════════════════════════════════════════════════════
METRICS = {
    # ── المبيعات ──
    "gross_sales": {
        "metric_id": "gross_sales", "name_ar": "إجمالي المبيعات", "name_en": "Gross Sales",
        "definition": "قيمة المبيعات قبل الخصومات والمرتجعات والضريبة.",
        "formula": "raw input",
        "unit": "currency", "category": "sales", "higher_is_better": True,
    },
    "discounts": {
        "metric_id": "discounts", "name_ar": "الخصومات", "name_en": "Discounts",
        "definition": "إجمالي الخصومات الممنوحة خلال الفترة.",
        "formula": "raw input",
        "unit": "currency", "category": "sales", "higher_is_better": False,
    },
    "returns": {
        "metric_id": "returns", "name_ar": "المرتجعات", "name_en": "Returns",
        "definition": "قيمة البضاعة المرتجعة خلال الفترة.",
        "formula": "raw input",
        "unit": "currency", "category": "sales", "higher_is_better": False,
    },
    "net_sales": {
        "metric_id": "net_sales", "name_ar": "صافي المبيعات", "name_en": "Net Sales",
        "definition": "المبيعات بعد خصم الخصومات والمرتجعات (بدون ضريبة).",
        "formula": "gross_sales − discounts − returns",
        "unit": "currency", "category": "sales", "higher_is_better": True,
    },
    "vat": {
        "metric_id": "vat", "name_ar": "ضريبة القيمة المضافة", "name_en": "VAT",
        "definition": "الضريبة المحصّلة — منفصلة عن صافي المبيعات.",
        "formula": "raw input",
        "unit": "currency", "category": "sales", "higher_is_better": None,
    },
    "transactions": {
        "metric_id": "transactions", "name_ar": "عدد المعاملات", "name_en": "Transactions",
        "definition": "عدد الفواتير/الطلبات خلال الفترة.",
        "formula": "raw input",
        "unit": "count", "category": "sales", "higher_is_better": True,
    },
    "aov": {
        "metric_id": "aov", "name_ar": "متوسط قيمة الطلب", "name_en": "Average Order Value",
        "definition": "متوسط قيمة الفاتورة الواحدة.",
        "formula": "net_sales ÷ transactions",
        "unit": "currency", "category": "sales", "higher_is_better": True,
    },
    # ── التكلفة والربح ──
    "cogs": {
        "metric_id": "cogs", "name_ar": "تكلفة البضاعة المباعة", "name_en": "COGS",
        "definition": "التكلفة المباشرة للبضاعة المباعة.",
        "formula": "raw input",
        "unit": "currency", "category": "cost", "higher_is_better": False,
    },
    "gross_profit": {
        "metric_id": "gross_profit", "name_ar": "الربح الإجمالي", "name_en": "Gross Profit",
        "definition": "الربح بعد خصم تكلفة البضاعة فقط.",
        "formula": "net_sales − cogs",
        "unit": "currency", "category": "profit", "higher_is_better": True,
    },
    "gross_margin": {
        "metric_id": "gross_margin", "name_ar": "هامش الربح الإجمالي", "name_en": "Gross Margin",
        "definition": "نسبة الربح الإجمالي من صافي المبيعات.",
        "formula": "(gross_profit ÷ net_sales) × 100",
        "unit": "percent", "category": "profit", "higher_is_better": True,
    },
    "operating_expenses": {
        "metric_id": "operating_expenses", "name_ar": "المصروفات التشغيلية", "name_en": "Operating Expenses",
        "definition": "مجموع المصروفات التشغيلية (رواتب، إيجار، تسويق...).",
        "formula": "sum(expense categories)",
        "unit": "currency", "category": "cost", "higher_is_better": False,
    },
    "ebit": {
        "metric_id": "ebit", "name_ar": "الربح التشغيلي", "name_en": "EBIT (Operating Profit)",
        "definition": "الربح قبل الفوائد والضرائب.",
        "formula": "gross_profit − operating_expenses",
        "unit": "currency", "category": "profit", "higher_is_better": True,
    },
    "ebitda": {
        "metric_id": "ebitda", "name_ar": "EBITDA", "name_en": "EBITDA",
        "definition": "الربح قبل الفوائد والضرائب والإهلاك والاستهلاك. يتطلب بيانات إهلاك.",
        "formula": "ebit + depreciation + amortization",
        "unit": "currency", "category": "profit", "higher_is_better": True,
    },
    "pbt": {
        "metric_id": "pbt", "name_ar": "الربح قبل الضريبة", "name_en": "Profit Before Tax",
        "definition": "الربح بعد الفوائد وقبل الضريبة. يتطلب بيانات فوائد.",
        "formula": "ebit − interest",
        "unit": "currency", "category": "profit", "higher_is_better": True,
    },
    "net_profit": {
        "metric_id": "net_profit", "name_ar": "صافي الربح", "name_en": "Net Profit",
        "definition": "الربح النهائي بعد كل البنود. يتطلب بيانات فوائد وضريبة — لا يُعرض بدونها.",
        "formula": "pbt − tax",
        "unit": "currency", "category": "profit", "higher_is_better": True,
    },
    "net_margin": {
        "metric_id": "net_margin", "name_ar": "هامش صافي الربح", "name_en": "Net Margin",
        "definition": "نسبة صافي الربح من صافي المبيعات.",
        "formula": "(net_profit ÷ net_sales) × 100",
        "unit": "percent", "category": "profit", "higher_is_better": True,
    },
    # ── النِسب التشغيلية ──
    "expense_ratio": {
        "metric_id": "expense_ratio", "name_ar": "نسبة المصروفات", "name_en": "Expense Ratio",
        "definition": "نسبة المصروفات التشغيلية من صافي المبيعات.",
        "formula": "(operating_expenses ÷ net_sales) × 100",
        "unit": "percent", "category": "ratio", "higher_is_better": False,
    },
    "cogs_pct": {
        "metric_id": "cogs_pct", "name_ar": "نسبة تكلفة البضاعة", "name_en": "COGS %",
        "definition": "نسبة تكلفة البضاعة من صافي المبيعات.",
        "formula": "(cogs ÷ net_sales) × 100",
        "unit": "percent", "category": "ratio", "higher_is_better": False,
    },
    "payroll_pct": {
        "metric_id": "payroll_pct", "name_ar": "نسبة الرواتب", "name_en": "Payroll %",
        "definition": "نسبة الرواتب من صافي المبيعات.",
        "formula": "(payroll ÷ net_sales) × 100",
        "unit": "percent", "category": "ratio", "higher_is_better": False,
        "sensitive": True,
    },
    "rent_pct": {
        "metric_id": "rent_pct", "name_ar": "نسبة الإيجار", "name_en": "Rent %",
        "definition": "نسبة الإيجار من صافي المبيعات.",
        "formula": "(rent ÷ net_sales) × 100",
        "unit": "percent", "category": "ratio", "higher_is_better": False,
    },
    "marketing_pct": {
        "metric_id": "marketing_pct", "name_ar": "نسبة التسويق", "name_en": "Marketing %",
        "definition": "نسبة التسويق من صافي المبيعات.",
        "formula": "(marketing ÷ net_sales) × 100",
        "unit": "percent", "category": "ratio", "higher_is_better": None,
    },
    "delivery_pct": {
        "metric_id": "delivery_pct", "name_ar": "نسبة التوصيل", "name_en": "Delivery %",
        "definition": "نسبة تكلفة التوصيل من صافي المبيعات.",
        "formula": "(delivery ÷ net_sales) × 100",
        "unit": "percent", "category": "ratio", "higher_is_better": False,
    },
    "return_pct": {
        "metric_id": "return_pct", "name_ar": "نسبة المرتجعات", "name_en": "Return %",
        "definition": "نسبة المرتجعات من إجمالي المبيعات.",
        "formula": "(returns ÷ gross_sales) × 100",
        "unit": "percent", "category": "ratio", "higher_is_better": False,
    },
    "discount_pct": {
        "metric_id": "discount_pct", "name_ar": "نسبة الخصومات", "name_en": "Discount %",
        "definition": "نسبة الخصومات من إجمالي المبيعات.",
        "formula": "(discounts ÷ gross_sales) × 100",
        "unit": "percent", "category": "ratio", "higher_is_better": False,
    },
    "growth": {
        "metric_id": "growth", "name_ar": "معدل النمو", "name_en": "Growth %",
        "definition": "نسبة تغيّر صافي المبيعات مقارنة بالفترة السابقة.",
        "formula": "((current − previous) ÷ previous) × 100",
        "unit": "percent", "category": "growth", "higher_is_better": True,
    },
}

# الأبعاد المدعومة
DIMENSIONS = ["company", "branch", "department", "product", "channel", "period"]


def get_metric_definition(metric_id):
    """يُرجع التعريف المرجعي لمؤشر. مصدر واحد للحقيقة.
    يُرجع None لمؤشر غير معرّف (لا يُخترع تعريف)."""
    return METRICS.get(metric_id)


def list_metrics(category=None):
    """يُرجع كل المؤشرات، أو مؤشرات فئة معيّنة."""
    if category:
        return {k: v for k, v in METRICS.items() if v.get("category") == category}
    return dict(METRICS)


def is_sensitive(metric_id):
    """هل المؤشر حسّاس (يتطلب صلاحية)؟"""
    m = METRICS.get(metric_id, {})
    return bool(m.get("sensitive"))


def _rounding_mode():
    try:
        from nabbah_finance import DEFAULT_ROUNDING_MODE
        return DEFAULT_ROUNDING_MODE
    except Exception:
        return "UNKNOWN"


def metric_envelope(metric_id, value, *, currency="SAR", period=None,
                    source="calculated", data_quality="unknown",
                    value_decimal=None, lang="ar"):
    """الغلاف المرجعي الكامل لأي نتيجة مؤشر.
    كل نتيجة تحمل: metric_id, name, definition, formula, value, currency,
    period, source, calculation_version, data_quality, timestamp."""
    from datetime import datetime, timezone
    d = METRICS.get(metric_id, {})
    return {
        "metric_id": metric_id,
        "name": d.get("name_ar" if lang == "ar" else "name_en", metric_id),
        "definition": d.get("definition", ""),
        "formula": d.get("formula", ""),
        "value": value,
        "value_decimal": value_decimal if value_decimal is not None else (str(value) if value is not None else None),
        "currency": currency if d.get("unit") == "currency" else None,
        "unit": d.get("unit", ""),
        "period": period,
        "source": source,
        "calculation_version": f"semantic-v{SEMANTIC_VERSION}",
        "rounding_mode": _rounding_mode(),
        "data_quality": data_quality,
        "has_data": value is not None,
        "higher_is_better": d.get("higher_is_better"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
