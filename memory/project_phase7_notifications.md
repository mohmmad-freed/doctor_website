---
name: phase7_notifications
description: Phase 7 notification center implementation — what was built and where
type: project
---

Phase 7 — Notification Center — implemented 2026-03-17.

**Why:** Users needed an in-app notification inbox; the backend AppointmentNotification model (with is_read field) already existed but had no UI layer.

**What was built:**
- `appointments/context_processors.py` — `unread_notifications()` — injects `unread_notification_count` globally
- `appointments/notification_views.py` — 3 views: `notifications_center`, `mark_notification_read`, `mark_all_notifications_read`
- `appointments/urls.py` — 3 new URL patterns under `/appointments/notifications/`
- Two notification center templates:
  - `appointments/templates/appointments/notifications_center_patient.html` — extends patients/base_dashboard.html (Tailwind)
  - `appointments/templates/appointments/notifications_center_staff.html` — extends accounts/base.html (CSS variables)
- Bell badge added to `patients/templates/patients/base_dashboard.html` navbar + mobile glass nav
- Bell badge added to `accounts/templates/accounts/navbar.html`
- Recent notifications panel (last 5) added to `patients/templates/patients/dashboard.html`
- `patients/views.py` dashboard view passes `recent_notifications` context
- `clinic_website/settings.py` — context processor registered
- `appointments/tests/test_notifications.py` — full test suite for visibility, read/unread, cross-user safety, linking

**How to apply:** When working on notification-related features, all of the above files are relevant. The `patient` FK on AppointmentNotification is actually the **recipient** (not just patients — staff also receive notifications via this field).
