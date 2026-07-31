# BURAQ Smart Attendance v5.2 — Guided Flow

Railway-ready WhatsApp attendance update.

## New flow

1. Register → Staff ID → Confirm
2. Admin approval হলে employee-কে automatic WhatsApp message
3. Employee selfie পাঠালে face reference media ID save
4. Bot automatic Attendance Menu দেখায়
5. Check In/Out → Send Location button → selfie → attendance save → menu
6. মাঝপথে বন্ধ হলে conversation state database-এ থাকে

## Railway update

পুরোনো repository files replace করে commit/push করুন। Railway নিজে redeploy করবে। PostgreSQL data delete হবে না; নতুন tables startup-এ auto-create হবে।

Recommended commit:

`Add guided location and selfie attendance flow`

## Railway Variables

Strict office radius check চালু করতে Railway Variables-এ দিন:

- `OFFICE_LATITUDE`
- `OFFICE_LONGITUDE`
- `OFFICE_RADIUS_METERS=150`

Latitude/longitude না দিলে location step গ্রহণ হবে, কিন্তু radius enforcement হবে না।

## Important limitation

এই version selfie media ID evidence হিসেবে save করে এবং registration reference রাখে। বাস্তব biometric face matching/liveness এখনো যোগ করা হয়নি; সেটি পরের Face AI module।
