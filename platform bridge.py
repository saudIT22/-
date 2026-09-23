"""
NABBAH 2.2 — Platform Integration Bridge
نقطة التكامل الفعلية بين محرّكات 2.2 وتطبيق نبّاه القائم.

المبدأ: main.py يستورد من هنا فقط — واجهة واحدة نظيفة.
البيانات تصل مُفلترة مسبقاً (company_id) — الجسر لا يستعلم من القاعدة
ولا يفتح أي مسار تجاوز للعزل.
"""
import sys, os
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "phase21"))
sys.path.insert(0, _HERE)

from nabbah_finance import to_decimal
from nabbah_trust import (check_completeness, check_validity, check_consistency,
                          check_freshness, check_duplicates, run_all_checks)
from kpi_engine import compute_kpis, kpi_value
from semantic_layer import get_metric_definition, is_sensitive
from analysis_engines import (analyze_by_dimension, compute_variance_analysis,
                              analyze_drivers, analyze_root_cause, compute_financial_impact)
from ai_gateway import (evaluate_quality_gate, build_ai_context,
                        request_ai_analysis, GeminiProvider, MockProvider)

from period_aggregation import split_by_period, parse_period  # noqa: E402

BRIDGE_VERSION = "1.0"


# ═══════════════════════════════════════════════════════════
#  محوّل: من نماذج نبّاه إلى مدخلات المحرّك
# ═══════════════════════════════════════════════════════════
def entry_to_raw(entry, module_data=None):
    """يحوّل CompanyEntry (+ بيانات الوحدة المالية) إلى مدخلات KPI Engine.

    entry: كائن CompanyEntry (أو dict)
    module_data: dict من CompanyModuleEntry.data (وحدة finance) — اختياري
    """
    def g(obj, key, default=None):
        if obj is None:
            return default
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    raw = {
        "gross_sales": g(entry, "sales"),
        "transactions": g(entry, "invoices"),
        "discounts": g(entry, "discounts"),
        "operating_expenses": g(entry, "expenses"),
    }
    # بيانات الوحدة المالية الموسّعة (إن وُجدت)
    if module_data:
        def pick(*keywords):
            for k, v in module_data.items():
                if any(w in str(k) or w in str(k).lower() for w in keywords):
                    n = to_decimal(v)
                    if n is not None:
                        return n
            return None
        raw["cogs"] = pick("تكلفة البضاعة", "cogs")
        raw["payroll"] = pick("رواتب", "payroll", "أجور")
        raw["rent"] = pick("إيجار", "rent")
        raw["marketing"] = pick("تسويق", "marketing")
        raw["delivery"] = pick("توصيل", "delivery", "شحن")
        raw["returns"] = pick("مرتجعات", "returns")
        raw["vat"] = pick("ضريبة القيمة", "القيمة المضافة", "vat")
        raw["depreciation"] = pick("إهلاك", "depreciation")
        raw["interest"] = pick("فوائد", "interest")
        raw["tax"] = pick("الضريبة المستحقة", "tax")
    return raw


