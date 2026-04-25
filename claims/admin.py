from django.contrib import admin
"""
Admin Configuration — Motor Insurance Claims System
Registers all models with enhanced Django admin views.
"""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.html import format_html
from .models import User, Vehicle, Policy, Claim, ClaimDocument, ClaimAssessment, Notification, AuditLog


# ─────────────────────────────────────────────────────────────────────────────
# USER ADMIN
# ─────────────────────────────────────────────────────────────────────────────

@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display    = ['email', 'get_full_name', 'role', 'is_active', 'is_email_verified', 'date_joined']
    list_filter     = ['role', 'is_active', 'is_email_verified', 'gender']
    search_fields   = ['email', 'first_name', 'last_name', 'national_id', 'phone_number']
    ordering        = ['-date_joined']
    readonly_fields = ['id', 'date_joined', 'last_login']

    fieldsets = (
        ('Login Credentials', {'fields': ('id', 'email', 'password')}),
        ('Personal Info',     {'fields': ('first_name', 'last_name', 'phone_number', 'date_of_birth', 'gender', 'national_id', 'profile_photo')}),
        ('Address',           {'fields': ('address', 'city')}),
        ('Role & Status',     {'fields': ('role', 'is_active', 'is_staff', 'is_superuser', 'is_email_verified')}),
        ('Important Dates',   {'fields': ('date_joined', 'last_login')}),
    )
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields':  ('email', 'first_name', 'last_name', 'role', 'password1', 'password2'),
        }),
    )


# ─────────────────────────────────────────────────────────────────────────────
# VEHICLE ADMIN
# ─────────────────────────────────────────────────────────────────────────────

@admin.register(Vehicle)
class VehicleAdmin(admin.ModelAdmin):
    list_display  = ['license_plate', 'make', 'model', 'year', 'owner', 'body_type', 'is_active']
    list_filter   = ['body_type', 'is_active', 'year']
    search_fields = ['license_plate', 'make', 'model', 'vin_number', 'owner__email']
    raw_id_fields = ['owner']


# ─────────────────────────────────────────────────────────────────────────────
# POLICY ADMIN
# ─────────────────────────────────────────────────────────────────────────────

@admin.register(Policy)
class PolicyAdmin(admin.ModelAdmin):
    list_display  = ['policy_number', 'client', 'vehicle', 'cover_type', 'status', 'start_date', 'end_date']
    list_filter   = ['cover_type', 'status']
    search_fields = ['policy_number', 'client__email', 'vehicle__license_plate']
    raw_id_fields = ['client', 'vehicle']


# ─────────────────────────────────────────────────────────────────────────────
# CLAIM ADMIN
# ─────────────────────────────────────────────────────────────────────────────

class ClaimDocumentInline(admin.TabularInline):
    model  = ClaimDocument
    extra  = 0
    fields = ['document_type', 'file', 'file_name', 'is_processed']
    readonly_fields = ['file_name', 'is_processed']


class ClaimAssessmentInline(admin.StackedInline):
    model  = ClaimAssessment
    extra  = 0
    fields = ['assessment_type', 'damage_severity', 'total_estimate', 'is_final']
    readonly_fields = ['total_estimate']


@admin.register(Claim)
class ClaimAdmin(admin.ModelAdmin):
    list_display    = ['claim_number', 'client', 'vehicle', 'incident_type', 'status', 'claimed_amount', 'submitted_at', 'processing_days_display']
    list_filter     = ['status', 'incident_type', 'fault_type', 'police_report_filed']
    search_fields   = ['claim_number', 'client__email', 'vehicle__license_plate', 'incident_location']
    raw_id_fields   = ['client', 'policy', 'vehicle', 'assigned_adjuster']
    inlines         = [ClaimDocumentInline, ClaimAssessmentInline]
    readonly_fields = ['id', 'claim_number', 'submitted_at', 'reviewed_at', 'decided_at', 'settled_at', 'created_at', 'updated_at']

    fieldsets = (
        ('Claim Identity',    {'fields': ('id', 'claim_number', 'status', 'client', 'policy', 'vehicle', 'assigned_adjuster')}),
        ('Incident Details',  {'fields': ('incident_type', 'incident_date', 'incident_time', 'incident_location', 'incident_description', 'fault_type')}),
        ('Police & Injuries', {'fields': ('police_report_filed', 'police_report_number', 'injuries_reported', 'injury_description')}),
        ('Third Party',       {'fields': ('third_party_involved', 'third_party_name', 'third_party_phone', 'third_party_license_plate', 'third_party_insurer'), 'classes': ('collapse',)}),
        ('Financials',        {'fields': ('claimed_amount', 'approved_amount', 'excess_applied', 'settlement_amount')}),
        ('Decision',          {'fields': ('decline_reason', 'adjuster_notes', 'decided_at', 'settled_at')}),
        ('AI Analysis',       {'fields': ('ai_damage_summary', 'ai_fraud_score', 'ai_estimated_repair'), 'classes': ('collapse',)}),
        ('Timestamps',        {'fields': ('submitted_at', 'reviewed_at', 'created_at', 'updated_at')}),
    )

    def processing_days_display(self, obj):
        days = obj.get_processing_days()
        if days is None:
            return '—'
        color = 'green' if days <= 3 else ('orange' if days <= 7 else 'red')
        return format_html('<span style="color:{}">{} days</span>', color, days)
    processing_days_display.short_description = 'Processing Days'


# ─────────────────────────────────────────────────────────────────────────────
# NOTIFICATION ADMIN
# ─────────────────────────────────────────────────────────────────────────────

@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display  = ['recipient', 'notification_type', 'channel', 'delivery_status', 'sent_at', 'is_read']
    list_filter   = ['notification_type', 'delivery_status', 'channel', 'is_read']
    search_fields = ['recipient__email', 'subject']
    readonly_fields = ['id', 'sent_at', 'created_at']


# ─────────────────────────────────────────────────────────────────────────────
# AUDIT LOG ADMIN
# ─────────────────────────────────────────────────────────────────────────────

@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display  = ['created_at', 'actor', 'action', 'resource', 'resource_id', 'ip_address']
    list_filter   = ['action', 'resource']
    search_fields = ['actor__email', 'resource_id', 'description']
    readonly_fields = [f.name for f in AuditLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

# Register your models here.
