import os
import time
import re
import json
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
    sector: Optional[str] = "restaurant"  # restaurant / cafe / retail
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
    company_id: Optional[int] = None            # مرتبط بشركة؟ (للمدراء والموظفين)
    company_role: str = ""                       # owner/manager/staff — فارغ = فرد
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

class ActivityLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    actor: str = "النظام"
    action: str = ""
    target_email: str = ""
    created_at: datetime = Field(default_factory=datetime.now)

# ===== جداول قسم الشركات (مستقل تماماً عن قسم المطاعم/الأفراد) =====
class Company(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str                                    # اسم الشركة
    owner_id: int = Field(index=True)            # المالك (user_id)
    plan: str = "enterprise"
    sector: str = "retail"                       # retail/fnb/services/other — نشاط الشركة
    cash_reserve: float = 0                       # الاحتياطي النقدي الحالي (للتدفق النقدي)
    monthly_obligations: float = 0                # الالتزامات الشهرية الثابتة (رواتب/إيجار/أقساط)
    is_active: int = 1
    created_at: datetime = Field(default_factory=datetime.now)

class CompanyBranch(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)          # تابع لأي شركة
    name: str                                    # اسم الفرع
    city: str = ""                               # المدينة (تُستخدم للخريطة)
    area: str = ""                               # الحي/المنطقة (اختياري)
    branch_type: str = "standalone"              # mall/strip/standalone/online/kiosk
    lat: float = 0.0                             # إحداثيات الفرع (تُملأ من المدينة)
    lng: float = 0.0
    target_sales: float = 0                      # هدف المبيعات الشهري (اختياري)
    target_customers: int = 0                    # هدف عدد العملاء الشهري (اختياري)
    is_active: int = 1
    created_at: datetime = Field(default_factory=datetime.now)

class CompanyEntry(SQLModel, table=True):
    """بيانات دورية لكل فرع — تتراكم لتعطي اتجاهات وتنبؤ."""
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    branch_id: int = Field(index=True)
    branch_name: str = ""                         # نسخة للعرض السريع
    period: str = ""                              # الفترة، مثل "2025-06"
    # ----- مدخلات خام (يدخلها المستخدم) -----
    sales: float = 0                              # إجمالي المبيعات
    invoices: int = 0                             # عدد الفواتير/الطلبات
    customers: int = 0                            # عدد العملاء
    new_customers: int = 0                        # عملاء جدد
    repeat_customers: int = 0                     # عملاء متكررون
    expenses: float = 0                           # المصروفات
    deposited: float = 0                          # المبلغ المُودَع فعلياً (لكشف فجوة البيع-الإيداع)
    discounts: float = 0                          # الخصومات
    top_products: str = ""                        # أكثر الأصناف مبيعاً (نص: صنف1 | صنف2 | صنف3)
    extra_data: str = ""                          # بيانات إضافية حسب القطاع/المستوى (JSON)
    notes: str = ""                               # ملاحظات
    # ----- محسوبة تلقائياً -----
    profit: float = 0
    margin: float = 0
    avg_invoice: float = 0
    repeat_rate: float = 0
    growth: float = 0                             # النمو مقابل الفترة السابقة %
    branch_score: int = 0                         # مؤشر أداء الفرع /100
    smart_message: str = ""                       # تحليل Gemini المحفوظ
    created_at: datetime = Field(default_factory=datetime.now)

def log_activity(actor: str, action: str, target_email: str = ""):
    """يسجّل حدثاً في سجل النشاط."""
    try:
        with Session(engine) as s:
            s.add(ActivityLog(actor=actor, action=action, target_email=target_email))
            s.commit()
    except Exception:
        pass


# قاعدة البيانات: تستخدم PostgreSQL من Railway تلقائياً، أو SQLite محلياً
db_url = os.getenv("DATABASE_URL", "sqlite:///nabbah.db")
# Railway يعطي postgres:// لكن SQLAlchemy يحتاج postgresql://
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)
engine = create_engine(db_url)
class CompanyMember(SQLModel, table=True):
    """أعضاء فريق الشركة وصلاحياتهم."""
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    name: str = ""
    email: str = ""
    role: str = "staff"                          # manager/accountant/staff (المالك ضمني)
    branch_id: Optional[int] = None              # لمدير فرع معيّن (اختياري)
    created_at: datetime = Field(default_factory=datetime.now)


class CompanyModuleEntry(SQLModel, table=True):
    """إدخالات الوحدات الموسّعة (مالية/مبيعات/عملاء/...). تُحفظ مرنة كـ JSON."""
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    branch_id: Optional[int] = Field(default=None, index=True)  # None = على مستوى الشركة
    module: str = Field(index=True)                              # finance / sales / customers / ...
    period: str = ""                                             # YYYY-MM
    data: str = ""                                               # JSON
    created_at: datetime = Field(default_factory=datetime.now)


SQLModel.metadata.create_all(engine)


# ===== Migration تلقائي: يضيف الأعمدة الجديدة لجداول موجودة =====
def run_migrations():
    """يضيف أعمدة company_id و company_role لجدول user إذا ما كانت موجودة."""
    from sqlalchemy import text
    is_postgres = db_url.startswith("postgresql")

    migrations = []
    if is_postgres:
        # PostgreSQL syntax
        migrations = [
            'ALTER TABLE "user" ADD COLUMN IF NOT EXISTS company_id INTEGER',
            'ALTER TABLE "user" ADD COLUMN IF NOT EXISTS company_role VARCHAR DEFAULT \'\'',
            'ALTER TABLE company ADD COLUMN IF NOT EXISTS cash_reserve DOUBLE PRECISION DEFAULT 0',
            'ALTER TABLE company ADD COLUMN IF NOT EXISTS monthly_obligations DOUBLE PRECISION DEFAULT 0',
            'ALTER TABLE companyentry ADD COLUMN IF NOT EXISTS deposited DOUBLE PRECISION DEFAULT 0',
            'ALTER TABLE companyentry ADD COLUMN IF NOT EXISTS extra_data VARCHAR DEFAULT \'\'',
        ]
    else:
        # SQLite - أبسط، لكن ما يدعم IF NOT EXISTS بنفس الطريقة
        try:
            with engine.connect() as conn:
                result = conn.execute(text("PRAGMA table_info(user)"))
                cols = [row[1] for row in result]
                if "company_id" not in cols:
                    migrations.append("ALTER TABLE user ADD COLUMN company_id INTEGER")
                if "company_role" not in cols:
                    migrations.append("ALTER TABLE user ADD COLUMN company_role VARCHAR DEFAULT ''")
                # أعمدة التدفق النقدي والتسرّب
                try:
                    ccols = [row[1] for row in conn.execute(text("PRAGMA table_info(company)"))]
                    if "cash_reserve" not in ccols:
                        migrations.append("ALTER TABLE company ADD COLUMN cash_reserve REAL DEFAULT 0")
                    if "monthly_obligations" not in ccols:
                        migrations.append("ALTER TABLE company ADD COLUMN monthly_obligations REAL DEFAULT 0")
                    ecols = [row[1] for row in conn.execute(text("PRAGMA table_info(companyentry)"))]
                    if "deposited" not in ecols:
                        migrations.append("ALTER TABLE companyentry ADD COLUMN deposited REAL DEFAULT 0")
                    if "extra_data" not in ecols:
                        migrations.append("ALTER TABLE companyentry ADD COLUMN extra_data VARCHAR DEFAULT ''")
                except Exception:
                    pass
        except Exception:
            pass

    for sql in migrations:
        try:
            with engine.connect() as conn:
                conn.execute(text(sql))
                conn.commit()
                print(f"✅ Migration OK: {sql[:60]}...")
        except Exception as e:
            print(f"⚠️ Migration skipped: {e}")

run_migrations()


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

@app.get("/trends.html")
def page_trends():
    return FileResponse("trends.html")

@app.get("/login.html")
def page_login():
    return FileResponse("login.html")

@app.get("/register.html")
def page_register():
    return FileResponse("register.html")

@app.get("/trial.html")
def page_trial():
    return FileResponse("trial.html")

@app.get("/admin")
def page_admin():
    return FileResponse("admin.html")

@app.get("/admin.html")
def page_admin_html():
    return FileResponse("admin.html")

@app.get("/privacy.html")
def page_privacy():
    return FileResponse("privacy.html")

@app.get("/terms.html")
def page_terms():
    return FileResponse("terms.html")

@app.get("/contact.html")
def page_contact():
    return FileResponse("contact.html")

@app.get("/refund.html")
def page_refund():
    return FileResponse("refund.html")

@app.get("/cookies.html")
def page_cookies():
    return FileResponse("cookies.html")

@app.get("/security.html")
def page_security():
    return FileResponse("security.html")


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
        log_activity("عميل جديد", f"سجّل حساباً جديداً ({user.business_name or 'بدون منشأة'})", user.email)
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


# ===== لوحة الإدارة =====
def verify_admin(x_admin_key: str = Header(default="")) -> bool:
    admin_secret = os.getenv("ADMIN_KEY", "")
    if not admin_secret or x_admin_key != admin_secret:
        raise HTTPException(status_code=403, detail="كلمة المسؤول غير صحيحة")
    return True

@app.get("/admin/users")
def admin_list_users(_: bool = Depends(verify_admin)):
    """يرجّع قائمة كل العملاء."""
    with Session(engine) as s:
        users = s.exec(select(User)).all()
        result = []
        for u in users:
            # عدد التحاليل
            entries_count = len(s.exec(select(Entry).where(Entry.user_id == u.id)).all())
            result.append({
                "id": u.id,
                "name": u.name,
                "email": u.email,
                "phone": u.phone,
                "business_name": u.business_name,
                "plan": u.plan,
                "is_active": u.is_active,
                "trial_used": u.trial_used,
                "subscription_end": u.subscription_end.isoformat() if u.subscription_end else None,
                "created_at": u.created_at.isoformat() if u.created_at else None,
                "entries_count": entries_count,
            })
        # ترتيب: الأحدث أولاً
        result.sort(key=lambda x: x["created_at"] or "", reverse=True)
        return result


# أسعار الباقات (ريال/شهر)
PLAN_PRICES = {"basic": 269, "pro": 699, "executive": 1299}

@app.get("/admin/stats")
def admin_stats(_: bool = Depends(verify_admin)):
    """إحصائيات شاملة للوحة الإدارة."""
    now = datetime.now()
    week_ago = now - timedelta(days=7)
    with Session(engine) as s:
        users = s.exec(select(User)).all()
        total = len(users)
        active = sum(1 for u in users if u.is_active == 1 and u.plan in ("basic", "pro", "executive"))
        frozen = sum(1 for u in users if u.is_active == 0 and u.plan in ("basic", "pro", "executive"))
        trial = sum(1 for u in users if u.plan in ("trial", "trial_used"))
        expired = sum(1 for u in users if u.subscription_end and u.subscription_end < now and u.plan in ("basic", "pro", "executive"))
        new_week = sum(1 for u in users if u.created_at and u.created_at >= week_ago)
        # الإيرادات الشهرية = مجموع أسعار باقات المشتركين النشطين
        monthly_revenue = sum(
            PLAN_PRICES.get(u.plan, 0)
            for u in users
            if u.is_active == 1 and u.plan in PLAN_PRICES
            and (not u.subscription_end or u.subscription_end >= now)
        )
        total_entries = len(s.exec(select(Entry)).all())
        return {
            "total": total,
            "active": active,
            "frozen": frozen,
            "trial": trial,
            "expired": expired,
            "new_week": new_week,
            "monthly_revenue": monthly_revenue,
            "yearly_revenue": monthly_revenue * 12,
            "total_entries": total_entries,
        }

@app.get("/admin/activity")
def admin_activity(_: bool = Depends(verify_admin)):
    """آخر 50 حدث في سجل النشاط."""
    with Session(engine) as s:
        logs = s.exec(select(ActivityLog)).all()
        logs.sort(key=lambda x: x.created_at or datetime.min, reverse=True)
        logs = logs[:50]
        return [{
            "actor": l.actor,
            "action": l.action,
            "target_email": l.target_email,
            "created_at": l.created_at.isoformat() if l.created_at else None,
        } for l in logs]

@app.get("/admin/user/{user_id}")
def admin_user_detail(user_id: int, _: bool = Depends(verify_admin)):
    """تفاصيل عميل واحد مع تحاليله."""
    with Session(engine) as s:
        u = s.get(User, user_id)
        if not u:
            raise HTTPException(status_code=404, detail="العميل غير موجود")
        entries = s.exec(select(Entry).where(Entry.user_id == user_id)).all()
        entries.sort(key=lambda x: x.created_at or datetime.min, reverse=True)
        return {
            "id": u.id,
            "name": u.name,
            "email": u.email,
            "phone": u.phone,
            "business_name": u.business_name,
            "plan": u.plan,
            "is_active": u.is_active,
            "trial_used": u.trial_used,
            "subscription_start": u.subscription_start.isoformat() if u.subscription_start else None,
            "subscription_end": u.subscription_end.isoformat() if u.subscription_end else None,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "entries": [{
                "restaurant": e.restaurant,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            } for e in entries],
        }


class AdminActionData(BaseModel):
    user_id: int
    plan: Optional[str] = "executive"
    days: Optional[int] = 30

@app.post("/admin/activate-user")
def admin_activate_user(data: AdminActionData, _: bool = Depends(verify_admin)):
    """تفعيل حساب عميل بـ user_id."""
    with Session(engine) as s:
        user = s.get(User, data.user_id)
        if not user:
            raise HTTPException(status_code=404, detail="المستخدم غير موجود")
        now = datetime.now()
        old_plan = user.plan
        user.is_active = 1
        user.plan = data.plan
        user.subscription_start = now
        user.subscription_end = now + timedelta(days=data.days)
        s.add(user)
        s.commit()
        plan_ar = {"basic": "الأساسية", "pro": "الاحترافية", "executive": "التنفيذية"}.get(data.plan, data.plan)
        if old_plan and old_plan != data.plan:
            log_activity("المسؤول", f"غيّر الاشتراك إلى {plan_ar} ({data.days} يوم)", user.email)
        else:
            log_activity("المسؤول", f"فعّل اشتراك {plan_ar} ({data.days} يوم)", user.email)
        return {"ok": True, "message": f"تم تفعيل {user.email} لمدة {data.days} يوم"}

@app.post("/admin/deactivate-user")
def admin_deactivate_user(data: AdminActionData, _: bool = Depends(verify_admin)):
    """إيقاف حساب عميل."""
    with Session(engine) as s:
        user = s.get(User, data.user_id)
        if not user:
            raise HTTPException(status_code=404, detail="المستخدم غير موجود")
        user.is_active = 0
        s.add(user)
        s.commit()
        log_activity("المسؤول", "أوقف الاشتراك", user.email)
        return {"ok": True, "message": f"تم إيقاف {user.email}"}


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


# ===== معايير القطاع (Benchmarks) =====
BENCHMARKS = {
    "restaurant": {
        "name": "مطاعم",
        "margin_good": 25,       # هامش ربح جيد %
        "margin_ok": 15,         # هامش مقبول %
        "avg_ticket_good": 80,   # متوسط فاتورة جيد ريال
        "expense_ratio_ok": 65,  # نسبة مصروفات مقبولة من الإيرادات %
        "orders_growth": 5,      # نمو طلبات مستهدف %
    },
    "cafe": {
        "name": "كافيهات",
        "margin_good": 35,
        "margin_ok": 20,
        "avg_ticket_good": 50,
        "expense_ratio_ok": 55,
        "orders_growth": 8,
    },
    "retail": {
        "name": "تجزئة",
        "margin_good": 30,
        "margin_ok": 18,
        "avg_ticket_good": 150,
        "expense_ratio_ok": 60,
        "orders_growth": 3,
    },
}

def get_benchmark_analysis(data: SalesData, margin: float, avg_ticket: float, expense_ratio: float) -> str:
    """مقارنة أرقام المنشأة بمعايير قطاعها."""
    sector = data.sector or "restaurant"
    bm = BENCHMARKS.get(sector, BENCHMARKS["restaurant"])
    lines = [f"\n📊 **مقارنة بمعايير قطاع {bm['name']}:**"]

    # هامش الربح
    if margin >= bm["margin_good"]:
        lines.append(f"✅ هامش الربح {margin}٪ — ممتاز (معيار القطاع: {bm['margin_good']}٪+)")
    elif margin >= bm["margin_ok"]:
        lines.append(f"⚠️ هامش الربح {margin}٪ — مقبول لكن دون المعيار المثالي ({bm['margin_good']}٪)")
    else:
        lines.append(f"❌ هامش الربح {margin}٪ — دون معيار القطاع ({bm['margin_ok']}٪ الحد الأدنى)")

    # متوسط الفاتورة
    if avg_ticket >= bm["avg_ticket_good"]:
        lines.append(f"✅ متوسط الفاتورة {avg_ticket} ريال — جيد لقطاع {bm['name']}")
    else:
        lines.append(f"⚠️ متوسط الفاتورة {avg_ticket} ريال — أقل من المعيار ({bm['avg_ticket_good']} ريال)")

    # نسبة المصروفات
    if expense_ratio <= bm["expense_ratio_ok"]:
        lines.append(f"✅ نسبة المصروفات {expense_ratio}٪ — ضمن المعيار المقبول")
    else:
        lines.append(f"⚠️ نسبة المصروفات {expense_ratio}٪ — أعلى من معيار القطاع ({bm['expense_ratio_ok']}٪)")

    return "\n".join(lines)

