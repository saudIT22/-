"""مولّد بيانات اختبار واقعية لمحرك المخاطر: يشغّل المحركات الحقيقية (2.5→3.2) على بيانات مولّدة
بنمط ضغط معروف: مورد مهيمن متأخر + ارتفاع أسعار + نفاد أصناف + تحصيل بطيء + عملاء مفقودون + أخطاء فواتير."""
import random
from datetime import date, timedelta

import sales_engine, inventory_engine, purchases_engine, cashflow_engine, hr_engine, ops_engine, finance_engine, leakage_engine, tax_engine

TODAY = date(2026, 9, 30)
BRANCHES = ["الرياض", "جدة", "الدمام"]
SKUS = {f"P{i}": (40 + 10 * i, 25 + 6 * i) for i in range(1, 9)}   # sku: (price, cost)


def months(n=12):
    out, y, m = [], TODAY.year, TODAY.month
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return sorted(out)


def build(seed=7, stressed=True):
    rnd = random.Random(seed)
    start = TODAY - timedelta(days=364)
    customers = [f"عميل {i}" for i in range(1, 61)]
    lost = set(customers[:12]) if stressed else set()
    sales = []
    n = 0
    d = start
    while d <= TODAY:
        recent = (TODAY - d).days < 90
        for b in BRANCHES:
            for _ in range(4):
                c = rnd.choice(customers)
                if recent and c in lost:
                    c = rnd.choice(customers[12:])
                sku = rnd.choice(list(SKUS))
                q = rnd.randint(1, 6) * (3 if c in lost else 1)
                g = SKUS[sku][0] * q
                disc = round(g * (0.12 if (stressed and recent and b == "جدة") else 0.03), 2)
                ret = round(g * 0.08, 2) if rnd.random() < (0.12 if stressed and recent else 0.03) else 0
                n += 1
                sales.append({"date": d.isoformat(), "period": d.strftime("%Y-%m"), "branch_name": b, "reference": f"INV-{n}",
                              "channel": "متجر", "product_sku": sku, "category": "عام", "quantity": q, "gross_sales": g,
                              "discounts": disc, "discount": disc, "returns": ret, "net_sales": round(g - disc - ret, 2),
                              "vat": round((g - disc - ret) * 0.15, 2), "payment_method": "نقد", "customer_name": c, "promotion": ""})
        d += timedelta(days=1)
    ms = months()
    # تكلفة ترتفع في آخر شهرين (ضغط الهامش)
    cost_map = {}
    for sku, (_, cost) in SKUS.items():
        for m in ms:
            cost_map[(sku, m)] = cost * (1.18 if stressed and m >= ms[-2] else 1.0)
    # المشتريات
    purchases = []
    for i, m in enumerate(ms):
        for sku, (_, cost) in SKUS.items():
            for b in BRANCHES:
                sup = "المورد الأول" if (rnd.random() < (0.78 if stressed else 0.35)) else rnd.choice(["المورد الثاني", "المورد الثالث"])
                pd_ = date(int(m[:4]), int(m[5:]), 5)
                exp = pd_ + timedelta(days=7)
                late = stressed and sup == "المورد الأول" and rnd.random() < 0.45
                uc = round(cost * (1.15 if stressed and m == ms[-1] else 1.0), 2)
                purchases.append({"date": pd_.isoformat(), "supplier_name": sup, "product_sku": sku, "category": "عام", "branch_name": b,
                                  "quantity": 50, "unit_cost": uc, "total_cost": round(uc * 50, 2), "reference": f"PO-{m}-{sku}-{b}",
                                  "status": "مستلم", "expected_date": exp.isoformat(),
                                  "received_date": (exp + timedelta(days=6 if late else 0)).isoformat(), "received_qty": 50})
    # المخزون: نفاد في الفرع الأول لعدة أصناف
    snaps = []
    for m in ms[-4:]:
        for sku, (_, cost) in SKUS.items():
            for b in BRANCHES:
                out = stressed and b == "الرياض" and sku in ("P1", "P2", "P3", "P4") and m >= ms[-2]
                q = 0 if out else 120
                snaps.append({"period": m, "branch_name": b, "product_sku": sku, "opening_qty": 100, "purchases_qty": 50,
                              "sold_qty": 60, "adjustments_qty": 0, "closing_qty": q, "closing_value": q * cost})
    # الحركات النقدية: استنزاف شهري → مدة سيولة قصيرة
    mv, bal = [], 600000.0
    for m in ms:
        dd = f"{m}-15"
        inn, out = (230000, 300000) if stressed else (300000, 260000)
        bal += inn - out
        mv.append({"date": dd, "direction": "in", "amount": inn, "movement_type": "تحصيل", "category": "sales_collection", "branch_name": None, "balance": None})
        mv.append({"date": f"{m}-20", "direction": "out", "amount": out, "movement_type": "دفع", "category": "suppliers", "branch_name": None, "balance": bal})
    # الذمم: متأخرة كثيراً
    ar = []
    for i in range(40):
        inv_d = TODAY - timedelta(days=rnd.randint(10, 150))
        due = inv_d + timedelta(days=30)
        paid = rnd.random() < (0.35 if stressed else 0.85)
        ar.append({"invoice_date": inv_d.isoformat(), "due_date": due.isoformat(), "amount": 10000, "paid_amount": 10000 if paid else 0,
                   "paid_date": (due - timedelta(days=2)).isoformat() if paid else "", "customer_name": customers[i], "reference": f"AR-{i}",
                   "branch_name": BRANCHES[i % 3]})
    # الموظفون: دوران مرتفع
    emps = []
    for i in range(40):
        hire = TODAY - timedelta(days=rnd.randint(400, 1500))
        left = stressed and i < 14
        emps.append({"employee_code": f"E{i}", "name": f"موظف {i}", "branch_name": BRANCHES[i % 3], "department": ["المطبخ", "الخدمة", "الإدارة"][i % 3],
                     "role": "موظف", "employment_status": "مغادر" if left else "نشط", "hire_date": hire.isoformat(),
                     "termination_date": (TODAY - timedelta(days=rnd.randint(10, 300))).isoformat() if left else "",
                     "monthly_cost": 6000, "performance_rating": 3})
    # العمليات: تأخير في فرع
    orders = []
    for k in range(600):
        dd = TODAY - timedelta(days=k % 60)
        b = BRANCHES[k % 3]
        cr = f"{dd.isoformat()} 12:00"
        slow = stressed and b == "جدة"
        rd = f"{dd.isoformat()} 12:{25 if slow else 12}"
        dl = f"{dd.isoformat()} 12:{55 if slow else 28}"
        orders.append({"reference": f"O{k}", "date": dd.isoformat(), "branch_name": b, "department": "التوصيل", "service": "توصيل",
                       "created_time": cr, "ready_time": rd, "delivered_time": dl, "due_time": f"{dd.isoformat()} 12:40", "status": "مكتمل",
                       "accurate": "لا" if (slow and k % 7 == 0) else "نعم", "rework": "نعم" if (slow and k % 9 == 0) else "لا"})
    # المصروفات
    exp_rows = []
    for m in ms:
        exp_rows.append({"date": f"{m}-01", "amount": 30000, "category": "رواتب", "description": "رواتب", "branch_name": None})
        exp_rows.append({"date": f"{m}-02", "amount": 12000 * (1.6 if stressed and m == ms[-1] else 1), "category": "إيجار", "description": "إيجار", "branch_name": None})
    # الفواتير الإلكترونية
    invs = []
    for s_ in sales[-400:]:
        tx = round(s_["net_sales"], 2)
        invs.append({"invoice_number": s_["reference"], "issue_date": s_["date"], "branch_name": s_["branch_name"], "taxable_amount": tx,
                     "vat_amount": round(tx * 0.15, 2), "total_amount": round(tx * 1.15, 2), "invoice_type": "مبسطة", "currency": "SAR",
                     "zatca_status": "reported"})
    if stressed:
        for j in range(1, 15):
            invs[-j]["vat_amount"] = None
            invs[j]["total_amount"] = invs[j]["taxable_amount"]
    return {"sales": sales, "cost_map": cost_map, "purchases": purchases, "snaps": snaps, "mv": mv, "ar": ar, "emps": emps,
            "orders": orders, "exp_rows": exp_rows, "invoices": invs}


