# Secretary Workspace — Implementation Plan

> Created: 2026-04-09  
> Based on: SECRETARY_ANALYSIS.md  
> Rule: Do NOT create any code until this plan is approved.

---

## SECTION A — MODULE VIABILITY ASSESSMENT

### ✅ FULLY SUPPORTED (zero schema changes)
| Module | Reason |
|---|---|
| Dashboard (core stats) | `Appointment` has date + status + clinic + doctor. `AppointmentType.price` enables revenue. `AppointmentNotification` enables activity feed. |
| Appointment status flow | `Appointment.status` already has all 7 states: PENDING→CONFIRMED→CHECKED_IN→IN_PROGRESS→COMPLETED/NO_SHOW/CANCELLED |
| Appointment list / search / filter | Existing model queries only |
| Appointment calendar (day/week) | Pure query — group appointments by date/time/doctor |
| Appointment reschedule | Update `appointment_date` + `appointment_time` fields |
| Conflict detection | Query: does another appointment exist for (doctor, date, time overlap)? Logic only |
| Walk-in registration | Create appointment with `status=CHECKED_IN` directly |
| Patient list (clinic roster) | `ClinicPatient` + `PatientProfile` + `CustomUser` — all exist |
| Patient profile view | All fields already in `PatientProfile` + `CustomUser` + related models |
| Patient search (phone / name / national_id) | All fields exist on `CustomUser` + `PatientProfile` |
| Patient edit (demographics) | `PatientProfile` + `CustomUser` — updateable |
| Waiting room board | Query CONFIRMED + CHECKED_IN appointments for today |
| Waiting room TV display | Same query, different template |
| Doctor schedule view (read-only) | `DoctorAvailability` + `ClinicWorkingHours` — read existing data |
| Doctor availability exceptions | `DoctorAvailabilityException` already exists |
| Appointment reminders (in-app) | `AppointmentNotification` with `notification_type=REMINDER` |
| SMS reminders | TweetsMS already integrated via `accounts/otp_utils.py` |
| Reports (daily, no-show, doctor utilization) | Pure Appointment queries, no new tables |
| Notification inbox | `AppointmentNotification` filtered by `context_role=SECRETARY` |
| Secretary profile view/edit | `CustomUser` fields |
| Time slot picker | `DoctorAvailability` + existing appointment overlap query |

### ⚠️ SUPPORTED WITH MINOR SAFE MIGRATIONS
| Module | Required Migration | Risk |
|---|---|---|
| Check-in with timestamp | Add `checked_in_at DateTimeField(null=True)` to `Appointment` | Zero — additive, nullable |
| Cancel with dedicated reason field | Add `cancellation_reason TextField(blank=True)` to `Appointment` | Zero — additive, blank |
| Patient file number | Add `file_number CharField(max_length=20, blank=True)` to `ClinicPatient` | Zero — additive, blank/null, auto-generated on creation |
| Billing — invoices | New models `Invoice`, `InvoiceItem`, `Payment` in `secretary/models.py` | Low — brand new tables, no existing table changes |
| Revenue report | Depends on `Invoice`/`Payment` models above | Blocked on billing migration |

### ❌ SKIPPED — REASONS DOCUMENTED
| Feature | Reason to Skip |
|---|---|
| **Recurring appointments** | No recurrence fields (`rrule`, `parent_appointment`, `recurrence_end`) anywhere. Adding a proper recurrence engine is a large subsystem — out of scope. |
| **Waiting list (formal queue model)** | A `WaitingList` model is unnecessary — a filtered view of PENDING/CONFIRMED appointments for today IS the waiting list. No schema needed. |
| **Patient merge** | Requires deduplication logic across 8+ related tables (appointments, prescriptions, records, orders, notifications). High data-integrity risk. Not a day-1 secretary feature. |
| **Patient file number (auto-gen)** | Partially supported — adding the field is safe; the auto-generation logic (e.g. YYYY-NNNN) needs a service. Included in minor migrations. |
| **Insurance claim tracking** | No insurance tables exist anywhere. Would require a full insurance subsystem (insurer catalog, claim numbers, coverage rules). Skip entirely. |
| **PDF receipt generation** | No PDF library in requirements (no reportlab, weasyprint, xhtml2pdf). Out of scope — use print-friendly HTML instead. |
| **Internal doctor↔secretary messaging** | No `Message` or `Thread` model. Would require a chat subsystem. Skip. |
| **Shift / rota management** | No `Shift` or `Rota` model. Secretary personal working hours not tracked. Out of scope. |
| **Quick settings (default view, default duration)** | Minor quality-of-life feature. Deferred — `localStorage`-based defaults in JS are sufficient without a DB table. |
| **Patient merge** | (repeated for emphasis) Risk of data corruption without thorough audit trail. Skip. |

---

## SECTION B — REQUIRED MIGRATIONS

Three migration groups must be created **before** any view/template work begins.

### Migration Group 1: `appointments` app — add tracking fields

**File:** `appointments/migrations/XXXX_add_checkin_cancel_fields.py`

```python
# On Appointment model:
checked_in_at = models.DateTimeField(null=True, blank=True)
# Set automatically when status changes to CHECKED_IN
# Used for: wait time calculation, queue order, reporting

cancellation_reason = models.TextField(blank=True, default="")
# Set when secretary/patient cancels
# Used for: cancellation reports, audit trail
```

**Impact:** Zero. Both fields are additive and nullable/blank. No data migration needed.

---

### Migration Group 2: `patients` app — patient file number

**File:** `patients/migrations/XXXX_add_file_number_to_clinicpatient.py`

```python
# On ClinicPatient model:
file_number = models.CharField(max_length=20, blank=True, default="")
# Auto-generated format: YYYY-NNNN (e.g. 2026-0001 per clinic)
# Generated in: patients/services.py::ensure_patient_profile()
# or on ClinicPatient.save() override
```

**Impact:** Zero. Additive, blank default. Existing rows get empty string.  
**Note:** Auto-generation logic: `f"{year}-{clinic_patient_count_this_year:04d}"` — computed in service, not DB constraint.

---

### Migration Group 3: `secretary` app — billing models

**File:** `secretary/migrations/0001_billing.py`

Three new tables — all brand new, no existing table modified.

#### `secretary_invoice` (Invoice)
```python
class Invoice(models.Model):
    STATUS = [("DRAFT","Draft"),("ISSUED","Issued"),("PAID","Paid"),
              ("PARTIAL","Partial"),("CANCELLED","Cancelled"),("REFUNDED","Refunded")]

    clinic         = ForeignKey(Clinic, on_delete=CASCADE)
    patient        = ForeignKey(CustomUser, on_delete=PROTECT, related_name="invoices")
    appointment    = ForeignKey(Appointment, null=True, blank=True,
                                on_delete=SET_NULL, related_name="invoices")
    invoice_number = CharField(max_length=30, unique=True)
    # Format: INV-YYYY-NNNNNN (e.g. INV-2026-000001) — auto on create
    status         = CharField(max_length=20, choices=STATUS, default="DRAFT")
    subtotal       = DecimalField(max_digits=10, decimal_places=2, default=0)
    discount       = DecimalField(max_digits=10, decimal_places=2, default=0)
    total          = DecimalField(max_digits=10, decimal_places=2, default=0)
    amount_paid    = DecimalField(max_digits=10, decimal_places=2, default=0)
    balance_due    = DecimalField(max_digits=10, decimal_places=2, default=0)
    notes          = TextField(blank=True)
    created_by     = ForeignKey(CustomUser, on_delete=PROTECT, related_name="created_invoices")
    created_at     = DateTimeField(auto_now_add=True)
    updated_at     = DateTimeField(auto_now=True)
    issued_at      = DateTimeField(null=True, blank=True)
    paid_at        = DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [("clinic","status"), ("clinic","created_at"), ("patient",)]
```

