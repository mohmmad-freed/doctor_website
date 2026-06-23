from django.db import models
from django.conf import settings
from django.utils.translation import gettext_lazy as _lazy
from clinics.models import Clinic


# ──────────────────────────────────────────────────────────────────────────────
# Billing Models
# ──────────────────────────────────────────────────────────────────────────────


class Invoice(models.Model):
    """
    A billing invoice issued by the clinic for services rendered to a patient.

    An invoice can be:
    - Linked to a specific appointment (most common case).
    - Standalone, created manually by the secretary (e.g. for services
      rendered across multiple appointments or walk-in visits).

    Status flow:
        DRAFT → ISSUED → PAID
        DRAFT → ISSUED → PARTIAL → PAID
        DRAFT → CANCELLED
        PAID   → REFUNDED
    """

    class Status(models.TextChoices):
        DRAFT = "DRAFT", _lazy("مسودة")
        ISSUED = "ISSUED", _lazy("صادرة")
        PAID = "PAID", _lazy("مدفوعة")
        PARTIAL = "PARTIAL", _lazy("مدفوعة جزئياً")
        CANCELLED = "CANCELLED", _lazy("ملغاة")
        REFUNDED = "REFUNDED", _lazy("مستردة")

    clinic = models.ForeignKey(
        Clinic,
        on_delete=models.CASCADE,
        related_name="invoices",
        verbose_name="العيادة",
    )
    patient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="invoices",
        verbose_name="المريض",
    )
    appointment = models.ForeignKey(
        "appointments.Appointment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invoices",
        verbose_name="الموعد المرتبط",
        help_text="The appointment this invoice was generated for (optional).",
    )
    invoice_number = models.CharField(
        max_length=30,
        unique=True,
        help_text="Auto-generated unique invoice number (e.g. INV-2026-000001).",
        verbose_name="رقم الفاتورة",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        verbose_name="الحالة",
    )
    subtotal = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name="المجموع الفرعي",
    )
    discount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name="الخصم",
    )
    total = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name="الإجمالي",
        help_text="subtotal − discount",
    )
    amount_paid = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name="المبلغ المدفوع",
    )
    balance_due = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name="المبلغ المتبقي",
        help_text="total − amount_paid",
    )
    notes = models.TextField(
        blank=True,
        verbose_name="ملاحظات",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_invoices",
        verbose_name="أنشئت بواسطة",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    issued_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="تاريخ الإصدار",
        help_text="Set when status transitions from DRAFT to ISSUED.",
    )
    paid_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="تاريخ السداد الكامل",
        help_text="Set when balance_due reaches zero.",
    )

    class Meta:
        verbose_name = "Invoice"
        verbose_name_plural = "Invoices"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["clinic", "status"], name="invoice_clinic_status_idx"),
            models.Index(fields=["clinic", "created_at"], name="invoice_clinic_date_idx"),
            models.Index(fields=["patient"], name="invoice_patient_idx"),
        ]

    def __str__(self):
        return f"{self.invoice_number} — {self.patient.name} ({self.get_status_display()})"

    @property
    def is_fully_paid(self):
        return self.balance_due <= 0 and self.status == self.Status.PAID

    @property
    def can_be_cancelled(self):
        """An invoice can only be cancelled if it has no payments and is DRAFT or ISSUED."""
        return self.status in (self.Status.DRAFT, self.Status.ISSUED) and self.amount_paid == 0

    @property
    def can_be_deleted(self):
        """A draft invoice with no payments may be permanently deleted."""
        return self.status == self.Status.DRAFT and self.amount_paid == 0


class InvoiceItem(models.Model):
    """
    A single line item on an invoice (e.g. one service, procedure, or product).

    Description is copied (snapshotted) from the AppointmentType at invoice
    creation time so that later changes to the catalog do not alter historical
    records.  The FK to AppointmentType is optional and kept only as a reference.
    """

    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name="الفاتورة",
    )
    appointment_type = models.ForeignKey(
        "appointments.AppointmentType",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invoice_items",
        verbose_name="نوع الموعد (مرجع)",
        help_text="Reference only — description is snapshotted at creation time.",
    )
    description = models.CharField(
        max_length=255,
        verbose_name="الوصف",
        help_text="Snapshotted name/description at time of invoicing.",
    )
    quantity = models.PositiveIntegerField(
        default=1,
        verbose_name="الكمية",
    )
    unit_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        verbose_name="سعر الوحدة",
    )
    total = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        verbose_name="الإجمالي",
        help_text="quantity × unit_price — computed on save.",
    )

    class Meta:
        verbose_name = "Invoice Item"
        verbose_name_plural = "Invoice Items"
        ordering = ["id"]

    def save(self, *args, **kwargs):
        self.total = self.quantity * self.unit_price
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.description} × {self.quantity} @ ₪{self.unit_price}"


