# BURAQ Smart Attendance v4.3 — Production Ready

## Railway-তে ব্যবহার

1. এই project GitHub-এ upload/push করুন।
2. Railway-তে repository deploy করুন। আলাদা Start Command বা PORT লিখবেন না।
3. Railway project-এ PostgreSQL service add করে app service-এ `DATABASE_URL` reference দিন।
4. app-এর public domain খুলুন এবং `/setup` page-এ Admin password, WhatsApp Access Token, Phone Number ID ও Verify Token save করুন।
5. Dashboard-এ দেখানো Callback URL এবং Verify Token Meta WhatsApp Configuration-এ একবার বসিয়ে `messages` subscribe করুন।

এরপর Mac/PC বন্ধ থাকলেও service Railway-এ চলবে।

## গুরুত্বপূর্ণ URL

- `/` — Admin dashboard/login
- `/health` — Railway liveness healthcheck (সবসময় দ্রুত 200)
- `/ready` — Database readiness check
- `/webhook/whatsapp` — Meta webhook

## Railway Healthcheck fix

v4.3-এ `/health` database provisioning-এর জন্য 503 দেয় না। PostgreSQL সাময়িকভাবে unavailable হলে app retry করে এবং startup বন্ধ না করে temporary SQLite fallback ব্যবহার করে। Dashboard warning দেখায়, যাতে service deploy হয় এবং পরে PostgreSQL reference ঠিক করা যায়। স্থায়ী attendance data-এর জন্য PostgreSQL অবশ্যই যুক্ত রাখুন।

## v5.1 changes
- Registration preview with YES/CANCEL confirmation.
- Better Bangladesh phone-number matching.
- Automatic approval when employee phone matches WhatsApp number.
- Pending admin approval when the number does not match.
- Improved check-in, check-out, worked-hours, late and early-leave messages.
- Safer duplicate-registration handling.

## v5.1.1 Registration Fix

- Railway startup now automatically syncs the bundled 29-person BURAQ employee list.
- Real Staff IDs such as `B520202` work without running an import command.
- Existing WhatsApp registrations are preserved during startup sync.
- The obsolete `BRQ001` demo record and example were removed.
- Efad's corrected phone number is included: `01706759945`.


## v5.1.2 — Approval Notification

Admin dashboard থেকে pending registration approve করলে employee-এর WhatsApp-এ সঙ্গে সঙ্গে confirmation message যায়। Approval database-এ save হয়; WhatsApp API সাময়িকভাবে ব্যর্থ হলেও approval বাতিল হয় না এবং error log-এ লেখা হয়।
