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
#  🔧 إعدادات البوت — عدّل هنا فقط
# ─────────────────────────────────────────────────────────

BOT_TOKEN       = "8857188327:AAF0F4nLxgGLaRQkFjS1BK8kc3eiDCoNBCA"
GROUP_CHAT_ID   = -1003997728302   # المجموعة المقيدة
TRIAL_MINUTES   = 30               # مدة التجربة المجانية بالدقائق

# ─────────────────────────────────────────────────────────
#  إعداد Logging
# ─────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("FullMarkBot")

# ─────────────────────────────────────────────────────────
#  Telegram API Helpers (بدون مكتبات خارجية ثقيلة)
# ─────────────────────────────────────────────────────────

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

async def tg(method: str, **kwargs) -> dict:
    """استدعاء Telegram Bot API بشكل async."""
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(f"{TELEGRAM_API}/{method}", json=kwargs)
        data = r.json()
        if not data.get("ok"):
            logger.warning(f"TG {method} failed: {data.get('description')}")
        return data


def tg_sync(method: str, **kwargs) -> dict:
    """نسخة sync لاستدعاء Telegram API (لـ Flask handlers)."""
    return asyncio.run(tg(method, **kwargs))


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
    """طرد المستخدم من المجموعة."""
    try:
        now    = int(time.time())
        result = await tg("banChatMember",
                          chat_id=chat_id,
                          user_id=user_id,
                          until_date=now + 35)   # حظر 35 ثانية = طرد فعلي
        # فك الحظر فوراً عشان يقدر يرجع بكود جديد
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
    """إنشاء رابط دخول مخصص (استخدام مرة واحدة)."""
    result = await tg("createChatInviteLink",
                      chat_id=chat_id,
                      member_limit=member_limit,
                      creates_join_request=False)
    if result.get("ok"):
        return result["result"]["invite_link"]
    return None

# ─────────────────────────────────────────────────────────
#  لوحة مفاتيح البوت
# ─────────────────────────────────────────────────────────

MAIN_MENU = {
    "inline_keyboard": [
        [
            {"text": "🎁 تجربة مجانية 30 دقيقة", "callback_data": "trial"}
        ],
        [
            {"text": "🔑 تفعيل كود الاشتراك",    "callback_data": "activate"}
        ],
        [
            {"text": "📊 حالة اشتراكي",           "callback_data": "status"}
        ],
        [
            {"text": "❓ مساعدة وتواصل",          "callback_data": "help"}
        ]
    ]
}

def cancel_keyboard():
    return {"inline_keyboard": [[{"text": "❌ إلغاء", "callback_data": "cancel"}]]}

# ─────────────────────────────────────────────────────────
#  حالات المحادثة (في الذاكرة — serverless آمن)
# ─────────────────────────────────────────────────────────

# { user_id: {"step": "WAIT_CODE", ...} }
_sessions: dict[int, dict] = {}

def get_session(user_id: int) -> dict:
    if user_id not in _sessions:
        _sessions[user_id] = {}
    return _sessions[user_id]

def clear_session(user_id: int):
    _sessions.pop(user_id, None)

# ─────────────────────────────────────────────────────────
#  رسالة Welcome عظيمة
# ─────────────────────────────────────────────────────────

WELCOME_TEXT = """\
🎓 <b>أهلاً بك في بوت Full Mark ثانوية عامة!</b>

━━━━━━━━━━━━━━━━━━━━━━━
🏆 <b>منصة المذاكرة الأولى للثانوية العامة</b>
━━━━━━━━━━━━━━━━━━━━━━━

📚 محتوى شامل لكل المواد
🎯 امتحانات وتدريبات تفاعلية
👨‍🏫 أفضل المدرسين والشروحات
📈 تابع تقدمك أولاً بأول

━━━━━━━━━━━━━━━━━━━━━━━
👇 <b>اختر من القائمة أدناه:</b>
"""

# ─────────────────────────────────────────────────────────
#  Flask App
# ─────────────────────────────────────────────────────────

app = Flask(__name__)


# ──────────────────────────────
#  معالج الـ Webhook الرئيسي
# ──────────────────────────────

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


# ──────────────────────────────
#  Dispatcher
# ──────────────────────────────

