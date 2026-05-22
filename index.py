"""
╔══════════════════════════════════════════════════════════════════╗
║       Full Mark ثانوية عامة — بوت الطلاب                        ║
║       Webhook: POST /telegram | Vercel + Firebase                ║
╚══════════════════════════════════════════════════════════════════╝

🔧 الإصلاحات:
  1. ✅ مسار Webhook: /telegram (مش /api)
  2. ✅ كل الكود Sync بدل Async — يحل مشكلة asyncio على Vercel
  3. ✅ sys.path مصلح لـ import صح
  4. ✅ Try-Except شامل + Fallback عند فشل DB
  5. ✅ /debug endpoint لتشخيص المشاكل
  6. ✅ رسالة /start: زر واحد → 3 خيارات
"""

import os, sys, time, logging
from datetime import datetime, timezone
from flask import Flask, request, jsonify
import httpx

# ── Fix import path for Vercel ──────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from database import (
        get_student, update_student, get_all_students,
        get_trial_info, get_active_subscription, init_firebase
    )
    DB_AVAILABLE = True
except Exception as _e:
    DB_AVAILABLE = False
    logging.warning(f"[DB] Import failed: {_e}")

# ── إعدادات ──────────────────────────────────────────────────────
BOT_TOKEN      = os.environ.get("BOT_TOKEN",      "8857188327:AAF0F4nLxgGLaRQkFjS1BK8kc3eiDCoNBCA")
GROUP_CHAT_ID  = int(os.environ.get("GROUP_CHAT_ID", "-1003997728302"))
ADMIN_USERNAME = "@DevAhmedmo"
TRIAL_MINUTES  = 30
TELEGRAM_API   = f"https://api.telegram.org/bot{BOT_TOKEN}"

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("FullMarkBot")

# ════════════════════════════════════════════════════════════════
#  Telegram API — كل الاتصالات Sync (httpx.Client)
#  هذا يحل مشكلة asyncio.run() على Vercel نهائياً
# ════════════════════════════════════════════════════════════════

def tg(method: str, **kwargs) -> dict:
    """استدعاء Telegram API بشكل Sync — لا يحتاج event loop."""
    try:
        with httpx.Client(timeout=15) as client:
            r = client.post(f"{TELEGRAM_API}/{method}", json=kwargs)
            data = r.json()
            if not data.get("ok"):
                logger.warning(f"TG {method} → {data.get('description')}")
            return data
    except Exception as e:
        logger.error(f"TG error ({method}): {e}")
        return {"ok": False, "description": str(e)}


def send_msg(chat_id: int, text: str,
             reply_markup=None, parse_mode: str = "HTML") -> dict:
    p = dict(chat_id=chat_id, text=text, parse_mode=parse_mode)
    if reply_markup:
        p["reply_markup"] = reply_markup
    return tg("sendMessage", **p)


def edit_msg(chat_id: int, message_id: int, text: str,
             reply_markup=None, parse_mode: str = "HTML") -> dict:
    p = dict(chat_id=chat_id, message_id=message_id,
             text=text, parse_mode=parse_mode)
    if reply_markup:
        p["reply_markup"] = reply_markup
    return tg("editMessageText", **p)


def answer_cb(callback_id: str, text: str = "") -> dict:
    return tg("answerCallbackQuery",
              callback_query_id=callback_id, text=text)


def kick_user(user_id: int) -> bool:
    """طرد مؤقت من المجموعة ثم فك الحظر فوراً."""
    try:
        tg("banChatMember",
           chat_id=GROUP_CHAT_ID,
           user_id=user_id,
           until_date=int(time.time()) + 35)
        time.sleep(0.5)
        tg("unbanChatMember",
           chat_id=GROUP_CHAT_ID,
           user_id=user_id,
           only_if_banned=True)
        logger.info(f"Kicked user {user_id}")
        return True
    except Exception as e:
        logger.error(f"Kick error {user_id}: {e}")
        return False


def create_invite() -> str | None:
    """رابط دخول لعضو واحد فقط."""
    r = tg("createChatInviteLink",
           chat_id=GROUP_CHAT_ID,
           member_limit=1,
           creates_join_request=False)
    return r["result"]["invite_link"] if r.get("ok") else None

# ════════════════════════════════════════════════════════════════
#  لوحات المفاتيح
# ════════════════════════════════════════════════════════════════

KB_START = {
    "inline_keyboard": [
        [{"text": "يلا ابدأ يا بطل 🚀", "callback_data": "main_menu"}]
    ]
}

