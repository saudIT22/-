"""
NABBAH 2.2 — Analysis Engines
STEP 3: Dimension Analysis · STEP 4: Variance · STEP 5: Driver
STEP 6: Root Cause · STEP 7: Financial Impact

كلها تستهلك KPI Engine — لا تكرّر أي صيغة.
"""
import sys, os
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "phase21"))
sys.path.insert(0, _HERE)

from decimal import Decimal
from datetime import datetime, timezone
from nabbah_finance import to_decimal, safe_divide, round_money, round_pct
from kpi_engine import compute_kpis, kpi_value
from semantic_layer import get_metric_definition, DIMENSIONS

ANALYSIS_VERSION = "1.0"


def _now():
    return datetime.now(timezone.utc).isoformat()


# ═══════════════════════════════════════════════════════════
#  STEP 3 — Dimension Analysis Layer
# ═══════════════════════════════════════════════════════════
def analyze_by_dimension(records: list, dimension: str, metric_id: str = "net_sales",
                         *, currency="SAR", period=None, data_quality="unknown"):
    """يحلّل مؤشراً عبر بُعد (فرع/قسم/منتج/قناة).

    records: [{dimension_value: str, **raw_kpi_inputs}]
    يُرجع: ترتيب + مساهمة كل عنصر (إن أمكن حسابها).
    """
    if dimension not in DIMENSIONS:
        return {"error": f"بُعد غير مدعوم: {dimension}", "supported": DIMENSIONS}
    if not records:
        return {"dimension": dimension, "metric_id": metric_id, "items": [],
                "has_data": False, "note": "لا بيانات لهذا البُعد.", "timestamp": _now()}

    items = []
    total = Decimal(0)
    for r in records:
        label = r.get(dimension) or r.get("label") or "غير محدد"
        kpis = compute_kpis(r, currency=currency, period=period, data_quality=data_quality)
        val = kpi_value(kpis, metric_id)
        if val is not None:
            total += val
        items.append({"label": label, "value_decimal": str(val) if val is not None else None,
                      "value": float(val) if val is not None else None, "has_data": val is not None})

    # المساهمة (فقط إن كان الإجمالي صالحاً)
    for it in items:
        if it["has_data"] and total != 0:
            share = safe_divide(to_decimal(it["value_decimal"]) * 100, total)
            it["contribution_pct"] = float(round_pct(share)) if share is not None else None
        else:
            it["contribution_pct"] = None  # غير قابلة للحساب — لا تُخترع

    items.sort(key=lambda x: (x["value"] is None, -(x["value"] or 0)))
    return {
        "dimension": dimension, "metric_id": metric_id,
        "total_decimal": str(total), "total": float(total),
        "items": items, "has_data": any(i["has_data"] for i in items),
        "currency": currency, "period": period,
        "calculation_version": f"analysis-v{ANALYSIS_VERSION}", "timestamp": _now(),
    }


# ═══════════════════════════════════════════════════════════
#  STEP 4 — Variance Engine
# ═══════════════════════════════════════════════════════════
def compute_variance_analysis(current, comparison, metric_id, *,
                              comparison_type="previous_period",
                              currency="SAR", period=None, data_quality="unknown"):
    """يحسب الانحراف بين قيمتين مع تفسير الإشارة حسب تعريف المؤشر.

    comparison_type: target | budget | previous_period | same_period_last_year
    """
    cur = to_decimal(current)
    cmp_val = to_decimal(comparison)
    d = get_metric_definition(metric_id) or {}
    higher_better = d.get("higher_is_better")

    base = {
        "metric_id": metric_id, "metric_name": d.get("name_ar", metric_id),
        "comparison_type": comparison_type,
        "current_value": float(cur) if cur is not None else None,
        "current_decimal": str(cur) if cur is not None else None,
        "comparison_value": float(cmp_val) if cmp_val is not None else None,
        "comparison_decimal": str(cmp_val) if cmp_val is not None else None,
        "currency": currency if d.get("unit") == "currency" else None,
        "period": period, "data_quality": data_quality,
        "calculation_version": f"variance-v{ANALYSIS_VERSION}", "timestamp": _now(),
    }
    if cur is None or cmp_val is None:
        base.update({"absolute_variance": None, "percentage_variance": None,
                     "direction": "unknown", "favorable": None,
                     "note": "بيانات ناقصة — لا يمكن حساب الانحراف."})
        return base

    abs_var = cur - cmp_val
    pct_var = None
    if cmp_val != 0:
        p = safe_divide(abs_var * 100, cmp_val)
        pct_var = round_pct(p) if p is not None else None
    else:
        base["note"] = "الأساس صفر — نسبة الانحراف غير قابلة للحساب."

    direction = "up" if abs_var > 0 else ("down" if abs_var < 0 else "flat")
    favorable = None
    if higher_better is True:
        favorable = abs_var >= 0
    elif higher_better is False:
        favorable = abs_var <= 0

    base.update({
        "absolute_variance": float(round_money(abs_var)),
        "absolute_decimal": str(round_money(abs_var)),
        "percentage_variance": float(pct_var) if pct_var is not None else None,
        "direction": direction, "favorable": favorable,
    })
    return base


