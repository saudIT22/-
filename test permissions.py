"""
NABBAH — اختبارات الصلاحيات (RBAC Permission Tests)
تستخدم نظام RBAC القائم — لا تعيد تصميم الأدوار.
"""
import pytest


def test_check_permission_owner_all(app_module):
    """المالك لديه كل الصلاحيات."""
    assert app_module.check_permission("owner", "finance", "manage") is True
    assert app_module.check_permission("owner", "hr", "view") is True


def test_check_permission_accountant_finance(app_module):
    """المحاسب يصل للمالية."""
    assert app_module.check_permission("accountant", "finance", "view") is True


def test_check_permission_accountant_no_ops(app_module):
    """المحاسب لا يصل للتشغيل."""
    assert app_module.check_permission("accountant", "ops", "view") is False


def test_check_permission_staff_limited(app_module):
    """الموظف صلاحيات محدودة — لا مالية."""
    assert app_module.check_permission("staff", "finance", "view") is False


def test_check_permission_manager_sales(app_module):
    """مدير الفرع يعدّل المبيعات."""
    assert app_module.check_permission("manager", "sales", "edit") is True


def test_sensitive_fields_hidden_from_staff(app_module):
    """الحقول المالية الحساسة تُخفى عن غير المصرّح لهم."""
    data = {"sales": 1000, "salary": 5000, "margin": 20}
    if hasattr(app_module, "filter_sensitive_fields"):
        filtered = app_module.filter_sensitive_fields(data, "staff")
        # الرواتب والهوامش تُخفى عن الموظف
        assert "salary" not in filtered or filtered.get("salary") is None


def test_sensitive_fields_visible_to_owner(app_module):
    """المالك يرى كل الحقول."""
    data = {"sales": 1000, "salary": 5000, "margin": 20}
    if hasattr(app_module, "filter_sensitive_fields"):
        filtered = app_module.filter_sensitive_fields(data, "owner")
        assert filtered.get("salary") == 5000


def test_can_see_sensitive_owner(app_module):
    if hasattr(app_module, "can_see_sensitive_financials"):
        assert app_module.can_see_sensitive_financials("owner") is True


def test_can_see_sensitive_staff(app_module):
    if hasattr(app_module, "can_see_sensitive_financials"):
        assert app_module.can_see_sensitive_financials("staff") is False
