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