# ═══════════════════════════════════════════════════════════
#  STEP 5 — Driver Analysis Engine
# ═══════════════════════════════════════════════════════════
def analyze_drivers(current_raw: dict, previous_raw: dict, metric_id="net_sales",
                    *, currency="SAR", period=None, data_quality="unknown"):
    """يفكّك تغيّر مؤشر إلى محرّكاته القابلة للقياس.

    قاعدة صارمة: لا ادّعاء سببية. نميّز:
      observed_fact · measured_driver · calculated_contribution · unmeasured
    عند تعذّر حساب المساهمة → contribution = "unavailable" (لا اختراع نسبة).
    """
    cur_k = compute_kpis(current_raw, currency=currency, period=period, data_quality=data_quality)
    prev_k = compute_kpis(previous_raw, currency=currency, period=period, data_quality=data_quality)

    target_cur = kpi_value(cur_k, metric_id)
    target_prev = kpi_value(prev_k, metric_id)

    # الحقيقة المرصودة
    observed = {
        "type": "observed_fact", "metric_id": metric_id,
        "current": float(target_cur) if target_cur is not None else None,
        "previous": float(target_prev) if target_prev is not None else None,
        "change": None, "change_pct": None,
    }
    if target_cur is not None and target_prev is not None:
        change = target_cur - target_prev
        observed["change"] = float(round_money(change))
        if target_prev != 0:
            cp = safe_divide(change * 100, target_prev)
            observed["change_pct"] = float(round_pct(cp)) if cp is not None else None

    # المحرّكات المرشّحة (حسب المؤشر الهدف)
    driver_map = {
        "net_sales": ["transactions", "aov", "discounts", "returns"],
        "gross_profit": ["net_sales", "cogs"],
        "net_profit": ["gross_profit", "operating_expenses"],
        "gross_margin": ["net_sales", "cogs"],
    }
    candidates = driver_map.get(metric_id, ["transactions", "aov"])

    drivers = []
    for did in candidates:
        cv = kpi_value(cur_k, did)
        pv = kpi_value(prev_k, did)
        dd = get_metric_definition(did) or {}
        entry = {
            "driver_id": did, "metric": metric_id,
            "driver_name": dd.get("name_ar", did),
            "current_value": float(cv) if cv is not None else None,
            "previous_value": float(pv) if pv is not None else None,
            "absolute_change": None, "percentage_change": None,
            "direction": "unknown",
            "contribution": "unavailable",   # الافتراضي: لا اختراع
            "evidence": "kpi_engine",
            "period": period, "data_quality": data_quality,
            "confidence": "low",
            "explanation": "",
            "type": "unmeasured",
        }
        if cv is None or pv is None:
            entry["explanation"] = f"بيانات {dd.get('name_ar', did)} ناقصة — لا يمكن قياس أثره."
            drivers.append(entry)
            continue

        change = cv - pv
        entry["absolute_change"] = float(round_money(change))
        entry["direction"] = "up" if change > 0 else ("down" if change < 0 else "flat")
        if pv != 0:
            pc = safe_divide(change * 100, pv)
            entry["percentage_change"] = float(round_pct(pc)) if pc is not None else None
        entry["type"] = "measured_driver"
        entry["confidence"] = "medium"

        # المساهمة: تُحسب فقط للحالات الرياضية المدعومة
        # net_sales = transactions × aov → مساهمة كل منهما قابلة للتفكيك
        if metric_id == "net_sales" and did in ("transactions", "aov"):
            tx_c, tx_p = kpi_value(cur_k, "transactions"), kpi_value(prev_k, "transactions")
            aov_c, aov_p = kpi_value(cur_k, "aov"), kpi_value(prev_k, "aov")
            if all(x is not None for x in (tx_c, tx_p, aov_c, aov_p)):
                if did == "transactions":
                    contrib = (tx_c - tx_p) * aov_p  # أثر الكمية بسعر الأساس
                else:
                    contrib = (aov_c - aov_p) * tx_c  # أثر السعر بالكمية الحالية
                entry["contribution"] = float(round_money(contrib))
                entry["type"] = "calculated_contribution"
                entry["confidence"] = "high"
                entry["explanation"] = f"مساهمة محسوبة رياضياً من تفكيك {metric_id}."
            else:
                entry["explanation"] = "المساهمة غير قابلة للحساب — بيانات ناقصة."
        else:
            entry["explanation"] = "التغيّر مقاس، لكن المساهمة الدقيقة غير قابلة للتفكيك بالبيانات المتاحة."

        drivers.append(entry)

    return {
        "observed_fact": observed, "drivers": drivers,
        "causation_disclaimer": "هذه محرّكات مقاسة ومرتبطة — ليست إثباتاً للسببية.",
        "calculation_version": f"driver-v{ANALYSIS_VERSION}", "timestamp": _now(),
    }


