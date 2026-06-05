import os
import time
import re
import jwt
import bcrypt
from dotenv import load_dotenv
from google import genai
from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
from sqlmodel import SQLModel, Field, create_engine, Session, select
from datetime import datetime, timedelta

load_dotenv()
ai_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

# مفتاح سري لتوقيع رموز الدخول (يُضبط في Railway Variables)
SECRET_KEY = os.getenv("SECRET_KEY", "nabbah-dev-secret-change-me")
TOKEN_DAYS = 30  # مدة صلاحية الدخول

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

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

class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    email: str = Field(index=True, unique=True)
    password_hash: str
    business_name: str = ""
    phone: str = ""
    plan: str = ""                      # فارغ = ما اشترك بعد | "trial" = في تجربة | "basic/pro/executive" = مشترك
    is_active: int = 0                  # 0 = غير مفعّل، 1 = مفعّل
    trial_used: int = 0                 # 0 = ما استخدم تجربة، 1 = استخدمها
    subscription_start: Optional[datetime] = None
    subscription_end: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.now)

class Entry(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: Optional[int] = Field(default=None, index=True)   # صاحب التحليل
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


# ===== أدوات الأمان: كلمات المرور والرموز =====
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False

def create_token(user_id: int) -> str:
    payload = {
        "user_id": user_id,
        "exp": datetime.utcnow() + timedelta(days=TOKEN_DAYS)
    }
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")

def get_current_user(authorization: str = Header(default="")) -> User:
    """يتحقق من رمز الدخول ويُرجع المستخدم، وإلا يرفض الطلب."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="يجب تسجيل الدخول")
    token = authorization[len("Bearer "):]
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        user_id = payload.get("user_id")
    except Exception:
        raise HTTPException(status_code=401, detail="انتهت الجلسة، سجّل الدخول من جديد")
    with Session(engine) as s:
        user = s.get(User, user_id)
        if not user:
            raise HTTPException(status_code=401, detail="المستخدم غير موجود")
        return user


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
        flags.append(f"هامش الربح {margin}% مرتفع جداً وغير معتاد — قد تكون المصروفات غير مكتملة")
    if margin < -50:
        flags.append(f"الخسارة كبيرة جداً (هامش {margin}%) — تأكد من صحة الإيرادات والمصروفات")
    if data.revenue > 0 and data.sales_today > data.revenue * 1.5:
        flags.append("مبيعات اليوم أكبر من إجمالي الإيرادات — قد تكون الأرقام مختلطة")
    if avg_ticket > 5000:
        flags.append(f"متوسط الفاتورة {avg_ticket} ريال مرتفع جداً — تأكد من المبيعات وعدد الطلبات")
    if expense_ratio > 0 and expense_ratio < 20:
        flags.append(f"المصروفات منخفضة جداً ({expense_ratio}% من الإيرادات) — قد تكون غير مكتملة")
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
اشرح حالة التغطية وهامش الأمان، واعرض المعادلة بوضوح ليثق المالك بالرقم.

## ✅ القرار التنفيذي النهائي
٥ أسطر: الحالة؟ أكبر مشكلة؟ أكبر فرصة؟ أول قرار؟ العائد المتوقع؟"""

    elif plan == "pro":
        return """## ⚡ الملخص السريع (30 ثانية)
في ٤-٥ أسطر مختصرة: الحالة العامة ({level} - {health_score}/100) | المشكلة رقم ١ | المشكلة رقم ٢ | الفرصة رقم ١ | أول خطوة الآن.

---

## 📊 التفاصيل الكاملة

### 💰 المؤشرات المالية
الإيرادات، المصروفات، الربح، الهامش، متوسط الفاتورة — مع تعليق خبير قصير على كل رقم.

### 🎯 تغطية المصروفات
اشرح حالة التغطية وهامش الأمان، واعرض المعادلة بوضوح.

### 🔍 المشكلات الرئيسية وحلولها
لكل مشكلة: الوصف | الخطورة (🔴/🟡/🟢) | التأثير المالي | **أكثر من حل** | نسبة الثقة (%).

### 🧩 تحليل الأسباب الجذرية
لكل مشكلة: السبب الجذري (إن توفرت بيانات) + المؤشر الداعم + نسبة الثقة.

### 💵 تقدير الأثر المالي
استخدم التقديرات الجاهزة (شهري وسنوي)، مرتبة من الأعلى أثراً.

### 🎯 ترتيب الأولويات
**افعل الآن** | **افعل لاحقاً**.

### 📈 المؤشرات الواجب مراقبتها (KPIs)

### 🧮 المؤشرات الذكية (اشرح كل درجة، لا تغيّرها)
- صحة المنشأة: {health_score}/100 ({level}) — لماذا؟
- المخاطر: {risk_score}/100 — لماذا؟
- الفرص: {opportunity_score}/100 — لماذا؟

### 📋 جودة البيانات: {data_quality}/100 ({quality_note})

### ✅ القرار التنفيذي النهائي
٥ أسطر: الحالة؟ أكبر خطر؟ أكبر فرصة؟ أول قرار؟ العائد المتوقع؟"""

    else:  # executive
        return """## ⚡ الملخص السريع (30 ثانية)
في ٤-٥ أسطر مختصرة: الحالة العامة ({level} - {health_score}/100) | المشكلة رقم ١ | المشكلة رقم ٢ | الفرصة رقم ١ | أول خطوة الآن.

---

## 📊 التفاصيل الكاملة

### 💰 المؤشرات المالية
الإيرادات، المصروفات، الربح، الهامش، متوسط الفاتورة — مع تعليق خبير قصير على كل رقم.

### 🎯 تغطية المصروفات
اشرح حالة التغطية وهامش الأمان، واعرض المعادلة بوضوح ليثق المالك بالرقم.

### 🔍 المشكلات الرئيسية وحلولها
لكل مشكلة: الوصف | الخطورة (🔴/🟡/🟢) | التأثير المالي | **أكثر من حل** | نسبة الثقة (%).

### 🧩 تحليل الأسباب الجذرية
لكل مشكلة: السبب الجذري (إن توفرت بيانات) + المؤشر الداعم + نسبة الثقة.

### 💵 تقدير الأثر المالي
استخدم التقديرات الجاهزة (شهري وسنوي)، مرتبة من الأعلى أثراً.

### 🎯 ترتيب الأولويات
**افعل الآن** | **افعل لاحقاً**.

### 📅 خطة تنفيذية ٣٠-٦٠-٩٠ يوم

### 📈 المؤشرات الواجب مراقبتها (KPIs)

### 🔮 التوقعات المستقبلية
استخدم التوقعات الرقمية المعطاة. ضع نسبة ثقة، ومع بيانات قليلة اجعلها منخفضة صراحة.

### 📉 تحليل المخاطر
مخاطر حرجة / متوسطة / منخفضة + أثر كل خطر + نسبة ثقة. بلا مبالغة.

### 💡 الفرص المخفية

### 🧮 المؤشرات الذكية (اشرح كل درجة، لا تغيّرها)
- صحة المنشأة: {health_score}/100 ({level}) — لماذا؟
- المخاطر: {risk_score}/100 — لماذا؟
- الفرص: {opportunity_score}/100 — لماذا؟

### 📋 جودة البيانات: {data_quality}/100 ({quality_note})

### ✅ القرار التنفيذي النهائي
٥ أسطر: الحالة؟ أكبر خطر؟ أكبر فرصة؟ أول قرار؟ العائد المتوقع؟

### 🎚️ مستوى الثقة الإجمالي بالتحليل
نسبة % + سبب أي نقص + ما الذي يرفعها."""


@app.get("/")
def home():
    return FileResponse("index.html")

@app.get("/index.html")
def page_index():
    return FileResponse("index.html")

@app.get("/input.html")
def page_input():
    return FileResponse("input.html")

@app.get("/dashboard.html")
def page_dashboard():
    return FileResponse("dashboard.html")

@app.get("/charts.html")
def page_charts():
    return FileResponse("charts.html")

@app.get("/login.html")
def page_login():
    return FileResponse("login.html")

@app.get("/register.html")
def page_register():
    return FileResponse("register.html")


# ===== التسجيل والدخول =====
class RegisterData(BaseModel):
    name: str
    email: str
    password: str
    business_name: Optional[str] = ""
    phone: Optional[str] = ""

class LoginData(BaseModel):
    email: str
    password: str

@app.post("/register")
def register(data: RegisterData):
    email = data.email.strip().lower()
    if len(data.password) < 6:
        raise HTTPException(status_code=400, detail="كلمة المرور يجب أن تكون 6 أحرف على الأقل")
    with Session(engine) as s:
        existing = s.exec(select(User).where(User.email == email)).first()
        if existing:
            raise HTTPException(status_code=400, detail="هذا البريد مسجّل مسبقاً")
        user = User(
            name=data.name.strip(),
            email=email,
            password_hash=hash_password(data.password),
            business_name=(data.business_name or "").strip(),
            phone=(data.phone or "").strip(),
        )
        s.add(user)
        s.commit()
        s.refresh(user)
        token = create_token(user.id)
        return {"token": token, "name": user.name, "is_active": user.is_active, "plan": user.plan}

@app.post("/login")
def login(data: LoginData):
    email = data.email.strip().lower()
    with Session(engine) as s:
        user = s.exec(select(User).where(User.email == email)).first()
        if not user or not verify_password(data.password, user.password_hash):
            raise HTTPException(status_code=401, detail="البريد أو كلمة المرور غير صحيحة")
        token = create_token(user.id)
        return {"token": token, "name": user.name, "is_active": user.is_active, "plan": user.plan}

@app.get("/me")
def me(user: User = Depends(get_current_user)):
    """يرجّع بيانات المستخدم الحالي وحالة اشتراكه."""
    return {
        "name": user.name,
        "email": user.email,
        "business_name": user.business_name,
        "phone": user.phone,
        "plan": user.plan,
        "is_active": user.is_active,
        "subscription_start": user.subscription_start,
        "subscription_end": user.subscription_end,
    }


# ===== تفعيل يدوي مؤقت (يُحذف بعد ربط الدفع) =====
class ActivateData(BaseModel):
    email: str
    admin_key: str
    plan: str = "executive"  # basic / pro / executive
    days: int = 30

@app.post("/admin-activate")
def admin_activate(data: ActivateData):
    """تفعيل حساب يدوياً للاختبار. يتطلّب كلمة مرور المسؤول."""
    admin_secret = os.getenv("ADMIN_KEY", "")
    if not admin_secret or data.admin_key != admin_secret:
        raise HTTPException(status_code=403, detail="كلمة المسؤول غير صحيحة")
    email = data.email.strip().lower()
    with Session(engine) as s:
        user = s.exec(select(User).where(User.email == email)).first()
        if not user:
            raise HTTPException(status_code=404, detail="الحساب غير موجود")
        now = datetime.now()
        user.is_active = 1
        user.plan = data.plan
        user.subscription_start = now
        user.subscription_end = now + timedelta(days=data.days)
        s.add(user)
        s.commit()
        return {
            "ok": True,
            "email": user.email,
            "plan": user.plan,
            "active_until": user.subscription_end.isoformat()
        }


# ===== كود التجربة (تحليل واحد فقط) =====
class TrialData(BaseModel):
    code: str

@app.post("/redeem-trial")
def redeem_trial(data: TrialData, user: User = Depends(get_current_user)):
    """يستخدم كود تجربة لإتاحة تحليل واحد فقط للمستخدم."""
    trial_code = os.getenv("TRIAL_CODE", "")
    if not trial_code:
        raise HTTPException(status_code=503, detail="كود التجربة غير متاح حالياً")
    if data.code.strip() != trial_code:
        raise HTTPException(status_code=400, detail="كود التجربة غير صحيح")
    with Session(engine) as s:
        u = s.get(User, user.id)
        if u.trial_used == 1:
            raise HTTPException(status_code=400, detail="استخدمت تجربتك مسبقاً، اشترك للمتابعة")
        if u.is_active == 1 and u.plan not in ("", "trial"):
            raise HTTPException(status_code=400, detail="لديك اشتراك فعّال بالفعل")
        u.is_active = 1
        u.plan = "trial"
        u.trial_used = 1
        s.add(u)
        s.commit()
        return {"ok": True, "message": "تم تفعيل التجربة — لك تحليل واحد فقط"}


@app.post("/analyze")
def analyze(data: SalesData, user: User = Depends(get_current_user)):
    # قفل: لازم يكون مشترك ومفعّل
    if user.is_active != 1:
        raise HTTPException(status_code=403, detail="يجب الاشتراك في إحدى الباقات لاستخدام التحليل")
    # تحقق من انتهاء الاشتراك
    if user.subscription_end and user.subscription_end < datetime.now():
        raise HTTPException(status_code=403, detail="انتهى اشتراكك، يرجى التجديد")

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
        forecast_text = f"""بناءً على {history_count + 1} إدخالات، متوسط معدل نمو الإيرادات: {forecast['avg_rate']}% لكل فترة.
- الشهر القادم: بين {forecast['next_month_cons']} (متحفظ) و {forecast['next_month_opt']} (متفائل) ريال
- بعد ٣ أشهر: بين {forecast['m3_cons']} و {forecast['m3_opt']} ريال
- بعد ٦ أشهر: بين {forecast['m6_cons']} و {forecast['m6_opt']} ريال"""
    else:
        forecast_text = "لا تتوفر بيانات تاريخية كافية للتوقع (يحتاج إدخالين أو أكثر)."

    sanity_flags = check_sanity(data, margin, avg_ticket, expense_ratio)
    if sanity_flags:
        sanity_text = "⚠️ ملاحظات على جودة المدخلات (فسّر النتائج بحذر):\n- " + "\n- ".join(sanity_flags)
    else:
        sanity_text = "✅ المدخلات تبدو منطقية ومتسقة."

    # ===== محرك الحسابات: الدرجات =====
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
    else: quality_note = "بيانات ناقصة — الدقة محدودة، يُنصح بإكمالها"

    if health_score >= 90: level = "ممتاز"; icon = "🟢"
    elif health_score >= 75: level = "جيد"; icon = "🟢"
    elif health_score >= 60: level = "تنبيه"; icon = "🟡"
    else: level = "خطر"; icon = "🔴"

    if covers_expenses:
        be_text = f"الإيرادات ({data.revenue} ريال) تغطّي المصروفات ({data.expenses} ريال) وتزيد عنها — هامش أمان {safety_margin}%. المعادلة: (الإيرادات − المصروفات) ÷ المصروفات × 100."
    else:
        be_text = f"الإيرادات ({data.revenue} ريال) لا تغطّي المصروفات ({data.expenses} ريال) — المنشأة في منطقة خسارة بنسبة {abs(safety_margin)}%."

    # بناء سطر الأصناف الأكثر مبيعاً
    top_items_parts = [data.top_item]
    if data.top_item_2 and data.top_item_2.strip():
        top_items_parts.append(data.top_item_2.strip())
    if data.top_item_3 and data.top_item_3.strip():
        top_items_parts.append(data.top_item_3.strip())
    top_items_str = " | ".join(top_items_parts)

    plan = data.plan if data.plan in ("basic", "pro", "executive") else "executive"
    sections = get_sections(plan).format(
        level=level, health_score=health_score, risk_score=risk_score,
        opportunity_score=opportunity_score, data_quality=data_quality, quality_note=quality_note
    )
    plan_names = {"basic": "الأساسية", "pro": "الاحترافية", "executive": "التنفيذية"}

    prompt = f"""أنت "نبّاه"، مستشار أعمال تنفيذي بخبرة تتجاوز ١٥ عاماً في تحليل المنشآت. مهمتك ليست وصف الأرقام، بل اكتشاف المشكلات الحقيقية والفرص الخفية كمستشار تنفيذي يكتب لمالك المنشأة.

# الباقة الحالية: {plan_names[plan]}
اكتب الأقسام المطلوبة لهذه الباقة فقط. لا تضف أقساماً خارجها.

# البيانات المؤكدة لمنشأة "{data.restaurant}":
- مبيعات اليوم: {data.sales_today} ريال | أمس: {data.sales_yesterday} ريال | التغير: {percent}%
- عدد الطلبات: {data.orders} | الأصناف: {data.items_count} | متوسط الفاتورة: {avg_ticket} ريال
- الأصناف الأكثر مبيعاً: {top_items_str}
- أوقات الذروة: {data.peak_hours}
- توزيع الطلبات بالوقت: {data.hourly_orders if data.hourly_orders else 'لم يُدخل'}
- الإيرادات: {data.revenue} ريال | المصروفات: {data.expenses} ريال ({expense_ratio}% من الإيرادات)
- صافي الربح: {profit} ريال | الهامش: {margin}% | الربح لكل طلب: {profit_per_order} ريال
- ملاحظات المالك: {data.notes}
- عدد الإدخالات التاريخية: {history_count}

# نتيجة فحص جودة المدخلات:
{sanity_text}

# تغطية المصروفات:
{be_text}

# التوقعات الرقمية:
{forecast_text}

# الدرجات الذكية (لا تغيّرها، اشرحها فقط):
- مؤشر صحة المنشأة: {health_score}/100 ({level})
- مؤشر المخاطر: {risk_score}/100
- مؤشر الفرص: {opportunity_score}/100
- جودة البيانات: {data_quality}/100 ({quality_note})

# تقديرات مالية جاهزة:
- توفير شهري لو خُفّضت المصروفات ١٠٪: {save_10} ريال | سنوياً: {save_10_year} ريال
- زيادة الإيراد لو ارتفعت المبيعات ١٥٪: {sales_up_15} ريال
- أثر رفع متوسط الفاتورة ٨٪: {ticket_up_8} ريال

# قواعد صارمة:
1. فرّق بوضوح: ✅ حقيقة مؤكدة | ⚠️ فرضية تحتاج تحقق | ❌ بيانات ناقصة.
2. لا تخترع أي رقم. كل الأرقام والدرجات استخدمها كما أُعطيت حرفياً.
3. الدرجات محسوبة مسبقاً — ممنوع تغييرها. اشرح "لماذا" كل درجة.
4. ضع نسبة ثقة (%) بعد كل استنتاج أو توصية مهمة.
5. ممنوع المبالغة. لا تتنبأ بـ"إفلاس" أو "كارثة" من بيانات قليلة.
6. للأسباب الجذرية: لا تخمّن. إذا لم تكفِ البيانات قل ذلك صراحة.
7. عند وجود تناقض: نبّه عليه، لكن أكمل التحليل ولا ترفض البيانات.
8. إذا كانت هناك ملاحظات على جودة المدخلات، اذكرها في البداية.
9. أي قسم لا تكفيه البيانات: تجاهله.
10. كن محدداً بالأرقام، ولهجة مهنية واثقة دون مبالغة.
11. اكتب فقط الأقسام المحددة لهذه الباقة. لا تضف أي قسم غير مذكور.

# مهم: ابدأ ردّك بهذه الكتلة بالضبط:
===NABBAH_EXEC===
ALERT: (أهم تنبيه — جملة واحدة محددة بالأرقام)
DECISION: (أهم قرار الآن — جملة واحدة)
OPPORTUNITY: (أهم فرصة — جملة واحدة)
===END===

# ثم اكتب الأقسام التالية فقط (حسب باقة {plan_names[plan]}):

{sections}"""

    models = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-flash-latest"]
    response = None
    for model_name in models:
        for attempt in range(2):
            try:
                response = ai_client.models.generate_content(
                    model=model_name,
                    contents=prompt
                )
                break
            except Exception as e:
                time.sleep(2)
        if response is not None:
            break
    if response is None:
        raise HTTPException(status_code=503, detail="الخدمة مزدحمة حالياً، حاول بعد دقيقة")

    clean_text, top_alert, top_decision, top_opportunity = extract_exec(response.text)

    if not top_alert:
        top_alert = "راجع المؤشرات المالية في التقرير الكامل" if level != "خطر" else "المنشأة في منطقة خطر — راجع التقرير فوراً"
    if not top_decision:
        top_decision = "اطّلع على قسم القرار التنفيذي في التقرير"
    if not top_opportunity:
        top_opportunity = "راجع قسم الفرص في التقرير"

    entry = Entry(
        user_id=user.id,
        restaurant=data.restaurant,
        sales_today=data.sales_today, sales_yesterday=data.sales_yesterday,
        orders=data.orders, items_count=data.items_count,
        top_item=data.top_item,
        top_item_2=data.top_item_2 or "",
        top_item_3=data.top_item_3 or "",
        hourly_orders=data.hourly_orders or "",
        peak_hours=data.peak_hours,
        revenue=data.revenue, expenses=data.expenses, notes=data.notes,
        plan=plan,
        change_percent=percent, profit=profit, margin=margin,
        health_score=health_score, risk_score=risk_score,
        opportunity_score=opportunity_score, data_quality=data_quality,
        covers_expenses=covers_expenses, safety_margin=safety_margin,
        top_alert=top_alert, top_decision=top_decision, top_opportunity=top_opportunity,
        smart_message=clean_text
    )
    with Session(engine) as session:
        session.add(entry)
        # إذا كان في وضع التجربة، اقفله بعد هذا التحليل الوحيد
        if user.plan == "trial":
            u = session.get(User, user.id)
            if u:
                u.is_active = 0
                u.plan = "trial_used"
                session.add(u)
        session.commit()

    return {
        "restaurant": data.restaurant, "change_percent": percent, "avg_ticket": avg_ticket,
        "profit": profit, "margin": margin, "profit_per_order": profit_per_order,
        "health_score": health_score, "risk_score": risk_score,
        "opportunity_score": opportunity_score, "data_quality": data_quality,
        "covers_expenses": covers_expenses, "safety_margin": safety_margin,
        "plan": plan,
        "top_alert": top_alert, "top_decision": top_decision, "top_opportunity": top_opportunity,
        "level": level, "icon": icon, "smart_message": clean_text
    }


@app.get("/history")
def history(user: User = Depends(get_current_user)):
    with Session(engine) as session:
        entries = session.exec(
            select(Entry).where(Entry.user_id == user.id)
        ).all()
        return entries
