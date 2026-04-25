
from rest_framework import serializers
from django.contrib.auth.password_validation import validate_password
from django.utils import timezone
from django.core.validators import RegexValidator
import re

from .models import User, Vehicle, Policy, Claim, ClaimDocument, ClaimAssessment, Notification, AuditLog


# ─────────────────────────────────────────────────────────────────────────────
# SHARED VALIDATION HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def validate_not_future_date(value, field_name='Date'):
    """Reject any date that is in the future."""
    if value and value > timezone.now().date():
        raise serializers.ValidationError(
            f'{field_name} cannot be in the future. Please enter the actual date of the incident.'
        )
    return value


def validate_not_too_old(value, years=10, field_name='Date'):
    """Reject dates that are unreasonably old (default 10 years)."""
    from datetime import date
    cutoff = date(timezone.now().year - years, 1, 1)
    if value and value < cutoff:
        raise serializers.ValidationError(
            f'{field_name} seems too far in the past. Please check the date you entered.'
        )
    return value


def validate_sentence_limit(value, max_sentences=2, field_name='Description'):
    """
    Limits text to a maximum number of sentences to prevent database overload.
    A sentence ends with . ! or ?
    Also ensures the text is meaningful (not random characters).
    """
    if not value or not value.strip():
        raise serializers.ValidationError(f'{field_name} cannot be empty.')

    text = value.strip()

    # Check for meaningless input — must have at least some real words
    words = [w for w in text.split() if re.match(r'^[a-zA-Z]{2,}', w)]
    if len(words) < 3:
        raise serializers.ValidationError(
            f'{field_name} must be a meaningful description using proper words (at least 3 words).'
        )

    # Count sentences by splitting on . ! ?
    sentences = [s.strip() for s in re.split(r'[.!?]+', text) if s.strip()]
    if len(sentences) > max_sentences:
        raise serializers.ValidationError(
            f'{field_name} must not exceed {max_sentences} sentence(s). '
            f'You entered {len(sentences)} sentences. Please be concise.'
        )

    return text


def validate_phone_number(value, field_name='Phone number'):
    """
    Validates phone number:
    - Digits only (optionally starting with +)
    - Between 7 and 10 digits (not more than 10)
    - Must look like a real number (not all same digit)
    """
    if not value or not value.strip():
        return value  # optional fields can be blank

    cleaned = value.strip()

    # Strip leading + for length check
    digits_only = re.sub(r'^\+', '', cleaned).replace(' ', '').replace('-', '')

    if not digits_only.isdigit():
        raise serializers.ValidationError(
            f'{field_name} must contain digits only (optionally starting with +).'
        )

    if len(digits_only) < 7:
        raise serializers.ValidationError(
            f'{field_name} is too short. Please enter at least 7 digits.'
        )

    if len(digits_only) > 10:
        raise serializers.ValidationError(
            f'{field_name} must not exceed 10 digits. You entered {len(digits_only)} digits.'
        )

    # Reject obviously fake numbers like 0000000000 or 1111111111
    if len(set(digits_only)) == 1:
        raise serializers.ValidationError(
            f'{field_name} does not look like a valid phone number.'
        )

    return cleaned


def validate_name_field(value, field_name='Name'):
    """Names should only contain letters, spaces, hyphens and apostrophes."""
    if not value or not value.strip():
        raise serializers.ValidationError(f'{field_name} cannot be empty.')
    if not re.match(r"^[a-zA-Z\s\-']+$", value.strip()):
        raise serializers.ValidationError(
            f'{field_name} must contain letters only (spaces, hyphens and apostrophes are allowed).'
        )
    if len(value.strip()) < 2:
        raise serializers.ValidationError(
            f'{field_name} must be at least 2 characters.'
        )
    return value.strip()


def validate_license_plate_format(value):
    """Zimbabwe license plate format: e.g. ABC 1234 or ABD1234."""
    cleaned = value.strip().upper().replace(' ', '')
    if not re.match(r'^[A-Z]{2,3}\d{3,4}[A-Z]?$', cleaned):
        raise serializers.ValidationError(
            'License plate format appears invalid. Expected format: e.g. ABC1234 or ABD123.'
        )
    return value.upper().strip()


# ─────────────────────────────────────────────────────────────────────────────
# AUTH SERIALIZERS
# ─────────────────────────────────────────────────────────────────────────────