# ═══════════════════════════════════════════════════════════
#  STEP 6 — Root Cause Engine
# ═══════════════════════════════════════════════════════════
def analyze_root_cause(driver_result: dict, *, data_quality="unknown", min_confidence="medium"):
    """يبني تحليل السبب الجذري مع فصل صارم:
    FACT · INTERPRETATION · HYPOTHESIS · RECOMMENDATION

    قاعدة حرجة: لا يحوّل فرضية غير مدعومة إلى سبب مؤكّد.
    عند نقص الأدلة → "insufficient evidence" + تحديد البيانات المطلوبة.
    """
    observed = driver_result.get("observed_fact", {})
    drivers = driver_result.get("drivers", [])

    facts = []
    interpretations = []
    hypotheses = []
    recommendations = []
    data_gaps = []

    # FACT: التغيّر المرصود
    if observed.get("change") is not None:
        d = get_metric_definition(observed.get("metric_id")) or {}
        facts.append({
            "statement": f"{d.get('name_ar', observed.get('metric_id'))} تغيّر بمقدار {observed['change']}"
                         + (f" ({observed['change_pct']}%)" if observed.get("change_pct") is not None else ""),
            "evidence": "kpi_engine", "confidence": "high",
        })
    else:
        data_gaps.append({
            "missing": observed.get("metric_id"),
            "why_it_matters": "بدون قيمة المؤشر لا يمكن تحديد التغيّر.",
            "required_data": "قيم المؤشر للفترتين الحالية والسابقة.",
        })

    # INTERPRETATION: المحرّكات المقاسة
    measured = [d for d in drivers if d["type"] in ("measured_driver", "calculated_contribution")]
    for m in measured:
        if m.get("percentage_change") is not None:
            interpretations.append({
                "statement": f"{m['driver_name']} تغيّر {m['percentage_change']}%",
                "evidence": m["evidence"], "confidence": m["confidence"],
                "contribution": m["contribution"],
            })

    # HYPOTHESIS: المحرّكات غير المقاسة (فرضيات صريحة، لا أسباب)
    unmeasured = [d for d in drivers if d["type"] == "unmeasured"]
    for u in unmeasured:
        hypotheses.append({
            "statement": f"قد يكون {u['driver_name']} عاملاً — لكن البيانات غير كافية للتأكيد.",
            "status": "unverified", "confidence": "low",
            "required_data": f"بيانات {u['driver_name']} للفترتين.",
        })
        data_gaps.append({
            "missing": u["driver_id"],
            "why_it_matters": "يمنع قياس مساهمته في التغيّر.",
            "required_data": f"قيم {u['driver_name']} للفترة الحالية والسابقة.",
        })

    # هل الأدلة كافية؟
    calculated = [d for d in drivers if d["type"] == "calculated_contribution"]
    sufficient = len(calculated) > 0 and data_quality not in ("fail", "low")

    if sufficient:
        top = max(calculated, key=lambda x: abs(x.get("contribution") or 0))
        root_cause_candidate = {
            "candidate": top["driver_name"],
            "contribution": top["contribution"],
            "confidence": top["confidence"],
            "status": "supported_by_calculation",
            "evidence": "تفكيك رياضي مباشر",
        }
        recommendations.append({
            "statement": f"ركّز على {top['driver_name']} — مساهمته الأكبر رياضياً.",
            "basis": "calculated_contribution", "certainty": "قابل للتنفيذ",
        })
    else:
        root_cause_candidate = {
            "candidate": None, "status": "insufficient_evidence",
            "reason": "لا توجد مساهمة محسوبة بدقة، أو جودة البيانات غير كافية.",
            "required_data": [g["required_data"] for g in data_gaps[:3]],
        }
        recommendations.append({
            "statement": "أكمل البيانات الناقصة قبل اتخاذ قرار مبني على السبب.",
            "basis": "data_gap", "certainty": "غير مؤكّد",
        })

    return {
        "FACT": facts,
        "INTERPRETATION": interpretations,
        "HYPOTHESIS": hypotheses,
        "RECOMMENDATION": recommendations,
        "root_cause": root_cause_candidate,
        "data_gaps": data_gaps,
        "data_quality": data_quality,
        "evidence_sufficient": sufficient,
        "calculation_version": f"rootcause-v{ANALYSIS_VERSION}",
        "timestamp": _now(),
    }


