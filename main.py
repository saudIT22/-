import os
import time
import re
import json
import jwt
import bcrypt
from dotenv import load_dotenv
from google import genai
from fastapi import FastAPI, HTTPException, Header, Depends, UploadFile, File, Form, Request
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import csv
import io
from pydantic import BaseModel
from typing import Optional
from sqlmodel import SQLModel, Field, create_engine, Session, select
from datetime import datetime, timedelta


def _now_naive():
    """وقت بلا منطقة زمنية — يقبله SQLModel الحديث للتخزين (naive storage)."""
    return datetime.now()


try:
    from pydantic import NaiveDatetime as _ND
    _DTCOL = _ND
except Exception:  # توافق مع إصدارات أقدم
    _DTCOL = datetime

load_dotenv()
ai_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

# مفتاح سري لتوقيع رموز الدخول (يُضبط في Railway Variables)
_secret_env = os.getenv("SECRET_KEY", "").strip()
# نحدّد البيئة: production يجب أن يفشل بوضوح لو المفتاح غائب
_env = os.getenv("ENVIRONMENT", os.getenv("ENV", "production")).strip().lower()
_is_testing = os.getenv("TESTING", "").strip().lower() in ("1", "true", "yes")
if not _secret_env:
    if _is_testing or _env in ("development", "dev", "local"):
        # بيئة تطوير/اختبار فقط: مفتاح مؤقت واضح التمييز
        import secrets as _secrets
        _secret_env = "dev-only-" + _secrets.token_urlsafe(32)
        print("ℹ️  [DEV] SECRET_KEY غير مضبوط — استُخدم مفتاح تطوير مؤقت (غير صالح للإنتاج).")
    else:
        # الإنتاج: نفشل بوضوح بدل التوليد الصامت — تنبيه صريح لضبط المتغيّر
        raise RuntimeError(
            "خطأ إعداد أمني حرج: SECRET_KEY غير مضبوط في متغيّرات البيئة. "
            "اضبط SECRET_KEY في Railway Variables قبل التشغيل. "
            "(للتطوير فقط: اضبط ENVIRONMENT=development أو TESTING=true)"
        )
SECRET_KEY = _secret_env
TOKEN_DAYS = 30  # مدة صلاحية الدخول

app = FastAPI()

# CORS: مقيّد على نطاقات نبّاه فقط (يمنع أي موقع خارجي من استدعاء الـAPI)
_allowed_origins = [
    "https://nabbah.com",
    "https://www.nabbah.com",
    "https://nabbah.up.railway.app",
]
# دعم ALLOWED_ORIGINS من البيئة (comma-separated) — يُضاف للقائمة الأساسية
_allowed_env = os.getenv("ALLOWED_ORIGINS", "").strip()
if _allowed_env:
    for _o in _allowed_env.split(","):
        _o = _o.strip()
        if _o and _o not in _allowed_origins:
            _allowed_origins.append(_o)
_extra_origin = os.getenv("EXTRA_ORIGIN", "").strip()
if _extra_origin and _extra_origin not in _allowed_origins:
    _allowed_origins.append(_extra_origin)
# في بيئة التطوير/الاختبار: نسمح بـ localhost
if _is_testing or _env in ("development", "dev", "local"):
    for _o in ("http://localhost:8000", "http://127.0.0.1:8000"):
        if _o not in _allowed_origins:
            _allowed_origins.append(_o)
# أمان: لا نسمح أبداً بـ wildcard مع credentials
if "*" in _allowed_origins:
    _allowed_origins = [o for o in _allowed_origins if o != "*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Admin-Key"],
)


# ═══════════════════════════════════════════════════════════
#  طبقة معالجة الأخطاء الموحّدة (Error Handling Layer) — Phase 1.5
#  تُخفي التفاصيل الداخلية عن المستخدم، تسجّلها داخلياً للمطوّر.
#  لا تكشف: أسماء جداول، مسارات، stack traces، تفاصيل قاعدة البيانات.
# ═══════════════════════════════════════════════════════════
import logging as _logging
_logging.basicConfig(level=_logging.INFO)
_logger = _logging.getLogger("nabbah")

from fastapi.responses import JSONResponse as _JSONResponse
from fastapi.exceptions import RequestValidationError as _ReqValidationError
from starlette.exceptions import HTTPException as _StarletteHTTPException


@app.exception_handler(_StarletteHTTPException)
async def _http_exception_handler(request: Request, exc):
    """معالج HTTPException — يمرّر الرسائل المقصودة (عربية آمنة) كما هي."""
    return _JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )


@app.exception_handler(_ReqValidationError)
async def _validation_exception_handler(request: Request, exc):
    """معالج أخطاء التحقق — رسالة عامة بدل تفاصيل Pydantic التقنية."""
    return _JSONResponse(
        status_code=422,
        content={"detail": "البيانات المُرسلة غير صحيحة — تأكّد من صحة الحقول."},
    )


import collections as _collections
import traceback as _traceback
import uuid as _uuid
_RECENT_ERRORS = _collections.deque(maxlen=100)   # آخر 100 عطل — تُقرأ من /admin/errors


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc):
    """معالج الأخطاء غير المتوقّعة: المستخدم يرى رسالة آمنة + رقم مرجع،
    والتفاصيل الكاملة تُسجَّل وتُحفظ لـ/admin/errors بنفس الرقم."""
    ref = _uuid.uuid4().hex[:8].upper()
    tb = _traceback.format_exception(type(exc), exc, exc.__traceback__)
    _RECENT_ERRORS.appendleft({
        "ref": ref, "time": datetime.now().isoformat(timespec="seconds"),
        "method": request.method, "path": request.url.path,
        "error": f"{type(exc).__name__}: {str(exc)[:500]}",
        "trace": "".join(tb[-4:])[-3000:],
    })
    _logger.error(f"[{ref}] خطأ غير متوقّع في {request.method} {request.url.path}: "
                  f"{type(exc).__name__}: {str(exc)[:300]}")
    return _JSONResponse(
        status_code=500,
        content={"detail": f"حدث خطأ غير متوقّع. حاول مرة أخرى، وإن استمرّ تواصل مع الدعم. (رقم المرجع: {ref})",
                 "ref": ref},
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
    subscription_start: Optional[_DTCOL] = None
    subscription_end: Optional[_DTCOL] = None
    company_id: Optional[int] = None            # مرتبط بشركة؟ (للمدراء والموظفين)
    company_role: str = ""                       # owner/manager/staff — فارغ = فرد
    created_at: _DTCOL = Field(default_factory=_now_naive)

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
    created_at: _DTCOL = Field(default_factory=_now_naive)

class ActivityLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    actor: str = "النظام"
    action: str = ""
    target_email: str = ""
    created_at: _DTCOL = Field(default_factory=_now_naive)

# ===== جداول قسم الشركات (مستقل تماماً عن قسم المطاعم/الأفراد) =====
class Company(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str                                    # اسم الشركة
    owner_id: int = Field(index=True)            # المالك (user_id)
    plan: str = "enterprise"
    sector: str = "retail"                       # نشاط الشركة (القطاع)
    cash_reserve: float = 0                       # الاحتياطي النقدي الحالي (للتدفق النقدي)
    monthly_obligations: float = 0                # الالتزامات الشهرية الثابتة (رواتب/إيجار/أقساط)
    is_active: int = 1
    # ===== ملف الشركة: نبّاه يعرف الشركة نفسها ويكيّف التحليل =====
    employees: int = 0                            # عدد الموظفين
    annual_revenue: float = 0                     # الإيرادات السنوية التقريبية
    target_margin: float = 0                      # هامش الربح المستهدف (%)
    fiscal_year_start: int = 1                    # شهر بداية السنة المالية (1-12)
    currency: str = "SAR"                         # العملة
    country: str = "SA"                           # الدولة
    top_priority: str = "profit"                  # الأولوية القصوى: growth/profit/liquidity/efficiency
    goals_json: str = "{}"                        # أهداف مخصّصة (مبيعات/ربح/عملاء/احتفاظ...) JSON
    alerts_json: str = "{}"                        # تفضيلات التنبيهات JSON
    created_at: _DTCOL = Field(default_factory=_now_naive)

class CompanyBranch(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)          # تابع لأي شركة
    name: str                                    # اسم الفرع
    city: str = ""                               # المدينة (تُستخدم للخريطة)
    area: str = ""                               # الحي/المنطقة (اختياري)
    branch_type: str = "standalone"              # mall/strip/standalone/online/kiosk
    # ===== الهيكل التنظيمي المرن (P0 — رؤية أحمد) =====
    # Company → Business Unit (اختياري) → Department (اختياري) → Branch
    # كلها اختيارية: شركة بلا فروع/وحدات تعمل بشكل كامل.
    business_unit: str = ""                       # وحدة الأعمال (مثال: مطاعم / تجزئة) — اختياري
    department: str = ""                          # القسم (مثال: العمليات / المبيعات) — اختياري
    lat: float = 0.0                             # إحداثيات الفرع (تُملأ من المدينة)
    lng: float = 0.0
    target_sales: float = 0                      # هدف المبيعات الشهري (اختياري)
    target_customers: int = 0                    # هدف عدد العملاء الشهري (اختياري)
    is_active: int = 1
    created_at: _DTCOL = Field(default_factory=_now_naive)

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
    created_at: _DTCOL = Field(default_factory=_now_naive)

def log_activity(actor: str, action: str, target_email: str = ""):
    """يسجّل حدثاً في سجل النشاط."""
    try:
        with Session(engine) as s:
            s.add(ActivityLog(actor=actor, action=action, target_email=target_email))
            s.commit()
    except Exception as _dbe:
        _logger.error(f"db op failed (line ~274): {type(_dbe).__name__}: {str(_dbe)[:150]}")


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
    created_at: _DTCOL = Field(default_factory=_now_naive)


class CompanyDecision(SQLModel, table=True):
    """قرارات معتمدة قيد التنفيذ والمتابعة — تُغلق بقياس النتيجة."""
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    title: str = ""
    detail: str = ""
    owner: str = ""                 # المسؤول عن التنفيذ
    due_date: str = ""              # موعد الإنجاز (YYYY-MM-DD)
    kpi: str = ""                   # مؤشر النجاح
    status: str = "open"            # open / done / cancelled
    baseline_sales: float = 0       # مبيعات الشركة وقت اعتماد القرار (للقياس)
    result_sales: float = 0         # مبيعات الشركة وقت الإغلاق
    result_note: str = ""           # ملاحظة النتيجة
    expected_impact: str = ""        # التوقّع عند الاعتماد (المتوقع مقابل الفعلي)
    linked_to: str = ""              # معرّفات القرارات المرتبطة (يعتمد عليها) — مفصولة بفاصلة
    approver: str = ""               # المعتمِد (صاحب القرار النهائي) — RACI: Accountable
    reviewer: str = ""               # المراجع (يقيس النتيجة) — RACI: Consulted/Informed
    rationale: str = ""              # لماذا اتخذنا هذا القرار؟ (Decision Memory)
    created_at: _DTCOL = Field(default_factory=_now_naive)
    closed_at: Optional[_DTCOL] = None
    # Phase 2.3 — additive, nullable (existing rows unaffected)
    branch_id: Optional[int] = Field(default=None, index=True)
    metric_id: str = ""
    baseline_value: Optional[float] = None
    expected_impact_value: Optional[float] = None
    actual_value: Optional[float] = None
    actual_impact_value: Optional[float] = None
    impact_status: str = ""          # "" | expected | insufficient_data | verified
    source_signal: str = ""          # signal id the decision came from
    updated_at: Optional[_DTCOL] = None
    # Decision memory & outcomes (additive, nullable)
    problem_type: str = ""           # rule code of the problem (e.g. negative_margin_branch)
    decision_type: str = ""          # category (sales, cost, branch, profitability, ...)
    measurement_period: str = ""     # YYYY-MM measured
    outcome_status: str = ""         # pending_measurement | insufficient_data | measured | verified | not_verified | cancelled
    outcome_notes: str = ""
    created_by: str = ""
    data_source: str = ""


class CompanyAction(SQLModel, table=True):
    """Phase 2.3 — task that implements a decision."""
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    decision_id: int = Field(index=True)
    branch_id: Optional[int] = Field(default=None, index=True)
    title: str = ""
    description: str = ""
    owner: str = ""
    priority: str = "P2"             # P1 | P2 | P3
    start_date: str = ""
    due_date: str = ""
    status: str = "not_started"      # not_started | in_progress | blocked | completed | cancelled
    progress: int = 0                # 0..100
    notes: str = ""
    created_at: _DTCOL = Field(default_factory=_now_naive)
    updated_at: Optional[_DTCOL] = None


class CompanyScenario(SQLModel, table=True):
    """Saved what-if scenario. Results are ESTIMATES, never actual financial data."""
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    branch_id: Optional[int] = Field(default=None, index=True)
    created_by: str = ""
    name: str = ""
    description: str = ""
    scenario_type: str = "custom"
    base_period: str = ""
    assumptions: str = "{}"          # JSON
    baseline_values: str = "{}"      # JSON snapshot used at last run
    scenario_values: str = "{}"      # JSON comparison rows at last run
    results: str = "{}"              # JSON full run output
    status: str = "draft"            # draft | ran | archived
    created_at: _DTCOL = Field(default_factory=_now_naive)
    updated_at: Optional[_DTCOL] = None
    ran_at: Optional[_DTCOL] = None


class CompanyDataset(SQLModel, table=True):
    """Phase 2.4 — سجل رفع بيانات: مصدرها وجودتها ونتيجتها (lineage + history)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    dataset_type: str = Field(index=True)     # sale | purchase | inventory | employee | ...
    source_file: str = ""
    source_type: str = "upload"
    period: str = ""
    branches: str = ""                        # أسماء/أرقام الفروع المتأثرة
    total_rows: int = 0
    valid_rows: int = 0
    rejected_rows: int = 0
    quality_score: int = 0
    quality_gate: str = ""                    # ALLOW | QUALIFY | BLOCK
    status: str = "processing"                # processing | validated | imported | partial | rejected
    mapping_json: str = "{}"
    staging_json: str = "[]"                  # صفوف قيد المراجعة قبل الاعتماد
    errors_json: str = "[]"
    uploaded_by: str = ""
    created_at: _DTCOL = Field(default_factory=_now_naive)
    imported_at: Optional[_DTCOL] = None


class CompanyDepartment(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    branch_id: Optional[int] = Field(default=None, index=True)
    name: str = ""
    code: str = ""
    active: int = 1
    extra_json: str = ""
    created_at: _DTCOL = Field(default_factory=_now_naive)


class CompanyEmployee(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    branch_id: Optional[int] = Field(default=None, index=True)
    department_id: Optional[int] = None
    employee_code: str = ""
    name: str = ""
    role: str = ""
    employment_status: str = "active"
    hire_date: str = ""
    termination_date: str = ""
    monthly_cost: Optional[float] = None       # حسّاس: يخضع لقواعد الصلاحيات القائمة
    email: str = ""
    phone: str = ""
    # Phase 2.9 — ذكاء الموارد البشرية (كلها اختيارية)
    employment_type: str = ""
    manager: str = ""
    termination_type: str = ""
    basic_salary: Optional[float] = None       # حسّاس
    allowances: Optional[float] = None         # حسّاس
    benefits: Optional[float] = None           # حسّاس
    performance_rating: Optional[float] = None
    last_promotion_date: str = ""
    training_hours: Optional[float] = None
    absence_days: Optional[float] = None
    overtime_hours: Optional[float] = None
    critical_role: Optional[int] = None
    successors: Optional[float] = None
    extra_json: str = ""                       # أعمدة الملف الإضافية كما هي — لا يُفقد شيء
    dataset_id: Optional[int] = None
    source_row: Optional[int] = None
    created_at: _DTCOL = Field(default_factory=_now_naive)


class CompanyProduct(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    sku: str = Field(default="", index=True)
    name: str = ""
    category: str = ""
    unit: str = ""
    cost: Optional[float] = None
    selling_price: Optional[float] = None
    active: int = 1
    extra_json: str = ""
    dataset_id: Optional[int] = None
    created_at: _DTCOL = Field(default_factory=_now_naive)


class CompanySupplier(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    name: str = ""
    supplier_code: str = ""
    category: str = ""
    payment_terms: str = ""
    contact_person: str = ""
    email: str = ""
    phone: str = ""
    city: str = ""
    tax_number: str = ""
    active: int = 1
    extra_json: str = ""
    dataset_id: Optional[int] = None
    created_at: _DTCOL = Field(default_factory=_now_naive)


class CompanyCustomer(SQLModel, table=True):
    """العملاء — Phase 2.5. البريد والجوال بيانات تواصل تخضع لعزل الشركة وصلاحيات البيانات."""
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    branch_id: Optional[int] = Field(default=None, index=True)
    customer_code: str = Field(default="", index=True)
    name: str = ""
    email: str = ""
    phone: str = ""
    city: str = ""
    segment: str = ""
    customer_type: str = ""
    tax_number: str = ""
    credit_limit: Optional[float] = None
    active: int = 1
    notes: str = ""
    extra_json: str = ""
    dataset_id: Optional[int] = Field(default=None, index=True)
    source_row: Optional[int] = None
    created_at: _DTCOL = Field(default_factory=_now_naive)


class CompanySale(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    branch_id: Optional[int] = Field(default=None, index=True)
    date: str = Field(default="", index=True)
    period: str = Field(default="", index=True)
    reference: str = ""
    channel: str = ""
    product_sku: str = ""
    category: str = ""
    quantity: Optional[float] = None
    gross_sales: Optional[float] = None
    discounts: Optional[float] = None
    returns: Optional[float] = None
    net_sales: Optional[float] = None
    vat: Optional[float] = None
    payment_method: str = ""
    promotion: str = ""                        # الحملة الترويجية (اختياري) — Phase 2.5
    customer_name: str = ""
    extra_json: str = ""
    dataset_id: Optional[int] = Field(default=None, index=True)
    source_row: Optional[int] = None
    created_at: _DTCOL = Field(default_factory=_now_naive)


class CompanyPurchase(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    branch_id: Optional[int] = Field(default=None, index=True)
    supplier_id: Optional[int] = None
    supplier_name: str = ""
    date: str = Field(default="", index=True)
    period: str = Field(default="", index=True)
    reference: str = ""
    product_sku: str = ""
    category: str = ""
    quantity: Optional[float] = None
    unit_cost: Optional[float] = None
    total_cost: Optional[float] = None
    vat: Optional[float] = None
    status: str = ""
    expected_date: str = ""                    # Phase 2.7 — التسليم والجودة
    received_date: str = ""
    received_qty: Optional[float] = None
    rejected_qty: Optional[float] = None
    extra_json: str = ""
    dataset_id: Optional[int] = Field(default=None, index=True)
    source_row: Optional[int] = None
    created_at: _DTCOL = Field(default_factory=_now_naive)


class CompanyInventory(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    branch_id: Optional[int] = Field(default=None, index=True)
    product_sku: str = Field(default="", index=True)
    period: str = Field(default="", index=True)
    opening_qty: Optional[float] = None
    opening_value: Optional[float] = None
    purchases_qty: Optional[float] = None
    sold_qty: Optional[float] = None
    adjustments_qty: Optional[float] = None
    closing_qty: Optional[float] = None
    closing_value: Optional[float] = None
    extra_json: str = ""
    dataset_id: Optional[int] = Field(default=None, index=True)
    source_row: Optional[int] = None
    created_at: _DTCOL = Field(default_factory=_now_naive)


class CompanyReceivable(SQLModel, table=True):
    """الذمم المدينة — Phase 2.8 (التحصيل وDSO والتقادم). تستخدم نفس الفروع والعملاء."""
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    branch_id: Optional[int] = Field(default=None, index=True)
    customer_name: str = ""
    reference: str = ""
    invoice_date: str = Field(default="", index=True)
    due_date: str = ""
    amount: Optional[float] = None
    paid_amount: Optional[float] = None
    paid_date: str = ""
    extra_json: str = ""
    dataset_id: Optional[int] = Field(default=None, index=True)
    source_row: Optional[int] = None
    created_at: _DTCOL = Field(default_factory=_now_naive)


class CompanyJobOpening(SQLModel, table=True):
    """الوظائف الشاغرة ومسار التوظيف — Phase 2.9."""
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    branch_id: Optional[int] = Field(default=None, index=True)
    department_id: Optional[int] = None
    title: str = ""
    opened_date: str = ""
    filled_date: str = ""
    status: str = ""
    applicants: Optional[float] = None
    interviews: Optional[float] = None
    offers: Optional[float] = None
    hires: Optional[float] = None
    hiring_cost: Optional[float] = None
    extra_json: str = ""
    dataset_id: Optional[int] = Field(default=None, index=True)
    source_row: Optional[int] = None
    created_at: _DTCOL = Field(default_factory=_now_naive)


class CompanyOpsOrder(SQLModel, table=True):
    """طلب/خدمة تشغيلية — Phase 2.10. الأوقات بصيغة YYYY-MM-DDTHH:MM."""
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    branch_id: Optional[int] = Field(default=None, index=True)
    department_id: Optional[int] = None
    reference: str = Field(default="", index=True)
    date: str = Field(default="", index=True)
    service: str = ""
    status: str = ""
    created_time: str = ""
    ready_time: str = ""
    delivered_time: str = ""
    due_time: str = ""
    items: Optional[float] = None
    accurate: Optional[int] = None
    defect_type: str = ""
    rework: Optional[int] = None
    extra_json: str = ""
    dataset_id: Optional[int] = Field(default=None, index=True)
    source_row: Optional[int] = None
    created_at: _DTCOL = Field(default_factory=_now_naive)


class CompanyOpsEvent(SQLModel, table=True):
    """مرحلة من مراحل العملية (بداية/نهاية) — لسير العمليات والاختناقات."""
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    branch_id: Optional[int] = Field(default=None, index=True)
    department_id: Optional[int] = None
    reference: str = ""
    stage: str = ""
    start_time: str = Field(default="", index=True)
    end_time: str = ""
    extra_json: str = ""
    dataset_id: Optional[int] = Field(default=None, index=True)
    source_row: Optional[int] = None
    created_at: _DTCOL = Field(default_factory=_now_naive)


class CompanyOpsIssue(SQLModel, table=True):
    """مشكلة/عطل تشغيلي."""
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    branch_id: Optional[int] = Field(default=None, index=True)
    department_id: Optional[int] = None
    title: str = ""
    opened_time: str = ""
    resolved_time: str = ""
    severity: str = ""
    status: str = ""
    owner: str = ""
    root_cause: str = ""
    impact: str = ""
    sla_hours: Optional[float] = None
    extra_json: str = ""
    dataset_id: Optional[int] = Field(default=None, index=True)
    source_row: Optional[int] = None
    created_at: _DTCOL = Field(default_factory=_now_naive)


class CompanyExpense(SQLModel, table=True):
    """المصروفات من النظام المحاسبي — Phase 2.11 (اختياري؛ بدونه تُستخدم الحركات النقدية كأساس نقدي)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    branch_id: Optional[int] = Field(default=None, index=True)
    date: str = Field(default="", index=True)
    amount: Optional[float] = None
    category: str = ""
    description: str = ""
    vendor: str = ""
    extra_json: str = ""
    dataset_id: Optional[int] = Field(default=None, index=True)
    source_row: Optional[int] = None
    created_at: _DTCOL = Field(default_factory=_now_naive)


class CompanyFinSetting(SQLModel, table=True):
    """مدخلات مالية يدوية لا تأتي من الوحدات: بنود الميزانية، الإهلاك، الفوائد، الموازنة."""
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    settings_json: str = "{}"
    updated_by: str = ""
    updated_at: _DTCOL = Field(default_factory=_now_naive)


class CompanyTaxInvoice(SQLModel, table=True):
    """سجل الفواتير الضريبية/الإلكترونية — Phase 3.2 (تصدير من نظام الفوترة؛ لا تكامل مباشر)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    branch_id: Optional[int] = Field(default=None, index=True)
    invoice_number: str = Field(default="", index=True)
    issue_date: str = Field(default="", index=True)
    invoice_type: str = ""
    buyer_name: str = ""
    buyer_vat: str = ""
    taxable_amount: Optional[float] = None
    vat_amount: Optional[float] = None
    total_amount: Optional[float] = None
    currency: str = ""
    original_invoice: str = ""
    zatca_status: str = ""
    extra_json: str = ""
    dataset_id: Optional[int] = Field(default=None, index=True)
    source_row: Optional[int] = None
    created_at: _DTCOL = Field(default_factory=_now_naive)


class CompanyTaxSetting(SQLModel, table=True):
    """إعدادات الضرائب: الرقم الضريبي، دورية الإقرار، شمول الأسعار للضريبة، الإقرارات المقدمة، والإعدادات التنظيمية."""
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    settings_json: str = "{}"
    updated_by: str = ""
    updated_at: _DTCOL = Field(default_factory=_now_naive)


class CompanyLeakSetting(SQLModel, table=True):
    """حدود تحليل التسرب (3.1): أقصى نسبة خصم، نسبة مرتجعات مقبولة، أيام مخاطر الذمم، تكلفة التمويل."""
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    settings_json: str = "{}"
    updated_by: str = ""
    updated_at: _DTCOL = Field(default_factory=_now_naive)


class CompanyOpsSetting(SQLModel, table=True):
    """إعدادات العمليات اليدوية: الطاقة اليومية لكل فرع، SLA بالدقائق، المستهدفات."""
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    settings_json: str = "{}"
    updated_by: str = ""
    updated_at: _DTCOL = Field(default_factory=_now_naive)


class CompanyPredictionRun(SQLModel, table=True):
    """تشغيل تنبؤ (3.7): واحد لكل شهر بيانات جديد — يحفظ نسخة النموذج والثقة والمسببات. التوقعات الأصلية لا تُعدّل (لقياس الدقة)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    base_period: str = Field(default="", index=True)
    model_version: str = ""
    horizon: int = 6
    confidence: Optional[float] = None
    drivers_json: str = "[]"
    made_on: str = Field(default="", index=True)
    created_at: _DTCOL = Field(default_factory=_now_naive)


class CompanyPrediction(SQLModel, table=True):
    """قيمة متوقعة لمؤشر/نطاق/شهر ضمن تشغيل — مع النطاق والثقة. تُقارن لاحقاً بالفعلي (الدقة، قبل/بعد)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    run_id: int = Field(index=True)
    metric: str = Field(default="", index=True)
    scope: str = "company"
    period: str = Field(default="", index=True)
    h: int = 1
    value: Optional[float] = None
    lower: Optional[float] = None
    upper: Optional[float] = None
    confidence: Optional[float] = None
    made_on: str = ""


class CompanyPredictionSetting(SQLModel, table=True):
    """أهداف التنبؤ (3.7): الهدف السنوي للإيراد والربح — وإلا تُقرأ من الموازنة أو أهداف الفروع."""
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    settings_json: str = "{}"
    updated_by: str = ""
    updated_at: _DTCOL = Field(default_factory=_now_naive)


# ═══ Phase 3.8 — Goals & Results: طبقة الأهداف فقط (الفعلي يُقرأ من المحركات — لا جداول بيانات مكررة) ═══
class CompanyStrategy(SQLModel, table=True):
    """الإطار الاستراتيجي: الرؤية والرسالة والركائز + لقطة آخر مراجعة للأهداف (لمعرفة ما تغيّر)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    vision: str = ""
    mission: str = ""
    pillars_json: str = "[]"
    pillar_names_json: str = "{}"
    snapshot_json: str = "{}"
    snapshot_at: str = ""
    updated_by: str = ""
    updated_at: _DTCOL = Field(default_factory=_now_naive)


class CompanyGoal(SQLModel, table=True):
    """هدف مرتبط بمؤشر حقيقي — الحالة محسوبة (status = آخر حالة محسوبة؛ status_override للإلغاء/الإيقاف فقط)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    parent_goal_id: Optional[int] = Field(default=None, index=True)
    name: str = ""
    objective: str = ""
    pillar: str = ""
    level: str = "company"            # company | department | branch | manager | employee
    branch: str = ""
    department: str = ""
    owner: str = ""
    owner_user_id: Optional[int] = Field(default=None, index=True)
    metric: str = ""
    unit: str = ""
    baseline: Optional[float] = None
    target: Optional[float] = None
    start_date: str = ""
    end_date: str = ""
    status: str = ""                  # آخر حالة محسوبة (للتقارير) — ليست إدخالاً يدوياً
    status_override: str = ""         # "" | cancelled | paused
    source: str = "manual"            # manual | template | import | cascade_auto | cascade_manual
    distribution: str = ""            # manual | auto (لأهداف الفروع المستمدة)
    priority: str = "medium"
    depends_on: str = ""              # معرّفات أهداف يعتمد عليها مفصولة بفاصلة
    manual_value: Optional[float] = None
    manual_at: str = ""
    manual_by: str = ""
    manual_source: str = ""
    phase_link: str = ""              # 3.1 / 3.2 / 3.3 / 3.4 / 3.5 ...
    approval: str = ""                # "" | pending | approved | rejected
    archived: int = 0
    created_by: str = ""
    created_at: _DTCOL = Field(default_factory=_now_naive)
    updated_at: Optional[_DTCOL] = None


class CompanyKeyResult(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    goal_id: int = Field(index=True)
    name: str = ""
    metric: str = ""
    unit: str = ""
    baseline: Optional[float] = None
    target: Optional[float] = None
    actual: Optional[float] = None    # يدوي فقط لمؤشر لا تملك نبّاه مصدره
    actual_at: str = ""
    actual_by: str = ""
    progress: Optional[float] = None  # آخر قيمة محسوبة
    status: str = ""
    created_at: _DTCOL = Field(default_factory=_now_naive)


class CompanyGoalMilestone(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    goal_id: int = Field(index=True)
    title: str = ""
    due_date: str = ""
    target: Optional[float] = None
    actual: Optional[float] = None    # يدوي اختياري — وإلا يُحسب من المصدر
    owner: str = ""
    evidence_note: str = ""
    status: str = ""                  # آخر حالة محسوبة
    created_at: _DTCOL = Field(default_factory=_now_naive)


class CompanyGoalAction(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    goal_id: int = Field(index=True)
    kr_id: Optional[int] = None
    action: str = ""
    owner: str = ""
    owner_user_id: Optional[int] = Field(default=None, index=True)
    due_date: str = ""
    expected_impact: Optional[float] = None
    actual_impact: Optional[float] = None
    impact_type: str = "potential"    # potential | recovery | actual
    status: str = "open"              # open | in_progress | blocked | done | cancelled
    decision_id: Optional[int] = Field(default=None, index=True)
    baseline_json: str = ""           # لقطة الهدف وقت القرار (قبل/بعد)
    created_by: str = ""
    created_at: _DTCOL = Field(default_factory=_now_naive)
    updated_at: Optional[_DTCOL] = None
    done_at: str = ""


class CompanyGoalHistory(SQLModel, table=True):
    """سجل حوكمة: كل تغيير بقيمته القديمة والجديدة ومن غيّره ومتى ولماذا."""
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    goal_id: int = Field(index=True)
    field: str = ""
    old_value: str = ""
    new_value: str = ""
    changed_by: str = ""
    changed_at: _DTCOL = Field(default_factory=_now_naive)
    reason: str = ""


class CompanyGoalComment(SQLModel, table=True):
    """التعاون: تعليق/إشارة/دليل/طلب تحديث/تصعيد/اعتماد/رفض/تعيين مسؤول."""
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    goal_id: int = Field(index=True)
    kind: str = "comment"
    text: str = ""
    mentions: str = ""
    attachment_name: str = ""
    attachment_url: str = ""
    by_user_id: Optional[int] = None
    by_name: str = ""
    created_at: _DTCOL = Field(default_factory=_now_naive)


class CompanyUserScope(SQLModel, table=True):
    """ربط المستخدم بمسمّاه وفرعه وقسمه — يحدد نطاق رؤية الأهداف (رئيس تنفيذي/مدير قسم/مدير فرع/موظف)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    user_id: int = Field(index=True)
    title: str = ""                   # ceo | dept_manager | branch_manager | employee | ""
    branch: str = ""
    department: str = ""
    updated_by: str = ""
    updated_at: _DTCOL = Field(default_factory=_now_naive)


# ═══ Phase 3.9 — Follow-up on Decisions: طبقة متابعة فوق CompanyDecision/CompanyAction الموجودين (لا جداول أرقام مكررة) ═══
class CompanyDecisionMeta(SQLModel, table=True):
    """مسار الاعتماد والربط لقرار موجود (1:1 مع CompanyDecision). القرار القديم بلا هذا السجل يُقرأ كما هو."""
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    decision_id: int = Field(index=True)
    workflow: str = "draft"           # draft | pending_approval | under_review | approved | rejected | in_progress | completed | measured | cancelled
    priority: str = "medium"
    category: str = ""
    source: str = "manual"            # risk | risk_driver | sector_benchmark | prediction | goal | leakage | tax | purchases | recommendation | manual
    source_ref: str = ""
    department: str = ""
    branch: str = ""
    owner_user_id: Optional[int] = None
    created_by_id: Optional[int] = None
    submitted_at: str = ""
    approved_by: str = ""
    approved_at: str = ""
    started_at: str = ""
    completed_at: str = ""
    cancelled_at: str = ""
    rejection_reason: str = ""
    cost: Optional[float] = None
    delay_reason: str = ""
    delay_note: str = ""
    depends_on: str = ""
    goal_id: Optional[int] = None
    risk_key: str = ""
    risk_baseline: Optional[float] = None
    root_cause: str = ""
    root_cause_baseline: Optional[float] = None
    leakage_key: str = ""
    leakage_baseline: Optional[float] = None
    expected_type: str = "potential"
    sensitive: int = 0
    updated_at: Optional[_DTCOL] = None


class CompanyDecisionKPI(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    decision_id: int = Field(index=True)
    metric: str = ""
    branch: str = ""
    baseline: Optional[float] = None   # لقطة وقت القرار (للمؤشرات بلا تاريخ شهري)
    baseline_at: str = ""
    period: str = ""
    target: Optional[float] = None
    actual: Optional[float] = None     # يدوي فقط لمؤشر لا تملك نبّاه مصدره
    actual_at: str = ""
    side_effect: int = 0


class CompanyDecisionOutcome(SQLModel, table=True):
    """قياس معتمد للقرار: متوقع/فعلي/فرق/نتيجة — لقطة لا تتغير بعد الاعتماد."""
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    decision_id: int = Field(index=True)
    expected_impact: Optional[float] = None
    actual_impact: Optional[float] = None
    variance: Optional[float] = None
    result: str = ""
    method: str = ""
    actual_manual: Optional[float] = None
    notes: str = ""
    snapshot_json: str = "{}"
    measured_by: str = ""
    measured_at: str = ""


class CompanyDecisionEvidence(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    decision_id: int = Field(index=True)
    source: str = ""
    reference: str = ""
    period: str = ""
    note: str = ""
    url: str = ""
    added_by: str = ""
    created_at: _DTCOL = Field(default_factory=_now_naive)


class CompanyDecisionHistory(SQLModel, table=True):
    """سجل تدقيق القرار: الحدث، الفاعل، الوقت، القيمة القديمة والجديدة."""
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    decision_id: int = Field(index=True)
    event: str = ""
    actor: str = ""
    timestamp: _DTCOL = Field(default_factory=_now_naive)
    old_value: str = ""
    new_value: str = ""
    note: str = ""


# ═══ Phase 3.10 — Board Presentation: طبقة التقرير فقط (الأرقام تُقرأ من المحركات — النسخة المحفوظة لا تُعدّل) ═══
class CompanyBoardReport(SQLModel, table=True):
    """حزمة مجلس محفوظة: نسخة مرقّمة لكل فترة + لقطة كاملة وقت الإنشاء (Board Snapshot). تغيّر البيانات = نسخة جديدة."""
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    period: str = Field(default="", index=True)
    version: int = 1
    title: str = ""
    status: str = "final"             # final | presented
    meeting_date: str = ""
    generated_by: str = ""
    generated_by_id: Optional[int] = None
    generated_at: _DTCOL = Field(default_factory=_now_naive)
    presented_at: str = ""
    snapshot_json: str = "{}"         # لقطة المقارنة بالاجتماع التالي
    pack_json: str = "{}"             # الحزمة كاملة كما كانت وقت الإنشاء
    checksum: str = ""
    engine_version: str = ""
    exports: int = 0


class CompanyBoardReportSection(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    report_id: int = Field(index=True)
    section: str = ""
    sort_order: int = 0
    content_json: str = "{}"
    source: str = ""


class CompanyBoardReportMetric(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    report_id: int = Field(index=True)
    metric: str = ""
    label: str = ""
    value: Optional[float] = None
    source_module: str = ""
    confidence: str = ""
    period: str = ""


class CompanyBoardDecisionItem(SQLModel, table=True):
    """قرار مطلوب من المجلس في حزمة معيّنة + قرار المجلس فيه (يُنفّذ عبر مسار 3.9)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    report_id: int = Field(index=True)
    decision_id: Optional[int] = None
    goal_id: Optional[int] = None
    title: str = ""
    priority: str = ""
    recommendation: str = ""
    resolution: str = ""              # "" | approved | rejected | deferred
    resolved_by: str = ""
    resolved_at: str = ""
    note: str = ""


class BenchmarkDataset(SQLModel, table=True):
    """طبقة المعايير القطاعية (3.5): company_id فارغ = معيار المنصّة (من الإدارة)، وإلا معيار خاص بالشركة.
    كل معيار بمصدره ومنهجيته وفترته وعيّنته وثقته — لا معيار بلا مصدر."""
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: Optional[int] = Field(default=None, index=True)
    metric: str = Field(default="", index=True)
    sector: str = Field(default="", index=True)
    sub_sector: Optional[str] = None
    country: Optional[str] = "SA"
    region: Optional[str] = None
    city: Optional[str] = None
    size_segment: Optional[str] = "all"
    business_model: Optional[str] = None
    period: Optional[str] = None
    period_end: Optional[str] = None
    basis: Optional[str] = None
    value: Optional[float] = None
    p10: Optional[float] = None
    p25: Optional[float] = None
    p50: Optional[float] = None
    p75: Optional[float] = None
    p90: Optional[float] = None
    sample_size: Optional[int] = None
    source: Optional[str] = None
    source_url: Optional[str] = None
    methodology: Optional[str] = None
    confidence: Optional[str] = None
    published_on: Optional[str] = None
    version: Optional[str] = None
    is_active: int = 1
    created_by: str = ""
    created_at: _DTCOL = Field(default_factory=_now_naive)


class CompanyBenchmarkComparison(SQLModel, table=True):
    """مقارنة الشركة بالمعيار (لقطة شهرية لكل مؤشر) — أساس تاريخ الموقع، ومصدر معيار الأقران المجهّل إن وافقت الشركة (share_ok)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    period: str = Field(default="", index=True)
    assessed_on: str = ""
    metric: str = Field(default="", index=True)
    company_value: Optional[float] = None
    benchmark_value: Optional[float] = None
    benchmark_id: Optional[str] = None
    origin: Optional[str] = None
    gap: Optional[float] = None
    gap_signed: Optional[float] = None
    gap_pct: Optional[float] = None
    percentile: Optional[float] = None
    position: Optional[str] = None
    confidence: Optional[str] = None
    sector: str = Field(default="", index=True)
    size_segment: Optional[str] = None
    region: Optional[str] = None
    share_ok: int = 0
    updated_at: _DTCOL = Field(default_factory=_now_naive)


class CompanySectorSetting(SQLModel, table=True):
    """ملف القطاع للشركة (3.5): القطاع الفرعي، المنطقة، المدينة، نموذج العمل، الحجم، والموافقة على المشاركة المجهّلة."""
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    settings_json: str = "{}"
    updated_by: str = ""
    updated_at: _DTCOL = Field(default_factory=_now_naive)


class CompanyRiskSetting(SQLModel, table=True):
    """قواعد المخاطر للشركة (3.3): حدود المحركات، أوزان الفئات، القواعد المعطّلة — فوق افتراضي القطاع."""
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    settings_json: str = "{}"
    updated_by: str = ""
    updated_at: _DTCOL = Field(default_factory=_now_naive)


class CompanyRisk(SQLModel, table=True):
    """سجل المخاطر (Risk Register 3.3): عنصر لكل خطر مكتشف أو يدوي. يشير لمصدره (risk_key = محرك الخطر) ولا ينسخ البيانات.
    المسار: detected → reviewed → decision → approved → action → measurement → reassessment (+ monitoring / closed)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    risk_key: str = Field(default="", index=True)
    title: str = ""
    category: str = ""
    level: str = ""
    status: str = "detected"
    owner: str = ""
    due_date: str = ""
    mitigation: str = ""
    decision_id: Optional[int] = Field(default=None, index=True)
    baseline_score: Optional[float] = None
    baseline_value: Optional[float] = None
    baseline_date: str = ""
    history_json: str = "[]"
    created_by: str = ""
    created_on: str = ""
    updated_at: _DTCOL = Field(default_factory=_now_naive)


class CompanyRiskAssessment(SQLModel, table=True):
    """لقطة تقييم يومية (Risk Assessment 3.3): المؤشر والفئات ومستوى كل محرك — أساس الخط الزمني والاتجاهات."""
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    assessed_on: str = Field(default="", index=True)
    index_score: Optional[float] = None
    confidence_pct: Optional[float] = None
    categories_json: str = "{}"
    driver_levels_json: str = "{}"
    driver_scores_json: str = "{}"
    engine_version: str = ""
    updated_at: _DTCOL = Field(default_factory=_now_naive)


class CompanyCashSetting(SQLModel, table=True):
    """مدخلات يدوية للسيولة لا توجد في الملفات: الرصيد الافتتاحي، الالتزامات المتداولة، الحد الأدنى، النقد المقيد."""
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    opening_balance: Optional[float] = None
    current_liabilities: Optional[float] = None
    min_cash: Optional[float] = None
    restricted_cash: Optional[float] = None
    updated_by: str = ""
    updated_at: _DTCOL = Field(default_factory=_now_naive)


class CompanyInventoryParam(SQLModel, table=True):
    """معاملات إعادة الطلب لكل صنف (يدوية أو من ملف) — Phase 2.6. branch_id فارغ = لكل الفروع."""
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    product_sku: str = Field(default="", index=True)
    branch_id: Optional[int] = Field(default=None, index=True)
    lead_time_days: Optional[float] = None
    safety_stock: Optional[float] = None
    min_order_qty: Optional[float] = None
    reorder_point: Optional[float] = None
    updated_by: str = ""
    updated_at: _DTCOL = Field(default_factory=_now_naive)


class CompanyCashMovement(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    branch_id: Optional[int] = Field(default=None, index=True)
    date: str = Field(default="", index=True)
    period: str = Field(default="", index=True)
    movement_type: str = ""
    category: str = ""
    amount: Optional[float] = None
    direction: str = ""                        # in | out
    reference: str = ""
    source: str = ""
    account: str = ""                          # Phase 2.8 — الحساب/البنك
    counterparty: str = ""                     # الطرف المقابل (عميل/مورد/جهة)
    balance: Optional[float] = None            # الرصيد المُبلّغ بعد الحركة (كشف البنك)
    extra_json: str = ""
    dataset_id: Optional[int] = Field(default=None, index=True)
    source_row: Optional[int] = None
    created_at: _DTCOL = Field(default_factory=_now_naive)


class CompanyMemory(SQLModel, table=True):
    """ذاكرة الشركة المؤسسية: كل تحليل وقرار وسؤال ورفع بيانات يُسجَّل هنا للأبد."""
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    kind: str = Field(index=True)   # analysis / question / upload / goals / decision
    title: str = ""
    content: str = ""               # النص الكامل (تحليل/إجابة...)
    created_at: _DTCOL = Field(default_factory=_now_naive)


class AuditLog(SQLModel, table=True):
    """سجل التدقيق: مَن فعل ماذا ومتى — لبناء الثقة والمساءلة (لطلبات المدققين والمستثمرين)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    user_id: int = Field(index=True)          # مَن نفّذ الإجراء
    user_name: str = ""                        # اسم المستخدم وقت الإجراء
    action: str = Field(index=True)            # نوع الإجراء: login/create/update/delete/upload/analyze/decision/export
    target: str = ""                           # ما تأثّر: "بيانات فرع الرياض" / "قرار #12" / ...
    details: str = ""                          # تفاصيل موجزة
    ip: str = ""                               # عنوان IP (للأمان)
    created_at: _DTCOL = Field(default_factory=datetime.now, index=True)


class CompanyModuleEntry(SQLModel, table=True):
    """إدخالات الوحدات الموسّعة (مالية/مبيعات/عملاء/...). تُحفظ مرنة كـ JSON."""
    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(index=True)
    branch_id: Optional[int] = Field(default=None, index=True)  # None = على مستوى الشركة
    module: str = Field(index=True)                              # finance / sales / customers / ...
    period: str = ""                                             # YYYY-MM
    data: str = ""                                               # JSON
    created_at: _DTCOL = Field(default_factory=_now_naive)


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
            'ALTER TABLE company ADD COLUMN IF NOT EXISTS employees INTEGER DEFAULT 0',
            'ALTER TABLE company ADD COLUMN IF NOT EXISTS annual_revenue DOUBLE PRECISION DEFAULT 0',
            'ALTER TABLE company ADD COLUMN IF NOT EXISTS target_margin DOUBLE PRECISION DEFAULT 0',
            'ALTER TABLE company ADD COLUMN IF NOT EXISTS fiscal_year_start INTEGER DEFAULT 1',
            'ALTER TABLE company ADD COLUMN IF NOT EXISTS currency VARCHAR DEFAULT \'SAR\'',
            'ALTER TABLE company ADD COLUMN IF NOT EXISTS country VARCHAR DEFAULT \'SA\'',
            'ALTER TABLE company ADD COLUMN IF NOT EXISTS top_priority VARCHAR DEFAULT \'profit\'',
            'ALTER TABLE company ADD COLUMN IF NOT EXISTS goals_json VARCHAR DEFAULT \'{}\'',
            'ALTER TABLE company ADD COLUMN IF NOT EXISTS alerts_json VARCHAR DEFAULT \'{}\'',
            'ALTER TABLE companyentry ADD COLUMN IF NOT EXISTS deposited DOUBLE PRECISION DEFAULT 0',
            'ALTER TABLE companyentry ADD COLUMN IF NOT EXISTS extra_data VARCHAR DEFAULT \'\'',
            'ALTER TABLE companybranch ADD COLUMN IF NOT EXISTS business_unit VARCHAR DEFAULT \'\'',
            'ALTER TABLE companybranch ADD COLUMN IF NOT EXISTS department VARCHAR DEFAULT \'\'',
            # ===== أعمدة جدول القرارات (companydecision) =====
            'ALTER TABLE companydecision ADD COLUMN IF NOT EXISTS expected_impact VARCHAR DEFAULT \'\'',
            'ALTER TABLE companydecision ADD COLUMN IF NOT EXISTS linked_to VARCHAR DEFAULT \'\'',
            'ALTER TABLE companydecision ADD COLUMN IF NOT EXISTS approver VARCHAR DEFAULT \'\'',
            'ALTER TABLE companydecision ADD COLUMN IF NOT EXISTS reviewer VARCHAR DEFAULT \'\'',
            'ALTER TABLE companydecision ADD COLUMN IF NOT EXISTS baseline_sales DOUBLE PRECISION DEFAULT 0',
            'ALTER TABLE companydecision ADD COLUMN IF NOT EXISTS result_sales DOUBLE PRECISION DEFAULT 0',
            'ALTER TABLE companydecision ADD COLUMN IF NOT EXISTS result_note VARCHAR DEFAULT \'\'',
            'ALTER TABLE companydecision ADD COLUMN IF NOT EXISTS kpi VARCHAR DEFAULT \'\'',
            'ALTER TABLE companydecision ADD COLUMN IF NOT EXISTS due_date VARCHAR DEFAULT \'\'',
            'ALTER TABLE companydecision ADD COLUMN IF NOT EXISTS owner VARCHAR DEFAULT \'\'',
            'ALTER TABLE companydecision ADD COLUMN IF NOT EXISTS closed_at TIMESTAMP',
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
                except Exception as _dbe:
                    _logger.error(f"db op failed (line ~626): {type(_dbe).__name__}: {str(_dbe)[:150]}")
        except Exception as _dbe:
            _logger.error(f"db op failed (line ~628): {type(_dbe).__name__}: {str(_dbe)[:150]}")

    for sql in migrations:
        try:
            with engine.connect() as conn:
                conn.execute(text(sql))
                conn.commit()
                print(f"✅ Migration OK: {sql[:60]}...")
        except Exception as e:
            print(f"⚠️ Migration skipped: {e}")


def auto_sync_columns():
    """مزامنة ذاتية شاملة: تفحص كل موديل مقابل جدوله وتضيف أي عمود ناقص تلقائياً.
    تضمن ألا يتكرر خطأ 'column does not exist' مع أي عمود جديد مستقبلاً."""
    from sqlalchemy import text  # كان مفقوداً: بدونه تفشل كل الأوامر بصمت
    # خريطة: اسم الجدول → قائمة (اسم العمود, نوع SQL) من الموديل
    TABLE_COLUMNS = {
        "companydecision": [
            ("company_id", "INTEGER"), ("title", "VARCHAR DEFAULT ''"),
            ("detail", "VARCHAR DEFAULT ''"), ("owner", "VARCHAR DEFAULT ''"),
            ("due_date", "VARCHAR DEFAULT ''"), ("kpi", "VARCHAR DEFAULT ''"),
            ("status", "VARCHAR DEFAULT 'open'"), ("baseline_sales", "DOUBLE PRECISION DEFAULT 0"),
            ("result_sales", "DOUBLE PRECISION DEFAULT 0"), ("result_note", "VARCHAR DEFAULT ''"),
            ("expected_impact", "VARCHAR DEFAULT ''"), ("linked_to", "VARCHAR DEFAULT ''"),
            ("approver", "VARCHAR DEFAULT ''"), ("reviewer", "VARCHAR DEFAULT ''"),
            ("rationale", "VARCHAR DEFAULT ''"), ("created_at", "TIMESTAMP DEFAULT NOW()"),
            ("closed_at", "TIMESTAMP"),
            ("branch_id", "INTEGER"), ("metric_id", "VARCHAR DEFAULT ''"),
            ("baseline_value", "DOUBLE PRECISION"), ("expected_impact_value", "DOUBLE PRECISION"),
            ("actual_value", "DOUBLE PRECISION"), ("actual_impact_value", "DOUBLE PRECISION"),
            ("impact_status", "VARCHAR DEFAULT ''"), ("source_signal", "VARCHAR DEFAULT ''"),
            ("updated_at", "TIMESTAMP"),
            ("problem_type", "VARCHAR DEFAULT ''"), ("decision_type", "VARCHAR DEFAULT ''"),
            ("measurement_period", "VARCHAR DEFAULT ''"), ("outcome_status", "VARCHAR DEFAULT ''"),
            ("outcome_notes", "VARCHAR DEFAULT ''"), ("created_by", "VARCHAR DEFAULT ''"),
            ("data_source", "VARCHAR DEFAULT ''"),
        ],
        "companybranch": [
            ("business_unit", "VARCHAR DEFAULT ''"), ("department", "VARCHAR DEFAULT ''"),
            ("area", "VARCHAR DEFAULT ''"), ("target_sales", "DOUBLE PRECISION DEFAULT 0"),
            ("target_customers", "INTEGER DEFAULT 0"),
        ],
        "companymoduleentry": [
            ("branch_id", "INTEGER"), ("data", "VARCHAR DEFAULT '{}'"),
        ],
        "companysale": [
            ("promotion", "VARCHAR DEFAULT ''"), ("customer_name", "VARCHAR DEFAULT ''"),
            ("extra_json", "TEXT DEFAULT ''"),
        ],
        "companysupplier": [
            ("supplier_code", "VARCHAR DEFAULT ''"), ("contact_person", "VARCHAR DEFAULT ''"),
            ("email", "VARCHAR DEFAULT ''"), ("phone", "VARCHAR DEFAULT ''"), ("city", "VARCHAR DEFAULT ''"),
            ("tax_number", "VARCHAR DEFAULT ''"), ("extra_json", "TEXT DEFAULT ''"),
        ],
        "companyemployee": [
            ("email", "VARCHAR DEFAULT ''"), ("phone", "VARCHAR DEFAULT ''"), ("extra_json", "TEXT DEFAULT ''"),
            ("employment_type", "VARCHAR DEFAULT ''"), ("manager", "VARCHAR DEFAULT ''"),
            ("termination_type", "VARCHAR DEFAULT ''"), ("basic_salary", "DOUBLE PRECISION"),
            ("allowances", "DOUBLE PRECISION"), ("benefits", "DOUBLE PRECISION"),
            ("performance_rating", "DOUBLE PRECISION"), ("last_promotion_date", "VARCHAR DEFAULT ''"),
            ("training_hours", "DOUBLE PRECISION"), ("absence_days", "DOUBLE PRECISION"),
            ("overtime_hours", "DOUBLE PRECISION"), ("critical_role", "INTEGER"), ("successors", "DOUBLE PRECISION"),
        ],
        "companyproduct": [("extra_json", "TEXT DEFAULT ''")],
        "companydepartment": [("extra_json", "TEXT DEFAULT ''")],
        "companypurchase": [("extra_json", "TEXT DEFAULT ''"), ("expected_date", "VARCHAR DEFAULT ''"),
                            ("received_date", "VARCHAR DEFAULT ''"), ("received_qty", "DOUBLE PRECISION"),
                            ("rejected_qty", "DOUBLE PRECISION")],
        "companyinventory": [("extra_json", "TEXT DEFAULT ''")],
        "companycashmovement": [("extra_json", "TEXT DEFAULT ''"), ("account", "VARCHAR DEFAULT ''"),
                                ("counterparty", "VARCHAR DEFAULT ''"), ("balance", "DOUBLE PRECISION")],
        '"user"': [
            ("business_name", "VARCHAR DEFAULT ''"), ("phone", "VARCHAR DEFAULT ''"),
            ("plan", "VARCHAR DEFAULT ''"), ("is_active", "INTEGER DEFAULT 0"),
            ("trial_used", "INTEGER DEFAULT 0"), ("subscription_start", "TIMESTAMP"),
            ("subscription_end", "TIMESTAMP"), ("company_id", "INTEGER"),
            ("company_role", "VARCHAR DEFAULT ''"), ("created_at", "TIMESTAMP DEFAULT NOW()"),
        ],
        "auditlog": [
            ("user_name", "VARCHAR DEFAULT ''"), ("action", "VARCHAR DEFAULT ''"),
            ("target", "VARCHAR DEFAULT ''"), ("details", "VARCHAR DEFAULT ''"),
            ("ip", "VARCHAR DEFAULT ''"),
        ],
    }
    report = {"added_or_ok": 0, "failed": []}
    for table, cols in TABLE_COLUMNS.items():
        for col_name, col_type in cols:
            sql = f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col_name} {col_type}'
            try:
                with engine.connect() as conn:
                    conn.execute(text(sql))
                    conn.commit()
                report["added_or_ok"] += 1
            except Exception as e:
                # لا نكسر الإقلاع، لكن لا نُخفي السبب أبداً
                report["failed"].append({"table": table, "column": col_name,
                                         "error": f"{type(e).__name__}: {str(e)[:200]}"})
    if report["failed"]:
        _logger.error(f"auto_sync_columns: فشل {len(report['failed'])} عمود — "
                      + ", ".join(f"{f['table']}.{f['column']}" for f in report["failed"][:10]))
    return report


run_migrations()
auto_sync_columns()


# ===== أدوات الأمان: كلمات المرور والرموز =====
# ===== حماية من تخمين كلمات المرور (Brute Force) =====
_login_attempts = {}   # {ip_or_email: [timestamps]}
_LOGIN_WINDOW = 900    # 15 دقيقة
_LOGIN_MAX = 8         # 8 محاولات فاشلة كحد أقصى


def _throttle_key(request, email: str) -> str:
    ip = ""
    try:
        ip = request.client.host if request and request.client else ""
    except Exception:
        pass
    return f"{ip}|{(email or '').lower()}"


def check_login_throttle(request, email: str):
    """يمنع أكثر من 8 محاولات فاشلة خلال 15 دقيقة لنفس (IP + بريد)."""
    key = _throttle_key(request, email)
    now = datetime.now().timestamp()
    hits = [t for t in _login_attempts.get(key, []) if now - t < _LOGIN_WINDOW]
    _login_attempts[key] = hits
    if len(hits) >= _LOGIN_MAX:
        wait = int((_LOGIN_WINDOW - (now - hits[0])) / 60) + 1
        raise HTTPException(429, f"محاولات كثيرة. حاول بعد {wait} دقيقة.")
    # تنظيف دوري بسيط لمنع تضخّم الذاكرة
    if len(_login_attempts) > 5000:
        for k in list(_login_attempts.keys()):
            if not [t for t in _login_attempts[k] if now - t < _LOGIN_WINDOW]:
                _login_attempts.pop(k, None)


def record_failed_login(request, email: str):
    key = _throttle_key(request, email)
    _login_attempts.setdefault(key, []).append(datetime.now().timestamp())


def clear_login_attempts(request, email: str):
    _login_attempts.pop(_throttle_key(request, email), None)


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


# ═══════════════════════════════════════════════════════════
#  طبقة عزل الشركات (Tenant Isolation Layer) — Phase 1.2
#  توحيد منطق العزل الموجود في helpers مركزية.
#  لا تغيّر السلوك — تجمع الأنماط المكرّرة في مكان واحد آمن.
#  القاعدة الذهبية: كل استعلام يجب أن يُقيَّد بشركة المستخدم.
# ═══════════════════════════════════════════════════════════
def get_active_company(s, user: User):
    """يُرجع شركة المستخدم النشطة بعد التحقق الكامل.
    يرفع الاستثناء المناسب إن لم توجد شركة أو لم تكن نشطة.
    مدخل موحّد لكل endpoint يحتاج شركة المستخدم."""
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    company = s.get(Company, user.company_id)
    if not company:
        raise HTTPException(403, "غير مصرّح")
    if company.is_active != 1:
        raise HTTPException(402, "شركتك قيد التفعيل — فعّلها من لوحة الإدارة")
    return company


def require_company_owner(s, user: User):
    """يُرجع الشركة بعد التأكد أن المستخدم هو مالكها.
    للعمليات الحسّاسة (حذف، إعدادات، إدارة الفريق)."""
    company = get_active_company(s, user)
    if company.owner_id != user.id:
        raise HTTPException(403, "غير مصرّح — هذه العملية للمالك فقط")
    return company


def get_owned_branch(s, user: User, branch_id: int):
    """يُرجع فرعاً بعد التأكد أنه يخصّ شركة المستخدم.
    يمنع تسرّب البيانات عبر تمرير branch_id لشركة أخرى (IDOR)."""
    if not branch_id:
        raise HTTPException(400, "معرّف الفرع مطلوب")
    branch = s.get(CompanyBranch, int(branch_id))
    if not branch:
        raise HTTPException(404, "الفرع غير موجود")
    # الحماية الحرجة: الفرع يجب أن يخصّ شركة المستخدم
    if branch.company_id != user.company_id:
        raise HTTPException(403, "غير مصرّح — هذا الفرع لا يخص شركتك")
    return branch


def scoped_branches(s, company_id: int):
    """يُرجع فروع الشركة النشطة فقط — استعلام مُقيَّد بالشركة."""
    return s.exec(
        select(CompanyBranch).where(
            CompanyBranch.company_id == company_id,
            CompanyBranch.is_active == 1,
        )
    ).all()


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

@app.get("/i18n.js")
def serve_i18n():
    # ملف نظام اللغتين — يُخدَم بنوع MIME الصحيح ليعمل زر تبديل اللغة
    import os as _os
    if _os.path.exists("i18n.js"):
        return FileResponse("i18n.js", media_type="application/javascript")
    # لو الملف غير مرفوع بعد: نرد سكربت فارغ آمن بدل كسر السيرفر
    from fastapi.responses import Response as _Resp
    return _Resp(content="/* i18n.js not uploaded yet */", media_type="application/javascript")


@app.get("/nabbah-sidebar.js")
def serve_sidebar():
    # الشريط الجانبي الموحّد (١٢ مجموعة) — يُخدَم بنوع MIME الصحيح
    import os as _os
    if _os.path.exists("nabbah-sidebar.js"):
        return FileResponse("nabbah-sidebar.js", media_type="application/javascript")
    from fastapi.responses import Response as _Resp
    return _Resp(content="/* nabbah-sidebar.js not uploaded yet */", media_type="application/javascript")


@app.get("/company-executive-report.html")
def page_exec_report():
    return FileResponse("company-executive-report.html")


@app.get("/company-financial-overview.html")
def page_fin_overview():
    return FileResponse("company-financial-overview.html")


@app.get("/company-ops-analytics.html")
def page_ops_analytics():
    return FileResponse("company-ops-analytics.html")


@app.get("/company-performance-prediction.html")
def page_performance_prediction():
    return FileResponse("company-performance-prediction.html")


@app.get("/company-goals-intelligence.html")
def page_goals_intelligence():
    return FileResponse("company-goals-intelligence.html")


@app.get("/company-decisions-intelligence.html")
def page_decisions_intelligence():
    return FileResponse("company-decisions-intelligence.html")


@app.get("/company-board-intelligence.html")
def page_board_intelligence():
    return FileResponse("company-board-intelligence.html")


@app.get("/company-sector-benchmark.html")
def page_sector_benchmark():
    return FileResponse("company-sector-benchmark.html")


@app.get("/company-risk-drivers.html")
def page_risk_drivers():
    return FileResponse("company-risk-drivers.html")


@app.get("/company-risk-intelligence.html")
def page_risk_intelligence():
    return FileResponse("company-risk-intelligence.html")


@app.get("/company-tax-intelligence.html")
def page_tax_intelligence():
    return FileResponse("company-tax-intelligence.html")


@app.get("/company-leakage-intelligence.html")
def page_leakage_intelligence():
    return FileResponse("company-leakage-intelligence.html")


@app.get("/company-financial-intelligence.html")
def page_financial_intelligence():
    return FileResponse("company-financial-intelligence.html")


@app.get("/company-operations-intelligence.html")
def page_operations_intelligence():
    return FileResponse("company-operations-intelligence.html")


@app.get("/company-hr-intelligence.html")
def page_hr_intelligence():
    return FileResponse("company-hr-intelligence.html")


@app.get("/company-cashflow-intelligence.html")
def page_cashflow_intelligence():
    return FileResponse("company-cashflow-intelligence.html")


@app.get("/company-purchases-intelligence.html")
def page_purchases_intelligence():
    return FileResponse("company-purchases-intelligence.html")


@app.get("/company-inventory-intelligence.html")
def page_inventory_intelligence():
    return FileResponse("company-inventory-intelligence.html")


@app.get("/company-inventory-analytics.html")
def page_inv_analytics():
    return FileResponse("company-inventory-analytics.html")


@app.get("/company-procurement-analytics.html")
def page_proc_analytics():
    return FileResponse("company-procurement-analytics.html")


@app.get("/company-sales-analytics.html")
def page_sales_analytics():
    return FileResponse("company-sales-analytics.html")


@app.get("/company-readiness.html")
def page_readiness():
    return FileResponse("company-readiness.html")


@app.get("/company-sales-intelligence.html")
def page_sales_intel():
    return FileResponse("company-sales-intelligence.html")


@app.get("/company-data-center.html")
def page_data_center():
    return FileResponse("company-data-center.html")


@app.get("/company-actions.html")
def page_actions():
    return FileResponse("company-actions.html")


@app.get("/company-scenarios.html")
def page_scenarios():
    return FileResponse("company-scenarios.html")


@app.get("/company-whatif.html")
def page_whatif():
    return FileResponse("company-whatif.html")


@app.get("/company-benchmark.html")
def page_benchmark():
    return FileResponse("company-benchmark.html")


@app.get("/company-customer-health.html")
def page_cust_health():
    return FileResponse("company-customer-health.html")


@app.get("/company-treasury.html")
def page_treasury():
    return FileResponse("company-treasury.html")


@app.get("/company-reports.html")
def page_reports():
    return FileResponse("company-reports.html")


@app.get("/company-check.html")
def page_check():
    return FileResponse("company-check.html")


@app.get("/nabbah-decision.js")
def serve_decision():
    # رحلة القرار (حوّل لقرار) — يُخدَم بنوع MIME الصحيح
    import os as _os
    if _os.path.exists("nabbah-decision.js"):
        return FileResponse("nabbah-decision.js", media_type="application/javascript")
    from fastapi.responses import Response as _Resp
    return _Resp(content="/* nabbah-decision.js not uploaded yet */", media_type="application/javascript")


@app.get("/nabbah-exec-intel.js")
def serve_exec_intel():
    import os as _os
    if _os.path.exists("nabbah-exec-intel.js"):
        return FileResponse("nabbah-exec-intel.js", media_type="application/javascript")
    raise HTTPException(404, "not found")


@app.get("/nabbah-ask.js")
def serve_ask():
    # اسأل نبّاه (البحث الذكي) — يُخدَم بنوع MIME الصحيح
    import os as _os
    if _os.path.exists("nabbah-ask.js"):
        return FileResponse("nabbah-ask.js", media_type="application/javascript")
    from fastapi.responses import Response as _Resp
    return _Resp(content="/* nabbah-ask.js not uploaded yet */", media_type="application/javascript")


@app.get("/nabbah-filters.js")
def serve_filters():
    # شريط الفلاتر الموحّد — يُخدَم بنوع MIME الصحيح
    import os as _os
    if _os.path.exists("nabbah-filters.js"):
        return FileResponse("nabbah-filters.js", media_type="application/javascript")
    from fastapi.responses import Response as _Resp
    return _Resp(content="/* nabbah-filters.js not uploaded yet */", media_type="application/javascript")


@app.get("/nabbah-design.css")
def serve_design():
    # نظام التصميم الموحّد (الألوان والحالات) — يُخدَم بنوع MIME الصحيح
    import os as _os
    if _os.path.exists("nabbah-design.css"):
        return FileResponse("nabbah-design.css", media_type="text/css")
    from fastapi.responses import Response as _Resp
    return _Resp(content="/* nabbah-design.css not uploaded yet */", media_type="text/css")

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
def login(data: LoginData, request: Request):
    email = data.email.strip().lower()
    check_login_throttle(request, email)
    with Session(engine) as s:
        user = s.exec(select(User).where(User.email == email)).first()
        if not user or not verify_password(data.password, user.password_hash):
            record_failed_login(request, email)
            raise HTTPException(status_code=401, detail="البريد أو كلمة المرور غير صحيحة")
        clear_login_attempts(request, email)
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

@app.get("/admin/errors")
def admin_recent_errors(_: bool = Depends(verify_admin)):
    """آخر الأعطال غير المتوقّعة بتفاصيلها — للمسؤول فقط (X-Admin-Key)."""
    return {"count": len(_RECENT_ERRORS), "errors": list(_RECENT_ERRORS)}


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
    lines = [f"\n📊 **مقارنة بمعايير قطاع {bm['name']}:**",
             "_(هذه المعايير تقديرية إرشادية مبنية على متوسطات القطاع العامة — وليست أرقاماً رسمية. ستصبح مقارنات فعلية عند توفّر بيانات كافية.)_"]

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
    else: quality_note = "نتيجة تحتاج إلى تحقق — بعض البيانات غير مكتملة، لذا الدقة محدودة (هذا ليس خللاً في شركتك). أكملها لتحليل أدق."

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


_LEGACY_ADAPTER = None


def _legacy_adapter():
    """Loads the central-engine adapter once. Returns None if phase21/phase22 are absent."""
    global _LEGACY_ADAPTER
    if _LEGACY_ADAPTER is None:
        try:
            import sys as _sys, os as _os
            _p22 = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "phase22")
            if _p22 not in _sys.path:
                _sys.path.insert(0, _p22)
            try:
                import legacy_adapters as _la
            except ModuleNotFoundError:
                import nabbah_engines  # noqa: F401
                import legacy_adapters as _la
            _LEGACY_ADAPTER = _la
        except Exception as _e:
            _logger.error(f"legacy adapter unavailable: {type(_e).__name__}: {str(_e)[:120]}")
            _LEGACY_ADAPTER = False
    return _LEGACY_ADAPTER or None


def compute_company_metrics(sales, invoices, customers, repeat_customers, expenses, prev_sales=None):
    """يحسب المؤشرات المالية ومؤشر أداء الفرع من المدخلات الخام.
    Phase 2.2 (Option B): financial fields come from the central engine in
    LEGACY_COMPATIBLE mode (identical output). The inline block below is the
    fallback used only if the engine files are unavailable."""
    _ad = _legacy_adapter()
    _m = None
    if _ad:
        try:
            _m = _ad.central_company_metrics(sales, invoices, customers, repeat_customers, expenses, prev_sales)
        except Exception:
            _m = None
    if _m is not None:
        profit, margin, avg_invoice = _m["profit"], _m["margin"], _m["avg_invoice"]
        repeat_rate, growth, has_prev = _m["repeat_rate"], _m["growth"], _m["has_prev"]
    else:
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


EXEC_FRAMEWORK = """

════════ إطار الرئيس التنفيذي AI في نبّاه (إلزامي) ════════
مهمتك ليست وصف الأرقام — بل اكتشاف الأموال المهدرة والمخاطر والفرص، وإصدار قرارات تنفيذية قابلة للتنفيذ.

★★★ القاعدة الذهبية: القرار أولاً، ثم الأدلة ★★★
ابدأ دائماً بالقرار والأثر المالي في الأعلى — بحيث يقرأه المدير في ثوانٍ ويتصرّف. ثم ضع الأدلة والبيانات الداعمة أسفله لمن أراد التعمّق. لا تُغرق المدير بالأرقام أولاً؛ الرقم يخدم القرار لا العكس.

★★★ صيغة الأثر المالي الموحّدة (إلزامية لكل قرار) ★★★
اكتب الأثر بهذا الشكل الواضح دائماً:
  • القرار: [جملة فعل واضحة]
  • الأثر المتوقع: +XX,XXX ريال [شهرياً/سنوياً]  (أو: توفير XX,XXX ريال)
  • الأولوية: عالية / متوسطة / منخفضة
  • درجة الثقة: XX%
  • سرعة التنفيذ: [فوري / أسبوع / شهر]
لا تكتب أثراً بلا رقم بالريال. لو تعذّر تقديره، قل صراحة: "الأثر غير قابل للتقدير — ينقصنا [البيان]".

قواعد صارمة:
1) استخدم فقط الأرقام الواردة في المعطيات. لا تكرر أرقام الجداول إلا لإثبات قرار. أي تقدير يُوسم بكلمة "تقدير" مع منطق حسابه (مثال: 480,000 × 5% = 24,000 ريال).

2) ★ عند نقص البيانات — طمئن ولا تُخوّف ★
   إذا كانت البيانات غير مكتملة، لا تقل ولا تُلمّح أبداً أن في الشركة "خللاً" أو "مشكلة". الصياغة الإلزامية:
   "نتيجة تحتاج إلى تحقق — هذه ليست مشكلة في شركتك، بل بعض البيانات غير مكتملة، لذا الدقة محدودة. لتحليل أدق أكمل: [عدّد البيانات المطلوبة بدقة]."
   افصل دائماً: نقص البيانات ≠ خلل في الأداء. لا تخلط بينهما إطلاقاً.

3) ممنوع الكلام العام منعاً باتاً: "يجب تحسين الأداء" ❌ — الصيغة الصحيحة: "خفض الخصومات 15% يزيد الربح المتوقع بـ245,000 ريال" ✅.

4) كل استنتاج يجب أن يجيب على السلسلة العشرية: ماذا يحدث؟ لماذا يحدث؟ (اسأل "لماذا" حتى 5 مرات وصولاً للجذر) ما أثره المالي؟ ما أفضل قرار؟ كم سيحقق القرار بالريال؟ ما أولويته؟ ما سرعة تنفيذه؟ ما خطر عدم التنفيذ؟ كيف نقيس نجاحه بعد 30 يوماً؟

5) رتّب القرارات دائماً حسب الأثر المالي الأكبر أولاً، ولكل قرار الصيغة الموحّدة أعلاه.

هيكل التحليل الشامل (طبّقه عند طلب تحليل كامل للشركة أو الفرع):
① القرارات التنفيذية أولاً: أهم 3 قرارات بصيغة الأثر الموحّدة — يقرأها المدير في أقل من دقيقة ويتصرّف.
② كشف هدر الإيرادات: افحص (الهدر، الخصومات، المرتجعات، انخفاض الهامش، التكاليف المرتفعة، ضعف التحصيل، ضعف التسعير) واحسب أثر كل بند بالريال.
③ تحليل السبب الجذري: أسلوب Why×5 (كأدلة داعمة للقرارات أعلاه).
④ الفرص السريعة (خلال أسبوع) ⑤ الفرص المتوسطة (30-90 يوم) ⑥ الفرص الاستراتيجية (6-24 شهر).
⑦ التوقعات: ثلاثة سيناريوهات — لو استمر الوضع / لو تحسّن / لو ساء.
⑧ التقدير المالي الإجمالي: الوفر المتوقع + زيادة الربح + تحسن التدفق النقدي.
⑨ مؤشرات المراقبة: أهم KPIs لمتابعة أثر القرارات.
⑩ البيانات الناقصة (إن وجدت): بصياغة القاعدة 2 المطمئنة — قائمة بما ينقص وأثره على الدقة، دون أي إيحاء بوجود خلل.
أما الأسئلة القصيرة والتحليلات الجزئية: أجب مباشرة وباختصار — لكن ابدأ بالقرار دائماً.

الاختبار النهائي: لو كنت أنا مالك الشركة، هل سأدفع 5,000 ريال مقابل هذا التحليل؟ هل وصلت للقرار في أول 10 ثوانٍ؟ إذا لا — أعد كتابته."""


EXEC_FRAMEWORK_EN = """

════════ Nabbah Executive AI Framework (MANDATORY) ════════
Your mission isn't to describe numbers — it's to detect wasted money, risks, and opportunities, and issue executable decisions.

★★★ THE GOLDEN RULE: Decision First, Then Evidence ★★★
Always start with the decision and its financial impact at the top — so the manager reads it in seconds and can act. Then place supporting evidence and data below for those who want to dig deeper. Don't drown the manager in numbers first; numbers serve the decision, not the other way around.

★★★ UNIFIED FINANCIAL IMPACT FORMAT (mandatory for every decision) ★★★
Write the impact in this clear format always:
  • Decision: [clear action verb]
  • Expected impact: +XX,XXX SAR [monthly/annually]  (or: Savings of XX,XXX SAR)
  • Priority: High / Medium / Low
  • Confidence: XX%
  • Time to execute: [immediate / one week / one month]
Never write an impact without a SAR figure. If unable to estimate, state clearly: "Impact not estimable — we're missing [item]."

Strict rules:
1) Use only the numbers provided in the data. Don't repeat table numbers except to prove a decision. Any estimate is tagged "estimate" with its calculation logic (e.g., 480,000 × 5% = 24,000 SAR).

2) ★ On missing data — reassure, don't scare ★
   If data is incomplete, never say or imply that there's a "problem" or "fault" in the company. The mandatory phrasing:
   "Result needs verification — this isn't a problem with your company; some data is simply incomplete, so accuracy is limited. For deeper analysis, please add: [list the missing items precisely]."
   Always separate: incomplete data ≠ poor performance. Never conflate them.

3) Vague statements are strictly forbidden: "Performance must improve" ❌ — Correct: "Reducing discounts by 15% is expected to increase profit by SAR 245,000" ✅.

4) Every conclusion must answer the full chain: What is happening? Why is it happening? (ask "why" up to 5 times to reach the root cause — don't stop at symptoms) What is its financial impact? What is the best decision? How much will the decision yield in SAR? What is its priority? How fast to execute? What is the risk of inaction? How do we measure success after 30 days?

5) Always rank decisions by largest financial impact first, and each decision follows the unified format above.

Full analysis structure (apply for a full company or branch analysis):
① Executive decisions first: top 3 decisions in the unified format — read in under a minute and acted on.
② Revenue leakage detection: examine (waste, discounts, returns, margin drop, high costs, weak collections, weak pricing) and calculate each in SAR.
③ Root-cause analysis: Why×5 method (as supporting evidence for decisions above).
④ Quick wins (within a week) ⑤ Medium wins (30-90 days) ⑥ Strategic opportunities (6-24 months).
⑦ Forecasts: three scenarios — if current continues / if improved / if worsened.
⑧ Overall financial estimate: expected savings + profit increase + cash flow improvement.
⑨ Monitoring metrics: top KPIs to track decision impact.
⑩ Missing data (if any): use the reassuring phrasing from rule 2 — list what's missing and its impact on accuracy, with no implication of any fault.
For short questions and partial analyses: answer directly and briefly — but start with the decision always.

Final check before delivery: If I were the business owner, would I pay SAR 5,000 for this analysis? Did I reach the decision in the first 10 seconds? If no — rewrite it."""


# ==================== NAMES IN ENGLISH ====================
SECTOR_NAMES_EN = {
    "fnb": "Restaurants & Cafés",
    "retail": "Retail",
    "ecommerce": "E-commerce",
    "manufacturing": "Manufacturing",
    "contracting": "Contracting",
    "distribution": "Distribution",
    "services": "Services",
    "clinics": "Clinics",
    "hospitals": "Hospitals",
    "logistics": "Logistics",
    "other": "Other",
}

PRIORITY_NAMES_EN = {
    "growth": "Growth & Expansion",
    "profit": "Profitability",
    "liquidity": "Liquidity & Cash Flow",
    "efficiency": "Operational Efficiency",
}

# Sector intelligence in English (matches SECTOR_INTELLIGENCE structure)
SECTOR_INTELLIGENCE_EN = {
    "fnb": {"kpis": ["Average order value", "Ingredient cost %", "Visit frequency", "Kitchen waste", "Peak-hour orders"],
        "terms": "order, table, ingredients, waste, peak, delivery, meal",
        "watch": "ingredient cost above 35%, kitchen waste above 5%, over-reliance on delivery apps and their commissions",
        "advice": "Focus on average order value (upselling), control ingredient cost, and reduce waste. Watch delivery commissions that may eat the margin."},
    "retail": {"kpis": ["Inventory turnover", "Dead stock", "Sales per m²", "Average basket", "Conversion rate"],
        "terms": "inventory, SKU, turnover, basket, dead stock, discounts, season",
        "watch": "dead stock tying up capital, discounts eroding margin, slow-moving SKUs",
        "advice": "Move dead stock via targeted offers, focus on high-turnover SKUs, and improve average basket."},
    "ecommerce": {"kpis": ["Customer acquisition cost", "Cart abandonment", "Customer lifetime value", "Return rate", "Conversion rate"],
        "terms": "visit, cart, conversion, shipping, return, acquisition, campaign",
        "watch": "acquisition cost above lifetime value, high cart abandonment, high returns",
        "advice": "Reduce acquisition cost vs LTV, address cart abandonment, and cut returns with more accurate descriptions."},
    "manufacturing": {"kpis": ["Production efficiency", "Unit cost", "Waste rate", "Capacity utilization", "Cycle time"],
        "terms": "production, unit, line, capacity, waste, raw materials, cycle",
        "watch": "unused production capacity, raw material waste, rising unit cost",
        "advice": "Raise capacity utilization, cut material waste, and analyze unit cost per line."},
    "contracting": {"kpis": ["Project margin", "Completion rate", "Project cash flow", "Outstanding payments", "Cost overrun"],
        "terms": "project, payment, invoice, completion, contractor, cost, receivables",
        "watch": "project cost overruns, delayed payments pressuring liquidity, low project margin",
        "advice": "Monitor cash flow per project, collect payments on time, and control cost overruns early."},
    "distribution": {"kpis": ["Distribution cost", "Inventory turnover", "Fleet efficiency", "Fulfilled orders", "Delivery time"],
        "terms": "distribution, fleet, order, warehouse, delivery, agent, inventory",
        "watch": "high distribution cost, underused fleet, delayed delivery",
        "advice": "Improve fleet and routing efficiency, control delivery cost, and cut lead times."},
    "services": {"kpis": ["Revenue per employee", "Team utilization", "Customer satisfaction", "Retention rate", "Contract value"],
        "terms": "service, contract, employee, hour, utilization, customer, retention",
        "watch": "low team utilization, over-reliance on few customers, customer churn",
        "advice": "Raise team utilization, diversify the customer base, and focus on retention and lifetime value."},
    "clinics": {"kpis": ["Appointment fill rate", "Revenue per patient", "Patient return rate", "Insurance collection", "No-shows"],
        "terms": "appointment, patient, occupancy, insurance, visit, follow-up, clinic",
        "watch": "empty appointments, delayed insurance collection, high no-show rate",
        "advice": "Increase appointment fill rate, speed up insurance collection, and reduce no-shows with reminders."},
    "hospitals": {"kpis": ["Bed occupancy rate", "Revenue per bed", "Average length of stay", "Insurance collection", "Occupancy rate"],
        "terms": "bed, patient, stay, insurance, department, occupancy, emergency",
        "watch": "empty beds, delayed insurance collection, longer-than-usual stay",
        "advice": "Improve bed occupancy, control length of stay, and speed up the insurance collection cycle."},
    "logistics": {"kpis": ["Shipment cost", "Fleet efficiency", "Delivery time", "On-time shipments", "Capacity utilization"],
        "terms": "shipment, fleet, delivery, route, capacity, warehouse, tracking",
        "watch": "high shipping cost, delayed delivery, underused capacity",
        "advice": "Improve route and fleet efficiency, control shipment cost, and increase on-time delivery rate."},
    "other": {"kpis": ["Profit margin", "Monthly growth", "Expense ratio", "Cash flow", "Revenue"],
        "terms": "revenue, expense, profit, growth, liquidity",
        "watch": "declining margin, rising expenses, cash flow pressure",
        "advice": "Monitor margin, expenses, and cash flow, and focus on the largest impact item."},
}


def build_company_profile_context_en(company) -> str:
    """English version of the company profile injection."""
    parts = [f"Company name: {company.name}",
             f"Activity/Sector: {SECTOR_NAMES_EN.get(company.sector, 'Not specified')}"]
    if getattr(company, "employees", 0):
        parts.append(f"Employees: {company.employees}")
    if getattr(company, "annual_revenue", 0):
        parts.append(f"Approximate annual revenue: {company.annual_revenue:,.0f} {company.currency}")
    if getattr(company, "target_margin", 0):
        parts.append(f"Target profit margin: {company.target_margin}%")
    prio = getattr(company, "top_priority", "profit")
    parts.append(f"Management's top priority: {PRIORITY_NAMES_EN.get(prio, prio)}")
    try:
        goals = json.loads(getattr(company, "goals_json", "{}") or "{}")
        if goals:
            gtxt = ", ".join(f"{k}: {v}" for k, v in goals.items() if v)
            if gtxt:
                parts.append(f"Specific goals: {gtxt}")
    except Exception:
        pass
    profile = "\n".join("- " + p for p in parts)
    return (
        "\n\n# Company profile (use it to tailor your analysis and priorities):\n" + profile +
        f"\n\nImportant: guide your recommendations to serve the top priority ({PRIORITY_NAMES_EN.get(prio, prio)}) first, "
        "and consider the company size and sector when suggesting solutions."
    )


def build_sector_context_en(sector: str) -> str:
    """English version of the sector intelligence injection."""
    si = SECTOR_INTELLIGENCE_EN.get(sector, SECTOR_INTELLIGENCE_EN["other"])
    name = SECTOR_NAMES_EN.get(sector, "the business")
    return (
        f"\n\n# Sector intelligence ({name}) — tailor your analysis to this business type:\n"
        f"- Key KPIs for this sector: {', '.join(si['kpis'])}\n"
        f"- Sector terminology (use it in your language): {si['terms']}\n"
        f"- What to watch specifically in this sector: {si['watch']}\n"
        f"- Recommendation guidance: {si['advice']}\n"
        "Use this sector's KPIs and terminology specifically — don't give generic analysis that fits any business."
    )



def save_memory(company_id: int, kind: str, title: str, content: str = ""):
    """يسجّل حدثاً في ذاكرة الشركة المؤسسية — لا يفشل أبداً حتى لا يكسر المسار الرئيسي."""
    try:
        with Session(engine) as ms:
            ms.add(CompanyMemory(
                company_id=company_id, kind=kind,
                title=str(title)[:200], content=str(content)[:3000],
            ))
            ms.commit()
    except Exception as _dbe:
        _logger.error(f"db op failed (line ~2289): {type(_dbe).__name__}: {str(_dbe)[:150]}")


def log_audit(company_id: int, user_id: int, user_name: str, action: str,
              target: str = "", details: str = "", ip: str = ""):
    """يسجّل إجراءً في سجل التدقيق — لا يفشل أبداً حتى لا يكسر المسار الرئيسي."""
    try:
        with Session(engine) as ms:
            ms.add(AuditLog(
                company_id=company_id, user_id=user_id,
                user_name=str(user_name)[:100], action=str(action)[:40],
                target=str(target)[:200], details=str(details)[:500], ip=str(ip)[:60],
            ))
            ms.commit()
    except Exception as _dbe:
        _logger.error(f"db op failed (line ~2304): {type(_dbe).__name__}: {str(_dbe)[:150]}")


def get_lang(request=None) -> str:
    """يقرأ لغة الطلب من هيدر X-Lang. الافتراضي عربي."""
    if request is None:
        return "ar"
    try:
        lang = (request.headers.get("X-Lang") or request.headers.get("x-lang") or "ar").strip().lower()
        return "en" if lang == "en" else "ar"
    except Exception:
        return "ar"


def company_gemini(prompt: str, company=None, lang: str = "ar") -> str:
    """يستدعي Gemini بالإطار المناسب حسب اللغة (ar/en). يحقن ملف الشركة وذكاء القطاع بالإنجليزي أو العربي."""
    if company is not None:
        try:
            if lang == "en":
                prompt = prompt + build_company_profile_context_en(company)
                prompt = prompt + build_sector_context_en(company.sector)
            else:
                prompt = prompt + build_company_profile_context(company)
                prompt = prompt + build_sector_context(company.sector)
        except Exception:
            pass
    # اختيار الإطار حسب اللغة
    prompt = prompt + (EXEC_FRAMEWORK_EN if lang == "en" else EXEC_FRAMEWORK)
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

        company = Company(name=name, owner_id=user.id, sector=sector, is_active=1)  # تفعيل فوري — الأدمن يتابع فقط
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
def _five_pillars(total_sales, total_expenses, branch_data):
    """يحسب الركائز الخمس لصحة الشركة (الربحية، السيولة، النمو، العملاء، المخاطر).
    كل ركيزة 0-100 بمعادلة معيارية شفافة — لا اختراع. has_data=False عند نقص البيانات."""
    pillars = {}
    margin = ((total_sales - total_expenses) / total_sales * 100) if (total_sales > 0 and total_expenses > 0) else 0
    expense_ratio = (total_expenses / total_sales * 100) if total_sales > 0 else 0
    net = total_sales - total_expenses
    growths = [bd["entry"].growth for bd in branch_data if bd["entry"].growth is not None]
    avg_growth = sum(growths) / len(growths) if growths else 0
    repeats = [bd["entry"].repeat_rate for bd in branch_data if bd["entry"].repeat_rate > 0]
    avg_repeat = sum(repeats) / len(repeats) if repeats else 0

    # ① الربحية — من الهامش
    if margin >= 25: p = 95
    elif margin >= 20: p = 85
    elif margin >= 15: p = 72
    elif margin >= 10: p = 58
    elif margin >= 5: p = 42
    elif margin > 0: p = 28
    else: p = 10
    pillars["profitability"] = {"name": "الربحية", "name_en": "Profitability", "score": p, "icon": "💰",
        "detail": f"هامش الربح {round(margin,1)}%" if margin else "لا توجد بيانات ربح كافية",
        "has_data": total_sales > 0 and total_expenses > 0}

    # ② السيولة — من صافي التشغيل ونسبة المصروفات
    if net > 0 and expense_ratio < 70: l = 90
    elif net > 0 and expense_ratio < 85: l = 72
    elif net > 0: l = 55
    elif net == 0: l = 40
    else: l = 20
    pillars["liquidity"] = {"name": "السيولة", "name_en": "Liquidity", "score": l, "icon": "💧",
        "detail": f"نسبة المصروفات {round(expense_ratio,1)}%" if total_sales > 0 else "لا توجد بيانات كافية",
        "has_data": total_sales > 0}

    # ③ النمو — من متوسط نمو الفروع
    if avg_growth >= 15: g = 92
    elif avg_growth >= 8: g = 78
    elif avg_growth >= 3: g = 62
    elif avg_growth >= 0: g = 48
    elif avg_growth >= -10: g = 30
    else: g = 15
    pillars["growth"] = {"name": "النمو", "name_en": "Growth", "score": g, "icon": "📈",
        "detail": f"متوسط النمو {round(avg_growth,1)}%" if growths else "يحتاج فترتين على الأقل",
        "has_data": len(growths) > 0}

    # ④ العملاء — من معدل التكرار
    if avg_repeat >= 40: c = 90
    elif avg_repeat >= 30: c = 76
    elif avg_repeat >= 20: c = 60
    elif avg_repeat >= 10: c = 42
    elif avg_repeat > 0: c = 28
    else: c = 0
    pillars["customers"] = {"name": "العملاء", "name_en": "Customers", "score": c, "icon": "👥",
        "detail": f"معدل تكرار العملاء {round(avg_repeat,1)}%" if repeats else "لا توجد بيانات عملاء",
        "has_data": len(repeats) > 0}

    # ⑤ المخاطر — معكوس (درجة عالية = مخاطر منخفضة)
    risk_pts = 0
    if net < 0: risk_pts += 40
    elif margin < 5: risk_pts += 22
    if expense_ratio >= 90: risk_pts += 30
    elif expense_ratio >= 80: risk_pts += 15
    if avg_growth < -10: risk_pts += 20
    elif avg_growth < 0: risk_pts += 10
    if avg_repeat and avg_repeat < 20: risk_pts += 12
    r = max(100 - risk_pts, 0)
    pillars["risk"] = {"name": "المخاطر", "name_en": "Risk", "score": r, "icon": "🛡️",
        "detail": ("مخاطر منخفضة" if r >= 70 else ("مخاطر متوسطة" if r >= 45 else "مخاطر مرتفعة")),
        "has_data": total_sales > 0}

    valid = [pl["score"] for pl in pillars.values() if pl["has_data"]]
    overall = round(sum(valid) / len(valid)) if valid else 0
    return {"pillars": pillars, "overall": overall}


def _branch_hr_metrics(s, company_id: int, branch_id: int, branch_sales: float) -> dict:
    """يحسب مؤشرات الموارد البشرية لفرع: عدد الموظفين، الإنتاجية (KPI)، دوران الموظفين.
    يعتمد على بيانات وحدة HR المُدخلة للفرع. يرجع has_data=False لو لا بيانات (بدون اختراع)."""
    try:
        he = s.exec(
            select(CompanyModuleEntry).where(
                CompanyModuleEntry.company_id == company_id,
                CompanyModuleEntry.module == "hr",
                (CompanyModuleEntry.branch_id == branch_id) | (CompanyModuleEntry.branch_id == None),
            ).order_by(CompanyModuleEntry.created_at.desc())
        ).first()
        if not he or not he.data:
            return {"has_data": False}
        d = json.loads(he.data)
        emp = turnover = salary_cost = None
        for k, v in d.items():
            kl = str(k).lower()
            try:
                fv = float(str(v).replace(",", "").replace("%", "").strip())
            except Exception:
                continue
            if emp is None and ("موظف" in k or "عدد" in kl or "employee" in kl or "headcount" in kl or "staff" in kl):
                emp = fv
            if turnover is None and ("دوران" in k or "turnover" in kl or "استقالة" in k):
                turnover = fv
            if salary_cost is None and ("رواتب" in k or "salary" in kl or "أجور" in k or "payroll" in kl):
                salary_cost = fv
        result = {"has_data": True}
        if emp and emp > 0:
            result["employees"] = int(emp)
            result["productivity"] = round(branch_sales / emp) if branch_sales > 0 else 0
            if salary_cost and branch_sales > 0:
                result["salary_ratio"] = round(salary_cost / branch_sales * 100, 1)
        if turnover is not None:
            result["turnover"] = round(turnover, 1)
        return result
    except Exception:
        return {"has_data": False}


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
                "hr": _branch_hr_metrics(s, company.id, b.id, latest.sales),
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

        # ===== تنبيهات نسبية (%) لأعلى اللوحة — خدمة ٨ =====
        pct_alarms = []
        if total_sales > 0:
            _exp_ratio = round(total_expenses / total_sales * 100) if total_expenses > 0 else 0
            if _exp_ratio >= 85:
                pct_alarms.append({"icon": "🔴", "label": "المصروفات", "value": f"{_exp_ratio}%",
                                   "msg": "المصروفات تلتهم معظم المبيعات", "level": "high"})
            elif _exp_ratio >= 75:
                pct_alarms.append({"icon": "🟡", "label": "المصروفات", "value": f"{_exp_ratio}%",
                                   "msg": "نسبة المصروفات مرتفعة", "level": "medium"})
        if branch_data:
            _weak_pct = round(weak / len(branch_data) * 100) if len(branch_data) else 0
            if _weak_pct >= 40:
                pct_alarms.append({"icon": "🔴", "label": "فروع ضعيفة", "value": f"{_weak_pct}%",
                                   "msg": f"{weak} من {len(branch_data)} فروع أداؤها ضعيف", "level": "high"})
        if avg_margin and avg_margin < 10:
            pct_alarms.append({"icon": "🟡", "label": "هامش الربح", "value": f"{round(avg_margin)}%",
                               "msg": "هامش الربح منخفض", "level": "medium"})

        return {
            "company": {"id": company.id, "name": company.name, "sector": company.sector},
            "has_data": len(active) > 0,
            "pct_alarms": pct_alarms,
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
def company_analyze(data: dict, request: Request, user: User = Depends(get_current_user)):
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
            raise HTTPException(400, "نتيجة تحتاج إلى تحقق — هذه ليست مشكلة في شركتك، بل لم تُدخل بيانات الفروع بعد. أضف بيانات فرع واحد على الأقل ليبدأ التحليل.")

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
{"5. **قياس أداء الموظفين (KPI/KRA)** — لكل مؤشر أداء رئيسي: الهدف (Target) مقابل الفعلي (Actual) ونسبة الإنجاز (Achievement %) والحالة (ممتاز/جيد/يحتاج تحسين). استخدم مؤشرات مثل: الإنتاجية لكل موظف (المبيعات ÷ عدد الموظفين)، نسبة تكلفة الرواتب من الإيرادات، معدل دوران الموظفين. لا تعطِ درجة عامة غامضة — اربط كل تقييم بمؤشر محدد وهدفه." if module == "hr" else ""}

اكتب بصيغة Markdown، عناوين ## واضحة، نقاط مرتّبة، أرقام محددة كلما أمكن. لا تخرج عن نطاق وحدة {mlabel}."""
            txt = company_gemini(prompt, company, lang=get_lang(request))
            if txt:
                clean, _a, _d, _o = extract_exec(txt)
                txt = clean
            log_activity(user.name, f"حلّل وحدة {mlabel}", user.email)
            if txt:
                save_memory(company.id, "analysis", f"تحليل وحدة {mlabel}", txt)
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
            txt = company_gemini(prompt, company, lang=get_lang(request))
            if txt:
                clean, _a, _d, _o = extract_exec(txt)
                txt = clean
                e.smart_message = clean
                s.add(e)
                s.commit()
            if txt:
                save_memory(company.id, "analysis", f"تحليل فرع {b.name}", txt)
            return {"ok": True, "scope": "branch", "branch": b.name,
                    "analysis": txt or "تعذّر توليد التحليل، حاول بعد قليل."}

        prompt = build_company_prompt(company, sector_name, rows)
        txt = company_gemini(prompt, company, lang=get_lang(request))
        if txt:
            clean, _a, _d, _o = extract_exec(txt)
            txt = clean
        log_activity(user.name, f"ولّد تحليل شركة: {company.name}", user.email)
        if txt:
            save_memory(company.id, "analysis", "التحليل التنفيذي الشامل", txt)
            log_audit(company.id, user.id, user.name, "analyze", "التحليل التنفيذي الشامل", ip=(request.client.host if request and request.client else ""))
        return {"ok": True, "scope": "company", "company": company.name,
                "analysis": txt or "تعذّر توليد التحليل، حاول بعد قليل."}


# ===== اسأل نبّاه الذكي (صندوق المحادثة) =====
@app.post("/company/finance-copilot")
def company_finance_copilot(data: dict, user: User = Depends(get_current_user)):
    """المساعد المالي الذكي (Copilot) — إضافة مميّزة:
    يجيب أسئلة مالية محددة بأرقام فورية دقيقة من البيانات (بلا انتظار AI).
    'كم أكبر مصروف؟' · 'أي فرع الأربح؟' · 'كم هامشي؟'"""
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    q = (data.get("question") or "").strip()
    if not q:
        raise HTTPException(400, "اكتب سؤالك")
    with Session(engine) as s:
        company = s.get(Company, user.company_id)
        if not company or not check_permission(get_user_role(s, user), "finance", "view"):
            raise HTTPException(403, "غير مصرّح")
        if company.is_active != 1:
            raise HTTPException(402, "شركتك قيد التفعيل")

        # نجمع البيانات
        branches = s.exec(select(CompanyBranch).where(CompanyBranch.company_id == company.id, CompanyBranch.is_active == 1)).all()
        brdata = []
        total_sales = total_expenses = total_profit = total_customers = 0.0
        for b in branches:
            e = s.exec(select(CompanyEntry).where(CompanyEntry.branch_id == b.id).order_by(CompanyEntry.created_at.desc())).first()
            if e:
                brdata.append({"name": b.name, "sales": e.sales or 0, "expenses": e.expenses or 0,
                               "profit": e.profit or 0, "margin": e.margin or 0, "customers": e.customers or 0})
                total_sales += e.sales or 0
                total_expenses += e.expenses or 0
                total_profit += e.profit or 0
                total_customers += e.customers or 0
        cur = company.currency or "ريال"
        fmt = lambda n: f"{round(n):,}"
        margin = round(total_profit / total_sales * 100, 1) if total_sales else 0

        ql = q.lower()
        answer = None

        # نمط الأسئلة المالية الشائعة (إجابة فورية دقيقة)
        if any(w in ql for w in ["هامش", "margin", "الربحية"]):
            answer = f"هامش الربح الحالي: **{margin}%** (صافي ربح {fmt(total_profit)} {cur} من مبيعات {fmt(total_sales)} {cur})."
        elif any(w in ql for w in ["أكبر مصروف", "أعلى مصروف", "أكثر صرف", "المصروفات"]):
            answer = f"إجمالي المصروفات: **{fmt(total_expenses)} {cur}** ({round(total_expenses/total_sales*100) if total_sales else 0}% من المبيعات). لتفصيل البنود، أدخِل بيانات الوحدة المالية."
        elif any(w in ql for w in ["أربح فرع", "أفضل فرع", "أعلى فرع", "الأفضل"]):
            if brdata:
                best = max(brdata, key=lambda x: x["profit"])
                answer = f"الفرع الأربح: **{best['name']}** بصافي ربح {fmt(best['profit'])} {cur} وهامش {round(best['margin'],1)}%."
        elif any(w in ql for w in ["أسوأ فرع", "أضعف فرع", "أقل فرع"]):
            if brdata:
                worst = min(brdata, key=lambda x: x["profit"])
                answer = f"الفرع الأضعف: **{worst['name']}** بصافي ربح {fmt(worst['profit'])} {cur} وهامش {round(worst['margin'],1)}%. يحتاج مراجعة."
        elif any(w in ql for w in ["مبيعات", "إيراد", "revenue", "sales"]):
            answer = f"إجمالي المبيعات: **{fmt(total_sales)} {cur}** عبر {len(brdata)} فرع."
        elif any(w in ql for w in ["ربح", "profit"]):
            answer = f"صافي الربح: **{fmt(total_profit)} {cur}** (هامش {margin}%)."
        elif any(w in ql for w in ["عملاء", "customers"]):
            answer = f"إجمالي العملاء: **{fmt(total_customers)}** عبر كل الفروع."
        elif any(w in ql for w in ["كم فرع", "عدد الفروع", "الفروع"]):
            answer = f"لديك **{len(branches)} فرع**، منها {len(brdata)} فرع فيه بيانات."

        if answer:
            return {"answered": True, "answer": answer, "instant": True}
        # لو ما فهم السؤال المالي المحدد، نوجّه لـ"اسأل نبّاه" العام
        return {"answered": False,
                "answer": "هذا سؤال يحتاج تحليلاً أعمق — استخدم «اسأل نبّاه» للإجابة التفصيلية بالذكاء الاصطناعي.",
                "instant": False}


@app.post("/company/ask")
def company_ask(request: Request, data: dict, user: User = Depends(get_current_user)):
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    q = (data.get("question") or "").strip()
    if not q:
        raise HTTPException(400, "اكتب سؤالك")
    # ذاكرة المحادثة: آخر ٦ رسائل من الواجهة (اختياري)
    history = data.get("history") or []
    with Session(engine) as s:
        company = s.get(Company, user.company_id)
        if not company or company.owner_id != user.id:
            raise HTTPException(403, "غير مصرّح")
        if company.is_active != 1:
            raise HTTPException(402, "شركتك قيد التفعيل — فعّلها من لوحة الإدارة")
        branches = s.exec(
            select(CompanyBranch).where(CompanyBranch.company_id == company.id, CompanyBranch.is_active == 1)
        ).all()

        # ═══ ١) بيانات الفروع: آخر فترة + تاريخ ٦ فترات لكشف الاتجاهات ═══
        rows = []
        total_sales = total_expenses = 0.0
        for b in branches:
            ents = s.exec(
                select(CompanyEntry).where(CompanyEntry.branch_id == b.id).order_by(CompanyEntry.created_at.desc())
            ).all()
            if not ents:
                continue
            e = ents[0]
            total_sales += e.sales
            total_expenses += e.expenses
            margin_txt = f"هامش {e.margin}%" if e.expenses > 0 else "المصروفات غير مدخلة"
            line = (f"- {b.name} ({b.city or '—'}) [{e.period}]: مبيعات {round(e.sales):,}ر، فواتير {e.invoices}، "
                    f"عملاء {e.customers}، {margin_txt}, نمو {e.growth}%، مؤشر {e.branch_score}/100")
            hist = ents[1:7]
            if hist:
                trail = " | ".join(f"{h.period}: {round(h.sales):,}ر" for h in reversed(hist))
                line += f"\n  التاريخ: {trail}"
            rows.append(line)
        context = "\n".join(rows) if rows else "لا توجد بيانات فروع بعد."

        # ═══ ٢) بيانات الوحدات (آخر إدخال لكل وحدة) ═══
        modules_txt = ""
        try:
            seen = set()
            mod_entries = s.exec(
                select(CompanyModuleEntry).where(
                    CompanyModuleEntry.company_id == company.id
                ).order_by(CompanyModuleEntry.created_at.desc())
            ).all()
            for me in mod_entries:
                key = (me.module, me.branch_id or 0)
                if key in seen or me.module == "goals":
                    continue
                seen.add(key)
                try:
                    md = json.loads(me.data) if me.data else {}
                except Exception:
                    continue
                clean = {k: v for k, v in md.items() if not k.startswith("__") and str(v).strip()}
                if clean:
                    scope = ""
                    if me.branch_id:
                        br = s.get(CompanyBranch, me.branch_id)
                        scope = f" (فرع {br.name})" if br else ""
                    label = MODULE_LABEL.get(me.module, me.module)
                    top = " | ".join(f"{k}: {v}" for k, v in list(clean.items())[:8])
                    modules_txt += f"\n- {label}{scope} [{me.period}]: {top}"
        except Exception:
            pass

        # ═══ ٣) الأهداف ═══
        goals_txt = ""
        try:
            g = s.exec(
                select(CompanyModuleEntry).where(
                    CompanyModuleEntry.company_id == company.id,
                    CompanyModuleEntry.module == "goals",
                ).order_by(CompanyModuleEntry.created_at.desc())
            ).first()
            if g and g.data:
                gd = json.loads(g.data)
                goals_txt = "\n".join(f"- {k}: {v}" for k, v in gd.items())
        except Exception:
            pass

        # ═══ ٤) مرجعية القطاع ═══
        sector = company.sector or "other"
        bench = SECTOR_BENCHMARKS.get(sector, SECTOR_BENCHMARKS["other"])
        bench_txt = " | ".join(f"{b['label']}: {b['value']}" for b in bench.values())

        # ═══ ٥) ذاكرة المحادثة ═══
        hist_txt = ""
        if isinstance(history, list) and history:
            parts = []
            for h in history[-6:]:
                role = "المالك" if h.get("role") == "user" else "نبّاه"
                content = str(h.get("content", ""))[:400]
                parts.append(f"{role}: {content}")
            hist_txt = "\n".join(parts)

        # ═══ ٦) الذاكرة المؤسسية: آخر تحليلات وقرارات الشركة ═══
        memory_txt = ""
        try:
            mems = s.exec(
                select(CompanyMemory).where(CompanyMemory.company_id == company.id)
                .order_by(CompanyMemory.created_at.desc()).limit(8)
            ).all()
            if mems:
                memory_txt = "\n".join(
                    f"- [{m.created_at.strftime('%Y-%m-%d')}] {m.title}: {m.content[:150]}"
                    for m in mems
                )
        except Exception:
            pass

        # ═══ ٦ب) سجل القرارات المقاسة: ماذا قررنا وهل نجح؟ ═══
        decisions_txt = ""
        try:
            decs = s.exec(
                select(CompanyDecision).where(CompanyDecision.company_id == company.id)
                .order_by(CompanyDecision.created_at.desc()).limit(6)
            ).all()
            if decs:
                lines = []
                for dd in decs:
                    if dd.status == "done" and dd.baseline_sales > 0 and dd.result_sales > 0:
                        chg = round((dd.result_sales - dd.baseline_sales) / dd.baseline_sales * 100, 1)
                        lines.append(f"- ✅ [{dd.created_at.strftime('%Y-%m')}] {dd.title} → نُفّذ، والمبيعات تغيّرت {chg:+}% بعده")
                    elif dd.status == "cancelled":
                        lines.append(f"- ✖ [{dd.created_at.strftime('%Y-%m')}] {dd.title} → أُلغي")
                    else:
                        lines.append(f"- ⏳ [{dd.created_at.strftime('%Y-%m')}] {dd.title} → قيد التنفيذ (مسؤول: {dd.owner or '—'})")
                decisions_txt = "\n".join(lines)
            if decisions_txt:
                memory_txt = (memory_txt + "\n\n# سجل القرارات ونتائجها المقاسة:\n" + decisions_txt) if memory_txt else ("# سجل القرارات ونتائجها المقاسة:\n" + decisions_txt)
        except Exception:
            pass

        exp_note = ""
        if total_sales > 0 and total_expenses <= 0:
            exp_note = "\n⚠️ تنبيه: المصروفات غير مدخلة (بيانات POS مبيعات فقط) — لا تحسب هامش أو ربح، ونبّه المالك لإضافتها."

        prompt = f"""أنت "نبّاه"، المستشار التنفيذي لشركة "{company.name}" (قطاع: {SECTOR_NAMES.get(sector, 'شركة')}). أجب عن سؤال المالك بدقة اعتماداً على البيانات التالية فقط.{exp_note}

# بيانات الفروع (آخر فترة + التاريخ):
{context}

# بيانات وحدات الشركة:{modules_txt or " لا توجد."}

# أهداف الشركة:
{goals_txt or "لم تُحدَّد أهداف بعد."}

# متوسطات قطاع {SECTOR_NAMES.get(sector, '')} (إرشادية):
{bench_txt}
{f'''
# ذاكرة الشركة (تحليلات وقرارات وأحداث سابقة):
{memory_txt}''' if memory_txt else ''}
{f'''
# المحادثة السابقة:
{hist_txt}''' if hist_txt else ''}

# سؤال المالك الآن:
{q}

أجب مباشرة وباختصار مناسب لحجم السؤال. اذكر الأرقام الداعمة من البيانات أعلاه، وقارن بالقطاع متى ما كان مفيداً، واختم بخطوة عملية واحدة محددة إن ناسب."""
        txt = company_gemini(prompt, company, lang=get_lang(request))
        log_activity(user.name, f"سأل نبّاه: {q[:60]}", user.email)
        if txt:
            save_memory(company.id, "question", q[:200], f"السؤال: {q}\n\nإجابة نبّاه: {txt}")
        return {"ok": True, "answer": txt or "تعذّر توليد الإجابة، حاول بعد قليل."}


# ===== بيانات التقرير التنفيذي (للطباعة) =====
# ═══════════════════════════════════════════════════════════
#  Phase 2.2 — تكامل محرّكات الذكاء المركزية
#  endpoint جديد يستخدم المحرّكات الموحّدة (KPI/Variance/Driver/
#  RootCause/Impact/AI Gateway). لا يلمس الـendpoints القديمة.
# ═══════════════════════════════════════════════════════════
_DATA_TABLES_READY = {"done": False}


def _ensure_data_tables():
    """يتأكد مرة واحدة أن جداول 2.4 موجودة (مهم لقواعد البيانات القائمة)."""
    if _DATA_TABLES_READY["done"]:
        return
    try:
        SQLModel.metadata.create_all(engine)
        _DATA_TABLES_READY["done"] = True
    except Exception as e:
        _logger.error(f"create_all للجداول الجديدة فشل: {type(e).__name__}: {str(e)[:200]}")


def _load_phase22_bridge():
    """يحمّل جسر Phase 2.2 بأمان — يُرجع None إن لم تُرفع الملفات."""
    try:
        import sys as _sys, os as _os
        _p22 = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "phase22")
        if _p22 not in _sys.path:
            _sys.path.insert(0, _p22)
        import platform_bridge as _bridge
        return _bridge
    except Exception as _e:
        _logger.error(f"Phase 2.2 bridge غير متاح: {type(_e).__name__}")
        return None


@app.get("/company/intelligence")
def company_intelligence(user: User = Depends(get_current_user), period: Optional[str] = None):
    """تحليل ذكاء الأعمال المركزي (Phase 2.2).
    يستخدم: KPI Engine + Data Trust + Driver/RootCause/Impact + Quality Gate.
    كل الأرقام من المحرّك المركزي (Decimal) — لا صيغ مكرّرة."""
    bridge = _load_phase22_bridge()
    if bridge is None:
        raise HTTPException(503, "محرّكات التحليل غير متاحة حالياً — ارفع ملفات phase22.")
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    with Session(engine) as s:
        company = s.get(Company, user.company_id)
        role = get_user_role(s, user)
        if not company or (not check_permission(role, "finance", "view") and role != "owner"):
            raise HTTPException(403, "غير مصرّح")
        if company.is_active != 1:
            raise HTTPException(402, "شركتك قيد التفعيل")

        # نجلب البيانات — مُفلترة بـcompany_id (العزل محفوظ)
        branches = s.exec(select(CompanyBranch).where(
            CompanyBranch.company_id == company.id, CompanyBranch.is_active == 1)).all()
        # Period-aware selection (remediation): the current and comparison periods are
        # determined from the parsed CompanyEntry.period, not from "latest two rows".
        # Double tenant scoping: company_id AND membership in this company's active branches.
        branch_ids = [b.id for b in branches]
        rows = []
        if branch_ids:
            rows = s.exec(select(CompanyEntry).where(
                CompanyEntry.company_id == company.id,
                CompanyEntry.branch_id.in_(branch_ids),
            ).order_by(CompanyEntry.created_at.desc()).limit(5000)).all()
        split = bridge.split_by_period(rows, current_period=period)
        entries = split["current_entries"]
        prev_entries = split["comparison_entries"]

        # بيانات الوحدة المالية (مُفلترة بالشركة)
        fin = s.exec(select(CompanyModuleEntry).where(
            CompanyModuleEntry.company_id == company.id,
            CompanyModuleEntry.module == "finance"
        ).order_by(CompanyModuleEntry.created_at.desc())).first()
        module_data = {}
        if fin and fin.data:
            try:
                module_data = json.loads(fin.data)
            except Exception:
                module_data = {}

        result = bridge.analyze_company_financials(
            entries, module_data=module_data,
            previous_entries=prev_entries or None,
            currency=company.currency or "SAR",
            period=split["current_period"],
            comparison_period=split["comparison_period"],
            period_gaps=split["data_gaps"],
            user_role=role, company_name=company.name)
        return result


@app.post("/company/intelligence/ask")
def company_intelligence_ask(data: dict, request: Request, user: User = Depends(get_current_user)):
    """سؤال AI عبر البوابة المُهيكلة (Phase 2.2).
    AI يستقبل أرقاماً محسوبة مسبقاً — لا يخترع، ويحترم بوابة الجودة."""
    bridge = _load_phase22_bridge()
    if bridge is None:
        raise HTTPException(503, "محرّكات التحليل غير متاحة حالياً.")
    question = (data.get("question") or "").strip()
    analysis = company_intelligence(user)  # يعيد استخدام نفس المنطق الآمن
    with Session(engine) as s:
        company = s.get(Company, user.company_id)
    return bridge.ai_analyze(company_gemini, analysis, question,
                             lang=get_lang(request), company=company)


# ═══════════════════════════════════════════════════════════
#  Phase 2.3 — Executive Intelligence, Decisions & Actions
#  Numbers come from phase23/intelligence_engine (deterministic).
#  AI is optional narrative only, through the Phase 2.2 quality gate.
# ═══════════════════════════════════════════════════════════
ACTION_STATUSES = {"not_started", "in_progress", "blocked", "completed", "cancelled"}
ACTION_TERMINAL = {"completed", "cancelled"}


def _load_phase23():
    try:
        import sys as _sys, os as _os
        _base = _os.path.dirname(_os.path.abspath(__file__))
        for _d in ("phase21", "phase22", "phase23"):
            _p = _os.path.join(_base, _d)
            if _p not in _sys.path:
                _sys.path.insert(0, _p)
        try:
            import intelligence_engine as _ie
            import period_aggregation as _pa
        except ModuleNotFoundError:
            # المجلدات غير موجودة: نستخدم حزمة المحركات في ملف واحد بجانب main.py
            import nabbah_engines  # noqa: F401
            import intelligence_engine as _ie
            import period_aggregation as _pa
        return _ie, _pa
    except Exception as _e:
        import os as _os
        _b = _os.path.dirname(_os.path.abspath(__file__))
        _d = _os.path.join(_b, "phase23")
        _have = sorted(_os.listdir(_d)) if _os.path.isdir(_d) else "المجلد غير موجود"
        _logger.error(f"Phase 2.3 engine unavailable: {type(_e).__name__}: {str(_e)[:120]} | "
                      f"phase23 = {_have} | phase22 = "
                      f"{sorted(_os.listdir(_os.path.join(_b,'phase22'))) if _os.path.isdir(_os.path.join(_b,'phase22')) else 'مفقود'} | "
                      f"phase21 = {sorted(_os.listdir(_os.path.join(_b,'phase21'))) if _os.path.isdir(_os.path.join(_b,'phase21')) else 'مفقود'}")
        return None, None


_P23_LAST_ERROR = {"msg": ""}


def _p23_diagnostic():
    """نص تشخيصي قصير يظهر للمالك في الواجهة بدل رسالة عامة."""
    import os as _os
    base = _os.path.dirname(_os.path.abspath(__file__))
    parts = []
    for d in ("phase21", "phase22", "phase23"):
        p = _os.path.join(base, d)
        if _os.path.isdir(p):
            files = sorted(f for f in _os.listdir(p) if f.endswith(".py") and not f.startswith("test_"))
            parts.append(f"{d}: {len(files)} ملف" + (f" ({', '.join(files)})" if len(files) < 8 else ""))
        else:
            parts.append(f"{d}: المجلد مفقود")
    bundle = _os.path.exists(_os.path.join(base, "nabbah_engines.py"))
    if bundle:
        parts = ["المجلدات: غير مطلوبة للتشغيل"]
    parts.append("nabbah_engines.py: " + ("موجود" if bundle else "مفقود — ارفعه بجانب main.py"))
    err = _P23_LAST_ERROR.get("msg") or "—"
    return f"سبب العطل: {err} | {' · '.join(parts)}"


def _load_p23_mod(name):
    _load_phase23()
    try:
        import importlib
        try:
            return importlib.import_module(name)
        except ModuleNotFoundError:
            import nabbah_engines  # noqa: F401
            return importlib.import_module(name)
    except Exception as _e:
        _P23_LAST_ERROR["msg"] = f"{type(_e).__name__}: {str(_e)[:120]}"
        _logger.error(f"Phase 2.3 module {name} unavailable: {_P23_LAST_ERROR['msg']}")
        return None



def _row_dict(e):
    return {"id": e.id, "branch_id": e.branch_id, "period": e.period, "sales": e.sales, "expenses": e.expenses,
            "invoices": e.invoices, "customers": e.customers, "repeat_customers": e.repeat_customers,
            "deposited": getattr(e, "deposited", 0), "margin": e.margin, "profit": e.profit,
            "created_at": e.created_at}


def _exec_scope(s, user, need="view"):
    """Auth + tenant + RBAC. Returns (company, role). Finance view (or owner) required."""
    _ensure_data_tables()
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    company = s.get(Company, user.company_id)
    role = get_user_role(s, user)
    if not company or (not check_permission(role, "finance", "view") and role != "owner"):
        raise HTTPException(403, "غير مصرّح")
    if company.is_active != 1:
        raise HTTPException(402, "شركتك قيد التفعيل")
    if need == "edit" and not check_permission(role, "decisions", "edit"):
        raise HTTPException(403, "غير مصرّح — تعديل القرارات للمالك فقط")
    return company, role


def _build_exec(s, company, period=None):
    ie, pa = _load_phase23()
    if ie is None:
        raise HTTPException(503, "محرّك الذكاء التنفيذي غير متاح — " + _p23_diagnostic())
    branches = s.exec(select(CompanyBranch).where(CompanyBranch.company_id == company.id,
                                                  CompanyBranch.is_active == 1)).all()
    ids = [b.id for b in branches]
    rows = []
    if ids:
        rows = [_row_dict(e) for e in s.exec(select(CompanyEntry).where(
            CompanyEntry.company_id == company.id, CompanyEntry.branch_id.in_(ids))
            .order_by(CompanyEntry.created_at.desc()).limit(5000)).all()]
    split = pa.split_by_period(rows, current_period=period)
    inv = {}
    me = s.exec(select(CompanyModuleEntry).where(CompanyModuleEntry.company_id == company.id,
                                                 CompanyModuleEntry.module == "inventory")
                .order_by(CompanyModuleEntry.created_at.desc())).first()
    if me and me.data:
        try:
            d = json.loads(me.data)
            for k, v in d.items():
                if "قيمة المخزون" in k: inv["value"] = v
                if "تكلفة البضاعة" in k: inv["cogs"] = v
        except Exception:
            pass
    open_dec = len(s.exec(select(CompanyDecision).where(CompanyDecision.company_id == company.id,
                                                        CompanyDecision.status == "open")).all())
    today = datetime.now().strftime("%Y-%m-%d")
    overdue = len([a for a in s.exec(select(CompanyAction).where(CompanyAction.company_id == company.id)).all()
                   if a.status not in ACTION_TERMINAL and a.due_date and a.due_date < today])
    return ie.build_executive(
        all_rows=rows, current_rows=split["current_entries"], comparison_rows=split["comparison_entries"],
        branches=[{"id": b.id, "name": b.name, "target_sales": b.target_sales} for b in branches],
        period=split["current_period"], comparison_period=split["comparison_period"],
        period_gaps=split["data_gaps"], company_target_margin=company.target_margin or None,
        inventory=inv, currency=company.currency or "SAR", open_decisions=open_dec, overdue_actions=overdue)


@app.post("/db-sync")
def db_sync(user: User = Depends(get_current_user)):
    """للمالك: يشغّل مزامنة الأعمدة ويعرض النتيجة بدل إخفائها، ويتحقق من الجداول الجديدة."""
    from sqlalchemy import text as _text, inspect as _inspect
    with Session(engine) as s:
        company = s.get(Company, user.company_id) if user.company_id else None
        if not company or company.owner_id != user.id:
            raise HTTPException(403, "غير مصرّح")
    result = auto_sync_columns()
    try:
        SQLModel.metadata.create_all(engine)
    except Exception as e:
        result["create_all_error"] = f"{type(e).__name__}: {str(e)[:200]}"
    insp = _inspect(engine)
    tables = insp.get_table_names()
    out = {"columns_ok": result["added_or_ok"], "columns_failed": result["failed"],
           "tables": {t: (t in tables) for t in ("companydecision", "companyaction", "companyscenario")}}
    for t in ("companydecision", "companyaction", "companyscenario"):
        if t in tables:
            out[f"{t}_columns"] = sorted(c["name"] for c in insp.get_columns(t))
    missing = [c for c in ("branch_id", "metric_id", "outcome_status", "problem_type", "data_source")
               if t and "companydecision" in tables and c not in out.get("companydecision_columns", [])]
    out["decision_missing_columns"] = missing
    out["healthy"] = not result["failed"] and not missing and all(out["tables"].values())
    return out


@app.get("/engines-check")
def engines_check(user: User = Depends(get_current_user)):
    """تشخيص للمالك: هل وصلت ملفات المحركات، وما سبب فشل أي منها؟"""
    import importlib, os as _os
    with Session(engine) as s:
        company = s.get(Company, user.company_id) if user.company_id else None
        if not company or company.owner_id != user.id:
            raise HTTPException(403, "غير مصرّح")
    base = _os.path.dirname(_os.path.abspath(__file__))
    _load_phase23()
    out = {"folders": {}, "files": {}, "imports": {}}
    expected = {
        "phase21": ["nabbah_finance.py", "nabbah_trust.py"],
        "phase22": ["semantic_layer.py", "kpi_engine.py", "analysis_engines.py", "ai_gateway.py",
                     "platform_bridge.py", "period_aggregation.py", "legacy_adapters.py"],
        "phase23": ["intelligence_engine.py", "forecast_engine.py", "scenario_engine.py",
                     "decision_memory.py", "rule_catalog.py"],
    }
    for folder, files in expected.items():
        d = _os.path.join(base, folder)
        out["folders"][folder] = _os.path.isdir(d)
        out["files"][folder] = {f: _os.path.exists(_os.path.join(d, f)) for f in files}
    for mod in ("nabbah_finance", "nabbah_trust", "platform_bridge", "period_aggregation", "legacy_adapters",
                "intelligence_engine", "forecast_engine", "scenario_engine", "decision_memory", "rule_catalog"):
        try:
            importlib.import_module(mod)
            out["imports"][mod] = "ok"
        except Exception as e:
            out["imports"][mod] = f"{type(e).__name__}: {str(e)[:160]}"
    out["missing_files"] = [f"{k}/{f}" for k, v in out["files"].items() for f, ok in v.items() if not ok]
    out["failed_imports"] = [k for k, v in out["imports"].items() if v != "ok"]
    return out


# ═══════════════════════════════════════════════════════════
#  Phase 2.4 — Data Foundation: datasets, ingestion, periods, master data
#  يعيد استخدام: parse_csv/parse_excel · طبقة الموثوقية · RBAC · سجل التدقيق
# ═══════════════════════════════════════════════════════════
DATA_ROLES_VIEW = ("owner", "manager", "accountant")
DATA_ROLES_EDIT = ("owner", "accountant")
MAX_STAGING_ROWS = 50000     # كان 5000. الأكبر من ذلك يُقسَّم لعدة ملفات (الرسالة توضّح العدد)


def _load_p24(name):
    """يحمّل محركات 2.4 من المجلد أو من حزمة nabbah_engines."""
    try:
        import sys as _sys, os as _os, importlib
        for _d in ("phase21", "phase22", "phase23", "phase24", "phase25", "phase26", "phase27", "phase28", "phase29", "phase210", "phase211", "phase30", "phase31", "phase32", "phase33", "phase34", "phase35", "phase37", "phase38", "phase39", "phase310"):
            _p = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), _d)
            if _p not in _sys.path:
                _sys.path.insert(0, _p)
        try:
            return importlib.import_module(name)
        except ModuleNotFoundError:
            import nabbah_engines  # noqa: F401
            return importlib.import_module(name)
    except Exception as _e:
        _P23_LAST_ERROR["msg"] = f"{type(_e).__name__}: {str(_e)[:120]}"
        _logger.error(f"Phase 2.4 module {name} unavailable: {_P23_LAST_ERROR['msg']}")
        return None


def _data_scope(s, user, need="view"):
    _ensure_data_tables()
    """نطاق الشركة + الصلاحيات لبيانات المؤسسة."""
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    company = s.get(Company, user.company_id)
    role = get_user_role(s, user) if company else None
    if not company or role not in DATA_ROLES_VIEW:
        raise HTTPException(403, "غير مصرّح")
    if company.is_active != 1:
        raise HTTPException(402, "شركتك قيد التفعيل")
    if need == "edit" and role not in DATA_ROLES_EDIT:
        raise HTTPException(403, "غير مصرّح — رفع البيانات للمالك والمحاسب")
    return company, role


def _branch_lookup(s, company_id):
    rows = s.exec(select(CompanyBranch).where(CompanyBranch.company_id == company_id,
                                              CompanyBranch.is_active == 1)).all()
    return {b.name: b.id for b in rows}, {b.id: b.name for b in rows}


def _dataset_json(d, role=None):
    j = {"id": d.id, "dataset_type": d.dataset_type, "source_file": d.source_file, "period": d.period,
         "branches": d.branches, "total_rows": d.total_rows, "valid_rows": d.valid_rows,
         "rejected_rows": d.rejected_rows, "quality_score": d.quality_score, "quality_gate": d.quality_gate,
         "status": d.status, "uploaded_by": d.uploaded_by,
         "created_at": d.created_at.isoformat() if d.created_at else None,
         "imported_at": d.imported_at.isoformat() if d.imported_at else None}
    if role not in DATA_ROLES_EDIT:
        j["uploaded_by"] = ""      # لا نكشف من رفع لغير المصرّح لهم
    return j


def _read_any_table(data, filename, ing):
    """يقرأ CSV/Excel بشمولية: كل الأوراق، ويختار صف العناوين الحقيقي (قد تسبقه عناوين تقرير)
    بمطابقة محرك الاستيعاب لكل الأنواع، ويلتقط السنة من العنوان أو اسم الملف."""
    name = (filename or "").lower()
    sheets = []
    if name.endswith(".csv"):
        for enc in ("utf-8-sig", "utf-8", "cp1256", "latin-1"):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise HTTPException(400, "تعذّر قراءة ترميز الملف — احفظه بصيغة UTF-8")
        sample = text[:5000]
        delim = ";" if sample.count(";") > sample.count(",") else ("\t" if sample.count("\t") > sample.count(",") else ",")
        sheets.append(("csv", [r for r in csv.reader(io.StringIO(text), delimiter=delim)]))
    elif name.endswith((".xlsx", ".xlsm")):
        try:
            import openpyxl
        except ImportError:
            raise HTTPException(400, "دعم Excel غير متوفّر — احفظ الملف كـ CSV ثم ارفعه")
        try:
            wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
            for ws in wb.worksheets:
                sheets.append((ws.title, [list(r) for r in ws.iter_rows(values_only=True)]))
        except Exception as e:
            _logger.error(f"excel read failed: {type(e).__name__}: {str(e)[:150]}")
            raise HTTPException(400, "تعذّر قراءة ملف Excel. تأكد أنه غير محمي بكلمة مرور وأنه .xlsx")
    else:
        raise HTTPException(400, "الصيغ المدعومة: CSV أو Excel (.csv .xlsx .xlsm). ملفات .xls القديمة: احفظها كـ .xlsx")
    best = None
    for title, all_rows in sheets:
        for i, row in enumerate(all_rows[:15]):
            cells = ["" if c is None else str(c).strip() for c in row]
            if sum(1 for c in cells if c) < 2:
                continue
            score = max((len(ing.map_columns(cells, t)["mapping"]) for t in ing.DATASET_TYPES), default=0)
            if best is None or score > best[0]:
                best = (score, title, i, all_rows)
    if not best:
        return [], [], None, None
    _, title, hidx, all_rows = best
    headers = ["" if c is None else str(c).strip() for c in all_rows[hidx]]
    rows = [r for r in all_rows[hidx + 1:] if any(c is not None and str(c).strip() != "" for c in r)]
    above = " ".join(str(c) for r in all_rows[:hidx] for c in r if c is not None)
    year_hint = ing.year_hint_from(above, title, filename) if hasattr(ing, "year_hint_from") else None
    return headers, rows, year_hint, title


@app.get("/company/datasets")
def company_datasets(user: User = Depends(get_current_user), dataset_type: str = "", status: str = ""):
    with Session(engine) as s:
        company, role = _data_scope(s, user)
        q = select(CompanyDataset).where(CompanyDataset.company_id == company.id)
        if dataset_type:
            q = q.where(CompanyDataset.dataset_type == dataset_type)
        if status:
            q = q.where(CompanyDataset.status == status)
        try:
            items = s.exec(q.order_by(CompanyDataset.created_at.desc()).limit(200)).all()
        except Exception as e:
            _logger.error(f"datasets list failed: {type(e).__name__}: {str(e)[:200]}")
            raise HTTPException(503, "جداول البيانات غير جاهزة بعد. أعد تحميل الصفحة بعد دقيقة، "
                                     "وإن استمرت المشكلة شغّل /db-sync.")
        cm = _load_p24("canonical_model")
        return {"datasets": [_dataset_json(d, role) for d in items], "count": len(items),
                "types": ({k: {"ar": v["ar"], "en": v["en"]} for k, v in cm.ENTITIES.items()} if cm else {}),
                "can_upload": role in DATA_ROLES_EDIT}


@app.post("/company/datasets/preview")
async def company_dataset_preview(file: UploadFile = File(...), dataset_type: str = Form(...),
                                  user: User = Depends(get_current_user)):
    """يقرأ الملف، يطابق الأعمدة، يتحقق من الصفوف، ويخزّنها للمراجعة — بدون استيراد."""
    ing, cm = _load_p24("ingestion"), _load_p24("canonical_model")
    if ing is None or cm is None:
        raise HTTPException(503, "محرّك استيعاب البيانات غير متاح — " + _p23_diagnostic())
    if dataset_type != "auto" and dataset_type not in cm.DATASET_TYPES:
        raise HTTPException(422, "نوع بيانات غير مدعوم")
    with Session(engine) as s:
        company, role = _data_scope(s, user, "edit")
        data = await file.read()
        try:
            headers, rows, year_hint, sheet = _read_any_table(data, file.filename, ing)
        except HTTPException:
            raise
        except Exception as e:
            _logger.error(f"dataset preview read failed: {type(e).__name__}: {str(e)[:150]}")
            raise HTTPException(400, "تعذّر قراءة الملف. تأكد أنه CSV أو Excel صالح.")
        if not headers or not rows:
            raise HTTPException(422, "الملف فارغ أو لم نجد فيه صف عناوين وبيانات تحته")
        if len(rows) > MAX_STAGING_ROWS:
            parts = -(-len(rows) // MAX_STAGING_ROWS)
            raise HTTPException(422, f"الملف فيه {len(rows):,} صفاً والحد {MAX_STAGING_ROWS:,} في الرفعة الواحدة — "
                                     f"قسّمه إلى {parts} ملفات وارفعها تباعاً (لن يتكرر شيء: المكرر يُكشف تلقائياً)")
        names, ids = _branch_lookup(s, company.id)
        single_branch = next(iter(ids)) if len(ids) == 1 else None
        # كشف نوع الملف تلقائياً — ومنع رفع ملف بنوع خاطئ يرفض كل صفوفه
        ranking = ing.detect_type(headers, branch_default=bool(single_branch)) if hasattr(ing, "detect_type") else []
        chosen_by_user = dataset_type != "auto"
        if not chosen_by_user:
            if ranking and ranking[0].get("needs_branch"):
                raise HTTPException(422, {
                    "message_ar": f"هذا ملف «{ranking[0]['ar']}» لكن ينقصه عمود «الفرع»، ولشركتك أكثر من فرع. "
                                  f"أضف عموداً باسم «الفرع» فيه اسم الفرع كما هو مسجّل في نبّاه ({'، '.join(list(names)[:5])}).",
                    "message_en": f"This is a {ranking[0]['en']} file but it has no Branch column and your company has "
                                  "several branches. Add a Branch column.", "suggestions": ranking[:3]})
            best = next((x for x in ranking if x["complete"]), None)
            if not best:
                top = ranking[0] if ranking else {}
                raise HTTPException(422, {
                    "message_ar": "لم نتعرف على نوع الملف. أقرب نوع: «" + top.get("ar", "—") + "» وتنقصه الأعمدة: "
                                  + "، ".join(top.get("missing_required", [])) + ". اختر النوع يدوياً أو أضف الأعمدة الناقصة.",
                    "message_en": "Could not recognise the file type. Closest: " + top.get("en", "—") + " — missing: "
                                  + ", ".join(top.get("missing_required", [])),
                    "suggestions": ranking[:3]})
            dataset_type = best["type"]
        mapped = ing.map_columns(headers, dataset_type)
        missing_now = [f for f in mapped["missing_required"] if not (f == "branch_id" and single_branch)]
        if chosen_by_user and missing_now:
            better = next((x for x in ranking if x["complete"] and x["type"] != dataset_type), None)
            if better:
                raise HTTPException(422, {
                    "message_ar": f"هذا الملف يبدو «{better['ar']}» وليس «{cm.ENTITIES[dataset_type]['ar']}» "
                                  f"(تنقصه: {', '.join(missing_now)}). اختر «{better['ar']}» أو «تلقائي» وأعد الرفع.",
                    "message_en": f"This file looks like {better['en']}, not {cm.ENTITIES[dataset_type]['en']}. "
                                  f"Choose {better['en']} or Auto and upload again.",
                    "suggested_type": better["type"], "suggestions": ranking[:3]})
        try:
            validation = ing.validate_rows(rows, mapped["mapping"], dataset_type, branch_names=names,
                                           headers=headers, default_branch_id=single_branch, year_hint=year_hint)
        except TypeError:     # محرك أقدم بلا الوسائط الجديدة
            validation = ing.validate_rows(rows, mapped["mapping"], dataset_type, branch_names=names)
        quality = ing.assess_quality(validation, dataset_type)
        periods = sorted({r.get("period") or (r.get("date") or "")[:7] for r in validation["valid"] if r.get("period") or r.get("date")})
        branches = sorted({ids.get(r.get("branch_id"), "") for r in validation["valid"] if r.get("branch_id")})
        d = CompanyDataset(company_id=company.id, dataset_type=dataset_type, source_file=(file.filename or "")[:200],
                           period=", ".join(periods[:6]), branches=", ".join(b for b in branches if b)[:200],
                           total_rows=validation["total"], valid_rows=validation["valid_count"],
                           rejected_rows=validation["rejected_count"], quality_score=quality["overall_score"],
                           quality_gate=quality["gate"], status="validated",
                           mapping_json=json.dumps({str(k): v for k, v in mapped["mapping"].items()}, ensure_ascii=False),
                           staging_json=json.dumps(validation["valid"], ensure_ascii=False, default=str),
                           errors_json=json.dumps(validation["rejected"][:200], ensure_ascii=False, default=str),
                           uploaded_by=user.name or user.email)
        s.add(d); s.commit(); s.refresh(d)
        log_audit(company.id, user.id, user.name, "dataset_preview", f"dataset:{d.id}",
                  f"type={dataset_type} rows={validation['total']} valid={validation['valid_count']}")
        return {"dataset": _dataset_json(d, role), "headers": headers,
                "mapping": {headers[i]: f for i, f in mapped["mapping"].items() if i < len(headers)},
                "unmapped": mapped["unmapped"], "missing_required": missing_now,
                "fields": mapped["fields"], "required": mapped["required"],
                "sample": validation["valid"][:10], "rejected_sample": validation["rejected"][:20],
                "quality": quality, "detected_type": dataset_type, "auto_detected": not chosen_by_user,
                "detected_label": cm.ENTITIES[dataset_type]["ar"],
                "default_branch_used": bool(single_branch and "branch_id" in mapped["missing_required"]),
                "warnings": validation.get("warnings", [])[:20], "warning_count": validation.get("warning_count", 0),
                "year_hint": year_hint, "sheet": sheet, "skipped_totals": len(validation.get("skipped_totals", [])),
                "extra_columns": validation.get("extra_columns", [])}


@app.post("/company/datasets/{dataset_id}/validate")
def company_dataset_validate(dataset_id: int, data: dict, user: User = Depends(get_current_user)):
    """إعادة التحقق بعد تعديل ربط الأعمدة يدوياً (بدون رفع الملف مرة أخرى)."""
    ing = _load_p24("ingestion")
    if ing is None:
        raise HTTPException(503, "محرّك استيعاب البيانات غير متاح")
    with Session(engine) as s:
        company, role = _data_scope(s, user, "edit")
        d = s.get(CompanyDataset, dataset_id)
        if not d or d.company_id != company.id:
            raise HTTPException(404, "الملف غير موجود")
        if d.status == "imported":
            raise HTTPException(409, "تم اعتماد هذه البيانات ولا يمكن تعديل ربطها")
        overrides = data.get("mapping") or {}
        if not isinstance(overrides, dict):
            raise HTTPException(422, "ربط الأعمدة غير صالح")
        mapping = {int(k): v for k, v in json.loads(d.mapping_json or "{}").items()}
        for k, v in overrides.items():
            try:
                mapping[int(k)] = str(v)
            except (TypeError, ValueError):
                raise HTTPException(422, "مفتاح عمود غير صالح")
        d.mapping_json = json.dumps({str(k): v for k, v in mapping.items()}, ensure_ascii=False)
        s.add(d); s.commit()
        log_audit(company.id, user.id, user.name, "dataset_mapping", f"dataset:{dataset_id}", str(overrides)[:200])
        return {"ok": True, "mapping": {str(k): v for k, v in mapping.items()},
                "note": "أعد رفع الملف لتطبيق ربط مختلف على الصفوف المرفوضة."}


# مفاتيح البيانات الأساسية: إعادة رفع نفس الموظف/المنتج/المورد/العميل/القسم تُحدّثه ولا تكرره
_MASTER_KEYS = {"CompanyEmployee": ("employee_code",), "CompanyProduct": ("sku",), "CompanySupplier": ("name",),
                "CompanyCustomer": ("name",), "CompanyDepartment": ("name",)}

_IMPORT_TARGETS = {
    "sale": ("CompanySale", ("branch_id", "date", "reference", "channel", "product_sku", "category", "quantity",
                              "gross_sales", "discounts", "returns", "net_sales", "vat", "payment_method",
                              "promotion", "customer_name")),
    "purchase": ("CompanyPurchase", ("branch_id", "date", "supplier_name", "reference", "product_sku", "category",
                                      "quantity", "unit_cost", "total_cost", "status", "vat", "expected_date",
                                      "received_date", "received_qty", "rejected_qty")),
    "inventory": ("CompanyInventory", ("branch_id", "product_sku", "period", "opening_qty", "opening_value",
                                        "purchases_qty", "sold_qty", "adjustments_qty", "closing_qty", "closing_value")),
    "cash_movement": ("CompanyCashMovement", ("branch_id", "date", "movement_type", "category", "amount",
                                               "direction", "reference", "source", "period", "account",
                                               "counterparty", "balance")),
    "receivable": ("CompanyReceivable", ("branch_id", "customer_name", "reference", "invoice_date", "due_date",
                                         "amount", "paid_amount", "paid_date")),
    "employee": ("CompanyEmployee", ("branch_id", "department_id", "employee_code", "name", "role",
                                      "employment_status", "hire_date", "termination_date", "monthly_cost",
                                      "email", "phone", "employment_type", "manager", "termination_type",
                                      "basic_salary", "allowances", "benefits", "performance_rating",
                                      "last_promotion_date", "training_hours", "absence_days", "overtime_hours",
                                      "critical_role", "successors")),
    "expense": ("CompanyExpense", ("branch_id", "date", "amount", "category", "description", "vendor")),
    "tax_invoice": ("CompanyTaxInvoice", ("branch_id", "invoice_number", "issue_date", "invoice_type", "buyer_name", "buyer_vat",
                                          "taxable_amount", "vat_amount", "total_amount", "currency", "original_invoice", "zatca_status")),
    "operation_order": ("CompanyOpsOrder", ("branch_id", "department_id", "reference", "date", "service", "status",
                                            "created_time", "ready_time", "delivered_time", "due_time", "items",
                                            "accurate", "defect_type", "rework")),
    "process_event": ("CompanyOpsEvent", ("branch_id", "department_id", "reference", "stage", "start_time", "end_time")),
    "operational_issue": ("CompanyOpsIssue", ("branch_id", "department_id", "title", "opened_time", "resolved_time",
                                              "severity", "status", "owner", "root_cause", "impact", "sla_hours")),
    "job_opening": ("CompanyJobOpening", ("branch_id", "department_id", "title", "opened_date", "filled_date", "status",
                                          "applicants", "interviews", "offers", "hires", "hiring_cost")),
    "product": ("CompanyProduct", ("sku", "name", "category", "unit", "cost", "selling_price", "active")),
    "supplier": ("CompanySupplier", ("name", "supplier_code", "category", "payment_terms", "contact_person", "email",
                                      "phone", "city", "tax_number", "active")),
    "customer": ("CompanyCustomer", ("name", "customer_code", "email", "phone", "city", "segment", "customer_type",
                                      "tax_number", "credit_limit", "branch_id", "active", "notes")),
    "department": ("CompanyDepartment", ("name", "branch_id", "code", "active")),
}


@app.post("/company/datasets/{dataset_id}/import")
def company_dataset_import(dataset_id: int, user: User = Depends(get_current_user)):
    """يعتمد الصفوف الصالحة فقط. لا يُستورد أي صف مرفوض إطلاقاً."""
    pm = _load_p24("period_model")
    with Session(engine) as s:
        company, role = _data_scope(s, user, "edit")
        d = s.get(CompanyDataset, dataset_id)
        if not d or d.company_id != company.id:
            raise HTTPException(404, "الملف غير موجود")
        if d.status == "imported":
            raise HTTPException(409, "تم اعتماد هذه البيانات مسبقاً")
        if d.quality_gate == "BLOCK":
            raise HTTPException(422, {"message_ar": "جودة البيانات غير كافية للاعتماد — صحّح الأخطاء وأعد الرفع",
                                      "message_en": "Data quality too low to import — fix the errors and re-upload",
                                      "quality_score": d.quality_score})
        target = _IMPORT_TARGETS.get(d.dataset_type)
        if not target:
            raise HTTPException(422, "نوع بيانات غير مدعوم للاعتماد")
        model = globals()[target[0]]
        fields = target[1]
        rows = json.loads(d.staging_json or "[]")
        created = updated = 0
        _depts = None
        for r in rows:
            payload = {f: r.get(f) for f in fields if f in r}
            payload["company_id"] = company.id
            extra = dict(r.get("extra") or {})
            if model in (CompanyEmployee, CompanyJobOpening, CompanyOpsOrder, CompanyOpsEvent, CompanyOpsIssue) \
                    and payload.get("department_id") not in (None, "") \
                    and not str(payload["department_id"]).isdigit():
                if _depts is None:
                    _depts = {x.name.strip(): x.id for x in s.exec(select(CompanyDepartment).where(
                        CompanyDepartment.company_id == company.id)).all()}
                name_ = str(payload["department_id"]).strip()
                payload["department_id"] = _depts.get(name_)
                if payload["department_id"] is None:
                    extra["department"] = name_          # لا نضع نصاً في عمود رقمي — نحفظه ولا نفقده
            elif payload.get("department_id") not in (None, ""):
                payload["department_id"] = int(payload["department_id"])
            if hasattr(model, "extra_json") and extra:
                payload["extra_json"] = json.dumps(extra, ensure_ascii=False, default=str)[:20000]
            if hasattr(model, "dataset_id"):
                payload["dataset_id"] = d.id
            if hasattr(model, "source_row"):
                payload["source_row"] = r.get("_row")
            if hasattr(model, "period") and not payload.get("period") and r.get("date") and pm:
                payload["period"] = pm.period_key(r["date"], "month") or ""
            key = _MASTER_KEYS.get(model.__name__)
            existing = None
            if key and all(payload.get(k) not in (None, "") for k in key):
                q = select(model).where(model.company_id == company.id)
                for k in key:
                    q = q.where(getattr(model, k) == payload[k])
                existing = s.exec(q).first()
            if existing is not None:         # بيانات أساسية: تُحدَّث ولا تُكرَّر عند إعادة الرفع
                for k, v in payload.items():
                    if k != "company_id" and v not in (None, ""):
                        setattr(existing, k, v)
                s.add(existing); updated += 1
                continue
            s.add(model(**payload)); created += 1
            if created % 2000 == 0:          # دفعات: ملفات كبيرة بلا ضغط على الذاكرة
                s.flush()
        d.status = "imported" if d.rejected_rows == 0 else "partial"
        d.imported_at = datetime.now()
        d.staging_json = "[]"          # لا نحتفظ بالنسخة المؤقتة بعد الاعتماد
        s.add(d); s.commit()
        log_audit(company.id, user.id, user.name, "dataset_import", f"dataset:{dataset_id}",
                  f"type={d.dataset_type} imported={created} updated={updated} rejected={d.rejected_rows} quality={d.quality_score}")
        return {"ok": True, "imported": created, "updated": updated, "rejected": d.rejected_rows, "status": d.status,
                "quality_score": d.quality_score, "quality_gate": d.quality_gate}


@app.get("/company/datasets/{dataset_id}")
def company_dataset_detail(dataset_id: int, user: User = Depends(get_current_user)):
    with Session(engine) as s:
        company, role = _data_scope(s, user)
        d = s.get(CompanyDataset, dataset_id)
        if not d or d.company_id != company.id:
            raise HTTPException(404, "الملف غير موجود")
        ing = _load_p24("ingestion")
        lineage = (ing.lineage(d.id, d.source_file, d.uploaded_by if role in DATA_ROLES_EDIT else "",
                               None, d.period, None) if ing else {})
        return {"dataset": _dataset_json(d, role),
                "mapping": json.loads(d.mapping_json or "{}"),
                "errors": json.loads(d.errors_json or "[]")[:100],
                "lineage": lineage}


@app.get("/company/periods")
def company_periods(user: User = Depends(get_current_user), grain: str = "month", source: str = "sale"):
    """الفترات المتاحة فعلياً + سياق المقارنة (بدون اختلاق فترات غير موجودة)."""
    pm = _load_p24("period_model")
    if pm is None:
        raise HTTPException(503, "محرّك الفترات غير متاح")
    if grain not in pm.GRAINS:
        raise HTTPException(422, "تدرّج زمني غير مدعوم")
    with Session(engine) as s:
        company, _ = _data_scope(s, user)
        keys = set()
        if source == "sale":
            for r in s.exec(select(CompanySale).where(CompanySale.company_id == company.id).limit(20000)).all():
                k = pm.period_key(r.date, grain)
                if k:
                    keys.add(k)
        else:
            for e in s.exec(select(CompanyEntry).where(CompanyEntry.company_id == company.id).limit(20000)).all():
                k = e.period if grain == "month" else None
                if k:
                    keys.add(k)
        return pm.build_comparison(keys, grain=grain)


@app.get("/company/master-data")
def company_master_data(user: User = Depends(get_current_user), entity: str = "product",
                        q: str = "", offset: int = 0, limit: int = 200):
    """قوائم البيانات الأساسية: المنتجات والموردون والعملاء والأقسام والموظفون — مع بحث وتصفّح."""
    from sqlalchemy import func as _f, or_ as _or
    with Session(engine) as s:
        company, role = _data_scope(s, user)
        models = {"product": CompanyProduct, "supplier": CompanySupplier, "customer": CompanyCustomer,
                  "department": CompanyDepartment, "employee": CompanyEmployee}
        model = models.get(entity)
        if not model:
            raise HTTPException(422, "كيان غير مدعوم")
        limit = max(1, min(int(limit or 200), 500))
        offset = max(0, int(offset or 0))
        base = select(model).where(model.company_id == company.id)
        cnt = select(_f.count()).select_from(model).where(model.company_id == company.id)
        if q.strip():
            like = f"%{q.strip()[:80]}%"
            cols = [getattr(model, c) for c in ("name", "email", "phone", "sku", "customer_code", "employee_code",
                                                 "supplier_code", "city") if hasattr(model, c)]
            cond = _or(*[c.ilike(like) for c in cols])
            base, cnt = base.where(cond), cnt.where(cond)
        total = s.exec(cnt).one()
        rows = s.exec(base.order_by(model.id).offset(offset).limit(limit)).all()
        out = []
        for r in rows:
            item = {c: getattr(r, c) for c in r.__fields__ if c not in ("company_id",)}
            if "extra_json" in item:
                try:
                    item["extra"] = json.loads(item.pop("extra_json") or "{}")
                except (TypeError, ValueError):
                    item["extra"] = {}
            if entity == "employee" and not can_see_sensitive_financials(role):
                item["monthly_cost"] = None     # حقل حسّاس: يخضع للقواعد القائمة
                item["restricted"] = True
            out.append(item)
        return {"entity": entity, "items": out, "count": len(out), "total": total, "offset": offset, "limit": limit}


@app.get("/company/metrics-registry")
def company_metrics_registry(user: User = Depends(get_current_user)):
    """التعريف المرجعي للمؤشرات — مصدر واحد لكل الوحدات."""
    mr = _load_p24("metric_registry")
    if mr is None:
        raise HTTPException(503, "سجل المؤشرات غير متاح")
    with Session(engine) as s:
        company, role = _data_scope(s, user)
    return {"metrics": mr.list_metrics(role=role), "conflicts": mr.conflicts()}


# ═══════════════════════════════════════════════════════════
#  Phase 2.5 — Sales Intelligence (يبني على بيانات 2.4 وإشارات 2.3)
# ═══════════════════════════════════════════════════════════
def _sales_scope(s, user):
    _ensure_data_tables()
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    company = s.get(Company, user.company_id)
    role = get_user_role(s, user) if company else None
    if not company or not (role == "owner" or check_permission(role, "sales", "view")
                           or check_permission(role, "finance", "view")):
        raise HTTPException(403, "غير مصرّح")
    if company.is_active != 1:
        raise HTTPException(402, "شركتك قيد التفعيل")
    return company, role


def _sales_rows(s, company_id, branch_ids=None):
    names = {b.id: b.name for b in s.exec(select(CompanyBranch).where(
        CompanyBranch.company_id == company_id)).all()}
    q = select(CompanySale).where(CompanySale.company_id == company_id)
    rows = []
    for r in s.exec(q.order_by(CompanySale.date.desc()).limit(20000)).all():
        if branch_ids and r.branch_id not in branch_ids:
            continue
        rows.append({"date": r.date, "period": r.period, "branch_id": r.branch_id,
                     "branch_name": names.get(r.branch_id) or (f"#{r.branch_id}" if r.branch_id else None),
                     "reference": r.reference, "channel": r.channel, "product_sku": r.product_sku,
                     "category": r.category, "quantity": r.quantity, "gross_sales": r.gross_sales,
                     "discounts": r.discounts, "returns": r.returns, "net_sales": r.net_sales,
                     "vat": r.vat, "payment_method": r.payment_method, "dataset_id": r.dataset_id,
                     "promotion": getattr(r, "promotion", "") or "", "customer_name": getattr(r, "customer_name", "") or ""})
    return rows


def _sales_quality(s, company_id):
    d = s.exec(select(CompanyDataset).where(CompanyDataset.company_id == company_id,
                                            CompanyDataset.dataset_type == "sale")
               .order_by(CompanyDataset.created_at.desc())).first()
    if not d:
        return {"status": "unknown", "gate": "QUALIFY", "reason_ar": "لا يوجد ملف مبيعات مرفوع"}
    return {"status": "pass" if d.quality_gate == "ALLOW" else ("warning" if d.quality_gate == "QUALIFY" else "fail"),
            "gate": d.quality_gate, "score": d.quality_score, "dataset_id": d.id,
            "source_file": d.source_file, "rejected_rows": d.rejected_rows}


def _sales_result(s, company, *, period=None, grain="month", branch_id=None,
                  channel="", category="", product=""):
    se = _load_p24("sales_engine")
    if se is None:
        raise HTTPException(503, "محرّك المبيعات غير متاح — " + _p23_diagnostic())
    all_rows = _sales_rows(s, company.id, [branch_id] if branch_id else None)
    flt = [r for r in all_rows
           if (not channel or (r.get("channel") or "") == channel)
           and (not category or (r.get("category") or "") == category)
           and (not product or (r.get("product_sku") or "") == product)]
    pm = _load_p24("period_model")
    cur_period = period
    if not cur_period and pm:
        keys = sorted({pm.period_key(r.get("date"), grain) for r in flt if pm.period_key(r.get("date"), grain)})
        cur_period = keys[-1] if keys else None
    rows = [r for r in flt if pm and pm.period_key(r.get("date"), grain) == cur_period] if cur_period else flt
    result = se.analyze_sales(rows, flt, period=cur_period, grain=grain,
                              currency=company.currency or "SAR", quality=_sales_quality(s, company.id))
    result["filters"] = {"period": cur_period, "grain": grain, "branch_id": branch_id,
                         "channel": channel, "category": category, "product": product}
    _bl = s.exec(select(CompanyBranch).where(CompanyBranch.company_id == company.id,
                                              CompanyBranch.is_active == 1)).all()
    result["options"] = {
        "branch_list": [{"id": b.id, "name": b.name} for b in _bl],
        "branches": sorted({r["branch_name"] for r in all_rows if r.get("branch_name")}),
        "channels": sorted({r["channel"] for r in all_rows if r.get("channel")}),
        "categories": sorted({r["category"] for r in all_rows if r.get("category")}),
        "products": sorted({r["product_sku"] for r in all_rows
                            if r.get("product_sku") and (not category or r.get("category") == category)})[:300],
        "periods": sorted({pm.period_key(r.get("date"), grain) for r in all_rows
                           if pm and pm.period_key(r.get("date"), grain)})[-24:] if pm else []}
    return result


@app.get("/company/sales-intelligence")
def company_sales_intelligence(user: User = Depends(get_current_user), period: str = "", grain: str = "month",
                               branch_id: Optional[int] = None, channel: str = "", category: str = "",
                               product: str = ""):
    with Session(engine) as s:
        company, role = _sales_scope(s, user)
        return _sales_result(s, company, period=period or None, grain=grain, branch_id=branch_id,
                             channel=channel, category=category, product=product)


@app.post("/company/sales/ai-insights")
def company_sales_ai_insights(data: dict, request: Request, user: User = Depends(get_current_user)):
    """AI يشرح أرقام محرك المبيعات المُتحقّقة فقط — لا يحسب ولا يخترع رقماً (المبدأ ١)."""
    _bid = data.get("branch_id")
    try:
        _bid = int(_bid) if _bid not in (None, "") else None
    except (TypeError, ValueError):
        raise HTTPException(400, "معرّف الفرع غير صالح")
    with Session(engine) as s:
        company, role = _sales_scope(s, user)
        res = _sales_result(s, company, period=data.get("period") or None, grain=data.get("grain") or "month",
                            branch_id=_bid, channel=str(data.get("channel") or ""),
                            category=str(data.get("category") or ""), product=str(data.get("product") or ""))
    if not res.get("has_data"):
        raise HTTPException(422, res.get("message_ar") or "لا توجد بيانات مبيعات")
    gw = _load_p24("ai_gateway")
    if gw is None:
        raise HTTPException(503, "بوابة الذكاء الاصطناعي غير متاحة — " + _p23_diagnostic())
    q = res.get("data_quality") or {}
    score = {"ALLOW": 90, "QUALIFY": 65, "BLOCK": 20}.get(q.get("gate"), 65)
    trust = {"overall_score": score, "status": q.get("status", "warning"),
             "has_critical_fail": q.get("gate") == "BLOCK",
             "main_causes": [{"explanation": e, "fix": ""} for s_ in res.get("signals", [])
                             if s_.get("type") == "data_quality" for e in s_.get("evidence", [])][:3]}
    ctx = {"period": res.get("period"), "grain": res.get("grain"), "currency": res.get("currency"),
           "filters": res.get("filters"),
           "verified_kpis": {k: {"current": v.get("current"), "previous": v.get("previous"),
                                 "change_pct": v.get("change_pct"), "unit": v.get("unit")}
                             for k, v in (res.get("kpis") or {}).items()},
           "signals": [{"type": x["type"], "name": x["name_ar"], "dimension": x.get("dimension"),
                        "evidence": x.get("evidence"), "suggested_action": x.get("suggested_action_ar")}
                       for x in res.get("signals", [])[:8]],
           "top_products": [{"product": p["key"], "net_sales": p["net_sales"]["value"], "growth_pct": p["growth_pct"]}
                            for p in (res.get("product_insights") or {}).get("top5", [])],
           "branches": [{"branch": b["key"], "net_sales": b["net_sales"]["value"], "growth_pct": b["growth_pct"]}
                        for b in (res.get("branches") or {}).get("items", [])[:10]],
           "data_completeness": res.get("completeness")}
    out = gw.request_ai_analysis(gw.GeminiProvider(company_gemini), ctx,
                                 "اشرح أداء المبيعات: الأدلة، ثم الأثر، ثم التوصية. لا تحسب أي رقم جديد.",
                                 trust_report=trust, lang=get_lang(request), company=company)
    log_audit(company.id, user.id, user.name, "sales_ai_insights", "sales", f"period={res.get('period')}")
    return {"period": res.get("period"), "ai": out}


@app.post("/company/sales/to-decision")
def company_sales_to_decision(data: dict, user: User = Depends(get_current_user)):
    """يحوّل إشارة مبيعات إلى قرار ومهمة — تُعاد الإشارة حسابياً في الخادم."""
    with Session(engine) as s:
        company, role = _exec_scope(s, user, need="edit")
        _bid = data.get("branch_id")
        try:
            _bid = int(_bid) if _bid not in (None, "") else None
        except (TypeError, ValueError):
            raise HTTPException(400, "معرّف الفرع غير صالح")
        res = _sales_result(s, company, period=data.get("period") or None,
                            grain=data.get("grain") or "month", branch_id=_bid,
                            channel=str(data.get("channel") or ""), category=str(data.get("category") or ""),
                            product=str(data.get("product") or ""))
        sig = next((x for x in res.get("signals", []) if x["id"] == str(data.get("signal_id") or "")), None)
        if not sig:
            raise HTTPException(404, "الإشارة غير موجودة أو لم تعد قائمة لهذه الفترة")
        impact = (sig.get("estimated_impact") or {}).get("value")
        d = CompanyDecision(
            company_id=company.id, title=str(data.get("title") or sig["name_ar"])[:200],
            detail=" · ".join(sig.get("evidence", []))[:1000], owner=str(data.get("owner") or "")[:100],
            due_date=str(data.get("due_date") or "")[:20], kpi=sig["metric_id"], status="open",
            baseline_sales=_company_total_sales(s, company.id),
            expected_impact=(f"{impact} {res['currency']} (تقديري)" if impact is not None else "غير قابل للتقدير")[:200],
            linked_to=f"sales_signal:{sig['id']}", rationale=sig.get("suggested_action_ar", "")[:500],
            metric_id=sig["metric_id"], baseline_value=sig.get("current_value"),
            expected_impact_value=impact, impact_status="expected", source_signal=sig["id"],
            problem_type=sig["code"], decision_type="sales", outcome_status="pending_measurement",
            created_by=user.name or user.email, data_source="companysale", updated_at=datetime.now())
        s.add(d); s.commit(); s.refresh(d)
        act = CompanyAction(company_id=company.id, decision_id=d.id, title=sig.get("suggested_action_ar", "")[:200],
                            owner=d.owner, priority="P1" if sig["severity"] in ("high", "critical") else "P2",
                            due_date=d.due_date, start_date=datetime.now().strftime("%Y-%m-%d"),
                            updated_at=datetime.now())
        s.add(act); s.commit(); s.refresh(act)
        log_audit(company.id, user.id, user.name, "decision_from_sales_signal", f"decision:{d.id}",
                  f"signal={sig['id']} code={sig['code']}")
        return {"ok": True, "decision_id": d.id, "action_ids": [act.id]}


# ═══════════════════════════════════════════════════════════
#  Phase 2.6 — Inventory Intelligence (يبني على بيانات 2.4 ومبيعات 2.5)
# ═══════════════════════════════════════════════════════════
def _inventory_scope(s, user, need="view"):
    _ensure_data_tables()
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    company = s.get(Company, user.company_id)
    role = get_user_role(s, user) if company else None
    if not company or not (role == "owner" or check_permission(role, "inventory", "view")
                           or check_permission(role, "finance", "view")):
        raise HTTPException(403, "غير مصرّح")
    if company.is_active != 1:
        raise HTTPException(402, "شركتك قيد التفعيل")
    if need == "edit" and not (role == "owner" or check_permission(role, "inventory", "edit") or role in DATA_ROLES_EDIT):
        raise HTTPException(403, "غير مصرّح — تعديل المخزون للمالك ومن لديه صلاحية المخزون")
    return company, role


def _int_or_none(v, label="الفرع"):
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        raise HTTPException(400, f"معرّف {label} غير صالح")


def _inventory_result(s, company, *, period=None, branch_id=None, category="", product="", status=""):
    ie = _load_p24("inventory_engine")
    if ie is None:
        raise HTTPException(503, "محرّك المخزون غير متاح — " + _p23_diagnostic())
    names = {b.id: b.name for b in s.exec(select(CompanyBranch).where(CompanyBranch.company_id == company.id)).all()}
    prods = {p.sku: {"name": p.name, "category": p.category, "cost": p.cost, "unit": p.unit}
             for p in s.exec(select(CompanyProduct).where(CompanyProduct.company_id == company.id)).all() if p.sku}
    inv = s.exec(select(CompanyInventory).where(CompanyInventory.company_id == company.id).limit(100000)).all()
    sales = _sales_rows(s, company.id, {branch_id} if branch_id else None)
    sale_cat = {}
    for r in sales:
        if r.get("product_sku") and r.get("category"):
            sale_cat.setdefault(r["product_sku"], r["category"])
    cat_of = lambda sku: (prods.get(sku) or {}).get("category") or sale_cat.get(sku) or "غير مصنّف"
    snaps_all = [{"period": r.period, "branch_id": r.branch_id,
                  "branch_name": names.get(r.branch_id) or (f"#{r.branch_id}" if r.branch_id else "—"),
                  "product_sku": r.product_sku, "opening_qty": r.opening_qty, "opening_value": r.opening_value,
                  "purchases_qty": r.purchases_qty, "sold_qty": r.sold_qty, "adjustments_qty": r.adjustments_qty,
                  "closing_qty": r.closing_qty, "closing_value": r.closing_value} for r in inv]
    flt = lambda x: ((not branch_id or x.get("branch_id") == branch_id)
                     and (not category or cat_of(x.get("product_sku")) == category)
                     and (not product or x.get("product_sku") == product))
    snaps = [x for x in snaps_all if flt(x)]
    sales_f = [r for r in sales if (not category or cat_of(r.get("product_sku")) == category)
               and (not product or r.get("product_sku") == product)]
    params = {}
    for pr in s.exec(select(CompanyInventoryParam).where(CompanyInventoryParam.company_id == company.id)).all():
        params[(pr.product_sku, names.get(pr.branch_id) if pr.branch_id else None)] = {
            "lead_time_days": pr.lead_time_days, "safety_stock": pr.safety_stock,
            "min_order_qty": pr.min_order_qty, "reorder_point": pr.reorder_point}
    res = ie.analyze_inventory(snaps, sales_f, prods, params, period=period, status_filter=status,
                               currency=getattr(company, "currency", None) or "SAR")
    res["filters"] = {"period": res.get("period"), "branch_id": branch_id, "category": category,
                      "product": product, "status": status}
    skus = sorted({x["product_sku"] for x in snaps_all if x.get("product_sku")})
    res["options"] = {"branch_list": [{"id": k, "name": v} for k, v in names.items()],
                      "categories": sorted({cat_of(k) for k in skus}),
                      "products": [{"sku": k, "name": (prods.get(k) or {}).get("name") or k} for k in skus
                                   if not category or cat_of(k) == category][:500],
                      "statuses": ["healthy", "watch", "low", "stockout", "slow", "obsolete"]}
    return res


@app.get("/company/inventory-intelligence")
def company_inventory_intelligence(user: User = Depends(get_current_user), period: str = "", branch_id: str = "",
                                   category: str = "", product: str = "", status: str = ""):
    with Session(engine) as s:
        company, role = _inventory_scope(s, user)
        res = _inventory_result(s, company, period=period or None, branch_id=_int_or_none(branch_id),
                                category=category, product=product, status=status)
        res["can_edit"] = role == "owner" or role in DATA_ROLES_EDIT or check_permission(role, "inventory", "edit")
        return res


@app.post("/company/inventory/manual")
def company_inventory_manual(data: dict, user: User = Depends(get_current_user)):
    """إدخال أو تحديث رصيد صنف يدوياً (بديل عن رفع ملف). لا يُنشئ بيانات ناقصة بصمت."""
    with Session(engine) as s:
        company, role = _inventory_scope(s, user, need="edit")
        period = str(data.get("period") or "").strip()[:7]
        sku = str(data.get("product_sku") or "").strip()[:100]
        bid = _int_or_none(data.get("branch_id"))
        if not re.match(r"^\d{4}-\d{2}$", period):
            raise HTTPException(422, "الفترة مطلوبة بصيغة YYYY-MM")
        if not sku:
            raise HTTPException(422, "المنتج مطلوب")
        if bid is None or (s.get(CompanyBranch, bid) or CompanyBranch(company_id=-1)).company_id != company.id:
            raise HTTPException(422, "اختر فرعاً من فروع شركتك")

        def num(k):
            v = data.get(k)
            if v in (None, ""):
                return None
            try:
                x = float(str(v).replace(",", ""))
            except ValueError:
                raise HTTPException(422, f"قيمة غير رقمية في {k}")
            if x < 0 and k != "adjustments_qty":
                raise HTTPException(422, f"قيمة سالبة غير مقبولة في {k}")
            return x
        qty = num("closing_qty")
        if qty is None:
            raise HTTPException(422, "الكمية الحالية مطلوبة")
        unit_cost, value = num("unit_cost"), num("closing_value")
        if value is None and unit_cost is not None:
            value = round(qty * unit_cost, 2)
        row = s.exec(select(CompanyInventory).where(CompanyInventory.company_id == company.id,
                                                    CompanyInventory.branch_id == bid,
                                                    CompanyInventory.product_sku == sku,
                                                    CompanyInventory.period == period)).first()
        created = row is None
        row = row or CompanyInventory(company_id=company.id, branch_id=bid, product_sku=sku, period=period)
        row.closing_qty, row.closing_value = qty, value
        for k in ("opening_qty", "purchases_qty", "sold_qty", "adjustments_qty"):
            v = num(k)
            if v is not None:
                setattr(row, k, v)
        s.add(row)
        if unit_cost is not None:
            pr = s.exec(select(CompanyProduct).where(CompanyProduct.company_id == company.id,
                                                     CompanyProduct.sku == sku)).first()
            if pr is None:
                s.add(CompanyProduct(company_id=company.id, sku=sku, name=str(data.get("name") or sku)[:200],
                                     category=str(data.get("category") or "")[:100], cost=unit_cost))
            elif pr.cost is None:
                pr.cost = unit_cost; s.add(pr)
        s.commit()
        log_audit(company.id, user.id, user.name, "inventory_manual_" + ("create" if created else "update"),
                  f"inventory:{sku}", f"period={period} branch={bid} qty={qty}")
        return {"ok": True, "created": created}


@app.get("/company/inventory/params")
def company_inventory_params(user: User = Depends(get_current_user)):
    with Session(engine) as s:
        company, role = _inventory_scope(s, user)
        rows = s.exec(select(CompanyInventoryParam).where(CompanyInventoryParam.company_id == company.id)).all()
        return {"items": [{"product_sku": r.product_sku, "branch_id": r.branch_id, "lead_time_days": r.lead_time_days,
                           "safety_stock": r.safety_stock, "min_order_qty": r.min_order_qty,
                           "reorder_point": r.reorder_point} for r in rows]}


@app.post("/company/inventory/params")
def company_inventory_params_save(data: dict, user: User = Depends(get_current_user)):
    """حفظ مدة التوريد ومخزون الأمان والحد الأدنى للطلب (ونقطة إعادة الطلب اليدوية إن رغبت) لصنف."""
    with Session(engine) as s:
        company, role = _inventory_scope(s, user, need="edit")
        sku = str(data.get("product_sku") or "").strip()[:100]
        if not sku:
            raise HTTPException(422, "المنتج مطلوب")
        bid = _int_or_none(data.get("branch_id"))
        if bid is not None and (s.get(CompanyBranch, bid) or CompanyBranch(company_id=-1)).company_id != company.id:
            raise HTTPException(422, "الفرع غير صالح")
        vals = {}
        for k in ("lead_time_days", "safety_stock", "min_order_qty", "reorder_point"):
            v = data.get(k)
            if v in (None, ""):
                vals[k] = None; continue
            try:
                vals[k] = float(str(v).replace(",", ""))
            except ValueError:
                raise HTTPException(422, f"قيمة غير رقمية في {k}")
            if vals[k] < 0:
                raise HTTPException(422, f"قيمة سالبة غير مقبولة في {k}")
        if all(v is None for v in vals.values()):
            raise HTTPException(422, "أدخل قيمة واحدة على الأقل")
        row = s.exec(select(CompanyInventoryParam).where(CompanyInventoryParam.company_id == company.id,
                                                         CompanyInventoryParam.product_sku == sku,
                                                         CompanyInventoryParam.branch_id == bid)).first()
        row = row or CompanyInventoryParam(company_id=company.id, product_sku=sku, branch_id=bid)
        for k, v in vals.items():
            setattr(row, k, v)
        row.updated_by, row.updated_at = (user.name or user.email)[:100], datetime.now()
        s.add(row); s.commit()
        log_audit(company.id, user.id, user.name, "inventory_params", f"inventory:{sku}", json.dumps(vals))
        return {"ok": True}


@app.post("/company/inventory/to-decision")
def company_inventory_to_decision(data: dict, user: User = Depends(get_current_user)):
    """يحوّل إشارة مخزون إلى قرار ومهمة — تُعاد الإشارة حسابياً في الخادم بنفس الفلاتر."""
    with Session(engine) as s:
        company, role = _exec_scope(s, user, need="edit")
        res = _inventory_result(s, company, period=data.get("period") or None,
                                branch_id=_int_or_none(data.get("branch_id")),
                                category=str(data.get("category") or ""), product=str(data.get("product") or ""))
        sig = next((x for x in res.get("signals", []) if x["id"] == str(data.get("signal_id") or "")), None)
        if not sig:
            raise HTTPException(404, "الإشارة غير موجودة أو لم تعد قائمة لهذه الفترة")
        impact = (sig.get("estimated_impact") or {}).get("value")
        metric = {"stockout_risk": "stockout_rate", "stockouts_now": "stockout_rate",
                  "excess_inventory": "dio", "high_turnover_category": "inventory_turnover"}.get(sig["code"], "inventory")
        d = CompanyDecision(
            company_id=company.id, title=str(data.get("title") or sig["name_ar"])[:200],
            detail=" · ".join(sig.get("evidence", []))[:1000], owner=str(data.get("owner") or "")[:100],
            due_date=str(data.get("due_date") or "")[:20], kpi=metric, status="open",
            baseline_sales=_company_total_sales(s, company.id),
            expected_impact=(f"{impact} {res['currency']} (تقديري)" if impact is not None else "غير قابل للتقدير")[:200],
            linked_to=f"inventory_signal:{sig['id']}", rationale=sig.get("suggested_action_ar", "")[:500],
            metric_id=metric, expected_impact_value=impact, impact_status="expected", source_signal=sig["id"],
            problem_type=sig["code"], decision_type="inventory", outcome_status="pending_measurement",
            created_by=user.name or user.email, data_source="companyinventory", updated_at=datetime.now())
        s.add(d); s.commit(); s.refresh(d)
        act = CompanyAction(company_id=company.id, decision_id=d.id, title=sig.get("suggested_action_ar", "")[:200],
                            owner=d.owner, priority="P1" if sig["severity"] in ("high", "critical") else "P2",
                            due_date=d.due_date, start_date=datetime.now().strftime("%Y-%m-%d"),
                            updated_at=datetime.now())
        s.add(act); s.commit(); s.refresh(act)
        log_audit(company.id, user.id, user.name, "decision_from_inventory_signal", f"decision:{d.id}",
                  f"signal={sig['id']} code={sig['code']}")
        return {"ok": True, "decision_id": d.id, "action_ids": [act.id]}


@app.post("/company/inventory/ai-insights")
def company_inventory_ai_insights(data: dict, request: Request, user: User = Depends(get_current_user)):
    """AI يشرح أرقام محرك المخزون المتحقَّق منها فقط — لا يحسب ولا يخترع رقماً."""
    with Session(engine) as s:
        company, role = _inventory_scope(s, user)
        res = _inventory_result(s, company, period=data.get("period") or None,
                                branch_id=_int_or_none(data.get("branch_id")),
                                category=str(data.get("category") or ""), product=str(data.get("product") or ""))
    if not res.get("has_data"):
        raise HTTPException(422, res.get("message_ar") or "لا توجد بيانات مخزون")
    gw = _load_p24("ai_gateway")
    if gw is None:
        raise HTTPException(503, "بوابة الذكاء الاصطناعي غير متاحة — " + _p23_diagnostic())
    dq = [x for x in res.get("signals", []) if x["type"] == "data_quality"]
    trust = {"overall_score": 85 if not dq else 65, "status": "pass" if not dq else "warning", "has_critical_fail": False,
             "main_causes": [{"explanation": e, "fix": ""} for x in dq for e in x.get("evidence", [])][:3]}
    ctx = {"period": res.get("period"), "currency": res.get("currency"), "filters": res.get("filters"),
           "verified_kpis": {k: {"current": v.get("current"), "previous": v.get("previous"), "unit": v.get("unit")}
                             for k, v in res.get("kpis", {}).items()},
           "status_counts": res.get("status_counts"), "by_branch": res.get("by_branch", [])[:10],
           "signals": [{"type": x["type"], "name": x["name_ar"], "dimension": x.get("dimension"),
                        "evidence": x.get("evidence")} for x in res.get("signals", [])[:8]],
           "rules": res.get("rules")}
    out = gw.request_ai_analysis(gw.GeminiProvider(company_gemini), ctx,
                                 "اشرح وضع المخزون: الأدلة، ثم الأثر، ثم التوصية. لا تحسب أي رقم جديد.",
                                 trust_report=trust, lang=get_lang(request), company=company)
    log_audit(company.id, user.id, user.name, "inventory_ai_insights", "inventory", f"period={res.get('period')}")
    return {"period": res.get("period"), "ai": out}


# ═══════════════════════════════════════════════════════════
#  Phase 2.7 — Purchases Intelligence (نفس مصدر البيانات: مركز البيانات → الجداول الموحّدة)
# ═══════════════════════════════════════════════════════════
def _purchases_scope(s, user):
    _ensure_data_tables()
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    company = s.get(Company, user.company_id)
    role = get_user_role(s, user) if company else None
    if not company or not (role == "owner" or check_permission(role, "procurement", "view")
                           or check_permission(role, "finance", "view")):
        raise HTTPException(403, "غير مصرّح")
    if company.is_active != 1:
        raise HTTPException(402, "شركتك قيد التفعيل")
    return company, role


def _purchases_result(s, company, *, period=None, grain="month", branch_id=None, category="", product="",
                      supplier="", status=""):
    pe = _load_p24("purchases_engine")
    if pe is None:
        raise HTTPException(503, "محرّك المشتريات غير متاح — " + _p23_diagnostic())
    names = {b.id: b.name for b in s.exec(select(CompanyBranch).where(CompanyBranch.company_id == company.id)).all()}
    rows_all = []
    for r in s.exec(select(CompanyPurchase).where(CompanyPurchase.company_id == company.id).limit(100000)).all():
        rows_all.append({"date": r.date, "branch_id": r.branch_id, "branch_name": names.get(r.branch_id) or "—",
                         "supplier_name": r.supplier_name or None, "reference": r.reference or None,
                         "product_sku": r.product_sku or None, "category": r.category or None, "quantity": r.quantity,
                         "unit_cost": r.unit_cost, "total_cost": r.total_cost, "status": r.status or None,
                         "expected_date": getattr(r, "expected_date", "") or None,
                         "received_date": getattr(r, "received_date", "") or None,
                         "received_qty": getattr(r, "received_qty", None), "rejected_qty": getattr(r, "rejected_qty", None)})
    f = lambda r: ((not branch_id or r.get("branch_id") == branch_id) and (not category or r.get("category") == category)
                   and (not product or r.get("product_sku") == product) and (not supplier or r.get("supplier_name") == supplier)
                   and (not status or (r.get("status") or "") == status))
    rows = [r for r in rows_all if f(r)]
    sales = [r for r in _sales_rows(s, company.id, {branch_id} if branch_id else None)
             if (not product or r.get("product_sku") == product)]
    inv = [{"period": r.period, "branch_name": names.get(r.branch_id) or "—", "product_sku": r.product_sku,
            "closing_value": r.closing_value}
           for r in s.exec(select(CompanyInventory).where(CompanyInventory.company_id == company.id).limit(100000)).all()
           if (not branch_id or r.branch_id == branch_id) and (not product or r.product_sku == product)]
    needs = []
    try:     # 2.6 → 2.7: أصناف تحتاج إعادة طلب تُعرض مع خيارات الموردين
        _inv = _inventory_result(s, company, branch_id=branch_id, product=product)
        needs = [{"product_sku": p["product_sku"], "branch": p["branch"], "status": p["status"]}
                 for p in (_inv.get("reorder") or []) if p["status"] in ("low", "stockout")]
    except HTTPException:
        pass
    res = pe.analyze_purchases(rows, period=period, grain=grain, sales_rows=sales, inventory_snaps=inv,
                               reorder_needs=needs, currency=getattr(company, "currency", None) or "SAR")
    res["filters"] = {"period": res.get("period"), "grain": grain, "branch_id": branch_id, "category": category,
                      "product": product, "supplier": supplier, "status": status}
    res["options"] = {"branch_list": [{"id": k, "name": v} for k, v in names.items()],
                      "categories": sorted({r["category"] for r in rows_all if r.get("category")}),
                      "products": sorted({r["product_sku"] for r in rows_all if r.get("product_sku")
                                          and (not category or r.get("category") == category)})[:500],
                      "suppliers": sorted({r["supplier_name"] for r in rows_all if r.get("supplier_name")})[:500],
                      "statuses": sorted({r["status"] for r in rows_all if r.get("status")})}
    return res


@app.get("/company/purchases-intelligence")
def company_purchases_intelligence(user: User = Depends(get_current_user), period: str = "", grain: str = "month",
                                   branch_id: str = "", category: str = "", product: str = "", supplier: str = "",
                                   status: str = ""):
    if grain not in ("day", "week", "month", "quarter", "year"):
        raise HTTPException(422, "التجميع غير صالح")
    with Session(engine) as s:
        company, role = _purchases_scope(s, user)
        return _purchases_result(s, company, period=period or None, grain=grain, branch_id=_int_or_none(branch_id),
                                 category=category, product=product, supplier=supplier, status=status)


@app.post("/company/purchases/to-decision")
def company_purchases_to_decision(data: dict, user: User = Depends(get_current_user)):
    """يحوّل إشارة مشتريات إلى قرار ومهمة — تُعاد الإشارة حسابياً في الخادم بنفس الفلاتر."""
    with Session(engine) as s:
        company, role = _exec_scope(s, user, need="edit")
        res = _purchases_result(s, company, period=data.get("period") or None, grain=data.get("grain") or "month",
                                branch_id=_int_or_none(data.get("branch_id")), category=str(data.get("category") or ""),
                                product=str(data.get("product") or ""), supplier=str(data.get("supplier") or ""),
                                status=str(data.get("status") or ""))
        sig = next((x for x in res.get("signals", []) if x["id"] == str(data.get("signal_id") or "")), None)
        if not sig:
            raise HTTPException(404, "الإشارة غير موجودة أو لم تعد قائمة لهذه الفترة")
        impact = (sig.get("estimated_impact") or {}).get("value")
        d = CompanyDecision(
            company_id=company.id, title=str(data.get("title") or sig["name_ar"])[:200],
            detail=" · ".join(sig.get("evidence", []))[:1000], owner=str(data.get("owner") or "")[:100],
            due_date=str(data.get("due_date") or "")[:20], kpi=sig.get("metric_id") or "purchase_spend", status="open",
            baseline_sales=_company_total_sales(s, company.id),
            expected_impact=(f"{impact} {res['currency']} (تقديري)" if impact is not None else "غير قابل للتقدير")[:200],
            linked_to=f"purchases_signal:{sig['id']}", rationale=sig.get("suggested_action_ar", "")[:500],
            metric_id=sig.get("metric_id") or "purchase_spend", expected_impact_value=impact, impact_status="expected",
            source_signal=sig["id"], problem_type=sig["code"], decision_type="purchases",
            outcome_status="pending_measurement", created_by=user.name or user.email,
            data_source="companypurchase", updated_at=datetime.now())
        s.add(d); s.commit(); s.refresh(d)
        act = CompanyAction(company_id=company.id, decision_id=d.id, title=sig.get("suggested_action_ar", "")[:200],
                            owner=d.owner, priority="P1" if sig["severity"] in ("high", "critical") else "P2",
                            due_date=d.due_date, start_date=datetime.now().strftime("%Y-%m-%d"), updated_at=datetime.now())
        s.add(act); s.commit(); s.refresh(act)
        log_audit(company.id, user.id, user.name, "decision_from_purchases_signal", f"decision:{d.id}",
                  f"signal={sig['id']} code={sig['code']}")
        return {"ok": True, "decision_id": d.id, "action_ids": [act.id]}


@app.post("/company/purchases/ai-insights")
def company_purchases_ai_insights(data: dict, request: Request, user: User = Depends(get_current_user)):
    """AI يشرح أرقام محرك المشتريات المتحقَّق منها فقط — لا يحسب ولا يخترع رقماً."""
    with Session(engine) as s:
        company, role = _purchases_scope(s, user)
        res = _purchases_result(s, company, period=data.get("period") or None, grain=data.get("grain") or "month",
                                branch_id=_int_or_none(data.get("branch_id")), category=str(data.get("category") or ""),
                                product=str(data.get("product") or ""), supplier=str(data.get("supplier") or ""))
    if not res.get("has_data"):
        raise HTTPException(422, res.get("message_ar") or "لا توجد بيانات مشتريات")
    gw = _load_p24("ai_gateway")
    if gw is None:
        raise HTTPException(503, "بوابة الذكاء الاصطناعي غير متاحة — " + _p23_diagnostic())
    dq = [x for x in res.get("signals", []) if x["type"] == "data_quality"]
    trust = {"overall_score": 85 if not dq else 65, "status": "pass" if not dq else "warning", "has_critical_fail": False,
             "main_causes": [{"explanation": e, "fix": ""} for x in dq for e in x.get("evidence", [])][:3]}
    ctx = {"period": res.get("period"), "currency": res.get("currency"), "filters": res.get("filters"),
           "verified_kpis": {k: {"current": v.get("current"), "previous": v.get("previous"), "change_pct": v.get("change_pct")}
                             for k, v in res.get("kpis", {}).items()},
           "concentration": res.get("concentration"),
           "top_suppliers": [{"supplier": x["supplier"], "share_pct": x["share_pct"], "delivery": x["delivery"],
                              "quality": x["quality"], "price": x["price"]} for x in res.get("top5", [])],
           "cross_module": res.get("cross"), "savings": res.get("savings"),
           "signals": [{"type": x["type"], "name": x["name_ar"], "dimension": x.get("dimension"),
                        "evidence": x.get("evidence")} for x in res.get("signals", [])[:8]]}
    out = gw.request_ai_analysis(gw.GeminiProvider(company_gemini), ctx,
                                 "اشرح وضع المشتريات والموردين: الأدلة، ثم الأثر، ثم التوصية. لا تحسب أي رقم جديد.",
                                 trust_report=trust, lang=get_lang(request), company=company)
    log_audit(company.id, user.id, user.name, "purchases_ai_insights", "purchases", f"period={res.get('period')}")
    return {"period": res.get("period"), "ai": out}


# ═══════════════════════════════════════════════════════════
#  Phase 2.8 — Cash Flow Intelligence (النقد من الحركات النقدية فقط؛ الوحدات الأخرى للربط)
# ═══════════════════════════════════════════════════════════
def _cash_scope(s, user, need="view"):
    _ensure_data_tables()
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    company = s.get(Company, user.company_id)
    role = get_user_role(s, user) if company else None
    if not company or not (role == "owner" or check_permission(role, "finance", "view")):
        raise HTTPException(403, "غير مصرّح — التدفق النقدي للمالك ومن لديه صلاحية المالية")
    if company.is_active != 1:
        raise HTTPException(402, "شركتك قيد التفعيل")
    if need == "edit" and not (role == "owner" or check_permission(role, "finance", "edit")):
        raise HTTPException(403, "غير مصرّح — تعديل إعدادات السيولة للمالك والمحاسب")
    return company, role


def _cash_settings(s, company_id):
    r = s.exec(select(CompanyCashSetting).where(CompanyCashSetting.company_id == company_id)).first()
    return {} if not r else {"opening_balance": r.opening_balance, "current_liabilities": r.current_liabilities,
                             "min_cash": r.min_cash, "restricted_cash": r.restricted_cash}


def _cashflow_result(s, company, *, period=None, branch_id=None):
    ce = _load_p24("cashflow_engine")
    if ce is None:
        raise HTTPException(503, "محرّك التدفق النقدي غير متاح — " + _p23_diagnostic())
    names = {b.id: b.name for b in s.exec(select(CompanyBranch).where(CompanyBranch.company_id == company.id)).all()}
    mv = [{"date": r.date, "direction": r.direction, "amount": r.amount, "movement_type": r.movement_type,
           "category": r.category, "counterparty": getattr(r, "counterparty", "") or None,
           "account": getattr(r, "account", "") or None, "balance": getattr(r, "balance", None),
           "source": r.source, "branch_name": names.get(r.branch_id) if r.branch_id else None, "branch_id": r.branch_id}
          for r in s.exec(select(CompanyCashMovement).where(CompanyCashMovement.company_id == company.id).limit(200000)).all()]
    ar = [{"invoice_date": r.invoice_date, "due_date": r.due_date, "amount": r.amount, "paid_amount": r.paid_amount,
           "paid_date": r.paid_date, "customer_name": r.customer_name, "reference": r.reference,
           "branch_name": names.get(r.branch_id) if r.branch_id else None, "branch_id": r.branch_id}
          for r in s.exec(select(CompanyReceivable).where(CompanyReceivable.company_id == company.id).limit(100000)).all()]
    if branch_id:
        mv = [m for m in mv if m["branch_id"] == branch_id]
        ar = [a for a in ar if a["branch_id"] == branch_id]
    sales = _sales_rows(s, company.id, {branch_id} if branch_id else None)
    inv_by_p = {}
    for r in s.exec(select(CompanyInventory).where(CompanyInventory.company_id == company.id).limit(100000)).all():
        if (not branch_id or r.branch_id == branch_id) and r.closing_value is not None:
            inv_by_p[r.period] = inv_by_p.get(r.period, 0) + r.closing_value
    ip = sorted(inv_by_p)
    pur_chg, open_po = None, None
    try:
        _p = _purchases_result(s, company, branch_id=branch_id)
        if _p.get("has_data"):
            pur_chg = _p["kpis"]["spend"].get("change_pct")
            open_po = (_p["kpis"]["open_pos"].get("open_value") or {}).get("value")
    except HTTPException:
        pass
    res = ce.analyze_cashflow(mv, receivables=ar, period=period, settings=_cash_settings(s, company.id), sales_rows=sales,
                              inventory_value=inv_by_p[ip[-1]] if ip else None,
                              inventory_value_prev=inv_by_p[ip[-2]] if len(ip) > 1 else None,
                              purchases_change_pct=pur_chg, open_po_value=open_po,
                              currency=getattr(company, "currency", None) or "SAR")
    res["filters"] = {"period": res.get("period"), "branch_id": branch_id}
    res["options"] = {"branch_list": [{"id": k, "name": v} for k, v in names.items()]}
    res["settings"] = _cash_settings(s, company.id)
    return res


@app.get("/company/cashflow-intelligence")
def company_cashflow_intelligence(user: User = Depends(get_current_user), period: str = "", branch_id: str = ""):
    with Session(engine) as s:
        company, role = _cash_scope(s, user)
        res = _cashflow_result(s, company, period=period or None, branch_id=_int_or_none(branch_id))
        res["can_edit"] = role == "owner" or check_permission(role, "finance", "edit")
        return res


@app.post("/company/cashflow/settings")
def company_cashflow_settings(data: dict, user: User = Depends(get_current_user)):
    """إدخال يدوي: الرصيد الافتتاحي، الالتزامات المتداولة، الحد الأدنى للنقد، النقد المقيد. الفارغ = غير متاح."""
    with Session(engine) as s:
        company, role = _cash_scope(s, user, need="edit")
        row = s.exec(select(CompanyCashSetting).where(CompanyCashSetting.company_id == company.id)).first() \
            or CompanyCashSetting(company_id=company.id)
        for k in ("opening_balance", "current_liabilities", "min_cash", "restricted_cash"):
            if k in data:
                v = data.get(k)
                if v in (None, ""):
                    setattr(row, k, None); continue
                try:
                    x = float(str(v).replace(",", ""))
                except ValueError:
                    raise HTTPException(422, f"قيمة غير رقمية في {k}")
                if x < 0 and k != "opening_balance":
                    raise HTTPException(422, f"قيمة سالبة غير مقبولة في {k}")
                setattr(row, k, x)
        row.updated_by, row.updated_at = (user.name or user.email)[:100], datetime.now()
        s.add(row); s.commit()
        log_audit(company.id, user.id, user.name, "cash_settings", "cashflow", json.dumps(
            {k: getattr(row, k) for k in ("opening_balance", "current_liabilities", "min_cash", "restricted_cash")}))
        return {"ok": True}


@app.post("/company/cashflow/to-decision")
def company_cashflow_to_decision(data: dict, user: User = Depends(get_current_user)):
    """يحوّل إشارة سيولة إلى قرار ومهمة — تُعاد الإشارة حسابياً في الخادم."""
    with Session(engine) as s:
        company, role = _exec_scope(s, user, need="edit")
        res = _cashflow_result(s, company, period=data.get("period") or None, branch_id=_int_or_none(data.get("branch_id")))
        sig = next((x for x in res.get("signals", []) if x["id"] == str(data.get("signal_id") or "")), None)
        if not sig:
            raise HTTPException(404, "الإشارة غير موجودة أو لم تعد قائمة لهذه الفترة")
        impact = (sig.get("estimated_impact") or {}).get("value")
        d = CompanyDecision(
            company_id=company.id, title=str(data.get("title") or sig["name_ar"])[:200],
            detail=" · ".join(sig.get("evidence", []))[:1000], owner=str(data.get("owner") or "")[:100],
            due_date=str(data.get("due_date") or "")[:20], kpi=sig.get("metric_id") or "net_cash_flow", status="open",
            baseline_sales=_company_total_sales(s, company.id),
            expected_impact=(f"{impact} {res['currency']} (تقديري)" if impact is not None else "غير قابل للتقدير")[:200],
            linked_to=f"cashflow_signal:{sig['id']}", rationale=sig.get("suggested_action_ar", "")[:500],
            metric_id=sig.get("metric_id") or "net_cash_flow", expected_impact_value=impact, impact_status="expected",
            source_signal=sig["id"], problem_type=sig["code"], decision_type="cashflow",
            outcome_status="pending_measurement", created_by=user.name or user.email,
            data_source="companycashmovement", updated_at=datetime.now())
        s.add(d); s.commit(); s.refresh(d)
        act = CompanyAction(company_id=company.id, decision_id=d.id, title=sig.get("suggested_action_ar", "")[:200],
                            owner=d.owner, priority="P1" if sig["severity"] == "high" else "P2", due_date=d.due_date,
                            start_date=datetime.now().strftime("%Y-%m-%d"), updated_at=datetime.now())
        s.add(act); s.commit(); s.refresh(act)
        log_audit(company.id, user.id, user.name, "decision_from_cashflow_signal", f"decision:{d.id}",
                  f"signal={sig['id']} code={sig['code']}")
        return {"ok": True, "decision_id": d.id, "action_ids": [act.id]}


@app.post("/company/cashflow/ai-insights")
def company_cashflow_ai_insights(data: dict, request: Request, user: User = Depends(get_current_user)):
    """AI يشرح أرقام محرك التدفق النقدي المتحقَّق منها فقط — لا يحسب ولا يخترع رقماً."""
    with Session(engine) as s:
        company, role = _cash_scope(s, user)
        res = _cashflow_result(s, company, period=data.get("period") or None, branch_id=_int_or_none(data.get("branch_id")))
    if not res.get("has_data"):
        raise HTTPException(422, res.get("message_ar") or "لا توجد حركات نقدية")
    gw = _load_p24("ai_gateway")
    if gw is None:
        raise HTTPException(503, "بوابة الذكاء الاصطناعي غير متاحة — " + _p23_diagnostic())
    dq = [x for x in res.get("signals", []) if x["type"] == "data_quality"]
    trust = {"overall_score": 85 if not dq else 65, "status": "pass" if not dq else "warning", "has_critical_fail": False,
             "main_causes": [{"explanation": e, "fix": ""} for x in dq for e in x.get("evidence", [])][:3]}
    ctx = {"period": res.get("period"), "currency": res.get("currency"), "position": res.get("position"),
           "flows": res.get("flows"), "variance": res.get("variance"),
           "drivers": [{"label": d["label"], "direction": d["direction"], "amount": d["amount"], "delta": d["delta"]}
                       for d in res.get("drivers", [])],
           "short_term": res.get("short_term"), "liquidity": res.get("liquidity"),
           "collections": {k: v for k, v in (res.get("collections") or {}).items() if k != "aging"},
           "signals": [{"type": x["type"], "name": x["name_ar"], "evidence": x.get("evidence")} for x in res.get("signals", [])[:8]]}
    out = gw.request_ai_analysis(gw.GeminiProvider(company_gemini), ctx,
                                 "اشرح لماذا تغيّر النقد: الأدلة، ثم الأثر، ثم التوصية، ثم الإجراء. لا تحسب أي رقم جديد.",
                                 trust_report=trust, lang=get_lang(request), company=company)
    log_audit(company.id, user.id, user.name, "cashflow_ai_insights", "cashflow", f"period={res.get('period')}")
    return {"period": res.get("period"), "ai": out}


# ═══════════════════════════════════════════════════════════
#  Phase 2.9 — People Intelligence (الموظفون من نفس جدول البيانات الأساسية)
# ═══════════════════════════════════════════════════════════
def _hr_scope(s, user):
    _ensure_data_tables()
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    company = s.get(Company, user.company_id)
    role = get_user_role(s, user) if company else None
    if not company or not (role == "owner" or check_permission(role, "hr", "view")
                           or check_permission(role, "finance", "view")):
        raise HTTPException(403, "غير مصرّح — الموارد البشرية للمالك ومن لديه صلاحية الموارد البشرية")
    if company.is_active != 1:
        raise HTTPException(402, "شركتك قيد التفعيل")
    return company, role


_HR_PAY_FIELDS = ("monthly_cost", "basic_salary", "allowances", "benefits")


def _hr_result(s, company, *, period=None, branch_id=None, department="", can_see_pay=True):
    he = _load_p24("hr_engine")
    if he is None:
        raise HTTPException(503, "محرّك الموارد البشرية غير متاح — " + _p23_diagnostic())
    names = {b.id: b.name for b in s.exec(select(CompanyBranch).where(CompanyBranch.company_id == company.id)).all()}
    deps = {d.id: d.name for d in s.exec(select(CompanyDepartment).where(CompanyDepartment.company_id == company.id)).all()}
    emps = []
    for e in s.exec(select(CompanyEmployee).where(CompanyEmployee.company_id == company.id).limit(50000)).all():
        extra = {}
        try:
            extra = json.loads(e.extra_json or "{}")
        except (TypeError, ValueError):
            pass
        row = {k: getattr(e, k, None) for k in ("employee_code", "name", "role", "employment_status", "hire_date",
                                                "termination_date", "monthly_cost", "employment_type", "manager",
                                                "termination_type", "basic_salary", "allowances", "benefits",
                                                "performance_rating", "last_promotion_date", "training_hours",
                                                "absence_days", "overtime_hours", "critical_role", "successors")}
        row["branch_id"], row["branch_name"] = e.branch_id, names.get(e.branch_id)
        row["department"] = deps.get(e.department_id) or extra.get("department")
        emps.append(row)
    if branch_id:
        emps = [x for x in emps if x["branch_id"] == branch_id]
    if department:
        emps = [x for x in emps if (x["department"] or "") == department]
    ops = [{"title": o.title, "opened_date": o.opened_date, "filled_date": o.filled_date, "status": o.status,
            "applicants": o.applicants, "interviews": o.interviews, "offers": o.offers, "hires": o.hires,
            "hiring_cost": o.hiring_cost, "department": deps.get(o.department_id) or json.loads(o.extra_json or "{}").get("department"),
            "branch_name": names.get(o.branch_id), "branch_id": o.branch_id}
           for o in s.exec(select(CompanyJobOpening).where(CompanyJobOpening.company_id == company.id).limit(10000)).all()]
    if branch_id:
        ops = [o for o in ops if o["branch_id"] == branch_id]
    if department:
        ops = [o for o in ops if (o["department"] or "") == department]
    payroll_cash = None
    try:
        _cf = _cashflow_result(s, company, period=period, branch_id=branch_id)
        if _cf.get("has_data") and not branch_id:
            payroll_cash = next(((d["amount"] or {}).get("value") for d in _cf.get("drivers", []) if d["key"] == "payroll"), None)
    except HTTPException:
        pass
    res = he.analyze_hr(emps, openings=ops, period=period, sales_rows=_sales_rows(s, company.id, {branch_id} if branch_id else None),
                        payroll_cash=payroll_cash if can_see_pay else None,
                        currency=getattr(company, "currency", None) or "SAR")
    if not can_see_pay and res.get("has_data"):      # RBAC على مستوى الحقل: لا رواتب لغير المخوّلين
        res["compensation"] = {"available": False, "restricted": True, "reason_ar": "بيانات الرواتب متاحة للمالك والمحاسب فقط"}
        res["kpis"]["cost"] = {"current": None, "restricted": True}
        res["pillars"]["cost"] = {"restricted": True}
        res["productivity"]["cost_to_revenue_pct"] = None
        for u in res["units"]["branches"] + res["units"]["departments"]:
            u["payroll"] = None
        for d in res["performance"].get("distribution", []) if res["performance"].get("available") else []:
            d["avg_cost"] = None
        res["signals"] = [x for x in res["signals"] if x["code"] != "cost_increase"]
    res["filters"] = {"period": res.get("period"), "branch_id": branch_id, "department": department}
    res["options"] = {"branch_list": [{"id": k, "name": v} for k, v in names.items()], "departments": sorted(set(deps.values()))}
    res["can_see_pay"] = can_see_pay
    return res


@app.get("/company/hr-intelligence")
def company_hr_intelligence(user: User = Depends(get_current_user), period: str = "", branch_id: str = "", department: str = ""):
    if period and not re.match(r"^\d{4}-\d{2}$", period):
        raise HTTPException(422, "الفترة بصيغة YYYY-MM")
    with Session(engine) as s:
        company, role = _hr_scope(s, user)
        return _hr_result(s, company, period=period or None, branch_id=_int_or_none(branch_id), department=department,
                          can_see_pay=role == "owner" or can_see_sensitive_financials(role))


@app.post("/company/hr/to-decision")
def company_hr_to_decision(data: dict, user: User = Depends(get_current_user)):
    with Session(engine) as s:
        company, role = _exec_scope(s, user, need="edit")
        res = _hr_result(s, company, period=data.get("period") or None, branch_id=_int_or_none(data.get("branch_id")),
                         department=str(data.get("department") or ""),
                         can_see_pay=role == "owner" or can_see_sensitive_financials(role))
        sig = next((x for x in res.get("signals", []) if x["id"] == str(data.get("signal_id") or "")), None)
        if not sig:
            raise HTTPException(404, "الإشارة غير موجودة أو لم تعد قائمة لهذه الفترة")
        d = CompanyDecision(
            company_id=company.id, title=str(data.get("title") or sig["name_ar"])[:200],
            detail=" · ".join(sig.get("evidence", []))[:1000], owner=str(data.get("owner") or "")[:100],
            due_date=str(data.get("due_date") or "")[:20], kpi=sig.get("metric_id") or "headcount", status="open",
            baseline_sales=_company_total_sales(s, company.id), expected_impact="غير قابل للتقدير",
            linked_to=f"hr_signal:{sig['id']}", rationale=sig.get("suggested_action_ar", "")[:500],
            metric_id=sig.get("metric_id") or "headcount", impact_status="expected", source_signal=sig["id"],
            problem_type=sig["code"], decision_type="hr", outcome_status="pending_measurement",
            created_by=user.name or user.email, data_source="companyemployee", updated_at=datetime.now())
        s.add(d); s.commit(); s.refresh(d)
        act = CompanyAction(company_id=company.id, decision_id=d.id, title=sig.get("suggested_action_ar", "")[:200],
                            owner=d.owner, priority="P1" if sig["severity"] == "high" else "P2", due_date=d.due_date,
                            start_date=datetime.now().strftime("%Y-%m-%d"), updated_at=datetime.now())
        s.add(act); s.commit(); s.refresh(act)
        log_audit(company.id, user.id, user.name, "decision_from_hr_signal", f"decision:{d.id}", f"signal={sig['id']}")
        return {"ok": True, "decision_id": d.id, "action_ids": [act.id]}


@app.post("/company/hr/ai-insights")
def company_hr_ai_insights(data: dict, request: Request, user: User = Depends(get_current_user)):
    """AI يشرح مؤشرات القوى العاملة المحسوبة فقط — بلا أسماء موظفين ولا رواتب أفراد."""
    with Session(engine) as s:
        company, role = _hr_scope(s, user)
        res = _hr_result(s, company, period=data.get("period") or None, branch_id=_int_or_none(data.get("branch_id")),
                         department=str(data.get("department") or ""),
                         can_see_pay=role == "owner" or can_see_sensitive_financials(role))
    if not res.get("has_data"):
        raise HTTPException(422, res.get("message_ar") or "لا توجد بيانات موظفين")
    gw = _load_p24("ai_gateway")
    if gw is None:
        raise HTTPException(503, "بوابة الذكاء الاصطناعي غير متاحة — " + _p23_diagnostic())
    t = res["turnover"]
    ctx = {"period": res["period"], "kpis": res["kpis"], "workforce": {k: res["workforce"][k] for k in ("active", "previous", "joiners", "leavers")},
           "turnover": {"rate_12m": t["rate_12m"], "by_branch": t["by_branch"][:5], "by_department": t["by_department"][:5],
                        "voluntary": t["voluntary"], "involuntary": t["involuntary"]},
           "performance": {k: v for k, v in res["performance"].items() if k in ("average", "distribution", "by_department")},
           "productivity": {k: v for k, v in res["productivity"].items() if k != "by_branch"},
           "flight_risk_count": res["flight_risk"]["count"], "succession": {k: v for k, v in res["succession"].items() if k != "roles"},
           "signals": [{"type": x["type"], "name": x["name_ar"], "dimension": x.get("dimension")} for x in res["signals"][:8]]}
    out = gw.request_ai_analysis(gw.GeminiProvider(company_gemini), ctx,
                                 "اشرح وضع القوى العاملة: الدليل، ثم مستوى الثقة، ثم التوصية. لا تحسب أي رقم ولا تذكر أفراداً.",
                                 trust_report={"overall_score": 80, "status": "pass", "has_critical_fail": False, "main_causes": []},
                                 lang=get_lang(request), company=company)
    log_audit(company.id, user.id, user.name, "hr_ai_insights", "hr", f"period={res['period']}")
    return {"period": res["period"], "ai": out}


# ═══════════════════════════════════════════════════════════
#  Phase 2.10 — Operations Intelligence (Command Center — ليس ERP)
# ═══════════════════════════════════════════════════════════
def _ops_scope(s, user, need="view"):
    _ensure_data_tables()
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    company = s.get(Company, user.company_id)
    role = get_user_role(s, user) if company else None
    if not company or not (role == "owner" or check_permission(role, "ops", "view") or check_permission(role, "ops", "edit")):
        raise HTTPException(403, "غير مصرّح — العمليات للمالك ومن لديه صلاحية العمليات")
    if company.is_active != 1:
        raise HTTPException(402, "شركتك قيد التفعيل")
    if need == "edit" and not (role == "owner" or check_permission(role, "ops", "edit")):
        raise HTTPException(403, "غير مصرّح — تعديل إعدادات العمليات")
    return company, role


def _ops_settings(s, company_id):
    r = s.exec(select(CompanyOpsSetting).where(CompanyOpsSetting.company_id == company_id)).first()
    try:
        return json.loads(r.settings_json or "{}") if r else {}
    except (TypeError, ValueError):
        return {}


def _ops_result(s, company, *, period=None, branch_id=None, department="", service=""):
    oe = _load_p24("ops_engine")
    if oe is None:
        raise HTTPException(503, "محرّك العمليات غير متاح — " + _p23_diagnostic())
    names = {b.id: b.name for b in s.exec(select(CompanyBranch).where(CompanyBranch.company_id == company.id)).all()}
    deps = {d.id: d.name for d in s.exec(select(CompanyDepartment).where(CompanyDepartment.company_id == company.id)).all()}

    def dep_of(r):
        if r.department_id:
            return deps.get(r.department_id)
        try:
            return json.loads(r.extra_json or "{}").get("department")
        except (TypeError, ValueError):
            return None
    keep = lambda r, d: (not branch_id or r.branch_id == branch_id) and (not department or (d or "") == department)
    orders, events, issues = [], [], []
    for r in s.exec(select(CompanyOpsOrder).where(CompanyOpsOrder.company_id == company.id).limit(300000)).all():
        d = dep_of(r)
        if keep(r, d) and (not service or r.service == service):
            orders.append({"reference": r.reference, "date": r.date, "branch_name": names.get(r.branch_id), "department": d,
                           "service": r.service or None, "status": r.status or None, "created_time": r.created_time or None,
                           "ready_time": r.ready_time or None, "delivered_time": r.delivered_time or None,
                           "due_time": r.due_time or None, "items": r.items, "accurate": r.accurate,
                           "defect_type": r.defect_type or None, "rework": r.rework})
    for r in s.exec(select(CompanyOpsEvent).where(CompanyOpsEvent.company_id == company.id).limit(300000)).all():
        d = dep_of(r)
        if keep(r, d):
            events.append({"reference": r.reference, "stage": r.stage, "start_time": r.start_time, "end_time": r.end_time,
                           "branch_name": names.get(r.branch_id), "department": d})
    for r in s.exec(select(CompanyOpsIssue).where(CompanyOpsIssue.company_id == company.id).limit(50000)).all():
        d = dep_of(r)
        if keep(r, d):
            issues.append({"title": r.title, "opened_time": r.opened_time, "resolved_time": r.resolved_time or None,
                           "severity": r.severity, "status": r.status, "owner": r.owner, "root_cause": r.root_cause or None,
                           "impact": r.impact or None, "sla_hours": r.sla_hours, "branch_name": names.get(r.branch_id), "department": d})
    st = _ops_settings(s, company.id)
    hc, sales_chg, stockouts, sup_late = None, None, None, None
    try:   # 2.9 → عدد الموظفين النشطين لكل فرع (بلا رواتب)
        _hr = _hr_result(s, company, can_see_pay=False)
        if _hr.get("has_data"):
            hc = {u["key"]: u["employees"] for u in _hr["units"]["branches"] if u["employees"]}
    except HTTPException:
        pass
    try:   # 2.5 → تغير المبيعات الشهري
        months = {}
        for x in _sales_rows(s, company.id, {branch_id} if branch_id else None):
            v = x.get("net_sales") if x.get("net_sales") is not None else x.get("gross_sales")
            if v is not None and x.get("date"):
                months[str(x["date"])[:7]] = months.get(str(x["date"])[:7], 0) + float(v)
        ks = sorted(months)
        if len(ks) >= 2 and months[ks[-2]]:
            sales_chg = round((months[ks[-1]] - months[ks[-2]]) / months[ks[-2]] * 100, 1)
    except Exception:
        pass
    try:   # 2.6 → النفاد · 2.7 → تأخر الموردين
        _inv = _inventory_result(s, company, branch_id=branch_id)
        if _inv.get("has_data"):
            stockouts = sum(1 for p in (_inv.get("reorder") or []) if p.get("status") == "stockout")
        _pur = _purchases_result(s, company, branch_id=branch_id)
        lates = [x["delivery"]["late_pct"] for x in _pur.get("suppliers", []) if x.get("delivery") and x["delivery"].get("late_pct") is not None]
        sup_late = round(sum(lates) / len(lates), 1) if lates else None
    except HTTPException:
        pass
    res = oe.analyze_operations(orders, events=events, issues=issues, period=period, settings=st, headcount_by_branch=hc,
                                sales_change_pct=sales_chg, stockouts=stockouts, supplier_late_pct=sup_late)
    res["filters"] = {"period": res.get("period"), "branch_id": branch_id, "department": department, "service": service}
    res["options"] = {"branch_list": [{"id": k, "name": v} for k, v in names.items()],
                      "departments": sorted({o["department"] for o in orders if o.get("department")}),
                      "services": sorted({o["service"] for o in orders if o.get("service")})}
    res["saved_settings"] = st
    return res


@app.get("/company/operations-intelligence")
def company_operations_intelligence(user: User = Depends(get_current_user), period: str = "", branch_id: str = "",
                                    department: str = "", service: str = ""):
    with Session(engine) as s:
        company, role = _ops_scope(s, user)
        res = _ops_result(s, company, period=period or None, branch_id=_int_or_none(branch_id), department=department, service=service)
        res["can_edit"] = role == "owner" or check_permission(role, "ops", "edit")
        return res


@app.post("/company/operations/settings")
def company_operations_settings(data: dict, user: User = Depends(get_current_user)):
    """الطاقة اليومية لكل فرع (طلبات/يوم)، SLA بالدقائق، ومستهدفات التسليم والدقة وزمن التنفيذ."""
    with Session(engine) as s:
        company, role = _ops_scope(s, user, need="edit")
        names = {b.name for b in s.exec(select(CompanyBranch).where(CompanyBranch.company_id == company.id)).all()}
        out = {"capacity": {}, "targets": {}}

        def num(v, k):
            if v in (None, ""):
                return None
            try:
                x = float(str(v).replace(",", ""))
            except ValueError:
                raise HTTPException(422, f"قيمة غير رقمية في {k}")
            if x < 0:
                raise HTTPException(422, f"قيمة سالبة في {k}")
            return x
        for b, v in (data.get("capacity") or {}).items():
            if b in names and num(v, b) is not None:
                out["capacity"][b] = num(v, b)
        out["sla_minutes"] = num(data.get("sla_minutes"), "sla_minutes")
        for k in ("on_time_target", "accuracy_target", "fulfillment_target_min"):
            if num((data.get("targets") or {}).get(k), k) is not None:
                out["targets"][k] = num(data["targets"][k], k)
        row = s.exec(select(CompanyOpsSetting).where(CompanyOpsSetting.company_id == company.id)).first() \
            or CompanyOpsSetting(company_id=company.id)
        row.settings_json, row.updated_by, row.updated_at = json.dumps(out, ensure_ascii=False), (user.name or user.email)[:100], datetime.now()
        s.add(row); s.commit()
        log_audit(company.id, user.id, user.name, "ops_settings", "operations", json.dumps(out, ensure_ascii=False)[:500])
        return {"ok": True, "settings": out}


@app.post("/company/operations/to-decision")
def company_operations_to_decision(data: dict, user: User = Depends(get_current_user)):
    with Session(engine) as s:
        company, role = _exec_scope(s, user, need="edit")
        res = _ops_result(s, company, period=data.get("period") or None, branch_id=_int_or_none(data.get("branch_id")),
                          department=str(data.get("department") or ""), service=str(data.get("service") or ""))
        sig = next((x for x in res.get("signals", []) if x["id"] == str(data.get("signal_id") or "")), None)
        if not sig:
            raise HTTPException(404, "الإشارة غير موجودة أو لم تعد قائمة لهذه الفترة")
        d = CompanyDecision(
            company_id=company.id, title=str(data.get("title") or sig["name_ar"])[:200],
            detail=" · ".join(sig.get("evidence", []))[:1000], owner=str(data.get("owner") or "")[:100],
            due_date=str(data.get("due_date") or "")[:20], kpi=sig.get("metric_id") or "efficiency", status="open",
            baseline_sales=_company_total_sales(s, company.id), expected_impact="غير قابل للتقدير",
            linked_to=f"ops_signal:{sig['id']}", rationale=sig.get("suggested_action_ar", "")[:500],
            metric_id=sig.get("metric_id") or "efficiency", impact_status="expected", source_signal=sig["id"],
            problem_type=sig["code"], decision_type="operations", outcome_status="pending_measurement",
            created_by=user.name or user.email, data_source="companyopsorder", updated_at=datetime.now())
        s.add(d); s.commit(); s.refresh(d)
        act = CompanyAction(company_id=company.id, decision_id=d.id, title=sig.get("suggested_action_ar", "")[:200],
                            owner=d.owner, priority="P1" if sig["severity"] == "high" else "P2", due_date=d.due_date,
                            start_date=datetime.now().strftime("%Y-%m-%d"), updated_at=datetime.now())
        s.add(act); s.commit(); s.refresh(act)
        log_audit(company.id, user.id, user.name, "decision_from_ops_signal", f"decision:{d.id}", f"signal={sig['id']}")
        return {"ok": True, "decision_id": d.id, "action_ids": [act.id]}


@app.post("/company/operations/ai-insights")
def company_operations_ai_insights(data: dict, request: Request, user: User = Depends(get_current_user)):
    """AI يجيب عن أسئلة التشغيل من المؤشرات المحسوبة فقط: الدليل ← الأثر ← التوصية ← الإجراء."""
    with Session(engine) as s:
        company, role = _ops_scope(s, user)
        res = _ops_result(s, company, period=data.get("period") or None, branch_id=_int_or_none(data.get("branch_id")),
                          department=str(data.get("department") or ""), service=str(data.get("service") or ""))
    if not res.get("has_data"):
        raise HTTPException(422, res.get("message_ar") or "لا توجد بيانات تشغيلية")
    gw = _load_p24("ai_gateway")
    if gw is None:
        raise HTTPException(503, "بوابة الذكاء الاصطناعي غير متاحة — " + _p23_diagnostic())
    q = str(data.get("question") or "لماذا تغيّرت كفاءة التشغيل؟ وأين أكبر اختناق؟")[:300]
    ctx = {"period": res["period"], "score": res["score"], "kpis": res["kpis"], "bottlenecks": res["bottlenecks"][:5],
           "fulfillment": res["fulfillment"], "capacity": {k: v for k, v in res["capacity"].items() if k != "how_ar"},
           "branches": [{k: v for k, v in b.items() if k != "components"} for b in res["branches"]][:10],
           "quality": {k: v for k, v in res["quality"].items() if k in ("quality_rate", "defect_rate", "top_defects")},
           "issues": {k: v for k, v in res["issues"].items() if k != "list"}, "cross_module": res["cross"],
           "signals": [{"type": x["type"], "name": x["name_ar"], "dimension": x.get("dimension"), "evidence": x.get("evidence")} for x in res["signals"][:8]]}
    out = gw.request_ai_analysis(gw.GeminiProvider(company_gemini), ctx,
                                 q + " — أجب من البيانات فقط: الدليل، ثم الأثر، ثم التوصية، ثم الإجراء. لا تحسب أي رقم جديد، ولا تجزم بسبب لا تثبته البيانات.",
                                 trust_report={"overall_score": 80, "status": "pass", "has_critical_fail": False, "main_causes": []},
                                 lang=get_lang(request), company=company)
    log_audit(company.id, user.id, user.name, "ops_ai_insights", "operations", f"period={res['period']}")
    return {"period": res["period"], "question": q, "ai": out}


# ═══════════════════════════════════════════════════════════
#  Phase 2.11 — Financial Unit (لا قاعدة بيانات مالية منفصلة: تجميع من 2.5–2.10)
# ═══════════════════════════════════════════════════════════
def _fin_scope(s, user, need="view"):
    _ensure_data_tables()
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    company = s.get(Company, user.company_id)
    role = get_user_role(s, user) if company else None
    # قائمة الدخل تكشف إجمالي الرواتب والهوامش → للمالك ومن يرى البيانات المالية الحساسة فقط
    if not company or not (role == "owner" or (check_permission(role, "finance", "view") and can_see_sensitive_financials(role))):
        raise HTTPException(403, "غير مصرّح — الوحدة المالية للمالك والمحاسب")
    if company.is_active != 1:
        raise HTTPException(402, "شركتك قيد التفعيل")
    if need == "edit" and not (role == "owner" or check_permission(role, "finance", "edit")):
        raise HTTPException(403, "غير مصرّح — تعديل المدخلات المالية")
    return company, role


def _fin_settings(s, company_id):
    r = s.exec(select(CompanyFinSetting).where(CompanyFinSetting.company_id == company_id)).first()
    try:
        return json.loads(r.settings_json or "{}") if r else {}
    except (TypeError, ValueError):
        return {}


def _fin_result(s, company, *, period=None, grain="month"):
    fe = _load_p24("finance_engine")
    if fe is None:
        raise HTTPException(503, "المحرّك المالي غير متاح — " + _p23_diagnostic())
    names = {b.id: b.name for b in s.exec(select(CompanyBranch).where(CompanyBranch.company_id == company.id)).all()}
    sales = [dict(r, discount=r.get("discounts")) for r in _sales_rows(s, company.id)]
    cost_map = {}
    for p in s.exec(select(CompanyProduct).where(CompanyProduct.company_id == company.id)).all():
        if p.cost is not None and p.sku:
            cost_map[p.sku] = p.cost
    for r in s.exec(select(CompanyInventory).where(CompanyInventory.company_id == company.id).limit(100000)).all():
        if r.product_sku and r.closing_qty and r.closing_value is not None and r.product_sku not in cost_map:
            cost_map[(r.product_sku, r.period)] = r.closing_value / r.closing_qty
    exp_rows = [{"date": e.date, "amount": e.amount, "category": e.category, "description": e.description,
                 "branch_name": names.get(e.branch_id)} for e in s.exec(select(CompanyExpense).where(CompanyExpense.company_id == company.id).limit(200000)).all()]
    cash_mv = [{"date": r.date, "direction": r.direction, "amount": r.amount, "movement_type": r.movement_type, "category": r.category,
                "counterparty": getattr(r, "counterparty", ""), "branch_name": names.get(r.branch_id)}
               for r in s.exec(select(CompanyCashMovement).where(CompanyCashMovement.company_id == company.id).limit(200000)).all()]
    emps = [{"monthly_cost": e.monthly_cost, "hire_date": e.hire_date, "termination_date": e.termination_date,
             "branch_name": names.get(e.branch_id)} for e in s.exec(select(CompanyEmployee).where(CompanyEmployee.company_id == company.id).limit(50000)).all()]
    cash, ar_total, ctx = {}, None, {}
    try:
        cf = _cashflow_result(s, company, period=period)
        if cf.get("has_data"):
            cash = {"balance": (cf["position"].get("balance") or {}).get("value"),
                    "net_by_month": {m["period"]: (m["net"] or {}).get("value") for m in cf.get("movement", [])},
                    "inflow": (cf["flows"].get("inflow") or {}).get("value"), "outflow": (cf["flows"].get("outflow") or {}).get("value"),
                    "runway": next((x["value"] for x in cf.get("liquidity", []) if x["code"] == "runway"), None),
                    "forecast": {p_["period"]: (p_.get("balance") or {}).get("value") for p_ in (cf.get("forecast") or {}).get("points", [])}}
            ar_total = ((cf.get("collections") or {}).get("total_receivables") or {}).get("value")
            for b in cf.get("branches", []):
                ctx.setdefault(b["branch"], {})["net_cash"] = (b.get("net") or {}).get("value")
    except HTTPException:
        pass
    inv_by_p, inv_branch = {}, {}
    for r in s.exec(select(CompanyInventory).where(CompanyInventory.company_id == company.id).limit(100000)).all():
        if r.closing_value is not None:
            inv_by_p[r.period] = inv_by_p.get(r.period, 0) + r.closing_value
    ip = sorted(inv_by_p)
    if ip:
        for r in s.exec(select(CompanyInventory).where(CompanyInventory.company_id == company.id, CompanyInventory.period == ip[-1])).all():
            if r.closing_value is not None:
                bn = names.get(r.branch_id)
                ctx.setdefault(bn, {})["inventory_value"] = round(ctx.get(bn, {}).get("inventory_value", 0) + r.closing_value, 2)
    for fn_, key, val in ((lambda: _purchases_result(s, company), "by_branch", "purchases"),):
        try:
            res_ = fn_()
            if res_.get("has_data"):
                for b in res_.get(key, []):
                    ctx.setdefault(b["key"], {})[val] = (b.get("spend") or {}).get("value")
        except HTTPException:
            pass
    try:
        op = _ops_result(s, company)
        if op.get("has_data"):
            for b in op.get("branches", []):
                ctx.setdefault(b["key"], {}).update({"ops_efficiency": b.get("efficiency"), "ops_on_time": b.get("on_time")})
    except HTTPException:
        pass
    res = fe.analyze_finance(sales, cost_map=cost_map, expense_lines=fe.build_expense_lines(exp_rows, cash_mv), employees=emps,
                             cash=cash, receivables_total=ar_total, inventory_value=inv_by_p[ip[-1]] if ip else None,
                             inventory_value_prev=inv_by_p[ip[-2]] if len(ip) > 1 else None, settings=_fin_settings(s, company.id),
                             period=period, grain=grain, branch_context={k: v for k, v in ctx.items() if k},
                             currency=getattr(company, "currency", None) or "SAR")
    res["saved_settings"] = _fin_settings(s, company.id)
    try:      # مؤشرات القطاع في الوحدة المالية — من نفس الطبقة
        sb = _sector_block(s, company, period=res.get("period"))
        res["sector"] = {"sector": sb["sector"], "name_ar": sb["sector_name_ar"], "kpis": sb["kpis"], "watch_ar": sb.get("watch_ar")} if sb else None
    except Exception as e:
        _logger.warning(f"sector kpis: {type(e).__name__}: {str(e)[:120]}")
        res["sector"] = None
    return res


@app.get("/company/financial-intelligence")
def company_financial_intelligence(user: User = Depends(get_current_user), period: str = "", grain: str = "month"):
    if grain not in ("month", "quarter", "year"):
        raise HTTPException(422, "التجميع غير صالح")
    with Session(engine) as s:
        company, role = _fin_scope(s, user)
        res = _fin_result(s, company, period=period or None, grain=grain)
        res["can_edit"] = role == "owner" or check_permission(role, "finance", "edit")
        log_audit(company.id, user.id, user.name, "financial_view", "finance", f"period={res.get('period')}")
        return res


@app.post("/company/financial/settings")
def company_financial_settings(data: dict, user: User = Depends(get_current_user)):
    """بنود الميزانية، الإهلاك والفوائد الشهرية، والموازنة. الفارغ = غير متاح (لا صفر)."""
    with Session(engine) as s:
        company, role = _fin_scope(s, user, need="edit")

        def num(v, k, allow_neg=False):
            if v in (None, ""):
                return None
            try:
                x = float(str(v).replace(",", ""))
            except ValueError:
                raise HTTPException(422, f"قيمة غير رقمية في {k}")
            if x < 0 and not allow_neg:
                raise HTTPException(422, f"قيمة سالبة في {k}")
            return x
        bal_keys = ("fixed_assets", "other_assets", "accounts_payable", "loans", "accrued", "other_liabilities", "paid_in_capital", "retained_earnings")
        out = {"balance": {k: num((data.get("balance") or {}).get(k), k, k == "retained_earnings") for k in bal_keys},
               "depreciation_monthly": num(data.get("depreciation_monthly"), "depreciation_monthly"),
               "interest_monthly": num(data.get("interest_monthly"), "interest_monthly"),
               "revenue_target": num(data.get("revenue_target"), "revenue_target"),
               "budget": {"revenue_monthly": num((data.get("budget") or {}).get("revenue_monthly"), "revenue_monthly"),
                          "opex_monthly": {k: num(v, k) for k, v in ((data.get("budget") or {}).get("opex_monthly") or {}).items()
                                           if k in ("payroll", "rent", "marketing", "delivery", "software", "maintenance", "utilities", "other")}}}
        row = s.exec(select(CompanyFinSetting).where(CompanyFinSetting.company_id == company.id)).first() or CompanyFinSetting(company_id=company.id)
        row.settings_json, row.updated_by, row.updated_at = json.dumps(out, ensure_ascii=False), (user.name or user.email)[:100], datetime.now()
        s.add(row); s.commit()
        log_audit(company.id, user.id, user.name, "financial_settings", "finance", json.dumps(out, ensure_ascii=False)[:800])
        return {"ok": True, "settings": out}


@app.post("/company/financial/to-decision")
def company_financial_to_decision(data: dict, user: User = Depends(get_current_user)):
    with Session(engine) as s:
        company, role = _fin_scope(s, user)
        _exec_scope(s, user, need="edit")
        res = _fin_result(s, company, period=data.get("period") or None)
        sig = next((x for x in res.get("signals", []) if x["id"] == str(data.get("signal_id") or "")), None)
        if not sig:
            raise HTTPException(404, "الإشارة غير موجودة أو لم تعد قائمة لهذه الفترة")
        d = CompanyDecision(
            company_id=company.id, title=str(data.get("title") or sig["name_ar"])[:200],
            detail=" · ".join(sig.get("evidence", []))[:1000], owner=str(data.get("owner") or "")[:100],
            due_date=str(data.get("due_date") or "")[:20], kpi=sig.get("metric_id") or "net_profit", status="open",
            baseline_sales=_company_total_sales(s, company.id), expected_impact=(sig.get("impact_ar") or "غير قابل للتقدير")[:200],
            linked_to=f"finance_signal:{sig['id']}", rationale=sig.get("suggested_action_ar", "")[:500],
            metric_id=sig.get("metric_id") or "net_profit", impact_status="expected", source_signal=sig["id"],
            problem_type=sig["code"], decision_type="finance", outcome_status="pending_measurement",
            created_by=user.name or user.email, data_source="financial_unit", updated_at=datetime.now())
        s.add(d); s.commit(); s.refresh(d)
        act = CompanyAction(company_id=company.id, decision_id=d.id, title=sig.get("suggested_action_ar", "")[:200],
                            owner=d.owner, priority="P1" if sig["severity"] == "high" else "P2", due_date=d.due_date,
                            start_date=datetime.now().strftime("%Y-%m-%d"), updated_at=datetime.now())
        s.add(act); s.commit(); s.refresh(act)
        log_audit(company.id, user.id, user.name, "decision_from_finance_signal", f"decision:{d.id}", f"signal={sig['id']}")
        return {"ok": True, "decision_id": d.id, "action_ids": [act.id]}


@app.post("/company/financial/ai-insights")
def company_financial_ai_insights(data: dict, request: Request, user: User = Depends(get_current_user)):
    """AI يشرح الأرقام المالية المحسوبة فقط — لا يحسب ولا يخترع."""
    with Session(engine) as s:
        company, role = _fin_scope(s, user)
        res = _fin_result(s, company, period=data.get("period") or None)
    gw = _load_p24("ai_gateway")
    if gw is None:
        raise HTTPException(503, "بوابة الذكاء الاصطناعي غير متاحة — " + _p23_diagnostic())
    q = str(data.get("question") or "لماذا تغيّر صافي الربح؟")[:300]
    ctx = {"period": res.get("period"), "kpis": res.get("kpis"), "statement": res.get("statement"), "variance": res.get("variance"),
           "expenses": {k: v for k, v in (res.get("expenses") or {}).items() if k in ("total", "change_pct", "drivers", "basis_ar")},
           "branches": [{k: v for k, v in b.items() if k != "opex_lines"} for b in res.get("branches", [])][:10],
           "data_quality": res.get("quality"), "sources": ["Sales 2.5", "Inventory 2.6", "Purchases 2.7", "Cash Flow 2.8", "HR 2.9", "Operations 2.10"],
           "signals": [{"type": x["type"], "name": x["name_ar"], "evidence": x.get("evidence"), "impact": x.get("impact_ar")} for x in res.get("signals", [])[:8]]}
    out = gw.request_ai_analysis(gw.GeminiProvider(company_gemini), ctx,
                                 q + " — أجب من الأرقام المحسوبة فقط: العوامل المدعومة بالبيانات، ثم الأثر، ثم التوصية، واذكر مصادر البيانات. لا تحسب رقماً جديداً.",
                                 trust_report={"overall_score": 80, "status": "pass", "has_critical_fail": False, "main_causes": []},
                                 lang=get_lang(request), company=company)
    log_audit(company.id, user.id, user.name, "financial_ai_insights", "finance", f"period={res.get('period')}")
    return {"period": res.get("period"), "question": q, "ai": out}


# ═══════════════════════════════════════════════════════════
#  Phase 3.1 — Revenue Leakage Intelligence (يقرأ نفس السجلات؛ لا ينسخها)
# ═══════════════════════════════════════════════════════════
def _leak_scope(s, user, need="view"):
    _ensure_data_tables()
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    company = s.get(Company, user.company_id)
    role = get_user_role(s, user) if company else None
    if not company or not (role == "owner" or check_permission(role, "finance", "view") or check_permission(role, "sales", "view")):
        raise HTTPException(403, "غير مصرّح — تحليل التسرب للمالك والمالية والمديرين")
    if company.is_active != 1:
        raise HTTPException(402, "شركتك قيد التفعيل")
    if need == "edit" and not (role == "owner" or check_permission(role, "finance", "edit")):
        raise HTTPException(403, "غير مصرّح — تعديل حدود التسرب")
    return company, role


def _leak_settings(s, company_id):
    r = s.exec(select(CompanyLeakSetting).where(CompanyLeakSetting.company_id == company_id)).first()
    try:
        return json.loads(r.settings_json or "{}") if r else {}
    except (TypeError, ValueError):
        return {}


# ═══════════════════════════════════════════════════════════
#  Sector Intelligence Layer — كل وحدة تسأل الطبقة نفسها (Core + قواعد القطاع)
# ═══════════════════════════════════════════════════════════
def _sector_inputs(s, company, branch_id=None):
    """سلاسل شهرية موحدة لمؤشرات القطاع: الإيراد، التكلفة، الرواتب، التوصيل، الفاقد — من نفس السجلات."""
    fe = _load_p24("finance_engine")
    names = {b.id: b.name for b in s.exec(select(CompanyBranch).where(CompanyBranch.company_id == company.id)).all()}
    cost = {p.sku: p.cost for p in s.exec(select(CompanyProduct).where(CompanyProduct.company_id == company.id)).all() if p.sku and p.cost is not None}
    mon = {}
    for r in _sales_rows(s, company.id, {branch_id} if branch_id else None):
        ym = str(r.get("date") or "")[:7]
        if not ym:
            continue
        m = mon.setdefault(ym, {"revenue": 0.0, "gross": 0.0, "discounts": 0.0, "returns": 0.0, "delivery_rev": 0.0, "cogs": None})
        g, d_, rt = r.get("gross_sales") or 0, r.get("discounts") or 0, r.get("returns") or 0
        net = r.get("net_sales") if r.get("net_sales") is not None else g - d_ - rt
        m["revenue"] += net; m["gross"] += g; m["discounts"] += d_; m["returns"] += rt
        if any(w in str(r.get("channel") or "").lower() for w in ("توصيل", "delivery", "تطبيق")):
            m["delivery_rev"] += net
        if r.get("quantity") is not None and cost.get(r.get("product_sku")) is not None:
            m["cogs"] = (m["cogs"] or 0) + r["quantity"] * cost[r["product_sku"]]
    if fe is not None:
        exp = [{"date": e.date, "amount": e.amount, "category": e.category, "description": e.description, "branch_name": names.get(e.branch_id)}
               for e in s.exec(select(CompanyExpense).where(CompanyExpense.company_id == company.id).limit(200000)).all() if not branch_id or e.branch_id == branch_id]
        cm = [{"date": r.date, "direction": r.direction, "amount": r.amount, "movement_type": r.movement_type, "category": r.category,
               "counterparty": getattr(r, "counterparty", ""), "branch_name": names.get(r.branch_id)}
              for r in s.exec(select(CompanyCashMovement).where(CompanyCashMovement.company_id == company.id).limit(200000)).all() if not branch_id or r.branch_id == branch_id]
        for x in fe.build_expense_lines(exp, cm):
            m = mon.get(x["ym"])
            if m is None:
                continue
            if x["cat"] == "payroll":
                m["payroll"] = (m.get("payroll") or 0) + float(x["amount"])
            if x["cat"] == "delivery":
                m["delivery_exp"] = (m.get("delivery_exp") or 0) + float(x["amount"])
    for r in s.exec(select(CompanyInventory).where(CompanyInventory.company_id == company.id).limit(100000)).all():
        if branch_id and r.branch_id != branch_id:
            continue
        m = mon.get(r.period)
        if m is None or r.adjustments_qty is None:
            continue
        uc = cost.get(r.product_sku) or ((r.closing_value / r.closing_qty) if r.closing_qty and r.closing_value is not None else None)
        if uc is not None:
            m["shrinkage"] = (m.get("shrinkage") or 0) + (max(0.0, -r.adjustments_qty) * uc)
    supplier_items, obsolete = None, None
    try:
        pr = _purchases_result(s, company, branch_id=branch_id)
        if pr.get("has_data"):
            supplier_items = [{"product": p["product_sku"], "supplier": "، ".join(p.get("suppliers") or []) or None, "change_pct": p.get("variance_pct"),
                               "impact": round((p["avg_unit_cost"] - p["baseline_unit_cost"]) * (p["qty"] or 0), 2) if p.get("variance_pct") and p["variance_pct"] > 0 else 0,
                               "potential_saving": (p.get("potential_saving") or {}).get("value") or 0}
                              for p in pr.get("products", []) if p.get("baseline_unit_cost") is not None and p.get("avg_unit_cost") is not None]
    except HTTPException:
        pass
    try:
        inv = _inventory_result(s, company, branch_id=branch_id)
        if inv.get("has_data"):
            obsolete = ((inv.get("kpis") or {}).get("obsolete_value") or {}).get("current")
    except HTTPException:
        pass
    return mon, supplier_items, obsolete


def _sector_block(s, company, period=None, branch_id=None):
    si = _load_p24("sector_intelligence")
    if si is None:
        return None
    mon, sup, obs = _sector_inputs(s, company, branch_id)
    months = sorted(mon)
    if not months:
        return None
    cur = period if period in months else months[-1]
    base = [m for m in months if m < cur][-3:]
    targets = (_leak_settings(s, company.id).get("sector_targets") or {})
    block = si.sector_leakage(getattr(company, "sector", None), mon, cur, base, supplier_items=sup, obsolete_value=obs, targets=targets)
    block["kpis"] = si.compute_kpis(getattr(company, "sector", None), mon, cur, base, targets=targets)
    return block


@app.get("/company/sector-profile")
def company_sector_profile(user: User = Depends(get_current_user)):
    """ملف القطاع للشركة: المصطلحات، مؤشرات القطاع، أنواع التسرب المطبقة وما تحتاجه من بيانات — تستخدمه كل الوحدات."""
    with Session(engine) as s:
        _ensure_data_tables()
        if not user.company_id:
            raise HTTPException(403, "لا توجد شركة نشطة")
        company = s.get(Company, user.company_id)
        if not company:
            raise HTTPException(404, "الشركة غير موجودة")
        si = _load_p24("sector_intelligence")
        if si is None:
            raise HTTPException(503, "طبقة القطاعات غير متاحة — " + _p23_diagnostic())
        p = si.get_profile(getattr(company, "sector", None))
        return {"sector": p["key"], "name_ar": p["name_ar"], "terms": p["terms"], "core_kpis": p["core_kpis"],
                "sector_kpis": [{"code": k, **{x: si.KPI_DEFS[k][x] for x in ("name_ar", "formula_ar")}} for k in p["kpis"]],
                "leakage_types": [{"code": t["code"], "name_ar": t["name_ar"], "group": t["group"], "needs_ar": t.get("needs_ar")} for t in p["leakage"]],
                "watch_ar": p.get("watch_ar")}


def _leak_result(s, company, role, *, period=None, grain="month", branch_id=None):
    le, fe = _load_p24("leakage_engine"), _load_p24("finance_engine")
    if le is None or fe is None:
        raise HTTPException(503, "محرّك التسرب غير متاح — " + _p23_diagnostic())
    names = {b.id: b.name for b in s.exec(select(CompanyBranch).where(CompanyBranch.company_id == company.id)).all()}
    sales = [dict(r, discount=r.get("discounts")) for r in _sales_rows(s, company.id, {branch_id} if branch_id else None)]
    cost_map = {p.sku: p.cost for p in s.exec(select(CompanyProduct).where(CompanyProduct.company_id == company.id)).all() if p.sku and p.cost is not None}
    exp_rows = [{"date": e.date, "amount": e.amount, "category": e.category, "description": e.description, "branch_name": names.get(e.branch_id)}
                for e in s.exec(select(CompanyExpense).where(CompanyExpense.company_id == company.id).limit(200000)).all()
                if not branch_id or e.branch_id == branch_id]
    cash_mv = [{"date": r.date, "direction": r.direction, "amount": r.amount, "movement_type": r.movement_type, "category": r.category,
                "counterparty": getattr(r, "counterparty", ""), "branch_name": names.get(r.branch_id)}
               for r in s.exec(select(CompanyCashMovement).where(CompanyCashMovement.company_id == company.id).limit(200000)).all()
               if not branch_id or r.branch_id == branch_id]
    ar = [{"invoice_date": r.invoice_date, "due_date": r.due_date, "amount": r.amount, "paid_amount": r.paid_amount,
           "customer_name": r.customer_name, "branch_name": names.get(r.branch_id)}
          for r in s.exec(select(CompanyReceivable).where(CompanyReceivable.company_id == company.id).limit(100000)).all()
          if not branch_id or r.branch_id == branch_id]
    st = _leak_settings(s, company.id)
    st["budget"] = (_fin_settings(s, company.id).get("budget") or {})          # موازنة المصروفات من الوحدة المالية — مصدر واحد
    acts = []
    for d in s.exec(select(CompanyDecision).where(CompanyDecision.company_id == company.id, CompanyDecision.decision_type == "leakage")).all():
        parts = (d.linked_to or "").split("|")
        a = s.exec(select(CompanyAction).where(CompanyAction.company_id == company.id, CompanyAction.decision_id == d.id)).first()
        stt = (a.status if a else "not_started")
        acts.append({"id": d.id, "title": d.title, "owner": d.owner, "due_date": d.due_date, "code": d.problem_type,
                     "dimension": parts[1] if len(parts) > 1 and parts[1] else None, "period": parts[2] if len(parts) > 2 else None,
                     "expected": d.expected_impact_value, "status": {"completed": "done", "cancelled": "cancelled", "in_progress": "in_progress"}.get(stt, "open")})
    sensitive = role == "owner" or can_see_sensitive_financials(role)
    res = le.analyze_leakage(sales, expense_lines=fe.build_expense_lines(exp_rows, cash_mv), receivables=ar, cost_map=cost_map,
                             settings=st, period=period, grain=grain, actions=acts,
                             restricted_categories=None if sensitive else {"payroll"},
                             currency=getattr(company, "currency", None) or "SAR",
                             sector_block=_sector_block(s, company, period=period, branch_id=branch_id) if sensitive else None)
    res["filters"] = {"period": res.get("period"), "grain": grain, "branch_id": branch_id}
    res["options"] = {"branch_list": [{"id": k, "name": v} for k, v in names.items()]}
    res["saved_settings"] = {k: v for k, v in st.items() if k != "budget"}
    res["can_see_sensitive"] = sensitive
    return res


@app.get("/company/leakage-intelligence")
def company_leakage_intelligence(user: User = Depends(get_current_user), period: str = "", grain: str = "month", branch_id: str = ""):
    if grain not in ("month", "quarter", "year"):
        raise HTTPException(422, "التجميع غير صالح")
    with Session(engine) as s:
        company, role = _leak_scope(s, user)
        res = _leak_result(s, company, role, period=period or None, grain=grain, branch_id=_int_or_none(branch_id))
        res["can_edit"] = role == "owner" or check_permission(role, "finance", "edit")
        log_audit(company.id, user.id, user.name, "leakage_view", "leakage", f"period={res.get('period')} branch={branch_id or 'all'}")
        return res


@app.post("/company/leakage/settings")
def company_leakage_settings(data: dict, user: User = Depends(get_current_user)):
    """حدود التسرب: الفارغ = يُستخدم خط الأساس من تاريخ الشركة نفسها."""
    with Session(engine) as s:
        company, role = _leak_scope(s, user, need="edit")
        out = {}
        for k, hi in (("max_discount_rate", 100), ("acceptable_return_rate", 100), ("cost_of_capital_annual", 100)):
            v = data.get(k)
            if v not in (None, ""):
                try:
                    x = float(str(v).replace(",", ""))
                except ValueError:
                    raise HTTPException(422, f"قيمة غير رقمية في {k}")
                if not 0 <= x <= hi:
                    raise HTTPException(422, f"القيمة خارج النطاق في {k}")
                out[k] = x
        rules = {}
        for k, lo, hi in (("ar_risk_days", 1, 720), ("baseline_months", 1, 12), ("heat_high_pct", 0.1, 50), ("heat_medium_pct", 0.1, 50)):
            v = (data.get("rules") or {}).get(k)
            if v not in (None, ""):
                try:
                    x = float(v)
                except ValueError:
                    raise HTTPException(422, f"قيمة غير رقمية في {k}")
                if not lo <= x <= hi:
                    raise HTTPException(422, f"القيمة خارج النطاق في {k}")
                rules[k] = x
        out["rules"] = rules
        tg = {}
        for k, v in (data.get("sector_targets") or {}).items():
            if v not in (None, "") and re.match(r"^[a-z_]{2,40}$", k):
                try:
                    x = float(v)
                except ValueError:
                    raise HTTPException(422, f"قيمة غير رقمية في {k}")
                if not 0 <= x <= 100:
                    raise HTTPException(422, f"القيمة خارج النطاق في {k}")
                tg[k] = x
        out["sector_targets"] = tg
        row = s.exec(select(CompanyLeakSetting).where(CompanyLeakSetting.company_id == company.id)).first() or CompanyLeakSetting(company_id=company.id)
        row.settings_json, row.updated_by, row.updated_at = json.dumps(out, ensure_ascii=False), (user.name or user.email)[:100], datetime.now()
        s.add(row); s.commit()
        log_audit(company.id, user.id, user.name, "leakage_settings", "leakage", json.dumps(out, ensure_ascii=False)[:500])
        return {"ok": True, "settings": out}


@app.post("/company/leakage/to-decision")
def company_leakage_to_decision(data: dict, user: User = Depends(get_current_user)):
    """تسرب → قرار + إجراء استرداد. القيمة المتوقعة تُحفظ منفصلة عن المسترد الفعلي الذي يُقاس لاحقاً."""
    with Session(engine) as s:
        company, role = _leak_scope(s, user)
        _exec_scope(s, user, need="edit")
        res = _leak_result(s, company, role, period=data.get("period") or None, branch_id=_int_or_none(data.get("branch_id")))
        sig = next((x for x in res.get("signals", []) if x["id"] == str(data.get("signal_id") or "")), None)
        if not sig:
            raise HTTPException(404, "الإشارة غير موجودة أو لم تعد قائمة لهذه الفترة")
        impact = (sig.get("estimated_impact") or {}).get("value")
        d = CompanyDecision(
            company_id=company.id, title=str(data.get("title") or sig["name_ar"])[:200],
            detail=" · ".join(sig.get("evidence", []))[:1000], owner=str(data.get("owner") or "")[:100],
            due_date=str(data.get("due_date") or "")[:20], kpi=sig.get("metric_id") or "leakage", status="open",
            baseline_sales=_company_total_sales(s, company.id),
            expected_impact=(f"{impact} {res['currency']} (مكتشف — ليس مسترداً)" if impact is not None else "غير قابل للتقدير")[:200],
            linked_to=f"leakage_signal:{sig['id']}|{sig.get('dimension') or ''}|{res['period']}"[:200],
            rationale=sig.get("suggested_action_ar", "")[:500], metric_id=sig.get("metric_id") or "leakage",
            expected_impact_value=impact, impact_status="expected", source_signal=sig["id"], problem_type=sig["code"],
            decision_type="leakage", outcome_status="pending_measurement", created_by=user.name or user.email,
            data_source="leakage_engine", updated_at=datetime.now())
        s.add(d); s.commit(); s.refresh(d)
        act = CompanyAction(company_id=company.id, decision_id=d.id, title=sig.get("suggested_action_ar", "")[:200],
                            owner=d.owner, priority="P1" if sig["severity"] == "high" else "P2", due_date=d.due_date,
                            start_date=datetime.now().strftime("%Y-%m-%d"), updated_at=datetime.now())
        s.add(act); s.commit(); s.refresh(act)
        log_audit(company.id, user.id, user.name, "decision_from_leakage_signal", f"decision:{d.id}", f"signal={sig['id']} impact={impact}")
        return {"ok": True, "decision_id": d.id, "action_ids": [act.id]}


@app.post("/company/leakage/ai-insights")
def company_leakage_ai_insights(data: dict, request: Request, user: User = Depends(get_current_user)):
    """AI يشرح نتائج محرك التسرب المحسوبة فقط: الدليل، المصدر، الفترة، طريقة الحساب."""
    with Session(engine) as s:
        company, role = _leak_scope(s, user)
        res = _leak_result(s, company, role, period=data.get("period") or None, branch_id=_int_or_none(data.get("branch_id")))
    if not res.get("has_data"):
        raise HTTPException(422, res.get("message_ar") or "لا توجد بيانات")
    gw = _load_p24("ai_gateway")
    if gw is None:
        raise HTTPException(503, "بوابة الذكاء الاصطناعي غير متاحة — " + _p23_diagnostic())
    q = str(data.get("question") or "أين أكبر مصدر للهدر؟")[:300]
    ctx = {"period": res["period"], "overview": res["overview"],
           "methods": {k: (res.get(k) or {}).get("method_ar") for k in ("opex", "discount", "returns", "ar")},
           "branches": [{k: v for k, v in b.items() if k not in ("trend",)} for b in res["branches"]][:10],
           "root_causes": res["root_causes"], "top_products": res["products"][:8],
           "signals": [{"name": x["name_ar"], "dimension": x.get("dimension"), "evidence": x.get("evidence")} for x in res["signals"][:8]]}
    out = gw.request_ai_analysis(gw.GeminiProvider(company_gemini), ctx,
                                 q + " — أجب من نتائج المحرك فقط واذكر لكل رقم: الدليل والمصدر والفترة وطريقة الحساب. لا تحسب ولا تجزم بسبب غير مثبت.",
                                 trust_report={"overall_score": 80, "status": "pass", "has_critical_fail": False, "main_causes": []},
                                 lang=get_lang(request), company=company)
    log_audit(company.id, user.id, user.name, "leakage_ai_insights", "leakage", f"period={res['period']}")
    return {"period": res["period"], "question": q, "ai": out}


# ═══════════════════════════════════════════════════════════
#  Phase 3.2 — Tax & Zakat Compliance Intelligence (فحص بيانات — ليس شهادة امتثال)
# ═══════════════════════════════════════════════════════════
def _tax_scope(s, user, need="view"):
    _ensure_data_tables()
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    company = s.get(Company, user.company_id)
    role = get_user_role(s, user) if company else None
    if not company or not (role == "owner" or (check_permission(role, "finance", "view") and can_see_sensitive_financials(role))):
        raise HTTPException(403, "غير مصرّح — الضرائب والزكاة للمالك والمحاسب")
    if company.is_active != 1:
        raise HTTPException(402, "شركتك قيد التفعيل")
    if need == "edit" and not (role == "owner" or check_permission(role, "finance", "edit")):
        raise HTTPException(403, "غير مصرّح — تعديل إعدادات الضرائب")
    return company, role


def _tax_settings(s, company_id):
    r = s.exec(select(CompanyTaxSetting).where(CompanyTaxSetting.company_id == company_id)).first()
    try:
        return json.loads(r.settings_json or "{}") if r else {}
    except (TypeError, ValueError):
        return {}


def _tax_save(s, company, user, new, action, target):
    row = s.exec(select(CompanyTaxSetting).where(CompanyTaxSetting.company_id == company.id)).first() or CompanyTaxSetting(company_id=company.id)
    old = row.settings_json or "{}"
    row.settings_json, row.updated_by, row.updated_at = json.dumps(new, ensure_ascii=False), (user.name or user.email)[:100], datetime.now()
    s.add(row); s.commit()
    # سجل التدقيق: القيمة السابقة والجديدة
    log_audit(company.id, user.id, user.name, action, target, json.dumps({"before": json.loads(old), "after": new}, ensure_ascii=False)[:500])


def _tax_result(s, company, *, period=None):
    te = _load_p24("tax_engine")
    if te is None:
        raise HTTPException(503, "محرّك الضرائب غير متاح — " + _p23_diagnostic())
    names = {b.id: b.name for b in s.exec(select(CompanyBranch).where(CompanyBranch.company_id == company.id)).all()}
    sales = _sales_rows(s, company.id)
    purchases = [{"date": p.date, "vat": p.vat, "branch_name": names.get(p.branch_id), "supplier": p.supplier_name}
                 for p in s.exec(select(CompanyPurchase).where(CompanyPurchase.company_id == company.id).limit(100000)).all()]
    inv = [{"invoice_number": i.invoice_number, "issue_date": i.issue_date, "invoice_type": i.invoice_type, "branch_name": names.get(i.branch_id),
            "buyer_name": i.buyer_name, "buyer_vat": i.buyer_vat, "taxable_amount": i.taxable_amount, "vat_amount": i.vat_amount,
            "total_amount": i.total_amount, "currency": i.currency, "original_invoice": i.original_invoice, "zatca_status": i.zatca_status}
           for i in s.exec(select(CompanyTaxInvoice).where(CompanyTaxInvoice.company_id == company.id).limit(200000)).all()]
    bal, src = {}, {}
    try:     # مدخلات الزكاة من الوحدة المالية (نفس المصدر)
        fn = _fin_result(s, company)
        if fn.get("has_data"):
            bs = fn["balance_sheet"]
            for grp in ("assets", "liabilities", "equity"):
                for x in bs[grp]:
                    k = {"payables": "payables", "capital": "paid_in_capital", "retained": "retained_earnings", "current_profit": "net_profit_ytd"}.get(x["key"], x["key"])
                    if x["value"] is not None:
                        bal[k], src[k] = x["value"], x["source"]
    except HTTPException:
        pass
    bal["_sources"] = src
    st = _tax_settings(s, company.id)
    if not st.get("vat_number") and getattr(company, "tax_number", ""):
        st["vat_number"] = company.tax_number
    si = _load_p24("sector_intelligence")
    res = te.analyze_tax(sales, purchases=purchases, invoices=inv, settings=st, balance_inputs=bal, period=period,
                         sector_tax=si.get_tax_profile(getattr(company, "sector", None)) if si else None,
                         currency=getattr(company, "currency", None) or "SAR")
    res["saved_settings"] = {k: v for k, v in st.items() if k != "regulatory_overrides"}
    res["regulatory_overrides"] = st.get("regulatory_overrides") or {}
    return res


@app.get("/company/tax-intelligence")
def company_tax_intelligence(user: User = Depends(get_current_user), period: str = ""):
    with Session(engine) as s:
        company, role = _tax_scope(s, user)
        res = _tax_result(s, company, period=period or None)
        res["can_edit"] = role == "owner" or check_permission(role, "finance", "edit")
        log_audit(company.id, user.id, user.name, "tax_view", "tax", f"period={res.get('period')}")
        return res


@app.post("/company/tax/settings")
def company_tax_settings(data: dict, user: User = Depends(get_current_user)):
    with Session(engine) as s:
        company, role = _tax_scope(s, user, need="edit")
        st = _tax_settings(s, company.id)
        vn = str(data.get("vat_number") or "").strip()
        if vn and not re.match(r"^3\d{13}3$", vn):
            raise HTTPException(422, "الرقم الضريبي يتكون من 15 رقماً ويبدأ وينتهي بـ 3")
        fq = data.get("filing_frequency") or None
        if fq not in (None, "monthly", "quarterly"):
            raise HTTPException(422, "دورية الإقرار: شهري أو ربع سنوي")
        inc = data.get("prices_include_vat")
        st.update({"vat_number": vn or None, "filing_frequency": fq, "responsible": str(data.get("responsible") or "")[:100] or None,
                   "prices_include_vat": True if inc in (True, "true", "1", "yes") else (False if inc in (False, "false", "0", "no") else None)})
        ov = data.get("regulatory_overrides")
        if isinstance(ov, dict):
            clean = {}
            for k, v in ov.items():
                if isinstance(v, dict) and k in ("vat_standard_rate", "vat_return_due", "penalties", "b2b_buyer_vat_required"):
                    if k == "penalties" and not all(isinstance(p, dict) and p.get("source") for p in (v.get("value") or [])):
                        raise HTTPException(422, "كل قاعدة غرامة تحتاج مصدراً")
                    clean[k] = {kk: v[kk] for kk in ("value", "effective_date", "version", "source", "applicability") if kk in v}
            st["regulatory_overrides"] = clean
        _tax_save(s, company, user, st, "tax_settings", "tax")
        return {"ok": True}


@app.post("/company/tax/mark-filed")
def company_tax_mark_filed(data: dict, user: User = Depends(get_current_user)):
    """تسجيل تقديم إقرار لفترة (أو إلغاؤه) — مع أثر في سجل التدقيق."""
    with Session(engine) as s:
        company, role = _tax_scope(s, user, need="edit")
        p_ = str(data.get("period") or "")
        if not re.match(r"^\d{4}-(\d{2}|Q[1-4])$", p_):
            raise HTTPException(422, "الفترة غير صالحة")
        st = _tax_settings(s, company.id)
        fp = st.get("filed_periods") or {}
        if data.get("undo"):
            fp.pop(p_, None)
        else:
            d_ = str(data.get("date") or datetime.now().strftime("%Y-%m-%d"))[:10]
            fp[p_] = {"date": d_, "by": user.name or user.email, "note": str(data.get("note") or "")[:200]}
        st["filed_periods"] = fp
        _tax_save(s, company, user, st, "tax_mark_filed", f"period:{p_}")
        return {"ok": True}


@app.get("/company/tax/audit")
def company_tax_audit(user: User = Depends(get_current_user), limit: int = 100):
    with Session(engine) as s:
        company, role = _tax_scope(s, user)
        rows = s.exec(select(AuditLog).where(AuditLog.company_id == company.id).order_by(AuditLog.created_at.desc()).limit(500)).all()
        out = [{"when": r.created_at.isoformat(timespec="minutes") if r.created_at else None, "who": r.user_name, "action": r.action,
                "target": r.target, "details": r.details} for r in rows
               if (r.action or "").startswith("tax_") or "tax_signal" in (r.target or "") or (r.action or "") == "decision_from_tax_signal"]
        return {"items": out[:max(1, min(limit, 300))]}


@app.post("/company/tax/to-decision")
def company_tax_to_decision(data: dict, user: User = Depends(get_current_user)):
    with Session(engine) as s:
        company, role = _tax_scope(s, user)
        _exec_scope(s, user, need="edit")
        res = _tax_result(s, company, period=data.get("period") or None)
        sig = next((x for x in res.get("signals", []) if x["id"] == str(data.get("signal_id") or "")), None)
        if not sig:
            raise HTTPException(404, "الإشارة غير موجودة أو لم تعد قائمة")
        impact = (sig.get("estimated_impact") or {}).get("value")
        d = CompanyDecision(
            company_id=company.id, title=str(data.get("title") or sig["name_ar"])[:200], detail=" · ".join(sig.get("evidence", []))[:1000],
            owner=str(data.get("owner") or "")[:100], due_date=str(data.get("due_date") or "")[:20], kpi=sig.get("metric_id") or "tax", status="open",
            baseline_sales=_company_total_sales(s, company.id), expected_impact=(f"{impact} {res['currency']} (أثر ضريبي على سجلات تحتاج مراجعة)" if impact is not None else "غير قابل للتقدير")[:200],
            linked_to=f"tax_signal:{sig['id']}|{sig.get('dimension') or ''}|{res['period']}"[:200], rationale=sig.get("suggested_action_ar", "")[:500],
            metric_id=sig.get("metric_id") or "tax", expected_impact_value=impact, impact_status="expected", source_signal=sig["id"], problem_type=sig["code"],
            decision_type="tax", outcome_status="pending_measurement", created_by=user.name or user.email, data_source="tax_engine", updated_at=datetime.now())
        s.add(d); s.commit(); s.refresh(d)
        act = CompanyAction(company_id=company.id, decision_id=d.id, title=sig.get("suggested_action_ar", "")[:200], owner=d.owner,
                            priority="P1" if sig["severity"] == "high" else "P2", due_date=d.due_date, start_date=datetime.now().strftime("%Y-%m-%d"), updated_at=datetime.now())
        s.add(act); s.commit(); s.refresh(act)
        log_audit(company.id, user.id, user.name, "decision_from_tax_signal", f"tax_signal:{sig['id']}", f"decision={d.id} code={sig['code']}")
        return {"ok": True, "decision_id": d.id, "action_ids": [act.id]}


@app.post("/company/tax/ai-insights")
def company_tax_ai_insights(data: dict, request: Request, user: User = Depends(get_current_user)):
    with Session(engine) as s:
        company, role = _tax_scope(s, user)
        res = _tax_result(s, company, period=data.get("period") or None)
    if not res.get("has_data"):
        raise HTTPException(422, res.get("message_ar") or "لا توجد بيانات")
    gw = _load_p24("ai_gateway")
    if gw is None:
        raise HTTPException(503, "بوابة الذكاء الاصطناعي غير متاحة — " + _p23_diagnostic())
    q = str(data.get("question") or "وش المشاكل الضريبية عندي؟")[:300]
    ctx = {"period": res["period"], "overview": res["overview"], "checklist": res["checklist"], "vat": res["vat"],
           "reconciliation": {k: v for k, v in res["reconciliation"].items() if k not in ("mismatches", "sales_without_invoice", "invoices_without_sales")},
           "einvoice": res["einvoice"], "calendar": res["calendar"]["items"][-3:], "zakat_readiness": res["zakat"]["readiness_pct"],
           "exposures": res["exposures"], "signals": [{"name": x["name_ar"], "evidence": x.get("evidence")} for x in res["signals"][:8]],
           "disclaimer": res["disclaimer_ar"]}
    out = gw.request_ai_analysis(gw.GeminiProvider(company_gemini), ctx,
                                 q + " — أجب من نتائج المحرك فقط مع الدليل. لا تحسب أرقاماً جديدة، ولا تصف الفروق بأنها تهرب، ولا تمنح حكماً بالامتثال القانوني.",
                                 trust_report={"overall_score": 80, "status": "pass", "has_critical_fail": False, "main_causes": []},
                                 lang=get_lang(request), company=company)
    log_audit(company.id, user.id, user.name, "tax_ai_insights", "tax", f"period={res['period']}")
    return {"period": res["period"], "question": q, "ai": out}


# ═══════════════════════════════════════════════════════════
#  Phase 3.3 — Risk Intelligence Center (محرك مخاطر مركزي واحد فوق نتائج الوحدات)
#  البيانات الموحّدة → الوحدات (2.5→3.2) → محرك المخاطر → سجل/قرار/إجراء/قياس
# ═══════════════════════════════════════════════════════════
# نطاق الفئات حسب الدور (لا ربط مستخدم↔فرع حالياً — مدير الفرع يرى التشغيل والعملاء للشركة)
_RISK_CATS_BY_ROLE = {"owner": None, "accountant": ["liquidity", "profit", "compliance"], "manager": ["operational", "customer"]}
_RISK_STATUSES = ("detected", "reviewed", "decision", "approved", "action", "measurement", "reassessment", "monitoring", "closed")
_RISK_MODS_CACHE = {}
_RISK_CACHE_TTL = 30


def _risk_scope(s, user, need="view"):
    _ensure_data_tables()
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    company = s.get(Company, user.company_id)
    role = get_user_role(s, user) if company else None
    if not company or (role or "staff") not in _RISK_CATS_BY_ROLE:
        raise HTTPException(403, "غير مصرّح — مركز المخاطر للمالك والمحاسب والمدير")
    if company.is_active != 1:
        raise HTTPException(402, "شركتك قيد التفعيل")
    if need == "edit" and role not in ("owner", "accountant", "manager"):
        raise HTTPException(403, "غير مصرّح — تعديل سجل المخاطر")
    if need == "settings" and role != "owner":
        raise HTTPException(403, "غير مصرّح — قواعد المخاطر يعدّلها المالك فقط")
    return company, role


def _risk_settings(s, company_id):
    r = s.exec(select(CompanyRiskSetting).where(CompanyRiskSetting.company_id == company_id)).first()
    try:
        return json.loads(r.settings_json or "{}") if r else {}
    except (TypeError, ValueError):
        return {}


def _risk_sector(company):
    si = _load_p24("sector_intelligence")
    return si.get_profile(getattr(company, "sector", None))["key"] if si else "other"


def _risk_modules(s, company, role):
    """يجمع نتائج الوحدات التي يسمح بها الدور. كل وحدة تفشل تُسجَّل وتُعرض «غير متاحة» — لا تُبتلع بصمت ولا تصبح صفراً."""
    cats = _RISK_CATS_BY_ROLE.get(role)
    key = (company.id, role)
    hit = _RISK_MODS_CACHE.get(key)
    if hit and (datetime.now() - hit[0]).total_seconds() < _RISK_CACHE_TTL:
        return hit[1], hit[2], hit[3]
    need = lambda *c: cats is None or any(x in cats for x in c)
    plan = [("cashflow", need("liquidity"), lambda: _cashflow_result(s, company)),
            ("finance", need("profit"), lambda: _fin_result(s, company)),
            ("leakage", need("profit"), lambda: _leak_result(s, company, role)),
            ("purchases", need("profit", "operational"), lambda: _purchases_result(s, company)),
            ("inventory", need("operational"), lambda: _inventory_result(s, company)),
            ("operations", need("operational"), lambda: _ops_result(s, company)),
            ("hr", need("operational"), lambda: _hr_result(s, company, can_see_pay=False)),
            ("tax", need("compliance"), lambda: _tax_result(s, company))]
    mods, errors = {}, {}
    for name, ok, fn_ in plan:
        if not ok:
            continue
        try:
            mods[name] = fn_()
        except HTTPException as e:
            errors[name] = str(e.detail)[:160]
        except Exception as e:
            _logger.error(f"risk module {name}: {type(e).__name__}: {str(e)[:160]}")
            errors[name] = f"خطأ في وحدة {name}: {type(e).__name__}"
    cust = _sales_rows(s, company.id) if need("customer") else []
    _RISK_MODS_CACHE[key] = (datetime.now(), mods, cust, errors)
    return mods, cust, errors


def _risk_history(s, company_id):
    rows = s.exec(select(CompanyRiskAssessment).where(CompanyRiskAssessment.company_id == company_id)
                  .order_by(CompanyRiskAssessment.assessed_on.desc()).limit(400)).all()
    out = []
    for r in reversed(rows):
        try:
            out.append({"date": r.assessed_on, "index": r.index_score, "confidence": r.confidence_pct,
                        "categories": json.loads(r.categories_json or "{}"), "driver_levels": json.loads(r.driver_levels_json or "{}"),
                        "driver_scores": json.loads(r.driver_scores_json or "{}")})
        except (TypeError, ValueError):
            _logger.warning(f"risk snapshot {r.id} unreadable")
    return out


def _risk_stored(s, company_id):
    out = []
    for r in s.exec(select(CompanyRisk).where(CompanyRisk.company_id == company_id)).all():
        try:
            hist = json.loads(r.history_json or "[]")
        except (TypeError, ValueError):
            hist = []
        out.append({"risk_key": r.risk_key, "title": r.title, "category": r.category, "level": r.level or None, "status": r.status,
                    "owner": r.owner, "due_date": r.due_date, "mitigation": r.mitigation, "decision_id": r.decision_id,
                    "baseline_score": r.baseline_score, "baseline_value": r.baseline_value, "baseline_date": r.baseline_date,
                    "created_on": r.created_on, "history": hist[-30:]})
    return out


def _risk_result(s, company, role, *, save_snapshot=True):
    re_ = _load_p24("risk_engine")
    if re_ is None:
        raise HTTPException(503, "محرّك المخاطر غير متاح — " + _p23_diagnostic())
    mods, cust, errors = _risk_modules(s, company, role)
    cats = _RISK_CATS_BY_ROLE.get(role)
    today_ = datetime.now().date()
    res = re_.analyze_risk(mods, customer_rows=cust, settings=_risk_settings(s, company.id), sector=_risk_sector(company),
                           history=_risk_history(s, company.id), stored=_risk_stored(s, company.id), today=today_,
                           currency=getattr(company, "currency", None) or "SAR", categories=cats)
    res["module_errors"] = errors
    res["modules"] = [{"key": k, "ar": re_.MODULE_AR.get(k, k), "has_data": bool((m or {}).get("has_data")),
                       "link": {"cashflow": "company-cashflow-intelligence.html", "finance": "company-financial-intelligence.html",
                                "leakage": "company-leakage-intelligence.html", "purchases": "company-purchases-intelligence.html",
                                "inventory": "company-inventory-intelligence.html", "operations": "company-operations-intelligence.html",
                                "hr": "company-hr-intelligence.html", "tax": "company-tax-intelligence.html"}.get(k)}
                      for k, m in mods.items()]
    # لقطة تقييم يومية واحدة (العرض الكامل فقط) — أساس الخط الزمني والاتجاهات وقبل/بعد
    if save_snapshot and cats is None and res["index"]["score"] is not None:
        snap = res["snapshot"]
        row = s.exec(select(CompanyRiskAssessment).where(CompanyRiskAssessment.company_id == company.id,
                                                         CompanyRiskAssessment.assessed_on == snap["date"])).first() or \
            CompanyRiskAssessment(company_id=company.id, assessed_on=snap["date"])
        row.index_score, row.confidence_pct = snap["index"], snap["confidence"]
        row.categories_json = json.dumps(snap["categories"], ensure_ascii=False)
        row.driver_levels_json = json.dumps(snap["driver_levels"], ensure_ascii=False)
        row.driver_scores_json = json.dumps(snap["driver_scores"], ensure_ascii=False)
        row.engine_version, row.updated_at = res["version"], datetime.now()
        s.add(row); s.commit()
    # ربط خطط المعالجة بالإجراءات الفعلية (نفس جدول الإجراءات — لا نسخة)
    dec_ids = [r["decision_id"] for r in res["register"] if r.get("decision_id")]
    if dec_ids:
        acts = s.exec(select(CompanyAction).where(CompanyAction.company_id == company.id, CompanyAction.decision_id.in_(dec_ids))).all()
        by = {}
        for a in acts:
            by.setdefault(a.decision_id, []).append({"id": a.id, "title": a.title, "status": a.status, "progress": a.progress,
                                                     "owner": a.owner, "due_date": a.due_date})
        for r in res["register"]:
            r["actions"] = by.get(r.get("decision_id"), [])
            if r["actions"]:
                r["actions_progress"] = round(sum(a["progress"] or 0 for a in r["actions"]) / len(r["actions"]), 1)
    res["role"] = role
    res["can_edit"] = role in ("owner", "accountant", "manager")
    res["can_edit_rules"] = role == "owner"
    return res


@app.get("/company/risk-intelligence")
def company_risk_intelligence(user: User = Depends(get_current_user)):
    with Session(engine) as s:
        company, role = _risk_scope(s, user)
        res = _risk_result(s, company, role)
        log_audit(company.id, user.id, user.name, "risk_view", "risk", f"index={res['index']['score']} role={role}")
        return res


@app.get("/company/risk/brief")
def company_risk_brief(user: User = Depends(get_current_user)):
    """موجز الرئيس التنفيذي — حتمي من نفس الأرقام."""
    with Session(engine) as s:
        company, role = _risk_scope(s, user)
        res = _risk_result(s, company, role)
        return {"as_of": res["as_of"], "index": res["index"], "confidence": res["confidence"], "brief": res["brief"],
                "top_risks": res["top_risks"], "impacts": res["impacts"], "scope": res["scope"]}


def _risk_row(s, company, key):
    return s.exec(select(CompanyRisk).where(CompanyRisk.company_id == company.id, CompanyRisk.risk_key == key)).first()


def _risk_log(row, user, frm, to, note=""):
    try:
        h = json.loads(row.history_json or "[]")
    except (TypeError, ValueError):
        h = []
    h.append({"when": datetime.now().strftime("%Y-%m-%d %H:%M"), "who": user.name or user.email, "from": frm, "to": to, "note": note[:300]})
    row.history_json = json.dumps(h[-100:], ensure_ascii=False)


def _risk_upsert(s, company, user, key, res):
    """يحفظ عنصر السجل ويثبّت خط الأساس من حساب الخادم (لا من العميل) عند أول حفظ."""
    re_ = _load_p24("risk_engine")
    drv = next((d for d in res["drivers"] if d["key"] == key), None)
    row = _risk_row(s, company, key)
    if not row:
        if not drv and not key.startswith("manual-"):
            raise HTTPException(404, "الخطر غير موجود أو خارج صلاحيتك")
        row = CompanyRisk(company_id=company.id, risk_key=key, created_by=(user.name or user.email)[:100],
                          created_on=datetime.now().strftime("%Y-%m-%d"))
        if drv:
            row.title, row.category, row.level = drv["name_ar"], drv["category"], drv["level"] or ""
            row.baseline_score, row.baseline_value = drv["score"], drv["value"] if isinstance(drv["value"], (int, float)) else None
            row.baseline_date = res["as_of"]
            row.owner = re_.OWNER_BY_DRIVER.get(key) or re_.OWNER_DEFAULT.get(drv["category"], "")
    elif drv:
        row.level = drv["level"] or row.level
    return row, drv


@app.post("/company/risk/register/update")
def company_risk_register_update(data: dict, user: User = Depends(get_current_user)):
    with Session(engine) as s:
        company, role = _risk_scope(s, user, need="edit")
        key = str(data.get("risk_key") or "")[:80]
        res = _risk_result(s, company, role, save_snapshot=False)
        if key not in {r["risk_key"] for r in res["register"]} and key not in {d["key"] for d in res["drivers"]}:
            raise HTTPException(404, "الخطر غير موجود أو خارج صلاحيتك")
        row, drv = _risk_upsert(s, company, user, key, res)
        before = row.status or "detected"
        st = str(data.get("status") or before)
        if st not in _RISK_STATUSES:
            raise HTTPException(422, "حالة غير صالحة")
        if st == "approved" and role not in ("owner",):
            raise HTTPException(403, "اعتماد القرار للمالك فقط")
        if "due_date" in data:
            dd = str(data.get("due_date") or "")
            if dd and not _valid_date(dd):
                raise HTTPException(422, "تاريخ غير صالح (YYYY-MM-DD)")
            row.due_date = dd
        if "owner" in data:
            row.owner = str(data.get("owner") or "")[:100]
        if "mitigation" in data:
            row.mitigation = str(data.get("mitigation") or "")[:1000]
        if st == "reassessment" and drv:     # إعادة التقييم = قياس جديد على نفس القاعدة
            data["note"] = (str(data.get("note") or "") + f" · الدرجة الآن {drv['score']} (خط الأساس {row.baseline_score})").strip(" ·")
        row.status, row.updated_at = st, datetime.now()
        _risk_log(row, user, before, st, str(data.get("note") or ""))
        s.add(row); s.commit()
        log_audit(company.id, user.id, user.name, "risk_register_update", f"risk:{key}", f"{before}->{st} owner={row.owner}")
        return {"ok": True, "status": st}


@app.post("/company/risk/register/add")
def company_risk_register_add(data: dict, user: User = Depends(get_current_user)):
    """خطر يدوي (غير مكتشف من البيانات) — يُعلَّم «يدوي» ولا يدخل في حساب المؤشر."""
    with Session(engine) as s:
        company, role = _risk_scope(s, user, need="edit")
        title = str(data.get("title") or "").strip()[:200]
        cat = str(data.get("category") or "")
        allowed = _RISK_CATS_BY_ROLE.get(role) or ["liquidity", "customer", "profit", "operational", "compliance"]
        if not title or cat not in allowed:
            raise HTTPException(422, "العنوان والفئة مطلوبان (ضمن صلاحيتك)")
        lv = str(data.get("level") or "medium")
        if lv not in ("low", "medium", "high", "critical"):
            raise HTTPException(422, "مستوى غير صالح")
        dd = str(data.get("due_date") or "")
        if dd and not _valid_date(dd):
            raise HTTPException(422, "تاريخ غير صالح (YYYY-MM-DD)")
        import hashlib
        key = "manual-" + hashlib.sha1(f"{company.id}|{title}|{datetime.now().isoformat()}".encode()).hexdigest()[:10]
        row = CompanyRisk(company_id=company.id, risk_key=key, title=title, category=cat, level=lv, status="detected",
                          owner=str(data.get("owner") or "")[:100], due_date=dd, mitigation=str(data.get("mitigation") or "")[:1000],
                          created_by=(user.name or user.email)[:100], created_on=datetime.now().strftime("%Y-%m-%d"))
        _risk_log(row, user, "", "detected", "خطر يدوي")
        s.add(row); s.commit()
        log_audit(company.id, user.id, user.name, "risk_register_add", f"risk:{key}", f"{cat}/{lv} {title[:80]}")
        return {"ok": True, "risk_key": key}


@app.post("/company/risk/to-decision")
def company_risk_to_decision(data: dict, user: User = Depends(get_current_user)):
    """يحوّل خطراً إلى قرار + إجراء. الأرقام يعاد حسابها في الخادم — قيم العميل تُتجاهل."""
    with Session(engine) as s:
        company, role = _risk_scope(s, user, need="edit")
        _exec_scope(s, user, need="edit")
        key = str(data.get("risk_key") or "")
        res = _risk_result(s, company, role, save_snapshot=False)
        drv = next((d for d in res["drivers"] if d["key"] == key and d["level"] in ("medium", "high", "critical")), None)
        if not drv:
            raise HTTPException(404, "الخطر غير موجود أو لم يعد مرتفعاً")
        re_ = _load_p24("risk_engine")
        imp = next((i for i in drv["impacts"] if i["type"] in ("actual", "potential", "exposure")), None)
        d = CompanyDecision(
            company_id=company.id, title=str(data.get("title") or f"معالجة خطر: {drv['name_ar']}")[:200],
            detail=" · ".join(drv["evidence"])[:1000], owner=str(data.get("owner") or "")[:100], due_date=str(data.get("due_date") or "")[:20],
            kpi=key, status="open", baseline_sales=_company_total_sales(s, company.id),
            expected_impact=(f"{imp['type_ar']} {imp['amount']} {res['currency']}" if imp else "غير قابل للتقدير")[:200],
            linked_to=f"risk:{key}|{res['as_of']}"[:200], rationale=(re_.ACTIONS.get(key) or "")[:500], metric_id=key,
            baseline_value=drv["value"] if isinstance(drv["value"], (int, float)) else None,
            expected_impact_value=imp["amount"] if imp else None, impact_status="expected", source_signal=f"risk:{key}",
            problem_type=key, decision_type="risk", outcome_status="pending_measurement", created_by=user.name or user.email,
            data_source="risk_engine", updated_at=datetime.now())
        s.add(d); s.commit(); s.refresh(d)
        created = []
        for a in (data.get("actions") or [{"title": re_.ACTIONS.get(key) or drv["name_ar"]}])[:10]:
            act = CompanyAction(company_id=company.id, decision_id=d.id, title=str(a.get("title") or "")[:200], owner=str(a.get("owner") or d.owner)[:100],
                                priority="P1" if drv["level"] in ("critical", "high") else "P2", due_date=str(a.get("due_date") or d.due_date)[:20],
                                start_date=datetime.now().strftime("%Y-%m-%d"), updated_at=datetime.now())
            s.add(act); s.commit(); s.refresh(act); created.append(act.id)
        row, _ = _risk_upsert(s, company, user, key, res)
        before = row.status or "detected"
        row.status, row.decision_id, row.updated_at = "decision", d.id, datetime.now()
        if data.get("owner"):
            row.owner = str(data["owner"])[:100]
        if data.get("due_date") and _valid_date(str(data["due_date"])):
            row.due_date = str(data["due_date"])
        _risk_log(row, user, before, "decision", f"قرار #{d.id}")
        s.add(row); s.commit()
        log_audit(company.id, user.id, user.name, "decision_from_risk", f"risk:{key}", f"decision={d.id} score={drv['score']} actions={created}")
        return {"ok": True, "decision_id": d.id, "action_ids": created}


@app.get("/company/risk/settings")
def company_risk_settings_get(user: User = Depends(get_current_user)):
    with Session(engine) as s:
        company, role = _risk_scope(s, user)
        re_ = _load_p24("risk_engine")
        if re_ is None:
            raise HTTPException(503, "محرّك المخاطر غير متاح — " + _p23_diagnostic())
        st = _risk_settings(s, company.id)
        sector = _risk_sector(company)
        th, src = re_.thresholds_for(st.get("thresholds"), sector)
        return {"sector": sector, "saved": st, "weights": re_.weights_for(sector, st.get("weights")),
                "drivers": [{"key": k, "name_ar": m["ar"], "category": m["cat"], "unit": m["unit"], "direction": m["dir"],
                             "default": list(m["t"]), "effective": list(th[k]), "source": src[k], "enabled": k not in (st.get("disabled") or [])}
                            for k, m in re_.DRIVERS.items()], "can_edit": role == "owner"}


@app.post("/company/risk/settings")
def company_risk_settings_save(data: dict, user: User = Depends(get_current_user)):
    with Session(engine) as s:
        company, role = _risk_scope(s, user, need="settings")
        re_ = _load_p24("risk_engine")
        if re_ is None:
            raise HTTPException(503, "محرّك المخاطر غير متاح — " + _p23_diagnostic())
        metas = dict(re_.DRIVERS)
        de_ = _load_p24("drivers_engine")
        if de_ is not None:      # حدود المسببات المساندة (3.4) في نفس الإعدادات
            metas.update({k: {"ar": m["ar"], "dir": m["dir"]} for k, m in de_.SUPPORT.items()})
        prev_st = _risk_settings(s, company.id)
        new = {"thresholds": {k: v for k, v in (prev_st.get("thresholds") or {}).items() if k.startswith("x_") and k not in (data.get("thresholds") or {})},
               "weights": {}, "disabled": [], "targets": prev_st.get("targets") or {}}
        for k, v in (data.get("thresholds") or {}).items():
            if k not in metas or v in (None, "", []):
                continue
            try:
                vals = [float(x) for x in v][:3]
            except (TypeError, ValueError):
                raise HTTPException(422, f"حدود غير رقمية: {metas[k]['ar']}")
            asc = metas[k]["dir"] == "above"
            if len(vals) != 3 or not ((asc and vals[0] <= vals[1] <= vals[2]) or (not asc and vals[0] >= vals[1] >= vals[2])):
                raise HTTPException(422, f"ترتيب الحدود غير صحيح: {metas[k]['ar']} (متوسط ثم مرتفع ثم حرج)")
            new["thresholds"][k] = vals
        for k, v in (data.get("weights") or {}).items():
            if k in re_.CATS and v not in (None, ""):
                try:
                    w = float(v)
                except (TypeError, ValueError):
                    raise HTTPException(422, "الوزن يجب أن يكون رقماً")
                if not 0 <= w <= 3:
                    raise HTTPException(422, "الوزن بين 0 و3")
                new["weights"][k] = w
        new["disabled"] = [k for k in (data.get("disabled") or []) if k in metas]
        row = s.exec(select(CompanyRiskSetting).where(CompanyRiskSetting.company_id == company.id)).first() or CompanyRiskSetting(company_id=company.id)
        old = row.settings_json or "{}"
        row.settings_json, row.updated_by, row.updated_at = json.dumps(new, ensure_ascii=False), (user.name or user.email)[:100], datetime.now()
        s.add(row); s.commit()
        log_audit(company.id, user.id, user.name, "risk_settings", "risk", json.dumps({"before": json.loads(old), "after": new}, ensure_ascii=False)[:500])
        return {"ok": True}


@app.get("/company/risk/audit")
def company_risk_audit(user: User = Depends(get_current_user)):
    with Session(engine) as s:
        company, role = _risk_scope(s, user)
        rows = s.exec(select(AuditLog).where(AuditLog.company_id == company.id).order_by(AuditLog.created_at.desc()).limit(500)).all()
        return {"items": [{"when": r.created_at.isoformat(timespec="minutes") if r.created_at else None, "who": r.user_name, "action": r.action,
                           "target": r.target, "details": r.details} for r in rows
                          if (r.action or "").startswith("risk_") and r.action != "risk_view" or r.action == "decision_from_risk"][:200]}


@app.post("/company/risk/ai-insights")
def company_risk_ai_insights(data: dict, request: Request, user: User = Depends(get_current_user)):
    """الذكاء الاصطناعي يشرح نتائج المحرك فقط — لا يحسب ولا يقرر."""
    with Session(engine) as s:
        company, role = _risk_scope(s, user)
        res = _risk_result(s, company, role, save_snapshot=False)
    if not res.get("has_data"):
        raise HTTPException(422, "لا توجد بيانات كافية لتقييم المخاطر")
    gw = _load_p24("ai_gateway")
    if gw is None:
        raise HTTPException(503, "بوابة الذكاء الاصطناعي غير متاحة — " + _p23_diagnostic())
    q = str(data.get("question") or "ما أكبر مخاطرة يجب أن أتصرف فيها؟")[:300]
    ctx = {"as_of": res["as_of"], "index": res["index"], "confidence": {k: res["confidence"][k] for k in ("pct", "sufficiency_ar", "missing")},
           "categories": [{k: c.get(k) for k in ("ar", "score", "level_ar", "formula_ar", "unable_reasons")} for c in res["categories"]],
           "drivers": [{k: d[k] for k in ("name_ar", "value", "unit", "score", "level_ar", "thresholds", "evidence", "impacts", "source_ar", "score_parts", "reason_ar")}
                       for d in res["drivers"]],
           "chains": [{"ar": c["ar"], "status": c["status_ar"]} for c in res["chains"] if c["active"]], "chain_note": res["graph"]["note_ar"],
           "root_causes": [{k: r[k] for k in ("name_ar", "summary_ar", "module_causes")} for r in res["root_causes"][:6]],
           "impacts": res["impacts"], "impact_note": res["impact_note_ar"], "trends": res["trends"]["windows"], "rules": {k: res["rules"][k] for k in ("score_rule_ar", "category_rule_ar", "index_rule_ar")}}
    out = gw.request_ai_analysis(gw.GeminiProvider(company_gemini), ctx,
                                 q + " — اشرح من نتائج المحرك فقط واذكر الدليل والمصدر لكل رقم. لا تحسب أرقاماً جديدة، ولا تغيّر الدرجات، "
                                     "ولا تجزم بسبب: الارتباط ليس سببية. إن كانت فئة «تعذّر التحديد» فقل ذلك واذكر البيانات المطلوبة.",
                                 trust_report={"overall_score": int(res["confidence"]["pct"] or 0), "status": "pass" if (res["confidence"]["pct"] or 0) >= 80 else "warning",
                                               "has_critical_fail": False, "main_causes": []},
                                 lang=get_lang(request), company=company)
    log_audit(company.id, user.id, user.name, "risk_ai_insights", "risk", f"q={q[:80]}")
    return {"as_of": res["as_of"], "question": q, "ai": out}


# ═══════════════════════════════════════════════════════════
#  Phase 3.4 — Risk Drivers Intelligence (ما الذي يحرّك الخطر وبكم؟)
#  يقرأ نتيجة محرك المخاطر 3.3 + نفس نتائج الوحدات (مخزّنة مؤقتاً) — لا بيانات جديدة ولا جداول مكررة
# ═══════════════════════════════════════════════════════════
def _drivers_result(s, company, role, *, save_snapshot=True):
    de = _load_p24("drivers_engine")
    if de is None:
        raise HTTPException(503, "محرّك المسببات غير متاح — " + _p23_diagnostic())
    risk = _risk_result(s, company, role, save_snapshot=save_snapshot)
    mods, cust, errors = _risk_modules(s, company, role)
    res = de.analyze_drivers(risk, mods, customer_rows=cust, settings=_risk_settings(s, company.id), history=_risk_history(s, company.id),
                             today=datetime.now().date(), currency=getattr(company, "currency", None) or "SAR", sector=_risk_sector(company))
    res["module_errors"] = errors
    res["role"] = role
    res["can_edit"] = role in ("owner", "accountant", "manager")
    res["can_edit_rules"] = role == "owner"
    return res


@app.get("/company/risk-drivers-intelligence")
def company_risk_drivers_intelligence(user: User = Depends(get_current_user)):
    with Session(engine) as s:
        company, role = _risk_scope(s, user)
        res = _drivers_result(s, company, role)
        log_audit(company.id, user.id, user.name, "risk_drivers_view", "risk_drivers", f"role={role}")
        return res


def _driver_register_row(s, company, user, drv, as_of):
    """صف السجل للمسبب (رئيسي = مفتاحه · مساند = x:مفتاحه) — خط الأساس من حساب الخادم عند أول حفظ."""
    rk = drv["register_key"]
    row = _risk_row(s, company, rk)
    if not row:
        row = CompanyRisk(company_id=company.id, risk_key=rk, title=drv["name_ar"], category=drv["category"], level=drv["level"] or "",
                          baseline_score=drv["score"], baseline_value=drv["value"] if isinstance(drv["value"], (int, float)) else None,
                          baseline_date=as_of, created_by=(user.name or user.email)[:100], created_on=datetime.now().strftime("%Y-%m-%d"))
    else:
        row.level = drv["level"] or row.level
    return row


@app.post("/company/risk-drivers/to-decision")
def company_risk_drivers_to_decision(data: dict, user: User = Depends(get_current_user)):
    """مسبب → توصية → قرار + إجراء (المسؤول، الاستحقاق، الأثر المتوقع). الأرقام من الخادم فقط."""
    with Session(engine) as s:
        company, role = _risk_scope(s, user, need="edit")
        _exec_scope(s, user, need="edit")
        key = str(data.get("driver_key") or "")
        res = _drivers_result(s, company, role, save_snapshot=False)
        drv = next((d for d in res.get("drivers", []) if d["key"] == key and d["level"] in ("medium", "high", "critical")), None)
        if not drv:
            raise HTTPException(404, "المسبب غير موجود أو لم يعد مرتفعاً أو خارج صلاحيتك")
        rec = drv.get("recommendation") or {}
        exp = rec.get("expected_impact") or {}
        d = CompanyDecision(
            company_id=company.id, title=str(data.get("title") or f"معالجة مسبب: {drv['name_ar']}")[:200],
            detail=" · ".join(drv["evidence"])[:1000], owner=str(data.get("owner") or "")[:100], due_date=str(data.get("due_date") or "")[:20],
            kpi=key, status="open", baseline_sales=_company_total_sales(s, company.id), expected_impact=(exp.get("ar") or "غير قابل للتقدير")[:200],
            linked_to=f"risk_driver:{key}|{res['as_of']}"[:200], rationale=(rec.get("text_ar") or "")[:500], metric_id=key,
            baseline_value=drv["value"] if isinstance(drv["value"], (int, float)) else None, expected_impact_value=exp.get("amount"),
            impact_status="expected", source_signal=f"risk_driver:{key}", problem_type=key, decision_type="risk_driver",
            outcome_status="pending_measurement", created_by=user.name or user.email, data_source="drivers_engine", updated_at=datetime.now())
        s.add(d); s.commit(); s.refresh(d)
        pr = (drv.get("priority") or {}).get("code")
        created = []
        for a in (data.get("actions") or [{"title": rec.get("text_ar") or drv["name_ar"]}])[:10]:
            act = CompanyAction(company_id=company.id, decision_id=d.id, title=str(a.get("title") or "")[:200], owner=str(a.get("owner") or d.owner)[:100],
                                priority="P1" if pr in ("immediate", "high") else "P2", due_date=str(a.get("due_date") or d.due_date)[:20],
                                start_date=datetime.now().strftime("%Y-%m-%d"), updated_at=datetime.now())
            s.add(act); s.commit(); s.refresh(act); created.append(act.id)
        row = _driver_register_row(s, company, user, drv, res["as_of"])
        before = row.status or "detected"
        row.status, row.decision_id, row.updated_at = "decision", d.id, datetime.now()
        if data.get("owner"):
            row.owner = str(data["owner"])[:100]
        if data.get("due_date") and _valid_date(str(data["due_date"])):
            row.due_date = str(data["due_date"])
        if not row.mitigation:
            row.mitigation = (rec.get("text_ar") or "")[:1000]
        _risk_log(row, user, before, "decision", f"قرار #{d.id} من مركز المسببات")
        s.add(row); s.commit()
        log_audit(company.id, user.id, user.name, "decision_from_risk_driver", f"risk_driver:{key}",
                  f"decision={d.id} score={drv['score']} priority={pr} actions={created}")
        return {"ok": True, "decision_id": d.id, "action_ids": created}


@app.post("/company/risk-drivers/update")
def company_risk_drivers_update(data: dict, user: User = Depends(get_current_user)):
    with Session(engine) as s:
        company, role = _risk_scope(s, user, need="edit")
        key = str(data.get("driver_key") or "")
        res = _drivers_result(s, company, role, save_snapshot=False)
        drv = next((d for d in res.get("drivers", []) if d["key"] == key), None)
        if not drv or not (drv.get("register") or drv["level"] in ("medium", "high", "critical")):
            raise HTTPException(404, "المسبب غير موجود أو خارج صلاحيتك")
        st = str(data.get("status") or (drv.get("register") or {}).get("status") or "detected")
        if st not in _RISK_STATUSES:
            raise HTTPException(422, "حالة غير صالحة")
        if st == "approved" and role != "owner":
            raise HTTPException(403, "اعتماد القرار للمالك فقط")
        row = _driver_register_row(s, company, user, drv, res["as_of"])
        before = row.status or "detected"
        if "due_date" in data:
            dd = str(data.get("due_date") or "")
            if dd and not _valid_date(dd):
                raise HTTPException(422, "تاريخ غير صالح (YYYY-MM-DD)")
            row.due_date = dd
        if "owner" in data:
            row.owner = str(data.get("owner") or "")[:100]
        if "mitigation" in data:
            row.mitigation = str(data.get("mitigation") or "")[:1000]
        row.status, row.updated_at = st, datetime.now()
        _risk_log(row, user, before, st, str(data.get("note") or ""))
        s.add(row); s.commit()
        log_audit(company.id, user.id, user.name, "risk_driver_update", f"risk_driver:{key}", f"{before}->{st} owner={row.owner}")
        return {"ok": True, "status": st}


@app.post("/company/risk-drivers/targets")
def company_risk_drivers_targets(data: dict, user: User = Depends(get_current_user)):
    """أهداف الشركة للمسببات (الحالي مقابل الهدف). فارغ = الحد المقبول الافتراضي."""
    with Session(engine) as s:
        company, role = _risk_scope(s, user, need="settings")
        re_, de = _load_p24("risk_engine"), _load_p24("drivers_engine")
        if re_ is None or de is None:
            raise HTTPException(503, "المحرّك غير متاح — " + _p23_diagnostic())
        allowed = set(re_.DRIVERS) | set(de.SUPPORT)
        tg = {}
        for k, v in (data.get("targets") or {}).items():
            if k not in allowed or v in (None, ""):
                continue
            try:
                tg[k] = float(v)
            except (TypeError, ValueError):
                raise HTTPException(422, f"هدف غير رقمي: {k}")
        row = s.exec(select(CompanyRiskSetting).where(CompanyRiskSetting.company_id == company.id)).first() or CompanyRiskSetting(company_id=company.id)
        try:
            cur = json.loads(row.settings_json or "{}")
        except (TypeError, ValueError):
            cur = {}
        old = cur.get("targets") or {}
        cur["targets"] = tg
        row.settings_json, row.updated_by, row.updated_at = json.dumps(cur, ensure_ascii=False), (user.name or user.email)[:100], datetime.now()
        s.add(row); s.commit()
        log_audit(company.id, user.id, user.name, "risk_driver_targets", "risk_drivers", json.dumps({"before": old, "after": tg}, ensure_ascii=False)[:500])
        return {"ok": True}


@app.post("/company/risk-drivers/ai-insights")
def company_risk_drivers_ai_insights(data: dict, request: Request, user: User = Depends(get_current_user)):
    """AI يبدأ من نتائج المحرك (المساهمة، الدليل، الفجوة) ولا يخمّن — يشرح فقط."""
    with Session(engine) as s:
        company, role = _risk_scope(s, user)
        res = _drivers_result(s, company, role, save_snapshot=False)
    if not res.get("has_data"):
        raise HTTPException(422, res.get("message_ar") or "لا توجد بيانات كافية")
    gw = _load_p24("ai_gateway")
    if gw is None:
        raise HTTPException(503, "بوابة الذكاء الاصطناعي غير متاحة — " + _p23_diagnostic())
    q = str(data.get("question") or "ليش الربحية نزلت؟")[:300]
    el = [d for d in res["drivers"] if d["level"] in ("medium", "high", "critical") or d.get("contribution")]
    ctx = {"as_of": res["as_of"], "risk_index": res["risk"]["index"], "narratives": res["narratives"], "contribution_rule": res["contribution_rule_ar"],
           "drivers": [{k: d.get(k) for k in ("name_ar", "kind_ar", "category_ar", "value", "unit", "level_ar", "evidence", "impacts", "gap", "contribution",
                                              "components", "trend", "persistence", "candidates", "recommendation", "priority")} | {"confidence": d["confidence"]["pct"]}
                       for d in el][:25],
           "chains": res["chains"], "executive": res["executive"], "data_needed": res["data"]["data_needed"],
           "rules": {k: res["rules"][k] for k in ("priority_ar", "candidate_ar")}}
    out = gw.request_ai_analysis(gw.GeminiProvider(company_gemini), ctx,
                                 q + " — ابدأ من نتائج المحرك: المسبب الأكثر مساهمة ثم الذي يليه، مع الدليل والأرقام كما هي. "
                                     "لا تحسب أرقاماً جديدة ولا تخمّن، وقل «سبب مرجّح» لا «السبب المؤكد». اختم بثلاثة إجراءات من توصيات المحرك.",
                                 trust_report={"overall_score": int(res["risk"]["confidence"]["pct"] or 0),
                                               "status": "pass" if (res["risk"]["confidence"]["pct"] or 0) >= 80 else "warning",
                                               "has_critical_fail": False, "main_causes": []},
                                 lang=get_lang(request), company=company)
    log_audit(company.id, user.id, user.name, "risk_drivers_ai_insights", "risk_drivers", f"q={q[:80]}")
    return {"as_of": res["as_of"], "question": q, "ai": out}


# ═══════════════════════════════════════════════════════════
#  Phase 3.5 — Sector Benchmark Intelligence (أين تقف الشركة مقابل معيار قطاعها؟)
#  طبقة معايير موثّقة (منصّة/شركة/أقران مجهّلون) + نفس نتائج الوحدات والمخاطر والمسببات
# ═══════════════════════════════════════════════════════════
_BENCH_FIELDS = ("metric", "sector", "sub_sector", "country", "region", "city", "size_segment", "business_model", "period", "period_end", "basis",
                 "value", "p10", "p25", "p50", "p75", "p90", "sample_size", "source", "source_url", "methodology", "confidence", "published_on", "version")


def _bench_settings(s, company_id):
    r = s.exec(select(CompanySectorSetting).where(CompanySectorSetting.company_id == company_id)).first()
    try:
        return json.loads(r.settings_json or "{}") if r else {}
    except (TypeError, ValueError):
        return {}


def _bench_profile(s, company):
    st = _bench_settings(s, company.id)
    city = st.get("city")
    if not city:
        br = s.exec(select(CompanyBranch).where(CompanyBranch.company_id == company.id)).all()
        cities = [b.city for b in br if getattr(b, "city", "")]
        city = max(set(cities), key=cities.count) if cities else None
    return {"sector": _risk_sector(company), "sub_sector": st.get("sub_sector") or None, "country": getattr(company, "country", None) or "SA",
            "region": st.get("region") or None, "city": city, "business_model": st.get("business_model") or None,
            "size_segment": st.get("size_segment") or None, "size_override": bool(st.get("size_segment")), "peer_opt_in": bool(st.get("peer_opt_in"))}


def _bench_row_dict(b):
    d = {k: getattr(b, k) for k in _BENCH_FIELDS}
    d.update({"id": b.id, "origin": "platform" if b.company_id is None else "company", "is_active": bool(b.is_active)})
    return d


def _bench_datasets(s, company):
    rows = s.exec(select(BenchmarkDataset).where(BenchmarkDataset.is_active == 1)).all()
    return [_bench_row_dict(b) for b in rows if b.company_id is None or b.company_id == company.id]


def _bench_peers(s, company, profile):
    """أقران نبّاه: آخر قيمة لكل شركة أخرى موافقة في نفس القطاع — تجميع فقط (≥ 5 شركات)، لا تُعاد أي قيمة فردية."""
    be = _load_p24("benchmark_engine")
    rows = s.exec(select(CompanyBenchmarkComparison).where(CompanyBenchmarkComparison.sector == profile["sector"],
                                                           CompanyBenchmarkComparison.share_ok == 1,
                                                           CompanyBenchmarkComparison.company_id != company.id)).all()
    latest = {}
    for r in rows:
        k = (r.company_id, r.metric)
        if k not in latest or (r.period or "") > (latest[k].period or ""):
            latest[k] = r
    def group(same_size):
        vals = {}
        for (cid, m), r in latest.items():
            if r.company_value is None or (same_size and r.size_segment != profile.get("size_segment")):
                continue
            vals.setdefault(m, []).append(r.company_value)
        return vals
    out = []
    if profile.get("size_segment"):
        out = be.peer_benchmarks(group(True), profile["sector"], profile["size_segment"])
    got = {p["metric"] for p in out}
    out += [p for p in be.peer_benchmarks(group(False), profile["sector"], "all") if p["metric"] not in got]
    return out


def _bench_history(s, company_id):
    rows = s.exec(select(CompanyBenchmarkComparison).where(CompanyBenchmarkComparison.company_id == company_id)
                  .order_by(CompanyBenchmarkComparison.period.desc()).limit(2000)).all()
    return [{"metric": r.metric, "period": r.period, "percentile": r.percentile, "gap": r.gap, "gap_signed": r.gap_signed} for r in rows]


def _bench_result(s, company, role, *, save=True):
    be, de = _load_p24("benchmark_engine"), _load_p24("drivers_engine")
    if be is None or de is None:
        raise HTTPException(503, "محرّك المقارنة غير متاح — " + _p23_diagnostic())
    risk = _risk_result(s, company, role, save_snapshot=save)
    mods, cust, errors = _risk_modules(s, company, role)
    dv = de.analyze_drivers(risk, mods, customer_rows=cust, settings=_risk_settings(s, company.id), history=_risk_history(s, company.id),
                            today=datetime.now().date(), sector=_risk_sector(company)) if risk.get("has_data") else {}
    profile = _bench_profile(s, company)
    res = be.analyze_benchmark(mods, sales_rows=cust, risk=risk, drivers=dv, datasets=_bench_datasets(s, company), peers=_bench_peers(s, company, profile),
                               profile=profile, history=_bench_history(s, company.id), today=datetime.now().date(),
                               categories=_RISK_CATS_BY_ROLE.get(role), currency=getattr(company, "currency", None) or "SAR")
    # لقطة شهرية لكل مؤشر (العرض الكامل فقط) — أساس تاريخ الموقع، ومصدر معيار الأقران إن وافقت الشركة
    if save and _RISK_CATS_BY_ROLE.get(role) is None and res.get("snapshot"):
        per = datetime.now().strftime("%Y-%m")
        old = {r.metric: r for r in s.exec(select(CompanyBenchmarkComparison).where(CompanyBenchmarkComparison.company_id == company.id,
                                                                                    CompanyBenchmarkComparison.period == per)).all()}
        prof = res["profile"]
        for x in res["snapshot"]:
            r = old.get(x["metric"]) or CompanyBenchmarkComparison(company_id=company.id, period=per, metric=x["metric"])
            for k in ("company_value", "benchmark_value", "gap", "gap_signed", "gap_pct", "percentile", "position", "confidence", "origin", "benchmark_id"):
                setattr(r, k, x.get(k) if k != "benchmark_id" else (str(x.get(k)) if x.get(k) is not None else None))
            r.sector, r.size_segment, r.region = prof["sector"], prof.get("size_segment"), prof.get("region")
            r.share_ok = 1 if prof.get("peer_opt_in") else 0
            r.assessed_on, r.updated_at = datetime.now().strftime("%Y-%m-%d"), datetime.now()
            s.add(r)
        s.commit()
    res["module_errors"] = errors
    res["role"] = role
    res["can_edit"] = role in ("owner", "accountant", "manager")
    res["can_edit_rules"] = role == "owner"
    return res


@app.get("/company/sector-benchmark-intelligence")
def company_sector_benchmark_intelligence(user: User = Depends(get_current_user)):
    with Session(engine) as s:
        company, role = _risk_scope(s, user)
        res = _bench_result(s, company, role)
        log_audit(company.id, user.id, user.name, "sector_benchmark_view", "sector_benchmark", f"compared={res['summary']['compared']} role={role}")
        return res


@app.post("/company/sector-benchmark/profile")
def company_sector_benchmark_profile(data: dict, user: User = Depends(get_current_user)):
    """ملف القطاع: القطاع الفرعي، المنطقة، المدينة، نموذج العمل، الحجم (اختياري)، والموافقة على المشاركة المجهّلة في معيار الأقران."""
    with Session(engine) as s:
        company, role = _risk_scope(s, user, need="settings")
        be = _load_p24("benchmark_engine")
        st = _bench_settings(s, company.id)
        old = dict(st)
        reg = data.get("region") or None
        if reg and reg not in be.REGIONS:
            raise HTTPException(422, "منطقة غير معروفة")
        seg = data.get("size_segment") or None
        if seg and seg not in be.SIZE_AR:
            raise HTTPException(422, "الحجم: micro / small / medium / large")
        st.update({"sub_sector": str(data.get("sub_sector") or "")[:60] or None, "region": reg, "city": str(data.get("city") or "")[:60] or None,
                   "business_model": str(data.get("business_model") or "")[:60] or None, "size_segment": seg,
                   "peer_opt_in": bool(data.get("peer_opt_in"))})
        row = s.exec(select(CompanySectorSetting).where(CompanySectorSetting.company_id == company.id)).first() or CompanySectorSetting(company_id=company.id)
        row.settings_json, row.updated_by, row.updated_at = json.dumps(st, ensure_ascii=False), (user.name or user.email)[:100], datetime.now()
        s.add(row)
        if not st["peer_opt_in"]:          # سحب الموافقة يسحب كل قيم الشركة من معيار الأقران فوراً
            for r in s.exec(select(CompanyBenchmarkComparison).where(CompanyBenchmarkComparison.company_id == company.id)).all():
                r.share_ok = 0
                s.add(r)
        s.commit()
        log_audit(company.id, user.id, user.name, "sector_profile", "sector_benchmark", json.dumps({"before": old, "after": st}, ensure_ascii=False)[:500])
        return {"ok": True}


def _bench_clean(data):
    be = _load_p24("benchmark_engine")
    b = {k: data.get(k) for k in _BENCH_FIELDS}
    for k in ("value", "p10", "p25", "p50", "p75", "p90", "sample_size"):
        if b.get(k) in ("", None):
            b[k] = None
    errs = be.validate_benchmark(b)
    if b.get("metric") and b["metric"] not in be.METRICS and str(b["metric"]).split(":", 1)[-1] not in be.SECTOR_KPI_META:
        errs.append("مؤشر غير معروف")
    if errs:
        raise HTTPException(422, " · ".join(errs))
    for k in ("value", "p10", "p25", "p50", "p75", "p90"):
        b[k] = float(b[k]) if b.get(k) is not None else None
    b["sample_size"] = int(float(b["sample_size"])) if b.get("sample_size") is not None else None
    for k in ("sector", "sub_sector", "country", "region", "city", "size_segment", "business_model", "period", "period_end", "basis", "source",
              "source_url", "methodology", "confidence", "published_on", "version", "metric"):
        b[k] = (str(b[k]).strip()[:500] if b.get(k) not in (None, "") else None)
    b["country"] = b["country"] or "SA"
    b["size_segment"] = b["size_segment"] or "all"
    return b


@app.get("/company/sector-benchmark/datasets")
def company_sector_benchmark_datasets(user: User = Depends(get_current_user)):
    with Session(engine) as s:
        company, role = _risk_scope(s, user)
        be = _load_p24("benchmark_engine")
        mets = [{"code": k, "ar": m["ar"], "unit": m["unit"], "group_ar": be.GROUPS[m["g"]]} for k, m in be.METRICS.items()]
        mets += [{"code": "sector:" + k, "ar": be.SECTOR_KPI_AR.get(k, k), "unit": "SAR" if k == "revenue_per_employee" else "%", "group_ar": be.GROUPS["sector"]}
                 for k in be.SECTOR_KPI_META]
        return {"items": _bench_datasets(s, company), "metrics": mets,
                "regions": be.REGIONS, "sizes": be.SIZE_AR, "profile": _bench_profile(s, company), "can_edit": role == "owner"}


@app.post("/company/sector-benchmark/datasets")
def company_sector_benchmark_add(data: dict, user: User = Depends(get_current_user)):
    """معيار خاص بالشركة (من تقرير قطاعي لديها) — المصدر والمنهجية والفترة والثقة إلزامية. يُرى لشركتك فقط."""
    with Session(engine) as s:
        company, role = _risk_scope(s, user, need="settings")
        items = data.get("items") if isinstance(data.get("items"), list) else [data]
        ids = []
        for it in items[:200]:
            b = _bench_clean(it)
            row = BenchmarkDataset(company_id=company.id, created_by=(user.name or user.email)[:100], **b)
            s.add(row); s.commit(); s.refresh(row); ids.append(row.id)
        log_audit(company.id, user.id, user.name, "benchmark_add", "sector_benchmark", f"ids={ids}")
        return {"ok": True, "ids": ids}


@app.post("/company/sector-benchmark/datasets/deactivate")
def company_sector_benchmark_deactivate(data: dict, user: User = Depends(get_current_user)):
    with Session(engine) as s:
        company, role = _risk_scope(s, user, need="settings")
        row = s.get(BenchmarkDataset, int(data.get("id") or 0))
        if not row or row.company_id != company.id:
            raise HTTPException(404, "المعيار غير موجود (معايير المنصّة تُدار من الإدارة)")
        row.is_active = 0
        s.add(row); s.commit()
        log_audit(company.id, user.id, user.name, "benchmark_deactivate", f"benchmark:{row.id}", row.metric)
        return {"ok": True}


@app.get("/admin/benchmarks")
def admin_benchmarks_list(_: bool = Depends(verify_admin)):
    with Session(engine) as s:
        _ensure_data_tables()
        rows = s.exec(select(BenchmarkDataset).where(BenchmarkDataset.company_id == None)).all()  # noqa: E711
        return {"items": [_bench_row_dict(b) for b in rows]}


@app.post("/admin/benchmarks")
def admin_benchmarks_add(data: dict, _: bool = Depends(verify_admin)):
    """معايير المنصّة (لكل العملاء في القطاع) — من مصادر موثّقة فقط، مع المصدر والمنهجية والعيّنة والثقة."""
    with Session(engine) as s:
        _ensure_data_tables()
        items = data.get("items") if isinstance(data.get("items"), list) else [data]
        ids = []
        for it in items[:500]:
            b = _bench_clean(it)
            row = BenchmarkDataset(company_id=None, created_by="admin", **b)
            s.add(row); s.commit(); s.refresh(row); ids.append(row.id)
        _logger.info(f"admin benchmarks added: {ids}")
        return {"ok": True, "ids": ids}


@app.post("/admin/benchmarks/deactivate")
def admin_benchmarks_deactivate(data: dict, _: bool = Depends(verify_admin)):
    with Session(engine) as s:
        row = s.get(BenchmarkDataset, int(data.get("id") or 0))
        if not row or row.company_id is not None:
            raise HTTPException(404, "معيار المنصّة غير موجود")
        row.is_active = 0
        s.add(row); s.commit()
        return {"ok": True}


@app.post("/company/sector-benchmark/to-decision")
def company_sector_benchmark_to_decision(data: dict, user: User = Depends(get_current_user)):
    """فجوة قطاعية → قرار + إجراء. الأرقام من الخادم؛ الأثر «توضيحي» وليس مضموناً."""
    with Session(engine) as s:
        company, role = _risk_scope(s, user, need="edit")
        _exec_scope(s, user, need="edit")
        code = str(data.get("metric") or "")
        res = _bench_result(s, company, role, save=False)
        x = (res.get("comparisons") or {}).get(code)
        if not x or x.get("position") not in ("below", "critical"):
            raise HTTPException(404, "لا توجد فجوة قطاعية لهذا المؤشر أو خارج صلاحيتك")
        why = x.get("why") or []
        imp = x.get("impact") or {}
        d = CompanyDecision(
            company_id=company.id, title=str(data.get("title") or f"سد فجوة قطاعية: {x['name_ar']}")[:200],
            detail=f"الشركة {x['company']} مقابل معيار {x['benchmark']} ({x['source'].get('source')} · {x['source'].get('period')}) · الثقة {x['confidence_ar']}"[:1000],
            owner=str(data.get("owner") or "")[:100], due_date=str(data.get("due_date") or "")[:20], kpi=code, status="open",
            baseline_sales=_company_total_sales(s, company.id),
            expected_impact=(f"{imp.get('type_ar')} {imp.get('amount')} — توضيحي وليس مضموناً" if imp.get("amount") else "غير قابل للتقدير")[:200],
            linked_to=f"sector_gap:{code}|{res['as_of']}"[:200], rationale=(why[0].get("action_ar") if why else "راجع مسببات الفجوة")[:500] or "",
            metric_id=code, baseline_value=x["company"], expected_impact_value=imp.get("amount"), impact_status="expected",
            source_signal=f"sector_gap:{code}", problem_type=f"sector_gap:{code}", decision_type="sector_benchmark",
            outcome_status="pending_measurement", created_by=user.name or user.email, data_source="benchmark_engine", updated_at=datetime.now())
        s.add(d); s.commit(); s.refresh(d)
        created = []
        for a in (data.get("actions") or [{"title": (why[0].get("action_ar") if why else None) or f"خطة لسد فجوة {x['name_ar']}"}])[:10]:
            act = CompanyAction(company_id=company.id, decision_id=d.id, title=str(a.get("title") or "")[:200], owner=str(a.get("owner") or d.owner)[:100],
                                priority="P1" if x["position"] == "critical" else "P2", due_date=str(a.get("due_date") or d.due_date)[:20],
                                start_date=datetime.now().strftime("%Y-%m-%d"), updated_at=datetime.now())
            s.add(act); s.commit(); s.refresh(act); created.append(act.id)
        log_audit(company.id, user.id, user.name, "decision_from_sector_gap", f"sector_gap:{code}", f"decision={d.id} gap={x['gap']} actions={created}")
        return {"ok": True, "decision_id": d.id, "action_ids": created}


@app.post("/company/sector-benchmark/ai-insights")
def company_sector_benchmark_ai(data: dict, request: Request, user: User = Depends(get_current_user)):
    """AI يشرح فقط: يتلقى المعيار ومصدره وفترته وعيّنته ومنهجيته وثقته — ولا يقرر متوسط القطاع بنفسه."""
    with Session(engine) as s:
        company, role = _risk_scope(s, user)
        res = _bench_result(s, company, role, save=False)
    gw = _load_p24("ai_gateway")
    if gw is None:
        raise HTTPException(503, "بوابة الذكاء الاصطناعي غير متاحة — " + _p23_diagnostic())
    q = str(data.get("question") or "كيف أداء شركتي مقارنة بالقطاع؟")[:300]
    comp = {k: {kk: v.get(kk) for kk in ("name_ar", "company", "benchmark", "gap", "gap_unit", "position_ar", "percentile", "confidence_ar", "impact", "why", "company_trend")}
            | {"source": {kk: (v.get("source") or {}).get(kk) for kk in ("origin_ar", "source", "period", "sample_size", "methodology", "geo_ar", "size_ar", "freshness_ar")}}
            for k, v in res["comparisons"].items() if v["status"] == "compared"}
    ctx = {"profile": res["profile"], "summary": res["summary"], "compared": comp,
           "unavailable": [v["name_ar"] for v in res["comparisons"].values() if v["status"] != "compared"],
           "opportunities": res["opportunities"], "insights": res["insights"], "branches": res["branches"]["rows"][:6], "rules": res["rules"]}
    out = gw.request_ai_analysis(gw.GeminiProvider(company_gemini), ctx,
                                 q + " — استخدم المعايير المعطاة فقط مع مصدرها وفترتها وثقتها. لا تقدّر متوسط السوق بنفسك ولا تذكر أرقاماً غير موجودة. "
                                     "المؤشر بلا معيار قل إنه «غير متاح». الأثر المالي توضيحي وليس مضموناً.",
                                 trust_report={"overall_score": int(res["quality"]["coverage_pct"] or 0), "status": "warning", "has_critical_fail": False, "main_causes": []},
                                 lang=get_lang(request), company=company)
    log_audit(company.id, user.id, user.name, "sector_benchmark_ai", "sector_benchmark", f"q={q[:80]}")
    return {"question": q, "ai": out}


# ═══════════════════════════════════════════════════════════
#  Phase 3.7 — Performance Prediction (توقع → سيناريو → قرار → إجراء → قياس)
#  يقرأ نفس البيانات الموحّدة ونتائج 3.3/3.4/3.5 — طبقة تنبؤ فقط (تشغيلات + توقعات محفوظة للدقة)
# ═══════════════════════════════════════════════════════════
def _pred_settings(s, company):
    r = s.exec(select(CompanyPredictionSetting).where(CompanyPredictionSetting.company_id == company.id)).first()
    try:
        st = json.loads(r.settings_json or "{}") if r else {}
    except (TypeError, ValueError):
        st = {}
    tg = dict(st.get("targets") or {})
    if tg.get("annual_revenue"):
        tg["source_ar"] = "هدف التنبؤ (إعدادات الصفحة)"
    else:
        bud = ((_fin_settings(s, company.id).get("budget") or {}).get("revenue_monthly"))
        if bud:
            tg.update({"annual_revenue": float(bud) * 12, "source_ar": "موازنة الإيراد الشهرية في الوحدة المالية × 12"})
        else:
            bt = sum((b.target_sales or 0) for b in s.exec(select(CompanyBranch).where(CompanyBranch.company_id == company.id)).all())
            if bt:
                tg.update({"annual_revenue": bt * 12, "source_ar": "مجموع أهداف الفروع الشهرية × 12"})
    return {"targets": tg, "fy_start": int(getattr(company, "fiscal_year_start", 1) or 1), "saved": st}


def _pred_stored(s, company_id):
    rows = s.exec(select(CompanyPrediction).where(CompanyPrediction.company_id == company_id).limit(20000)).all()
    return [{"metric": r.metric, "scope": r.scope, "period": r.period, "h": r.h, "value": r.value, "lower": r.lower, "upper": r.upper,
             "confidence": r.confidence, "made_on": r.made_on, "run_id": r.run_id} for r in rows]


def _pred_decisions(s, company_id):
    out = []
    for d in s.exec(select(CompanyDecision).where(CompanyDecision.company_id == company_id, CompanyDecision.decision_type == "prediction")).all():
        out.append({"id": d.id, "title": d.title, "created_on": d.created_at.strftime("%Y-%m-%d") if d.created_at else "",
                    "metric": "net_profit" if (d.kpi or "").startswith("profit") else "revenue", "owner": d.owner, "status": d.status})
    return out


def _pred_result(s, company, role, *, save=True):
    pe, de, be = _load_p24("prediction_engine"), _load_p24("drivers_engine"), _load_p24("benchmark_engine")
    if pe is None or de is None:
        raise HTTPException(503, "محرّك التنبؤ غير متاح — " + _p23_diagnostic())
    risk = _risk_result(s, company, role, save_snapshot=save)
    mods, cust, errors = _risk_modules(s, company, role)
    today_ = datetime.now().date()
    dv = de.analyze_drivers(risk, mods, customer_rows=cust, settings=_risk_settings(s, company.id), history=_risk_history(s, company.id),
                            today=today_, sector=_risk_sector(company)) if risk.get("has_data") else {}
    bz = None
    if be is not None and risk.get("has_data"):
        prof = _bench_profile(s, company)
        bz = be.analyze_benchmark(mods, sales_rows=cust, risk=risk, drivers=dv, datasets=_bench_datasets(s, company), peers=_bench_peers(s, company, prof),
                                  profile=prof, history=_bench_history(s, company.id), today=today_, categories=_RISK_CATS_BY_ROLE.get(role))
    st = _pred_settings(s, company)
    res = pe.analyze_prediction(mods, sales_rows=cust, risk=risk, drivers=dv, bench=bz, settings={"targets": st["targets"], "fy_start": st["fy_start"]},
                                stored=_pred_stored(s, company.id), decisions=_pred_decisions(s, company.id), sector=_risk_sector(company), today=today_,
                                categories=_RISK_CATS_BY_ROLE.get(role), currency=getattr(company, "currency", None) or "SAR")
    # حفظ تشغيل واحد لكل شهر بيانات جديد (العرض الكامل) — التوقع الأصلي يبقى كما هو لقياس الدقة لاحقاً
    snap = res.get("snapshot")
    if save and snap and _RISK_CATS_BY_ROLE.get(role) is None:
        exists = s.exec(select(CompanyPredictionRun).where(CompanyPredictionRun.company_id == company.id, CompanyPredictionRun.base_period == snap["base_period"],
                                                           CompanyPredictionRun.model_version == snap["model"])).first()
        if not exists:
            run = CompanyPredictionRun(company_id=company.id, base_period=snap["base_period"], model_version=snap["model"], horizon=snap["horizon"],
                                       confidence=snap["confidence"], drivers_json=json.dumps(snap["drivers"], ensure_ascii=False)[:20000],
                                       made_on=today_.isoformat())
            s.add(run); s.commit(); s.refresh(run)
            for p in snap["predictions"]:
                s.add(CompanyPrediction(company_id=company.id, run_id=run.id, made_on=today_.isoformat(), **p))
            s.commit()
    runs = s.exec(select(CompanyPredictionRun).where(CompanyPredictionRun.company_id == company.id).order_by(CompanyPredictionRun.made_on.desc()).limit(24)).all()
    res["runs"] = [{"id": r.id, "base_period": r.base_period, "model": r.model_version, "confidence": r.confidence, "made_on": r.made_on} for r in runs]
    res["targets_source"] = st["targets"].get("source_ar")
    res["saved_settings"] = st["saved"]
    res["module_errors"] = errors
    res["role"] = role
    res["can_edit"] = role in ("owner", "accountant", "manager")
    res["can_edit_rules"] = role == "owner"
    return res


@app.get("/company/performance-prediction")
def company_performance_prediction(user: User = Depends(get_current_user)):
    with Session(engine) as s:
        company, role = _risk_scope(s, user)
        res = _pred_result(s, company, role)
        res.pop("scenario_baseline", None)
        log_audit(company.id, user.id, user.name, "prediction_view", "prediction", f"status={res.get('status')} role={role}")
        return res


@app.post("/company/performance-prediction/scenario")
def company_prediction_scenario(data: dict, user: User = Depends(get_current_user)):
    """محاكاة «ماذا لو» على خط الأساس المتوقع — نسخة حسابية مؤقتة لا تعدّل أي بيانات. الحفظ اختياري."""
    with Session(engine) as s:
        company, role = _risk_scope(s, user)
        pe = _load_p24("prediction_engine")
        a, errs = pe.validate_assumptions(data.get("assumptions") or {})
        if errs:
            raise HTTPException(422, " · ".join(errs))
        res = _pred_result(s, company, role, save=False)
        base = res.get("scenario_baseline")
        if not base:
            raise HTTPException(422, "المحاكاة تحتاج توقع الربح (تكلفة المنتجات والمصروفات) وصلاحية الاطلاع على الربح")
        name = str(data.get("name") or "سيناريو مخصص")[:120]
        b0 = pe.simulate(base, {}, "الأساس")
        r = pe.simulate(base, a, name)
        r.update({"delta_revenue": round(r["revenue"] - b0["revenue"], 2), "delta_profit": round(r["net_profit"] - b0["net_profit"], 2),
                  "delta_cash": round((r["cash_end"] or 0) - (b0["cash_end"] or 0), 2) if r["cash_end"] is not None else None, "base": b0})
        sid = None
        if data.get("save"):
            if role not in ("owner", "accountant", "manager"):
                raise HTTPException(403, "غير مصرّح بحفظ السيناريو")
            sc = CompanyScenario(company_id=company.id, created_by=(user.name or user.email)[:100], name=name, scenario_type="prediction",
                                 base_period=res["snapshot"]["base_period"], assumptions=json.dumps(a, ensure_ascii=False),
                                 baseline_values=json.dumps({"revenue": b0["revenue"], "net_profit": b0["net_profit"], "cash_end": b0["cash_end"]}, ensure_ascii=False),
                                 results=json.dumps({k: v for k, v in r.items() if k != "base"}, ensure_ascii=False)[:20000], status="ran",
                                 updated_at=datetime.now(), ran_at=datetime.now())
            s.add(sc); s.commit(); s.refresh(sc); sid = sc.id
        log_audit(company.id, user.id, user.name, "prediction_scenario", "prediction", json.dumps({"assumptions": a, "saved": sid}, ensure_ascii=False)[:400])
        return {"ok": True, "scenario": r, "saved_id": sid}


@app.get("/company/performance-prediction/scenarios")
def company_prediction_scenarios(user: User = Depends(get_current_user)):
    with Session(engine) as s:
        company, role = _risk_scope(s, user)
        rows = s.exec(select(CompanyScenario).where(CompanyScenario.company_id == company.id, CompanyScenario.scenario_type == "prediction")
                      .order_by(CompanyScenario.created_at.desc()).limit(50)).all()
        out = []
        for r in rows:
            try:
                res = json.loads(r.results or "{}")
            except (TypeError, ValueError):
                res = {}
            out.append({"id": r.id, "name": r.name, "created_by": r.created_by, "base_period": r.base_period, "assumptions": json.loads(r.assumptions or "{}"),
                        "revenue": res.get("revenue"), "net_profit": res.get("net_profit"), "delta_profit": res.get("delta_profit"),
                        "cash_end": res.get("cash_end"), "created_at": r.created_at.isoformat() if r.created_at else None})
        return {"items": out}


@app.post("/company/performance-prediction/settings")
def company_prediction_settings(data: dict, user: User = Depends(get_current_user)):
    with Session(engine) as s:
        company, role = _risk_scope(s, user, need="settings")
        tg = {}
        for k in ("annual_revenue", "annual_profit"):
            v = data.get(k)
            if v not in (None, ""):
                try:
                    tg[k] = float(v)
                except (TypeError, ValueError):
                    raise HTTPException(422, "الهدف يجب أن يكون رقماً")
                if k == "annual_revenue" and tg[k] <= 0:
                    raise HTTPException(422, "هدف الإيراد يجب أن يكون موجباً")
        row = s.exec(select(CompanyPredictionSetting).where(CompanyPredictionSetting.company_id == company.id)).first() or CompanyPredictionSetting(company_id=company.id)
        old = row.settings_json or "{}"
        row.settings_json, row.updated_by, row.updated_at = json.dumps({"targets": tg}, ensure_ascii=False), (user.name or user.email)[:100], datetime.now()
        s.add(row); s.commit()
        log_audit(company.id, user.id, user.name, "prediction_settings", "prediction", json.dumps({"before": json.loads(old), "after": tg}, ensure_ascii=False)[:400])
        return {"ok": True}


@app.post("/company/performance-prediction/to-decision")
def company_prediction_to_decision(data: dict, user: User = Depends(get_current_user)):
    """توقع (خطر/فرصة) → قرار + إجراء. خط الأساس = التوقع المحفوظ قبل القرار؛ القياس لاحقاً قبل/بعد."""
    with Session(engine) as s:
        company, role = _risk_scope(s, user, need="edit")
        _exec_scope(s, user, need="edit")
        code = str(data.get("code") or "")
        res = _pred_result(s, company, role, save=True)
        item = next((p for p in res.get("plan") or [] if p["code"] == code), None)
        opp = next((o for o in res.get("opportunities") or [] if o["code"] == code), None)
        if not item and not opp:
            raise HTTPException(404, "التوقع غير موجود أو لم يعد قائماً")
        title = (item or {}).get("prediction_ar") or opp["ar"]
        impact = ((item or {}).get("impact") or {}).get("amount") if item else ((opp.get("range") or [None, None])[1])
        kpi = "profit" if code.split(":")[0] in ("profit_decline", "margin_decline") or code.startswith("recovery") else "revenue"
        d = CompanyDecision(
            company_id=company.id, title=str(data.get("title") or f"معالجة توقع: {title}")[:200],
            detail=" · ".join(((item or {}).get("cause") and [c.get("ar", "") + ": " + c.get("evidence_ar", "") for c in item["cause"]]) or (opp or {}).get("evidence") or [])[:1000],
            owner=str(data.get("owner") or (item or {}).get("owner_ar") or "")[:100], due_date=str(data.get("due_date") or (item or {}).get("due") or "")[:20],
            kpi=kpi, status="open", baseline_sales=_company_total_sales(s, company.id),
            expected_impact=(f"{impact:,.0f} {res.get('currency')} (تقديري من التوقع)" if impact else "غير قابل للتقدير")[:200],
            linked_to=f"prediction:{code}|{(res.get('snapshot') or {}).get('base_period')}"[:200], rationale=((item or {}).get("action_ar") or (opp or {}).get("action_ar") or "")[:500],
            metric_id=kpi, expected_impact_value=impact, impact_status="expected", source_signal=f"prediction:{code}", problem_type=f"prediction:{code}",
            decision_type="prediction", outcome_status="pending_measurement", created_by=user.name or user.email, data_source="prediction_engine", updated_at=datetime.now())
        s.add(d); s.commit(); s.refresh(d)
        act = CompanyAction(company_id=company.id, decision_id=d.id, title=str(data.get("action") or d.rationale or title)[:200], owner=d.owner,
                            priority="P1" if (item or {}).get("level") in ("critical", "high") else "P2", due_date=d.due_date,
                            start_date=datetime.now().strftime("%Y-%m-%d"), updated_at=datetime.now())
        s.add(act); s.commit(); s.refresh(act)
        log_audit(company.id, user.id, user.name, "decision_from_prediction", f"prediction:{code}", f"decision={d.id} impact={impact}")
        return {"ok": True, "decision_id": d.id, "action_ids": [act.id]}


@app.post("/company/performance-prediction/ai-insights")
def company_prediction_ai(data: dict, request: Request, user: User = Depends(get_current_user)):
    """AI يقرأ نتائج محرك التنبؤ ولا يخترع أرقاماً — لا يحسب توقعاً بنفسه."""
    with Session(engine) as s:
        company, role = _risk_scope(s, user)
        res = _pred_result(s, company, role, save=False)
    if res.get("status") != "ok":
        raise HTTPException(422, res.get("message_ar") or "لا يوجد توقع")
    gw = _load_p24("ai_gateway")
    if gw is None:
        raise HTTPException(503, "بوابة الذكاء الاصطناعي غير متاحة — " + _p23_diagnostic())
    q = str(data.get("question") or "ما المتوقع للمبيعات خلال 6 أشهر؟")[:300]
    ctx = {"outlook": res["outlook"], "sales": {k: res["sales"].get(k) for k in ("points", "slope_per_month", "seasonality_ar", "backtest", "confidence", "sufficiency_ar")},
           "profit": {k: res["profit"].get(k) for k in ("status", "total", "now", "forecast_avg", "bridge", "bridge_note_ar")},
           "cash": {k: res["cash"].get(k) for k in ("status", "start_balance", "end_balance", "first_negative")},
           "branches": [{k: b.get(k) for k in ("branch", "current_avg", "forecast_m3", "change_pct", "confidence")} for b in res["branches"]],
           "growth": res["growth"], "drivers": res["drivers"][:12], "gap": res["gap"], "risks": res["risks"], "opportunities": res["opportunities"],
           "scenarios": [{k: x.get(k) for k in ("name", "assumptions", "revenue", "net_profit", "delta_profit", "cash_end")} for x in res["scenarios"]],
           "accuracy": res["accuracy"].get("overall"), "methodology": res["methodology"]}
    out = gw.request_ai_analysis(gw.GeminiProvider(company_gemini), ctx,
                                 q + " — استخدم أرقام محرك التنبؤ كما هي مع الثقة والنطاق. لا تحسب توقعاً جديداً ولا تقل إن الرقم مؤكد. "
                                     "السيناريو محاكاة وليس وعداً. إن لم يُحسب شيء فقل ذلك.",
                                 trust_report={"overall_score": int(res["outlook"]["confidence"]["score"]), "status": "pass" if res["outlook"]["confidence"]["score"] >= 75 else "warning",
                                               "has_critical_fail": False, "main_causes": []},
                                 lang=get_lang(request), company=company)
    log_audit(company.id, user.id, user.name, "prediction_ai", "prediction", f"q={q[:80]}")
    return {"question": q, "ai": out}


# ═══════════════════════════════════════════════════════════
#  Phase 3.8 — Goals & Results Intelligence (هدف → فعلي → توقع → فجوة → إجراء → مسؤول → مرحلة → نتيجة → قبل/بعد)
#  طبقة أهداف فقط: الفعلي والتوقع والمخاطر تُقرأ من محركات 3.3/3.4/3.5/3.7 — لا جداول بيانات مكررة
# ═══════════════════════════════════════════════════════════
_GOAL_TITLES = ("", "ceo", "cfo", "board", "dept_manager", "branch_manager", "employee")
_GOAL_COLLAB_KINDS = ("comment", "mention", "evidence", "request_update", "escalate", "assign_owner", "approve", "reject")


def _goals_scope(s, user):
    """كل أعضاء الشركة يصلون للمركز — ما يرونه يحدده نطاقهم (المحرك يرشّح الأهداف). لا وصول عبر الشركات."""
    _ensure_data_tables()
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    company = s.get(Company, user.company_id)
    if not company:
        raise HTTPException(403, "غير مصرّح")
    if company.is_active != 1:
        raise HTTPException(402, "شركتك قيد التفعيل")
    role = get_user_role(s, user) or "staff"
    sc = s.exec(select(CompanyUserScope).where(CompanyUserScope.company_id == company.id, CompanyUserScope.user_id == user.id)).first()
    title, branch, dept = (sc.title, sc.branch, sc.department) if sc else ("", "", "")
    if not sc and role == "manager":
        m = s.exec(select(CompanyMember).where(CompanyMember.company_id == company.id, CompanyMember.email == user.email)).first()
        if m and m.branch_id:
            b = s.get(CompanyBranch, m.branch_id)
            if b and b.company_id == company.id:
                title, branch = "branch_manager", b.name
    if role == "staff" and not title:
        title = "employee"
    viewer = {"role": role, "user_id": user.id, "name": user.name or "", "email": user.email or "", "title": title or None,
              "branch": branch or None, "department": dept or None}
    return company, role, viewer


def _goal_dict(g):
    return {"id": g.id, "parent_id": g.parent_goal_id, "name": g.name, "objective": g.objective, "pillar": g.pillar or None, "level": g.level or "company",
            "branch": g.branch or None, "department": g.department or None, "owner": g.owner or None, "owner_user_id": g.owner_user_id, "metric": g.metric or None,
            "unit": g.unit or None, "baseline": g.baseline, "target": g.target, "start": g.start_date, "end": g.end_date, "status_override": g.status_override or None,
            "source": g.source, "distribution": g.distribution or None, "priority": g.priority or "medium",
            "depends_on": [int(x) for x in (g.depends_on or "").split(",") if x.strip().isdigit()], "manual_value": g.manual_value, "manual_at": g.manual_at,
            "manual_by": g.manual_by, "manual_source": g.manual_source, "phase_link": g.phase_link or None, "created_by": g.created_by, "approval": g.approval or None,
            "computed_status": g.status}


def _goals_rows(s, cid):
    goals = s.exec(select(CompanyGoal).where(CompanyGoal.company_id == cid, CompanyGoal.archived == 0).limit(2000)).all()
    ids = {g.id for g in goals}
    krs = [k for k in s.exec(select(CompanyKeyResult).where(CompanyKeyResult.company_id == cid).limit(5000)).all() if k.goal_id in ids]
    ms = [m for m in s.exec(select(CompanyGoalMilestone).where(CompanyGoalMilestone.company_id == cid).limit(5000)).all() if m.goal_id in ids]
    acts = [a for a in s.exec(select(CompanyGoalAction).where(CompanyGoalAction.company_id == cid).limit(5000)).all() if a.goal_id in ids]
    hist = s.exec(select(CompanyGoalHistory).where(CompanyGoalHistory.company_id == cid).order_by(CompanyGoalHistory.changed_at.desc()).limit(600)).all()
    com = s.exec(select(CompanyGoalComment).where(CompanyGoalComment.company_id == cid).order_by(CompanyGoalComment.created_at.desc()).limit(600)).all()
    strat = s.exec(select(CompanyStrategy).where(CompanyStrategy.company_id == cid)).first()
    return {"goals": goals, "krs": krs, "ms": ms, "acts": acts, "hist": hist, "com": com, "strat": strat}


def _goals_data(s, company):
    """نتائج المحركات السابقة بنطاق كامل (المحرك يرشّح الأهداف حسب المستخدم — من يرى الهدف يرى رقمه فقط)."""
    de, pe, be = _load_p24("drivers_engine"), _load_p24("prediction_engine"), _load_p24("benchmark_engine")
    risk = _risk_result(s, company, "owner", save_snapshot=False)
    mods, cust, errors = _risk_modules(s, company, "owner")
    today_ = datetime.now().date()
    dv, bz, pz = {}, None, None
    errors = dict(errors or {})
    # كل محرك سابق اختياري للأهداف: إن فشل يُسجَّل ويُكمل المركز بدونه (لا يسقط الصفحة)
    if risk.get("has_data") and de is not None:
        try:
            dv = de.analyze_drivers(risk, mods, customer_rows=cust, settings=_risk_settings(s, company.id), history=_risk_history(s, company.id),
                                    today=today_, sector=_risk_sector(company))
        except Exception as e:
            _logger.error(f"goals: drivers failed: {type(e).__name__}: {str(e)[:200]}")
            errors["drivers"], dv = f"مسببات المخاطر غير متاحة: {type(e).__name__}", {}
        if be is not None:
            try:
                prof = _bench_profile(s, company)
                bz = be.analyze_benchmark(mods, sales_rows=cust, risk=risk, drivers=dv, datasets=_bench_datasets(s, company), peers=_bench_peers(s, company, prof),
                                          profile=prof, history=_bench_history(s, company.id), today=today_)
            except Exception as e:
                _logger.error(f"goals: benchmark failed: {type(e).__name__}: {str(e)[:200]}")
                errors["benchmark"], bz = f"المقارنة بالقطاع غير متاحة: {type(e).__name__}", None
    st = _pred_settings(s, company)
    if pe is not None and cust:
        try:
            pz = pe.analyze_prediction(mods, sales_rows=cust, risk=risk, drivers=dv, bench=bz, settings={"targets": st["targets"], "fy_start": st["fy_start"]},
                                       sector=_risk_sector(company), today=today_, currency=getattr(company, "currency", None) or "SAR")
        except Exception as e:
            _logger.error(f"goals: prediction failed: {type(e).__name__}: {str(e)[:200]}")
            errors["prediction"], pz = f"التنبؤ غير متاح: {type(e).__name__}", None
    brs = s.exec(select(CompanyBranch).where(CompanyBranch.company_id == company.id)).all()
    existing = {"annual_revenue": st["targets"].get("annual_revenue"), "revenue_source_ar": st["targets"].get("source_ar"),
                "annual_profit": st["targets"].get("annual_profit"), "target_margin": getattr(company, "target_margin", None) or None,
                "branch_targets": {b.name: b.target_sales for b in brs if b.target_sales}}
    return {"mods": mods, "cust": cust, "risk": risk, "drivers": dv, "bench": bz, "pred": pz, "errors": errors, "fy_start": st["fy_start"], "existing": existing, "today": today_}


def _goals_ctx(s, company, data=None):
    ge = _load_p24("goals_engine")
    d = data or _goals_data(s, company)
    return ge.build_context(d["mods"], d["cust"], risk=d["risk"], drivers=d["drivers"], bench=d["bench"], pred=d["pred"], today=d["today"], fy_start=d["fy_start"]), d


def _goal_decisions(s, cid, acts):
    out, ids = [], [a.decision_id for a in acts if a.decision_id and a.baseline_json]
    decs = {d.id: d for d in s.exec(select(CompanyDecision).where(CompanyDecision.company_id == cid, CompanyDecision.decision_type == "goal")).all()} if ids else {}
    for a in acts:
        if a.decision_id in decs:
            d = decs[a.decision_id]
            try:
                bl = json.loads(a.baseline_json or "{}")
            except (TypeError, ValueError):
                bl = {}
            out.append({"id": d.id, "goal_id": a.goal_id, "title": d.title, "owner": d.owner, "status": d.status,
                        "created_on": d.created_at.strftime("%Y-%m-%d") if d.created_at else "", "baseline": bl})
    return out


def _goals_result(s, company, role, viewer, *, save=True, cd=None):
    ge = _load_p24("goals_engine")
    if ge is None:
        raise HTTPException(503, "محرّك الأهداف غير متاح — " + _p23_diagnostic())
    R_ = _goals_rows(s, company.id)
    ctx, d = cd or _goals_ctx(s, company)
    st = R_["strat"]
    try:
        prev = json.loads(st.snapshot_json or "{}") if st else {}
        strategy = {"vision": st.vision, "mission": st.mission, "pillars": json.loads(st.pillars_json or "[]") or None,
                    "pillar_names": json.loads(st.pillar_names_json or "{}"), "updated_at": st.updated_at.strftime("%Y-%m-%d") if st.updated_at else None,
                    "updated_by": st.updated_by} if st else {}
    except (TypeError, ValueError):
        prev, strategy = {}, {}
    goals = [_goal_dict(g) for g in R_["goals"]]
    krs = [{"id": k.id, "goal_id": k.goal_id, "name": k.name, "metric": k.metric or None, "baseline": k.baseline, "target": k.target, "actual": k.actual,
            "actual_at": k.actual_at, "actual_by": k.actual_by, "unit": k.unit} for k in R_["krs"]]
    ms = [{"id": m.id, "goal_id": m.goal_id, "title": m.title, "due": m.due_date, "target": m.target, "actual": m.actual, "owner": m.owner,
           "evidence_note": m.evidence_note} for m in R_["ms"]]
    acts = [{"id": a.id, "goal_id": a.goal_id, "kr_id": a.kr_id, "action": a.action, "owner": a.owner, "owner_user_id": a.owner_user_id, "due": a.due_date,
             "expected_impact": a.expected_impact, "actual_impact": a.actual_impact, "impact_type": a.impact_type, "status": a.status, "decision_id": a.decision_id}
            for a in R_["acts"]]
    hist = [{"id": h.id, "goal_id": h.goal_id, "field": h.field, "old_value": h.old_value, "new_value": h.new_value, "changed_by": h.changed_by,
             "changed_at": h.changed_at.strftime("%Y-%m-%d %H:%M") if h.changed_at else "", "reason": h.reason} for h in R_["hist"]]
    com = [{"id": c.id, "goal_id": c.goal_id, "kind": c.kind, "text": c.text, "mentions": [x for x in (c.mentions or "").split(",") if x],
            "attachment_name": c.attachment_name, "attachment_url": c.attachment_url, "by": c.by_name,
            "at": c.created_at.strftime("%Y-%m-%d %H:%M") if c.created_at else ""} for c in R_["com"]]
    res = ge.analyze_goals(goals, krs=krs, milestones=ms, actions=acts, strategy=strategy, history=hist, comments=com, previous=prev,
                           decisions=_goal_decisions(s, company.id, R_["acts"]), existing=d["existing"], sector=_risk_sector(company), today=d["today"],
                           viewer=viewer, currency=getattr(company, "currency", None) or "SAR", fy_start=d["fy_start"], ctx=ctx)
    full = res["scope"]["kind"] == "all"
    if save and full:
        rows = {g.id: g for g in R_["goals"]}
        dirty = False
        for e in res["goals"]:
            g = rows.get(e["id"])
            if g and g.status != e["status"]:
                g.status = e["status"]; s.add(g); dirty = True
        kr_ev = {k["id"]: k for e in res["goals"] for k in e.get("key_results") or [] if k.get("valid")}
        for k in R_["krs"]:
            v = kr_ev.get(k.id)
            if v and (k.status != v["status"] or k.progress != v.get("progress")):
                k.status, k.progress = v["status"], v.get("progress"); s.add(k); dirty = True
        ms_ev = {m["id"]: m for m in res["milestones"]}
        for m in R_["ms"]:
            v = ms_ev.get(m.id)
            if v and m.status != v["status"]:
                m.status = v["status"]; s.add(m); dirty = True
        # لقطة المراجعة: تُدوَّر أسبوعياً — تغيّر الحالة يُسجَّل في التاريخ آلياً (system)
        age_ok = True
        if st and st.snapshot_at:
            try:
                age_ok = (d["today"] - datetime.strptime(st.snapshot_at[:10], "%Y-%m-%d").date()).days >= 7
            except ValueError:
                age_ok = True
        if age_ok and res["goals"]:
            st = st or CompanyStrategy(company_id=company.id)
            for h in res.get("auto_history") or []:
                s.add(CompanyGoalHistory(company_id=company.id, goal_id=h["goal_id"], field=h["field"], old_value=str(h["old_value"])[:200],
                                         new_value=str(h["new_value"])[:200], changed_by="system", reason=h["reason"][:300]))
            st.snapshot_json, st.snapshot_at = json.dumps(res["snapshot"], ensure_ascii=False)[:60000], d["today"].isoformat()
            s.add(st); dirty = True
        if dirty:
            s.commit()
    if res["scope"]["kind"] not in ("all", "executive"):
        res["sector"] = {**res["sector"], "templates": [{**t, "current": None, "current_basis_ar": None} for t in res["sector"]["templates"]]}
        res["suggestions"] = []
    res["role"] = role
    res["module_errors"] = d["errors"]
    res["can_create"] = role == "owner" or (viewer.get("title") in ("ceo", "dept_manager", "branch_manager")) or (role in ("manager", "accountant") and viewer.get("title") != "employee")
    res["can_strategy"] = role == "owner" or viewer.get("title") == "ceo"
    res["can_scope"] = role == "owner"
    res["branches_list"] = sorted({b.name for b in s.exec(select(CompanyBranch).where(CompanyBranch.company_id == company.id)).all()} | set(ctx["branches"]))
    res["goal_names"] = [{"id": g["id"], "name": g["name"], "level": g["level"], "metric": g["metric"]} for g in res["goals"]]
    return res


@app.get("/company/goals-intelligence")
def company_goals_intelligence(user: User = Depends(get_current_user)):
    with Session(engine) as s:
        company, role, viewer = _goals_scope(s, user)
        try:
            res = _goals_result(s, company, role, viewer)
        except HTTPException:
            raise
        except Exception as e:
            import traceback as _tb
            _logger.error("goals-intelligence failed:\n" + _tb.format_exc()[-3000:])
            try:
                s.rollback()
            except Exception:
                pass
            raise HTTPException(500, f"خطأ في مركز الأهداف — {type(e).__name__}: {str(e)[:200]} (التفاصيل في سجل الخادم)")
        log_audit(company.id, user.id, user.name, "goals_view", "goals", f"scope={res['scope']['kind']} goals={len(res['goals'])}")
        return res


def _goal_row(s, company, gid):
    try:
        gid = int(gid)
    except (TypeError, ValueError):
        raise HTTPException(422, "معرّف الهدف غير صالح")
    g = s.get(CompanyGoal, gid)
    if not g or g.company_id != company.id or g.archived:
        raise HTTPException(404, "الهدف غير موجود")
    return g


def _goal_hist(s, company, gid, changes, by, reason):
    for c in changes:
        s.add(CompanyGoalHistory(company_id=company.id, goal_id=gid, field=c["field"], old_value=json.dumps(c["old"], ensure_ascii=False)[:300] if not isinstance(c["old"], str) else c["old"][:300],
                                 new_value=json.dumps(c["new"], ensure_ascii=False)[:300] if not isinstance(c["new"], str) else c["new"][:300],
                                 changed_by=by[:100], reason=(reason or "")[:300]))


def _fnum(v, label):
    if v in (None, ""):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise HTTPException(422, f"{label} يجب أن يكون رقماً")
    if x != x or abs(x) > 1e13:
        raise HTTPException(422, f"{label} غير صالح")
    return x


@app.post("/company/goals-center/save")
def company_goal_save(data: dict, user: User = Depends(get_current_user)):
    """إنشاء/تعديل هدف: يُتحقق من عقد البيانات، وكل تغيير يُسجَّل (القيمة القديمة/الجديدة/من/متى/لماذا)."""
    with Session(engine) as s:
        company, role, viewer = _goals_scope(s, user)
        ge = _load_p24("goals_engine")
        known = {g.id for g in s.exec(select(CompanyGoal).where(CompanyGoal.company_id == company.id, CompanyGoal.archived == 0)).all()}
        new = {"name": str(data.get("name") or "").strip()[:200], "objective": str(data.get("objective") or "")[:500], "pillar": data.get("pillar") or None,
               "level": data.get("level") or "company", "branch": (str(data.get("branch") or "").strip() or None), "department": data.get("department") or None,
               "owner": (str(data.get("owner") or "").strip()[:100] or None), "metric": data.get("metric") or None, "unit": (str(data.get("unit") or "")[:30] or None),
               "baseline": _fnum(data.get("baseline"), "خط الأساس"), "target": _fnum(data.get("target"), "المستهدف"),
               "start": str(data.get("start") or "")[:10], "end": str(data.get("end") or "")[:10], "status_override": data.get("status_override") or None,
               "priority": data.get("priority") or "medium", "distribution": data.get("distribution") or None, "phase_link": data.get("phase_link") or None,
               "parent_id": int(data["parent_id"]) if str(data.get("parent_id") or "").isdigit() else None,
               "depends_on": [int(x) for x in (data.get("depends_on") or []) if str(x).isdigit()]}
        ou = data.get("owner_user_id")
        if str(ou or "").isdigit():
            u2 = s.get(User, int(ou))
            if not u2 or u2.company_id != company.id:
                raise HTTPException(422, "المسؤول ليس من فريق شركتك")
            new["owner_user_id"], new["owner"] = u2.id, new["owner"] or u2.name
        if new["status_override"] not in (None, "cancelled", "paused"):
            raise HTTPException(422, "الحالة اليدوية المسموحة: ملغى أو موقوف فقط — بقية الحالات تُحسب آلياً")
        gid = data.get("id")
        row = _goal_row(s, company, gid) if gid else None
        has_krs = bool(data.get("has_krs")) or (row is not None and bool(s.exec(select(CompanyKeyResult).where(CompanyKeyResult.goal_id == row.id)).first()))
        errs = ge.validate_goal({**new, "id": row.id if row else None}, known_ids=known, has_krs=has_krs)
        if errs:
            raise HTTPException(422, " · ".join(errs))
        if new["parent_id"]:
            seen, p = set(), new["parent_id"]
            while p:
                if row and p == row.id or p in seen:
                    raise HTTPException(422, "التسلسل يُنشئ حلقة (الهدف يصبح أباً لنفسه)")
                seen.add(p)
                pr = s.get(CompanyGoal, p)
                p = pr.parent_goal_id if pr and pr.company_id == company.id else None
        by = (user.name or user.email)[:100]
        reason = str(data.get("reason") or "").strip()
        if row:
            old = _goal_dict(row)
            if not ge.can_edit(old, viewer) or not ge.can_edit({**old, **new}, viewer):
                raise HTTPException(403, "غير مصرّح بتعديل هذا الهدف")
            ch = ge.diff_goal(old, {**new, **({"owner_user_id": new["owner_user_id"]} if "owner_user_id" in new else {})})
            if not ch:
                return {"ok": True, "id": row.id, "changes": 0}
            if ge.requires_reason(ch) and not reason:
                raise HTTPException(422, "سبب التغيير مطلوب عند تعديل المستهدف أو المدة أو المؤشر (حوكمة)")
        else:
            if not ge.can_edit(new, viewer):
                raise HTTPException(403, "غير مصرّح بإنشاء هدف بهذا المستوى/النطاق")
            row = CompanyGoal(company_id=company.id, created_by=by, source=data.get("source") if data.get("source") in ("manual", "template", "import") else "manual")
            ch = [{"field": "created", "old": "", "new": new["name"]}]
        row.parent_goal_id, row.name, row.objective, row.pillar = new["parent_id"], new["name"], new["objective"], new["pillar"] or ""
        row.level, row.branch, row.department, row.owner = new["level"], new["branch"] or "", new["department"] or "", new["owner"] or ""
        if "owner_user_id" in new:
            row.owner_user_id = new["owner_user_id"]
        row.metric, row.unit, row.baseline, row.target = new["metric"] or "", new["unit"] or "", new["baseline"], new["target"]
        row.start_date, row.end_date, row.status_override, row.priority = new["start"], new["end"], new["status_override"] or "", new["priority"]
        row.distribution, row.phase_link = new["distribution"] or "", new["phase_link"] or ""
        row.depends_on = ",".join(str(x) for x in new["depends_on"])
        row.updated_at = datetime.now()
        s.add(row); s.commit(); s.refresh(row)
        _goal_hist(s, company, row.id, ch, by, reason)
        s.commit()
        log_audit(company.id, user.id, user.name, "goal_save", f"goal:{row.id}", json.dumps([c["field"] for c in ch], ensure_ascii=False)[:300])
        return {"ok": True, "id": row.id, "changes": len(ch)}


@app.post("/company/goals-center/archive")
def company_goal_archive(data: dict, user: User = Depends(get_current_user)):
    with Session(engine) as s:
        company, role, viewer = _goals_scope(s, user)
        ge = _load_p24("goals_engine")
        row = _goal_row(s, company, data.get("id"))
        if not ge.can_edit(_goal_dict(row), viewer):
            raise HTTPException(403, "غير مصرّح")
        reason = str(data.get("reason") or "").strip()
        if not reason:
            raise HTTPException(422, "سبب الأرشفة مطلوب")
        row.archived, row.updated_at = 1, datetime.now()
        s.add(row)
        _goal_hist(s, company, row.id, [{"field": "archived", "old": "0", "new": "1"}], (user.name or user.email), reason)
        s.commit()
        log_audit(company.id, user.id, user.name, "goal_archive", f"goal:{row.id}", reason[:200])
        return {"ok": True}


@app.post("/company/goals-center/kr")
def company_goal_kr(data: dict, user: User = Depends(get_current_user)):
    """نتيجة رئيسية (OKR): مؤشر + خط أساس + مستهدف + مصدر — لا نتائج وصفية. delete=true للحذف."""
    with Session(engine) as s:
        company, role, viewer = _goals_scope(s, user)
        ge = _load_p24("goals_engine")
        g = _goal_row(s, company, data.get("goal_id"))
        if not ge.can_edit(_goal_dict(g), viewer):
            raise HTTPException(403, "غير مصرّح")
        by = (user.name or user.email)[:100]
        if data.get("id"):
            k = s.get(CompanyKeyResult, int(data["id"]))
            if not k or k.company_id != company.id or k.goal_id != g.id:
                raise HTTPException(404, "النتيجة غير موجودة")
            if data.get("delete"):
                _goal_hist(s, company, g.id, [{"field": "kr_deleted", "old": k.name or k.metric, "new": ""}], by, str(data.get("reason") or ""))
                s.delete(k); s.commit()
                return {"ok": True}
        else:
            k = CompanyKeyResult(company_id=company.id, goal_id=g.id)
        kd = {"metric": data.get("metric") or None, "baseline": _fnum(data.get("baseline"), "خط الأساس"), "target": _fnum(data.get("target"), "المستهدف")}
        errs = ge.validate_kr(kd)
        if errs:
            raise HTTPException(422, " · ".join(errs))
        old = {"target": k.target, "baseline": k.baseline, "metric": k.metric}
        k.name, k.metric, k.baseline, k.target = str(data.get("name") or "")[:200], kd["metric"], kd["baseline"], kd["target"]
        k.unit = str(data.get("unit") or "")[:30]
        av = _fnum(data.get("actual"), "القيمة الحالية")
        if av is not None:
            if (ge.metric_meta(kd["metric"]) or {}).get("src") != "manual":
                raise HTTPException(422, "قيمة هذا المؤشر تُقرأ من بيانات نبّاه — لا تُدخل يدوياً")
            k.actual, k.actual_at, k.actual_by = av, datetime.now().strftime("%Y-%m-%d"), by
        s.add(k); s.commit(); s.refresh(k)
        _goal_hist(s, company, g.id, [{"field": f"kr:{k.id}", "old": old, "new": kd}], by, str(data.get("reason") or ""))
        s.commit()
        log_audit(company.id, user.id, user.name, "goal_kr", f"goal:{g.id}", f"kr={k.id}")
        return {"ok": True, "id": k.id}


@app.post("/company/goals-center/milestone")
def company_goal_milestone(data: dict, user: User = Depends(get_current_user)):
    with Session(engine) as s:
        company, role, viewer = _goals_scope(s, user)
        ge = _load_p24("goals_engine")
        g = _goal_row(s, company, data.get("goal_id"))
        gd = _goal_dict(g)
        if not ge.can_edit(gd, viewer):
            raise HTTPException(403, "غير مصرّح")
        by = (user.name or user.email)[:100]
        if data.get("generate"):
            ctx, _d = _goals_ctx(s, company)
            prop = ge.generate_milestones(gd, ctx)
            if not data.get("save"):
                return {"ok": True, "proposals": prop}
            for p in prop:
                s.add(CompanyGoalMilestone(company_id=company.id, goal_id=g.id, title=p["title"][:200], due_date=p["due"], target=p["target"], owner=p.get("owner") or ""))
            _goal_hist(s, company, g.id, [{"field": "milestones_generated", "old": "", "new": len(prop)}], by, prop[0]["basis_ar"] if prop else "")
            s.commit()
            return {"ok": True, "created": len(prop)}
        if data.get("id"):
            m = s.get(CompanyGoalMilestone, int(data["id"]))
            if not m or m.company_id != company.id or m.goal_id != g.id:
                raise HTTPException(404, "المرحلة غير موجودة")
            if data.get("delete"):
                s.delete(m); _goal_hist(s, company, g.id, [{"field": "milestone_deleted", "old": m.title, "new": ""}], by, str(data.get("reason") or "")); s.commit()
                return {"ok": True}
        else:
            m = CompanyGoalMilestone(company_id=company.id, goal_id=g.id)
        md = {"title": str(data.get("title") or "").strip()[:200], "due": str(data.get("due") or "")[:10], "target": _fnum(data.get("target"), "مستهدف المرحلة")}
        errs = ge.validate_milestone(md, gd)
        if errs:
            raise HTTPException(422, " · ".join(errs))
        old = {"due": m.due_date, "target": m.target}
        m.title, m.due_date, m.target = md["title"], md["due"], md["target"]
        m.owner, m.evidence_note = str(data.get("owner") or "")[:100], str(data.get("evidence_note") or "")[:500]
        m.actual = _fnum(data.get("actual"), "الفعلي")
        s.add(m); s.commit(); s.refresh(m)
        _goal_hist(s, company, g.id, [{"field": f"milestone:{m.id}", "old": old, "new": {"due": m.due_date, "target": m.target}}], by, str(data.get("reason") or ""))
        s.commit()
        return {"ok": True, "id": m.id}


@app.post("/company/goals-center/action")
def company_goal_action(data: dict, user: User = Depends(get_current_user)):
    """إجراء للهدف: محرر الهدف ينشئ ويعدّل؛ المُسند إليه يحدّث الحالة والأثر الفعلي فقط."""
    with Session(engine) as s:
        company, role, viewer = _goals_scope(s, user)
        ge = _load_p24("goals_engine")
        g = _goal_row(s, company, data.get("goal_id"))
        gd = _goal_dict(g)
        editor = ge.can_edit(gd, viewer) or ge._is_mine(gd, viewer)
        by = (user.name or user.email)[:100]
        if data.get("id"):
            a = s.get(CompanyGoalAction, int(data["id"]))
            if not a or a.company_id != company.id or a.goal_id != g.id:
                raise HTTPException(404, "الإجراء غير موجود")
            assignee = ge._is_mine({"owner": a.owner, "owner_user_id": a.owner_user_id}, viewer)
            if not editor and not assignee:
                raise HTTPException(403, "غير مصرّح")
        else:
            if not editor:
                raise HTTPException(403, "غير مصرّح بإضافة إجراء لهذا الهدف")
            a, assignee = CompanyGoalAction(company_id=company.id, goal_id=g.id, created_by=by), False
        old = {"status": a.status, "actual_impact": a.actual_impact, "expected_impact": a.expected_impact, "owner": a.owner, "due": a.due_date}
        if editor:
            a.action = str(data.get("action") if data.get("action") is not None else a.action or "").strip()[:300]
            a.owner = str(data.get("owner") if data.get("owner") is not None else a.owner or "")[:100]
            if str(data.get("owner_user_id") or "").isdigit():
                u2 = s.get(User, int(data["owner_user_id"]))
                if not u2 or u2.company_id != company.id:
                    raise HTTPException(422, "المسؤول ليس من فريق شركتك")
                a.owner_user_id, a.owner = u2.id, a.owner or u2.name
            a.due_date = str(data.get("due") if data.get("due") is not None else a.due_date or "")[:10]
            if "expected_impact" in data:
                a.expected_impact = _fnum(data.get("expected_impact"), "الأثر المتوقع")
            if data.get("impact_type") in ("potential", "recovery", "actual"):
                a.impact_type = data["impact_type"]
            if str(data.get("kr_id") or "").isdigit():
                a.kr_id = int(data["kr_id"])
        if "status" in data:
            a.status = str(data["status"])
        if "actual_impact" in data:
            a.actual_impact = _fnum(data.get("actual_impact"), "الأثر الفعلي")
        errs = ge.validate_action({"action": a.action, "status": a.status, "due": a.due_date, "expected_impact": a.expected_impact, "actual_impact": a.actual_impact})
        if errs:
            raise HTTPException(422, " · ".join(errs))
        if a.status == "done" and not a.done_at:
            a.done_at = datetime.now().strftime("%Y-%m-%d")
        a.updated_at = datetime.now()
        s.add(a); s.commit(); s.refresh(a)
        new = {"status": a.status, "actual_impact": a.actual_impact, "expected_impact": a.expected_impact, "owner": a.owner, "due": a.due_date}
        if new != old:
            _goal_hist(s, company, g.id, [{"field": f"action:{a.id}", "old": old, "new": new}], by, str(data.get("reason") or ""))
            s.commit()
        log_audit(company.id, user.id, user.name, "goal_action", f"goal:{g.id}", f"action={a.id} status={a.status}")
        return {"ok": True, "id": a.id}


@app.post("/company/goals-center/value")
def company_goal_value(data: dict, user: User = Depends(get_current_user)):
    """قيمة يدوية فقط لمؤشر لا تملك نبّاه مصدره (NPS/ساعات التدريب...) — موسومة بمن أدخلها ومتى ومن أين."""
    with Session(engine) as s:
        company, role, viewer = _goals_scope(s, user)
        ge = _load_p24("goals_engine")
        g = _goal_row(s, company, data.get("id"))
        gd = _goal_dict(g)
        if not (ge.can_edit(gd, viewer) or ge._is_mine(gd, viewer)):
            raise HTTPException(403, "غير مصرّح")
        meta = ge.metric_meta(g.metric) or {}
        if meta.get("src") not in ("manual", "actions"):
            raise HTTPException(422, "قيمة هذا المؤشر تُحسب من بيانات نبّاه تلقائياً — لا تُعدّل يدوياً")
        v = _fnum(data.get("value"), "القيمة")
        if v is None:
            raise HTTPException(422, "القيمة مطلوبة")
        old = g.manual_value
        g.manual_value, g.manual_at, g.manual_by = v, datetime.now().strftime("%Y-%m-%d"), (user.name or user.email)[:100]
        g.manual_source = str(data.get("source") or "")[:200]
        s.add(g)
        _goal_hist(s, company, g.id, [{"field": "manual_value", "old": old, "new": v}], (user.name or user.email), str(data.get("reason") or g.manual_source))
        s.commit()
        log_audit(company.id, user.id, user.name, "goal_value", f"goal:{g.id}", f"{old}→{v}")
        return {"ok": True}


@app.post("/company/goals-center/collab")
def company_goal_collab(data: dict, user: User = Depends(get_current_user)):
    """التعاون بصلاحيات: تعليق/إشارة/دليل/طلب تحديث/تصعيد للكل ضمن النطاق · تعيين مسؤول لمحرر الهدف · اعتماد/رفض لغير صاحب الهدف."""
    with Session(engine) as s:
        company, role, viewer = _goals_scope(s, user)
        ge = _load_p24("goals_engine")
        g = _goal_row(s, company, data.get("goal_id"))
        gd = _goal_dict(g)
        kind = str(data.get("kind") or "comment")
        if kind not in _GOAL_COLLAB_KINDS:
            raise HTTPException(422, "نوع غير معروف")
        assigned = {a.goal_id for a in s.exec(select(CompanyGoalAction).where(CompanyGoalAction.company_id == company.id, CompanyGoalAction.owner_user_id == user.id)).all()}
        if not ge.can_collab(kind, gd, viewer, assigned):
            raise HTTPException(403, "غير مصرّح بهذا الإجراء على هذا الهدف")
        text = str(data.get("text") or "").strip()[:2000]
        url = str(data.get("attachment_url") or "").strip()[:500]
        if url and not url.lower().startswith(("https://", "http://")):
            raise HTTPException(422, "رابط الدليل يجب أن يبدأ بـ https://")
        by = (user.name or user.email)[:100]
        if kind == "assign_owner":
            ou = data.get("owner_user_id")
            nm = str(data.get("owner") or "").strip()[:100]
            if str(ou or "").isdigit():
                u2 = s.get(User, int(ou))
                if not u2 or u2.company_id != company.id:
                    raise HTTPException(422, "المسؤول ليس من فريق شركتك")
                g.owner_user_id, nm = u2.id, nm or u2.name
            if not nm:
                raise HTTPException(422, "حدد المسؤول")
            _goal_hist(s, company, g.id, [{"field": "owner", "old": g.owner, "new": nm}], by, text)
            g.owner = nm
            text = text or f"تعيين المسؤول: {nm}"
        elif kind in ("approve", "reject"):
            if kind == "reject" and not text:
                raise HTTPException(422, "سبب الرفض مطلوب")
            _goal_hist(s, company, g.id, [{"field": "approval", "old": g.approval, "new": "approved" if kind == "approve" else "rejected"}], by, text)
            g.approval = "approved" if kind == "approve" else "rejected"
        elif kind in ("comment", "mention", "escalate", "request_update") and not text:
            raise HTTPException(422, "النص مطلوب")
        elif kind == "evidence" and not (url or text):
            raise HTTPException(422, "أرفق رابط الدليل أو وصفه")
        g.updated_at = datetime.now()
        s.add(g)
        mentions = [str(x)[:60] for x in (data.get("mentions") or [])][:10]
        c = CompanyGoalComment(company_id=company.id, goal_id=g.id, kind=kind, text=text, mentions=",".join(mentions),
                               attachment_name=str(data.get("attachment_name") or "")[:200], attachment_url=url, by_user_id=user.id, by_name=by)
        s.add(c); s.commit(); s.refresh(c)
        log_audit(company.id, user.id, user.name, f"goal_{kind}", f"goal:{g.id}", text[:200])
        return {"ok": True, "id": c.id}


@app.post("/company/goals-center/cascade")
def company_goal_cascade(data: dict, user: User = Depends(get_current_user)):
    """توزيع هدف الشركة على الفروع: مقترح (حسب الحصة أو بالتساوي) → يُحفظ باعتماد المستخدم ويُوسم «موزّع تلقائياً»."""
    with Session(engine) as s:
        company, role, viewer = _goals_scope(s, user)
        ge = _load_p24("goals_engine")
        g = _goal_row(s, company, data.get("goal_id"))
        gd = _goal_dict(g)
        if not ge.can_edit(gd, viewer):
            raise HTTPException(403, "غير مصرّح")
        ctx, _d = _goals_ctx(s, company)
        prop = ge.auto_cascade(gd, ctx, "equal" if data.get("basis") == "equal" else "share")
        if prop["status"] != "ok" or not data.get("save"):
            return prop
        kids = s.exec(select(CompanyGoal).where(CompanyGoal.company_id == company.id, CompanyGoal.parent_goal_id == g.id, CompanyGoal.archived == 0)).all()
        have = {k.branch for k in kids if k.metric == g.metric}
        created, skipped = [], []
        by = (user.name or user.email)[:100]
        for r in prop["rows"]:
            if r["branch"] in have:
                skipped.append(r["branch"]); continue
            c = CompanyGoal(company_id=company.id, parent_goal_id=g.id, name=f"{g.name} — {r['branch']}", objective=g.objective, pillar=g.pillar, level="branch",
                            branch=r["branch"], department=g.department, metric=g.metric, unit=g.unit, target=r["target"], start_date=g.start_date,
                            end_date=g.end_date, source="cascade_auto", distribution="auto", priority=g.priority, created_by=by, updated_at=datetime.now())
            s.add(c); s.commit(); s.refresh(c)
            _goal_hist(s, company, c.id, [{"field": "created", "old": "", "new": c.name}], by, prop["basis_ar"])
            created.append(c.id)
        _goal_hist(s, company, g.id, [{"field": "cascade", "old": "", "new": f"{len(created)} فرع"}], by, prop["basis_ar"])
        s.commit()
        log_audit(company.id, user.id, user.name, "goal_cascade", f"goal:{g.id}", f"created={created} skipped={skipped}")
        return {**prop, "created": created, "skipped": skipped}


@app.post("/company/goals-center/import")
def company_goals_import(data: dict, user: User = Depends(get_current_user)):
    """استيراد أهداف موجودة في نبّاه (موازنة/التنبؤ/أهداف الفروع/الهامش) أو قالب قطاع — بقرار المستخدم فقط."""
    with Session(engine) as s:
        company, role, viewer = _goals_scope(s, user)
        if not (role == "owner" or viewer.get("title") == "ceo"):
            raise HTTPException(403, "الاستيراد للمالك أو الرئيس التنفيذي")
        ge = _load_p24("goals_engine")
        items = data.get("items") or []
        if not isinstance(items, list) or not items or len(items) > 30:
            raise HTTPException(422, "اختر من 1 إلى 30 هدفاً")
        known = {g.id for g in s.exec(select(CompanyGoal).where(CompanyGoal.company_id == company.id)).all()}
        by = (user.name or user.email)[:100]
        ids = []
        for it in items:
            gd = {"name": str(it.get("name") or "")[:200], "metric": it.get("metric"), "target": _fnum(it.get("target"), "المستهدف"), "start": str(it.get("start") or "")[:10],
                  "end": str(it.get("end") or "")[:10], "level": it.get("level") or "company", "branch": it.get("branch") or None, "pillar": it.get("pillar") or None,
                  "baseline": _fnum(it.get("baseline"), "خط الأساس")}
            errs = ge.validate_goal(gd, known_ids=known)
            if errs:
                raise HTTPException(422, f"«{gd['name']}»: " + " · ".join(errs))
            c = CompanyGoal(company_id=company.id, name=gd["name"], metric=gd["metric"], target=gd["target"], baseline=gd["baseline"], start_date=gd["start"],
                            end_date=gd["end"], level=gd["level"], branch=gd["branch"] or "", pillar=gd["pillar"] or "",
                            source="template" if it.get("from") == "template" else "import", created_by=by, updated_at=datetime.now())
            s.add(c); s.commit(); s.refresh(c)
            _goal_hist(s, company, c.id, [{"field": "created", "old": "", "new": c.name}], by, str(it.get("from_ar") or it.get("basis_ar") or "استيراد")[:300])
            ids.append(c.id)
        s.commit()
        log_audit(company.id, user.id, user.name, "goals_import", "goals", f"ids={ids}")
        return {"ok": True, "ids": ids}


@app.post("/company/goals-center/strategy")
def company_goals_strategy(data: dict, user: User = Depends(get_current_user)):
    with Session(engine) as s:
        company, role, viewer = _goals_scope(s, user)
        if not (role == "owner" or viewer.get("title") == "ceo"):
            raise HTTPException(403, "الإطار الاستراتيجي يعدّله المالك أو الرئيس التنفيذي")
        ge = _load_p24("goals_engine")
        pillars = [p for p in (data.get("pillars") or []) if p in ge.PILLARS]
        if data.get("pillars") is not None and not pillars:
            raise HTTPException(422, "اختر ركيزة واحدة على الأقل")
        names = {k: str(v)[:60] for k, v in (data.get("pillar_names") or {}).items() if k in ge.PILLARS and str(v).strip()}
        st = s.exec(select(CompanyStrategy).where(CompanyStrategy.company_id == company.id)).first() or CompanyStrategy(company_id=company.id)
        old = {"vision": st.vision, "mission": st.mission, "pillars": st.pillars_json}
        st.vision, st.mission = str(data.get("vision") or "")[:1000], str(data.get("mission") or "")[:1000]
        st.pillars_json, st.pillar_names_json = json.dumps(pillars or ge.DEFAULT_PILLARS, ensure_ascii=False), json.dumps(names, ensure_ascii=False)
        st.updated_by, st.updated_at = (user.name or user.email)[:100], datetime.now()
        s.add(st); s.commit()
        log_audit(company.id, user.id, user.name, "goals_strategy", "goals", json.dumps({"before": old, "after": {"vision": st.vision, "pillars": pillars}}, ensure_ascii=False)[:400])
        return {"ok": True}


@app.get("/company/goals-center/scope")
def company_goals_scope_list(user: User = Depends(get_current_user)):
    with Session(engine) as s:
        company, role, viewer = _goals_scope(s, user)
        if role != "owner":
            raise HTTPException(403, "ربط المستخدمين بالنطاق للمالك فقط")
        users = s.exec(select(User).where(User.company_id == company.id)).all()
        sc = {x.user_id: x for x in s.exec(select(CompanyUserScope).where(CompanyUserScope.company_id == company.id)).all()}
        return {"users": [{"id": u.id, "name": u.name, "email": u.email, "role": get_user_role(s, u), "title": (sc.get(u.id).title if sc.get(u.id) else ""),
                           "branch": (sc.get(u.id).branch if sc.get(u.id) else ""), "department": (sc.get(u.id).department if sc.get(u.id) else "")} for u in users]}


@app.post("/company/goals-center/scope")
def company_goals_scope_set(data: dict, user: User = Depends(get_current_user)):
    with Session(engine) as s:
        company, role, viewer = _goals_scope(s, user)
        if role != "owner":
            raise HTTPException(403, "ربط المستخدمين بالنطاق للمالك فقط")
        ge = _load_p24("goals_engine")
        u2 = s.get(User, int(data.get("user_id") or 0)) if str(data.get("user_id") or "").isdigit() else None
        if not u2 or u2.company_id != company.id:
            raise HTTPException(404, "المستخدم ليس من شركتك")
        title = str(data.get("title") or "")
        if title not in _GOAL_TITLES:
            raise HTTPException(422, "المسمّى غير صالح")
        br, dp = str(data.get("branch") or "")[:100], str(data.get("department") or "")
        if title == "branch_manager" and not br:
            raise HTTPException(422, "مدير الفرع يحتاج فرعاً")
        if title == "dept_manager" and dp not in ge.DEPARTMENTS:
            raise HTTPException(422, "مدير القسم يحتاج قسماً صالحاً")
        row = s.exec(select(CompanyUserScope).where(CompanyUserScope.company_id == company.id, CompanyUserScope.user_id == u2.id)).first() \
            or CompanyUserScope(company_id=company.id, user_id=u2.id)
        old = {"title": row.title, "branch": row.branch, "department": row.department}
        row.title, row.branch, row.department, row.updated_by, row.updated_at = title, br, dp if dp in ge.DEPARTMENTS else "", (user.name or user.email)[:100], datetime.now()
        s.add(row); s.commit()
        log_audit(company.id, user.id, user.name, "goals_scope", f"user:{u2.id}", json.dumps({"before": old, "after": {"title": title, "branch": br, "department": dp}}, ensure_ascii=False))
        return {"ok": True}


@app.post("/company/goals-center/to-decision")
def company_goal_to_decision(data: dict, user: User = Depends(get_current_user)):
    """هدف معرّض للخطر → قرار (مسؤول، موعد، أثر متوقع على المؤشر وأثر مالي مصنّف) + إجراء → متابعة القرارات (3.9).
    خط الأساس = لقطة الهدف وقت القرار لقياس قبل/بعد. الأرقام تُعاد حسابها في الخادم."""
    with Session(engine) as s:
        company, role, viewer = _goals_scope(s, user)
        ge = _load_p24("goals_engine")
        g = _goal_row(s, company, data.get("goal_id"))
        if not ge.can_edit(_goal_dict(g), viewer):
            raise HTTPException(403, "غير مصرّح بإنشاء قرار لهذا الهدف")
        res = _goals_result(s, company, role, viewer, save=False)
        e = next((x for x in res["goals"] if x["id"] == g.id), None)
        if not e:
            raise HTTPException(404, "الهدف خارج نطاقك")
        code = str(data.get("code") or "")
        opt = next((o for o in e.get("decision_options") or [] if o["code"] == code), None)
        if not opt:
            raise HTTPException(422, "لا يوجد قرار مقترح بهذا الرمز — الهدف ليس معرّضاً للخطر أو تغيّرت حالته")
        owner = str(data.get("owner") or e.get("owner") or "")[:100]
        due = str(data.get("due_date") or "")[:10]
        if due and not _valid_date(due):
            raise HTTPException(422, "تاريخ غير صالح")
        if not owner or not due:
            raise HTTPException(422, "القرار يحتاج مسؤولاً وموعداً")
        amt = opt.get("expected_financial_impact")
        d = CompanyDecision(
            company_id=company.id, title=str(data.get("title") or opt["title"])[:200],
            detail=(f"الهدف: {e['name']} · الحالة: {e['status_ar']} · " + "؛ ".join(e.get("why") or []))[:1000],
            owner=owner, due_date=due, kpi=(e.get("metric") or "")[:50], status="open", baseline_sales=_company_total_sales(s, company.id),
            expected_impact=(f"{opt['expected_kpi_impact_ar']} · " + (f"{amt:,.0f} {res.get('currency')} ({opt.get('impact_type_ar')})" if amt else "أثر مالي غير قابل للتقدير"))[:200],
            linked_to=f"goal:{g.id}"[:200], rationale=(opt.get("basis_ar") or "")[:500], metric_id=(e.get("metric") or "")[:50], baseline_value=e.get("actual"),
            expected_impact_value=amt, impact_status="expected", source_signal=f"goal:{g.id}:{code}"[:200], problem_type=f"goal:{e['status']}",
            decision_type="goal", outcome_status="pending_measurement", created_by=user.name or user.email, data_source="goals_engine", updated_at=datetime.now())
        s.add(d); s.commit(); s.refresh(d)
        ca = CompanyAction(company_id=company.id, decision_id=d.id, title=str(data.get("action") or opt["title"])[:200], owner=owner,
                           priority="P1" if e["status"] == "behind" else "P2", due_date=due, start_date=datetime.now().strftime("%Y-%m-%d"), updated_at=datetime.now())
        s.add(ca)
        ga = CompanyGoalAction(company_id=company.id, goal_id=g.id, action=str(data.get("action") or opt["title"])[:300], owner=owner, due_date=due,
                               expected_impact=amt, impact_type=opt.get("impact_type") or "potential", status="open", decision_id=d.id,
                               baseline_json=json.dumps(ge.decision_baseline(e), ensure_ascii=False, default=str)[:4000], created_by=(user.name or user.email)[:100])
        s.add(ga)
        _goal_hist(s, company, g.id, [{"field": "decision", "old": "", "new": f"قرار #{d.id}: {d.title}"}], (user.name or user.email), opt.get("basis_ar") or "")
        s.commit(); s.refresh(ca); s.refresh(ga)
        log_audit(company.id, user.id, user.name, "decision_from_goal", f"goal:{g.id}", f"decision={d.id} code={code} impact={amt}")
        return {"ok": True, "decision_id": d.id, "action_ids": [ca.id], "goal_action_id": ga.id}


@app.post("/company/goals-center/ai-insights")
def company_goals_ai(data: dict, request: Request, user: User = Depends(get_current_user)):
    """AI يفسر نتائج محرك الأهداف فقط (ضمن نطاق المستخدم) — لا يخترع أهدافاً ولا أرقاماً."""
    with Session(engine) as s:
        company, role, viewer = _goals_scope(s, user)
        res = _goals_result(s, company, role, viewer, save=False)
    ge, gw = _load_p24("goals_engine"), _load_p24("ai_gateway")
    if gw is None:
        raise HTTPException(503, "بوابة الذكاء الاصطناعي غير متاحة — " + _p23_diagnostic())
    if not res["goals"]:
        raise HTTPException(422, "لا توجد أهداف ضمن نطاقك لتفسيرها")
    q = str(data.get("question") or ge.AI_QUESTIONS[0])[:300]
    ctx = ge.ai_context(res)
    lim = sum(1 for g in res["goals"] if (g.get("confidence") or {}).get("level") == "limited")
    out = gw.request_ai_analysis(gw.GeminiProvider(company_gemini), ctx,
                                 q + " — فسّر من أرقام محرك الأهداف كما هي (الحالة والإسقاط والفجوة والثقة). لا تخترع أهدافاً أو أرقاماً، "
                                     "ولا تقل إن مخاطرة «سببت» الفجوة — قل «مرتبطة». إن كانت البيانات غير متاحة فقل ذلك.",
                                 trust_report={"overall_score": max(40, 90 - lim * 10), "status": "warning" if lim else "pass", "has_critical_fail": False, "main_causes": []},
                                 lang=get_lang(request), company=company)
    log_audit(company.id, user.id, user.name, "goals_ai", "goals", f"q={q[:80]}")
    return {"question": q, "ai": out}


# ═══════════════════════════════════════════════════════════
#  Phase 3.9 — Follow-up on Decisions (قرار → اعتماد → تنفيذ → نتيجة → قياس → تعلّم)
#  طبقة متابعة فوق CompanyDecision/CompanyAction — الفعلي من المصدر الموحد (كتالوج 3.8)، والقرارات القديمة تُقرأ دون تعديل
# ═══════════════════════════════════════════════════════════
def _dec_now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _dec_hist(s, company_id, did, event, actor, old="", new="", note=""):
    s.add(CompanyDecisionHistory(company_id=company_id, decision_id=did, event=event, actor=(actor or "")[:100],
                                 old_value=str(old if old is not None else "")[:300], new_value=str(new if new is not None else "")[:300], note=(note or "")[:500]))


def _dec_sync_legacy(d, wf):
    """يبقي صفحة القرارات السابقة متسقة: ملغى/مرفوض → cancelled · مكتمل/مُقاس → done · غير ذلك → open."""
    d.status = "cancelled" if wf in ("cancelled", "rejected") else "done" if wf in ("completed", "measured") else "open"
    if d.status != "open" and not d.closed_at:
        d.closed_at = datetime.now()
    d.updated_at = datetime.now()


def _dec_rows(s, cid):
    decs = s.exec(select(CompanyDecision).where(CompanyDecision.company_id == cid).order_by(CompanyDecision.created_at.desc()).limit(1000)).all()
    metas = {m.decision_id: m for m in s.exec(select(CompanyDecisionMeta).where(CompanyDecisionMeta.company_id == cid)).all()}
    kpis, outs, evs, hist, acts = {}, {}, {}, {}, {}
    for k in s.exec(select(CompanyDecisionKPI).where(CompanyDecisionKPI.company_id == cid)).all():
        kpis.setdefault(k.decision_id, []).append(k)
    for o in s.exec(select(CompanyDecisionOutcome).where(CompanyDecisionOutcome.company_id == cid)).all():
        if o.decision_id not in outs or (o.measured_at or "") >= (outs[o.decision_id].measured_at or ""):
            outs[o.decision_id] = o
    for e in s.exec(select(CompanyDecisionEvidence).where(CompanyDecisionEvidence.company_id == cid)).all():
        evs.setdefault(e.decision_id, []).append(e)
    for h in s.exec(select(CompanyDecisionHistory).where(CompanyDecisionHistory.company_id == cid)).all():
        hist.setdefault(h.decision_id, []).append(h)
    for a in s.exec(select(CompanyAction).where(CompanyAction.company_id == cid)).all():
        acts.setdefault(a.decision_id, []).append(a)
    gacts = {a.decision_id: a for a in s.exec(select(CompanyGoalAction).where(CompanyGoalAction.company_id == cid)).all() if a.decision_id}
    rbase = {r.decision_id: r for r in s.exec(select(CompanyRisk).where(CompanyRisk.company_id == cid)).all() if getattr(r, "decision_id", None)}
    return decs, metas, kpis, outs, evs, hist, acts, gacts, rbase


def _dec_dict(dx, d, m, kpis, out, evs, hist, acts, gact, rk):
    act = [{"id": a.id, "title": a.title, "owner": a.owner, "due": a.due_date, "status": a.status, "progress": a.progress or 0} for a in acts]
    base = {"id": d.id, "title": d.title, "description": d.detail, "rationale": d.rationale, "owner": d.owner, "due": d.due_date or None,
            "created_at": d.created_at.strftime("%Y-%m-%d") if d.created_at else None, "created_by": d.created_by or None, "actions": act}
    if m:
        base.update({"workflow": m.workflow, "priority": m.priority or "medium", "category": m.category or None, "source": m.source or "manual", "source_ref": m.source_ref,
                     "department": m.department or None, "branch": m.branch or None, "owner_user_id": m.owner_user_id, "created_by_id": m.created_by_id,
                     "approved_by": m.approved_by or None, "approved_at": m.approved_at or None, "completed_at": m.completed_at or None, "cost": m.cost,
                     "delay_reason": m.delay_reason or None, "delay_note": m.delay_note, "depends_on": [int(x) for x in (m.depends_on or "").split(",") if x.strip().isdigit()],
                     "goal_id": m.goal_id, "risk_key": m.risk_key or None, "risk_baseline": m.risk_baseline, "root_cause": m.root_cause or None,
                     "root_cause_baseline": m.root_cause_baseline, "leakage_key": m.leakage_key or None, "leakage_baseline": m.leakage_baseline,
                     "expected_type": m.expected_type or "potential", "sensitive": bool(m.sensitive), "expected_impact": d.expected_impact_value,
                     "kpis": [{"id": k.id, "metric": k.metric, "branch": k.branch or None, "baseline": k.baseline, "baseline_at": k.baseline_at, "target": k.target,
                               "actual_manual": k.actual, "actual_at": k.actual_at, "side_effect": bool(k.side_effect)} for k in kpis]})
    else:
        lg = dx.from_legacy({"status": d.status, "linked_to": d.linked_to, "decision_type": d.decision_type, "kpi": d.kpi, "metric_id": d.metric_id,
                             "baseline_value": d.baseline_value, "expected_impact_value": d.expected_impact_value, "source_signal": d.source_signal,
                             "created_at": base["created_at"]}, act)
        base.update({**lg, "priority": "medium", "completed_at": d.closed_at.strftime("%Y-%m-%d") if (d.closed_at and d.status == "done") else None,
                     "approved_at": base["created_at"], "approved_by": d.approver or None})
    if rk is not None and base.get("risk_baseline") is None:
        base["risk_baseline"] = getattr(rk, "baseline_score", None)
        base["risk_key"] = base.get("risk_key") or getattr(rk, "risk_key", None)
    if gact is not None:
        base["goal_id"] = base.get("goal_id") or gact.goal_id
        try:
            base["goal_baseline"] = json.loads(gact.baseline_json or "{}")
        except (TypeError, ValueError):
            base["goal_baseline"] = {}
    if out:
        base["outcome"] = {"actual_manual": out.actual_manual, "notes": out.notes, "measured_at": out.measured_at, "measured_by": out.measured_by}
    base["evidence"] = [{"id": e.id, "source": e.source, "reference": e.reference, "period": e.period, "note": e.note, "url": e.url, "by": e.added_by,
                         "at": e.created_at.strftime("%Y-%m-%d %H:%M") if e.created_at else ""} for e in evs]
    hs = [{"event": h.event, "actor": h.actor, "at": h.timestamp.strftime("%Y-%m-%d %H:%M") if h.timestamp else "", "old": h.old_value, "new": h.new_value, "note": h.note} for h in hist]
    if not any(h["event"] == "created" for h in hs):
        hs.append({"event": "created", "actor": d.created_by or "—", "at": d.created_at.strftime("%Y-%m-%d %H:%M") if d.created_at else "", "old": "", "new": d.title, "note": "من سجل القرارات"})
    if not m and d.closed_at:
        hs.append({"event": "completed" if d.status == "done" else "cancelled", "actor": "—", "at": d.closed_at.strftime("%Y-%m-%d %H:%M"), "old": "", "new": d.status, "note": d.result_note or ""})
    base["history"] = hs
    return base


def _dec_result(s, company, role, viewer, *, cd=None):
    dx, ge = _load_p24("decisions_engine"), _load_p24("goals_engine")
    if dx is None or ge is None:
        raise HTTPException(503, "محرّك متابعة القرارات غير متاح — " + _p23_diagnostic())
    decs, metas, kpis, outs, evs, hist, acts, gacts, rbase = _dec_rows(s, company.id)
    items = [_dec_dict(dx, d, metas.get(d.id), kpis.get(d.id, []), outs.get(d.id), evs.get(d.id, []), hist.get(d.id, []), acts.get(d.id, []),
                       gacts.get(d.id), rbase.get(d.id)) for d in decs]
    ctx, data = cd or _goals_ctx(s, company)
    gids = {x["goal_id"] for x in items if x.get("goal_id")}
    gev = {}
    for g in s.exec(select(CompanyGoal).where(CompanyGoal.company_id == company.id)).all() if gids else []:
        if g.id in gids and ge.visible(_goal_dict(g), viewer):
            try:
                gev[g.id] = ge.evaluate_goal(_goal_dict(g), ctx)
            except Exception as e:
                _logger.error(f"decision goal link {g.id}: {type(e).__name__}: {str(e)[:150]}")
    res = dx.analyze_decisions(items, goal_evals=gev, sector=_risk_sector(company), today=data["today"], viewer=viewer,
                               currency=getattr(company, "currency", None) or "SAR", ctx=ctx)
    res["role"] = role
    res["module_errors"] = data["errors"]
    res["can_create"] = dx.can("create", {}, viewer)
    res["users"] = [{"id": u.id, "name": u.name} for u in s.exec(select(User).where(User.company_id == company.id)).all()] if res["can_create"] else []
    res["branches_list"] = sorted({b.name for b in s.exec(select(CompanyBranch).where(CompanyBranch.company_id == company.id)).all()} | set(ctx["branches"]))
    res["goals_list"] = [{"id": g.id, "name": g.name} for g in s.exec(select(CompanyGoal).where(CompanyGoal.company_id == company.id, CompanyGoal.archived == 0)).all()
                         if ge.visible(_goal_dict(g), viewer)] if res["can_create"] else []
    return res, items


@app.get("/company/decisions-intelligence")
def company_decisions_intelligence(user: User = Depends(get_current_user)):
    with Session(engine) as s:
        company, role, viewer = _goals_scope(s, user)
        try:
            res, _ = _dec_result(s, company, role, viewer)
        except HTTPException:
            raise
        except Exception as e:
            import traceback as _tb
            _logger.error("decisions-intelligence failed:\n" + _tb.format_exc()[-3000:])
            raise HTTPException(500, f"خطأ في مركز متابعة القرارات — {type(e).__name__}: {str(e)[:200]} (التفاصيل في سجل الخادم)")
        log_audit(company.id, user.id, user.name, "decisions_view", "decisions", f"scope={res['scope']['kind']} n={len(res['decisions'])}")
        return res


def _dec_load(s, company, did):
    try:
        did = int(did)
    except (TypeError, ValueError):
        raise HTTPException(422, "معرّف القرار غير صالح")
    d = s.get(CompanyDecision, did)
    if not d or d.company_id != company.id:
        raise HTTPException(404, "القرار غير موجود")
    return d


def _dec_meta(s, company, d, dx, create=True):
    """يعيد طبقة المتابعة؛ القرار القديم يُتبنّى عند أول إجراء (مع حفظ حالته المستنتجة) دون تعديل بياناته."""
    m = s.exec(select(CompanyDecisionMeta).where(CompanyDecisionMeta.company_id == company.id, CompanyDecisionMeta.decision_id == d.id)).first()
    if m or not create:
        return m
    acts = [{"status": a.status, "progress": a.progress} for a in s.exec(select(CompanyAction).where(CompanyAction.company_id == company.id, CompanyAction.decision_id == d.id)).all()]
    lg = dx.from_legacy({"status": d.status, "linked_to": d.linked_to, "decision_type": d.decision_type, "kpi": d.kpi, "metric_id": d.metric_id,
                         "expected_impact_value": d.expected_impact_value, "source_signal": d.source_signal}, acts)
    m = CompanyDecisionMeta(company_id=company.id, decision_id=d.id, workflow=lg["workflow"], source=lg["source"], goal_id=lg["goal_id"], risk_key=lg["risk_key"] or "",
                            depends_on=",".join(str(x) for x in lg["depends_on"]), approved_at=d.created_at.strftime("%Y-%m-%d") if d.created_at else "",
                            approved_by=d.approver or "", completed_at=d.closed_at.strftime("%Y-%m-%d") if (d.closed_at and d.status == "done") else "")
    s.add(m)
    for k in lg["kpis"]:
        s.add(CompanyDecisionKPI(company_id=company.id, decision_id=d.id, metric=k["metric"], baseline=k.get("baseline"),
                                 baseline_at=d.created_at.strftime("%Y-%m-%d") if d.created_at else ""))
    _dec_hist(s, company.id, d.id, "edited", "system", "", lg["workflow"], "تبنّي قرار من السجل السابق في مركز المتابعة (بدون تعديل بياناته)")
    s.commit(); s.refresh(m)
    return m


def _dec_engine_dict(s, company, d, dx):
    decs, metas, kpis, outs, evs, hist, acts, gacts, rbase = _dec_rows(s, company.id)
    return _dec_dict(dx, d, metas.get(d.id), kpis.get(d.id, []), outs.get(d.id), evs.get(d.id, []), hist.get(d.id, []), acts.get(d.id, []), gacts.get(d.id), rbase.get(d.id))


def _dec_baselines(s, company, dx, metrics, branch, m):
    """لقطات خط الأساس وقت القرار من المصدر الموحد (للمؤشرات بلا تاريخ شهري) + المخاطر/السبب/التسرب."""
    ctx, data = _goals_ctx(s, company)
    out = {}
    for met in metrics:
        meta = _load_p24("goals_engine").metric_meta(met)
        if not meta:
            continue
        months = dx.complete_months(ctx, meta)[-dx.BEFORE_MONTHS:] if meta["src"] in ("sales", "fin", "ratio") else None
        snap = dx.kpi_snapshot(met, ctx, branch if meta["branch"] else None, months)
        out[met] = (snap.get("value"), snap.get("basis_ar") or "")
    for key, attr in ((m.risk_key, "risk_baseline"), (m.root_cause, "root_cause_baseline")):
        if key and getattr(m, attr) is None:
            dr = dx._driver(ctx, key)
            setattr(m, attr, dr.get("score") if dr else None)
    if m.leakage_key and m.leakage_baseline is None:
        t = next((x for x in (((ctx["mods"].get("leakage") or {}).get("overview") or {}).get("types") or []) if x.get("key") == m.leakage_key), None)
        m.leakage_baseline = (t or {}).get("amount")
    return out


@app.post("/company/decisions-center/save")
def company_decision_center_save(data: dict, user: User = Depends(get_current_user)):
    """إنشاء/تعديل قرار مع مؤشره وخط أساسه وأثره المتوقع ومصدره. الأرقام المرجعية تُلتقط من المصدر الموحد في الخادم."""
    with Session(engine) as s:
        company, role, viewer = _goals_scope(s, user)
        dx = _load_p24("decisions_engine")
        if dx is None:
            raise HTTPException(503, "محرّك متابعة القرارات غير متاح — " + _p23_diagnostic())
        by = (user.name or user.email)[:100]
        kp = [{"metric": k.get("metric"), "target": k.get("target"), "side_effect": bool(k.get("side_effect")), "branch": k.get("branch")}
              for k in (data.get("kpis") or []) if isinstance(k, dict) and k.get("metric")]
        new = {"title": str(data.get("title") or "").strip()[:200], "priority": data.get("priority") or "medium", "category": data.get("category") or None,
               "due": str(data.get("due") or "")[:10] or None, "expected_impact": data.get("expected_impact"), "cost": data.get("cost"), "kpis": kp,
               "delay_reason": data.get("delay_reason") or None}
        errs = dx.validate_decision(new)
        if errs:
            raise HTTPException(422, " · ".join(errs))
        exp, cost = _fnum(data.get("expected_impact"), "الأثر المتوقع"), _fnum(data.get("cost"), "التكلفة")
        deps = [int(x) for x in (data.get("depends_on") or []) if str(x).isdigit()]
        own_ids = {x.id for x in s.exec(select(CompanyDecision).where(CompanyDecision.company_id == company.id)).all()}
        if any(x not in own_ids for x in deps):
            raise HTTPException(422, "قرار معتمد عليه غير موجود في شركتك")
        gid = int(data["goal_id"]) if str(data.get("goal_id") or "").isdigit() else None
        if gid:
            gg = s.get(CompanyGoal, gid)
            if not gg or gg.company_id != company.id:
                raise HTTPException(422, "الهدف ليس من شركتك")
        ou = None
        if str(data.get("owner_user_id") or "").isdigit():
            ou = s.get(User, int(data["owner_user_id"]))
            if not ou or ou.company_id != company.id:
                raise HTTPException(422, "المسؤول ليس من فريق شركتك")
        owner = str(data.get("owner") or (ou.name if ou else "") or "").strip()[:100]
        if data.get("id"):
            d = _dec_load(s, company, data["id"])
            m = _dec_meta(s, company, d, dx)
            cur = _dec_engine_dict(s, company, d, dx)
            if not dx.can("edit", cur, viewer):
                raise HTTPException(403, "غير مصرّح بتعديل هذا القرار")
            if m.workflow in ("measured", "cancelled"):
                raise HTTPException(409, "القرار مغلق — لا يُعدّل")
            changes = [(f, a, b) for f, a, b in (("title", d.title, new["title"]), ("owner", d.owner, owner), ("due", d.due_date, new["due"] or ""),
                                                  ("expected_impact", d.expected_impact_value, exp), ("priority", m.priority, new["priority"]), ("cost", m.cost, cost))
                       if (a or None) != (b or None)]
            if changes and m.workflow not in ("draft", "rejected") and not str(data.get("reason") or "").strip():
                raise HTTPException(422, "سبب التعديل مطلوب بعد إرسال القرار للاعتماد (حوكمة)")
        else:
            if not dx.can("create", {}, viewer):
                raise HTTPException(403, "غير مصرّح بإنشاء قرار — المالك والرئيس التنفيذي والمدراء")
            d = CompanyDecision(company_id=company.id, status="open", created_by=by, baseline_sales=_company_total_sales(s, company.id), decision_type="")
            m = None
            changes = []
        cat = new["category"] or ""
        src = data.get("source") if data.get("source") in dx.SOURCES else "manual"
        d.title, d.detail, d.rationale = new["title"], str(data.get("description") or "")[:1000], str(data.get("rationale") or "")[:500]
        d.owner, d.due_date, d.expected_impact_value = owner, new["due"] or "", exp
        d.expected_impact = (f"{exp:,.0f} {getattr(company, 'currency', None) or 'SAR'} (متوقع)" if exp is not None else "")[:200]
        prim = next((k for k in kp if not k["side_effect"]), None)
        d.kpi = d.metric_id = (prim or {}).get("metric") or ""
        d.decision_type = d.decision_type or src
        d.linked_to = (f"goal:{gid}" if gid else (f"risk:{data.get('risk_key')}" if data.get("risk_key") else ",".join(str(x) for x in deps)))[:200]
        d.updated_at = datetime.now()
        s.add(d); s.commit(); s.refresh(d)
        if m is None:
            m = CompanyDecisionMeta(company_id=company.id, decision_id=d.id, workflow="draft", created_by_id=user.id)
            _dec_hist(s, company.id, d.id, "created", by, "", d.title, str(data.get("rationale") or "")[:300])
        m.priority, m.category, m.source, m.source_ref = new["priority"], cat, src, str(data.get("source_ref") or "")[:200]
        m.department = str(data.get("department") or "")[:40] if data.get("department") in _load_p24("goals_engine").DEPARTMENTS else ""
        m.branch, m.owner_user_id, m.cost = str(data.get("branch") or "")[:100], ou.id if ou else m.owner_user_id, cost
        m.depends_on, m.goal_id = ",".join(str(x) for x in deps if x != d.id), gid
        for f in ("risk_key", "root_cause", "leakage_key"):
            v = str(data.get(f) or "")[:60]
            if v != (getattr(m, f) or ""):
                setattr(m, f, v)
                setattr(m, {"risk_key": "risk_baseline", "root_cause": "root_cause_baseline", "leakage_key": "leakage_baseline"}[f], None)
        m.expected_type = data.get("expected_type") if data.get("expected_type") in ("potential", "recovery", "actual") else "potential"
        m.sensitive = 1 if data.get("sensitive") else 0
        m.updated_at = datetime.now()
        # KPIs: تُستبدل قبل الاعتماد فقط (بعده تُعدّل من نقطة المؤشرات بسجل)
        if m.workflow in ("draft", "rejected", "pending_approval", "under_review") or not m.id:
            old = s.exec(select(CompanyDecisionKPI).where(CompanyDecisionKPI.company_id == company.id, CompanyDecisionKPI.decision_id == d.id)).all()
            for k in old:
                s.delete(k)
            snaps = _dec_baselines(s, company, dx, [k["metric"] for k in kp], m.branch or None, m)
            for k in kp:
                b = snaps.get(k["metric"], (None, ""))
                s.add(CompanyDecisionKPI(company_id=company.id, decision_id=d.id, metric=k["metric"], branch=str(k.get("branch") or "")[:100],
                                         target=_fnum(k.get("target"), "مستهدف المؤشر"), side_effect=1 if k["side_effect"] else 0,
                                         baseline=b[0], baseline_at=datetime.now().strftime("%Y-%m-%d"), period=b[1][:120]))
        else:
            _dec_baselines(s, company, dx, [], m.branch or None, m)
        s.add(m)
        for f, a, b in changes:
            _dec_hist(s, company.id, d.id, "owner_changed" if f == "owner" else "edited", by, a, b, str(data.get("reason") or f))
        s.commit()
        log_audit(company.id, user.id, user.name, "decision_save", f"decision:{d.id}", json.dumps([c[0] for c in changes], ensure_ascii=False)[:200])
        if data.get("submit"):
            return company_decision_center_transition({"id": d.id, "to": "pending_approval"}, user=user)
        return {"ok": True, "id": d.id}


@app.post("/company/decisions-center/transition")
def company_decision_center_transition(data: dict, user: User = Depends(get_current_user)):
    """مسار الاعتماد: مسودة → بانتظار الاعتماد → مراجعة → معتمد/مرفوض → قيد التنفيذ → مكتمل → مُقاس. كل انتقال بصلاحيته ويُسجّل."""
    with Session(engine) as s:
        company, role, viewer = _goals_scope(s, user)
        dx = _load_p24("decisions_engine")
        d = _dec_load(s, company, data.get("id"))
        m = _dec_meta(s, company, d, dx)
        to = str(data.get("to") or "")
        cur = _dec_engine_dict(s, company, d, dx)
        errs = dx.transition_check(cur, to, viewer)
        if errs:
            raise HTTPException(403 if any("صلاحيت" in e or "المنشئ" in e for e in errs) else 422, " · ".join(errs))
        note = str(data.get("note") or data.get("reason") or "").strip()[:500]
        if to in ("rejected", "cancelled") and not note:
            raise HTTPException(422, "السبب مطلوب للرفض أو الإلغاء")
        if to == "measured":
            return company_decision_center_measure({"id": d.id, "notes": note}, user=user)
        by = (user.name or user.email)[:100]
        today_ = datetime.now().strftime("%Y-%m-%d")
        old = m.workflow
        m.workflow = to
        if to == "pending_approval":
            m.submitted_at = today_
        elif to == "approved":
            m.approved_by, m.approved_at = by, today_
            d.approver = by
        elif to == "rejected":
            m.rejection_reason = note
        elif to == "in_progress":
            m.started_at = m.started_at or today_
        elif to == "completed":
            m.completed_at = today_
        elif to == "cancelled":
            m.cancelled_at = today_
        m.updated_at = datetime.now()
        _dec_sync_legacy(d, to)
        if to == "completed":
            d.outcome_status = "pending_measurement"
        s.add(m); s.add(d)
        ev = {"pending_approval": "submitted", "under_review": "under_review", "draft": "draft"}.get(to, to)
        _dec_hist(s, company.id, d.id, ev, by, old, to, note)
        if data.get("recommend") and to == "under_review":
            _dec_hist(s, company.id, d.id, "recommended", by, "", "", note)
        s.commit()
        log_audit(company.id, user.id, user.name, f"decision_{to}", f"decision:{d.id}", f"{old}->{to} {note[:120]}")
        return {"ok": True, "id": d.id, "workflow": to}


@app.post("/company/decisions-center/measure")
def company_decision_center_measure(data: dict, user: User = Depends(get_current_user)):
    """اعتماد القياس: لقطة متوقع/فعلي/فرق/نتيجة من المحرك. القيمة اليدوية تُقبل فقط مع دليل وتُوسم يدوية. بلا بيانات → لا حكم."""
    with Session(engine) as s:
        company, role, viewer = _goals_scope(s, user)
        dx = _load_p24("decisions_engine")
        d = _dec_load(s, company, data.get("id"))
        m = _dec_meta(s, company, d, dx)
        cur = _dec_engine_dict(s, company, d, dx)
        if not dx.can("measure", cur, viewer):
            raise HTTPException(403, "اعتماد القياس للمالك أو الرئيس التنفيذي")
        if m.workflow != "completed":
            raise HTTPException(422, "يُقاس القرار بعد إكماله فقط (المكتمل ≠ الناجح)")
        manual = _fnum(data.get("actual_manual"), "الأثر الفعلي")
        notes = str(data.get("notes") or "").strip()[:1000]
        if manual is not None and not notes:
            raise HTTPException(422, "القيمة اليدوية تحتاج دليلاً/مصدراً مكتوباً")
        if manual is not None:
            cur["outcome"] = {"actual_manual": manual, "notes": notes}
        res, items = _dec_result(s, company, role, viewer)
        cur["workflow"] = "completed"
        ctx, _d = _goals_ctx(s, company)
        e = dx.evaluate_decision({**cur, "outcome": cur.get("outcome") or {}}, ctx, datetime.now().date(), res["overview"]["high_impact_threshold"])
        o = e["outcome"]
        if o["state"] not in ("measurable", "measured"):
            raise HTTPException(422, (o.get("state_ar") or dx.CANNOT_MEASURE_AR) + (" — " + o["reason_ar"] if o.get("reason_ar") else ""))
        by = (user.name or user.email)[:100]
        today_ = datetime.now().strftime("%Y-%m-%d")
        s.add(CompanyDecisionOutcome(company_id=company.id, decision_id=d.id, expected_impact=o.get("expected"), actual_impact=o.get("actual"), variance=o.get("variance"),
                                     result=o.get("result") or "", method=(o.get("method_ar") or "")[:300], actual_manual=manual, notes=notes,
                                     snapshot_json=json.dumps({"kpis": e["kpis"], "outcome": o, "effectiveness": e["effectiveness"]}, ensure_ascii=False, default=str)[:20000],
                                     measured_by=by, measured_at=today_))
        m.workflow, m.updated_at = "measured", datetime.now()
        _dec_sync_legacy(d, "measured")
        d.outcome_status, d.impact_status = "measured", "measured"
        d.actual_impact_value = o.get("actual")
        d.measurement_period = ",".join((e.get("primary") or {}).get("after_months") or [])[:50]
        d.outcome_notes = (notes or o.get("result_ar") or "")[:1000]
        s.add(m); s.add(d)
        _dec_hist(s, company.id, d.id, "measured", by, "completed", f"{o.get('result_ar')} · فعلي {o.get('actual')}", notes or (o.get("method_ar") or ""))
        s.commit()
        log_audit(company.id, user.id, user.name, "decision_measured", f"decision:{d.id}", f"result={o.get('result')} actual={o.get('actual')} manual={manual is not None}")
        return {"ok": True, "id": d.id, "outcome": o, "effectiveness": e["effectiveness"]}


@app.post("/company/decisions-center/kpi")
def company_decision_center_kpi(data: dict, user: User = Depends(get_current_user)):
    """إضافة/تعديل مؤشر قرار (مستهدف، أثر جانبي، قيمة يدوية لمؤشر بلا مصدر) — بسجل."""
    with Session(engine) as s:
        company, role, viewer = _goals_scope(s, user)
        dx, ge = _load_p24("decisions_engine"), _load_p24("goals_engine")
        d = _dec_load(s, company, data.get("id"))
        m = _dec_meta(s, company, d, dx)
        cur = _dec_engine_dict(s, company, d, dx)
        by = (user.name or user.email)[:100]
        if data.get("kpi_id"):
            k = s.get(CompanyDecisionKPI, int(data["kpi_id"]))
            if not k or k.company_id != company.id or k.decision_id != d.id:
                raise HTTPException(404, "المؤشر غير موجود")
        else:
            k = None
        if "actual" in data and k is not None and set(data) <= {"id", "kpi_id", "actual", "note"}:
            if not dx.can("progress", cur, viewer):
                raise HTTPException(403, "غير مصرّح")
            if (ge.metric_meta(k.metric) or {}).get("src") != "manual":
                raise HTTPException(422, "قيمة هذا المؤشر تُقرأ من بيانات نبّاه تلقائياً — لا تُدخل يدوياً")
            old = k.actual
            k.actual, k.actual_at = _fnum(data.get("actual"), "القيمة"), datetime.now().strftime("%Y-%m-%d")
            s.add(k)
            _dec_hist(s, company.id, d.id, "kpi_updated", by, old, k.actual, str(data.get("note") or k.metric))
            s.commit()
            return {"ok": True}
        if not dx.can("kpi", cur, viewer):
            raise HTTPException(403, "غير مصرّح بتعديل مؤشرات القرار")
        if data.get("delete") and k:
            _dec_hist(s, company.id, d.id, "kpi_updated", by, k.metric, "حذف", str(data.get("note") or ""))
            s.delete(k); s.commit()
            return {"ok": True}
        met = data.get("metric") or (k.metric if k else None)
        if not ge.metric_meta(met):
            raise HTTPException(422, "المؤشر غير موجود في الكتالوج")
        if k is None:
            snaps = _dec_baselines(s, company, dx, [met], m.branch or None, m)
            b = snaps.get(met, (None, ""))
            k = CompanyDecisionKPI(company_id=company.id, decision_id=d.id, metric=met, baseline=b[0], baseline_at=datetime.now().strftime("%Y-%m-%d"), period=b[1][:120])
        old = {"target": k.target, "side_effect": k.side_effect}
        if "target" in data:
            k.target = _fnum(data.get("target"), "المستهدف")
        if "side_effect" in data:
            k.side_effect = 1 if data.get("side_effect") else 0
        s.add(k)
        if not d.kpi and not k.side_effect:
            d.kpi = d.metric_id = met
            s.add(d)
        _dec_hist(s, company.id, d.id, "kpi_updated", by, old, {"metric": met, "target": k.target, "side_effect": k.side_effect}, str(data.get("note") or ""))
        s.commit()
        log_audit(company.id, user.id, user.name, "decision_kpi", f"decision:{d.id}", met)
        return {"ok": True}


@app.post("/company/decisions-center/action")
def company_decision_center_action(data: dict, user: User = Depends(get_current_user)):
    """إجراءات القرار (CompanyAction نفسه): المدير ينشئ ويسند، والمسند إليه يحدّث التقدم."""
    with Session(engine) as s:
        company, role, viewer = _goals_scope(s, user)
        dx = _load_p24("decisions_engine")
        d = _dec_load(s, company, data.get("id"))
        _dec_meta(s, company, d, dx)
        cur = _dec_engine_dict(s, company, d, dx)
        by = (user.name or user.email)[:100]
        if data.get("action_id"):
            a = s.get(CompanyAction, int(data["action_id"]))
            if not a or a.company_id != company.id or a.decision_id != d.id:
                raise HTTPException(404, "الإجراء غير موجود")
            mine = _load_p24("goals_engine")._is_mine({"owner": a.owner}, viewer)
            if not (dx.can("assign", cur, viewer) or (mine and dx.can("progress", cur, viewer))):
                raise HTTPException(403, "غير مصرّح")
            before = f"{a.status}/{a.progress}"
            if data.get("status"):
                if data["status"] not in ACTION_STATUSES:
                    raise HTTPException(422, "حالة غير صالحة")
                a.status = data["status"]
            if "progress" in data:
                try:
                    p_ = int(data["progress"])
                except (TypeError, ValueError):
                    raise HTTPException(422, "نسبة الإنجاز غير صالحة")
                if not 0 <= p_ <= 100:
                    raise HTTPException(422, "نسبة الإنجاز بين 0 و100")
                a.progress = p_
            if a.status == "completed":
                a.progress = 100
            note = str(data.get("note") or "").strip()
            if note:
                a.notes = ((a.notes + "\n") if a.notes else "") + f"[{_dec_now()} · {by}] {note[:500]}"
            a.updated_at = datetime.now()
            s.add(a)
            _dec_hist(s, company.id, d.id, "progress", by, before, f"{a.status}/{a.progress}", note or a.title)
            s.commit()
            return {"ok": True, "action_id": a.id}
        if not dx.can("assign", cur, viewer):
            raise HTTPException(403, "إضافة الإجراءات للمدراء والإدارة")
        title = str(data.get("title") or "").strip()[:200]
        if not title:
            raise HTTPException(422, "عنوان الإجراء مطلوب")
        due = str(data.get("due") or "")[:10]
        if due and not _valid_date(due):
            raise HTTPException(422, "تاريخ غير صالح")
        a = CompanyAction(company_id=company.id, decision_id=d.id, title=title, owner=str(data.get("owner") or d.owner or "")[:100], due_date=due,
                          start_date=datetime.now().strftime("%Y-%m-%d"), priority="P2", updated_at=datetime.now())
        s.add(a)
        _dec_hist(s, company.id, d.id, "assigned", by, "", f"{title} → {a.owner or '—'}", "")
        s.commit(); s.refresh(a)
        return {"ok": True, "action_id": a.id}


@app.post("/company/decisions-center/evidence")
def company_decision_center_evidence(data: dict, user: User = Depends(get_current_user)):
    with Session(engine) as s:
        company, role, viewer = _goals_scope(s, user)
        dx = _load_p24("decisions_engine")
        d = _dec_load(s, company, data.get("id"))
        _dec_meta(s, company, d, dx)
        if not dx.can("evidence", _dec_engine_dict(s, company, d, dx), viewer):
            raise HTTPException(403, "غير مصرّح")
        url = str(data.get("url") or "").strip()[:500]
        if url and not url.lower().startswith(("https://", "http://")):
            raise HTTPException(422, "رابط الدليل يجب أن يبدأ بـ https://")
        src, ref, note = str(data.get("source") or "")[:120], str(data.get("reference") or "")[:200], str(data.get("note") or "")[:1000]
        if not (src or ref or note or url):
            raise HTTPException(422, "اكتب مصدر الدليل أو مرجعه")
        by = (user.name or user.email)[:100]
        s.add(CompanyDecisionEvidence(company_id=company.id, decision_id=d.id, source=src, reference=ref, period=str(data.get("period") or "")[:40], note=note, url=url, added_by=by))
        _dec_hist(s, company.id, d.id, "evidence", by, "", src or ref or url, note[:200])
        s.commit()
        log_audit(company.id, user.id, user.name, "decision_evidence", f"decision:{d.id}", (src or ref)[:100])
        return {"ok": True}


@app.post("/company/decisions-center/delay")
def company_decision_center_delay(data: dict, user: User = Depends(get_current_user)):
    with Session(engine) as s:
        company, role, viewer = _goals_scope(s, user)
        dx = _load_p24("decisions_engine")
        d = _dec_load(s, company, data.get("id"))
        m = _dec_meta(s, company, d, dx)
        if not dx.can("delay_reason", _dec_engine_dict(s, company, d, dx), viewer):
            raise HTTPException(403, "غير مصرّح")
        r = str(data.get("reason") or "")
        if r not in dx.DELAY_REASONS:
            raise HTTPException(422, "سبب التأخير غير معروف")
        by = (user.name or user.email)[:100]
        old = m.delay_reason
        m.delay_reason, m.delay_note, m.updated_at = r, str(data.get("note") or "")[:300], datetime.now()
        new_due = str(data.get("new_due") or "")[:10]
        if new_due:
            if not _valid_date(new_due):
                raise HTTPException(422, "تاريخ غير صالح")
            if not dx.can("edit", _dec_engine_dict(s, company, d, dx), viewer):
                raise HTTPException(403, "تغيير الموعد للمدير أو الإدارة")
            _dec_hist(s, company.id, d.id, "edited", by, d.due_date, new_due, "تمديد الموعد: " + dx.DELAY_REASONS[r])
            d.due_date = new_due
            s.add(d)
        s.add(m)
        _dec_hist(s, company.id, d.id, "delay_reason", by, old, r, m.delay_note)
        s.commit()
        return {"ok": True}


@app.post("/company/decisions-center/escalate")
def company_decision_center_escalate(data: dict, user: User = Depends(get_current_user)):
    """تصعيد للرئيس التنفيذي/المالك أو تعيين مسؤول جديد (حسب الصلاحية)."""
    with Session(engine) as s:
        company, role, viewer = _goals_scope(s, user)
        dx = _load_p24("decisions_engine")
        d = _dec_load(s, company, data.get("id"))
        m = _dec_meta(s, company, d, dx)
        cur = _dec_engine_dict(s, company, d, dx)
        if not dx.can("escalate", cur, viewer):
            raise HTTPException(403, "التصعيد للمدير أو الإدارة")
        by = (user.name or user.email)[:100]
        note = str(data.get("note") or "").strip()[:500]
        if not note:
            raise HTTPException(422, "اكتب سبب التصعيد")
        new_owner = str(data.get("new_owner") or "").strip()[:100]
        if new_owner:
            if not dx.can("assign", cur, viewer):
                raise HTTPException(403, "غير مصرّح بتغيير المسؤول")
            _dec_hist(s, company.id, d.id, "owner_changed", by, d.owner, new_owner, note)
            d.owner = new_owner
            m.owner_user_id = None
            s.add(d)
        if m.priority not in ("critical",):
            _dec_hist(s, company.id, d.id, "edited", by, m.priority, "critical", "رفع الأولوية بالتصعيد")
            m.priority = "critical"
        m.updated_at = datetime.now()
        s.add(m)
        _dec_hist(s, company.id, d.id, "escalated", by, "", "الرئيس التنفيذي / المالك", note)
        s.commit()
        log_audit(company.id, user.id, user.name, "decision_escalated", f"decision:{d.id}", note[:200])
        return {"ok": True}


@app.post("/company/decisions-center/ai-insights")
def company_decisions_ai(data: dict, request: Request, user: User = Depends(get_current_user)):
    """AI يحلل نتائج القرارات ويكتب المراجعة من أرقام المحرك فقط — لا يصدر قراراً."""
    with Session(engine) as s:
        company, role, viewer = _goals_scope(s, user)
        res, _ = _dec_result(s, company, role, viewer)
    dx, gw = _load_p24("decisions_engine"), _load_p24("ai_gateway")
    if gw is None:
        raise HTTPException(503, "بوابة الذكاء الاصطناعي غير متاحة — " + _p23_diagnostic())
    if not res["decisions"]:
        raise HTTPException(422, "لا توجد قرارات ضمن نطاقك")
    q = str(data.get("question") or res["ai_questions"][0])[:300]
    ctx = dx.ai_context(res)
    did = data.get("decision_id")
    if did is not None:
        one = next((e for e in res["decisions"] if str(e["id"]) == str(did)), None)
        if not one:
            raise HTTPException(404, "القرار خارج نطاقك")
        ctx = {"decision": next(x for x in ctx["decisions"] if x["title"] == one["title"]) if any(x["title"] == one["title"] for x in ctx["decisions"]) else {"title": one["title"], "review": one["review"]},
               "rule": ctx["rule"]}
        q = q or "اكتب مراجعة هذا القرار"
    out = gw.request_ai_analysis(gw.GeminiProvider(company_gemini), ctx,
                                 q + " — اعتمد أرقام محرك القرارات كما هي (متوقع/فعلي/فرق/نتيجة/فعالية). لا تُصدر قراراً، ولا تقل إن القرار «سبّب» التغيّر — "
                                     "قبل/بعد مقارنة زمنية. إن كان القياس غير ممكن فقل ذلك صراحة.",
                                 trust_report={"overall_score": 75, "status": "pass", "has_critical_fail": False, "main_causes": []},
                                 lang=get_lang(request), company=company)
    log_audit(company.id, user.id, user.name, "decisions_ai", "decisions", f"q={q[:80]}")
    return {"question": q, "ai": out}


# ═══════════════════════════════════════════════════════════
#  Phase 3.10 — Board Presentation (حزمة قرار لمجلس الإدارة) — تقرأ من الطبقة الموحدة فقط
#  النسخ المحفوظة لا تُعدّل: تغيّر البيانات = نسخة جديدة · كل عرض/إنشاء/تصدير يُسجَّل
# ═══════════════════════════════════════════════════════════
def _board_scope(s, user):
    company, role, viewer = _goals_scope(s, user)
    be = _load_p24("board_engine")
    if be is None:
        raise HTTPException(503, "محرّك عرض المجلس غير متاح — " + _p23_diagnostic())
    sc = be.board_scope(viewer)
    if sc["kind"] == "none":
        raise HTTPException(403, "عرض مجلس الإدارة للمالك والرئيس التنفيذي والمدير المالي وأعضاء المجلس والمدراء")
    return company, role, viewer, sc, be


def _board_prev(s, company_id, before_id=None):
    q = select(CompanyBoardReport).where(CompanyBoardReport.company_id == company_id).order_by(CompanyBoardReport.generated_at.desc())
    for r in s.exec(q.limit(50)).all():
        if before_id and r.id >= before_id:
            continue
        try:
            return json.loads(r.snapshot_json or "{}") or None, r
        except (TypeError, ValueError):
            continue
    return None, None


def _board_live(s, company, role, viewer, sc, be, *, before_id=None):
    """يبني الحزمة الحية: نفس سياق المحركات مرة واحدة → الأهداف (3.8) والقرارات (3.9) → المجلس. العرض للمجلس على مستوى الشركة."""
    cd = _goals_ctx(s, company)
    ctx, d = cd
    data_viewer = {"role": "owner", "user_id": viewer.get("user_id")} if sc["kind"] in ("full", "cfo", "board") else viewer
    gres = _goals_result(s, company, role, data_viewer, save=False, cd=cd)
    dres, _ = _dec_result(s, company, role, data_viewer, cd=cd)
    prev, prev_row = _board_prev(s, company.id, before_id)
    gb = {}
    for b in gres.get("branches") or []:
        sts = [g["status"] for g in (b.get("goals") or []) + (b.get("implied") or [])]
        if sts:
            gb[b["branch"]] = {"behind": sts.count("behind"), "at_risk": sts.count("at_risk"), "on_track": sts.count("on_track")}
    pack = be.analyze_board(ctx=ctx, risk=d["risk"], drivers=d["drivers"], pred=d["pred"], goals_res=gres, dec_res=dres,
                            budget=(_fin_settings(s, company.id).get("budget") or {}), previous=prev, sector=_risk_sector(company), today=d["today"],
                            viewer=viewer, currency=getattr(company, "currency", None) or "SAR", fy_start=d["fy_start"], company_name=company.name, goal_branches=gb)
    pack["previous_report"] = {"id": prev_row.id, "version": prev_row.version, "period": prev_row.period,
                               "at": prev_row.generated_at.strftime("%Y-%m-%d") if prev_row.generated_at else ""} if prev_row else None
    pack["module_errors"] = d["errors"]
    return pack


def _board_reports(s, company_id):
    rows = s.exec(select(CompanyBoardReport).where(CompanyBoardReport.company_id == company_id).order_by(CompanyBoardReport.generated_at.desc()).limit(100)).all()
    views = {}
    for a in s.exec(select(AuditLog).where(AuditLog.company_id == company_id, AuditLog.action == "board_report_view")).all():
        views[a.target] = views.get(a.target, 0) + 1
    return [{"id": r.id, "period": r.period, "version": r.version, "title": r.title, "status": r.status, "generated_by": r.generated_by,
             "generated_at": r.generated_at.strftime("%Y-%m-%d %H:%M") if r.generated_at else "", "meeting_date": r.meeting_date, "presented_at": r.presented_at,
             "exports": r.exports or 0, "views": views.get(f"board:{r.id}", 0), "engine": r.engine_version} for r in rows]


@app.get("/company/board-intelligence")
def company_board_intelligence(user: User = Depends(get_current_user)):
    with Session(engine) as s:
        company, role, viewer, sc, be = _board_scope(s, user)
        try:
            pack = _board_live(s, company, role, viewer, sc, be)
        except HTTPException:
            raise
        except Exception as e:
            import traceback as _tb
            _logger.error("board-intelligence failed:\n" + _tb.format_exc()[-3000:])
            raise HTTPException(500, f"خطأ في عرض مجلس الإدارة — {type(e).__name__}: {str(e)[:200]} (التفاصيل في سجل الخادم)")
        pack["reports"] = _board_reports(s, company.id)
        pack["live"] = True
        log_audit(company.id, user.id, user.name, "board_view", "board:live", f"scope={sc['kind']}")
        return pack


@app.post("/company/board/generate")
def company_board_generate(data: dict, user: User = Depends(get_current_user)):
    """3.10.17/3.10.19 — ينشئ حزمة مجلس كنسخة جديدة مجمّدة (لا يُعدّل أي نسخة سابقة)."""
    with Session(engine) as s:
        company, role, viewer, sc, be = _board_scope(s, user)
        if not sc["can_generate"]:
            raise HTTPException(403, "إنشاء حزمة المجلس للمالك والرئيس التنفيذي والمدير المالي")
        md = str(data.get("meeting_date") or "")[:10]
        if md and not _valid_date(md):
            raise HTTPException(422, "تاريخ الاجتماع غير صالح")
        full = {"role": "owner", "user_id": user.id}
        pack = _board_live(s, company, role, full, be.board_scope(full), be)
        same = s.exec(select(CompanyBoardReport).where(CompanyBoardReport.company_id == company.id, CompanyBoardReport.period == pack["period"])).all()
        ver = max([r.version for r in same] or [0]) + 1
        import hashlib as _h
        body = json.dumps(pack, ensure_ascii=False, default=str)
        r = CompanyBoardReport(company_id=company.id, period=pack["period"], version=ver, title=str(data.get("title") or f"حزمة مجلس الإدارة — {pack['period']} — النسخة {ver}")[:200],
                               status="final", meeting_date=md, generated_by=(user.name or user.email)[:100], generated_by_id=user.id, generated_at=datetime.now(),
                               snapshot_json=json.dumps(pack["snapshot"], ensure_ascii=False, default=str), pack_json=body,
                               checksum=_h.sha256(body.encode()).hexdigest()[:32], engine_version=pack["version"])
        s.add(r); s.commit(); s.refresh(r)
        for i, (k, _a) in enumerate(be.SECTIONS):
            v = pack.get(k)
            s.add(CompanyBoardReportSection(company_id=company.id, report_id=r.id, section=k, sort_order=i,
                                            content_json=json.dumps(v, ensure_ascii=False, default=str)[:200000], source=",".join(sorted({m["source_module"] for m in pack["metrics"]}))[:200] if k == "financial" else ""))
        for m in pack["metrics"]:
            s.add(CompanyBoardReportMetric(company_id=company.id, report_id=r.id, metric=str(m["metric"])[:80], label=str(m.get("label") or "")[:120],
                                           value=m.get("value"), source_module=str(m.get("source_module") or "")[:40], confidence=str(m.get("confidence") if m.get("confidence") is not None else "")[:20],
                                           period=str(m.get("period") or "")[:40]))
        for it in pack["decisions_required"]:
            s.add(CompanyBoardDecisionItem(company_id=company.id, report_id=r.id, decision_id=it.get("decision_id"), goal_id=it.get("goal_id"), title=it["title"][:200],
                                           priority=it.get("risk_if_delayed") or "", recommendation=it.get("recommendation") or ""))
        s.commit()
        log_audit(company.id, user.id, user.name, "board_generate", f"board:{r.id}", f"period={r.period} version={ver} checksum={r.checksum}")
        return {"ok": True, "id": r.id, "version": ver, "period": r.period}


@app.get("/company/board/report/{rid}")
def company_board_report(rid: int, user: User = Depends(get_current_user)):
    """النسخة المحفوظة كما كانت وقت إنشائها (مقيّدة بصلاحية القارئ) + من شاهدها ومن صدّرها."""
    with Session(engine) as s:
        company, role, viewer, sc, be = _board_scope(s, user)
        r = s.get(CompanyBoardReport, rid)
        if not r or r.company_id != company.id:
            raise HTTPException(404, "الحزمة غير موجودة")
        try:
            pack = json.loads(r.pack_json or "{}")
        except (TypeError, ValueError):
            raise HTTPException(500, "تعذّرت قراءة الحزمة المحفوظة")
        pack = be.restrict(pack, viewer)
        items = s.exec(select(CompanyBoardDecisionItem).where(CompanyBoardDecisionItem.company_id == company.id, CompanyBoardDecisionItem.report_id == r.id)).all()
        trail = s.exec(select(AuditLog).where(AuditLog.company_id == company.id, AuditLog.target == f"board:{r.id}").order_by(AuditLog.created_at.desc()).limit(200)).all()
        log_audit(company.id, user.id, user.name, "board_report_view", f"board:{r.id}", f"version={r.version}")
        pack.update({"live": False, "report": {"id": r.id, "period": r.period, "version": r.version, "title": r.title, "status": r.status, "generated_by": r.generated_by,
                                               "generated_at": r.generated_at.strftime("%Y-%m-%d %H:%M") if r.generated_at else "", "checksum": r.checksum,
                                               "meeting_date": r.meeting_date, "presented_at": r.presented_at, "exports": r.exports or 0},
                     "resolutions": [{"id": i.id, "decision_id": i.decision_id, "goal_id": i.goal_id, "title": i.title, "recommendation": i.recommendation,
                                      "resolution": i.resolution, "resolved_by": i.resolved_by, "resolved_at": i.resolved_at, "note": i.note} for i in items],
                     "access_log": [{"action": a.action, "user": a.user_name, "at": a.created_at.strftime("%Y-%m-%d %H:%M") if a.created_at else "", "details": a.details} for a in trail],
                     "reports": _board_reports(s, company.id),
                     "frozen_note_ar": "نسخة مجمّدة كما كانت وقت إنشائها — لا تتغير مع البيانات. لإظهار الأرقام الحالية أنشئ نسخة جديدة."})
        return pack


@app.post("/company/board/export")
def company_board_export(data: dict, user: User = Depends(get_current_user)):
    """3.10.18 — تسجيل تصدير PDF (الطباعة من المتصفح بتنسيق الحزمة) — من صدّر، أي نسخة، متى."""
    with Session(engine) as s:
        company, role, viewer, sc, be = _board_scope(s, user)
        if sc["kind"] not in ("full", "cfo", "board"):
            raise HTTPException(403, "تصدير حزمة المجلس للإدارة العليا وأعضاء المجلس فقط")
        rid = data.get("id")
        if rid:
            r = s.get(CompanyBoardReport, int(rid))
            if not r or r.company_id != company.id:
                raise HTTPException(404, "الحزمة غير موجودة")
            r.exports = (r.exports or 0) + 1
            s.add(r); s.commit()
            log_audit(company.id, user.id, user.name, "board_export", f"board:{r.id}", f"format=pdf version={r.version}")
        else:
            log_audit(company.id, user.id, user.name, "board_export", "board:live", "format=pdf live (غير محفوظ)")
        return {"ok": True}


@app.post("/company/board/present")
def company_board_present(data: dict, user: User = Depends(get_current_user)):
    """يوسم الحزمة «عُرضت في اجتماع» — تصبح أساس مقارنة الاجتماع القادم."""
    with Session(engine) as s:
        company, role, viewer, sc, be = _board_scope(s, user)
        if not sc["can_resolve"]:
            raise HTTPException(403, "للمالك أو الرئيس التنفيذي")
        r = s.get(CompanyBoardReport, int(data.get("id") or 0))
        if not r or r.company_id != company.id:
            raise HTTPException(404, "الحزمة غير موجودة")
        r.status, r.presented_at = "presented", datetime.now().strftime("%Y-%m-%d")
        s.add(r); s.commit()
        log_audit(company.id, user.id, user.name, "board_present", f"board:{r.id}", f"version={r.version}")
        return {"ok": True}


@app.post("/company/board/resolve")
def company_board_resolve(data: dict, user: User = Depends(get_current_user)):
    """قرار المجلس في بند مطلوب: اعتماد/رفض/تأجيل — الاعتماد والرفض يُنفّذان عبر مسار الاعتماد في 3.9 (لا مسار موازٍ)."""
    with Session(engine) as s:
        company, role, viewer, sc, be = _board_scope(s, user)
        if not sc["can_resolve"]:
            raise HTTPException(403, "قرار المجلس يسجله المالك أو الرئيس التنفيذي")
        it = s.get(CompanyBoardDecisionItem, int(data.get("item_id") or 0))
        if not it or it.company_id != company.id:
            raise HTTPException(404, "البند غير موجود")
        res = str(data.get("resolution") or "")
        if res not in ("approved", "rejected", "deferred"):
            raise HTTPException(422, "القرار: اعتماد أو رفض أو تأجيل")
        note = str(data.get("note") or "").strip()[:500]
        if res in ("rejected", "deferred") and not note:
            raise HTTPException(422, "السبب مطلوب للرفض أو التأجيل")
        if it.resolution:
            raise HTTPException(409, "سُجّل قرار المجلس لهذا البند — أنشئ حزمة جديدة لإعادة الطرح")
        rep = s.get(CompanyBoardReport, it.report_id)
        out = None
        if it.decision_id and res in ("approved", "rejected"):
            d = s.get(CompanyDecision, it.decision_id)
            if d and d.company_id == company.id:
                m = s.exec(select(CompanyDecisionMeta).where(CompanyDecisionMeta.company_id == company.id, CompanyDecisionMeta.decision_id == d.id)).first()
                if m and m.workflow in ("pending_approval", "under_review"):
                    out = company_decision_center_transition({"id": d.id, "to": res, "note": f"قرار مجلس الإدارة — حزمة {rep.period if rep else ''} ن{rep.version if rep else ''}" + (f": {note}" if note else "")}, user=user)
        it.resolution, it.resolved_by, it.resolved_at, it.note = res, (user.name or user.email)[:100], datetime.now().strftime("%Y-%m-%d"), note
        s.add(it); s.commit()
        log_audit(company.id, user.id, user.name, "board_resolution", f"board:{it.report_id}", f"item={it.id} decision={it.decision_id} {res}")
        return {"ok": True, "resolution": res, "decision_transition": out}


@app.post("/company/board/ai-insights")
def company_board_ai(data: dict, request: Request, user: User = Depends(get_current_user)):
    """Board Intelligence — يجيب من حزمة المجلس فقط (مقيّدة بصلاحية السائل) ولا يُصدر قراراً بدل المجلس."""
    with Session(engine) as s:
        company, role, viewer, sc, be = _board_scope(s, user)
        if data.get("report_id"):
            r = s.get(CompanyBoardReport, int(data["report_id"]))
            if not r or r.company_id != company.id:
                raise HTTPException(404, "الحزمة غير موجودة")
            pack = be.restrict(json.loads(r.pack_json or "{}"), viewer)
        else:
            pack = _board_live(s, company, role, viewer, sc, be)
    gw = _load_p24("ai_gateway")
    if gw is None:
        raise HTTPException(503, "بوابة الذكاء الاصطناعي غير متاحة — " + _p23_diagnostic())
    q = str(data.get("question") or pack["ai_questions"][0])[:300]
    if data.get("narrative"):
        q = "اكتب سرداً تنفيذياً قصيراً للمجلس: ماذا حدث، لماذا، الأثر المالي، ماذا سيحدث، القرار المطلوب"
    out = gw.request_ai_analysis(gw.GeminiProvider(company_gemini), be.ai_context(pack),
                                 q + " — من أرقام حزمة المجلس فقط بثقتها. صياغة تنفيذية قصيرة. لا تخترع أرقاماً ولا تقرر بدل المجلس، والعلاقات ليست سببية مثبتة.",
                                 trust_report={"overall_score": 75, "status": "pass", "has_critical_fail": False, "main_causes": []},
                                 lang=get_lang(request), company=company)
    log_audit(company.id, user.id, user.name, "board_ai", f"board:{data.get('report_id') or 'live'}", f"q={q[:80]}")
    return {"question": q, "ai": out}


@app.get("/company/executive-intelligence")
def company_executive_intelligence(request: Request, user: User = Depends(get_current_user),
                                   period: Optional[str] = None, ai: int = 0):
    with Session(engine) as s:
        company, _role = _exec_scope(s, user)
        result = _build_exec(s, company, period)
        result["ai_summary"] = None
        try:   # إشارات الوحدات: المبيعات (لا تُنشأ إن لم توجد بيانات تفصيلية)
            _sales = _sales_result(s, company, grain="month")
            if _sales.get("has_data"):
                result["module_signals"] = {"sales": _sales.get("signals", [])[:6]}
                result["risks"] = result["risks"] + [x for x in _sales.get("signals", []) if x["type"] == "risk"][:3]
                result["opportunities"] = result["opportunities"] + [x for x in _sales.get("signals", []) if x["type"] == "opportunity"][:2]
            try:   # إشارات المخزون 2.6 (لا تُنشأ إن لم توجد بيانات مخزون)
                _inv = _inventory_result(s, company)
                if _inv.get("has_data"):
                    result.setdefault("module_signals", {})["inventory"] = _inv.get("signals", [])[:6]
                    result["risks"] = result["risks"] + [x for x in _inv.get("signals", []) if x["type"] == "risk"][:2]
            except HTTPException:
                pass
            try:   # إشارات الضرائب 3.2 (للمالك/المحاسب)
                if _role == "owner" or can_see_sensitive_financials(_role):
                    _tx = _tax_result(s, company)
                    if _tx.get("has_data"):
                        result.setdefault("module_signals", {})["tax"] = _tx.get("signals", [])[:6]
                        result["risks"] = result["risks"] + [x for x in _tx.get("signals", []) if x["type"] == "risk"][:2]
            except HTTPException:
                pass
            try:   # إشارات التسرب 3.1
                _lk = _leak_result(s, company, _role)
                if _lk.get("has_data"):
                    result.setdefault("module_signals", {})["leakage"] = _lk.get("signals", [])[:6]
                    result["risks"] = result["risks"] + [x for x in _lk.get("signals", []) if x["type"] == "risk"][:2]
            except HTTPException:
                pass
            try:   # إشارات الوحدة المالية 2.11 (للمالك/المحاسب فقط)
                if _role == "owner" or can_see_sensitive_financials(_role):
                    _fn = _fin_result(s, company)
                    if _fn.get("has_data"):
                        result.setdefault("module_signals", {})["finance"] = _fn.get("signals", [])[:6]
                        result["risks"] = result["risks"] + [x for x in _fn.get("signals", []) if x["type"] == "risk"][:2]
            except HTTPException:
                pass
            try:   # إشارات العمليات 2.10
                _op = _ops_result(s, company)
                if _op.get("has_data"):
                    result.setdefault("module_signals", {})["operations"] = _op.get("signals", [])[:6]
                    result["risks"] = result["risks"] + [x for x in _op.get("signals", []) if x["type"] == "risk"][:2]
            except HTTPException:
                pass
            try:   # إشارات الموارد البشرية 2.9 (بلا رواتب)
                _hr = _hr_result(s, company, can_see_pay=False)
                if _hr.get("has_data"):
                    result.setdefault("module_signals", {})["hr"] = _hr.get("signals", [])[:6]
                    result["risks"] = result["risks"] + [x for x in _hr.get("signals", []) if x["type"] == "risk"][:2]
            except HTTPException:
                pass
            try:   # إشارات التدفق النقدي 2.8
                _cf = _cashflow_result(s, company)
                if _cf.get("has_data"):
                    result.setdefault("module_signals", {})["cashflow"] = _cf.get("signals", [])[:6]
                    result["risks"] = result["risks"] + [x for x in _cf.get("signals", []) if x["type"] == "risk"][:2]
            except HTTPException:
                pass
            try:   # مركز المخاطر 3.3 — المؤشر العام + أهم المخاطر (نطاق الدور نفسه)
                if (_role or "") in _RISK_CATS_BY_ROLE:
                    _rk = _risk_result(s, company, _role, save_snapshot=False)
                    if _rk.get("has_data"):
                        result["risk_index"] = {"score": _rk["index"]["score"], "level": _rk["index"]["level"], "level_ar": _rk["index"]["level_ar"],
                                                "confidence_pct": _rk["confidence"]["pct"], "sufficiency_ar": _rk["confidence"]["sufficiency_ar"],
                                                "headline": _rk["brief"]["headline"], "scope_note_ar": _rk["scope"]["note_ar"],
                                                "link": "company-risk-intelligence.html"}
                        result.setdefault("module_signals", {})["risk"] = _rk.get("signals", [])[:6]
                        _de = _load_p24("drivers_engine")
                        if _de is not None:      # 3.4: أهم المسببات للعرض التنفيذي (نفس النتائج المخزنة مؤقتاً)
                            _mods, _cust, _ = _risk_modules(s, company, _role)
                            _dv = _de.analyze_drivers(_rk, _mods, customer_rows=_cust, settings=_risk_settings(s, company.id),
                                                      history=_risk_history(s, company.id), today=datetime.now().date(), sector=_risk_sector(company))
                            if _dv.get("has_data"):
                                result["risk_drivers"] = {**_dv["executive"], "link": "company-risk-drivers.html"}
                            _be = _load_p24("benchmark_engine")
                            if _be is not None:      # 3.5: موقع الشركة مقابل القطاع (فقط حيث يوجد معيار موثّق)
                                _prof = _bench_profile(s, company)
                                _bz = _be.analyze_benchmark(_mods, sales_rows=_cust, risk=_rk, drivers=_dv, datasets=_bench_datasets(s, company),
                                                            peers=_bench_peers(s, company, _prof), profile=_prof, history=_bench_history(s, company.id),
                                                            today=datetime.now().date(), categories=_RISK_CATS_BY_ROLE.get(_role))
                                result["sector_position"] = {**{k: _bz["summary"][k] for k in ("compared", "above", "near", "below", "critical", "benchmark_unavailable", "biggest_gap", "biggest_strength")},
                                                             "link": "company-sector-benchmark.html"}
                                result.setdefault("module_signals", {})["benchmark"] = _bz.get("signals", [])[:6]
                            _pe, _pz = _load_p24("prediction_engine"), None
                            if _pe is not None:      # 3.7: النظرة المستقبلية (نفس البيانات المخزنة مؤقتاً — بلا حفظ)
                                _ps = _pred_settings(s, company)
                                _pz = _pe.analyze_prediction(_mods, sales_rows=_cust, risk=_rk, drivers=_dv, settings={"targets": _ps["targets"], "fy_start": _ps["fy_start"]},
                                                             sector=_risk_sector(company), today=datetime.now().date(), categories=_RISK_CATS_BY_ROLE.get(_role))
                                if _pz.get("status") == "ok":
                                    result["prediction"] = {"outlook": _pz["outlook"], "headline": _pz["brief"]["headline"], "risks": _pz["risks"][:3],
                                                            "link": "company-performance-prediction.html"}
                                    result.setdefault("module_signals", {})["prediction"] = _pz.get("signals", [])[:6]
                            _ge = _load_p24("goals_engine")
                            try:
                              if _ge is not None:      # 3.8: الأهداف والنتائج (نطاق المستخدم نفسه — نفس البيانات)
                                _gr = _goals_rows(s, company.id)
                                if _gr["goals"]:
                                    _gc, _gco, _gv = _goals_scope(s, user)
                                    _gz = _ge.analyze_goals([_goal_dict(g) for g in _gr["goals"]],
                                                            milestones=[{"id": m.id, "goal_id": m.goal_id, "title": m.title, "due": m.due_date, "target": m.target, "actual": m.actual} for m in _gr["ms"]],
                                                            actions=[{"id": a.id, "goal_id": a.goal_id, "action": a.action, "owner": a.owner, "owner_user_id": a.owner_user_id, "due": a.due_date,
                                                                      "expected_impact": a.expected_impact, "actual_impact": a.actual_impact, "status": a.status} for a in _gr["acts"]],
                                                            mods=_mods, sales_rows=_cust, risk=_rk, drivers=_dv, pred=_pz, sector=_risk_sector(company),
                                                            today=datetime.now().date(), viewer=_gv, currency=getattr(company, "currency", None) or "SAR")
                                    result["goals"] = {"counts": _gz["overview"]["counts"], "headline": _gz["overview"]["headline_ar"], "top_risk": _gz["overview"]["top_risk"][:3],
                                                       "link": "company-goals-intelligence.html"}
                                    result.setdefault("module_signals", {})["goals"] = _gz.get("signals", [])[:6]
                            except Exception as _e:      # الأهداف إضافة — فشلها لا يُسقط مركز القيادة
                                _logger.error(f"executive goals block: {type(_e).__name__}: {str(_e)[:200]}")
                            try:      # 3.9: متابعة القرارات — متأخر/بانتظار الاعتماد/المتوقع مقابل الفعلي (نطاق المستخدم)
                                if _load_p24("decisions_engine") is not None:
                                    _dc, _dco, _dv9 = _goals_scope(s, user)
                                    _dz, _ = _dec_result(s, company, _dco, _dv9)
                                    if _dz["decisions"]:
                                        _ov = _dz["overview"]
                                        result["decisions_followup"] = {**{k: _ov[k] for k in ("open", "pending_approval", "overdue", "completed", "measured", "high_impact",
                                                                                            "expected_total", "actual_total", "achievement_pct", "headline_ar")},
                                                                        "escalations": _dz["escalations"][:3], "link": "company-decisions-intelligence.html"}
                                        result.setdefault("module_signals", {})["decisions"] = _dz.get("signals", [])[:6]
                            except Exception as _e:      # فشل المتابعة لا يُسقط مركز القيادة
                                _logger.error(f"executive decisions block: {type(_e).__name__}: {str(_e)[:200]}")
                            try:      # 3.10: آخر حزمة مجلس (بلا إعادة حساب — قراءة السجل فقط)
                                _br = s.exec(select(CompanyBoardReport).where(CompanyBoardReport.company_id == company.id)
                                             .order_by(CompanyBoardReport.generated_at.desc())).first()
                                if _br:
                                    _bs = json.loads(_br.snapshot_json or "{}")
                                    result["board"] = {"last_pack": {"id": _br.id, "period": _br.period, "version": _br.version, "status": _br.status,
                                                                     "generated_at": _br.generated_at.strftime("%Y-%m-%d") if _br.generated_at else ""},
                                                       "lenses": _bs.get("lenses"), "link": "company-board-intelligence.html"}
                            except Exception as _e:
                                _logger.error(f"executive board block: {type(_e).__name__}: {str(_e)[:200]}")
            except HTTPException:
                pass
            try:   # إشارات المشتريات 2.7
                _pur = _purchases_result(s, company)
                if _pur.get("has_data"):
                    result.setdefault("module_signals", {})["purchases"] = _pur.get("signals", [])[:6]
                    result["risks"] = result["risks"] + [x for x in _pur.get("signals", []) if x["type"] == "risk"][:2]
                    result["opportunities"] = result["opportunities"] + [x for x in _pur.get("signals", []) if x["type"] == "opportunity"][:1]
            except HTTPException:
                pass
        except HTTPException:
            pass
        except Exception as _e:
            _logger.error(f"sales signals skipped: {type(_e).__name__}: {str(_e)[:120]}")
        if ai:
            bridge = _load_phase22_bridge()
            if bridge is not None:
                trust = {"overall_score": {"pass": 90, "warning": 65, "fail": 20}[result["data_quality"]["status"]],
                         "status": result["data_quality"]["status"],
                         "has_critical_fail": result["data_quality"]["gate"] == "BLOCK", "main_causes": [
                             {"explanation": w["message_ar"], "fix": w["fix"]} for w in result["data_quality"]["warnings"][:3]]}
                ctx = {"summary": result["summary"], "top_risks": result["risks"][:5],
                       "recommendations": result["recommendations"][:5], "data_quality": result["data_quality"]["status"]}
                from ai_gateway import GeminiProvider, request_ai_analysis
                result["ai_summary"] = request_ai_analysis(
                    GeminiProvider(company_gemini), ctx, "اكتب ملخصاً تنفيذياً مختصراً",
                    trust_report=trust, lang=get_lang(request), company=company)
        return result


@app.post("/company/recommendations/to-decision")
def company_recommendation_to_decision(data: dict, user: User = Depends(get_current_user)):
    """Creates a decision (+ actions) from a signal. Signal is RE-COMPUTED server-side:
    numbers from the client are ignored."""
    signal_id = str(data.get("signal_id") or "")
    with Session(engine) as s:
        company, _role = _exec_scope(s, user, need="edit")
        intel = _build_exec(s, company, data.get("period"))
        sig = next((x for x in intel["risks"] + intel["opportunities"] if x["id"] == signal_id), None)
        rec = next((r for r in intel["recommendations"] if r["signal_id"] == signal_id), None)
        if not sig or not rec:
            raise HTTPException(404, "التوصية غير موجودة أو لم تعد قائمة لهذه الفترة")
        impact = (rec.get("expected_impact") or {}).get("value")
        d = CompanyDecision(
            company_id=company.id, title=str(data.get("title") or rec["problem_ar"])[:200],
            detail=f"{rec['evidence']['detail_ar']} | القاعدة: {rec['evidence']['rule']}"[:1000],
            owner=str(data.get("owner") or "")[:100], due_date=str(data.get("due_date") or "")[:20],
            kpi=sig["metric_id"], status="open", baseline_sales=_company_total_sales(s, company.id),
            expected_impact=(f"{impact} {intel['currency']} (تقديري)" if impact is not None else "غير قابل للتقدير")[:200],
            linked_to=f"signal:{signal_id}", rationale=rec["recommendation_ar"][:500],
            branch_id=sig.get("branch_id"), metric_id=sig["metric_id"], baseline_value=sig["current_value"],
            expected_impact_value=impact, impact_status="expected", source_signal=signal_id,
            updated_at=datetime.now(), problem_type=rec.get("problem_type", sig["code"]),
            decision_type=sig.get("category", ""), outcome_status="pending_measurement",
            created_by=user.name or user.email, data_source="companyentry")
        s.add(d); s.commit(); s.refresh(d)
        created = []
        for a in (data.get("actions") or [{"title": rec["suggested_action"]["title_ar"]}])[:10]:
            act = CompanyAction(company_id=company.id, decision_id=d.id, branch_id=sig.get("branch_id"),
                                title=str(a.get("title") or "")[:200], owner=str(a.get("owner") or d.owner)[:100],
                                priority=rec["priority"], due_date=str(a.get("due_date") or d.due_date)[:20],
                                start_date=datetime.now().strftime("%Y-%m-%d"), updated_at=datetime.now())
            s.add(act); s.commit(); s.refresh(act); created.append(act.id)
        log_audit(company.id, user.id, user.name, "decision_from_recommendation", f"decision:{d.id}",
                  f"signal={signal_id} actions={created}")
        return {"ok": True, "decision_id": d.id, "action_ids": created}


def _action_json(a, today):
    return {"id": a.id, "decision_id": a.decision_id, "branch_id": a.branch_id, "title": a.title,
            "description": a.description, "owner": a.owner, "priority": a.priority, "start_date": a.start_date,
            "due_date": a.due_date, "status": a.status, "progress": a.progress, "notes": a.notes,
            "overdue": bool(a.status not in ACTION_TERMINAL and a.due_date and a.due_date < today),
            "updated_at": a.updated_at.isoformat() if a.updated_at else None}


@app.get("/company/actions")
def company_actions(user: User = Depends(get_current_user), status: str = "", branch_id: Optional[int] = None,
                    owner: str = "", overdue: int = 0, due_from: str = "", due_to: str = "", q: str = "",
                    sort: str = "due_date"):
    with Session(engine) as s:
        if not user.company_id:
            raise HTTPException(403, "لا توجد شركة نشطة")
        company = s.get(Company, user.company_id)
        role = get_user_role(s, user) if company else None
        if not company or not check_permission(role, "decisions", "view"):
            raise HTTPException(403, "غير مصرّح")
        for v in (due_from, due_to):
            if v and not _valid_date(v):
                raise HTTPException(422, "تاريخ غير صالح (YYYY-MM-DD)")
        today = datetime.now().strftime("%Y-%m-%d")
        all_items = s.exec(select(CompanyAction).where(CompanyAction.company_id == company.id)).all()
        decisions = {d.id: d for d in s.exec(select(CompanyDecision).where(CompanyDecision.company_id == company.id)).all()}
        branches = {b.id: b.name for b in s.exec(select(CompanyBranch).where(CompanyBranch.company_id == company.id)).all()}
        rcat = _load_p23_mod("rule_catalog")
        rows = []
        for a in all_items:
            j = _action_json(a, today)
            _pt = decisions.get(a.decision_id).problem_type if decisions.get(a.decision_id) else ""
            j["recommendation_title"] = ({"ar": rcat.localize(_pt, "ar")["title"], "en": rcat.localize(_pt, "en")["title"]}
                                         if (rcat and _pt) else None)
            d = decisions.get(a.decision_id)
            j.update({"branch_name": branches.get(a.branch_id), "created_at": a.created_at.isoformat() if a.created_at else None,
                      "decision_title": d.title if d else None, "recommendation": (d.source_signal if d else "") or None,
                      "problem_type": d.problem_type if d else None, "metric_id": d.metric_id if d else None,
                      "expected_impact": d.expected_impact_value if d else None,
                      "actual_impact": d.actual_impact_value if d else None,
                      "outcome_status": (d.outcome_status or "pending_measurement") if d else None})
            rows.append(j)
        total = len(rows)
        summary = {"total": total,
                   "open": sum(1 for r in rows if r["status"] not in ACTION_TERMINAL),
                   "overdue": sum(1 for r in rows if r["overdue"]),
                   "in_progress": sum(1 for r in rows if r["status"] == "in_progress"),
                   "completed": sum(1 for r in rows if r["status"] == "completed"),
                   "blocked": sum(1 for r in rows if r["status"] == "blocked")}
        live = [r for r in rows if r["status"] != "cancelled"]
        summary["completion_rate"] = round(sum(r["progress"] for r in live) / len(live), 1) if live else None
        if status: rows = [r for r in rows if r["status"] == status]
        if branch_id is not None: rows = [r for r in rows if r["branch_id"] == branch_id]
        if owner: rows = [r for r in rows if r["owner"] == owner]
        if overdue: rows = [r for r in rows if r["overdue"]]
        if due_from: rows = [r for r in rows if r["due_date"] and r["due_date"] >= due_from]
        if due_to: rows = [r for r in rows if r["due_date"] and r["due_date"] <= due_to]
        if q:
            ql = q.lower()
            rows = [r for r in rows if ql in (r["title"] or "").lower() or ql in (r["description"] or "").lower()
                    or ql in (r["decision_title"] or "").lower()]
        if sort == "priority":
            rows.sort(key=lambda r: (r["priority"], r["due_date"] or "9999"))
        else:
            rows.sort(key=lambda r: (r["due_date"] or "9999", r["priority"]))
        return {"actions": rows, "count": len(rows), "summary": summary,
                "owners": sorted({r["owner"] for r in rows if r["owner"]}), "can_edit": check_permission(role, "decisions", "edit")}


@app.post("/company/actions/update")
def company_action_update(data: dict, user: User = Depends(get_current_user)):
    with Session(engine) as s:
        company, _role = _exec_scope(s, user, need="edit")
        a = s.get(CompanyAction, int(data.get("id") or 0))
        if not a or a.company_id != company.id:
            raise HTTPException(404, "المهمة غير موجودة")
        if a.status in ACTION_TERMINAL:
            raise HTTPException(409, "المهمة مغلقة ولا يمكن تعديلها")
        new_status = str(data.get("status") or a.status)
        if new_status not in ACTION_STATUSES:
            raise HTTPException(400, "حالة غير صالحة")
        progress = data.get("progress", a.progress)
        try:
            progress = int(progress)
        except (TypeError, ValueError):
            raise HTTPException(400, "نسبة إنجاز غير صالحة")
        if not 0 <= progress <= 100:
            raise HTTPException(400, "نسبة الإنجاز يجب أن تكون بين 0 و100")
        if new_status == "completed":
            progress = 100
        if "due_date" in data:
            due = str(data.get("due_date") or "")
            if due and not _valid_date(due):
                raise HTTPException(422, "تاريخ الاستحقاق غير صالح (YYYY-MM-DD)")
            a.due_date = due
        if "owner" in data:
            a.owner = str(data.get("owner") or "")[:100]
        before = f"{a.status}/{a.progress}"
        a.status, a.progress = new_status, progress
        note = str(data.get("note") or data.get("notes") or "").strip()
        if note:
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            a.notes = ((a.notes + "\n") if a.notes else "") + f"[{stamp} · {user.name}] {note[:500]}"
        a.updated_at = datetime.now()
        s.add(a); s.commit()
        log_audit(company.id, user.id, user.name, "action_update", f"action:{a.id}",
                  f"{before} -> {a.status}/{a.progress}" + (f" note" if note else ""))
        return {"ok": True, "action": _action_json(a, datetime.now().strftime("%Y-%m-%d"))}


@app.post("/company/decisions/measure")
def company_decision_measure(data: dict, user: User = Depends(get_current_user)):
    """Measures a decision's KPI in the latest month after it. 'verified' = reliable measurement
    (complete month, all branches in scope), never a causal claim."""
    with Session(engine) as s:
        company, _role = _exec_scope(s, user, need="edit")
        d = s.get(CompanyDecision, int(data.get("decision_id") or 0))
        if not d or d.company_id != company.id:
            raise HTTPException(404, "القرار غير موجود")
        dm, pa = _load_p23_mod("decision_memory"), _load_p23_mod("period_aggregation")
        if dm is None or pa is None:
            raise HTTPException(503, "المحرّك غير متاح — " + _p23_diagnostic())
        branches = s.exec(select(CompanyBranch).where(CompanyBranch.company_id == company.id,
                                                      CompanyBranch.is_active == 1)).all()
        scope = [d.branch_id] if d.branch_id else [b.id for b in branches]
        rows = []
        if scope:
            rows = [_row_dict(e) for e in s.exec(select(CompanyEntry).where(
                CompanyEntry.company_id == company.id, CompanyEntry.branch_id.in_(scope))).all()]
        dec_month = d.created_at.strftime("%Y-%m") if d.created_at else None
        months = sorted({pa.format_period(pa.parse_period(r["period"])) for r in rows if pa.parse_period(r["period"])})
        later = [m for m in months if dec_month and m > dec_month]
        measured = later[-1] if later else None
        split = pa.split_by_period(rows, current_period=measured) if measured else {}
        out = dm.evaluate_measurement(d, split, scope, dec_month, measured)
        d.outcome_status = out["outcome_status"]
        d.measurement_period = out.get("measurement_period") or ""
        d.data_source = out["data_source"]
        if out.get("actual_value") is not None:
            d.actual_value, d.actual_impact_value = out["actual_value"], out["actual_change"]
        d.impact_status = {"verified": "verified", "measured": "measured"}.get(out["outcome_status"], "insufficient_data")
        if "notes" in data:
            d.outcome_notes = str(data.get("notes") or "")[:1000]
        d.updated_at = datetime.now()
        s.add(d); s.commit()
        log_audit(company.id, user.id, user.name, "decision_measure", f"decision:{d.id}",
                  f"status={d.outcome_status} period={d.measurement_period} actual={d.actual_value}")
        return {"ok": True, **out, "impact_status": d.impact_status,
                "baseline_value": d.baseline_value, "expected_impact_value": d.expected_impact_value}


def _decisions_scope(s, user):
    """Decision memory follows the existing RBAC resource 'decisions' (owner, manager: view)."""
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    company = s.get(Company, user.company_id)
    if not company or not check_permission(get_user_role(s, user), "decisions", "view"):
        raise HTTPException(403, "غير مصرّح")
    if company.is_active != 1:
        raise HTTPException(402, "شركتك قيد التفعيل")
    return company


def _decision_json(d):
    return {"id": d.id, "title": d.title, "metric_id": d.metric_id, "branch_id": d.branch_id,
            "problem_type": d.problem_type, "decision_type": d.decision_type, "status": d.status,
            "baseline_value": d.baseline_value, "expected_impact_value": d.expected_impact_value,
            "actual_value": d.actual_value, "actual_impact_value": d.actual_impact_value,
            "outcome_status": d.outcome_status or "pending_measurement", "measurement_period": d.measurement_period,
            "outcome_notes": d.outcome_notes, "created_by": d.created_by, "source_signal": d.source_signal,
            "created_at": d.created_at.isoformat() if d.created_at else None}


@app.get("/company/decisions/{decision_id}/memory")
def company_decision_memory_one(decision_id: int, user: User = Depends(get_current_user)):
    with Session(engine) as s:
        company = _decisions_scope(s, user)
        d = s.get(CompanyDecision, decision_id)
        if not d or d.company_id != company.id:
            raise HTTPException(404, "القرار غير موجود")
        dm = _load_p23_mod("decision_memory")
        if dm is None:
            raise HTTPException(503, "المحرّك غير متاح — " + _p23_diagnostic())
        others = s.exec(select(CompanyDecision).where(CompanyDecision.company_id == company.id)).all()
        return {"decision": _decision_json(d), "outcome": dm.summarize(d),
                "similar": dm.find_similar(d, others)}


@app.get("/company/decision-memory")
def company_decision_memory(user: User = Depends(get_current_user), metric_id: str = "",
                            problem_type: str = "", branch_id: Optional[int] = None):
    """Past decisions related to a problem the user is looking at now (same company only)."""
    if not (metric_id or problem_type):
        raise HTTPException(400, "حدّد المؤشر أو نوع المشكلة")
    with Session(engine) as s:
        company = _decisions_scope(s, user)
        dm = _load_p23_mod("decision_memory")
        if dm is None:
            raise HTTPException(503, "المحرّك غير متاح — " + _p23_diagnostic())
        others = s.exec(select(CompanyDecision).where(CompanyDecision.company_id == company.id)).all()
        target = {"id": None, "metric_id": metric_id, "problem_type": problem_type, "branch_id": branch_id}
        return dm.find_similar(target, others)


# ── Saved what-if scenarios ────────────────────────────────
def _scenario_json(sc):
    ld = lambda t: json.loads(t or "{}")
    return {"id": sc.id, "name": sc.name, "description": sc.description, "scenario_type": sc.scenario_type,
            "branch_id": sc.branch_id, "base_period": sc.base_period, "status": sc.status,
            "assumptions": ld(sc.assumptions), "baseline_values": ld(sc.baseline_values),
            "scenario_values": ld(sc.scenario_values), "results": ld(sc.results), "created_by": sc.created_by,
            "created_at": sc.created_at.isoformat() if sc.created_at else None,
            "updated_at": sc.updated_at.isoformat() if sc.updated_at else None,
            "ran_at": sc.ran_at.isoformat() if sc.ran_at else None, "is_estimate": True}


def _scenario_scope(s, user, need="view"):
    _ensure_data_tables()
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    company = s.get(Company, user.company_id)
    role = get_user_role(s, user)
    if not company or not (role == "owner" or check_permission(role, "finance", "view")):
        raise HTTPException(403, "غير مصرّح")
    if company.is_active != 1:
        raise HTTPException(402, "شركتك قيد التفعيل")
    if need == "edit" and not (role == "owner" or check_permission(role, "finance", "edit")):
        raise HTTPException(403, "غير مصرّح — تعديل السيناريوهات للمالك والمحاسب")
    if need == "delete" and role != "owner":
        raise HTTPException(403, "غير مصرّح — الحذف للمالك فقط")
    return company


def _scenario_run(s, company, clean):
    se, pa = _load_p23_mod("scenario_engine"), _load_p23_mod("period_aggregation")
    if se is None or pa is None:
        raise HTTPException(503, "محرّك السيناريوهات غير متاح — " + _p23_diagnostic())
    branches = s.exec(select(CompanyBranch).where(CompanyBranch.company_id == company.id,
                                                  CompanyBranch.is_active == 1)).all()
    ids = [b.id for b in branches]
    if clean.get("branch_id") is not None and clean["branch_id"] not in ids:
        raise HTTPException(404, "الفرع غير موجود")
    rows = []
    if ids:
        rows = [_row_dict(e) for e in s.exec(select(CompanyEntry).where(
            CompanyEntry.company_id == company.id, CompanyEntry.branch_id.in_(ids))).all()]
    split = pa.split_by_period(rows, current_period=clean.get("base_period"))
    breakdown = {}
    fin = s.exec(select(CompanyModuleEntry).where(CompanyModuleEntry.company_id == company.id,
                                                  CompanyModuleEntry.module == "finance")
                 .order_by(CompanyModuleEntry.created_at.desc())).first()
    if fin and fin.data:
        try:
            for k, v in json.loads(fin.data).items():
                for key, words in (("cogs", ("تكلفة البضاعة", "cogs")), ("payroll", ("رواتب", "payroll")),
                                   ("rent", ("إيجار", "rent")), ("marketing", ("تسويق", "marketing"))):
                    if any(w in str(k).lower() for w in words):
                        breakdown[key] = v
        except Exception:
            breakdown = {}
    base = se.build_baseline(split["current_entries"], clean.get("branch_id"), breakdown, split["current_period"])
    return se.run(base, clean["assumptions"]), split["current_period"]


def _scenario_validate(data):
    se = _load_p23_mod("scenario_engine")
    if se is None:
        raise HTTPException(503, "محرّك السيناريوهات غير متاح — " + _p23_diagnostic())
    try:
        return se.validate(data)
    except se.ScenarioError as e:
        raise HTTPException(422, {"field": e.field, "message_ar": e.message_ar, "message_en": e.message_en})


def _scenario_store(sc, result, period):
    sc.base_period = period or ""
    sc.baseline_values = json.dumps(result.get("baseline_values", {}), ensure_ascii=False)
    sc.scenario_values = json.dumps(result.get("comparison", []), ensure_ascii=False)
    sc.results = json.dumps(result, ensure_ascii=False, default=str)
    sc.status = "ran" if result.get("status") == "ok" else "draft"
    sc.ran_at = datetime.now(); sc.updated_at = datetime.now()


@app.get("/company/scenarios")
def company_scenarios_list(user: User = Depends(get_current_user)):
    with Session(engine) as s:
        company = _scenario_scope(s, user)
        items = s.exec(select(CompanyScenario).where(CompanyScenario.company_id == company.id,
                                                     CompanyScenario.status != "archived")
                       .order_by(CompanyScenario.updated_at.desc())).all()
        branches = s.exec(select(CompanyBranch).where(CompanyBranch.company_id == company.id,
                                                      CompanyBranch.is_active == 1)).all()
        return {"scenarios": [_scenario_json(x) for x in items], "count": len(items),
                "branches": [{"id": x.id, "name": x.name} for x in branches]}


@app.post("/company/scenarios")
def company_scenarios_create(data: dict, user: User = Depends(get_current_user)):
    with Session(engine) as s:
        company = _scenario_scope(s, user, "edit")
        clean = _scenario_validate(data)
        result, period = _scenario_run(s, company, clean)
        sc = CompanyScenario(company_id=company.id, branch_id=clean["branch_id"], created_by=user.name or user.email,
                             name=clean["name"], description=clean["description"], scenario_type=clean["scenario_type"],
                             assumptions=json.dumps(clean["assumptions"]))
        _scenario_store(sc, result, period)
        s.add(sc); s.commit(); s.refresh(sc)
        log_audit(company.id, user.id, user.name, "scenario_create", f"scenario:{sc.id}", clean["name"])
        return _scenario_json(sc)


def _own_scenario(s, company, sid):
    sc = s.get(CompanyScenario, sid)
    if not sc or sc.company_id != company.id or sc.status == "archived":
        raise HTTPException(404, "السيناريو غير موجود")
    return sc


@app.get("/company/scenarios/{sid}")
def company_scenarios_get(sid: int, user: User = Depends(get_current_user)):
    with Session(engine) as s:
        return _scenario_json(_own_scenario(s, _scenario_scope(s, user), sid))


@app.put("/company/scenarios/{sid}")
def company_scenarios_update(sid: int, data: dict, user: User = Depends(get_current_user)):
    with Session(engine) as s:
        company = _scenario_scope(s, user, "edit")
        sc = _own_scenario(s, company, sid)
        clean = _scenario_validate(data)
        result, period = _scenario_run(s, company, clean)
        sc.name, sc.description, sc.scenario_type = clean["name"], clean["description"], clean["scenario_type"]
        sc.branch_id, sc.assumptions = clean["branch_id"], json.dumps(clean["assumptions"])
        _scenario_store(sc, result, period)
        s.add(sc); s.commit(); s.refresh(sc)
        log_audit(company.id, user.id, user.name, "scenario_update", f"scenario:{sc.id}", clean["name"])
        return _scenario_json(sc)


@app.post("/company/scenarios/{sid}/run")
def company_scenarios_run(sid: int, user: User = Depends(get_current_user)):
    """Re-runs against the CURRENT baseline (server-side data only)."""
    with Session(engine) as s:
        company = _scenario_scope(s, user, "edit")
        sc = _own_scenario(s, company, sid)
        clean = {"assumptions": json.loads(sc.assumptions or "{}"), "branch_id": sc.branch_id, "base_period": None}
        result, period = _scenario_run(s, company, clean)
        _scenario_store(sc, result, period)
        s.add(sc); s.commit(); s.refresh(sc)
        return _scenario_json(sc)


@app.get("/company/scenarios/{sid}/compare")
def company_scenarios_compare(sid: int, user: User = Depends(get_current_user)):
    with Session(engine) as s:
        sc = _own_scenario(s, _scenario_scope(s, user), sid)
        r = json.loads(sc.results or "{}")
        return {"id": sc.id, "name": sc.name, "base_period": sc.base_period, "is_estimate": True,
                "status": r.get("status"), "comparison": r.get("comparison", []), "warnings": r.get("warnings", []),
                "revenue_impact": r.get("revenue_impact"), "profit_impact": r.get("profit_impact"),
                "margin_impact_pts": r.get("margin_impact_pts")}


@app.delete("/company/scenarios/{sid}")
def company_scenarios_delete(sid: int, user: User = Depends(get_current_user)):
    """Soft delete (archived) — history is kept for audit."""
    with Session(engine) as s:
        company = _scenario_scope(s, user, "delete")
        sc = _own_scenario(s, company, sid)
        sc.status, sc.updated_at = "archived", datetime.now()
        s.add(sc); s.commit()
        log_audit(company.id, user.id, user.name, "scenario_archive", f"scenario:{sid}", sc.name)
        return {"ok": True}


# ── Action Center ───────────────────────────────────────────
@app.post("/company/actions")
def company_action_create(data: dict, user: User = Depends(get_current_user)):
    with Session(engine) as s:
        company, _ = _exec_scope(s, user, need="edit")
        d = s.get(CompanyDecision, int(data.get("decision_id") or 0))
        if not d or d.company_id != company.id:
            raise HTTPException(404, "القرار غير موجود")
        title = str(data.get("title") or "").strip()
        if not title:
            raise HTTPException(422, "عنوان الإجراء مطلوب")
        due = str(data.get("due_date") or "")
        if due and not _valid_date(due):
            raise HTTPException(422, "تاريخ الاستحقاق غير صالح (YYYY-MM-DD)")
        pr = data.get("priority") or "P2"
        if pr not in ("P1", "P2", "P3"):
            raise HTTPException(422, "أولوية غير صالحة")
        a = CompanyAction(company_id=company.id, decision_id=d.id, branch_id=d.branch_id, title=title[:200],
                          description=str(data.get("description") or "")[:1000], owner=str(data.get("owner") or "")[:100],
                          priority=pr, due_date=due, start_date=datetime.now().strftime("%Y-%m-%d"), updated_at=datetime.now())
        s.add(a); s.commit(); s.refresh(a)
        log_audit(company.id, user.id, user.name, "action_create", f"action:{a.id}", f"decision={d.id}")
        return {"ok": True, "action": _action_json(a, datetime.now().strftime("%Y-%m-%d"))}


def _valid_date(v):
    try:
        datetime.strptime(v, "%Y-%m-%d"); return True
    except ValueError:
        return False


@app.get("/company/actions/{aid}/history")
def company_action_history(aid: int, user: User = Depends(get_current_user)):
    with Session(engine) as s:
        if not user.company_id:
            raise HTTPException(403, "لا توجد شركة نشطة")
        company = s.get(Company, user.company_id)
        if not company or not check_permission(get_user_role(s, user), "decisions", "view"):
            raise HTTPException(403, "غير مصرّح")
        a = s.get(CompanyAction, aid)
        if not a or a.company_id != company.id:
            raise HTTPException(404, "المهمة غير موجودة")
        logs = s.exec(select(AuditLog).where(AuditLog.company_id == company.id, AuditLog.target == f"action:{aid}")
                      .order_by(AuditLog.created_at.desc())).all()
        return {"history": [{"action": l.action, "by": l.user_name, "details": l.details,
                             "at": l.created_at.isoformat() if l.created_at else None} for l in logs]}


@app.get("/company/financial-overview")
def company_financial_overview(user: User = Depends(get_current_user)):
    """الوحدة المالية الشاملة (P3 — رؤية أحمد): P&L + النِسب المالية.
    محمي للمالك والمحاسب. كل رقم من البيانات الفعلية، مع has_data."""
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    with Session(engine) as s:
        company = s.get(Company, user.company_id)
        if not company or not check_permission(get_user_role(s, user), "finance", "view"):
            raise HTTPException(403, "غير مصرّح — الوحدة المالية للمالك والمحاسب فقط")
        if company.is_active != 1:
            raise HTTPException(402, "شركتك قيد التفعيل")

        branches = s.exec(
            select(CompanyBranch).where(CompanyBranch.company_id == company.id, CompanyBranch.is_active == 1)
        ).all()
        total_sales = total_expenses = total_profit = 0.0
        # نجمع بيانات مالية موسّعة إن وُجدت (من وحدة المالية)
        cogs = payroll = marketing = rent = other_exp = 0.0
        has_breakdown = False
        for b in branches:
            e = s.exec(select(CompanyEntry).where(CompanyEntry.branch_id == b.id).order_by(CompanyEntry.created_at.desc())).first()
            if e:
                total_sales += e.sales or 0
                total_expenses += e.expenses or 0
                total_profit += e.profit or 0

        # نحاول جلب تفصيل المصروفات من وحدة المالية
        fin_entry = s.exec(
            select(CompanyModuleEntry).where(
                CompanyModuleEntry.company_id == company.id,
                CompanyModuleEntry.module == "finance",
            ).order_by(CompanyModuleEntry.created_at.desc())
        ).first()
        if fin_entry and fin_entry.data:
            try:
                fd = json.loads(fin_entry.data)
                def pick(*kw):
                    for k, v in fd.items():
                        if any(w in str(k).lower() or w in str(k) for w in kw):
                            try: return float(str(v).replace(",", "").replace("%", "").strip())
                            except: pass
                    return 0
                cogs = pick("تكلفة البضاعة", "cogs", "تكلفة المبيعات")
                payroll = pick("رواتب", "salary", "payroll", "أجور")
                marketing = pick("تسويق", "marketing", "إعلان")
                rent = pick("إيجار", "rent")
                if cogs or payroll or marketing or rent:
                    has_breakdown = True
                    other_exp = max(total_expenses - cogs - payroll - marketing - rent, 0)
            except Exception:
                pass

        # P&L
        gross_profit = (total_sales - cogs) if has_breakdown else None
        gross_margin = round(gross_profit / total_sales * 100, 1) if (gross_profit is not None and total_sales > 0) else None
        net_margin = round(total_profit / total_sales * 100, 1) if total_sales > 0 else 0
        # EBITDA تقريبي (صافي + استهلاك مقدّر — نعرضه فقط لو فيه تفصيل)
        ebitda = round(total_profit + (rent * 0.1)) if has_breakdown else None

        # النِسب المالية (نعرض فقط المتوفّرة)
        ratios = []
        ratios.append({"name": "هامش صافي الربح", "value": net_margin, "unit": "%",
                       "formula": "صافي الربح ÷ الإيرادات", "has_data": total_sales > 0,
                       "status": "good" if net_margin >= 15 else ("warn" if net_margin >= 8 else "bad")})
        if gross_margin is not None:
            ratios.append({"name": "هامش الربح الإجمالي", "value": gross_margin, "unit": "%",
                           "formula": "(الإيرادات − تكلفة البضاعة) ÷ الإيرادات", "has_data": True,
                           "status": "good" if gross_margin >= 40 else ("warn" if gross_margin >= 25 else "bad")})
        exp_ratio = round(total_expenses / total_sales * 100, 1) if total_sales > 0 else 0
        ratios.append({"name": "نسبة المصروفات التشغيلية", "value": exp_ratio, "unit": "%",
                       "formula": "المصروفات ÷ الإيرادات", "has_data": total_sales > 0,
                       "status": "good" if exp_ratio < 70 else ("warn" if exp_ratio < 85 else "bad")})
        if has_breakdown and total_sales > 0:
            ratios.append({"name": "نسبة تكلفة البضاعة (COGS)", "value": round(cogs / total_sales * 100, 1),
                           "unit": "%", "formula": "تكلفة البضاعة ÷ الإيرادات", "has_data": True, "status": None})
            ratios.append({"name": "نسبة الرواتب", "value": round(payroll / total_sales * 100, 1),
                           "unit": "%", "formula": "الرواتب ÷ الإيرادات", "has_data": True, "status": None})

        # جودة البيانات
        _q, _ = check_data_quality_rules([s.exec(select(CompanyEntry).where(CompanyEntry.branch_id == b.id).order_by(CompanyEntry.created_at.desc())).first() for b in branches if s.exec(select(CompanyEntry).where(CompanyEntry.branch_id == b.id)).first()])
        confidence = compute_confidence_flag(_q)

        # ===== الميزانية العمومية + الذمم (AR/AP) — من وحدة المالية =====
        def _fpick(fd, *kw):
            for k, v in (fd or {}).items():
                if any(w in str(k).lower() or w in str(k) for w in kw):
                    try: return float(str(v).replace(",", "").replace("%", "").strip())
                    except: pass
            return None
        fd = {}
        if fin_entry and fin_entry.data:
            try: fd = json.loads(fin_entry.data)
            except: fd = {}
        # الميزانية
        assets = _fpick(fd, "أصول", "assets", "موجودات")
        liabilities = _fpick(fd, "التزامات", "خصوم", "liabilities")
        equity = None
        if assets is not None and liabilities is not None:
            equity = assets - liabilities
        cash_reserve = company.cash_reserve or 0
        balance_sheet = {
            "has_data": assets is not None or liabilities is not None,
            "assets": round(assets) if assets is not None else None,
            "liabilities": round(liabilities) if liabilities is not None else None,
            "equity": round(equity) if equity is not None else None,
            "cash": round(cash_reserve) if cash_reserve else None,
        }
        # الذمم المدينة (AR) والدائنة (AP)
        ar = _fpick(fd, "ذمم مدينة", "مستحقات", "receivable", "تحصيل")
        ap = _fpick(fd, "ذمم دائنة", "مستحقات دائنة", "payable", "موردين")
        dso = None
        if ar is not None and total_sales > 0:
            dso = round(ar / total_sales * 30)  # أيام التحصيل التقريبية (شهري)
        receivables = {
            "has_data": ar is not None or ap is not None,
            "ar": round(ar) if ar is not None else None,
            "ap": round(ap) if ap is not None else None,
            "dso": dso,
            "net_position": (round(ar - ap) if (ar is not None and ap is not None) else None),
        }

        # ===== قائمة التدفقات النقدية (Cash Flow Statement) — P0 من التقرير =====
        # مبسّطة: التشغيلي (الربح) + تغيّر الذمم، الاستثماري، التمويلي
        operating_cf = round(total_profit)  # التدفق التشغيلي ≈ صافي الربح
        ar_change = -(ar if ar is not None else 0) * 0.1  # تقدير محافظ لتغيّر الذمم
        net_cash_flow = round(operating_cf + ar_change)
        cash_flow = {
            "has_data": total_sales > 0,
            "operating": operating_cf,
            "investing": None,   # يحتاج بيانات استثمار
            "financing": None,   # يحتاج بيانات تمويل
            "net_change": net_cash_flow,
            "opening_cash": round(cash_reserve) if cash_reserve else None,
            "closing_cash": round(cash_reserve + net_cash_flow) if cash_reserve else None,
        }

        # ===== الموازنة مقابل الفعلي (Budget vs Actual) — P0 من التقرير =====
        budget_data = _fpick(fd, "الموازنة", "الميزانية التقديرية", "budget", "المخطّط")
        budget_variance = None
        if budget_data is not None and budget_data > 0:
            variance = total_sales - budget_data
            variance_pct = round(variance / budget_data * 100, 1)
            budget_variance = {
                "has_data": True, "budget": round(budget_data), "actual": round(total_sales),
                "variance": round(variance), "variance_pct": variance_pct,
                "status": "above" if variance >= 0 else "below",
            }
        else:
            budget_variance = {"has_data": False}

        # ===== التنبؤ المالي (Financial Forecast) — من اتجاه البيانات =====
        # نجمع آخر فترتين لكل فرع لحساب الاتجاه
        forecast = {"has_data": False}
        growths_f = [e.growth for _, e in [(b, s.exec(select(CompanyEntry).where(CompanyEntry.branch_id == b.id).order_by(CompanyEntry.created_at.desc())).first()) for b in branches] if e and e.growth is not None]
        if growths_f:
            avg_g = sum(growths_f) / len(growths_f)
            next_revenue = round(total_sales * (1 + avg_g / 100))
            next_profit = round(total_profit * (1 + avg_g / 100))
            forecast = {
                "has_data": True, "trend_pct": round(avg_g, 1),
                "next_revenue": next_revenue, "next_profit": next_profit,
                "note": "توقّع الفترة القادمة مبني على اتجاه نموّك الحالي.",
            }

        return {
            "company": {"name": company.name},
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "currency": company.currency or "SAR",
            "pnl": {
                "revenue": round(total_sales),
                "cogs": round(cogs) if has_breakdown else None,
                "gross_profit": round(gross_profit) if gross_profit is not None else None,
                "gross_margin": gross_margin,
                "operating_expenses": round(total_expenses),
                "net_profit": round(total_profit),
                "net_margin": net_margin,
                "ebitda": ebitda,
                "has_breakdown": has_breakdown,
                "expense_breakdown": ({"cogs": round(cogs), "payroll": round(payroll),
                                       "marketing": round(marketing), "rent": round(rent),
                                       "other": round(other_exp)} if has_breakdown else None),
            },
            "balance_sheet": balance_sheet,
            "receivables": receivables,
            "cash_flow": cash_flow,
            "budget_variance": budget_variance,
            "forecast": forecast,
            "ratios": ratios,
            "confidence": confidence,
        }


@app.get("/company/consolidated")
def company_consolidated(user: User = Depends(get_current_user)):
    """النظرة المجمّعة (Consolidated — رؤية أحمد): تجمّع الأداء حسب وحدة الأعمال.
    للشركات متعددة الأنشطة. النِسب تُحسب صحيحاً (إجمالي/إجمالي، لا متوسط نِسب)."""
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    with Session(engine) as s:
        company = s.get(Company, user.company_id)
        if not company or not check_permission(get_user_role(s, user), "dashboard", "view"):
            raise HTTPException(403, "غير مصرّح")
        if company.is_active != 1:
            raise HTTPException(402, "شركتك قيد التفعيل")

        branches = s.exec(
            select(CompanyBranch).where(CompanyBranch.company_id == company.id, CompanyBranch.is_active == 1)
        ).all()

        # نجمّع حسب وحدة الأعمال (business_unit). الفروع بلا وحدة → "عام"
        units = {}
        grand_sales = grand_expenses = grand_profit = 0.0
        for b in branches:
            e = s.exec(
                select(CompanyEntry).where(CompanyEntry.branch_id == b.id).order_by(CompanyEntry.created_at.desc())
            ).first()
            if not e:
                continue
            unit = b.business_unit.strip() or "النشاط الرئيسي"
            if unit not in units:
                units[unit] = {"name": unit, "sales": 0.0, "expenses": 0.0, "profit": 0.0,
                               "branches": 0, "scores": []}
            u = units[unit]
            u["sales"] += e.sales or 0
            u["expenses"] += e.expenses or 0
            u["profit"] += e.profit or 0
            u["branches"] += 1
            if e.branch_score:
                u["scores"].append(e.branch_score)
            grand_sales += e.sales or 0
            grand_expenses += e.expenses or 0
            grand_profit += e.profit or 0

        # نحسب النِسب صحيحاً لكل وحدة (إجمالي الربح ÷ إجمالي المبيعات)
        unit_list = []
        for u in units.values():
            margin = round((u["profit"] / u["sales"]) * 100, 1) if u["sales"] > 0 else 0
            score = round(sum(u["scores"]) / len(u["scores"])) if u["scores"] else 0
            unit_list.append({
                "name": u["name"], "sales": round(u["sales"]), "expenses": round(u["expenses"]),
                "profit": round(u["profit"]), "margin": margin, "branches": u["branches"], "score": score,
            })
        unit_list.sort(key=lambda x: x["sales"], reverse=True)

        # النِسبة المجمّعة الصحيحة (إجمالي/إجمالي — لا متوسط النِسب)
        consolidated_margin = round((grand_profit / grand_sales) * 100, 1) if grand_sales > 0 else 0
        is_multi_unit = len(units) > 1

        return {
            "company": {"name": company.name},
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "is_multi_unit": is_multi_unit,
            "consolidated": {
                "total_sales": round(grand_sales), "total_expenses": round(grand_expenses),
                "total_profit": round(grand_profit), "margin": consolidated_margin,
                "units_count": len(units), "branches_count": len(branches),
            },
            "units": unit_list,
        }


@app.get("/company/executive-report")
def company_executive_report(user: User = Depends(get_current_user)):
    """التقرير التنفيذي الذكي (رؤية V2): يجمّع كل التحليلات في تقرير واحد
    بالطبقات: لقطة → مؤشرات → أداء → أسباب → أثر → توقّع → توصية → قرار.
    لا يعيد الحساب — يجمّع من الدوال الموجودة (صحة، ركائز، مخاطر)."""
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    with Session(engine) as s:
        company = s.get(Company, user.company_id)
        if not company or not check_permission(get_user_role(s, user), "reports", "view"):
            raise HTTPException(403, "غير مصرّح — التقرير للمالك والمحاسب فقط")
        if company.is_active != 1:
            raise HTTPException(402, "شركتك قيد التفعيل")

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
        total_expenses = sum(e.expenses for _, e in rows)
        total_profit = sum(e.profit for _, e in rows)
        total_customers = sum(e.customers for _, e in rows)
        avg_margin = round((total_profit / total_sales) * 100, 1) if total_sales else 0
        overall = round(sum(e.branch_score for _, e in rows) / n)
        ranked = sorted(rows, key=lambda x: x[1].branch_score, reverse=True)
        best, worst = ranked[0], ranked[-1]

        # ① لقطة (Snapshot)
        level_word = "مستقرة" if overall >= 60 else ("تحتاج انتباه" if overall >= 40 else "حرجة")
        snapshot = {
            "health": overall, "status": level_word,
            "sales": round(total_sales), "profit": round(total_profit),
            "margin": avg_margin, "customers": total_customers, "branches": n,
        }

        # ② الأداء والاتجاه (نستخدم النمو من الفروع)
        growths = [e.growth for _, e in rows if e.growth is not None]
        avg_growth = round(sum(growths) / len(growths), 1) if growths else 0

        # ③ الملخص التنفيذي (نصّي — من الأرقام)
        summary_parts = []
        summary_parts.append(f"بلغت المبيعات {round(total_sales):,} ريال بهامش ربح {avg_margin}%.")
        if avg_growth > 0:
            summary_parts.append(f"نمو إيجابي بمعدّل {avg_growth}%.")
        elif avg_growth < 0:
            summary_parts.append(f"تراجع في النمو بمعدّل {avg_growth}% يحتاج معالجة.")
        summary_parts.append(f"الفرع الأقوى: {best[0].name} ({best[1].branch_score})، والأضعف: {worst[0].name} ({worst[1].branch_score}).")

        # ④ المحرّكات (drivers) — إيجابية وسلبية
        drivers_pos, drivers_neg = [], []
        for b, e in rows:
            if e.growth and e.growth > 5:
                drivers_pos.append({"name": b.name, "value": f"+{e.growth}% نمو"})
            if e.margin and e.margin < 10:
                drivers_neg.append({"name": b.name, "value": f"هامش {e.margin}% منخفض"})

        # ⑤ الأثر المالي المقدّر
        impact = None
        if worst[1].margin and worst[1].margin < avg_margin:
            gap = round((avg_margin - worst[1].margin) / 100 * worst[1].sales)
            impact = {"desc": f"لو وصل {worst[0].name} لمتوسط الهامش", "value": gap, "unit": "ريال"}

        # ⑥ القرارات الموصى بها
        decisions = []
        if worst[1].branch_score < 45:
            decisions.append({"title": f"خطة إنقاذ لفرع {worst[0].name}",
                             "reason": f"أداؤه {worst[1].branch_score} — الأدنى", "priority": "عالية"})
        if avg_margin < 15:
            decisions.append({"title": "مراجعة التكاليف لرفع الهامش",
                             "reason": f"الهامش {avg_margin}% دون الصحّي", "priority": "عالية"})
        if drivers_pos:
            decisions.append({"title": f"تعميم نجاح {drivers_pos[0]['name']}",
                             "reason": "أداء نمو ممتاز يستحق التعميم", "priority": "متوسطة"})

        # ⑦ جودة البيانات (confidence)
        _q, _ = check_data_quality_rules([e for _, e in rows])
        confidence = compute_confidence_flag(_q)

        return {
            "company": {"name": company.name, "sector": SECTOR_NAMES.get(company.sector, company.sector)},
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "period": "الفترة الحالية",
            "snapshot": snapshot,
            "growth": avg_growth,
            "executive_summary": " ".join(summary_parts),
            "drivers": {"positive": drivers_pos[:3], "negative": drivers_neg[:3]},
            "impact": impact,
            "branches": [{
                "name": b.name, "city": b.city, "sales": round(e.sales), "margin": e.margin,
                "score": e.branch_score, "growth": e.growth, "level": score_level(e.branch_score)[0],
            } for b, e in ranked],
            "best_branch": {"name": best[0].name, "score": best[1].branch_score},
            "worst_branch": {"name": worst[0].name, "score": worst[1].branch_score},
            "decisions": decisions,
            "confidence": confidence,
        }


@app.get("/company/report")
def company_report(user: User = Depends(get_current_user)):
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    with Session(engine) as s:
        company = s.get(Company, user.company_id)
        if not company or not check_permission(get_user_role(s, user), "reports", "view"):
            raise HTTPException(403, "غير مصرّح — التقرير المالي للمالك والمحاسب فقط")
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


@app.get("/company/treasury")
def company_treasury(user: User = Depends(get_current_user)):
    """الخزينة والسيولة (Treasury Intelligence) — تعميق من التقرير:
    Cash runway · توقّع 13 أسبوع · AR/AP aging · جدول الديون · الالتزامات القادمة."""
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    with Session(engine) as s:
        company = s.get(Company, user.company_id)
        if not company or not check_permission(get_user_role(s, user), "cashflow", "view"):
            raise HTTPException(403, "غير مصرّح — الخزينة للمالك والمحاسب فقط")
        if company.is_active != 1:
            raise HTTPException(402, "شركتك قيد التفعيل")

        cash = company.cash_reserve or 0
        obligations = company.monthly_obligations or 0
        est = company_monthly_estimate(s, company.id)
        monthly_net = round(est["profit"] - obligations)
        cur = company.currency or "SAR"

        # Cash Runway (كم شهر تكفي السيولة)
        runway_months = round(cash / abs(monthly_net), 1) if monthly_net < 0 and cash > 0 else None
        runway_status = "critical" if (runway_months is not None and runway_months < 3) else ("warn" if (runway_months is not None and runway_months < 6) else "good")

        # توقّع ١٣ أسبوع (13-week cash forecast) — من الصافي الشهري
        weekly_net = round(monthly_net / 4.33)
        forecast_13w = []
        running = cash
        for w in range(1, 14):
            running += weekly_net
            forecast_13w.append({"week": w, "cash": round(running)})
        # نقطة العجز (متى تنفد السيولة)
        deficit_week = None
        for f in forecast_13w:
            if f["cash"] < 0:
                deficit_week = f["week"]; break

        # AR/AP aging من وحدة المالية
        fin = s.exec(select(CompanyModuleEntry).where(
            CompanyModuleEntry.company_id == company.id, CompanyModuleEntry.module == "finance"
        ).order_by(CompanyModuleEntry.created_at.desc())).first()
        fd = {}
        if fin and fin.data:
            try: fd = json.loads(fin.data)
            except: pass
        def fpick(*kw):
            for k, v in fd.items():
                if any(w in k for w in kw):
                    try: return float(str(v).replace(",", "").replace("%", "").strip())
                    except: continue
            return None
        ar = fpick("ذمم مدينة", "مستحقات لك")
        ap = fpick("ذمم دائنة", "مستحقات عليك")
        debt_short = fpick("ديون قصيرة", "قصيرة الأجل")
        debt_long = fpick("ديون طويلة", "طويلة الأجل")

        return {
            "has_data": True,
            "company": {"name": company.name},
            "currency": cur,
            "cash_now": round(cash),
            "monthly_net": monthly_net,
            "obligations": round(obligations),
            "runway": {"months": runway_months, "status": runway_status,
                       "note": "السيولة كافية" if runway_months is None else f"تكفي {runway_months} شهر بالمعدل الحالي"},
            "forecast_13w": forecast_13w,
            "deficit_week": deficit_week,
            "receivables": {"ar": round(ar) if ar is not None else None, "ap": round(ap) if ap is not None else None,
                            "net": round(ar - ap) if (ar is not None and ap is not None) else None},
            "debt": {"short": round(debt_short) if debt_short is not None else None,
                     "long": round(debt_long) if debt_long is not None else None,
                     "total": round((debt_short or 0) + (debt_long or 0)) if (debt_short is not None or debt_long is not None) else None},
        }


@app.get("/company/cashflow")
def company_cashflow(user: User = Depends(get_current_user)):
    """التدفق النقدي التنبؤي: كم شهر تكفي السيولة + نقطة العجز المتوقّعة.
    صلاحية: المالك والمحاسب فقط (مورد مالي حساس)."""
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    with Session(engine) as s:
        company = s.get(Company, user.company_id)
        if not company:
            raise HTTPException(403, "غير مصرّح")
        # فحص الصلاحية عبر RBAC (بدل حصرها على المالك فقط)
        role = get_user_role(s, user)
        if not check_permission(role, "cashflow", "view"):
            raise HTTPException(403, "غير مصرّح — التدفق النقدي متاح للمالك والمحاسب فقط")
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
# ===== الخدمة 6: تحليل هدر الإيرادات والاحتيال (Leakage Detection) =====
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
        if not company or not check_permission(get_user_role(s, user), "leakage", "view"):
            raise HTTPException(403, "غير مصرّح — تحليل هدر الإيرادات للمالك والمحاسب فقط")
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
    "accountant": {"label": "محاسب", "perms": "الاطّلاع على التقارير المالية والتدفق النقدي وتحليل هدر الإيرادات."},
    "staff":      {"label": "موظف", "perms": "إدخال البيانات التشغيلية فقط."},
}

# ═══════════════════════════════════════════════════════════
#  نظام الصلاحيات المركزي (RBAC) — خدمة ١ من المرحلة ١
#  مصفوفة صلاحيات صريحة: كل دور وما يُسمح له من موارد وإجراءات.
#  تُطبّق على مستوى الخادم (server-side) — لا يمكن تجاوزها من الواجهة.
# ═══════════════════════════════════════════════════════════
# الموارد (resources): وحدات المنصة التي تُحمى
# الإجراءات (actions): view (اطّلاع) / edit (تعديل) / manage (إدارة كاملة)
ROLE_PERMISSIONS = {
    "owner": {
        # المالك: كل شيء
        "_all": {"view", "edit", "manage"},
    },
    "manager": {
        # مدير الفرع: يرى ويدير فرعه فقط (يُقيّد بـ branch_id لاحقاً)
        "dashboard":   {"view"},
        "health":      {"view"},
        "branches":    {"view"},          # فرعه فقط
        "sales":       {"view", "edit"},
        "ops":         {"view", "edit"},
        "inventory":   {"view", "edit"},
        "customers":   {"view", "edit"},
        "hr":          {"view"},
        "input":       {"view", "edit"},
        "decisions":   {"view"},
        "predictions": {"view"},
    },
    "accountant": {
        # المحاسب: المالية فقط (لا تشغيل، لا HR تفصيلي)
        "dashboard":   {"view"},
        "finance":     {"view", "edit"},
        "cashflow":    {"view"},
        "leakage":     {"view"},
        "tax":         {"view", "edit"},
        "reports":     {"view"},
        "health":      {"view"},
    },
    "staff": {
        # الموظف: إدخال تشغيلي فقط
        "input":       {"view", "edit"},
        "sales":       {"edit"},
        "ops":         {"edit"},
    },
}

# الحقول المالية الحساسة التي تُخفى عن الأدوار غير المصرّح لها (أمان مستوى العمود)
SENSITIVE_FINANCIAL_FIELDS = {"salary", "salaries", "رواتب", "أجور", "payroll",
                               "margin", "هامش", "profit_margin", "net_profit", "صافي الربح"}
# الأدوار المصرّح لها برؤية الحقول المالية الحساسة
ROLES_SEE_SENSITIVE = {"owner", "accountant"}


def check_permission(user_role: str, resource: str, action: str = "view") -> bool:
    """يتحقق: هل هذا الدور مصرّح له بهذا الإجراء على هذا المورد؟
    تُطبّق على مستوى الخادم — الأساس الذي تعتمد عليه كل الخدمات اللاحقة."""
    role = (user_role or "staff").lower()
    if role == "owner":
        return True
    perms = ROLE_PERMISSIONS.get(role, {})
    # صلاحية شاملة
    if "_all" in perms and action in perms["_all"]:
        return True
    allowed = perms.get(resource, set())
    return action in allowed


def can_see_sensitive_financials(user_role: str) -> bool:
    """هل يُسمح لهذا الدور برؤية الحقول المالية الحساسة (رواتب، هوامش)؟"""
    return (user_role or "").lower() in ROLES_SEE_SENSITIVE


def get_user_role(s, user: User) -> str:
    """يحدّد دور المستخدم في شركته الحالية: owner إن كان المالك، وإلا دوره في CompanyMember."""
    if not user.company_id:
        return ""
    company = s.get(Company, user.company_id)
    if company and company.owner_id == user.id:
        return "owner"
    m = s.exec(
        select(CompanyMember).where(
            CompanyMember.company_id == user.company_id,
            CompanyMember.email == user.email,
        )
    ).first()
    return m.role if m else "staff"


def filter_sensitive_fields(data: dict, user_role: str) -> dict:
    """يزيل الحقول المالية الحساسة من قاموس البيانات إن لم يكن الدور مصرّحاً له.
    أمان مستوى العمود على مستوى الاستعلام — لا مجرد إخفاء في الواجهة."""
    if can_see_sensitive_financials(user_role):
        return data
    if not isinstance(data, dict):
        return data
    cleaned = {}
    for k, v in data.items():
        kl = str(k).lower()
        if any(sf in kl for sf in SENSITIVE_FINANCIAL_FIELDS):
            continue  # نحذف الحقل الحساس تماماً
        cleaned[k] = v
    return cleaned


# ═══════════════════════════════════════════════════════════
#  طبقة أمان الصلاحيات (RBAC Enforcement Layer) — Phase 1.3
#  helpers موحّدة تجمع اشتقاق الدور + التحقق من الصلاحية.
#  لا تغيّر السلوك — تُوحّد النمط المكرّر في مكان واحد.
# ═══════════════════════════════════════════════════════════
def user_can(s, user: User, resource: str, action: str = "view") -> bool:
    """يجمع اشتقاق الدور + فحص الصلاحية في استدعاء واحد.
    يُرجع True/False — للاستخدام في المنطق الشرطي."""
    role = get_user_role(s, user)
    return check_permission(role, resource, action)


def require_permission(s, user: User, resource: str, action: str = "view"):
    """يتحقق من الصلاحية ويرفع 403 إن رُفضت — للحماية المباشرة.
    يُرجع دور المستخدم عند النجاح (لاستخدامه في تصفية الحقول الحساسة)."""
    role = get_user_role(s, user)
    if not check_permission(role, resource, action):
        raise HTTPException(403, "غير مصرّح — ليس لديك صلاحية للوصول لهذا القسم")
    return role


def scope_by_role(s, user: User, branches):
    """يقيّد الفروع حسب الدور: مدير الفرع يرى فرعه فقط، الباقي يرى الكل.
    (يطبّق مبدأ Row-Level ضمن الشركة الواحدة)."""
    role = get_user_role(s, user)
    if role in ("owner", "accountant"):
        return branches  # يرون كل فروع الشركة
    # مدير الفرع / الموظف: نقيّد بفرعه إن كان محدّداً في CompanyMember
    m = s.exec(
        select(CompanyMember).where(
            CompanyMember.company_id == user.company_id,
            CompanyMember.email == user.email,
        )
    ).first()
    if m and m.branch_id:
        return [b for b in branches if b.id == m.branch_id]
    return branches  # لا فرع محدّد → يرى الكل (سلوك افتراضي حالي)


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
                raise HTTPException(400, "نتيجة تحتاج إلى تحقق — لإجراء المحاكاة نحتاج بيانات فترتين على الأقل. أضف بياناتك لتفعيلها.")

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
        week_ago = datetime.now() - timedelta(days=7)
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
                "owner_phone": (owner.phone if owner else "") or "—",
                "branch_count": branch_count,
                "entries_count": entries_count,
                "is_active": c.is_active,
                "is_new": bool(c.created_at and c.created_at >= week_ago),
                "created_at": c.created_at.isoformat() if c.created_at else None,
            })
        result.sort(key=lambda x: x["created_at"] or "", reverse=True)
        new_count = sum(1 for r in result if r["is_new"])
        return {"companies": result, "total": len(result), "new_this_week": new_count}


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
    # ═══ طبقة سلامة البيانات (Phase 1.4): تحقّق قبل الحفظ ═══
    validation = validate_module_input(data)
    cleaned = validation["cleaned"]
    # نزيل القيم الفارغة (كما كان)
    cleaned = {k: v for k, v in cleaned.items() if v is not None and str(v).strip() != ""}
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
            _logger.error(f"فشل حفظ الوحدة: {type(e).__name__}: {str(e)[:200]}"); raise HTTPException(500, "تعذّر حفظ البيانات. حاول مرة أخرى.")
        log_activity(user.name, f"حفظ بيانات وحدة {MODULE_LABEL.get(module, module)} ({period})", user.email)
        return {"ok": True, "id": entry.id, "period": period, "module": module,
                "fields": len(cleaned), "warnings": validation.get("warnings", [])}


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


@app.get("/company-hr-analytics.html")
def page_company_hr_analytics():
    return FileResponse("company-hr-analytics.html")

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
            "margin": round((total_sales - total_expenses) / total_sales * 100, 1) if (total_sales > 0 and total_expenses > 0) else 0,
            "expenses_missing": total_expenses <= 0 and total_sales > 0,
            "branches_count": len(branches),
            "branches_with_data": len(branch_data),
        }

        # ===== كشف تلقائي للمشاكل والفرص =====
        # 1) فروع ضعيفة الأداء (مؤشّر < 40)
        weak = [bd for bd in branch_data if bd["entry"].branch_score < 40]
        if weak:
            names = "، ".join(bd["branch"].name for bd in weak[:3])
            weak_sales = sum(bd["entry"].sales for bd in weak)
            impact = round(weak_sales * 0.15)
            decisions.append({
                "priority": "عاجل", "icon": "🚨",
                "title": f"{len(weak)} فرع ضعيف الأداء يحتاج تدخّل فوري",
                "detail": f"الفروع: {names}. مؤشّرها أقل من 40/100. رفع أدائها لمستوى المتوسط قد يضيف ~{impact:,} ريال شهرياً (تقدير: 15% من مبيعاتها البالغة {round(weak_sales):,} ر). | ثقة 90% · تنفيذ 2-4 أسابيع",
                "action": "افتح مقارنة الفروع لمعرفة السبب الجذري",
                "link": "company-branches.html",
            })

        # 2) فروع بهامش منخفض جداً (<10%) — فقط لو المصروفات مدخلة
        thin = [bd for bd in branch_data if bd["entry"].margin < 10 and bd["entry"].sales > 0 and bd["entry"].expenses > 0]
        if thin:
            thin_sales = sum(bd["entry"].sales for bd in thin)
            impact = round(thin_sales * 0.05)
            decisions.append({
                "priority": "عاجل", "icon": "💸",
                "title": f"{len(thin)} فرع بهامش ربح منخفض جداً",
                "detail": f"هامش الربح أقل من 10%. تحسين الهامش 5 نقاط مئوية فقط يضيف ~{impact:,} ريال شهرياً (تقدير: 5% × مبيعاتها {round(thin_sales):,} ر). | ثقة 85% · تنفيذ 1-2 أسبوع",
                "action": "حلّل الأسباب عبر التحليل التنفيذي",
                "link": "company-dashboard.html",
            })

        # 3) فروع تراجعت مبيعاتها (نمو سالب أكثر من 15%-)
        declining = [bd for bd in branch_data if bd["entry"].growth <= -15]
        if declining:
            names = "، ".join(bd["branch"].name for bd in declining[:3])
            dec_sales = sum(bd["entry"].sales for bd in declining)
            impact = round(dec_sales * 0.10)
            decisions.append({
                "priority": "مهم", "icon": "📉",
                "title": f"تراجع حاد في مبيعات {len(declining)} فرع",
                "detail": f"{names} — تراجع المبيعات تجاوز 15%. استرداد نصف التراجع فقط يعيد ~{impact:,} ريال شهرياً (تقدير). | ثقة 80% · تنفيذ 2-6 أسابيع",
                "action": "افحص أسباب التراجع",
                "link": "company-branches.html",
            })

        # 4) تحليل هدر الإيرادات
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
                    "action": "افتح تحليل هدر الإيرادات",
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
            if kpis.get("expenses_missing"):
                alerts.append({"icon": "📥", "msg": "المصروفات غير مدخلة (بيانات POS مبيعات فقط) — أضفها من الوحدة المالية لتفعيل تحليل الربحية والهامش"})
            elif kpis["margin"] < 15:
                alerts.append({"icon": "⚠️", "msg": f"هامش الشركة الإجمالي {kpis['margin']}% — تحت المعدل الصحّي"})
            if len([bd for bd in branch_data if bd["entry"].repeat_rate < 20]) > 0:
                alerts.append({"icon": "👥", "msg": "معدل تكرار العملاء منخفض في بعض الفروع — راجع تجربة العميل"})

        # ترتيب القرارات: عاجل أولاً
        priority_order = {"عاجل": 0, "مهم": 1, "متوسط": 2}
        decisions.sort(key=lambda d: priority_order.get(d["priority"], 9))

        # اقتطاع لأهم 4
        decisions = decisions[:4]
        opportunities = opportunities[:4]

        # ===== ملخّص المخاطر (مدمج في الملخص التنفيذي — بدل تبويب منفصل) =====
        risk_summary = []
        _margins = [bd["entry"].margin for bd in branch_data if bd["entry"].sales > 0 and bd["entry"].expenses > 0]
        _avg_margin = sum(_margins) / len(_margins) if _margins else 0
        _repeats = [bd["entry"].repeat_rate for bd in branch_data if bd["entry"].repeat_rate > 0]
        _avg_repeat = sum(_repeats) / len(_repeats) if _repeats else 0
        _net = total_sales - total_expenses
        # سيولة
        _cash = company.cash_reserve or 0
        _oblig = company.monthly_obligations or 0
        if _net < 0:
            risk_summary.append({"icon": "💧", "title": "مخاطر السيولة", "level": "high",
                                 "reason": "صافي التشغيل سالب هذا الشهر", "link": "company-cashflow.html"})
        elif _oblig > 0 and _cash > 0 and (_cash / max(_oblig, 1)) < 3:
            risk_summary.append({"icon": "💧", "title": "مخاطر السيولة", "level": "medium",
                                 "reason": f"السيولة تكفي {round(_cash/max(_oblig,1),1)} شهر", "link": "company-cashflow.html"})
        # ربحية
        if _margins and _avg_margin < 10:
            risk_summary.append({"icon": "📉", "title": "انخفاض الأرباح", "level": "high",
                                 "reason": f"هامش الربح {round(_avg_margin)}% — منخفض جداً", "link": "company-finance.html"})
        elif _margins and _avg_margin < 20:
            risk_summary.append({"icon": "📉", "title": "انخفاض الأرباح", "level": "medium",
                                 "reason": f"هامش الربح {round(_avg_margin)}% — دون المعدل الصحّي", "link": "company-finance.html"})
        # فقد العملاء
        if _repeats and _avg_repeat < 20:
            risk_summary.append({"icon": "👋", "title": "فقد العملاء", "level": "high",
                                 "reason": f"معدل تكرار العملاء منخفض ({round(_avg_repeat)}%)", "link": "company-customers.html"})
        risk_summary.sort(key=lambda r: 0 if r["level"] == "high" else 1)

        return {
            "company": {"name": company.name, "sector": SECTOR_NAMES.get(company.sector, company.sector)},
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "brief": {
                "greeting": f"مرحباً {user.name.split()[0] if user.name else ''} 👋 — هذا ملخّصك التنفيذي اليوم",
                "line": (
                    f"عندك {len(decisions)} قرار يحتاج انتباهك و{len(opportunities)} فرصة نمو"
                    if decisions else
                    "لا قرارات عاجلة اليوم — شركتك في وضع مستقر، راجع الفرص أدناه"
                ),
                "top": [
                    {"icon": d["icon"], "title": d["title"], "priority": d["priority"]}
                    for d in decisions[:3]
                ],
            },
            "kpis": kpis,
            "decisions": decisions,
            "opportunities": opportunities,
            "alerts": alerts,
            "risks": risk_summary,
            "five_pillars": _five_pillars(total_sales, total_expenses, branch_data),
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

        # ===== السيناريوهات الثلاثة (Base-Best-Worst) — من التقرير =====
        base_6m_sales = round(sum(total_sales_proj))
        base_6m_profit = round(sum(total_profit_proj))
        scenarios = {
            "worst": {"label": "متشائم", "sales": round(base_6m_sales * 0.88), "profit": round(base_6m_profit * 0.80),
                      "note": "افتراض ثابت: −12% مبيعات و−20% ربح (ليس مشتقاً من بياناتك)", "basis": "fixed_assumption"},
            "base": {"label": "أساسي", "sales": base_6m_sales, "profit": base_6m_profit,
                     "note": "استمرار الاتجاه الحالي"},
            "best": {"label": "متفائل", "sales": round(base_6m_sales * 1.12), "profit": round(base_6m_profit * 1.20),
                     "note": "افتراض ثابت: +12% مبيعات و+20% ربح (ليس مشتقاً من بياناتك)", "basis": "fixed_assumption"},
        }

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
            "scenarios": scenarios,
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
                if e.sales > 0 and e.expenses > 0:
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
        margin_vals = [r[1].margin for r in rows if r[1].sales > 0 and r[1].expenses > 0]
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
            "risks": "مؤشرات تسرّب في بعض الفروع — افتح صفحة تحليل هدر الإيرادات.",
        }
        main_cause = explanations.get(weakest["key"], "راجع البيانات لمعرفة السبب.")

        # ===== صحة كل فرع (Health by Branch decomposition — من ملف المرحلة ١) =====
        branch_health = []
        for b, e in rows:
            bh = e.branch_score if e.branch_score else 0
            if bh >= 75: bcolor = "#10b981"
            elif bh >= 55: bcolor = "#f5b301"
            elif bh >= 35: bcolor = "#f59e0b"
            else: bcolor = "#ef4444"
            branch_health.append({
                "name": b.name, "city": b.city or "", "score": bh, "color": bcolor,
                "margin": round(e.margin, 1) if e.margin else 0,
                "has_data": e.sales > 0,
            })
        branch_health.sort(key=lambda x: x["score"], reverse=True)

        # ===== confidence_flag: صحة الشركة تحمل مؤشر جودة بياناتها =====
        _all_e = []
        for _b, _e in rows:
            _all_e.append(_e)
        _qscore, _ = check_data_quality_rules(_all_e)
        _confidence = compute_confidence_flag(_qscore)

        return {
            "company": {"name": company.name},
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "overall": overall,
            "level": level,
            "color": color,
            "axes": axes,
            "weakest_axis": weakest["label"],
            "main_cause": main_cause,
            "branch_health": branch_health,
            "confidence": _confidence,
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

# ═══════════════════════════════════════════════════════════
#  محرك قواعد جودة البيانات (خدمة ٣ من المرحلة ١)
#  قواعد منطقية قابلة للتوسّع — تفحص كل دفعة بيانات وتعطي درجة 0-100.
#  القاعدة الذهبية: أي بيانات جودتها < 60% تحمل confidence_flag
#  حقل مستمر تستخدمه كل الخدمات اللاحقة (صحة، مركز قيادة، تنبؤ...).
# ═══════════════════════════════════════════════════════════
QUALITY_THRESHOLD = 60  # عتبة الثقة: أقل منها = confidence_flag


# ═══════════════════════════════════════════════════════════
#  طبقة سلامة البيانات (Data Integrity Layer) — Phase 1.4
#  تحقّق من المدخلات عند الحفظ: أنواع، حدود، قيم منطقية.
#  لا ترفض بيانات المستخدم قسراً (تجنّب الكسر) — تنظّف وتُبلّغ.
# ═══════════════════════════════════════════════════════════
# حدود منطقية للقيم المالية (سقف معقول لكشف الأخطاء الجسيمة)
MAX_REASONABLE_VALUE = 10_000_000_000  # 10 مليار — سقف لكشف الأخطاء الكتابية
# الحقول التي يجب ألا تكون سالبة (قيم مطلقة)
NON_NEGATIVE_HINTS = ("مبيعات", "sales", "إيراد", "revenue", "عدد", "count",
                       "عملاء", "customers", "موظف", "employee", "كمية", "quantity",
                       "مخزون", "inventory", "تكلفة", "cost", "رواتب", "salary")


def validate_module_input(data: dict) -> dict:
    """يتحقق من مدخلات الوحدة قبل الحفظ.
    يُرجع: {cleaned, warnings, rejected}
    - cleaned: القيم الصالحة (بعد التنظيف)
    - warnings: تنبيهات على قيم مشبوهة (لا ترفض)
    - rejected: قيم رُفضت (نوع خاطئ فقط)
    آمن: لا يرفض بيانات صالحة، يزيل فقط الفاسد بوضوح."""
    cleaned = {}
    warnings = []
    rejected = []
    if not isinstance(data, dict):
        return {"cleaned": {}, "warnings": [], "rejected": ["البيانات ليست كائناً صالحاً"]}

    for k, v in data.items():
        key = str(k).strip()
        if not key:
            continue
        # القيم النصّية الوصفية (أسباب، ملاحظات) تُقبل كما هي
        if key.startswith("__") or isinstance(v, str) and not _looks_numeric(v):
            cleaned[key] = v
            continue
        # نحاول تحويل رقمي
        num = _safe_number(v)
        if num is None:
            # قيمة غير رقمية في حقل يُتوقّع أن يكون رقمياً — نقبلها كنص (لا نرفض)
            cleaned[key] = v
            continue
        # فحص ١: قيمة سالبة في حقل يجب أن يكون موجباً
        kl = key.lower()
        if num < 0 and any(h in key or h in kl for h in NON_NEGATIVE_HINTS):
            # لا نرفض (قد تكون مرتجعات) — لكن ننبّه
            warnings.append(f"قيمة سالبة في «{key}» ({num}) — تأكّد أنها مقصودة")
            cleaned[key] = num
        # فحص ٢: قيمة أكبر من المعقول (خطأ كتابي محتمل)
        elif abs(num) > MAX_REASONABLE_VALUE:
            warnings.append(f"قيمة كبيرة جداً في «{key}» — تأكّد من عدم وجود خطأ كتابي")
            cleaned[key] = num
        else:
            cleaned[key] = num

    return {"cleaned": cleaned, "warnings": warnings, "rejected": rejected}


def _looks_numeric(s):
    """هل النص يبدو رقماً؟"""
    try:
        float(str(s).replace(",", "").replace("%", "").strip())
        return True
    except (ValueError, TypeError):
        return False


def _safe_number(v):
    """يحوّل لرقم بأمان أو None."""
    if isinstance(v, (int, float)):
        return v
    try:
        return float(str(v).replace(",", "").replace("%", "").strip())
    except (ValueError, TypeError):
        return None


def check_data_quality_rules(entries):
    """يفحص قائمة إدخالات بقواعد منطقية ويرجع (score, flags).
    قابل للتوسّع: أضف قاعدة جديدة في RULES دون تغيير المنطق."""
    if not entries:
        return 0, [{"rule": "no_data", "msg": "لا توجد بيانات", "severity": "high"}]

    flags = []
    total_checks = 0
    passed_checks = 0

    for e in entries:
        # ① المبيعات غير سالبة
        total_checks += 1
        if getattr(e, "sales", 0) is not None and getattr(e, "sales", 0) >= 0:
            passed_checks += 1
        else:
            flags.append({"rule": "negative_sales", "msg": f"مبيعات سالبة في {getattr(e,'period','?')}", "severity": "high"})

        # ② المصروفات غير سالبة
        total_checks += 1
        if getattr(e, "expenses", 0) is not None and getattr(e, "expenses", 0) >= 0:
            passed_checks += 1
        else:
            flags.append({"rule": "negative_expenses", "msg": f"مصروفات سالبة في {getattr(e,'period','?')}", "severity": "high"})

        # ③ المصروفات لا تتجاوز المبيعات بشكل غير منطقي (>300%)
        total_checks += 1
        sales = getattr(e, "sales", 0) or 0
        exp = getattr(e, "expenses", 0) or 0
        if sales == 0 or exp <= sales * 3:
            passed_checks += 1
        else:
            flags.append({"rule": "expenses_exceed", "msg": f"مصروفات مرتفعة جداً مقابل المبيعات في {getattr(e,'period','?')}", "severity": "medium"})

        # ④ وجود فترة (تاريخ) صحيحة
        total_checks += 1
        if getattr(e, "period", None):
            passed_checks += 1
        else:
            flags.append({"rule": "missing_period", "msg": "فترة زمنية مفقودة", "severity": "medium"})

        # ⑤ عدد العملاء غير سالب
        total_checks += 1
        if getattr(e, "customers", 0) is None or getattr(e, "customers", 0) >= 0:
            passed_checks += 1
        else:
            flags.append({"rule": "negative_customers", "msg": "عدد عملاء سالب", "severity": "medium"})

    score = round((passed_checks / total_checks) * 100) if total_checks else 0
    return score, flags


@app.post("/company/whatif")
def company_whatif(data: dict, user: User = Depends(get_current_user)):
    """محاكاة السيناريوهات (What-If) — إضافة مميّزة:
    'لو رفعت الأسعار 10%؟' → نبّاه يحاكي الأثر على الربح والهامش فوراً.
    كل الحسابات من بياناتك الفعلية — لا اختراع."""
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    with Session(engine) as s:
        company = s.get(Company, user.company_id)
        if not company or not check_permission(get_user_role(s, user), "finance", "view"):
            raise HTTPException(403, "غير مصرّح")
        if company.is_active != 1:
            raise HTTPException(402, "شركتك قيد التفعيل")

        # الوضع الحالي (خط الأساس)
        branches = s.exec(select(CompanyBranch).where(CompanyBranch.company_id == company.id, CompanyBranch.is_active == 1)).all()
        base_sales = base_expenses = base_customers = 0.0
        for b in branches:
            e = s.exec(select(CompanyEntry).where(CompanyEntry.branch_id == b.id).order_by(CompanyEntry.created_at.desc())).first()
            if e:
                base_sales += e.sales or 0
                base_expenses += e.expenses or 0
                base_customers += e.customers or 0
        if base_sales <= 0:
            return {"has_data": False, "message": "لا توجد بيانات كافية للمحاكاة."}

        base_profit = base_sales - base_expenses
        base_margin = round(base_profit / base_sales * 100, 1) if base_sales else 0

        # المتغيّرات من الطلب (نسب التغيير %)
        price_change = float(data.get("price_change", 0) or 0)        # تغيير الأسعار %
        volume_change = float(data.get("volume_change", 0) or 0)      # تغيير الكمية/العملاء %
        cost_change = float(data.get("cost_change", 0) or 0)          # تغيير التكاليف %
        expense_change = float(data.get("expense_change", 0) or 0)    # تغيير المصروفات %

        # المحاكاة (منطق مالي معياري)
        # المبيعات الجديدة = الأساس × (1+تغير السعر) × (1+تغير الكمية)
        new_sales = base_sales * (1 + price_change / 100) * (1 + volume_change / 100)
        # المصروفات: جزء متغيّر مع الكمية (COGS) + تغيير مباشر
        new_expenses = base_expenses * (1 + volume_change / 100) * (1 + cost_change / 100) * (1 + expense_change / 100)
        new_profit = new_sales - new_expenses
        new_margin = round(new_profit / new_sales * 100, 1) if new_sales else 0

        profit_change = round(new_profit - base_profit)
        profit_change_pct = round((new_profit - base_profit) / abs(base_profit) * 100, 1) if base_profit else 0

        return {
            "has_data": True,
            "currency": company.currency or "SAR",
            "baseline": {"sales": round(base_sales), "expenses": round(base_expenses),
                         "profit": round(base_profit), "margin": base_margin},
            "scenario": {"sales": round(new_sales), "expenses": round(new_expenses),
                         "profit": round(new_profit), "margin": new_margin},
            "impact": {"profit_change": profit_change, "profit_change_pct": profit_change_pct,
                       "margin_change": round(new_margin - base_margin, 1),
                       "direction": "positive" if profit_change >= 0 else "negative"},
            "inputs": {"price_change": price_change, "volume_change": volume_change,
                       "cost_change": cost_change, "expense_change": expense_change},
        }


@app.get("/company/benchmark")
def company_benchmark(user: User = Depends(get_current_user)):
    """البنشمارك القطاعي (P1 من التقرير): يقارن أداء الشركة بمعايير قطاعها.
    المعايير تقديرية إرشادية — تُوسم بوضوح (لا تُقدَّم كأرقام رسمية)."""
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    with Session(engine) as s:
        company = s.get(Company, user.company_id)
        if not company:
            raise HTTPException(403, "غير مصرّح")
        if company.is_active != 1:
            raise HTTPException(402, "شركتك قيد التفعيل")

        # نحسب أداء الشركة الفعلي
        branches = s.exec(select(CompanyBranch).where(CompanyBranch.company_id == company.id, CompanyBranch.is_active == 1)).all()
        total_sales = total_expenses = total_invoices = 0.0
        growths = []
        for b in branches:
            e = s.exec(select(CompanyEntry).where(CompanyEntry.branch_id == b.id).order_by(CompanyEntry.created_at.desc())).first()
            if e:
                total_sales += e.sales or 0
                total_expenses += e.expenses or 0
                total_invoices += e.invoices or 0
                if e.growth is not None:
                    growths.append(e.growth)
        if total_sales <= 0:
            return {"has_data": False, "message": "لا توجد بيانات كافية للمقارنة."}

        my_margin = round((total_sales - total_expenses) / total_sales * 100, 1)
        my_expense_ratio = round(total_expenses / total_sales * 100, 1)
        my_avg_ticket = round(total_sales / total_invoices) if total_invoices > 0 else 0
        my_growth = round(sum(growths) / len(growths), 1) if growths else 0

        # نجلب معايير القطاع (بمرونة — نطابق قطاع الشركة)
        sector_map = {"fnb": "restaurant", "retail": "retail", "cafe": "cafe"}
        bkey = sector_map.get(company.sector, "restaurant")
        bm = BENCHMARKS.get(bkey, BENCHMARKS["restaurant"])

        def compare(my_val, benchmark, higher_better=True):
            if higher_better:
                pct = round((my_val - benchmark) / benchmark * 100) if benchmark else 0
                status = "above" if my_val >= benchmark else "below"
            else:
                pct = round((benchmark - my_val) / benchmark * 100) if benchmark else 0
                status = "above" if my_val <= benchmark else "below"
            return {"pct": pct, "status": status}

        metrics = [
            {"label": "هامش الربح", "my": my_margin, "benchmark": bm["margin_ok"], "unit": "%",
             **compare(my_margin, bm["margin_ok"], True)},
            {"label": "متوسط قيمة الفاتورة", "my": my_avg_ticket, "benchmark": bm["avg_ticket_good"], "unit": "ريال",
             **compare(my_avg_ticket, bm["avg_ticket_good"], True)},
            {"label": "نسبة المصروفات", "my": my_expense_ratio, "benchmark": bm["expense_ratio_ok"], "unit": "%",
             **compare(my_expense_ratio, bm["expense_ratio_ok"], False)},
            {"label": "معدل النمو", "my": my_growth, "benchmark": bm["orders_growth"], "unit": "%",
             **compare(my_growth, bm["orders_growth"], True)},
        ]
        # درجة الموقع التنافسي (كم مؤشر فوق المعيار)
        above_count = sum(1 for m in metrics if m["status"] == "above")
        position_score = round(above_count / len(metrics) * 100)
        position = "متفوّق على القطاع" if position_score >= 75 else ("في مستوى القطاع" if position_score >= 50 else "دون مستوى القطاع")

        return {
            "has_data": True,
            "company": {"name": company.name},
            "sector_name": bm["name"],
            "metrics": metrics,
            "position_score": position_score,
            "position": position,
            "disclaimer": "المعايير تقديرية إرشادية مبنية على متوسطات القطاع العامة — ليست أرقاماً رسمية.",
        }


@app.get("/company/customer-health")
def company_customer_health(user: User = Depends(get_current_user)):
    """صحة العملاء والتنبؤ بالتسرّب (Customer Health) — إضافة مميّزة (Gainsight-style):
    درجة صحة قاعدة العملاء + مؤشرات التسرّب + توصيات الاحتفاظ.
    من بيانات وحدة العملاء الفعلية + معدل التكرار."""
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    with Session(engine) as s:
        company = s.get(Company, user.company_id)
        if not company or (not check_permission(get_user_role(s, user), "customers", "view") and get_user_role(s, user) != "owner"):
            raise HTTPException(403, "غير مصرّح")
        if company.is_active != 1:
            raise HTTPException(402, "شركتك قيد التفعيل")

        # نجمع بيانات العملاء من الفروع + وحدة العملاء
        branches = s.exec(select(CompanyBranch).where(CompanyBranch.company_id == company.id, CompanyBranch.is_active == 1)).all()
        total_customers = 0.0
        repeats = []
        for b in branches:
            e = s.exec(select(CompanyEntry).where(CompanyEntry.branch_id == b.id).order_by(CompanyEntry.created_at.desc())).first()
            if e:
                total_customers += e.customers or 0
                if e.repeat_rate and e.repeat_rate > 0:
                    repeats.append(e.repeat_rate)
        avg_repeat = round(sum(repeats) / len(repeats), 1) if repeats else 0

        # بيانات وحدة العملاء الموسّعة (إن وُجدت)
        ce = s.exec(select(CompanyModuleEntry).where(
            CompanyModuleEntry.company_id == company.id, CompanyModuleEntry.module == "customers"
        ).order_by(CompanyModuleEntry.created_at.desc())).first()
        cdata = {}
        if ce and ce.data:
            try: cdata = json.loads(ce.data)
            except: pass
        def cpick(*kw):
            for k, v in cdata.items():
                if any(w in k for w in kw):
                    try: return float(str(v).replace(",", "").replace("%", "").strip())
                    except: continue
            return None
        new_customers = cpick("عملاء جدد", "جدد")
        lost_customers = cpick("عملاء مفقودين", "فقدنا", "مغادرين")
        complaints = cpick("شكاوى", "شكوى")
        nps = cpick("nps", "رضا", "توصية")

        if total_customers <= 0 and not cdata:
            return {"has_data": False, "message": "لا توجد بيانات عملاء بعد. أدخِل بيانات وحدة العملاء."}

        # درجة صحة العملاء (0-100)
        health = 0
        factors = []
        if avg_repeat >= 40: health += 40; factors.append(("ولاء ممتاز", "good"))
        elif avg_repeat >= 25: health += 28; factors.append(("ولاء جيد", "good"))
        elif avg_repeat >= 15: health += 18; factors.append(("ولاء متوسط", "warn"))
        elif avg_repeat > 0: health += 8; factors.append(("ولاء ضعيف", "bad"))
        # نمو العملاء
        if new_customers is not None and lost_customers is not None:
            net = new_customers - lost_customers
            if net > 0: health += 30; factors.append((f"نمو صافي +{int(net)} عميل", "good"))
            else: health += 10; factors.append((f"تراجع صافي {int(net)} عميل", "bad"))
        elif total_customers > 0:
            health += 25
        # الشكاوى
        if complaints is not None:
            if complaints == 0: health += 15; factors.append(("لا شكاوى", "good"))
            elif complaints < 10: health += 8; factors.append((f"{int(complaints)} شكوى", "warn"))
            else: factors.append((f"{int(complaints)} شكوى — مرتفع", "bad"))
        # NPS
        if nps is not None:
            if nps >= 50: health += 15; factors.append((f"NPS {int(nps)} ممتاز", "good"))
            elif nps >= 0: health += 8; factors.append((f"NPS {int(nps)}", "warn"))
        health = min(health, 100)

        # مخاطر التسرّب (Churn Risk)
        churn_risk = "منخفض"
        churn_color = "good"
        churn_pct = 0
        if lost_customers is not None and total_customers > 0:
            churn_pct = round(lost_customers / total_customers * 100, 1)
            if churn_pct >= 15: churn_risk = "مرتفع"; churn_color = "bad"
            elif churn_pct >= 7: churn_risk = "متوسط"; churn_color = "warn"
        elif avg_repeat < 20 and avg_repeat > 0:
            churn_risk = "مرتفع (تكرار منخفض)"; churn_color = "bad"; churn_pct = round(100 - avg_repeat, 0)

        # توصيات الاحتفاظ
        recommendations = []
        if avg_repeat < 25:
            recommendations.append("أطلق برنامج ولاء — معدل التكرار الحالي منخفض ويهدّد الإيراد المتكرر.")
        if lost_customers and lost_customers > 0:
            recommendations.append(f"تواصل مع العملاء المفقودين ({int(lost_customers)}) لفهم أسباب المغادرة.")
        if complaints and complaints >= 10:
            recommendations.append("عالج الشكاوى المرتفعة — سبب رئيسي للتسرّب.")
        if not recommendations:
            recommendations.append("حافظ على مستوى الولاء الحالي وراقب المؤشرات دورياً.")

        hlevel = "صحية" if health >= 70 else ("تحتاج انتباه" if health >= 45 else "حرجة")
        return {
            "has_data": True,
            "company": {"name": company.name},
            "health_score": health, "health_level": hlevel,
            "total_customers": int(total_customers),
            "avg_repeat": avg_repeat,
            "churn_risk": churn_risk, "churn_color": churn_color, "churn_pct": churn_pct,
            "new_customers": int(new_customers) if new_customers is not None else None,
            "lost_customers": int(lost_customers) if lost_customers is not None else None,
            "nps": int(nps) if nps is not None else None,
            "factors": factors,
            "recommendations": recommendations,
        }


@app.get("/company/readiness")
def company_readiness(user: User = Depends(get_current_user)):
    """درجة جاهزية التحليل (من تقرير التدقيق): لكل شركة ولكل فرع.
    تُظهر بوضوح مدى اكتمال البيانات — بدل أن يبدو التحليل يقينياً دائماً."""
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    with Session(engine) as s:
        company = s.get(Company, user.company_id)
        if not company:
            raise HTTPException(403, "غير مصرّح")
        if company.is_active != 1:
            raise HTTPException(402, "شركتك قيد التفعيل")

        branches = s.exec(
            select(CompanyBranch).where(CompanyBranch.company_id == company.id, CompanyBranch.is_active == 1)
        ).all()
        # الوحدات المتوقّعة (كل وحدة مكتملة ترفع الجاهزية)
        EXPECTED_MODULES = ["finance", "sales", "customers", "hr", "ops", "inventory"]
        branch_readiness = []
        all_scores = []
        for b in branches:
            e = s.exec(select(CompanyEntry).where(CompanyEntry.branch_id == b.id).order_by(CompanyEntry.created_at.desc())).first()
            # جاهزية الفرع = اكتمال البيانات الأساسية + الوحدات
            score = 0
            factors = []
            if e and e.sales > 0:
                score += 30; factors.append("مبيعات ✓")
            else:
                factors.append("مبيعات ✗")
            if e and e.expenses > 0:
                score += 25; factors.append("مصروفات ✓")
            else:
                factors.append("مصروفات ✗")
            if e and e.customers > 0:
                score += 15; factors.append("عملاء ✓")
            # الوحدات الموسّعة لهذا الفرع
            mods = s.exec(select(CompanyModuleEntry).where(
                CompanyModuleEntry.company_id == company.id,
                (CompanyModuleEntry.branch_id == b.id) | (CompanyModuleEntry.branch_id == None)
            )).all()
            filled_mods = len(set(m.module for m in mods))
            score += min(filled_mods * 5, 30)
            score = min(score, 100)
            all_scores.append(score)
            level = "جاهز للتحليل" if score >= 75 else ("جزئي" if score >= 45 else "بيانات ناقصة")
            branch_readiness.append({
                "name": b.name, "city": b.city or "", "score": score, "level": level,
                "factors": factors, "modules_filled": filled_mods,
            })
        branch_readiness.sort(key=lambda x: x["score"], reverse=True)

        overall = round(sum(all_scores) / len(all_scores)) if all_scores else 0
        overall_level = "جاهزة للتحليل" if overall >= 75 else ("جاهزية جزئية" if overall >= 45 else "بيانات ناقصة")
        return {
            "company": {"name": company.name},
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "overall_readiness": overall,
            "overall_level": overall_level,
            "branch_readiness": branch_readiness,
            "note": "درجة الجاهزية تقيس اكتمال بياناتك — كلما زادت، زادت دقة كل التحليلات والتوقعات.",
        }


def compute_confidence_flag(quality_score):
    """القاعدة الذهبية: جودة < 60% → confidence_flag مرفوع.
    ترجع dict يُخزّن مع أي مؤشر مبني على هذه البيانات."""
    return {
        "flag": quality_score < QUALITY_THRESHOLD,
        "quality": quality_score,
        "label": ("موثوقية عالية" if quality_score >= 75 else
                  ("موثوقية متوسطة" if quality_score >= QUALITY_THRESHOLD else "تحتاج إلى تحقق")),
        "note": ("" if quality_score >= QUALITY_THRESHOLD else
                 "هذه النتيجة مبنية على بيانات غير مكتملة — تعامل معها بحذر حتى تكتمل البيانات."),
    }


@app.get("/company/sales-analytics")
def company_sales_analytics(user: User = Depends(get_current_user)):
    """وحدة المبيعات التفصيلية: إيراد، نمو، متوسط الطلب، التحويل، الخصومات.
    كل مؤشر has_data — أسماء الحقول تطابق صفحة الإدخال بالضبط."""
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    with Session(engine) as s:
        company = s.get(Company, user.company_id)
        if not company or (not check_permission(get_user_role(s, user), "sales", "view") and get_user_role(s, user) != "owner"):
            raise HTTPException(403, "غير مصرّح — وحدة المبيعات")
        if company.is_active != 1:
            raise HTTPException(402, "شركتك قيد التفعيل")

        entries = s.exec(
            select(CompanyModuleEntry).where(
                CompanyModuleEntry.company_id == company.id,
                CompanyModuleEntry.module == "sales",
            ).order_by(CompanyModuleEntry.created_at.desc())
        ).all()
        merged = {}
        for e in entries:
            try: merged.update(json.loads(e.data) if e.data else {})
            except: pass

        def pick(*kw):
            for k, v in merged.items():
                if any(w in k for w in kw):
                    try: return float(str(v).replace(",", "").replace("%", "").strip())
                    except: continue
            return None

        has_any = len(merged) > 0
        revenue = pick("إجمالي الإيرادات")
        orders = pick("عدد الطلبات")
        visitors = pick("عدد الزوار")
        target = pick("هدف المبيعات")
        discounts = pick("قيمة الخصومات")
        returns = pick("قيمة المرتجعات")
        prev_revenue = pick("إيرادات الفترة السابقة")

        # المؤشرات المشتقّة
        aov = round(revenue / orders) if (revenue and orders and orders > 0) else None
        conversion = round(orders / visitors * 100, 1) if (orders and visitors and visitors > 0) else None
        growth = round((revenue - prev_revenue) / prev_revenue * 100, 1) if (revenue and prev_revenue and prev_revenue > 0) else None
        target_pct = round(revenue / target * 100, 1) if (revenue and target and target > 0) else None
        discount_pct = round(discounts / revenue * 100, 1) if (discounts is not None and revenue and revenue > 0) else None
        return_pct = round(returns / revenue * 100, 1) if (returns is not None and revenue and revenue > 0) else None

        metrics = []
        metrics.append({"key":"revenue","icon":"💰","label":"إجمالي الإيرادات","value":round(revenue) if revenue is not None else None,"unit":"ريال","has_data":revenue is not None,"status":None})
        metrics.append({"key":"growth","icon":"📈","label":"نمو المبيعات","value":growth,"unit":"%","has_data":growth is not None,
                        "status":("good" if (growth is not None and growth>=5) else ("warn" if (growth is not None and growth>=0) else "bad")) if growth is not None else None})
        metrics.append({"key":"orders","icon":"🛒","label":"عدد الطلبات","value":int(orders) if orders is not None else None,"unit":"طلب","has_data":orders is not None,"status":None})
        metrics.append({"key":"aov","icon":"🧾","label":"متوسط قيمة الطلب","value":aov,"unit":"ريال","has_data":aov is not None,"status":None})
        metrics.append({"key":"conversion","icon":"🎯","label":"معدل التحويل","value":conversion,"unit":"%","has_data":conversion is not None,
                        "status":("good" if (conversion and conversion>=3) else "warn") if conversion is not None else None})
        metrics.append({"key":"target","icon":"🏁","label":"تحقيق الهدف","value":target_pct,"unit":"%","has_data":target_pct is not None,
                        "status":("good" if (target_pct and target_pct>=100) else ("warn" if (target_pct and target_pct>=80) else "bad")) if target_pct is not None else None})
        metrics.append({"key":"discount","icon":"🏷️","label":"نسبة الخصومات","value":discount_pct,"unit":"%","has_data":discount_pct is not None,
                        "status":("good" if (discount_pct is not None and discount_pct<10) else "warn") if discount_pct is not None else None})
        metrics.append({"key":"returns","icon":"↩️","label":"نسبة المرتجعات","value":return_pct,"unit":"%","has_data":return_pct is not None,
                        "status":("good" if (return_pct is not None and return_pct<5) else "warn") if return_pct is not None else None})

        filled = sum(1 for m in metrics if m["has_data"])
        return {
            "company": {"name": company.name},
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "has_any_data": has_any,
            "metrics": metrics,
            "filled_count": filled,
            "total_count": len(metrics),
        }


@app.get("/company/procurement-analytics")
def company_procurement_analytics(user: User = Depends(get_current_user)):
    """وحدة المشتريات الشاملة: إنفاق، موردين، مخاطر تركّز، وفورات.
    كل مؤشر has_data — أسماء الحقول تطابق صفحة الإدخال بالضبط."""
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    with Session(engine) as s:
        company = s.get(Company, user.company_id)
        if not company or (not check_permission(get_user_role(s, user), "procurement", "view") and get_user_role(s, user) != "owner"):
            raise HTTPException(403, "غير مصرّح — وحدة المشتريات")
        if company.is_active != 1:
            raise HTTPException(402, "شركتك قيد التفعيل")

        entries = s.exec(
            select(CompanyModuleEntry).where(
                CompanyModuleEntry.company_id == company.id,
                CompanyModuleEntry.module == "procurement",
            ).order_by(CompanyModuleEntry.created_at.desc())
        ).all()
        merged = {}
        for e in entries:
            try: merged.update(json.loads(e.data) if e.data else {})
            except: pass

        def pick(*kw):
            for k, v in merged.items():
                if any(w in k for w in kw):
                    try: return float(str(v).replace(",", "").replace("%", "").strip())
                    except: continue
            return None

        has_any = len(merged) > 0
        # الحقول الخام (تطابق صفحة الإدخال)
        total_spend = pick("إجمالي الإنفاق")
        suppliers = pick("عدد الموردين")
        top_supplier_spend = pick("إنفاق أكبر مورّد")
        savings = pick("الوفورات المحقّقة")
        po_count = pick("عدد أوامر الشراء")
        po_cycle = pick("متوسط دورة أمر الشراء")
        late_deliveries = pick("توريدات متأخرة")

        # المؤشرات المشتقّة
        concentration = round(top_supplier_spend / total_spend * 100, 1) if (top_supplier_spend and total_spend and total_spend > 0) else None
        savings_pct = round(savings / total_spend * 100, 1) if (savings is not None and total_spend and total_spend > 0) else None
        late_pct = round(late_deliveries / po_count * 100, 1) if (late_deliveries is not None and po_count and po_count > 0) else None

        metrics = []
        metrics.append({"key":"spend","icon":"💵","label":"إجمالي الإنفاق","value":round(total_spend) if total_spend is not None else None,"unit":"ريال","has_data":total_spend is not None,"status":None})
        metrics.append({"key":"suppliers","icon":"🏭","label":"عدد الموردين","value":int(suppliers) if suppliers is not None else None,"unit":"مورّد","has_data":suppliers is not None,"status":None})
        metrics.append({"key":"concentration","icon":"⚠️","label":"تركّز المورّدين (أكبر مورّد)","value":concentration,"unit":"%","has_data":concentration is not None,
                        "status":("good" if (concentration is not None and concentration<30) else ("warn" if (concentration is not None and concentration<50) else "bad")) if concentration is not None else None})
        metrics.append({"key":"savings","icon":"💰","label":"الوفورات المحقّقة","value":savings_pct,"unit":"%","has_data":savings_pct is not None,
                        "status":("good" if (savings_pct and savings_pct>=5) else "warn") if savings_pct is not None else None})
        metrics.append({"key":"po","icon":"📋","label":"عدد أوامر الشراء","value":int(po_count) if po_count is not None else None,"unit":"أمر","has_data":po_count is not None,"status":None})
        metrics.append({"key":"cycle","icon":"⏱️","label":"دورة أمر الشراء","value":round(po_cycle,1) if po_cycle is not None else None,"unit":"يوم","has_data":po_cycle is not None,
                        "status":("good" if (po_cycle is not None and po_cycle<7) else "warn") if po_cycle is not None else None})
        metrics.append({"key":"late","icon":"🚚","label":"التوريدات المتأخرة","value":late_pct,"unit":"%","has_data":late_pct is not None,
                        "status":("good" if (late_pct is not None and late_pct<10) else ("warn" if (late_pct is not None and late_pct<25) else "bad")) if late_pct is not None else None})

        filled = sum(1 for m in metrics if m["has_data"])
        return {
            "company": {"name": company.name},
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "has_any_data": has_any,
            "metrics": metrics,
            "filled_count": filled,
            "total_count": len(metrics),
        }


@app.get("/company/inventory-analytics")
def company_inventory_analytics(user: User = Depends(get_current_user)):
    """وحدة المخزون الشاملة: دوران، DIO، نفاد، قيمة، بطيء الحركة.
    كل مؤشر has_data — أسماء الحقول تطابق صفحة الإدخال بالضبط."""
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    with Session(engine) as s:
        company = s.get(Company, user.company_id)
        if not company or (not check_permission(get_user_role(s, user), "inventory", "view") and get_user_role(s, user) != "owner"):
            raise HTTPException(403, "غير مصرّح — وحدة المخزون")
        if company.is_active != 1:
            raise HTTPException(402, "شركتك قيد التفعيل")

        entries = s.exec(
            select(CompanyModuleEntry).where(
                CompanyModuleEntry.company_id == company.id,
                CompanyModuleEntry.module == "inventory",
            ).order_by(CompanyModuleEntry.created_at.desc())
        ).all()
        merged = {}
        for e in entries:
            try: merged.update(json.loads(e.data) if e.data else {})
            except: pass

        def pick(*kw):
            for k, v in merged.items():
                if any(w in k for w in kw):
                    try: return float(str(v).replace(",", "").replace("%", "").strip())
                    except: continue
            return None

        has_any = len(merged) > 0
        # الحقول الخام (تطابق صفحة الإدخال)
        inv_value = pick("قيمة المخزون")
        cogs_period = pick("تكلفة البضاعة المباعة")
        stockouts = pick("مرات نفاد")
        skus = pick("عدد الأصناف")
        slow_items = pick("أصناف بطيئة")
        obsolete = pick("أصناف راكدة")
        stock_count = pick("عدد وحدات المخزون")

        # المؤشرات المشتقّة
        turnover = round(cogs_period / inv_value, 1) if (cogs_period and inv_value and inv_value > 0) else None
        dio = round(inv_value / cogs_period * 30) if (cogs_period and inv_value and cogs_period > 0) else None
        slow_pct = round(slow_items / skus * 100, 1) if (slow_items is not None and skus and skus > 0) else None

        metrics = []
        metrics.append({"key":"inv_value","icon":"📦","label":"قيمة المخزون الحالية","value":round(inv_value) if inv_value is not None else None,"unit":"ريال","has_data":inv_value is not None,"status":None})
        metrics.append({"key":"turnover","icon":"🔄","label":"معدل دوران المخزون","value":turnover,"unit":"مرة","has_data":turnover is not None,
                        "status":("good" if (turnover and turnover>=6) else ("warn" if (turnover and turnover>=3) else "bad")) if turnover is not None else None})
        metrics.append({"key":"dio","icon":"📅","label":"أيام بقاء المخزون (DIO)","value":dio,"unit":"يوم","has_data":dio is not None,
                        "status":("good" if (dio is not None and dio<45) else ("warn" if (dio is not None and dio<90) else "bad")) if dio is not None else None})
        metrics.append({"key":"stockouts","icon":"⚠️","label":"مرات نفاد المخزون","value":int(stockouts) if stockouts is not None else None,"unit":"مرة","has_data":stockouts is not None,
                        "status":("good" if (stockouts is not None and stockouts<3) else ("warn" if (stockouts is not None and stockouts<8) else "bad")) if stockouts is not None else None})
        metrics.append({"key":"skus","icon":"🏷️","label":"عدد الأصناف","value":int(skus) if skus is not None else None,"unit":"صنف","has_data":skus is not None,"status":None})
        metrics.append({"key":"slow","icon":"🐌","label":"الأصناف بطيئة الحركة","value":slow_pct,"unit":"%","has_data":slow_pct is not None,
                        "status":("good" if (slow_pct is not None and slow_pct<10) else ("warn" if (slow_pct is not None and slow_pct<25) else "bad")) if slow_pct is not None else None})
        metrics.append({"key":"obsolete","icon":"🗑️","label":"الأصناف الراكدة","value":int(obsolete) if obsolete is not None else None,"unit":"صنف","has_data":obsolete is not None,"status":None})
        metrics.append({"key":"stock_count","icon":"📊","label":"إجمالي وحدات المخزون","value":int(stock_count) if stock_count is not None else None,"unit":"وحدة","has_data":stock_count is not None,"status":None})

        # ===== مؤشرات مؤسسية عميقة (من تقرير التدقيق) =====
        gross_margin_inv = pick("هامش إجمالي", "ربحية المخزون")
        reorder = pick("نقطة إعادة الطلب", "reorder")
        lead_time = pick("مدة التوريد", "lead time", "زمن التوريد")
        overstock = pick("مخزون زائد", "overstock", "فائض")
        # GMROI = هامش الربح الإجمالي ÷ متوسط تكلفة المخزون
        gmroi = None
        if gross_margin_inv is not None and inv_value and inv_value > 0:
            gmroi = round(gross_margin_inv / inv_value * 100, 1)
        elif cogs_period and inv_value and inv_value > 0 and turnover:
            # تقدير: هامش × دوران
            gmroi = round((1 - 0.6) * turnover, 1)  # افتراض هامش 40%

        # GMROI (ربحية الاستثمار في المخزون)
        metrics.append({"key":"gmroi","icon":"💎","label":"عائد الاستثمار في المخزون (GMROI)","value":gmroi,"unit":"","has_data":gmroi is not None,
                        "status":("good" if (gmroi and gmroi>=3) else ("warn" if (gmroi and gmroi>=1.5) else "bad")) if gmroi is not None else None})
        # المخزون الزائد (Overstock)
        overstock_pct = round(overstock / skus * 100, 1) if (overstock is not None and skus and skus > 0) else None
        metrics.append({"key":"overstock","icon":"📈","label":"المخزون الزائد","value":overstock_pct,"unit":"%","has_data":overstock_pct is not None,
                        "status":("good" if (overstock_pct is not None and overstock_pct<10) else "warn") if overstock_pct is not None else None})
        # نقطة إعادة الطلب
        metrics.append({"key":"reorder","icon":"🔔","label":"نقطة إعادة الطلب","value":int(reorder) if reorder is not None else None,"unit":"وحدة","has_data":reorder is not None,"status":None})
        # مدة التوريد (Lead Time)
        metrics.append({"key":"lead_time","icon":"🚚","label":"مدة التوريد","value":round(lead_time,1) if lead_time is not None else None,"unit":"يوم","has_data":lead_time is not None,
                        "status":("good" if (lead_time is not None and lead_time<7) else "warn") if lead_time is not None else None})

        # ===== تصنيف ABC (تحليل باريتو للأصناف) =====
        abc = None
        if skus and slow_items is not None and obsolete is not None:
            fast = skus - (slow_items or 0) - (obsolete or 0)
            abc = {
                "a": {"count": int(fast), "pct": round(fast/skus*100), "label": "أصناف A (سريعة — 80% من القيمة)"},
                "b": {"count": int(slow_items or 0), "pct": round((slow_items or 0)/skus*100), "label": "أصناف B (متوسطة)"},
                "c": {"count": int(obsolete or 0), "pct": round((obsolete or 0)/skus*100), "label": "أصناف C (راكدة — مرشّحة للتصفية)"},
            }

        filled = sum(1 for m in metrics if m["has_data"])
        return {
            "company": {"name": company.name},
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "has_any_data": has_any,
            "metrics": metrics,
            "abc_analysis": abc,
            "filled_count": filled,
            "total_count": len(metrics),
        }


@app.get("/company/ops-analytics")
def company_ops_analytics(user: User = Depends(get_current_user)):
    """وحدة العمليات الشاملة (المرحلة ٢): كفاءة، تسليم، جودة، SLA، طاقة.
    كل مؤشر has_data — يميّز نقص البيانات عن ضعف الأداء (بلا اختراع)."""
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    with Session(engine) as s:
        company = s.get(Company, user.company_id)
        if not company or (not check_permission(get_user_role(s, user), "ops", "view") and get_user_role(s, user) != "owner"):
            raise HTTPException(403, "غير مصرّح — وحدة العمليات")
        if company.is_active != 1:
            raise HTTPException(402, "شركتك قيد التفعيل")

        ops_entries = s.exec(
            select(CompanyModuleEntry).where(
                CompanyModuleEntry.company_id == company.id,
                CompanyModuleEntry.module == "ops",
            ).order_by(CompanyModuleEntry.created_at.desc())
        ).all()
        merged = {}
        for oe in ops_entries:
            try: merged.update(json.loads(oe.data) if oe.data else {})
            except: pass

        def pick(*kw):
            for k, v in merged.items():
                kl = str(k).lower()
                if any(w in k or w in kl for w in kw):
                    try: return float(str(v).replace(",", "").replace("%", "").strip())
                    except: continue
            return None

        has_any = len(merged) > 0
        # نطابق أسماء الحقول الفعلية في صفحة الإدخال (company-ops)
        active = pick("النشطة", "المشاريع/الطلبات النشطة")
        completed = pick("المكتملة", "المنجزة")
        delayed = pick("المتأخرة")
        on_time_count = pick("المنفّذة في الوقت", "في الوقت المحدد")
        proc_time = pick("وقت معالجة", "متوسط وقت")
        daily_prod = pick("الإنتاجية اليومية", "الإنتاجية")
        faults = pick("عدد الأعطال", "الأعطال")
        critical_faults = pick("الأعطال الحرجة")
        downtime = pick("ساعات التوقف", "التوقف")

        # نشتق المؤشرات من الحقول الخام
        on_time = None
        if on_time_count is not None and completed and completed > 0:
            on_time = round(min(on_time_count / completed * 100, 100), 1)
        elif completed is not None and delayed is not None and (completed + delayed) > 0:
            on_time = round(completed / (completed + delayed) * 100, 1)
        # دقة/جودة من العيوب
        defect = None
        if critical_faults is not None and completed and completed > 0:
            defect = round(critical_faults / completed * 100, 1)
        accuracy = round(100 - defect, 1) if defect is not None else None
        fulfillment = proc_time
        capacity = None  # يحتاج بيانات طاقة قصوى — نتركه للإدخال المستقبلي
        sla = on_time  # التزام SLA ≈ نسبة التسليم في الوقت
        productivity = daily_prod

        metrics = []
        # ① كفاءة تشغيلية شاملة (نحسبها من المتوفّر)
        eff_parts = [x for x in [on_time, accuracy, (100-defect if defect is not None else None), sla] if x is not None]
        overall_eff = round(sum(eff_parts)/len(eff_parts)) if eff_parts else None
        metrics.append({"key":"efficiency","icon":"⚙️","label":"الكفاءة التشغيلية","value":overall_eff,"unit":"/100",
                        "has_data":overall_eff is not None,
                        "status":("good" if (overall_eff and overall_eff>=80) else ("warn" if (overall_eff and overall_eff>=60) else "bad")) if overall_eff else None})
        # ② التسليم في الوقت
        metrics.append({"key":"ontime","icon":"⏱️","label":"التسليم في الوقت","value":round(on_time,1) if on_time is not None else None,"unit":"%",
                        "has_data":on_time is not None,"status":("good" if (on_time and on_time>=90) else ("warn" if (on_time and on_time>=75) else "bad")) if on_time is not None else None})
        # ③ دقة الطلبات
        metrics.append({"key":"accuracy","icon":"✅","label":"دقة الطلبات","value":round(accuracy,1) if accuracy is not None else None,"unit":"%",
                        "has_data":accuracy is not None,"status":("good" if (accuracy and accuracy>=95) else ("warn" if (accuracy and accuracy>=85) else "bad")) if accuracy is not None else None})
        # ④ زمن التنفيذ
        metrics.append({"key":"fulfillment","icon":"🚀","label":"زمن التنفيذ","value":round(fulfillment,1) if fulfillment is not None else None,"unit":"ساعة",
                        "has_data":fulfillment is not None,"status":None})
        # ⑤ معدل العيوب
        metrics.append({"key":"defect","icon":"⚠️","label":"معدل العيوب","value":round(defect,1) if defect is not None else None,"unit":"%",
                        "has_data":defect is not None,"status":("good" if (defect is not None and defect<3) else ("warn" if (defect is not None and defect<8) else "bad")) if defect is not None else None})
        # ⑥ استغلال الطاقة
        metrics.append({"key":"capacity","icon":"📊","label":"استغلال الطاقة","value":round(capacity,1) if capacity is not None else None,"unit":"%",
                        "has_data":capacity is not None,"status":("good" if (capacity and 70<=capacity<=90) else "warn") if capacity is not None else None})
        # ⑦ التزام SLA
        metrics.append({"key":"sla","icon":"📋","label":"التزام SLA","value":round(sla,1) if sla is not None else None,"unit":"%",
                        "has_data":sla is not None,"status":("good" if (sla and sla>=90) else ("warn" if (sla and sla>=75) else "bad")) if sla is not None else None})
        # ⑧ الإنتاجية
        metrics.append({"key":"productivity","icon":"📈","label":"الإنتاجية","value":round(productivity,1) if productivity is not None else None,"unit":"%",
                        "has_data":productivity is not None,"status":None})
        # ⑨ وقت التوقف
        metrics.append({"key":"downtime","icon":"🔧","label":"وقت التوقف","value":round(downtime,1) if downtime is not None else None,"unit":"ساعة",
                        "has_data":downtime is not None,"status":("good" if (downtime is not None and downtime<5) else "warn") if downtime is not None else None})

        # ===== مؤشرات عميقة (من تقرير التدقيق) =====
        wait_time = pick("وقت الانتظار", "الانتظار")
        complaints_ops = pick("الشكاوى", "شكاوى")
        returns_ops = pick("المرتجعات", "مرتجعات")
        # المبيعات لكل موظف/وردية (من الطلبات المكتملة)
        # تكلفة عدم التصحيح (Cost of Poor Quality) — الأثر المالي للهدر
        # نحسبها من: معدل العيوب × متوسط قيمة الطلب × عدد الطلبات
        cost_of_waste = None
        if defect is not None and completed and completed > 0:
            # نجيب متوسط قيمة الطلب من بيانات المبيعات
            _branches = s.exec(select(CompanyBranch).where(CompanyBranch.company_id == company.id, CompanyBranch.is_active == 1)).all()
            _total_sales = 0
            for _b in _branches:
                _e = s.exec(select(CompanyEntry).where(CompanyEntry.branch_id == _b.id).order_by(CompanyEntry.created_at.desc())).first()
                if _e: _total_sales += _e.sales or 0
            if _total_sales > 0 and completed > 0:
                avg_order = _total_sales / completed
                # تكلفة العيوب = نسبة العيوب × المبيعات (تقدير محافظ)
                cost_of_waste = round(defect / 100 * _total_sales)

        # ⑩ وقت الانتظار
        metrics.append({"key":"wait","icon":"⏳","label":"وقت الانتظار","value":round(wait_time,1) if wait_time is not None else None,"unit":"دقيقة",
                        "has_data":wait_time is not None,"status":("good" if (wait_time is not None and wait_time<10) else "warn") if wait_time is not None else None})
        # ⑪ المرتجعات
        return_rate = round(returns_ops / completed * 100, 1) if (returns_ops is not None and completed and completed > 0) else None
        metrics.append({"key":"returns","icon":"↩️","label":"معدل المرتجعات","value":return_rate,"unit":"%",
                        "has_data":return_rate is not None,"status":("good" if (return_rate is not None and return_rate<3) else "warn") if return_rate is not None else None})

        filled = sum(1 for m in metrics if m["has_data"])
        return {
            "company": {"name": company.name},
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "cost_of_waste": cost_of_waste,
            "currency": company.currency or "SAR",
            "has_any_data": has_any,
            "metrics": metrics,
            "filled_count": filled,
            "total_count": len(metrics),
        }


@app.get("/company/hr-analytics")
def company_hr_analytics(user: User = Depends(get_current_user)):
    """وحدة الموارد البشرية الشاملة (المرحلة ٢): ١٠ مؤشرات HR.
    كل مؤشر يحمل has_data — يميّز نقص البيانات عن ضعف الأداء (بلا اختراع)."""
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    with Session(engine) as s:
        company = s.get(Company, user.company_id)
        if not company:
            raise HTTPException(403, "غير مصرّح")
        role = get_user_role(s, user)
        if not check_permission(role, "hr", "view") and role != "owner":
            raise HTTPException(403, "غير مصرّح — وحدة الموارد البشرية")
        if company.is_active != 1:
            raise HTTPException(402, "شركتك قيد التفعيل")

        # نجمع كل بيانات HR (شركة + فروع)
        hr_entries = s.exec(
            select(CompanyModuleEntry).where(
                CompanyModuleEntry.company_id == company.id,
                CompanyModuleEntry.module == "hr",
            ).order_by(CompanyModuleEntry.created_at.desc())
        ).all()

        # نستخرج القيم بمرونة من كل الإدخالات
        def _pick(d, *keywords):
            for k, v in d.items():
                kl = str(k).lower()
                if any(kw in k or kw in kl for kw in keywords):
                    try:
                        return float(str(v).replace(",", "").replace("%", "").strip())
                    except Exception:
                        continue
            return None

        merged = {}
        for he in hr_entries:
            try:
                merged.update(json.loads(he.data) if he.data else {})
            except Exception:
                pass

        # إجمالي مبيعات الشركة (لحساب الإيراد/موظف)
        total_sales = 0.0
        total_profit = 0.0
        for b in s.exec(select(CompanyBranch).where(CompanyBranch.company_id == company.id, CompanyBranch.is_active == 1)).all():
            e = s.exec(select(CompanyEntry).where(CompanyEntry.branch_id == b.id).order_by(CompanyEntry.created_at.desc())).first()
            if e:
                total_sales += e.sales or 0
                total_profit += e.profit or 0

        has_any = len(merged) > 0
        employees = _pick(merged, "موظف", "عدد", "employee", "headcount", "staff")
        turnover = _pick(merged, "دوران", "turnover", "استقالة")
        new_hires = _pick(merged, "توظيف", "تعيين", "hire", "recruit")
        salary_cost = _pick(merged, "رواتب", "salary", "أجور", "payroll")
        engagement = _pick(merged, "رضا", "engagement", "ارتباط", "معنويات")
        training_hrs = _pick(merged, "تدريب", "training", "تطوير", "development")
        high_perf = _pick(merged, "متميز", "high", "ممتاز")
        avg_perf = _pick(merged, "متوسط", "average", "avg")
        low_perf = _pick(merged, "ضعيف", "low", "منخفض")
        time_to_hire = _pick(merged, "وقت التوظيف", "time to hire", "مدة التعيين")
        flight_risk = _pick(merged, "مخاطر ترك", "flight risk", "خطر مغادرة")

        metrics = []
        # ① ملخص القوى العاملة
        metrics.append({"key": "workforce", "icon": "👥", "label": "إجمالي القوى العاملة",
            "value": int(employees) if employees else None, "unit": "موظف",
            "has_data": employees is not None})
        # ② الإيراد لكل موظف (KPI رئيسي)
        rev_per_emp = round(total_sales / employees) if (employees and employees > 0 and total_sales > 0) else None
        metrics.append({"key": "rev_per_emp", "icon": "💰", "label": "الإيراد لكل موظف",
            "value": rev_per_emp, "unit": "ريال",
            "has_data": rev_per_emp is not None})
        # ③ الربح لكل موظف
        profit_per_emp = round(total_profit / employees) if (employees and employees > 0 and total_profit) else None
        metrics.append({"key": "profit_per_emp", "icon": "📈", "label": "الربح لكل موظف",
            "value": profit_per_emp, "unit": "ريال",
            "has_data": profit_per_emp is not None})
        # ④ معدل الدوران
        metrics.append({"key": "turnover", "icon": "🔄", "label": "معدل دوران الموظفين",
            "value": round(turnover, 1) if turnover is not None else None, "unit": "%",
            "has_data": turnover is not None,
            "status": ("good" if (turnover is not None and turnover < 10) else ("warn" if (turnover is not None and turnover < 20) else "bad")) if turnover is not None else None})
        # ⑤ نسبة تكلفة الرواتب (حقل حسّاس — يظهر فقط للأدوار المصرّح لها)
        salary_ratio = round(salary_cost / total_sales * 100, 1) if (salary_cost and total_sales > 0) else None
        # أمان مستوى العمود: نخفي الرواتب عن غير المصرّح (مدير الفرع، الموظف)
        if not can_see_sensitive_financials(role):
            salary_ratio = None
        metrics.append({"key": "salary_ratio", "icon": "💵", "label": "نسبة تكلفة الرواتب من الإيرادات",
            "value": salary_ratio, "unit": "%",
            "has_data": salary_ratio is not None,
            "restricted": (not can_see_sensitive_financials(role))})
        # ⑥ درجة الرضا الوظيفي (engagement)
        metrics.append({"key": "engagement", "icon": "❤️", "label": "درجة الرضا الوظيفي",
            "value": round(engagement, 1) if engagement is not None else None, "unit": "/5" if (engagement and engagement <= 5) else "%",
            "has_data": engagement is not None})
        # ⑦ ساعات التدريب والتطوير
        metrics.append({"key": "training", "icon": "🎓", "label": "ساعات التدريب والتطوير",
            "value": round(training_hrs) if training_hrs is not None else None, "unit": "ساعة",
            "has_data": training_hrs is not None})
        # ⑧ التوظيف (تعيينات جديدة + وقت التوظيف)
        metrics.append({"key": "hiring", "icon": "🆕", "label": "التعيينات الجديدة",
            "value": int(new_hires) if new_hires is not None else None, "unit": "موظف",
            "has_data": new_hires is not None,
            "extra": (f"وقت التوظيف {round(time_to_hire)} يوم" if time_to_hire is not None else None)})
        # ⑨ توزيع الأداء (متميز/متوسط/ضعيف)
        perf_data = high_perf is not None or avg_perf is not None or low_perf is not None
        metrics.append({"key": "performance", "icon": "⭐", "label": "توزيع الأداء",
            "value": None, "has_data": perf_data,
            "distribution": {"high": int(high_perf) if high_perf else 0,
                             "avg": int(avg_perf) if avg_perf else 0,
                             "low": int(low_perf) if low_perf else 0} if perf_data else None})
        # ⑩ مخاطر ترك العمل (flight risk / succession)
        metrics.append({"key": "flight_risk", "icon": "⚠️", "label": "مخاطر ترك العمل",
            "value": int(flight_risk) if flight_risk is not None else None, "unit": "موظف",
            "has_data": flight_risk is not None})

        filled = sum(1 for m in metrics if m["has_data"])
        return {
            "company": {"name": company.name},
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "has_any_data": has_any,
            "metrics": metrics,
            "filled_count": filled,
            "total_count": len(metrics),
        }


@app.get("/company/data-quality")
def company_data_quality(user: User = Depends(get_current_user)):
    """يحسب جودة بيانات كل وحدة + جودة شاملة + فحص القواعد المنطقية."""
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

        # ===== فحص القواعد المنطقية على بيانات الفروع =====
        all_entries = []
        for b in s.exec(select(CompanyBranch).where(CompanyBranch.company_id == company.id, CompanyBranch.is_active == 1)).all():
            all_entries.extend(s.exec(select(CompanyEntry).where(CompanyEntry.branch_id == b.id)).all())
        rules_score, rules_flags = check_data_quality_rules(all_entries)

        # الجودة النهائية تدمج الاكتمال والقواعد المنطقية
        combined_quality = round(overall * 0.6 + rules_score * 0.4) if all_entries else overall
        confidence = compute_confidence_flag(combined_quality)

        return {
            "company": {"name": company.name},
            "overall_quality": combined_quality,
            "overall_status": overall_status,
            "overall_level": overall_level,
            "basics": {"quality": basics_quality, "branches_with_data": branches_with_data, "branches_total": branches_total},
            "modules": modules,
            "rules": {"score": rules_score, "flags": rules_flags[:12], "flags_count": len(rules_flags)},
            "confidence": confidence,
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
        save_memory(company.id, "goals", f"تحديث أهداف {entry.period}",
                    " | ".join(f"{k}: {v}" for k, v in cleaned.items()))
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

        # درجة التحقيق الاستراتيجي الإجمالية (متوسط نسب الإنجاز)
        achievements = []
        for c in comparisons:
            if c.get("goal", 0) > 0:
                pct = min(round(c["actual"] / c["goal"] * 100), 100) if c["label"] != "سقف المصروفات" else min(round(c["goal"] / max(c["actual"], 1) * 100), 100)
                achievements.append(pct)
        overall_achievement = round(sum(achievements) / len(achievements)) if achievements else 0

        return {
            "company": {"name": company.name},
            "period": period,
            "goals": goals,
            "comparisons": comparisons,
            "has_goals": len(goals) > 0,
            "overall_achievement": overall_achievement,
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


# ============================================================
# ===== القطاعات الذكية: KPIs ومصطلحات وتنبيهات وتوصيات لكل قطاع =====
# نبّاه يتكيّف مع قطاعك — لا "نبّاه لكل القطاعات"
# ============================================================
SECTOR_INTELLIGENCE = {
    "fnb": {
        "kpis": ["متوسط قيمة الطلب", "تكلفة المكوّنات %", "معدل تكرار الزيارة", "هدر المطبخ", "الطلبات في الذروة"],
        "terms": "الطلب، الطاولة، المكوّنات، الهدر، الذروة، التوصيل، الوجبة",
        "watch": "ارتفاع تكلفة المكوّنات فوق 35%، هدر مطبخ فوق 5%، اعتماد مفرط على تطبيقات التوصيل وعمولاتها",
        "advice": "ركّز على متوسط قيمة الطلب (upselling)، اضبط تكلفة المكوّنات، وقلّل الهدر. راقب عمولات التوصيل التي قد تبتلع الهامش.",
    },
    "retail": {
        "kpis": ["معدل دوران المخزون", "المخزون الراكد", "المبيعات لكل متر²", "متوسط سلة الشراء", "معدل التحويل"],
        "terms": "المخزون، الصنف، الدوران، السلة، الراكد، التخفيضات، الموسم",
        "watch": "مخزون راكد يحتجز رأس المال، تخفيضات تأكل الهامش، أصناف بطيئة الحركة",
        "advice": "حرّك المخزون الراكد بعروض مدروسة، ركّز على الأصناف عالية الدوران، وحسّن متوسط سلة الشراء.",
    },
    "ecommerce": {
        "kpis": ["تكلفة اكتساب العميل", "معدل التخلي عن السلة", "القيمة الدائمة للعميل", "معدل الإرجاع", "معدل التحويل"],
        "terms": "الزيارة، السلة، التحويل، الشحن، الإرجاع، الاكتساب، الحملة",
        "watch": "تكلفة اكتساب أعلى من قيمة العميل، معدل تخلّي عن السلة مرتفع، إرجاعات كثيرة",
        "advice": "قلّل تكلفة الاكتساب مقابل القيمة الدائمة، عالج التخلي عن السلة، وخفّض معدل الإرجاع بوصف أدق.",
    },
    "manufacturing": {
        "kpis": ["كفاءة الإنتاج", "تكلفة الوحدة", "معدل الهدر", "استغلال الطاقة الإنتاجية", "زمن الدورة"],
        "terms": "الإنتاج، الوحدة، الخط، الطاقة، الهدر، المواد الخام، الدورة",
        "watch": "طاقة إنتاجية غير مستغلة، هدر مواد خام، ارتفاع تكلفة الوحدة",
        "advice": "ارفع استغلال الطاقة الإنتاجية، اخفض هدر المواد، وحلّل تكلفة الوحدة لكل خط.",
    },
    "contracting": {
        "kpis": ["هامش المشروع", "نسبة الإنجاز", "التدفق النقدي للمشروع", "الدفعات المستحقة", "تجاوز التكلفة"],
        "terms": "المشروع، الدفعة، المستخلص، الإنجاز، المقاول، التكلفة، المستحقات",
        "watch": "تجاوز تكلفة المشاريع، دفعات متأخرة تضغط السيولة، هامش مشروع منخفض",
        "advice": "راقب التدفق النقدي لكل مشروع، حصّل الدفعات في وقتها، واضبط تجاوز التكلفة مبكراً.",
    },
    "distribution": {
        "kpis": ["تكلفة التوزيع", "معدل دوران المخزون", "كفاءة الأسطول", "الطلبات المكتملة", "زمن التسليم"],
        "terms": "التوزيع، الأسطول، الطلب، المستودع، التسليم، الوكيل، المخزون",
        "watch": "تكلفة توزيع مرتفعة، أسطول غير مستغل، تأخر تسليم",
        "advice": "حسّن كفاءة الأسطول ومسارات التوزيع، اضبط تكلفة التسليم، وقلّل زمن التوريد.",
    },
    "services": {
        "kpis": ["الإيراد لكل موظف", "معدل استغلال الفريق", "رضا العملاء", "معدل الاحتفاظ", "قيمة العقد"],
        "terms": "الخدمة، العقد، الموظف، الساعة، الاستغلال، العميل، الاحتفاظ",
        "watch": "استغلال منخفض للفريق، اعتماد على عملاء قلائل، تسرّب عملاء",
        "advice": "ارفع استغلال الفريق، نوّع قاعدة العملاء، وركّز على الاحتفاظ والقيمة الدائمة.",
    },
    "clinics": {
        "kpis": ["معدل إشغال المواعيد", "الإيراد لكل مريض", "معدل عودة المرضى", "التحصيل من التأمين", "لا-حضور"],
        "terms": "الموعد، المريض، الإشغال، التأمين، الكشف، المتابعة، العيادة",
        "watch": "مواعيد شاغرة، تأخر تحصيل التأمين، نسبة عدم حضور مرتفعة",
        "advice": "ارفع إشغال المواعيد، سرّع تحصيل التأمين، وقلّل عدم الحضور بتذكيرات.",
    },
    "hospitals": {
        "kpis": ["معدل إشغال الأسرّة", "الإيراد لكل سرير", "متوسط مدة الإقامة", "التحصيل من التأمين", "معدل الإشغال"],
        "terms": "السرير، المريض، الإقامة، التأمين، القسم، الإشغال، الطوارئ",
        "watch": "أسرّة شاغرة، تأخر تحصيل التأمين، مدة إقامة أطول من المعتاد",
        "advice": "حسّن إشغال الأسرّة، اضبط مدة الإقامة، وسرّع دورة التحصيل من التأمين.",
    },
    "logistics": {
        "kpis": ["تكلفة الشحنة", "كفاءة الأسطول", "زمن التسليم", "الشحنات في الوقت", "استغلال السعة"],
        "terms": "الشحنة، الأسطول، التسليم، المسار، السعة، المستودع، التتبع",
        "watch": "تكلفة شحن مرتفعة، تأخر تسليم، سعة غير مستغلة",
        "advice": "حسّن كفاءة المسارات والأسطول، اضبط تكلفة الشحنة، وارفع نسبة التسليم في الوقت.",
    },
    "other": {
        "kpis": ["هامش الربح", "النمو الشهري", "نسبة المصروفات", "التدفق النقدي", "الإيراد"],
        "terms": "الإيراد، المصروف، الربح، النمو، السيولة",
        "watch": "تراجع الهامش، ارتفاع المصروفات، ضغط السيولة",
        "advice": "راقب الهامش والمصروفات والتدفق النقدي، وركّز على أكبر بند مؤثّر.",
    },
}


def build_sector_context(sector: str) -> str:
    """يبني وصفاً لذكاء القطاع يُحقن في التحليل ليتكيّف مع طبيعة النشاط."""
    si = SECTOR_INTELLIGENCE.get(sector, SECTOR_INTELLIGENCE["other"])
    name = SECTOR_NAMES.get(sector, "النشاط")
    return (
        f"\n\n# ذكاء القطاع ({name}) — كيّف تحليلك مع طبيعة هذا النشاط:\n"
        f"- مؤشرات الأداء الأهم لهذا القطاع: {'، '.join(si['kpis'])}\n"
        f"- مصطلحات القطاع (استخدمها في لغتك): {si['terms']}\n"
        f"- ما يجب مراقبته في هذا القطاع تحديداً: {si['watch']}\n"
        f"- توجيه التوصيات: {si['advice']}\n"
        f"استخدم مؤشرات ومصطلحات هذا القطاع تحديداً — لا تعطِ تحليلاً عاماً يصلح لأي نشاط."
    )



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
        margin_vals = [e.margin for e in rows if e.sales > 0 and e.expenses > 0]
        repeat_vals = [e.repeat_rate for e in rows if e.repeat_rate > 0]
        growth_vals = [e.growth for e in rows if e.growth != 0]

        actuals = {
            "margin": round(sum(margin_vals)/len(margin_vals), 1) if margin_vals else 0,
            "repeat_rate": round(sum(repeat_vals)/len(repeat_vals), 1) if repeat_vals else 0,
            "growth": round(sum(growth_vals)/len(growth_vals), 1) if growth_vals else 0,
            "expense_ratio": round((total_expenses/total_sales)*100, 1) if total_sales else 0,
        }

        comparisons = []
        _no_expenses = total_expenses <= 0
        for key, b in bench.items():
            if key in ("margin", "expense_ratio") and _no_expenses:
                continue  # المصروفات غير مدخلة — مقارنتها مضلّلة
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
        if total_expenses > 0 and avg_margin < bench_margin - 3:
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
        if total_expenses > 0 and exp_ratio > bench_exp + 5:
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
                    "recommendation": "افتح صفحة تحليل هدر الإيرادات وراجع التفاصيل.",
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


# ============================================================
# ===== مركز الرفع الذكي: Excel/CSV → كل الوحدات =====
# ============================================================

# قاموس التطابق الذكي: مفتاح القاموس = الحقل القياسي عندنا، القيم = أسماء محتملة
SMART_FIELD_MAP = {
    # ===== أعمدة معاملات POS الخام (يلخّصها نبّاه تلقائياً) =====
    "tx_date":          ["date","تاريخ","التاريخ","تاريخ المعاملة","trans date","transaction date","order date","تاريخ الطلب","تاريخ الفاتورة","invoice date","posting date","تاريخ الحركة","event_time","created_at","timestamp"],
    "tx_branch":        ["branch","الفرع","فرع","branch name","store","المتجر","location","الموقع","outlet","المنفذ","site","الفرع/المتجر","store name","store_name","location_name"],
    "tx_amount":        ["totalamount","total","amount","الإجمالي","إجمالي","قيمة الفاتورة","total amount","grand total","المبلغ الإجمالي","net","صافي","net_total","subtotal","المبلغ","total_price","order_total","invoice_total","الإجمالي مع الضريبة","بعد الضريبة","الصافي","المبلغ النهائي","total incl vat","المبلغ بعد الضريبة"],
    "tx_qty":           ["qty","quantity","الكمية","كمية","العدد","عدد","units","الوحدات"],
    "tx_unit_price":    ["unitprice","unit price","السعر","price","سعر","unit_price","price per unit","سعر الوحدة"],
    "tx_status":        ["status","الحالة","حالة","order status","payment status","transaction status","حالة الطلب"],
    "tx_customer":      ["customerid","customer id","رقم العميل","customer","العميل","client","الزبون","customer_id","client_id","العميل رقم","البريد الإلكتروني","email","الايميل","إيميل العميل","جوال العميل","رقم الجوال","phone"],
    "tx_product":       ["productid","product id","المنتج","product","رقم المنتج","product name","اسم المنتج","item","الصنف","sku","الفئة","category","التصنيف"],

    # ===== أعمدة كشف الحساب البنكي (يكشفها نبّاه ويقارنها بالمبيعات) =====
    "bank_credit":      ["إجمالي الإيداعات","اجمالي الايداعات","total deposits","credit","دائن","إيداع","الإيداع","إيداعات","deposit","deposits","credit amount","مبلغ دائن","وارد","له","إجمالي الإيداعات","اجمالي الايداعات","مجموع الإيداعات"],
    "bank_debit":       ["إجمالي السحوبات","اجمالي السحوبات","total withdrawals","debit","مدين","سحب","المسحوبات","withdrawal","debit amount","مبلغ مدين","صادر","عليه","خصم","إجمالي السحوبات","اجمالي السحوبات","مجموع السحوبات","إجمالي المسحوبات"],
    "bank_balance":     ["balance","الرصيد","رصيد","running balance","الرصيد المتاح"],
    "bank_desc":        ["description","الوصف","البيان","details","التفاصيل","narrative","تفاصيل العملية"],
    "bank_net":         ["صافي الحركة","net movement"],
    "bank_ops":         ["عدد المعاملات","عدد العمليات","transactions count","no of transactions"],

    # ===== أعمدة تقارير POS التفصيلية =====
    "tx_pre_tax":       ["المبلغ قبل الضريبة","قبل الضريبة","subtotal","pre-tax","pretax","amount before tax"],
    "tx_vat":           ["الضريبة","ضريبة القيمة المضافة","vat","tax amount","قيمة الضريبة"],
    "tx_discount":      ["الخصم","discount","خصم","قيمة الخصم"],
    "tx_payment_method":["طريقة الدفع","payment method","paymentmethod","وسيلة الدفع","نوع الدفع"],
    "tx_receipt":       ["رقم الإيصال","receipt","receipt no","رقم الفاتورة","رقم العملية","reference"],
    "tx_cashier":       ["كود الصراف","cashier","الكاشير","الموظف","اسم الموظف","كود الموظف"],
    "tx_terminal":      ["رقم الطرفية","terminal","terminal id","pos id"],
    "tx_time":          ["الوقت","time"],
    "tx_day":           ["اليوم","day","يوم الأسبوع"],
    "tx_notes":         ["ملاحظات","notes","ملاحظة","comment"],

    # ===== الفروع (ملخّصات) =====
    "branch_name":      ["branch","فرع","اسم الفرع","name","branch name","الفرع","store","المتجر","outlet","location"],
    "city":             ["city","مدينة","المدينة","town","المنطقة"],
    "period":           ["period","فترة","الفترة","month","شهر","الشهر","date","تاريخ","reporting period","report month","الفترة المحاسبية"],

    # ===== مبيعات أساسية =====
    "sales":            ["sales","مبيعات","إجمالي المبيعات","total sales","revenue","الإيرادات","المبيعات","gross sales","net sales","دخل","الدخل","total revenue","monthly sales","إيرادات","مبيعات الشهر","المداخيل"],
    "expenses":         ["expenses","مصروفات","المصروفات","cost","التكاليف","تكاليف","expenditures","الاصروف","total expenses","operating expenses","opex","المصاريف","الصرف"],
    "invoices":         ["invoices","فواتير","عدد الفواتير","orders","طلبات","عدد الطلبات","bills","transactions","number of orders","الطلبات","الفواتير","total orders"],
    "customers":        ["customers","عملاء","عدد العملاء","عدد الزبائن","clients","total customers","unique customers","الزبائن","العملاء"],
    "new_customers":    ["new customers","عملاء جدد","new","new clients","first time customers","العملاء الجدد"],
    "repeat_customers": ["repeat","عملاء متكررون","returning","repeat customers","loyal customers","المتكررون"],
    "discounts":        ["discounts","خصومات","الخصومات","discount","promotions","التخفيضات","العروض"],
    "deposited":        ["deposited","مودع","المبلغ المُودَع","bank deposit","الإيداع","cash deposited","الإيداعات البنكية","المودع بالبنك"],
    "top_products":     ["top products","أكثر مبيعاً","أكثر الأصناف","best sellers","المنتجات الأكثر","الأصناف الأعلى"],

    # ===== المالية =====
    "cogs":             ["cogs","تكلفة المبيعات","cost of goods","تكلفة البضاعة","cost of sales","تكلفة البضاعة المباعة","cost of revenue","direct costs","التكاليف المباشرة"],
    "salaries":         ["salaries","الرواتب","الراتب","payroll","رواتب","salary","wages","الأجور","staff cost","تكلفة الموظفين","إجمالي الرواتب","مرتبات"],
    "rent":             ["rent","إيجار","الإيجار","إيجارات","الإيجارات","rents","rental","monthly rent","الإيجار الشهري"],
    "marketing":        ["marketing","تسويق","التسويق","ads","الإعلانات","advertising","الترويج","تكاليف التسويق","marketing cost"],
    "utilities":        ["utilities","كهرباء","المرافق","electricity","الفواتير","water","الماء","الكهرباء والمياه"],
    "logistics_cost":   ["shipping","نقل","الشحن","logistics","التوصيل","delivery cost","تكلفة الشحن","مصاريف النقل"],
    "ar":               ["receivables","ذمم مدينة","المدينون","accounts receivable","a/r","المستحقات","العملاء المدينون","ذمم مدينة تجارية"],
    "ap":               ["payables","ذمم دائنة","الدائنون","accounts payable","a/p","الالتزامات","الموردون","ذمم دائنة تجارية"],
    "cash":             ["cash","النقد","نقد","البنك","cash and bank","cash on hand","الأرصدة النقدية","السيولة","النقدية","cash & bank"],
    "short_debt":       ["short term debt","ديون قصيرة","قروض قصيرة","current portion","الديون قصيرة الأجل"],
    "long_debt":        ["long term debt","ديون طويلة","قروض طويلة","الديون طويلة الأجل","long-term loans"],

    # ===== العملاء =====
    "lost_customers":   ["lost","مفقودون","عملاء مفقودون","churned","خسر","العملاء المفقودون","attrition"],
    "nps":              ["nps","صافي المرشحين","صافي مؤشر الترشيح","net promoter score","nps score"],
    "satisfaction":     ["satisfaction","رضا","نسبة الرضا","csat","customer satisfaction","رضا العملاء","satisfaction rate"],
    "complaints":       ["complaints","شكاوى","الشكاوى","complaint","عدد الشكاوى"],

    # ===== الموارد البشرية =====
    "employees":        ["employees","عدد الموظفين","الموظفين","staff","headcount","total employees","عدد العاملين","الطاقم"],
    "resignations":     ["resignations","المستقيلين","المستقيلون","quits","terminations","تركوا العمل","الاستقالات"],
    "absence":          ["absence","الغياب","غياب","absences","absent days","أيام الغياب"],
    "training":         ["training","التدريب","تدريب","training cost","ميزانية التدريب","training budget"],
    "saudization":      ["saudization","السعودة","نسبة السعودة","saudi ratio","السعوديين"],
    "vacancies":        ["vacancies","الشواغر","الوظائف الشاغرة","open positions","openings"],

    # ===== المخزون =====
    "inventory_value":  ["inventory value","قيمة المخزون","stock value","inventory","المخزون","stock","inventory total","قيمة المخزون الحالية"],
    "dead_stock":       ["dead stock","مخزون راكد","المخزون الراكد","المخزون الزائد","slow moving","excess inventory"],
    "stockouts":        ["stockouts","نفاد المخزون","نفاذ المخزون","out of stock","نواقص","stock out"],
    "waste":            ["waste","هدر","الهدر","الفاقد","spoilage","wastage","التالف","الفاقد بسبب التلف"],

    # ===== المشتريات =====
    "suppliers":        ["suppliers","الموردين","عدد الموردين","vendors","suppliers count","عدد الموردين النشطين"],
    "purchases":        ["purchases","المشتريات","قيمة المشتريات","total purchases","مشتريات","إجمالي المشتريات","procurement"],
    "supplier_delay":   ["supplier delay","تأخر المورد","lead time","supplier lead time","متوسط التأخر"],

    # ===== تشغيل =====
    "active_projects":  ["active projects","مشاريع نشطة","الطلبات النشطة","open orders","in progress","قيد التنفيذ"],
    "delayed_projects": ["delayed","متأخرة","الطلبات المتأخرة","late orders","overdue","المشاريع المتأخرة"],
    "on_time_orders":   ["on time","في الوقت","الطلبات في الوقت","on-time delivery","الالتزام بالمواعيد","تسليم في الموعد"],
    "breakdowns":       ["breakdowns","الأعطال","عدد الأعطال","failures","الأعطال الحرجة","downtime events"],

    # ===== إعدادات الشركة =====
    "cash_reserve":     ["cash reserve","الاحتياطي النقدي","reserves","الاحتياطي","احتياطي السيولة"],
    "monthly_obligations":["monthly obligations","الالتزامات الشهرية","monthly liabilities","fixed obligations","الالتزامات الثابتة"],
}


def _norm(s):
    """تطبيع للمقارنة: lowercase + إزالة فراغات وعلامات."""
    if s is None: return ""
    s = str(s).strip().lower()
    for junk in ("(sar)", "ر.س", "sar"):
        s = s.replace(junk, " ")
    s = s.replace("(ريال)", "").replace("(ر)", "").replace("ريال", "").replace("(يوم)", "")
    s = s.replace("%", "").replace("(", " ").replace(")", " ")
    return " ".join(s.split())


def match_column(header):
    """يطابق عنوان عمود مع حقل قياسي — بذكاء عالٍ."""
    h = _norm(header)
    if not h: return None
    # ١) تطابق دقيق
    for std, names in SMART_FIELD_MAP.items():
        for nm in names:
            if _norm(nm) == h:
                return std
    # ٢) تطابق احتواء (contains)
    for std, names in SMART_FIELD_MAP.items():
        for nm in names:
            n = _norm(nm)
            if n and len(n) >= 3 and (n in h or h in n):
                return std
    # ٣) تطابق كلمات (word-level) — كل كلمة من العنوان يبحث عنها
    words = set(h.split())
    for std, names in SMART_FIELD_MAP.items():
        for nm in names:
            n_words = set(_norm(nm).split())
            if n_words and len(n_words & words) >= max(1, len(n_words) // 2):
                return std
    return None


def infer_column_by_content(sample_values, header=""):
    """يستنتج معنى عمود من محتواه (لو الاسم غامض) — يفحص أول ٢٠ قيمة."""
    if not sample_values: return None
    clean = [v for v in sample_values[:20] if v is not None and str(v).strip() != ""]
    if not clean: return None
    # هل كلها أرقام؟
    nums = [_to_num(v) for v in clean]
    all_numeric = all(n is not None for n in nums)
    # هل كلها تواريخ؟
    dates = [parse_date_to_period(v) for v in clean]
    mostly_dates = sum(1 for d in dates if d) >= len(clean) * 0.7
    if mostly_dates:
        return "tx_date"
    # هل كلها نصوص قصيرة تُشبه أسماء فروع/مدن؟
    if not all_numeric:
        avg_len = sum(len(str(v).strip()) for v in clean) / len(clean)
        unique_ratio = len(set(str(v).strip() for v in clean)) / len(clean)
        # لو تكرار عالي + نصوص قصيرة → غالباً فرع/مدينة
        if avg_len < 25 and unique_ratio < 0.5:
            hh = _norm(header)
            if any(k in hh for k in ("city","مدين","location","موقع","منطقة")):
                return "city"
            return "tx_branch" if "branch" in hh or "فرع" in hh or "store" in hh else None
    # لو أرقام كبيرة (>1000) وبعنوان يحوي مبلغ/إجمالي — مبيعات
    if all_numeric:
        avg = sum(nums) / len(nums)
        hh = _norm(header)
        if avg > 100:
            if any(k in hh for k in ("total","amount","إجمالي","مبلغ","المبلغ","قيمة","sales","revenue")):
                return "tx_amount"
    return None


def find_header_row(all_rows, max_scan=15):
    """يبحث عن صف العناوين الحقيقي في أول ١٥ صف (الملفات الحقيقية فيها عناوين تقارير فوق).
    يرجع (index الصف، سنة مكتشفة من صفوف العنوان إن وجدت)."""
    import re as _re
    best_idx, best_score = 0, -1
    year_hint = None
    for i, row in enumerate(all_rows[:max_scan]):
        if not row:
            continue
        for c in row:
            if c is None:
                continue
            m = _re.search(r"(20\d{2})", str(c))
            if m and year_hint is None:
                year_hint = int(m.group(1))
        score = 0
        non_empty = 0
        for c in row:
            if c is None or str(c).strip() == "":
                continue
            non_empty += 1
            if match_column(str(c)):
                score += 1
        if score > best_score and score >= 2 and non_empty >= 3:
            best_idx, best_score = i, score
    if best_score < 2:
        best_idx = 0
    return best_idx, year_hint


def parse_csv(data_bytes):
    """يقرأ CSV ويرجع (headers, rows)."""
    # نحاول UTF-8 ثم cp1256 ثم latin-1
    for enc in ("utf-8-sig", "utf-8", "cp1256", "latin-1"):
        try:
            text = data_bytes.decode(enc)
            break
        except Exception: continue
    else:
        raise HTTPException(400, "تعذّر قراءة ترميز الملف — احفظه بصيغة UTF-8")
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows: return [], [], {"year_hint": None}
    hidx, year_hint = find_header_row(rows)
    headers = [str(c).strip() for c in rows[hidx]]
    return headers, rows[hidx+1:], {"year_hint": year_hint}


def parse_excel(data_bytes):
    """يقرأ Excel باستخدام openpyxl. لو غير مثبّت يرجع خطأ واضح."""
    try:
        import openpyxl
    except ImportError:
        raise HTTPException(400, "دعم Excel غير متوفّر — احفظ الملف كـ CSV ثم ارفعه")
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data_bytes), data_only=True, read_only=True)
        ws = wb.active
        all_rows = list(ws.iter_rows(values_only=True))
        if not all_rows: return [], [], {"year_hint": None}
        hidx, year_hint = find_header_row(all_rows)
        headers = [str(c).strip() if c is not None else "" for c in all_rows[hidx]]
        rows = [[c for c in r] for r in all_rows[hidx+1:] if any(c is not None for c in r)]
        return headers, rows, {"year_hint": year_hint}
    except Exception as e:
        _logger.error(f"فشل قراءة الملف: {type(e).__name__}: {str(e)[:200]}"); raise HTTPException(400, "تعذّر قراءة الملف. تأكّد أنه Excel أو CSV صالح.")


def _to_num(v):
    """يحوّل لرقم لو ممكن، وإلا يرجع None."""
    if v is None: return None
    if isinstance(v, (int, float)): return float(v)
    s = str(v).strip().replace(",", "").replace("ريال", "").replace("%", "")
    if not s: return None
    try: return float(s)
    except: return None


AR_MONTHS_MAP = {
    "يناير": 1, "فبراير": 2, "مارس": 3, "أبريل": 4, "ابريل": 4, "مايو": 5,
    "يونيو": 6, "يوليو": 7, "أغسطس": 8, "اغسطس": 8, "سبتمبر": 9,
    "أكتوبر": 10, "اكتوبر": 10, "نوفمبر": 11, "ديسمبر": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def parse_date_to_period(val, day_first=None, year_hint=None):
    """يحوّل أي تاريخ شائع لـ YYYY-MM.
    day_first=True → DD/MM/YYYY (النمط السعودي/البنكي)
    day_first=False → MM/DD/YYYY (النمط الأمريكي/POS)
    day_first=None → يخمّن (القيمة الأولى ≤12 تُعامل كشهر)"""
    if val is None: return None
    s = str(val).strip()
    if not s: return None
    # اسم شهر عربي/إنجليزي (مثل "يناير" أو "مارس 2024")
    s_low = s.lower()
    for mname, mnum in AR_MONTHS_MAP.items():
        if mname in s_low:
            import re as _re
            ym = _re.search(r"(20\d{2})", s)
            y = int(ym.group(1)) if ym else (year_hint or datetime.now().year)
            return f"{y:04d}-{mnum:02d}"
    # YYYY-MM-DD or YYYY/MM/DD or YYYY-MM
    if len(s) >= 7 and (s[4] in "-/"):
        try:
            y = int(s[:4]); m = int(s[5:7])
            if 1 <= m <= 12 and 2000 <= y <= 2100:
                return f"{y:04d}-{m:02d}"
        except: pass
    # MM/DD/YYYY أو DD/MM/YYYY أو M/D/YYYY
    parts = s.replace("-", "/").split("/")
    if len(parts) == 3:
        try:
            a, b, c = int(parts[0]), int(parts[1]), int(parts[2])
            if c > 1900:  # السنة في الآخر
                if day_first is True:
                    m = b if 1 <= b <= 12 else a
                elif day_first is False:
                    m = a if 1 <= a <= 12 else b
                else:
                    m = a if 1 <= a <= 12 else b
                y = c
                if 1 <= m <= 12 and 2000 <= y <= 2100:
                    return f"{y:04d}-{m:02d}"
            elif a > 1900:  # السنة في الأول
                m = b
                if 1 <= m <= 12:
                    return f"{a:04d}-{m:02d}"
        except: pass
    return None


def detect_date_format(rows, date_idx):
    """يفحص عيّنة تواريخ ويحدد: هل النمط DD/MM (سعودي) أم MM/DD (أمريكي)؟
    المنطق: لو أي قيمة أولى >12 فهي يوم أكيد → DD/MM. ولو أي قيمة ثانية >12 → MM/DD."""
    first_gt12 = second_gt12 = 0
    checked = 0
    for r in rows[:200]:
        if date_idx >= len(r) or r[date_idx] is None:
            continue
        s = str(r[date_idx]).strip().replace("-", "/")
        parts = s.split("/")
        if len(parts) != 3:
            continue
        try:
            a, b = int(parts[0]), int(parts[1])
            checked += 1
            if a > 12: first_gt12 += 1
            if b > 12: second_gt12 += 1
        except:
            continue
    if first_gt12 > 0 and second_gt12 == 0:
        return True    # اليوم أولاً (سعودي/بنكي)
    if second_gt12 > 0 and first_gt12 == 0:
        return False   # الشهر أولاً (أمريكي/POS)
    return None        # غامض — نستخدم الافتراضي


def is_transactions_file(col_map):
    """يكشف لو الملف ملف معاملات خام (يحتوي tx_date + tx_branch + tx_amount)."""
    cols = set(col_map.values())
    return "tx_date" in cols and "tx_branch" in cols and "tx_amount" in cols


def is_bank_statement(col_map):
    """يكشف لو الملف كشف حساب بنكي: تاريخ/شهر + (دائن أو مدين)، وبدون عمود فرع."""
    cols = set(col_map.values())
    has_date = "tx_date" in cols or "period" in cols
    has_bank_cols = "bank_credit" in cols or "bank_debit" in cols
    return has_date and has_bank_cols and "tx_branch" not in cols


def aggregate_bank_statement(rows, col_map, year_hint=None):
    """يلخّص كشف الحساب: إيداعات ومسحوبات شهرية.
    يفهم الكشوف التفصيلية (تاريخ) والملخصات الشهرية (عمود الشهر بأسماء عربية).
    يرجع {period: {"deposits": x, "withdrawals": y, "ops": n}}"""
    field_to_idx = {std: idx for idx, std in col_map.items()}
    date_idx = field_to_idx.get("tx_date", field_to_idx.get("period", -1))
    # كشوف البنوك السعودية غالباً DD/MM — نكشف النمط من البيانات نفسها
    day_first = detect_date_format(rows, date_idx)
    if day_first is None:
        day_first = True  # الافتراضي للكشوف البنكية: يوم/شهر
    months = {}
    for r in rows:
        if not r or all(c is None or str(c).strip() == "" for c in r):
            continue
        def get(field):
            idx = field_to_idx.get(field)
            if idx is None or idx >= len(r): return None
            return r[idx]
        date_val = get("tx_date")
        if date_val is None or str(date_val).strip() == "":
            date_val = get("period")
        period = parse_date_to_period(date_val, day_first=day_first, year_hint=year_hint)
        if not period:
            continue  # صفوف العناوين/الإجمالي السنوي تفشل هنا وتُتجاهل تلقائياً
        credit = _to_num(get("bank_credit")) or 0
        debit = _to_num(get("bank_debit")) or 0
        # بعض البنوك تضع المدين بإشارة سالبة في نفس العمود
        if credit < 0:
            debit += abs(credit); credit = 0
        if debit < 0:
            debit = abs(debit)
        b = months.setdefault(period, {"deposits": 0.0, "withdrawals": 0.0, "ops": 0})
        b["deposits"] += credit
        b["withdrawals"] += debit
        ops_val = _to_num(get("bank_ops"))
        b["ops"] += int(ops_val) if ops_val else 1
    return {p: {"deposits": round(v["deposits"], 2), "withdrawals": round(v["withdrawals"], 2), "ops": v["ops"]}
            for p, v in months.items()}


def aggregate_transactions(rows, col_map, headers, year_hint=None):
    """يلخّص معاملات POS الخام إلى صفوف شهرية بالفرع.
    يفهم: الإجمالي أو (قبل الضريبة + الضريبة)، الخصومات، الإيصالات الفريدة، وحالات الرفض."""
    field_to_idx = {std: idx for idx, std in col_map.items()}
    day_first = detect_date_format(rows, field_to_idx.get("tx_date", -1))
    if day_first is None:
        day_first = False  # الافتراضي لملفات POS: شهر/يوم
    aggregated = {}
    RETURNED = ("returned", "refunded", "مرتجع", "ملغي", "ملغية", "ملغاة", "cancelled", "canceled",
                "مرفوضة", "مرفوض", "rejected", "declined", "فاشلة", "failed")
    for r in rows:
        if not r or all(c is None or str(c).strip() == "" for c in r):
            continue
        def get(field):
            idx = field_to_idx.get(field)
            if idx is None or idx >= len(r): return None
            return r[idx]

        branch_name = str(get("tx_branch") or "").strip()
        period = parse_date_to_period(get("tx_date"), day_first=day_first, year_hint=year_hint)

        # المبلغ: الإجمالي أولاً، وإلا (قبل الضريبة + الضريبة)، وإلا قبل الضريبة
        amount = _to_num(get("tx_amount"))
        if amount is None:
            pre = _to_num(get("tx_pre_tax"))
            vat = _to_num(get("tx_vat"))
            if pre is not None:
                amount = pre + (vat or 0)

        status = str(get("tx_status") or "").strip().lower()
        customer = get("tx_customer")
        discount = _to_num(get("tx_discount")) or 0
        receipt = get("tx_receipt")

        if not branch_name or not period or amount is None:
            continue

        key = (branch_name, period)
        bucket = aggregated.setdefault(key, {
            "sales": 0.0, "invoices": 0, "customers_set": set(),
            "returned_amount": 0.0, "returns_count": 0,
            "discounts": 0.0, "receipts_set": set(),
        })

        is_returned = status in RETURNED
        if is_returned:
            bucket["returned_amount"] += amount
            bucket["returns_count"] += 1
        else:
            bucket["sales"] += amount
            bucket["discounts"] += discount
            if receipt is not None and str(receipt).strip():
                bucket["receipts_set"].add(str(receipt).strip())
            else:
                bucket["invoices"] += 1
            if customer is not None and str(customer).strip():
                bucket["customers_set"].add(str(customer).strip())

    result = {}
    for key, b in aggregated.items():
        # لو فيه أرقام إيصالات: عدد الفواتير = الإيصالات الفريدة (أدق)
        invoices = len(b["receipts_set"]) if b["receipts_set"] else b["invoices"]
        result[key] = {
            "sales": round(b["sales"], 2),
            "invoices": invoices,
            "customers": len(b["customers_set"]),
            "returned_amount": round(b["returned_amount"], 2),
            "returns_count": b["returns_count"],
            "discounts": round(b["discounts"], 2),
        }
    return result


@app.post("/company/upload-preview")
async def company_upload_preview(file: UploadFile = File(...), user: User = Depends(get_current_user)):
    """يقرأ الملف، يطابق الأعمدة، يرجع معاينة قبل الحفظ."""
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    with Session(engine) as s:
        company = s.get(Company, user.company_id)
        if not company or company.owner_id != user.id:
            raise HTTPException(403, "غير مصرّح")
        if company.is_active != 1:
            raise HTTPException(402, "شركتك قيد التفعيل")

    data = await file.read()
    # حماية: حد أعلى ٢٠ MB
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(400, f"الملف كبير جداً ({len(data)//(1024*1024)} MB). الحد الأقصى ٢٠ MB.")
    name = (file.filename or "").lower()
    if name.endswith(".csv"):
        headers, rows, fctx = parse_csv(data)
    elif name.endswith((".xlsx", ".xlsm")):
        headers, rows, fctx = parse_excel(data)
    else:
        raise HTTPException(400, "ادعم CSV أو Excel (.csv .xlsx) فقط")

    if not headers or not rows:
        raise HTTPException(400, "الملف فاضي أو لا يحوي صفوف")

    # حماية: حد ١٠٠ ألف صف
    if len(rows) > 100000:
        raise HTTPException(400, f"عدد الصفوف كبير جداً ({len(rows)}). الحد ١٠٠,٠٠٠ صف.")

    # تطابق الأعمدة (٣ مراحل: اسم، احتواء، تخمين من المحتوى)
    matched = []
    for i, h in enumerate(headers):
        std = match_column(h)
        # لو ما تطابق، جرّب التخمين من المحتوى
        if not std:
            sample_vals = [r[i] for r in rows[:20] if i < len(r)]
            std = infer_column_by_content(sample_vals, h)
        label = SMART_FIELD_MAP.get(std, [std])[0] if std else None
        matched.append({"index": i, "original": h, "matched": std, "label": label})
    matched_count = sum(1 for m in matched if m["matched"])

    # عيّنة ٥ صفوف بقيم منظّفة
    sample = []
    for r in rows[:5]:
        sample.append([(_to_num(c) if _to_num(c) is not None else (str(c).strip() if c is not None else "")) for c in r])

    # === كشف ملف معاملات POS الخام + معاينة الملخّص ===
    tx_summary = None
    bank_summary = None
    _cmap = {m["index"]: m["matched"] for m in matched if m["matched"]}
    if is_bank_statement(_cmap):
        bmonths = aggregate_bank_statement(rows, _cmap, year_hint=fctx.get("year_hint"))
        if bmonths:
            periods_sorted = sorted(bmonths.keys())
            bank_summary = {
                "is_bank": True,
                "months_count": len(bmonths),
                "from": periods_sorted[0], "to": periods_sorted[-1],
                "total_deposits": round(sum(v["deposits"] for v in bmonths.values())),
                "total_withdrawals": round(sum(v["withdrawals"] for v in bmonths.values())),
                "total_ops": sum(v["ops"] for v in bmonths.values()),
            }
    elif is_transactions_file(_cmap):
        agg = aggregate_transactions(rows, _cmap, headers, year_hint=fctx.get("year_hint"))
        if agg:
            _branches = sorted(set(k[0] for k in agg.keys()))
            _periods = sorted(set(k[1] for k in agg.keys()))
            tx_summary = {
                "is_transactions": True,
                "summary_rows": len(agg),
                "branches_count": len(_branches),
                "periods_count": len(_periods),
                "total_sales": round(sum(v["sales"] for v in agg.values())),
                "total_returns": round(sum(v["returned_amount"] for v in agg.values())),
                "branches": _branches[:10],
            }

    # ===== تقرير جودة الملف (من التقرير: الصف، الخطأ، الحل) =====
    file_issues = []
    # ① أعمدة غير مطابقة
    unmatched_headers = [m["original"] for m in matched if not m["matched"]]
    if unmatched_headers:
        file_issues.append({
            "type": "unmatched", "severity": "warn",
            "msg": f"{len(unmatched_headers)} عمود لم يُطابَق تلقائياً: {'، '.join(unmatched_headers[:3])}",
            "fix": "راجع أسماء الأعمدة أو طابِقها يدوياً — الأعمدة غير المطابقة ستُتجاهل."
        })
    # ② قيم سالبة أو غير منطقية في العيّنة
    neg_found = False
    for r in rows[:50]:
        for c in r:
            n = _to_num(c)
            if n is not None and n < 0:
                neg_found = True; break
        if neg_found: break
    if neg_found:
        file_issues.append({
            "type": "negative", "severity": "warn",
            "msg": "الملف يحتوي قيماً سالبة",
            "fix": "تأكد أن القيم السالبة مقصودة (مثل المرتجعات) — أو صحّحها قبل الاستيراد."
        })
    # ③ صفوف فارغة
    empty_rows = sum(1 for r in rows if not any(str(c).strip() for c in r if c is not None))
    if empty_rows > 0:
        file_issues.append({
            "type": "empty", "severity": "info",
            "msg": f"{empty_rows} صف فارغ سيُتجاهل",
            "fix": "الصفوف الفارغة تُتخطّى تلقائياً — لا إجراء مطلوب."
        })
    # درجة جودة الملف
    file_quality = 100
    file_quality -= len(unmatched_headers) * 8
    if neg_found: file_quality -= 10
    file_quality = max(file_quality, 0)

    return {
        "tx_summary": tx_summary,
        "bank_summary": bank_summary,
        "filename": file.filename,
        "total_rows": len(rows),
        "headers": headers,
        "matched": matched,
        "matched_count": matched_count,
        "unmatched_count": len(headers) - matched_count,
        "sample": sample,
        "file_issues": file_issues,
        "file_quality": file_quality,
        "guidance": "راجع تطابق الأعمدة وتقرير الجودة، ثم اضغط 'استورد' لتوزيع البيانات على الوحدات.",
    }


@app.post("/company/upload-import")
async def company_upload_import(file: UploadFile = File(...), user: User = Depends(get_current_user)):
    """يستورد الملف ويوزّع البيانات: ينشئ فروع جديدة + يحفظ CompanyEntry + يحدّث الوحدات."""
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    data = await file.read()
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(400, "الملف كبير جداً (أعلى من ٢٠ MB)")
    name = (file.filename or "").lower()
    if name.endswith(".csv"):
        headers, rows, fctx = parse_csv(data)
    elif name.endswith((".xlsx", ".xlsm")):
        headers, rows, fctx = parse_excel(data)
    else:
        raise HTTPException(400, "ادعم CSV أو Excel فقط")
    if len(rows) > 100000:
        raise HTTPException(400, "عدد الصفوف كبير جداً (أعلى من ١٠٠,٠٠٠)")

    # خريطة index → standard field (مع تخمين)
    col_map = {}
    for i, h in enumerate(headers):
        std = match_column(h)
        if not std:
            sample_vals = [r[i] for r in rows[:20] if i < len(r)]
            std = infer_column_by_content(sample_vals, h)
        if std: col_map[i] = std

    if not col_map:
        raise HTTPException(400, "لم نتعرّف على أي عمود — تأكد من أسماء الأعمدة (مثل: الفرع، المبيعات، المصروفات…)")

    # ============================================================
    # === مسار كشف الحساب البنكي: حفظ الإيداعات الشهرية للمطابقة ===
    # ============================================================
    if is_bank_statement(col_map):
        bmonths = aggregate_bank_statement(rows, col_map, year_hint=fctx.get("year_hint"))
        if not bmonths:
            raise HTTPException(400, "لم نستطع قراءة كشف الحساب — تأكد من أعمدة التاريخ والدائن/المدين")
        with Session(engine) as s:
            company = s.get(Company, user.company_id)
            if not company or company.owner_id != user.id:
                raise HTTPException(403, "غير مصرّح")
            if company.is_active != 1:
                raise HTTPException(402, "شركتك قيد التفعيل")
            created = updated = 0
            for period, v in sorted(bmonths.items()):
                data_json = json.dumps({
                    "الإيداعات (ريال)": v["deposits"],
                    "المسحوبات (ريال)": v["withdrawals"],
                    "عدد العمليات": v["ops"],
                    "__source": "bank",
                }, ensure_ascii=False)
                existing = s.exec(
                    select(CompanyModuleEntry).where(
                        CompanyModuleEntry.company_id == company.id,
                        CompanyModuleEntry.module == "bank",
                        CompanyModuleEntry.period == period,
                    )
                ).first()
                if existing:
                    existing.data = data_json
                    s.add(existing); updated += 1
                else:
                    s.add(CompanyModuleEntry(
                        company_id=company.id, branch_id=None,
                        module="bank", period=period, data=data_json,
                    )); created += 1
            s.commit()
            log_activity(user.name, f"رفع كشف حساب بنكي {file.filename}: {len(bmonths)} شهر", user.email)
            log_audit(company.id, user.id, getattr(user,"name",""), "upload", f"كشف بنكي: {file.filename}")
            save_memory(company.id, "upload", f"رفع كشف حساب بنكي: {file.filename}",
                        f"{len(bmonths)} شهر — إيداعات {round(sum(x['deposits'] for x in bmonths.values())):,} ر")
            return {
                "ok": True,
                "filename": file.filename,
                "is_bank": True,
                "created_entries": created,
                "updated_entries": updated,
                "module_entries": created + updated,
                "created_branches": 0,
                "matched_columns": len(col_map),
                "summary": (
                    f"🏦 تم اكتشاف كشف حساب بنكي وقراءته: {len(bmonths)} شهر "
                    f"({created} جديد، {updated} محدَّث). "
                    f"الآن يقارن نبّاه إيداعاتك الفعلية بمبيعاتك المسجّلة في خريطة تسرّب الأموال."
                ),
            }

    # ============================================================
    # === مسار ملفات معاملات POS الخام: تلخيص تلقائي ثم حفظ ===
    # ============================================================
    if is_transactions_file(col_map):
        try:
            aggregated = aggregate_transactions(rows, col_map, headers, year_hint=fctx.get("year_hint"))
        except Exception as e:
            _logger.error(f"فشل تلخيص المعاملات: {type(e).__name__}: {str(e)[:200]}"); raise HTTPException(400, "تعذّر تحليل المعاملات في الملف. تأكّد من تنسيق البيانات.")
        if not aggregated:
            raise HTTPException(400, "لم نستطع تلخيص المعاملات — تأكد من صحة أعمدة التاريخ والفرع والمبلغ")
        with Session(engine) as s:
            company = s.get(Company, user.company_id)
            if not company or company.owner_id != user.id:
                raise HTTPException(403, "غير مصرّح")
            if company.is_active != 1:
                raise HTTPException(402, "شركتك قيد التفعيل")

            existing_branches = {b.name.strip().lower(): b for b in s.exec(
                select(CompanyBranch).where(CompanyBranch.company_id == company.id)
            ).all()}
            created_branches = created_entries = updated_entries = 0
            last_sales_by_branch = {}   # لحساب النمو بين الفترات
            returns_by_branch = {}      # لوحدة المبيعات (المرتجعات)

            # نرتّب حسب الفترة زمنياً حتى يُحسب النمو صح
            try:
                for (branch_name, period), agg in sorted(aggregated.items(), key=lambda kv: (kv[0][0], kv[0][1])):
                    key = branch_name.strip().lower()
                    if key in existing_branches:
                        branch = existing_branches[key]
                    else:
                        branch = CompanyBranch(company_id=company.id, name=branch_name.strip(), city="", branch_type="standalone")
                        s.add(branch); s.commit(); s.refresh(branch)
                        existing_branches[key] = branch
                        created_branches += 1

                    prev_sales = last_sales_by_branch.get(branch.id)
                    m = compute_company_metrics(
                        sales=agg["sales"], invoices=agg["invoices"], customers=agg["customers"],
                        repeat_customers=0, expenses=0, prev_sales=prev_sales,
                    )
                    last_sales_by_branch[branch.id] = agg["sales"]
                    returns_by_branch[branch.id] = (agg["returned_amount"], agg["returns_count"], period)

                    # المصروفات غير موجودة في ملف POS → لا نختلق ربحاً وهمياً
                    # نصفّر الربح والهامش حتى لا تتلوث مؤشرات الربحية
                    existing_entry = s.exec(
                        select(CompanyEntry).where(
                            CompanyEntry.branch_id == branch.id,
                            CompanyEntry.period == period,
                        )
                    ).first()
                    if existing_entry:
                        existing_entry.sales = agg["sales"]
                        existing_entry.invoices = agg["invoices"]
                        existing_entry.customers = agg["customers"]
                        existing_entry.discounts = agg.get("discounts", 0.0)
                        existing_entry.avg_invoice = m["avg_invoice"]
                        existing_entry.growth = m["growth"]
                        existing_entry.branch_score = m["branch_score"]
                        if existing_entry.expenses <= 0:
                            existing_entry.profit = 0
                            existing_entry.margin = 0
                        s.add(existing_entry)
                        updated_entries += 1
                    else:
                        entry = CompanyEntry(
                            company_id=company.id, branch_id=branch.id, branch_name=branch.name, period=period,
                            sales=agg["sales"], expenses=0,
                            invoices=agg["invoices"], customers=agg["customers"],
                            new_customers=0, repeat_customers=0,
                            discounts=agg.get("discounts", 0.0), deposited=0, top_products="",
                            profit=0, margin=0,
                            avg_invoice=m["avg_invoice"], repeat_rate=0,
                            growth=m["growth"], branch_score=m["branch_score"],
                        )
                        s.add(entry)
                        created_entries += 1
                    s.commit()

            except Exception as e:
                s.rollback()
                _logger.error(f"فشل حفظ الملخّصات: {type(e).__name__}: {str(e)[:200]}"); raise HTTPException(500, "تعذّر حفظ ملخّص الملف. حاول مرة أخرى.")
            # وحدة المبيعات: مرتجعات آخر فترة لكل فرع (بمصدر pos)
            module_entries = 0
            for bid, (ret_amt, ret_cnt, period) in returns_by_branch.items():
                if ret_amt > 0 or ret_cnt > 0:
                    me = CompanyModuleEntry(
                        company_id=company.id, branch_id=bid, module="sales", period=period,
                        data=json.dumps({"المرتجعات (ريال)": round(ret_amt, 2), "__source": "pos"}, ensure_ascii=False),
                    )
                    s.add(me); module_entries += 1
            s.commit()

            log_activity(user.name, f"رفع ملف معاملات POS {file.filename}: {len(aggregated)} ملخّص شهري", user.email)
            log_audit(company.id, user.id, getattr(user,"name",""), "upload", f"ملف POS: {file.filename}")
            save_memory(company.id, "upload", f"رفع ملف POS: {file.filename}",
                        f"تم استيراد {len(aggregated)} سجل شهري ({created_branches} فرع جديد، {created_entries} إدخال جديد، {updated_entries} محدَّث)")
            return {
                "ok": True,
                "filename": file.filename,
                "is_transactions": True,
                "created_branches": created_branches,
                "created_entries": created_entries,
                "updated_entries": updated_entries,
                "module_entries": module_entries,
                "matched_columns": len(col_map),
                "summary": (
                    f"🧾 تم اكتشاف ملف معاملات POS وتلخيصه تلقائياً: {len(aggregated)} سجل شهري "
                    f"({created_branches} فرع جديد، {created_entries} إدخال جديد، {updated_entries} إدخال محدَّث). "
                    f"ملاحظة: ملفات POS لا تحوي المصروفات — أضفها من الوحدة المالية أو ارفع ملف مصروفات لتفعيل تحليل الربحية الكامل."
                ),
            }

    # حقول الفروع الأساسية (تذهب لـ CompanyEntry)
    BASE_FIELDS = {"sales","expenses","invoices","customers","new_customers","repeat_customers",
                   "discounts","deposited","top_products"}
    # حقول كل وحدة (تذهب لـ CompanyModuleEntry حسب الوحدة)
    MODULE_OF = {
        "cogs":"finance","salaries":"finance","rent":"finance","marketing":"finance",
        "utilities":"finance","logistics_cost":"finance","ar":"finance","ap":"finance",
        "cash":"finance","short_debt":"finance","long_debt":"finance",
        "lost_customers":"customers","nps":"customers","satisfaction":"customers","complaints":"customers",
        "employees":"hr","resignations":"hr","absence":"hr","training":"hr","saudization":"hr","vacancies":"hr",
        "inventory_value":"inventory","dead_stock":"inventory","stockouts":"inventory","waste":"inventory",
        "suppliers":"procurement","purchases":"procurement","supplier_delay":"procurement",
        "active_projects":"ops","delayed_projects":"ops","on_time_orders":"ops","breakdowns":"ops",
    }
    FIELD_LABELS_AR = {
        "cogs":"تكلفة المبيعات COGS (ريال)","salaries":"الرواتب الإجمالية (ريال)","rent":"الإيجارات (ريال)",
        "marketing":"التسويق والإعلانات (ريال)","utilities":"الكهرباء والمرافق (ريال)","logistics_cost":"النقل والشحن (ريال)",
        "ar":"الذمم المدينة — مستحقات لك (ريال)","ap":"الذمم الدائنة — مستحقات عليك (ريال)",
        "cash":"النقد في الصندوق والبنك (ريال)","short_debt":"الديون قصيرة الأجل (ريال)","long_debt":"الديون طويلة الأجل (ريال)",
        "lost_customers":"عملاء مفقودون (لم يعودوا)","nps":"درجة NPS (-100 إلى +100)",
        "satisfaction":"نسبة رضا العملاء %","complaints":"عدد الشكاوى",
        "employees":"عدد الموظفين الإجمالي","resignations":"المستقيلون / المنفصلون","absence":"أيام الغياب",
        "training":"ميزانية التدريب (ريال)","saudization":"نسبة السعودة %","vacancies":"الوظائف الشاغرة",
        "inventory_value":"قيمة المخزون الحالية (ريال)","dead_stock":"قيمة المخزون الراكد (ريال)",
        "stockouts":"عدد مرات نفاد المخزون","waste":"قيمة الهدر (ريال)",
        "suppliers":"عدد الموردين النشطين","purchases":"إجمالي قيمة المشتريات (ريال)",
        "supplier_delay":"متوسط تأخر المورد (يوم)",
        "active_projects":"المشاريع/الطلبات النشطة","delayed_projects":"المشاريع/الطلبات المتأخرة",
        "on_time_orders":"عدد الطلبات المنفّذة في الوقت المحدد","breakdowns":"عدد الأعطال",
    }

    with Session(engine) as s:
        company = s.get(Company, user.company_id)
        if not company or company.owner_id != user.id:
            raise HTTPException(403, "غير مصرّح")
        if company.is_active != 1:
            raise HTTPException(402, "شركتك قيد التفعيل")

        existing_branches = {b.name.strip().lower(): b for b in s.exec(
            select(CompanyBranch).where(CompanyBranch.company_id == company.id)
        ).all()}

        created_branches = 0
        created_entries = 0
        # تجميع بيانات الوحدات لكل فرع
        module_data_per_branch = {}  # {branch_id: {module: {label: value}}}
        company_level_modules = {}    # {module: {label: value}} لو ما فيه فرع

        for r in rows:
            if not any(c is not None and str(c).strip() != "" for c in r):
                continue
            # استخرج القيم حسب التطابق
            extracted = {}
            for i, std in col_map.items():
                if i < len(r):
                    val = r[i]
                    if std in ("branch_name","city","period","top_products"):
                        extracted[std] = str(val).strip() if val is not None else ""
                    else:
                        n = _to_num(val)
                        if n is not None: extracted[std] = n

            # الفرع
            branch_name = extracted.get("branch_name", "").strip()
            branch = None
            if branch_name:
                key = branch_name.lower()
                if key in existing_branches:
                    branch = existing_branches[key]
                else:
                    branch = CompanyBranch(company_id=company.id, name=branch_name,
                                           city=extracted.get("city","").strip(), type="standalone")
                    s.add(branch); s.commit(); s.refresh(branch)
                    existing_branches[key] = branch
                    created_branches += 1

            # CompanyEntry (لو فيه فرع + مبيعات)
            if branch and "sales" in extracted and extracted["sales"] > 0:
                period = extracted.get("period") or datetime.now().strftime("%Y-%m")
                # تأكد من صياغة YYYY-MM
                period = str(period).strip()
                if len(period) >= 7 and period[4] == "-":
                    period = period[:7]
                prev = s.exec(
                    select(CompanyEntry).where(CompanyEntry.branch_id == branch.id).order_by(CompanyEntry.created_at.desc())
                ).first()
                prev_sales = prev.sales if prev else None
                m = compute_company_metrics(
                    sales=extracted.get("sales", 0),
                    invoices=int(extracted.get("invoices", 0)),
                    customers=int(extracted.get("customers", 0)),
                    repeat_customers=int(extracted.get("repeat_customers", 0)),
                    expenses=extracted.get("expenses", 0),
                    prev_sales=prev_sales,
                )
                entry = CompanyEntry(
                    company_id=company.id, branch_id=branch.id, branch_name=branch.name, period=period,
                    sales=extracted.get("sales", 0),
                    expenses=extracted.get("expenses", 0),
                    invoices=int(extracted.get("invoices", 0)),
                    customers=int(extracted.get("customers", 0)),
                    new_customers=int(extracted.get("new_customers", 0)),
                    repeat_customers=int(extracted.get("repeat_customers", 0)),
                    discounts=extracted.get("discounts", 0),
                    deposited=extracted.get("deposited", 0),
                    top_products=str(extracted.get("top_products", "")),
                    profit=m["profit"], margin=m["margin"], avg_invoice=m["avg_invoice"],
                    repeat_rate=m["repeat_rate"], growth=m["growth"], branch_score=m["branch_score"],
                )
                s.add(entry); s.commit()
                created_entries += 1

            # توزيع بيانات الوحدات
            for std, val in extracted.items():
                if std in MODULE_OF and FIELD_LABELS_AR.get(std):
                    mod = MODULE_OF[std]
                    label = FIELD_LABELS_AR[std]
                    bid = branch.id if branch else 0
                    bucket = module_data_per_branch.setdefault(bid, {}).setdefault(mod, {})
                    bucket[label] = val

            # إعدادات شركة (احتياطي والتزامات)
            if "cash_reserve" in extracted:
                company.cash_reserve = float(extracted["cash_reserve"])
            if "monthly_obligations" in extracted:
                company.monthly_obligations = float(extracted["monthly_obligations"])

        # احفظ بيانات الوحدات
        module_entries_count = 0
        now_period = datetime.now().strftime("%Y-%m")
        for bid, mods in module_data_per_branch.items():
            for mod, fields in mods.items():
                if not fields: continue
                fields["__source"] = "excel" if name.endswith(("xlsx","xlsm")) else "csv"
                entry = CompanyModuleEntry(
                    company_id=company.id,
                    branch_id=bid if bid > 0 else None,
                    module=mod,
                    period=now_period,
                    data=json.dumps(fields, ensure_ascii=False),
                )
                s.add(entry); module_entries_count += 1

        s.add(company)
        s.commit()
        log_activity(user.name, f"رفع ملف {file.filename}: {created_entries} سجل + {module_entries_count} وحدة", user.email)
        log_audit(company.id, user.id, getattr(user,"name",""), "upload", f"ملف بيانات: {file.filename}")
        save_memory(company.id, "upload", f"رفع ملف بيانات: {file.filename}",
                    f"{created_branches} فرع جديد، {created_entries} إدخال، {module_entries_count} وحدة محدّثة")

        return {
            "ok": True,
            "filename": file.filename,
            "created_branches": created_branches,
            "created_entries": created_entries,
            "module_entries": module_entries_count,
            "matched_columns": len(col_map),
            "summary": f"تم استيراد البيانات بنجاح. {created_branches} فرع جديد، {created_entries} إدخال فترة، {module_entries_count} وحدة محدّثة.",
        }


@app.get("/company-upload.html")
def page_company_upload():
    return FileResponse("company-upload.html")


@app.get("/nabbah-data-template.xlsx")
def download_template():
    """تنزيل قالب Excel الجاهز للعميل."""
    return FileResponse(
        "nabbah-data-template.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="nabbah-data-template.xlsx",
    )


# ============================================================
# ===== ذاكرة الشركة: سجل التحليلات والقرارات =====
# ============================================================

# ═══════════════════════════════════════════════════════════
#  بحث دلالي خفيف لذاكرة الشركة (خدمة ٧ من المرحلة ١)
#  بديل عملي عن pgvector (قد لا يتوفّر على Railway):
#  توسيع المرادفات + ترجيح الصلة + ترتيب بالأهمية — بلا مكتبات خارجية.
#  يمكن الترقية لـ pgvector لاحقاً دون تغيير الواجهة.
# ═══════════════════════════════════════════════════════════
SEMANTIC_SYNONYMS = {
    "ربح": ["ربحية", "أرباح", "هامش", "مكسب", "عائد"],
    "خسارة": ["خسائر", "عجز", "تراجع", "هبوط"],
    "مبيعات": ["بيع", "إيرادات", "دخل", "مبيع"],
    "مصروف": ["مصروفات", "تكلفة", "تكاليف", "نفقات", "صرف"],
    "عميل": ["عملاء", "زبون", "زبائن", "مستهلك"],
    "موظف": ["موظفين", "فريق", "عمالة", "كادر"],
    "فرع": ["فروع", "موقع", "مواقع"],
    "مخاطر": ["خطر", "تهديد", "مشكلة", "تحذير"],
    "قرار": ["قرارات", "توصية", "توصيات", "إجراء"],
    "نمو": ["توسّع", "زيادة", "ارتفاع", "تطوّر"],
    "سيولة": ["نقد", "نقدية", "تدفق", "كاش"],
    "مخزون": ["بضاعة", "مستودع", "أصناف"],
}


def expand_query_terms(q):
    """يوسّع كلمات البحث بمرادفاتها الدلالية (بحث بالمعنى لا الحرف)."""
    q = (q or "").strip().lower()
    if not q:
        return []
    words = q.split()
    expanded = set(words)
    for w in words:
        for key, syns in SEMANTIC_SYNONYMS.items():
            if w == key or w in syns:
                expanded.add(key)
                expanded.update(syns)
    return list(expanded)


def semantic_score(item_text, terms):
    """يحسب درجة صلة بين نص وعبارات البحث الموسّعة (0-100)."""
    if not terms:
        return 0
    text = (item_text or "").lower()
    hits = sum(1 for t in terms if t in text)
    # ترجيح: نسبة التطابق + مكافأة للتطابقات المتعددة
    base = (hits / len(terms)) * 100 if terms else 0
    return round(min(base * 1.5, 100))


@app.get("/company/memory")
def company_memory_list(q: str = "", kind: str = "", user: User = Depends(get_current_user)):
    """يجلب سجل ذاكرة الشركة مع بحث دلالي (يفهم المعنى والمرادفات)."""
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    with Session(engine) as s:
        company = s.get(Company, user.company_id)
        if not company or company.owner_id != user.id:
            raise HTTPException(403, "غير مصرّح")
        if company.is_active != 1:
            raise HTTPException(402, "شركتك قيد التفعيل")
        query = select(CompanyMemory).where(CompanyMemory.company_id == company.id)
        if kind:
            query = query.where(CompanyMemory.kind == kind)
        items = s.exec(query.order_by(CompanyMemory.created_at.desc()).limit(200)).all()
        qn = q.strip().lower()
        if qn:
            # بحث دلالي: توسيع المرادفات + ترتيب بالصلة
            terms = expand_query_terms(qn)
            scored = []
            for m in items:
                full = (m.title or "") + " " + (m.content or "")
                sc = semantic_score(full, terms)
                if sc > 0:
                    scored.append((sc, m))
            scored.sort(key=lambda x: x[0], reverse=True)
            items = [m for sc, m in scored]
        KIND_META = {
            "analysis": ("🧑‍💼", "تحليل"), "question": ("💬", "سؤال"),
            "upload": ("📂", "رفع بيانات"), "goals": ("🎯", "أهداف"), "decision": ("⚡", "قرار"),
        }
        return {
            "company": {"name": company.name},
            "total": len(items),
            "items": [
                {
                    "id": m.id, "kind": m.kind,
                    "icon": KIND_META.get(m.kind, ("📌", m.kind))[0],
                    "kind_label": KIND_META.get(m.kind, ("📌", m.kind))[1],
                    "title": m.title,
                    "content": m.content,
                    "date": m.created_at.strftime("%Y-%m-%d %H:%M"),
                }
                for m in items[:100]
            ],
        }


@app.get("/company-memory.html")
def page_company_memory():
    return FileResponse("company-memory.html")


# ============================================================
# ===== نسخة النظام: للتحقق أن آخر تحديث منشور فعلاً =====
# ============================================================

NABBAH_VERSION = "5.2-smart-recognition"

@app.get("/version")
def version_check():
    """افتح nabbah.up.railway.app/version — لو ما شفت هذي النسخة فالتحديث غير منشور."""
    # النسخة العامة مختصرة عمداً — التفاصيل الكاملة عبر /files-check للأدمن
    import os as _os
    _pages = {p: _os.path.exists(p) for p in ("company-actions.html", "company-scenarios.html")}
    _engines = {d: _os.path.isdir(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), d))
                for d in ("phase21", "phase22", "phase23")}
    return {
        "version": NABBAH_VERSION,
        "build": "phase-2.3",
        "pages_present": _pages,
        "engines_present": _engines,
        "status": "ok",
        "features": {
            "pos_transactions_import": True,
            "company_memory": True,
            "executive_framework": True,
            "smart_column_inference": True,
            "header_row_detection": True,
            "arabic_months": True,
            "bank_statement_import": True,
            "executive_brief": True,
        },
        "excel_support": _check_openpyxl(),
        "health": _runtime_health(),
        "time": datetime.now().isoformat(),
    }


REQUIRED_RUNTIME_FILES = ("main.py", "nabbah_engines.py", "requirements.txt", "i18n.js",
                          "nabbah-sidebar.js", "nabbah-design.css", "nabbah-exec-intel.js",
                          "nabbah-ask.js", "nabbah-decision.js", "nabbah-filters.js",
                          "nabbah-data-template.xlsx")
ENGINE_MODULES = ("nabbah_finance", "nabbah_trust", "semantic_layer", "kpi_engine", "analysis_engines",
                  "ai_gateway", "period_aggregation", "legacy_adapters", "platform_bridge",
                  "intelligence_engine", "forecast_engine", "scenario_engine", "decision_memory",
                  "rule_catalog", "canonical_model", "period_model", "metric_registry", "ingestion",
                  "sales_engine", "inventory_engine", "purchases_engine", "cashflow_engine", "hr_engine", "ops_engine", "finance_engine", "sector_intelligence", "leakage_engine", "tax_engine", "risk_engine", "drivers_engine", "benchmark_engine", "prediction_engine", "goals_engine", "decisions_engine", "board_engine")


def _runtime_health():
    """فحص حقيقي لا افتراض: الملفات المطلوبة، تحميل المحركات الـ19، وتطابق جدول المستخدمين مع النموذج."""
    import os as _os, importlib, re as _re
    base = _os.path.dirname(_os.path.abspath(__file__))
    try:
        src = open(_os.path.join(base, "main.py"), encoding="utf-8").read()
        pages = set(_re.findall(r'FileResponse\(\s*["\']([^"\']+)["\']', src))
    except Exception:
        pages = set()
    missing = sorted(f for f in set(REQUIRED_RUNTIME_FILES) | pages
                     if not _os.path.exists(_os.path.join(base, f)))
    failed = []
    for m in ENGINE_MODULES:
        try:
            try:
                importlib.import_module(m)
            except ModuleNotFoundError:
                import nabbah_engines  # noqa: F401
                importlib.import_module(m)
        except Exception as e:
            failed.append(f"{m}: {type(e).__name__}")
    try:
        from sqlalchemy import inspect as _inspect
        db_cols = {c["name"] for c in _inspect(engine).get_columns("user")}
        user_missing = sorted(set(User.__table__.columns.keys()) - db_cols)
    except Exception as e:
        user_missing = [f"check failed: {type(e).__name__}"]
    ok = not missing and not failed and not user_missing
    return {"status": "ok" if ok else "degraded",
            "missing_files": missing,
            "engines": f"{len(ENGINE_MODULES) - len(failed)}/{len(ENGINE_MODULES)}",
            "engines_failed": failed,
            "user_table_missing_columns": user_missing}


def _check_openpyxl():
    try:
        import openpyxl  # noqa
        return True
    except ImportError:
        return False


# ============================================================
# ===== حالة الإعداد: قائمة Onboarding ذكية =====
# ============================================================

@app.get("/company/setup-status")
def company_setup_status(user: User = Depends(get_current_user)):
    """يفحص مراحل إعداد الشركة ويرجع قائمة تقدّم — أساس تجربة الترحيب."""
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    with Session(engine) as s:
        company = s.get(Company, user.company_id)
        if not company or company.owner_id != user.id:
            raise HTTPException(403, "غير مصرّح")

        # ١) الشركة مفعّلة؟
        activated = company.is_active == 1

        # ٢) بيانات فروع موجودة؟
        entries_count = len(s.exec(
            select(CompanyEntry).where(CompanyEntry.company_id == company.id)
        ).all())
        has_data = entries_count > 0

        # ٣) مصروفات مدخلة؟ (ضرورية لتحليل الربحية)
        has_expenses = False
        if has_data:
            exp_entries = s.exec(
                select(CompanyEntry).where(
                    CompanyEntry.company_id == company.id,
                    CompanyEntry.expenses > 0,
                )
            ).first()
            has_expenses = exp_entries is not None

        # ٤) أهداف محدّدة؟
        has_goals = s.exec(
            select(CompanyModuleEntry).where(
                CompanyModuleEntry.company_id == company.id,
                CompanyModuleEntry.module == "goals",
            )
        ).first() is not None

        # ٥) وحدة واحدة على الأقل معبّأة؟
        has_module = s.exec(
            select(CompanyModuleEntry).where(
                CompanyModuleEntry.company_id == company.id,
                CompanyModuleEntry.module != "goals",
            )
        ).first() is not None

        steps = [
            {"key": "activate", "done": activated, "icon": "🔓",
             "title": "تفعيل الشركة",
             "hint": "" if activated else "شركتك قيد التفعيل — تواصل مع نبّاه",
             "link": ""},
            {"key": "data", "done": has_data, "icon": "📂",
             "title": "رفع أول بيانات (Excel/CSV/POS)",
             "hint": f"{entries_count} إدخال محفوظ" if has_data else "ارفع ملفك وكل الخدمات تشتغل فوراً",
             "link": "company-upload.html"},
            {"key": "expenses", "done": has_expenses, "icon": "💰",
             "title": "إدخال المصروفات",
             "hint": "" if has_expenses else "ضرورية لتحليل الربحية والهامش والتدفق النقدي",
             "link": "company-finance.html"},
            {"key": "goals", "done": has_goals, "icon": "🎯",
             "title": "تحديد الأهداف",
             "hint": "" if has_goals else "حدّد هدف المبيعات والربح ليقيس نبّاه الإنجاز",
             "link": "company-goals.html"},
            {"key": "module", "done": has_module, "icon": "🧩",
             "title": "تعبئة وحدة إضافية",
             "hint": "" if has_module else "عملاء أو موارد بشرية أو مخزون — كل وحدة تزيد عمق التحليل",
             "link": "company-customers.html"},
        ]
        done_count = sum(1 for st in steps if st["done"])
        return {
            "steps": steps,
            "done": done_count,
            "total": len(steps),
            "pct": round(done_count / len(steps) * 100),
            "complete": done_count == len(steps),
        }


# ============================================================
# ===== متابعة القرارات: اعتماد → تنفيذ → قياس النتيجة =====
# ============================================================

def _company_total_sales(s, company_id):
    """إجمالي مبيعات آخر فترة لكل فرع — خط الأساس لقياس أثر القرار."""
    total = 0.0
    for b in s.exec(select(CompanyBranch).where(CompanyBranch.company_id == company_id, CompanyBranch.is_active == 1)).all():
        e = s.exec(select(CompanyEntry).where(CompanyEntry.branch_id == b.id).order_by(CompanyEntry.created_at.desc())).first()
        if e:
            total += e.sales
    return round(total, 2)


@app.post("/company/decisions/save")
def company_decision_save(data: dict, user: User = Depends(get_current_user)):
    """يعتمد قراراً للمتابعة — مع تسجيل خط الأساس تلقائياً."""
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    title = (data.get("title") or "").strip()
    if not title:
        raise HTTPException(400, "اكتب عنوان القرار")
    with Session(engine) as s:
        company = s.get(Company, user.company_id)
        if not company or company.owner_id != user.id:
            raise HTTPException(403, "غير مصرّح")
        if company.is_active != 1:
            raise HTTPException(402, "شركتك قيد التفعيل")
        d = CompanyDecision(
            company_id=company.id,
            title=title[:200],
            detail=str(data.get("detail") or "")[:1000],
            owner=str(data.get("owner") or "")[:100],
            due_date=str(data.get("due_date") or "")[:10],
            kpi=str(data.get("kpi") or "")[:200],
            expected_impact=str(data.get("expected_impact") or "")[:200],
            linked_to=str(data.get("linked_to") or "")[:200],
            approver=str(data.get("approver") or "")[:100],
            reviewer=str(data.get("reviewer") or "")[:100],
            rationale=str(data.get("rationale") or "")[:500],
            baseline_sales=_company_total_sales(s, company.id),
        )
        s.add(d); s.commit(); s.refresh(d)
        save_memory(company.id, "decision", f"اعتماد قرار: {title[:100]}",
                    f"{d.detail}\nالمسؤول: {d.owner or '—'} | الموعد: {d.due_date or '—'} | KPI: {d.kpi or '—'}")
        return {"ok": True, "id": d.id}


@app.get("/company/decisions")
def company_decisions_list(user: User = Depends(get_current_user)):
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    with Session(engine) as s:
        company = s.get(Company, user.company_id)
        if not company or company.owner_id != user.id:
            raise HTTPException(403, "غير مصرّح")
        if company.is_active != 1:
            raise HTTPException(402, "شركتك قيد التفعيل")
        items = s.exec(
            select(CompanyDecision).where(CompanyDecision.company_id == company.id)
            .order_by(CompanyDecision.created_at.desc()).limit(100)
        ).all()
        today = datetime.now().strftime("%Y-%m-%d")
        out = []
        # لحساب تكلفة التأخير: المبيعات اليومية التقديرية للشركة
        daily_sales = round(_company_total_sales(s, company.id) / 30) if _company_total_sales(s, company.id) > 0 else 0
        for d in items:
            overdue = bool(d.status == "open" and d.due_date and d.due_date < today)
            impact = None
            if d.status == "done" and d.baseline_sales > 0 and d.result_sales > 0:
                change = round((d.result_sales - d.baseline_sales) / d.baseline_sales * 100, 1)
                impact = {"baseline": round(d.baseline_sales), "result": round(d.result_sales), "change_pct": change}

            # ===== درجة جودة القرار (Decision Health Score 0-100) =====
            # من: مسؤول + موعد + KPI + خط أساس + نتيجة + عدم التأخير
            hs = 0
            hs += 20 if d.owner else 0                          # مسؤول محدد
            hs += 20 if d.due_date else 0                       # موعد واضح
            hs += 20 if d.kpi else 0                            # KPI محدد
            hs += 15 if d.baseline_sales > 0 else 0             # خط أساس قبل القرار
            hs += 15 if (d.status == "done" and d.result_sales > 0) else 0  # نتيجة مقاسة
            hs += 10 if not overdue else 0                      # ليس متأخراً
            health_label = "قرار صحي" if hs >= 75 else ("يحتاج تحسين" if hs >= 50 else "ضعيف الجودة")

            # ===== تكلفة التأخير (Cost of Delay) =====
            delay_cost = None
            delay_days = 0
            if overdue and d.due_date:
                try:
                    from datetime import date as _date
                    dd = _date.fromisoformat(d.due_date)
                    delay_days = (datetime.now().date() - dd).days
                    if delay_days > 0 and daily_sales > 0:
                        # تقدير محافظ: 2% من المبيعات اليومية كأثر تقديري للقرار المتأخر
                        delay_cost = round(delay_days * daily_sales * 0.02)
                except Exception:
                    pass

            # ===== المتوقع مقابل الفعلي + درس (Post-Mortem) =====
            expected_vs_actual = None
            lesson = None
            if d.status == "done" and d.expected_impact:
                actual_txt = ""
                if impact:
                    actual_txt = f"المبيعات تغيّرت {impact['change_pct']:+}%"
                expected_vs_actual = {"expected": d.expected_impact, "actual": actual_txt or (d.result_note or "—")}
                # درس تلقائي بسيط
                if impact:
                    if impact["change_pct"] > 0:
                        lesson = "القرار حقّق أثراً إيجابياً — يمكن تكرار نهجه في قرارات مشابهة."
                    elif impact["change_pct"] < 0:
                        lesson = "النتيجة جاءت دون المتوقّع — راجع الافتراضات قبل قرارات مشابهة."
                    else:
                        lesson = "لم يظهر أثر واضح — قد تحتاج فترة أطول أو مؤشراً أدق."

            out.append({
                "id": d.id, "title": d.title, "detail": d.detail,
                "owner": d.owner, "due_date": d.due_date, "kpi": d.kpi,
                "status": d.status, "overdue": overdue,
                "created": d.created_at.strftime("%Y-%m-%d"),
                "closed": d.closed_at.strftime("%Y-%m-%d") if d.closed_at else None,
                "result_note": d.result_note, "impact": impact,
                "health_score": hs, "health_label": health_label,
                "delay_days": delay_days, "delay_cost": delay_cost,
                "expected_impact": d.expected_impact,
                "expected_vs_actual": expected_vs_actual, "lesson": lesson,
                "linked_to": d.linked_to,
                "approver": d.approver, "reviewer": d.reviewer,
                "rationale": d.rationale,
            })
        open_count = sum(1 for d in out if d["status"] == "open")
        # ===== القرارات المرتبطة (Dependency) =====
        # نبني خريطة: أي قرار متأخر → القرارات التي تعتمد عليه تتأثّر
        id_to_title = {d["id"]: d["title"] for d in out}
        overdue_ids = {d["id"] for d in out if d["overdue"]}
        for d in out:
            # القرارات التي يعتمد عليها هذا القرار
            deps = [int(x) for x in (d.get("linked_to") or "").split(",") if x.strip().isdigit()] if d.get("linked_to") else []
            d["linked_titles"] = [id_to_title.get(dep, "") for dep in deps if dep in id_to_title]
            # هل أحد اعتمادياته متأخر؟
            blocked_by = [id_to_title.get(dep, "") for dep in deps if dep in overdue_ids]
            d["blocked_by"] = [t for t in blocked_by if t]
        # نحسب: القرارات التي تتأثّر بكل قرار متأخر
        affected_count = 0
        for oid in overdue_ids:
            for d in out:
                deps = [int(x) for x in (d.get("linked_to") or "").split(",") if x.strip().isdigit()] if d.get("linked_to") else []
                if oid in deps:
                    affected_count += 1
        # درجة ذكاء القرارات المؤسسية (متوسط جودة كل القرارات)
        scores = [d["health_score"] for d in out]
        intelligence_score = round(sum(scores) / len(scores)) if scores else 0
        total_delay_cost = sum(d["delay_cost"] for d in out if d["delay_cost"])
        # ===== قرارات تحتاج تدخّل (تنبيه ذكي) =====
        needs_attention = []
        for dd in out:
            if dd["overdue"]:
                needs_attention.append(f"«{dd['title'][:40]}» متأخر {dd['delay_days']} يوم")
            elif dd["status"] == "open" and not dd["owner"]:
                needs_attention.append(f"«{dd['title'][:40]}» بلا مسؤول محدد")
            elif dd["status"] == "open" and not dd["kpi"]:
                needs_attention.append(f"«{dd['title'][:40]}» بلا مؤشر نجاح")
        return {"items": out, "open_count": open_count,
                "overdue_count": sum(1 for d in out if d["overdue"]),
                "intelligence_score": intelligence_score,
                "total_delay_cost": total_delay_cost,
                "needs_attention": needs_attention[:5],
                "affected_count": affected_count}


@app.post("/company/decisions/close")
def company_decision_close(data: dict, user: User = Depends(get_current_user)):
    """يغلق قراراً ويقيس النتيجة تلقائياً (مبيعات قبل/بعد)."""
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    did = data.get("id")
    with Session(engine) as s:
        company = s.get(Company, user.company_id)
        if not company or company.owner_id != user.id:
            raise HTTPException(403, "غير مصرّح")
        d = s.get(CompanyDecision, int(did)) if did else None
        if not d or d.company_id != company.id:
            raise HTTPException(404, "القرار غير موجود")
        d.status = str(data.get("status") or "done")
        d.result_note = str(data.get("result_note") or "")[:500]
        d.result_sales = _company_total_sales(s, company.id)
        d.closed_at = datetime.now()
        s.add(d); s.commit()
        change_txt = ""
        if d.baseline_sales > 0 and d.result_sales > 0:
            change = round((d.result_sales - d.baseline_sales) / d.baseline_sales * 100, 1)
            change_txt = f" | المبيعات تغيّرت {change:+}% منذ الاعتماد"
        save_memory(company.id, "decision", f"إغلاق قرار: {d.title[:100]}",
                    f"الحالة: {d.status} | {d.result_note or 'بدون ملاحظة'}{change_txt}")
        return {"ok": True}


@app.get("/company-decisions.html")
def page_company_decisions():
    return FileResponse("company-decisions.html")


# ============================================================
# ===== الفحص الذاتي: أي ملف ناقص أو قديم على السيرفر؟ =====
# ============================================================

@app.get("/files-check")
def files_check(_: bool = Depends(verify_admin)):
    """افتح nabbah.com/files-check — يبيّن أي صفحة ناقصة على السيرفر فوراً."""
    import os
    REQUIRED_PAGES = [
        "index.html", "login.html", "admin.html",
        "company-register.html", "company-dashboard.html", "company-input.html",
        "company-branches.html", "company-report.html", "company-team.html",
        "company-cashflow.html", "company-leakage.html", "company-tax.html",
        "company-finance.html", "company-sales.html", "company-customers.html",
        "company-hr.html", "company-ops.html", "company-inventory.html",
        "company-procurement.html", "company-events.html", "company-competitors.html",
        "company-command-center.html", "company-health.html", "company-goals.html",
        "company-data-quality.html", "company-predictions.html", "company-risks.html",
        "company-board.html", "company-root-cause.html", "company-benchmarks.html",
        "company-upload.html", "company-memory.html", "company-decisions.html",
        "company-monthly-report.html",
        "nabbah-data-template.xlsx",
    ]
    missing, present = [], []
    for f in REQUIRED_PAGES:
        if os.path.exists(f):
            present.append({"file": f, "size_kb": round(os.path.getsize(f) / 1024, 1)})
        else:
            missing.append(f)
    return {
        "version": NABBAH_VERSION,
        "status": "✅ كل الملفات موجودة" if not missing else f"❌ {len(missing)} ملف ناقص على السيرفر",
        "missing_files": missing,
        "present_count": len(present),
        "present": present,
    }


@app.get("/pages-check")
def pages_check(_: bool = Depends(verify_admin)):
    """يفحص إذا صفحات الوحدات محدّثة (فيها التحصينات) أو نسخ قديمة."""
    import os
    # علامات النسخة الجديدة في كل صفحة
    MARKERS = {
        "company-customers.html": "نُظهر المحتوى دائماً",
        "company-finance.html": "نُظهر المحتوى دائماً",
        "company-sales.html": "نُظهر المحتوى دائماً",
        "company-hr.html": "نُظهر المحتوى دائماً",
        "company-ops.html": "نُظهر المحتوى دائماً",
        "company-inventory.html": "نُظهر المحتوى دائماً",
        "company-procurement.html": "نُظهر المحتوى دائماً",
        "company-events.html": "نُظهر المحتوى دائماً",
        "company-competitors.html": "نُظهر المحتوى دائماً",
        "company-predictions.html": "خطأ في الصفحة",
        "company-data-quality.html": "خطأ في الصفحة",
    }
    results = []
    old_pages = []
    for fname, marker in MARKERS.items():
        if not os.path.exists(fname):
            results.append({"file": fname, "status": "❌ مفقود"})
            old_pages.append(fname)
            continue
        try:
            content = open(fname, encoding="utf-8").read()
            if marker in content:
                results.append({"file": fname, "status": "✅ محدّث"})
            else:
                results.append({"file": fname, "status": "⚠️ نسخة قديمة — أعد رفعها"})
                old_pages.append(fname)
        except Exception as e:
            _logger.error(f"فشل معالجة ملف {fname}: {type(e).__name__}: {str(e)[:150]}"); results.append({"file": fname, "status": "تعذّرت المعالجة"})
    return {
        "summary": "✅ كل الصفحات محدّثة" if not old_pages else f"⚠️ {len(old_pages)} صفحة قديمة أو مفقودة تحتاج إعادة رفع",
        "needs_reupload": old_pages,
        "details": results,
    }


# ============================================================
# ===== خريطة تسرّب الأموال: "وين تروح فلوسك؟" بالريال =====
# ============================================================

def _latest_module_field(s, company_id, module, field_contains):
    """يجمع قيمة حقل من آخر إدخال لكل فرع في وحدة معيّنة."""
    total = 0.0
    found = False
    seen_branches = set()
    entries = s.exec(
        select(CompanyModuleEntry).where(
            CompanyModuleEntry.company_id == company_id,
            CompanyModuleEntry.module == module,
        ).order_by(CompanyModuleEntry.created_at.desc())
    ).all()
    for me in entries:
        key = me.branch_id or 0
        if key in seen_branches:
            continue
        seen_branches.add(key)
        try:
            data = json.loads(me.data) if me.data else {}
        except Exception:
            continue
        for k, v in data.items():
            if field_contains in k:
                n = _to_num(v)
                if n is not None and n > 0:
                    total += n
                    found = True
    return (round(total, 2), found)


@app.get("/company/money-map")
def company_money_map(user: User = Depends(get_current_user)):
    """يبني خريطة التسرّب: كل بند يسرّب فلوس + قيمته بالريال + كيف تصلحه."""
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    with Session(engine) as s:
        company = s.get(Company, user.company_id)
        if not company or company.owner_id != user.id:
            raise HTTPException(403, "غير مصرّح")
        if company.is_active != 1:
            raise HTTPException(402, "شركتك قيد التفعيل")

        branches = s.exec(select(CompanyBranch).where(
            CompanyBranch.company_id == company.id, CompanyBranch.is_active == 1)).all()
        rows = []
        for b in branches:
            e = s.exec(select(CompanyEntry).where(CompanyEntry.branch_id == b.id)
                       .order_by(CompanyEntry.created_at.desc())).first()
            if e:
                rows.append(e)
        if not rows:
            raise HTTPException(400, "لا توجد بيانات — ارفع ملفك أو أدخل بيانات الفروع أولاً")

        total_sales = sum(e.sales for e in rows)
        total_expenses = sum(e.expenses for e in rows)
        cards = []

        # ١) الخصومات (من بيانات الفروع)
        discounts = round(sum(e.discounts for e in rows), 2)
        if discounts > 0:
            pct = round(discounts / total_sales * 100, 1) if total_sales else 0
            recover = round(discounts * 0.3)
            cards.append({
                "key": "discounts", "icon": "🏷️", "label": "الخصومات",
                "amount": discounts, "kind": "خسارة قابلة للاسترداد جزئياً",
                "evidence": f"إجمالي الخصومات {discounts:,.0f} ر = {pct}% من المبيعات",
                "fix": f"راجع مبررات الخصم لكل فرع — تقليصها 30% يعيد ~{recover:,} ر (تقدير)",
                "link": "company-sales.html", "severity": "high" if pct > 4 else "mid",
            })

        # ٢) المرتجعات (وحدة المبيعات)
        returns_amt, has_returns = _latest_module_field(s, company.id, "sales", "المرتجعات")
        if has_returns and returns_amt > 0:
            pct = round(returns_amt / total_sales * 100, 1) if total_sales else 0
            cards.append({
                "key": "returns", "icon": "↩️", "label": "المرتجعات",
                "amount": returns_amt, "kind": "خسارة مباشرة",
                "evidence": f"قيمة المرتجعات {returns_amt:,.0f} ر = {pct}% من المبيعات",
                "fix": "افحص أكثر المنتجات/الفروع استرجاعاً وعالج السبب (جودة؟ وصف؟ توصيل؟)",
                "link": "company-sales.html", "severity": "high" if pct > 5 else "mid",
            })

        # ٣) الهدر (وحدة المخزون)
        waste_amt, has_waste = _latest_module_field(s, company.id, "inventory", "الهدر")
        if has_waste and waste_amt > 0:
            cards.append({
                "key": "waste", "icon": "🗑️", "label": "هدر المخزون",
                "amount": waste_amt, "kind": "خسارة مباشرة",
                "evidence": f"قيمة الهدر المسجّلة {waste_amt:,.0f} ر",
                "fix": "راجع سلسلة التخزين والطلب — خفض الهدر 50% يوفّر ~" + f"{round(waste_amt*0.5):,} ر (تقدير)",
                "link": "company-inventory.html", "severity": "high",
            })

        # ٤) المخزون الراكد (أموال محتجزة)
        dead_amt, has_dead = _latest_module_field(s, company.id, "inventory", "الراكد")
        if has_dead and dead_amt > 0:
            cards.append({
                "key": "dead_stock", "icon": "📦", "label": "المخزون الراكد",
                "amount": dead_amt, "kind": "أموال محتجزة",
                "evidence": f"{dead_amt:,.0f} ر بضاعة لا تتحرك",
                "fix": "صفِّها بعروض خاصة — تحرير 50% منها يضخ ~" + f"{round(dead_amt*0.5):,} ر سيولة (تقدير)",
                "link": "company-inventory.html", "severity": "mid",
            })

        # ٥) الذمم المدينة (تحصيل متأخر)
        ar_amt, has_ar = _latest_module_field(s, company.id, "finance", "المدينة")
        if has_ar and ar_amt > 0:
            monthly_cost = round(ar_amt * 0.01)
            cards.append({
                "key": "receivables", "icon": "⏳", "label": "التحصيل المتأخر",
                "amount": ar_amt, "kind": "أموال محتجزة عند العملاء",
                "evidence": f"ذمم مدينة {ar_amt:,.0f} ر خارج حسابك",
                "fix": f"شدّد التحصيل — كل شهر تأخير يكلّفك ~{monthly_cost:,} ر فرصة بديلة (تقدير 1%)",
                "link": "company-finance.html", "severity": "high" if total_sales and ar_amt > total_sales * 0.5 else "mid",
            })

        # ٦) مصروفات أعلى من القطاع
        if total_expenses > 0 and total_sales > 0:
            sector = company.sector or "other"
            bench = SECTOR_BENCHMARKS.get(sector, SECTOR_BENCHMARKS["other"])
            bench_ratio = bench["expense_ratio"]["value"]
            actual_ratio = total_expenses / total_sales * 100
            if actual_ratio > bench_ratio + 3:
                excess = round(total_sales * (actual_ratio - bench_ratio) / 100)
                cards.append({
                    "key": "excess_expenses", "icon": "💸", "label": "مصروفات فوق متوسط القطاع",
                    "amount": excess, "kind": "تكلفة زائدة",
                    "evidence": f"نسبتك {round(actual_ratio,1)}% مقابل {bench_ratio}% للقطاع — فائض ~{excess:,} ر",
                    "fix": "افتح الوحدة المالية وحدد أكبر 3 بنود ترتفع عن المعتاد",
                    "link": "company-finance.html", "severity": "high",
                })

        # ٧) المطابقة البنكية: إيداعات فعلية مقابل مبيعات مسجّلة (الأقوى)
        try:
            bank_entries = s.exec(
                select(CompanyModuleEntry).where(
                    CompanyModuleEntry.company_id == company.id,
                    CompanyModuleEntry.module == "bank",
                ).order_by(CompanyModuleEntry.period.desc()).limit(6)
            ).all()
            if bank_entries:
                # مبيعات كل فترة (مجموع الفروع)
                sales_by_period = {}
                all_entries = s.exec(
                    select(CompanyEntry).where(CompanyEntry.company_id == company.id)
                ).all()
                for e in all_entries:
                    sales_by_period[e.period] = sales_by_period.get(e.period, 0) + e.sales
                gaps = []
                total_gap = 0.0
                for be in bank_entries:
                    try:
                        bd = json.loads(be.data) if be.data else {}
                    except Exception:
                        continue
                    deposits = _to_num(bd.get("الإيداعات (ريال)")) or 0
                    period_sales = sales_by_period.get(be.period, 0)
                    if period_sales > 0 and deposits < period_sales * 0.95:
                        gap = round(period_sales - deposits)
                        gaps.append((be.period, gap, round(period_sales), round(deposits)))
                        total_gap += gap
                if gaps:
                    gaps.sort(key=lambda g: g[1], reverse=True)
                    top = gaps[:2]
                    ev = " | ".join(f"{p}: مبيعات {ps:,} مقابل إيداعات {dp:,} (فجوة {g:,})" for p, g, ps, dp in top)
                    cards.append({
                        "key": "bank_gap", "icon": "🏦", "label": "فجوة بنكية (مبيعات لم تصل للبنك)",
                        "amount": round(total_gap), "kind": "خسارة مباشرة",
                        "evidence": f"مطابقة {len(bank_entries)} شهر من كشف حسابك: {ev}",
                        "fix": "راجع مسار الكاش من الكاشير للبنك — قد تكون مدفوعات آجلة مشروعة أو تسرّباً يستحق تحقيقاً",
                        "link": "company-leakage.html", "severity": "high",
                    })
        except Exception:
            pass

        # الترتيب حسب المبلغ
        cards.sort(key=lambda c: c["amount"], reverse=True)
        direct_loss = sum(c["amount"] for c in cards if c["kind"] in ("خسارة مباشرة", "تكلفة زائدة"))
        recoverable = sum(c["amount"] for c in cards if "قابلة" in c["kind"])
        held = sum(c["amount"] for c in cards if "محتجزة" in c["kind"])

        missing = []
        if not has_waste and not has_dead:
            missing.append("بيانات المخزون (الهدر والراكد)")
        if not has_ar:
            missing.append("الذمم المدينة (الوحدة المالية)")
        if total_expenses <= 0:
            missing.append("المصروفات")
        try:
            _has_bank = s.exec(select(CompanyModuleEntry).where(
                CompanyModuleEntry.company_id == company.id,
                CompanyModuleEntry.module == "bank")).first() is not None
            if not _has_bank:
                missing.append("كشف الحساب البنكي (للمطابقة الفعلية)")
        except Exception:
            pass

        return {
            "company": {"name": company.name},
            "total_sales": round(total_sales),
            "cards": cards,
            "direct_loss": round(direct_loss),
            "recoverable": round(recoverable),
            "held_cash": round(held),
            "summary": (
                f"رصد نبّاه {len(cards)} بنداً يسرّب أموالك — خسائر مباشرة وتكاليف زائدة ~{round(direct_loss):,} ر، "
                f"وأموال محتجزة ~{round(held):,} ر."
                if cards else "لا تسرّبات واضحة بالبيانات الحالية — أدخل بيانات أكثر لفحص أعمق."
            ),
            "missing_data": missing,
        }


# ============================================================
# ===== النظرة المستقبلية: سيولة/ربح/نمو/مخاطر بإشارات =====
# ============================================================

@app.get("/company/outlook")
def company_outlook(user: User = Depends(get_current_user)):
    """يجيب: ماذا سيحدث؟ — أربع إشارات بأسباب وتوصيات، محسوبة من البيانات."""
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    with Session(engine) as s:
        company = s.get(Company, user.company_id)
        if not company or company.owner_id != user.id:
            raise HTTPException(403, "غير مصرّح")
        if company.is_active != 1:
            raise HTTPException(402, "شركتك قيد التفعيل")

        branches = s.exec(select(CompanyBranch).where(
            CompanyBranch.company_id == company.id, CompanyBranch.is_active == 1)).all()
        rows = []
        for b in branches:
            ents = s.exec(select(CompanyEntry).where(CompanyEntry.branch_id == b.id)
                          .order_by(CompanyEntry.created_at.desc()).limit(6)).all()
            if ents:
                rows.append(ents)
        if not rows:
            raise HTTPException(400, "لا توجد بيانات بعد")

        total_sales = sum(r[0].sales for r in rows)
        total_expenses = sum(r[0].expenses for r in rows)
        avg_growth = sum(r[0].growth for r in rows) / len(rows)
        signals = []

        def sig(key, label, status, value, reason, action):
            colors = {"green": ("🟢", "#10b981"), "amber": ("🟠", "#f59e0b"), "red": ("🔴", "#ef4444"), "gray": ("⚪", "#9ca3af")}
            e, c = colors[status]
            signals.append({"key": key, "label": label, "emoji": e, "color": c,
                            "value": value, "reason": reason, "action": action})

        # ═══ السيولة ═══
        reserve = getattr(company, "cash_reserve", 0) or 0
        obligations = getattr(company, "monthly_obligations", 0) or 0
        burn = obligations if obligations > 0 else (total_expenses if total_expenses > 0 else 0)
        if reserve > 0 and burn > 0:
            runway = round(reserve / burn, 1)
            days = round(runway * 30)
            if runway >= 6:
                sig("liquidity", "السيولة", "green", f"تكفي ~{runway} شهر",
                    f"الاحتياطي {reserve:,.0f} ر ÷ التزامات {burn:,.0f} ر شهرياً",
                    "وضع مريح — راجعها شهرياً")
            elif runway >= 3:
                sig("liquidity", "السيولة", "amber", f"تكفي ~{runway} شهر",
                    f"بعد ~{days} يوم قد تحتاج ضخ سيولة إذا استمر الوضع",
                    "سرّع التحصيل وأجّل المصروفات غير الحرجة")
            else:
                sig("liquidity", "السيولة", "red", f"تكفي ~{runway} شهر فقط",
                    f"خطر عجز خلال ~{days} يوم",
                    "إجراء فوري: حصّل الذمم، أوقف مصروفات غير أساسية، رتّب تمويلاً جسرياً")
        else:
            sig("liquidity", "السيولة", "gray", "غير محسوبة",
                "الاحتياطي النقدي أو الالتزامات الشهرية غير مدخلة",
                "أدخلها من إعدادات الشركة لتفعيل إنذار السيولة")

        # ═══ الربح ═══
        if total_expenses > 0 and total_sales > 0:
            margin = (total_sales - total_expenses) / total_sales * 100
            # اتجاه الهامش من التاريخ
            old_margins = []
            for ents in rows:
                for e in ents[1:4]:
                    if e.expenses > 0 and e.sales > 0:
                        old_margins.append((e.sales - e.expenses) / e.sales * 100)
            trend = margin - (sum(old_margins)/len(old_margins)) if old_margins else 0
            if margin >= 15 and trend >= -2:
                sig("profit", "الربح", "green", f"هامش {round(margin,1)}%",
                    "فوق الحد الصحي ومستقر", "حافظ على انضباط التكاليف")
            elif margin >= 8:
                proj = round(total_sales * 0.03)
                sig("profit", "الربح", "amber", f"هامش {round(margin,1)}%",
                    f"الهامش رقيق{' ويتراجع' if trend < -2 else ''} — استمراره يهدد الربحية",
                    f"تحسين 3 نقاط يضيف ~{proj:,} ر شهرياً (تقدير)")
            else:
                sig("profit", "الربح", "red", f"هامش {round(margin,1)}%",
                    "تحت الحد الصحي — الشركة تعمل بهامش خطر",
                    "افتح خريطة تسرّب الأموال وعالج أكبر بندين فوراً")
        else:
            sig("profit", "الربح", "gray", "غير محسوب",
                "المصروفات غير مدخلة (بيانات مبيعات فقط)",
                "أدخل المصروفات من الوحدة المالية لتفعيل تحليل الربحية")

        # ═══ النمو ═══
        if avg_growth >= 3:
            sig("growth", "النمو", "green", f"+{round(avg_growth,1)}% شهرياً",
                "المبيعات في اتجاه صاعد", "استثمر في أفضل قنواتك الحالية")
        elif avg_growth >= -3:
            sig("growth", "النمو", "amber", f"{round(avg_growth,1):+}% شهرياً",
                "شبه مستقر — بلا نمو حقيقي",
                "افحص الفروع الأضعف وقارنها بالأقوى")
        else:
            decline_3m = round(total_sales * abs(avg_growth) / 100 * 3)
            sig("growth", "النمو", "red", f"{round(avg_growth,1)}% شهرياً",
                f"استمرار التراجع 3 أشهر يفقدك ~{decline_3m:,} ر (تقدير)",
                "حلّل الأسباب الجذرية فوراً وافحص المنافسين والأحداث")

        # ═══ المخاطر (مركّب) ═══
        risk_points = 0
        risk_reasons = []
        if reserve > 0 and burn > 0 and reserve / burn < 3:
            risk_points += 2; risk_reasons.append("سيولة قصيرة")
        if total_expenses > 0 and total_sales > 0 and (total_sales-total_expenses)/total_sales*100 < 8:
            risk_points += 2; risk_reasons.append("هامش خطر")
        if avg_growth < -5:
            risk_points += 2; risk_reasons.append("تراجع مبيعات")
        weak_branches = sum(1 for r in rows if r[0].branch_score < 40)
        if weak_branches:
            risk_points += 1; risk_reasons.append(f"{weak_branches} فرع ضعيف")
        if risk_points == 0:
            sig("risk", "المخاطر", "green", "منخفضة", "لا مؤشرات خطر حالية بالبيانات المتوفرة", "استمر بالمراقبة الشهرية")
        elif risk_points <= 2:
            sig("risk", "المخاطر", "amber", "متوسطة", "، ".join(risk_reasons), "عالج المؤشر الأصفر قبل ما يحمرّ")
        else:
            sig("risk", "المخاطر", "red", "مرتفعة", "، ".join(risk_reasons), "افتح محرك المخاطر وابدأ بالأعلى درجة")

        return {"signals": signals, "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M")}


# ============================================================
# ===== التقرير الشهري: "وين راحت فلوسك؟" (توليد كسول + كاش) =====
# ============================================================

def _last_complete_period():
    """الشهر المكتمل الأخير: لو نحن في يوليو → يونيو."""
    now = datetime.now()
    y, m = now.year, now.month - 1
    if m == 0:
        y, m = y - 1, 12
    return f"{y:04d}-{m:02d}"


@app.get("/company/monthly-report")
def company_monthly_report(period: str = "", request: Request = None, user: User = Depends(get_current_user)):
    """تقرير شهري تنفيذي: وين راحت الفلوس؟ — يتولّد مرة لكل شهر ويُحفظ."""
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    period = (period or "").strip()[:7] or _last_complete_period()
    with Session(engine) as s:
        company = s.get(Company, user.company_id)
        if not company or company.owner_id != user.id:
            raise HTTPException(403, "غير مصرّح")
        if company.is_active != 1:
            raise HTTPException(402, "شركتك قيد التفعيل")

        # ═══ أرقام الشهر (مجموع الفروع للفترة) ═══
        entries = s.exec(select(CompanyEntry).where(
            CompanyEntry.company_id == company.id, CompanyEntry.period == period)).all()
        if not entries:
            raise HTTPException(400, f"لا توجد بيانات لشهر {period} — ارفع بيانات هذا الشهر أولاً")
        # آخر إدخال لكل فرع في الفترة
        latest = {}
        for e in sorted(entries, key=lambda x: x.created_at):
            latest[e.branch_id] = e
        rows = list(latest.values())
        sales = round(sum(e.sales for e in rows))
        expenses = round(sum(e.expenses for e in rows))
        discounts = round(sum(e.discounts for e in rows))
        profit = round(sales - expenses) if expenses > 0 else None

        # الشهر السابق للمقارنة
        y, m = int(period[:4]), int(period[5:7])
        pm = f"{y-1:04d}-12" if m == 1 else f"{y:04d}-{m-1:02d}"
        prev_entries = s.exec(select(CompanyEntry).where(
            CompanyEntry.company_id == company.id, CompanyEntry.period == pm)).all()
        prev_latest = {}
        for e in sorted(prev_entries, key=lambda x: x.created_at):
            prev_latest[e.branch_id] = e
        prev_sales = round(sum(e.sales for e in prev_latest.values())) if prev_latest else 0
        change_pct = round((sales - prev_sales) / prev_sales * 100, 1) if prev_sales else None

        # أفضل وأضعف فرع
        best = max(rows, key=lambda e: e.branch_score) if rows else None
        worst = min(rows, key=lambda e: e.branch_score) if rows else None

        # ═══ البنك (لو مرفوع) ═══
        bank = None
        be = s.exec(select(CompanyModuleEntry).where(
            CompanyModuleEntry.company_id == company.id,
            CompanyModuleEntry.module == "bank",
            CompanyModuleEntry.period == period)).first()
        if be:
            try:
                bd = json.loads(be.data)
                deposits = _to_num(bd.get("الإيداعات (ريال)")) or 0
                bank = {"deposits": round(deposits),
                        "gap": round(sales - deposits) if sales > deposits else 0}
            except Exception:
                pass

        # ═══ قرارات الشهر ═══
        month_start = datetime(y, m, 1)
        month_end = datetime(y + (1 if m == 12 else 0), 1 if m == 12 else m + 1, 1)
        decs = s.exec(select(CompanyDecision).where(
            CompanyDecision.company_id == company.id)).all()
        opened = [d for d in decs if d.created_at and month_start <= d.created_at < month_end]
        closed = [d for d in decs if d.closed_at and month_start <= d.closed_at < month_end and d.status == "done"]

        # ═══ الكاش: هل التقرير متولّد سابقاً؟ ═══
        cache_title = f"التقرير الشهري {period}"
        cached = s.exec(select(CompanyMemory).where(
            CompanyMemory.company_id == company.id,
            CompanyMemory.kind == "report",
            CompanyMemory.title == cache_title)).first()

        if cached and cached.content:
            narrative = cached.content
        else:
            branches_txt = "\n".join(
                f"- {e.branch_name}: مبيعات {round(e.sales):,} ر" +
                (f"، مصروفات {round(e.expenses):,} ر" if e.expenses > 0 else "") +
                f"، خصومات {round(e.discounts):,} ر، مؤشر {e.branch_score}/100، نمو {e.growth}%"
                for e in rows)
            bank_txt = ""
            if bank:
                bank_txt = f"\n# المطابقة البنكية:\nإيداعات فعلية {bank['deposits']:,} ر مقابل مبيعات {sales:,} ر" + (f" — فجوة {bank['gap']:,} ر" if bank['gap'] > 0 else " — متطابقة تقريباً")
            decs_txt = ""
            if opened or closed:
                decs_txt = "\n# قرارات الشهر:\n" + "\n".join(
                    [f"- اعتُمد: {d.title}" for d in opened[:4]] +
                    [f"- أُنجز: {d.title} ({d.result_note or 'بدون ملاحظة'})" for d in closed[:4]])
            prompt = f"""اكتب "التقرير الشهري: وين راحت فلوسك؟" لشركة "{company.name}" عن شهر {period}.

# أرقام الشهر:
- المبيعات: {sales:,} ر{f" (الشهر السابق {prev_sales:,} ر، تغيّر {change_pct:+}%)" if change_pct is not None else ""}
- المصروفات: {f"{expenses:,} ر" if expenses > 0 else "غير مدخلة"}
- الربح: {f"{profit:,} ر" if profit is not None else "غير محسوب (المصروفات ناقصة)"}
- الخصومات: {discounts:,} ر
- أفضل فرع: {best.branch_name} ({best.branch_score}/100) | أضعف فرع: {worst.branch_name} ({worst.branch_score}/100)

# تفاصيل الفروع:
{branches_txt}{bank_txt}{decs_txt}

اكتب تقريراً تنفيذياً موجزاً (300-450 كلمة) بأقسام: ① أين ذهبت الفلوس هذا الشهر (بالريال) ② أهم 3 ملاحظات ③ قرار الشهر القادم الواحد الأهم بأثره المالي. التزم بإطارك الصارم."""
            narrative = company_gemini(prompt, company, lang=get_lang(request)) or "تعذّر توليد السرد — الأرقام أدناه صحيحة."
            save_memory(company.id, "report", cache_title, narrative)

        return {
            "period": period,
            "company": {"name": company.name},
            "kpis": {
                "sales": sales, "prev_sales": prev_sales, "change_pct": change_pct,
                "expenses": expenses if expenses > 0 else None,
                "profit": profit, "discounts": discounts,
            },
            "best_branch": {"name": best.branch_name, "score": best.branch_score} if best else None,
            "worst_branch": {"name": worst.branch_name, "score": worst.branch_score} if worst else None,
            "bank": bank,
            "decisions": {"opened": len(opened), "closed": len(closed)},
            "narrative": narrative,
            "cached": bool(cached),
        }


@app.get("/company-monthly-report.html")
def page_company_monthly_report():
    return FileResponse("company-monthly-report.html")


# ============================================================
# ===== ملف الشركة: نبّاه يعرف الشركة نفسها ويكيّف التحليل =====
# ============================================================

PRIORITY_NAMES = {
    "growth": "النمو والتوسّع",
    "profit": "الربحية",
    "liquidity": "السيولة والتدفق النقدي",
    "efficiency": "الكفاءة التشغيلية",
}


def build_company_profile_context(company) -> str:
    """يبني وصفاً نصياً لملف الشركة يُحقن في تحليل Gemini ليكيّف التوصيات."""
    parts = []
    parts.append(f"اسم الشركة: {company.name}")
    parts.append(f"النشاط/القطاع: {SECTOR_NAMES.get(company.sector, 'غير محدد')}")
    if getattr(company, "employees", 0):
        parts.append(f"عدد الموظفين: {company.employees}")
    if getattr(company, "annual_revenue", 0):
        parts.append(f"الإيرادات السنوية التقريبية: {company.annual_revenue:,.0f} {company.currency}")
    if getattr(company, "target_margin", 0):
        parts.append(f"هامش الربح المستهدف: {company.target_margin}%")
    prio = getattr(company, "top_priority", "profit")
    parts.append(f"الأولوية القصوى للإدارة: {PRIORITY_NAMES.get(prio, prio)}")
    # أهداف مخصّصة
    try:
        goals = json.loads(getattr(company, "goals_json", "{}") or "{}")
        if goals:
            gtxt = "، ".join(f"{k}: {v}" for k, v in goals.items() if v)
            if gtxt:
                parts.append(f"أهداف محددة: {gtxt}")
    except Exception:
        pass
    profile = "\n".join("- " + p for p in parts)
    return (
        "\n\n# ملف الشركة (استخدمه لتكييف تحليلك وأولوياتك):\n" + profile +
        f"\n\nملاحظة مهمة: وجّه توصياتك بما يخدم أولوية الإدارة القصوى ({PRIORITY_NAMES.get(prio, prio)}) أولاً، "
        "وراعِ حجم الشركة وقطاعها عند اقتراح الحلول."
    )


@app.get("/company/profile")
def get_company_profile(user: User = Depends(get_current_user)):
    """يجلب ملف الشركة الكامل."""
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    with Session(engine) as s:
        company = s.get(Company, user.company_id)
        if not company or company.owner_id != user.id:
            raise HTTPException(403, "غير مصرّح")
        try:
            goals = json.loads(company.goals_json or "{}")
        except Exception:
            goals = {}
        try:
            alerts = json.loads(company.alerts_json or "{}")
        except Exception:
            alerts = {}
        return {
            "name": company.name,
            "sector": company.sector,
            "sector_name": SECTOR_NAMES.get(company.sector, ""),
            "employees": getattr(company, "employees", 0),
            "annual_revenue": getattr(company, "annual_revenue", 0),
            "target_margin": getattr(company, "target_margin", 0),
            "fiscal_year_start": getattr(company, "fiscal_year_start", 1),
            "currency": getattr(company, "currency", "SAR"),
            "country": getattr(company, "country", "SA"),
            "cash_reserve": company.cash_reserve,
            "monthly_obligations": company.monthly_obligations,
            "top_priority": getattr(company, "top_priority", "profit"),
            "goals": goals,
            "alerts": alerts,
            "sectors_available": SECTOR_NAMES,
            "priorities_available": PRIORITY_NAMES,
        }


@app.post("/company/profile")
async def save_company_profile(request: Request, user: User = Depends(get_current_user)):
    """يحفظ ملف الشركة (كل الأقسام الستة)."""
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    data = await request.json()
    with Session(engine) as s:
        company = s.get(Company, user.company_id)
        if not company or company.owner_id != user.id:
            raise HTTPException(403, "غير مصرّح")

        # ① معلومات الشركة
        if "name" in data and str(data["name"]).strip():
            company.name = str(data["name"]).strip()[:100]
        if "sector" in data and data["sector"] in SECTOR_NAMES:
            company.sector = data["sector"]
        if "currency" in data:
            company.currency = str(data["currency"]).strip()[:8] or "SAR"
        if "country" in data:
            company.country = str(data["country"]).strip()[:8] or "SA"
        if "fiscal_year_start" in data:
            try:
                m = int(data["fiscal_year_start"])
                if 1 <= m <= 12:
                    company.fiscal_year_start = m
            except Exception:
                pass
        # معلومات الحجم
        for fld, attr in [("employees", "employees")]:
            if fld in data:
                try:
                    company.__setattr__(attr, int(data[fld]))
                except Exception:
                    pass
        for fld in ["annual_revenue", "target_margin", "cash_reserve", "monthly_obligations"]:
            if fld in data:
                v = _to_num(data[fld])
                if v is not None:
                    company.__setattr__(fld, v)
        # ⑤ الأهداف والأولوية
        if "top_priority" in data and data["top_priority"] in PRIORITY_NAMES:
            company.top_priority = data["top_priority"]
        if "goals" in data and isinstance(data["goals"], dict):
            company.goals_json = json.dumps(data["goals"], ensure_ascii=False)[:2000]
        # ⑥ التنبيهات
        if "alerts" in data and isinstance(data["alerts"], dict):
            company.alerts_json = json.dumps(data["alerts"], ensure_ascii=False)[:2000]

        s.add(company)
        s.commit()
        save_memory(company.id, "goals", "تحديث ملف الشركة",
                    f"الأولوية: {PRIORITY_NAMES.get(company.top_priority, '')} · القطاع: {SECTOR_NAMES.get(company.sector, '')}")
        return {"ok": True, "message": "تم حفظ ملف الشركة بنجاح"}


@app.get("/company-settings.html")
def page_company_settings():
    return FileResponse("company-settings.html")


@app.get("/company/sector-intelligence")
def get_sector_intelligence(user: User = Depends(get_current_user)):
    """يعرض مؤشرات ومصطلحات وتوصيات القطاع — ليرى العميل أن نبّاه يتكيّف مع قطاعه."""
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    with Session(engine) as s:
        company = s.get(Company, user.company_id)
        if not company or company.owner_id != user.id:
            raise HTTPException(403, "غير مصرّح")
        si = SECTOR_INTELLIGENCE.get(company.sector, SECTOR_INTELLIGENCE["other"])
        return {
            "sector": company.sector,
            "sector_name": SECTOR_NAMES.get(company.sector, "النشاط"),
            "kpis": si["kpis"],
            "watch": si["watch"],
            "advice": si["advice"],
        }


@app.get("/company/audit-log")
def get_audit_log(request: Request, user: User = Depends(get_current_user)):
    """سجل التدقيق: آخر ١٠٠ إجراء على الشركة (للمالك فقط — للمساءلة والثقة)."""
    if not user.company_id:
        raise HTTPException(403, "لا توجد شركة نشطة")
    with Session(engine) as s:
        company = s.get(Company, user.company_id)
        if not company or company.owner_id != user.id:
            raise HTTPException(403, "غير مصرّح — سجل التدقيق للمالك فقط")
        logs = s.exec(
            select(AuditLog).where(AuditLog.company_id == company.id)
            .order_by(AuditLog.created_at.desc()).limit(100)
        ).all()
        ACTION_LABELS = {
            "login": "تسجيل دخول", "create": "إنشاء", "update": "تعديل",
            "delete": "حذف", "upload": "رفع بيانات", "analyze": "طلب تحليل",
            "decision": "قرار", "export": "تصدير", "settings": "تغيير إعدادات",
        }
        return {
            "count": len(logs),
            "logs": [{
                "user_name": l.user_name or "—",
                "action": l.action,
                "action_label": ACTION_LABELS.get(l.action, l.action),
                "target": l.target,
                "details": l.details,
                "created_at": l.created_at.isoformat() if l.created_at else "",
            } for l in logs],
        }


@app.get("/company-audit.html")
def page_company_audit():
    return FileResponse("company-audit.html")
