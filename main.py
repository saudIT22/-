import os
import time
import re
from dotenv import load_dotenv
from google import genai
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles      # ← جديد ١
from fastapi.responses import FileResponse       # ← جديد ٢
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
from sqlmodel import SQLModel, Field, create_engine, Session
from datetime import datetime

load_dotenv()
ai_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")   # ← جديد ٣

class SalesData(BaseModel):
    restaurant: str
    sales_today: float
    sales_yesterday: float
    orders: int
    items_count: int
    top_item: str
    top_item_2: Optional[str] = ""
    top_item_3: Optional[str] = ""
    hourly_orders: Optional[str] = ""
    peak_hours: str
    revenue: float
    expenses: float
    notes: Optional[str] = ""
    plan: Optional[str] = "executive"

class Entry(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    restaurant: str
    sales_today: float
    sales_yesterday: float
    orders: int
    items_count: int
    top_item: str
    top_item_2: str = ""
    top_item_3: str = ""
    hourly_orders: str = ""
    peak_hours: str
    revenue: float
    expenses: float
    notes: str = ""
    plan: str = "executive"
    change_percent: float = 0
    profit: float = 0
    margin: float = 0
    health_score: int = 0
    risk_score: int = 0
    opportunity_score: int = 0
    data_quality: int = 0
    covers_expenses: int = 0
    safety_margin: float = 0
    top_alert: str = ""
    top_decision: str = ""
    top_opportunity: str = ""
    smart_message: str = ""
    created_at: datetime = Field(default_factory=datetime.now)

engine = create_engine("sqlite:///nabbah.db")
SQLModel.metadata.create_all(engine)


def extract_exec(text):
    alert = decision = opportunity = ""
    m = re.search(r"===NABBAH_EXEC===(.*?)===END===", text, re.DOTALL)
    if m:
        block = m.group(1)
        for line in block.splitlines():
            line = line.strip()
            if line.startswith("ALERT:"):
                alert = line[len("ALERT:"):].strip()
            elif line.startswith("DECISION:"):
                decision = line[len("DECISION:"):].strip()
            elif line.startswith("OPPORTUNITY:"):
                opportunity = line[len("OPPORTUNITY:"):].strip()
        text = (text[:m.start()] + text[m.end():]).strip()
    return text, alert, decision, opportunity


def check_sanity(data, margin, avg_ticket, expense_ratio):
    flags = []
    if data.revenue <= 0:
        flags.append("الإيرادات صفر أو بالسالب — رقم غير منطقي")
    if data.expenses < 0:
        flags.append("المصروفات بالسالب — رقم غير منطقي")
    if margin > 60:
        flags.append(f"هامش الربح {margin}% مرتفع جداً — قد تكون المصروفات غير مكتملة")
    if margin < -50:
        flags.append(f"الخسارة كبيرة جداً (هامش {margin}%) — تأكد من صحة الأرقام")
    if data.revenue > 0 and data.sales_today > data.revenue * 1.5:
        flags.append("مبيعات اليوم أكبر من إجمالي الإيرادات — قد تكون الأرقام مختلطة")
    if avg_ticket > 5000:
        flags.append(f"متوسط الفاتورة {avg_ticket} ريال مرتفع جداً")
    if expense_ratio > 0 and expense_ratio < 20:
        flags.append(f"المصروفات منخفضة جداً ({expense_ratio}%) — قد تكون غير مكتملة")
    return flags


def build_forecast(history_revenues, current_revenue):
    all_rev = history_revenues + [current_revenue]
    if len(all_rev) < 2:
        return None
    rates = []
    for i in range(1, len(all_rev)):
        prev = all_rev[i-1]
        if prev > 0:
            rates.append((all_rev[i] - prev) / prev)
    if not rates:
        return None
    avg_rate = sum(rates) / len(rates)
    conservative_rate = avg_rate - abs(avg_rate) * 0.5
    optimistic_rate = avg_rate + abs(avg_rate) * 0.5
    def project(rate, months):
        return round(current_revenue * ((1 + rate) ** months), 0)
    return {
        "avg_rate": round(avg_rate * 100, 1),
        "next_month_cons": project(conservative_rate, 1),
        "next_month_opt": project(optimistic_rate, 1),
        "m3_cons": project(conservative_rate, 3),
        "m3_opt": project(optimistic_rate, 3),
        "m6_cons": project(conservative_rate, 6),
        "m6_opt": project(optimistic_rate, 6),
    }


def get_sections(plan):
    if plan == "basic":
        return """## ⚡ الملخص السريع (30 ثانية)
في ٤-٥ أسطر مختصرة: الحالة العامة ({level} - {health_score}/100) | أهم مشكلة واحدة | أهم فرصة واحدة | أول خطوة الآن.

## 💰 المؤشرات المالية الأساسية
الإيرادات، المصروفات، صافي الربح، هامش الربح — مع تعليق خبير قصير على كل رقم.

## 🎯 تغطية المصروفات
اشرح حالة التغطية وهامش الأمان، واعرض المعادلة بوضوح.

## ✅ القرار التنفيذي النهائي
٥ أسطر: الحالة؟ أكبر مشكلة؟ أكبر فرصة؟ أول قرار؟ العائد المتوقع؟"""

    elif plan == "pro":
        return """## ⚡ الملخص السريع (30 ثانية)
في ٤-٥ أسطر مختصرة: الحالة العامة ({level} - {health_score}/100) | المشكلة رقم ١ | المشكلة رقم ٢ | الفرصة رقم ١ | أول خطوة الآن.

---

## 📊 التفاصيل الكاملة

### 💰 المؤشرات المالية
الإيرادات، المصروفات، الربح، الهامش، متوسط الفاتورة — مع تعليق خبير قصير.

### 🎯 تغطية المصروفات
اشرح حالة التغطية وهامش الأمان، واعرض المعادلة بوضوح.

### 🔍 المشكلات الرئيسية وحلولها
لكل مشكلة: الوصف | الخطورة (🔴/🟡/🟢) | التأثير المالي | أكثر من حل | نسبة الثقة (%).

### 🧩 تحليل الأسباب الجذرية
لكل مشكلة: السبب الجذري + المؤشر الداعم + نسبة الثقة.

### 💵 تقدير الأثر المالي
مرتبة من الأعلى أثراً.

### 🎯 ترتيب الأولويات
افعل الآن | افعل لاحقاً.

### 📈 المؤشرات الواجب مراقبتها (KPIs)

### 🧮 المؤشرات الذكية
- صحة المنشأة: {health_score}/100 ({level}) — لماذا؟
- المخاطر: {risk_score}/100 — لماذا؟
- الفرص: {opportunity_score}/100 — لماذا؟

### 📋 جودة البيانات: {data_quality}/100 ({quality_note})

### ✅ القرار التنفيذي النهائي
٥ أسطر: الحالة؟ أكبر خطر؟ أكبر فرصة؟ أول قرار؟ العائد المتوقع؟"""

    else:
        return """## ⚡ الملخص السريع (30 ثانية)
في ٤-٥ أسطر مختصرة: الحالة العامة ({level} - {health_score}/100) | المشكلة رقم ١ | المشكلة رقم ٢ | الفرصة رقم ١ | أول خطوة الآن.

---

## 📊 التفاصيل الكاملة

### 💰 المؤشرات المالية
الإيرادات، المصروفات، الربح، الهامش، متوسط الفاتورة — مع تعليق خبير قصير.

### 🎯 تغطية المصروفات
اشرح حالة التغطية وهامش الأمان، واعرض المعادلة بوضوح.

### 🔍 المشكلات الرئيسية وحلولها
لكل مشكلة: الوصف | الخطورة (🔴/🟡/🟢) | التأثير المالي | أكثر من حل | نسبة الثقة (%).

### 🧩 تحليل الأسباب الجذرية
لكل مشكلة: السبب الجذري + المؤشر الداعم + نسبة الثقة.

### 💵 تقدير الأثر المالي
مرتبة من الأعلى أثراً.

### 🎯 ترتيب الأولويات
افعل الآن | افعل لاحقاً.

### 📅 خطة تنفيذية ٣٠-٦٠-٩٠ يوم

### 📈 المؤشرات الواجب مراقبتها (KPIs)

### 🔮 التوقعات المستقبلية
استخدم التوقعات الرقمية المعطاة مع نسبة ثقة واضحة.

### 📉 تحليل المخاطر
مخاطر حرجة / متوسطة / منخفضة + أثر + نسبة ثقة.

### 💡 الفرص المخفية

### 🧮 المؤشرات الذكية
- صحة المنشأة: {health_score}/100 ({level}) — لماذا؟
- المخاطر: {risk_score}/100 — لماذا؟
- الفرص: {opportunity_score}/100 — لماذا؟

### 📋 جودة البيانات: {data_quality}/100 ({quality_note})

### ✅ القرار التنفيذي النهائي
٥ أسطر: الحالة؟ أكبر خطر؟ أكبر فرصة؟ أول قرار؟ العائد المتوقع؟

### 🎚️ مستوى الثقة الإجمالي
نسبة % + سبب أي نقص + ما الذي يرفعها."""


@app.get("/")                          # ← جديد ٤
def home():
    return FileResponse("static/index.html")


@app.post("/analyze")
def analyze(data: SalesData):
    change = data.sales_today - data.sales_yesterday
    percent = round((change / data.sales_yesterday) * 100, 1) if data.sales_yesterday > 0 else 0

    profit = data.revenue - data.expenses
    margin = round((profit / data.revenue) * 100, 1) if data.revenue > 0 else 0
    avg_ticket = round(data.sales_today / data.orders, 1) if data.orders > 0 else 0
    profit_per_order = round(profit / data.orders, 1) if data.orders > 0 else 0
    expense_ratio = round((data.expenses / data.revenue) * 100, 1) if data.revenue > 0 else 0

    covers_expenses = 1 if data.revenue >= data.expenses else 0
    safety_margin = round(((data.revenue - data.expenses) / data.expenses) * 100, 1) if data.expenses > 0 else 0

    save_10 = round(data.expenses * 0.10, 0)
    save_10_year = round(save_10 * 12, 0)
    sales_up_15 = round(data.sales_today * 0.15, 0)
    ticket_up_8 = round(avg_ticket * 0.08 * data.orders, 0)

    with Session(engine) as s:
        all_entries = s.query(Entry).all()
        history_count = len(all_entries)
        history_revenues = [e.revenue for e in all_entries]

    forecast = build_forecast(history_revenues, data.revenue)
    if forecast:
        forecast_text = f"""بناءً على {history_count + 1} إدخالات، متوسط معدل نمو الإيرادات: {forecast['avg_rate']}%.
- الشهر القادم: بين {forecast['next_month_cons']} (متحفظ) و {forecast['next_month_opt']} (متفائل) ريال
- بعد ٣ أشهر: بين {forecast['m3_cons']} و {forecast['m3_opt']} ريال
- بعد ٦ أشهر: بين {forecast['m6_cons']} و {forecast['m6_opt']} ريال"""
    else:
        forecast_text = "لا تتوفر بيانات تاريخية كافية للتوقع (يحتاج إدخالين أو أكثر)."

    sanity_flags = check_sanity(data, margin, avg_ticket, expense_ratio)
    sanity_text = ("⚠️ ملاحظات على جودة المدخلات:\n- " + "\n- ".join(sanity_flags)) if sanity_flags else "✅ المدخلات تبدو منطقية ومتسقة."

    health = 0
    if margin >= 25: health += 40
    elif margin >= 15: health += 32
    elif margin >= 10: health += 24
    elif margin >= 5: health += 14
    elif margin > 0: health += 6
    if percent >= 10: health += 30
    elif percent >= 0: health += 22
    elif percent >= -10: health += 12
    elif percent >= -25: health += 5
    if profit > 0: health += 30
    health_score = min(health, 100)

    risk = 0
    if profit < 0: risk += 40
    elif margin < 5: risk += 25
    elif margin < 10: risk += 12
    if expense_ratio >= 90: risk += 30
    elif expense_ratio >= 80: risk += 18
    elif expense_ratio >= 70: risk += 8
    if percent <= -25: risk += 30
    elif percent <= -10: risk += 18
    elif percent < 0: risk += 8
    risk_score = min(risk, 100)

    opportunity = 15
    if 0 < margin < 10: opportunity += 25
    elif 10 <= margin < 20: opportunity += 15
    if expense_ratio >= 80: opportunity += 30
    elif expense_ratio >= 70: opportunity += 20
    elif expense_ratio >= 60: opportunity += 10
    if percent < 0: opportunity += 25
    else: opportunity += 15
    opportunity_score = min(opportunity, 100)

    quality = 100
    if data.sales_yesterday <= 0: quality -= 15
    if data.orders <= 0: quality -= 15
    if data.items_count <= 0: quality -= 10
    if not data.top_item.strip(): quality -= 5
    if not data.peak_hours.strip(): quality -= 5
    if data.revenue <= 0: quality -= 20
    if data.expenses <= 0: quality -= 20
    if not data.notes or len(data.notes.strip()) < 3: quality -= 5
    if history_count < 2: quality -= 10
    data_quality = max(quality, 0)

    if data_quality >= 85: quality_note = "بيانات شبه مكتملة — دقة عالية"
    elif data_quality >= 60: quality_note = "بيانات جيدة مع بعض النقص"
    else: quality_note = "بيانات ناقصة — الدقة محدودة"

    if health_score >= 90: level = "ممتاز"; icon = "🟢"
    elif health_score >= 75: level = "جيد"; icon = "🟢"
    elif health_score >= 60: level = "تنبيه"; icon = "🟡"
    else: level = "خطر"; icon = "🔴"

    be_text = (f"الإيرادات ({data.revenue} ريال) تغطّي المصروفات ({data.expenses} ريال) — هامش أمان {safety_margin}%."
               if covers_expenses else
               f"الإيرادات ({data.revenue} ريال) لا تغطّي المصروفات ({data.expenses} ريال) — عجز {abs(safety_margin)}%.")

    top_items_parts = [data.top_item]
    if data.top_item_2 and data.top_item_2.strip(): top_items_parts.append(data.top_item_2.strip())
    if data.top_item_3 and data.top_item_3.strip(): top_items_parts.append(data.top_item_3.strip())
    top_items_str = " | ".join(top_items_parts)

    plan = data.plan if data.plan in ("basic", "pro", "executive") else "executive"
    sections = get_sections(plan).format(
        level=level, health_score=health_score, risk_score=risk_score,
        opportunity_score=opportunity_score, data_quality=data_quality, quality_note=quality_note
    )
    plan_names = {"basic": "الأساسية", "pro": "الاحترافية", "executive": "التنفيذية"}

    prompt = f"""أنت "نبّاه"، مستشار أعمال تنفيذي بخبرة ١٥ عاماً. مهمتك اكتشاف المشكلات الحقيقية والفرص الخفية.

# الباقة: {plan_names[plan]}

# بيانات منشأة "{data.restaurant}":
- مبيعات اليوم: {data.sales_today} | أمس: {data.sales_yesterday} | التغير: {percent}%
- الطلبات: {data.orders} | الأصناف: {data.items_count} | متوسط الفاتورة: {avg_ticket} ريال
- الأصناف الأكثر مبيعاً: {top_items_str}
- أوقات الذروة: {data.peak_hours}
- توزيع الطلبات بالوقت: {data.hourly_orders if data.hourly_orders else 'لم يُدخل'}
- الإيرادات: {data.revenue} | المصروفات: {data.expenses} ({expense_ratio}%)
- صافي الربح: {profit} | الهامش: {margin}% | الربح/طلب: {profit_per_order}
- ملاحظات: {data.notes}
- إدخالات تاريخية: {history_count}

# جودة المدخلات: {sanity_text}
# تغطية المصروفات: {be_text}
# التوقعات: {forecast_text}

# الدرجات (لا تغيّرها):
- الصحة: {health_score}/100 ({level})
- المخاطر: {risk_score}/100
- الفرص: {opportunity_score}/100
- جودة البيانات: {data_quality}/100

# تقديرات جاهزة:
- توفير 10% مصروفات: {save_10} شهرياً / {save_10_year} سنوياً
- زيادة مبيعات 15%: {sales_up_15}
- رفع الفاتورة 8%: {ticket_up_8}

# قواعد صارمة:
1. ✅ حقيقة | ⚠️ فرضية | ❌ ناقص
2. لا تخترع أرقاماً
3. الدرجات ثابتة — اشرحها فقط
4. نسبة ثقة بعد كل استنتاج
5. لا مبالغة
6. اكتب فقط أقسام الباقة المحددة

===NABBAH_EXEC===
ALERT: (أهم تنبيه — جملة واحدة بالأرقام)
DECISION: (أهم قرار — جملة واحدة)
OPPORTUNITY: (أهم فرصة — جملة واحدة)
===END===

{sections}"""

    models = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-flash-latest"]
    response = None
    for model_name in models:
        for attempt in range(2):
            try:
                response = ai_client.models.generate_content(model=model_name, contents=prompt)
                break
            except Exception:
                time.sleep(2)
        if response is not None:
            break
    if response is None:
        raise HTTPException(status_code=503, detail="الخدمة مزدحمة، حاول بعد دقيقة")

    clean_text, top_alert, top_decision, top_opportunity = extract_exec(response.text)

    if not top_alert: top_alert = "راجع المؤشرات المالية في التقرير" if level != "خطر" else "المنشأة في منطقة خطر — راجع التقرير فوراً"
    if not top_decision: top_decision = "اطّلع على قسم القرار التنفيذي"
    if not top_opportunity: top_opportunity = "راجع قسم الفرص في التقرير"

    entry = Entry(
        restaurant=data.restaurant, sales_today=data.sales_today, sales_yesterday=data.sales_yesterday,
        orders=data.orders, items_count=data.items_count, top_item=data.top_item,
        top_item_2=data.top_item_2 or "", top_item_3=data.top_item_3 or "",
        hourly_orders=data.hourly_orders or "", peak_hours=data.peak_hours,
        revenue=data.revenue, expenses=data.expenses, notes=data.notes, plan=plan,
        change_percent=percent, profit=profit, margin=margin,
        health_score=health_score, risk_score=risk_score,
        opportunity_score=opportunity_score, data_quality=data_quality,
        covers_expenses=covers_expenses, safety_margin=safety_margin,
        top_alert=top_alert, top_decision=top_decision, top_opportunity=top_opportunity,
        smart_message=clean_text
    )
    with Session(engine) as session:
        session.add(entry)
        session.commit()

    return {
        "restaurant": data.restaurant, "change_percent": percent, "avg_ticket": avg_ticket,
        "profit": profit, "margin": margin, "profit_per_order": profit_per_order,
        "health_score": health_score, "risk_score": risk_score,
        "opportunity_score": opportunity_score, "data_quality": data_quality,
        "covers_expenses": covers_expenses, "safety_margin": safety_margin, "plan": plan,
        "top_alert": top_alert, "top_decision": top_decision, "top_opportunity": top_opportunity,
        "level": level, "icon": icon, "smart_message": clean_text
    }


@app.get("/history")
def history():
    with Session(engine) as session:
        return session.query(Entry).all()