# ═══════════════════════════════════════════════════════════
#  STEP 7 — Financial Impact Engine
# ═══════════════════════════════════════════════════════════
def compute_financial_impact(driver_result: dict, *, currency="SAR", period=None,
                             data_quality="unknown"):
    """يترجم المحرّكات المقاسة إلى أثر مالي — فقط حيث تدعمه الرياضيات.

    لا يخترع مدخلات · لا يخلط عملات · يحافظ على الإشارة السالبة.
    الأثر غير القابل للحساب → is_estimate=None + value=None.
    """
    impacts = []
    for d in driver_result.get("drivers", []):
        contrib = d.get("contribution")
        if contrib == "unavailable" or contrib is None:
            impacts.append({
                "impact_type": f"{d['driver_id']}_impact",
                "driver_name": d["driver_name"],
                "value": None, "value_decimal": None,
                "currency": currency, "period": period,
                "formula": "غير قابل للحساب",
                "source": "driver_engine",
                "calculation_version": f"impact-v{ANALYSIS_VERSION}",
                "data_quality": data_quality, "confidence": "none",
                "is_estimate": None,
                "note": "الأثر غير قابل للحساب — بيانات ناقصة.",
                "timestamp": _now(),
            })
            continue
        val = to_decimal(contrib)
        impacts.append({
            "impact_type": f"{d['driver_id']}_impact",
            "driver_name": d["driver_name"],
            "value": float(round_money(val)),
            "value_decimal": str(round_money(val)),
            "currency": currency, "period": period,
            "formula": d.get("explanation", "تفكيك رياضي"),
            "source": "driver_engine",
            "calculation_version": f"impact-v{ANALYSIS_VERSION}",
            "data_quality": data_quality,
            "confidence": d.get("confidence", "medium"),
            "is_estimate": False,  # محسوب لا مقدّر
            "direction": "positive" if val >= 0 else "negative",
            "timestamp": _now(),
        })

    # الأثر الكلي (فقط من المحسوب)
    total = Decimal(0)
    calculable = [i for i in impacts if i["value_decimal"] is not None]
    for i in calculable:
        total += to_decimal(i["value_decimal"])

    return {
        "impacts": impacts,
        "total_impact": float(round_money(total)) if calculable else None,
        "total_decimal": str(round_money(total)) if calculable else None,
        "currency": currency, "period": period,
        "calculable_count": len(calculable),
        "unavailable_count": len(impacts) - len(calculable),
        "calculation_version": f"impact-v{ANALYSIS_VERSION}",
        "timestamp": _now(),
    }
