import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('appointments', '0007_add_appointment_tracking_fields'),
        ('clinics', '0006_order_catalog'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Invoice',
            fields=[
                (
                    'id',
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name='ID',
                    ),
                ),
                (
                    'invoice_number',
                    models.CharField(
                        max_length=30,
                        unique=True,
                        help_text='Auto-generated unique invoice number (e.g. INV-2026-000001).',
                        verbose_name='رقم الفاتورة',
                    ),
                ),
                (
                    'status',
                    models.CharField(
                        choices=[
                            ('DRAFT', 'مسودة'),
                            ('ISSUED', 'صادرة'),
                            ('PAID', 'مدفوعة'),
                            ('PARTIAL', 'مدفوعة جزئياً'),
                            ('CANCELLED', 'ملغاة'),
                            ('REFUNDED', 'مستردة'),
                        ],
                        default='DRAFT',
                        max_length=20,
                        verbose_name='الحالة',
                    ),
                ),
                (
                    'subtotal',
                    models.DecimalField(
                        decimal_places=2,
                        default=0,
                        max_digits=10,
                        verbose_name='المجموع الفرعي',
                    ),
                ),
                (
                    'discount',
                    models.DecimalField(
                        decimal_places=2,
                        default=0,
                        max_digits=10,
                        verbose_name='الخصم',
                    ),
                ),
                (
                    'total',
                    models.DecimalField(
                        decimal_places=2,
                        default=0,
                        help_text='subtotal − discount',
                        max_digits=10,
                        verbose_name='الإجمالي',
                    ),
                ),
                (
                    'amount_paid',
                    models.DecimalField(
                        decimal_places=2,
                        default=0,
                        max_digits=10,
                        verbose_name='المبلغ المدفوع',
                    ),
                ),
                (
                    'balance_due',
                    models.DecimalField(
                        decimal_places=2,
                        default=0,
                        help_text='total − amount_paid',
                        max_digits=10,
                        verbose_name='المبلغ المتبقي',
                    ),
                ),
                (
                    'notes',
                    models.TextField(blank=True, verbose_name='ملاحظات'),
                ),
                (
                    'created_at',
                    models.DateTimeField(auto_now_add=True),
                ),
                (
                    'updated_at',
                    models.DateTimeField(auto_now=True),
                ),
                (
                    'issued_at',
                    models.DateTimeField(
                        blank=True,
                        null=True,
                        help_text='Set when status transitions from DRAFT to ISSUED.',
                        verbose_name='تاريخ الإصدار',
                    ),
                ),
                (
                    'paid_at',
                    models.DateTimeField(
                        blank=True,
                        null=True,
                        help_text='Set when balance_due reaches zero.',
                        verbose_name='تاريخ السداد الكامل',
                    ),
                ),
                (
                    'appointment',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='invoices',
                        to='appointments.appointment',
                        verbose_name='الموعد المرتبط',
                        help_text='The appointment this invoice was generated for (optional).',
                    ),
                ),
                (
                    'clinic',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='invoices',
                        to='clinics.clinic',
                        verbose_name='العيادة',
                    ),
                ),
                (
                    'created_by',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='created_invoices',
                        to=settings.AUTH_USER_MODEL,
                        verbose_name='أنشئت بواسطة',
                    ),
                ),
                (
                    'patient',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='invoices',
                        to=settings.AUTH_USER_MODEL,
                        verbose_name='المريض',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Invoice',
                'verbose_name_plural': 'Invoices',
                'ordering': ['-created_at'],
                'indexes': [
                    models.Index(fields=['clinic', 'status'], name='invoice_clinic_status_idx'),
                    models.Index(fields=['clinic', 'created_at'], name='invoice_clinic_date_idx'),
                    models.Index(fields=['patient'], name='invoice_patient_idx'),
                ],
            },
        ),
        migrations.CreateModel(
            name='InvoiceItem',
            fields=[
                (
                    'id',
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name='ID',
                    ),
                ),
                (
                    'description',
                    models.CharField(
                        max_length=255,
                        verbose_name='الوصف',
                        help_text='Snapshotted name/description at time of invoicing.',
                    ),
                ),
                (
                    'quantity',
                    models.PositiveIntegerField(default=1, verbose_name='الكمية'),
                ),
                (
                    'unit_price',
                    models.DecimalField(
                        decimal_places=2,
                        max_digits=10,
                        verbose_name='سعر الوحدة',
                    ),
                ),
                (
                    'total',
                    models.DecimalField(
                        decimal_places=2,
                        max_digits=10,
                        verbose_name='الإجمالي',
                        help_text='quantity × unit_price — computed on save.',
                    ),
                ),
                (
                    'appointment_type',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='invoice_items',
                        to='appointments.appointmenttype',
                        verbose_name='نوع الموعد (مرجع)',
                        help_text='Reference only — description is snapshotted at creation time.',
                    ),
                ),
                (
                    'invoice',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='items',
                        to='secretary.invoice',
                        verbose_name='الفاتورة',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Invoice Item',
                'verbose_name_plural': 'Invoice Items',
                'ordering': ['id'],
            },
        ),
        migrations.CreateModel(
            name='Payment',
            fields=[
                (
                    'id',
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name='ID',
                    ),
                ),
                (
                    'amount',
                    models.DecimalField(
                        decimal_places=2,
                        max_digits=10,
                        verbose_name='المبلغ',
                    ),
                ),
                (
                    'method',
                    models.CharField(
                        choices=[
                            ('CASH', 'نقدي'),
                            ('CARD', 'بطاقة بنكية'),
                            ('TRANSFER', 'تحويل بنكي'),
                            ('OTHER', 'أخرى'),
                        ],
                        default='CASH',
                        max_length=20,
                        verbose_name='طريقة الدفع',
                    ),
                ),
                (
                    'reference',
                    models.CharField(
                        blank=True,
                        max_length=100,
                        verbose_name='مرجع',
                        help_text='Card last-4 digits, transfer reference number, etc.',
                    ),
                ),
                (
                    'notes',
                    models.TextField(blank=True, verbose_name='ملاحظات'),
                ),
                (
                    'received_at',
                    models.DateTimeField(
                        auto_now_add=True,
                        verbose_name='وقت الاستلام',
                    ),
                ),
                (
                    'clinic',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='payments',
                        to='clinics.clinic',
                        verbose_name='العيادة',
                        help_text='Denormalized for efficient daily-summary queries.',
                    ),
                ),
                (
                    'invoice',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='payments',
                        to='secretary.invoice',
                        verbose_name='الفاتورة',
                    ),
                ),
                (
                    'received_by',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='received_payments',
                        to=settings.AUTH_USER_MODEL,
                        verbose_name='استلم بواسطة',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Payment',
                'verbose_name_plural': 'Payments',
                'ordering': ['-received_at'],
                'indexes': [
                    models.Index(fields=['clinic', 'received_at'], name='payment_clinic_date_idx'),
                ],
            },
        ),
    ]
