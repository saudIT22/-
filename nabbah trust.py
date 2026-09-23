"""
NABBAH 2.0 — Data Trust Layer (Phase 2.1)
طبقة موثوقية بيانات قابلة لإعادة الاستخدام.

تفحص ٦ أبعاد: Completeness, Validity, Consistency, Freshness,
Reconciliation, Duplicates.

كل نتيجة: status (pass/warning/fail), severity, affected_records,
explanation, suggested_fix, timestamp.

ملف مستقل — لا يلمس main.py. يطوّر Data Quality الموجود، لا يكرّره.
الإصدار: 1.0
"""
from datetime import datetime, timezone, timedelta
from decimal import Decimal

TRUST_VERSION = "1.0"


def _now():
    return datetime.now(timezone.utc).isoformat()


def _check(dimension, status, *, severity="low", affected=0, explanation="", fix=""):
    """يُغلّف نتيجة فحص واحدة بالبنية الإلزامية."""
    return {
        "dimension": dimension,
        "status": status,          # pass / warning / fail
        "severity": severity,      # low / medium / high / critical
        "affected_records": affected,
        "explanation": explanation,
        "suggested_fix": fix,
        "timestamp": _now(),
    }


def _to_num(v):
    if v is None:
        return None
    try:
        return float(str(v).replace(",", "").replace("%", "").strip())
    except (ValueError, TypeError):
        return None


# ═══════════════════════════════════════════════════════════
#  ١. Completeness — اكتمال الحقول المطلوبة
# ═══════════════════════════════════════════════════════════
def check_completeness(records, required_fields):
    """يفحص وجود الحقول المطلوبة في كل سجل."""
    if not records:
        return _check("completeness", "fail", severity="high", affected=0,
                      explanation="لا توجد سجلات للفحص.",
                      fix="أدخِل بيانات الفروع أولاً.")
    missing_count = 0
    for r in records:
        for f in required_fields:
            val = r.get(f) if isinstance(r, dict) else getattr(r, f, None)
            if val is None or (isinstance(val, str) and val.strip() == ""):
                missing_count += 1
    total_possible = len(records) * len(required_fields)
    if missing_count == 0:
        return _check("completeness", "pass", explanation="كل الحقول المطلوبة موجودة.")
    ratio = missing_count / total_possible if total_possible else 0
    sev = "critical" if ratio > 0.5 else ("high" if ratio > 0.25 else "medium")
    return _check("completeness", "fail" if ratio > 0.25 else "warning",
                  severity=sev, affected=missing_count,
                  explanation=f"{missing_count} حقل مطلوب مفقود من {total_possible}.",
                  fix="أكمل الحقول الناقصة لرفع دقة التحليل.")


# ═══════════════════════════════════════════════════════════
#  ٢. Validity — صحة القيم (سالبة، نوع، حدود)
# ═══════════════════════════════════════════════════════════
def check_validity(records, non_negative_fields, numeric_fields=None):
    """يفحص القيم غير الصحيحة:
    - سالبة في حقول موجبة → تحذير
    - غير رقمية في حقول رقمية → خطأ جودة (لا تُتجاهل)"""
    if not records:
        return _check("validity", "pass", explanation="لا سجلات للفحص.")
    invalid_negative = 0
    invalid_type = 0
    numeric_fields = numeric_fields or non_negative_fields
    for r in records:
        # فحص القيم غير الرقمية في الحقول الرقمية
        for f in numeric_fields:
            raw = r.get(f) if isinstance(r, dict) else getattr(r, f, None)
            if raw is not None and str(raw).strip() != "":
                if _to_num(raw) is None:
                    invalid_type += 1  # قيمة غير رقمية في حقل رقمي = خطأ
        # فحص السالب
        for f in non_negative_fields:
            val = _to_num(r.get(f) if isinstance(r, dict) else getattr(r, f, None))
            if val is not None and val < 0:
                invalid_negative += 1
    total_invalid = invalid_negative + invalid_type
    if total_invalid == 0:
        return _check("validity", "pass", explanation="كل القيم ضمن النطاق الصحيح ومن النوع الصحيح.")
    # القيم غير الرقمية = فشل (أخطر من السالب)
    if invalid_type > 0:
        return _check("validity", "fail", severity="high", affected=total_invalid,
                      explanation=f"{invalid_type} قيمة غير رقمية في حقول مالية + {invalid_negative} قيمة سالبة.",
                      fix="صحّح القيم غير الرقمية — لا يمكن حسابها.")
    return _check("validity", "warning", severity="medium", affected=invalid_negative,
                  explanation=f"{invalid_negative} قيمة سالبة في حقول يُتوقّع أن تكون موجبة.",
                  fix="تأكّد أن القيم السالبة مقصودة (مرتجعات) أو صحّحها.")


