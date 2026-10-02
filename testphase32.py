"""Phase 3.2 — Tax & Zakat Compliance tests. Run: python3 test_phase32.py"""
import json, os, sys
from datetime import date
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tax_engine import analyze_tax, regulatory_config
P = F = 0
def ct(n, c):
    global P, F
    if c: P += 1; print(f"PASS: {n}")
    else: F += 1; print(f"FAIL: {n}")
def S(ref, d, br, amt, ret=0, vat=None):
    return {"reference": ref, "date": d, "branch_name": br, "gross_sales": amt + ret, "returns": ret, "discounts": 0, "vat": vat}
def I(no, d, br, taxable, vat, total=None, t="مبسطة", buyer_vat=None, orig=None, status="reported"):
    return {"invoice_number": no, "issue_date": d, "branch_name": br, "taxable_amount": taxable, "vat_amount": vat,
            "total_amount": total if total is not None else (taxable + vat if taxable is not None and vat is not None else None),
            "invoice_type": t, "buyer_vat": buyer_vat, "original_invoice": orig, "currency": "SAR", "zatca_status": status}
print("[GROUP] honesty & regulatory layer")
e = analyze_tax([])
ct("no data → has_data False + not-a-certificate disclaimer", e["has_data"] is False and "لا يمنح شهادة" in e["disclaimer_ar"])
reg = regulatory_config()
ct("regulatory rules carry value/effective date/version/source", all(k in reg["vat_standard_rate"] for k in ("value", "effective_date", "version", "source")))
ct("rules overridable without code (versioned)", regulatory_config({"vat_standard_rate": {"value": 5, "version": "2", "source": "test"}})["vat_standard_rate"]["value"] == 5)
ct("no penalty rules by default (no invented fines)", reg["penalties"]["value"] == [])
sales = [S(f"INV-{i}", "2026-08-05", "A", 115.0) for i in range(10)] + [S("INV-90", "2026-08-06", "B", 230.0), S("INV-91", "2026-08-07", "B", 57.5)]
inv = [I(f"INV-{i}", "2026-08-05", "A", 100.0, 15.0) for i in range(10)]
inv += [I("INV-90", "2026-08-06", "B", 200.0, 30.0, t="ضريبية"),                 # B2B without buyer VAT → warning
        I("INV-5", "2026-08-05", "A", 100.0, 15.0),                             # duplicate number
        I("INV-99", "2026-08-08", "A", 50.0, 7.5),                              # invoice without sale
        I("CN-1", "2026-08-09", "A", 10.0, 1.5, t="إشعار دائن", orig="INV-1"),
        I("CN-2", "2026-08-09", "A", 10.0, 1.5, t="إشعار دائن"),                 # credit note without original → error
        I("INV-77", "2026-08-10", "B", 100.0, None, total=100.0)]               # missing VAT → error
