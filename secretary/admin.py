from django.contrib import admin

from secretary.models import PurchaseRequest, PurchaseRequestItem


class PurchaseRequestItemInline(admin.TabularInline):
    model = PurchaseRequestItem
    extra = 0
    readonly_fields = ("total",)


@admin.register(PurchaseRequest)
class PurchaseRequestAdmin(admin.ModelAdmin):
    list_display = (
        "request_number", "clinic", "title", "category",
        "status", "total", "requested_by", "created_at",
    )
    list_filter = ("status", "category", "clinic")
    search_fields = ("request_number", "title", "requested_by__name")
    readonly_fields = ("request_number", "total", "created_at", "updated_at", "reviewed_at")
    inlines = [PurchaseRequestItemInline]
