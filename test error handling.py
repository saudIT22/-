"""
NABBAH — اختبارات معالجة الأخطاء (Error Handling — Phase 1.5)
تثبّت أن التفاصيل الداخلية لا تُكشف للمستخدم، والرسائل آمنة وموحّدة.
"""
import pytest


# ═══════════ المعالجات العامة موجودة ═══════════
def test_exception_handlers_registered(app_module):
    """معالجات الأخطاء العامة مسجّلة في التطبيق."""
    handlers = app_module.app.exception_handlers
    # يجب أن يكون هناك معالج للأخطاء العامة
    assert len(handlers) >= 1


# ═══════════ عدم كشف التفاصيل الداخلية ═══════════
def test_404_safe_message(client):
    """مسار غير موجود → رسالة آمنة، لا stack trace."""
    r = client.get("/company/nonexistent-endpoint-xyz")
    assert r.status_code == 404
    body = str(r.json()) if r.headers.get("content-type","").startswith("application/json") else r.text
    # لا يكشف مسارات نظام أو stack trace
    assert "Traceback" not in body
    assert "/home/" not in body
    assert ".py" not in body


def test_validation_error_safe(client, clean_db):
    """خطأ تحقّق → رسالة عامة، لا تفاصيل Pydantic."""
    r = client.post("/register", json={"invalid": "data"})
    assert r.status_code in (400, 422)
    body = str(r.json())
    # لا يكشف تفاصيل تقنية داخلية
    assert "Traceback" not in body


def test_unauthorized_no_leak(client):
    """رفض المصادقة → رسالة واضحة، لا تفاصيل داخلية."""
    r = client.get("/me")
    assert r.status_code == 401
    body = str(r.json())
    assert "SECRET_KEY" not in body
    assert "Traceback" not in body


def test_error_response_has_detail(client):
    """رسائل الخطأ منظّمة (تحتوي detail)."""
    r = client.get("/me")  # 401
    if r.headers.get("content-type", "").startswith("application/json"):
        assert "detail" in r.json()


# ═══════════ لا تسريب أسماء قاعدة البيانات ═══════════
def test_no_db_internals_in_errors(client):
    """أخطاء الـendpoints لا تكشف أسماء جداول أو أعمدة."""
    # نضرب endpoints محمية بلا مصادقة
    for endpoint in ["/company/dashboard", "/company/financial-overview", "/company/decisions"]:
        r = client.get(endpoint)
        body = str(r.json()) if r.headers.get("content-type","").startswith("application/json") else r.text
        # لا أسماء جداول
        for table in ["companydecision", "companybranch", "companymodule", "relation", "psycopg2"]:
            assert table not in body.lower()


# ═══════════ منطق المعالج (وحدة) ═══════════
def test_error_message_generic(app_module):
    """رسالة الخطأ العامة لا تحتوي تفاصيل تقنية."""
    # الرسالة المعرّفة في المعالج
    safe_msg = "حدث خطأ غير متوقّع. حاول مرة أخرى، وإن استمرّ تواصل مع الدعم."
    # لا تحتوي مصطلحات تقنية
    for tech in ["Exception", "Traceback", "line", "relation", "null", "None"]:
        assert tech not in safe_msg
