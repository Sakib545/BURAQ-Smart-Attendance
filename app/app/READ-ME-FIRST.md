# REPLACE — বর্তমান main-এর উপর বসানোর জন্য

আপনার এখনকার কমিট: `2422f95`। এই ফোল্ডারের ফাইলগুলো ওর উপরেই বসবে।

আমি বসিয়ে চালিয়ে পরীক্ষা করেছি — **96 passed**।

---

## ⚠️ একটাই নিয়ম: সব ফাইল একসাথে

**আংশিক replace করবেন না।** এখন repo-তে ChatGPT-র `leave_flow.py` আছে, যার
ফাংশনের নাম আলাদা (`handle_leave_message`)। আমার `services.py` খোঁজে
`handle_leave_state`. একটা নিলে আরেকটা না নিলে **অ্যাপ import-এই ক্র্যাশ করবে** —
পুরো সিস্টেম বন্ধ হয়ে যাবে।

নিচের ১৩টা ফাইলই একসাথে replace করুন।

---

## কোন ফাইল কোথায়

সব ফাইল **`app/`, `tests/`, `scripts/` ফোল্ডারের ভেতরে** — repo-র root-এ নয়।

```
app/config.py          ← replace
app/database.py        ← replace
app/main.py            ← replace
app/services.py        ← replace   (late-এর বাগ ফিক্স এখানে)
app/whatsapp.py        ← replace   (মেনুতে ছুটির ২টি row — এটাই বাদ পড়েছিল)
app/leave_flow.py      ← replace
app/performance.py     ← replace

tests/test_leave_flow.py     ← replace
tests/test_performance.py    ← replace
tests/test_shift_rules.py    ← replace
tests/test_smoke.py          ← replace
tests/test_shift_detection.py  ← নতুন ফাইল

scripts/repair_shift_lateness.py  ← নতুন ফাইল
```

`replace-current-main.patch`-এ বর্তমান main থেকে পার্থক্যটা আছে।

---

## এখনকার কোডে যা যা ঠিক হবে

| সমস্যা | অবস্থা |
|---|---|
| WhatsApp মেনুতে ছুটির অপশন নেই | `app/whatsapp.py` replace হলে আসবে |
| Text menu-তে ৫টা অপশন | ৭টা হবে |
| ৩:৫৬-এ check in → 446m late | `resolve_attendance_shift()` দিয়ে ঠিক |
| পুরোনো ভুল late রেকর্ড | repair script দিয়ে ঠিক |
| `pytest tests/` আটকে যায় | ঠিক |
| version assertion ফেল (9.24.1 vs 9.26.0) | ঠিক |

ChatGPT-র তিনটা ভালো উন্নতিও রাখা আছে — atomic duplicate guard,
notice type-এর server-side যাচাই, পাঠাতে ব্যর্থ হলে retry।

---

## ধাপ

**১. ফাইল বসান** — উপরের ১৩টা, ঠিক ফোল্ডারে।

**২. লোকালে টেস্ট করুন — push করার আগে:**

```bash
pytest tests/ -q
```

**96 passed** আসতে হবে। না এলে ফাইল মেশানো হয়ে গেছে — push করবেন না।

**৩. Push ও redeploy।**

**৪. পুরোনো ভুল রেকর্ড ঠিক করুন:**

```bash
python scripts/repair_shift_lateness.py            # শুধু দেখাবে
python scripts/repair_shift_lateness.py --apply    # দেখে সন্তুষ্ট হলে
```

`--apply` ছাড়া কিছুই লেখে না। শুধু `attendance_shift` আর `late_minutes` বদলায়।

---

## Deploy-এর পর যাচাই

- Logs: `BURAQ v9.26.0 started`
- WhatsApp-এ `Menu` → **৭টা** অপশন, ছুটির আবেদন ও My Leave সহ
- Second Shift-এর কেউ ৩:৫৬-এ check in → **On time**
- Dashboard-এ 🏆 Monthly Performance কার্ড

Database migration আলাদা লাগবে না। কোনো টেবিল drop বা truncate হয় না।
`v9.26-leave-performance` marker আগের মতোই রাখা আছে।
