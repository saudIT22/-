"""
NABBAH — اختبارات انحدار المصادقة (Auth Regression Tests)
تتحقق من السلوك الحالي — لا تعيد تصميم المصادقة.
"""
import pytest


def test_password_never_plaintext(app_module):
    """كلمات المرور تُخزّن مجزّأة (bcrypt) لا نصاً صريحاً."""
    hashed = app_module.hash_password("MySecret123")
    assert hashed != "MySecret123"
    assert hashed.startswith("$2")  # علامة bcrypt


def test_password_verify_correct(app_module):
    hashed = app_module.hash_password("MySecret123")
    assert app_module.verify_password("MySecret123", hashed) is True


def test_password_verify_wrong(app_module):
    hashed = app_module.hash_password("MySecret123")
    assert app_module.verify_password("WrongPassword", hashed) is False


def test_jwt_has_expiration(app_module):
    """رمز JWT يحتوي تاريخ انتهاء (exp)."""
    import jwt as _jwt
    token = app_module.create_token(1)
    payload = _jwt.decode(token, app_module.SECRET_KEY, algorithms=["HS256"])
    assert "exp" in payload


def test_protected_endpoint_requires_auth(client):
    """endpoint محمي يرفض الطلب بلا رمز."""
    r = client.get("/me")
    assert r.status_code == 401


def test_invalid_token_rejected(client):
    """رمز غير صالح يُرفض."""
    r = client.get("/me", headers={"Authorization": "Bearer invalid.token.here"})
    assert r.status_code == 401


def test_malformed_auth_header_rejected(client):
    """ترويسة مصادقة غير صحيحة تُرفض."""
    r = client.get("/me", headers={"Authorization": "NotBearer xyz"})
    assert r.status_code == 401


def test_admin_endpoint_rejects_normal_user(client):
    """endpoints الإدارة ترفض بلا ADMIN_KEY صحيح."""
    r = client.get("/admin/companies", headers={"X-Admin-Key": "wrong-key"})
    assert r.status_code in (401, 403)