# ═══════════════════════════════════════════════════════════
#  الواجهة الرئيسية: تحليل مالي كامل لشركة
# ═══════════════════════════════════════════════════════════
def analyze_company_financials(entries, *, module_data=None, previous_entries=None,
                               currency="SAR", period=None, user_role="owner",
                               company_name=None, comparison_period=None, period_gaps=None):
    """التحليل المالي المركزي لشركة — نقطة الدخول من main.py.

    entries: قائمة CompanyEntry (مُفلترة بـcompany_id مسبقاً في main.py)
    يُرجع: kpis + trust + variance + drivers + root_cause + impact + gate

    الأمان: يستقبل بيانات مُفلترة — لا يستعلم، لا يفتح تجاوزاً.
    الصلاحيات: يحجب المؤشرات الحسّاسة حسب الدور.
    """
    # ① نجمّع البيانات الخام (المجموع على مستوى الشركة)
    # Missing stays missing: a key is only set once a real value is seen (no silent zero).
    totals = {"gross_sales": None, "transactions": None, "operating_expenses": None, "discounts": None}
    records = []
    for e in entries or []:
        r = entry_to_raw(e, module_data)
        records.append(r)
        for k in totals:
            v = to_decimal(r.get(k))
            if v is not None:
                totals[k] = v if totals[k] is None else totals[k] + v
    # ندمج بيانات الوحدة المالية (على مستوى الشركة)
    if module_data:
        md_raw = entry_to_raw(None, module_data)
        for k, v in md_raw.items():
            if v is not None and k not in totals:
                totals[k] = v

    # الفترة السابقة (للنمو والمحرّكات)
    prev_totals = None
    if previous_entries:
        prev_totals = {"gross_sales": None, "transactions": None, "operating_expenses": None}
        for e in previous_entries:
            r = entry_to_raw(e)
            for k in prev_totals:
                v = to_decimal(r.get(k))
                if v is not None:
                    prev_totals[k] = v if prev_totals[k] is None else prev_totals[k] + v
        if all(v is None for v in prev_totals.values()):
            prev_totals = None  # comparison period has no usable data

    # ② فحوص الموثوقية (Phase 2.1)
    trust_checks = [
        check_completeness(records, ["gross_sales", "operating_expenses"]),
        check_validity(records, ["gross_sales", "transactions"],
                       numeric_fields=["gross_sales", "transactions", "operating_expenses"]),
        check_duplicates(records, ["gross_sales", "transactions"]),
    ]
    trust = run_all_checks(trust_checks)
    quality_label = trust["status"]

    # ③ المؤشرات (KPI Engine المركزي)
    kpi_input = dict(totals)
    if prev_totals:
        # نحسب صافي المبيعات السابق عبر المحرك (لا صيغة مكرّرة)
        prev_kpis = compute_kpis(prev_totals, currency=currency, data_quality=quality_label)
        prev_ns = kpi_value(prev_kpis, "net_sales")
        if prev_ns is not None:
            kpi_input["previous_net_sales"] = prev_ns
    kpis = compute_kpis(kpi_input, currency=currency, period=period,
                        source="company_entries", data_quality=quality_label)

    # ④ حجب المؤشرات الحسّاسة حسب الدور (يحترم RBAC القائم)
    ROLES_SEE_SENSITIVE = {"owner", "accountant"}
    if user_role not in ROLES_SEE_SENSITIVE:
        for mid in list(kpis.keys()):
            if is_sensitive(mid):
                kpis[mid]["value"] = None
                kpis[mid]["value_decimal"] = None
                kpis[mid]["has_data"] = False
                kpis[mid]["restricted"] = True

    # ⑤ المحرّكات + السبب الجذري + الأثر (عند توفّر فترة سابقة)
    drivers = root_cause = impact = None
    if prev_totals:
        drivers = analyze_drivers(kpi_input, prev_totals, "net_sales",
                                  currency=currency, period=period, data_quality=quality_label)
        root_cause = analyze_root_cause(drivers, data_quality=quality_label)
        impact = compute_financial_impact(drivers, currency=currency, period=period,
                                          data_quality=quality_label)

    # ⑥ بوابة الجودة
    gate = evaluate_quality_gate(trust)

    return {
        "kpis": kpis, "trust": trust, "gate": gate,
        "drivers": drivers, "root_cause": root_cause, "financial_impact": impact,
        "currency": currency, "period": period,
        "comparison_period": comparison_period if prev_totals else None,
        "has_comparison": bool(prev_totals),
        "data_gaps": list(period_gaps or []),
        "company": company_name,
        "bridge_version": f"bridge-v{BRIDGE_VERSION}",
    }


def analyze_branches(branch_records, metric_id="net_sales", *, currency="SAR", period=None):
    """تحليل الفروع عبر Dimension Layer.
    branch_records: [{branch: name, sales:..., invoices:...}] — مُفلترة مسبقاً."""
    normalized = []
    for r in branch_records or []:
        raw = entry_to_raw(r) if not isinstance(r, dict) or "gross_sales" not in r else dict(r)
        raw["branch"] = r.get("branch") if isinstance(r, dict) else getattr(r, "name", "فرع")
        normalized.append(raw)
    return analyze_by_dimension(normalized, "branch", metric_id,
                                currency=currency, period=period)


def ai_analyze(gemini_fn, analysis_result, question="", *, lang="ar", company=None):
    """تحليل AI عبر البوابة — يستهلك نتائج المحرّكات المُتحقّقة.

    gemini_fn: دالة company_gemini من main.py (حقن التبعية — لا استيراد دائري)
    """
    provider = GeminiProvider(gemini_fn)
    ctx = build_ai_context(
        kpis=analysis_result.get("kpis"),
        drivers=analysis_result.get("drivers"),
        root_cause=analysis_result.get("root_cause"),
        impact=analysis_result.get("financial_impact"),
        trust_report=analysis_result.get("trust"),
        company_name=analysis_result.get("company"),
        period=analysis_result.get("period"),
    )
    return request_ai_analysis(provider, ctx, question,
                               trust_report=analysis_result.get("trust"),
                               lang=lang, company=company)
