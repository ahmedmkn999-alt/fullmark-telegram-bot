"""
╔══════════════════════════════════════════════════════════════════╗
║       Full Mark ثانوية عامة — بوت الطلاب                        ║
║       Webhook على Vercel | متصل بـ Firebase لوحة التحكم          ║
╚══════════════════════════════════════════════════════════════════╝
"""

import os, time, asyncio, logging
from datetime import datetime, timezone
from flask import Flask, request, jsonify
import httpx

from api.database import (
    get_student, update_student, get_all_students,
    save_student_telegram_id, get_trial_info, get_active_subscription
)

# ─────────────────────────────────────────────────────────
#  🔧 إعدادات البوت
# ─────────────────────────────────────────────────────────

BOT_TOKEN     = "8857188327:AAF0F4nLxgGLaRQkFjS1BK8kc3eiDCoNBCA"
GROUP_CHAT_ID = -1003997728302
TRIAL_MINUTES = 30

# ─────────────────────────────────────────────────────────
#  Logging
# ─────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("FullMarkBot")

# ─────────────────────────────────────────────────────────
#  Telegram API Helpers
# ─────────────────────────────────────────────────────────

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"


async def tg(method: str, **kwargs) -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(f"{TELEGRAM_API}/{method}", json=kwargs)
        data = r.json()
        if not data.get("ok"):
            logger.warning(f"TG {method} failed: {data.get('description')}")
        return data


async def send_message(chat_id: int, text: str, reply_markup=None, parse_mode="HTML"):
    payload = dict(chat_id=chat_id, text=text, parse_mode=parse_mode)
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return await tg("sendMessage", **payload)


async def answer_callback(callback_id: str, text: str = "", alert: bool = False):
    return await tg("answerCallbackQuery",
                    callback_query_id=callback_id,
                    text=text, show_alert=alert)


async def kick_user(chat_id: int, user_id: int):
    """طرد المستخدم من المجموعة ثم فك الحظر فوراً."""
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


async def create_invite_link(chat_id: int, member_limit: int = 1):
    result = await tg("createChatInviteLink",
                      chat_id=chat_id,
                      member_limit=member_limit,
                      creates_join_request=False)
    if result.get("ok"):
        return result["result"]["invite_link"]
    return None

# ─────────────────────────────────────────────────────────
#  لوحة المفاتيح
# ─────────────────────────────────────────────────────────

MAIN_MENU = {
    "inline_keyboard": [
        [{"text": "🎁 تجربة مجانية 30 دقيقة", "callback_data": "trial"}],
        [{"text": "🔑 تفعيل كود الاشتراك",    "callback_data": "activate"}],
        [{"text": "📊 حالة اشتراكي",           "callback_data": "status"}],
        [{"text": "❓ مساعدة وتواصل",          "callback_data": "help"}]
    ]
}


def cancel_keyboard():
    return {"inline_keyboard": [[{"text": "❌ إلغاء", "callback_data": "cancel"}]]}

# ─────────────────────────────────────────────────────────
#  Sessions
# ─────────────────────────────────────────────────────────

_sessions: dict = {}


def get_session(user_id: int) -> dict:
    if user_id not in _sessions:
        _sessions[user_id] = {}
    return _sessions[user_id]


def clear_session(user_id: int):
    _sessions.pop(user_id, None)

# ─────────────────────────────────────────────────────────
#  رسالة الترحيب
# ─────────────────────────────────────────────────────────

WELCOME_TEXT = (
    "🎓 <b>أهلاً بك في بوت Full Mark ثانوية عامة!</b>\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━━\n"
    "🏆 <b>منصة المذاكرة الأولى للثانوية العامة</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "📚 محتوى شامل لكل المواد\n"
    "🎯 امتحانات وتدريبات تفاعلية\n"
    "👨‍🏫 أفضل المدرسين والشروحات\n"
    "📈 تابع تقدمك أولاً بأول\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━━\n"
    "👇 <b>اختر من القائمة أدناه:</b>"
)

# ─────────────────────────────────────────────────────────
#  Flask App
# ─────────────────────────────────────────────────────────

app = Flask(__name__)