#### `secretary_invoiceitem` (InvoiceItem)
```python
class InvoiceItem(models.Model):
    invoice        = ForeignKey(Invoice, on_delete=CASCADE, related_name="items")
    description    = CharField(max_length=255)
    # Free-text description (copied from AppointmentType.name at invoice time)
    appointment_type = ForeignKey(AppointmentType, null=True, blank=True,
                                  on_delete=SET_NULL)
    quantity       = PositiveIntegerField(default=1)
    unit_price     = DecimalField(max_digits=10, decimal_places=2)
    total          = DecimalField(max_digits=10, decimal_places=2)
    # total = quantity * unit_price (computed on save)
```

#### `secretary_payment` (Payment)
```python
class Payment(models.Model):
    METHOD = [("CASH","نقدي"),("CARD","بطاقة"),("TRANSFER","تحويل"),("OTHER","أخرى")]

    invoice        = ForeignKey(Invoice, on_delete=CASCADE, related_name="payments")
    clinic         = ForeignKey(Clinic, on_delete=CASCADE)
    amount         = DecimalField(max_digits=10, decimal_places=2)
    method         = CharField(max_length=20, choices=METHOD, default="CASH")
    reference      = CharField(max_length=100, blank=True)
    # For card: last 4 digits. For transfer: reference number.
    notes          = TextField(blank=True)
    received_by    = ForeignKey(CustomUser, on_delete=PROTECT, related_name="received_payments")
    received_at    = DateTimeField(auto_now_add=True)
```

---

## SECTION C — COMPLETE FILE STRUCTURE

Every file that will be created or modified, organized by module.

```
secretary/
│
├── models.py                         [MODIFY] Add Invoice, InvoiceItem, Payment
├── forms.py                          [CREATE] All secretary-specific forms
├── services.py                       [CREATE] Business logic layer
├── urls.py                           [MODIFY] Add ~25 new URL patterns
├── views.py                          [MODIFY] Add ~30 new view functions
├── context_processors.py             [KEEP as-is]
│
├── migrations/
│   └── 0001_billing.py               [CREATE] Invoice, InvoiceItem, Payment tables
│
└── templates/secretary/
    │
    ├── base_secretary.html           [MODIFY] Add new nav links, update mobile nav
    ├── dashboard.html                [MODIFY] Richer stats, doctor status, activity
    │
    ├── appointments/
    │   ├── list.html                 [RENAME + ENHANCE] was appointments_list.html
    │   ├── calendar.html             [CREATE] Day/week calendar with doctor filter
    │   ├── detail.html               [CREATE] Single appointment full view
    │   ├── create.html               [RENAME + ENHANCE] was create_appointment.html
    │   └── edit.html                 [RENAME + ENHANCE] was edit_appointment.html
    │
    ├── patients/
    │   ├── list.html                 [CREATE] Clinic patient roster with search
    │   ├── detail.html               [CREATE] Patient full profile page
    │   ├── register.html             [RENAME] was register_patient.html
    │   └── edit.html                 [CREATE] Edit patient demographics
    │
    ├── waiting_room/
    │   ├── board.html                [CREATE] Secretary's queue management board
    │   └── display.html             [CREATE] TV/kiosk display for patients
    │
    ├── billing/
    │   ├── invoices.html             [CREATE] Invoice list with filters
    │   ├── invoice_detail.html       [CREATE] Single invoice (print-friendly)
    │   ├── invoice_create.html       [CREATE] Create invoice (from appt or manual)
    │   └── daily_summary.html        [CREATE] End-of-day cash register summary
    │
    ├── reports/
    │   ├── index.html                [CREATE] Reports hub with quick stats
    │   ├── daily.html                [CREATE] Daily appointment breakdown
    │   ├── noshows.html              [CREATE] No-show & cancellation report
    │   ├── revenue.html              [CREATE] Revenue collection report
    │   └── doctors.html              [CREATE] Doctor utilization / load report
    │
    ├── settings/
    │   └── profile.html              [CREATE] Secretary profile view + edit
    │
    └── htmx/
        ├── patient_search_results.html  [KEEP — enhance]
        ├── patient_card.html            [KEEP — enhance]
        ├── time_slots.html              [CREATE] Available time slots grid
        ├── appointment_status_chip.html [CREATE] Status badge after HTMX update
        ├── waiting_room_row.html        [CREATE] Single patient row in queue
        ├── doctor_status_cards.html     [CREATE] Today's doctor availability strip
        ├── invoice_row.html             [CREATE] Single invoice row after creation
        └── calendar_appointments.html   [CREATE] Appointment blocks for calendar day
```

**Other app file changes:**

```
appointments/
└── migrations/
    └── XXXX_add_checkin_cancel_fields.py   [CREATE]

patients/
└── migrations/
    └── XXXX_add_file_number_clinicpatient.py [CREATE]
```

---

## SECTION D — MODULE-BY-MODULE SPECIFICATION

---

### MODULE 1 — DASHBOARD

**Files:** `secretary/views.py::dashboard()` (enhance), `secretary/templates/secretary/dashboard.html` (enhance), `htmx/doctor_status_cards.html` (new)

**URL:** `GET /secretary/` (existing)

**Page Sections:**

#### 1.1 — Stats Bar (top row, 5 cards)
Query: `Appointment.objects.filter(clinic=request.clinic, appointment_date=today)`

| Card | Value | Color |
|---|---|---|
| إجمالي المواعيد | count all | Gray |
| في الانتظار | PENDING + CONFIRMED count | Yellow |
| تم تسجيل الوصول | CHECKED_IN count | Blue |
| قيد التنفيذ | IN_PROGRESS count | Purple |
| مكتمل | COMPLETED count | Green |

Revenue card (6th, only if billing enabled — `Invoice` model exists):
- `Payment.objects.filter(clinic=..., received_at__date=today).aggregate(Sum('amount'))`

#### 1.2 — Upcoming Appointments (next 2 hours)
Query: `appointment_date=today, appointment_time__range=(now, now+2h), status__in=[CONFIRMED, CHECKED_IN]`  
Display: compact table — time | patient name | doctor | appointment type | status badge  
Limit: 8 rows, "عرض الكل" link to full list

#### 1.3 — Doctor Status Strip (HTMX, auto-refreshes every 60s)
For each doctor in clinic (`ClinicStaff.role=DOCTOR, is_active=True`):
- Check `DoctorAvailability` for today's weekday → is doctor scheduled?
- Check `DoctorAvailabilityException` covering today → is doctor blocked?
- Check `Appointment` with `status=IN_PROGRESS` for this doctor → currently with patient?
- Status: 🟢 متاح | 🔴 مع مريض | ⚫ غير متاح اليوم
- Template: `htmx/doctor_status_cards.html`
- HTMX: `hx-get="/secretary/htmx/doctor-status/" hx-trigger="load, every 60s"`

