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

    // تحديث زر التبديل نفسه
    const btn = document.getElementById("langToggle");
    if (btn) btn.textContent = lang === "ar" ? "EN" : "ع";

    // حدث مخصّص للصفحات اللي تبي تتفاعل (مثل إعادة رسم الشارت بلغة ثانية)
    window.dispatchEvent(new CustomEvent("nabbah:langchange", { detail: { lang: lang } }));
  }

  function toggle() {
    apply(getLang() === "ar" ? "en" : "ar");
  }

  // ينشئ زر التبديل تلقائياً لو ما كان موجوداً في الصفحة
  function ensureToggle() {
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
