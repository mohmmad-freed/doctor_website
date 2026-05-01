from django.contrib import admin
from .models import (
    DoctorAvailability,
    DoctorFavouriteDrug,
    DoctorIntakeFormTemplate,
    DoctorIntakeQuestion,
    DoctorIntakeRule,
    DoctorProfile,
    DoctorSpecialty,
    Specialty,
)


@admin.register(Specialty)
class SpecialtyAdmin(admin.ModelAdmin):
    list_display = ["name_ar", "name", "description"]
    search_fields = ["name", "name_ar"]
    ordering = ["name_ar"]


class DoctorSpecialtyInline(admin.TabularInline):
    model = DoctorSpecialty
    extra = 1
    min_num = 0
    autocomplete_fields = ["specialty"]


@admin.register(DoctorProfile)
class DoctorProfileAdmin(admin.ModelAdmin):
    list_display = ["user", "get_primary_specialty", "years_of_experience"]
    list_filter = ["specialties"]
    search_fields = ["user__name", "user__phone"]
    raw_id_fields = ["user"]
    inlines = [DoctorSpecialtyInline]

    def get_primary_specialty(self, obj):
        ps = obj.primary_specialty
        return ps.name_ar if ps else "—"
    get_primary_specialty.short_description = "التخصص الرئيسي"


@admin.register(DoctorAvailability)
class DoctorAvailabilityAdmin(admin.ModelAdmin):
    list_display = ["doctor", "clinic", "get_day_display", "start_time", "end_time", "is_active"]
    list_filter = ["clinic", "day_of_week", "is_active"]
    search_fields = ["doctor__name", "doctor__phone", "clinic__name"]
    list_editable = ["is_active"]
    ordering = ["doctor", "day_of_week", "start_time"]

    def get_day_display(self, obj):
        return obj.get_day_of_week_display()
    get_day_display.short_description = "اليوم"
    get_day_display.admin_order_field = "day_of_week"


# ─── Intake Forms Admin ──────────────────────────────────────────────────


class DoctorIntakeQuestionInline(admin.TabularInline):
    model = DoctorIntakeQuestion
    extra = 1
    min_num = 0
    fields = ["order", "question_text", "question_text_ar", "field_type", "is_required", "choices", "placeholder"]
    ordering = ["order"]


class DoctorIntakeRuleInline(admin.TabularInline):
    model = DoctorIntakeRule
    fk_name = "source_question"
    extra = 0
    fields = ["source_question", "operator", "expected_value", "action", "target_question"]


@admin.register(DoctorIntakeFormTemplate)
class DoctorIntakeFormTemplateAdmin(admin.ModelAdmin):
    list_display = [
        "display_title",
        "doctor",
        "appointment_type",
        "question_count",
        "is_active",
        "updated_at",
    ]
    list_filter = ["is_active", "doctor"]
    search_fields = ["title", "title_ar", "doctor__name"]
    list_editable = ["is_active"]
    raw_id_fields = ["doctor", "appointment_type"]
    inlines = [DoctorIntakeQuestionInline]

    fieldsets = (
        (None, {
            "fields": ("doctor", "appointment_type"),
        }),
        ("Form Details", {
            "fields": ("title", "title_ar", "description", "is_active"),
        }),
    )

    def question_count(self, obj):
        return obj.questions.count()
    question_count.short_description = "عدد الأسئلة"


@admin.register(DoctorIntakeQuestion)
class DoctorIntakeQuestionAdmin(admin.ModelAdmin):
    list_display = ["display_text", "template", "field_type", "is_required", "order"]
    list_filter = ["field_type", "is_required", "template__doctor"]
    search_fields = ["question_text", "question_text_ar"]
    ordering = ["template", "order"]
    inlines = [DoctorIntakeRuleInline]


@admin.register(DoctorIntakeRule)
class DoctorIntakeRuleAdmin(admin.ModelAdmin):
    list_display = ["__str__", "operator", "action"]
    list_filter = ["operator", "action"]
    raw_id_fields = ["source_question", "target_question"]


@admin.register(DoctorFavouriteDrug)
class DoctorFavouriteDrugAdmin(admin.ModelAdmin):
    list_display = ["user", "drug_product", "created_at"]
    list_filter = ["drug_product__clinic", "created_at"]
    search_fields = ["user__name", "user__phone", "drug_product__generic_name", "drug_product__commercial_name"]
    raw_id_fields = ["user", "drug_product"]
    ordering = ["-created_at"]


# ─── Doctor Verification Admin (Dual-Layer) ──────────────────────────────

from .models import DoctorVerification, ClinicDoctorCredential
from django.utils import timezone


