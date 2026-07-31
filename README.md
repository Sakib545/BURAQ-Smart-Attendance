# BURAQ Smart Attendance v6.2 — Live Selfie Challenge

## নতুন নিরাপত্তা

- Check In / Check Out location সফল হলে random live challenge দেয়:
  - সোজা সামনে তাকান
  - মাথা সামান্য বাম দিকে ঘুরান
  - মাথা সামান্য ডান দিকে ঘুরান
- Challenge ২ মিনিটের মধ্যে সম্পন্ন করতে হয়।
- Face AI challenge pose, registered face এবং office location—তিনটি verify করে।
- ভুল pose, অন্য মুখ, একাধিক মুখ, ঝাপসা ছবি বা expired challenge reject হয়।
- Successful attendance message-এ Live Challenge Verified দেখায়।

## গুরুত্বপূর্ণ সীমাবদ্ধতা

WhatsApp Cloud API নির্ভরযোগ্যভাবে জানায় না একটি image Camera থেকে এসেছে নাকি Gallery থেকে। তাই gallery upload সরাসরি ১০০% block করা সম্ভব নয়। এই build random pose + ২ মিনিট expiry ব্যবহার করে পুরোনো/static gallery photo দিয়ে attendance দেওয়া অনেক কঠিন করে। আরও শক্তিশালী security-এর জন্য পরবর্তী ধাপে short-video blink/head-turn liveness যোগ করা যাবে।

## Railway deploy

পুরোনো repository-এর files এই project-এর files দিয়ে replace করে deploy করুন। Existing PostgreSQL data delete হবে না।

Required variables:

```text
OFFICE_LATITUDE=25.18892481916644
OFFICE_LONGITUDE=89.87014577946071
OFFICE_RADIUS_METERS=100
```