async def _dispatch(update: dict):
    try:
        if "message" in update:
            await _handle_message(update["message"])
        elif "callback_query" in update:
            await _handle_callback(update["callback_query"])
    except Exception as e:
        logger.exception(f"Dispatch error: {e}")


# ──────────────────────────────
#  معالج الرسائل النصية
# ──────────────────────────────

async def _handle_message(msg: dict):
    user_id   = msg["from"]["id"]
    chat_id   = msg["chat"]["id"]
    text      = msg.get("text", "").strip()
    first     = msg["from"].get("first_name", "صديقي")
    session   = get_session(user_id)

    # ── /start ──
    if text.startswith("/start"):
        clear_session(user_id)
        await send_message(chat_id,
                           WELCOME_TEXT.replace("صديقي", first),
                           reply_markup=MAIN_MENU)
        return

    # ── إلغاء ──
    if text == "إلغاء" or text == "/cancel":
        clear_session(user_id)
        await send_message(chat_id, "↩️ تم الإلغاء.", reply_markup=MAIN_MENU)
        return

    step = session.get("step")

    # ── انتظار كود التفعيل ──
    if step == "WAIT_CODE":
        await _process_activation_code(chat_id, user_id, text)
        return

    # أي رسالة تانية → أظهر القائمة
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

    # ── إلغاء ──
    if data == "cancel":
        clear_session(user_id)
        await tg("editMessageText",
                 chat_id=chat_id, message_id=msg_id,
                 text="↩️ تم الإلغاء.",
                 parse_mode="HTML",
                 reply_markup=MAIN_MENU)
        return

    # ── تجربة مجانية ──
    if data == "trial":
        await _handle_trial(chat_id, msg_id, user_id, first)
        return

    # ── تفعيل كود ──
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

    # ── حالة الاشتراك ──
    if data == "status":
        await _handle_status(chat_id, msg_id, user_id)
        return

    # ── مساعدة ──
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

    # ── رجوع للقائمة الرئيسية ──
    if data == "back_main":
        clear_session(user_id)
        await tg("editMessageText",
                 chat_id=chat_id, message_id=msg_id,
                 text=WELCOME_TEXT,
                 parse_mode="HTML",
                 reply_markup=MAIN_MENU)
        return

    # ── تأكيد التجربة ──
    if data == "confirm_trial":
        await _start_trial(chat_id, msg_id, user_id, first)
        return


# ─────────────────────────────────────────────────────────
#  منطق التجربة المجانية
# ─────────────────────────────────────────────────────────