# ═══════════════════════════════════════════════════════════
#  ٣. Consistency — تطابق الإجماليات مع التفاصيل
# ═══════════════════════════════════════════════════════════
def check_consistency(total, parts, *, tolerance=Decimal("0.01"), label="الإجمالي"):
    """يفحص أن الإجمالي = مجموع التفاصيل (ضمن هامش).
    القيم الناقصة في التفاصيل تُوسم صراحة (لا تُعامَل كصفر صامت)."""
    t = _to_num(total)
    if t is None:
        return _check("consistency", "warning", severity="low",
                      explanation=f"{label} غير متوفّر للمطابقة.", fix="أدخِل الإجمالي.")
    # نفصل القيم المتوفّرة عن الناقصة
    valid_parts = [_to_num(p) for p in parts]
    missing = sum(1 for p in valid_parts if p is None)
    if missing > 0:
        return _check("consistency", "warning", severity="medium", affected=missing,
                      explanation=f"{missing} من بنود التفاصيل ناقصة — لا يمكن التأكد من مطابقة {label}.",
                      fix="أكمل بنود التفاصيل الناقصة قبل المطابقة.")
    parts_sum = sum(p for p in valid_parts if p is not None)
    diff = abs(t - parts_sum)
    if diff <= float(tolerance) * max(abs(t), 1):
        return _check("consistency", "pass",
                      explanation=f"{label} يطابق مجموع التفاصيل.")
    return _check("consistency", "warning", severity="medium", affected=1,
                  explanation=f"{label} ({t}) لا يطابق مجموع التفاصيل ({parts_sum}). الفرق {diff}.",
                  fix="راجع التفاصيل أو الإجمالي — قد يكون هناك بند مفقود.")


# ═══════════════════════════════════════════════════════════
#  ٤. Freshness — قدم البيانات
# ═══════════════════════════════════════════════════════════
def check_freshness(last_updated, *, stale_days=90):
    """يحذّر من البيانات القديمة."""
    if last_updated is None:
        return _check("freshness", "warning", severity="low",
                      explanation="تاريخ آخر تحديث غير معروف.",
                      fix="سجّل تاريخ تحديث البيانات.")
    try:
        if isinstance(last_updated, str):
            last = datetime.fromisoformat(last_updated.replace("Z", "+00:00"))
        else:
            last = last_updated
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - last).days
    except (ValueError, TypeError):
        return _check("freshness", "warning", severity="low",
                      explanation="تعذّر قراءة تاريخ التحديث.", fix="تحقّق من صيغة التاريخ.")
    if age_days <= stale_days:
        return _check("freshness", "pass",
                      explanation=f"البيانات حديثة (آخر تحديث قبل {age_days} يوم).")
    sev = "high" if age_days > 180 else "medium"
    return _check("freshness", "warning", severity=sev, affected=1,
                  explanation=f"البيانات قديمة — آخر تحديث قبل {age_days} يوم.",
                  fix="حدّث البيانات لتحليل يعكس الوضع الحالي.")


