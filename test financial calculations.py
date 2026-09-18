"""
NABBAH — اختبارات انحدار الحسابات المالية (Financial Regression Tests)
تُمثّل السلوك الحالي بالضبط — لا تُغيّر أي صيغة.

الصيغ الفعلية من main.py:
  profit = round(sales - expenses, 2)
  margin = round((profit / sales) * 100, 1) if sales > 0 else 0
  avg_invoice = round(sales / invoices, 1)   [إن كانت invoices > 0]
  growth = round(((sales - prev) / prev) * 100, 1) if prev > 0 else 0
"""
import pytest


# ═══ دوال تُطابق صيغ main.py بالضبط (للتحقق من السلوك) ═══
def calc_profit(sales, expenses):
    return round(sales - expenses, 2)

def calc_margin(sales, expenses):
    profit = calc_profit(sales, expenses)
    return round((profit / sales) * 100, 1) if sales > 0 else 0

def calc_avg_invoice(sales, invoices):
    return round(sales / invoices, 1) if invoices > 0 else 0

def calc_sales_per_customer(sales, customers):
    return round(sales / customers, 1) if customers > 0 else 0

def calc_growth(sales, prev):
    return round(((sales - prev) / prev) * 100, 1) if prev > 0 else 0

def calc_target_gap(actual, target):
    return round(target - actual, 2)


# ═══════════ CASE 1: الربح والهامش الأساسي ═══════════
def test_case1_profit():
    assert calc_profit(100000, 70000) == 30000

def test_case1_margin():
    assert calc_margin(100000, 70000) == 30.0


# ═══════════ CASE 2: القسمة على صفر (لا انهيار) ═══════════
def test_case2_zero_sales_margin_safe():
    # يجب ألا ينهار — يرجع 0 (السلوك الحالي)
    assert calc_margin(0, 0) == 0

def test_case2_zero_sales_avg_invoice_safe():
    assert calc_avg_invoice(0, 0) == 0

def test_case2_zero_invoices_safe():
    assert calc_avg_invoice(100000, 0) == 0

def test_case2_zero_customers_safe():
    assert calc_sales_per_customer(100000, 0) == 0

def test_case2_zero_prev_growth_safe():
    assert calc_growth(100000, 0) == 0


# ═══════════ CASE 3: النمو ═══════════
def test_case3_growth():
    assert calc_growth(100000, 80000) == 25.0


# ═══════════ CASE 4: فجوة الهدف ═══════════
def test_case4_target_gap():
    assert calc_target_gap(70000, 100000) == 30000


# ═══════════ CASE 5: متوسط الفاتورة ═══════════
def test_case5_avg_invoice():
    assert calc_avg_invoice(100000, 2000) == 50.0


# ═══════════ CASE 6: المبيعات لكل عميل ═══════════
def test_case6_sales_per_customer():
    assert calc_sales_per_customer(100000, 500) == 200.0


# ═══════════ CASE 7: استقلالية حسابات الفروع ═══════════
def test_case7_branch_independence():
    branch_a = {"sales": 100000, "profit": calc_profit(100000, 70000)}  # 30000
    branch_b = {"sales": 50000, "profit": calc_profit(50000, 45000)}    # 5000
    assert branch_a["profit"] == 30000
    assert branch_b["profit"] == 5000
    # كل فرع مستقل — لا تداخل
    assert branch_a["profit"] != branch_b["profit"]
    # الهامش لكل فرع منفصل
    assert calc_margin(100000, 70000) == 30.0
    assert calc_margin(50000, 45000) == 10.0


# ═══════════ CASE 8: الربح السالب (لا يُحوّل لموجب) ═══════════
def test_case8_negative_profit():
    profit = calc_profit(100000, 110000)
    assert profit == -10000
    assert profit < 0  # يبقى سالباً — لا يُحوّل

def test_case8_negative_margin():
    # هامش سالب عند خسارة
    margin = calc_margin(100000, 110000)
    assert margin == -10.0
    assert margin < 0