async def _handle_trial(chat_id: int, msg_id: int, user_id: int, first: str):
    """التحقق من التجربة وإما السماح أو الرفض."""

    # هل لديه اشتراك نشط؟
    active_sub = get_active_subscription(user_id)
    if active_sub:
        exp_ts  = active_sub.get("expiry", 0) / 1000
        exp_str = _format_date(exp_ts)
        await tg("editMessageText",
                 chat_id=chat_id, message_id=msg_id,
                 text=(
                     f"✅ <b>أنت مشترك بالفعل!</b>\n\n"
                     f"👤 الاسم: {active_sub.get('name', 'غير محدد')}\n"
                     f"📅 ينتهي: {exp_str}\n\n"
                     f"لا تحتاج للتجربة المجانية 😊"
                 ),
                 parse_mode="HTML",
                 reply_markup={"inline_keyboard": [
                     [{"text": "↩️ رجوع", "callback_data": "back_main"}]
                 ]})
        return

    # هل استخدم التجربة من قبل؟
    trial_info = get_trial_info(user_id)
    if trial_info:
        exp_ts  = trial_info.get("expiry", 0) / 1000
        now_ts  = time.time()
        if exp_ts > now_ts:
            remaining = int((exp_ts - now_ts) / 60)
            await tg("editMessageText",
                     chat_id=chat_id, message_id=msg_id,
                     text=(
                         f"⏳ <b>لديك تجربة نشطة بالفعل!</b>\n\n"
                         f"⏱ متبقي: <b>{remaining} دقيقة</b>\n\n"
                         f"ادخل المجموعة من الرابط الذي أُرسل لك."
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

    # عرض التجربة للمرة الأولى
    await tg("editMessageText",
             chat_id=chat_id, message_id=msg_id,
             text=(
                 f"🎁 <b>تجربة مجانية لمدة {TRIAL_MINUTES} دقيقة!</b>\n\n"
                 "✨ ستحصل على:\n"
                 "• رابط دخول مباشر للمجموعة\n"
                 f"• وصول كامل لمدة {TRIAL_MINUTES} دقيقة\n"
                 "• بعدها سيتم إخراجك تلقائياً\n\n"
                 "⚠️ <b>ملاحظة:</b> التجربة تُستخدم مرة واحدة فقط!\n\n"
                 "هل تريد بدء التجربة الآن؟"
             ),
             parse_mode="HTML",
             reply_markup={"inline_keyboard": [
                 [{"text": "✅ نعم، ابدأ التجربة", "callback_data": "confirm_trial"}],
                 [{"text": "❌ لا، رجوع",          "callback_data": "back_main"}]
             ]})


async def _start_trial(chat_id: int, msg_id: int, user_id: int, first: str):
    """بدء التجربة الفعلية: إنشاء رابط + تسجيل في Firebase."""

    await tg("editMessageText",
             chat_id=chat_id, message_id=msg_id,
             text="⏳ <b>جاري تجهيز رابط التجربة...</b>",
             parse_mode="HTML")

    # إنشاء رابط دخول مخصص (member_limit=1)
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

    # حساب وقت الانتهاء
    now_ms  = int(time.time() * 1000)
    exp_ms  = now_ms + (TRIAL_MINUTES * 60 * 1000)
    exp_str = _format_date(exp_ms / 1000)

    # توليد كود تجربة وحفظه في Firebase (نفس تنسيق لوحة التحكم)
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
    # حفظ في students مباشرةً (نفس جدول لوحة التحكم)
    from api.database import init_firebase
    from firebase_admin import db as fdb
    init_firebase()
    fdb.reference(f"students/{trial_code}").set(trial_data)

    logger.info(f"Trial started: user={user_id} code={trial_code} exp={exp_str}")

    # إرسال الرسالة الأخيرة مع الرابط
    await tg("editMessageText",
             chat_id=chat_id, message_id=msg_id,
             text=(
                 f"🎉 <b>تجربتك جاهزة يا {first}!</b>\n\n"
                 f"⏱ <b>المدة:</b> {TRIAL_MINUTES} دقيقة\n"
                 f"📅 <b>تنتهي:</b> {exp_str}\n\n"
                 f"🔗 <b>رابط الدخول:</b>\n{invite_link}\n\n"
                 f"⚠️ <b>مهم جداً:</b>\n"
                 f"• الرابط للاستخدام مرة واحدة فقط\n"
                 f"• بعد انتهاء الوقت ستُخرج تلقائياً\n"
                 f"• التجربة لا تتكرر — استخدمها بحكمة 😊"
             ),
             parse_mode="HTML",
             reply_markup={"inline_keyboard": [
                 [{"text": "🔑 اشترك الآن بعد التجربة", "callback_data": "activate"}]
             ]})

    # جدولة الطرد بعد انتهاء الوقت (background task)
    asyncio.create_task(_schedule_kick(user_id, trial_code, TRIAL_MINUTES * 60))


async def _schedule_kick(user_id: int, code: str, delay_seconds: int):
    """انتظر وبعدين اطرد الطالب إذا انتهت التجربة ولم يشترك."""
    await asyncio.sleep(delay_seconds)

    # تحقق إذا الكود لسه نشط (ممكن الأدمن أنهاه من لوحة التحكم قبل الوقت)
    student = get_student(code)
    if not student:
        return

    now_ms = int(time.time() * 1000)
    # الطرد إذا: الكود منتهي أو محظور
    if student.get("expiry", 0) <= now_ms or student.get("banned", False):
        await kick_user(GROUP_CHAT_ID, user_id)
        logger.info(f"Auto-kicked trial user {user_id}")
        try:
            await send_message(user_id,
                               "⌛ <b>انتهت فترة التجربة المجانية!</b>\n\n"
                               "نأمل أن المحتوى نال إعجابك 😊\n"
                               "للاستمرار في التعلم اشترك معنا 👇",
                               reply_markup={"inline_keyboard": [
                                   [{"text": "🔑 اشترك الآن", "callback_data": "activate"}]
                               ]})
        except Exception:
            pass


# ─────────────────────────────────────────────────────────
#  منطق تفعيل الكود
# ─────────────────────────────────────────────────────────

async def _process_activation_code(chat_id: int, user_id: int, code: str):
    """التحقق من كود التفعيل وتفعيله."""
    clear_session(user_id)

    # التحقق من صيغة الكود
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

    # جلب بيانات الكود من Firebase
    student = get_student(code)

    # ── الكود غير موجود ──
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

    # ── الكود محظور ──
    if student.get("banned", False):
        await send_message(chat_id,
                           "🚫 <b>هذا الكود محظور!</b>\n\n"
                           "تواصل مع الإدارة لمعرفة السبب.",
                           reply_markup={"inline_keyboard": [
                               [{"text": "↩️ رجوع", "callback_data": "back_main"}]
                           ]})
        return

    # ── الكود منتهي الصلاحية ──
    if student.get("expiry", 0) <= now_ms:
        await send_message(chat_id,
                           "⌛ <b>هذا الكود منتهي الصلاحية!</b>\n\n"
                           "انتهى تاريخ هذا الكود.\n"
                           "تواصل مع الموظف لتجديد الاشتراك.",
                           reply_markup={"inline_keyboard": [
                               [{"text": "↩️ رجوع", "callback_data": "back_main"}]
                           ]})
        return

    # ── الكود مستخدم من قبل شخص آخر ──
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

    # ── تفعيل الكود ✅ ──
    exp_str = _format_date(student["expiry"] / 1000)

    # تحديث Firebase: ربط الكود بـ Telegram ID
    update_student(code, {
        "telegram_id":        user_id,
        "telegram_linked_at": now_ms,
        "activated_at":       now_ms,
    })

    # إنشاء رابط دخول للمجموعة
    invite_link = await create_invite_link(GROUP_CHAT_ID, member_limit=1)

    logger.info(f"Code activated: user={user_id} code={code}")

    if invite_link:
        await send_message(chat_id,
                           f"🎉 <b>تم تفعيل اشتراكك بنجاح!</b>\n\n"
                           f"👤 <b>الاسم:</b> {student.get('name', 'غير محدد')}\n"
                           f"📅 <b>ينتهي:</b> {exp_str}\n\n"
                           f"🔗 <b>رابط المجموعة:</b>\n{invite_link}\n\n"
                           f"✅ مرحباً بك في Full Mark!\n"
                           f"بالتوفيق في دراستك 📚",
                           reply_markup={"inline_keyboard": [
                               [{"text": "📊 حالة اشتراكي", "callback_data": "status"}]
                           ]})
    else:
        await send_message(chat_id,
                           f"✅ <b>تم تفعيل اشتراكك!</b>\n\n"
                           f"👤 <b>الاسم:</b> {student.get('name', 'غير محدد')}\n"
                           f"📅 <b>ينتهي:</b> {exp_str}\n\n"
                           f"⚠️ لم نتمكن من إنشاء رابط الدخول الآن.\n"
                           f"تواصل مع الإدارة للحصول على الرابط.",
                           reply_markup={"inline_keyboard": [
                               [{"text": "↩️ رجوع", "callback_data": "back_main"}]
                           ]})


# ─────────────────────────────────────────────────────────
#  حالة الاشتراك
# ─────────────────────────────────────────────────────────

async def _handle_status(chat_id: int, msg_id: int, user_id: int):
    """عرض حالة اشتراك الطالب."""
    # ابحث عن اشتراك نشط مرتبط بـ telegram_id
    from api.database import init_firebase
    from firebase_admin import db as fdb
    init_firebase()

    all_students = fdb.reference("students").get() or {}
    now_ms = int(time.time() * 1000)

    user_records = []
    for code, data in all_students.items():
        if isinstance(data, dict) and data.get("telegram_id") == user_id:
            user_records.append({"code": code, **data})

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
                     [{"text": "🔑 تفعيل كود",         "callback_data": "activate"}],
                     [{"text": "🎁 تجربة مجانية",      "callback_data": "trial"}],
                     [{"text": "↩️ رجوع",              "callback_data": "back_main"}]
                 ]})
        return

    # أحدث سجل
    latest = sorted(user_records, key=lambda x: x.get("expiry", 0), reverse=True)[0]
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
                 f"📊 <b>حالة اشتراكك</b>\n\n"
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
#  Endpoint خاص: الحظر الفوري من لوحة التحكم
# ─────────────────────────────────────────────────────────
# لوحة التحكم تستدعي هذا الـ endpoint عند حظر طالب أو أكواد موظف

