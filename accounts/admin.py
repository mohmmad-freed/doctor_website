from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import CustomUser, City, IdentityClaim
from .services.identity_claim_service import reject_national_id, verify_national_id


@admin.register(City)
class CityAdmin(admin.ModelAdmin):
    list_display = ['name']
    search_fields = ['name']


@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    model = CustomUser
    list_display = ['phone', 'name', 'email', 'role', 'get_roles_display', 'city', 'is_staff', 'is_active']
    list_filter = ['role', 'city', 'is_staff', 'is_active']

    @admin.display(description="All Roles")
    def get_roles_display(self, obj):
        return ", ".join(obj.roles) if obj.roles else "-"

    fieldsets = (
        (None, {'fields': ('phone', 'password')}),
        ('Personal Info', {'fields': ('name', 'email', 'national_id', 'city', 'role', 'roles')}),
        ('Permissions', {'fields': ('is_staff', 'is_active', 'is_superuser', 'groups', 'user_permissions')}),
        ('Important dates', {'fields': ('last_login', 'date_joined')}),
    )

    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('phone', 'name', 'email', 'national_id', 'city', 'role', 'roles', 'password1', 'password2', 'is_staff', 'is_active')}
        ),
    )

    search_fields = ('phone', 'name', 'email', 'national_id')
    ordering = ('-date_joined',)


@admin.register(IdentityClaim)
class IdentityClaimAdmin(admin.ModelAdmin):
    list_display = [
        "user",
        "national_id",
        "status",
        "verified_by",
        "verified_at",
        "created_at",
    ]
    list_filter = ["status", "created_at"]
    search_fields = ["user__phone", "user__name", "national_id"]
    raw_id_fields = ["user", "verified_by"]
    readonly_fields = ["created_at", "updated_at", "verified_at"]
    actions = ["approve_claims", "reject_claims"]

    @admin.action(description="Approve selected identity claims")
    def approve_claims(self, request, queryset):
        approved = 0
        for claim in queryset:
            try:
                verify_national_id(claim.id, request.user)
                approved += 1
            except Exception:
                continue
        self.message_user(request, f"Approved {approved} claim(s).")

    @admin.action(description="Reject selected identity claims")
    def reject_claims(self, request, queryset):
        rejected = 0
        for claim in queryset:
            try:
                reject_national_id(claim.id, request.user, reason="Rejected from admin bulk action.")
                rejected += 1
            except Exception:
                continue
        self.message_user(request, f"Rejected {rejected} claim(s).")
