"""
╔══════════════════════════════════════════════════════════════════╗
║       Full Mark ثانوية عامة — بوت الطلاب                        ║
║       Webhook على Vercel | متصل بـ Firebase لوحة التحكم          ║
║       Route: POST /telegram                                      ║
╚══════════════════════════════════════════════════════════════════╝

🔧 المشاكل التي تم إصلاحها:
  1. مسار الـ Webhook كان /api → تم تغييره إلى /telegram
  2. Import من database.py كان خاطئاً → تم إصلاح sys.path
  3. رسالة /start تظهر القائمة مباشرة → تم تغييرها لزر واحد أولاً
  4. لم يكن هناك fallback عند فشل DB → تم إضافة try-except كامل
  5. خيار "طريقة الاشتراك" مع @DevAhmedmo لم يكن موجوداً → تم إضافته
"""

import os, sys, time, asyncio, logging
from datetime import datetime, timezone
from flask import Flask, request, jsonify
import httpx

# ─────────────────────────────────────────────────────────
#  🔧 إصلاح مسار الاستيراد (ضروري لـ Vercel)
#  يضمن أن Python يجد database.py بجانب index.py داخل api/
# ─────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# نستورد بشكل آمن — لو فشل الاتصال بـ Firebase لن يموت البوت
try:
    from database import (
        get_student, update_student, get_all_students,
        save_student_telegram_id, get_trial_info,
        get_active_subscription, init_firebase
    )
    DB_AVAILABLE = True
except Exception as _db_import_err:
    DB_AVAILABLE = False
    logging.warning(f"Database import failed: {_db_import_err}")

# ─────────────────────────────────────────────────────────
#  🔧 إعدادات البوت
# ─────────────────────────────────────────────────────────

BOT_TOKEN     = os.environ.get("BOT_TOKEN", "8857188327:AAF0F4nLxgGLaRQkFjS1BK8kc3eiDCoNBCA")
GROUP_CHAT_ID = int(os.environ.get("GROUP_CHAT_ID", "-1003997728302"))
ADMIN_USERNAME = "@DevAhmedmo"
TRIAL_MINUTES = 30

# ─────────────────────────────────────────────────────────
#  Logging
# ─────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("FullMarkBot")

# ─────────────────────────────────────────────────────────
#  Telegram API Helpers
# ─────────────────────────────────────────────────────────

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"


