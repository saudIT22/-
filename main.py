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

# ===== جداول الشركات =====
class Company(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str                                    # اسم الشركة
    owner_id: int = Field(index=True)            # المالك (user_id)
    plan: str = "enterprise"
    sector: str = "restaurant"                   # restaurant/cafe/retail
    is_active: int = 1
    created_at: datetime = Field(default_factory=datetime.now)

class Branch(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)          # تابع لأي شركة
    name: str                                    # اسم الفرع
    branch_type: str = "standalone"              # mall/residential/cloud/airport/standalone
    manager_id: Optional[int] = None            # مدير الفرع (user_id) — اختياري
    manager_name: str = ""                       # اسم مدير الفرع (للعرض)
    is_active: int = 1
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
# ===== COMPANY ENDPOINTS — باقة الشركات =====
# ============================================================

# --- صفحة تسجيل الشركة ---
@app.get("/company-register.html")
def page_company_register():
    return FileResponse("company-register.html")

@app.get("/company-dashboard.html")
def page_company_dashboard():
    return FileResponse("company-dashboard.html")

# --- إنشاء شركة جديدة ---
@app.post("/company/create")
def company_create(
    data: dict,
    user: User = Depends(get_current_user)
):
    company_name = data.get("name", "").strip()
    sector = data.get("sector", "restaurant")
    branches_raw = data.get("branches", [])  # قائمة أسماء الفروع

    if not company_name:
        raise HTTPException(400, "اسم الشركة مطلوب")
    if not branches_raw:
        raise HTTPException(400, "أضف فرعاً واحداً على الأقل")

    with Session(engine) as s:
        # السماح بـ 3 شركات كحد أقصى لكل مستخدم
        existing_companies = s.exec(select(Company).where(Company.owner_id == user.id)).all()
        if len(existing_companies) >= 3:
            raise HTTPException(400, "وصلت الحد الأقصى (3 شركات لكل حساب). احذف شركة قديمة لإضافة جديدة.")

        # تحقق من عدم تكرار الاسم لنفس المستخدم
        for c in existing_companies:
            if c.name.strip().lower() == company_name.strip().lower():
                raise HTTPException(400, f"لديك شركة بنفس الاسم '{company_name}' مسجّلة بالفعل")

        # إنشاء الشركة
        company = Company(name=company_name, owner_id=user.id, sector=sector)
        s.add(company)
        s.commit()
        s.refresh(company)

        # إنشاء الفروع
        for b_name in branches_raw:
            b_name = b_name.strip()
            if b_name:
                branch = Branch(company_id=company.id, name=b_name)
                s.add(branch)

        # ربط المستخدم بآخر شركة سجّلها (تكون النشطة)
        user_db = s.get(User, user.id)
        user_db.company_id = company.id
        user_db.company_role = "owner"
        s.add(user_db)
        s.commit()

        log_activity(user.name, f"أنشأ شركة: {company_name}", user.email)
        return {"ok": True, "company_id": company.id, "message": f"تم إنشاء {company_name} بنجاح"}


# --- لوحة المدير التنفيذي (بيانات حقيقية) ---
@app.get("/company/dashboard")
def company_dashboard(user: User = Depends(get_current_user)):
    if not user.company_id:
        raise HTTPException(403, "حسابك غير مرتبط بشركة")

    with Session(engine) as s:
        company = s.get(Company, user.company_id)
        if not company:
            raise HTTPException(404, "الشركة غير موجودة")
        if company.owner_id != user.id and user.company_role not in ("owner","manager"):
            raise HTTPException(403, "ليس لديك صلاحية")

        # جلب الفروع
        branches = s.exec(
            select(Branch).where(Branch.company_id == company.id, Branch.is_active == 1)
        ).all()

        branch_ids = [b.id for b in branches]
        branch_map = {b.id: b.name for b in branches}

        # جلب أحدث تحليل لكل فرع (مرتبط بمدير الفرع أو باسم الفرع)
        branch_stats = []
        total_revenue = 0
        total_profit = 0
        total_orders = 0

        for branch in branches:
            # الفروع مرتبطة بمستخدم مدير الفرع أو بالاسم في Entry.restaurant
            entries = s.exec(
                select(Entry)
                .where(Entry.restaurant == branch.name)
                .order_by(Entry.created_at.desc())
            ).all()

            if entries:
                latest = entries[0]
                # تاريخ 4 تحاليل للاتجاه
                trend = [e.revenue for e in reversed(entries[:4])]
                total_revenue += latest.revenue
                total_profit  += latest.profit
                total_orders  += latest.orders

                # تحديد حالة الفرع
                if latest.health_score >= 70:
                    status = "good"
                elif latest.health_score >= 45:
                    status = "warn"
                else:
                    status = "danger"

                branch_stats.append({
                    "id": branch.id,
                    "name": branch.name,
                    "revenue": latest.revenue,
                    "profit": latest.profit,
                    "margin": latest.margin,
                    "orders": latest.orders,
                    "health_score": latest.health_score,
                    "risk_score": latest.risk_score,
                    "status": status,
                    "trend": trend,
                    "last_updated": latest.created_at.isoformat() if latest.created_at else None,
                    "top_alert": latest.top_alert,
                    "top_decision": latest.top_decision,
                })
            else:
                # فرع بدون تحاليل بعد
                branch_stats.append({
                    "id": branch.id, "name": branch.name,
                    "revenue": 0, "profit": 0, "margin": 0,
                    "orders": 0, "health_score": 0, "risk_score": 0,
                    "status": "empty", "trend": [],
                    "last_updated": None, "top_alert": "", "top_decision": "",
                })

        # ترتيب الفروع من الأفضل للأضعف
        branch_stats.sort(key=lambda b: b["health_score"], reverse=True)

        # الصحة الكلية للشركة
        active = [b for b in branch_stats if b["status"] != "empty"]
        company_health = round(sum(b["health_score"] for b in active) / len(active)) if active else 0

        # أفضل وأضعف فرع
        best  = branch_stats[0] if branch_stats else None
        worst = branch_stats[-1] if len(branch_stats) > 1 else None

        # تنبيهات ذكية تلقائية
        alerts = []
        for b in branch_stats:
            if b["status"] == "danger":
                alerts.append({
                    "type": "danger",
                    "branch": b["name"],
                    "msg": f"درجة الصحة {b['health_score']} — يحتاج تدخلاً فورياً",
                    "detail": b["top_alert"] or "مصروفات مرتفعة أو إيرادات منخفضة"
                })
            elif b["status"] == "warn":
                alerts.append({
                    "type": "warn",
                    "branch": b["name"],
                    "msg": f"أداء متدنٍّ — درجة الصحة {b['health_score']}",
                    "detail": b["top_alert"] or "يحتاج مراجعة"
                })
            if b["health_score"] >= 80:
                alerts.append({
                    "type": "opportunity",
                    "branch": b["name"],
                    "msg": f"أداء ممتاز — يمكن الاستفادة من نموذجه",
                    "detail": b["top_decision"] or "ادرس أسلوب هذا الفرع وطبّقه على غيره"
                })

        return {
            "company": {"id": company.id, "name": company.name, "sector": company.sector},
            "summary": {
                "total_revenue": round(total_revenue),
                "total_profit": round(total_profit),
                "total_orders": total_orders,
                "company_health": company_health,
                "branch_count": len(branches),
                "best_branch": best["name"] if best else "",
                "worst_branch": worst["name"] if worst else "",
            },
            "branches": branch_stats,
            "alerts": alerts[:6],  # أهم 6 تنبيهات
        }


# --- معلومات شركة المستخدم ---
@app.get("/company/info")
def company_info(user: User = Depends(get_current_user)):
    """يرجّع كل شركات المستخدم + الشركة النشطة حالياً."""
    with Session(engine) as s:
        # كل الشركات اللي يملكها المستخدم
        all_companies = s.exec(select(Company).where(Company.owner_id == user.id)).all()

        # أو شركة هو موظف فيها (مدير فرع/محاسب)
        if not all_companies and user.company_id:
            c = s.get(Company, user.company_id)
            if c:
                all_companies = [c]

        if not all_companies:
            return {"has_company": False, "companies": [], "max_reached": False}

        # الشركة النشطة (المرتبط بها user.company_id)
        active_id = user.company_id

        companies_list = []
        active_company = None
        active_branches = []

        for c in all_companies:
            branches = s.exec(select(Branch).where(Branch.company_id == c.id, Branch.is_active == 1)).all()
            companies_list.append({
                "id": c.id,
                "name": c.name,
                "sector": c.sector,
                "branch_count": len(branches),
                "is_active": c.id == active_id,
            })
            if c.id == active_id:
                active_company = c
                active_branches = branches

        # إذا ما فيه نشطة، خذ أول وحدة
        if not active_company:
            active_company = all_companies[0]
            active_branches = s.exec(select(Branch).where(Branch.company_id == active_company.id, Branch.is_active == 1)).all()
            # حدّث المستخدم
            udb = s.get(User, user.id)
            udb.company_id = active_company.id
            if not udb.company_role:
                udb.company_role = "owner" if active_company.owner_id == user.id else "manager"
            s.add(udb); s.commit()

        return {
            "has_company": True,
            "companies": companies_list,
            "company": {"id": active_company.id, "name": active_company.name, "sector": active_company.sector},
            "branches": [{"id": b.id, "name": b.name} for b in active_branches],
            "role": user.company_role,
            "max_reached": len(all_companies) >= 3,
            "can_add_more": len(all_companies) < 3,
        }


# --- تبديل الشركة النشطة ---
@app.post("/company/switch")
def company_switch(data: dict, user: User = Depends(get_current_user)):
    """يبدّل الشركة النشطة للمستخدم."""
    company_id = data.get("company_id")
    if not company_id:
        raise HTTPException(400, "أرسل company_id")
    with Session(engine) as s:
        company = s.get(Company, int(company_id))
        if not company:
            raise HTTPException(404, "الشركة غير موجودة")
        if company.owner_id != user.id:
            raise HTTPException(403, "هذه الشركة ليست لك")

        udb = s.get(User, user.id)
        udb.company_id = company.id
        udb.company_role = "owner"
        s.add(udb); s.commit()
        return {"ok": True, "company": {"id": company.id, "name": company.name}}


# --- إضافة فرع جديد ---
@app.post("/company/add-branch")
def company_add_branch(data: dict, user: User = Depends(get_current_user)):
    branch_name = data.get("name", "").strip()
    if not branch_name:
        raise HTTPException(400, "اسم الفرع مطلوب")
    if not user.company_id or user.company_role not in ("owner",):
        raise HTTPException(403, "غير مصرّح")
    with Session(engine) as s:
        branch = Branch(company_id=user.company_id, name=branch_name)
        s.add(branch)
        s.commit()
        s.refresh(branch)
        log_activity(user.name, f"أضاف فرع: {branch_name}", user.email)
        return {"ok": True, "branch_id": branch.id}

# ============================================================
# ===== BRANCH COMPARISON — مقارنة الفروع الذكية (الخدمة 2) =====
# ============================================================

def ask_gemini(prompt: str) -> str:
    """استدعاء Gemini مع إعادة المحاولة — للأسئلة الذكية."""
    models = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-flash-latest"]
    for model_name in models:
        for attempt in range(2):
            try:
                r = ai_client.models.generate_content(model=model_name, contents=prompt)
                if r and r.text:
                    return r.text.strip()
            except Exception:
                time.sleep(1.5)
    return ""


def compute_branch_score(latest, all_branch_latest, sector):
    """مؤشر أداء موحّد للفرع من 100 — يجمع 5 عوامل."""
    bm = BENCHMARKS.get(sector, BENCHMARKS["restaurant"])

    # 1) الإيرادات (نسبةً لأعلى فرع) — 25 نقطة
    max_rev = max((b.revenue for b in all_branch_latest), default=1) or 1
    rev_score = (latest.revenue / max_rev) * 25

    # 2) الربحية (الهامش مقابل معيار القطاع) — 25 نقطة
    margin_score = min(latest.margin / bm["margin_good"], 1.0) * 25 if bm["margin_good"] else 0

    # 3) العملاء/الطلبات (نسبةً لأعلى فرع) — 20 نقطة
    max_ord = max((b.orders for b in all_branch_latest), default=1) or 1
    ord_score = (latest.orders / max_ord) * 20

    # 4) متوسط الفاتورة (مقابل المعيار) — 15 نقطة
    avg_ticket = (latest.revenue / latest.orders) if latest.orders else 0
    ticket_score = min(avg_ticket / bm["avg_ticket_good"], 1.0) * 15 if bm["avg_ticket_good"] else 0

    # 5) درجة الصحة العامة — 15 نقطة
    health_score = (latest.health_score / 100) * 15

    total = rev_score + margin_score + ord_score + ticket_score + health_score
    return round(min(total, 100)), {
        "revenue": round(rev_score, 1),
        "margin": round(margin_score, 1),
        "orders": round(ord_score, 1),
        "ticket": round(ticket_score, 1),
        "health": round(health_score, 1),
        "avg_ticket": round(avg_ticket),
    }


def analyze_root_cause(entries):
    """كشف أسباب تغيّر الأداء تلقائياً — يقارن آخر تحليلين."""
    if len(entries) < 2:
        return {"change_pct": 0, "reasons": [], "direction": "stable"}

    latest, prev = entries[0], entries[1]
    reasons = []

    # تغيّر الإيراد الكلي
    rev_change = ((latest.revenue - prev.revenue) / prev.revenue * 100) if prev.revenue else 0

    # متوسط الفاتورة
    at_now = (latest.revenue / latest.orders) if latest.orders else 0
    at_prev = (prev.revenue / prev.orders) if prev.orders else 0
    if at_prev:
        at_change = (at_now - at_prev) / at_prev * 100
        if at_change <= -8:
            reasons.append(f"انخفاض متوسط الفاتورة {abs(round(at_change))}٪")
        elif at_change >= 8:
            reasons.append(f"ارتفاع متوسط الفاتورة {round(at_change)}٪ ✅")

    # المصروفات
    if prev.expenses:
        exp_change = (latest.expenses - prev.expenses) / prev.expenses * 100
        if exp_change >= 12:
            reasons.append(f"زيادة المصروفات {round(exp_change)}٪")

    # عدد العملاء
    if prev.orders:
        ord_change = (latest.orders - prev.orders) / prev.orders * 100
        if ord_change <= -8:
            reasons.append(f"تراجع عدد العملاء {abs(round(ord_change))}٪")
        elif ord_change >= 10:
            reasons.append(f"نمو عدد العملاء {round(ord_change)}٪ ✅")

    # الهامش
    margin_change = latest.margin - prev.margin
    if margin_change <= -3:
        reasons.append(f"انخفاض هامش الربح {abs(round(margin_change,1))} نقطة")

    direction = "up" if rev_change > 3 else ("down" if rev_change < -3 else "stable")
    return {"change_pct": round(rev_change, 1), "reasons": reasons, "direction": direction}


def detect_anomalies(branch_data, company_avg):
    """كشف الفروع غير الطبيعية مقارنة بمتوسط الشركة."""
    anomalies = []
    rev = branch_data["revenue"]
    margin = branch_data["margin"]

    if company_avg["revenue"] and rev < company_avg["revenue"] * 0.66:
        diff = round((1 - rev / company_avg["revenue"]) * 100)
        anomalies.append(f"المبيعات أقل من متوسط الشركة بـ {diff}٪")
    if company_avg["margin"] and margin < company_avg["margin"] - 8:
        anomalies.append(f"هامش الربح أقل من متوسط الشركة بـ {round(company_avg['margin']-margin)} نقطة")
    if company_avg["avg_ticket"] and branch_data["scores"]["avg_ticket"] < company_avg["avg_ticket"] * 0.7:
        anomalies.append("متوسط الفاتورة منخفض بشكل غير طبيعي")
    return anomalies


@app.get("/company-branches.html")
def page_company_branches():
    return FileResponse("company-branches.html")


@app.get("/company/branches")
def company_branches_compare(user: User = Depends(get_current_user)):
    """المقارنة الذكية الكاملة للفروع."""
    if not user.company_id:
        raise HTTPException(403, "حسابك غير مرتبط بشركة")

    with Session(engine) as s:
        company = s.get(Company, user.company_id)
        if not company:
            raise HTTPException(404, "الشركة غير موجودة")
        if company.owner_id != user.id and user.company_role not in ("owner", "manager"):
            raise HTTPException(403, "ليس لديك صلاحية")

        branches = s.exec(
            select(Branch).where(Branch.company_id == company.id, Branch.is_active == 1)
        ).all()

        # اجمع آخر تحليل + تاريخ لكل فرع
        branch_entries = {}   # branch_id -> list entries (desc)
        latest_per_branch = []
        for b in branches:
            entries = s.exec(
                select(Entry).where(Entry.restaurant == b.name).order_by(Entry.created_at.desc())
            ).all()
            branch_entries[b.id] = entries
            if entries:
                latest_per_branch.append(entries[0])

        if not latest_per_branch:
            return {"company": {"name": company.name}, "branches": [], "managers": [],
                    "best_practices": None, "empty": True}

        # متوسطات الشركة
        n = len(latest_per_branch)
        company_avg = {
            "revenue": sum(e.revenue for e in latest_per_branch) / n,
            "margin": sum(e.margin for e in latest_per_branch) / n,
            "avg_ticket": sum((e.revenue/e.orders if e.orders else 0) for e in latest_per_branch) / n,
        }

        results = []
        for b in branches:
            entries = branch_entries[b.id]
            if not entries:
                results.append({
                    "id": b.id, "name": b.name, "type": b.branch_type,
                    "manager": b.manager_name, "score": 0, "status": "empty",
                    "revenue": 0, "margin": 0, "orders": 0,
                    "scores": {}, "root_cause": None, "anomalies": [], "forecast": None,
                })
                continue

            latest = entries[0]
            score, breakdown = compute_branch_score(latest, latest_per_branch, company.sector)
            root = analyze_root_cause(entries)

            bdata = {
                "id": b.id, "name": b.name, "type": b.branch_type,
                "manager": b.manager_name,
                "score": score,
                "revenue": round(latest.revenue),
                "margin": latest.margin,
                "orders": latest.orders,
                "health": latest.health_score,
                "scores": breakdown,
                "root_cause": root,
                "top_item": latest.top_item,
                "last_updated": latest.created_at.isoformat() if latest.created_at else None,
            }
            bdata["anomalies"] = detect_anomalies(bdata, company_avg)

            # حالة الفرع
            if score >= 75: bdata["status"] = "excellent"
            elif score >= 55: bdata["status"] = "good"
            elif score >= 40: bdata["status"] = "warn"
            else: bdata["status"] = "danger"

            # توقع بسيط (اتجاه آخر 3 تحاليل)
            revs = [e.revenue for e in reversed(entries[:3])]
            if len(revs) >= 2:
                growth = (revs[-1] - revs[0]) / revs[0] * 100 if revs[0] else 0
                bdata["forecast"] = {
                    "next_revenue": round(revs[-1] * (1 + growth/100/2)),
                    "growth_pct": round(growth, 1),
                }
            else:
                bdata["forecast"] = None

            results.append(bdata)

        # ترتيب حسب المؤشر
        results.sort(key=lambda x: x["score"], reverse=True)
        for i, r in enumerate(results):
            r["rank"] = i + 1

        # ترتيب المدراء (الفروع اللي لها مدير)
        managers = [{"name": r["manager"], "branch": r["name"], "score": r["score"]}
                    for r in results if r["manager"]]
        managers.sort(key=lambda x: x["score"], reverse=True)

        # أفضل الممارسات (من الفرع الأعلى)
        best_practices = None
        ranked = [r for r in results if r["status"] != "empty"]
        if len(ranked) >= 2:
            top = ranked[0]
            bottom = ranked[-1]
            gap_ticket = top["scores"].get("avg_ticket", 0) - bottom["scores"].get("avg_ticket", 0)
            best_practices = {
                "top_branch": top["name"],
                "top_score": top["score"],
                "top_avg_ticket": top["scores"].get("avg_ticket", 0),
                "top_item": top.get("top_item", ""),
                "ticket_gap": gap_ticket,
                "suggestion": f"فرع {top['name']} متوسط فاتورته أعلى بـ {gap_ticket} ريال. ادرس قائمته وأسلوب البيع وطبّقه على الفروع الأضعف.",
            }

        return {
            "company": {"name": company.name, "sector": company.sector},
            "branches": results,
            "managers": managers,
            "best_practices": best_practices,
            "company_avg": {k: round(v) for k, v in company_avg.items()},
            "empty": False,
        }


@app.post("/company/branch-ask")
def company_branch_ask(data: dict, user: User = Depends(get_current_user)):
    """محرك الأسئلة الذكي — يسأل المدير بالعربي عن فروعه."""
    if not user.company_id:
        raise HTTPException(403, "حسابك غير مرتبط بشركة")
    question = data.get("question", "").strip()
    if not question:
        raise HTTPException(400, "اكتب سؤالك")

    with Session(engine) as s:
        company = s.get(Company, user.company_id)
        branches = s.exec(
            select(Branch).where(Branch.company_id == company.id, Branch.is_active == 1)
        ).all()

        # اجمع سياق كل فرع
        context_lines = []
        for b in branches:
            latest = s.exec(
                select(Entry).where(Entry.restaurant == b.name).order_by(Entry.created_at.desc())
            ).first()
            if latest:
                at = round(latest.revenue/latest.orders) if latest.orders else 0
                context_lines.append(
                    f"- فرع {b.name}: إيرادات {round(latest.revenue)} ريال، ربح {round(latest.profit)}، "
                    f"هامش {latest.margin}٪، عملاء {latest.orders}، متوسط الفاتورة {at} ريال، "
                    f"درجة الصحة {latest.health_score}/100، أكثر صنف: {latest.top_item}"
                )
            else:
                context_lines.append(f"- فرع {b.name}: لا توجد بيانات بعد")

        context = "\n".join(context_lines)

        prompt = f"""أنت "نبّاه"، محلل أعمال خبير. مدير شركة "{company.name}" يسألك سؤالاً عن فروعه.
استخدم البيانات التالية فقط، وأجب بإجابة عملية مباشرة بالعربية (لا تتجاوز 6 أسطر)، مدعومة بالأرقام، واذكر أسباباً وتوصية واضحة.

بيانات الفروع:
{context}

سؤال المدير: {question}

أجب الآن بشكل تنفيذي مختصر:"""

        answer = ask_gemini(prompt)
        if not answer:
            answer = "الخدمة مزدحمة حالياً، حاول بعد لحظات."
        return {"answer": answer}


@app.post("/company/simulate")
def company_simulate(data: dict, user: User = Depends(get_current_user)):
    """محاكي القرارات — يتوقع أثر قرار قبل تنفيذه على كل الفروع."""
    if not user.company_id:
        raise HTTPException(403, "حسابك غير مرتبط بشركة")

    # القرارات: price_change%, discount_change%, staff_change% (يؤثر على المصروفات)
    price_change = float(data.get("price_change", 0))      # رفع/خفض الأسعار %
    staff_change = float(data.get("staff_change", 0))      # زيادة/نقص الموظفين %
    marketing = float(data.get("marketing", 0))           # ميزانية تسويق إضافية %

    with Session(engine) as s:
        company = s.get(Company, user.company_id)
        branches = s.exec(
            select(Branch).where(Branch.company_id == company.id, Branch.is_active == 1)
        ).all()

        results = []
        tot_before_profit = 0
        tot_after_profit = 0

        for b in branches:
            latest = s.exec(
                select(Entry).where(Entry.restaurant == b.name).order_by(Entry.created_at.desc())
            ).first()
            if not latest:
                continue

            revenue = latest.revenue
            expenses = latest.expenses
            orders = latest.orders

            # أثر رفع الأسعار: الإيراد يزيد، لكن الطلب قد ينخفض (مرونة سعرية ~0.4)
            price_effect = price_change / 100
            demand_drop = price_effect * 0.4   # كل 1% رفع سعر = 0.4% نقص طلب
            new_revenue = revenue * (1 + price_effect) * (1 - demand_drop)

            # أثر التسويق: زيادة طلب تقديرية
            new_revenue *= (1 + (marketing/100) * 0.5)

            # أثر الموظفين على المصروفات (رواتب ~30% من المصروفات)
            new_expenses = expenses * (1 + (staff_change/100) * 0.30)
            # التسويق يزيد المصروفات
            new_expenses *= (1 + (marketing/100) * 0.15)

            before_profit = revenue - expenses
            after_profit = new_revenue - new_expenses
            tot_before_profit += before_profit
            tot_after_profit += after_profit

            results.append({
                "name": b.name,
                "before_profit": round(before_profit),
                "after_profit": round(after_profit),
                "diff": round(after_profit - before_profit),
                "diff_pct": round((after_profit-before_profit)/before_profit*100,1) if before_profit else 0,
            })

        total_diff = tot_after_profit - tot_before_profit
        return {
            "branches": results,
            "summary": {
                "before_profit": round(tot_before_profit),
                "after_profit": round(tot_after_profit),
                "total_diff": round(total_diff),
                "total_diff_pct": round(total_diff/tot_before_profit*100,1) if tot_before_profit else 0,
            }
        }

# ============================================================
# ===== TEAM PERMISSIONS — صلاحيات الفريق (الخدمة 3) =====
# ============================================================

@app.get("/company-team.html")
def page_company_team():
    return FileResponse("company-team.html")


@app.get("/company/team")
def company_team_list(user: User = Depends(get_current_user)):
    """قائمة أعضاء الفريق — للمالك فقط."""
    if not user.company_id:
        raise HTTPException(403, "حسابك غير مرتبط بشركة")
    with Session(engine) as s:
        company = s.get(Company, user.company_id)
        if company.owner_id != user.id:
            raise HTTPException(403, "هذه الصفحة للمالك فقط")

        members = s.exec(
            select(User).where(User.company_id == user.company_id)
        ).all()
        branches = s.exec(
            select(Branch).where(Branch.company_id == user.company_id, Branch.is_active == 1)
        ).all()
        branch_map = {b.id: b.name for b in branches}

        team = []
        for m in members:
            # اسم الفرع المرتبط بمدير الفرع
            assigned = ""
            for b in branches:
                if b.manager_id == m.id:
                    assigned = b.name
                    break
            team.append({
                "id": m.id,
                "name": m.name,
                "email": m.email,
                "role": m.company_role or "owner",
                "assigned_branch": assigned,
                "is_owner": m.id == company.owner_id,
            })

        return {
            "company": {"name": company.name},
            "team": team,
            "branches": [{"id": b.id, "name": b.name, "manager_id": b.manager_id} for b in branches],
        }


@app.post("/company/team/add")
def company_team_add(data: dict, user: User = Depends(get_current_user)):
    """إضافة عضو فريق جديد — ينشئ حساب ويربطه بالشركة."""
    if not user.company_id:
        raise HTTPException(403, "حسابك غير مرتبط بشركة")

    name = data.get("name", "").strip()
    email = data.get("email", "").strip().lower()
    password = data.get("password", "").strip()
    role = data.get("role", "staff")          # manager / accountant / staff
    branch_id = data.get("branch_id")          # للمدير: الفرع المسؤول عنه

    if not name or not email or not password:
        raise HTTPException(400, "الاسم والبريد وكلمة المرور مطلوبة")
    if len(password) < 6:
        raise HTTPException(400, "كلمة المرور 6 أحرف على الأقل")
    if role not in ("manager", "accountant", "staff"):
        raise HTTPException(400, "الدور غير صحيح")

    with Session(engine) as s:
        company = s.get(Company, user.company_id)
        if company.owner_id != user.id:
            raise HTTPException(403, "فقط المالك يضيف أعضاء")

        # تأكد ما فيه حساب بنفس البريد
        existing = s.exec(select(User).where(User.email == email)).first()
        if existing:
            raise HTTPException(400, "هذا البريد مسجّل بالفعل")

        # أنشئ الحساب
        new_user = User(
            name=name,
            email=email,
            password_hash=hash_password(password),
            company_id=user.company_id,
            company_role=role,
            is_active=1,
            plan="enterprise_member",
        )
        s.add(new_user)
        s.commit()
        s.refresh(new_user)

        # لو مدير فرع، اربطه بالفرع
        if role == "manager" and branch_id:
            branch = s.get(Branch, int(branch_id))
            if branch and branch.company_id == user.company_id:
                branch.manager_id = new_user.id
                branch.manager_name = name
                s.add(branch)
                s.commit()

        log_activity(user.name, f"أضاف عضو فريق: {name} ({role})", email)
        return {"ok": True, "message": f"تم إضافة {name} بنجاح"}


@app.post("/company/team/remove")
def company_team_remove(data: dict, user: User = Depends(get_current_user)):
    """إزالة عضو من الفريق."""
    if not user.company_id:
        raise HTTPException(403, "حسابك غير مرتبط بشركة")
    member_id = data.get("member_id")

    with Session(engine) as s:
        company = s.get(Company, user.company_id)
        if company.owner_id != user.id:
            raise HTTPException(403, "فقط المالك يزيل أعضاء")
        if int(member_id) == company.owner_id:
            raise HTTPException(400, "لا يمكن إزالة المالك")

        member = s.get(User, int(member_id))
        if not member or member.company_id != user.company_id:
            raise HTTPException(404, "العضو غير موجود")

        # فك ارتباطه بأي فرع
        branches = s.exec(select(Branch).where(Branch.manager_id == member.id)).all()
        for b in branches:
            b.manager_id = None
            b.manager_name = ""
            s.add(b)

        # فك ارتباطه بالشركة (ما نحذف الحساب، فقط نفصله)
        member.company_id = None
        member.company_role = ""
        member.is_active = 0
        s.add(member)
        s.commit()

        log_activity(user.name, f"أزال عضو الفريق: {member.name}", member.email)
        return {"ok": True}

# ===== حذف الشركة (للتجربة والإعادة) =====
@app.post("/company/delete")
def company_delete(user: User = Depends(get_current_user)):
    """يحذف شركة المستخدم وكل فروعها — للمالك فقط."""
    if not user.company_id:
        raise HTTPException(403, "ليس لديك شركة")
    with Session(engine) as s:
        company = s.get(Company, user.company_id)
        if not company or company.owner_id != user.id:
            raise HTTPException(403, "فقط المالك يحذف الشركة")

        # احذف الفروع
        branches = s.exec(select(Branch).where(Branch.company_id == company.id)).all()
        for b in branches:
            s.delete(b)

        # فك ربط كل الأعضاء
        members = s.exec(select(User).where(User.company_id == company.id)).all()
        for m in members:
            m.company_id = None
            m.company_role = ""
            s.add(m)

        # احذف الشركة
        s.delete(company)
        s.commit()
        log_activity(user.name, f"حذف الشركة: {company.name}", user.email)
        return {"ok": True, "message": "تم حذف الشركة"}

# ============================================================
# ===== EXECUTIVE REPORT — التقرير التنفيذي التلقائي (الخدمة 4) =====
# ============================================================

@app.get("/company-report.html")
def page_company_report():
    return FileResponse("company-report.html")


@app.get("/company/report")
def company_report(user: User = Depends(get_current_user)):
    """تقرير تنفيذي شامل: ملخص الشركة + كل الفروع + أهم القرارات."""
    if not user.company_id:
        raise HTTPException(403, "حسابك غير مرتبط بشركة")

    with Session(engine) as s:
        company = s.get(Company, user.company_id)
        if company.owner_id != user.id and user.company_role not in ("owner", "manager"):
            raise HTTPException(403, "ليس لديك صلاحية")

        branches = s.exec(
            select(Branch).where(Branch.company_id == company.id, Branch.is_active == 1)
        ).all()

        # جمع بيانات الفروع
        branch_reports = []
        latest_list = []
        total_revenue = total_profit = total_expenses = total_orders = 0

        for b in branches:
            entries = s.exec(
                select(Entry).where(Entry.restaurant == b.name).order_by(Entry.created_at.desc())
            ).all()
            if not entries:
                continue
            latest = entries[0]
            latest_list.append(latest)
            total_revenue += latest.revenue
            total_profit += latest.profit
            total_expenses += latest.expenses
            total_orders += latest.orders

            # اتجاه آخر تحليلين
            trend = "stable"
            if len(entries) >= 2:
                diff = latest.revenue - entries[1].revenue
                trend = "up" if diff > 0 else ("down" if diff < 0 else "stable")

            branch_reports.append({
                "name": b.name,
                "revenue": round(latest.revenue),
                "profit": round(latest.profit),
                "margin": latest.margin,
                "orders": latest.orders,
                "health_score": latest.health_score,
                "trend": trend,
                "top_alert": latest.top_alert or "",
                "top_decision": latest.top_decision or "",
                "top_item": latest.top_item or "",
            })

        if not branch_reports:
            return {"company": {"name": company.name}, "empty": True}

        # ترتيب
        branch_reports.sort(key=lambda x: x["health_score"], reverse=True)
        n = len(branch_reports)
        company_health = round(sum(b["health_score"] for b in branch_reports) / n)
        avg_margin = round(sum(b["margin"] for b in branch_reports) / n, 1)

        best = branch_reports[0]
        worst = branch_reports[-1]

        # أهم القرارات التنفيذية (مجمّعة)
        key_decisions = []
        # 1. الفرع الأضعف
        if worst["health_score"] < 50:
            key_decisions.append({
                "priority": "عاجل",
                "title": f"تدخّل فوري في فرع {worst['name']}",
                "detail": f"درجة صحته {worst['health_score']}/100 وهامشه {worst['margin']}٪. " + (worst["top_alert"] or "يحتاج مراجعة شاملة للمصروفات والمبيعات."),
            })
        # 2. تعميم نجاح الأفضل
        if best["health_score"] >= 75:
            key_decisions.append({
                "priority": "فرصة",
                "title": f"تعميم نموذج فرع {best['name']}",
                "detail": f"الأعلى أداءً ({best['health_score']}/100). ادرس أسلوبه" + (f" وأكثر أصنافه مبيعاً ({best['top_item']})" if best['top_item'] else "") + " وطبّقه على باقي الفروع.",
            })
        # 3. الهامش العام
        if avg_margin < 20:
            key_decisions.append({
                "priority": "مهم",
                "title": "متوسط هامش الشركة منخفض",
                "detail": f"الهامش العام {avg_margin}٪ دون المستوى الصحي. راجع التسعير وهيكل التكاليف عبر الفروع.",
            })

        # توزيع حالة الفروع
        excellent = len([b for b in branch_reports if b["health_score"] >= 70])
        warning = len([b for b in branch_reports if 45 <= b["health_score"] < 70])
        critical = len([b for b in branch_reports if b["health_score"] < 45])

        from datetime import datetime as _dt
        return {
            "company": {"name": company.name, "sector": company.sector},
            "generated_at": _dt.now().strftime("%Y-%m-%d %H:%M"),
            "summary": {
                "total_revenue": round(total_revenue),
                "total_profit": round(total_profit),
                "total_expenses": round(total_expenses),
                "total_orders": total_orders,
                "company_health": company_health,
                "avg_margin": avg_margin,
                "branch_count": n,
                "best_branch": best["name"],
                "worst_branch": worst["name"],
                "excellent": excellent,
                "warning": warning,
                "critical": critical,
            },
            "branches": branch_reports,
            "key_decisions": key_decisions,
            "empty": False,
        }
