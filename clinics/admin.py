from django.contrib import admin
from django.utils import timezone
from django.utils.html import format_html
from .models import (
    Clinic, ClinicStaff, ClinicActivationCode, ClinicSubscription,
    ClinicVerification, InvitationAuditLog, ClinicHoliday, DoctorAvailabilityException,
    DrugFamily, DrugProduct, OrderCatalogItem,
)


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
    list_display = ['code', 'clinic_name', 'phone', 'national_id', 'plan_name', 'plan_type', 'max_doctors', 'max_secretaries', 'subscription_expires_at', 'expires_at', 'is_used', 'used_by', 'created_at', 'used_at']
    list_filter = ['is_used', 'plan_name', 'plan_type', 'created_at']
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
            'fields': ('plan_name', 'plan_type', 'subscription_expires_at', 'max_doctors', 'max_secretaries'),
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
    list_display = [
        'clinic', 'plan_name', 'plan_type', 'max_doctors', 'max_secretaries',
        'expires_at', 'status_badge', 'is_effectively_active', 'activated_by', 'updated_at',
    ]
    list_filter = ['plan_name', 'plan_type', 'status', 'created_at']
    search_fields = ['clinic__name', 'notes']
    readonly_fields = ['created_at', 'updated_at', 'is_effectively_active', 'current_doctors_count', 'current_secretaries_count']
    actions = ['action_activate', 'action_suspend', 'action_extend_30', 'action_extend_365']

    fieldsets = (
        ('Subscription', {
            'fields': ('clinic', 'plan_name', 'plan_type', 'status', 'expires_at')
        }),
        ('Capacity Limits', {
            'fields': ('max_doctors', 'max_secretaries'),
            'description': 'Override capacity for this clinic. 0 = unlimited.',
        }),
        ('Current Usage', {
            'fields': ('current_doctors_count', 'current_secretaries_count', 'is_effectively_active'),
        }),
        ('Admin', {
            'fields': ('activated_by', 'notes'),
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
        }),
    )

    @admin.display(description='Status', ordering='status')
    def status_badge(self, obj):
        colors = {'ACTIVE': 'green', 'EXPIRED': 'orange', 'SUSPENDED': 'red'}
        color = colors.get(obj.status, 'gray')
        return format_html('<b style="color:{};">{}</b>', color, obj.get_status_display())

    @admin.display(boolean=True, description='Effectively Active')
    def is_effectively_active(self, obj):
        return obj.is_effectively_active()

    @admin.display(description='Doctors (active)')
    def current_doctors_count(self, obj):
        return obj.current_doctors_count()

    @admin.display(description='Secretaries (active)')
    def current_secretaries_count(self, obj):
        return obj.current_secretaries_count()

    def _update_subscriptions(self, request, queryset, status=None, extend_days=None):
        updated = 0
        for sub in queryset:
            if status:
                sub.status = status
            if extend_days:
                base = max(sub.expires_at, timezone.now())
                sub.expires_at = base + timezone.timedelta(days=extend_days)
                sub.status = "ACTIVE"
            sub.activated_by = request.user
            sub.save()
            updated += 1
        self.message_user(request, f"{updated} subscription(s) updated.")

    @admin.action(description='✅ Activate selected subscriptions')
    def action_activate(self, request, queryset):
        self._update_subscriptions(request, queryset, status="ACTIVE")

    @admin.action(description='🚫 Suspend selected subscriptions')
    def action_suspend(self, request, queryset):
        self._update_subscriptions(request, queryset, status="SUSPENDED")

    @admin.action(description='📅 Extend by 30 days (and activate)')
    def action_extend_30(self, request, queryset):
        self._update_subscriptions(request, queryset, extend_days=30)

    @admin.action(description='📅 Extend by 365 days (and activate)')
    def action_extend_365(self, request, queryset):
        self._update_subscriptions(request, queryset, extend_days=365)


@admin.register(ClinicHoliday)
class ClinicHolidayAdmin(admin.ModelAdmin):
    list_display = ['clinic', 'title', 'start_date', 'end_date', 'is_active', 'created_by', 'created_at']
    list_filter = ['is_active', 'clinic', 'start_date']
    search_fields = ['clinic__name', 'title']
    readonly_fields = ['created_at', 'updated_at']
    date_hierarchy = 'start_date'

    fieldsets = (
        ('Holiday Details', {
            'fields': ('clinic', 'title', 'start_date', 'end_date', 'is_active')
        }),
        ('Metadata', {
            'fields': ('created_by', 'created_at', 'updated_at'),
        }),
    )


@admin.register(DoctorAvailabilityException)
class DoctorAvailabilityExceptionAdmin(admin.ModelAdmin):
    list_display = ['doctor', 'clinic', 'start_date', 'end_date', 'reason', 'is_active', 'created_by', 'created_at']
    list_filter = ['is_active', 'clinic', 'start_date']
    search_fields = ['doctor__name', 'clinic__name', 'reason']
    readonly_fields = ['created_at', 'updated_at']
    date_hierarchy = 'start_date'

    fieldsets = (
        ('Exception Details', {
            'fields': ('doctor', 'clinic', 'start_date', 'end_date', 'reason', 'is_active')
        }),
        ('Metadata', {
            'fields': ('created_by', 'created_at', 'updated_at'),
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



class DrugProductInline(admin.TabularInline):
    model = DrugProduct
    extra = 0
    fields = ['generic_name', 'commercial_name', 'default_dosage', 'default_frequency', 'default_duration', 'is_active']


@admin.register(DrugFamily)
class DrugFamilyAdmin(admin.ModelAdmin):
    list_display = ['name', 'clinic', 'created_at']
    list_filter = ['clinic']
    search_fields = ['name', 'clinic__name']
    inlines = [DrugProductInline]


@admin.register(DrugProduct)
class DrugProductAdmin(admin.ModelAdmin):
    list_display = ['generic_name', 'commercial_name', 'family', 'clinic', 'is_active', 'created_at']
    list_filter = ['clinic', 'family', 'is_active']
    search_fields = ['generic_name', 'commercial_name']


@admin.register(OrderCatalogItem)
class OrderCatalogItemAdmin(admin.ModelAdmin):
    list_display = ['name', 'category', 'clinic', 'is_active', 'created_at']
    list_filter = ['clinic', 'category', 'is_active']
    search_fields = ['name']
