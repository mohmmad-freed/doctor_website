from django.db import models
from django.conf import settings
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
        DRAFT = "DRAFT", "مسودة"
        ISSUED = "ISSUED", "صادرة"
        PAID = "PAID", "مدفوعة"
        PARTIAL = "PARTIAL", "مدفوعة جزئياً"
        CANCELLED = "CANCELLED", "ملغاة"
        REFUNDED = "REFUNDED", "مستردة"

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
        CASH = "CASH", "نقدي"
        CARD = "CARD", "بطاقة بنكية"
        TRANSFER = "TRANSFER", "تحويل بنكي"
        OTHER = "OTHER", "أخرى"

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
