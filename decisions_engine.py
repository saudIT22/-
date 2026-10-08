"""Phase 3.9 — Follow-up on Decisions Intelligence — مركز متابعة القرارات والتنفيذ.

قرار → اعتماد → تنفيذ → نتيجة → قياس → تعلّم. محرك حتمي واحد لكل القطاعات.
قواعد: المكتمل ≠ الناجح · بلا بيانات KPI لا نحكم بنجاح أو فشل · قبل/بعد مقارنة زمنية وليست إثبات سببية ·
الأثر المالي مصنّف (متوقع/فعلي/فرق) · الفعلي يُقرأ من المصدر الموحد (نفس كتالوج مؤشرات 3.8) · الذكاء الاصطناعي يشرح فقط.
"""
from datetime import date, datetime, timedelta
import re

import risk_engine as RE
import prediction_engine as PE
import goals_engine as G

DEC_VERSION = "1.0"
MODEL = f"decisions-v{DEC_VERSION}"
BEFORE_MONTHS = 3
AFTER_MONTHS_MAX = 3
MIN_AFTER_MONTHS = 1
ESCALATE_LATE_DAYS = 14

# ═══════════════════════════════════════════════════════════
# 3.9.1 — Decision Data Contract + 3.9.6 حالات القرار
# ═══════════════════════════════════════════════════════════
WORKFLOW = ["draft", "pending_approval", "under_review", "approved", "rejected", "in_progress", "completed", "measured", "cancelled"]
WF_AR = {"draft": "مسودة", "pending_approval": "بانتظار الاعتماد", "under_review": "قيد المراجعة", "approved": "معتمد", "rejected": "مرفوض",
         "in_progress": "قيد التنفيذ", "completed": "مكتمل", "measured": "مُقاس", "cancelled": "ملغى"}
STATUS_AR = {**WF_AR, "late": "متأخر", "evaluating": "بانتظار القياس"}
STATUS_ICON = {"draft": "📝", "pending_approval": "⏳", "under_review": "🔎", "approved": "✅", "rejected": "⛔", "in_progress": "🔄", "late": "🔴",
               "completed": "☑️", "evaluating": "🧪", "measured": "📏", "cancelled": "✖"}
OPEN = ("approved", "in_progress")
TERMINAL = ("measured", "cancelled", "rejected")
TRANSITIONS = {"draft": ["pending_approval", "cancelled"], "pending_approval": ["under_review", "approved", "rejected", "cancelled"],
               "under_review": ["approved", "rejected", "cancelled"], "rejected": ["draft"], "approved": ["in_progress", "cancelled"],
               "in_progress": ["completed", "cancelled"], "completed": ["measured", "in_progress"], "measured": [], "cancelled": []}
EVENT_AR = {"created": "أنشأ القرار", "submitted": "أرسل للاعتماد", "under_review": "راجع القرار", "recommended": "أوصى بالاعتماد", "approved": "اعتمد القرار",
            "rejected": "رفض القرار", "assigned": "أسند القرار", "in_progress": "بدأ التنفيذ", "completed": "أكمل التنفيذ", "measured": "قاس النتيجة",
            "cancelled": "ألغى القرار", "kpi_updated": "حدّث مؤشر القرار", "evidence": "أضاف دليلاً", "escalated": "صعّد القرار", "delay_reason": "سجّل سبب التأخير",
            "edited": "عدّل القرار", "draft": "أعاد القرار مسودة", "owner_changed": "غيّر المسؤول", "progress": "حدّث التقدم"}
PRIORITY = {"critical": ("حرجة", 4), "high": ("مرتفعة", 3), "medium": ("متوسطة", 2), "low": ("منخفضة", 1)}
DELAY_REASONS = {"supplier": "بانتظار المورد", "budget": "الميزانية", "approval": "بانتظار اعتماد", "data": "بيانات ناقصة", "resources": "نقص موارد/فريق",
                 "dependency": "قرار آخر لم يُنفذ", "external": "عامل خارجي", "other": "أخرى"}
SOURCES = {"risk": ("3.3", "مركز المخاطر", "company-risk-intelligence.html"), "risk_driver": ("3.4", "مسببات المخاطر", "company-risk-drivers.html"),
           "sector_benchmark": ("3.5", "المقارنة بالقطاع", "company-sector-benchmark.html"), "prediction": ("3.7", "التنبؤ بالأداء", "company-performance-prediction.html"),
           "goal": ("3.8", "الأهداف والنتائج", "company-goals-intelligence.html"), "leakage": ("3.1", "استرداد الأموال", "company-leakage-intelligence.html"),
           "tax": ("3.2", "الضرائب والزكاة", "company-tax-intelligence.html"), "purchases": ("2.7", "المشتريات", "company-procurement-analytics.html"),
           "recommendation": ("2.3", "توصيات نبّاه", "company-command-center.html"), "manual": ("—", "قرار يدوي", None)}
# 3.9.24 — فئات القرار (المؤشر الافتراضي والقسم) + إعداد القطاعات
CATEGORIES = {"pricing": ("التسعير", "gross_margin", "sales"), "discount": ("سياسة الخصم", "discount_rate", "sales"), "supplier": ("الموردون", "cost_increase_pct", "purchasing"),
              "cost": ("خفض التكاليف", "expense_ratio", "finance"), "inventory": ("المخزون", "stockout_rate", "inventory"), "staffing": ("التوظيف والفريق", "employee_turnover", "hr"),
              "collections": ("التحصيل", "dso", "finance"), "marketing": ("التسويق والعروض", "revenue", "marketing"), "operations": ("العمليات", "on_time", "operations"),
              "leakage": ("معالجة التسرب", "leakage_pct", "finance"), "compliance": ("الامتثال", "overdue_filings", "finance"), "branch": ("الفروع", "revenue", "sales"),
              "growth": ("النمو", "revenue", "sales"), "other": ("أخرى", None, "management")}
SECTOR_DECISIONS = {
    "fnb": ["supplier", "cost", "operations", "staffing", "discount"], "retail": ["inventory", "pricing", "marketing", "supplier"],
    "ecommerce": ["marketing", "operations", "discount", "inventory"], "manufacturing": ["supplier", "cost", "operations", "inventory"],
    "contracting": ["pricing", "collections", "supplier", "cost"], "distribution": ["collections", "inventory", "supplier", "pricing"],
    "services": ["staffing", "pricing", "operations", "collections"], "clinics": ["operations", "collections", "staffing", "growth"],
    "hospitals": ["operations", "collections", "staffing", "cost"], "logistics": ["operations", "cost", "staffing", "pricing"], "other": ["cost", "growth", "supplier", "collections"]}
SECTOR_TERMS = {"fnb": {"cost": "تكلفة الطعام والتشغيل", "operations": "التوصيل والتنفيذ", "staffing": "العمالة"},
                "clinics": {"operations": "الطاقة الاستيعابية", "growth": "استغلال الخدمات"}, "hospitals": {"operations": "الطاقة الاستيعابية"},
                "contracting": {"pricing": "هامش المشاريع", "cost": "تكلفة المشاريع"}, "retail": {"marketing": "العروض الترويجية"}}
# مرادفات مؤشرات القرارات القديمة (2.3/3.3–3.7) → كتالوج 3.8
KPI_ALIAS = {"net_sales": "revenue", "sales": "revenue", "revenue": "revenue", "profit": "net_profit", "net_profit": "net_profit", "gross_margin": "gross_margin",
             "expense_ratio": "expense_ratio", "growth": "revenue", "repeat_rate": "repeat_purchase", "dso_days": "dso", "dso": "dso"}
RESULT_AR = {"excellent": "ممتاز", "strong": "قوي", "partial": "جزئي", "weak": "ضعيف", "negative": "سلبي", "mixed": "مختلط", "positive": "إيجابي",
             "no_change": "بلا تغيّر واضح", "not_measurable": "غير قابل للقياس", "pending": "بانتظار القياس"}
RESULT_RULE_AR = "النتيجة = الفعلي ÷ المتوقع: ممتاز ≥ 110% · قوي ≥ 90% · جزئي ≥ 50% · ضعيف > 0 · سلبي ≤ 0 · «مختلط» إذا تحسّن المؤشر الرئيسي وساء مؤشر جانبي أكثر من 5%"
CANNOT_MEASURE_AR = "Outcome cannot be measured — insufficient KPI data · لا يمكن الحكم بنجاح القرار أو فشله دون بيانات المؤشر"
CAUSAL_NOTE_AR = "قبل/بعد مقارنة زمنية — التغيّر قد يرتبط بعوامل أخرى غير القرار، ولا يثبت أن القرار هو السبب"