def run_modules(data, period=None):
    D = data
    mods = {}
    mods["sales"] = sales_engine.analyze_sales(D["sales"], D["sales"], period=period)
    mods["inventory"] = inventory_engine.analyze_inventory(D["snaps"], sales_rows=D["sales"], period=period)
    mods["purchases"] = purchases_engine.analyze_purchases(D["purchases"], period=period, sales_rows=D["sales"], inventory_snaps=D["snaps"])
    mods["cashflow"] = cashflow_engine.analyze_cashflow(D["mv"], receivables=D["ar"], period=period, sales_rows=D["sales"])
    mods["hr"] = hr_engine.analyze_hr(D["emps"], today=TODAY, sales_rows=D["sales"])
    mods["operations"] = ops_engine.analyze_operations(D["orders"], settings={"capacity": {b: 6 for b in BRANCHES}, "sla_minutes": 40},
                                                       now=None)
    lines = finance_engine.build_expense_lines(D["exp_rows"], D["mv"])
    mods["finance"] = finance_engine.analyze_finance(D["sales"], cost_map=D["cost_map"], expense_lines=lines, employees=D["emps"],
                                                     cash={"balance": 100000})
    mods["leakage"] = leakage_engine.analyze_leakage(D["sales"], expense_lines=lines, receivables=D["ar"],
                                                     cost_map={k: v for k, v in D["cost_map"].items() if not isinstance(k, tuple)})
    mods["tax"] = tax_engine.analyze_tax(D["sales"], invoices=D["invoices"], settings={"filing_frequency": "monthly"}, today=TODAY)
    return mods
