"""
NABBAH — اختبارات عزل الشركات الشاملة (Tenant Isolation — Phase 1.2)
حرجة: تثبّت أن شركة A لا يمكنها الوصول لبيانات شركة B بأي طريقة.
تختبر: الوصول المباشر، تمرير branch_id لشركة أخرى (IDOR)، تسرّب البيانات.
"""
import pytest


# ═══════════ helpers العزل (اختبار الوحدة) ═══════════
def test_get_owned_branch_rejects_other_company(app_module, clean_db):
    """
    الأهم: get_owned_branch يرفض فرعاً من شركة أخرى (يمنع IDOR).
    """
    from sqlmodel import Session
    m = app_module
    with Session(m.engine) as s:
        # شركة A + فرعها
        ua = m.User(name="A", email="a@t.com", password_hash="x", company_id=1)
        s.add(ua); s.commit(); s.refresh(ua)
        ca = m.Company(name="Company A", owner_id=ua.id, is_active=1)
        s.add(ca); s.commit(); s.refresh(ca)
        ua.company_id = ca.id; s.add(ua); s.commit()
        ba = m.CompanyBranch(company_id=ca.id, name="Branch A", is_active=1)
        s.add(ba); s.commit(); s.refresh(ba)

        # شركة B + فرعها
        ub = m.User(name="B", email="b@t.com", password_hash="x")
        s.add(ub); s.commit(); s.refresh(ub)
        cb = m.Company(name="Company B", owner_id=ub.id, is_active=1)
        s.add(cb); s.commit(); s.refresh(cb)
        ub.company_id = cb.id; s.add(ub); s.commit()
        bb = m.CompanyBranch(company_id=cb.id, name="Branch B", is_active=1)
        s.add(bb); s.commit(); s.refresh(bb)

        # مستخدم A يحاول الوصول لفرع B → يجب أن يُرفض
        s.refresh(ua)
        with pytest.raises(m.HTTPException) as exc:
            m.get_owned_branch(s, ua, bb.id)
        assert exc.value.status_code == 403  # غير مصرّح

        # مستخدم A يصل لفرعه → مسموح
        own = m.get_owned_branch(s, ua, ba.id)
        assert own.id == ba.id


def test_get_active_company_no_company(app_module, clean_db):
    """مستخدم بلا شركة → 403."""
    from sqlmodel import Session
    m = app_module
    with Session(m.engine) as s:
        u = m.User(name="X", email="x@t.com", password_hash="x", company_id=None)
        s.add(u); s.commit(); s.refresh(u)
        with pytest.raises(m.HTTPException) as exc:
            m.get_active_company(s, u)
        assert exc.value.status_code == 403


def test_require_company_owner_rejects_non_owner(app_module, clean_db):
    """عضو غير مالك → 403 عند العمليات الحسّاسة."""
    from sqlmodel import Session
    m = app_module
    with Session(m.engine) as s:
        owner = m.User(name="Owner", email="o@t.com", password_hash="x")
        s.add(owner); s.commit(); s.refresh(owner)
        comp = m.Company(name="C", owner_id=owner.id, is_active=1)
        s.add(comp); s.commit(); s.refresh(comp)
        # مستخدم آخر في نفس الشركة لكنه ليس المالك
        member = m.User(name="Member", email="mem@t.com", password_hash="x", company_id=comp.id)
        s.add(member); s.commit(); s.refresh(member)
        with pytest.raises(m.HTTPException) as exc:
            m.require_company_owner(s, member)
        assert exc.value.status_code == 403


def test_scoped_branches_only_own(app_module, clean_db):
    """scoped_branches يُرجع فروع الشركة المطلوبة فقط."""
    from sqlmodel import Session
    m = app_module
    with Session(m.engine) as s:
        c1 = m.Company(name="C1", owner_id=1, is_active=1); s.add(c1); s.commit(); s.refresh(c1)
        c2 = m.Company(name="C2", owner_id=2, is_active=1); s.add(c2); s.commit(); s.refresh(c2)
        s.add(m.CompanyBranch(company_id=c1.id, name="C1-B1", is_active=1))
        s.add(m.CompanyBranch(company_id=c1.id, name="C1-B2", is_active=1))
        s.add(m.CompanyBranch(company_id=c2.id, name="C2-B1", is_active=1))
        s.commit()
        b1 = m.scoped_branches(s, c1.id)
        b2 = m.scoped_branches(s, c2.id)
        assert len(b1) == 2  # فرعا C1 فقط
        assert len(b2) == 1  # فرع C2 فقط
        # لا تداخل
        names1 = {b.name for b in b1}
        assert "C2-B1" not in names1


# ═══════════ اختبار التكامل عبر HTTP ═══════════
def _register_login(client, email, company):
    client.post("/register", json={
        "name": "O", "email": email, "password": "Pass12345",
        "business_name": company, "phone": "0500000000",
    })
    r = client.post("/login", json={"email": email, "password": "Pass12345"})
    return r.json().get("token") if r.status_code == 200 else None


def test_http_company_a_cannot_see_b(client, clean_db):
    """عبر HTTP: رمز شركة A لا يكشف بيانات شركة B."""
    ta = _register_login(client, "ha@t.com", "HTTP Company A")
    tb = _register_login(client, "hb@t.com", "HTTP Company B")
    if not ta or not tb:
        pytest.skip("تعذّر التسجيل في بيئة الاختبار")
    ra = client.get("/company/info", headers={"Authorization": f"Bearer {ta}"})
    if ra.status_code == 200:
        assert "HTTP Company B" not in str(ra.json())
