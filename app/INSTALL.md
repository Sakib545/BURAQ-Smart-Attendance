# BURAQ Smart Attendance — v9.26.0

তিনটি জিনিস: **late-এর বাগ ঠিক**, **WhatsApp ছুটির আবেদন**, **Monthly Performance**।

---

## ⚠️ সবচেয়ে জরুরি নিয়ম

**প্রতিটি ফাইল `app/`, `tests/`, `scripts/` ফোল্ডারের ভেতরে বসাতে হবে — root-এ নয়।**

আপনার repo-র root-এ `main.py`, `services.py`, `config.py`, `face_ai.py`, `ui.py`,
`whatsapp.py` নামে পুরোনো duplicate কপি আছে যেগুলো কেউ import করে না। `Dockerfile`
আর `start.sh` শুধু `app.main:app` চালায়। আগে দুইবার এখানেই fix হারিয়ে গেছে।

---

## ১. Late-এর বাগ (সবচেয়ে জরুরি)

**সমস্যা:** Second Shift-এর কর্মী ৩:৫৬-এ এসে (ডিউটি শুরুর ৪ মিনিট আগে)
"446m late" দেখাচ্ছিলেন।

**কারণ:** cutoff `16:00` মানে ১৬:০০-এর আগে চেক-ইন = First Shift। তাই ১৫:৫৬-কে
First Shift ধরে সকাল ৮:৩০ থেকে দেরি গোনা হচ্ছিল → ৪৪৬ মিনিট।

**সমাধান:** `app/services.py`-তে নতুন `resolve_attendance_shift()` — কর্মচারীর
**assigned shift** আগে দেখে, ঘড়ি পরে।

- `shift` কলামে `second` / `evening` / `night` → সবসময় Second Shift।
  যত আগেই আসুন, ভুল হবে না। cutoff আর এঁদের উপর প্রভাব ফেলে না।
- `shift` কলামে `morning` (ডিফল্ট) → আগের মতোই ঘড়ি দেখে ঠিক হবে।
  **কারও behaviour বদলায়নি** — তাই যাঁদের shift কলাম ভুল সেট করা, তাঁদের
  ক্ষেত্রেও নতুন কোনো সমস্যা তৈরি হবে না।

আসল দেরি এখনো ঠিকই গোনা হয়: Second Shift-এর কেউ ৪:২৫-এ এলে ২৫ মিনিট late।

Cutoff `16:00`-ই রাখতে পারেন, বদলানোর দরকার নেই।

### পুরোনো ভুল রেকর্ড ঠিক করা

যেগুলো ইতিমধ্যে ৪৪৬/৪৪৯ লেখা হয়ে গেছে সেগুলো নিজে থেকে ঠিক হবে না।

```bash
# প্রথমে শুধু দেখুন কী বদলাবে (কিছুই লেখে না)
python scripts/repair_shift_lateness.py

# তারিখ নির্দিষ্ট করতে চাইলে
python scripts/repair_shift_lateness.py --from 2026-08-01 --to 2026-08-21

# দেখে সন্তুষ্ট হলে তবেই
python scripts/repair_shift_lateness.py --apply
```

`--apply` ছাড়া কিছুই লেখে না। শুধু `attendance_shift` আর `late_minutes` বদলায় —
check-in/check-out সময় কখনো ছোঁয় না।

---

## ২. WhatsApp দিয়ে ছুটির আবেদন

কর্মচারী লিখবেন `leave` / `ছুটি` / `6` → ধরন → শুরুর তারিখ → শেষ তারিখ → কারণ → `YES`।

তারিখ: `2026-08-25`, `25/08/2026`, `২৫/০৮/২০২৬`, `আজ`, `আগামীকাল`
বাতিল: যেকোনো ধাপে `cancel`
অবস্থা দেখা: `my leave` / `7`

আবেদন সোজা HR ড্যাশবোর্ডে যায়। HR approve/reject করলে কর্মচারী WhatsApp-এ জানেন।

যাচাই: শেষ তারিখ শুরুর আগে নয় · ৩০ দিনের বেশি পুরোনো নয় · সর্বোচ্চ ৬০ দিন ·
আগের ছুটির সাথে তারিখ মিলে গেলে বাধা।