@app.route("/api", methods=["POST"])
def webhook():
    data = request.get_json(force=True, silent=True)
    if not data:
        return jsonify(ok=True)
    asyncio.run(_dispatch(data))
    return jsonify(ok=True)


@app.route("/", methods=["GET"])
@app.route("/api", methods=["GET"])
def health():
    return "🤖 Full Mark Student Bot is running!", 200


# ─────────────────────────────────────────────────────────
#  🔍 Middleware: فحص الانتهاء/الحظر عند كل تفاعل
# ─────────────────────────────────────────────────────────

async def check_and_kick_if_expired(user_id: int) -> bool:
    """
    يُستدعى تلقائياً في بداية كل message أو callback_query.
    يفحص Firebase: إذا كان للطالب كود منتهي أو محظور
    يطرده فوراً من المجموعة ويعيد True، وإلا يعيد False.
    """
    from api.database import init_firebase
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

        # ── الكود منتهي أو محظور → اطرد الطالب فوراً ──
        kick_result = await kick_user(GROUP_CHAT_ID, user_id)

        if kick_result and kick_result.get("ok"):
            logger.info(
                f"[Middleware] Kicked user={user_id} code={code} "
                f"expired={is_expired} banned={is_banned}"
            )

            if is_banned:
                msg = "🚫 <b>تم إيقاف اشتراكك!</b>\n\nتواصل مع الإدارة لمعرفة السبب."
            elif data.get("type") == "trial":
                msg = (
                    "⌛ <b>انتهت فترة التجربة المجانية!</b>\n\n"
                    "نأمل أن المحتوى نال إعجابك 😊\n"
                    "للاستمرار في التعلم اشترك معنا 👇"
                )
            else:
                msg = "⌛ <b>انتهى اشتراكك!</b>\n\nجدد اشتراكك للاستمرار في التعلم 📚"

            try:
                await send_message(
                    user_id, msg,
                    reply_markup={"inline_keyboard": [
                        [{"text": "🔑 تفعيل / تجديد الاشتراك",
                          "callback_data": "activate"}]
                    ]}
                )
            except Exception:
                pass

        return True  # تم الطرد — أوقف معالجة الـ update

    return False  # كل شيء طبيعي


# ──────────────────────────────
#  Dispatcher
# ──────────────────────────────

async def _dispatch(update: dict):
    try:
        # ── استخرج user_id ──
        user_id = None
        if "message" in update:
            user_id = update["message"]["from"]["id"]
        elif "callback_query" in update:
            user_id = update["callback_query"]["from"]["id"]

        # ── Middleware: فحص الانتهاء/الحظر قبل أي معالجة ──
        if user_id:
            was_kicked = await check_and_kick_if_expired(user_id)
            if was_kicked:
                if "callback_query" in update:
                    try:
                        await answer_callback(update["callback_query"]["id"])
                    except Exception:
                        pass
                return  # لا تكمل معالجة الـ update

        if "message" in update:
            await _handle_message(update["message"])
        elif "callback_query" in update:
            await _handle_callback(update["callback_query"])

    except Exception as e:
        logger.exception(f"Dispatch error: {e}")


# ──────────────────────────────
#  معالج الرسائل
# ──────────────────────────────

async def _handle_message(msg: dict):
    user_id = msg["from"]["id"]
    chat_id = msg["chat"]["id"]
    text    = msg.get("text", "").strip()
    first   = msg["from"].get("first_name", "صديقي")
    session = get_session(user_id)

    if text.startswith("/start"):
        clear_session(user_id)
        await send_message(chat_id,
                           WELCOME_TEXT.replace("صديقي", first),
                           reply_markup=MAIN_MENU)
        return

    if text in ("إلغاء", "/cancel"):
        clear_session(user_id)
        await send_message(chat_id, "↩️ تم الإلغاء.", reply_markup=MAIN_MENU)
        return

    if session.get("step") == "WAIT_CODE":
        await _process_activation_code(chat_id, user_id, text)
        return

    await send_message(chat_id,
                       f"👋 <b>أهلاً {first}!</b>\nاختر من القائمة 👇",
                       reply_markup=MAIN_MENU)


