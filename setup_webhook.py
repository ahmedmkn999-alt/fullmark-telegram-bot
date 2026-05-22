"""
╔══════════════════════════════════════════════════════════╗
║     Full Mark — تسجيل Webhook على Telegram               ║
║     شغّل هذا السكريبت مرة واحدة بعد كل نشر على Vercel  ║
╚══════════════════════════════════════════════════════════╝

الاستخدام:
    python setup_webhook.py https://YOUR-PROJECT.vercel.app

أو عدّل VERCEL_URL أدناه مباشرةً ثم شغّل:
    python setup_webhook.py
"""

import sys
import httpx

# ── إعدادات ──────────────────────────────────────────────
BOT_TOKEN   = "8857188327:AAF0F4nLxgGLaRQkFjS1BK8kc3eiDCoNBCA"
VERCEL_URL  = ""   # ← ضع رابط Vercel هنا لو ما مررته كـ argument

# المسار الصحيح للـ Webhook — يجب أن يكون /telegram
WEBHOOK_PATH = "/telegram"
# ─────────────────────────────────────────────────────────

def main():
    base_url = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else VERCEL_URL.rstrip("/")

    if not base_url:
        print("❌ خطأ: مرر رابط Vercel كـ argument أو عدّل VERCEL_URL في السكريبت")
        print("مثال: python setup_webhook.py https://my-bot.vercel.app")
        sys.exit(1)

    webhook_url = f"{base_url}{WEBHOOK_PATH}"
    api_base    = f"https://api.telegram.org/bot{BOT_TOKEN}"

    print(f"🔗 رابط الـ Webhook: {webhook_url}")
    print("⏳ جاري التسجيل...")

    with httpx.Client(timeout=15) as client:

        # ── احذف الـ Webhook القديم أولاً ──
        del_r = client.post(f"{api_base}/deleteWebhook", json={"drop_pending_updates": True})
        del_data = del_r.json()
        if del_data.get("ok"):
            print("🗑️  تم حذف الـ Webhook القديم")
        else:
            print(f"⚠️  تحذير عند الحذف: {del_data.get('description')}")

        # ── سجّل الـ Webhook الجديد ──
        set_r = client.post(f"{api_base}/setWebhook", json={
            "url":               webhook_url,
            "allowed_updates":   ["message", "callback_query"],
            "drop_pending_updates": True,
            "max_connections":   40,
        })
        set_data = set_r.json()

        if set_data.get("ok"):
            print(f"✅ تم تسجيل الـ Webhook بنجاح!")
            print(f"   URL: {webhook_url}")
        else:
            print(f"❌ فشل التسجيل: {set_data.get('description')}")
            sys.exit(1)

        # ── تحقق من الإعدادات ──
        info_r = client.get(f"{api_base}/getWebhookInfo")
        info   = info_r.json().get("result", {})
        print("\n📋 معلومات الـ Webhook الحالي:")
        print(f"   URL:             {info.get('url')}")
        print(f"   Pending updates: {info.get('pending_update_count', 0)}")
        print(f"   Last error:      {info.get('last_error_message', 'لا يوجد')}")
        print(f"   Max connections: {info.get('max_connections', '؟')}")

    print("\n🎉 البوت جاهز! أرسل /start للتجربة.")


if __name__ == "__main__":
    main()