def get_history_analysis(entries: list) -> str:
    """تحليل مبني على تاريخ العميل مع توقعات الأسبوع القادم."""
    if len(entries) < 2:
        return ""

    # آخر 5 تحاليل
    recent = sorted(entries, key=lambda e: e.created_at or datetime.min)[-5:]

    # اتجاه المبيعات
    sales_list = [e.sales_today for e in recent if e.sales_today]
    margin_list = [e.margin for e in recent if e.margin]
    health_list = [e.health_score for e in recent if e.health_score]

    lines = ["\n📈 **تحليل مسار منشأتك:**"]

    if len(sales_list) >= 2:
        sales_trend = sales_list[-1] - sales_list[0]
        sales_pct = round((sales_trend / sales_list[0]) * 100, 1) if sales_list[0] else 0
        if sales_pct > 5:
            lines.append(f"✅ مبيعاتك في تحسّن مستمر (+{sales_pct}٪ مقارنة بأول إدخال)")
        elif sales_pct < -5:
            lines.append(f"⚠️ مبيعاتك في تراجع ({sales_pct}٪) — يحتاج مراجعة")
        else:
            lines.append(f"➡️ مبيعاتك مستقرة نسبياً ({sales_pct:+.1f}٪)")

    if len(margin_list) >= 2:
        margin_trend = margin_list[-1] - margin_list[0]
        if margin_trend > 2:
            lines.append(f"✅ هامش الربح يتحسّن (+{margin_trend:.1f}٪ منذ أول تحليل)")
        elif margin_trend < -2:
            lines.append(f"⚠️ هامش الربح يتراجع ({margin_trend:.1f}٪) — راجع مصروفاتك")

    if len(health_list) >= 2:
        health_trend = health_list[-1] - health_list[0]
        if health_trend > 5:
            lines.append(f"✅ درجة صحة منشأتك ترتفع ({health_trend:+.0f} نقطة)")
        elif health_trend < -5:
            lines.append(f"⚠️ درجة الصحة تنخفض ({health_trend:.0f} نقطة) — انتبه للاتجاه")

    # توقعات الأسبوع القادم
    if len(sales_list) >= 3:
        lines.append("\n🔮 **توقعات الأسبوع القادم:**")
        avg_growth = (sales_list[-1] - sales_list[-3]) / 2 if len(sales_list) >= 3 else 0
        forecast = round(sales_list[-1] + avg_growth)
        if avg_growth > 0:
            lines.append(f"📈 المبيعات اليومية المتوقعة: {forecast:,} ريال (استناداً لمسار النمو الأخير)")
        elif avg_growth < 0:
            lines.append(f"📉 المبيعات المتوقعة: {forecast:,} ريال — المسار الحالي يشير لتراجع، وقت التدخّل الآن")
        else:
            lines.append(f"➡️ المبيعات المتوقعة: {forecast:,} ريال (استقرار نسبي)")

    return "\n".join(lines) if len(lines) > 1 else ""


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
        user_entries = s.exec(select(Entry).where(Entry.user_id == user.id)).all()
        user_entries_sorted = sorted(user_entries, key=lambda e: e.created_at or datetime.min)
        history_count = len(user_entries)
        history_revenues = [e.revenue for e in user_entries_sorted]

    # تحليل مبني على تاريخ العميل
    history_insight = get_history_analysis(user_entries_sorted)

    # مقارنة بمعايير القطاع
    benchmark_insight = get_benchmark_analysis(data, margin, avg_ticket, expense_ratio)

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

    # الباقة تُحدّد من اشتراك العميل الفعلي (وليس من اختياره في الصفحة)
    # عميل التجربة يحصل على مستوى الباقة الأساسية
    if user.plan in ("basic", "pro", "executive"):
        plan = user.plan
    elif user.plan == "trial":
        plan = "basic"
    else:
        plan = "basic"
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

# مقارنة بمعايير قطاع {BENCHMARKS.get(data.sector or 'restaurant', BENCHMARKS['restaurant'])['name']}:
{benchmark_insight}

# تحليل مسار المنشأة (مبني على تاريخ العميل):
{history_insight if history_insight else "لا يتوفر تاريخ كافٍ للتحليل (هذا أول تحليل أو تحليل واحد سابق)."}

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

    log_activity(user.name or "عميل", f"ولّد تقريراً جديداً ({data.restaurant})", user.email)

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


@app.get("/trends")
def trends(user: User = Depends(get_current_user)):
    """تطوّر مؤشرات المنشأة عبر الوقت + مقارنة آخر تحليلين."""
    with Session(engine) as session:
        entries = session.exec(
            select(Entry).where(Entry.user_id == user.id)
        ).all()
        entries.sort(key=lambda e: e.created_at or datetime.min)

        points = [{
            "date": e.created_at.isoformat() if e.created_at else None,
            "restaurant": e.restaurant,
            "sales": e.sales_today,
            "revenue": e.revenue,
            "expenses": e.expenses,
            "profit": e.profit,
            "margin": e.margin,
            "orders": e.orders,
            "health_score": e.health_score,
            "risk_score": e.risk_score,
            "opportunity_score": e.opportunity_score,
        } for e in entries]

        # المقارنة بين آخر تحليلين
        comparison = None
        if len(entries) >= 2:
            last = entries[-1]
            prev = entries[-2]
            def delta(now, before):
                diff = now - before
                pct = round((diff / before) * 100, 1) if before else 0
                return {"now": now, "before": before, "diff": round(diff, 1), "pct": pct,
                        "dir": "up" if diff > 0 else ("down" if diff < 0 else "same")}
            comparison = {
                "from_date": prev.created_at.isoformat() if prev.created_at else None,
                "to_date": last.created_at.isoformat() if last.created_at else None,
                "sales": delta(last.sales_today, prev.sales_today),
                "revenue": delta(last.revenue, prev.revenue),
                "expenses": delta(last.expenses, prev.expenses),
                "profit": delta(last.profit, prev.profit),
                "margin": delta(last.margin, prev.margin),
                "health_score": delta(last.health_score, prev.health_score),
            }

        return {
            "count": len(points),
            "points": points,
            "comparison": comparison,
        }

# ============================================================
# ===== قسم الشركات — نظيف ومستقل تماماً عن المطاعم =====
# ============================================================

SECTOR_NAMES = {
    "fnb": "مطاعم وكافيهات",
    "retail": "تجارة تجزئة",
    "ecommerce": "تجارة إلكترونية",
    "manufacturing": "تصنيع",
    "contracting": "مقاولات",
    "distribution": "توزيع",
    "services": "خدمات",
    "clinics": "عيادات",
    "hospitals": "مستشفيات",
    "logistics": "لوجستيات",
    "other": "أخرى",
}

# إحداثيات أبرز المدن السعودية (للخريطة)
SA_CITIES = {
    "الرياض": (24.7136, 46.6753),
    "جدة": (21.4858, 39.1925),
    "مكة": (21.3891, 39.8579),
    "مكة المكرمة": (21.3891, 39.8579),
    "المدينة": (24.5247, 39.5692),
    "المدينة المنورة": (24.5247, 39.5692),
    "الدمام": (26.4207, 50.0888),
    "الخبر": (26.2794, 50.2083),
    "الظهران": (26.2361, 50.0393),
    "الطائف": (21.2703, 40.4158),
    "تبوك": (28.3838, 36.5550),
    "بريدة": (26.3260, 43.9750),
    "عنيزة": (26.0840, 43.9940),
    "خميس مشيط": (18.3000, 42.7300),
    "أبها": (18.2164, 42.5053),
    "حائل": (27.5114, 41.7208),
    "نجران": (17.4933, 44.1277),
    "جازان": (16.8894, 42.5611),
    "ينبع": (24.0890, 38.0618),
    "الأحساء": (25.3833, 49.5833),
    "الهفوف": (25.3647, 49.5870),
    "القطيف": (26.5650, 49.9963),
    "عرعر": (30.9753, 41.0381),
    "سكاكا": (29.9697, 40.2064),
    "الجبيل": (27.0046, 49.6606),
}

def geocode_city(city, seed=0):
    """يرجّع إحداثيات تقريبية للمدينة مع توزيع بسيط حتى لا تتطابق الدبابيس."""
    base = SA_CITIES.get((city or "").strip(), SA_CITIES["الرياض"])
    jitter_lat = ((seed % 7) - 3) * 0.012
    jitter_lng = ((seed % 5) - 2) * 0.012
    return round(base[0] + jitter_lat, 5), round(base[1] + jitter_lng, 5)


def score_level(score):
    """مستوى ولون مؤشر الفرع (يطابق مفتاح الألوان في اللوحة)."""
    if score >= 70:
        return ("ممتاز", "#10b981")
    if score >= 55:
        return ("جيد", "#f5b301")
    if score >= 40:
        return ("متوسط", "#f59e0b")
    return ("ضعيف", "#ef4444")


def compute_company_metrics(sales, invoices, customers, repeat_customers, expenses, prev_sales=None):
    """يحسب المؤشرات المالية ومؤشر أداء الفرع من المدخلات الخام."""
    profit = round(sales - expenses, 2)
    margin = round((profit / sales) * 100, 1) if sales > 0 else 0
    avg_invoice = round(sales / invoices, 1) if invoices > 0 else 0
    repeat_rate = round((repeat_customers / customers) * 100, 1) if customers > 0 else 0
    has_prev = bool(prev_sales and prev_sales > 0)
    growth = round(((sales - prev_sales) / prev_sales) * 100, 1) if has_prev else 0

    score = 0
    # الهامش (35)
    if margin >= 25: score += 35
    elif margin >= 18: score += 28
    elif margin >= 12: score += 20
    elif margin >= 6: score += 11
    elif margin > 0: score += 5
    # النمو (30) — بلا فترة سابقة نعطي وسطاً محايداً
    if not has_prev:
        score += 18
    elif growth >= 10: score += 30
    elif growth >= 3: score += 23
    elif growth >= 0: score += 16
    elif growth >= -8: score += 8
    elif growth >= -20: score += 3
    # ولاء العملاء (20)
    if repeat_rate >= 40: score += 20
    elif repeat_rate >= 25: score += 14
    elif repeat_rate >= 15: score += 9
    elif repeat_rate > 0: score += 4
    # ربحية موجبة (15)
    if profit > 0: score += 15
    score = min(score, 100)

    return {
        "profit": profit, "margin": margin, "avg_invoice": avg_invoice,
        "repeat_rate": repeat_rate, "growth": growth, "branch_score": score,
    }


def company_gemini(prompt: str) -> str:
    """يستدعي Gemini بنفس سلسلة الـ fallback المستخدمة في تحليل المطاعم."""
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
        return ""
    return response.text or ""


def require_company_access(user: User):
    """قسم الشركات يتطلب اشتراكاً مفعّلاً (مثل تحليل المطاعم)."""
    if user.is_active != 1:
        raise HTTPException(status_code=403, detail="باقة الشركات تتطلب اشتراكاً مفعّلاً")
    if user.subscription_end and user.subscription_end < datetime.now():
        raise HTTPException(status_code=403, detail="انتهى اشتراكك، يرجى التجديد")


def build_company_prompt(company, sector_name, rows):
    """بناء برومبت التحليل التنفيذي على مستوى الشركة كاملة."""
    n = len(rows)
    total_sales = sum(e.sales for _, e in rows)
    total_cust = sum(e.customers for _, e in rows)
    total_profit = sum(e.profit for _, e in rows)
    total_inv = sum(e.invoices for _, e in rows)
    avg_margin = round((total_profit / total_sales) * 100, 1) if total_sales else 0
    avg_invoice = round(total_sales / total_inv, 1) if total_inv else 0
    overall = round(sum(e.branch_score for _, e in rows) / n) if n else 0
    ranked = sorted(rows, key=lambda x: x[1].branch_score, reverse=True)

    lines = []
    for b, e in ranked:
        tgt = ""
        if b.target_sales > 0:
            tgt = f" | الهدف {round(b.target_sales)}ر ({round((e.sales / b.target_sales) * 100)}%)"
        lines.append(
            f"- {b.name} ({b.city or 'بدون مدينة'}): مبيعات {round(e.sales)}ر | عملاء {e.customers} | "
            f"متوسط فاتورة {e.avg_invoice}ر | هامش {e.margin}% | تكرار {e.repeat_rate}% | "
            f"نمو {e.growth}% | مؤشر {e.branch_score}/100{tgt}"
        )
    table = "\n".join(lines)
    best = ranked[0][0].name
    worst = ranked[-1][0].name if n > 1 else best

    return f"""أنت "نبّاه"، مستشار تنفيذي بخبرة تتجاوز ١٥ عاماً في إدارة الشركات متعددة الفروع. تكتب لمالك/مدير شركة "{company.name}" ({sector_name}) تقريراً تنفيذياً يكتشف المشكلات الحقيقية والفرص الخفية عبر الفروع — لا تصف الأرقام فقط.

# بيانات الشركة (مؤكدة — لا تخترع أرقاماً):
- عدد الفروع النشطة: {n}
- إجمالي المبيعات: {round(total_sales)} ريال | إجمالي العملاء: {total_cust} | إجمالي الفواتير: {total_inv}
- متوسط الفاتورة العام: {avg_invoice} ريال | الهامش العام: {avg_margin}% | صافي الربح: {round(total_profit)} ريال
- مؤشر الأداء العام للشركة: {overall}/100
- أفضل فرع: {best} | أضعف فرع: {worst}

# جدول الفروع (مرتّب من الأعلى أداءً):
{table}

# قواعد صارمة:
1. لا تخترع أي رقم. استخدم الأرقام والمؤشرات كما أُعطيت حرفياً.
2. ضع نسبة ثقة (%) بعد كل توصية مهمة.
3. ممنوع المبالغة أو التهويل من بيانات قليلة.
4. للأسباب الجذرية: إن لم تكفِ البيانات قل ذلك صراحة.
5. لهجة مهنية واثقة، محددة بالأرقام، بالعربية.
6. قارن الأداء بالأهداف إن وُجدت.

# ابدأ ردّك بهذه الكتلة بالضبط:
===NABBAH_EXEC===
ALERT: (أهم تنبيه عبر الفروع — جملة محددة بالأرقام)
DECISION: (أهم قرار تنفيذي الآن — جملة واحدة)
OPPORTUNITY: (أهم فرصة — جملة واحدة)
===END===

# ثم اكتب الأقسام التالية:

## ⚡ الملخص التنفيذي السريع (30 ثانية)
الحالة العامة ({overall}/100) | أقوى فرع | أضعف فرع | أهم قرار الآن.

## 📊 المؤشرات المالية للشركة
المبيعات، الربح، الهامش، متوسط الفاتورة، العملاء — تعليق خبير قصير على كل رقم.

## 🏆 ترتيب الفروع وقراءته
لماذا تصدّر {best}؟ ولماذا تأخّر {worst}؟ الفجوة وما تعنيه.

## 🧩 تحليل الأسباب الجذرية للفروع الأضعف
لكل فرع ضعيف: السبب المرجّح (مدعوم بالأرقام) + نسبة الثقة.

## ✅ أفضل الممارسات (من الفرع الأعلى)
ما الذي يستحق تعميمه من {best} على باقي الفروع.

## 🎯 الأداء مقابل الأهداف
قارن المبيعات الفعلية بالأهداف للفروع التي لها هدف.

## 💡 الفرص المخفية عبر الفروع

## 📉 تحليل المخاطر
مخاطر حرجة/متوسطة + الأثر + نسبة ثقة.

## 📅 خطة تنفيذية ٣٠-٦٠-٩٠ يوم

## ✅ القرار التنفيذي النهائي
٥ أسطر: الحالة؟ أكبر خطر؟ أكبر فرصة؟ أول قرار؟ العائد المتوقع؟"""


def build_branch_prompt(company, sector_name, b, e, avg_margin, avg_inv, avg_score, hist_txt):
    """بناء برومبت تحليل فرع واحد مقارنةً بمتوسط فروع الشركة."""
    tgt = ""
    if b.target_sales > 0:
        tgt = f"\n- هدف المبيعات: {round(b.target_sales)} ريال (التحقيق {round((e.sales / b.target_sales) * 100)}%)"
    extra_txt = ""
    if getattr(e, "extra_data", ""):
        try:
            ed = json.loads(e.extra_data)
            if isinstance(ed, dict) and ed:
                extra_txt = "\n- مؤشرات إضافية للقطaع: " + " | ".join(f"{k}: {v}" for k, v in ed.items() if str(v).strip())
        except Exception:
            pass

    # بيانات وحدات ERP المصغّر (مالية/مبيعات/عملاء) — آخر إدخال لكل وحدة
    modules_txt = ""
    try:
        with Session(engine) as _ms:
            for mod in ("finance", "sales", "customers", "hr", "ops", "inventory", "procurement", "events", "competitors"):
                me = _ms.exec(
                    select(CompanyModuleEntry).where(
                        CompanyModuleEntry.company_id == b.company_id,
                        CompanyModuleEntry.module == mod,
                    ).where(
                        (CompanyModuleEntry.branch_id == b.id) | (CompanyModuleEntry.branch_id == None)
                    ).order_by(CompanyModuleEntry.created_at.desc())
                ).first()
                if me and me.data:
                    try:
                        md = json.loads(me.data)
                        if isinstance(md, dict) and md:
                            label = MODULE_LABEL.get(mod, mod)
                            top = " | ".join(f"{k}: {v}" for k, v in list(md.items())[:8] if str(v).strip())
                            if top:
                                modules_txt += f"\n- {label}: {top}"
                    except Exception:
                        pass
    except Exception:
        pass
    return f"""أنت "نبّاه"، مستشار تنفيذي بخبرة طويلة. تحلّل أداء فرع "{b.name}" ضمن شركة "{company.name}" ({sector_name}) وتقارنه بباقي فروع الشركة.

# بيانات الفرع (مؤكدة — لا تخترع):
- المدينة: {b.city or 'غير محددة'} | النوع: {b.branch_type}
- المبيعات: {round(e.sales)} ريال | العملاء: {e.customers} | الفواتير: {e.invoices}
- متوسط الفاتورة: {e.avg_invoice} ريال | الهامش: {e.margin}% | صافي الربح: {round(e.profit)} ريال
- العملاء المتكررون: {e.repeat_rate}% | النمو عن الفترة السابقة: {e.growth}%
- المنتجات الأكثر مبيعاً: {e.top_products or 'غير مُدخلة'}{extra_txt}{modules_txt}
- مؤشر أداء الفرع: {e.branch_score}/100{tgt}
- مسار الفرع عبر الفترات: {hist_txt}

# مقارنة بمتوسط فروع الشركة:
- متوسط الهامش: {avg_margin}% | متوسط الفاتورة: {avg_inv} ريال | متوسط المؤشر: {avg_score}/100

# قواعد: لا تخترع أرقاماً، ضع نسبة ثقة بعد كل توصية، بلا مبالغة، بالعربية.

# ابدأ بهذه الكتلة بالضبط:
===NABBAH_EXEC===
ALERT: (أهم تنبيه — جملة محددة بالأرقام)
DECISION: (أهم قرار للفرع الآن)
OPPORTUNITY: (أهم فرصة)
===END===

# ثم:
## ⚡ ملخص سريع
## 📊 قراءة مؤشرات الفرع مقابل متوسط الشركة
## 🔍 المشكلات وحلولها (لكل مشكلة: الأثر المالي + أكثر من حل + نسبة ثقة)
## 💡 الفرص
## 📅 خطوات الأسبوع القادم
## ✅ القرار النهائي للفرع"""


# ===== صفحات قسم الشركات =====
@app.get("/company-register.html")
def page_company_register():
    return FileResponse("company-register.html")

@app.get("/company-dashboard.html")
def page_company_dashboard():
    return FileResponse("company-dashboard.html")

@app.get("/company-input.html")
def page_company_input():
    return FileResponse("company-input.html")

@app.get("/company-report.html")
def page_company_report():
    return FileResponse("company-report.html")