KB_MAIN = {
    "inline_keyboard": [
        [{"text": "🎁 تجربة مجانية (نص ساعة)",   "callback_data": "trial"}],
        [{"text": "💳 طريقة الاشتراك",             "callback_data": "how_subscribe"}],
        [{"text": "🔑 تفعيل الكود / دخول المواد", "callback_data": "activate"}],
    ]
}

KB_BACK = {"inline_keyboard": [[{"text": "↩️ رجوع", "callback_data": "main_menu"}]]}

def kb_cancel():
    return {"inline_keyboard": [[{"text": "❌ إلغاء", "callback_data": "main_menu"}]]}

# ════════════════════════════════════════════════════════════════
#  Sessions
# ════════════════════════════════════════════════════════════════

_sessions: dict = {}

def sess(uid: int) -> dict:
    return _sessions.setdefault(uid, {})

def clear_sess(uid: int):
    _sessions.pop(uid, None)

# ════════════════════════════════════════════════════════════════
#  Flask App
# ════════════════════════════════════════════════════════════════

app = Flask(__name__)


@app.route("/telegram", methods=["POST"])
def webhook():
    """✅ مسار الـ Webhook الصحيح — POST /telegram"""
    data = request.get_json(force=True, silent=True)
    if not data:
        return jsonify(ok=True)

    # استخرج chat_id للـ fallback
    fallback_cid = None
    try:
        if "message" in data:
            fallback_cid = data["message"]["chat"]["id"]
        elif "callback_query" in data:
            fallback_cid = data["callback_query"]["message"]["chat"]["id"]
    except Exception:
        pass

    try:
        dispatch(data)
    except Exception as e:
        logger.exception(f"Dispatch crashed: {e}")
        # ── Fallback: البوت يرد حتى لو حصل خطأ ──
        if fallback_cid:
            try:
                send_msg(fallback_cid,
                         "👋 <b>أهلاً بك في Full Mark!</b>\n\n"
                         "اضغط /start للبدء.",
                         reply_markup=KB_START)
            except Exception:
                pass

    return jsonify(ok=True)


@app.route("/", methods=["GET"])
@app.route("/telegram", methods=["GET"])
@app.route("/health", methods=["GET"])
def health():
    return "🤖 Full Mark Bot — OK | Webhook: POST /telegram", 200


@app.route("/debug", methods=["GET"])
def debug():
    """✅ endpoint للتشخيص — افتحه في المتصفح لو البوت مش شغال"""
    try:
        wh = tg("getWebhookInfo").get("result", {})
    except Exception as e:
        wh = {"error": str(e)}

    return jsonify({
        "bot_token_prefix": BOT_TOKEN[:12] + "...",
        "group_chat_id":    GROUP_CHAT_ID,
        "db_available":     DB_AVAILABLE,
        "webhook_url":      wh.get("url", "not set"),
        "webhook_pending":  wh.get("pending_update_count", "?"),
        "webhook_error":    wh.get("last_error_message", "none"),
        "status":           "running"
    })

# ════════════════════════════════════════════════════════════════
#  Middleware: فحص الانتهاء/الحظر عند كل تفاعل (بدون Cron Jobs)
#  يقرأ expiry timestamp من Firebase ويطرد لو انتهى الوقت
# ════════════════════════════════════════════════════════════════

def check_and_kick(user_id: int) -> bool:
    """
    يُستدعى عند كل رسالة/ضغطة زر.
    يحسب برمجياً إذا انتهى وقت الكود — لا يحتاج Cron.
    """
    if not DB_AVAILABLE:
        return False
    try:
        from firebase_admin import db as fdb
        init_firebase()
        now_ms = int(time.time() * 1000)
        students = fdb.reference("students").get() or {}

        for code, d in students.items():
            if not isinstance(d, dict):
                continue
            if d.get("telegram_id") != user_id:
                continue

            expired = d.get("expiry", 0) <= now_ms
            banned  = d.get("banned", False)
            if not (expired or banned):
                continue

            kick_user(user_id)

            if banned:
                msg = (f"🚫 <b>تم إيقاف اشتراكك!</b>\n\n"
                       f"للاستفسار: {ADMIN_USERNAME}")
            elif d.get("type") == "trial":
                msg = ("⌛ <b>انتهت التجربة المجانية!</b>\n\n"
                       "اشترك معنا للاستمرار في التعلم 👇")
            else:
                msg = ("⌛ <b>انتهى اشتراكك!</b>\n\n"
                       "جدد اشتراكك للاستمرار 📚")

            try:
                send_msg(user_id, msg, reply_markup={"inline_keyboard": [
                    [{"text": "🔑 تفعيل / تجديد", "callback_data": "activate"}],
                    [{"text": "💳 طريقة الاشتراك", "callback_data": "how_subscribe"}],
                ]})
            except Exception:
                pass

            logger.info(f"[MW] kicked={user_id} code={code} expired={expired} banned={banned}")
            return True

    except Exception as e:
        logger.warning(f"[MW] check error: {e}")

    return False