@app.route("/api/ban-student", methods=["POST"])
def ban_student():
    """
    يُستدعى من لوحة التحكم لطرد طالب فوراً.
    Body: { "code": "123456789" }
    """
    body    = request.get_json(force=True, silent=True) or {}
    code    = body.get("code", "").strip()
    if not code:
        return jsonify(ok=False, error="code required"), 400

    student = get_student(code)
    if not student:
        return jsonify(ok=False, error="student not found"), 404

    tg_id = student.get("telegram_id")
    if tg_id:
        asyncio.run(kick_user(GROUP_CHAT_ID, tg_id))
        asyncio.run(send_message(tg_id,
                                 "🚫 <b>تم إيقاف اشتراكك!</b>\n\n"
                                 "تواصل مع الإدارة لمعرفة السبب."))
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
                        await send_message(tg_id,
                                           "🚫 <b>تم إيقاف اشتراكك!</b>\n\n"
                                           "تواصل مع الإدارة لمعرفة السبب.")
                    except Exception:
                        pass
                    kicked_list.append(tg_id)

    asyncio.run(kick_all())
    logger.info(f"Staff ban: staff={staff_id} kicked={len(kicked_list)}")
    return jsonify(ok=True, kicked_count=len(kicked_list), kicked_ids=kicked_list)


