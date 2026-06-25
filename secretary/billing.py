"""
Billing / accounting service layer for the secretary portal.

A "billing session" is an :class:`~secretary.models.Invoice` linked to an
:class:`~appointments.models.Appointment`. It is opened while the patient is in
the waiting room (``CHECKED_IN``), stays editable through ``IN_PROGRESS``, and
locks when the appointment becomes ``COMPLETED``. Payments may be recorded at
any time (subject to an overpayment guard) so outstanding debt can be settled
later.

All monetary values are :class:`~decimal.Decimal` with two decimal places
(currency ₪), matching the Invoice/InvoiceItem/Payment models.
"""

import logging
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.db.models import Q, Sum
from django.utils import timezone
from django.utils.translation import gettext as _

from appointments.models import Appointment
from secretary.models import Invoice, InvoiceItem, Payment

logger = logging.getLogger(__name__)

TWOPLACES = Decimal("0.01")
ZERO = Decimal("0.00")

# Upper bound for a single line item's total. The Invoice/InvoiceItem money columns
# are DecimalField(max_digits=10) (≤ 99,999,999.99); capping each line well below that
# keeps room for the invoice subtotal to sum multiple lines without a DB overflow, and
# turns an absurd charge into a clean BillingError instead of an uncaught 500.
MAX_LINE_TOTAL = Decimal("9999999.99")

# Invoice statuses that do NOT count toward a patient's outstanding balance.
_VOID_STATUSES = (Invoice.Status.CANCELLED, Invoice.Status.REFUNDED)

# Appointment statuses during which a session is "open" (charges editable).
_OPEN_APPT_STATUSES = (Appointment.Status.CHECKED_IN, Appointment.Status.IN_PROGRESS)

# Invoices that are still an *open billing session* — the patient's current bill,
# not yet debt. Queryset twin of ``is_editable()``: a session is open while its
# appointment is CHECKED_IN/IN_PROGRESS, or — for a standalone invoice — while it
# is still DRAFT/PARTIAL. The balance only becomes debt once the visit is completed
# (DRAFT → ISSUED) with an amount left unpaid.
# (For ``appointment=NULL`` rows the first clause is false since ``NULL IN (...)``
# is false, so the standalone clause governs them.)
_OPEN_SESSION_Q = (
    Q(appointment__status__in=_OPEN_APPT_STATUSES)
    | Q(
        appointment__isnull=True,
        status__in=(Invoice.Status.DRAFT, Invoice.Status.PARTIAL),
    )
)


class BillingError(Exception):
    """Raised when a billing operation is not allowed (carries an Arabic message)."""

    def __init__(self, message):
        self.message = message
        super().__init__(message)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _to_money(value):
    """Coerce a user-supplied value to a 2-place Decimal, or raise BillingError."""
    try:
        return Decimal(str(value)).quantize(TWOPLACES)
    except (InvalidOperation, TypeError, ValueError):
        raise BillingError(_("قيمة مالية غير صالحة."))


def generate_invoice_number(clinic):
    """Return a unique invoice number ``INV-{year}-{seq:06d}`` (global per year)."""
    year = timezone.now().year
    prefix = f"INV-{year}-"
    last = (
        Invoice.objects.filter(invoice_number__startswith=prefix)
        .order_by("-invoice_number")
        .values_list("invoice_number", flat=True)
        .first()
    )
    seq = 1
    if last:
        try:
            seq = int(last.rsplit("-", 1)[1]) + 1
        except (ValueError, IndexError):
            seq = Invoice.objects.filter(invoice_number__startswith=prefix).count() + 1
    return f"{prefix}{seq:06d}"


def get_open_invoice(appointment):
    """Return the active (non-void) invoice for this appointment, or ``None``."""
    return (
        appointment.invoices.exclude(status__in=_VOID_STATUSES)
        .order_by("-created_at")
        .first()
    )


