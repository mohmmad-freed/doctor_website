"""
Procurement service layer for the secretary portal.

A :class:`~secretary.models.PurchaseRequest` is an internal request by a secretary
to buy something for the clinic or staff. It is created ``PENDING`` and reviewed by
the clinic owner (accept/reject with a note). Unlike an :class:`Invoice`, it does
not charge a patient — it is an internal procurement record.

All monetary values are :class:`~decimal.Decimal` with two decimal places (₪),
matching the PurchaseRequest/PurchaseRequestItem models.
"""

import logging
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.translation import gettext as _

from secretary.models import PurchaseRequest, PurchaseRequestItem

logger = logging.getLogger(__name__)

TWOPLACES = Decimal("0.01")
ZERO = Decimal("0.00")


class ProcurementError(Exception):
    """Raised when a procurement operation is not allowed (carries an Arabic message)."""

    def __init__(self, message):
        self.message = message
        super().__init__(message)


def _to_money(value):
    """Coerce a user-supplied value to a 2-place Decimal, or raise ProcurementError."""
    try:
        return Decimal(str(value)).quantize(TWOPLACES)
    except (InvalidOperation, TypeError, ValueError):
        raise ProcurementError(_("قيمة مالية غير صالحة."))


def generate_request_number(clinic):
    """Return a unique purchase request number ``PR-{year}-{seq:06d}`` (global per year)."""
    year = timezone.now().year
    prefix = f"PR-{year}-"
    last = (
        PurchaseRequest.objects.filter(request_number__startswith=prefix)
        .order_by("-request_number")
        .values_list("request_number", flat=True)
        .first()
    )
    seq = 1
    if last:
        try:
            seq = int(last.rsplit("-", 1)[1]) + 1
        except (ValueError, IndexError):
            seq = PurchaseRequest.objects.filter(request_number__startswith=prefix).count() + 1
    return f"{prefix}{seq:06d}"


def recompute_total(purchase_request):
    """Recompute the request total from its line items, then save."""
    total = purchase_request.items.aggregate(s=Sum("total"))["s"] or ZERO
    purchase_request.total = total
    purchase_request.save(update_fields=["total", "updated_at"])
    return purchase_request


def create_purchase_request(*, clinic, user, title, category, note="", items):
    """Create a PENDING purchase request with its line items.

    ``items`` is an iterable of dicts ``{"description", "quantity", "unit_price"}``.
    Validates that at least one valid item is present. Returns the created request.
    """
    title = (title or "").strip()
    if not title:
        raise ProcurementError(_("يرجى إدخال عنوان للطلب."))

    if category not in PurchaseRequest.Category.values:
        category = PurchaseRequest.Category.CLINIC

    cleaned_items = []
    for row in items or []:
        description = (row.get("description") or "").strip()
        if not description:
            continue  # skip blank rows
        try:
            quantity = int(row.get("quantity") or 0)
        except (TypeError, ValueError):
            raise ProcurementError(_("الكمية يجب أن تكون رقماً صحيحاً."))
        if quantity < 1:
            raise ProcurementError(_("الكمية يجب أن تكون 1 على الأقل."))
        unit_price = _to_money(row.get("unit_price"))
        if unit_price < ZERO:
            raise ProcurementError(_("سعر الوحدة غير صالح."))
        cleaned_items.append((description, quantity, unit_price))

    if not cleaned_items:
        raise ProcurementError(_("يرجى إضافة عنصر واحد على الأقل إلى الطلب."))

    with transaction.atomic():
        request_obj = None
        for _attempt in range(5):
            try:
                with transaction.atomic():
                    request_obj = PurchaseRequest.objects.create(
                        clinic=clinic,
                        requested_by=user,
                        request_number=generate_request_number(clinic),
                        title=title,
                        category=category,
                        note=(note or "").strip(),
                        status=PurchaseRequest.Status.PENDING,
                    )
                break
            except IntegrityError:
                request_obj = None  # request_number collided — regenerate and retry
        if request_obj is None:
            raise ProcurementError(_("تعذّر إنشاء رقم طلب فريد. يرجى المحاولة مرة أخرى."))

        for description, quantity, unit_price in cleaned_items:
            PurchaseRequestItem.objects.create(
                request=request_obj,
                description=description,
                quantity=quantity,
                unit_price=unit_price,
            )
        recompute_total(request_obj)

    return request_obj