# ──────────────────────────────
#  معالج الأزرار
# ──────────────────────────────

async def _handle_callback(cb: dict):
    user_id = cb["from"]["id"]
    chat_id = cb["message"]["chat"]["id"]
    msg_id  = cb["message"]["message_id"]
    data    = cb.get("data", "")
    first   = cb["from"].get("first_name", "صديقي")
    cb_id   = cb["id"]

    await answer_callback(cb_id)

    if data == "cancel":
        clear_session(user_id)
        await tg("editMessageText",
                 chat_id=chat_id, message_id=msg_id,
                 text="↩️ تم الإلغاء.",
                 parse_mode="HTML",
                 reply_markup=MAIN_MENU)
        return

    if data == "trial":
        await _handle_trial(chat_id, msg_id, user_id, first)
        return

    if data == "activate":
        get_session(user_id)["step"] = "WAIT_CODE"
        await tg("editMessageText",
                 chat_id=chat_id, message_id=msg_id,
                 text=(
                     "🔑 <b>تفعيل كود الاشتراك</b>\n\n"
                     "أرسل الكود المكوّن من 9 أرقام:\n"
                     "مثال: <code>659752466</code>"
                 ),
                 parse_mode="HTML",
                 reply_markup=cancel_keyboard())
        return

    if data == "status":
        await _handle_status(chat_id, msg_id, user_id)
        return

    if data == "help":
        await tg("editMessageText",
                 chat_id=chat_id, message_id=msg_id,
                 text=(
                     "❓ <b>مساعدة وتواصل</b>\n\n"
                     "📞 للتواصل مع الإدارة:\n"
                     "• راسل أحد موظفينا مباشرةً\n"
                     "• أو تواصل معنا عبر المجموعة\n\n"
                     "⚠️ إذا واجهت مشكلة في التفعيل:\n"
                     "تأكد أن الكود صحيح وغير منتهي الصلاحية"
                 ),
                 parse_mode="HTML",
                 reply_markup={"inline_keyboard": [
                     [{"text": "↩️ رجوع", "callback_data": "back_main"}]
                 ]})
        return

    if data == "back_main":
        clear_session(user_id)
        await tg("editMessageText",
                 chat_id=chat_id, message_id=msg_id,
                 text=WELCOME_TEXT,
                 parse_mode="HTML",
                 reply_markup=MAIN_MENU)
        return

    if data == "confirm_trial":
        await _start_trial(chat_id, msg_id, user_id, first)
        return


# ─────────────────────────────────────────────────────────
#  منطق التجربة المجانية
# ─────────────────────────────────────────────────────────