def is_editable(invoice):
    """Whether charges may be added/removed (i.e. the session is still open).

    Editability is driven by the linked appointment's status — charges stay
    editable through ``CHECKED_IN``/``IN_PROGRESS`` and lock at ``COMPLETED`` —
    so a partial payment taken mid-visit does not freeze the bill.
    """
    if invoice.status in _VOID_STATUSES:
        return False
    appt = invoice.appointment
    if appt is None:
        # Standalone invoice: editable until paid/issued.
        return invoice.status in (Invoice.Status.DRAFT, Invoice.Status.PARTIAL)
    return appt.status in _OPEN_APPT_STATUSES


def recompute_invoice_totals(invoice):
    """Recompute subtotal/total/balance_due and payment status, then save."""
    subtotal = invoice.items.aggregate(s=Sum("total"))["s"] or ZERO
    invoice.subtotal = subtotal
    # Clamp the discount to [0, subtotal] so the invariant 0 ≤ total ≤ subtotal holds
    # no matter how `discount` was set — a negative discount can't inflate the bill and
    # an over-large one can't drive total/balance_due negative (which would otherwise
    # shrink patient_outstanding, the payment cap, across the patient's other invoices).
    discount = max(ZERO, min(invoice.discount or ZERO, subtotal))
    invoice.discount = discount
    invoice.total = subtotal - discount
    paid = invoice.amount_paid or ZERO
    invoice.balance_due = invoice.total - paid

    # Payment-state status. Never touch a void invoice, and never downgrade an
    # ISSUED (locked) invoice back to DRAFT — only move it forward to PARTIAL/PAID.
    if invoice.status not in _VOID_STATUSES:
        if invoice.total > ZERO and invoice.balance_due <= ZERO:
            invoice.status = Invoice.Status.PAID
            if invoice.paid_at is None:
                invoice.paid_at = timezone.now()
        elif paid > ZERO:
            invoice.status = Invoice.Status.PARTIAL
        # else: leave as-is (DRAFT or ISSUED)
    invoice.save()
    return invoice


def patient_outstanding(clinic, patient, exclude_invoice=None):
    """Total *payable* by the patient: sum of ``balance_due`` over non-void invoices.

    Includes the current open billing session — this is the cap used by the payment
    guard so the current bill can be settled. For what the UI shows as *debt*, use
    :func:`patient_debt` instead.
    """
    qs = (
        Invoice.objects.filter(clinic=clinic, patient=patient)
        .exclude(status__in=_VOID_STATUSES)
    )
    if exclude_invoice is not None:
        qs = qs.exclude(pk=exclude_invoice.pk)
    return qs.aggregate(s=Sum("balance_due"))["s"] or ZERO


def _debt_qs(clinic):
    """Invoices that count as patient *debt*: non-void and finalized.

    Excludes open billing sessions (the current bill) — those only become debt once
    the visit is completed and the invoice is issued with a remaining balance.
    """
    return (
        Invoice.objects.filter(clinic=clinic)
        .exclude(status__in=_VOID_STATUSES)
        .exclude(_OPEN_SESSION_Q)
    )


def patient_debt(clinic, patient, exclude_invoice=None):
    """Patient's finalized outstanding debt (excludes the open billing session)."""
    qs = _debt_qs(clinic).filter(patient=patient)
    if exclude_invoice is not None:
        qs = qs.exclude(pk=exclude_invoice.pk)
    return qs.aggregate(s=Sum("balance_due"))["s"] or ZERO


def clinic_total_debt(clinic):
    """Sum of all finalized outstanding debt across the clinic's patients."""
    return _debt_qs(clinic).aggregate(s=Sum("balance_due"))["s"] or ZERO


def debt_map(clinic, patient_ids):
    """Return ``{patient_id: Decimal}`` finalized debt balances (only > 0).

    One aggregate query for a whole page of patients — avoids N+1 when rendering
    debt badges on appointment lists. Open billing sessions are excluded.
    """
    ids = [pid for pid in set(patient_ids) if pid]
    if not ids:
        return {}
    rows = (
        _debt_qs(clinic)
        .filter(patient_id__in=ids)
        .values("patient_id")
        .annotate(total=Sum("balance_due"))
        .filter(total__gt=ZERO)
    )
    return {r["patient_id"]: r["total"] for r in rows}