@app.get("/company-branches.html")
def page_company_branches():
    return FileResponse("company-branches.html")

@app.get("/company-tax.html")
def page_company_tax():
    return FileResponse("company-tax.html")


# ===== الضريبة والزكاة: بيانات افتراضية من الشركة =====
@app.get("/company/tax-defaults")
def company_tax_defaults(user: User = Depends(get_current_user)):
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    with Session(engine) as s:
        company = s.get(Company, user.company_id)
        if not company or company.owner_id != user.id:
            raise HTTPException(403, "غير مصرّح")
        if company.is_active != 1:
            raise HTTPException(402, "شركتك قيد التفعيل — فعّلها من لوحة الإدارة")
        branches = s.exec(
            select(CompanyBranch).where(CompanyBranch.company_id == company.id, CompanyBranch.is_active == 1)
        ).all()
        sales = expenses = 0.0
        for b in branches:
            e = s.exec(
                select(CompanyEntry).where(CompanyEntry.branch_id == b.id).order_by(CompanyEntry.created_at.desc())
            ).first()
            if e:
                sales += e.sales
                expenses += e.expenses
        return {
            "company": company.name,
            "sales": round(sales),
            "expenses": round(expenses),
            "profit": round(sales - expenses),
        }


# ===== معلومات الشركة النشطة + قائمة الشركات (للتوجيه والتبديل) =====
@app.get("/company/info")
def company_info(user: User = Depends(get_current_user)):
    with Session(engine) as s:
        companies = s.exec(
            select(Company).where(Company.owner_id == user.id, Company.is_active == 1)
        ).all()
        if not companies:
            return {"has_company": False, "companies": [], "active": None,
                    "branches": [], "max_reached": False}

        active_id = user.company_id
        if active_id not in [c.id for c in companies]:
            active_id = companies[0].id
            udb = s.get(User, user.id)
            udb.company_id = active_id
            udb.company_role = "owner"
            s.add(udb)
            s.commit()

        comp_list = []
        for c in companies:
            bc = len(s.exec(
                select(CompanyBranch).where(CompanyBranch.company_id == c.id, CompanyBranch.is_active == 1)
            ).all())
            comp_list.append({"id": c.id, "name": c.name, "sector": c.sector,
                              "branch_count": bc, "active": c.id == active_id})

        active = next((c for c in companies if c.id == active_id), companies[0])
        branches = s.exec(
            select(CompanyBranch).where(CompanyBranch.company_id == active.id, CompanyBranch.is_active == 1)
        ).all()
        b_list = [{"id": b.id, "name": b.name, "city": b.city, "type": b.branch_type,
                   "target_sales": b.target_sales, "target_customers": b.target_customers}
                  for b in branches]

        return {
            "has_company": True,
            "max_reached": len(companies) >= 3,
            "companies": comp_list,
            "active": {"id": active.id, "name": active.name, "sector": active.sector, "is_active": active.is_active},
            "subscribed": active.is_active == 1,
            "branches": b_list,
        }


# ===== إنشاء شركة جديدة =====
@app.post("/company/create")
def company_create(data: dict, user: User = Depends(get_current_user)):
    name = (data.get("name") or "").strip()
    sector = (data.get("sector") or "retail").strip()
    branches_raw = data.get("branches", [])

    if not name:
        raise HTTPException(400, "اسم الشركة مطلوب")
    if not branches_raw:
        raise HTTPException(400, "أضف فرعاً واحداً على الأقل")

    with Session(engine) as s:
        existing = s.exec(
            select(Company).where(Company.owner_id == user.id, Company.is_active == 1)
        ).all()
        if len(existing) >= 3:
            raise HTTPException(400, "وصلت الحد الأقصى (3 شركات). احذف شركة لإضافة جديدة.")
        for c in existing:
            if c.name.strip().lower() == name.lower():
                raise HTTPException(400, f"لديك شركة بنفس الاسم '{name}'")

        company = Company(name=name, owner_id=user.id, sector=sector, is_active=0)
        s.add(company)
        s.commit()
        s.refresh(company)

        for i, b in enumerate(branches_raw):
            if isinstance(b, dict):
                bn = (b.get("name") or "").strip()
                city = (b.get("city") or "").strip()
                btype = (b.get("type") or "standalone").strip()
            else:
                bn = str(b).strip()
                city = ""
                btype = "standalone"
            if not bn:
                continue
            lat, lng = geocode_city(city, company.id + i + len(bn))
            s.add(CompanyBranch(company_id=company.id, name=bn, city=city,
                                branch_type=btype, lat=lat, lng=lng))

        udb = s.get(User, user.id)
        udb.company_id = company.id
        udb.company_role = "owner"
        s.add(udb)
        s.commit()

        log_activity(user.name, f"أنشأ شركة: {name}", user.email)
        return {"ok": True, "company_id": company.id, "message": f"تم إنشاء {name} بنجاح"}


# ===== تبديل الشركة النشطة =====
@app.post("/company/switch")
def company_switch(data: dict, user: User = Depends(get_current_user)):
    cid = data.get("company_id")
    with Session(engine) as s:
        company = s.get(Company, int(cid)) if cid else None
        if not company or company.owner_id != user.id:
            raise HTTPException(404, "الشركة غير موجودة")
        udb = s.get(User, user.id)
        udb.company_id = company.id
        s.add(udb)
        s.commit()
        return {"ok": True, "active": company.id, "name": company.name}


# ===== حذف الشركة النشطة + فروعها + بياناتها =====
@app.post("/company/delete")
def company_delete(data: dict = None, user: User = Depends(get_current_user)):
    with Session(engine) as s:
        cid = data.get("company_id") if data else None
        cid = int(cid) if cid else user.company_id
        if not cid:
            raise HTTPException(400, "لا توجد شركة")
        company = s.get(Company, cid)
        if not company or company.owner_id != user.id:
            raise HTTPException(403, "فقط المالك يحذف الشركة")

        for b in s.exec(select(CompanyBranch).where(CompanyBranch.company_id == cid)).all():
            s.delete(b)
        for e in s.exec(select(CompanyEntry).where(CompanyEntry.company_id == cid)).all():
            s.delete(e)
        s.delete(company)
        s.commit()

        udb = s.get(User, user.id)
        rest = s.exec(
            select(Company).where(Company.owner_id == user.id, Company.is_active == 1)
        ).all()
        udb.company_id = rest[0].id if rest else None
        s.add(udb)
        s.commit()

        log_activity(user.name, f"حذف الشركة: {company.name}", user.email)
        return {"ok": True}


# ===== إضافة فرع =====
@app.post("/company/add-branch")
def company_add_branch(data: dict, user: User = Depends(get_current_user)):
    if not user.company_id:
        raise HTTPException(400, "لا توجد شركة نشطة")
    name = (data.get("name") or "").strip()
    city = (data.get("city") or "").strip()
    btype = (data.get("type") or "standalone").strip()
    if not name:
        raise HTTPException(400, "اسم الفرع مطلوب")
    with Session(engine) as s:
        company = s.get(Company, user.company_id)
        if not company or company.owner_id != user.id:
            raise HTTPException(403, "غير مصرّح")
        if company.is_active != 1:
            raise HTTPException(402, "شركتك قيد التفعيل — فعّلها من لوحة الإدارة")
        lat, lng = geocode_city(city, company.id + len(name) + 7)
        b = CompanyBranch(company_id=company.id, name=name, city=city,
                          branch_type=btype, lat=lat, lng=lng)
        s.add(b)
        s.commit()
        s.refresh(b)
        log_activity(user.name, f"أضاف فرع: {name}", user.email)
        return {"ok": True, "branch_id": b.id}


# ===== حذف فرع =====
@app.post("/company/remove-branch")
def company_remove_branch(data: dict, user: User = Depends(get_current_user)):
    bid = data.get("branch_id")
    with Session(engine) as s:
        b = s.get(CompanyBranch, int(bid)) if bid else None
        if not b:
            raise HTTPException(404, "الفرع غير موجود")
        company = s.get(Company, b.company_id)
        if not company or company.owner_id != user.id:
            raise HTTPException(403, "غير مصرّح")
        for e in s.exec(select(CompanyEntry).where(CompanyEntry.branch_id == b.id)).all():
            s.delete(e)
        s.delete(b)
        s.commit()
        return {"ok": True}


# ===== ضبط هدف الفرع (الأهداف) =====
@app.post("/company/set-target")
def company_set_target(data: dict, user: User = Depends(get_current_user)):
    bid = data.get("branch_id")
    with Session(engine) as s:
        b = s.get(CompanyBranch, int(bid)) if bid else None
        if not b:
            raise HTTPException(404, "الفرع غير موجود")
        company = s.get(Company, b.company_id)
        if not company or company.owner_id != user.id:
            raise HTTPException(403, "غير مصرّح")
        if company.is_active != 1:
            raise HTTPException(402, "شركتك قيد التفعيل — فعّلها من لوحة الإدارة")
        if "target_sales" in data:
            b.target_sales = float(data.get("target_sales") or 0)
        if "target_customers" in data:
            b.target_customers = int(data.get("target_customers") or 0)
        s.add(b)
        s.commit()
        return {"ok": True}


# ===== إدخال بيانات دورية لفرع (يبدأ الحساب فوراً) =====
@app.post("/company/entry")
def company_entry(data: dict, user: User = Depends(get_current_user)):
    bid = data.get("branch_id")
    with Session(engine) as s:
        branch = s.get(CompanyBranch, int(bid)) if bid else None
        if not branch:
            raise HTTPException(404, "الفرع غير موجود — اكتب اسم فرع صحيح أو أضف فرعاً جديداً")
        company = s.get(Company, branch.company_id)
        if not company or company.owner_id != user.id:
            raise HTTPException(403, "غير مصرّح بهذا الفرع")
        if company.is_active != 1:
            raise HTTPException(402, "شركتك قيد التفعيل — فعّلها من لوحة الإدارة")

        try:
            period = (str(data.get("period") or datetime.now().strftime("%Y-%m"))).strip()
            sales = float(data.get("sales") or 0)
            invoices = int(float(data.get("invoices") or 0))
            customers = int(float(data.get("customers") or 0))
            new_customers = int(float(data.get("new_customers") or 0))
            repeat_customers = int(float(data.get("repeat_customers") or 0))
            expenses = float(data.get("expenses") or 0)
            deposited = float(data.get("deposited") or 0)
            discounts = float(data.get("discounts") or 0)
            top_products = (str(data.get("top_products") or "")).strip()
            notes = (str(data.get("notes") or "")).strip()
            extra = data.get("extra") or {}
            extra_data = json.dumps(extra, ensure_ascii=False) if isinstance(extra, dict) and extra else ""
        except (ValueError, TypeError):
            raise HTTPException(400, "فيه قيمة غير رقمية في الحقول — تأكد أن المبالغ والأعداد أرقام صحيحة")

        if sales <= 0:
            raise HTTPException(400, "أدخل قيمة مبيعات صحيحة (أكبر من صفر)")

        prev = s.exec(
            select(CompanyEntry).where(CompanyEntry.branch_id == branch.id).order_by(CompanyEntry.created_at.desc())
        ).first()
        prev_sales = prev.sales if prev else None

        m = compute_company_metrics(sales, invoices, customers, repeat_customers, expenses, prev_sales)

        def build_entry():
            return CompanyEntry(
                company_id=company.id, branch_id=branch.id, branch_name=branch.name, period=period,
                sales=sales, invoices=invoices, customers=customers, new_customers=new_customers,
                repeat_customers=repeat_customers, expenses=expenses, discounts=discounts,
                deposited=deposited, top_products=top_products, notes=notes,
                extra_data=extra_data,
                profit=m["profit"], margin=m["margin"], avg_invoice=m["avg_invoice"],
                repeat_rate=m["repeat_rate"], growth=m["growth"], branch_score=m["branch_score"],
            )

        try:
            entry = build_entry()
            s.add(entry)
            s.commit()
            s.refresh(entry)
        except Exception as e:
            s.rollback()
            # محاولة إصلاح ذاتي: عمود ناقص؟ شغّل الترحيل وأعد المحاولة مرة
            try:
                run_migrations()
                entry = build_entry()
                s.add(entry)
                s.commit()
                s.refresh(entry)
            except Exception as e2:
                raise HTTPException(500, f"تعذّر حفظ البيانات: {str(e2)[:180]}")

        log_activity(user.name, f"أدخل بيانات فرع {branch.name} ({period})", user.email)
        return {"ok": True, "entry_id": entry.id, "branch": branch.name, "metrics": m}


# ===== لوحة المدير التنفيذي — كل المؤشرات في استجابة واحدة =====
@app.get("/company/dashboard")
def company_dashboard(user: User = Depends(get_current_user)):
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    with Session(engine) as s:
        company = s.get(Company, user.company_id)
        if not company or company.owner_id != user.id:
            raise HTTPException(403, "غير مصرّح")
        if company.is_active != 1:
            raise HTTPException(402, "شركتك قيد التفعيل — فعّلها من لوحة الإدارة")

        branches = s.exec(
            select(CompanyBranch).where(CompanyBranch.company_id == company.id, CompanyBranch.is_active == 1)
        ).all()

        branch_data = []
        for b in branches:
            entries = s.exec(
                select(CompanyEntry).where(CompanyEntry.branch_id == b.id).order_by(CompanyEntry.created_at.desc())
            ).all()
            if not entries:
                branch_data.append({
                    "id": b.id, "name": b.name, "city": b.city, "type": b.branch_type,
                    "lat": b.lat, "lng": b.lng, "has_data": False,
                    "score": 0, "level": "بدون بيانات", "color": "#94a3b8",
                })
                continue
            latest = entries[0]
            prev = entries[1] if len(entries) > 1 else None
            level, color = score_level(latest.branch_score)
            trend = "same"
            score_change = 0
            if prev:
                score_change = latest.branch_score - prev.branch_score
                trend = "up" if score_change > 0 else ("down" if score_change < 0 else "same")
            branch_data.append({
                "id": b.id, "name": b.name, "city": b.city, "type": b.branch_type,
                "lat": b.lat, "lng": b.lng, "has_data": True,
                "sales": round(latest.sales), "customers": latest.customers, "invoices": latest.invoices,
                "avg_invoice": latest.avg_invoice, "margin": latest.margin, "profit": round(latest.profit),
                "expenses": round(latest.expenses), "repeat_rate": latest.repeat_rate, "growth": latest.growth,
                "score": latest.branch_score, "level": level, "color": color,
                "trend": trend, "score_change": score_change,
                "top_products": latest.top_products, "period": latest.period,
                "target_sales": round(b.target_sales),
                "target_pct": round((latest.sales / b.target_sales) * 100, 1) if b.target_sales > 0 else 0,
                "history": [{"period": e.period, "sales": round(e.sales), "score": e.branch_score,
                             "margin": e.margin, "customers": e.customers} for e in reversed(entries)],
            })

        active = [b for b in branch_data if b["has_data"]]
        total_sales = sum(b["sales"] for b in active)
        total_customers = sum(b["customers"] for b in active)
        total_invoices = sum(b["invoices"] for b in active)
        total_profit = sum(b["profit"] for b in active)
        total_expenses = sum(b["expenses"] for b in active)
        avg_invoice = round(total_sales / total_invoices, 1) if total_invoices > 0 else 0
        avg_margin = round((total_profit / total_sales) * 100, 1) if total_sales > 0 else 0
        overall_score = round(sum(b["score"] for b in active) / len(active)) if active else 0

        ranking = sorted(active, key=lambda x: x["score"], reverse=True)
        best = ranking[0] if ranking else None
        worst = ranking[-1] if len(ranking) > 1 else None

        # تحليل الأسباب (الفرع الأضعف مقابل متوسط الشركة)
        root_cause = None
        if worst and len(active) > 1:
            cnt = len(active)
            avg_sales = total_sales / cnt
            avg_cust = total_customers / cnt
            avg_rep = sum(b["repeat_rate"] for b in active) / cnt

            def pct_diff(val, avg):
                return round(((val - avg) / avg) * 100) if avg > 0 else 0

            factors = [
                {"label": "متوسط الفاتورة", "diff": pct_diff(worst["avg_invoice"], avg_invoice)},
                {"label": "عدد العملاء", "diff": pct_diff(worst["customers"], avg_cust)},
                {"label": "العملاء المتكررون", "diff": pct_diff(worst["repeat_rate"], avg_rep)},
                {"label": "المبيعات", "diff": pct_diff(worst["sales"], avg_sales)},
            ]
            factors = sorted([f for f in factors if f["diff"] < 0], key=lambda f: f["diff"])
            root_cause = {"branch": worst["name"], "factors": factors[:4]}

        # التنبؤ بالأداء (30 يوم) لكل فرع له تاريخ كافٍ
        forecast = []
        for b in active:
            hist = [h["sales"] for h in b["history"]]
            if len(hist) >= 2:
                f = build_forecast(hist[:-1], hist[-1])
                if f:
                    forecast.append({
                        "branch": b["name"], "rate": f["avg_rate"],
                        "next_cons": f["next_month_cons"], "next_opt": f["next_month_opt"],
                        "dir": "up" if f["avg_rate"] >= 0 else "down",
                    })

        # مقارنة الفروع المتشابهة (حسب النوع)
        groups = {}
        for b in active:
            groups.setdefault(b["type"], []).append(b)
        similar = []
        for gtype, items in groups.items():
            if len(items) >= 2:
                items_sorted = sorted(items, key=lambda x: x["score"], reverse=True)
                similar.append({
                    "type": gtype,
                    "branches": [{"name": x["name"], "score": x["score"], "sales": x["sales"]} for x in items_sorted],
                })

        excellent = len([b for b in active if b["score"] >= 70])
        good = len([b for b in active if 55 <= b["score"] < 70])
        mid = len([b for b in active if 40 <= b["score"] < 55])
        weak = len([b for b in active if b["score"] < 40])

        return {
            "company": {"id": company.id, "name": company.name, "sector": company.sector},
            "has_data": len(active) > 0,
            "summary": {
                "total_sales": round(total_sales), "total_customers": total_customers,
                "total_invoices": total_invoices, "total_profit": round(total_profit),
                "total_expenses": round(total_expenses), "avg_invoice": avg_invoice,
                "avg_margin": avg_margin, "overall_score": overall_score,
                "branch_count": len(branches), "active_count": len(active),
                "best_branch": best["name"] if best else "", "worst_branch": worst["name"] if worst else "",
                "excellent": excellent, "good": good, "mid": mid, "weak": weak,
            },
            "branches": branch_data,
            "ranking": ranking,
            "root_cause": root_cause,
            "best_practice": best,
            "forecast": forecast,
            "similar": similar,
        }


