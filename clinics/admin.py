from django.contrib import admin
from .models import Clinic, ClinicStaff, ClinicActivationCode, ClinicSubscription, ClinicVerification, InvitationAuditLog


@admin.register(Clinic)
class ClinicAdmin(admin.ModelAdmin):
    list_display = ['name', 'main_doctor', 'phone', 'email', 'status', 'is_active', 'created_at']
    list_filter = ['status', 'is_active', 'created_at']
    search_fields = ['name', 'main_doctor__name', 'main_doctor__email']
    readonly_fields = ['created_at']
    filter_horizontal = ['specialties']

    fieldsets = (
        ('Clinic Information', {
            'fields': ('name', 'address', 'phone', 'email', 'description', 'specialties')
        }),
        ('Management', {
            'fields': ('main_doctor', 'status', 'is_active')
        }),
        ('Metadata', {
            'fields': ('created_at',)
        }),
    )


@admin.register(ClinicStaff)
class ClinicStaffAdmin(admin.ModelAdmin):
    list_display = ['user', 'clinic', 'role', 'added_by', 'is_active', 'added_at']
    list_filter = ['role', 'is_active', 'clinic', 'added_at']
    search_fields = ['user__name', 'user__email', 'clinic__name']
    readonly_fields = ['added_at']

    fieldsets = (
        ('Staff Information', {
            'fields': ('clinic', 'user', 'role', 'is_active')
        }),
        ('Metadata', {
            'fields': ('added_by', 'added_at')
        }),
    )


@admin.register(ClinicActivationCode)
class ClinicActivationCodeAdmin(admin.ModelAdmin):
    list_display = ['code', 'clinic_name', 'phone', 'national_id', 'plan_type', 'max_doctors', 'subscription_expires_at', 'expires_at', 'is_used', 'used_by', 'created_at', 'used_at']
    list_filter = ['is_used', 'plan_type', 'created_at']
    search_fields = ['code', 'clinic_name', 'phone', 'national_id', 'used_by__name']
    readonly_fields = ['is_used', 'used_by', 'used_at', 'created_at']

    fieldsets = (
        ('Code Details', {
            'fields': ('code', 'clinic_name', 'expires_at')
        }),
        ('Intended Owner', {
            'fields': ('phone', 'national_id'),
            'description': 'Phone and national ID of the intended clinic owner. These must match what the user enters during signup.',
        }),
        ('Subscription', {
            'fields': ('plan_type', 'subscription_expires_at', 'max_doctors'),
            'description': 'Subscription terms that will be bound to the clinic when this code is used.',
        }),
        ('Usage', {
            'fields': ('is_used', 'used_by', 'used_at', 'created_at'),
        }),
    )


@admin.register(ClinicVerification)
class ClinicVerificationAdmin(admin.ModelAdmin):
    list_display = [
        "clinic",
        "owner_phone_verified_at",
        "owner_email_verified_at",
        "clinic_phone_verified_at",
        "clinic_email_verified_at",
        "created_at",
    ]
    list_filter = ["created_at"]
    search_fields = ["clinic__name", "clinic__main_doctor__name"]
    readonly_fields = [
        "owner_phone_verified_at",
        "owner_email_verified_at",
        "clinic_phone_verified_at",
        "clinic_email_verified_at",
        "created_at",
    ]

    fieldsets = (
        ("Clinic", {"fields": ("clinic",)}),
        (
            "Verification Timestamps",
            {
                "fields": (
                    "owner_phone_verified_at",
                    "owner_email_verified_at",
                    "clinic_phone_verified_at",
                    "clinic_email_verified_at",
                )
            },
        ),
        ("Metadata", {"fields": ("created_at",)}),
    )


@admin.register(ClinicSubscription)
class ClinicSubscriptionAdmin(admin.ModelAdmin):
    list_display = ['clinic', 'plan_type', 'max_doctors', 'expires_at', 'status', 'created_at']
    list_filter = ['plan_type', 'status', 'created_at']
    search_fields = ['clinic__name']
    readonly_fields = ['created_at']

    fieldsets = (
        ('Subscription Details', {
            'fields': ('clinic', 'plan_type', 'expires_at', 'max_doctors', 'status')
        }),
        ('Metadata', {
            'fields': ('created_at',)
        }),
    )


@admin.register(InvitationAuditLog)
class InvitationAuditLogAdmin(admin.ModelAdmin):
    list_display = ['invitation', 'clinic', 'action', 'performed_by', 'timestamp']
    list_filter = ['action', 'clinic', 'timestamp']
    search_fields = ['invitation__doctor_name', 'invitation__doctor_phone', 'clinic__name']
    readonly_fields = ['clinic', 'invitation', 'action', 'performed_by', 'timestamp']

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