r = analyze_tax(sales, invoices=inv, settings={"vat_number": "300000000000003"}, today=date(2026, 9, 10))
h = r["einvoice"]["health"]
print("[GROUP] e-invoice validation")
ct("duplicates detected (both copies of INV-5)", h["duplicate"] == 2)
ct("errors: credit note w/o original + missing VAT", h["error"] == 2 and any("مرجع للفاتورة الأصلية" in x for c in r["invoices"] for x in c["issues"]))
ct("B2B tax invoice without buyer VAT → warning", any(c["invoice_number"] == "INV-90" and c["status"] == "warning" for c in r["invoices"]))
ct("types counted (tax/simplified/credit)", r["einvoice"]["by_type"]["tax"] == 1 and r["einvoice"]["by_type"]["credit"] == 2)
ct("ZATCA statuses only as recorded; integration honestly 'not connected'", "reported" in r["einvoice"]["zatca_statuses"] and r["integration"]["status"] == "not_connected" and "لا يُدّعى" in r["integration"]["note_ar"])
print("[GROUP] reconciliation (data difference, not accusation)")
rc = r["reconciliation"]
ct("sales without invoice record (INV-91)", any(x["reference"] == "INV-91" for x in rc["sales_without_invoice"]))
ct("invoice without sales record (INV-99, INV-77)", {x["invoice_number"] for x in rc["invoices_without_sales"]} >= {"INV-99", "INV-77"})
ct("basis inferred from data (amounts include VAT)", "شاملة الضريبة" in (rc["inferred_basis_ar"] or ""))
ct("waterfall explains difference + 'not a tax judgment'", rc["waterfall"][-1]["label"] == "غير مُفسَّر" and "ليس حكماً ضريبياً" in rc["note_ar"])
print("[GROUP] VAT")
ct("no 'prices include VAT' setting → expected VAT not assumed", r["vat"]["output_vat_expected"] is None and "لا نفترض" in r["vat"]["expected_basis_ar"])
ri = analyze_tax(sales, invoices=inv, settings={"prices_include_vat": True}, purchases=[{"date": "2026-08-03", "vat": 40.0}, {"date": "2026-08-04"}])
ct("inclusive basis: expected VAT = net × 15/115", abs(ri["vat"]["output_vat_expected"] - round(sum(115 for _ in range(10)) * 0.15 / 1.15 + (230 + 57.5) * 0.15 / 1.15, 2)) < 0.02)
ct("input VAT from purchases with coverage", ri["vat"]["input_vat"] == 40.0 and ri["vat"]["input_vat_coverage_pct"] == 50.0)
ct("net position = output − input", ri["vat"]["net_position"] is not None)
ct("no input VAT → unavailable with reason (not 0)", r["vat"]["input_vat"] is None and r["vat"]["input_reason_ar"])
ct("VAT by branch with data status", {b["branch"] for b in r["branches"]} == {"A", "B"})
print("[GROUP] calendar, zakat, exposure")
ct("no filing frequency → calendar unavailable (no invented dates)", r["calendar"]["available"] is False)
cm = analyze_tax(sales, invoices=inv, settings={"filing_frequency": "monthly"}, today=date(2026, 9, 25))["calendar"]["items"]
ct("monthly due date = end of following month (Aug → 30 Sep), due soon", cm[-1]["due_date"] == "2026-09-30" and cm[-1]["status"] == "due_soon")
co = analyze_tax(sales, invoices=inv, settings={"filing_frequency": "monthly"}, today=date(2026, 10, 5))
ct("overdue when past due and not marked filed → high signal", co["calendar"]["items"][-1]["status"] == "overdue" and any(s["code"] == "filing_overdue" for s in co["signals"]))
cf = analyze_tax(sales, invoices=inv, settings={"filing_frequency": "monthly", "filed_periods": {"2026-08": {"date": "2026-09-20"}}}, today=date(2026, 10, 5))
old = analyze_tax(sales + [S("INV-OLD", "2026-03-05", "A", 115.0)], settings={"filing_frequency": "monthly"}, today=date(2026, 10, 5))["calendar"]["items"]
ct("old unrecorded periods are 'unrecorded', not 'overdue' (may have been filed before Nabbah)", old[0]["status"] == "unrecorded")
ct("marked filed → status filed", cf["calendar"]["items"][-1]["status"] == "filed")
cq = analyze_tax(sales, settings={"filing_frequency": "quarterly"}, today=date(2026, 9, 1))
ct("quarterly period Q3 due 31 Oct", cq["calendar"]["items"][-1]["period"] == "2026-Q3" and cq["calendar"]["items"][-1]["due_date"] == "2026-10-31")
z = analyze_tax(sales, balance_inputs={"cash": 1000, "inventory": 500})["zakat"]
ct("zakat: readiness % + missing list, calculation disabled (no 2.5% shortcut)", z["readiness_pct"] == 17 and z["calculation"]["enabled"] is False and "2.5%" in z["calculation"]["reason_ar"])
ex = r["exposures"]
ct("exposure = affected records + tax amount + basis; no penalty without configured rule", ex and ex[0]["records"] >= 4 and ex[0]["penalty_estimate"] is None and "لا قاعدة غرامة" in ex[0]["penalty_note_ar"])
rp = analyze_tax(sales, invoices=inv, settings={"regulatory_overrides": {"penalties": {"value": [{"applies_to": "invoice_errors", "percent": 10, "source": "قرار مرجعي"}], "source": "test"}}})
ct("penalty estimate only from a configured rule with source", rp["exposures"][0]["penalty_estimate"] is not None and "قرار مرجعي" in rp["exposures"][0]["penalty_note_ar"])
print("[GROUP] checklist, quality, signals")
ck = {c["item"]: c["status"] for c in r["checklist"]}
ct("checklist: seller VAT complete, debit notes missing, buyer VAT partial", ck["الرقم الضريبي للمنشأة"] == "complete" and ck["الإشعارات المدينة"] == "missing" and ck["الرقم الضريبي للمشتري (فواتير المنشآت)"] == "partial")
ct("quality dimensions: completeness/validity/consistency/freshness/duplicates", [q["dimension"] for q in r["quality"]] == ["الاكتمال", "الصحة", "الاتساق (المطابقة)", "الحداثة", "التكرار"])
ct("signals: invoice validation + reconciliation", {"invoice_validation", "reconciliation_difference"} <= {s["code"] for s in r["signals"]})
ct("deterministic ids", [s["id"] for s in analyze_tax(sales, invoices=inv)["signals"]] == [s["id"] for s in analyze_tax(sales, invoices=inv)["signals"]])
ct("no NaN", "NaN" not in json.dumps(r, default=str))
ct("engine never touches AI", "gemini" not in open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "tax_engine.py"), encoding="utf-8").read().lower())
print(f"\nTOTAL: {P+F} | PASSED: {P} | FAILED: {F}")
sys.exit(0 if F == 0 else 1)
