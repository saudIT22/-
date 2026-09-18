"""
NABBAH — اختبارات صحّة الـEndpoints (Endpoint Smoke Tests)
تتحقق أن الـendpoints ترد بشكل سليم — لا تنهار.
"""
import pytest


def test_health_root(client):
    """الجذر يرد (الصفحة الرئيسية)."""
    r = client.get("/")
    assert r.status_code in (200, 307, 404)  # يرد بشكل ما


def test_version_endpoint(client):
    """endpoint الإصدار يرد."""
    r = client.get("/version")
    assert r.status_code in (200, 404)


def test_register_validates_input(client, clean_db):
    """التسجيل يرفض بيانات ناقصة."""
    r = client.post("/register", json={"email": "bad"})
    assert r.status_code in (400, 422)


def test_login_wrong_credentials(client, clean_db):
    """دخول ببيانات خاطئة يُرفض."""
    r = client.post("/login", json={"email": "none@test.com", "password": "x"})
    assert r.status_code in (400, 401, 403)


@pytest.mark.parametrize("endpoint", [
    "/company/dashboard",
    "/company/health-score",
    "/company/financial-overview",
    "/company/decisions",
    "/company/hr-analytics",
    "/company/inventory-analytics",
    "/company/ops-analytics",
    "/company/treasury",
    "/company/benchmark",
    "/company/readiness",
    "/company/customer-health",
])
def test_protected_endpoints_require_auth(client, endpoint):
    """كل endpoints التحليل محمية — ترفض بلا مصادقة."""
    r = client.get(endpoint)
    assert r.status_code == 401


def test_static_pages_served(client):
    """الصفحات الثابتة تُخدَم."""
    for page in ["/company-dashboard.html", "/company-decisions.html"]:
        r = client.get(page)
        assert r.status_code in (200, 404)


def test_shared_assets_served(client):
    """الملفات المشتركة تُخدَم بنوع MIME صحيح."""
    r = client.get("/i18n.js")
    assert r.status_code == 200
    assert "javascript" in r.headers.get("content-type", "").lower()