# ════════════════════════════════════════════════════════════════
#  Dispatcher
# ════════════════════════════════════════════════════════════════

def dispatch(update: dict):
    uid = None
    if "message" in update:
        uid = update["message"]["from"]["id"]
    elif "callback_query" in update:
        uid = update["callback_query"]["from"]["id"]

    if uid and DB_AVAILABLE:
        if check_and_kick(uid):
            if "callback_query" in update:
                try:
                    answer_cb(update["callback_query"]["id"])
                except Exception:
                    pass
            return

    if "message" in update:
        handle_message(update["message"])
    elif "callback_query" in update:
        handle_callback(update["callback_query"])

# ════════════════════════════════════════════════════════════════
#  معالج الرسائل
# ════════════════════════════════════════════════════════════════

def handle_message(msg: dict):
    uid   = msg["from"]["id"]
    cid   = msg["chat"]["id"]
    text  = msg.get("text", "").strip()
    first = msg["from"].get("first_name", "صديقي")

    if text.startswith("/start"):
        clear_sess(uid)
        send_msg(cid,
                 f"🎓 <b>أهلاً بك يا {first} في بوت Full Mark!</b>\n\n"
                 "━━━━━━━━━━━━━━━━━━━━━━━\n"
                 "🏆 <b>منصة المذاكرة الأولى للثانوية العامة</b>\n"
                 "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                 "📚 محتوى شامل لكل المواد\n"
                 "🎯 امتحانات وتدريبات تفاعلية\n"
                 "👨‍🏫 شرح مباشر من أفضل المدرسين\n"
                 "📈 تابع تقدمك أولاً بأول\n\n"
                 "━━━━━━━━━━━━━━━━━━━━━━━\n"
                 "👇 اضغط الزر للبدء:",
                 reply_markup=KB_START)
        return

    if text in ("إلغاء", "/cancel"):
        clear_sess(uid)
        send_msg(cid, "↩️ تم الإلغاء. اضغط /start للقائمة.")
        return

    if sess(uid).get("step") == "WAIT_CODE":
        process_code(cid, uid, text)
        return

    send_msg(cid,
             f"👋 <b>أهلاً {first}!</b>\nاضغط /start للقائمة 👇",
             reply_markup=KB_START)

# ════════════════════════════════════════════════════════════════
#  معالج الأزرار
# ════════════════════════════════════════════════════════════════

def handle_callback(cb: dict):
    uid   = cb["from"]["id"]
    cid   = cb["message"]["chat"]["id"]
    mid   = cb["message"]["message_id"]
    data  = cb.get("data", "")
    first = cb["from"].get("first_name", "صديقي")
    cbid  = cb["id"]

    answer_cb(cbid)

    if data == "main_menu":
        clear_sess(uid)
        edit_msg(cid, mid,
                 f"👋 <b>أهلاً {first}!</b>\n\nاختار من القائمة 👇",
                 reply_markup=KB_MAIN)

    elif data == "trial":
        handle_trial(cid, mid, uid, first)

    elif data == "confirm_trial":
        start_trial(cid, mid, uid, first)

    elif data == "how_subscribe":
        edit_msg(cid, mid,
                 f"💳 <b>طريقة الاشتراك في Full Mark</b>\n\n"
                 "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                 "📋 <b>خطوات الاشتراك:</b>\n\n"
                 f"1️⃣ تواصل مع الأدمن: <b>{ADMIN_USERNAME}</b>\n\n"
                 "2️⃣ أخبره بالاشتراك الذي تريده\n\n"
                 "3️⃣ ادفع رسوم الاشتراك\n\n"
                 "4️⃣ ستحصل على <b>كود 9 أرقام</b>\n\n"
                 "5️⃣ ارجع واضغط على\n"
                 "    <b>🔑 تفعيل الكود / دخول المواد</b>\n\n"
                 "━━━━━━━━━━━━━━━━━━━━━━━\n"
                 "⚡ <b>التفعيل فوري بعد الدفع!</b>",
                 reply_markup={"inline_keyboard": [
                     [{"text": f"💬 تواصل مع {ADMIN_USERNAME}",
                       "url": f"https://t.me/{ADMIN_USERNAME.lstrip('@')}"}],
                     [{"text": "🔑 عندي كود — فعّله الآن", "callback_data": "activate"}],
                     [{"text": "↩️ رجوع", "callback_data": "main_menu"}],
                 ]})

    elif data == "activate":
        sess(uid)["step"] = "WAIT_CODE"
        edit_msg(cid, mid,
                 "🔑 <b>تفعيل كود الاشتراك</b>\n\n"
                 "أرسل الكود المكوّن من 9 أرقام:\n"
                 "مثال: <code>659752466</code>",
                 reply_markup=kb_cancel())

    elif data in ("back_main", "cancel"):
        clear_sess(uid)
        edit_msg(cid, mid,
                 f"👋 <b>أهلاً {first}!</b>\n\nاختار من القائمة 👇",
                 reply_markup=KB_MAIN)

