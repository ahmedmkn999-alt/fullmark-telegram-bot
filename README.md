# 🎓 Full Mark ثانوية عامة — بوت الطلاب

بوت Telegram للطلاب يعمل بنظام **Webhook على Vercel**، متصل بنفس Firebase الخاص بلوحة التحكم (FullMark Admins).

---

## 📁 هيكل المشروع

```
fullmark_student_bot/
├── api/
│   ├── __init__.py
│   ├── index.py        ← البوت الرئيسي (Flask + Webhook)
│   └── database.py     ← موديول Firebase (نفس قاعدة البيانات)
├── requirements.txt
├── vercel.json
├── setup_webhook.py    ← سكريبت تسجيل الـ Webhook
└── README.md
```

---

## 🔄 التزامن مع لوحة التحكم

| الحدث في لوحة التحكم | ما يحدث في بوت الطلاب |
|---|---|
| حظر طالب | طرده من المجموعة فوراً + إشعار |
| حظر أكواد موظف | طرد كل طلابه فوراً |
| إنهاء وقت كود | طرد الطالب عند أول check-expired |
| انتهاء التجربة (30 دقيقة) | طرد تلقائي + رسالة |

---

## 🚀 خطوات الرفع على Vercel

### 1. رفع المشروع
```bash
# ارفع المجلد على GitHub ثم اربطه بـ Vercel
# أو استخدم Vercel CLI:
vercel deploy --prod
```

### 2. تسجيل الـ Webhook
```bash
pip install httpx
python setup_webhook.py https://YOUR-PROJECT.vercel.app
```

### 3. التحقق
ابعت `/start` للبوت وهيشتغل فوراً ✅

---

## ⚙️ متغيرات البيئة (اختياري — للأمان)

بدلاً من وضع بيانات Firebase في الكود مباشرةً، ضعها في Vercel Environment Variables:

```
FIREBASE_CREDENTIALS_JSON = { ... محتوى service-account.json كاملاً ... }
```

---

## 🔗 ربط لوحة التحكم ببوت الطلاب

لتفعيل الحظر الفوري، أضف هذه الاستدعاءات في كود لوحة التحكم (index.js):

### عند حظر طالب:
```javascript
// بعد: await withTimeout(ref.update({ banned: newBanned }));
if (newBanned) {
  await fetch("https://YOUR-STUDENT-BOT.vercel.app/api/ban-student", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code: text })
  });
}
```

### عند حظر أكواد موظف:
```javascript
// بعد: await withTimeout(db.ref('students').update(updates));
await fetch("https://YOUR-STUDENT-BOT.vercel.app/api/ban-staff-codes", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ staff_id: staffInfo.id })
});
```

---

## 📋 الـ Endpoints

| Endpoint | Method | الوصف |
|---|---|---|
| `/api` | POST | الـ Webhook الرئيسي |
| `/api` | GET | Health Check |
| `/api/ban-student` | POST | طرد طالب فوري من لوحة التحكم |
| `/api/ban-staff-codes` | POST | طرد طلاب موظف محظور |
| `/api/check-expired` | GET/POST | فحص الأكواد المنتهية (Cron) |

---

## 🎓 مميزات البوت

- ✅ تجربة مجانية 30 دقيقة مع رابط دخول مخصص
- ✅ تفعيل أكواد الاشتراك مع التحقق الكامل
- ✅ طرد تلقائي عند انتهاء الوقت
- ✅ تزامن فوري مع لوحة التحكم
- ✅ حالة الاشتراك للطالب
- ✅ تصميم Inline Keyboard احترافي
- ✅ لا يعلق — async بالكامل
