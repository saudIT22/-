"""
NABBAH 2.2 — AI Gateway + Data Quality Gate
STEP 8: عقد AI مُهيكل · STEP 9: بوابة جودة البيانات

المبدأ الحاكم: AI ليس مصدر الحقيقة المالية.
يستقبل نتائج مُتحقّقة من المحرّكات، ويُنتج تفسيراً مُهيكلاً فقط.
"""
import sys, os
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "phase21"))
sys.path.insert(0, _HERE)

from datetime import datetime, timezone

GATEWAY_VERSION = "1.0"


def _now():
    return datetime.now(timezone.utc).isoformat()


# ═══════════════════════════════════════════════════════════
#  STEP 9 — Data Quality Gate (ALLOW / QUALIFY / BLOCK)
# ═══════════════════════════════════════════════════════════
def evaluate_quality_gate(trust_report: dict):
    """يقرّر ما إذا كانت التوصية المالية مسموحة.

    ALLOW   — جودة عالية، توصية قطعية مسموحة
    QUALIFY — جودة متوسطة، توصية مشروطة بتنويه
    BLOCK   — جودة منخفضة/فاشلة، التوصية المالية محظورة
    """
    if not trust_report:
        return {
            "status": "BLOCK",
            "reason": "لا توجد معلومات جودة بيانات — لا يمكن إصدار توصية مالية.",
            "required_data": ["تشغيل فحوص جودة البيانات أولاً."],
            "allow_financial_recommendation": False,
            "gate_version": f"gate-v{GATEWAY_VERSION}", "timestamp": _now(),
        }

    score = trust_report.get("overall_score", 0)
    status_in = trust_report.get("status", "fail")
    has_critical = trust_report.get("has_critical_fail", False)
    causes = trust_report.get("main_causes", [])

    if has_critical or status_in == "fail" or score < 50:
        return {
            "status": "BLOCK",
            "reason": "جودة البيانات غير كافية — " + (
                causes[0]["explanation"] if causes else "فشل في فحوص أساسية."),
            "required_data": [c.get("fix", "") for c in causes[:3] if c.get("fix")],
            "allow_financial_recommendation": False,
            "quality_score": score,
            "gate_version": f"gate-v{GATEWAY_VERSION}", "timestamp": _now(),
        }
    if status_in == "warning" or score < 80:
        return {
            "status": "QUALIFY",
            "reason": "جودة البيانات متوسطة — التوصية مشروطة وغير قطعية.",
            "qualification": "هذه التوصية مبنية على بيانات ناقصة جزئياً — راجع الأسباب قبل التنفيذ.",
            "required_data": [c.get("fix", "") for c in causes[:3] if c.get("fix")],
            "allow_financial_recommendation": True,
            "quality_score": score,
            "gate_version": f"gate-v{GATEWAY_VERSION}", "timestamp": _now(),
        }
    return {
        "status": "ALLOW",
        "reason": "جودة البيانات كافية.",
        "allow_financial_recommendation": True,
        "quality_score": score,
        "gate_version": f"gate-v{GATEWAY_VERSION}", "timestamp": _now(),
    }


# ═══════════════════════════════════════════════════════════
#  STEP 8 — AI Gateway (عقد مُهيكل + مزوّد قابل للاستبدال)
# ═══════════════════════════════════════════════════════════
class AIProvider:
    """واجهة المزوّد — قابلة للاستبدال (Gemini/غيره).
    لا يُربط منطق نبّاه بمزوّد واحد."""
    name = "base"

    def generate(self, prompt: str, **kwargs) -> str:
        raise NotImplementedError


class MockProvider(AIProvider):
    """مزوّد وهمي للاختبارات الحتمية (لا استدعاء حيّ)."""
    name = "mock"

    def __init__(self, canned_response="تحليل تجريبي."):
        self.canned = canned_response
        self.last_prompt = None

    def generate(self, prompt: str, **kwargs) -> str:
        self.last_prompt = prompt
        return self.canned


class GeminiProvider(AIProvider):
    """غلاف للمزوّد الحالي (company_gemini في main.py).
    يُمرَّر كدالة — لا استيراد مباشر (يتجنّب التبعية الدائرية)."""
    name = "gemini"

    def __init__(self, gemini_fn):
        self.gemini_fn = gemini_fn

    def generate(self, prompt: str, **kwargs) -> str:
        return self.gemini_fn(prompt, kwargs.get("company"), lang=kwargs.get("lang", "ar"))


def build_ai_context(*, kpis=None, variance=None, drivers=None, root_cause=None,
                     impact=None, trust_report=None, company_name=None, period=None):
    """يبني سياق AI من نتائج المحرّكات المُتحقّقة فقط.
    الأرقام تأتي جاهزة — AI لا يحسبها."""
    ctx = {
        "company": company_name, "period": period,
        "verified_metrics": {}, "variance": None, "drivers": [],
        "root_cause": None, "financial_impact": None,
        "data_quality": None,
    }
    if kpis:
        for mid, k in kpis.items():
            if k.get("has_data"):
                ctx["verified_metrics"][mid] = {
                    "name": k.get("name"), "value": k.get("value"),
                    "unit": k.get("unit"), "currency": k.get("currency"),
                    "source": k.get("source"), "calculation": k.get("formula"),
                    "quality": k.get("data_quality"),
                }
    if variance:
        ctx["variance"] = variance
    if drivers:
        ctx["drivers"] = [
            {"name": d["driver_name"], "change_pct": d.get("percentage_change"),
             "contribution": d.get("contribution"), "type": d.get("type"),
             "confidence": d.get("confidence")}
            for d in drivers.get("drivers", [])
        ]
    if root_cause:
        ctx["root_cause"] = {
            "candidate": root_cause.get("root_cause", {}).get("candidate"),
            "status": root_cause.get("root_cause", {}).get("status"),
            "evidence_sufficient": root_cause.get("evidence_sufficient"),
            "data_gaps": root_cause.get("data_gaps", [])[:3],
        }
    if impact:
        ctx["financial_impact"] = {
            "total": impact.get("total_impact"), "currency": impact.get("currency"),
            "calculable": impact.get("calculable_count"),
        }
    if trust_report:
        ctx["data_quality"] = {
            "score": trust_report.get("overall_score"),
            "status": trust_report.get("status"),
            "main_causes": [c.get("explanation") for c in trust_report.get("main_causes", [])[:3]],
        }
    return ctx


