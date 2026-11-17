/* نبّاه — Service Worker (المرحلة 9): تثبيت التطبيق + صفحة «غير متصل».
   لا يخزّن أي بيانات شركة: طلبات الواجهة البرمجية تمر للشبكة دائماً. الصفحات: الشبكة أولاً ثم آخر نسخة. */
const V = "nabbah-shell-9.0";
const SHELL = ["/nabbah-sidebar.js", "/icon-192.png", "/icon-512.png", "/manifest.webmanifest"];
const OFFLINE = '<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
  + '<title>نبّاه — غير متصل</title><body style="font-family:system-ui;background:#07100c;color:#e7f2ec;display:grid;place-items:center;min-height:100vh;margin:0;text-align:center;padding:16px">'
  + '<div><h1 style="color:#10b981">لا يوجد اتصال</h1><p>نبّاه يعرض أرقاماً حيّة من بياناتك، لذلك يحتاج اتصالاً بالإنترنت.</p><p>أعد المحاولة عند عودة الاتصال.</p>'
  + '<button onclick="location.reload()" style="padding:10px 18px;border-radius:10px;border:0;background:#0f766e;color:#fff;font-size:15px">إعادة المحاولة</button></div></body></html>';
self.addEventListener("install", e => { e.waitUntil(caches.open(V).then(c => c.addAll(SHELL)).then(() => self.skipWaiting())); });
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== V).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", e => {
  const r = e.request;
  if (r.method !== "GET") return;
  const u = new URL(r.url);
  if (u.origin !== location.origin) return;
  if (r.mode === "navigate" && u.pathname.endsWith(".html")) {
    e.respondWith(fetch(r).then(res => {
      if (res.ok) { const cp = res.clone(); caches.open(V).then(c => c.put(r, cp)); }
      return res;
    }).catch(() => caches.match(r).then(m => m || new Response(OFFLINE, { headers: { "Content-Type": "text/html; charset=utf-8" } }))));
    return;
  }
  if (SHELL.indexOf(u.pathname) >= 0) {
    e.respondWith(caches.match(r).then(m => m || fetch(r)));
  }
});
