#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════
#  NABBAH — تشغيل الاختبارات بأمان
#  يضمن: بيئة اختبار معزولة، لا اتصال بقاعدة الإنتاج
# ═══════════════════════════════════════════════════════════
set -e

echo "🔒 فرض بيئة الاختبار الآمنة..."
export TESTING=true
export ENVIRONMENT=development
export DATABASE_URL="sqlite:///./test_nabbah.db"
export SECRET_KEY="test-only-secret-not-for-production"
export ADMIN_KEY="test-admin-key"

# حماية: نرفض أي DATABASE_URL يشير للإنتاج
if echo "$DATABASE_URL" | grep -qiE "railway|rlwy|\.railway\.app"; then
  echo "🛑 توقّف: DATABASE_URL يشير للإنتاج! الاختبارات لا تلمس الإنتاج."
  exit 1
fi

echo "📦 تثبيت متطلبات الاختبار..."
pip install -q -r requirements-test.txt 2>/dev/null || pip install -q pytest httpx

echo "🧪 تشغيل الاختبارات..."
python3 -m pytest tests/ -v

echo ""
echo "🧹 تنظيف قاعدة الاختبار..."
rm -f ./test_nabbah.db

echo "✅ انتهت الاختبارات."