# ===== تفاصيل فرع واحد + تاريخه =====
@app.get("/company/branch/{branch_id}")
def company_branch_detail(branch_id: int, user: User = Depends(get_current_user)):
    with Session(engine) as s:
        b = s.get(CompanyBranch, branch_id)
        if not b:
            raise HTTPException(404, "الفرع غير موجود")
        company = s.get(Company, b.company_id)
        if not company or company.owner_id != user.id:
            raise HTTPException(403, "غير مصرّح")
        if company.is_active != 1:
            raise HTTPException(402, "شركتك قيد التفعيل — فعّلها من لوحة الإدارة")
        entries = s.exec(
            select(CompanyEntry).where(CompanyEntry.branch_id == b.id).order_by(CompanyEntry.created_at)
        ).all()
        latest = entries[-1] if entries else None
        return {
            "branch": {"id": b.id, "name": b.name, "city": b.city, "type": b.branch_type,
                       "target_sales": b.target_sales, "target_customers": b.target_customers},
            "latest": ({
                "period": latest.period, "sales": round(latest.sales), "customers": latest.customers,
                "invoices": latest.invoices, "avg_invoice": latest.avg_invoice, "margin": latest.margin,
                "profit": round(latest.profit), "repeat_rate": latest.repeat_rate, "growth": latest.growth,
                "score": latest.branch_score, "top_products": latest.top_products,
                "smart_message": latest.smart_message,
            } if latest else None),
            "history": [{"period": e.period, "sales": round(e.sales), "score": e.branch_score,
                         "margin": e.margin, "customers": e.customers} for e in entries],
        }


# ===== تحليل Gemini (للشركة كاملة أو لفرع) =====
@app.post("/company/analyze")
def company_analyze(data: dict, user: User = Depends(get_current_user)):
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    scope = (data.get("scope") or "company").strip()
    with Session(engine) as s:
        company = s.get(Company, user.company_id)
        if not company or company.owner_id != user.id:
            raise HTTPException(403, "غير مصرّح")
        if company.is_active != 1:
            raise HTTPException(402, "شركتك قيد التفعيل — فعّلها من لوحة الإدارة")
        branches = s.exec(
            select(CompanyBranch).where(CompanyBranch.company_id == company.id, CompanyBranch.is_active == 1)
        ).all()
        rows = []
        for b in branches:
            e = s.exec(
                select(CompanyEntry).where(CompanyEntry.branch_id == b.id).order_by(CompanyEntry.created_at.desc())
            ).first()
            if e:
                rows.append((b, e))
        if not rows:
            raise HTTPException(400, "لا توجد بيانات كافية. أدخل بيانات الفروع أولاً.")

        sector_name = SECTOR_NAMES.get(company.sector, "شركة")

        # === تحليل وحدة محددة فقط ===
        if scope == "module":
            module = (data.get("module") or "").strip()
            if module not in ALLOWED_MODULES:
                raise HTTPException(400, "وحدة غير معروفة")
            mlabel = MODULE_LABEL.get(module, module)
            # نجمع آخر إدخال للوحدة (مستوى الشركة + كل فرع)
            module_rows = s.exec(
                select(CompanyModuleEntry).where(
                    CompanyModuleEntry.company_id == company.id,
                    CompanyModuleEntry.module == module,
                ).order_by(CompanyModuleEntry.created_at.desc())
            ).all()
            seen = set(); module_snapshots = []
            for me in module_rows:
                key = me.branch_id or 0
                if key in seen: continue
                seen.add(key)
                try:
                    md = json.loads(me.data) if me.data else {}
                except Exception:
                    md = {}
                if md:
                    if me.branch_id:
                        br = s.get(CompanyBranch, me.branch_id)
                        scope_name = f"فرع {br.name}" if br else f"فرع #{me.branch_id}"
                    else:
                        scope_name = "على مستوى الشركة"
                    module_snapshots.append((scope_name, me.period, md))
            if not module_snapshots:
                raise HTTPException(400, f"لا توجد بيانات في وحدة {mlabel} — احفظ بيانات الوحدة أولاً")

            # ملخّص مالي عام مختصر (سياق ضروري)
            total_sales = sum(r[1].sales for r in rows)
            total_profit = sum(r[1].profit for r in rows)
            margin = round((total_profit / total_sales) * 100, 1) if total_sales > 0 else 0

            lines = [f"السياق: شركة \"{company.name}\" في قطاع {sector_name}، {len(rows)} فروع، إجمالي مبيعات {round(total_sales)} ريال، هامش الربح {margin}%.",
                     "",
                     f"بيانات وحدة \"{mlabel}\" (آخر إدخال):"]
            for sn, period, md in module_snapshots:
                lines.append(f"\n— {sn} ({period}):")
                for k, v in md.items():
                    lines.append(f"  • {k}: {v}")
            data_block = "\n".join(lines)

            prompt = f"""أنت مستشار تنفيذي متخصّص في "{mlabel}" تتحدث بالعربية بأسلوب محترف ومباشر.

{data_block}

اكتب تحليلاً مركّزاً على وحدة "{mlabel}" فقط (لا تحلل الفروع أو الشركة بشكل عام). يتضمن:
1. **القراءة السريعة** — جملتان تلخّصان وضع هذه الوحدة.
2. **أبرز ٢-٣ مؤشرات قوية** أو إيجابية في الوحدة.
3. **أبرز ٢-٣ مخاطر أو ثغرات** يجب الانتباه لها.
4. **القرارات الموصى بها** — ٣ قرارات تنفيذية مرتبة بحسب الأولوية ضمن نطاق هذه الوحدة فقط.

اكتب بصيغة Markdown، عناوين ## واضحة، نقاط مرتّبة، أرقام محددة كلما أمكن. لا تخرج عن نطاق وحدة {mlabel}."""
            txt = company_gemini(prompt)
            if txt:
                clean, _a, _d, _o = extract_exec(txt)
                txt = clean
            log_activity(user.name, f"حلّل وحدة {mlabel}", user.email)
            return {"ok": True, "scope": "module", "module": module, "label": mlabel,
                    "analysis": txt or "تعذّر توليد التحليل، حاول بعد قليل."}

        if scope == "branch":
            bid = int(data.get("branch_id") or 0)
            target = next(((b, e) for (b, e) in rows if b.id == bid), None)
            if not target:
                raise HTTPException(400, "لا توجد بيانات لهذا الفرع")
            b, e = target
            n = len(rows)
            avg_margin = round(sum(x[1].margin for x in rows) / n, 1)
            avg_inv = round(sum(x[1].avg_invoice for x in rows) / n, 1)
            avg_score = round(sum(x[1].branch_score for x in rows) / n)
            hist = s.exec(
                select(CompanyEntry).where(CompanyEntry.branch_id == b.id).order_by(CompanyEntry.created_at)
            ).all()
            hist_txt = " ← ".join(f"{h.period}: {round(h.sales)}ر ({h.branch_score}/100)" for h in hist[-6:]) or "فترة واحدة"
            prompt = build_branch_prompt(company, sector_name, b, e, avg_margin, avg_inv, avg_score, hist_txt)
            txt = company_gemini(prompt)
            if txt:
                clean, _a, _d, _o = extract_exec(txt)
                txt = clean
                e.smart_message = clean
                s.add(e)
                s.commit()
            return {"ok": True, "scope": "branch", "branch": b.name,
                    "analysis": txt or "تعذّر توليد التحليل، حاول بعد قليل."}

        prompt = build_company_prompt(company, sector_name, rows)
        txt = company_gemini(prompt)
        if txt:
            clean, _a, _d, _o = extract_exec(txt)
            txt = clean
        log_activity(user.name, f"ولّد تحليل شركة: {company.name}", user.email)
        return {"ok": True, "scope": "company", "company": company.name,
                "analysis": txt or "تعذّر توليد التحليل، حاول بعد قليل."}


# ===== اسأل نبّاه الذكي (صندوق المحادثة) =====
@app.post("/company/ask")
def company_ask(data: dict, user: User = Depends(get_current_user)):
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    q = (data.get("question") or "").strip()
    if not q:
        raise HTTPException(400, "اكتب سؤالك")
    with Session(engine) as s:
        company = s.get(Company, user.company_id)
        if not company or company.owner_id != user.id:
            raise HTTPException(403, "غير مصرّح")
        if company.is_active != 1:
            raise HTTPException(402, "شركتك قيد التفعيل — فعّلها من لوحة الإدارة")
        branches = s.exec(
            select(CompanyBranch).where(CompanyBranch.company_id == company.id, CompanyBranch.is_active == 1)
        ).all()
        rows = []
        for b in branches:
            e = s.exec(
                select(CompanyEntry).where(CompanyEntry.branch_id == b.id).order_by(CompanyEntry.created_at.desc())
            ).first()
            if e:
                rows.append(f"- {b.name} ({b.city or '—'}): مبيعات {round(e.sales)}ر، عملاء {e.customers}، "
                            f"هامش {e.margin}%، تكرار {e.repeat_rate}%، مؤشر {e.branch_score}/100")
        context = "\n".join(rows) if rows else "لا توجد بيانات فروع بعد."
        prompt = f"""أنت "نبّاه"، مساعد تحليلي لشركة "{company.name}". أجب عن سؤال المالك بدقة واختصار اعتماداً على بيانات الفروع التالية فقط. لا تخترع أرقاماً، وإن لم تكفِ البيانات قل ذلك صراحة. بالعربية ولهجة مهنية واضحة.

# بيانات الفروع (آخر فترة لكل فرع):
{context}

# سؤال المالك:
{q}

أجب مباشرة، ومتى ما ناسب اذكر أرقاماً داعمة وخطوة عملية واحدة."""
        txt = company_gemini(prompt)
        return {"ok": True, "answer": txt or "تعذّر توليد الإجابة، حاول بعد قليل."}


# ===== بيانات التقرير التنفيذي (للطباعة) =====
@app.get("/company/report")
def company_report(user: User = Depends(get_current_user)):
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    with Session(engine) as s:
        company = s.get(Company, user.company_id)
        if not company or company.owner_id != user.id:
            raise HTTPException(403, "غير مصرّح")
        if company.is_active != 1:
            raise HTTPException(402, "شركتك قيد التفعيل — فعّلها من لوحة الإدارة")
        branches = s.exec(
            select(CompanyBranch).where(CompanyBranch.company_id == company.id, CompanyBranch.is_active == 1)
        ).all()
        rows = []
        for b in branches:
            e = s.exec(
                select(CompanyEntry).where(CompanyEntry.branch_id == b.id).order_by(CompanyEntry.created_at.desc())
            ).first()
            if e:
                rows.append((b, e))
        if not rows:
            return {"company": {"name": company.name}, "empty": True}

        n = len(rows)
        total_sales = sum(e.sales for _, e in rows)
        total_profit = sum(e.profit for _, e in rows)
        total_customers = sum(e.customers for _, e in rows)
        avg_margin = round((total_profit / total_sales) * 100, 1) if total_sales else 0
        overall = round(sum(e.branch_score for _, e in rows) / n)
        ranked = sorted(rows, key=lambda x: x[1].branch_score, reverse=True)
        best = ranked[0]
        worst = ranked[-1]

        branch_rows = [{
            "name": b.name, "city": b.city, "sales": round(e.sales), "customers": e.customers,
            "margin": e.margin, "score": e.branch_score, "growth": e.growth,
            "level": score_level(e.branch_score)[0],
        } for b, e in ranked]

        key_decisions = []
        if worst[1].branch_score < 45:
            key_decisions.append({
                "priority": "عاجل",
                "title": f"تدخّل فوري في فرع {worst[0].name}",
                "detail": f"مؤشره {worst[1].branch_score}/100 وهامشه {worst[1].margin}٪ — يحتاج مراجعة شاملة للمبيعات والمصروفات.",
            })
        if best[1].branch_score >= 70:
            key_decisions.append({
                "priority": "فرصة",
                "title": f"تعميم نموذج فرع {best[0].name}",
                "detail": f"الأعلى أداءً ({best[1].branch_score}/100). ادرس أسلوبه وطبّقه على باقي الفروع.",
            })
        if avg_margin < 18:
            key_decisions.append({
                "priority": "مهم",
                "title": "متوسط هامش الشركة منخفض",
                "detail": f"الهامش العام {avg_margin}٪ — راجع التسعير وهيكل التكاليف عبر الفروع.",
            })

        from datetime import datetime as _dt
        return {
            "company": {"name": company.name, "sector": SECTOR_NAMES.get(company.sector, "شركة")},
            "generated_at": _dt.now().strftime("%Y-%m-%d %H:%M"),
            "empty": False,
            "summary": {
                "total_sales": round(total_sales), "total_profit": round(total_profit),
                "total_customers": total_customers, "avg_margin": avg_margin,
                "overall_score": overall, "branch_count": n,
                "best_branch": best[0].name, "worst_branch": worst[0].name,
            },
            "branches": branch_rows,
            "key_decisions": key_decisions,
        }


# ============================================================
# ===== الخدمة 5: التدفق النقدي التنبؤي (Cash Runway) =====
# ============================================================

AR_MONTHS = ["", "يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
             "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"]


def _avg_recent(entries, attr, k=3):
    """متوسط آخر k قيم لخاصية معيّنة (لتقدير شهري مستقر)."""
    vals = [getattr(e, attr) for e in entries[-k:]] if entries else []
    return (sum(vals) / len(vals)) if vals else 0


def company_monthly_estimate(s, company_id):
    """يقدّر المبيعات/المصروفات/الربح الشهرية للشركة من متوسط آخر فترات كل فرع."""
    branches = s.exec(
        select(CompanyBranch).where(CompanyBranch.company_id == company_id, CompanyBranch.is_active == 1)
    ).all()
    sales = expenses = profit = 0.0
    have_data = False
    for b in branches:
        ents = s.exec(
            select(CompanyEntry).where(CompanyEntry.branch_id == b.id).order_by(CompanyEntry.created_at)
        ).all()
        if not ents:
            continue
        have_data = True
        sales += _avg_recent(ents, "sales")
        expenses += _avg_recent(ents, "expenses")
        profit += _avg_recent(ents, "profit")
    return {"sales": round(sales), "expenses": round(expenses), "profit": round(profit), "have_data": have_data}


@app.post("/company/financials")
def company_financials(data: dict, user: User = Depends(get_current_user)):
    """ضبط الاحتياطي النقدي والالتزامات الشهرية."""
    if not user.company_id:
        raise HTTPException(400, "لا توجد شركة نشطة")
    with Session(engine) as s:
        company = s.get(Company, user.company_id)
        if not company or company.owner_id != user.id:
            raise HTTPException(403, "غير مصرّح")
        if company.is_active != 1:
            raise HTTPException(402, "شركتك قيد التفعيل — فعّلها من لوحة الإدارة")
        if "cash_reserve" in data:
            company.cash_reserve = float(data.get("cash_reserve") or 0)
        if "monthly_obligations" in data:
            company.monthly_obligations = float(data.get("monthly_obligations") or 0)
        s.add(company)
        s.commit()
        return {"ok": True, "cash_reserve": company.cash_reserve, "monthly_obligations": company.monthly_obligations}