@admin.register(DoctorVerification)
class DoctorVerificationAdmin(admin.ModelAdmin):
    list_display = [
        "user",
        "identity_status",
        "has_id_doc",
        "has_license",
        "identity_reviewed_at",
    ]
    list_filter = ["identity_status"]
    search_fields = ["user__name", "user__phone"]
    readonly_fields = [
        "user", "created_at", "updated_at",
        "identity_reviewed_by", "identity_reviewed_at",
    ]
    actions = ["approve_identity", "reject_identity"]

    fieldsets = (
        ("Doctor", {"fields": ("user",)}),
        ("Identity Verification", {
            "fields": (
                "identity_status",
                "identity_document", "medical_license",
                "identity_rejection_reason",
            ),
        }),
        ("Review Metadata", {
            "fields": ("identity_reviewed_by", "identity_reviewed_at", "created_at", "updated_at"),
        }),
    )

    def has_id_doc(self, obj):
        return bool(obj.identity_document)
    has_id_doc.boolean = True
    has_id_doc.short_description = "هوية"

    def has_license(self, obj):
        return bool(obj.medical_license)
    has_license.boolean = True
    has_license.short_description = "رخصة"

    @admin.action(description="✅ Approve identity verification")
    def approve_identity(self, request, queryset):
        updated = 0
        for obj in queryset.filter(identity_status__in=["IDENTITY_PENDING_REVIEW", "IDENTITY_UNVERIFIED"]):
            obj.identity_status = "IDENTITY_VERIFIED"
            obj.identity_reviewed_by = request.user
            obj.identity_reviewed_at = timezone.now()
            obj.identity_rejection_reason = ""
            obj.save()
            updated += 1
            # Send email
            try:
                from accounts.email_utils import send_verification_approved_email
                send_verification_approved_email(obj.user, layer="identity")
            except Exception:
                pass
        self.message_user(request, f"Approved {updated} verification(s).")

    @admin.action(description="❌ Reject identity verification")
    def reject_identity(self, request, queryset):
        updated = 0
        for obj in queryset.filter(identity_status="IDENTITY_PENDING_REVIEW"):
            obj.identity_status = "IDENTITY_REJECTED"
            obj.identity_reviewed_by = request.user
            obj.identity_reviewed_at = timezone.now()
            obj.save()
            updated += 1
            try:
                from accounts.email_utils import send_verification_rejected_email
                send_verification_rejected_email(
                    obj.user, reason=obj.identity_rejection_reason, layer="identity"
                )
            except Exception:
                pass
        self.message_user(request, f"Rejected {updated} verification(s).")


@admin.register(ClinicDoctorCredential)
class ClinicDoctorCredentialAdmin(admin.ModelAdmin):
    list_display = [
        "doctor",
        "clinic",
        "specialty",
        "credential_status",
        "reviewed_at",
    ]
    list_filter = ["credential_status", "clinic"]
    search_fields = ["doctor__name", "doctor__phone", "clinic__name"]
    readonly_fields = ["doctor", "clinic", "specialty", "created_at", "updated_at", "reviewed_by", "reviewed_at"]
    actions = ["approve_credential", "reject_credential"]

    fieldsets = (
        ("Assignment", {
            "fields": ("doctor", "clinic", "specialty"),
        }),
        ("Credential Verification", {
            "fields": (
                "credential_status",
                "specialty_certificate",
                "rejection_reason",
            ),
        }),
        ("Review Metadata", {
            "fields": ("reviewed_by", "reviewed_at", "created_at", "updated_at"),
        }),
    )

    @admin.action(description="✅ Approve clinic credential")
    def approve_credential(self, request, queryset):
        updated = 0
        for obj in queryset.filter(credential_status="CREDENTIALS_PENDING"):
            obj.credential_status = "CREDENTIALS_VERIFIED"
            obj.reviewed_by = request.user
            obj.reviewed_at = timezone.now()
            obj.rejection_reason = ""
            obj.save()
            updated += 1
            try:
                from accounts.email_utils import send_verification_approved_email
                send_verification_approved_email(obj.doctor, layer="credential")
            except Exception:
                pass
        self.message_user(request, f"Approved {updated} credential(s).")

    @admin.action(description="❌ Reject clinic credential")
    def reject_credential(self, request, queryset):
        updated = 0
        for obj in queryset.filter(credential_status="CREDENTIALS_PENDING"):
            obj.credential_status = "CREDENTIALS_REJECTED"
            obj.reviewed_by = request.user
            obj.reviewed_at = timezone.now()
            obj.save()
            updated += 1
            try:
                from accounts.email_utils import send_verification_rejected_email
                send_verification_rejected_email(
                    obj.doctor, reason=obj.rejection_reason, layer="credential"
                )
            except Exception:
                pass
        self.message_user(request, f"Rejected {updated} credential(s).")