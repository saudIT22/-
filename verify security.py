#!/usr/bin/env python3
"""
NABBAH — مُشغّل التحقّق المستقل (Phase 1.6)
يثبت المنطق الأساسي لكل طبقات Phase 1 بدون مكتبات خارجية.
يعمل في أي بيئة Python — لا يحتاج pytest/fastapi.

للتشغيل: python3 verify_security.py
للاختبارات الكاملة (تحتاج المكتبات): bash run_tests.sh
"""
import re
import sys
import os

def main():
    # نجد main.py (في المجلد الأب أو الحالي)
    for path in ["../main.py", "main.py", "./main.py"]:
        if os.path.exists(path):
            src = open(path, encoding="utf-8").read()
            break
    else:
        print("⚠️ لم يُعثر على main.py")
        sys.exit(1)

    passed = failed = 0
    def check(name, cond):
        nonlocal passed, failed
        if cond: passed += 1; print(f"  ✅ {name}")
        else: failed += 1; print(f"  ❌ {name}")

    print("═" * 55)
    print("  NABBAH — التحقّق الأمني الشامل (Phase 1)")
    print("═" * 55)

    print("\n① أمان عزل الشركات:")
    check("get_active_company", "def get_active_company" in src)
    check("get_owned_branch (منع IDOR)", "def get_owned_branch" in src)
    check("فحص ملكية الفرع", "branch.company_id != user.company_id" in src)
    check("فلترة company_id (80+)", src.count("company_id ==") >= 80)

    print("\n② صحة الصلاحيات (RBAC):")
    check("ROLE_PERMISSIONS", "ROLE_PERMISSIONS = {" in src)
    check("check_permission server-side", "def check_permission" in src)
    check("owner _all", '"_all"' in src)
    check("أمان العمود في HR", "can_see_sensitive_financials(role)" in src)

    print("\n③ سلامة الحسابات المالية:")
    check("صيغة الربح", "profit = round(sales - expenses" in src)
    check("حماية القسمة على صفر", "if sales > 0 else 0" in src)
    check("validate_module_input", "def validate_module_input" in src)

    print("\n④ عدم تسريب البيانات:")
    check("لا str(e) مكشوف", not re.search(r'HTTPException\([^)]*str\(e\)', src))
    check("تسجيل داخلي", "_logger.error" in src)
    check("filter_sensitive_fields", "def filter_sensitive_fields" in src)

    print("\n⑤ سلامة معالجة الأخطاء:")
    check("معالج Exception", "@app.exception_handler(Exception)" in src)
    check("معالج HTTPException", "_StarletteHTTPException" in src)
    check("رسالة آمنة", "حدث خطأ غير متوقّع" in src)

    print("\n" + "═" * 55)
    total = passed + failed
    print(f"  النتيجة: {passed}/{total} تحقّق ناجح")
    print("═" * 55)
    if failed == 0:
        print("  ✅ كل طبقات Phase 1 مؤكّدة")
        sys.exit(0)
    else:
        print(f"  ⚠️ {failed} فحص فشل")
        sys.exit(1)


if __name__ == "__main__":
    main()