class UserRegistrationSerializer(serializers.ModelSerializer):
    """Used for new client sign-up."""
    password         = serializers.CharField(write_only=True, validators=[validate_password])
    confirm_password = serializers.CharField(write_only=True)

    class Meta:
        model  = User
        fields = [
            'email', 'password', 'confirm_password',
            'first_name', 'last_name', 'phone_number',
            'date_of_birth', 'gender', 'national_id', 'address', 'city',
        ]

    def validate_first_name(self, value):
        return validate_name_field(value, 'First name')

    def validate_last_name(self, value):
        return validate_name_field(value, 'Last name')

    def validate_phone_number(self, value):
        return validate_phone_number(value, 'Phone number')

    def validate_date_of_birth(self, value):
        """User must be at least 18 years old and cannot be born in the future."""
        if not value:
            return value
        today = timezone.now().date()
        if value > today:
            raise serializers.ValidationError('Date of birth cannot be in the future.')
        age = (today - value).days // 365
        if age < 18:
            raise serializers.ValidationError('You must be at least 18 years old to register.')
        if age > 120:
            raise serializers.ValidationError('Date of birth appears to be invalid.')
        return value

    def validate_national_id(self, value):
        """Basic sanity check on national ID — not all spaces or random characters."""
        if value and value.strip():
            if len(value.strip()) < 5:
                raise serializers.ValidationError('National ID appears too short.')
            if not re.search(r'[a-zA-Z0-9]', value):
                raise serializers.ValidationError('National ID must contain letters or numbers.')
        return value

    def validate_city(self, value):
        if value and not re.match(r"^[a-zA-Z\s\-']+$", value.strip()):
            raise serializers.ValidationError('City name must contain letters only.')
        return value

    def validate(self, attrs):
        if attrs['password'] != attrs.pop('confirm_password'):
            raise serializers.ValidationError({'confirm_password': 'Passwords do not match.'})
        return attrs

    def create(self, validated_data):
        user = User.objects.create_user(**validated_data)
        return user


class UserLoginSerializer(serializers.Serializer):
    """Validates email + password for login."""
    email    = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    def validate_email(self, value):
        if not value or not value.strip():
            raise serializers.ValidationError('Email address is required.')
        return value.lower().strip()


class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True, validators=[validate_password])
    confirm_new  = serializers.CharField(write_only=True)

    def validate_old_password(self, value):
        if not value or not value.strip():
            raise serializers.ValidationError('Current password is required.')
        return value

    def validate(self, attrs):
        if attrs['new_password'] != attrs['confirm_new']:
            raise serializers.ValidationError({'confirm_new': 'Passwords do not match.'})
        if attrs['new_password'] == attrs['old_password']:
            raise serializers.ValidationError({'new_password': 'New password must be different from your current password.'})
        return attrs


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value):
        return value.lower().strip()


class PasswordResetConfirmSerializer(serializers.Serializer):
    token        = serializers.UUIDField()
    new_password = serializers.CharField(write_only=True, validators=[validate_password])
    confirm_new  = serializers.CharField(write_only=True)

    def validate(self, attrs):
        if attrs['new_password'] != attrs['confirm_new']:
            raise serializers.ValidationError({'confirm_new': 'Passwords do not match.'})
        return attrs


# ─────────────────────────────────────────────────────────────────────────────
# USER SERIALIZERS
# ─────────────────────────────────────────────────────────────────────────────

class UserProfileSerializer(serializers.ModelSerializer):
    """Full profile — read & update."""
    full_name = serializers.SerializerMethodField()

    class Meta:
        model  = User
        fields = [
            'id', 'email', 'role', 'full_name',
            'first_name', 'last_name', 'phone_number',
            'date_of_birth', 'gender', 'national_id',
            'address', 'city', 'profile_photo',
            'is_email_verified', 'date_joined', 'last_login',
        ]
        read_only_fields = ['id', 'email', 'role', 'is_email_verified', 'date_joined', 'last_login']

    def get_full_name(self, obj):
        return obj.get_full_name()

    def validate_first_name(self, value):
        return validate_name_field(value, 'First name')

    def validate_last_name(self, value):
        return validate_name_field(value, 'Last name')

    def validate_phone_number(self, value):
        return validate_phone_number(value, 'Phone number')

    def validate_city(self, value):
        if value and not re.match(r"^[a-zA-Z\s\-']+$", value.strip()):
            raise serializers.ValidationError('City name must contain letters only.')
        return value


