"""
╔══════════════════════════════════════════════════════════╗
║        FullMark Student Bot – Database Module            ║
║   يتصل بنفس Firebase الخاص بلوحة التحكم تماماً          ║
╚══════════════════════════════════════════════════════════╝

📌 ضع بيانات Firebase هنا — هي نفس بيانات لوحة التحكم الحالية
"""

import firebase_admin
from firebase_admin import credentials, db
import os, json, time

# ─────────────────────────────────────────────────────────
#  🔧 إعدادات Firebase  ← عدّل هنا فقط
# ─────────────────────────────────────────────────────────

FIREBASE_DATABASE_URL = "https://fullmark-neweddition-default-rtdb.europe-west1.firebasedatabase.app"

# الطريقة 1: بيانات Service Account مباشرة (للتطوير المحلي)
FIREBASE_CREDENTIALS = {
    "type": "service_account",
    "project_id": "fullmark-neweddition",
    "private_key_id": "8e6bea610e1b4d1af3d27c27e137bd34e6636a4e",
    "private_key": (
        "-----BEGIN PRIVATE KEY-----\n"
        "MIIEvAIBADANBgkqhkiG9w0BAQEFAASCBKYwggSiAgEAAoIBAQCj593kaisD2UBo\n"
        "oiWS25EmmBDCer1zV9LTsjtkh0Si7iEkZHA9wknRV71bAdhnBYB9ZVYSN5mjlnSf\n"
        "NhkO/CJ5gUtP9v0XPXk1SwFnx2qXq+WS3Cu+P87k43s/n7GhU3upFRGH5mi01Wq1\n"
        "BdW86TgTbwwSROXCnJ7N/8f3CvLj8rfuVO9C8Q57OLPVLZJxLqcRH52Fl/d0I+uv\n"
        "87pUl8+TjUOkkUEi94q/Wnv4+RG99hMuZxTY8K+zVs7nrkYVMszGnLV7Ww7chN9L\n"
        "ql4Ws9m3Xk8DfN0qsl2Mm3Zlkyx5Kn21Z8uf1MvK5gj6e8FThg5rtvMNGuBkdLv6\n"
        "IfVcz9yDAgMBAAECgf9Z6EM8MseoBDMlycvShSyZwV5mpOPCI8Si/C+hkZsP/XMd\n"
        "mwQJhy+E9jhoM11M/6lj8Aegqq+g9J/LOlgJQ8m4ToSKxaA+SfCy1IAbhNqsYWo8\n"
        "FoAhD/I5M1+vBr93W1iRfqx5MAHqJlaDSx0d5nFJG0a4ARx49Kl891FVZgYraxLU\n"
        "lLwK00kJbQaZ7cltFw76puOfwces1Zjyfa7csLUUnABO/4GkjN5JIbMGjGFetCNM\n"
        "1xN0kT8FhTTf/o1IP0YDVuPNgosYGanfQrKtWlBdw6CbeIxb06lDjW20glAD97Bd\n"
        "TIm6DsiNl9HS5bot2qOYekEbNfzPqweEinN6Da0CgYEA1sMr+DjgQB3QqCyHrFRg\n"
        "nfovm4uQFb6bVVgmOQHb9MCn5BjQQgsF6gvTrxpp69p0ALKJJCdk1trI9N7i+qwv\n"
        "z8eAf4ytHLFJMEyo9XJJmDu3i8EOACYuCyWzwCwbY/ckAc6FsZiMgAmIaSoNFpjH\n"
        "lNvyON5gGKgWJvPA4t6Ik1UCgYEAw2DK5K+DoieUbT4MzMl5U5syJZi0iPox5tpb\n"
        "+FL2+f8vbZq+mWOHosGA0YL9ejwCCoJ01exulNOlTdGI2o28u+fyoH0ozt54yqDR\n"
        "tqxbfDsPzQOYiwWn+Du1VEHAZuXH9az5PHM9ggqc/KuCONHbMsIG1RI7eFOQzawu\n"
        "JuLF4HcCgYEAtk3W9U7SjZrBlQC36sF1gqTt5MwD83FpyniZearqXEluO2IU5vsU\n"
        "eiiv+OQjJeK6thzX7ajDIN931uWdJ80iiO6BVcTE7qZPyoBIrJHnhyKqHCg1Ckte\n"
        "qnfGrkrCtYkFN8NoGem02rs84Iihs5zdTq+mXj/mswd8RnSEOBFPPkECgYAk5rEr\n"
        "hCLei48zGtccDqmFqvhLtY3TmT23lmJsgm73RMVWdDWvjubdTKLh71WkspTIG1+p\n"
        "z+AK5/Z+viaU8NRGwUZIHZuJhudVjg5N7DvTOOyBEj7LcyQIdG6JHWoThS7BLgxc\n"
        "6H8jgpGn/1S3GpvF+HOF5s2oqk/dKLoGyioJfQKBgQDQc2sWkR7raz1Z8v92hEE8\n"
        "kOE/FPLvDdlH5psghq05ZU0yxbBPCjJ6QmV1LLy1F+Ya+XeLjp5AE0jTpgorUlLB\n"
        "ZIc4wYipK2x0PypqgJD7+L3evqAwuXZEvtWiZPB2N+ueXsE00T4m35N4w9VNadI4\n"
        "dDfLoOIsWbfGCuvOA0SmZQ==\n"
        "-----END PRIVATE KEY-----\n"
    ),
    "client_email": "firebase-adminsdk-fbsvc@fullmark-neweddition.iam.gserviceaccount.com",
    "client_id": "103917924440939464168",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
}

