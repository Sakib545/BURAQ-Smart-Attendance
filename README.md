# BURAQ Smart Attendance v6.0 — Face AI

## নতুন কী আছে
- ৩টি registration selfie থেকে employee-specific Face AI profile
- Attendance selfie-এর মুখ registered employee-এর মুখের সঙ্গে মিলিয়ে দেখা
- অন্য ব্যক্তির selfie হলে attendance reject
- একাধিক মুখ, মুখ না পাওয়া, খুব দূরের/low-resolution ছবি reject
- WhatsApp image Graph API থেকে securely download করে processing
- GPS office-radius verification আগের মতো চালু
- Professional dashboard refresh এবং Employee page-এ Face AI 0/3–3/3 status
- Admin-এর জন্য Reset Face button

## Railway Update
পুরোনো repository-এর files replace করে deploy করুন। PostgreSQL data delete হবে না। প্রথম build-এ OpenCV face models download হবে, তাই build কিছুটা বেশি সময় নিতে পারে।

Variables:
```
OFFICE_LATITUDE=25.18892481916644
OFFICE_LONGITUDE=89.87014577946071
OFFICE_RADIUS_METERS=100
```

## গুরুত্বপূর্ণ migration note
v5.2-এর পুরোনো single selfie শুধু media ID ছিল; biometric embedding ছিল না। তাই existing employees প্রথম Check In-এ ৩টি নতুন selfie দিয়ে Face Registration সম্পন্ন করবে। Admin চাইলে Employees page থেকে `Reset Face` চাপতে পারবেন।

## Security note
Face matching এখন বাস্তব AI embedding comparison করে। এটি অন্য মানুষের selfie আটকায়। তবে একটি স্থির printed/photo-screen attack শতভাগ আটকাতে active liveness challenge প্রয়োজন; সেটি আলাদা camera/video challenge feature।
