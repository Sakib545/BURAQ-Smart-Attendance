# v9.27.0 — পরিষ্কার repo, মেনু ঠিক, Payroll finalize/undo, Leaderboard

এটা **সম্পূর্ণ repository**, আলাদা ফাইলের প্যাকেজ নয়। পুরোটা একবারে replace করুন —
তাহলে আর ফাইল কোন ফোল্ডারে যাবে সেই ঝামেলাই থাকবে না।

---

## কীভাবে আপলোড করবেন

**সবচেয়ে নিরাপদ (git দিয়ে):**

```bash
git clone https://github.com/Sakib545/BURAQ-Smart-Attendance.git buraq
cd buraq
git rm -r --cached . -q          # index পরিষ্কার, ফাইল এখনো ডিস্কে
rm -rf * .gitignore              # পুরোনো ফাইল মুছে ফেলুন (.git ফোল্ডার থাকবে)
# এই zip-এর সব ফাইল এখানে কপি করুন
git add -A
git commit -m "chore: clean repo, fix WhatsApp menu, payroll bulk finalize + undo, leaderboard (v9.27.0)"
git push
```

**GitHub ওয়েব দিয়ে করলে:** আগে পুরোনো `app/app/` ফোল্ডার, ভেতরের
`BURAQ-Smart-Attendance-main/` ফোল্ডার, আর root-এর `main.py` / `services.py` /
`config.py` / `whatsapp.py` / `ui.py` / `face_ai.py` / `base.html` **delete করুন**,
তারপর এই zip-এর ফাইলগুলো আপলোড করুন। এগুলো না মুছলে পুরোনো নকল ফাইল থেকে যাবে।

**Push করার আগে অবশ্যই:**

```bash
pytest tests/ -q
```

**101 passed** আসতে হবে।

---

## ১. WhatsApp মেনু ঠিক হয়েছে

কমান্ডগুলো আগেও কাজ করত, কিন্তু মেনুতে দেখা যেত না — তাই কর্মীরা জানতই না।

```
1️⃣ Register   2️⃣ Check In   3️⃣ Check Out   4️⃣ My Attendance
5️⃣ My Duty    6️⃣ ছুটির আবেদন   7️⃣ My Leave
```

Interactive menu-তেও **ছুটির আবেদন** ও **My Leave** যোগ হয়েছে।

সাথে একটা পুরোনো ভুল: `"5"` একই সাথে Help আর My Duty দুটোতেই ম্যাপ করা ছিল,
তাই My Duty নম্বর দিয়ে কখনো পাওয়া যেত না। এখন Help = `Help`, My Duty = `5`।

## ২. টেস্ট suite আবার চলে

আগে `pytest tests/` collect-ই হতো না, পরে চললেও **আটকে যেত**।

কারণ: প্রতিটি টেস্ট ফাইল import-এর সময় `Path(os.environ["DATABASE_PATH"]).unlink()`
চালাত। `setdefault` পরের ফাইলগুলোতে নিষ্ক্রিয়, তাই সবাই **প্রথম ফাইলের ডেটাবেস**
মুছে ফেলত। আলাদা করে চালালে সমস্যা দেখা যেত না — এজন্যই বারবার চোখ এড়িয়ে গেছে।

`tests/conftest.py` এখন পুরো run-এর জন্য একটাই ডেটাবেস ঠিক করে দেয়।

**101 passed** — সোজা ক্রমে, উল্টো ক্রমে, আর প্রতিটি ফাইল আলাদা করেও।

## ৩. Payroll — এক ক্লিকে finalize, ভুল হলে undo

**`/payroll` পেজে "Finalize all" বোতাম।** চাপার আগে দেখাবে কারা প্রস্তুত,
কারা আটকে আছে — **নাম ও কারণসহ**:

```
✅ ২৪ জন প্রস্তুত · মোট ৳৩,৮৪,০০০
⚠️  ৩ জন আটকে আছে:
    B520214 — Basic Salary is not set
    B520229 — 2 days without Check Out — review first
    B520233 — No scheduled duty found for this month
```

আটকে যাওয়াগুলো draft-এ থেকে যাবে, চুপচাপ বাদ পড়বে না।

**Undo।** `payroll_change_logs`-এ প্রতিটি বদলের পুরো snapshot আগে থেকেই জমা হচ্ছিল,
কেউ কখনো পড়েনি। এখন এক ক্লিকে পুরো batch draft-এ ফেরত — **২৪ ঘণ্টার মধ্যে**।
তারপরও per-employee reopen আছে।