async def _handle_trial(chat_id: int, msg_id: int, user_id: int, first: str):
    active_sub = get_active_subscription(user_id)
    if active_sub:
        exp_str = _format_date(active_sub.get("expiry", 0) / 1000)
        await tg("editMessageText",
                 chat_id=chat_id, message_id=msg_id,
                 text=(
                     "✅ <b>أنت مشترك بالفعل!</b>\n\n"
                     f"👤 الاسم: {active_sub.get('name', 'غير محدد')}\n"
                     f"📅 ينتهي: {exp_str}\n\n"
                     "لا تحتاج للتجربة المجانية 😊"
                 ),
                 parse_mode="HTML",
                 reply_markup={"inline_keyboard": [
                     [{"text": "↩️ رجوع", "callback_data": "back_main"}]
                 ]})
        return

    trial_info = get_trial_info(user_id)
    if trial_info:
        exp_ts = trial_info.get("expiry", 0) / 1000
        now_ts = time.time()
        if exp_ts > now_ts:
            remaining = int((exp_ts - now_ts) / 60)
            await tg("editMessageText",
                     chat_id=chat_id, message_id=msg_id,
                     text=(
                         "⏳ <b>لديك تجربة نشطة بالفعل!</b>\n\n"
                         f"⏱ متبقي: <b>{remaining} دقيقة</b>\n\n"
                         "ادخل المجموعة من الرابط الذي أُرسل لك."
                     ),
                     parse_mode="HTML",
                     reply_markup={"inline_keyboard": [
                         [{"text": "↩️ رجوع", "callback_data": "back_main"}]
                     ]})
        else:
            await tg("editMessageText",
                     chat_id=chat_id, message_id=msg_id,
                     text=(
                         "🚫 <b>انتهت فرصتك في التجربة المجانية!</b>\n\n"
                         "لقد استخدمت التجربة المجانية مسبقاً.\n"
                         "للاشتراك في المنصة كلّم أحد موظفينا 👇"
                     ),
                     parse_mode="HTML",
                     reply_markup={"inline_keyboard": [
                         [{"text": "🔑 تفعيل كود اشتراك", "callback_data": "activate"}],
                         [{"text": "↩️ رجوع",             "callback_data": "back_main"}]
                     ]})
        return

    await tg("editMessageText",
             chat_id=chat_id, message_id=msg_id,
             text=(
                 f"🎁 <b>تجربة مجانية لمدة {TRIAL_MINUTES} دقيقة!</b>\n\n"
                 "✨ ستحصل على:\n"
                 "• رابط دخول مباشر للمجموعة\n"
                 f"• وصول كامل لمدة {TRIAL_MINUTES} دقيقة\n"
                 "• بعدها سيتم إخراجك تلقائياً عند أول تفاعل\n\n"
                 "⚠️ <b>ملاحظة:</b> التجربة تُستخدم مرة واحدة فقط!\n\n"
                 "هل تريد بدء التجربة الآن؟"
             ),
             parse_mode="HTML",
             reply_markup={"inline_keyboard": [
                 [{"text": "✅ نعم، ابدأ التجربة", "callback_data": "confirm_trial"}],
                 [{"text": "❌ لا، رجوع",          "callback_data": "back_main"}]
             ]})


async def _start_trial(chat_id: int, msg_id: int, user_id: int, first: str):
    await tg("editMessageText",
             chat_id=chat_id, message_id=msg_id,
             text="⏳ <b>جاري تجهيز رابط التجربة...</b>",
             parse_mode="HTML")

    invite_link = await create_invite_link(GROUP_CHAT_ID, member_limit=1)
    if not invite_link:
        await tg("editMessageText",
                 chat_id=chat_id, message_id=msg_id,
                 text=(
                     "❌ <b>عذراً، حدث خطأ!</b>\n\n"
                     "لم نتمكن من إنشاء رابط الدخول.\n"
                     "حاول مرة أخرى أو تواصل مع الإدارة."
                 ),
                 parse_mode="HTML",
                 reply_markup={"inline_keyboard": [
                     [{"text": "↩️ رجوع", "callback_data": "back_main"}]
                 ]})
        return

    now_ms  = int(time.time() * 1000)
    exp_ms  = now_ms + (TRIAL_MINUTES * 60 * 1000)
    exp_str = _format_date(exp_ms / 1000)

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

    from api.database import init_firebase
    from firebase_admin import db as fdb
    init_firebase()
    fdb.reference(f"students/{trial_code}").set(trial_data)
    logger.info(f"Trial started: user={user_id} code={trial_code} exp={exp_str}")

    await tg("editMessageText",
             chat_id=chat_id, message_id=msg_id,
             text=(
                 f"🎉 <b>تجربتك جاهزة يا {first}!</b>\n\n"
                 f"⏱ <b>المدة:</b> {TRIAL_MINUTES} دقيقة\n"
                 f"📅 <b>تنتهي:</b> {exp_str}\n\n"
                 f"🔗 <b>رابط الدخول:</b>\n{invite_link}\n\n"
                 "⚠️ <b>مهم جداً:</b>\n"
                 "• الرابط للاستخدام مرة واحدة فقط\n"
                 "• عند انتهاء الوقت ستُخرج تلقائياً\n"
                 "• التجربة لا تتكرر — استخدمها بحكمة 😊"
             ),
             parse_mode="HTML",
             reply_markup={"inline_keyboard": [
                 [{"text": "🔑 اشترك الآن بعد التجربة", "callback_data": "activate"}]
             ]})


# ─────────────────────────────────────────────────────────
#  منطق تفعيل الكود
# ─────────────────────────────────────────────────────────