AI_CONTRACT_INSTRUCTION = """أنت محلّل تنفيذي في منصة نبّاه.

قواعد إلزامية:
1. كل الأرقام أمامك محسوبة مسبقاً ومُتحقّق منها — لا تحسب رقماً بنفسك ولا تخترع رقماً غير موجود.
2. لا تدّعِ سبباً غير مدعوم بالأدلة المرفقة.
3. عند نقص البيانات، اذكر ما ينقص ولماذا يهم — لا تملأ الفراغ بتخمين.
4. افصل إجابتك إلى الأقسام التالية حصراً:

FACTS: الحقائق المحسوبة (من الأرقام المرفقة فقط)
INTERPRETATION: قراءتك لما تعنيه الأرقام
HYPOTHESES: فرضيات محتملة — موسومة بوضوح أنها غير مؤكّدة
RECOMMENDATIONS: توصيات عملية
DATA GAPS: البيانات الناقصة وأثرها

5. إن كانت جودة البيانات منخفضة، صرّح بذلك ولا تعطِ توصية مالية قطعية."""


def request_ai_analysis(provider: AIProvider, context: dict, question: str = "",
                        *, trust_report=None, lang="ar", company=None):
    """الطلب المركزي للتحليل — يطبّق البوابة والعقد.

    يُرجع استجابة مُهيكلة + حالة البوابة. لا يسمح بتجاوز الجودة.
    """
    gate = evaluate_quality_gate(trust_report) if trust_report else {
        "status": "QUALIFY", "reason": "لم تُفحص جودة البيانات.",
        "allow_financial_recommendation": True,
        "gate_version": f"gate-v{GATEWAY_VERSION}", "timestamp": _now(),
    }

    # البوابة تحظر → لا استدعاء AI للتوصيات المالية
    if gate["status"] == "BLOCK":
        return {
            "gate": gate,
            "ai_called": False,
            "response": {
                "FACTS": [],
                "INTERPRETATION": [],
                "HYPOTHESES": [],
                "RECOMMENDATIONS": [],
                "DATA_GAPS": gate.get("required_data", []),
            },
            "message": gate["reason"],
            "provider": provider.name if provider else None,
            "gateway_version": f"gateway-v{GATEWAY_VERSION}",
            "timestamp": _now(),
        }

    # نبني الموجّه من السياق المُتحقّق
    import json
    prompt = (AI_CONTRACT_INSTRUCTION + "\n\n"
              + "البيانات المُتحقّقة:\n"
              + json.dumps(context, ensure_ascii=False, indent=2)[:6000]
              + (f"\n\nالسؤال: {question}" if question else ""))
    if gate["status"] == "QUALIFY":
        prompt += f"\n\n⚠️ تنويه إلزامي: {gate.get('qualification','جودة البيانات متوسطة — التوصيات غير قطعية.')}"

    raw = provider.generate(prompt, company=company, lang=lang)
    structured = parse_ai_response(raw)

    return {
        "gate": gate,
        "ai_called": True,
        "response": structured,
        "raw_length": len(raw or ""),
        "provider": provider.name,
        "gateway_version": f"gateway-v{GATEWAY_VERSION}",
        "timestamp": _now(),
    }


def parse_ai_response(raw: str):
    """يفكّك استجابة AI إلى الأقسام الخمسة.
    ما لا يُطابق قسماً يبقى في INTERPRETATION (لا يُهمَل)."""
    sections = {"FACTS": [], "INTERPRETATION": [], "HYPOTHESES": [],
                "RECOMMENDATIONS": [], "DATA_GAPS": []}
    if not raw:
        return sections
    markers = {
        "FACTS": ["FACTS", "الحقائق"],
        "INTERPRETATION": ["INTERPRETATION", "التفسير", "القراءة"],
        "HYPOTHESES": ["HYPOTHESES", "الفرضيات", "HYPOTHESIS"],
        "RECOMMENDATIONS": ["RECOMMENDATIONS", "التوصيات"],
        "DATA_GAPS": ["DATA GAPS", "DATA_GAPS", "البيانات الناقصة", "الفجوات"],
    }
    current = "INTERPRETATION"
    for line in raw.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        matched = None
        for sec, keys in markers.items():
            if any(stripped.upper().startswith(k.upper()) or stripped.startswith(k) for k in keys):
                matched = sec
                break
        if matched:
            current = matched
            # نزيل العنوان من السطر
            rest = stripped
            for k in markers[matched]:
                rest = rest.replace(k, "").replace(k.upper(), "")
            rest = rest.lstrip(":：- ").strip()
            if rest:
                sections[current].append(rest)
        else:
            sections[current].append(stripped)
    return sections
