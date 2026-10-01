"""E2E (API + DB): re-uploading master data updates instead of duplicating; HR intelligence + salary RBAC."""
import os
import main
from sqlmodel import Session, select
from conftest import auth

SAMPLES = os.environ.get("NABBAH_SAMPLES", "/home/claude/samples")


def _upload(client, h, fname):
    with open(os.path.join(SAMPLES, fname), "rb") as f:
        r = client.post("/company/datasets/preview", headers=h, files={"file": (fname, f.read())}, data={"dataset_type": "auto"})
    assert r.status_code == 200, r.text[:300]
    ds = r.json()
    r = client.post(f"/company/datasets/{ds['dataset_id']}/import", headers=h, json={})
    assert r.status_code == 200, r.text[:300]
    return r.json()


def test_master_reupload_updates_not_duplicates(client, tenants):
    t = tenants["A"]
    with Session(main.engine) as s:
        for nm in ("A", "B", "الرياض - النخيل"):
            if not s.exec(select(main.CompanyBranch).where(main.CompanyBranch.company_id == t["company"], main.CompanyBranch.name == nm)).first():
                s.add(main.CompanyBranch(company_id=t["company"], name=nm, city="Jeddah", is_active=1))
        s.commit()
    h = auth(t["owner"])
    first = _upload(client, h, "2_الموظفون.xlsx")
    assert first["imported"] == 40
    second = _upload(client, h, "13_الموظفون_الموسع.xlsx")
    assert second["updated"] == 40 and second["imported"] == 12, second      # 40 updated, 12 former employees added
    third = _upload(client, h, "13_الموظفون_الموسع.xlsx")
    assert third["imported"] == 0 and third["updated"] == 52, third
    with Session(main.engine) as s:
        rows = s.exec(select(main.CompanyEmployee).where(main.CompanyEmployee.company_id == t["company"])).all()
        assert len(rows) == 52 and len({r.employee_code for r in rows}) == 52
        e1 = next(r for r in rows if r.employee_code == "E001")
        assert e1.performance_rating is not None and e1.basic_salary is not None     # enriched by the update


def test_hr_intelligence_and_salary_rbac(client, tenants):
    t = tenants["A"]
    _upload(client, auth(t["owner"]), "14_الوظائف_الشاغرة.xlsx")
    own = client.get("/company/hr-intelligence?period=2026-08", headers=auth(t["owner"])).json()
    assert own["has_data"] and own["can_see_pay"] and own["compensation"]["available"]
    assert own["recruitment"]["available"] and own["recruitment"]["open"] >= 1
    assert own["kpis"]["headcount"]["current"] > 0 and own["turnover"]["rate_12m"] is not None
    mgr = client.get("/company/hr-intelligence?period=2026-08", headers=auth(t["roles"]["manager"]))
    if mgr.status_code == 200:                                # manager may view HR but never salaries
        m = mgr.json()
        assert m["can_see_pay"] is False and m["compensation"].get("restricted") is True
        assert all(u["payroll"] is None for u in m["units"]["branches"])
        assert "344650" not in mgr.text and "basic_salary" not in mgr.text
    staff = client.get("/company/hr-intelligence", headers=auth(t["roles"]["staff"]))
    assert staff.status_code in (200, 403)
    if staff.status_code == 200:
        assert staff.json()["compensation"].get("restricted") is True
    other = client.get("/company/hr-intelligence", headers=auth(tenants["B"]["owner"])).json()
    assert other.get("has_data") is False                     # company isolation: B sees nothing of A


def test_hr_signal_to_decision(client, tenants):
    t = tenants["A"]
    res = client.get("/company/hr-intelligence?period=2026-08", headers=auth(t["owner"])).json()
    sig = next(x for x in res["signals"] if x["type"] == "risk")
    r = client.post("/company/hr/to-decision", headers=auth(t["owner"]), json={"signal_id": sig["id"], "period": "2026-08", "owner": "HR"})
    assert r.status_code == 200 and r.json()["decision_id"]
    bad = client.post("/company/hr/to-decision", headers=auth(t["owner"]), json={"signal_id": "hr-forged", "period": "2026-08"})
    assert bad.status_code == 404
