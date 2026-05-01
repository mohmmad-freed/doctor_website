from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.contrib import messages
from django.urls import reverse
from django.db.models import Count, Case, When, IntegerField
from django.http import HttpResponseForbidden, HttpResponseBadRequest

from clinics.models import Clinic, ClinicStaff, DrugFamily, DrugProduct, OrderCatalogItem
from .models import DoctorFavouriteDrug


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------

def _get_doctor_clinics(user):
    """Return queryset of clinics where the user is any active staff member (DOCTOR or MAIN_DOCTOR)."""
    return Clinic.objects.filter(
        staff_members__user=user,
        staff_members__revoked_at__isnull=True,
    ).distinct()


def _doctor_required(request):
    """Return None if user has any doctor role, else return a redirect."""
    roles = request.user.roles or []
    if not any(r in roles for r in ("DOCTOR", "MAIN_DOCTOR")):
        messages.error(request, "هذه الصفحة متاحة للأطباء فقط.")
        return redirect(reverse("accounts:home"))
    return None


def _resolve_catalog_clinic(request, clinic_id=None):
    """
    Given an optional clinic_id, return (clinic, all_clinics) for any doctor.
    If clinic_id is None or invalid, defaults to the first clinic.
    Returns (None, []) if user belongs to no clinics.
    """
    all_clinics = list(_get_doctor_clinics(request.user).order_by("name"))
    if not all_clinics:
        return None, []
    if clinic_id:
        clinic = next((c for c in all_clinics if c.id == clinic_id), all_clinics[0])
    else:
        clinic = all_clinics[0]
    return clinic, all_clinics


# -----------------------------------------------------------------------
# Main catalog page
# -----------------------------------------------------------------------

@login_required
def order_catalog(request):
    """Tabbed catalog page. All clinic doctors can add/edit/delete catalog items."""
    denied = _doctor_required(request)
    if denied:
        return denied

    try:
        clinic_id = int(request.GET.get("clinic_id", 0)) or None
    except (ValueError, TypeError):
        clinic_id = None

    clinic, all_clinics = _resolve_catalog_clinic(request, clinic_id)

    if not clinic:
        messages.warning(request, "لا توجد عيادة مرتبطة بحسابك.")
        return redirect(reverse("doctors:dashboard"))

    active_tab = request.GET.get("tab", "drugs")
    valid_tabs = {"drugs", "lab", "radiology", "microbiology", "procedure"}
    if active_tab not in valid_tabs:
        active_tab = "drugs"

    drug_families = list(DrugFamily.objects.filter(clinic=clinic).annotate(product_count=Count("products")))

    fav_ids = set(
        DoctorFavouriteDrug.objects
        .filter(user=request.user, drug_product__clinic=clinic)
        .values_list("drug_product_id", flat=True)
    )
    fav_rank = Case(When(id__in=fav_ids, then=0), default=1, output_field=IntegerField())
    drug_products = list(
        DrugProduct.objects.filter(clinic=clinic)
        .select_related("family")
        .annotate(_fav_rank=fav_rank)
        .order_by("_fav_rank", "generic_name")
    )
    for dp in drug_products:
        dp.is_favourite = dp.id in fav_ids

    lab_items = list(OrderCatalogItem.objects.filter(clinic=clinic, category=OrderCatalogItem.Category.LAB))
    radiology_items = list(OrderCatalogItem.objects.filter(clinic=clinic, category=OrderCatalogItem.Category.RADIOLOGY))
    microbiology_items = list(OrderCatalogItem.objects.filter(clinic=clinic, category=OrderCatalogItem.Category.MICROBIOLOGY))
    procedure_items = list(OrderCatalogItem.objects.filter(clinic=clinic, category=OrderCatalogItem.Category.PROCEDURE))

    return render(request, "doctors/order_catalog.html", {
        "clinic": clinic,
        "all_clinics": all_clinics,
        "active_tab": active_tab,
        "drug_families": drug_families,
        "drug_products": drug_products,
        "lab_items": lab_items,
        "radiology_items": radiology_items,
        "microbiology_items": microbiology_items,
        "procedure_items": procedure_items,
        "fav_ids": fav_ids,
        "can_edit": True,
    })


# -----------------------------------------------------------------------
# Favourite Drug Toggle (HTMX)
# -----------------------------------------------------------------------