class UserMinimalSerializer(serializers.ModelSerializer):
    """Lightweight user info used in nested contexts."""
    full_name = serializers.SerializerMethodField()

    class Meta:
        model  = User
        fields = ['id', 'full_name', 'email', 'phone_number', 'role']

    def get_full_name(self, obj):
        return obj.get_full_name()


# ─────────────────────────────────────────────────────────────────────────────
# VEHICLE SERIALIZERS
# ─────────────────────────────────────────────────────────────────────────────

class VehicleSerializer(serializers.ModelSerializer):
    owner_name = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model  = Vehicle
        fields = [
            'id', 'owner', 'owner_name', 'make', 'model', 'year',
            'color', 'body_type', 'license_plate', 'vin_number',
            'engine_number', 'chassis_number', 'is_active',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'owner', 'owner_name', 'created_at', 'updated_at']

    def get_owner_name(self, obj):
        return obj.owner.get_full_name()

    def validate_make(self, value):
        if not value or not value.strip():
            raise serializers.ValidationError('Vehicle make (e.g. Toyota) is required.')
        if not re.match(r"^[a-zA-Z0-9\s\-]+$", value.strip()):
            raise serializers.ValidationError('Vehicle make must contain letters or numbers only.')
        return value.strip().title()

    def validate_model(self, value):
        if not value or not value.strip():
            raise serializers.ValidationError('Vehicle model (e.g. Corolla) is required.')
        if not re.match(r"^[a-zA-Z0-9\s\-]+$", value.strip()):
            raise serializers.ValidationError('Vehicle model must contain letters or numbers only.')
        return value.strip().title()

    def validate_color(self, value):
        if not value or not value.strip():
            raise serializers.ValidationError('Vehicle color is required.')
        if not re.match(r"^[a-zA-Z\s\-]+$", value.strip()):
            raise serializers.ValidationError('Color must contain letters only (e.g. Silver, Dark Blue).')
        return value.strip().title()

    def validate_license_plate(self, value):
        return validate_license_plate_format(value)

    def validate_year(self, value):
        current_year = timezone.now().year
        if value < 1980 or value > current_year + 1:
            raise serializers.ValidationError(
                f'Vehicle year must be between 1980 and {current_year + 1}.'
            )
        return value

    def validate_vin_number(self, value):
        if value and value.strip():
            cleaned = value.strip()
            if len(cleaned) != 17:
                raise serializers.ValidationError(
                    'VIN number must be exactly 17 characters.'
                )
            if not re.match(r'^[A-HJ-NPR-Z0-9]{17}$', cleaned.upper()):
                raise serializers.ValidationError(
                    'VIN number contains invalid characters. VINs use letters (except I, O, Q) and digits.'
                )
            return cleaned.upper()
        return value


# ─────────────────────────────────────────────────────────────────────────────
# POLICY SERIALIZERS
# ─────────────────────────────────────────────────────────────────────────────

class PolicySerializer(serializers.ModelSerializer):
    client_name  = serializers.SerializerMethodField(read_only=True)
    vehicle_info = VehicleSerializer(source='vehicle', read_only=True)
    is_active    = serializers.BooleanField(read_only=True)

    class Meta:
        model  = Policy
        fields = [
            'id', 'policy_number', 'client', 'client_name',
            'vehicle', 'vehicle_info', 'cover_type', 'status',
            'sum_insured', 'premium_amount', 'excess_amount',
            'start_date', 'end_date', 'is_active',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'policy_number', 'client', 'created_at', 'updated_at']

    def get_client_name(self, obj):
        return obj.client.get_full_name()

    def validate_start_date(self, value):
        return validate_not_future_date(value, 'Policy start date')

    def validate(self, attrs):
        start = attrs.get('start_date')
        end   = attrs.get('end_date')
        if start and end and end <= start:
            raise serializers.ValidationError({'end_date': 'Policy end date must be after the start date.'})
        return attrs


# ─────────────────────────────────────────────────────────────────────────────
# CLAIM DOCUMENT SERIALIZERS
# ─────────────────────────────────────────────────────────────────────────────