async def tg(method: str, **kwargs) -> dict:
    """إرسال طلب لـ Telegram API مع معالجة الأخطاء."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(f"{TELEGRAM_API}/{method}", json=kwargs)
            data = r.json()
            if not data.get("ok"):
                logger.warning(f"TG {method} failed: {data.get('description')}")
            return data
    except Exception as e:
        logger.error(f"TG request error ({method}): {e}")
        return {"ok": False, "description": str(e)}


async def send_message(chat_id: int, text: str,
                       reply_markup=None, parse_mode: str = "HTML") -> dict:
    payload = dict(chat_id=chat_id, text=text, parse_mode=parse_mode)
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return await tg("sendMessage", **payload)


async def edit_message(chat_id: int, message_id: int, text: str,
                       reply_markup=None, parse_mode: str = "HTML") -> dict:
    payload = dict(
        chat_id=chat_id, message_id=message_id,
        text=text, parse_mode=parse_mode
    )
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return await tg("editMessageText", **payload)


async def answer_callback(callback_id: str, text: str = "",
                          alert: bool = False) -> dict:
    return await tg("answerCallbackQuery",
                    callback_query_id=callback_id,
                    text=text, show_alert=alert)


async def kick_user(chat_id: int, user_id: int) -> dict | None:
    """طرد المستخدم من المجموعة ثم فك الحظر فوراً (kick وليس ban دائم)."""
    try:
        now = int(time.time())
        result = await tg("banChatMember",
                          chat_id=chat_id,
                          user_id=user_id,
                          until_date=now + 35)
        await asyncio.sleep(1)
        await tg("unbanChatMember",
                 chat_id=chat_id,
                 user_id=user_id,
                 only_if_banned=True)
        logger.info(f"Kicked user {user_id} from {chat_id}")
        return result
    except Exception as e:
        logger.error(f"Kick error for {user_id}: {e}")
        return None


async def create_invite_link(chat_id: int, member_limit: int = 1) -> str | None:
    """إنشاء رابط دخول خاص بعضو واحد فقط."""
    result = await tg("createChatInviteLink",
                      chat_id=chat_id,
                      member_limit=member_limit,
                      creates_join_request=False)
    if result.get("ok"):
        return result["result"]["invite_link"]
    return None

# ─────────────────────────────────────────────────────────
#  لوحات المفاتيح
# ─────────────────────────────────────────────────────────

# زر البداية — يظهر مباشرة مع رسالة /start
START_KEYBOARD = {
    "inline_keyboard": [
        [{"text": "يلا ابدأ يا بطل 🚀", "callback_data": "main_menu"}]
    ]
}

# القائمة الرئيسية الثلاثية — تظهر بعد الضغط على زر البداية
MAIN_MENU = {
    "inline_keyboard": [
        [{"text": "🎁 تجربة مجانية (نص ساعة)",      "callback_data": "trial"}],
        [{"text": "💳 طريقة الاشتراك",               "callback_data": "how_to_subscribe"}],
        [{"text": "🔑 تفعيل الكود / دخول المواد",    "callback_data": "activate"}],
    ]
}

BACK_KEYBOARD = {
    "inline_keyboard": [
        [{"text": "↩️ رجوع للقائمة", "callback_data": "main_menu"}]
    ]
}


def cancel_keyboard():
    return {"inline_keyboard": [[{"text": "❌ إلغاء", "callback_data": "main_menu"}]]}

# ─────────────────────────────────────────────────────────
#  Sessions (ذاكرة مؤقتة لتتبع خطوات المستخدم)
# ─────────────────────────────────────────────────────────

_sessions: dict = {}


def get_session(user_id: int) -> dict:
    if user_id not in _sessions:
        _sessions[user_id] = {}
    return _sessions[user_id]


def clear_session(user_id: int):
    _sessions.pop(user_id, None)

# ─────────────────────────────────────────────────────────
#  النصوص الثابتة
# ─────────────────────────────────────────────────────────

WELCOME_TEXT = (
    "🎓 <b>أهلاً بك في بوت Full Mark!</b>\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━━\n"
    "🏆 <b>منصة المذاكرة الأولى للثانوية العامة</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "📚 محتوى شامل لكل مواد الثانوية\n"
    "🎯 امتحانات وتدريبات تفاعلية\n"
    "👨‍🏫 شرح مباشر من أفضل المدرسين\n"
    "📈 تابع تقدمك أولاً بأول\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━━\n"
    "👇 اضغط الزر أدناه للبدء:"
)

HOW_TO_SUBSCRIBE_TEXT = (
    "💳 <b>طريقة الاشتراك في Full Mark</b>\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "📋 <b>خطوات الاشتراك:</b>\n\n"
    "1️⃣ تواصل مع الأدمن مباشرةً:\n"
    f"    👤 <b>{ADMIN_USERNAME}</b>\n\n"
    "2️⃣ أخبره بالاشتراك الذي تريده\n\n"
    "3️⃣ قم بسداد رسوم الاشتراك\n\n"
    "4️⃣ ستحصل على <b>كود تفعيل</b> مكوّن من 9 أرقام\n\n"
    "5️⃣ ارجع للبوت واضغط على\n"
    "    <b>🔑 تفعيل الكود / دخول المواد</b>\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━━\n"
    "⚡ <b>التفعيل فوري بعد الدفع!</b>"
)

# ─────────────────────────────────────────────────────────
#  Flask App
# ─────────────────────────────────────────────────────────

app = Flask(__name__)


@app.route("/telegram", methods=["POST"])
def webhook():
    """
    ✅ نقطة الاستقبال الرئيسية — POST /telegram
    ملاحظة: تم إصلاح المسار من /api إلى /telegram كما هو مطلوب.
    """
    data = request.get_json(force=True, silent=True)
    if not data:
        return jsonify(ok=True)

    # ── استخرج user_id للـ Fallback ──
    fallback_chat_id = None
    try:
        if "message" in data:
            fallback_chat_id = data["message"]["chat"]["id"]
        elif "callback_query" in data:
            fallback_chat_id = data["callback_query"]["message"]["chat"]["id"]
    except Exception:
        pass

    try:
        asyncio.run(_dispatch(data))
    except Exception as e:
        logger.exception(f"Fatal dispatch error: {e}")
        # ── Fallback: إذا حدث أي خطأ، أرسل رسالة ترحيبية أساسية ──
        if fallback_chat_id:
            try:
                asyncio.run(send_message(
                    fallback_chat_id,
                    "👋 <b>أهلاً بك في Full Mark!</b>\n\n"
                    "🔄 البوت يعمل بشكل طبيعي.\n"
                    "اضغط /start للبدء.",
                    reply_markup=START_KEYBOARD
                ))
            except Exception as fallback_err:
                logger.error(f"Fallback send failed: {fallback_err}")

    return jsonify(ok=True)


@app.route("/", methods=["GET"])
@app.route("/telegram", methods=["GET"])
@app.route("/health", methods=["GET"])
def health():
    return "🤖 Full Mark Student Bot is running! | Webhook: POST /telegram", 200


# ─────────────────────────────────────────────────────────
#  🔍 Middleware: فحص الانتهاء/الحظر — يُستدعى عند كل تفاعل
#  يعتمد على الـ Timestamp المخزن في Firebase بدون Cron Jobs
# ─────────────────────────────────────────────────────────

async def check_and_kick_if_expired(user_id: int) -> bool:
    """
    يفحص Firebase: إذا انتهى وقت كود الطالب أو كان محظوراً،
    يطرده فوراً ويعيد True. يُستدعى في بداية كل تفاعل (بدون Cron).
    """
    if not DB_AVAILABLE:
        return False  # لو DB غير متاح، نكمل بشكل طبيعي

    try:
        from firebase_admin import db as fdb
        init_firebase()

        now_ms = int(time.time() * 1000)
        all_students = fdb.reference("students").get() or {}

        for code, data in all_students.items():
            if not isinstance(data, dict):
                continue
            if data.get("telegram_id") != user_id:
                continue

            is_expired = data.get("expiry", 0) <= now_ms
            is_banned  = data.get("banned", False)

            if not (is_expired or is_banned):
                continue  # الكود لا يزال نشطاً

            # ── طرد الطالب ──
            kick_result = await kick_user(GROUP_CHAT_ID, user_id)

            if is_banned:
                msg = (
                    "🚫 <b>تم إيقاف اشتراكك!</b>\n\n"
                    f"للاستفسار تواصل مع الإدارة: {ADMIN_USERNAME}"
                )
            elif data.get("type") == "trial":
                msg = (
                    "⌛ <b>انتهت فترة التجربة المجانية!</b>\n\n"
                    "نأمل أن المحتوى نال إعجابك 😊\n"
                    "اشترك معنا للاستمرار في التعلم 👇"
                )
            else:
                msg = (
                    "⌛ <b>انتهى اشتراكك!</b>\n\n"
                    "جدد اشتراكك للاستمرار في التعلم 📚"
                )

            try:
                await send_message(
                    user_id, msg,
                    reply_markup={"inline_keyboard": [
                        [{"text": "🔑 تفعيل / تجديد الاشتراك",
                          "callback_data": "activate"}],
                        [{"text": "💳 طريقة الاشتراك",
                          "callback_data": "how_to_subscribe"}],
                    ]}
                )
            except Exception:
                pass

            logger.info(
                f"[Middleware] Kicked user={user_id} code={code} "
                f"expired={is_expired} banned={is_banned}"
            )
            return True

    except Exception as e:
        logger.warning(f"check_and_kick_if_expired error: {e}")
        # لا تطرده لو حصل خطأ في قراءة DB

    return False


# ─────────────────────────────────────────────────────────
#  Dispatcher الرئيسي
# ─────────────────────────────────────────────────────────

async def _dispatch(update: dict):
    """يوزع كل update واردة على المعالج المناسب."""
    try:
        user_id = None
        if "message" in update:
            user_id = update["message"]["from"]["id"]
        elif "callback_query" in update:
            user_id = update["callback_query"]["from"]["id"]

        # ── Middleware: فحص انتهاء/حظر قبل أي معالجة ──
        if user_id and DB_AVAILABLE:
            was_kicked = await check_and_kick_if_expired(user_id)
            if was_kicked:
                if "callback_query" in update:
                    try:
                        await answer_callback(update["callback_query"]["id"])
                    except Exception:
                        pass
                return

        if "message" in update:
            await _handle_message(update["message"])
        elif "callback_query" in update:
            await _handle_callback(update["callback_query"])

    except Exception as e:
        logger.exception(f"Dispatch error: {e}")
        raise  # نرفعها للـ webhook ليتعامل معها بالـ Fallback


# ─────────────────────────────────────────────────────────
#  معالج الرسائل
# ─────────────────────────────────────────────────────────

async def _handle_message(msg: dict):
    user_id = msg["from"]["id"]
    chat_id = msg["chat"]["id"]
    text    = msg.get("text", "").strip()
    first   = msg["from"].get("first_name", "صديقي")
    session = get_session(user_id)

    # ── /start → رسالة ترحيبية + زر واحد ──
    if text.startswith("/start"):
        clear_session(user_id)
        welcome = WELCOME_TEXT.replace("بك", f"بك يا <b>{first}</b>", 1)
        await send_message(chat_id, welcome, reply_markup=START_KEYBOARD)
        return

    # ── /cancel أو إلغاء ──
    if text in ("إلغاء", "/cancel"):
        clear_session(user_id)
        await send_message(
            chat_id,
            "↩️ تم الإلغاء. اضغط /start للعودة للقائمة.",
        )
        return

    # ── انتظار كود التفعيل ──
    if session.get("step") == "WAIT_CODE":
        await _process_activation_code(chat_id, user_id, text)
        return

    # ── أي رسالة عشوائية ──
    await send_message(
        chat_id,
        f"👋 <b>أهلاً {first}!</b>\nاضغط /start للوصول للقائمة 👇",
        reply_markup=START_KEYBOARD
    )


# ─────────────────────────────────────────────────────────
#  معالج الأزرار (Callbacks)
# ─────────────────────────────────────────────────────────

async def _handle_callback(cb: dict):
    user_id = cb["from"]["id"]
    chat_id = cb["message"]["chat"]["id"]
    msg_id  = cb["message"]["message_id"]
    data    = cb.get("data", "")
    first   = cb["from"].get("first_name", "صديقي")
    cb_id   = cb["id"]

    await answer_callback(cb_id)

    # ── زر البداية → القائمة الثلاثية ──
    if data == "main_menu":
        clear_session(user_id)
        await edit_message(
            chat_id, msg_id,
            f"👋 <b>أهلاً {first}!</b>\n\nاختار من القائمة 👇",
            reply_markup=MAIN_MENU
        )
        return

    # ── تجربة مجانية ──
    if data == "trial":
        await _handle_trial(chat_id, msg_id, user_id, first)
        return

    # ── تأكيد بدء التجربة ──
    if data == "confirm_trial":
        await _start_trial(chat_id, msg_id, user_id, first)
        return

    # ── طريقة الاشتراك ──
    if data == "how_to_subscribe":
        await edit_message(
            chat_id, msg_id,
            HOW_TO_SUBSCRIBE_TEXT,
            reply_markup={"inline_keyboard": [
                [{"text": f"💬 تواصل مع {ADMIN_USERNAME}", "url": f"https://t.me/{ADMIN_USERNAME.lstrip('@')}"}],
                [{"text": "🔑 عندي كود — فعّله الآن", "callback_data": "activate"}],
                [{"text": "↩️ رجوع", "callback_data": "main_menu"}],
            ]}
        )
        return

    # ── تفعيل كود الاشتراك ──
    if data == "activate":
        get_session(user_id)["step"] = "WAIT_CODE"
        await edit_message(
            chat_id, msg_id,
            "🔑 <b>تفعيل كود الاشتراك</b>\n\n"
            "أرسل الكود المكوّن من 9 أرقام:\n"
            "مثال: <code>659752466</code>",
            reply_markup=cancel_keyboard()
        )
        return

    # ── رجوع (للتوافق مع النسخ القديمة) ──
    if data in ("back_main", "cancel"):
        clear_session(user_id)
        await edit_message(
            chat_id, msg_id,
            f"👋 <b>أهلاً {first}!</b>\n\nاختار من القائمة 👇",
            reply_markup=MAIN_MENU
        )
        return


# ─────────────────────────────────────────────────────────
#  منطق التجربة المجانية
# ─────────────────────────────────────────────────────────

async def _handle_trial(chat_id: int, msg_id: int, user_id: int, first: str):
    """التحقق من أهلية التجربة وعرض الخيارات المناسبة."""

    # ── إذا DB غير متاح، إجراء مبدئي ──
    if not DB_AVAILABLE:
        await edit_message(
            chat_id, msg_id,
            "⚠️ <b>خطأ مؤقت في النظام!</b>\n\n"
            "حاول مرة أخرى بعد قليل أو تواصل مع الإدارة.",
            reply_markup=BACK_KEYBOARD
        )
        return

    # ── فحص اشتراك نشط (لا يحتاج تجربة) ──
    try:
        active_sub = get_active_subscription(user_id)
        if active_sub:
            exp_str = _format_date(active_sub.get("expiry", 0) / 1000)
            await edit_message(
                chat_id, msg_id,
                "✅ <b>أنت مشترك بالفعل!</b>\n\n"
                f"👤 الاسم: {active_sub.get('name', 'غير محدد')}\n"
                f"📅 ينتهي: {exp_str}\n\n"
                "لا تحتاج للتجربة المجانية 😊",
                reply_markup=BACK_KEYBOARD
            )
            return
    except Exception as e:
        logger.warning(f"get_active_subscription error: {e}")

    # ── فحص تجربة سابقة ──
    try:
        trial_info = get_trial_info(user_id)
    except Exception as e:
        logger.warning(f"get_trial_info error: {e}")
        trial_info = None

    if trial_info:
        exp_ts = trial_info.get("expiry", 0) / 1000
        now_ts = time.time()

        if exp_ts > now_ts:
            # تجربة لا تزال نشطة
            remaining = max(1, int((exp_ts - now_ts) / 60))
            await edit_message(
                chat_id, msg_id,
                "⏳ <b>لديك تجربة نشطة بالفعل!</b>\n\n"
                f"⏱ متبقي: <b>{remaining} دقيقة</b>\n\n"
                "ادخل المجموعة من الرابط الذي أُرسل لك سابقاً.",
                reply_markup=BACK_KEYBOARD
            )
        else:
            # التجربة انتهت
            await edit_message(
                chat_id, msg_id,
                "🚫 <b>انتهت فرصتك في التجربة المجانية!</b>\n\n"
                "استخدمت التجربة المجانية مسبقاً.\n"
                f"للاشتراك تواصل مع الإدارة: {ADMIN_USERNAME}",
                reply_markup={"inline_keyboard": [
                    [{"text": f"💬 تواصل مع {ADMIN_USERNAME}",
                      "url": f"https://t.me/{ADMIN_USERNAME.lstrip('@')}"}],
                    [{"text": "🔑 تفعيل كود اشتراك", "callback_data": "activate"}],
                    [{"text": "↩️ رجوع",              "callback_data": "main_menu"}],
                ]}
            )
        return

    # ── المرة الأولى — عرض تفاصيل التجربة ──
    await edit_message(
        chat_id, msg_id,
        f"🎁 <b>تجربة مجانية لمدة {TRIAL_MINUTES} دقيقة!</b>\n\n"
        "✨ ستحصل على:\n"
        "• رابط دخول مباشر للمجموعة\n"
        f"• وصول كامل لمدة {TRIAL_MINUTES} دقيقة\n"
        "• بعدها ستُخرج تلقائياً عند أول تفاعل\n\n"
        "⚠️ <b>ملاحظة:</b> التجربة تُستخدم مرة واحدة فقط!\n\n"
        "هل تريد بدء التجربة الآن؟",
        reply_markup={"inline_keyboard": [
            [{"text": "✅ نعم، ابدأ التجربة", "callback_data": "confirm_trial"}],
            [{"text": "❌ لا، رجوع",          "callback_data": "main_menu"}],
        ]}
    )


async def _start_trial(chat_id: int, msg_id: int, user_id: int, first: str):
    """إنشاء التجربة المجانية وتخزينها في Firebase."""
    await edit_message(
        chat_id, msg_id,
        "⏳ <b>جاري تجهيز رابط التجربة...</b>",
    )

    # ── إنشاء رابط الدخول ──
    invite_link = await create_invite_link(GROUP_CHAT_ID, member_limit=1)
    if not invite_link:
        await edit_message(
            chat_id, msg_id,
            "❌ <b>عذراً، حدث خطأ!</b>\n\n"
            "لم نتمكن من إنشاء رابط الدخول.\n"
            f"تواصل مع الإدارة: {ADMIN_USERNAME}",
            reply_markup=BACK_KEYBOARD
        )
        return

    now_ms    = int(time.time() * 1000)
    exp_ms    = now_ms + (TRIAL_MINUTES * 60 * 1000)
    exp_str   = _format_date(exp_ms / 1000)
    trial_code = _gen_code()

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
        "telegram_id": user_id,
        "invite_link": invite_link,
    }

    # ── تخزين في Firebase ──
    try:
        from firebase_admin import db as fdb
        init_firebase()
        fdb.reference(f"students/{trial_code}").set(trial_data)
        logger.info(f"Trial started: user={user_id} code={trial_code} exp={exp_str}")
    except Exception as e:
        logger.error(f"Failed to save trial to Firebase: {e}")
        # أكمل وأرسل الرابط حتى لو فشل الحفظ
        # (سيضطر إلى التواصل مع الإدارة لاحقاً)

    await edit_message(
        chat_id, msg_id,
        f"🎉 <b>تجربتك جاهزة يا {first}!</b>\n\n"
        f"⏱ <b>المدة:</b> {TRIAL_MINUTES} دقيقة\n"
        f"📅 <b>تنتهي:</b> {exp_str}\n\n"
        f"🔗 <b>رابط الدخول:</b>\n{invite_link}\n\n"
        "⚠️ <b>مهم جداً:</b>\n"
        "• الرابط للاستخدام مرة واحدة فقط\n"
        "• عند انتهاء الوقت ستُخرج تلقائياً\n"
        "• التجربة لا تتكرر — استخدمها بحكمة 😊",
        reply_markup={"inline_keyboard": [
            [{"text": "🔑 اشترك الآن بعد التجربة", "callback_data": "activate"}],
            [{"text": "💳 طريقة الاشتراك",         "callback_data": "how_to_subscribe"}],
        ]}
    )


# ─────────────────────────────────────────────────────────
#  منطق تفعيل الكود
# ─────────────────────────────────────────────────────────

async def _process_activation_code(chat_id: int, user_id: int, code: str):
    """التحقق من كود التفعيل وتفعيل الاشتراك إذا كان صحيحاً."""
    clear_session(user_id)

    # ── تحقق من تنسيق الكود ──
    if not code.isdigit() or len(code) != 9:
        await send_message(
            chat_id,
            "❌ <b>صيغة الكود غير صحيحة!</b>\n\n"
            "الكود مكوّن من 9 أرقام بالضبط.\n"
            "مثال: <code>659752466</code>",
            reply_markup={"inline_keyboard": [
                [{"text": "🔑 حاول مرة أخرى", "callback_data": "activate"}],
                [{"text": "↩️ رجوع",           "callback_data": "main_menu"}],
            ]}
        )
        return

    # ── تحقق من وجود DB ──
    if not DB_AVAILABLE:
        await send_message(
            chat_id,
            "⚠️ <b>خطأ مؤقت في النظام!</b>\n\n"
            f"حاول مرة أخرى بعد قليل أو تواصل مع الإدارة: {ADMIN_USERNAME}",
            reply_markup=BACK_KEYBOARD
        )
        return

    # ── جلب بيانات الطالب ──
    try:
        student = get_student(code)
    except Exception as e:
        logger.error(f"get_student error: {e}")
        await send_message(
            chat_id,
            "⚠️ <b>خطأ مؤقت في الاتصال بقاعدة البيانات!</b>\n\n"
            "حاول مرة أخرى بعد قليل.",
            reply_markup={"inline_keyboard": [
                [{"text": "🔄 إعادة المحاولة", "callback_data": "activate"}],
                [{"text": "↩️ رجوع",           "callback_data": "main_menu"}],
            ]}
        )
        return

    if student is None:
        await send_message(
            chat_id,
            "❌ <b>الكود غير موجود!</b>\n\n"
            "تأكد من الكود وحاول مرة أخرى.\n"
            f"إذا استمرت المشكلة تواصل مع: {ADMIN_USERNAME}",
            reply_markup={"inline_keyboard": [
                [{"text": "🔑 حاول مرة أخرى", "callback_data": "activate"}],
                [{"text": "↩️ رجوع",           "callback_data": "main_menu"}],
            ]}
        )
        return

    now_ms = int(time.time() * 1000)

    if student.get("banned", False):
        await send_message(
            chat_id,
            "🚫 <b>هذا الكود محظور!</b>\n\n"
            f"تواصل مع الإدارة: {ADMIN_USERNAME}",
            reply_markup=BACK_KEYBOARD
        )
        return

    if student.get("expiry", 0) <= now_ms:
        await send_message(
            chat_id,
            "⌛ <b>هذا الكود منتهي الصلاحية!</b>\n\n"
            "انتهى تاريخ هذا الكود.\n"
            f"تواصل مع {ADMIN_USERNAME} لتجديد الاشتراك.",
            reply_markup={"inline_keyboard": [
                [{"text": f"💬 تواصل مع {ADMIN_USERNAME}",
                  "url": f"https://t.me/{ADMIN_USERNAME.lstrip('@')}"}],
                [{"text": "↩️ رجوع", "callback_data": "main_menu"}],
            ]}
        )
        return

    existing_tg_id = student.get("telegram_id")
    if existing_tg_id and existing_tg_id != user_id:
        await send_message(
            chat_id,
            "📱 <b>هذا الكود مستخدم بالفعل!</b>\n\n"
            "تم تفعيل هذا الكود من حساب آخر.\n"
            "كل كود مخصص لطالب واحد فقط.",
            reply_markup=BACK_KEYBOARD
        )
        return

    # ── الكود صحيح — ربطه بالحساب ──
    exp_str = _format_date(student["expiry"] / 1000)
    try:
        update_student(code, {
            "telegram_id":        user_id,
            "telegram_linked_at": now_ms,
            "activated_at":       now_ms,
        })
    except Exception as e:
        logger.error(f"update_student error: {e}")

    # ── إنشاء رابط الدخول ──
    invite_link = await create_invite_link(GROUP_CHAT_ID, member_limit=1)
    logger.info(f"Code activated: user={user_id} code={code}")

    if invite_link:
        await send_message(
            chat_id,
            "🎉 <b>تم تفعيل اشتراكك بنجاح!</b>\n\n"
            f"👤 <b>الاسم:</b> {student.get('name', 'غير محدد')}\n"
            f"📅 <b>ينتهي:</b> {exp_str}\n\n"
            f"🔗 <b>رابط المجموعة:</b>\n{invite_link}\n\n"
            "✅ مرحباً بك في Full Mark!\n"
            "بالتوفيق في دراستك 📚",
            reply_markup={"inline_keyboard": [
                [{"text": "📊 حالة اشتراكي", "callback_data": "status"}]
            ]}
        )
    else:
        await send_message(
            chat_id,
            "✅ <b>تم تفعيل اشتراكك!</b>\n\n"
            f"👤 <b>الاسم:</b> {student.get('name', 'غير محدد')}\n"
            f"📅 <b>ينتهي:</b> {exp_str}\n\n"
            "⚠️ لم نتمكن من إنشاء رابط الدخول الآن.\n"
            f"تواصل مع الإدارة: {ADMIN_USERNAME}",
            reply_markup=BACK_KEYBOARD
        )


# ─────────────────────────────────────────────────────────
#  Endpoints الحظر الفوري (تُستدعى من لوحة التحكم)
# ─────────────────────────────────────────────────────────

@app.route("/api/ban-student", methods=["POST"])
def ban_student():
    """
    ✅ يُستدعى من لوحة التحكم لطرد طالب فوراً.
    Body: { "code": "123456789" }
    """
    if not DB_AVAILABLE:
        return jsonify(ok=False, error="database not available"), 503

    body = request.get_json(force=True, silent=True) or {}
    code = body.get("code", "").strip()
    if not code:
        return jsonify(ok=False, error="code required"), 400

    student = get_student(code)
    if not student:
        return jsonify(ok=False, error="student not found"), 404

    tg_id = student.get("telegram_id")
    if tg_id:
        asyncio.run(kick_user(GROUP_CHAT_ID, tg_id))
        asyncio.run(send_message(
            tg_id,
            "🚫 <b>تم إيقاف اشتراكك!</b>\n\n"
            f"للاستفسار تواصل مع الإدارة: {ADMIN_USERNAME}"
        ))
        logger.info(f"Banned & kicked: code={code} tg={tg_id}")
        return jsonify(ok=True, kicked=True, telegram_id=tg_id)

    return jsonify(ok=True, kicked=False, reason="no telegram_id")


@app.route("/api/ban-staff-codes", methods=["POST"])
def ban_staff_codes():
    """
    ✅ طرد كل الطلاب المرتبطين بأكواد موظف معين.
    Body: { "staff_id": "123456" }
    """
    if not DB_AVAILABLE:
        return jsonify(ok=False, error="database not available"), 503

    body     = request.get_json(force=True, silent=True) or {}
    staff_id = str(body.get("staff_id", "")).strip()
    if not staff_id:
        return jsonify(ok=False, error="staff_id required"), 400

    all_students = get_all_students()
    kicked_list  = []

    async def kick_all():
        for code, data in all_students.items():
            if not isinstance(data, dict):
                continue
            if str(data.get("creator", "")) == staff_id and data.get("banned"):
                tg_id = data.get("telegram_id")
                if tg_id:
                    await kick_user(GROUP_CHAT_ID, tg_id)
                    try:
                        await send_message(
                            tg_id,
                            "🚫 <b>تم إيقاف اشتراكك!</b>\n\n"
                            f"للاستفسار تواصل مع الإدارة: {ADMIN_USERNAME}"
                        )
                    except Exception:
                        pass
                    kicked_list.append(tg_id)

    asyncio.run(kick_all())
    logger.info(f"Staff ban: staff={staff_id} kicked={len(kicked_list)}")
    return jsonify(ok=True, kicked_count=len(kicked_list), kicked_ids=kicked_list)


# ─────────────────────────────────────────────────────────
#  Utilities
# ─────────────────────────────────────────────────────────

def _format_date(ts: float) -> str:
    try:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        months_ar = [
            "", "يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
            "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"
        ]
        return f"{dt.day} {months_ar[dt.month]} {dt.year}"
    except Exception:
        return "غير محدد"


def _gen_code() -> str:
    import random
    return str(random.randint(100_000_000, 999_999_999))


# ─────────────────────────────────────────────────────────
#  تشغيل محلي
# ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"Starting dev server on port {port}")
    logger.info(f"Webhook URL: http://localhost:{port}/telegram")
    app.run(host="0.0.0.0", port=port, debug=True)