class Payment(models.Model):
    """
    Records a single payment received against an invoice.

    An invoice may have multiple partial payments (e.g. patient pays half
    today and the rest next week).  The Invoice.amount_paid and
    Invoice.balance_due fields are updated by secretary/services.py
    after each payment is saved.
    """

    class Method(models.TextChoices):
        CASH = "CASH", _lazy("نقدي")
        CARD = "CARD", _lazy("بطاقة بنكية")
        TRANSFER = "TRANSFER", _lazy("تحويل بنكي")
        OTHER = "OTHER", _lazy("أخرى")

    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.CASCADE,
        related_name="payments",
        verbose_name="الفاتورة",
    )
    clinic = models.ForeignKey(
        Clinic,
        on_delete=models.CASCADE,
        related_name="payments",
        verbose_name="العيادة",
        help_text="Denormalized for efficient daily-summary queries.",
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        verbose_name="المبلغ",
    )
    method = models.CharField(
        max_length=20,
        choices=Method.choices,
        default=Method.CASH,
        verbose_name="طريقة الدفع",
    )
    reference = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="مرجع",
        help_text="Card last-4 digits, transfer reference number, etc.",
    )
    notes = models.TextField(
        blank=True,
        verbose_name="ملاحظات",
    )
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="received_payments",
        verbose_name="استلم بواسطة",
    )
    received_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="وقت الاستلام",
    )

    class Meta:
        verbose_name = "Payment"
        verbose_name_plural = "Payments"
        ordering = ["-received_at"]
        indexes = [
            models.Index(fields=["clinic", "received_at"], name="payment_clinic_date_idx"),
        ]

    def __str__(self):
        return (
            f"₪{self.amount} ({self.get_method_display()}) "
            f"— Inv {self.invoice.invoice_number} @ {self.received_at:%Y-%m-%d %H:%M}"
        )


# ──────────────────────────────────────────────────────────────────────────────
# Procurement (Purchase Requests)
# ──────────────────────────────────────────────────────────────────────────────


class PurchaseRequest(models.Model):
    """
    A request by a secretary to purchase something for the clinic or staff.

    Unlike an :class:`Invoice` (which charges a patient for services), a purchase
    request is an internal procurement record that goes **pending** to the clinic
    owner. The owner accepts or rejects it with a note that the secretary can read.

    Status flow:
        PENDING → APPROVED
        PENDING → REJECTED
    """

    class Category(models.TextChoices):
        CLINIC = "CLINIC", _lazy("العيادة")
        STAFF = "STAFF", _lazy("الطاقم")
        GENERAL = "GENERAL", _lazy("عام")

    class Status(models.TextChoices):
        PENDING = "PENDING", _lazy("قيد المراجعة")
        APPROVED = "APPROVED", _lazy("مقبول")
        REJECTED = "REJECTED", _lazy("مرفوض")

    clinic = models.ForeignKey(
        Clinic,
        on_delete=models.CASCADE,
        related_name="purchase_requests",
        verbose_name="العيادة",
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="purchase_requests",
        verbose_name="مقدّم الطلب",
    )
    request_number = models.CharField(
        max_length=30,
        unique=True,
        help_text="Auto-generated unique request number (e.g. PR-2026-000001).",
        verbose_name="رقم الطلب",
    )
    title = models.CharField(
        max_length=255,
        verbose_name="عنوان الطلب",
    )
    category = models.CharField(
        max_length=20,
        choices=Category.choices,
        default=Category.CLINIC,
        verbose_name="الفئة",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        verbose_name="الحالة",
    )
    note = models.TextField(
        blank=True,
        verbose_name="سبب الطلب",
        help_text="Secretary's optional justification for the purchase (the 'why'), shown to the owner.",
    )
    total = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        verbose_name="الإجمالي",
        help_text="Sum of line item totals — recomputed on change.",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_purchase_requests",
        verbose_name="روجع بواسطة",
    )
    reviewed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="تاريخ المراجعة",
    )
    owner_note = models.TextField(
        blank=True,
        verbose_name="ملاحظة المالك",
        help_text="The owner's note on approval/rejection — visible to the secretary.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Purchase Request"
        verbose_name_plural = "Purchase Requests"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["clinic", "status"], name="prq_clinic_status_idx"),
            models.Index(fields=["clinic", "created_at"], name="prq_clinic_date_idx"),
        ]

    def __str__(self):
        return f"{self.request_number} — {self.title} ({self.get_status_display()})"

    @property
    def is_editable(self):
        """Secretary may edit/delete the request only while it is still pending."""
        return self.status == self.Status.PENDING


class PurchaseRequestItem(models.Model):
    """A single line item on a purchase request (e.g. one product to buy)."""

    request = models.ForeignKey(
        PurchaseRequest,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name="الطلب",
    )
    description = models.CharField(
        max_length=255,
        verbose_name="الوصف",
    )
    quantity = models.PositiveIntegerField(
        default=1,
        verbose_name="الكمية",
    )
    unit_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        verbose_name="سعر الوحدة",
    )
    total = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        verbose_name="الإجمالي",
        help_text="quantity × unit_price — computed on save.",
    )

    class Meta:
        verbose_name = "Purchase Request Item"
        verbose_name_plural = "Purchase Request Items"
        ordering = ["id"]

    def save(self, *args, **kwargs):
        self.total = self.quantity * self.unit_price
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.description} × {self.quantity} @ ₪{self.unit_price}"