# ════════════════════════════════════════════════════════════════
#  منطق التجربة المجانية
# ════════════════════════════════════════════════════════════════

def handle_trial(cid: int, mid: int, uid: int, first: str):
    if not DB_AVAILABLE:
        edit_msg(cid, mid,
                 "⚠️ <b>خطأ مؤقت!</b>\nحاول بعد قليل.",
                 reply_markup=KB_BACK)
        return

    # مشترك بالفعل؟
    try:
        sub = get_active_subscription(uid)
        if sub:
            edit_msg(cid, mid,
                     "✅ <b>أنت مشترك بالفعل!</b>\n\n"
                     f"👤 {sub.get('name','غير محدد')}\n"
                     f"📅 ينتهي: {fmt_date(sub.get('expiry',0)/1000)}\n\n"
                     "لا تحتاج التجربة المجانية 😊",
                     reply_markup=KB_BACK)
            return
    except Exception as e:
        logger.warning(f"get_active_subscription: {e}")

    # سبق تجرّب؟
    try:
        trial = get_trial_info(uid)
    except Exception as e:
        logger.warning(f"get_trial_info: {e}")
        trial = None

    if trial:
        exp_ts = trial.get("expiry", 0) / 1000
        now_ts = time.time()
        if exp_ts > now_ts:
            rem = max(1, int((exp_ts - now_ts) / 60))
            edit_msg(cid, mid,
                     f"⏳ <b>لديك تجربة نشطة!</b>\n\n"
                     f"⏱ متبقي: <b>{rem} دقيقة</b>\n\n"
                     "ادخل من الرابط الذي أُرسل لك.",
                     reply_markup=KB_BACK)
        else:
            edit_msg(cid, mid,
                     "🚫 <b>انتهت فرصة التجربة المجانية!</b>\n\n"
                     "استخدمتها مسبقاً.\n"
                     f"للاشتراك تواصل مع: {ADMIN_USERNAME}",
                     reply_markup={"inline_keyboard": [
                         [{"text": f"💬 {ADMIN_USERNAME}",
                           "url": f"https://t.me/{ADMIN_USERNAME.lstrip('@')}"}],
                         [{"text": "🔑 تفعيل كود", "callback_data": "activate"}],
                         [{"text": "↩️ رجوع",      "callback_data": "main_menu"}],
                     ]})
        return

    # المرة الأولى
    edit_msg(cid, mid,
             f"🎁 <b>تجربة مجانية {TRIAL_MINUTES} دقيقة!</b>\n\n"
             "✨ ستحصل على:\n"
             "• رابط دخول مباشر للمجموعة\n"
             f"• وصول كامل لمدة {TRIAL_MINUTES} دقيقة\n"
             "• بعدها ستُخرج تلقائياً\n\n"
             "⚠️ التجربة تُستخدم مرة واحدة فقط!\n\n"
             "هل تبدأ الآن؟",
             reply_markup={"inline_keyboard": [
                 [{"text": "✅ نعم، ابدأ!", "callback_data": "confirm_trial"}],
                 [{"text": "❌ لا، رجوع",  "callback_data": "main_menu"}],
             ]})