HR-side আগে থেকেই ছিল, নতুন টেবিল লাগেনি।

---

## ৩. Monthly Performance

নতুন পেজ `/performance-awards`।

স্কোর = উপস্থিতি ৫০ + সময়ানুবর্তিতা ৩৫ + Check-out সম্পূর্ণতা ১৫।
অনুমোদিত ছুটি স্কোর কমায় না। ১০ দিনের কম duty হলে র‍্যাঙ্কিংয়ে আসেন না।

- 🏆 সেরা কর্মী → অভিনন্দনের বার্তা
- 👏 ভালো মাস → ধন্যবাদ বার্তা
- 📋 দুর্বল মাস → **ব্যক্তিগত, তথ্যভিত্তিক নোট** — কোনো আখ্যা নয়, কোনো তুলনা নয়,
  শুধু সংখ্যা + "হিসাব ভুল মনে হলে জানান" + "সমস্যা থাকলে কথা বলুন"

**কোনো বার্তা নিজে থেকে যায় না।** HR প্রতিটি বার্তার হুবহু টেক্সট প্রিভিউ করে
বাটনে চাপ দিলে তবেই যায়। একই notice দুইবার যায় না (ডেটাবেস UNIQUE constraint)।
Meta পাঠাতে ব্যর্থ হলে reservation ছেড়ে দেয়, যাতে আবার চেষ্টা করা যায়।
বার্তার ধরন server-side ranking থেকে আসে — ফর্ম বদলে অন্য ধরনের বার্তা পাঠানো যায় না।

---

## ফাইল তালিকা

### নতুন (যোগ করুন)

```
app/leave_flow.py
app/performance.py
scripts/repair_shift_lateness.py
tests/test_leave_flow.py
tests/test_performance.py
tests/test_shift_detection.py
```

### পরিবর্তিত (replace করুন)

| ফাইল | কী বদলেছে |
|---|---|
| `app/services.py` | `resolve_attendance_shift()`, leave কমান্ড, menu |
| `app/config.py` | `SECOND_SHIFT_FROM` default `16:00` |
| `app/whatsapp.py` | Interactive menu-তে ছুটির ২টি row |
| `app/main.py` | `/performance-awards`, leave notification, version 9.26.0 |
| `app/database.py` | `performance_notices` টেবিল |
| `tests/test_smoke.py` | version assertion 9.26.0 |
| `tests/test_shift_rules.py` | version assertion 9.26.0 |

`all-changes.patch`-এ শুধু বদলানো লাইনগুলো (১৩৮ লাইন)।

---

## Deploy

1. উপরের ফাইলগুলো ঠিক ফোল্ডারে বসান
2. Railway-তে `SECOND_SHIFT_FROM` থাকলে `16:00` করুন (বা মুছে দিন)
3. Push → redeploy
4. `python scripts/repair_shift_lateness.py` চালিয়ে পুরোনো রেকর্ড দেখুন,
   ঠিক মনে হলে `--apply`

Database migration আলাদা লাগবে না। কোনো টেবিল drop বা truncate হয় না।

## যাচাই

```bash
pytest tests/ -q     # 96 passed
```

প্রতিটি ফাইল আলাদা করেও পাস করে (একসাথে চালালেই কেবল পাস করে — এমন নয়)।

Deploy-এর পর দেখুন:
- Logs: `BURAQ v9.26.0 started`
- Dashboard-এ 🏆 Monthly Performance কার্ড
- Second Shift-এর কেউ ৩:৫৬-এ check in করলে **On time**
- WhatsApp-এ `Menu` লিখলে ছুটির আবেদন ও My Leave

---

## এখনো বাকি (ঐচ্ছিক)

- Root-এর duplicate ফাইল delete: `main.py`, `services.py`, `config.py`,
  `face_ai.py`, `ui.py`, `whatsapp.py`, `base.html`
- `.zip` ও `__pycache__` `.gitignore`-এ দিন
- `process()`-এ `"5"` একই সাথে Help ও My Duty-তে ম্যাপ করা
- GitHub Actions CI — প্রতি push-এ `pytest`