class ClaimDocumentSerializer(serializers.ModelSerializer):
    file_url = serializers.SerializerMethodField()

    class Meta:
        model  = ClaimDocument
        fields = [
            'id', 'claim', 'uploaded_by', 'document_type',
            'file', 'file_url', 'file_name', 'file_size', 'mime_type',
            'description', 'ai_analysis', 'is_processed', 'created_at',
        ]
        read_only_fields = ['id', 'uploaded_by', 'file_name', 'file_size', 'ai_analysis', 'is_processed', 'created_at']

    def get_file_url(self, obj):
        request = self.context.get('request')
        if obj.file and request:
            return request.build_absolute_uri(obj.file.url)
        return None

    def validate_file(self, value):
        max_size = 10 * 1024 * 1024  # 10 MB
        allowed  = ['image/jpeg', 'image/png', 'image/webp', 'application/pdf']
        if value.size > max_size:
            raise serializers.ValidationError('File too large. Maximum size is 10 MB.')
        if hasattr(value, 'content_type') and value.content_type not in allowed:
            raise serializers.ValidationError('Unsupported file type. Allowed: JPEG, PNG, WEBP, PDF.')
        return value

    def validate_description(self, value):
        """Optional description — limit to 1 sentence if provided."""
        if value and value.strip():
            words = [w for w in value.split() if re.match(r'^[a-zA-Z]{2,}', w)]
            if len(words) < 2:
                raise serializers.ValidationError(
                    'Document description must be meaningful (at least 2 words).'
                )
            sentences = [s.strip() for s in re.split(r'[.!?]+', value) if s.strip()]
            if len(sentences) > 1:
                raise serializers.ValidationError(
                    'Document description must be one sentence only.'
                )
        return value


class ClaimDocumentUploadSerializer(serializers.ModelSerializer):
    """Used for uploading documents to an existing claim."""

    class Meta:
        model  = ClaimDocument
        fields = [
            'id', 'claim', 'document_type', 'file',
            'description',
        ]
        read_only_fields = ['id']

    def validate_file(self, value):
        max_size = 10 * 1024 * 1024  # 10 MB
        allowed  = ['image/jpeg', 'image/png', 'image/webp', 'application/pdf']
        if value.size > max_size:
            raise serializers.ValidationError('File too large. Maximum size is 10 MB.')
        if hasattr(value, 'content_type') and value.content_type not in allowed:
            raise serializers.ValidationError('Unsupported file type. Allowed: JPEG, PNG, WEBP, PDF.')
        return value

    def validate_description(self, value):
        if value and value.strip():
            words = [w for w in value.split() if re.match(r'^[a-zA-Z]{2,}', w)]
            if len(words) < 2:
                raise serializers.ValidationError(
                    'Document description must be meaningful (at least 2 words).'
                )
            sentences = [s.strip() for s in re.split(r'[.!?]+', value) if s.strip()]
            if len(sentences) > 2:
                raise serializers.ValidationError(
                    'Document description must not exceed 2 sentences.'
                )
        return value

    def create(self, validated_data):
        request = self.context.get('request')
        if request and hasattr(request, 'user'):
            validated_data['uploaded_by'] = request.user
        file = validated_data.get('file')
        if file:
            validated_data['file_name'] = file.name
            validated_data['file_size'] = file.size
            if hasattr(file, 'content_type'):
                validated_data['mime_type'] = file.content_type
        return super().create(validated_data)


# ─────────────────────────────────────────────────────────────────────────────
# CLAIM ASSESSMENT SERIALIZERS
# ─────────────────────────────────────────────────────────────────────────────

