"""
NABBAH — إعداد الاختبارات المشترك (conftest)
يضمن: بيئة اختبار معزولة، لا اتصال بقاعدة الإنتاج، إعداد آمن.
"""
import os
import sys

# ═══ حماية حرجة: نفرض وضع الاختبار قبل استيراد أي شيء ═══
os.environ["TESTING"] = "true"
os.environ["ENVIRONMENT"] = "development"
# قاعدة اختبار معزولة (SQLite في الذاكرة/ملف مؤقت) — لا الإنتاج أبداً
os.environ["DATABASE_URL"] = "sqlite:///./test_nabbah.db"
os.environ["SECRET_KEY"] = "test-only-secret-not-for-production"
os.environ["ADMIN_KEY"] = "test-admin-key"

import pytest


def _guard_production_db():
    """يفشل فوراً لو كان هناك أي احتمال للاتصال بقاعدة الإنتاج."""
    db = os.environ.get("DATABASE_URL", "")
    # علامات قاعدة الإنتاج على Railway
    danger = ["railway", "rlwy", "proxy.rlwy", "postgres.railway", ".railway.app"]
    if any(d in db.lower() for d in danger):
        pytest.exit(
            "🛑 توقّف الأمان: DATABASE_URL يشير إلى قاعدة إنتاج! "
            "الاختبارات يجب ألا تتصل بالإنتاج أبداً.",
            returncode=1,
        )
    if not db.startswith("sqlite"):
        pytest.exit(
            f"🛑 توقّف الأمان: قاعدة الاختبار ليست SQLite معزولة (DATABASE_URL={db[:30]}...).",
            returncode=1,
        )


_guard_production_db()

# نضيف مسار المشروع لاستيراد main
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(scope="session")
def app_module():
    """يستورد التطبيق مرة واحدة في وضع الاختبار الآمن."""
    _guard_production_db()
    import main
    return main


@pytest.fixture(scope="session")
def client(app_module):
    """FastAPI TestClient على قاعدة اختبار معزولة."""
    from fastapi.testclient import TestClient
    # ننشئ الجداول في قاعدة الاختبار
    from sqlmodel import SQLModel
    SQLModel.metadata.create_all(app_module.engine)
    return TestClient(app_module.app)


@pytest.fixture
def clean_db(app_module):
    """قاعدة نظيفة قبل كل اختبار يحتاجها."""
    from sqlmodel import SQLModel
    SQLModel.metadata.drop_all(app_module.engine)
    SQLModel.metadata.create_all(app_module.engine)
    yield


def teardown_module():
    """تنظيف ملف قاعدة الاختبار."""
    try:
        os.remove("./test_nabbah.db")
    except OSError:
        pass