#### 1.4 — Recent Activity Feed
Source: `AppointmentNotification.objects.filter(context_role=SECRETARY, appointment__clinic=clinic).order_by('-id')[:10]`  
Display: icon + message + relative timestamp (e.g. "منذ 5 دقائق")  
Activity types mapped to icons (booked=calendar, cancelled=x, status_changed=arrow, etc.)

#### 1.5 — Quick Action Buttons (3 prominent buttons)
- `+ موعد جديد` → `/secretary/appointments/create/`
- `+ تسجيل مريض` → `/secretary/patients/register/`
- `تسجيل الوصول` → `/secretary/waiting-room/`

**New HTMX endpoint:**
```
GET /secretary/htmx/doctor-status/   → doctor_status_htmx()
```

---

### MODULE 2 — APPOINTMENT MANAGEMENT

#### 2.1 — Appointment List (enhanced)

**File:** `secretary/templates/secretary/appointments/list.html`  
**URL:** `GET /secretary/appointments/`  
**View:** `appointments_list()` (enhance existing)

**Filters (form, GET params):**
- `status` — multi-select (all statuses as checkboxes, default: all active)
- `date` — date picker (default: today)
- `doctor_id` — dropdown of clinic doctors
- `search` — free-text (patient name or phone)

**Table columns:**
| # | الوقت | المريض | الطبيب | الخدمة | الحالة | الإجراءات |
|---|---|---|---|---|---|---|
- Status: colored badge, HTMX-clickable to change
- Actions: تفاصيل | تعديل | إلغاء (contextual based on status)
- Checked-in quick button: if status=CONFIRMED → show "تسجيل الوصول" HTMX button

**Pagination:** simple prev/next, 25 per page

---

#### 2.2 — Appointment Detail Page

**File:** `secretary/templates/secretary/appointments/detail.html`  
**URL:** `GET /secretary/appointments/<id>/`  
**View:** `appointment_detail()` (new)

**Sections:**
- Header: patient name + appointment date/time + status badge
- Patient info card: phone, national_id, file_number, gender, DOB
- Appointment info: doctor, type, duration, price, reason, notes
- Status timeline: visual stepper showing journey (PENDING→CONFIRMED→CHECKED_IN→...)
- **Status action buttons** (contextual):
  - PENDING → `تأكيد الموعد` (→CONFIRMED), `إلغاء`
  - CONFIRMED → `تسجيل الوصول` (→CHECKED_IN), `إعادة الجدولة`, `إلغاء`
  - CHECKED_IN → `إرسال للطبيب` (→IN_PROGRESS), `إلغاء`
  - IN_PROGRESS → `اكتمل` (→COMPLETED), `لم يحضر` (→NO_SHOW)
- Intake form answers (read-only display)
- Linked invoice (if billing): amount, paid status, `إنشاء فاتورة` if none exists
- Cancellation reason (if cancelled): shown in red alert

**HTMX status update (inline, no page reload):**
```
POST /secretary/appointments/<id>/status/
Body: { status: "CHECKED_IN" }
Returns: updated status chip HTML
```

---

#### 2.3 — Appointment Create (enhanced)

**File:** `secretary/templates/secretary/appointments/create.html`  
**URL:** `GET/POST /secretary/appointments/create/`  
**View:** `create_appointment()` (enhance existing)

**Form flow (3 steps, single page with JS show/hide):**

**Step 1 — Patient Selection**
- Phone input with HTMX live search (existing `patient_search_htmx`)
- Results: name, phone, file_number, last visit date
- "تسجيل مريض جديد" link if not found

**Step 2 — Appointment Details**
- Doctor dropdown (only doctors active in this clinic)
- Appointment type dropdown (types active for selected doctor)
  - HTMX: doctor change triggers type reload
  - Shows: name, duration, price
- Date picker: only allows working days (`ClinicWorkingHours`)
- Time slot grid (HTMX): `GET /secretary/htmx/time-slots/?doctor=X&date=Y&type=Z`
  - Returns: grid of available 15-min slots
  - Red = occupied, Green = available
  - Respects: DoctorAvailability hours, existing appointments + duration, ClinicHoliday, DoctorAvailabilityException
- Conflict detection: if selected slot overlaps, show warning

**Step 3 — Notes & Confirm**
- Reason for visit (text area)
- Notes (text area)
- Initial status: PENDING (default) or CHECKED_IN (walk-in checkbox)
- Submit → conflict check server-side → save or show error

**New HTMX endpoints:**
```
GET  /secretary/htmx/time-slots/       → get_time_slots_htmx()
GET  /secretary/htmx/doctor-types/     → get_doctor_types_htmx()
```

---

#### 2.4 — Appointment Edit

**File:** `secretary/templates/secretary/appointments/edit.html`  
**URL:** `GET/POST /secretary/appointments/<id>/edit/`  
**View:** `edit_appointment()` (enhance existing)

Same form as create, pre-populated. Allow changes only if `status in [PENDING, CONFIRMED]`.  
Conflict detection runs again on save. If date/time changed, old slot freed automatically.

---

#### 2.5 — Appointment Status Flow (HTMX)

**URL:** `POST /secretary/appointments/<id>/status/`  
**View:** `update_appointment_status_htmx()` (new)  
**Template:** `htmx/appointment_status_chip.html` (new)

Logic:
```
PENDING    → allowed transitions: CONFIRMED, CANCELLED
CONFIRMED  → allowed transitions: CHECKED_IN, CANCELLED
CHECKED_IN → allowed transitions: IN_PROGRESS, CANCELLED
IN_PROGRESS → allowed transitions: COMPLETED, NO_SHOW
```
- Validates transition is legal
- Sets `checked_in_at = now()` when transitioning to CHECKED_IN
- Creates `AppointmentNotification` for patient on status change
- Returns updated status chip HTML for HTMX swap

---

#### 2.6 — Appointment Cancellation

**URL:** `POST /secretary/appointments/<id>/cancel/`  
**View:** `cancel_appointment()` (enhance existing — add reason field)

- Accepts `cancellation_reason` in POST body
- Saves to `appointment.cancellation_reason`
- Creates APPOINTMENT_CANCELLED notification for patient
- Redirects back with success message

---

#### 2.7 — Reschedule

**URL:** `GET/POST /secretary/appointments/<id>/reschedule/`  
**View:** `reschedule_appointment()` (new — reuses edit form with limited fields)

Shows only: date picker + time slot picker. Pre-fills with current date/time.  
Creates RESCHEDULED notification for patient on save.

---

#### 2.8 — Walk-in Registration

Handled in create form via checkbox: "مريض حاضر الآن (بدون موعد مسبق)"  
Sets `status=CHECKED_IN` and `checked_in_at=now()` on create.  
Adds directly to waiting room queue.

---

#### 2.9 — Calendar View

**File:** `secretary/templates/secretary/appointments/calendar.html`  
**URL:** `GET /secretary/appointments/calendar/`  
**View:** `calendar_view()` (new)

**Two modes (tabs):**

**Day view (default):**
- Time column (07:00–22:00, 30-min rows)
- Doctor columns (one column per active doctor today)
- Appointment blocks: patient name + type + status color
- Click block → detail page
- HTMX navigation: `← اليوم → ` triggers day reload
- HTMX: `GET /secretary/htmx/calendar-day/?date=YYYY-MM-DD&doctor=all`