def _d(v):
    return G._d(v)


def _n(v):
    return G._num(v)


def validate_decision(d):
    """عقد بيانات القرار: قرار مؤثر = عنوان + سبب + مؤشر + مسؤول + موعد (والمسودة تُقبل ناقصة)."""
    err = []
    if not (d.get("title") or "").strip():
        err.append("عنوان القرار مطلوب")
    if d.get("priority") and d["priority"] not in PRIORITY:
        err.append("الأولوية غير صالحة")
    if d.get("category") and d["category"] not in CATEGORIES:
        err.append("فئة القرار غير معروفة")
    if d.get("due") and not _d(d["due"]):
        err.append("تاريخ الاستحقاق غير صالح (YYYY-MM-DD)")
    for f in ("expected_impact", "cost"):
        if d.get(f) not in (None, "") and _n(d[f]) is None:
            err.append("القيم المالية يجب أن تكون رقمية")
            break
    if _n(d.get("cost")) is not None and _n(d["cost"]) < 0:
        err.append("تكلفة التنفيذ لا تكون سالبة")
    for k in d.get("kpis") or []:
        if not G.metric_meta(k.get("metric")):
            err.append(f"المؤشر «{k.get('metric')}» غير موجود في الكتالوج")
        if k.get("target") not in (None, "") and _n(k["target"]) is None:
            err.append("مستهدف المؤشر يجب أن يكون رقمياً")
    if d.get("delay_reason") and d["delay_reason"] not in DELAY_REASONS:
        err.append("سبب التأخير غير معروف")
    return err


def readiness(d):
    """ما ينقص القرار ليُرسل للاعتماد (القرار المؤثر يحتاج سبباً ومؤشراً ومسؤولاً وموعداً وأثراً متوقعاً)."""
    miss = []
    if not (d.get("rationale") or d.get("description")):
        miss.append("السبب")
    if not [k for k in d.get("kpis") or [] if not k.get("side_effect")]:
        miss.append("مؤشر KPI")
    if not d.get("owner"):
        miss.append("المسؤول")
    if not d.get("due"):
        miss.append("الموعد")
    if _n(d.get("expected_impact")) is None:
        miss.append("الأثر المتوقع")
    return miss


# ═══════════════════════════════════════════════════════════
# 3.9.9 / 3.9.16 — KPI من المصدر الموحد + محرك قبل/بعد
# قبل = متوسط 3 أشهر مكتملة قبل شهر القرار · بعد = حتى 3 أشهر مكتملة بعد شهر الإكمال (لا يُخلط شهر التنفيذ)
# ═══════════════════════════════════════════════════════════
def complete_months(ctx, meta):
    if meta["src"] == "sales":
        sm = ctx["sm"]
        return sorted(m for m in sm["company"] if m != sm.get("partial_month"))
    if meta["src"] in ("fin", "ratio"):
        return sorted(ctx["trends"])
    return []


def kpi_snapshot(metric, ctx, branch=None, months=None):
    """قيمة المؤشر على نافذة أشهر (معدل شهري للتراكمي، نسبة مجاميع للنسب) أو القيمة الحالية لمؤشرات المستوى."""
    meta = G.metric_meta(metric)
    if not meta:
        return {"status": "unavailable", "value": None, "reason_ar": "المؤشر غير معرّف"}
    lvl = "branch" if branch else "company"
    if meta["src"] in ("sales", "fin", "ratio"):
        if not months:
            return {"status": "unavailable", "value": None, "reason_ar": "لا توجد أشهر مكتملة في النافذة"}
        g = {"id": None, "metric": metric, "branch": branch, "level": lvl, "start": G._mstart(months[0]).isoformat(), "end": G._mend(months[-1]).isoformat()}
        a = G.metric_actual(g, ctx)
        if a.get("status") != "ok":
            return {"status": "unavailable", "value": None, "reason_ar": a.get("reason_ar")}
        v = a["value"] / len(months) if meta["kind"] == "flow" else a["value"]
        return {"status": "ok", "value": round(v, 4), "months": list(months), "per": "شهرياً" if meta["kind"] == "flow" else None,
                "basis_ar": ("متوسط شهري " if meta["kind"] == "flow" else "") + f"{months[0]} → {months[-1]} ({len(months)} شهر)", "a": a}
    a = G.metric_actual({"id": None, "metric": metric, "branch": branch, "level": lvl, "start": "2000-01-01", "end": "2100-01-01"}, ctx)
    if a.get("status") != "ok":
        return {"status": "unavailable", "value": None, "reason_ar": a.get("reason_ar")}
    return {"status": "ok", "value": a["value"], "months": None, "basis_ar": "القيمة الحالية · " + (a.get("basis_ar") or ""), "current_only": True}


def windows(ctx, meta, decision_day, completed_day):
    av = complete_months(ctx, meta)
    dm = G._ymd(decision_day) if decision_day else None
    before = [m for m in av if dm and m < dm][-BEFORE_MONTHS:]
    after = []
    if completed_day:
        cm = G._ymd(completed_day)
        after = [m for m in av if m > cm][:AFTER_MONTHS_MAX]
    return before, after


def _improve(meta, before, after):
    """التحسن موقّع باتجاه المؤشر (+ = تحسن)."""
    if before is None or after is None:
        return None
    return (after - before) if meta["dir"] == "higher" else (before - after)


def kpi_eval(k, dec, ctx):
    meta = G.metric_meta(k.get("metric"))
    if not meta:
        return {"metric": k.get("metric"), "status": "unavailable", "reason_ar": "مؤشر غير معرّف", "measurable": False}
    dday = _d(dec.get("approved_at")) or _d(dec.get("created_at"))
    cday = _d(dec.get("completed_at"))
    bw, aw = windows(ctx, meta, dday, cday)
    br = k.get("branch") or dec.get("branch")
    if meta["src"] in ("sales", "fin", "ratio") and br and not meta["branch"]:
        br = None
    # قبل: من النافذة إن أمكن، وإلا خط الأساس المحفوظ وقت القرار
    bs = kpi_snapshot(k["metric"], ctx, br, bw) if bw else {"status": "unavailable", "value": None}
    before, before_basis = (bs["value"], bs.get("basis_ar")) if bs["status"] == "ok" else (_n(k.get("baseline")), (f"خط أساس محفوظ وقت القرار ({k.get('baseline_at') or '—'})" if _n(k.get("baseline")) is not None else None))
    if bs["status"] != "ok" and _n(k.get("baseline")) is not None and meta["src"] in ("sales", "fin", "ratio"):
        before_basis = f"خط أساس محفوظ ({k.get('baseline_at') or '—'}) — لا أشهر مكتملة كافية قبل القرار"
    now = kpi_snapshot(k["metric"], ctx, br, complete_months(ctx, meta)[-BEFORE_MONTHS:] if meta["src"] in ("sales", "fin", "ratio") else None)
    current = now["value"] if now["status"] == "ok" else _n(k.get("actual_manual"))
    after = None
    after_basis = None
    if cday:
        if meta["src"] in ("sales", "fin", "ratio"):
            if len(aw) >= MIN_AFTER_MONTHS:
                s_ = kpi_snapshot(k["metric"], ctx, br, aw)
                after, after_basis = (s_["value"], s_.get("basis_ar")) if s_["status"] == "ok" else (None, s_.get("reason_ar"))
        else:
            after, after_basis = current, "القيمة الحالية بعد الإكمال"
        if after is None and _n(k.get("actual_manual")) is not None:
            after, after_basis = _n(k["actual_manual"]), f"قيمة مسجّلة يدوياً ({k.get('actual_at') or '—'})"
    tgt = _n(k.get("target"))
    ref = after if after is not None else current
    prog = None
    if tgt is not None and before is not None and ref is not None and tgt != before:
        prog = (ref - before) / (tgt - before)
    imp = _improve(meta, before, ref)
    rel = (imp / abs(before) * 100) if (imp is not None and before) else None
    measurable = before is not None and ref is not None
    return {"id": k.get("id"), "metric": k["metric"], "metric_ar": meta["ar"], "unit": meta["unit"], "dir": meta["dir"], "kind": meta["kind"], "money": meta["money"],
            "branch": br, "side_effect": bool(k.get("side_effect")), "before": None if before is None else round(before, 4), "before_basis_ar": before_basis,
            "target": tgt, "current": None if current is None else round(current, 4), "current_basis_ar": now.get("basis_ar") if now["status"] == "ok" else (k.get("actual_at") and "يدوي"),
            "after": None if after is None else round(after, 4), "after_basis_ar": after_basis, "after_months": aw, "before_months": bw,
            "change": None if (before is None or ref is None) else round(ref - before, 4), "improvement": None if imp is None else round(imp, 4),
            "improvement_pct": None if rel is None else round(rel, 1), "progress_pct": None if prog is None else round(prog * 100, 1),
            "target_met": (tgt is not None and ref is not None and ((ref >= tgt) if meta["dir"] == "higher" else (ref <= tgt))),
            "measurable": measurable, "status": "ok" if measurable else "unavailable",
            "reason_ar": None if measurable else (now.get("reason_ar") or bs.get("reason_ar") or "لا خط أساس أو لا قيمة حالية"),
            "source_ar": G.SRC_AR.get(meta["src"]), "link": G.SRC_LINK.get(meta["src"])}


