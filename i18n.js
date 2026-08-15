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
