"""Forms for the secretary billing module."""

from decimal import Decimal

from django import forms
from django.utils.translation import gettext_lazy as _

from secretary.models import Payment

# Shared Tailwind / dark-mode input styling (matches patients/forms.py convention).
_INPUT = (
    "w-full px-4 py-2 border rounded-xl focus:ring-2 focus:ring-primary-500 "
    "focus:border-primary-500 dark:bg-slate-800 dark:border-slate-700 dark:text-white"
)


class ChargeForm(forms.Form):
    """A single charge (line item) added to an open billing session."""

    description = forms.CharField(
        max_length=255,
        label=_("الوصف"),
        widget=forms.TextInput(attrs={
            "class": _INPUT,
            "placeholder": _("مثال: رسوم حقنة"),
        }),
    )
    quantity = forms.IntegerField(
        min_value=1,
        initial=1,
        label=_("الكمية"),
        widget=forms.NumberInput(attrs={"class": _INPUT, "min": "1"}),
    )
    unit_price = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("0.00"),
        label=_("سعر الوحدة"),
        widget=forms.NumberInput(attrs={"class": _INPUT, "step": "0.01", "min": "0"}),
    )


class PaymentForm(forms.Form):
    """A payment recorded against an invoice, capped at the patient's total dues."""

    amount = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("0.01"),
        label=_("المبلغ المدفوع"),
        widget=forms.NumberInput(attrs={"class": _INPUT, "step": "0.01", "min": "0"}),
    )
    method = forms.ChoiceField(
        choices=Payment.Method.choices,
        initial=Payment.Method.CASH,
        label=_("طريقة الدفع"),
        widget=forms.Select(attrs={"class": _INPUT}),
    )
    reference = forms.CharField(
        max_length=100,
        required=False,
        label=_("مرجع (اختياري)"),
        widget=forms.TextInput(attrs={
            "class": _INPUT,
            "placeholder": _("رقم العملية / آخر 4 أرقام البطاقة"),
        }),
    )
    breakdown = forms.CharField(
        required=False,
        label=_("تفصيل الدفعة"),
        widget=forms.Textarea(attrs={
            "class": _INPUT,
            "rows": 3,
            "placeholder": _("مثال: كشف 50، حقنة 25، سداد دين 75"),
        }),
    )

    def __init__(self, *args, max_payable=None, **kwargs):
        self.max_payable = Decimal(str(max_payable)) if max_payable is not None else None
        super().__init__(*args, **kwargs)

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        if amount <= Decimal("0.00"):
            raise forms.ValidationError(_("يجب إدخال مبلغ أكبر من صفر."))
        if self.max_payable is not None and amount > self.max_payable:
            raise forms.ValidationError(
                _("المبلغ يتجاوز إجمالي المستحقات على المريض (الحد الأقصى ₪%(max)s).")
                % {"max": self.max_payable}
            )
        return amount