class ClaimAssessmentSerializer(serializers.ModelSerializer):
    assessor_name  = serializers.SerializerMethodField(read_only=True)
    total_estimate = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model  = ClaimAssessment
        fields = [
            'id', 'claim', 'assessor', 'assessor_name', 'assessment_type',
            'damage_description', 'damage_severity', 'damaged_parts',
            'parts_cost', 'labour_cost', 'paint_cost', 'other_costs', 'total_estimate',
            'recommended_workshop', 'workshop_contact',
            'is_final', 'notes', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'assessor', 'assessor_name', 'total_estimate', 'created_at', 'updated_at']

    def get_assessor_name(self, obj):
        return obj.assessor.get_full_name() if obj.assessor else 'AI System'

    def validate_damage_description(self, value):
        return validate_sentence_limit(value, max_sentences=3, field_name='Damage description')

    def validate_workshop_contact(self, value):
        return validate_phone_number(value, 'Workshop contact number')

    def validate_recommended_workshop(self, value):
        if value and value.strip():
            if not re.match(r"^[a-zA-Z0-9\s\-'&.]+$", value.strip()):
                raise serializers.ValidationError(
                    'Workshop name contains invalid characters.'
                )
        return value

    def validate_parts_cost(self, value):
        if value < 0:
            raise serializers.ValidationError('Parts cost cannot be negative.')
        return value

    def validate_labour_cost(self, value):
        if value < 0:
            raise serializers.ValidationError('Labour cost cannot be negative.')
        return value

    def validate_paint_cost(self, value):
        if value < 0:
            raise serializers.ValidationError('Paint cost cannot be negative.')
        return value

    def validate_other_costs(self, value):
        if value < 0:
            raise serializers.ValidationError('Other costs cannot be negative.')
        return value

    def validate(self, attrs):
        parts  = attrs.get('parts_cost', 0)
        labour = attrs.get('labour_cost', 0)
        paint  = attrs.get('paint_cost', 0)
        other  = attrs.get('other_costs', 0)
        attrs['total_estimate'] = parts + labour + paint + other
        return attrs


# ─────────────────────────────────────────────────────────────────────────────
# CLAIM SERIALIZERS
# ─────────────────────────────────────────────────────────────────────────────

class ClaimListSerializer(serializers.ModelSerializer):
    """Compact version for listing claims (dashboard/table view)."""
    client_name     = serializers.SerializerMethodField()
    vehicle_display = serializers.SerializerMethodField()
    processing_days = serializers.SerializerMethodField()

    class Meta:
        model  = Claim
        fields = [
            'id', 'claim_number', 'client', 'client_name',
            'vehicle', 'vehicle_display', 'incident_type', 'incident_date',
            'status', 'claimed_amount', 'approved_amount',
            'submitted_at', 'processing_days', 'created_at',
        ]

    def get_client_name(self, obj):
        return obj.client.get_full_name()

    def get_vehicle_display(self, obj):
        return str(obj.vehicle)

    def get_processing_days(self, obj):
        return obj.get_processing_days()


class ClaimDetailSerializer(serializers.ModelSerializer):
    """Full detail — used for individual claim view."""
    client_info      = UserMinimalSerializer(source='client', read_only=True)
    vehicle_info     = VehicleSerializer(source='vehicle', read_only=True)
    policy_info      = PolicySerializer(source='policy', read_only=True)
    adjuster_info    = UserMinimalSerializer(source='assigned_adjuster', read_only=True)
    documents        = ClaimDocumentSerializer(many=True, read_only=True)
    assessments      = ClaimAssessmentSerializer(many=True, read_only=True)
    processing_days  = serializers.SerializerMethodField()

    class Meta:
        model  = Claim
        fields = [
            'id', 'claim_number',
            'client', 'client_info',
            'policy', 'policy_info',
            'vehicle', 'vehicle_info',
            'assigned_adjuster', 'adjuster_info',
            'incident_type', 'incident_date', 'incident_time',
            'incident_location', 'incident_description',
            'fault_type', 'police_report_filed', 'police_report_number',
            'injuries_reported', 'injury_description',
            'third_party_involved', 'third_party_name', 'third_party_phone',
            'third_party_license_plate', 'third_party_insurer',
            'claimed_amount', 'approved_amount', 'excess_applied', 'settlement_amount',
            'status', 'submitted_at', 'reviewed_at', 'decided_at', 'settled_at',
            'decline_reason', 'adjuster_notes',
            'ai_damage_summary', 'ai_fraud_score', 'ai_estimated_repair',
            'documents', 'assessments', 'processing_days',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'claim_number', 'client',
            'submitted_at', 'reviewed_at', 'decided_at', 'settled_at',
            'ai_damage_summary', 'ai_fraud_score', 'ai_estimated_repair',
            'created_at', 'updated_at',
        ]

    def get_processing_days(self, obj):
        return obj.get_processing_days()