def kpi_money(kv, ctx):
    """أثر مالي مقاس من مؤشر (معدل بعد − قبل) × أشهر القياس. للنسب: فرق النقاط × إيراد أشهر القياس."""
    if kv.get("after") is None or kv.get("before") is None or not kv.get("after_months"):
        return None, None
    m = len(kv["after_months"])
    if kv["money"] and kv["kind"] == "flow":
        return round(kv["improvement"] * m, 2), f"(بعد {kv['after']:,.0f} − قبل {kv['before']:,.0f}) شهرياً × {m} شهر قياس"
    if kv["unit"] == "%" and kv["metric"] in ("gross_margin", "net_margin", "ebitda_margin", "expense_ratio"):
        rev = sum(G._num(ctx["trends"][x].get("revenue")) or 0 for x in kv["after_months"] if x in ctx["trends"])
        if rev:
            return round(kv["improvement"] / 100 * rev, 2), f"تحسن {kv['improvement']:+.2f} نقطة × إيراد أشهر القياس {rev:,.0f}"
    return None, None


# ═══════════════════════════════════════════════════════════
# 3.9.10 → 3.9.13 — الربط بالأهداف والمخاطر والأسباب والتسرب
# ═══════════════════════════════════════════════════════════
def _driver(ctx, key):
    return (ctx.get("dd") or {}).get(key) or (ctx.get("rd") or {}).get(key)


def risk_link(dec, ctx):
    k = dec.get("risk_key")
    if not k:
        return None
    d = _driver(ctx, k)
    base = _n(dec.get("risk_baseline"))
    if not d or d.get("score") is None:
        return {"key": k, "status": "unavailable", "reason_ar": "المسبب غير محسوب الآن", "baseline": base}
    cur = d["score"]
    return {"key": k, "name_ar": d.get("name_ar"), "baseline": base, "current": cur, "level": d.get("level"), "level_ar": RE.LEVEL_AR.get(d.get("level")),
            "change": None if base is None else round(cur - base, 1), "link": d.get("link"),
            "verdict_ar": None if base is None else ("انخفض الخطر" if cur < base - 2 else "ارتفع الخطر" if cur > base + 2 else "الخطر بلا تغيّر يُذكر"),
            "sentence_ar": f"{d.get('name_ar')}: {base:.0f} → {cur:.0f}" if base is not None else f"{d.get('name_ar')}: الآن {cur:.0f} (لا خط أساس محفوظ)"}


def root_cause_link(dec, ctx):
    k = dec.get("root_cause")
    if not k:
        return None
    d = _driver(ctx, k)
    base = _n(dec.get("root_cause_baseline"))
    if not d or d.get("score") is None:
        return {"key": k, "status_key": "unknown", "status_ar": "غير قابل للتحديد — بيانات المسبب غير متاحة"}
    cur = d["score"]
    if d.get("level") == "low" and (base is None or cur < base):
        st = "resolved"
    elif base is not None and cur < base - 5:
        st = "partial"
    else:
        st = "persistent"
    return {"key": k, "name_ar": d.get("name_ar"), "baseline": base, "current": cur, "level_ar": RE.LEVEL_AR.get(d.get("level")), "status_key": st,
            "status_ar": {"resolved": "عولج (Resolved)", "partial": "عولج جزئياً (Partially Resolved)", "persistent": "مستمر (Persistent)"}[st],
            "note_ar": "السبب من مسببات 3.4 (الأسباب الجذرية 3.6 غير مفعّلة) — الحالة من درجة المسبب الآن مقابل وقت القرار"}


LEAK_TYPES = {"discount": "خصومات فوق المعتاد", "returns": "مرتجعات فوق المعتاد", "opex": "مصروفات تشغيلية زائدة"}