def open_invoice_map(clinic, appointment_ids):
    """Return ``{appointment_id: Invoice}`` — the latest non-void invoice per appointment."""
    ids = [a for a in set(appointment_ids) if a]
    if not ids:
        return {}
    result = {}
    for inv in (
        Invoice.objects.filter(clinic=clinic, appointment_id__in=ids)
        .exclude(status__in=_VOID_STATUSES)
        .order_by("appointment_id", "-created_at")
    ):
        result.setdefault(inv.appointment_id, inv)  # newest per appointment wins
    return result


def patient_debtors(clinic):
    """Every patient in the clinic with finalized outstanding debt, largest first.

    Open billing sessions are excluded — a patient mid-visit is not yet a debtor.
    """
    return (
        _debt_qs(clinic)
        .values("patient_id", "patient__name", "patient__phone")
        .annotate(total_due=Sum("balance_due"))
        .filter(total_due__gt=ZERO)
        .order_by("-total_due")
    )


# ──────────────────────────────────────────────────────────────────────────────
# Session lifecycle
# ──────────────────────────────────────────────────────────────────────────────


def open_billing_session(appointment, by_user, ip=None):
    """Open a billing session (Invoice) for a checked-in patient.

    Guards that the patient is present in the clinic (CHECKED_IN or IN_PROGRESS)
    and that no open session already exists. Seeds the consultation fee from the
    appointment type. Idempotent: returns the existing open invoice if there is one.
    """
    if appointment.status not in _OPEN_APPT_STATUSES:
        raise BillingError(_("لا يمكن بدء الفوترة إلا أثناء وجود المريض في العيادة (الانتظار أو مع الطبيب)."))

    existing = get_open_invoice(appointment)
    if existing is not None:
        return existing

    with transaction.atomic():
        invoice = None
        for _attempt in range(5):
            try:
                with transaction.atomic():
                    invoice = Invoice.objects.create(
                        clinic=appointment.clinic,
                        patient=appointment.patient,
                        appointment=appointment,
                        invoice_number=generate_invoice_number(appointment.clinic),
                        status=Invoice.Status.DRAFT,
                        created_by=by_user,
                    )
                break
            except IntegrityError:
                invoice = None  # invoice_number collided — regenerate and retry
        if invoice is None:
            raise BillingError(_("تعذّر إنشاء رقم فاتورة فريد. يرجى المحاولة مرة أخرى."))

        # Seed the consultation fee from the appointment type (editable later).
        appt_type = appointment.appointment_type
        if appt_type is not None and appt_type.price is not None:
            InvoiceItem.objects.create(
                invoice=invoice,
                appointment_type=appt_type,
                description=appt_type.name_ar or appt_type.name,
                quantity=1,
                unit_price=appt_type.price,
            )
        recompute_invoice_totals(invoice)

        # Audit trail: who opened this billing session.
        from clinics.audit import log_activity
        from clinics.models import ActivityLog
        log_activity(
            actor=by_user,
            clinic=invoice.clinic,
            action=ActivityLog.Action.INVOICE_OPENED,
            target=invoice,
            ip=ip,
            metadata={
                "invoice_number": invoice.invoice_number,
                "appointment_id": appointment.id,
            },
        )
    return invoice