**Week view:**
- 7-day grid (current week)
- Each cell: count of appointments for that day
- Click day → goes to day view for that day
- Doctor filter dropdown

**HTMX endpoints:**
```
GET /secretary/htmx/calendar-day/    → calendar_day_htmx()
GET /secretary/htmx/calendar-week/   → calendar_week_htmx()
```

---

### MODULE 3 — PATIENT MANAGEMENT

#### 3.1 — Patient List

**File:** `secretary/templates/secretary/patients/list.html`  
**URL:** `GET /secretary/patients/`  
**View:** `patient_list()` (new)

Query: `ClinicPatient.objects.filter(clinic=request.clinic).select_related('patient__patientprofile')`

**Search bar** (HTMX live search, 300ms debounce):
- Fields: patient name, phone, national_id, file_number
- URL: `GET /secretary/patients/search/?q=...`
- Returns: partial table rows

**Table columns:**
| رقم الملف | الاسم | الهاتف | الجنس | آخر زيارة | إجمالي الزيارات | الإجراءات |
|---|---|---|---|---|---|---|
- آخر زيارة: last COMPLETED appointment date
- إجمالي الزيارات: count COMPLETED appointments
- Actions: عرض الملف | موعد جديد | تعديل

**Pagination:** 20 per page  
**Export to print:** print-friendly table view (CSS `@media print`)

---

#### 3.2 — Patient Detail / Profile

**File:** `secretary/templates/secretary/patients/detail.html`  
**URL:** `GET /secretary/patients/<patient_id>/`  
**View:** `patient_detail()` (new)

**Tab 1 — البيانات الشخصية:**
- Full name, phone, national_id, file_number, city
- DOB, age (computed), gender, blood type
- Medical history, allergies (read-only)
- Emergency contact name + phone
- Edit button → `/secretary/patients/<id>/edit/`

**Tab 2 — المواعيد:**
- All appointments for this patient at THIS clinic
- Table: date, doctor, type, status, actions (view detail, create new)
- Sorted: newest first
- Filter: status dropdown
- "موعد جديد" button pre-fills patient in create form

**Tab 3 — السجلات الطبية (read-only):**
- `MedicalRecord.objects.filter(patient=patient, clinic=clinic)`
- Shows: title, category, date, uploaded_by
- NO upload from secretary (doctor privilege only)

**Tab 4 — الفواتير (if billing enabled):**
- `Invoice.objects.filter(patient=patient, clinic=clinic)`
- Outstanding balance (sum of balance_due)
- "إنشاء فاتورة" button

---

#### 3.3 — Patient Registration (enhanced)

**File:** `secretary/templates/secretary/patients/register.html`  
**URL:** `GET/POST /secretary/patients/register/`  
**View:** `register_patient()` (enhance existing)

**Form fields (enhanced from current):**
- Phone (required, with format validator)
- Full name (required)
- National ID (optional)
- Date of birth (optional, date picker)
- Gender (radio: ذكر / أنثى)
- Blood type (optional, dropdown)
- City (optional, dropdown from `City` model)
- Medical history (optional, textarea)
- Allergies (optional, textarea)
- Emergency contact name + phone (optional)

**On submit:**
1. Check if user with phone already exists
2. If yes: add to clinic (`ClinicPatient`) with the existing user + generate/keep file_number
3. If no: create `CustomUser` (role=PATIENT, roles=["PATIENT"]) + `PatientProfile` + `ClinicPatient`
4. Auto-generate `file_number`: format `{YYYY}-{N:04d}` (count of ClinicPatient for this clinic this year + 1)
5. Redirect to patient detail page with success message

---

#### 3.4 — Patient Edit

**File:** `secretary/templates/secretary/patients/edit.html`  
**URL:** `GET/POST /secretary/patients/<patient_id>/edit/`  
**View:** `edit_patient()` (new)