class ClaimSubmitSerializer(serializers.ModelSerializer):
    """Used by client to submit a new claim — full validation applied."""

    class Meta:
        model  = Claim
        fields = [
            'policy', 'vehicle',
            'incident_type', 'incident_date', 'incident_time',
            'incident_location', 'incident_description',
            'fault_type', 'police_report_filed', 'police_report_number',
            'injuries_reported', 'injury_description',
            'third_party_involved', 'third_party_name', 'third_party_phone',
            'third_party_license_plate', 'third_party_insurer',
            'claimed_amount',
        ]

    # ── Field-level validators ────────────────────────────────────────────────

    def validate_incident_date(self, value):
        """
        1. Cannot be in the future.
        2. Cannot be more than 10 years ago (likely a mistake).
        """
        value = validate_not_future_date(value, 'Incident date')
        value = validate_not_too_old(value, years=10, field_name='Incident date')
        return value

    def validate_incident_location(self, value):
        """Must be a meaningful location, not random characters."""
        if not value or not value.strip():
            raise serializers.ValidationError('Incident location is required.')
        if len(value.strip()) < 5:
            raise serializers.ValidationError(
                'Incident location is too short. Please enter a recognisable address or area.'
            )
        words = [w for w in value.split() if re.match(r'^[a-zA-Z]{2,}', w)]
        if len(words) < 1:
            raise serializers.ValidationError(
                'Incident location must contain at least one recognisable place name.'
            )
        return value.strip()

    def validate_incident_description(self, value):
        """
        Must be meaningful and no more than 2 sentences
        to keep the database clean and responses concise.
        """
        return validate_sentence_limit(value, max_sentences=2, field_name='Incident description')

    def validate_injury_description(self, value):
        """Optional but if provided, must be meaningful and max 2 sentences."""
        if value and value.strip():
            return validate_sentence_limit(value, max_sentences=2, field_name='Injury description')
        return value

    def validate_police_report_number(self, value):
        """If provided, must look like a real reference number."""
        if value and value.strip():
            if len(value.strip()) < 4:
                raise serializers.ValidationError(
                    'Police report number appears too short. Please enter the full reference number.'
                )
            if not re.search(r'[a-zA-Z0-9]', value):
                raise serializers.ValidationError(
                    'Police report number must contain letters or digits.'
                )
        return value

    def validate_claimed_amount(self, value):
        """Amount must be positive and not absurdly large."""
        if value is not None:
            if value <= 0:
                raise serializers.ValidationError(
                    'Claimed amount must be greater than zero.'
                )
            if value > 10_000_000:
                raise serializers.ValidationError(
                    'Claimed amount exceeds the maximum allowed value. Please verify the amount.'
                )
        return value

    def validate_third_party_phone(self, value):
        return validate_phone_number(value, 'Third party phone number')

    def validate_third_party_name(self, value):
        if value and value.strip():
            if not re.match(r"^[a-zA-Z\s\-']+$", value.strip()):
                raise serializers.ValidationError(
                    'Third party name must contain letters only.'
                )
            if len(value.strip()) < 2:
                raise serializers.ValidationError(
                    'Third party name is too short.'
                )
        return value

    def validate_third_party_license_plate(self, value):
        if value and value.strip():
            return validate_license_plate_format(value)
        return value

    def validate_third_party_insurer(self, value):
        if value and value.strip():
            if not re.match(r"^[a-zA-Z0-9\s\-'&.]+$", value.strip()):
                raise serializers.ValidationError(
                    'Third party insurer name contains invalid characters.'
                )
        return value

    # ── Cross-field / object-level validation ─────────────────────────────────

    def validate(self, attrs):
        request = self.context['request']
        policy  = attrs.get('policy')
        vehicle = attrs.get('vehicle')

        # Policy must belong to the submitting client
        if policy and policy.client != request.user:
            raise serializers.ValidationError({'policy': 'You do not own this policy.'})

        # Vehicle must belong to the submitting client
        if vehicle and vehicle.owner != request.user:
            raise serializers.ValidationError({'vehicle': 'You do not own this vehicle.'})

        # Policy must currently be active
        if policy and not policy.is_active:
            raise serializers.ValidationError(
                {'policy': 'This policy is not currently active. You cannot submit a claim on an expired policy.'}
            )

        # Vehicle must match the policy
        if policy and vehicle and policy.vehicle != vehicle:
            raise serializers.ValidationError(
                {'vehicle': 'The selected vehicle does not match the selected policy.'}
            )

        # If third-party involved, at least a name or plate is required
        if attrs.get('third_party_involved'):
            if not attrs.get('third_party_name') and not attrs.get('third_party_license_plate'):
                raise serializers.ValidationError(
                    {'third_party_name': 'If a third party was involved, please provide their name or license plate.'}
                )

        # If injuries reported, description is required
        if attrs.get('injuries_reported') and not attrs.get('injury_description', '').strip():
            raise serializers.ValidationError(
                {'injury_description': 'Please describe the injuries that were reported.'}
            )

        # If police report filed, report number is required
        if attrs.get('police_report_filed') and not attrs.get('police_report_number', '').strip():
            raise serializers.ValidationError(
                {'police_report_number': 'Please provide the police report reference number.'}
            )

        return attrs


