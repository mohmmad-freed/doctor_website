# ERROR_MESSAGES.md

## Purpose

This document defines the standard user-facing error and success messages used across the platform.

The goal is to ensure that:

- all messages shown to users are consistent
- all user-facing messages are written in Arabic
- developers reuse standardized messages instead of writing new ones each time

This improves:

- user experience
- translation consistency
- maintainability of the system

---

# Language Policy

All **user-facing messages must be written in Arabic**.

The platform primarily targets Arabic-speaking users, therefore:

- validation errors must be Arabic
- system errors shown in UI must be Arabic
- success messages must be Arabic

Internal logs, developer messages, and system debugging messages may remain in English.

---

# Doctor Invitation Errors

## Doctor Already in Clinic


"هذا الطبيب عضو بالفعل في العيادة."


---

## Pending Invitation Exists


"توجد دعوة معلقة لهذا الطبيب بالفعل."


---

## Daily Invitation Limit Reached


"تم الوصول إلى الحد اليومي للدعوات."


---

## Doctor Invitation Limit Reached


"تمت دعوة هذا الطبيب عدة مرات مؤخرًا. يرجى المحاولة لاحقًا."


---

## Resend Cooldown Active


"يرجى الانتظار قبل إعادة إرسال الدعوة."


---

## Maximum Resend Attempts Reached


"تم الوصول إلى الحد الأقصى لإعادة إرسال الدعوة."


---

# Input Validation Errors

## Invalid Phone Number


"يرجى إدخال رقم هاتف صحيح."


---

## Invalid Email Address


"يرجى إدخال بريد إلكتروني صحيح."


---

## Doctor Name Required


"اسم الطبيب لا يمكن أن يكون فارغًا."


---

## Specialty Required


"يرجى اختيار تخصص واحد على الأقل."


---

# Invitation State Errors

## Invitation Expired


"انتهت صلاحية هذه الدعوة."


---

## Invitation Cancelled


"تم إلغاء هذه الدعوة."


---

## Invitation Invalid


"هذه الدعوة لم تعد صالحة."


---

# Authorization Errors

## Permission Denied


"ليس لديك صلاحية لتنفيذ هذا الإجراء."


---

## Unauthorized Invitation Action


"غير مصرح لك بإرسال دعوات أطباء."


---

# Doctor Verification Errors

## Doctor Not Verified


"لا يمكن استخدام الحساب حتى يتم توثيق الطبيب."


---

## Verification Rejected


"تم رفض الوثائق المرفوعة. يرجى رفع وثائق صحيحة."


---

# Appointment Errors

## Appointment Conflict


"لا يمكن حجز هذا الموعد بسبب وجود تضارب في المواعيد."


---

## Appointment Not Available


"هذا الموعد غير متاح."


---

## Doctor Not Available


"الطبيب غير متاح في هذا الوقت."


---

# Success Messages

## Invitation Sent


"تم إرسال الدعوة بنجاح."


---

## Invitation Accepted


"تم قبول الدعوة بنجاح."


---

## Invitation Rejected


"تم رفض الدعوة."


---

## Invitation Cancelled


"تم إلغاء الدعوة."


---

# Developer Usage Recommendation

Developers should avoid hardcoding messages directly in business logic.

Instead, messages should be centralized in a constants file.

Example:

```python
ERROR_MESSAGES = {
    "INVALID_PHONE": "يرجى إدخال رقم هاتف صحيح.",
    "INVALID_EMAIL": "يرجى إدخال بريد إلكتروني صحيح.",
    "SPECIALTY_REQUIRED": "يرجى اختيار تخصص واحد على الأقل.",
    "DOCTOR_ALREADY_IN_CLINIC": "هذا الطبيب عضو بالفعل في العيادة.",
    "PENDING_INVITATION_EXISTS": "توجد دعوة معلقة لهذا الطبيب بالفعل.",
}