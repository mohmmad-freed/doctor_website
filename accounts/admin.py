from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import CustomUser, City


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