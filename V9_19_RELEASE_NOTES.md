# BURAQ v9.19 — UI Polish & Face AI Tuning

- Global UI consistency, keyboard focus states, responsive forms, sticky readable tables.
- Stricter blur and brightness validation for attendance selfies.
- Configurable face match and quality thresholds.
- Duplicate detection prioritizes image hashes; identity embedding alone cannot reject a genuine new selfie.
- Near-duplicate comparisons are scoped to the same employee, while exact reused hashes remain checked globally.
- No database migration or reset. Existing records and Railway variables are preserved.