@login_required
@require_POST
def toggle_favourite_drug(request):
    """HTMX endpoint: toggle a drug as favourite for the logged-in doctor."""
    denied = _doctor_required(request)
    if denied:
        return HttpResponseForbidden()

    try:
        drug_id = int(request.POST.get("drug_id", 0))
    except (ValueError, TypeError):
        return HttpResponseBadRequest("invalid drug_id")
    if not drug_id:
        return HttpResponseBadRequest("missing drug_id")

    clinic_ids = list(_get_doctor_clinics(request.user).values_list("id", flat=True))
    drug = get_object_or_404(DrugProduct, id=drug_id, clinic_id__in=clinic_ids)

    obj, created = DoctorFavouriteDrug.objects.get_or_create(
        user=request.user, drug_product=drug,
    )
    if not created:
        obj.delete()
    is_fav = created

    return render(request, "doctors/partials/favourite_star.html", {
        "drug": drug, "is_fav": is_fav,
    })


# -----------------------------------------------------------------------
# Drug Family CRUD
# -----------------------------------------------------------------------

@login_required
def drug_family_create(request):
    denied = _doctor_required(request)
    if denied:
        return denied

    try:
        clinic_id = int(request.GET.get("clinic_id") or request.POST.get("clinic_id", 0)) or None
    except (ValueError, TypeError):
        clinic_id = None

    clinic, all_clinics = _resolve_catalog_clinic(request, clinic_id)
    if not clinic:
        return redirect(reverse("doctors:order_catalog"))

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        description = request.POST.get("description", "").strip()
        if not name:
            messages.error(request, "اسم المجموعة مطلوب.")
        elif DrugFamily.objects.filter(clinic=clinic, name=name).exists():
            messages.error(request, "يوجد مجموعة بهذا الاسم مسبقاً.")
        else:
            DrugFamily.objects.create(clinic=clinic, name=name, description=description)
            messages.success(request, f"تمت إضافة المجموعة «{name}» بنجاح.")
            return redirect(f"{reverse('doctors:order_catalog')}?tab=drugs&clinic_id={clinic.id}")

    return render(request, "doctors/order_catalog/drug_family_form.html", {
        "clinic": clinic,
        "all_clinics": all_clinics,
        "form_title": "إضافة مجموعة دوائية",
        "submit_label": "إضافة",
    })


@login_required
def drug_family_edit(request, family_id):
    denied = _doctor_required(request)
    if denied:
        return denied

    owned_clinic_ids = list(_get_doctor_clinics(request.user).values_list("id", flat=True))
    family = get_object_or_404(DrugFamily, id=family_id, clinic_id__in=owned_clinic_ids)
    clinic = family.clinic
    all_clinics = list(_get_doctor_clinics(request.user).order_by("name"))

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        description = request.POST.get("description", "").strip()
        if not name:
            messages.error(request, "اسم المجموعة مطلوب.")
        elif DrugFamily.objects.filter(clinic=clinic, name=name).exclude(id=family_id).exists():
            messages.error(request, "يوجد مجموعة بهذا الاسم مسبقاً.")
        else:
            family.name = name
            family.description = description
            family.save()
            messages.success(request, f"تم تعديل المجموعة «{name}» بنجاح.")
            return redirect(f"{reverse('doctors:order_catalog')}?tab=drugs&clinic_id={clinic.id}")

    return render(request, "doctors/order_catalog/drug_family_form.html", {
        "clinic": clinic,
        "all_clinics": all_clinics,
        "family": family,
        "form_title": "تعديل المجموعة الدوائية",
        "submit_label": "حفظ التعديلات",
    })


@login_required
def drug_family_delete(request, family_id):
    if request.method != "POST":
        return redirect(reverse("doctors:order_catalog"))
    denied = _doctor_required(request)
    if denied:
        return denied

    owned_clinic_ids = list(_get_doctor_clinics(request.user).values_list("id", flat=True))
    family = get_object_or_404(DrugFamily, id=family_id, clinic_id__in=owned_clinic_ids)
    clinic_id = family.clinic_id
    name = family.name
    # Drugs under this family become family=NULL (SET_NULL FK)
    family.delete()
    messages.success(request, f"تم حذف المجموعة «{name}».")
    return redirect(f"{reverse('doctors:order_catalog')}?tab=drugs&clinic_id={clinic_id}")