# ─────────────────────────────────────────────────────────
#  Endpoint لمراقبة الأكواد المنتهية (يُنفَّذ بـ cron job)
# ─────────────────────────────────────────────────────────

@app.route("/api/check-expired", methods=["GET", "POST"])
def check_expired():
    """
    طرد الطلاب الذين انتهت تجربتهم أو اشتراكهم.
    استدعيه كـ cron job كل دقيقة من Vercel أو أي خدمة خارجية.
    """
    all_students = get_all_students()
    now_ms       = int(time.time() * 1000)
    kicked       = []

    async def do_kicks():
        for code, data in all_students.items():
            if not isinstance(data, dict):
                continue
            tg_id = data.get("telegram_id")
            if not tg_id:
                continue
            expired = data.get("expiry", 0) <= now_ms
            banned  = data.get("banned", False)
            # لو منتهي أو محظور وفي Telegram ID → اطرده
            if (expired or banned) and tg_id:
                result = await kick_user(GROUP_CHAT_ID, tg_id)
                if result and result.get("ok"):
                    kicked.append({"code": code, "tg_id": tg_id})
                    # تنبيه للطالب إذا انتهى اشتراكه
                    if expired and not banned:
                        try:
                            await send_message(tg_id,
                                               "⌛ <b>انتهى اشتراكك!</b>\n\n"
                                               "جدد اشتراكك للاستمرار في التعلم 📚",
                                               reply_markup={"inline_keyboard": [
                                                   [{"text": "🔑 تجديد الاشتراك",
                                                     "callback_data": "activate"}]
                                               ]})
                        except Exception:
                            pass

    asyncio.run(do_kicks())
    logger.info(f"Expiry check: kicked {len(kicked)}")
    return jsonify(ok=True, kicked=len(kicked), details=kicked)


# ─────────────────────────────────────────────────────────
#  Utilities
# ─────────────────────────────────────────────────────────

def _format_date(ts: float) -> str:
    """تنسيق التاريخ بالعربي."""
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
    """توليد كود 9 أرقام — نفس تنسيق لوحة التحكم."""
    import random
    return str(random.randint(100_000_000, 999_999_999))


# ─────────────────────────────────────────────────────────
#  تشغيل محلي (للتطوير فقط)
# ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"Starting dev server on port {port}")
    app.run(host="0.0.0.0", port=port, debug=True)
