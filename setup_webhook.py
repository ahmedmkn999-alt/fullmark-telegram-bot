#!/usr/bin/env python3
"""
سكريبت تسجيل الـ Webhook — شغّله مرة واحدة بعد الرفع على Vercel

الاستخدام:
  python setup_webhook.py https://YOUR-BOT.vercel.app
"""

import sys
import httpx

BOT_TOKEN = "8857188327:AAF0F4nLxgGLaRQkFjS1BK8kc3eiDCoNBCA"

def main():
    if len(sys.argv) < 2:
        print("❌ أرسل رابط Vercel كـ argument")
        print("   مثال: python setup_webhook.py https://fullmark-students.vercel.app")
        sys.exit(1)

    base_url   = sys.argv[1].rstrip("/")
    webhook_url = f"{base_url}/api"

    print(f"🔗 جاري تسجيل Webhook على: {webhook_url}")

    r = httpx.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook",
        json={
            "url":             webhook_url,
            "allowed_updates": ["message", "callback_query"],
            "drop_pending_updates": True,
        }
    )

    data = r.json()
    if data.get("ok"):
        print(f"✅ تم تسجيل الـ Webhook بنجاح!")
        print(f"   {data.get('description')}")
    else:
        print(f"❌ فشل التسجيل: {data}")

    # عرض معلومات البوت
    info = httpx.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getMe").json()
    if info.get("ok"):
        bot = info["result"]
        print(f"\n🤖 معلومات البوت:")
        print(f"   الاسم: {bot['first_name']}")
        print(f"   يوزر: @{bot['username']}")
        print(f"   ID:   {bot['id']}")

if __name__ == "__main__":
    main()
