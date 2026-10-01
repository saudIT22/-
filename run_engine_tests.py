"""
نبّاه — مشغّل اختبارات المحركات (394 اختباراً) بغضّ النظر عن مكان مجلدات phase.
يقبل phase21..phase25 في الجذر أو داخل engines/. يفشل (exit 1) إن فشل أي اختبار.
يتحقق أيضاً أن nabbah_engines.py مطابق حرفياً لملفات المحركات (الدرس ١ في وثيقة التسليم).
"""
import base64, glob, os, re, shutil, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
PHASES = ["phase21", "phase22", "phase23", "phase24", "phase25", "phase26", "phase27", "phase28", "phase29"]
SKIP = {"test_api_integration.py"}  # pytest فارغ قديم — استُبدل بـ test_api_security.py


def find_phase(p):
    for base in (ROOT, os.path.join(ROOT, "engines")):
        if os.path.isdir(os.path.join(base, p)):
            return os.path.join(base, p)
    return None


def check_bundle(dirs):
    src = open(os.path.join(ROOT, "nabbah_engines.py"), encoding="utf-8").read()
    mods = dict(re.findall(r'\("(\w+)", "([A-Za-z0-9+/=]+)"\)', src))
    bad = []
    for d in dirs.values():
        for f in glob.glob(os.path.join(d, "*.py")):
            n = os.path.basename(f)[:-3]
            if n.startswith("test_"):
                continue
            cur = open(f, encoding="utf-8").read()
            if n not in mods:
                bad.append(f"{n}: غير موجود في الحزمة")
            elif base64.b64decode(mods[n]).decode("utf-8") != cur:
                bad.append(f"{n}: الحزمة قديمة — أعد بناء nabbah_engines.py")
    return bad


def main():
    dirs = {p: find_phase(p) for p in PHASES}
    missing = [p for p, d in dirs.items() if not d]
    if missing:
        print("❌ مجلدات مفقودة:", missing); return 1

    bad = check_bundle(dirs)
    for b in bad:
        print("❌", b)

    work = tempfile.mkdtemp()
    for p, d in dirs.items():
        shutil.copytree(d, os.path.join(work, p))
    for f in glob.glob(os.path.join(ROOT, "*.html")) + glob.glob(os.path.join(ROOT, "*.js")) + [os.path.join(ROOT, "main.py")]:
        shutil.copy(f, work)

    env = dict(os.environ, PYTHONPATH=os.pathsep.join(os.path.join(work, p) for p in PHASES))
    total = failed_files = 0
    for p in PHASES:
        for t in sorted(glob.glob(os.path.join(work, p, "test_*.py"))):
            if os.path.basename(t) in SKIP:
                continue
            r = subprocess.run([sys.executable, os.path.basename(t)], cwd=os.path.dirname(t),
                               env=env, capture_output=True, text=True, timeout=300)
            out = r.stdout + r.stderr
            m = re.search(r"TOTAL:\s*(\d+)\s*\|\s*PASSED:\s*(\d+)\s*\|\s*FAILED:\s*(\d+)", out) \
                or re.search(r"(\d+)/(\d+)\s*اختبار ناجح", out)
            if m and len(m.groups()) == 3:
                n, ok = int(m.group(1)), int(m.group(3)) == 0
            elif m:
                n, ok = int(m.group(2)), m.group(1) == m.group(2)
            else:
                n, ok = 0, False
            ok = ok and r.returncode == 0
            total += n
            failed_files += (not ok)
            print(("✅" if ok else "❌"), f"{p}/{os.path.basename(t)}: {n}")
            if not ok:
                print(out[-2000:])
    print(f"\nالمجموع: {total} اختباراً — ملفات فاشلة: {failed_files}")
    return 1 if (failed_files or bad) else 0


if __name__ == "__main__":
    sys.exit(main())