def add_charge(invoice, *, description, quantity, unit_price, actor=None, ip=None):
    """Add a line item to an open session and recompute totals."""
    if not is_editable(invoice):
        raise BillingError(_("لا يمكن تعديل الرسوم بعد إغلاق جلسة الفوترة."))
    description = (description or "").strip()
    if not description:
        raise BillingError(_("يرجى إدخال وصف للرسوم."))
    if quantity is None or int(quantity) < 1:
        raise BillingError(_("الكمية يجب أن تكون 1 على الأقل."))
    unit_price = _to_money(unit_price)
    if unit_price < ZERO:
        raise BillingError(_("سعر الوحدة غير صالح."))
    if int(quantity) * unit_price > MAX_LINE_TOTAL:
        raise BillingError(_("إجمالي الرسم كبير جداً. يرجى إدخال كمية أو سعر أقل."))

    with transaction.atomic():
        item = InvoiceItem.objects.create(
            invoice=invoice,
            description=description,
            quantity=int(quantity),
            unit_price=unit_price,
        )
        recompute_invoice_totals(invoice)

        # Audit trail: who added this charge.
        from clinics.audit import log_activity
        from clinics.models import ActivityLog
        log_activity(
            actor=actor,
            clinic=invoice.clinic,
            action=ActivityLog.Action.INVOICE_CHARGE_ADDED,
            target=invoice,
            ip=ip,
            metadata={
                "invoice_number": invoice.invoice_number,
                "description": description,
                "quantity": int(quantity),
                "unit_price": str(unit_price),
            },
        )
    return item


def remove_charge(item, actor=None, ip=None):
    """Delete a line item from an open session and recompute totals."""
    invoice = item.invoice
    if not is_editable(invoice):
        raise BillingError(_("لا يمكن تعديل الرسوم بعد إغلاق جلسة الفوترة."))
    # Snapshot the line for the audit row before it cascades away.
    line_desc = item.description
    line_total = str(item.total)
    with transaction.atomic():
        item.delete()
        recompute_invoice_totals(invoice)

        # Audit trail: who removed this charge.
        from clinics.audit import log_activity
        from clinics.models import ActivityLog
        log_activity(
            actor=actor,
            clinic=invoice.clinic,
            action=ActivityLog.Action.INVOICE_CHARGE_REMOVED,
            target=invoice,
            ip=ip,
            metadata={
                "invoice_number": invoice.invoice_number,
                "description": line_desc,
                "line_total": line_total,
            },
        )
    return invoice


def delete_invoice(invoice, actor=None, ip=None):
    """Permanently delete a draft invoice (line items cascade). Guards eligibility."""
    if not invoice.can_be_deleted:
        raise BillingError(_("لا يمكن حذف هذه الفاتورة (مسودة فقط وبدون دفعات)."))
    # Snapshot identity for the audit row before the row is gone.
    clinic = invoice.clinic
    invoice_number = invoice.invoice_number
    patient_id = invoice.patient_id
    invoice_id = invoice.pk
    invoice.delete()

    # Audit trail: who deleted this invoice (target_id captured pre-delete).
    from clinics.audit import log_activity
    from clinics.models import ActivityLog
    log_activity(
        actor=actor,
        clinic=clinic,
        action=ActivityLog.Action.INVOICE_DELETED,
        target_type="Invoice",
        target_id=invoice_id,
        ip=ip,
        metadata={"invoice_number": invoice_number, "patient_id": patient_id},
    )


# ──────────────────────────────────────────────────────────────────────────────
# Payments
# ──────────────────────────────────────────────────────────────────────────────