def start_trial(cid: int, mid: int, uid: int, first: str):
    edit_msg(cid, mid, "⏳ <b>جاري تجهيز الرابط...</b>")

    link = create_invite()
    if not link:
        edit_msg(cid, mid,
                 f"❌ <b>خطأ في إنشاء الرابط!</b>\n\n"
                 f"تواصل مع: {ADMIN_USERNAME}",
                 reply_markup=KB_BACK)
        return

    now_ms  = int(time.time() * 1000)
    exp_ms  = now_ms + TRIAL_MINUTES * 60 * 1000
    code    = gen_code()

    trial_data = {
        "name":        f"تجربة - {first}",
        "expiry":      exp_ms,
        "hwid":        None,
        "banned":      False,
        "creator":     0,
        "creatorName": "بوت الطلاب",
        "createdAt":   now_ms,
        "free":        True,
        "type":        "trial",
        "telegram_id": uid,
        "invite_link": link,
    }

    try:
        from firebase_admin import db as fdb
        init_firebase()
        fdb.reference(f"students/{code}").set(trial_data)
        logger.info(f"Trial saved: uid={uid} code={code}")
    except Exception as e:
        logger.error(f"Trial save failed: {e}")
        # نكمل ونبعت الرابط حتى لو الحفظ فشل

    edit_msg(cid, mid,
             f"🎉 <b>تجربتك جاهزة يا {first}!</b>\n\n"
             f"⏱ المدة: {TRIAL_MINUTES} دقيقة\n"
             f"📅 تنتهي: {fmt_date(exp_ms/1000)}\n\n"
             f"🔗 <b>رابط الدخول:</b>\n{link}\n\n"
             "⚠️ <b>مهم:</b>\n"
             "• الرابط لاستخدام مرة واحدة\n"
             "• ستُخرج تلقائياً عند انتهاء الوقت\n"
             "• التجربة لا تتكرر 😊",
             reply_markup={"inline_keyboard": [
                 [{"text": "🔑 اشترك بعد التجربة", "callback_data": "activate"}],
                 [{"text": "💳 طريقة الاشتراك",    "callback_data": "how_subscribe"}],
             ]})

# ════════════════════════════════════════════════════════════════
#  تفعيل الكود
# ════════════════════════════════════════════════════════════════

def process_code(cid: int, uid: int, code: str):
    clear_sess(uid)

    if not code.isdigit() or len(code) != 9:
        send_msg(cid,
                 "❌ <b>صيغة خاطئة!</b>\n\n"
                 "الكود 9 أرقام بالضبط.\n"
                 "مثال: <code>659752466</code>",
                 reply_markup={"inline_keyboard": [
                     [{"text": "🔑 حاول مرة أخرى", "callback_data": "activate"}],
                     [{"text": "↩️ رجوع",           "callback_data": "main_menu"}],
                 ]})
        return

    if not DB_AVAILABLE:
        send_msg(cid,
                 f"⚠️ <b>خطأ مؤقت!</b>\nحاول بعد قليل أو تواصل: {ADMIN_USERNAME}",
                 reply_markup=KB_BACK)
        return

    try:
        student = get_student(code)
    except Exception as e:
        logger.error(f"get_student: {e}")
        send_msg(cid,
                 "⚠️ <b>خطأ في الاتصال!</b>\nحاول مرة أخرى.",
                 reply_markup={"inline_keyboard": [
                     [{"text": "🔄 أعد المحاولة", "callback_data": "activate"}],
                 ]})
        return

    if student is None:
        send_msg(cid,
                 "❌ <b>الكود غير موجود!</b>\n\n"
                 f"تأكد من الكود أو تواصل: {ADMIN_USERNAME}",
                 reply_markup={"inline_keyboard": [
                     [{"text": "🔑 حاول مرة أخرى", "callback_data": "activate"}],
                     [{"text": "↩️ رجوع",           "callback_data": "main_menu"}],
                 ]})
        return

    now_ms = int(time.time() * 1000)

    if student.get("banned"):
        send_msg(cid,
                 f"🚫 <b>هذا الكود محظور!</b>\n\nتواصل: {ADMIN_USERNAME}",
                 reply_markup=KB_BACK)
        return

    if student.get("expiry", 0) <= now_ms:
        send_msg(cid,
                 f"⌛ <b>هذا الكود منتهي!</b>\n\nجدد مع: {ADMIN_USERNAME}",
                 reply_markup={"inline_keyboard": [
                     [{"text": f"💬 {ADMIN_USERNAME}",
                       "url": f"https://t.me/{ADMIN_USERNAME.lstrip('@')}"}],
                     [{"text": "↩️ رجوع", "callback_data": "main_menu"}],
                 ]})
        return

    existing = student.get("telegram_id")
    if existing and existing != uid:
        send_msg(cid,
                 "📱 <b>الكود مستخدم بالفعل!</b>\n\n"
                 "كل كود لطالب واحد فقط.",
                 reply_markup=KB_BACK)
        return

    # ✅ الكود صحيح — تفعيل
    exp_str = fmt_date(student["expiry"] / 1000)
    try:
        update_student(code, {
            "telegram_id":        uid,
            "telegram_linked_at": now_ms,
            "activated_at":       now_ms,
        })
    except Exception as e:
        logger.error(f"update_student: {e}")

    link = create_invite()
    logger.info(f"Activated: uid={uid} code={code}")

    if link:
        send_msg(cid,
                 "🎉 <b>تم التفعيل بنجاح!</b>\n\n"
                 f"👤 <b>الاسم:</b> {student.get('name','غير محدد')}\n"
                 f"📅 <b>ينتهي:</b> {exp_str}\n\n"
                 f"🔗 <b>رابط المجموعة:</b>\n{link}\n\n"
                 "✅ مرحباً بك في Full Mark! 📚",
                 reply_markup={"inline_keyboard": [
                     [{"text": "📊 حالة اشتراكي", "callback_data": "status"}]
                 ]})
    else:
        send_msg(cid,
                 "✅ <b>تم التفعيل!</b>\n\n"
                 f"👤 {student.get('name','غير محدد')}\n"
                 f"📅 ينتهي: {exp_str}\n\n"
                 f"⚠️ تعذّر إنشاء الرابط — تواصل: {ADMIN_USERNAME}",
                 reply_markup=KB_BACK)