# ─────────────────────────────────────────────────────────
#  تهيئة Firebase (مرة واحدة فقط)
# ─────────────────────────────────────────────────────────

def init_firebase():
    """تهيئة Firebase — تدعم متغيرات البيئة على Vercel أو البيانات المباشرة."""
    if firebase_admin._apps:
        return  # تم التهيئة مسبقاً

    # على Vercel: ضع محتوى service account كاملاً في متغير FIREBASE_CREDENTIALS_JSON
    env_creds = os.environ.get("FIREBASE_CREDENTIALS_JSON")
    if env_creds:
        cred_dict = json.loads(env_creds)
        cred = credentials.Certificate(cred_dict)
    else:
        cred = credentials.Certificate(FIREBASE_CREDENTIALS)

    firebase_admin.initialize_app(cred, {"databaseURL": FIREBASE_DATABASE_URL})


# ─────────────────────────────────────────────────────────
#  دوال قاعدة البيانات
# ─────────────────────────────────────────────────────────

def get_student(code: str) -> dict | None:
    """جلب بيانات طالب بالكود من جدول students."""
    init_firebase()
    snap = db.reference(f"students/{code}").get()
    return snap  # None لو مش موجود


def update_student(code: str, data: dict):
    """تحديث بيانات طالب في جدول students."""
    init_firebase()
    db.reference(f"students/{code}").update(data)


def get_all_students() -> dict:
    """جلب كل الطلاب — للاستخدام في عمليات الحظر الجماعي."""
    init_firebase()
    return db.reference("students").get() or {}


def save_student_telegram_id(code: str, telegram_id: int):
    """ربط كود الطالب بـ Telegram ID — يُستخدم للطرد التلقائي."""
    init_firebase()
    db.reference(f"students/{code}").update({
        "telegram_id": telegram_id,
        "telegram_linked_at": int(time.time() * 1000)
    })


def get_trial_info(telegram_id: int) -> dict | None:
    """
    التحقق إذا الطالب استخدم التجربة من قبل.
    يبحث في students عن أي كود مرتبط بنفس telegram_id من نوع trial.
    """
    init_firebase()
    all_students = db.reference("students").get() or {}
    for code, data in all_students.items():
        if (
            isinstance(data, dict)
            and data.get("telegram_id") == telegram_id
            and data.get("type") == "trial"
        ):
            return {"code": code, **data}
    return None


def get_active_subscription(telegram_id: int) -> dict | None:
    """البحث عن اشتراك نشط مرتبط بـ telegram_id."""
    init_firebase()
    now_ms = int(time.time() * 1000)
    all_students = db.reference("students").get() or {}
    for code, data in all_students.items():
        if (
            isinstance(data, dict)
            and data.get("telegram_id") == telegram_id
            and not data.get("banned", False)
            and data.get("expiry", 0) > now_ms
            and data.get("type") != "trial"
        ):
            return {"code": code, **data}
    return None