# ═══════════════════════════════════════════════════════════
#  ٥. Reconciliation — التسوية (المودع مقابل المبيعات)
# ═══════════════════════════════════════════════════════════
def check_reconciliation(recorded, expected, *, label="المبلغ", tolerance_pct=Decimal("5")):
    """يطابق قيمتين يُفترض تقاربهما (مثل المودع مقابل صافي المبيعات)."""
    r = _to_num(recorded)
    e = _to_num(expected)
    if r is None or e is None:
        return _check("reconciliation", "warning", severity="low",
                      explanation=f"بيانات ناقصة لتسوية {label}.", fix="أدخِل القيمتين للمطابقة.")
    if e == 0:
        return _check("reconciliation", "warning", severity="low",
                      explanation="القيمة المتوقّعة صفر — لا يمكن حساب نسبة الفرق.", fix="")
    diff_pct = abs(r - e) / abs(e) * 100
    if diff_pct <= float(tolerance_pct):
        return _check("reconciliation", "pass",
                      explanation=f"{label} متطابق ضمن الهامش المقبول.")
    sev = "high" if diff_pct > 20 else "medium"
    return _check("reconciliation", "warning", severity=sev, affected=1,
                  explanation=f"فرق {round(diff_pct,1)}% بين {label} المسجّل ({r}) والمتوقّع ({e}).",
                  fix="راجع أسباب الفرق — قد تكون مصروفات نقدية أو أخطاء تسجيل.")


# ═══════════════════════════════════════════════════════════
#  ٦. Duplicates — التكرار
# ═══════════════════════════════════════════════════════════
def check_duplicates(records, key_fields):
    """يكشف السجلات المكرّرة بناءً على حقول مفتاحية."""
    if not records:
        return _check("duplicates", "pass", explanation="لا سجلات للفحص.")
    seen = {}
    dupes = 0
    for r in records:
        key = tuple(str(r.get(f) if isinstance(r, dict) else getattr(r, f, None)) for f in key_fields)
        if key in seen:
            dupes += 1
        else:
            seen[key] = True
    if dupes == 0:
        return _check("duplicates", "pass", explanation="لا سجلات مكرّرة.")
    return _check("duplicates", "warning", severity="medium", affected=dupes,
                  explanation=f"{dupes} سجل مكرّر (نفس {', '.join(key_fields)}).",
                  fix="احذف التكرار لتجنّب تضخيم الأرقام.")


# ═══════════════════════════════════════════════════════════
#  التقرير الشامل + درجة الموثوقية
# ═══════════════════════════════════════════════════════════
def run_all_checks(checks: list):
    """يجمّع نتائج الفحوص ويحسب درجة موثوقية مرجّحة.
    الفشل الحرج (critical/high) يُخفّض الدرجة بقوة — لا متوسط بسيط يُخفيه."""
    if not checks:
        return {"overall_score": 0, "status": "fail", "checks": [], "timestamp": _now()}
    weights = {"pass": 100, "warning": 60, "fail": 0}
    base_score = sum(weights.get(c["status"], 0) for c in checks) / len(checks)

    # عقوبة الفشل الحرج: أي fail بخطورة عالية يضع سقفاً للدرجة
    has_critical_fail = any(c["status"] == "fail" and c["severity"] in ("critical", "high") for c in checks)
    has_any_fail = any(c["status"] == "fail" for c in checks)
    if has_critical_fail:
        score = min(base_score, 40)  # سقف 40 عند فشل مالي حرج
    elif has_any_fail:
        score = min(base_score, 60)  # سقف 60 عند أي فشل
    else:
        score = base_score

    overall_status = "pass" if score >= 80 else ("warning" if score >= 50 else "fail")
    main_causes = [
        {"dimension": c["dimension"], "severity": c["severity"],
         "explanation": c["explanation"], "fix": c["suggested_fix"]}
        for c in checks
        if c["status"] != "pass" and c["severity"] in ("high", "critical", "medium")
    ]
    return {
        "overall_score": round(score),
        "status": overall_status,
        "checks": checks,
        "main_causes": main_causes[:5],
        "has_critical_fail": has_critical_fail,
        "trust_version": TRUST_VERSION,
        "timestamp": _now(),
    }


def can_ai_recommend(trust_report):
    """🔴 الربط الفعلي: يمنع التوصية المالية القطعية عند فشل الجودة.
    يُرجع (allowed: bool, reason: str) — يستخدمه AI قبل أي توصية مالية."""
    if trust_report.get("has_critical_fail"):
        return (False, "جودة البيانات غير كافية (فشل حرج) — لا توصية مالية قطعية.")
    if trust_report.get("status") == "fail":
        return (False, "جودة البيانات منخفضة — التوصيات غير مؤكّدة.")
    if trust_report.get("status") == "warning":
        return (True, "توصية مشروطة — جودة البيانات متوسطة، راجع الأسباب.")
    return (True, "جودة البيانات كافية.")