Same fields as registration form, pre-populated.  
Secretary can edit: demographics, contact info, emergency contact, notes.  
Secretary CANNOT edit: phone (primary identity key), national_id, medical history (doctor's domain).  
Redirect to patient detail on save.

---

### MODULE 4 — CHECK-IN & WAITING ROOM

#### 4.1 — Waiting Room Board (Secretary View)

**File:** `secretary/templates/secretary/waiting_room/board.html`  
**URL:** `GET /secretary/waiting-room/`  
**View:** `waiting_room_board()` (new)

**Layout:** Two columns

**Column A — في الانتظار (CONFIRMED):**
- Today's CONFIRMED appointments, ordered by appointment_time
- Each row: position number | time | patient name | doctor | type | minutes until slot
- Actions: `تسجيل الوصول` (HTMX → CHECKED_IN), `إلغاء`

**Column B — وصل / قيد الانتظار الفعلي (CHECKED_IN):**
- Today's CHECKED_IN appointments, ordered by `checked_in_at`
- Each row: position in queue | patient name | doctor | wait time (now - checked_in_at) | status
- Actions: `استدعاء` (→IN_PROGRESS, HTMX), `إعادة ترتيب`
- Wait time: highlighted red if > appointment_type.duration_minutes * 1.5

**Auto-refresh:** HTMX polling every 30s on both columns
```
GET /secretary/htmx/waiting-room-confirmed/  → waiting_room_confirmed_htmx()
GET /secretary/htmx/waiting-room-checkedin/  → waiting_room_checkedin_htmx()
```

---

#### 4.2 — Check-in Flow

**URL:** `POST /secretary/appointments/<id>/checkin/`  
**View:** `checkin_appointment()` (new, HTMX)

1. Confirm appointment exists + belongs to clinic
2. Confirm status is CONFIRMED (or PENDING for walk-in)
3. Set `status = CHECKED_IN`
4. Set `checked_in_at = now()`
5. Create AppointmentNotification (STATUS_CHANGED for SECRETARY context)
6. Return HTMX partial: updated row in waiting room OR redirect to waiting room

---

#### 4.3 — TV / Kiosk Display Mode

**File:** `secretary/templates/secretary/waiting_room/display.html`  
**URL:** `GET /secretary/waiting-room/display/`  
**View:** `waiting_room_display()` (new)

**Purpose:** Full-screen, auto-refreshing display for a screen in the waiting room.

**Layout:**
- Clinic name + logo area (top)
- Large table: 
  | المريض | الطبيب | الحالة |
  |---|---|---|
  | اسم المريض (partial — for privacy) | اسم الطبيب | قيد الانتظار / ادخل الآن |
- Shows only CHECKED_IN and IN_PROGRESS appointments
- "ادخل الآن" badge is bold green pulsing for IN_PROGRESS
- Auto-refresh: `<meta http-equiv="refresh" content="20">` (simple, no JS needed)
- Kiosk styling: large font, high contrast, dark background

**Privacy note:** Patient name shown as first name + initial only (e.g. "محمد ع.").

---

### MODULE 5 — BILLING & PAYMENTS

*Depends on Migration Group 3 (Invoice, InvoiceItem, Payment models).*

#### 5.1 — Invoice List

**File:** `secretary/templates/secretary/billing/invoices.html`  
**URL:** `GET /secretary/billing/`  
**View:** `billing_invoices()` (new)

**Filters:** status, date range, patient search  
**Table:** رقم الفاتورة | المريض | التاريخ | الإجمالي | المدفوع | المتبقي | الحالة | إجراءات  
**Summary bar:** إجمالي الإيرادات اليوم | فواتير معلقة | مدفوع اليوم

---

#### 5.2 — Invoice Detail / Print View

**File:** `secretary/templates/secretary/billing/invoice_detail.html`  
**URL:** `GET /secretary/billing/invoices/<id>/`  
**View:** `invoice_detail()` (new)

**Sections:**
- Clinic header (name, address, phone)
- Invoice number + date + status
- Patient details
- Appointment reference (if linked)
- **Items table:** الوصف | الكمية | سعر الوحدة | الإجمالي
- Subtotal → discount → **Total**
- Payments received (list)
- Balance due (highlighted red if > 0)

**Actions:**
- `سجل دفعة` → inline payment form (HTMX modal)
- `إلغاء الفاتورة` (if DRAFT/ISSUED and no payments)
- `طباعة` → `window.print()` (CSS `@media print` hides nav/buttons)

---

#### 5.3 — Invoice Creation

**File:** `secretary/templates/secretary/billing/invoice_create.html`  
**URL:** `GET/POST /secretary/billing/create/`  
         `GET/POST /secretary/billing/appointments/<apt_id>/invoice/` (pre-fill from appointment)  
**View:** `invoice_create()` (new)

**Form:**
- Patient (pre-filled from appointment or searchable)
- Appointment (optional link)
- Items (dynamic add/remove rows via JS):
  - Description (free text or select from AppointmentType catalog)
  - Quantity | Unit Price
  - Total (auto-computed)
- Discount (flat amount or %)
- Notes

**On submit:** auto-generate invoice_number, set status=DRAFT, compute totals.  
**"حفظ وإصدار"** button: set status=ISSUED immediately.

---

#### 5.4 — Payment Recording (HTMX inline modal)

**URL:** `POST /secretary/billing/invoices/<id>/payment/`  
**View:** `record_payment()` (new)  
**Template:** `htmx/payment_form.html` (new)

Form: المبلغ | طريقة الدفع | مرجع (اختياري) | ملاحظات  
On save:
1. Create `Payment` record
2. Update `Invoice.amount_paid += payment.amount`
3. Update `Invoice.balance_due = total - amount_paid`
4. If `balance_due <= 0`: set `Invoice.status = PAID`, set `paid_at = now()`
5. Else if `amount_paid > 0`: set `Invoice.status = PARTIAL`
6. Return updated invoice summary HTML for HTMX swap

---

#### 5.5 — Daily Summary (End of Day)

**File:** `secretary/templates/secretary/billing/daily_summary.html`  
**URL:** `GET /secretary/billing/daily/`  
**View:** `daily_summary()` (new)

**Query:** All payments + appointments for selected date (default: today)

**Sections:**
- Date selector
- Revenue by payment method (cash / card / transfer table)
- Invoice status breakdown (paid / partial / outstanding)
- Appointment count by status
- Total collected today (bold)
- Print button

---

### MODULE 6 — DOCTOR SCHEDULE (Secretary View)

**Secretary has READ + limited WRITE access (add exceptions only, not base availability)**

#### 6.1 — Doctor Schedule View

**File:** `secretary/templates/secretary/` (inline in dashboard or separate page)  
**URL:** `GET /secretary/schedule/`  
**View:** `doctor_schedule()` (new)

**Display:** Weekly grid per doctor
- Rows: days of week (with Arabic names)
- Columns: each doctor in clinic
- Cell: working time range from `DoctorAvailability` or "إجازة" from `DoctorAvailabilityException`
- Appointment count for that day (badge overlay on cell)

**Actions available to secretary:**
- `عرض مواعيد اليوم` → calendar day view filtered by doctor
- `تسجيل غياب` → create `DoctorAvailabilityException` for a date range

#### 6.2 — Block Doctor Time (Exception)

**URL:** `GET/POST /secretary/schedule/block/`  
**View:** `block_doctor_time()` (new)

**Form:** Doctor dropdown | Start date | End date | Reason (dropdown: إجازة / مرض / اجتماع / أخرى)  
On save: creates `DoctorAvailabilityException` with `is_active=True`  
**Note:** Secretary can only ADD exceptions, not modify base `DoctorAvailability` (that is the doctor's own domain).

---

### MODULE 7 — COMMUNICATIONS

#### 7.1 — Appointment Reminder (SMS)

**URL:** `POST /secretary/appointments/<id>/remind/`  
**View:** `send_appointment_reminder()` (new, HTMX)

Trigger: Button on appointment detail page — "إرسال تذكير"

Logic:
1. Check `appointment.reminder_sent` is False (prevent double-sending)
2. Format SMS message: "تذكير: لديك موعد في {clinic.name} يوم {date} الساعة {time} مع {doctor.name}"
3. Call TweetsMS via `accounts/otp_utils.py` send function (reuse SMS infrastructure)
4. Set `appointment.reminder_sent = True`
5. Create `AppointmentNotification` (type=REMINDER)
6. Return HTMX: disable button + "تم الإرسال" text

**Rate limiting:** Cannot send reminder more than once per appointment (enforced by `reminder_sent` flag).

---

#### 7.2 — Notification Inbox

**File:** `secretary/templates/secretary/` (add to nav)  
**URL:** `GET /secretary/notifications/`  
**View:** `notifications_list()` (new)

Query: `AppointmentNotification.objects.filter(context_role=SECRETARY, appointment__clinic=clinic).order_by('-id')`

Display: list of notifications with icon (booking=calendar, cancel=x, status=arrow)  
Each row: icon | title | message excerpt | date | is_read badge  
Click row: marks as read (HTMX POST), links to related appointment  

**Mark all read:**
```
POST /secretary/notifications/mark-all-read/  → mark_all_notifications_read()
```

---

### MODULE 8 — REPORTS

All reports are pure query views — no new models. Secretary-scoped to `request.clinic`.

#### 8.1 — Reports Hub

**File:** `secretary/templates/secretary/reports/index.html`  
**URL:** `GET /secretary/reports/`  
**View:** `reports_index()` (new)

Quick stats for today + 4 report cards with links.

---

#### 8.2 — Daily Appointment Report

**File:** `secretary/templates/secretary/reports/daily.html`  
**URL:** `GET /secretary/reports/daily/`  
**View:** `report_daily()` (new)

**Filter:** date picker (default: today)

**Output:**
- Status breakdown table: status | count | %
- Appointment list: time | patient | doctor | type | status | price
- Total appointments | Total value (sum of appointment_type.price for COMPLETED)
- Doctor breakdown: doctor name | # appointments | # completed | # no-show

**Print-friendly** via CSS `@media print`

---

#### 8.3 — No-Show & Cancellation Report

**File:** `secretary/templates/secretary/reports/noshows.html`  
**URL:** `GET /secretary/reports/noshows/`  
**View:** `report_noshows()` (new)

**Filter:** date range (default: last 30 days), doctor

**Output:**
- No-show rate: count NO_SHOW / total × 100%
- Cancellation rate: count CANCELLED / total × 100%
- Cancellations with reasons (from `cancellation_reason` field)
- Top 5 patients with most no-shows (for follow-up)
- Day-of-week breakdown (which days have most no-shows)

---

#### 8.4 — Revenue Report (requires billing module)

**File:** `secretary/templates/secretary/reports/revenue.html`  
**URL:** `GET /secretary/reports/revenue/`  
**View:** `report_revenue()` (new)

*Only shown in nav if Invoice/Payment models exist.*

**Filter:** date range, doctor

**Output:**
- Total invoiced | Total collected | Outstanding
- Payment method breakdown (cash / card / transfer)
- Daily revenue chart (table format — no JS charting library)
- Top services by revenue (group by InvoiceItem.description)

---

#### 8.5 — Doctor Utilization Report

**File:** `secretary/templates/secretary/reports/doctors.html`  
**URL:** `GET /secretary/reports/doctors/`  
**View:** `report_doctors()` (new)

**Filter:** date range (default: this month), doctor

**Output per doctor:**
- Scheduled slots (from DoctorAvailability × working days in range)
- Booked appointments: count by status
- Utilization %: booked / scheduled slots × 100%
- Average daily appointments
- Most common appointment type

---

### MODULE 9 — SETTINGS & PROFILE

#### 9.1 — Secretary Profile

**File:** `secretary/templates/secretary/settings/profile.html`  
**URL:** `GET/POST /secretary/settings/profile/`  
**View:** `settings_profile()` (new)

**Read section:**
- Name, phone, email, role(s), clinic name, added since (ClinicStaff.added_at)

**Edit form (inline toggle):**
- Name (editable)
- Email (editable via OTP, links to existing `accounts:change_email_otp_request`)
- Phone (links to existing `accounts:change_phone_request`)
- Password (links to existing forgot_password flow)

**Clinic info (read-only):** Clinic name, address, subscription plan expiry (from ClinicSubscription)

---

## SECTION E — URL PATTERNS (complete new list)

All URLs to add to `secretary/urls.py`:

```python
# Dashboard enhancement
path("htmx/doctor-status/",          views.doctor_status_htmx,             name="doctor_status_htmx"),

# Appointments
path("appointments/<int:pk>/",        views.appointment_detail,             name="appointment_detail"),
path("appointments/<int:pk>/status/", views.update_appointment_status_htmx, name="update_status"),
path("appointments/<int:pk>/checkin/",views.checkin_appointment,            name="checkin_appointment"),
path("appointments/<int:pk>/reschedule/", views.reschedule_appointment,     name="reschedule_appointment"),
path("appointments/<int:pk>/remind/", views.send_appointment_reminder,      name="send_reminder"),
path("appointments/calendar/",        views.calendar_view,                  name="calendar"),

# Calendar HTMX
path("htmx/calendar-day/",            views.calendar_day_htmx,              name="calendar_day_htmx"),
path("htmx/calendar-week/",           views.calendar_week_htmx,             name="calendar_week_htmx"),

# Booking helpers HTMX
path("htmx/time-slots/",              views.get_time_slots_htmx,            name="time_slots_htmx"),
path("htmx/doctor-types/",            views.get_doctor_types_htmx,          name="doctor_types_htmx"),

# Patients
path("patients/",                     views.patient_list,                   name="patient_list"),
path("patients/<int:pk>/",            views.patient_detail,                 name="patient_detail"),
path("patients/<int:pk>/edit/",       views.edit_patient,                   name="edit_patient"),

# Waiting room
path("waiting-room/",                 views.waiting_room_board,             name="waiting_room"),
path("waiting-room/display/",         views.waiting_room_display,           name="waiting_room_display"),
path("htmx/waiting-room-confirmed/",  views.waiting_room_confirmed_htmx,    name="waiting_room_confirmed_htmx"),
path("htmx/waiting-room-checkedin/",  views.waiting_room_checkedin_htmx,    name="waiting_room_checkedin_htmx"),

# Billing
path("billing/",                      views.billing_invoices,               name="billing_invoices"),
path("billing/create/",               views.invoice_create,                 name="invoice_create"),
path("billing/appointments/<int:apt_id>/invoice/", views.invoice_create,    name="invoice_from_appointment"),
path("billing/invoices/<int:pk>/",    views.invoice_detail,                 name="invoice_detail"),
path("billing/invoices/<int:pk>/payment/", views.record_payment,            name="record_payment"),
path("billing/daily/",                views.daily_summary,                  name="daily_summary"),

# Reports
path("reports/",                      views.reports_index,                  name="reports_index"),
path("reports/daily/",                views.report_daily,                   name="report_daily"),
path("reports/noshows/",              views.report_noshows,                 name="report_noshows"),
path("reports/revenue/",              views.report_revenue,                 name="report_revenue"),
path("reports/doctors/",              views.report_doctors,                 name="report_doctors"),

# Schedule
path("schedule/",                     views.doctor_schedule,                name="doctor_schedule"),
path("schedule/block/",               views.block_doctor_time,              name="block_doctor_time"),

# Notifications
path("notifications/",                views.notifications_list,             name="notifications_list"),
path("notifications/mark-all-read/",  views.mark_all_notifications_read,    name="mark_notifications_read"),

# Settings
path("settings/profile/",             views.settings_profile,              name="settings_profile"),
```

---

## SECTION F — SHARED COMPONENTS TO REUSE

The following patterns from the analysis will be copy-adapted (not rebuilt from scratch):

| Component | Source | Reused In |
|---|---|---|
| Status badge HTML pattern | `dashboard.html` existing | All appointment tables |
| Stat card HTML pattern (bg-white, rounded-2xl, shadow-sm) | `dashboard.html` existing | Dashboard, reports |
| HTMX patient search (`hx-get`, `hx-trigger="keyup delay:300ms"`) | `create_appointment.html` existing | Patient list, create appt |
| Table pattern (overflow-x-auto, th uppercase, tr hover) | `appointments_list.html` existing | All list pages |
| Empty state pattern (icon + h3 + p + CTA) | `appointments_list.html` existing | All list pages |
| Django messages alert block | `base_secretary.html` existing | Reused via base extension |
| Dark mode init script | `base_secretary.html` existing | Already in base, no action |
| Form input classes (rounded-xl, focus:ring-purple-500) | `create_appointment.html` existing | All forms |
| Mobile bottom nav | `base_secretary.html` existing | Update with new links |
| Notification bell | `base_secretary.html` existing | Keep as-is |
| Loader overlay (heartbeat CSS) | `loader.css` | Include in base template |
| `_require_secretary()` guard | `views.py` existing | All new views must call this |

---

## SECTION G — `base_secretary.html` NAVIGATION UPDATES

Current nav links:
```
Dashboard | Appointments | Create Appointment | Register Patient | Invitations
```

Proposed new nav links (desktop):
```
Dashboard | Appointments ▼ | Patients | Waiting Room | Billing | Reports | Schedule
```

**Appointments dropdown:**
- قائمة المواعيد → `secretary:appointments_list`
- موعد جديد → `secretary:create_appointment`
- التقويم → `secretary:calendar`

**Mobile bottom nav** (5 icons, most-used):
```
[🏠 الرئيسية] [📅 المواعيد] [👥 المرضى] [⏳ غرفة الانتظار] [☰ المزيد]
```

The "المزيد" icon opens a slide-up sheet with: Billing, Reports, Schedule, Settings, Notifications.

---

## SECTION H — FORMS TO CREATE (`secretary/forms.py`)

```python
class AppointmentStatusForm(forms.Form):
    status = forms.ChoiceField(choices=Appointment.STATUS_CHOICES)

class RescheduleForm(forms.ModelForm):
    class Meta:
        model = Appointment
        fields = ["appointment_date", "appointment_time"]

class CancellationForm(forms.Form):
    cancellation_reason = forms.CharField(widget=forms.Textarea, required=False)

class PatientEditForm(forms.ModelForm):
    # Edits CustomUser.name + PatientProfile fields
    name = forms.CharField()
    date_of_birth = forms.DateField(required=False)
    gender = forms.ChoiceField(required=False)
    blood_type = forms.ChoiceField(required=False)
    emergency_contact_name = forms.CharField(required=False)
    emergency_contact_phone = forms.CharField(required=False)

class InvoiceCreateForm(forms.ModelForm):
    class Meta:
        model = Invoice
        fields = ["patient", "appointment", "discount", "notes"]

class PaymentForm(forms.ModelForm):
    class Meta:
        model = Payment
        fields = ["amount", "method", "reference", "notes"]

class DoctorBlockForm(forms.ModelForm):
    class Meta:
        model = DoctorAvailabilityException
        fields = ["doctor", "start_date", "end_date", "reason"]
    # doctor filtered to clinic doctors in __init__

class ReminderFilterForm(forms.Form):
    date = forms.DateField(required=False)
    doctor_id = forms.IntegerField(required=False)
    date_from = forms.DateField(required=False)
    date_to = forms.DateField(required=False)
```

---

## SECTION I — SERVICES TO CREATE (`secretary/services.py`)

```python
def get_available_time_slots(doctor, clinic, date, appointment_type):
    """
    Returns list of available datetime slots for booking.
    Checks: DoctorAvailability, existing appointments + durations, ClinicHoliday,
            DoctorAvailabilityException, ClinicWorkingHours.
    Returns: list of time strings ["08:00", "08:30", ...] — 30-min grid
    """

def check_appointment_conflict(doctor, clinic, date, time, duration_minutes, exclude_id=None):
    """
    Returns True if the given slot overlaps with any existing confirmed/active appointment.
    """

def generate_file_number(clinic):
    """
    Generates next file number: YYYY-NNNN format.
    Thread-safe via select_for_update or atomic + count query.
    """

def generate_invoice_number(clinic):
    """
    Generates: INV-YYYY-NNNNNN
    """

def compute_invoice_totals(invoice):
    """
    Recomputes subtotal, total, balance_due from InvoiceItems and Payments.
    Called after any item/payment change.
    """

def get_doctor_status_today(doctor, clinic, date=None):
    """
    Returns: 'available' | 'with_patient' | 'off_today'
    Checks DoctorAvailability, DoctorAvailabilityException, IN_PROGRESS appointment.
    """

def get_waiting_room_data(clinic):
    """
    Returns dict: {
        'confirmed': QuerySet of today's CONFIRMED appointments ordered by time,
        'checked_in': QuerySet of today's CHECKED_IN appointments ordered by checked_in_at,
    }
    """
```

---

## SECTION J — IMPLEMENTATION ORDER (Phases)

Dependencies flow: migrations → core views → advanced features → billing → reports

---

### Phase 0 — Migrations (must come first, ~1 day)

1. `appointments` migration: `checked_in_at`, `cancellation_reason`
2. `patients` migration: `file_number` on ClinicPatient
3. `secretary` migration: Invoice, InvoiceItem, Payment models

Run `makemigrations` and `migrate` before writing any views.

---

### Phase 1 — Core Patient & Appointment Workflow (~3 days)

Priority: these are used every day, every hour.

4. `patient_list()` view + `patients/list.html`
5. `patient_detail()` view + `patients/detail.html` (tabs: info, appointments, records)
6. `edit_patient()` view + `patients/edit.html`
7. Enhance `register_patient()`: add file_number generation, all demographics fields
8. `appointment_detail()` view + `appointments/detail.html` (status timeline, actions)
9. `update_appointment_status_htmx()` + `htmx/appointment_status_chip.html`
10. `checkin_appointment()` HTMX endpoint

---

### Phase 2 — Dashboard Enhancement (~1 day)

11. Enhance `dashboard()` view: richer query, revenue card, activity feed
12. Enhance `dashboard.html`: doctor status strip, activity feed, revenue card
13. `doctor_status_htmx()` + `htmx/doctor_status_cards.html`
14. Update `base_secretary.html` nav: add new links + mobile "المزيد" sheet

---

### Phase 3 — Enhanced Booking (~2 days)

15. `get_time_slots_htmx()` + `htmx/time_slots.html`
16. `get_doctor_types_htmx()` (HTMX reload types when doctor changes)
17. Enhance `create_appointment()`: slot picker, conflict detection, walk-in checkbox
18. `reschedule_appointment()` view + template
19. `send_appointment_reminder()` HTMX endpoint

---

### Phase 4 — Calendar View (~2 days)

20. `calendar_view()` + `appointments/calendar.html` (day view)
21. `calendar_day_htmx()` + `htmx/calendar_appointments.html`
22. Week view tab in calendar template
23. `calendar_week_htmx()`

---

### Phase 5 — Waiting Room (~1 day)

24. `waiting_room_board()` + `waiting_room/board.html`
25. `waiting_room_confirmed_htmx()` + `waiting_room_checkedin_htmx()` (HTMX polling)
26. `htmx/waiting_room_row.html`
27. `waiting_room_display()` + `waiting_room/display.html` (TV mode)

---

### Phase 6 — Billing (~3 days)

28. Add Invoice/InvoiceItem/Payment to `secretary/models.py`
29. Run billing migration
30. `services.generate_invoice_number()`, `services.compute_invoice_totals()`
31. `billing_invoices()` + `billing/invoices.html`
32. `invoice_create()` + `billing/invoice_create.html`
33. `invoice_detail()` + `billing/invoice_detail.html` (print-friendly)
34. `record_payment()` HTMX + `htmx/payment_form.html`
35. `daily_summary()` + `billing/daily_summary.html`
36. Plug invoice link into appointment detail page (Tab 4)

---

### Phase 7 — Reports (~2 days)

37. `reports_index()` + `reports/index.html`
38. `report_daily()` + `reports/daily.html`
39. `report_noshows()` + `reports/noshows.html`
40. `report_doctors()` + `reports/doctors.html`
41. `report_revenue()` + `reports/revenue.html` (only if billing enabled)

---

### Phase 8 — Doctor Schedule & Block Time (~1 day)

42. `doctor_schedule()` + schedule page (read-only weekly grid)
43. `block_doctor_time()` + form
44. `DoctorBlockForm` in `secretary/forms.py`

---

### Phase 9 — Notifications & Communications (~1 day)

45. `notifications_list()` + notifications template
46. `mark_all_notifications_read()` HTMX endpoint
47. Add notification link to `base_secretary.html` bell

---

### Phase 10 — Settings & Polish (~1 day)

48. `settings_profile()` + `settings/profile.html`
49. Final nav update in `base_secretary.html`
50. QA pass: test all HTMX endpoints, check dark mode on all new pages, verify RTL layout

---

## SECTION K — CONSTRAINTS & RULES FOR IMPLEMENTATION

All code written under this plan must follow these non-negotiable rules derived from the codebase analysis:

1. **No build step.** All CSS is Tailwind CDN + inline classes. No npm, no webpack, no PostCSS.
2. **Arabic first.** All visible UI text must be in Arabic. No placeholder English text in templates.
3. **`dir="rtl"` is global** — never override to LTR in secretary templates.
4. **All views must call `_require_secretary(request)`** as the first guard. Return 403/redirect if it returns None.
5. **Tenant isolation always.** Every query must be filtered by `request.clinic`. No cross-clinic data leaks.
6. **Use `user.has_role("SECRETARY")` not `user.role == "SECRETARY"`** — multi-role support.
7. **Status transitions are server-validated.** Client cannot send an invalid transition (e.g., IN_PROGRESS → CONFIRMED). Validate the transition map in the view.
8. **No sidebar.** Navigation is top navbar (desktop) + bottom bar (mobile). Never introduce a left-side drawer.
9. **Django messages for user feedback.** No JS toast library. Use `messages.success()`, `messages.error()`, `messages.warning()`.
10. **HTMX for partial updates.** Live search, status changes, waiting room refresh, slot picker — all use HTMX `hx-get`/`hx-post` + `hx-target` + `hx-swap`.
11. **No hard deletes.** Cancellation = status change. Staff removal = `is_active=False`. Invoice cancellation = status change.
12. **Service layer for business logic.** Views call `secretary/services.py` functions. No business logic inline in views.
13. **`@transaction.atomic` on multi-step writes.** Any view that writes >1 row must be atomic.
14. **Dark mode on every new template.** Every element must have a `dark:` variant. Test by toggling theme.
15. **Billing is additive.** Billing features gracefully degrade if Invoice model queries fail (use `getattr`, `try/except`, or feature flags).

---

*End of plan. No code files should be created until this plan is reviewed and approved.*

---

## SECTION F — IMPLEMENTATION STATUS (Final, 2026-04-10)

### ✅ COMPLETED MODULES

| Module | Files | Notes |
|---|---|---|
| Dashboard | `views.py::dashboard`, `dashboard.html`, `htmx/doctor_status_cards.html` | Stats strip, 2-hour upcoming, doctor status, activity feed, revenue card (billing-optional) |
| Appointment List | `views.py::appointments_list`, `appointments/list.html` | Full filter/sort/pagination, cancel modal, check-in inline |
| Appointment Detail | `views.py::appointment_detail`, `appointment_detail.html` | Status chip (HTMX), cancel panel, notes |
| Create Appointment | `views.py::create_appointment`, `appointments/create.html` | 3-step wizard: patient search, time slot, confirmation |
| Edit Appointment | `views.py::edit_appointment`, `edit_appointment.html` | Reschedule + doctor/type change |
| Appointment Calendar | `views.py::calendar_view`, `appointments/calendar.html` | FullCalendar week/day, JSON feed, doctor filter |
| Patient List | `views.py::patient_list`, `patients/list.html` + `htmx/patient_list_rows.html` | HTMX live search, file number, last visit |
| Patient Detail | `views.py::patient_detail`, `patients/detail.html` | 4-tab: info / appointments / records / billing |
| Patient Edit | `views.py::edit_patient`, `patients/edit.html` | Demographics only; phone locked |
| Patient Register | `views.py::create_new_patient`, `patients/register.html` | Auto-generates file number |
| Waiting Room Board | `views.py::waiting_room`, `waiting_room/board.html` + HTMX partials | Two-column CONFIRMED + CHECKED_IN, 30s poll |
| Waiting Room Display | `views.py::waiting_room_display`, `waiting_room/display.html` | No-auth TV/kiosk, privacy names, 20s refresh |
| Check-in Search | `views.py::checkin_search`, `waiting_room/checkin_search.html` | Search by name/phone/file#, today's appointments, walk-in |
| Doctor Schedule | `views.py::doctor_schedule`, `schedule/index.html` | Week grid, cell states, block panel |
| Block Doctor Time | `views.py::block_doctor_time`, `schedule/block.html` | Conflict detection, force-confirm |
| Delete Block | `views.py::delete_doctor_block` | Soft-delete (`is_active=False`) |
| Reports Hub | `views.py::reports_index`, `reports/index.html` | Quick stats, 4 report cards |
| Daily Report | `views.py::report_daily`, `reports/daily.html` | Status bars, doctor breakdown, CSV + print |
| Visits Report | `views.py::report_visits`, `reports/visits.html` | New vs returning, doctor filter, CSV + print |
| No-shows Report | `views.py::report_noshows`, `reports/noshows.html` | Rates, day-of-week chart, top offenders, CSV + print |
| Doctor Utilization | `views.py::report_doctors`, `reports/doctors.html` | Horizontal bar chart, utilization %, CSV + print |
| Billing Invoices | `views.py::billing_invoices`, `billing/invoices.html` | Invoice list, status filter, summary bar |
| Daily Summary | `views.py::daily_summary`, `billing/daily_summary.html` | End-of-day cash register summary |
| Secretary Profile | `views.py::settings_profile`, `settings/profile.html` | Edit name/email/city, change password, localStorage preferences |
| Invitations | `views.py::secretary_invitations_inbox`, `invitations_inbox.html` | Accept/reject flow |
| Notifications | Handled by `appointments` app | Sidebar badge, unread count |
| HTMX Endpoints | `time_slots_htmx`, `doctor_status_htmx`, `doctor_types_htmx`, `patient_list_htmx`, `patient_search_htmx`, `waiting_room_confirmed_htmx`, `waiting_room_checkedin_htmx` | All wired up |

### ❌ SKIPPED MODULES (as planned in Section A)

| Module | Reason |
|---|---|
| Revenue report | No complete billing data (Invoice/Payment exist but no report view built) |
| Recurring appointments | No recurrence model — deferred |
| Patient merge | High data-integrity risk — deferred |
| Shift/rota management | No Shift model — deferred |
| Internal messaging | No Message/Thread model — deferred |
| PDF receipt generation | No PDF library — use print-friendly HTML instead |
| Insurance tracking | No insurance models — deferred |
| API endpoints for reports | Server-rendered only; no DRF endpoints for reports |

### 🐛 BUGS FIXED IN FINAL POLISH PASS (2026-04-10)

| Bug | File | Fix |
|---|---|---|
| `register_patient` URL name did not exist | `base_secretary.html`, `dashboard.html`, `appointments/create.html`, `patient_card.html` | Changed all to `create_new_patient` |
| `DoctorAvailabilityException` queried `exception_date` (non-existent field) | `views.py::_get_doctor_statuses` | Fixed to `start_date__lte + end_date__gte` |
| `DoctorAvailability.day_of_week` is int (0–6) but helper used `strftime("%A").upper()` string | `views.py::_get_doctor_statuses` | Changed to `today.weekday()` |
| Invoice loading silently swallowed `AttributeError` etc via bare `except Exception: pass` | `views.py::patient_detail` | Changed to `except ImportError:` with comment |
| `reschedule_appointment` in sidebar active-state check (URL doesn't exist) | `base_secretary.html` | Removed |
| Double-submit on all POST forms | `base_secretary.html` | Global `submit` listener disables button + shows spinner |