# ════════════════════════════════════════════════════════════════
#  Endpoints لوحة التحكم
# ════════════════════════════════════════════════════════════════

@app.route("/api/ban-student", methods=["POST"])
def api_ban_student():
    """يُستدعى من لوحة التحكم لطرد طالب فوراً. Body: {code}"""
    if not DB_AVAILABLE:
        return jsonify(ok=False, error="db unavailable"), 503
    body = request.get_json(force=True, silent=True) or {}
    code = body.get("code", "").strip()
    if not code:
        return jsonify(ok=False, error="code required"), 400
    student = get_student(code)
    if not student:
        return jsonify(ok=False, error="not found"), 404
    tg_id = student.get("telegram_id")
    if tg_id:
        kick_user(tg_id)
        send_msg(tg_id,
                 f"🚫 <b>تم إيقاف اشتراكك!</b>\n\nتواصل: {ADMIN_USERNAME}")
        logger.info(f"api_ban: code={code} tg={tg_id}")
        return jsonify(ok=True, kicked=True, telegram_id=tg_id)
    return jsonify(ok=True, kicked=False, reason="no telegram_id")


@app.route("/api/ban-staff-codes", methods=["POST"])
def api_ban_staff():
    """طرد كل طلاب موظف محظور. Body: {staff_id}"""
    if not DB_AVAILABLE:
        return jsonify(ok=False, error="db unavailable"), 503
    body     = request.get_json(force=True, silent=True) or {}
    staff_id = str(body.get("staff_id", "")).strip()
    if not staff_id:
        return jsonify(ok=False, error="staff_id required"), 400
    kicked = []
    for code, d in get_all_students().items():
        if not isinstance(d, dict):
            continue
        if str(d.get("creator", "")) == staff_id and d.get("banned"):
            tg_id = d.get("telegram_id")
            if tg_id:
                kick_user(tg_id)
                try:
                    send_msg(tg_id,
                             f"🚫 <b>تم إيقاف اشتراكك!</b>\n\nتواصل: {ADMIN_USERNAME}")
                except Exception:
                    pass
                kicked.append(tg_id)
    logger.info(f"api_ban_staff: staff={staff_id} kicked={len(kicked)}")
    return jsonify(ok=True, kicked_count=len(kicked), kicked_ids=kicked)

# ════════════════════════════════════════════════════════════════
#  Utilities
# ════════════════════════════════════════════════════════════════

def fmt_date(ts: float) -> str:
    try:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        m = ["","يناير","فبراير","مارس","أبريل","مايو","يونيو",
             "يوليو","أغسطس","سبتمبر","أكتوبر","نوفمبر","ديسمبر"]
        return f"{dt.day} {m[dt.month]} {dt.year}"
    except Exception:
        return "غير محدد"


def gen_code() -> str:
    import random
    return str(random.randint(100_000_000, 999_999_999))


# ════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"Dev server → http://localhost:{port}/telegram")
    app.run(host="0.0.0.0", port=port, debug=True)