class ClaimDecisionSerializer(serializers.Serializer):
    """Used by adjuster/admin to approve or decline a claim."""
    decision        = serializers.ChoiceField(choices=['APPROVED', 'DECLINED'])
    approved_amount = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True)
    excess_applied  = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)
    decline_reason  = serializers.CharField(required=False, allow_blank=True)
    adjuster_notes  = serializers.CharField(required=False, allow_blank=True)

    def validate_approved_amount(self, value):
        if value is not None and value <= 0:
            raise serializers.ValidationError('Approved amount must be greater than zero.')
        return value

    def validate_excess_applied(self, value):
        if value is not None and value < 0:
            raise serializers.ValidationError('Excess amount cannot be negative.')
        return value

    def validate_decline_reason(self, value):
        """Decline reason must be a meaningful explanation."""
        if value and value.strip():
            words = [w for w in value.split() if re.match(r'^[a-zA-Z]{2,}', w)]
            if len(words) < 3:
                raise serializers.ValidationError(
                    'Decline reason must be a meaningful explanation (at least 3 words).'
                )
        return value

    def validate(self, attrs):
        if attrs['decision'] == 'APPROVED' and not attrs.get('approved_amount'):
            raise serializers.ValidationError(
                {'approved_amount': 'Approved amount is required when approving a claim.'}
            )
        if attrs['decision'] == 'DECLINED' and not attrs.get('decline_reason', '').strip():
            raise serializers.ValidationError(
                {'decline_reason': 'A reason must be provided when declining a claim.'}
            )
        return attrs


# ─────────────────────────────────────────────────────────────────────────────
# NOTIFICATION SERIALIZER
# ─────────────────────────────────────────────────────────────────────────────

class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Notification
        fields = [
            'id', 'recipient', 'claim', 'notification_type', 'channel',
            'subject', 'body', 'delivery_status', 'sent_at',
            'is_read', 'created_at',
        ]
        read_only_fields = fields


# ─────────────────────────────────────────────────────────────────────────────
# DASHBOARD / REPORT SERIALIZERS
# ─────────────────────────────────────────────────────────────────────────────

class DashboardStatsSerializer(serializers.Serializer):
    """Summary statistics for the dashboard."""
    total_claims          = serializers.IntegerField()
    pending_claims        = serializers.IntegerField()
    approved_claims       = serializers.IntegerField()
    declined_claims       = serializers.IntegerField()
    settled_claims        = serializers.IntegerField()
    total_claimed_amount  = serializers.DecimalField(max_digits=14, decimal_places=2)
    total_approved_amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    avg_processing_days   = serializers.FloatField()
    claims_this_month     = serializers.IntegerField()


class ClaimReportSerializer(serializers.Serializer):
    """Data structure for generated PDF/Excel report."""
    claim_number       = serializers.CharField()
    client_name        = serializers.CharField()
    policy_number      = serializers.CharField()
    vehicle            = serializers.CharField()
    incident_date      = serializers.DateField()
    incident_type      = serializers.CharField()
    incident_location  = serializers.CharField()
    status             = serializers.CharField()
    claimed_amount     = serializers.DecimalField(max_digits=12, decimal_places=2, allow_null=True)
    approved_amount    = serializers.DecimalField(max_digits=12, decimal_places=2, allow_null=True)
    settlement_amount  = serializers.DecimalField(max_digits=12, decimal_places=2, allow_null=True)
    damage_description = serializers.CharField(allow_blank=True)
    estimated_repair   = serializers.DecimalField(max_digits=12, decimal_places=2, allow_null=True)
    adjuster_notes     = serializers.CharField(allow_blank=True)
    submitted_at       = serializers.DateTimeField(allow_null=True)
    decided_at         = serializers.DateTimeField(allow_null=True)
