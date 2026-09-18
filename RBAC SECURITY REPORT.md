[RBAC SECURITY REPORT.md](https://github.com/user-attachments/files/32386516/RBAC.SECURITY.REPORT.md)
# NABBAH — تقرير طبقة أمان الصلاحيات (RBAC Security Report)
**Phase 1.3** · توحيد وتقوية RBAC + اختبارات · **بدون كسر أي وظيفة**

---

## ملخّص تنفيذي

**نظام الصلاحيات (RBAC) قوي ومكتمل** — مصفوفة صريحة، أمان مستوى العمود، حماية server-side. تم توحيد النمط المكرّر في helpers، وبناء اختبارات شاملة تثبّت أن كل دور يصل لما يخصّه فقط ولا يتجاوز صلاحياته.

---

## PART 1: تدقيق RBAC الحالي

### الأدوار الأربعة
| الدور | الصلاحيات |
|---|---|
| **owner** (مالك) | كل شيء (`_all`: view/edit/manage) |
| **accountant** (محاسب) | المالية فقط (finance, cashflow, leakage, tax, reports) — لا تشغيل، لا HR |
| **manager** (مدير فرع) | تشغيل فرعه (sales, ops, inventory, customers) — لا مالية |
| **staff** (موظف) | إدخال تشغيلي فقط (input, sales/edit, ops/edit) |

### آلية الحماية
- **`check_permission(role, resource, action)`** — server-side، المصدر الوحيد للقرار.
- **مصفوفة صريحة** `ROLE_PERMISSIONS` (resource × action لكل دور).
- **`get_user_role`** — يشتق الدور بأمان (owner من الملكية، وإلا من CompanyMember، الافتراضي staff).
- **16 استدعاء** `check_permission` عبر الـendpoints.

### أمان مستوى العمود (Column-Level Security)
- `SENSITIVE_FINANCIAL_FIELDS` — رواتب، هوامش، صافي ربح.
- `filter_sensitive_fields` — **يحذف الحقول الحساسة من الاستعلام** (لا مجرد إخفاء بصري).
- فقط `owner` و `accountant` يرون هذه الحقول.

---

## فحص تغطية الحماية

**كل الـendpoints الحسّاسة محميّة:**

| الفئة | الحالة |
|---|---|
| المالية (finance, cashflow, treasury, leakage, tax) | ✅ محميّة بـ check_permission |
| الإدارة (delete, team) | ✅ owner-only صراحة (`owner_id != user.id`) |
| الإعدادات | ✅ محميّة |

**لا endpoint حسّاس مكشوف.**

---

## PART 2: التوحيد (Helpers جديدة)

أُضيفت 3 دوال تُوحّد النمط المكرّر (`check_permission(get_user_role(...))` — 13 مرة):

| الدالة | الوظيفة |
|---|---|
| `user_can(s, user, resource, action)` | دور + صلاحية → True/False |
| `require_permission(s, user, resource, action)` | يرفع 403 إن رُفض، يُرجع الدور عند النجاح |
| `scope_by_role(s, user, branches)` | Row-Level: مدير الفرع يرى فرعه فقط |

**متاحة للتوحيد التدريجي — لم نُعِد كتابة الـendpoints (تجنّب الكسر).**

---

## PART 3: الاختبارات

`test_rbac.py` — تغطية شاملة:

| المجموعة | يتحقق |
|---|---|
| `TestOwnerRole` | المالك يصل لكل شيء |
| `TestAccountantRole` | المحاسب: مالية نعم، تشغيل/HR لا |
| `TestManagerRole` | المدير: تشغيل نعم، مالية لا |
| `TestStaffRole` | الموظف: إدخال فقط |
| `TestUnknownRole` | دور مجهول/فارغ/None → لا صلاحيات المالك |
| `TestSensitiveFields` | الرواتب/الهوامش تُخفى عن غير المصرّح |
| `TestPermissionHelpers` | user_can, require_permission |
| `TestPrivilegeEscalation` | لا تصعيد صلاحيات |

### نتائج التحقق المباشر
```
✅ owner→finance/manage        ✅ staff→finance (رفض)
✅ accountant→finance          ✅ دور فارغ→finance (رفض)
✅ accountant→ops (رفض)        ✅ None→finance (رفض)
✅ manager→sales/edit          ✅ staff تصعيد (رفض)
✅ manager→finance (رفض)

أمان العمود:
✅ موظف لا يرى الراتب    ✅ موظف يرى المبيعات
✅ موظف لا يرى الهامش    ✅ المالك يرى كل شي

النتيجة: 10/10 صلاحيات + 4/4 أمان عمود ✅
```

---

## منع تصعيد الصلاحيات (Privilege Escalation)

اختُبر صراحة:
- ✅ الموظف لا يصل للمالية بأي إجراء (view/edit/manage)
- ✅ مدير الفرع لا يصل للمالية
- ✅ المحاسب لا يدير التشغيل
- ✅ دور مجهول/فارغ لا يُعطى صلاحيات المالك

---

## ما لم يُلمَس (كما طُلب)
- ❌ لا تغيير في منطق الأعمال
- ❌ لا تغيير في مصفوفة الصلاحيات (بقيت كما هي)
- ❌ لا تغيير في الواجهة
- ❌ لا migration
- ❌ لم تُعَد كتابة الـendpoints

**التغيير الوحيد:** 3 helpers إضافية (لا تُستدعى قسراً).

---

## الخلاصة الأمنية

| البُعد | التقييم |
|---|---|
| مصفوفة الصلاحيات | ✅ صريحة ومكتملة |
| الحماية server-side | ✅ لا تُتجاوز من الواجهة |
| أمان مستوى العمود | ✅ الرواتب/الهوامش محميّة |
| منع التصعيد | ✅ مؤكّد بالاختبار |
| تغطية الـendpoints | ✅ كل الحسّاس محمي |

**نظام صلاحيات مؤسسي متين — كل دور محصور في صلاحياته.**

---
*تقرير Phase 1.3 — أمان الصلاحيات. لا وظيفة كُسرت.*