# -----------------------------------------------------------------------
# Drug Product CRUD
# -----------------------------------------------------------------------

@login_required
def drug_product_create(request):
    denied = _doctor_required(request)
    if denied:
        return denied

    try:
        clinic_id = int(request.GET.get("clinic_id") or request.POST.get("clinic_id", 0)) or None
    except (ValueError, TypeError):
        clinic_id = None

    clinic, all_clinics = _resolve_catalog_clinic(request, clinic_id)
    if not clinic:
        return redirect(reverse("doctors:order_catalog"))

    families = DrugFamily.objects.filter(clinic=clinic).order_by("name")

    if request.method == "POST":
        generic_name = request.POST.get("generic_name", "").strip()
        commercial_name = request.POST.get("commercial_name", "").strip()
        family_id = request.POST.get("family_id") or None

        if not generic_name:
            messages.error(request, "الاسم العلمي للدواء مطلوب.")
        elif not commercial_name:
            messages.error(request, "الاسم التجاري للدواء مطلوب.")
        else:
            family = None
            if family_id:
                family = families.filter(id=family_id).first()
            DrugProduct.objects.create(
                clinic=clinic,
                family=family,
                generic_name=generic_name,
                commercial_name=commercial_name,
            )
            _lang = (request.user.preferred_language or "ar")
            messages.success(
                request,
                f"Drug «{generic_name}» has been added successfully." if _lang == "en"
                else f"تمت إضافة الدواء «{generic_name}» بنجاح.",
            )
            return redirect(f"{reverse('doctors:order_catalog')}?tab=drugs&clinic_id={clinic.id}")

    # Pre-select family from GET param
    preselect_family_id = request.GET.get("family_id")

    return render(request, "doctors/order_catalog/drug_product_form.html", {
        "clinic": clinic,
        "all_clinics": all_clinics,
        "families": families,
        "preselect_family_id": preselect_family_id,
        "form_title": "إضافة دواء",
        "submit_label": "إضافة",
    })


@login_required
def drug_product_edit(request, product_id):
    denied = _doctor_required(request)
    if denied:
        return denied

    owned_clinic_ids = list(_get_doctor_clinics(request.user).values_list("id", flat=True))
    product = get_object_or_404(DrugProduct, id=product_id, clinic_id__in=owned_clinic_ids)
    clinic = product.clinic
    all_clinics = list(_get_doctor_clinics(request.user).order_by("name"))
    families = DrugFamily.objects.filter(clinic=clinic).order_by("name")

    if request.method == "POST":
        generic_name = request.POST.get("generic_name", "").strip()
        commercial_name = request.POST.get("commercial_name", "").strip()
        family_id = request.POST.get("family_id") or None

        if not generic_name:
            messages.error(request, "الاسم العلمي للدواء مطلوب.")
        elif not commercial_name:
            messages.error(request, "الاسم التجاري للدواء مطلوب.")
        else:
            product.generic_name = generic_name
            product.commercial_name = commercial_name
            product.family = families.filter(id=family_id).first() if family_id else None
            product.save()
            messages.success(request, f"تم تعديل الدواء «{generic_name}» بنجاح.")
            return redirect(f"{reverse('doctors:order_catalog')}?tab=drugs&clinic_id={clinic.id}")

    return render(request, "doctors/order_catalog/drug_product_form.html", {
        "clinic": clinic,
        "all_clinics": all_clinics,
        "product": product,
        "families": families,
        "form_title": "تعديل بيانات الدواء",
        "submit_label": "حفظ التعديلات",
    })


@login_required
def drug_product_delete(request, product_id):
    if request.method != "POST":
        return redirect(reverse("doctors:order_catalog"))
    denied = _doctor_required(request)
    if denied:
        return denied

    owned_clinic_ids = list(_get_doctor_clinics(request.user).values_list("id", flat=True))
    product = get_object_or_404(DrugProduct, id=product_id, clinic_id__in=owned_clinic_ids)
    clinic_id = product.clinic_id
    name = product.generic_name
    product.delete()
    _lang = (request.user.preferred_language or "ar")
    messages.success(
        request,
        f"Drug «{name}» has been deleted." if _lang == "en"
        else f"تم حذف الدواء «{name}».",
    )
    return redirect(f"{reverse('doctors:order_catalog')}?tab=drugs&clinic_id={clinic_id}")