async def _process_activation_code(chat_id: int, user_id: int, code: str):
    clear_session(user_id)

    if not code.isdigit() or len(code) != 9:
        await send_message(chat_id,
                           "❌ <b>صيغة الكود غير صحيحة!</b>\n\n"
                           "الكود مكوّن من 9 أرقام بالضبط.\n"
                           "مثال: <code>659752466</code>",
                           reply_markup={"inline_keyboard": [
                               [{"text": "🔑 حاول مرة أخرى", "callback_data": "activate"}],
                               [{"text": "↩️ رجوع",           "callback_data": "back_main"}]
                           ]})
        return

    student = get_student(code)

    if student is None:
        await send_message(chat_id,
                           "❌ <b>الكود غير موجود!</b>\n\n"
                           "تأكد من الكود وحاول مرة أخرى.\n"
                           "إذا المشكلة استمرت تواصل مع الموظف الذي أعطاك الكود.",
                           reply_markup={"inline_keyboard": [
                               [{"text": "🔑 حاول مرة أخرى", "callback_data": "activate"}],
                               [{"text": "↩️ رجوع",           "callback_data": "back_main"}]
                           ]})
        return

    now_ms = int(time.time() * 1000)

    if student.get("banned", False):
        await send_message(chat_id,
                           "🚫 <b>هذا الكود محظور!</b>\n\n"
                           "تواصل مع الإدارة لمعرفة السبب.",
                           reply_markup={"inline_keyboard": [
                               [{"text": "↩️ رجوع", "callback_data": "back_main"}]
                           ]})
        return

    if student.get("expiry", 0) <= now_ms:
        await send_message(chat_id,
                           "⌛ <b>هذا الكود منتهي الصلاحية!</b>\n\n"
                           "انتهى تاريخ هذا الكود.\n"
                           "تواصل مع الموظف لتجديد الاشتراك.",
                           reply_markup={"inline_keyboard": [
                               [{"text": "↩️ رجوع", "callback_data": "back_main"}]
                           ]})
        return

    existing_tg_id = student.get("telegram_id")
    if existing_tg_id and existing_tg_id != user_id:
        await send_message(chat_id,
                           "📱 <b>هذا الكود مستخدم بالفعل!</b>\n\n"
                           "تم تفعيل هذا الكود من حساب آخر.\n"
                           "كل كود مخصص لطالب واحد فقط.",
                           reply_markup={"inline_keyboard": [
                               [{"text": "↩️ رجوع", "callback_data": "back_main"}]
                           ]})
        return

    exp_str = _format_date(student["expiry"] / 1000)
    update_student(code, {
        "telegram_id":        user_id,
        "telegram_linked_at": now_ms,
        "activated_at":       now_ms,
    })

    invite_link = await create_invite_link(GROUP_CHAT_ID, member_limit=1)
    logger.info(f"Code activated: user={user_id} code={code}")

    if invite_link:
        await send_message(chat_id,
                           f"🎉 <b>تم تفعيل اشتراكك بنجاح!</b>\n\n"
                           f"👤 <b>الاسم:</b> {student.get('name', 'غير محدد')}\n"
                           f"📅 <b>ينتهي:</b> {exp_str}\n\n"
                           f"🔗 <b>رابط المجموعة:</b>\n{invite_link}\n\n"
                           "✅ مرحباً بك في Full Mark!\n"
                           "بالتوفيق في دراستك 📚",
                           reply_markup={"inline_keyboard": [
                               [{"text": "📊 حالة اشتراكي", "callback_data": "status"}]
                           ]})
    else:
        await send_message(chat_id,
                           f"✅ <b>تم تفعيل اشتراكك!</b>\n\n"
                           f"👤 <b>الاسم:</b> {student.get('name', 'غير محدد')}\n"
                           f"📅 <b>ينتهي:</b> {exp_str}\n\n"
                           "⚠️ لم نتمكن من إنشاء رابط الدخول الآن.\n"
                           "تواصل مع الإدارة للحصول على الرابط.",
                           reply_markup={"inline_keyboard": [
                               [{"text": "↩️ رجوع", "callback_data": "back_main"}]
                           ]})


