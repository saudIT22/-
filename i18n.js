/* ═══════════════════════════════════════════════════════════
   نبّاه · نظام اللغتين (i18n)  —  Nabbah Bilingual Engine
   يعمل بدون أي مكتبة خارجية. يترجم العناصر المعلّمة بـ data-i18n،
   ويبدّل اتجاه الصفحة (RTL/LTR) والخط تلقائياً، ويحفظ اختيار المستخدم.
   الترجمة "تحويل لغة فقط" — دقيقة، بلا تغيير في التصميم أو المنطق.
   ═══════════════════════════════════════════════════════════ */
(function () {
  "use strict";

  // ── قاموس الترجمة: المفتاح ثابت، والقيم بالعربي والإنجليزي ──
  const DICT = {
    // ---- عام / تنقّل ----
    "nav.dashboard": { ar: "اللوحة", en: "Dashboard" },
    "nav.input": { ar: "إدخال البيانات", en: "Data Input" },
    "nav.branches": { ar: "الفروع", en: "Branches" },
    "nav.report": { ar: "التقرير", en: "Report" },
    "nav.leakage": { ar: "تسرّب الأرباح", en: "Profit Leaks" },
    "nav.predictions": { ar: "التوقعات", en: "Forecasts" },
    "nav.decisions": { ar: "القرارات", en: "Decisions" },
    "nav.memory": { ar: "الذاكرة", en: "Memory" },
    "nav.monthly": { ar: "التقرير الشهري", en: "Monthly Report" },
    "nav.settings": { ar: "الإعدادات", en: "Settings" },
    "nav.logout": { ar: "تسجيل الخروج", en: "Log out" },
    "nav.home": { ar: "الرئيسية", en: "Home" },

    // ---- القائمة العلوية (الصفحة الرئيسية) ----
    "landing.how": { ar: "كيف يعمل", en: "How it works" },
    "landing.why": { ar: "لماذا نبّاه", en: "Why Nabbah" },
    "landing.features": { ar: "المميزات", en: "Features" },
    "landing.pricing": { ar: "الباقات", en: "Pricing" },
    "landing.trial": { ar: "كود التجربة", en: "Trial Code" },
    "landing.companies": { ar: "الشركات", en: "Companies" },
    "landing.login": { ar: "تسجيل الدخول", en: "Log in" },
    "landing.cta": { ar: "ابدأ تحليل منشأتك", en: "Start Your Analysis" },

    // ---- الصفحة الرئيسية: المحتوى الكامل ----
    "home.lead": { ar: "نبّاه يحوّل مبيعاتك وإيراداتك ومصروفاتك إلى تقرير تنفيذي عربي: مشاكل واضحة، حلول عملية، فرص للنمو، وقرارات مرتّبة — مع درجات ذكية تقرأ صحة منشأتك من نظرة.", en: "Nabbah turns your sales, revenue, and expenses into an executive report: clear problems, practical solutions, growth opportunities, and ranked decisions — with smart scores that read your business health at a glance." },
    "home.steps.title": { ar: "ثلاث خطوات، وتقريرك جاهز", en: "Three steps, and your report is ready" },
    "home.steps.sub": { ar: "بدون تعقيد محاسبي — بلغة يفهمها صاحب العمل.", en: "No accounting complexity — in language a business owner understands." },
    "home.step1.t": { ar: "أدخل أرقامك", en: "Enter your numbers" },
    "home.step1.p": { ar: "مبيعات، إيرادات، مصروفات، وعدد الطلبات. ما عندك رقم؟ القسم يختفي تلقائياً، ولا نخترع شيئاً.", en: "Sales, revenue, expenses, and order count. Missing a number? That section disappears automatically — we invent nothing." },
    "home.step2.t": { ar: "نحلّلها بعمق", en: "We analyze deeply" },
    "home.step2.p": { ar: "محرّك حسابات يقرأ منطقية الأرقام، يحسب الدرجات، وينبّه لأي تناقض دون رفض بياناتك.", en: "A calculation engine reads the logic of your numbers, computes scores, and flags any inconsistency without rejecting your data." },
    "home.step3.t": { ar: "احصل على تقرير", en: "Get your report" },
    "home.step3.p": { ar: "ملخّص سريع في 30 ثانية، ثم تفاصيل: مشاكل، حلول، فرص، وأهم القرارات بأولوياتها.", en: "A 30-second summary, then details: problems, solutions, opportunities, and top decisions by priority." },
    "home.feat.title": { ar: "كل ما تحتاجه لقرار أذكى", en: "Everything you need for a smarter decision" },
    "home.feat.sub": { ar: "أدوات مبنية لتقرأ منشأتك كما يقرأها مستشار محترف.", en: "Tools built to read your business the way a professional advisor would." },
    "home.f1.t": { ar: "درجة صحة منشأتك", en: "Business Health Score" },
    "home.f1.p": { ar: "درجة واحدة تلخّص وضعك، ولونها يتبع حالتك — أخضر، تنبيه، أو حرج.", en: "One score that sums up your standing, colored by your state — green, warning, or critical." },
    "home.f2.t": { ar: "تغطية المصروفات", en: "Expense Coverage" },
    "home.f2.p": { ar: "كم تغطي إيراداتك من مصروفاتك، مع شرح المعادلة خطوة بخطوة.", en: "How much of your expenses your revenue covers, with the formula explained step by step." },
    "home.f3.t": { ar: "توقعات رقمية", en: "Numeric Forecasts" },
    "home.f3.p": { ar: "سيناريو متحفظ وآخر متفائل، تُبنى من إدخالين أو أكثر — بلا مبالغة.", en: "A conservative and an optimistic scenario, built from two or more entries — no exaggeration." },
    "home.f4.t": { ar: "كشف التناقضات", en: "Inconsistency Detection" },
    "home.f4.p": { ar: "ينبّهك حين لا تتطابق الأرقام، مثل هامش غريب يشير لنقص في التسجيل.", en: "Alerts you when numbers don't add up, like an odd margin pointing to missing records." },
    "home.f5.t": { ar: "أسرع ربح", en: "Quickest Win" },
    "home.f5.p": { ar: "بطاقة تبرز أسهل خطوة ذات أثر مالي مباشر تقدر تنفّذها اليوم.", en: "A card highlighting the easiest high-impact step you can take today." },
    "home.f6.t": { ar: "أهم 3 قرارات", en: "Top 3 Decisions" },
    "home.f6.p": { ar: "تنبيه، قرار، وفرصة — مرتّبة بالأولوية حسب أثرها على منشأتك.", en: "An alert, a decision, and an opportunity — ranked by their impact on your business." },
    "home.trust.line": { ar: "منصة سعودية موثوقة · بالعربية · تسجيل فوري · للشركات والسلاسل متعددة الفروع", en: "Trusted Saudi platform · Arabic · instant sign-up · for companies and multi-branch chains" },
    "home.e1.t": { ar: "لوحة المدير التنفيذي", en: "Executive Dashboard" },
    "home.e1.p": { ar: "كل فروعك ومؤشراتها — مبيعات، هامش، عملاء، أداء — في شاشة واحدة.", en: "All your branches and their metrics — sales, margin, customers, performance — on one screen." },
    "home.e2.t": { ar: "مقارنة الفروع الذكية", en: "Smart Branch Comparison" },
    "home.e2.p": { ar: "ترتيب الفروع، أسباب تراجع الأضعف، وأفضل ممارسات الأقوى لتعميمها.", en: "Branch ranking, why the weakest declined, and the best practices of the strongest to replicate." },
    "home.e3.t": { ar: "التدفق النقدي التنبؤي", en: "Predictive Cash Flow" },
    "home.e3.p": { ar: "كم شهر تكفي سيولتك، ومتى أول عجز متوقّع — قبل ما يصير.", en: "How many months your liquidity lasts, and when the first expected shortfall hits — before it happens." },
    "home.e4.t": { ar: "كشف التسرّب والاحتيال", en: "Leak & Fraud Detection" },
    "home.e4.p": { ar: "قفزات مصروفات بلا مبيعات، وفجوات بين البيع والإيداع تكشف الهدر.", en: "Expense spikes without sales, and gaps between sales and deposits that reveal waste." },
    "home.e5.t": { ar: "محاكي القرارات", en: "Decision Simulator" },
    "home.e5.p": { ar: "شوف أثر رفع الأسعار أو التوظيف أو التسويق على الربح قبل التنفيذ.", en: "See the impact of raising prices, hiring, or marketing on profit before you act." },
    "home.e6.t": { ar: "صلاحيات الفريق", en: "Team Permissions" },
    "home.e6.p": { ar: "مالك، مدير فرع، محاسب، موظف — كل واحد يشوف ما يخصّه.", en: "Owner, branch manager, accountant, employee — each sees only what concerns them." },
    "home.pricing.title": { ar: "باقات نبّاه للشركات", en: "Nabbah Business Plans" },
    "home.pricing.sub": { ar: "استثمار يحمي قراراتك ويكشف الهدر قبل ما يكلّفك أضعافه.", en: "An investment that protects your decisions and catches waste before it costs you many times over." },
    "home.pricing.note": { ar: "الاشتراك يُفعّل بعد التأكيد · بدون رسوم إعداد · إلغاء في أي وقت", en: "Subscription activates after confirmation · no setup fees · cancel anytime" },
    "home.creed.title": { ar: "مصداقية قبل كل شيء", en: "Credibility above all" },

    // ---- صفحة إعدادات الشركة ----
    "set.title": { ar: "إعداد الشركة", en: "Company Setup" },
    "set.sub": { ar: "كل ما يعرفه نبّاه عن شركتك — ليكيّف تحليله وأولوياته حسب واقعك.", en: "Everything Nabbah knows about your company — to tailor its analysis and priorities to your reality." },
    "set.s1": { ar: "معلومات الشركة", en: "Company Info" },
    "set.s1.sub": { ar: "الاسم، القطاع، الدولة، العملة، السنة المالية", en: "Name, sector, country, currency, fiscal year" },
    "set.name": { ar: "اسم الشركة", en: "Company Name" },
    "set.sector": { ar: "القطاع / النشاط", en: "Sector / Activity" },
    "set.country": { ar: "الدولة", en: "Country" },
    "set.currency": { ar: "العملة", en: "Currency" },
    "set.fiscal": { ar: "بداية السنة المالية (شهر)", en: "Fiscal Year Start (month)" },
    "set.s2": { ar: "الهيكل والحجم", en: "Structure & Size" },
    "set.s2.sub": { ar: "عدد الموظفين، الإيرادات، هامش الربح المستهدف", en: "Employees, revenue, target margin" },
    "set.employees": { ar: "عدد الموظفين", en: "Employees" },
    "set.revenue": { ar: "الإيرادات السنوية التقريبية", en: "Approx. Annual Revenue" },
    "set.margin": { ar: "هامش الربح المستهدف (%)", en: "Target Profit Margin (%)" },
    "set.s3": { ar: "مصادر البيانات", en: "Data Sources" },
    "set.s3.sub": { ar: "من أين يقرأ نبّاه بياناتك", en: "Where Nabbah reads your data" },
    "set.src.pos": { ar: "نقاط البيع (POS)", en: "Point of Sale (POS)" },
    "set.src.bank": { ar: "كشف الحساب البنكي", en: "Bank Statement" },
    "set.src.acc": { ar: "المحاسبة", en: "Accounting" },
    "set.src.inv": { ar: "المخزون", en: "Inventory" },
    "set.src.note": { ar: "الأخضر = مفعّل حالياً. الربط المباشر مع الأنظمة قادم قريباً — حالياً الرفع اليدوي للملفات.", en: "Green = active. Direct system integration is coming soon — currently manual file upload." },
    "set.s4": { ar: "الصلاحيات", en: "Permissions" },
    "set.s4.sub": { ar: "من يرى ماذا في المنصة", en: "Who sees what in the platform" },
    "role.owner": { ar: "المالك", en: "Owner" },
    "role.ceo": { ar: "المدير التنفيذي", en: "CEO" },
    "role.cfo": { ar: "المدير المالي", en: "CFO" },
    "role.branch": { ar: "مدير الفرع", en: "Branch Manager" },
    "role.analyst": { ar: "محلل", en: "Analyst" },
    "set.role.note": { ar: "إدارة أعضاء الفريق وصلاحياتهم من صفحة الفريق.", en: "Manage team members and their permissions from the Team page." },
    "set.s5": { ar: "الأهداف والأولوية", en: "Goals & Priority" },
    "set.s5.sub": { ar: "أهم شيء لإدارتك — يوجّه نبّاه توصياته حسبه", en: "What matters most to you — Nabbah steers its recommendations by it" },
    "set.priority": { ar: "الأولوية القصوى", en: "Top Priority" },
    "set.goal.sales": { ar: "هدف المبيعات الشهري", en: "Monthly Sales Goal" },
    "set.goal.profit": { ar: "هدف الربح الشهري", en: "Monthly Profit Goal" },
    "set.goal.customers": { ar: "هدف عدد العملاء", en: "Customer Count Goal" },
    "set.goal.retention": { ar: "هدف الاحتفاظ بالعملاء (%)", en: "Retention Goal (%)" },
    "set.s6": { ar: "التنبيهات", en: "Alerts" },
    "set.s6.sub": { ar: "متى ينبّهك نبّاه", en: "When Nabbah alerts you" },
    "alert.sales": { ar: "انخفاض المبيعات", en: "Sales drop" },
    "alert.expenses": { ar: "ارتفاع المصروفات", en: "Expense rise" },
    "alert.liquidity": { ar: "انخفاض السيولة", en: "Low liquidity" },
    "alert.inventory": { ar: "مشكلة في المخزون", en: "Inventory issue" },
    "alert.performance": { ar: "انخفاض الأداء", en: "Performance drop" },
    "set.sector.smart": { ar: "نبّاه يتكيّف مع قطاعك", en: "Nabbah adapts to your sector" },

    // ---- صفحات المنصة الأساسية ----
    "page.input.title": { ar: "إدخال بيانات الفروع", en: "Enter Branch Data" },
    "page.input.sub": { ar: "أدخل بيانات فرع واحد شهرياً — نبّاه يحلّل ويقارن تلقائياً.", en: "Enter one branch's data per month — Nabbah analyzes and compares automatically." },
    "page.decisions.title": { ar: "مركز القرارات", en: "Decision Center" },
    "page.decisions.sub": { ar: "قرارات مرتّبة بأثرها المالي — مع متابعة النتيجة.", en: "Decisions ranked by financial impact — with result tracking." },
    "page.leakage.title": { ar: "خريطة تسرّب الأرباح", en: "Profit Leak Map" },
    "page.leakage.sub": { ar: "وين تروح فلوسك — بالريال والبند.", en: "Where your money goes — in SAR, by item." },
    "page.report.title": { ar: "التقرير التنفيذي", en: "Executive Report" },
    "page.report.sub": { ar: "تقرير شامل جاهز للطباعة أو العرض على المجلس.", en: "A comprehensive report ready to print or present to the board." },
    "page.branches.title": { ar: "الفروع", en: "Branches" },
    "page.branches.sub": { ar: "إدارة فروع شركتك والمقارنة بينها.", en: "Manage and compare your company's branches." },
    "page.cashflow.title": { ar: "التدفق النقدي", en: "Cash Flow" },
    "page.cashflow.sub": { ar: "كم شهر تكفي سيولتك، ومتى أول ضغط متوقع.", en: "How many months your liquidity lasts, and when the first pressure is expected." },
    "page.monthly.title": { ar: "التقرير الشهري", en: "Monthly Report" },
    "page.monthly.sub": { ar: "ملخّص شهري تلقائي — «وين راحت فلوسك».", en: "Automatic monthly summary — 'where did your money go'." },
    // أزرار مشتركة عبر الصفحات
    "btn.refresh": { ar: "تحديث التحليل", en: "Refresh Analysis" },
    "btn.new": { ar: "إضافة جديد", en: "Add New" },
    "btn.edit": { ar: "تعديل", en: "Edit" },
    "btn.delete": { ar: "حذف", en: "Delete" },
    "btn.view": { ar: "عرض", en: "View" },
    "btn.export": { ar: "تصدير", en: "Export" },
    // حالات القرار
    "dec.state.pending": { ar: "قيد التنفيذ", en: "In Progress" },
    "dec.state.approved": { ar: "معتمد", en: "Approved" },
    "dec.state.done": { ar: "منجز", en: "Completed" },
    "dec.state.rejected": { ar: "مرفوض", en: "Rejected" },

    // ---- صفحة القرارات (كاملة) ----
    "dec.page.title": { ar: "نبّاه · متابعة القرارات", en: "Nabbah · Decision Tracking" },
    "dec.command": { ar: "مركز القيادة", en: "Command Center" },
    "dec.head.desc": { ar: "اعتمد القرار → حدّد المسؤول والموعد والـKPI → أغلقه ويقيس نبّاه النتيجة تلقائياً (المبيعات قبل/بعد)", en: "Approve decision → set owner, due date, KPI → close it and Nabbah measures the result automatically (sales before/after)" },
    "common.loading2": { ar: "جارٍ التحميل…", en: "Loading…" },
    "dec.state.late": { ar: "متأخر", en: "Overdue" },
    "dec.new.title": { ar: "اعتماد قرار جديد", en: "Approve New Decision" },
    "dec.new.baseline": { ar: "يُسجَّل خط الأساس (مبيعاتك الحالية) تلقائياً لقياس الأثر لاحقاً", en: "The baseline (your current sales) is recorded automatically to measure impact later" },
    "dec.field.decision": { ar: "القرار", en: "Decision" },
    "dec.field.details": { ar: "التفاصيل (اختياري)", en: "Details (optional)" },
    "dec.field.owner": { ar: "المسؤول", en: "Owner" },
    "dec.field.due": { ar: "موعد الإنجاز", en: "Due Date" },
    "dec.field.kpi": { ar: "مؤشر النجاح KPI", en: "Success KPI" },
    "dec.approve.btn": { ar: "اعتماد القرار", en: "Approve Decision" },
    "dec.memory": { ar: "ذاكرة الشركة", en: "Company Memory" },

    // ---- صفحة اللوحة (كاملة) ----
    "dash.page.title": { ar: "نبّاه · لوحة الشركة", en: "Nabbah · Company Dashboard" },
    "dash.companies": { ar: "قسم الشركات", en: "Companies" },
    "dash.pending.msg": { ar: "تم تسجيل شركتك بنجاح ✅ وهي الآن بانتظار تفعيل الاشتراك. بمجرد التفعيل تنفتح لك كل خدمات قسم الشركات (اللوحة، مقارنة الفروع، التدفق النقدي، كشف التسرّب، وغيرها).", en: "Your company was registered successfully ✅ and is now awaiting subscription activation. Once active, all Companies services unlock (dashboard, branch comparison, cash flow, leak detection, and more)." },
    "dash.start.fast": { ar: "ابدأ فوراً —", en: "Start instantly —" },
    "dash.upload.line": { ar: "ارفع ملف Excel أو CSV واحد", en: "Upload one Excel or CSV file" },
    "dash.upload.desc": { ar: "من نظامك (نظام كاشير POS، نظام محاسبة، ERP، أو ملف يدوي)، ويشغّل نبّاه التحليل الكامل تلقائياً — بدون أي إدخال يدوي فرع بفرع.", en: "from your system (POS, accounting, ERP, or a manual file), and Nabbah runs the full analysis automatically — with no manual branch-by-branch entry." },
    "dash.cmp.title": { ar: "مقارنة الفروع — المبيعات", en: "Branch Comparison — Sales" },
    "dash.map.title": { ar: "توزيع الفروع على الخريطة", en: "Branches on the Map" },
    "dash.rootcause.title": { ar: "تحليل الأسباب", en: "Root-Cause Analysis" },
    "dash.bestprac.title": { ar: "أفضل الممارسات", en: "Best Practices" },
    "dash.similar.title": { ar: "مقارنة الفروع المتشابهة", en: "Similar Branches Comparison" },
    "dash.forecast.title": { ar: "التنبؤ بالأداء (الفترة القادمة)", en: "Performance Forecast (Next Period)" },
    "dash.sim.title": { ar: "محاكي القرارات", en: "Decision Simulator" },
    "dash.rate.exc": { ar: "ممتاز", en: "Excellent" },
    "dash.rate.good": { ar: "جيد", en: "Good" },
    "dash.rate.avg": { ar: "متوسط", en: "Average" },
    "dash.rate.weak": { ar: "ضعيف", en: "Weak" },

    // ---- التدفق النقدي ----
    "cash.trail": { ar: "مسار السيولة (12 شهر)", en: "Liquidity Trail (12 months)" },
    "cash.data": { ar: "بيانات السيولة", en: "Liquidity Data" },
    "cash.reserve": { ar: "الاحتياطي النقدي الحالي (ريال)", en: "Current Cash Reserve (SAR)" },
    "cash.obligations": { ar: "الالتزامات الشهرية الثابتة (ريال)", en: "Monthly Fixed Obligations (SAR)" },
    "cash.save": { ar: "حفظ وإعادة الحساب", en: "Save & Recalculate" },
    // ---- الفروع ----
    "br.table": { ar: "جدول مقارنة الفروع", en: "Branch Comparison Table" },
    "br.detail": { ar: "تفاصيل فرع", en: "Branch Details" },
    // ---- التقارير ----
    "rep.print": { ar: "طباعة / PDF", en: "Print / PDF" },
    // ---- الإدخال ----
    "in.branch": { ar: "الفرع", en: "Branch" },
    "in.period": { ar: "الفترة (الشهر والسنة)", en: "Period (Month & Year)" },
    "in.sales": { ar: "إجمالي المبيعات (ريال)", en: "Total Sales (SAR)" },
    "in.expenses": { ar: "المصروفات (ريال)", en: "Expenses (SAR)" },
    "in.invoices": { ar: "عدد الفواتير / الطلبات", en: "Number of Invoices / Orders" },
    "in.customers": { ar: "عدد العملاء", en: "Number of Customers" },
    "in.notes": { ar: "ملاحظات", en: "Notes" },
    "in.save": { ar: "حفظ وحساب المؤشرات", en: "Save & Compute Metrics" },
    "in.analyze": { ar: "التحليل التنفيذي للفرع", en: "Branch Executive Analysis" },
    "in.add.other": { ar: "إدخال فرع آخر", en: "Enter Another Branch" },
    // ---- الصحة ----
    "health.title": { ar: "صحة الشركة", en: "Company Health" },
    // ---- الأهداف ----
    "goals.title": { ar: "الأهداف والنتائج", en: "Goals & Results" },
    "goals.update": { ar: "تحديث الأهداف", en: "Update Goals" },
    "goals.period": { ar: "الفترة (السنة)", en: "Period (Year)" },
    "goals.save": { ar: "حفظ الأهداف", en: "Save Goals" },
    // ---- Benchmarks ----
    "bench.title": { ar: "مقارنة مع القطاع", en: "Sector Benchmarks" },
    "bench.estimate": { ar: "معايير تقديرية إرشادية — ليست أرقاماً رسمية", en: "Indicative estimates — not official figures" },
    // ---- سجل التدقيق ----
    "audit.title": { ar: "سجل التدقيق", en: "Audit Log" },
    "audit.sub": { ar: "سجل زمني لكل إجراء مهم على المنصة — للمساءلة والشفافية.", en: "A chronological log of every important action — for accountability and transparency." },
    "audit.info": { ar: "🔒 هذا السجل مرئي للمالك فقط. يوثّق مَن قام بأي إجراء ومتى — يعزّز ثقة المدققين والمستثمرين ويدعم حوكمة الشركة.", en: "🔒 Visible to the owner only. It records who did what and when — building auditor and investor trust and supporting governance." },
    "audit.empty": { ar: "لا توجد سجلات بعد. ستظهر هنا كل الإجراءات المهمة تلقائياً.", en: "No records yet. All important actions will appear here automatically." },
    "audit.col.action": { ar: "الإجراء", en: "Action" },
    "audit.col.target": { ar: "المستهدف", en: "Target" },
    "audit.col.user": { ar: "المستخدم", en: "User" },
    "audit.col.time": { ar: "التاريخ والوقت", en: "Date & Time" },
    "nav.audit": { ar: "سجل التدقيق", en: "Audit Log" },
    // ---- شفافية منهجية مركز القيادة ----
    "cmd.how.title": { ar: "ℹ️ كيف يبني نبّاه هذه الرؤى؟ (شفافية المنهجية)", en: "ℹ️ How does Nabbah build these insights? (Methodology transparency)" },
    "cmd.how.intro": { ar: "مركز القيادة ليس «صندوقاً أسود» — كل رؤية مبنية على بياناتك الفعلية عبر خطوات واضحة:", en: "The Command Center is not a 'black box' — every insight is built on your actual data through clear steps:" },
    "cmd.how.s1": { ar: "جمع البيانات: يقرأ آخر إدخال لكل فرع (مبيعات، مصروفات، عملاء، مخزون...) من بياناتك المُدخلة.", en: "Data collection: reads the latest entry for each branch (sales, expenses, customers, inventory...) from your entered data." },
    "cmd.how.s2": { ar: "حساب المؤشرات: يحسب الهامش والنمو ونِسب الأداء بمعادلات مالية معيارية — لا تقدير عشوائي.", en: "Metric calculation: computes margin, growth, and performance ratios using standard financial formulas — no random guessing." },
    "cmd.how.s3": { ar: "كشف الأنماط: يقارن الفروع والفترات ويرصد الانحرافات (هامش منخفض، تكلفة مرتفعة، هدر).", en: "Pattern detection: compares branches and periods and flags deviations (low margin, high cost, leakage)." },
    "cmd.how.s4": { ar: "توليد القرارات: كل قرار مرتبط بالرقم الذي أنتجه، ومرتّب حسب أثره المالي المقدّر بالريال.", en: "Decision generation: each decision is tied to the number that produced it, ranked by its estimated financial impact in SAR." },
    "cmd.how.s5": { ar: "الذكاء الاصطناعي: يُستخدم فقط لصياغة التوصيات بلغة تنفيذية واضحة — لا لاختراع أرقام. كل رقم مصدره بياناتك.", en: "AI: used only to phrase recommendations in clear executive language — never to invent numbers. Every figure comes from your data." },
    "cmd.how.note": { ar: "عند نقص أي بيانات، يوضّح نبّاه ذلك صراحةً بدل التخمين — النتائج الناقصة تُوسم «تحتاج إلى تحقق».", en: "When any data is missing, Nabbah states it explicitly instead of guessing — incomplete results are marked 'needs verification'." },
    "cmd.risks.title": { ar: "مؤشرات المخاطر", en: "Risk Indicators" },
    "cmd.risks.note": { ar: "المخاطر مدمجة في ملخّصك التنفيذي — اضغط أي مؤشر للتفاصيل الكاملة.", en: "Risks are integrated into your executive summary — click any indicator for full details." },
    "cmd.pillars.title": { ar: "الركائز الخمس لصحة الشركة", en: "Five Pillars of Company Health" },
    "cmd.pillars.note": { ar: "كل ركيزة محسوبة بمعادلة معيارية من بياناتك. «—» يعني بيانات غير مكتملة لتلك الركيزة (ليست ضعفاً في الأداء).", en: "Each pillar is computed by a standard formula from your data. «—» means incomplete data for that pillar (not poor performance)." },
    "health.radar": { ar: "🕸️ رادار المحاور الخمسة", en: "🕸️ Five-Axis Radar" },
    "health.priority": { ar: "الأولوية للتحسين:", en: "Priority to improve:" },
    "health.composite": { ar: "مؤشر مركّب من ٥ محاور موزونة", en: "Composite of 5 weighted axes" },
    "health.live": { ar: "مباشر", en: "Live" },
    "health.byBranch": { ar: "🏬 صحة كل فرع", en: "🏬 Health by Branch" },
    "health.noBranchData": { ar: "لا توجد بيانات", en: "No data" },
    "br.employees": { ar: "الموظفون", en: "Employees" },
    "br.productivity": { ar: "إنتاجية/موظف", en: "Productivity/Emp" },

    // ---- عناصر مشتركة عبر كل وحدات التحليل ----
    "unit.input.settings": { ar: "إعدادات الإدخال", en: "Input Settings" },
    "unit.scope": { ar: "النطاق", en: "Scope" },
    "unit.period": { ar: "الفترة", en: "Period" },
    "unit.fields": { ar: "الحقول", en: "Fields" },
    "unit.source": { ar: "من أين أتت هذه البيانات؟", en: "Where did this data come from?" },
    "unit.exec": { ar: "التحليل التنفيذي للوحدة", en: "Unit Executive Analysis" },
    "unit.request": { ar: "اطلب التحليل التنفيذي", en: "Request Executive Analysis" },
    // ---- عناوين الوحدات ----
    "unit.finance": { ar: "الوحدة المالية الموسّعة", en: "Extended Finance Unit" },
    "unit.finance.save": { ar: "حفظ بيانات الوحدة المالية الموسّعة", en: "Save Finance Unit Data" },
    "unit.sales": { ar: "وحدة المبيعات التفصيلية", en: "Detailed Sales Unit" },
    "unit.sales.save": { ar: "حفظ بيانات وحدة المبيعات التفصيلية", en: "Save Sales Unit Data" },
    "unit.customers": { ar: "وحدة العملاء", en: "Customers Unit" },
    "unit.customers.save": { ar: "حفظ بيانات وحدة العملاء", en: "Save Customers Unit Data" },
    "unit.inventory": { ar: "وحدة المخزون", en: "Inventory Unit" },
    "unit.inventory.save": { ar: "حفظ بيانات وحدة المخزون", en: "Save Inventory Unit Data" },
    "unit.ops": { ar: "الوحدة التشغيلية", en: "Operations Unit" },
    "unit.ops.save": { ar: "حفظ بيانات الوحدة التشغيلية", en: "Save Operations Unit Data" },
    "unit.hr": { ar: "وحدة الموارد البشرية", en: "HR Unit" },
    "unit.hr.save": { ar: "حفظ بيانات وحدة الموارد البشرية", en: "Save HR Unit Data" },
    "unit.procurement": { ar: "وحدة المشتريات", en: "Procurement Unit" },
    "unit.tax": { ar: "وحدة الضريبة والزكاة", en: "Tax & Zakat Unit" },
    "unit.events": { ar: "وحدة الأحداث والمواسم", en: "Events & Seasons Unit" },
    "unit.competitors": { ar: "وحدة المنافسين", en: "Competitors Unit" },

    // ---- تسجيل شركة ----
    "reg.title": { ar: "إنشاء شركة جديدة", en: "Create New Company" },
    "reg.name": { ar: "اسم الشركة", en: "Company Name" },
    "reg.activity": { ar: "نشاط الشركة", en: "Company Activity" },
    "reg.branches": { ar: "الفروع", en: "Branches" },
    "reg.add.branch": { ar: "إضافة فرع", en: "Add Branch" },
    "reg.create": { ar: "إنشاء الشركة والبدء", en: "Create Company & Start" },
    // ---- مركز القيادة ----
    "cmd.title": { ar: "مركز القيادة التنفيذي", en: "Executive Command Center" },
    "cmd.refresh": { ar: "تحديث", en: "Refresh" },
    // ---- التنبؤات ----
    "pred.title": { ar: "التنبؤ بالأداء", en: "Performance Forecast" },
    "pred.company": { ar: "توقع الشركة (٦ أشهر)", en: "Company Forecast (6 months)" },
    "pred.branch": { ar: "توقع كل فرع", en: "Per-Branch Forecast" },
    "pred.how": { ar: "كيف يحسب نبّاه التنبؤ؟", en: "How does Nabbah compute the forecast?" },
    // ---- المخاطر ----
    "risks.title": { ar: "محرّك المخاطر", en: "Risk Engine" },
    // ---- الذاكرة ----
    "mem.title": { ar: "ذاكرة الشركة", en: "Company Memory" },
    // ---- الفريق ----
    "team.title": { ar: "صلاحيات الفريق", en: "Team Permissions" },
    "team.members": { ar: "أعضاء الفريق", en: "Team Members" },
    "team.add": { ar: "إضافة عضو", en: "Add Member" },
    "team.name": { ar: "الاسم", en: "Name" },
    "team.email": { ar: "الإيميل (اختياري)", en: "Email (optional)" },
    "team.role": { ar: "الصلاحية", en: "Role" },
    "team.branch": { ar: "الفرع (لمدير الفرع)", en: "Branch (for branch manager)" },
    "team.add.btn": { ar: "إضافة العضو", en: "Add Member" },
    "team.guide": { ar: "دليل الصلاحيات", en: "Permissions Guide" },
    // ---- عناوين متنوعة ----
    "rootcause.title": { ar: "تحليل الأسباب الجذرية", en: "Root-Cause Analysis" },
    "board.title": { ar: "تقرير مجلس الإدارة", en: "Board Report" },
    "dq.title": { ar: "جودة البيانات", en: "Data Quality" },
    "upload.title": { ar: "رفع الملفات", en: "File Upload" },
    "tax.title": { ar: "الضريبة والزكاة", en: "Tax & Zakat" },
    "rootcause.alt": { ar: "الأسباب الجذرية", en: "Root Causes" },
    "upload.smart": { ar: "رفع البيانات الذكي", en: "Smart Data Upload" },

    // ---- مجموعات القائمة المنظّمة ----
    "navg.executive": { ar: "نظرة تنفيذية", en: "Executive Overview" },
    "navg.financial": { ar: "الأداء المالي", en: "Financial Performance" },
    "navg.operational": { ar: "الأداء التشغيلي", en: "Operational Performance" },
    "navg.branches": { ar: "الفروع والعملاء", en: "Branches & Customers" },
    "navg.analysis": { ar: "التحليل والتوقعات", en: "Analysis & Forecasts" },
    "navg.reports": { ar: "القرارات والتقارير", en: "Decisions & Reports" },
    "navg.data": { ar: "البيانات والإعدادات", en: "Data & Settings" },
    // عناصر القائمة
    "nav.command": { ar: "مركز القيادة التنفيذي", en: "Executive Command Center" },
    "nav.health": { ar: "صحة الشركة", en: "Company Health" },
    "nav.goals": { ar: "الأهداف والنتائج", en: "Goals & Results" },
    "nav.board": { ar: "عرض مجلس الإدارة", en: "Board View" },
    "nav.finance": { ar: "الوحدة المالية", en: "Finance Unit" },
    "nav.cashflow": { ar: "التدفق النقدي", en: "Cash Flow" },
    "nav.leakage": { ar: "تحليل هدر الإيرادات", en: "Revenue Leakage" },
    "nav.tax": { ar: "الضريبة والزكاة", en: "Tax & Zakat" },
    "nav.sales": { ar: "المبيعات التفصيلية", en: "Detailed Sales" },
    "nav.ops": { ar: "التشغيل", en: "Operations" },
    "nav.inventory": { ar: "المخزون", en: "Inventory" },
    "nav.procurement": { ar: "المشتريات", en: "Procurement" },
    "nav.hr": { ar: "الموارد البشرية", en: "Human Resources" },
    "nav.customers": { ar: "وحدة العملاء", en: "Customers" },
    "nav.competitors": { ar: "المنافسون", en: "Competitors" },
    "nav.events": { ar: "الأحداث المؤثرة", en: "Key Events" },
    "nav.predictions": { ar: "التنبؤ بالأداء", en: "Performance Forecast" },
    "nav.rootcause": { ar: "الأسباب الجذرية", en: "Root Causes" },
    "nav.benchmarks": { ar: "مقارنة بالقطاع", en: "Sector Benchmarks" },
    "nav.risks": { ar: "محرّك المخاطر", en: "Risk Engine" },
    "nav.monthly": { ar: "التقرير الشهري", en: "Monthly Report" },
    "nav.memory": { ar: "ذاكرة الشركة", en: "Company Memory" },
    "nav.upload": { ar: "رفع ملف Excel/CSV", en: "Upload Excel/CSV" },
    "nav.dataquality": { ar: "جودة البيانات", en: "Data Quality" },
    "nav.team": { ar: "صلاحيات الفريق", en: "Team Permissions" },
    // عناوين حقيقية للصفحات
    "page.input.h": { ar: "إدخال بيانات فرع", en: "Enter Branch Data" },
    "page.decisions.h": { ar: "متابعة القرارات", en: "Decision Tracking" },
    "page.leakage.h": { ar: "كشف التسرّب والاحتيال", en: "Leak & Fraud Detection" },
    "page.branches.h": { ar: "مقارنة الفروع", en: "Branch Comparison" },
    "page.cashflow.h": { ar: "التدفق النقدي التنبؤي", en: "Predictive Cash Flow" },
    "page.monthly.h": { ar: "التقرير الشهري", en: "Monthly Report" },
    "btn.save": { ar: "حفظ التغييرات", en: "Save Changes" },
    "home.hero.1": { ar: "أرقام منشأتك تستحق", en: "Your numbers deserve" },
    "home.hero.2": { ar: "قراراً أوضح", en: "a clearer decision" },
    "home.badge": { ar: "تحليل بتقنيات متقدمة ومراجعة خبراء", en: "Advanced analysis with expert review" },
    "home.cta.start": { ar: "ابدأ الآن", en: "Start Now" },
    "home.cta.plans": { ar: "شاهد الباقات", en: "View Plans" },
    "home.stat1": { ar: "تقرير كامل جاهز", en: "Full report ready" },
    "home.stat2": { ar: "لصحة منشأتك", en: "for your business health" },
    "home.stat2.t": { ar: "درجات ذكية", en: "Smart Scores" },
    "home.stat3.t": { ar: "قرارات", en: "Decisions" },
    "home.stat3": { ar: "مرئية بالأولوية", en: "ranked by priority" },
    "home.report.title": { ar: "تقرير منشأتك", en: "Your Business Report" },
    "home.plan.pro": { ar: "الباقة الاحترافية", en: "Professional Plan" },
    "home.chip.facts": { ar: "حقائق مؤكدة", en: "Confirmed facts" },
    "home.chip.assump": { ar: "فرضيات واضحة", en: "Clear assumptions" },
    "home.chip.missing": { ar: "بيانات ناقصة", en: "Missing data" },

    // ---- أزرار ومشتركات ----
    "btn.save": { ar: "حفظ", en: "Save" },
    "btn.analyze": { ar: "تحليل", en: "Analyze" },
    "btn.upload": { ar: "رفع ملف", en: "Upload File" },
    "btn.add": { ar: "إضافة", en: "Add" },
    "btn.cancel": { ar: "إلغاء", en: "Cancel" },
    "btn.back": { ar: "رجوع", en: "Back" },
    "btn.next": { ar: "التالي", en: "Next" },
    "btn.print": { ar: "طباعة / PDF", en: "Print / PDF" },
    "btn.confirm": { ar: "تأكيد", en: "Confirm" },
    "btn.start": { ar: "ابدأ الآن", en: "Start Now" },
    "btn.login": { ar: "تسجيل الدخول", en: "Log in" },
    "btn.register": { ar: "إنشاء حساب", en: "Sign up" },

    "common.sar": { ar: "ريال", en: "SAR" },
    "common.monthly": { ar: "شهرياً", en: "monthly" },
    "common.loading": { ar: "جارٍ التحميل…", en: "Loading…" },
    "common.branch": { ar: "فرع", en: "Branch" },
    "common.company": { ar: "الشركة", en: "Company" },
    "common.sales": { ar: "المبيعات", en: "Sales" },
    "common.expenses": { ar: "المصروفات", en: "Expenses" },
    "common.profit": { ar: "الربح", en: "Profit" },

    // ---- اللوحة ----
    "dash.title": { ar: "لوحة المدير التنفيذي", en: "Executive Dashboard" },
    "dash.subtitle": { ar: "تحليل شامل لأداء الفروع ومقارنتها بالمؤشرات الرئيسية", en: "Full analysis of branch performance against key metrics" },
    "dash.pulse": { ar: "نبض شركتك", en: "Company Pulse" },
    "dash.pulse.hint": { ar: "راجعه أسبوعياً — محسوب من بياناتك الفعلية", en: "Review weekly — computed from your actual data" },
    "dash.empty.title": { ar: "الشركة جاهزة — أدخل بيانات الفروع", en: "Company ready — add your branch data" },

    // ---- الإشارات (النبض) ----
    "signal.liquidity": { ar: "السيولة", en: "Liquidity" },
    "signal.profit": { ar: "الربح", en: "Profit" },
    "signal.growth": { ar: "النمو", en: "Growth" },
    "signal.risk": { ar: "المخاطر", en: "Risk" },

    // ---- القرارات ----
    "dec.title": { ar: "مركز القرارات", en: "Decision Center" },
    "dec.impact": { ar: "الأثر المالي المتوقع", en: "Expected Financial Impact" },
    "dec.confidence": { ar: "درجة الثقة", en: "Confidence" },
    "dec.effort": { ar: "الوقت المطلوب", en: "Time to Execute" },
    "dec.priority": { ar: "الأولوية", en: "Priority" },
    "dec.owner": { ar: "المسؤول", en: "Owner" },
    "dec.due": { ar: "الموعد", en: "Due Date" },
    "dec.status.open": { ar: "قيد التنفيذ", en: "In Progress" },
    "dec.status.done": { ar: "منجز", en: "Done" },
    "dec.approve": { ar: "اعتمد للمتابعة", en: "Approve & Track" },

    // ---- البيانات الناقصة (رسالة مطمئنة) ----
    "missing.title": { ar: "نتيجة تحتاج إلى تحقق", en: "Result needs verification" },
    "missing.body": { ar: "هذه ليست مشكلة في شركتك — بعض البيانات غير مكتملة، لذا الدقة محدودة. أكمل البيانات التالية لتحليل أعمق:", en: "This isn't a problem with your company — some data is incomplete, so accuracy is limited. Add the following for deeper analysis:" },

    // ---- التوقعات ----
    "outlook.title": { ar: "النظرة المستقبلية", en: "Forecast Outlook" },

    // ---- اللوحة: بطاقات ومقاطع كاملة ----
    "dash.loading": { ar: "جارٍ تحميل بيانات الشركة…", en: "Loading company data…" },
    "dash.start.title": { ar: "ابدأ بإنشاء شركتك", en: "Start by creating your company" },
    "dash.start.body": { ar: "سجّل شركتك وفروعها، أدخل بيانات كل فرع، ويبدأ نبّاه يحلل الأداء ويقارن الفروع ويعطيك قرارات تنفيذية فوراً.", en: "Register your company and branches, enter each branch's data, and Nabbah starts analyzing performance, comparing branches, and giving you executive decisions right away." },
    "dash.start.btn": { ar: "إنشاء شركة جديدة", en: "Create a new company" },
    "dash.pending.title": { ar: "شركتك قيد التفعيل", en: "Your company is being activated" },
    "dash.pending.body": { ar: "تم تسجيل شركتك بنجاح وهي الآن بانتظار تفعيل الاشتراك. بمجرد التفعيل تنفتح لك كل خدمات قسم الشركات.", en: "Your company was registered successfully and is awaiting subscription activation. Once active, all company services unlock." },
    "dash.pending.contact": { ar: "تواصل لإتمام التفعيل", en: "Contact us to activate" },
    "dash.pending.plans": { ar: "عرض الباقات والأسعار", en: "View plans & pricing" },
    "dash.ready.title": { ar: "الشركة جاهزة — أدخل بيانات الفروع", en: "Company ready — add your branch data" },
    "dash.ready.upload": { ar: "رفع ملف — الأسرع والأذكى", en: "Upload a file — fastest and smartest" },
    "dash.ready.manual": { ar: "أو إدخال يدوي فرع بفرع", en: "Or enter manually, branch by branch" },
    "dash.cmp.sales": { ar: "مقارنة الفروع — المبيعات", en: "Branch Comparison — Sales" },
    "dash.cmp.sales.sub": { ar: "المبيعات الفعلية لكل فرع (آخر فترة)", en: "Actual sales per branch (latest period)" },
    "dash.rank": { ar: "ترتيب الفروع (مؤشر الأداء)", en: "Branch Ranking (Performance Score)" },
    "dash.rank.sub": { ar: "من 100 — مع اتجاه المؤشر عن الفترة السابقة", en: "Out of 100 — with trend vs previous period" },
    "dash.map": { ar: "توزيع الفروع على الخريطة", en: "Branches on the Map" },
    "dash.map.sub": { ar: "كل فرع بلونه حسب أدائه", en: "Each branch colored by performance" },
    "rate.excellent": { ar: "ممتاز", en: "Excellent" },
    "rate.good": { ar: "جيد", en: "Good" },
    "rate.mid": { ar: "متوسط", en: "Average" },
    "rate.weak": { ar: "ضعيف", en: "Weak" },
    "dash.rootcause": { ar: "تحليل الأسباب", en: "Root-Cause Analysis" },
    "dash.rootcause.sub": { ar: "الفرع الأقل أداءً مقابل متوسط الشركة", en: "Lowest branch vs company average" },
    "dash.bestpractice": { ar: "أفضل الممارسات", en: "Best Practices" },
    "dash.bestpractice.sub": { ar: "من الفرع الأعلى أداءً", en: "From the top-performing branch" },
    "dash.similar": { ar: "مقارنة الفروع المتشابهة", en: "Compare Similar Branches" },
    "dash.similar.sub": { ar: "حسب نوع الفرع", en: "By branch type" },
    "dash.forecast": { ar: "التنبؤ بالأداء (الفترة القادمة)", en: "Performance Forecast (next period)" },
    "dash.forecast.sub": { ar: "مبني على اتجاه مبيعات كل فرع عبر الفترات السابقة", en: "Based on each branch's sales trend across prior periods" },
    "dash.simulator": { ar: "محاكي القرارات", en: "Decision Simulator" },
    "dash.simulator.sub": { ar: "شوف أثر قراراتك على المبيعات والربح قبل ما تنفّذها", en: "See the impact of your decisions on sales and profit before you act" },
  };

  const STORAGE_KEY = "nabbah_lang";
  const FONT_AR = "'Tajawal', sans-serif";
  const FONT_EN = "'Inter', 'Segoe UI', system-ui, sans-serif";

  function getLang() {
    return localStorage.getItem(STORAGE_KEY) || "ar";
  }

  function t(key, lang) {
    lang = lang || getLang();
    const entry = DICT[key];
    if (!entry) return key;
    return entry[lang] || entry.ar || key;
  }

  // يطبّق اللغة على كل عناصر data-i18n في الصفحة
  function apply(lang) {
    lang = lang || getLang();
    localStorage.setItem(STORAGE_KEY, lang);

    // اتجاه الصفحة والخط
    const html = document.documentElement;
    html.setAttribute("lang", lang);
    html.setAttribute("dir", lang === "ar" ? "rtl" : "ltr");
    document.body && (document.body.style.fontFamily = lang === "ar" ? FONT_AR : FONT_EN);

    // ترجمة النصوص
    document.querySelectorAll("[data-i18n]").forEach(function (el) {
      const key = el.getAttribute("data-i18n");
      const val = t(key, lang);
      if (val && val !== key) el.textContent = val;
    });
    // ترجمة placeholder
    document.querySelectorAll("[data-i18n-ph]").forEach(function (el) {
      const key = el.getAttribute("data-i18n-ph");
      const val = t(key, lang);
      if (val && val !== key) el.setAttribute("placeholder", val);
    });
    // ترجمة العنوان (title attribute)
    document.querySelectorAll("[data-i18n-title]").forEach(function (el) {
      const key = el.getAttribute("data-i18n-title");
      const val = t(key, lang);
      if (val && val !== key) el.setAttribute("title", val);
    });

    // تحديث زر التبديل نفسه (عائم أو في القائمة)
    const btn = document.getElementById("langToggle");
    if (btn) btn.textContent = lang === "ar" ? "EN" : "ع";
    const navBtn = document.getElementById("navLangToggle");
    if (navBtn) navBtn.textContent = lang === "ar" ? "EN" : "عربي";

    // حدث مخصّص للصفحات اللي تبي تتفاعل (مثل إعادة رسم الشارت بلغة ثانية)
    window.dispatchEvent(new CustomEvent("nabbah:langchange", { detail: { lang: lang } }));
  }

  function toggle() {
    apply(getLang() === "ar" ? "en" : "ar");
  }

  // ينشئ زر التبديل تلقائياً لو ما كان موجوداً في الصفحة
  function ensureToggle() {
    // لو فيه زر في القائمة (navLangToggle) نستخدمه ولا ننشئ العائم
    var navBtn = document.getElementById("navLangToggle");
    if (navBtn) {
      navBtn.textContent = getLang() === "ar" ? "EN" : "عربي";
      navBtn.onclick = toggle;
      return;
    }
    if (document.getElementById("langToggle")) return;
    const btn = document.createElement("button");
    btn.id = "langToggle";
    btn.type = "button";
    btn.setAttribute("aria-label", "Switch language");
    btn.textContent = getLang() === "ar" ? "EN" : "ع";
    btn.style.cssText =
      "position:fixed;bottom:18px;" + (getLang() === "ar" ? "left:18px;" : "right:18px;") +
      "z-index:9999;width:46px;height:46px;border-radius:50%;border:1px solid rgba(16,185,129,.35);" +
      "background:#0b8457;color:#fff;font-weight:800;font-size:15px;cursor:pointer;" +
      "box-shadow:0 6px 20px rgba(0,0,0,.35);font-family:system-ui,sans-serif;transition:.2s";
    btn.onmouseenter = function () { btn.style.transform = "scale(1.08)"; };
    btn.onmouseleave = function () { btn.style.transform = "scale(1)"; };
    btn.onclick = toggle;
    document.body.appendChild(btn);
  }

  // ═══ اعتراض fetch: يرسل هيدر X-Lang تلقائياً مع كل طلب للسيرفر ═══
  // بهذا، التحليلات والتقارير من الباك-إند ترجع بلغة المستخدم دون تعديل كل صفحة.
  (function () {
    if (window.__nabbahFetchPatched) return;
    window.__nabbahFetchPatched = true;
    const orig = window.fetch;
    window.fetch = function (input, init) {
      init = init || {};
      const headers = new Headers(init.headers || {});
      if (!headers.has("X-Lang")) headers.set("X-Lang", getLang());
      init.headers = headers;
      return orig.call(this, input, init);
    };
  })();

  // واجهة عامة
  window.NabbahI18n = { apply: apply, toggle: toggle, t: t, getLang: getLang, dict: DICT };

  // تشغيل تلقائي عند تحميل الصفحة
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { ensureToggle(); apply(); });
  } else {
    ensureToggle();
    apply();
  }
})();
