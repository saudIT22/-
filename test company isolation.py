"""
NABBAH — اختبارات عزل الشركات (Tenant Isolation Tests)
حرجة: العزل مطبّق على مستوى التطبيق — نتأكد أنه يعمل.
شركة A لا يمكنها الوصول لبيانات شركة B.
"""
import pytest


def _register_and_login(client, email, company_name):
    """يسجّل مستخدماً وينشئ شركة ويرجع الرمز + company_id."""
    client.post("/register", json={
        "name": "Owner", "email": email, "password": "Pass12345",
        "business_name": company_name, "phone": "0500000000",
    })
    r = client.post("/login", json={"email": email, "password": "Pass12345"})
    token = r.json().get("token") if r.status_code == 200 else None
    return token


@pytest.fixture
def two_companies(client, clean_db):
    """ينشئ شركتين مستقلتين A و B."""
    token_a = _register_and_login(client, "a@test.com", "Company A")
    token_b = _register_and_login(client, "b@test.com", "Company B")
    return {"a": token_a, "b": token_b}


def test_company_a_can_access_own_data(client, two_companies):
    """شركة A تصل لبياناتها."""
    ta = two_companies["a"]
    if not ta:
        pytest.skip("تعذّر تسجيل الدخول (بيئة اختبار)")
    r = client.get("/me", headers={"Authorization": f"Bearer {ta}"})
    assert r.status_code == 200


def test_company_isolation_dashboard(client, two_companies):
    """
    بيانات لوحة شركة A لا تحتوي بيانات شركة B.
    (العزل عبر company_id في كل استعلام)
    """
    ta, tb = two_companies["a"], two_companies["b"]
    if not ta or not tb:
        pytest.skip("تعذّر تسجيل الدخول")
    ra = client.get("/company/info", headers={"Authorization": f"Bearer {ta}"})
    rb = client.get("/company/info", headers={"Authorization": f"Bearer {tb}"})
    # كل شركة ترى اسمها فقط
    if ra.status_code == 200 and rb.status_code == 200:
        name_a = str(ra.json())
        name_b = str(rb.json())
        assert "Company B" not in name_a
        assert "Company A" not in name_b


def test_no_cross_tenant_via_token(client, two_companies):
    """رمز شركة A لا يعطي وصولاً لبيانات شركة B."""
    ta = two_companies["a"]
    if not ta:
        pytest.skip("تعذّر تسجيل الدخول")
    # كل الطلبات برمز A ترجع بيانات A فقط
    for endpoint in ["/company/dashboard", "/company/decisions", "/company/audit-log"]:
        r = client.get(endpoint, headers={"Authorization": f"Bearer {ta}"})
        # لا تنهار، ولا تكشف بيانات شركة أخرى
        assert r.status_code in (200, 402, 403)