@app.get("/company/cashflow")
def company_cashflow(user: User = Depends(get_current_user)):
    """التدفق النقدي التنبؤي: كم شهر تكفي السيولة + نقطة العجز المتوقّعة."""
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    with Session(engine) as s:
        company = s.get(Company, user.company_id)
        if not company or company.owner_id != user.id:
            raise HTTPException(403, "غير مصرّح")
        if company.is_active != 1:
            raise HTTPException(402, "شركتك قيد التفعيل — فعّلها من لوحة الإدارة")

        est = company_monthly_estimate(s, company.id)
        reserve = company.cash_reserve or 0
        obligations = company.monthly_obligations or 0

        # صافي التدفق الشهري = ربح التشغيل − الالتزامات الثابتة
        monthly_net = round(est["profit"] - obligations)

        needs_setup = (reserve <= 0 and obligations <= 0)

        # حالة + runway
        runway_months = None
        deficit_label = None
        if monthly_net >= 0:
            status = "positive"
            # أشهر الأمان لو توقّف الدخل تماماً
            safety_months = round(reserve / obligations, 1) if obligations > 0 else None
        else:
            burn = abs(monthly_net)
            runway_months = round(reserve / burn, 1) if burn > 0 else None
            safety_months = runway_months
            if runway_months is None:
                status = "unknown"
            elif runway_months < 3:
                status = "critical"
            elif runway_months < 6:
                status = "warning"
            else:
                status = "watch"

        # إسقاط رصيد السيولة 12 شهر
        now = datetime.now()
        projection = []
        bal = reserve
        deficit_index = None
        for i in range(0, 13):
            m = ((now.month - 1 + i) % 12) + 1
            y = now.year + ((now.month - 1 + i) // 12)
            if i > 0:
                bal += monthly_net
            projection.append({"i": i, "label": f"{AR_MONTHS[m]} {y}", "short": f"{m}/{y}", "balance": round(bal)})
            if deficit_index is None and bal < 0 and i > 0:
                deficit_index = i
                deficit_label = f"{AR_MONTHS[m]} {y}"

        alert = None
        if status == "critical":
            alert = f"⚠️ تحذير حرج: السيولة تكفي {runway_months} شهر فقط. أول عجز متوقّع في {deficit_label}."
        elif status == "warning":
            alert = f"انتبه: السيولة تكفي {runway_months} شهر. راقب المصروفات قبل {deficit_label}."

        return {
            "company": {"name": company.name},
            "needs_setup": needs_setup,
            "has_data": est["have_data"],
            "cash_reserve": round(reserve),
            "monthly_obligations": round(obligations),
            "monthly_sales": est["sales"],
            "monthly_expenses": est["expenses"],
            "monthly_profit": est["profit"],
            "monthly_net": monthly_net,
            "status": status,
            "runway_months": runway_months,
            "safety_months": safety_months,
            "deficit_label": deficit_label,
            "deficit_index": deficit_index,
            "projection": projection,
            "alert": alert,
        }


# ============================================================
# ===== الخدمة 6: كشف التسرّب والاحتيال (Leakage Detection) =====
# ============================================================

def detect_leakage(entries, company_expense_ratio):
    """يحلّل تاريخ فرع ويرجّع درجة مخاطرة + أسباب الاشتباه.
    entries: مرتّبة زمنياً تصاعدياً."""
    if not entries:
        return {"risk": 0, "reasons": []}
    latest = entries[-1]
    prior = entries[:-1]
    reasons = []
    risk = 0

    # متوسطات تاريخية (قبل آخر فترة)
    avg_exp = _avg_recent(prior, "expenses") if prior else latest.expenses
    avg_sales = _avg_recent(prior, "sales") if prior else latest.sales
    avg_margin = (sum(e.margin for e in prior) / len(prior)) if prior else latest.margin

    sales_growth = ((latest.sales - avg_sales) / avg_sales * 100) if avg_sales > 0 else 0
    exp_growth = ((latest.expenses - avg_exp) / avg_exp * 100) if avg_exp > 0 else 0

    # 1) قفزة مصروفات بلا مبيعات مقابلة
    if prior and exp_growth >= 20 and sales_growth < (exp_growth - 15):
        risk += 30
        reasons.append({"type": "قفزة مصروفات", "severity": "high",
                        "detail": f"المصروفات ارتفعت {round(exp_growth)}% بينما المبيعات تغيّرت {round(sales_growth)}% فقط."})

    # 2) فجوة بيع-إيداع
    if latest.deposited and latest.deposited > 0 and latest.sales > 0:
        gap = (latest.sales - latest.deposited) / latest.sales * 100
        if gap >= 5:
            risk += 35
            reasons.append({"type": "فجوة بيع-إيداع", "severity": "high",
                            "detail": f"المبيعات {round(latest.sales)}ر والمُودَع {round(latest.deposited)}ر — فجوة {round(gap)}%."})

    # 3) انهيار الهامش
    if prior and (avg_margin - latest.margin) >= 10:
        risk += 20
        reasons.append({"type": "تراجع الهامش", "severity": "medium",
                        "detail": f"الهامش نزل من {round(avg_margin)}% إلى {latest.margin}% (−{round(avg_margin - latest.margin)} نقطة)."})

    # 4) خصومات مرتفعة
    if latest.sales > 0 and latest.discounts > 0:
        disc_ratio = latest.discounts / latest.sales * 100
        if disc_ratio >= 15:
            risk += 15
            reasons.append({"type": "خصومات مرتفعة", "severity": "medium",
                            "detail": f"الخصومات {round(disc_ratio)}% من المبيعات."})

    # 5) هبوط مبيعات مع ثبات/ارتفاع المصروفات
    if prior and sales_growth <= -15 and exp_growth >= -3:
        risk += 20
        reasons.append({"type": "هبوط مبيعات بلا خفض تكاليف", "severity": "medium",
                        "detail": f"المبيعات نزلت {round(abs(sales_growth))}% والمصروفات ثابتة تقريباً."})

    # 6) نسبة مصروفات أعلى بكثير من متوسط الشركة
    if latest.sales > 0:
        br_ratio = latest.expenses / latest.sales * 100
        if company_expense_ratio > 0 and br_ratio >= company_expense_ratio + 15:
            risk += 15
            reasons.append({"type": "مصروفات أعلى من الشركة", "severity": "low",
                            "detail": f"نسبة مصروفات الفرع {round(br_ratio)}% مقابل {round(company_expense_ratio)}% متوسط الشركة."})

    return {"risk": min(risk, 100), "reasons": reasons}


@app.get("/company/leakage")
def company_leakage(user: User = Depends(get_current_user)):
    """كشف الفروع المشبوهة (تسرّب/احتيال) وترتيبها حسب درجة المخاطرة."""
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    with Session(engine) as s:
        company = s.get(Company, user.company_id)
        if not company or company.owner_id != user.id:
            raise HTTPException(403, "غير مصرّح")
        if company.is_active != 1:
            raise HTTPException(402, "شركتك قيد التفعيل — فعّلها من لوحة الإدارة")
        branches = s.exec(
            select(CompanyBranch).where(CompanyBranch.company_id == company.id, CompanyBranch.is_active == 1)
        ).all()

        # متوسط نسبة المصروفات للشركة
        tot_sales = tot_exp = 0.0
        per_branch = []
        for b in branches:
            ents = s.exec(
                select(CompanyEntry).where(CompanyEntry.branch_id == b.id).order_by(CompanyEntry.created_at)
            ).all()
            if ents:
                tot_sales += ents[-1].sales
                tot_exp += ents[-1].expenses
            per_branch.append((b, ents))
        company_exp_ratio = (tot_exp / tot_sales * 100) if tot_sales > 0 else 0

        results = []
        for b, ents in per_branch:
            if not ents:
                results.append({"id": b.id, "name": b.name, "city": b.city,
                                "risk": 0, "level": "بدون بيانات", "color": "#94a3b8",
                                "single": len(ents) < 2, "reasons": []})
                continue
            r = detect_leakage(ents, company_exp_ratio)
            risk = r["risk"]
            if risk >= 60:
                level, color = "خطر مرتفع", "#ef4444"
            elif risk >= 30:
                level, color = "اشتباه متوسط", "#f59e0b"
            elif risk >= 1:
                level, color = "اشتباه منخفض", "#f5b301"
            else:
                level, color = "سليم", "#10b981"
            results.append({
                "id": b.id, "name": b.name, "city": b.city,
                "risk": risk, "level": level, "color": color,
                "single": len(ents) < 2, "reasons": r["reasons"],
            })

        results.sort(key=lambda x: x["risk"], reverse=True)
        flagged = len([x for x in results if x["risk"] >= 30])
        return {
            "company": {"name": company.name},
            "company_expense_ratio": round(company_exp_ratio, 1),
            "flagged_count": flagged,
            "branches": results,
        }


# ===== صفحات الخدمتين =====
@app.get("/company-cashflow.html")
def page_company_cashflow():
    return FileResponse("company-cashflow.html")

@app.get("/company-leakage.html")
def page_company_leakage():
    return FileResponse("company-leakage.html")


# ============================================================
# ===== الخدمة 3: صلاحيات الفريق (Team Permissions) =====
# ============================================================

ROLE_INFO = {
    "owner":      {"label": "مالك", "perms": "كل الصلاحيات: إدارة الشركة والفروع والفريق وكل التحليلات."},
    "manager":    {"label": "مدير فرع", "perms": "إدخال بيانات فرعه ومتابعة أدائه وتحليلاته."},
    "accountant": {"label": "محاسب", "perms": "الاطّلاع على التقارير المالية والتدفق النقدي وكشف التسرّب."},
    "staff":      {"label": "موظف", "perms": "إدخال البيانات التشغيلية فقط."},
}


@app.get("/company/team")
def company_team(user: User = Depends(get_current_user)):
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    with Session(engine) as s:
        company = s.get(Company, user.company_id)
        if not company or company.owner_id != user.id:
            raise HTTPException(403, "غير مصرّح")
        if company.is_active != 1:
            raise HTTPException(402, "شركتك قيد التفعيل — فعّلها من لوحة الإدارة")
        owner = s.get(User, company.owner_id)
        branches = s.exec(
            select(CompanyBranch).where(CompanyBranch.company_id == company.id, CompanyBranch.is_active == 1)
        ).all()
        branch_map = {b.id: b.name for b in branches}
        members = s.exec(select(CompanyMember).where(CompanyMember.company_id == company.id)).all()
        team = [{
            "id": "owner", "name": (owner.name if owner else "المالك"), "email": (owner.email if owner else ""),
            "role": "owner", "role_label": "مالك", "perms": ROLE_INFO["owner"]["perms"],
            "branch": "", "removable": False,
        }]
        for m in members:
            ri = ROLE_INFO.get(m.role, ROLE_INFO["staff"])
            team.append({
                "id": m.id, "name": m.name, "email": m.email, "role": m.role,
                "role_label": ri["label"], "perms": ri["perms"],
                "branch": branch_map.get(m.branch_id, "") if m.branch_id else "",
                "removable": True,
            })
        return {
            "company": {"name": company.name},
            "team": team,
            "roles": [{"key": k, "label": v["label"], "perms": v["perms"]} for k, v in ROLE_INFO.items() if k != "owner"],
            "branches": [{"id": b.id, "name": b.name} for b in branches],
        }


@app.post("/company/team/add")
def company_team_add(data: dict, user: User = Depends(get_current_user)):
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip()
    role = (data.get("role") or "staff").strip()
    branch_id = data.get("branch_id")
    if not name:
        raise HTTPException(400, "اسم العضو مطلوب")
    if role not in ("manager", "accountant", "staff"):
        raise HTTPException(400, "صلاحية غير صحيحة")
    with Session(engine) as s:
        company = s.get(Company, user.company_id)
        if not company or company.owner_id != user.id:
            raise HTTPException(403, "غير مصرّح")
        if company.is_active != 1:
            raise HTTPException(402, "شركتك قيد التفعيل — فعّلها من لوحة الإدارة")
        existing = s.exec(select(CompanyMember).where(CompanyMember.company_id == company.id)).all()
        if email:
            for m in existing:
                if m.email and m.email.lower() == email.lower():
                    raise HTTPException(400, f"العضو ({email}) مضاف مسبقاً")
        bid = int(branch_id) if branch_id else None
        mem = CompanyMember(company_id=company.id, name=name, email=email, role=role, branch_id=bid)
        s.add(mem)
        s.commit()
        s.refresh(mem)
        log_activity(user.name, f"أضاف عضو فريق: {name} ({role})", user.email)
        return {"ok": True, "member_id": mem.id}


@app.post("/company/team/remove")
def company_team_remove(data: dict, user: User = Depends(get_current_user)):
    mid = data.get("member_id")
    with Session(engine) as s:
        mem = s.get(CompanyMember, int(mid)) if mid else None
        if not mem:
            raise HTTPException(404, "العضو غير موجود")
        company = s.get(Company, mem.company_id)
        if not company or company.owner_id != user.id:
            raise HTTPException(403, "غير مصرّح")
        if company.is_active != 1:
            raise HTTPException(402, "شركتك قيد التفعيل — فعّلها من لوحة الإدارة")
        s.delete(mem)
        s.commit()
        return {"ok": True}


@app.get("/company-team.html")
def page_company_team():
    return FileResponse("company-team.html")


# ============================================================
# ===== الخدمة 2 (إكمال): محاكي القرارات (Decision Simulator) =====
# ============================================================

@app.post("/company/simulate")
def company_simulate(data: dict, user: User = Depends(get_current_user)):
    """يحاكي أثر قرارات (رفع أسعار/تسويق/توظيف) على المبيعات والربح — معادلات قطعية."""
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    scope = (data.get("scope") or "company").strip()
    price_pct = float(data.get("price_pct") or 0)          # تغيير الأسعار %
    marketing_pct = float(data.get("marketing_pct") or 0)  # إنفاق تسويقي كنسبة من المبيعات %
    staff_change = int(data.get("staff_change") or 0)      # تغيير عدد الموظفين (+/-)
    staff_cost = float(data.get("staff_cost") or 5000)     # تكلفة الموظف الشهرية

    with Session(engine) as s:
        company = s.get(Company, user.company_id)
        if not company or company.owner_id != user.id:
            raise HTTPException(403, "غير مصرّح")
        if company.is_active != 1:
            raise HTTPException(402, "شركتك قيد التفعيل — فعّلها من لوحة الإدارة")

        # القاعدة: فرع محدد أو إجمالي الشركة
        base_sales = base_expenses = 0.0
        label = company.name
        if scope == "branch" and data.get("branch_id"):
            b = s.get(CompanyBranch, int(data.get("branch_id")))
            if not b or b.company_id != company.id:
                raise HTTPException(404, "الفرع غير موجود")
            e = s.exec(
                select(CompanyEntry).where(CompanyEntry.branch_id == b.id).order_by(CompanyEntry.created_at.desc())
            ).first()
            if not e:
                raise HTTPException(400, "لا توجد بيانات لهذا الفرع")
            base_sales = e.sales
            base_expenses = e.expenses
            label = b.name
        else:
            branches = s.exec(
                select(CompanyBranch).where(CompanyBranch.company_id == company.id, CompanyBranch.is_active == 1)
            ).all()
            for b in branches:
                e = s.exec(
                    select(CompanyEntry).where(CompanyEntry.branch_id == b.id).order_by(CompanyEntry.created_at.desc())
                ).first()
                if e:
                    base_sales += e.sales
                    base_expenses += e.expenses
            if base_sales <= 0:
                raise HTTPException(400, "لا توجد بيانات كافية للمحاكاة")

        base_profit = base_sales - base_expenses
        base_margin = round((base_profit / base_sales) * 100, 1) if base_sales > 0 else 0

        # --- نموذج الأثر (مرونة محافظة) ---
        # رفع الأسعار: مرونة طلب -0.5 (رفع 10% → حجم -5%)
        p = price_pct / 100.0
        price_factor = (1 + p) * (1 + (-0.5) * p)
        new_sales = base_sales * price_factor

        # التسويق: كل 1% إنفاق → +0.8% مبيعات (متناقص قليلاً)، والتكلفة تُضاف للمصروفات
        mk = marketing_pct / 100.0
        marketing_uplift = base_sales * mk * 0.8
        marketing_cost = base_sales * mk
        new_sales += marketing_uplift

        # التوظيف: كل موظف +2% سعة مبيعات (بحد +10%) وتكلفته تُضاف
        cap = min(abs(staff_change) * 0.02, 0.10) * (1 if staff_change > 0 else -1)
        new_sales *= (1 + cap)
        new_expenses = base_expenses + marketing_cost + (staff_change * staff_cost)

        new_profit = new_sales - new_expenses
        new_margin = round((new_profit / new_sales) * 100, 1) if new_sales > 0 else 0

        d_sales = round(new_sales - base_sales)
        d_profit = round(new_profit - base_profit)
        d_margin = round(new_margin - base_margin, 1)

        verdict = "إيجابي" if d_profit > 0 else ("سلبي" if d_profit < 0 else "متعادل")

        return {
            "scope": scope, "label": label,
            "base": {"sales": round(base_sales), "expenses": round(base_expenses),
                     "profit": round(base_profit), "margin": base_margin},
            "projected": {"sales": round(new_sales), "expenses": round(new_expenses),
                          "profit": round(new_profit), "margin": new_margin},
            "delta": {"sales": d_sales, "profit": d_profit, "margin": d_margin},
            "verdict": verdict,
            "inputs": {"price_pct": price_pct, "marketing_pct": marketing_pct,
                       "staff_change": staff_change, "staff_cost": staff_cost},
        }


# ============================================================
# ===== إدارة الشركات من لوحة الأدمن =====
# ============================================================

@app.get("/admin/companies")
def admin_list_companies(_: bool = Depends(verify_admin)):
    """قائمة كل الشركات المسجّلة مع حالتها."""
    with Session(engine) as s:
        companies = s.exec(select(Company)).all()
        result = []
        for c in companies:
            owner = s.get(User, c.owner_id)
            branch_count = len(s.exec(
                select(CompanyBranch).where(CompanyBranch.company_id == c.id, CompanyBranch.is_active == 1)
            ).all())
            entries_count = len(s.exec(select(CompanyEntry).where(CompanyEntry.company_id == c.id)).all())
            result.append({
                "id": c.id,
                "name": c.name,
                "sector": SECTOR_NAMES.get(c.sector, c.sector),
                "owner_name": owner.name if owner else "—",
                "owner_email": owner.email if owner else "—",
                "branch_count": branch_count,
                "entries_count": entries_count,
                "is_active": c.is_active,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            })
        result.sort(key=lambda x: x["created_at"] or "", reverse=True)
        return result


@app.post("/admin/company-activate")
def admin_company_activate(data: dict, _: bool = Depends(verify_admin)):
    """تفعيل شركة."""
    cid = data.get("company_id")
    with Session(engine) as s:
        company = s.get(Company, int(cid)) if cid else None
        if not company:
            raise HTTPException(404, "الشركة غير موجودة")
        company.is_active = 1
        s.add(company)
        s.commit()
        log_activity("الأدمن", f"فعّل الشركة: {company.name}", "")
        return {"ok": True, "message": f"تم تفعيل {company.name}"}


@app.post("/admin/company-deactivate")
def admin_company_deactivate(data: dict, _: bool = Depends(verify_admin)):
    """إيقاف شركة."""
    cid = data.get("company_id")
    with Session(engine) as s:
        company = s.get(Company, int(cid)) if cid else None
        if not company:
            raise HTTPException(404, "الشركة غير موجودة")
        company.is_active = 0
        s.add(company)
        s.commit()
        log_activity("الأدمن", f"أوقف الشركة: {company.name}", "")
        return {"ok": True, "message": f"تم إيقاف {company.name}"}


# ============================================================
# ===== وحدات ERP المصغّر: مالية / مبيعات / عملاء =====
# ============================================================

ALLOWED_MODULES = {"finance", "sales", "customers", "hr", "ops", "inventory", "procurement", "events", "competitors"}
MODULE_LABEL = {
    "finance": "المالية", "sales": "المبيعات", "customers": "العملاء",
    "hr": "الموارد البشرية", "ops": "التشغيل", "inventory": "المخزون", "procurement": "المشتريات",
    "events": "الأحداث المؤثرة", "competitors": "المنافسون",
}


def _module_guard(s, user, module):
    if module not in ALLOWED_MODULES:
        raise HTTPException(400, "وحدة غير معروفة")
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    company = s.get(Company, user.company_id)
    if not company or company.owner_id != user.id:
        raise HTTPException(403, "غير مصرّح")
    if company.is_active != 1:
        raise HTTPException(402, "شركتك قيد التفعيل — فعّلها من لوحة الإدارة")
    return company


@app.post("/company/module/save")
def company_module_save(payload: dict, user: User = Depends(get_current_user)):
    """حفظ بيانات وحدة (مالية/مبيعات/عملاء) لفرع معيّن أو لكل الشركة."""
    module = (payload.get("module") or "").strip()
    period = (payload.get("period") or datetime.now().strftime("%Y-%m")).strip()
    branch_id = payload.get("branch_id")
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        raise HTTPException(400, "البيانات غير صالحة")
    # نظّف القيم الفارغة
    cleaned = {k: v for k, v in data.items() if v is not None and str(v).strip() != ""}
    with Session(engine) as s:
        company = _module_guard(s, user, module)
        bid = int(branch_id) if branch_id else None
        if bid:
            br = s.get(CompanyBranch, bid)
            if not br or br.company_id != company.id:
                raise HTTPException(404, "الفرع غير موجود")
        try:
            entry = CompanyModuleEntry(
                company_id=company.id, branch_id=bid, module=module,
                period=period, data=json.dumps(cleaned, ensure_ascii=False),
            )
            s.add(entry)
            s.commit()
            s.refresh(entry)
        except Exception as e:
            s.rollback()
            raise HTTPException(500, f"تعذّر الحفظ: {str(e)[:160]}")
        log_activity(user.name, f"حفظ بيانات وحدة {MODULE_LABEL.get(module, module)} ({period})", user.email)
        return {"ok": True, "id": entry.id, "period": period, "module": module, "fields": len(cleaned)}


@app.get("/company/module/{module}")
def company_module_get(module: str, user: User = Depends(get_current_user)):
    """يجلب آخر إدخال محفوظ لكل فرع (وعلى مستوى الشركة) + قائمة الفروع."""
    with Session(engine) as s:
        company = _module_guard(s, user, module)
        branches = s.exec(
            select(CompanyBranch).where(CompanyBranch.company_id == company.id, CompanyBranch.is_active == 1)
        ).all()
        all_entries = s.exec(
            select(CompanyModuleEntry).where(
                CompanyModuleEntry.company_id == company.id,
                CompanyModuleEntry.module == module,
            ).order_by(CompanyModuleEntry.created_at.desc())
        ).all()
        latest_by_branch = {}  # key: branch_id (or 0 لمستوى الشركة) -> dict
        for e in all_entries:
            key = e.branch_id or 0
            if key in latest_by_branch:
                continue
            try:
                d = json.loads(e.data) if e.data else {}
            except Exception:
                d = {}
            latest_by_branch[key] = {"period": e.period, "data": d, "saved_at": e.created_at.isoformat()}
        return {
            "company": {"id": company.id, "name": company.name, "sector": company.sector},
            "module": module,
            "label": MODULE_LABEL.get(module, module),
            "branches": [{"id": b.id, "name": b.name, "city": b.city} for b in branches],
            "company_level": latest_by_branch.get(0),
            "per_branch": [
                {"branch_id": b.id, "name": b.name, "saved": latest_by_branch.get(b.id)}
                for b in branches
            ],
            "history_count": len(all_entries),
        }


# Endpoints الصفحات
@app.get("/company-finance.html")
def page_company_finance():
    return FileResponse("company-finance.html")

@app.get("/company-sales.html")
def page_company_sales():
    return FileResponse("company-sales.html")

@app.get("/company-customers.html")
def page_company_customers():
    return FileResponse("company-customers.html")

@app.get("/company-hr.html")
def page_company_hr():
    return FileResponse("company-hr.html")

@app.get("/company-ops.html")
def page_company_ops():
    return FileResponse("company-ops.html")

@app.get("/company-inventory.html")
def page_company_inventory():
    return FileResponse("company-inventory.html")

@app.get("/company-procurement.html")
def page_company_procurement():
    return FileResponse("company-procurement.html")


# ============================================================
# ===== مركز القيادة التنفيذي (Executive Command Center) =====
# ============================================================

@app.get("/company/command-center")
def company_command_center(user: User = Depends(get_current_user)):
    """يجمّع كل المصادر ويعطي الرئيس التنفيذي: 4 قرارات + 4 فرص + التنبيهات."""
    with Session(engine) as s:
        if not user.company_id:
            raise HTTPException(403, "لا توجد شركة نشطة")
        company = s.get(Company, user.company_id)
        if not company or company.owner_id != user.id:
            raise HTTPException(403, "غير مصرّح")
        if company.is_active != 1:
            raise HTTPException(402, "شركتك قيد التفعيل — فعّلها من لوحة الإدارة")

        branches = s.exec(
            select(CompanyBranch).where(CompanyBranch.company_id == company.id, CompanyBranch.is_active == 1)
        ).all()

        # جمع آخر إدخال لكل فرع
        branch_data = []
        total_sales = total_expenses = 0.0
        for b in branches:
            e = s.exec(
                select(CompanyEntry).where(CompanyEntry.branch_id == b.id).order_by(CompanyEntry.created_at.desc())
            ).first()
            if e:
                branch_data.append({"branch": b, "entry": e})
                total_sales += e.sales
                total_expenses += e.expenses

        decisions = []      # قرارات تنفيذية (عاجلة)
        opportunities = []  # فرص نمو
        alerts = []         # تنبيهات
        kpis = {
            "total_sales": round(total_sales),
            "total_profit": round(total_sales - total_expenses),
            "margin": round((total_sales - total_expenses) / total_sales * 100, 1) if total_sales > 0 else 0,
            "branches_count": len(branches),
            "branches_with_data": len(branch_data),
        }

        # ===== كشف تلقائي للمشاكل والفرص =====
        # 1) فروع ضعيفة الأداء (مؤشّر < 40)
        weak = [bd for bd in branch_data if bd["entry"].branch_score < 40]
        if weak:
            names = "، ".join(bd["branch"].name for bd in weak[:3])
            decisions.append({
                "priority": "عاجل", "icon": "🚨",
                "title": f"{len(weak)} فرع ضعيف الأداء يحتاج تدخّل فوري",
                "detail": f"الفروع: {names}. مؤشّرها أقل من 40/100.",
                "action": "افتح مقارنة الفروع لمعرفة السبب الجذري",
                "link": "company-branches.html",
            })

        # 2) فروع بهامش منخفض جداً (<10%)
        thin = [bd for bd in branch_data if bd["entry"].margin < 10 and bd["entry"].sales > 0]
        if thin:
            decisions.append({
                "priority": "عاجل", "icon": "💸",
                "title": f"{len(thin)} فرع بهامش ربح منخفض جداً",
                "detail": f"هامش الربح أقل من 10% — راجع التكاليف فوراً.",
                "action": "حلّل الأسباب عبر التحليل التنفيذي",
                "link": "company-dashboard.html",
            })

        # 3) فروع تراجعت مبيعاتها (نمو سالب أكثر من 15%-)
        declining = [bd for bd in branch_data if bd["entry"].growth <= -15]
        if declining:
            names = "، ".join(bd["branch"].name for bd in declining[:3])
            decisions.append({
                "priority": "مهم", "icon": "📉",
                "title": f"تراجع حاد في مبيعات {len(declining)} فرع",
                "detail": f"{names} — تراجع المبيعات تجاوز 15%.",
                "action": "افحص أسباب التراجع",
                "link": "company-branches.html",
            })

        # 4) كشف التسرّب
        try:
            company_exp_ratio = (total_expenses / total_sales * 100) if total_sales > 0 else 0
            high_risk = 0
            for bd in branch_data:
                ents = s.exec(
                    select(CompanyEntry).where(CompanyEntry.branch_id == bd["branch"].id).order_by(CompanyEntry.created_at)
                ).all()
                if ents:
                    r = detect_leakage(ents, company_exp_ratio)
                    if r["risk"] >= 60:
                        high_risk += 1
            if high_risk > 0:
                decisions.append({
                    "priority": "عاجل", "icon": "🛡️",
                    "title": f"اشتباه تسرّب/احتيال في {high_risk} فرع",
                    "detail": "تم كشف مؤشرات قوية على تسرّب — راجعها فوراً.",
                    "action": "افتح كشف التسرّب",
                    "link": "company-leakage.html",
                })
        except Exception:
            pass

        # 5) التدفق النقدي
        try:
            cash = company.cash_reserve or 0
            obligations = company.monthly_obligations or 0
            # تقدير صافي شهري من البيانات
            monthly_net = round((total_sales - total_expenses) / max(len(branch_data), 1)) - obligations
            if monthly_net < 0 and cash > 0:
                runway = round(cash / abs(monthly_net), 1)
                if runway < 3:
                    decisions.append({
                        "priority": "عاجل", "icon": "💧",
                        "title": f"السيولة تكفي {runway} شهر فقط",
                        "detail": "وضع حرج في التدفق النقدي — قرارات تخفيض تكلفة عاجلة.",
                        "action": "افتح التدفق النقدي",
                        "link": "company-cashflow.html",
                    })
        except Exception:
            pass

        # ===== الفرص =====
        # فروع ممتازة لتعميم ممارساتها
        strong = [bd for bd in branch_data if bd["entry"].branch_score >= 70]
        if strong:
            best = max(strong, key=lambda x: x["entry"].branch_score)
            opportunities.append({
                "icon": "⭐", "title": f"فرع {best['branch'].name} يتفوّق — عمّم ممارساته",
                "detail": f"مؤشّر {best['entry'].branch_score}/100. ادرس ممارساته وطبّقها على الفروع الأضعف لرفع الأداء العام.",
                "link": "company-branches.html",
            })

        # نمو إيجابي قوي
        growing = [bd for bd in branch_data if bd["entry"].growth >= 15]
        if growing:
            opportunities.append({
                "icon": "📈", "title": f"{len(growing)} فرع ينمو بقوة",
                "detail": "فروع ينمو فيها الطلب — فرصة لزيادة الاستثمار/الموظفين/المخزون.",
                "link": "company-dashboard.html",
            })

        # عدم استكمال البيانات → فرصة تحسين الذكاء
        modules_with_data = 0
        for mod in ALLOWED_MODULES:
            cnt = s.exec(
                select(CompanyModuleEntry).where(
                    CompanyModuleEntry.company_id == company.id,
                    CompanyModuleEntry.module == mod,
                )
            ).all()
            if cnt:
                modules_with_data += 1
        if modules_with_data < 4:
            opportunities.append({
                "icon": "🧩", "title": "أكمل وحدات الـ ERP لتحليل أعمق",
                "detail": f"تم تعبئة {modules_with_data} وحدة فقط من {len(ALLOWED_MODULES)} — كل وحدة إضافية تزيد دقة التحليل.",
                "link": "company-dashboard.html",
            })

        # هامش ربح ممتاز على مستوى الشركة
        if kpis["margin"] >= 25 and len(branch_data) > 0:
            opportunities.append({
                "icon": "💎", "title": "هامش ربح ممتاز — فرصة توسّع",
                "detail": f"هامش الشركة {kpis['margin']}% — وضع مالي قوي يدعم افتتاح فرع جديد أو زيادة التسويق.",
                "link": "company-dashboard.html",
            })

        # ===== التنبيهات السريعة =====
        if not branch_data:
            alerts.append({"icon": "📋", "msg": "لا توجد بيانات فروع بعد — ابدأ بإدخال البيانات الأساسية"})
        else:
            if kpis["margin"] < 15:
                alerts.append({"icon": "⚠️", "msg": f"هامش الشركة الإجمالي {kpis['margin']}% — تحت المعدل الصحّي"})
            if len([bd for bd in branch_data if bd["entry"].repeat_rate < 20]) > 0:
                alerts.append({"icon": "👥", "msg": "معدل تكرار العملاء منخفض في بعض الفروع — راجع تجربة العميل"})

        # ترتيب القرارات: عاجل أولاً
        priority_order = {"عاجل": 0, "مهم": 1, "متوسط": 2}
        decisions.sort(key=lambda d: priority_order.get(d["priority"], 9))

        # اقتطاع لأهم 4
        decisions = decisions[:4]
        opportunities = opportunities[:4]

        return {
            "company": {"name": company.name, "sector": SECTOR_NAMES.get(company.sector, company.sector)},
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "kpis": kpis,
            "decisions": decisions,
            "opportunities": opportunities,
            "alerts": alerts,
            "modules_filled": modules_with_data,
            "modules_total": len(ALLOWED_MODULES),
        }


# Routes الصفحات الجديدة
@app.get("/company-events.html")
def page_company_events():
    return FileResponse("company-events.html")

@app.get("/company-competitors.html")
def page_company_competitors():
    return FileResponse("company-competitors.html")

@app.get("/company-command-center.html")
def page_company_command_center():
    return FileResponse("company-command-center.html")


# ============================================================
# ===== Prediction AI: التنبؤ بالمبيعات والأرباح =====
# ============================================================

@app.get("/company/predictions")
def company_predictions(user: User = Depends(get_current_user)):
    """يحسب توقعات الـ 6 أشهر القادمة بناءً على معدل النمو الفعلي لكل فرع."""
    with Session(engine) as s:
        if not user.company_id:
            raise HTTPException(403, "لا توجد شركة نشطة")
        company = s.get(Company, user.company_id)
        if not company or company.owner_id != user.id:
            raise HTTPException(403, "غير مصرّح")
        if company.is_active != 1:
            raise HTTPException(402, "شركتك قيد التفعيل — فعّلها من لوحة الإدارة")

        branches = s.exec(
            select(CompanyBranch).where(CompanyBranch.company_id == company.id, CompanyBranch.is_active == 1)
        ).all()

        branch_forecasts = []
        total_sales_proj = [0.0] * 6
        total_profit_proj = [0.0] * 6
        has_history = False

        for b in branches:
            ents = s.exec(
                select(CompanyEntry).where(CompanyEntry.branch_id == b.id).order_by(CompanyEntry.created_at)
            ).all()
            if not ents:
                continue

            last = ents[-1]
            # حساب معدل النمو من آخر 3 فترات (أو كل المتاح)
            recent = ents[-min(4, len(ents)):]
            growth_rates = []
            for i in range(1, len(recent)):
                if recent[i-1].sales > 0:
                    g = (recent[i].sales - recent[i-1].sales) / recent[i-1].sales
                    growth_rates.append(g)
            avg_growth = sum(growth_rates) / len(growth_rates) if growth_rates else 0
            # قيد على النمو الشهري (±15% حد أقصى لتفادي مبالغات)
            avg_growth = max(-0.15, min(0.15, avg_growth))

            if len(ents) >= 2:
                has_history = True

            margin = (last.profit / last.sales) if last.sales > 0 else 0
            # ثقة التنبؤ
            if len(ents) >= 4:
                confidence = "عالية"
            elif len(ents) >= 2:
                confidence = "متوسطة"
            else:
                confidence = "منخفضة"

            months_sales = []
            months_profit = []
            base_sales = last.sales
            for m in range(6):
                base_sales = base_sales * (1 + avg_growth)
                months_sales.append(round(base_sales))
                months_profit.append(round(base_sales * margin))
                total_sales_proj[m] += base_sales
                total_profit_proj[m] += base_sales * margin

            branch_forecasts.append({
                "branch": b.name,
                "current_sales": round(last.sales),
                "current_profit": round(last.profit),
                "monthly_growth_pct": round(avg_growth * 100, 1),
                "confidence": confidence,
                "history_points": len(ents),
                "next_6m_sales": months_sales,
                "next_6m_profit": months_profit,
                "total_6m_sales": round(sum(months_sales)),
                "total_6m_profit": round(sum(months_profit)),
            })

        # توليد أسماء الأشهر القادمة
        now = datetime.now()
        ar_months = ["", "يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
                     "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"]
        labels = []
        for i in range(1, 7):
            m = ((now.month - 1 + i) % 12) + 1
            y = now.year + ((now.month - 1 + i) // 12)
            labels.append(f"{ar_months[m]} {y}")

        return {
            "company": {"name": company.name},
            "has_history": has_history,
            "labels": labels,
            "branch_forecasts": branch_forecasts,
            "company_projection": {
                "sales": [round(v) for v in total_sales_proj],
                "profit": [round(v) for v in total_profit_proj],
                "total_sales_6m": round(sum(total_sales_proj)),
                "total_profit_6m": round(sum(total_profit_proj)),
            },
        }


# ============================================================
# ===== AI Risk Engine: محرك المخاطر =====
# ============================================================

@app.get("/company/risks")
def company_risks(user: User = Depends(get_current_user)):
    """يحدّد المخاطر الأربع: سيولة، فقد عملاء، انخفاض أرباح، تعثّر تشغيلي."""
    with Session(engine) as s:
        if not user.company_id:
            raise HTTPException(403, "لا توجد شركة نشطة")
        company = s.get(Company, user.company_id)
        if not company or company.owner_id != user.id:
            raise HTTPException(403, "غير مصرّح")
        if company.is_active != 1:
            raise HTTPException(402, "شركتك قيد التفعيل — فعّلها من لوحة الإدارة")

        branches = s.exec(
            select(CompanyBranch).where(CompanyBranch.company_id == company.id, CompanyBranch.is_active == 1)
        ).all()

        # جمع آخر إدخال لكل فرع
        total_sales = total_expenses = 0.0
        margin_sum = 0.0
        margin_count = 0
        repeat_sum = 0.0
        repeat_count = 0
        growth_values = []
        for b in branches:
            e = s.exec(
                select(CompanyEntry).where(CompanyEntry.branch_id == b.id).order_by(CompanyEntry.created_at.desc())
            ).first()
            if e:
                total_sales += e.sales
                total_expenses += e.expenses
                if e.sales > 0:
                    margin_sum += e.margin
                    margin_count += 1
                if e.repeat_rate > 0:
                    repeat_sum += e.repeat_rate
                    repeat_count += 1
                if e.growth != 0:
                    growth_values.append(e.growth)

        avg_margin = margin_sum / margin_count if margin_count else 0
        avg_repeat = repeat_sum / repeat_count if repeat_count else 0
        avg_growth = sum(growth_values) / len(growth_values) if growth_values else 0
        monthly_net = total_sales - total_expenses

        risks = []

        # 1) مخاطر السيولة
        liquidity_score = 0
        liquidity_reasons = []
        cash = company.cash_reserve or 0
        obligations = company.monthly_obligations or 0
        if obligations > 0 and cash > 0:
            runway = cash / max(abs(min(monthly_net - obligations, 0)), 1)
            if runway < 3:
                liquidity_score = 85; liquidity_reasons.append(f"السيولة تكفي {round(runway,1)} شهر فقط")
            elif runway < 6:
                liquidity_score = 50; liquidity_reasons.append(f"السيولة تكفي {round(runway,1)} شهر — راقب")
            else:
                liquidity_score = 15; liquidity_reasons.append("السيولة بحالة آمنة")
        else:
            liquidity_score = 30; liquidity_reasons.append("بيانات السيولة غير مكتملة — أدخلها في صفحة التدفق النقدي")
        if monthly_net < 0:
            liquidity_score = max(liquidity_score, 70); liquidity_reasons.append("صافي تشغيل سالب")
        risks.append({
            "title": "مخاطر السيولة", "icon": "💧", "score": min(liquidity_score, 100),
            "reasons": liquidity_reasons, "link": "company-cashflow.html",
            "action": "افتح التدفق النقدي وراجع الالتزامات",
        })

        # 2) فقد العملاء
        churn_score = 0
        churn_reasons = []
        if avg_repeat < 20 and repeat_count > 0:
            churn_score = 75; churn_reasons.append(f"معدل تكرار العملاء منخفض ({round(avg_repeat)}%)")
        elif avg_repeat < 35 and repeat_count > 0:
            churn_score = 45; churn_reasons.append(f"معدل تكرار متوسط ({round(avg_repeat)}%) — يحتاج تحسين")
        elif repeat_count == 0:
            churn_score = 35; churn_reasons.append("لم تُدخل بيانات العملاء المتكررين")
        else:
            churn_score = 15; churn_reasons.append(f"تكرار العملاء جيد ({round(avg_repeat)}%)")
        # إذا في وحدة العملاء بيانات NPS
        try:
            ce = s.exec(
                select(CompanyModuleEntry).where(
                    CompanyModuleEntry.company_id == company.id,
                    CompanyModuleEntry.module == "customers",
                ).order_by(CompanyModuleEntry.created_at.desc())
            ).first()
            if ce and ce.data:
                cdata = json.loads(ce.data)
                for k, v in cdata.items():
                    if "NPS" in k:
                        try:
                            nps = float(v)
                            if nps < 0:
                                churn_score = max(churn_score, 80); churn_reasons.append(f"NPS سالب ({round(nps)})")
                            elif nps < 30:
                                churn_score = max(churn_score, 55); churn_reasons.append(f"NPS منخفض ({round(nps)})")
                        except Exception: pass
        except Exception: pass
        risks.append({
            "title": "فقد العملاء", "icon": "👋", "score": min(churn_score, 100),
            "reasons": churn_reasons, "link": "company-customers.html",
            "action": "افحص وحدة العملاء وحسّن الاحتفاظ",
        })

        # 3) انخفاض الأرباح
        profit_score = 0
        profit_reasons = []
        if avg_margin < 10 and margin_count > 0:
            profit_score = 80; profit_reasons.append(f"هامش الربح {round(avg_margin)}% — منخفض جداً")
        elif avg_margin < 20 and margin_count > 0:
            profit_score = 45; profit_reasons.append(f"هامش الربح {round(avg_margin)}% — دون المعدل")
        elif margin_count == 0:
            profit_score = 30; profit_reasons.append("لا توجد بيانات هامش")
        else:
            profit_score = 15; profit_reasons.append(f"هامش الربح صحي ({round(avg_margin)}%)")
        if avg_growth < -10:
            profit_score = max(profit_score, 75); profit_reasons.append(f"تراجع مبيعات بمعدل {round(avg_growth)}%")
        risks.append({
            "title": "انخفاض الأرباح", "icon": "📉", "score": min(profit_score, 100),
            "reasons": profit_reasons, "link": "company-dashboard.html",
            "action": "حلّل الفروع الأضعف وراجع التكاليف",
        })

        # 4) تعثّر تشغيلي (من وحدتي التشغيل والمشتريات)
        ops_score = 0
        ops_reasons = []
        try:
            for mod in ("ops", "procurement"):
                me = s.exec(
                    select(CompanyModuleEntry).where(
                        CompanyModuleEntry.company_id == company.id,
                        CompanyModuleEntry.module == mod,
                    ).order_by(CompanyModuleEntry.created_at.desc())
                ).first()
                if me and me.data:
                    d = json.loads(me.data)
                    for k, v in d.items():
                        try:
                            val = float(v)
                            if "متأخر" in k and val > 0:
                                ops_score = max(ops_score, 60); ops_reasons.append(f"{k}: {round(val)}")
                            if "الالتزام" in k and val < 80:
                                ops_score = max(ops_score, 55); ops_reasons.append(f"{k}: {round(val)}% (يحتاج تحسين)")
                            if "الأعطال" in k and val > 0:
                                ops_score = max(ops_score, 45); ops_reasons.append(f"{k}: {round(val)}")
                        except Exception: pass
        except Exception: pass
        if not ops_reasons:
            ops_score = 20; ops_reasons.append("لا توجد مؤشرات تعثّر — أو لم تُدخل بيانات التشغيل بعد")
        risks.append({
            "title": "تعثّر تشغيلي", "icon": "⚙️", "score": min(ops_score, 100),
            "reasons": ops_reasons, "link": "company-ops.html",
            "action": "افتح وحدة التشغيل والمشتريات",
        })

        # ترتيب حسب الخطورة
        for r in risks:
            if r["score"] >= 70: r["level"] = "خطر مرتفع"; r["color"] = "#ef4444"
            elif r["score"] >= 45: r["level"] = "خطر متوسط"; r["color"] = "#f59e0b"
            elif r["score"] >= 25: r["level"] = "خطر منخفض"; r["color"] = "#f5b301"
            else: r["level"] = "آمن"; r["color"] = "#10b981"
        risks.sort(key=lambda x: x["score"], reverse=True)

        overall = round(sum(r["score"] for r in risks) / len(risks)) if risks else 0
        if overall >= 60: overall_level = "خطر مرتفع"; overall_color = "#ef4444"
        elif overall >= 40: overall_level = "خطر متوسط"; overall_color = "#f59e0b"
        else: overall_level = "آمن نسبياً"; overall_color = "#10b981"

        return {
            "company": {"name": company.name},
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "overall_risk": overall,
            "overall_level": overall_level,
            "overall_color": overall_color,
            "risks": risks,
        }


# ===== Routes الصفحات الجديدة =====
@app.get("/company-predictions.html")
def page_company_predictions():
    return FileResponse("company-predictions.html")

@app.get("/company-risks.html")
def page_company_risks():
    return FileResponse("company-risks.html")

@app.get("/company-board.html")
def page_company_board():
    return FileResponse("company-board.html")


# ============================================================
# ===== AI Health Score — مؤشر صحة الشركة الشامل =====
# ============================================================

@app.get("/company/health-score")
def company_health_score(user: User = Depends(get_current_user)):
    """٥ محاور: الربحية، السيولة، النمو، رضا العملاء، إدارة المخاطر — مع شرح."""
    with Session(engine) as s:
        if not user.company_id:
            raise HTTPException(403, "لا توجد شركة نشطة")
        company = s.get(Company, user.company_id)
        if not company or company.owner_id != user.id:
            raise HTTPException(403, "غير مصرّح")
        if company.is_active != 1:
            raise HTTPException(402, "شركتك قيد التفعيل — فعّلها من لوحة الإدارة")

        branches = s.exec(
            select(CompanyBranch).where(CompanyBranch.company_id == company.id, CompanyBranch.is_active == 1)
        ).all()
        rows = []
        for b in branches:
            e = s.exec(
                select(CompanyEntry).where(CompanyEntry.branch_id == b.id).order_by(CompanyEntry.created_at.desc())
            ).first()
            if e: rows.append((b, e))

        if not rows:
            raise HTTPException(400, "لا توجد بيانات فروع — أدخل البيانات الأساسية أولاً")

        total_sales = sum(r[1].sales for r in rows)
        total_expenses = sum(r[1].expenses for r in rows)
        margin_vals = [r[1].margin for r in rows if r[1].sales > 0]
        avg_margin = sum(margin_vals) / len(margin_vals) if margin_vals else 0
        repeat_vals = [r[1].repeat_rate for r in rows if r[1].repeat_rate > 0]
        avg_repeat = sum(repeat_vals) / len(repeat_vals) if repeat_vals else 0
        growth_vals = [r[1].growth for r in rows if r[1].growth != 0]
        avg_growth = sum(growth_vals) / len(growth_vals) if growth_vals else 0

        # ١) الربحية (0-100)
        if avg_margin >= 25: profitability = 95
        elif avg_margin >= 18: profitability = 80
        elif avg_margin >= 12: profitability = 60
        elif avg_margin >= 6: profitability = 40
        elif avg_margin > 0: profitability = 20
        else: profitability = 10

        # ٢) السيولة (0-100)
        cash = company.cash_reserve or 0
        obligations = company.monthly_obligations or 0
        monthly_net = total_sales - total_expenses - obligations
        if monthly_net >= 0 and cash > 0:
            liquidity = 90
        elif monthly_net >= 0:
            liquidity = 75
        elif cash > 0 and obligations > 0:
            runway = cash / max(abs(monthly_net), 1)
            if runway >= 6: liquidity = 65
            elif runway >= 3: liquidity = 45
            else: liquidity = 20
        else:
            liquidity = 50  # بيانات ناقصة

        # ٣) النمو (0-100)
        if avg_growth >= 15: growth = 95
        elif avg_growth >= 5: growth = 80
        elif avg_growth >= 0: growth = 65
        elif avg_growth >= -5: growth = 45
        elif avg_growth >= -15: growth = 25
        else: growth = 10

        # ٤) رضا العملاء (0-100) — من معدل التكرار + NPS لو موجود
        cust_score = 50
        if repeat_vals:
            if avg_repeat >= 50: cust_score = 90
            elif avg_repeat >= 35: cust_score = 75
            elif avg_repeat >= 20: cust_score = 55
            else: cust_score = 30
        # تحسين بـ NPS إن وُجد
        try:
            ce = s.exec(
                select(CompanyModuleEntry).where(
                    CompanyModuleEntry.company_id == company.id,
                    CompanyModuleEntry.module == "customers",
                ).order_by(CompanyModuleEntry.created_at.desc())
            ).first()
            if ce and ce.data:
                cd = json.loads(ce.data)
                for k, v in cd.items():
                    if "NPS" in k:
                        try:
                            nps = float(v)
                            if nps >= 50: cust_score = max(cust_score, 90)
                            elif nps >= 30: cust_score = max(cust_score, 75)
                            elif nps >= 0: cust_score = max(cust_score, 55)
                            else: cust_score = min(cust_score, 35)
                        except Exception: pass
        except Exception: pass

        # ٥) إدارة المخاطر (0-100) — معكوس مؤشر التسرّب
        risk_score = 80  # افتراضي جيد
        try:
            company_exp_ratio = (total_expenses / total_sales * 100) if total_sales > 0 else 0
            high_risk_count = 0
            for b, _ in rows:
                ents = s.exec(
                    select(CompanyEntry).where(CompanyEntry.branch_id == b.id).order_by(CompanyEntry.created_at)
                ).all()
                if ents:
                    r = detect_leakage(ents, company_exp_ratio)
                    if r["risk"] >= 60: high_risk_count += 1
            if high_risk_count >= 3: risk_score = 30
            elif high_risk_count == 2: risk_score = 45
            elif high_risk_count == 1: risk_score = 65
        except Exception: pass

        axes = [
            {"key": "profitability", "label": "الربحية", "score": profitability,
             "metric": f"هامش الربح {round(avg_margin,1)}%",
             "icon": "💰"},
            {"key": "liquidity", "label": "السيولة", "score": liquidity,
             "metric": f"احتياطي {round(cash)} ر · صافي شهري {round(monthly_net)} ر",
             "icon": "💧"},
            {"key": "growth", "label": "النمو", "score": growth,
             "metric": f"معدّل النمو {round(avg_growth,1)}%",
             "icon": "📈"},
            {"key": "customers", "label": "رضا العملاء", "score": cust_score,
             "metric": f"تكرار {round(avg_repeat)}%" if repeat_vals else "بيانات محدودة",
             "icon": "❤️"},
            {"key": "risks", "label": "إدارة المخاطر", "score": risk_score,
             "metric": "تسرّب منخفض" if risk_score >= 65 else "تسرّب محتمل",
             "icon": "🛡️"},
        ]

        # الدرجة الشاملة بأوزان
        weights = {"profitability": 0.25, "liquidity": 0.25, "growth": 0.20, "customers": 0.15, "risks": 0.15}
        overall = sum(a["score"] * weights[a["key"]] for a in axes)
        overall = round(overall)

        if overall >= 80: level = "ممتاز"; color = "#10b981"
        elif overall >= 65: level = "جيد"; color = "#34d399"
        elif overall >= 50: level = "مقبول"; color = "#f5b301"
        elif overall >= 35: level = "ضعيف"; color = "#f59e0b"
        else: level = "حرج"; color = "#ef4444"

        # تلوين كل محور
        for a in axes:
            if a["score"] >= 75: a["color"] = "#10b981"; a["light"] = "🟢"
            elif a["score"] >= 55: a["color"] = "#f5b301"; a["light"] = "🟡"
            elif a["score"] >= 35: a["color"] = "#f59e0b"; a["light"] = "🟠"
            else: a["color"] = "#ef4444"; a["light"] = "🔴"

        # أضعف محور = السبب الرئيسي
        weakest = min(axes, key=lambda x: x["score"])
        explanations = {
            "profitability": "هامش الربح منخفض — راجع التكاليف ورفع الأسعار في الفروع الأقوى.",
            "liquidity": "السيولة تحت ضغط — راجع الالتزامات الشهرية وأدخل بيانات التدفق النقدي.",
            "growth": "النمو متباطئ — راجع استراتيجية التسويق والاحتفاظ بالعملاء.",
            "customers": "رضا العملاء يحتاج تحسين — راجع وحدة العملاء والـ NPS.",
            "risks": "مؤشرات تسرّب في بعض الفروع — افتح صفحة كشف التسرّب.",
        }
        main_cause = explanations.get(weakest["key"], "راجع البيانات لمعرفة السبب.")

        return {
            "company": {"name": company.name},
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "overall": overall,
            "level": level,
            "color": color,
            "axes": axes,
            "weakest_axis": weakest["label"],
            "main_cause": main_cause,
        }


@app.get("/company-health.html")
def page_company_health():
    return FileResponse("company-health.html")


# ============================================================
# ===== جودة البيانات لكل وحدة =====
# ============================================================

# الحقول المهمة لكل وحدة (للحساب المرجعي)
MODULE_KEY_FIELDS = {
    "finance":     16, "sales":       10, "customers":  8,
    "hr":          12, "ops":         6,  "inventory":  8,
    "procurement": 10, "events":      8,  "competitors": 12,
}

@app.get("/company/data-quality")
def company_data_quality(user: User = Depends(get_current_user)):
    """يحسب جودة بيانات كل وحدة + جودة شاملة."""
    with Session(engine) as s:
        if not user.company_id:
            raise HTTPException(403, "لا توجد شركة نشطة")
        company = s.get(Company, user.company_id)
        if not company or company.owner_id != user.id:
            raise HTTPException(403, "غير مصرّح")
        if company.is_active != 1:
            raise HTTPException(402, "شركتك قيد التفعيل")

        modules = []
        for mod, expected in MODULE_KEY_FIELDS.items():
            entries = s.exec(
                select(CompanyModuleEntry).where(
                    CompanyModuleEntry.company_id == company.id,
                    CompanyModuleEntry.module == mod,
                ).order_by(CompanyModuleEntry.created_at.desc())
            ).all()
            if not entries:
                quality = 0; status = "❌"; level = "لا بيانات"
            else:
                seen = set(); filled_count = 0
                for me in entries:
                    key = me.branch_id or 0
                    if key in seen: continue
                    seen.add(key)
                    try:
                        md = json.loads(me.data) if me.data else {}
                        filled_count = max(filled_count, len(md))
                    except Exception: pass
                quality = min(round((filled_count / expected) * 100), 100)
                if quality >= 75: status = "🟢"; level = "ممتازة"
                elif quality >= 45: status = "🟡"; level = "متوسطة"
                else: status = "🟠"; level = "ضعيفة"
            modules.append({
                "module": mod, "label": MODULE_LABEL.get(mod, mod),
                "quality": quality, "status": status, "level": level,
                "filled": filled_count if entries else 0, "expected": expected,
            })

        # أساسيات الفروع
        branches_with_data = 0; branches_total = 0
        for b in s.exec(select(CompanyBranch).where(CompanyBranch.company_id == company.id, CompanyBranch.is_active == 1)).all():
            branches_total += 1
            if s.exec(select(CompanyEntry).where(CompanyEntry.branch_id == b.id)).first():
                branches_with_data += 1
        basics_quality = round((branches_with_data / branches_total) * 100) if branches_total else 0

        overall = round(sum(m["quality"] for m in modules) / len(modules) * 0.7 + basics_quality * 0.3) if modules else 0
        if overall >= 75: overall_status = "🟢"; overall_level = "موثوقية عالية"
        elif overall >= 45: overall_status = "🟡"; overall_level = "موثوقية متوسطة"
        else: overall_status = "🟠"; overall_level = "موثوقية محدودة"

        return {
            "company": {"name": company.name},
            "overall_quality": overall,
            "overall_status": overall_status,
            "overall_level": overall_level,
            "basics": {"quality": basics_quality, "branches_with_data": branches_with_data, "branches_total": branches_total},
            "modules": modules,
            "note": "كل ما زادت جودة البيانات، زادت دقة التحليل والتنبؤ والمخاطر.",
        }


# ============================================================
# ===== وحدة الأهداف والنتائج =====
# ============================================================

@app.post("/company/goals/save")
def company_goals_save(data: dict, user: User = Depends(get_current_user)):
    """يحفظ أهداف الشركة كسجلّ في جدول الوحدات (module=goals)."""
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    with Session(engine) as s:
        company = s.get(Company, user.company_id)
        if not company or company.owner_id != user.id:
            raise HTTPException(403, "غير مصرّح")
        if company.is_active != 1:
            raise HTTPException(402, "شركتك قيد التفعيل")
        goals = data.get("goals") or {}
        cleaned = {k: v for k, v in goals.items() if v is not None and str(v).strip() != ""}
        entry = CompanyModuleEntry(
            company_id=company.id, branch_id=None, module="goals",
            period=data.get("period") or datetime.now().strftime("%Y"),
            data=json.dumps(cleaned, ensure_ascii=False),
        )
        s.add(entry); s.commit(); s.refresh(entry)
        return {"ok": True, "fields": len(cleaned)}


@app.get("/company/goals")
def company_goals_get(user: User = Depends(get_current_user)):
    """يجلب آخر أهداف + يحسب نسبة الإنجاز مقابل البيانات الفعلية."""
    with Session(engine) as s:
        if not user.company_id:
            raise HTTPException(403, "لا توجد شركة نشطة")
        company = s.get(Company, user.company_id)
        if not company or company.owner_id != user.id:
            raise HTTPException(403, "غير مصرّح")
        if company.is_active != 1:
            raise HTTPException(402, "شركتك قيد التفعيل")

        last = s.exec(
            select(CompanyModuleEntry).where(
                CompanyModuleEntry.company_id == company.id,
                CompanyModuleEntry.module == "goals",
            ).order_by(CompanyModuleEntry.created_at.desc())
        ).first()
        goals = {}
        period = ""
        if last and last.data:
            try: goals = json.loads(last.data)
            except Exception: goals = {}
            period = last.period

        # الفعلي من بيانات الفروع
        total_sales = total_profit = total_customers = 0.0
        repeat_vals = []; growth_vals = []; total_expenses = 0.0
        for b in s.exec(select(CompanyBranch).where(CompanyBranch.company_id == company.id, CompanyBranch.is_active == 1)).all():
            e = s.exec(
                select(CompanyEntry).where(CompanyEntry.branch_id == b.id).order_by(CompanyEntry.created_at.desc())
            ).first()
            if e:
                total_sales += e.sales; total_profit += e.profit
                total_expenses += e.expenses; total_customers += e.customers
                if e.repeat_rate > 0: repeat_vals.append(e.repeat_rate)
                if e.growth != 0: growth_vals.append(e.growth)

        avg_repeat = sum(repeat_vals)/len(repeat_vals) if repeat_vals else 0

        # مقارنة هدف vs فعلي
        comparisons = []
        def cmp(label, goal_key, actual, unit="ر"):
            try: goal = float(goals.get(goal_key, 0) or 0)
            except: goal = 0
            if goal <= 0: return
            pct = round((actual / goal) * 100, 1) if goal > 0 else 0
            remaining = max(round(goal - actual), 0)
            if pct >= 100: status = "🟢"; level = "تحقق"
            elif pct >= 80: status = "🟢"; level = "قريب من الهدف"
            elif pct >= 60: status = "🟡"; level = "في المسار"
            elif pct >= 40: status = "🟠"; level = "متعثّر"
            else: status = "🔴"; level = "بعيد"
            comparisons.append({
                "label": label, "goal": round(goal), "actual": round(actual),
                "pct": pct, "remaining": remaining, "status": status, "level": level, "unit": unit,
            })
        cmp("هدف المبيعات", "هدف المبيعات (ريال)", total_sales)
        cmp("هدف الربح", "هدف الربح (ريال)", total_profit)
        cmp("هدف العملاء", "هدف عدد العملاء", total_customers, unit="عميل")
        cmp("هدف معدل الاحتفاظ", "هدف معدل الاحتفاظ %", avg_repeat, unit="%")
        # خفض المصاريف (هدف أقل)
        try:
            exp_goal = float(goals.get("هدف سقف المصروفات (ريال)", 0) or 0)
            if exp_goal > 0:
                pct_under = round((1 - total_expenses/exp_goal) * 100, 1) if exp_goal>0 else 0
                pct = round((exp_goal/total_expenses)*100, 1) if total_expenses>0 else 100
                if total_expenses <= exp_goal: status = "🟢"; level = f"تحت السقف ({pct_under}%)"
                else: status = "🔴"; level = f"تجاوز السقف"
                comparisons.append({
                    "label": "سقف المصروفات", "goal": round(exp_goal), "actual": round(total_expenses),
                    "pct": pct, "remaining": 0, "status": status, "level": level, "unit": "ر",
                })
        except: pass

        return {
            "company": {"name": company.name},
            "period": period,
            "goals": goals,
            "comparisons": comparisons,
            "has_goals": len(goals) > 0,
        }


@app.get("/company-goals.html")
def page_company_goals():
    return FileResponse("company-goals.html")

@app.get("/company-data-quality.html")
def page_company_quality():
    return FileResponse("company-data-quality.html")



# ============================================================
# ===== Benchmarks: المرجعية الصناعية لكل قطاع =====
# ============================================================
# مصدر القيم: مراجع عامة لمتوسطات قطاعات الشركات المتوسطة بالسوق السعودي/الخليجي.
# تُستخدم للمقارنة الإرشادية فقط، ليست أرقاماً رسمية.

SECTOR_BENCHMARKS = {
    "fnb": {
        "margin": {"value": 12, "label": "هامش الربح %", "higher_is_better": True},
        "repeat_rate": {"value": 35, "label": "معدل تكرار العملاء %", "higher_is_better": True},
        "growth": {"value": 8, "label": "النمو الشهري %", "higher_is_better": True},
        "expense_ratio": {"value": 70, "label": "نسبة المصروفات للمبيعات %", "higher_is_better": False},
    },
    "retail": {
        "margin": {"value": 18, "label": "هامش الربح %", "higher_is_better": True},
        "repeat_rate": {"value": 30, "label": "معدل تكرار العملاء %", "higher_is_better": True},
        "growth": {"value": 5, "label": "النمو الشهري %", "higher_is_better": True},
        "expense_ratio": {"value": 65, "label": "نسبة المصروفات %", "higher_is_better": False},
    },
    "ecommerce": {
        "margin": {"value": 22, "label": "هامش الربح %", "higher_is_better": True},
        "repeat_rate": {"value": 25, "label": "معدل تكرار العملاء %", "higher_is_better": True},
        "growth": {"value": 12, "label": "النمو الشهري %", "higher_is_better": True},
        "expense_ratio": {"value": 60, "label": "نسبة المصروفات %", "higher_is_better": False},
    },
    "manufacturing": {
        "margin": {"value": 16, "label": "هامش الربح %", "higher_is_better": True},
        "repeat_rate": {"value": 50, "label": "معدل تكرار العملاء %", "higher_is_better": True},
        "growth": {"value": 4, "label": "النمو الشهري %", "higher_is_better": True},
        "expense_ratio": {"value": 72, "label": "نسبة المصروفات %", "higher_is_better": False},
    },
    "contracting": {
        "margin": {"value": 10, "label": "هامش الربح %", "higher_is_better": True},
        "repeat_rate": {"value": 40, "label": "معدل تكرار العملاء %", "higher_is_better": True},
        "growth": {"value": 6, "label": "النمو الشهري %", "higher_is_better": True},
        "expense_ratio": {"value": 78, "label": "نسبة المصروفات %", "higher_is_better": False},
    },
    "distribution": {
        "margin": {"value": 8, "label": "هامش الربح %", "higher_is_better": True},
        "repeat_rate": {"value": 55, "label": "معدل تكرار العملاء %", "higher_is_better": True},
        "growth": {"value": 5, "label": "النمو الشهري %", "higher_is_better": True},
        "expense_ratio": {"value": 80, "label": "نسبة المصروفات %", "higher_is_better": False},
    },
    "services": {
        "margin": {"value": 25, "label": "هامش الربح %", "higher_is_better": True},
        "repeat_rate": {"value": 45, "label": "معدل تكرار العملاء %", "higher_is_better": True},
        "growth": {"value": 7, "label": "النمو الشهري %", "higher_is_better": True},
        "expense_ratio": {"value": 60, "label": "نسبة المصروفات %", "higher_is_better": False},
    },
    "clinics": {
        "margin": {"value": 28, "label": "هامش الربح %", "higher_is_better": True},
        "repeat_rate": {"value": 50, "label": "معدل تكرار المرضى %", "higher_is_better": True},
        "growth": {"value": 6, "label": "النمو الشهري %", "higher_is_better": True},
        "expense_ratio": {"value": 58, "label": "نسبة المصروفات %", "higher_is_better": False},
    },
    "hospitals": {
        "margin": {"value": 15, "label": "هامش الربح %", "higher_is_better": True},
        "repeat_rate": {"value": 45, "label": "معدل تكرار المرضى %", "higher_is_better": True},
        "growth": {"value": 4, "label": "النمو الشهري %", "higher_is_better": True},
        "expense_ratio": {"value": 72, "label": "نسبة المصروفات %", "higher_is_better": False},
    },
    "logistics": {
        "margin": {"value": 12, "label": "هامش الربح %", "higher_is_better": True},
        "repeat_rate": {"value": 60, "label": "معدل تكرار العملاء %", "higher_is_better": True},
        "growth": {"value": 6, "label": "النمو الشهري %", "higher_is_better": True},
        "expense_ratio": {"value": 75, "label": "نسبة المصروفات %", "higher_is_better": False},
    },
    "other": {
        "margin": {"value": 15, "label": "هامش الربح %", "higher_is_better": True},
        "repeat_rate": {"value": 35, "label": "معدل تكرار العملاء %", "higher_is_better": True},
        "growth": {"value": 5, "label": "النمو الشهري %", "higher_is_better": True},
        "expense_ratio": {"value": 70, "label": "نسبة المصروفات %", "higher_is_better": False},
    },
}


@app.get("/company/benchmarks")
def company_benchmarks(user: User = Depends(get_current_user)):
    """يقارن أداء شركتك مع متوسط قطاعك."""
    with Session(engine) as s:
        if not user.company_id:
            raise HTTPException(403, "لا توجد شركة نشطة")
        company = s.get(Company, user.company_id)
        if not company or company.owner_id != user.id:
            raise HTTPException(403, "غير مصرّح")
        if company.is_active != 1:
            raise HTTPException(402, "شركتك قيد التفعيل")

        sector = company.sector or "other"
        bench = SECTOR_BENCHMARKS.get(sector, SECTOR_BENCHMARKS["other"])
        sector_label = SECTOR_NAMES.get(sector, "أخرى")

        # حساب الفعلي
        branches = s.exec(select(CompanyBranch).where(CompanyBranch.company_id == company.id, CompanyBranch.is_active == 1)).all()
        rows = []
        for b in branches:
            e = s.exec(select(CompanyEntry).where(CompanyEntry.branch_id == b.id).order_by(CompanyEntry.created_at.desc())).first()
            if e: rows.append(e)
        if not rows:
            raise HTTPException(400, "لا توجد بيانات فروع — أدخل البيانات أولاً")

        total_sales = sum(e.sales for e in rows)
        total_expenses = sum(e.expenses for e in rows)
        margin_vals = [e.margin for e in rows if e.sales > 0]
        repeat_vals = [e.repeat_rate for e in rows if e.repeat_rate > 0]
        growth_vals = [e.growth for e in rows if e.growth != 0]

        actuals = {
            "margin": round(sum(margin_vals)/len(margin_vals), 1) if margin_vals else 0,
            "repeat_rate": round(sum(repeat_vals)/len(repeat_vals), 1) if repeat_vals else 0,
            "growth": round(sum(growth_vals)/len(growth_vals), 1) if growth_vals else 0,
            "expense_ratio": round((total_expenses/total_sales)*100, 1) if total_sales else 0,
        }

        comparisons = []
        for key, b in bench.items():
            actual = actuals.get(key, 0)
            target = b["value"]
            diff = round(actual - target, 1)
            # تقدير الفجوة
            if b["higher_is_better"]:
                gap_pct = round((actual - target) / target * 100, 1) if target else 0
                better = actual >= target
            else:
                gap_pct = round((target - actual) / target * 100, 1) if target else 0
                better = actual <= target

            if better and abs(gap_pct) < 10:
                status = "🟢"; verdict = "مطابق للسوق"; color = "#10b981"
            elif better:
                status = "🟢"; verdict = "متفوّق على السوق"; color = "#10b981"
            elif abs(gap_pct) < 10:
                status = "🟡"; verdict = "قريب من السوق"; color = "#f5b301"
            elif abs(gap_pct) < 25:
                status = "🟠"; verdict = "دون متوسط السوق"; color = "#f59e0b"
            else:
                status = "🔴"; verdict = "فجوة كبيرة"; color = "#ef4444"

            comparisons.append({
                "key": key, "label": b["label"],
                "actual": actual, "benchmark": target,
                "diff": diff, "gap_pct": gap_pct,
                "higher_is_better": b["higher_is_better"],
                "status": status, "verdict": verdict, "color": color,
            })

        # ملخّص عام
        green = sum(1 for c in comparisons if c["status"] == "🟢")
        red = sum(1 for c in comparisons if c["status"] in ("🟠", "🔴"))
        if green >= len(comparisons) * 0.75:
            summary = f"أداء قوي مقابل قطاع {sector_label} — متفوّق في معظم المؤشرات."
        elif red >= len(comparisons) * 0.5:
            summary = f"عدة فجوات مقابل متوسطات قطاع {sector_label} — تحتاج خطة تحسين."
        else:
            summary = f"أداء متباين مقابل قطاع {sector_label} — نقاط قوة ونقاط للتحسين."

        return {
            "company": {"name": company.name},
            "sector": sector_label,
            "comparisons": comparisons,
            "summary": summary,
            "note": "المرجعية تقديرية إرشادية لمتوسطات القطاع، وليست أرقاماً رسمية. تُستخدم للمقارنة العامة فقط.",
        }


# ============================================================
# ===== AI Root Cause: تحليل الأسباب الجذرية بالأدلة =====
# ============================================================

@app.get("/company/root-cause")
def company_root_cause(user: User = Depends(get_current_user)):
    """يكتشف الأسباب الجذرية لانخفاض الربحية مع نسبة الإسهام والأدلة."""
    with Session(engine) as s:
        if not user.company_id:
            raise HTTPException(403, "لا توجد شركة نشطة")
        company = s.get(Company, user.company_id)
        if not company or company.owner_id != user.id:
            raise HTTPException(403, "غير مصرّح")
        if company.is_active != 1:
            raise HTTPException(402, "شركتك قيد التفعيل")

        branches = s.exec(select(CompanyBranch).where(CompanyBranch.company_id == company.id, CompanyBranch.is_active == 1)).all()
        rows = []
        for b in branches:
            ents = s.exec(select(CompanyEntry).where(CompanyEntry.branch_id == b.id).order_by(CompanyEntry.created_at.desc())).all()
            if ents: rows.append((b, ents))
        if not rows:
            raise HTTPException(400, "لا توجد بيانات فروع")

        # المؤشّر الذي نحلل سببه: الربح
        total_sales = sum(r[1][0].sales for r in rows)
        total_expenses = sum(r[1][0].expenses for r in rows)
        total_profit = total_sales - total_expenses
        avg_margin = (total_profit/total_sales*100) if total_sales else 0
        avg_growth = sum(r[1][0].growth for r in rows)/len(rows) if rows else 0

        sector = company.sector or "other"
        bench = SECTOR_BENCHMARKS.get(sector, SECTOR_BENCHMARKS["other"])

        # ============= الكشف =============
        causes = []  # كل سبب: title, contribution, confidence, evidence, recommendation

        # 1) هامش الربح أقل من القطاع
        bench_margin = bench["margin"]["value"]
        if avg_margin < bench_margin - 3:
            gap = bench_margin - avg_margin
            causes.append({
                "title": "هامش الربح أقل من متوسط القطاع",
                "contribution": min(round(gap * 4), 35),
                "confidence": 85 if len(rows) >= 3 else 65,
                "evidence": [
                    f"هامشك الفعلي: {round(avg_margin,1)}%",
                    f"متوسط قطاع {SECTOR_NAMES.get(sector,'')}: {bench_margin}%",
                    f"الفجوة: {round(gap,1)} نقطة مئوية",
                ],
                "recommendation": "راجع التسعير وتكلفة المبيعات. خفّض ٢-٣ بنود مصاريف غير حرجة.",
            })

        # 2) فروع ضعيفة تجرّ الإجمالي
        weak = [r for r in rows if r[1][0].branch_score < 50]
        if weak and len(weak) >= max(1, len(rows)//3):
            weak_loss = sum(r[1][0].sales*0.1 for r in weak)  # تقدير: 10% من مبيعات الضعيف لو حسّن
            causes.append({
                "title": f"{len(weak)} فرع ضعيف الأداء يخفض الإجمالي",
                "contribution": min(round((weak_loss/max(total_sales,1))*100), 30),
                "confidence": 90,
                "evidence": [
                    f"الفروع الضعيفة: {'، '.join(w[0].name for w in weak[:3])}",
                    f"مؤشّر أداءها أقل من 50/100",
                    f"تساهم بـ{round(sum(w[1][0].sales for w in weak)/total_sales*100,1)}% من المبيعات بإنتاجية أدنى من المتوقع",
                ],
                "recommendation": "افتح مقارنة الفروع وطبّق ممارسات الفرع الأفضل على هذه الفروع.",
            })

        # 3) ارتفاع المصروفات للمبيعات
        exp_ratio = (total_expenses/total_sales*100) if total_sales else 0
        bench_exp = bench["expense_ratio"]["value"]
        if exp_ratio > bench_exp + 5:
            gap = exp_ratio - bench_exp
            causes.append({
                "title": "نسبة المصروفات أعلى من متوسط القطاع",
                "contribution": min(round(gap * 2), 30),
                "confidence": 80,
                "evidence": [
                    f"نسبة مصروفاتك: {round(exp_ratio,1)}%",
                    f"متوسط القطاع: {bench_exp}%",
                    f"المصاريف الزائدة المقدرة: {round(total_sales*(gap/100))} ريال",
                ],
                "recommendation": "راجع وحدة المالية: COGS والرواتب والإيجارات — وأكبر بنود التسرّب.",
            })

        # 4) تراجع النمو
        if avg_growth < -5:
            causes.append({
                "title": "تراجع المبيعات (نمو سالب)",
                "contribution": min(round(abs(avg_growth) * 2), 25),
                "confidence": 85,
                "evidence": [
                    f"متوسط النمو الشهري: {round(avg_growth,1)}%",
                    f"عدد الفروع المتراجعة: {len([r for r in rows if r[1][0].growth<0])}/{len(rows)}",
                ],
                "recommendation": "افحص الأحداث المؤثرة، راجع التسويق والمنافسين، تحقق من رضا العملاء.",
            })

        # 5) ضعف الاحتفاظ بالعملاء
        repeat_vals = [r[1][0].repeat_rate for r in rows if r[1][0].repeat_rate > 0]
        avg_repeat = sum(repeat_vals)/len(repeat_vals) if repeat_vals else 0
        bench_repeat = bench["repeat_rate"]["value"]
        if repeat_vals and avg_repeat < bench_repeat - 5:
            gap = bench_repeat - avg_repeat
            causes.append({
                "title": "ضعف معدل تكرار العملاء",
                "contribution": min(round(gap), 20),
                "confidence": 75,
                "evidence": [
                    f"معدل تكرارك: {round(avg_repeat)}%",
                    f"متوسط القطاع: {bench_repeat}%",
                    f"الفجوة: {round(gap)} نقطة",
                ],
                "recommendation": "افتح وحدة العملاء — راجع NPS، شكاوى، مدة الاستجابة.",
            })

        # 6) مؤشرات تسرّب
        try:
            company_exp_ratio = (total_expenses / total_sales * 100) if total_sales else 0
            risky = []
            for b, ents in rows:
                r = detect_leakage(ents, company_exp_ratio)
                if r["risk"] >= 60:
                    risky.append((b.name, r["risk"]))
            if risky:
                causes.append({
                    "title": "اشتباه تسرّب في بعض الفروع",
                    "contribution": min(len(risky)*10, 20),
                    "confidence": 80,
                    "evidence": [f"فرع {n} — درجة مخاطرة {s}/100" for n, s in risky[:3]],
                    "recommendation": "افتح صفحة كشف التسرّب وراجع التفاصيل.",
                })
        except Exception: pass

        # ترتيب حسب المساهمة
        causes.sort(key=lambda c: c["contribution"], reverse=True)
        causes = causes[:5]  # أعلى ٥ أسباب
        total_contribution = sum(c["contribution"] for c in causes)

        return {
            "company": {"name": company.name},
            "metric": "تراجع الربحية",
            "current_value": f"هامش الربح {round(avg_margin,1)}% — متوسط القطاع {bench_margin}%",
            "causes": causes,
            "total_explained": min(total_contribution, 95),
            "summary": f"اكتشف نبّاه {len(causes)} أسباب رئيسية تُفسّر حوالي {min(total_contribution,95)}% من فجوة الأداء." if causes else "لا توجد أسباب جذرية واضحة — الأداء قريب من المتوقع.",
        }


@app.get("/company-benchmarks.html")
def page_company_benchmarks():
    return FileResponse("company-benchmarks.html")

@app.get("/company-root-cause.html")
def page_company_root_cause():
    return FileResponse("company-root-cause.html")