def leakage_link(dec, ctx):
    k = dec.get("leakage_key")
    if not k:
        return None
    ov = ((ctx["mods"].get("leakage") or {}).get("overview") or {})
    t = next((x for x in ov.get("types") or [] if x.get("key") == k), None)
    base = _n(dec.get("leakage_baseline"))
    exp = _n(dec.get("expected_impact"))
    if not t or not t.get("available"):
        return {"key": k, "status": "unavailable", "reason_ar": "نوع التسرب غير متاح في مركز استرداد الأموال", "expected": exp}
    cur = _n(t.get("amount"))
    red = None if (base is None or cur is None) else round(base - cur, 2)
    cday = _d(dec.get("completed_at"))
    months = max(1, (ctx["today"] - cday).days // 30) if cday else None
    actual = round(red * months, 2) if (red is not None and months and red > 0) else (0.0 if red is not None and cday else None)
    return {"key": k, "ar": LEAK_TYPES.get(k, k), "baseline": base, "current": cur, "reduction": red, "months": months, "expected_recovery": exp,
            "actual_recovery": actual, "recovery_rate_pct": round(actual / exp * 100, 1) if (actual is not None and exp) else None,
            "basis_ar": "انخفاض التسرب الشهري (وقت القرار − الآن) × الأشهر منذ الإكمال — تقدير من مركز استرداد الأموال", "link": "company-leakage-intelligence.html"}


def goal_link(dec, goal_evals):
    gid = dec.get("goal_id")
    if gid is None:
        return None
    g = (goal_evals or {}).get(gid) or (goal_evals or {}).get(str(gid))
    if not g:
        return {"goal_id": gid, "status": "unavailable", "reason_ar": "الهدف غير متاح ضمن نطاقك أو مؤرشف"}
    b = dec.get("goal_baseline") or {}
    return {"goal_id": gid, "name": g.get("name"), "status": g.get("status"), "status_ar": g.get("status_ar"), "progress_pct": g.get("progress_pct"),
            "before_progress_pct": round(b["progress"] * 100, 1) if b.get("progress") is not None else None, "before_status": b.get("status"),
            "expected_gap_now": g.get("expected_gap"), "expected_gap_before": b.get("expected_gap"), "unit": g.get("unit"),
            "link": f"company-goals-intelligence.html#goal/{gid}", "note_ar": "تقدم الهدف يتحدث تلقائياً من البيانات — لا يُضاف إليه أثر القرار يدوياً"}


# ═══════════════════════════════════════════════════════════
# 3.9.6 — Decision Status Engine (مخزّن + مشتق: متأخر / بانتظار القياس)
# ═══════════════════════════════════════════════════════════
def display_status(dec, today, measurable_now=False):
    wf = dec.get("workflow") or "draft"
    due = _d(dec.get("due"))
    if wf in ("approved", "in_progress", "pending_approval", "under_review") and due and due < today:
        return "late"
    if wf == "completed":
        return "evaluating"
    return wf


def result_of(expected, actual, primary_imp=None, side_bad=False):
    if actual is None and primary_imp is None:
        return "not_measurable"
    if expected and expected > 0 and actual is not None:
        r = actual / expected
        res = "excellent" if r >= 1.10 else "strong" if r >= 0.90 else "partial" if r >= 0.50 else "weak" if r > 0 else "negative"
    elif primary_imp is not None:
        res = "positive" if primary_imp > 0 else "negative" if primary_imp < 0 else "no_change"
    else:
        res = "negative" if actual < 0 else "positive" if actual > 0 else "no_change"
    if side_bad and res in ("excellent", "strong", "partial", "positive"):
        return "mixed"
    return res


def outcome(dec, kvs, ctx, leak):
    """3.9.14 → 3.9.16: متوقع / فعلي / فرق / نتيجة. المكتمل ≠ الناجح."""
    exp = _n(dec.get("expected_impact"))
    wf = dec.get("workflow")
    prim = next((k for k in kvs if not k["side_effect"]), None)
    sides = [k for k in kvs if k["side_effect"]]
    side_bad = [k for k in sides if k.get("improvement_pct") is not None and k["improvement_pct"] < -5]
    stored = dec.get("outcome") or {}
    out = {"expected": exp, "expected_type": dec.get("expected_type") or "potential", "primary_kpi": prim["metric_ar"] if prim else None,
           "side_effects": [{"metric_ar": k["metric_ar"], "before": k["before"], "after": k["after"] if k["after"] is not None else k["current"], "unit": k["unit"],
                             "improvement_pct": k["improvement_pct"], "worse": k in side_bad} for k in sides]}
    if wf not in ("completed", "measured"):
        out.update({"state": "not_ready", "state_ar": "يُقاس بعد إكمال التنفيذ", "actual": None, "variance": None, "result": "pending", "result_ar": RESULT_AR["pending"],
                    "live_progress_pct": prim.get("progress_pct") if prim else None})
        return out
    actual, method, manual = None, None, False
    if leak and leak.get("actual_recovery") is not None:
        # قرار تسرب يُقاس بالاسترداد الفعلي من مركز استرداد الأموال (3.1) قبل أي تقدير من المؤشر
        actual, method = leak["actual_recovery"], leak["basis_ar"]
    elif prim:
        actual, method = kpi_money(prim, ctx)
    if _n(stored.get("actual_manual")) is not None:
        actual, method, manual = _n(stored["actual_manual"]), f"قيمة مسجّلة يدوياً مع دليل: {stored.get('notes') or '—'}", True
    prim_ready = bool(prim and prim["after"] is not None and prim["before"] is not None)
    if not prim_ready and actual is None:
        reason = (prim or {}).get("reason_ar") or ("لا يوجد مؤشر مرتبط بالقرار" if not prim else None)
        if prim and prim["before"] is not None and prim["after"] is None and prim["kind"] and G.metric_meta(prim["metric"])["src"] in ("sales", "fin", "ratio"):
            out.update({"state": "evaluating", "state_ar": f"بانتظار شهر مكتمل بعد الإكمال للقياس (يلزم {MIN_AFTER_MONTHS} على الأقل)", "actual": None, "variance": None,
                        "result": "pending", "result_ar": RESULT_AR["pending"]})
            return out
        out.update({"state": "cannot_measure", "state_ar": CANNOT_MEASURE_AR, "reason_ar": reason, "actual": None, "variance": None,
                    "result": "not_measurable", "result_ar": RESULT_AR["not_measurable"]})
        return out
    res = result_of(exp, actual, prim["improvement"] if prim_ready else None, bool(side_bad))
    out.update({"state": "measured" if wf == "measured" else "measurable", "state_ar": "مُقاس" if wf == "measured" else "جاهز للقياس — اعتمد القياس لإغلاق القرار",
                "actual": actual, "actual_type": "actual", "method_ar": method, "manual": manual,
                "variance": None if (actual is None or exp is None) else round(actual - exp, 2),
                "achievement_pct": None if (actual is None or not exp) else round(actual / exp * 100, 1),
                "result": res, "result_ar": RESULT_AR[res], "rule_ar": RESULT_RULE_AR, "causal_note_ar": CAUSAL_NOTE_AR,
                "measured_at": stored.get("measured_at"), "confidence": "limited" if manual else ("high" if prim_ready and len(prim.get("after_months") or []) >= 2 else "medium")})
    if side_bad:
        out["mixed_note_ar"] = ("المؤشر الرئيسي تحسّن لكن ساء: " if res == "mixed" else "وساء أيضاً: ") + "، ".join(f"{k['metric_ar']} ({k['improvement_pct']:+.1f}%)" for k in side_bad) + " — المكتمل ≠ الناجح"
    return out


# ═══════════════════════════════════════════════════════════
# 3.9.17 / 3.9.18 — Effectiveness (مكوّنات ظاهرة) + ROI
# ═══════════════════════════════════════════════════════════
EFF_W = [("kpi", "تحسن المؤشر", 25), ("financial", "الأثر المالي مقابل المتوقع", 25), ("completion", "الإنجاز", 15), ("timeliness", "الالتزام بالموعد", 15),
         ("target", "بلوغ المستهدف", 10), ("side_effects", "الآثار الجانبية", 5), ("confidence", "ثقة البيانات", 5)]
EFF_RULE_AR = "الفعالية = " + " + ".join(f"{w}% {a}" for _, a, w in EFF_W) + " — المكونات غير المتاحة تُستبعد ويُعاد التوزيع، وتظهر كل قيمة."


def effectiveness(dec, kvs, out, today, actions):
    prim = next((k for k in kvs if not k["side_effect"]), None)
    wf = dec.get("workflow")
    comp = {}
    if prim and prim.get("measurable"):
        if prim.get("progress_pct") is not None:
            comp["kpi"] = max(0.0, min(1.0, prim["progress_pct"] / 100))
        elif prim.get("improvement") is not None:
            comp["kpi"] = 1.0 if prim["improvement"] > 0 else 0.5 if prim["improvement"] == 0 else 0.0
    if out.get("achievement_pct") is not None:
        comp["financial"] = max(0.0, min(1.0, out["achievement_pct"] / 100))
    if wf in ("completed", "measured"):
        comp["completion"] = 1.0
    elif wf in ("in_progress", "approved") and actions:
        live = [a for a in actions if a.get("status") != "cancelled"]
        comp["completion"] = (sum((a.get("progress") or 0) for a in live) / len(live) / 100) if live else 0.0
    elif wf in ("approved", "in_progress"):
        comp["completion"] = 0.0
    due, done = _d(dec.get("due")), _d(dec.get("completed_at"))
    if due:
        ref = done or today
        late = (ref - due).days
        comp["timeliness"] = 1.0 if late <= 0 else max(0.0, 1 - late / 30)
    if prim and prim.get("target") is not None and prim.get("measurable"):
        comp["target"] = 1.0 if prim["target_met"] else max(0.0, min(1.0, (prim.get("progress_pct") or 0) / 100))
    if out.get("side_effects"):
        comp["side_effects"] = 0.0 if any(s["worse"] for s in out["side_effects"]) else 1.0
    elif prim and prim.get("measurable"):
        comp["side_effects"] = 1.0
    comp["confidence"] = 0.5 if out.get("manual") else (1.0 if (prim and prim.get("measurable")) else 0.0)
    rows = [{"key": k, "ar": a, "weight": w, "value_pct": None if comp.get(k) is None else round(comp[k] * 100, 1)} for k, a, w in EFF_W]
    av = [(w, comp[k]) for k, _, w in EFF_W if comp.get(k) is not None]
    score = round(sum(w * v for w, v in av) / sum(w for w, _ in av) * 100, 1) if av else None
    return {"score": score, "components": rows, "final": wf == "measured", "label_ar": None if score is None else ("مبدئي — قبل القياس" if wf != "measured" else "نهائي"),
            "rule_ar": EFF_RULE_AR}


def roi(dec, out):
    cost, act = _n(dec.get("cost")), out.get("actual")
    if not cost:
        return {"available": False, "reason_ar": "لم تُسجّل تكلفة تنفيذ للقرار"}
    if act is None:
        return {"available": False, "cost": cost, "reason_ar": "العائد الفعلي لم يُقس بعد"}
    net = act - cost
    return {"available": True, "cost": cost, "return": act, "net": round(net, 2), "roi_pct": round(net / cost * 100, 1),
            "rule_ar": "العائد على القرار = (الأثر الفعلي − تكلفة التنفيذ) ÷ التكلفة"}


# ═══════════════════════════════════════════════════════════
# 3.9.19 — Delay & Escalation · 3.9.20 — Dependencies · القرارات المتكررة
# ═══════════════════════════════════════════════════════════
def high_impact(dec, threshold):
    e = _n(dec.get("expected_impact"))
    return (e is not None and e >= threshold) or dec.get("priority") in ("high", "critical")


def delay(dec, status, today, threshold):
    due = _d(dec.get("due"))
    if status != "late" or not due:
        return None
    days = (today - due).days
    hi = high_impact(dec, threshold)
    esc = hi or days > ESCALATE_LATE_DAYS
    e = _n(dec.get("expected_impact"))
    return {"days": days, "reason": dec.get("delay_reason"), "reason_ar": DELAY_REASONS.get(dec.get("delay_reason")) or "لم يُسجّل سبب",
            "reason_note": dec.get("delay_note"), "impact_at_risk": e, "high_impact": hi, "escalate": esc,
            "escalate_ar": ("تصعيد للرئيس التنفيذي/المالك" if esc else None),
            "sentence_ar": (f"قرار بأثر متوقع {e:,.0f} متأخر {days} يوماً" if e else f"قرار متأخر {days} يوماً")}


def dependency_view(decs_by_id, did, statuses):
    d = decs_by_id[did]
    ups = []
    for u in d.get("depends_on") or []:
        x = decs_by_id.get(u)
        if not x:
            ups.append({"id": u, "title": "قرار خارج نطاقك أو محذوف", "status": None, "blocking": False})
            continue
        st = statuses.get(u)
        ups.append({"id": u, "title": x["title"], "status": st, "status_ar": STATUS_AR.get(st), "blocking": st not in ("completed", "evaluating", "measured")})
    downs = [{"id": x["id"], "title": x["title"], "status": statuses.get(x["id"]), "status_ar": STATUS_AR.get(statuses.get(x["id"]))}
             for x in decs_by_id.values() if did in (x.get("depends_on") or [])]
    return {"upstream": ups, "downstream": downs, "blocked": any(u["blocking"] for u in ups),
            "affects_ar": (f"تأخّر هذا القرار يؤثر على {len(downs)} قرار يعتمد عليه" if downs and statuses.get(did) == "late" else None)}


def dependency_issues(decs_by_id):
    out = []
    for i, d in decs_by_id.items():
        seen, stack = set(), list(d.get("depends_on") or [])
        while stack:
            x = stack.pop()
            if x == i:
                out.append(f"حلقة اعتماد تشمل «{d['title']}»")
                break
            if x in seen or x not in decs_by_id:
                continue
            seen.add(x)
            stack += list(decs_by_id[x].get("depends_on") or [])
    return sorted(set(out))


_STOP = {"في", "من", "على", "إلى", "مع", "عن", "ال", "و", "the", "of", "to", "and", "for", "a", "قرار", "معالجة"}


def _tokens(t):
    t = re.sub(r"[^\w\s]", " ", str(t or "").lower())
    return {w[2:] if w.startswith("ال") and len(w) > 4 else w for w in t.split() if len(w) > 2 and w not in _STOP}


def recurring(decs, today, window_days=365, min_count=3):
    """قرار مشابه تكرر ≥ 3 مرات خلال سنة: نفس الفئة/المؤشر/المصدر أو عنوان متشابه (Jaccard ≥ 0.5)."""
    pool = [d for d in decs if _d(d.get("created_at")) and (today - _d(d["created_at"])).days <= window_days and d.get("workflow") != "cancelled"]
    groups, used = [], set()
    for d in sorted(pool, key=lambda x: str(x.get("created_at"))):
        if d["id"] in used:
            continue
        td = _tokens(d["title"])
        grp = [d]
        for o in pool:
            if o["id"] == d["id"] or o["id"] in used:
                continue
            to = _tokens(o["title"])
            j = len(td & to) / len(td | to) if (td | to) else 0
            same_key = (d.get("risk_key") and d.get("risk_key") == o.get("risk_key")) or (d.get("root_cause") and d.get("root_cause") == o.get("root_cause")) or \
                       (d.get("category") and d.get("category") == o.get("category") and d.get("category") != "other" and
                        (d.get("primary_metric") and d.get("primary_metric") == o.get("primary_metric")))
            if j >= 0.5 or same_key:
                grp.append(o)
        if len(grp) >= min_count:
            used |= {x["id"] for x in grp}
            ds = sorted(_d(x["created_at"]) for x in grp)
            months = max(1, round((ds[-1] - ds[0]).days / 30))
            groups.append({"ids": [x["id"] for x in grp], "titles": [x["title"] for x in grp], "count": len(grp), "months": months,
                           "sentence_ar": f"تم اتخاذ قرار مشابه {len(grp)} مرات خلال {months} أشهر",
                           "insight_ar": "لم تتم معالجة السبب بشكل دائم — المشكلة قد تكون هيكلية وليست حالة مؤقتة. راجع الأسباب (3.4/3.6) بدل تكرار نفس الإجراء."})
    return groups


# ═══════════════════════════════════════════════════════════
# 3.9.5 — RBAC (يبني على نطاق المستخدم من 3.8: المسمّى + الفرع + القسم)
# ═══════════════════════════════════════════════════════════
MGR_DEPTS = {"operations", "inventory", "sales", "marketing", "purchasing", "hr"}
ACC_DEPTS = {"finance", "management"}


def _mine(dec, v):
    return G._is_mine({"owner": dec.get("owner"), "owner_user_id": dec.get("owner_user_id")}, v) or \
        bool(v.get("user_id") and dec.get("created_by_id") == v.get("user_id"))


def _assigned(dec, v):
    return any(G._is_mine({"owner": a.get("owner"), "owner_user_id": a.get("owner_user_id")}, v) for a in dec.get("actions") or [])


def is_exec(v):
    return (v or {}).get("role") == "owner" or (v or {}).get("title") == "ceo"


def is_mgr(v):
    v = v or {}
    return v.get("title") in ("dept_manager", "branch_manager") or (v.get("role") in ("manager", "accountant") and v.get("title") != "employee")


def dept_of(dec):
    return dec.get("department") or CATEGORIES.get(dec.get("category") or "other", (None, None, "management"))[2]


def visible(dec, v):
    v = v or {"role": "owner"}
    if is_exec(v) or _mine(dec, v) or _assigned(dec, v):
        return True
    if dec.get("sensitive") and v.get("role") != "accountant":
        return False
    if v.get("title") == "branch_manager" and v.get("branch"):
        return dec.get("branch") == v["branch"]
    if v.get("title") == "dept_manager" and v.get("department"):
        return dept_of(dec) == v["department"]
    if v.get("role") == "manager" and v.get("title") != "employee":
        return dept_of(dec) in MGR_DEPTS
    if v.get("role") == "accountant" and v.get("title") != "employee":
        return dept_of(dec) in ACC_DEPTS or bool(dec.get("sensitive"))
    return False


def scope_of(v):
    v = v or {}
    if is_exec(v):
        return {"kind": "all", "ar": "كل القرارات (مالك / رئيس تنفيذي)"}
    if v.get("title") == "branch_manager" and v.get("branch"):
        return {"kind": "branch", "ar": f"قرارات فرع {v['branch']} + القرارات المسندة إليك"}
    if v.get("title") == "dept_manager" and v.get("department"):
        return {"kind": "department", "ar": f"قرارات قسم {G.DEPARTMENTS.get(v['department'], v['department'])} + المسندة إليك"}
    if is_mgr(v):
        return {"kind": "category", "ar": "قرارات أقسام صلاحيتك + المسندة إليك (اربط المستخدم بفرع/قسم لتحديد أدق)"}
    return {"kind": "own", "ar": "القرارات المسندة إليك فقط"}


PERM_AR = {"create": "إنشاء", "edit": "تعديل", "submit": "إرسال للاعتماد", "review": "مراجعة", "recommend": "توصية بالاعتماد", "assign": "إسناد",
           "approve": "اعتماد", "reject": "رفض", "escalate": "تصعيد", "cancel": "إلغاء/إغلاق", "start": "بدء التنفيذ", "complete": "إكمال",
           "measure": "اعتماد القياس", "progress": "تحديث التقدم", "evidence": "إضافة دليل", "delay_reason": "سبب التأخير", "kpi": "مؤشرات القرار"}


def can(action, dec, v):
    v = v or {}
    if action == "create":
        return is_exec(v) or is_mgr(v)
    if not visible(dec, v):
        return False
    ex, mg, mine = is_exec(v), is_mgr(v), _mine(dec, v) or _assigned(dec, v)
    if action in ("approve", "reject", "cancel", "measure"):
        return ex
    if action == "escalate":
        return ex or mg
    if action in ("edit", "submit", "review", "recommend", "assign", "kpi"):
        return ex or mg
    if action in ("start", "complete"):
        return ex or mg or G._is_mine({"owner": dec.get("owner"), "owner_user_id": dec.get("owner_user_id")}, v)
    if action in ("progress", "evidence", "delay_reason"):
        return ex or mg or mine
    return action == "view"


ACTION_FOR = {"pending_approval": "submit", "under_review": "review", "approved": "approve", "rejected": "reject", "in_progress": "start",
              "completed": "complete", "measured": "measure", "cancelled": "cancel", "draft": "edit"}


def transition_check(dec, to, v):
    """يعيد قائمة أخطاء الانتقال (فارغة = مسموح)."""
    fr = dec.get("workflow") or "draft"
    err = []
    if to not in TRANSITIONS.get(fr, []):
        err.append(f"لا يمكن الانتقال من «{WF_AR.get(fr, fr)}» إلى «{WF_AR.get(to, to)}»")
    if not can(ACTION_FOR.get(to, "edit"), dec, v):
        err.append(f"صلاحيتك لا تسمح بـ«{PERM_AR.get(ACTION_FOR.get(to, 'edit'))}»")
    if to == "pending_approval":
        miss = readiness(dec)
        if miss:
            err.append("القرار يحتاج قبل الإرسال للاعتماد: " + "، ".join(miss))
    if to == "approved" and (dec.get("created_by_id") and dec.get("created_by_id") == v.get("user_id")) and v.get("role") != "owner":
        err.append("لا يعتمد المنشئ قراره بنفسه (إلا المالك)")
    return err


# ═══════════════════════════════════════════════════════════
# تقييم قرار واحد
# ═══════════════════════════════════════════════════════════
def evaluate_decision(dec, ctx, today, threshold, goal_evals=None):
    kvs = [kpi_eval(k, dec, ctx) for k in dec.get("kpis") or []]
    leak = leakage_link(dec, ctx)
    st = display_status(dec, today)
    out = outcome(dec, kvs, ctx, leak)
    if st == "evaluating" and out["state"] in ("measurable",):
        st_note = "جاهز للقياس"
    else:
        st_note = None
    eff = effectiveness(dec, kvs, out, today, dec.get("actions") or [])
    src = SOURCES.get(dec.get("source") or "manual", SOURCES["manual"])
    prim = next((k for k in kvs if not k["side_effect"]), None)
    acts = dec.get("actions") or []
    live = [a for a in acts if a.get("status") != "cancelled"]
    return {**{k: dec.get(k) for k in ("id", "title", "description", "rationale", "owner", "owner_user_id", "branch", "due", "category", "priority", "workflow",
                                       "created_at", "created_by", "approved_by", "approved_at", "completed_at", "source", "source_ref", "goal_id", "risk_key",
                                       "root_cause", "leakage_key", "cost", "sensitive", "legacy", "depends_on", "delay_reason")},
            "department": dept_of(dec), "department_ar": G.DEPARTMENTS.get(dept_of(dec), dept_of(dec)),
            "category_ar": CATEGORIES.get(dec.get("category") or "other", ("أخرى",))[0], "priority_ar": PRIORITY.get(dec.get("priority") or "medium", ("متوسطة",))[0],
            "status": st, "status_ar": STATUS_AR[st], "icon": STATUS_ICON.get(st, ""), "status_note_ar": st_note, "workflow_ar": WF_AR.get(dec.get("workflow") or "draft"),
            "source_code": src[0], "source_ar": src[1], "source_link": src[2], "expected_impact": _n(dec.get("expected_impact")),
            "expected_type": dec.get("expected_type") or "potential", "high_impact": high_impact(dec, threshold), "readiness_missing": readiness(dec),
            "kpis": kvs, "primary": prim, "outcome": out, "effectiveness": eff, "roi": roi(dec, out), "delay": delay(dec, st, today, threshold),
            "links": {"goal": goal_link(dec, goal_evals), "risk": risk_link(dec, ctx), "root_cause": root_cause_link(dec, ctx), "leakage": leak},
            "actions": acts, "actions_progress": round(sum((a.get("progress") or 0) for a in live) / len(live), 1) if live else None,
            "evidence": dec.get("evidence") or [], "history": sorted(dec.get("history") or [], key=lambda h: str(h.get("at") or "")),
            "next_steps": TRANSITIONS.get(dec.get("workflow") or "draft", [])}


def review_draft(e):
    """مراجعة القرار (حتمية) — يكتبها المحرك من الأرقام؛ الذكاء الاصطناعي يشرحها فقط."""
    o = e["outcome"]
    p = e.get("primary") or {}
    lines = [f"القرار: {e['title']}"]
    if p:
        tgt = f" المستهدف {p['target']:,.2f}" if p.get("target") is not None else ""
        lines.append(f"المؤشر: {p['metric_ar']} — قبل {p['before'] if p.get('before') is not None else 'غير متاح'} بعد {p['after'] if p.get('after') is not None else (p.get('current') if p.get('current') is not None else 'غير متاح')}.{tgt}")
    if o.get("expected") is not None:
        lines.append(f"المتوقع: {o['expected']:,.0f}")
    if o.get("actual") is not None:
        lines.append(f"الفعلي: {o['actual']:,.0f}" + (f" · الفرق: {o['variance']:+,.0f}" if o.get("variance") is not None else ""))
    lines.append(f"النتيجة: {o.get('result_ar')}" + (f" — {o['mixed_note_ar']}" if o.get("mixed_note_ar") else ""))
    if o.get("state") == "cannot_measure":
        lines.append(CANNOT_MEASURE_AR)
    return lines


# ═══════════════════════════════════════════════════════════
# التحليل الكامل — مركز متابعة القرارات
# ═══════════════════════════════════════════════════════════
def _group(evals, key, label=None):
    m = {}
    for e in evals:
        k = e.get(key) or "—"
        x = m.setdefault(k, {"key": k, "ar": (label or {}).get(k, k), "count": 0, "open": 0, "late": 0, "expected": 0.0, "actual": 0.0, "measured": 0, "eff": [], "ids": []})
        x["count"] += 1
        x["ids"].append(e["id"])
        x["open"] += e["status"] in ("approved", "in_progress", "late")
        x["late"] += e["status"] == "late"
        if e["workflow"] not in ("cancelled", "rejected", "draft"):
            x["expected"] += e.get("expected_impact") or 0
        if e["outcome"].get("actual") is not None:
            x["actual"] += e["outcome"]["actual"]
            x["measured"] += 1
        if e["effectiveness"]["score"] is not None and e["workflow"] in ("completed", "measured"):
            x["eff"].append(e["effectiveness"]["score"])
    out = []
    for x in m.values():
        x["effectiveness"] = round(sum(x["eff"]) / len(x["eff"]), 1) if x["eff"] else None
        x["expected"], x["actual"] = round(x["expected"], 2), round(x["actual"], 2)
        del x["eff"]
        out.append(x)
    return sorted(out, key=lambda x: (-x["late"], -x["open"], -x["count"]))


def analyze_decisions(decisions, *, mods=None, sales_rows=None, risk=None, drivers=None, goal_evals=None, sector=None, today=None, viewer=None,
                      currency="SAR", ctx=None):
    today = today or date.today()
    viewer = viewer or {"role": "owner"}
    ctx = ctx or G.build_context(mods, sales_rows, risk=risk, drivers=drivers, today=today)
    ttm = sum(v for _, v in PE.series_of(ctx["sm"]["company"], "revenue", exclude=ctx["sm"].get("partial_month"))[-12:]) if ctx["sm"]["company"] else 0
    threshold = max(50_000.0, round(ttm * 0.01, -3))
    vis = [d for d in decisions or [] if visible(d, viewer)]
    evals = [evaluate_decision(d, ctx, today, threshold, goal_evals) for d in vis]
    by_id = {e["id"]: e for e in evals}
    dmap = {d["id"]: d for d in vis}
    statuses = {e["id"]: e["status"] for e in evals}
    for e in evals:
        e["dependencies"] = dependency_view(dmap, e["id"], statuses)
        e["permissions"] = [a for a in PERM_AR if a != "create" and can(a, dmap[e["id"]], viewer)]
        e["review"] = review_draft(e)
    for d in vis:
        d["primary_metric"] = (by_id[d["id"]].get("primary") or {}).get("metric")
    rec = recurring(vis, today)
    rec_ids = {i for g in rec for i in g["ids"]}
    for e in evals:
        e["recurring"] = e["id"] in rec_ids
    # التنبيهات والتصعيد
    alerts = []

    def al(kind, color, e, title, detail, esc=False):
        alerts.append({"kind": kind, "color": color, "decision_id": e["id"], "decision": e["title"], "title_ar": title, "detail_ar": detail, "owner": e.get("owner"), "escalate": esc})
    for e in evals:
        dl = e.get("delay")
        if dl:
            al("late_escalate" if dl["escalate"] else "late", "red" if dl["escalate"] else "orange", e, "🔴 قرار متأخر — تصعيد" if dl["escalate"] else "🟠 قرار متأخر",
               dl["sentence_ar"] + f" · السبب: {dl['reason_ar']}" + (" · يُقترح: التصعيد للرئيس التنفيذي أو تعيين مسؤول جديد" if dl["escalate"] else ""), dl["escalate"])
        if e["workflow"] in ("pending_approval", "under_review"):
            sub = next((h for h in reversed(e["history"]) if h.get("event") == "submitted"), None)
            age = (today - _d(sub["at"])).days if sub and _d(sub.get("at")) else None
            if age is not None and age > 7:
                al("approval_waiting", "yellow", e, "🟡 بانتظار الاعتماد", f"منذ {age} يوماً")
        if e["dependencies"]["blocked"] and e["status"] not in ("completed", "evaluating", "measured", "cancelled"):
            al("blocked", "orange", e, "🟠 معطّل باعتماد", "يعتمد على: " + "، ".join(u["title"] for u in e["dependencies"]["upstream"] if u["blocking"]))
        if e["workflow"] in ("approved", "in_progress") and not e.get("owner"):
            al("no_owner", "grey", e, "⚪ قرار بلا مسؤول", "عيّن مسؤولاً")
        if e["workflow"] not in ("cancelled", "rejected") and not e.get("primary"):
            al("no_kpi", "grey", e, "⚪ قرار بلا مؤشر", "لن يمكن قياس نتيجته — اربطه بمؤشر")
        o = e["outcome"]
        if o.get("state") == "measurable" and e["workflow"] == "completed":
            al("ready_to_measure", "blue", e, "🔵 جاهز للقياس", f"النتيجة الحالية: {o['result_ar']} — اعتمد القياس لإغلاق القرار")
        if o.get("state") == "cannot_measure":
            al("cannot_measure", "grey", e, "❔ لا يمكن قياس النتيجة", o.get("reason_ar") or CANNOT_MEASURE_AR)
        if o.get("result") in ("mixed", "negative"):
            al("bad_outcome", "red" if o["result"] == "negative" else "orange", e, "🔴 نتيجة سلبية" if o["result"] == "negative" else "🟠 نتيجة مختلطة",
               o.get("mixed_note_ar") or f"الفعلي {o.get('actual')} مقابل المتوقع {o.get('expected')}")
    for g_ in rec:
        alerts.append({"kind": "recurring", "color": "yellow", "decision_id": g_["ids"][-1], "decision": g_["titles"][-1], "title_ar": "🟡 قرار متكرر",
                       "detail_ar": g_["sentence_ar"] + " — " + g_["insight_ar"], "owner": None, "escalate": False})
    order = {"red": 0, "orange": 1, "yellow": 2, "blue": 3, "grey": 4}
    alerts.sort(key=lambda a: order.get(a["color"], 9))
    live = [e for e in evals if e["workflow"] not in ("cancelled", "rejected", "draft")]
    exp_total = round(sum(e.get("expected_impact") or 0 for e in live), 2)
    measured = [e for e in evals if e["outcome"].get("actual") is not None]
    act_total = round(sum(e["outcome"]["actual"] for e in measured), 2)
    exp_measured = round(sum(e.get("expected_impact") or 0 for e in measured), 2)
    cnt = lambda f: sum(1 for e in evals if f(e))
    overview = {"open": cnt(lambda e: e["status"] in ("approved", "in_progress", "late")), "pending_approval": cnt(lambda e: e["workflow"] in ("pending_approval", "under_review")),
                "overdue": cnt(lambda e: e["status"] == "late"), "in_progress": cnt(lambda e: e["workflow"] == "in_progress"),
                "completed": cnt(lambda e: e["workflow"] in ("completed", "measured")), "measured": cnt(lambda e: e["workflow"] == "measured"),
                "evaluating": cnt(lambda e: e["status"] == "evaluating"), "drafts": cnt(lambda e: e["workflow"] == "draft"),
                "high_impact": cnt(lambda e: e["high_impact"] and e["status"] in ("approved", "in_progress", "late", "pending_approval", "under_review")),
                "cancelled": cnt(lambda e: e["workflow"] in ("cancelled", "rejected")), "total": len(evals),
                "expected_total": exp_total, "actual_total": act_total, "expected_of_measured": exp_measured,
                "achievement_pct": round(act_total / exp_measured * 100, 1) if exp_measured else None,
                "headline_ar": (f"قرارات متوقع منها {exp_total:,.0f} {currency}" + (f"، تحقق فعلياً من المقاس منها {act_total:,.0f} من أصل {exp_measured:,.0f} متوقع" if measured else " — لم يُقس أي قرار بعد")),
                "high_impact_threshold": threshold}
    # الأثر المالي (جدول المتوقع/الفعلي/الفرق/النتيجة)
    fin = [{"id": e["id"], "title": e["title"], "expected": e.get("expected_impact"), "actual": e["outcome"].get("actual"), "variance": e["outcome"].get("variance"),
            "achievement_pct": e["outcome"].get("achievement_pct"), "result": e["outcome"].get("result"), "result_ar": e["outcome"].get("result_ar"),
            "type": e.get("expected_type"), "method_ar": e["outcome"].get("method_ar"), "manual": e["outcome"].get("manual"), "status_ar": e["status_ar"]}
           for e in evals if e.get("expected_impact") is not None or e["outcome"].get("actual") is not None]
    # فعالية القرارات وتاريخها — أي أنواع القرارات تحقق نتائج أفضل؟
    def eff_hist(key, labels=None):
        rows = []
        for gk, grp in {k: [e for e in evals if (e.get(key) or "—") == k and e["workflow"] == "measured"] for k in {(e.get(key) or "—") for e in evals}}.items():
            if not grp:
                continue
            sc = [e["effectiveness"]["score"] for e in grp if e["effectiveness"]["score"] is not None]
            ok = sum(1 for e in grp if e["outcome"].get("result") in ("excellent", "strong", "positive"))
            rows.append({"key": gk, "ar": (labels or {}).get(gk, gk), "measured": len(grp), "avg_effectiveness": round(sum(sc) / len(sc), 1) if sc else None,
                         "success_rate_pct": round(ok / len(grp) * 100, 1), "expected": round(sum(e.get("expected_impact") or 0 for e in grp), 2),
                         "actual": round(sum(e["outcome"].get("actual") or 0 for e in grp), 2)})
        return sorted(rows, key=lambda r: -(r["avg_effectiveness"] or 0))
    cat_ar = {k: v[0] for k, v in CATEGORIES.items()}
    src_ar = {k: v[1] for k, v in SOURCES.items()}
    # الخريطة الحرارية: الأثر × الإلحاح
    heat = []
    for e in evals:
        if e["status"] not in ("approved", "in_progress", "late", "pending_approval", "under_review", "draft"):
            continue
        due = _d(e.get("due"))
        urgent = e["status"] == "late" or (due is not None and (due - today).days <= 7) or e.get("priority") == "critical"
        q = ("red" if e["high_impact"] and urgent else "orange" if e["high_impact"] else "yellow" if urgent else "green")
        heat.append({"id": e["id"], "title": e["title"], "quadrant": q, "impact": e.get("expected_impact"), "due": e.get("due"), "status_ar": e["status_ar"]})
    delays = [e for e in evals if e.get("delay")]
    reasons = {}
    for e in delays:
        r = e["delay"]["reason"] or "none"
        x = reasons.setdefault(r, {"reason": r, "ar": DELAY_REASONS.get(r, "لم يُسجّل سبب"), "count": 0, "days": 0, "impact": 0.0, "ids": []})
        x["count"] += 1
        x["days"] += e["delay"]["days"]
        x["impact"] += e["delay"]["impact_at_risk"] or 0
        x["ids"].append(e["id"])
    trail = sorted(({**h, "decision_id": e["id"], "decision": e["title"], "event_ar": EVENT_AR.get(h.get("event"), h.get("event"))} for e in evals for h in e["history"]),
                   key=lambda h: str(h.get("at") or ""), reverse=True)
    sec = sector or "other"
    dq = {"no_kpi": [{"id": e["id"], "title": e["title"]} for e in evals if not e.get("primary") and e["workflow"] not in ("cancelled", "rejected")],
          "cannot_measure": [{"id": e["id"], "title": e["title"], "reason_ar": e["outcome"].get("reason_ar")} for e in evals if e["outcome"].get("state") == "cannot_measure"],
          "manual": [{"id": e["id"], "title": e["title"]} for e in evals if e["outcome"].get("manual")],
          "legacy": sum(1 for e in evals if e.get("legacy")), "data_end": ctx["data_end"].isoformat() if ctx.get("data_end") else None,
          "rule_ar": "بلا بيانات KPI لا نقول إن القرار نجح أو فشل · القيم اليدوية موسومة · القرارات القديمة تُقرأ من سجل القرارات دون تعديلها"}
    return {
        "has_data": bool(evals), "version": MODEL, "as_of": today.isoformat(), "currency": currency, "scope": scope_of(viewer),
        "overview": overview, "decisions": evals,
        "lists": {"open": [e["id"] for e in evals if e["status"] in ("approved", "in_progress")], "pending": [e["id"] for e in evals if e["workflow"] in ("pending_approval", "under_review", "draft")],
                  "late": [e["id"] for e in sorted(delays, key=lambda x: (-int(x["delay"]["escalate"]), -x["delay"]["days"]))],
                  "completed": [e["id"] for e in evals if e["workflow"] in ("completed", "measured")],
                  "high_impact": [e["id"] for e in sorted([e for e in evals if e["high_impact"] and e["workflow"] not in ("cancelled", "rejected")], key=lambda x: -(x.get("expected_impact") or 0))]},
        "by_owner": _group(evals, "owner"), "by_branch": _group(evals, "branch"), "by_department": _group(evals, "department", G.DEPARTMENTS),
        "financial": {"rows": fin, "expected_total": exp_total, "actual_total": act_total, "variance_total": round(act_total - exp_measured, 2) if measured else None,
                      "note_ar": "الفعلي يُحسب من المؤشر (بعد − قبل) أو من مركز التسرب أو قيمة يدوية موسومة — مقارنة زمنية وليست إثبات سببية"},
        "effectiveness": {"by_category": eff_hist("category", cat_ar), "by_source": eff_hist("source", src_ar), "by_department": eff_hist("department", G.DEPARTMENTS),
                          "rule_ar": EFF_RULE_AR},
        "recurring": rec, "delays": {"reasons": sorted(reasons.values(), key=lambda x: -x["count"]), "items": [e["id"] for e in delays]},
        "alerts": alerts, "escalations": [a for a in alerts if a.get("escalate")], "heatmap": heat,
        "heat_ar": {"red": "🔴 أثر مرتفع + إلحاح مرتفع", "orange": "🟠 أثر مرتفع + إلحاح منخفض", "yellow": "🟡 أثر منخفض + إلحاح مرتفع", "green": "🟢 أثر منخفض + إلحاح منخفض"},
        "trail": trail[:400], "dependency_issues": dependency_issues(dmap),
        "sector": {"sector": sec, "categories": [{"key": c, "ar": SECTOR_TERMS.get(sec, {}).get(c) or CATEGORIES[c][0], "kpi": CATEGORIES[c][1],
                                                  "kpi_ar": (G.metric_meta(CATEGORIES[c][1]) or {}).get("ar")} for c in SECTOR_DECISIONS.get(sec, SECTOR_DECISIONS["other"])],
                   "note_ar": "محرك قرارات واحد لكل القطاعات — القطاع يحدد الفئات المقترحة ومؤشراتها ومصطلحاتها فقط"},
        "catalog": {"categories": {k: {"ar": v[0], "kpi": v[1], "dept": v[2]} for k, v in CATEGORIES.items()}, "priorities": {k: v[0] for k, v in PRIORITY.items()},
                    "delay_reasons": DELAY_REASONS, "sources": {k: v[1] for k, v in SOURCES.items()}, "workflow": WF_AR,
                    "metrics": [{"code": k, "ar": v["ar"], "unit": v["unit"]} for k, v in G.METRICS.items()], "departments": G.DEPARTMENTS},
        "data_quality": dq, "can_create": can("create", {}, viewer),
        "methodology": {"engine": MODEL, "before_after_ar": f"قبل = متوسط آخر {BEFORE_MONTHS} أشهر مكتملة قبل شهر القرار · بعد = حتى {AFTER_MONTHS_MAX} أشهر مكتملة بعد شهر الإكمال · {CAUSAL_NOTE_AR}",
                        "result_ar": RESULT_RULE_AR, "effectiveness_ar": EFF_RULE_AR, "escalation_ar": f"تصعيد: قرار متأخر عالي الأثر (≥ {threshold:,.0f} أو أولوية مرتفعة) أو متأخر أكثر من {ESCALATE_LATE_DAYS} يوماً",
                        "status_ar": "المكتمل ≠ الناجح: مكتمل → بانتظار القياس → مُقاس"},
        "ai_questions": ["ما القرارات المتأخرة ذات التأثير الأكبر؟", "أي القرارات حققت أفضل نتيجة؟", "لماذا القرار لم يحقق النتيجة المتوقعة؟",
                         "هل هناك قرار متكرر لم يعالج السبب الجذري؟", "ما القرارات التي أثرت على الربحية؟", "ما القرارات التي يجب تصعيدها؟", "ما القرارات التي يمكن إغلاقها؟"],
        "signals": [{"id": f"dec-{e['id']}", "type": "risk", "code": f"decision:{e['id']}", "source_module": "decisions", "name_ar": f"قرار متأخر: {e['title']}",
                     "severity": "high" if e["delay"]["escalate"] else "medium", "dimension": e.get("branch"), "period": today.strftime("%Y-%m"),
                     "evidence": [e["delay"]["sentence_ar"], "السبب: " + e["delay"]["reason_ar"]], "suggested_action_ar": e["delay"]["escalate_ar"] or "تابع مع المسؤول",
                     "estimated_impact": {"value": e["delay"]["impact_at_risk"], "type_ar": "أثر متوقع معرّض للتأخير"} if e["delay"]["impact_at_risk"] else None,
                     "metric_id": (e.get("primary") or {}).get("metric"), "method": MODEL} for e in delays][:10],
        "disclaimer_ar": "نباه تتابع القرار وتقيس ما حدث بعده بمنهجية معلنة — المكتمل لا يعني الناجح، وقبل/بعد ليست إثبات سببية.",
    }


def ai_context(res):
    return {"overview": {k: res["overview"][k] for k in ("open", "pending_approval", "overdue", "completed", "measured", "expected_total", "actual_total", "achievement_pct")},
            "decisions": [{"title": e["title"], "status": e["status_ar"], "owner": e.get("owner"), "due": e.get("due"), "category": e["category_ar"], "source": e["source_ar"],
                           "expected": e.get("expected_impact"), "actual": e["outcome"].get("actual"), "variance": e["outcome"].get("variance"),
                           "result": e["outcome"].get("result_ar"), "effectiveness": e["effectiveness"]["score"],
                           "kpi": {k: (e.get("primary") or {}).get(k) for k in ("metric_ar", "before", "after", "current", "target")} if e.get("primary") else None,
                           "delay": {k: e["delay"][k] for k in ("days", "reason_ar", "escalate")} if e.get("delay") else None,
                           "side_effects": e["outcome"].get("mixed_note_ar"), "recurring": e.get("recurring"), "review": e["review"]} for e in res["decisions"][:30]],
            "recurring": [g["sentence_ar"] + ": " + "، ".join(g["titles"][:3]) for g in res["recurring"]],
            "effectiveness_by_category": res["effectiveness"]["by_category"][:8],
            "rule": "اشرح فقط من أرقام المحرك. لا تُصدر قراراً ولا تخترع أرقاماً. قبل/بعد ليست إثبات سببية. إن كان القياس غير ممكن فقل ذلك."}


def from_legacy(d, actions=None):
    """قراءة قرار قديم (CompanyDecision بلا طبقة 3.9) دون تعديله: open → معتمد/قيد التنفيذ · done → مكتمل · cancelled → ملغى."""
    acts = actions or []
    st = d.get("status") or "open"
    wf = "cancelled" if st == "cancelled" else "completed" if st == "done" else ("in_progress" if any(a.get("status") in ("in_progress", "completed") or (a.get("progress") or 0) > 0 for a in acts) else "approved")
    lt = d.get("linked_to") or ""
    dt = d.get("decision_type") or ""
    m = KPI_ALIAS.get((d.get("metric_id") or d.get("kpi") or "").strip().lower())
    src = dt if dt in SOURCES else ("recommendation" if d.get("source_signal") else "manual")
    goal_id = int(lt.split(":")[1]) if lt.startswith("goal:") and lt.split(":")[1].isdigit() else None
    risk_key = lt.split(":")[1].split("|")[0] if lt.startswith("risk:") else None
    deps = [int(x) for x in lt.split(",") if x.strip().isdigit()] if lt and not ":" in lt else []
    return {"workflow": wf, "source": src, "goal_id": goal_id, "risk_key": risk_key, "depends_on": deps,
            "kpis": [{"metric": m, "baseline": d.get("baseline_value"), "baseline_at": d.get("created_at")}] if m else [],
            "expected_impact": d.get("expected_impact_value"), "legacy": True}