**`paid` কখনো ফেরানো যায় না।** টাকা দেওয়ার পর record বদলালে হিসাব আর
নির্ভরযোগ্য থাকে না। ভুল হলে পরের মাসে adjustment হিসেবে দেখাতে হবে — সেটাই
সঠিক হিসাবরক্ষণ। Batch-এর কোনো একটা payslip paid হয়ে গেলে পুরো batch-এর
undo বোতামও বন্ধ হয়ে যায়।

নতুন ফাইল: `app/payroll_ops.py` · `tests/test_payroll_ops.py` (১৬টি টেস্ট)

## ৪. Leaderboard — `/leaderboard`

`app/performance.py` আগে থেকেই মাসিক স্কোর হিসাব করত, দেখানোর জায়গা ছিল না।

- শীর্ষ ৩ জনের podium (🥇🥈🥉)
- পূর্ণ র‍্যাঙ্কিং টেবিল, ডিপার্টমেন্ট ফিল্টার, মাস বাছাই
- স্কোর = উপস্থিতি ৫০ + সময়ানুবর্তিতা ৩৫ + Check-out সম্পূর্ণতা ১৫
- **অনুমোদিত ছুটি স্কোর কমায় না**
- ১০ দিনের কম duty হলে র‍্যাঙ্কিংয়ে আসে না

**এই পেজে বেতনের কোনো অঙ্ক নেই** — কে কত পায় সেটা সহকর্মীর দেখার জিনিস নয়।
Payroll আলাদা পেজে, আলাদা permission-এ।

## ৫. একটা লুকানো বাগ ধরা পড়েছে

Testing করতে গিয়ে দেখলাম: কোনো finalized payslip-এর `calculation_snapshot`
যদি পুরোনো ভার্সনে লেখা হয়ে থাকে (`total_deduction` জাতীয় নতুন key না থাকলে),
**পুরো `/payroll` পেজ 500 error** দিত — একটা পুরোনো record পুরো মাসটাই
অদেখা করে দিত। প্রোডাকশনে v9.19 বা v9.24-এ finalize করা কোনো record থাকলে
এটা ঘটতে পারত। ঠিক করা হয়েছে, regression টেস্টও আছে।

## ৬. Repo পরিষ্কার

মুছে ফেলা হয়েছে:

- `app/app/` — আপনার আপলোড ভুল জায়গায় গিয়ে তৈরি হয়েছিল (মৃত কোড)
- `BURAQ-Smart-Attendance-main/` — repo-র ভেতরে repo-র পুরো নকল কপি
- root-এর ১৮টি মৃত ডুপ্লিকেট (`main.py`, `services.py`, `config.py`,
  `whatsapp.py`, `ui.py`, `face_ai.py`, `base.html` ইত্যাদি) — `start.sh` শুধু
  `app.main:app` চালায়, তাই এগুলো কখনো import হতো না, শুধু বিভ্রান্তি ছড়াত
- ৩টি `.zip` build archive, সব `__pycache__`
- `app/INSTALL.md`, `app/all-changes.patch`

যোগ হয়েছে `.gitignore` — যাতে `.zip`, `__pycache__`, `*.db` আর কমিট না হয়।

আকার: **৮৯২ KB** (আগে ৬.৩ MB)

---

## Deploy করার পর যাচাই

- Logs: `BURAQ v9.27.0 started`
- WhatsApp-এ `Menu` → **৭টা** অপশন
- Dashboard-এ 🥇 **Leaderboard** কার্ড
- `/payroll` → **Finalize all** বোতাম, তার পাশে **Undo last finalize**

Database migration আলাদা লাগবে না। কোনো টেবিল drop বা truncate হয় না —
`payroll_ops` বিদ্যমান `payroll_change_logs` টেবিলই ব্যবহার করে।

---

## এখনো বাকি (ঐচ্ছিক)

- **Payroll breakdown view** — মূল বেতন থেকে নিট পর্যন্ত ধাপে ধাপে ব্যাখ্যা
  (আপনার "বোঝা সহজ করা" চাওয়াটার অংশ, এখনো করা হয়নি)
- **GitHub Actions CI** — প্রতি push-এ `pytest`। এই কথোপকথনে একই ধরনের ভুল
  তিনবার হয়েছে; CI থাকলে প্রথমবারেই ধরা পড়ত
- **ভিডিও প্রজেক্ট** — Remotion প্রজেক্ট তৈরি হয়ে আছে, render বাকি