# ─────────────────────────────────────────────────────────
#  حالة الاشتراك
# ─────────────────────────────────────────────────────────

async def _handle_status(chat_id: int, msg_id: int, user_id: int):
    from api.database import init_firebase
    from firebase_admin import db as fdb
    init_firebase()

    all_students = fdb.reference("students").get() or {}
    now_ms = int(time.time() * 1000)

    user_records = [
        {"code": code, **data}
        for code, data in all_students.items()
        if isinstance(data, dict) and data.get("telegram_id") == user_id
    ]

    if not user_records:
        await tg("editMessageText",
                 chat_id=chat_id, message_id=msg_id,
                 text=(
                     "📊 <b>حالة اشتراكك</b>\n\n"
                     "❌ لم يتم العثور على أي اشتراك مرتبط بحسابك.\n\n"
                     "قم بتفعيل كودك أو جرّب التجربة المجانية!"
                 ),
                 parse_mode="HTML",
                 reply_markup={"inline_keyboard": [
                     [{"text": "🔑 تفعيل كود",    "callback_data": "activate"}],
                     [{"text": "🎁 تجربة مجانية", "callback_data": "trial"}],
                     [{"text": "↩️ رجوع",         "callback_data": "back_main"}]
                 ]})
        return

    latest    = sorted(user_records, key=lambda x: x.get("expiry", 0), reverse=True)[0]
    exp_ms    = latest.get("expiry", 0)
    is_active = exp_ms > now_ms and not latest.get("banned", False)
    exp_str   = _format_date(exp_ms / 1000)

    if latest.get("banned"):
        status_icon = "🚫 محظور"
    elif is_active:
        remaining_days = int((exp_ms - now_ms) / (86400 * 1000))
        status_icon = f"✅ نشط — متبقي {remaining_days} يوم"
    else:
        status_icon = "⌛ منتهي"

    sub_type = {
        "trial":       "🎁 تجربة مجانية",
        "month":       "📅 اشتراك شهري",
        "free_month":  "🎁 شهر مجاني",
        "custom_days": f"🗓️ {latest.get('daysCount', '?')} يوم",
    }.get(latest.get("type", ""), "📌 اشتراك")

    await tg("editMessageText",
             chat_id=chat_id, message_id=msg_id,
             text=(
                 "📊 <b>حالة اشتراكك</b>\n\n"
                 f"👤 <b>الاسم:</b> {latest.get('name', 'غير محدد')}\n"
                 f"🔑 <b>الكود:</b> <code>{latest['code']}</code>\n"
                 f"📦 <b>النوع:</b> {sub_type}\n"
                 f"📅 <b>الانتهاء:</b> {exp_str}\n"
                 f"📶 <b>الحالة:</b> {status_icon}"
             ),
             parse_mode="HTML",
             reply_markup={"inline_keyboard": [
                 [{"text": "↩️ رجوع", "callback_data": "back_main"}]
             ]})


# ─────────────────────────────────────────────────────────
#  Endpoints الحظر الفوري (تُستدعى من لوحة التحكم)
# ─────────────────────────────────────────────────────────

@app.route("/api/ban-student", methods=["POST"])
def ban_student():
    """
    يُستدعى من لوحة التحكم لطرد طالب فوراً.
    Body: { "code": "123456789" }
    """
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
            "🚫 <b>تم إيقاف اشتراكك!</b>\n\nتواصل مع الإدارة لمعرفة السبب."
        ))
        logger.info(f"Banned & kicked: code={code} tg={tg_id}")
        return jsonify(ok=True, kicked=True, telegram_id=tg_id)

    return jsonify(ok=True, kicked=False, reason="no telegram_id")


@app.route("/api/ban-staff-codes", methods=["POST"])
def ban_staff_codes():
    """
    طرد كل الطلاب المرتبطين بأكواد موظف معين.
    Body: { "staff_id": "123456" }
    """
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
                            "🚫 <b>تم إيقاف اشتراكك!</b>\n\nتواصل مع الإدارة لمعرفة السبب."
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
    app.run(host="0.0.0.0", port=port, debug=True)