# -----------------------------------------------------------------------
# Non-drug Catalog Item CRUD
# -----------------------------------------------------------------------

@login_required
def catalog_item_create(request):
    denied = _doctor_required(request)
    if denied:
        return denied

    try:
        clinic_id = int(request.GET.get("clinic_id") or request.POST.get("clinic_id", 0)) or None
    except (ValueError, TypeError):
        clinic_id = None

    clinic, all_clinics = _resolve_catalog_clinic(request, clinic_id)
    if not clinic:
        return redirect(reverse("doctors:order_catalog"))

    category = request.GET.get("category") or request.POST.get("category", "")
    valid_categories = {c for c, _ in OrderCatalogItem.Category.choices}
    if category not in valid_categories:
        category = OrderCatalogItem.Category.LAB

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        category = request.POST.get("category", category)
        if category not in valid_categories:
            category = OrderCatalogItem.Category.LAB
        if not name:
            messages.error(request, "الاسم مطلوب.")
        elif OrderCatalogItem.objects.filter(clinic=clinic, category=category, name=name).exists():
            messages.error(request, "يوجد عنصر بهذا الاسم في هذه الفئة مسبقاً.")
        else:
            OrderCatalogItem.objects.create(clinic=clinic, category=category, name=name)
            messages.success(request, f"تمت الإضافة بنجاح.")
            tab = category.lower()
            return redirect(f"{reverse('doctors:order_catalog')}?tab={tab}&clinic_id={clinic.id}")

    category_label = dict(OrderCatalogItem.Category.choices).get(category, "")
    tab = category.lower()

    return render(request, "doctors/order_catalog/catalog_item_form.html", {
        "clinic": clinic,
        "all_clinics": all_clinics,
        "category": category,
        "category_label": category_label,
        "categories": OrderCatalogItem.Category.choices,
        "form_title": f"إضافة — {category_label}",
        "submit_label": "إضافة",
        "back_url": f"{reverse('doctors:order_catalog')}?tab={tab}&clinic_id={clinic.id}",
    })


@login_required
def catalog_item_edit(request, item_id):
    denied = _doctor_required(request)
    if denied:
        return denied

    owned_clinic_ids = list(_get_doctor_clinics(request.user).values_list("id", flat=True))
    item = get_object_or_404(OrderCatalogItem, id=item_id, clinic_id__in=owned_clinic_ids)
    clinic = item.clinic
    all_clinics = list(_get_doctor_clinics(request.user).order_by("name"))

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        if not name:
            messages.error(request, "الاسم مطلوب.")
        elif OrderCatalogItem.objects.filter(
            clinic=clinic, category=item.category, name=name
        ).exclude(id=item_id).exists():
            messages.error(request, "يوجد عنصر بهذا الاسم مسبقاً.")
        else:
            item.name = name
            item.save()
            messages.success(request, f"تم التعديل بنجاح.")
            tab = item.category.lower()
            return redirect(f"{reverse('doctors:order_catalog')}?tab={tab}&clinic_id={clinic.id}")

    category_label = dict(OrderCatalogItem.Category.choices).get(item.category, "")
    tab = item.category.lower()

    return render(request, "doctors/order_catalog/catalog_item_form.html", {
        "clinic": clinic,
        "all_clinics": all_clinics,
        "item": item,
        "category": item.category,
        "category_label": category_label,
        "categories": OrderCatalogItem.Category.choices,
        "form_title": f"تعديل — {category_label}",
        "submit_label": "حفظ التعديلات",
        "back_url": f"{reverse('doctors:order_catalog')}?tab={tab}&clinic_id={clinic.id}",
    })


@login_required
def catalog_item_delete(request, item_id):
    if request.method != "POST":
        return redirect(reverse("doctors:order_catalog"))
    denied = _doctor_required(request)
    if denied:
        return denied

    owned_clinic_ids = list(_get_doctor_clinics(request.user).values_list("id", flat=True))
    item = get_object_or_404(OrderCatalogItem, id=item_id, clinic_id__in=owned_clinic_ids)
    clinic_id = item.clinic_id
    tab = item.category.lower()
    item.delete()
    messages.success(request, "تم الحذف بنجاح.")
    return redirect(f"{reverse('doctors:order_catalog')}?tab={tab}&clinic_id={clinic_id}")