@transaction.atomic
def record_payment(*, primary_invoice, amount, method, reference="", breakdown="", by_user, ip=None):
    """Record a payment, guarding against overpayment and settling debt FIFO.

    The amount may never exceed the patient's *total* outstanding balance
    (this invoice + any prior debt). Anything beyond the current invoice's
    balance is applied to the patient's other unpaid invoices, oldest first.

    Returns the list of :class:`Payment` rows created (one per invoice touched).
    """
    amount = _to_money(amount)
    if amount <= ZERO:
        raise BillingError(_("يجب إدخال مبلغ أكبر من صفر."))

    clinic = primary_invoice.clinic
    patient = primary_invoice.patient

    # Lock every non-void invoice for this patient up front so the overpayment check
    # and the FIFO allocation act on a consistent, serialized view. Without the row
    # lock two concurrent payments could each read the same balances, both pass the
    # cap, and together overpay (TOCTOU). The cap is derived from this locked set.
    locked = list(
        Invoice.objects.select_for_update()
        .filter(clinic=clinic, patient=patient)
        .exclude(status__in=_VOID_STATUSES)
        .order_by("created_at")
    )
    # Resolve the primary against the locked (non-void) set: a void invoice is absent
    # here and so can never receive an allocation.
    locked_primary = next((inv for inv in locked if inv.pk == primary_invoice.pk), None)

    max_payable = sum((inv.balance_due or ZERO for inv in locked), ZERO)
    if amount > max_payable:
        raise BillingError(
            _("المبلغ المُدخل يتجاوز إجمالي المستحقات على المريض (الحد الأقصى ₪%(max)s).")
            % {"max": max_payable}
        )

    # Allocation order: this invoice first, then other unpaid invoices oldest first.
    targets = []
    if locked_primary is not None and (locked_primary.balance_due or ZERO) > ZERO:
        targets.append((locked_primary, True))
    targets.extend(
        (inv, False)
        for inv in locked
        if inv.pk != primary_invoice.pk and (inv.balance_due or ZERO) > ZERO
    )

    remaining = amount
    payments = []
    for inv, is_primary in targets:
        if remaining <= ZERO:
            break
        portion = min(remaining, inv.balance_due)
        if portion <= ZERO:
            continue
        payments.append(
            Payment.objects.create(
                invoice=inv,
                clinic=clinic,
                amount=portion,
                method=method,
                reference=reference if is_primary else "",
                notes=breakdown if is_primary else _("سداد دين سابق"),
                received_by=by_user,
            )
        )
        inv.amount_paid = (inv.amount_paid or ZERO) + portion
        recompute_invoice_totals(inv)
        remaining -= portion

    # Audit trail: who recorded this payment and how it was allocated (FIFO).
    if payments:
        from clinics.audit import log_activity
        from clinics.models import ActivityLog
        log_activity(
            actor=by_user,
            clinic=clinic,
            action=ActivityLog.Action.PAYMENT_RECORDED,
            target=primary_invoice,
            ip=ip,
            metadata={
                "amount": str(amount),
                "method": method,
                "primary_invoice": primary_invoice.invoice_number,
                "allocations": [
                    {"invoice_number": p.invoice.invoice_number, "amount": str(p.amount)}
                    for p in payments
                ],
            },
        )

    return payments


# ──────────────────────────────────────────────────────────────────────────────
# Appointment status sync
# ──────────────────────────────────────────────────────────────────────────────


def on_appointment_status_changed(appointment, new_status):
    """Lock or void the open invoice in lockstep with the appointment status.

    Called from the appointment status-transition paths. Never raises — billing
    must never block a status change; failures are logged instead.
    """
    try:
        invoice = get_open_invoice(appointment)
        if invoice is None:
            return

        if new_status == Appointment.Status.COMPLETED:
            # Lock charges: DRAFT → ISSUED (don't disturb PARTIAL/PAID/ISSUED).
            if invoice.status == Invoice.Status.DRAFT:
                invoice.status = Invoice.Status.ISSUED
                if invoice.issued_at is None:
                    invoice.issued_at = timezone.now()
                invoice.save(update_fields=["status", "issued_at", "updated_at"])
        elif new_status in (
            Appointment.Status.CANCELLED,
            Appointment.Status.NO_SHOW,
            Appointment.Status.CONFIRMED,  # "remove from queue" undo of a check-in
        ):
            # Auto-void only an untouched session (DRAFT, no payments) so a
            # phantom consultation-fee debt never lingers after an undo/cancel.
            if invoice.status == Invoice.Status.DRAFT and (invoice.amount_paid or ZERO) <= ZERO:
                invoice.status = Invoice.Status.CANCELLED
                invoice.save(update_fields=["status", "updated_at"])
    except Exception:
        logger.exception(
            "Billing status sync failed for appointment %s",
            getattr(appointment, "id", "?"),
        )
