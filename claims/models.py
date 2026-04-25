from django.db import models
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.utils import timezone
from django.core.validators import RegexValidator, FileExtensionValidator
import uuid
import os


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────



def claim_document_path(instance, filename):
    ext = filename.split('.')[-1]
    unique = uuid.uuid4().hex[:8]
    return f'claims/{instance.claim.claim_number}/documents/{unique}.{ext}'


phone_validator = RegexValidator(
    regex=r'^\+?1?\d{9,15}$',
    message="Phone number must be in format: '+999999999'. Up to 15 digits allowed."
)


# ─────────────────────────────────────────────────────────────────────────────
# USER MANAGER
# ─────────────────────────────────────────────────────────────────────────────

class UserManager(BaseUserManager):

    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError('Email address is required.')
        email = self.normalize_email(email)
        extra_fields.setdefault('is_active', True)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('role', User.Role.ADMIN)
        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')
        return self.create_user(email, password, **extra_fields)


# ─────────────────────────────────────────────────────────────────────────────
# USER MODEL
# ─────────────────────────────────────────────────────────────────────────────

class User(AbstractBaseUser, PermissionsMixin):
    """
    Custom user model using email as the unique identifier.
    Supports three roles: CLIENT, ADJUSTER, ADMIN.
    """

    class Role(models.TextChoices):
        CLIENT   = 'CLIENT',   'Client'
        ADJUSTER = 'ADJUSTER', 'Claims Adjuster'
        GARAGE   = 'GARAGE',   'Garage / Repairer'
        ADMIN    = 'ADMIN',    'Administrator'

    class Gender(models.TextChoices):
        MALE   = 'M', 'Male'
        FEMALE = 'F', 'Female'
        OTHER  = 'O', 'Other'

    # Core auth fields
    id         = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email      = models.EmailField(unique=True, db_index=True)
    role       = models.CharField(max_length=10, choices=Role.choices, default=Role.CLIENT)
    is_active  = models.BooleanField(default=True)
    is_staff   = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)
    last_login = models.DateTimeField(null=True, blank=True)

    # Personal details
    first_name    = models.CharField(max_length=100)
    last_name     = models.CharField(max_length=100)
    phone_number  = models.CharField(max_length=20, validators=[phone_validator], blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    gender        = models.CharField(max_length=1, choices=Gender.choices, blank=True)
    national_id   = models.CharField(max_length=50, blank=True, help_text='National ID or passport number')
    address       = models.TextField(blank=True)
    city          = models.CharField(max_length=100, blank=True)
    profile_photo = models.CharField(max_length=255, blank=True)

    # Email verification
    is_email_verified      = models.BooleanField(default=False)
    email_verification_token = models.UUIDField(default=uuid.uuid4, null=True, blank=True)

    # Password reset
    password_reset_token   = models.UUIDField(null=True, blank=True)
    password_reset_expires = models.DateTimeField(null=True, blank=True)

    objects = UserManager()

    USERNAME_FIELD  = 'email'
    REQUIRED_FIELDS = ['first_name', 'last_name']

    class Meta:
        db_table    = 'users'
        verbose_name = 'User'
        verbose_name_plural = 'Users'
        ordering    = ['-date_joined']

    def __str__(self):
        return f'{self.get_full_name()} <{self.email}>'

    def get_full_name(self):
        return f'{self.first_name} {self.last_name}'.strip()

    def get_short_name(self):
        return self.first_name

    @property
    def is_client(self):
        return self.role == self.Role.CLIENT

    @property
    def is_adjuster(self):
        return self.role == self.Role.ADJUSTER

    @property
    def is_garage(self):
        return self.role == self.Role.GARAGE

    @property
    def is_admin_user(self):
        return self.role == self.Role.ADMIN


# ─────────────────────────────────────────────────────────────────────────────
# VEHICLE MODEL
# ─────────────────────────────────────────────────────────────────────────────

class Vehicle(models.Model):
    """Vehicle details linked to a client."""

    class BodyType(models.TextChoices):
        SEDAN      = 'SEDAN',      'Sedan'
        SUV        = 'SUV',        'SUV'
        HATCHBACK  = 'HATCHBACK',  'Hatchback'
        PICKUP     = 'PICKUP',     'Pickup Truck'
        VAN        = 'VAN',        'Van / Minibus'
        TRUCK      = 'TRUCK',      'Truck'
        MOTORCYCLE = 'MOTORCYCLE', 'Motorcycle'
        OTHER      = 'OTHER',      'Other'

    id             = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner          = models.ForeignKey(User, on_delete=models.CASCADE, related_name='vehicles')
    make           = models.CharField(max_length=100, help_text='e.g. Toyota')
    model          = models.CharField(max_length=100, help_text='e.g. Corolla')
    year           = models.PositiveIntegerField()
    color          = models.CharField(max_length=50)
    body_type      = models.CharField(max_length=15, choices=BodyType.choices, default=BodyType.SEDAN)
    license_plate  = models.CharField(max_length=20, unique=True, db_index=True)
    vin_number     = models.CharField(max_length=17, blank=True, help_text='Vehicle Identification Number')
    engine_number  = models.CharField(max_length=50, blank=True)
    chassis_number = models.CharField(max_length=50, blank=True)
    is_active      = models.BooleanField(default=True)
    created_at     = models.DateTimeField(auto_now_add=True)
    updated_at     = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'vehicles'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.year} {self.make} {self.model} ({self.license_plate})'


# ─────────────────────────────────────────────────────────────────────────────
# POLICY MODEL
# ─────────────────────────────────────────────────────────────────────────────

class Policy(models.Model):
    """Motor insurance policy issued to a client."""

    class CoverType(models.TextChoices):
        COMPREHENSIVE = 'COMPREHENSIVE', 'Comprehensive'
        THIRD_PARTY   = 'THIRD_PARTY',   'Third Party Only'
        FIRE_THEFT    = 'FIRE_THEFT',    'Third Party Fire & Theft'

    class Status(models.TextChoices):
        ACTIVE    = 'ACTIVE',    'Active'
        EXPIRED   = 'EXPIRED',   'Expired'
        SUSPENDED = 'SUSPENDED', 'Suspended'
        CANCELLED = 'CANCELLED', 'Cancelled'

    id             = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    policy_number  = models.CharField(max_length=30, unique=True, db_index=True)
    client         = models.ForeignKey(User, on_delete=models.CASCADE, related_name='policies')
    vehicle        = models.ForeignKey(Vehicle, on_delete=models.CASCADE, related_name='policies')
    cover_type     = models.CharField(max_length=20, choices=CoverType.choices)
    status         = models.CharField(max_length=15, choices=Status.choices, default=Status.ACTIVE)
    sum_insured    = models.DecimalField(max_digits=12, decimal_places=2, help_text='USD')
    premium_amount = models.DecimalField(max_digits=10, decimal_places=2, help_text='Annual premium USD')
    excess_amount  = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, help_text='Excess/deductible USD')
    start_date     = models.DateField()
    end_date       = models.DateField()
    created_at     = models.DateTimeField(auto_now_add=True)
    updated_at     = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'policies'
        ordering = ['-created_at']

    def __str__(self):
        return f'Policy {self.policy_number} — {self.client.get_full_name()}'

    @property
    def is_active(self):
        return self.status == self.Status.ACTIVE and self.end_date >= timezone.now().date()

    def save(self, *args, **kwargs):
        if not self.policy_number:
            self.policy_number = self._generate_policy_number()
        super().save(*args, **kwargs)

    @staticmethod
    def _generate_policy_number():
        import random, string
        prefix = 'ZIM-MOT'
        suffix = ''.join(random.choices(string.digits, k=8))
        return f'{prefix}-{suffix}'


# ─────────────────────────────────────────────────────────────────────────────
# CLAIM MODEL
# ─────────────────────────────────────────────────────────────────────────────

class Claim(models.Model):
    """
    Core claim model — submitted by a client after an accident.
    """

    class Status(models.TextChoices):
        DRAFT       = 'DRAFT',       'Draft'
        SUBMITTED   = 'SUBMITTED',   'Submitted'
        UNDER_REVIEW = 'UNDER_REVIEW', 'Under Review'
        APPROVED    = 'APPROVED',    'Approved'
        DECLINED    = 'DECLINED',    'Declined'
        SETTLED     = 'SETTLED',     'Settled'
        WITHDRAWN   = 'WITHDRAWN',   'Withdrawn'

    class IncidentType(models.TextChoices):
        COLLISION     = 'COLLISION',     'Collision / Accident'
        THEFT         = 'THEFT',         'Theft / Attempted Theft'
        FIRE          = 'FIRE',          'Fire Damage'
        FLOOD         = 'FLOOD',         'Flood / Water Damage'
        VANDALISM     = 'VANDALISM',     'Vandalism'
        ANIMAL        = 'ANIMAL',        'Animal Strike'
        NATURAL       = 'NATURAL',       'Natural Disaster'
        OTHER         = 'OTHER',         'Other'

    class FaultType(models.TextChoices):
        AT_FAULT     = 'AT_FAULT',     'At Fault'
        NOT_AT_FAULT = 'NOT_AT_FAULT', 'Not At Fault'
        PARTIAL      = 'PARTIAL',      'Partial Fault'
        UNKNOWN      = 'UNKNOWN',      'Unknown'

    # Identifiers
    id            = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    claim_number  = models.CharField(max_length=30, unique=True, db_index=True, blank=True)

    # Relationships
    client        = models.ForeignKey(User, on_delete=models.CASCADE, related_name='claims')
    policy        = models.ForeignKey(Policy, on_delete=models.CASCADE, related_name='claims')
    vehicle       = models.ForeignKey(Vehicle, on_delete=models.CASCADE, related_name='claims')
    assigned_adjuster = models.ForeignKey(
        User, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='assigned_claims',
        limit_choices_to={'role': User.Role.ADJUSTER}
    )

    # Incident details
    incident_type      = models.CharField(max_length=20, choices=IncidentType.choices)
    incident_date      = models.DateField()
    incident_time      = models.TimeField(null=True, blank=True)
    incident_location  = models.CharField(max_length=255)
    incident_description = models.TextField(help_text='Detailed description of the accident / incident')
    fault_type         = models.CharField(max_length=15, choices=FaultType.choices, default=FaultType.UNKNOWN)
    police_report_filed = models.BooleanField(default=False)
    police_report_number = models.CharField(max_length=100, blank=True)
    injuries_reported  = models.BooleanField(default=False)
    injury_description = models.TextField(blank=True)

    # Third-party info (if applicable)
    third_party_involved      = models.BooleanField(default=False)
    third_party_name          = models.CharField(max_length=200, blank=True)
    third_party_phone         = models.CharField(max_length=20, blank=True)
    third_party_license_plate = models.CharField(max_length=20, blank=True)
    third_party_insurer       = models.CharField(max_length=200, blank=True)

    # Claim amounts
    claimed_amount    = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    approved_amount   = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    excess_applied    = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    settlement_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    # Workflow
    status         = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    submitted_at   = models.DateTimeField(null=True, blank=True)
    reviewed_at    = models.DateTimeField(null=True, blank=True)
    decided_at     = models.DateTimeField(null=True, blank=True)
    settled_at     = models.DateTimeField(null=True, blank=True)
    decline_reason = models.TextField(blank=True)
    adjuster_notes = models.TextField(blank=True)

    # AI assessment
    ai_damage_summary   = models.TextField(blank=True, help_text='AI-generated damage summary')
    ai_fraud_score      = models.FloatField(null=True, blank=True, help_text='0.0–1.0 fraud probability')
    ai_estimated_repair = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'claims'
        ordering = ['-created_at']

    def __str__(self):
        return f'Claim {self.claim_number} — {self.client.get_full_name()} ({self.status})'

    def save(self, *args, **kwargs):
        if not self.claim_number:
            self.claim_number = self._generate_claim_number()
        # Auto-set submitted_at timestamp
        if self.status == self.Status.SUBMITTED and not self.submitted_at:
            self.submitted_at = timezone.now()
        super().save(*args, **kwargs)

    @staticmethod
    def _generate_claim_number():
        import random
        year = timezone.now().year
        seq  = random.randint(100000, 999999)
        return f'CLM-{year}-{seq}'

    def get_processing_days(self):
        """Returns number of days since submission."""
        if self.submitted_at:
            return (timezone.now() - self.submitted_at).days
        return None

    @property
    def is_open(self):
        return self.status in [self.Status.DRAFT, self.Status.SUBMITTED, self.Status.UNDER_REVIEW]


# ─────────────────────────────────────────────────────────────────────────────
# CLAIM DOCUMENT MODEL
# ─────────────────────────────────────────────────────────────────────────────

class ClaimDocument(models.Model):
    """
    Documents and photos uploaded as evidence for a claim.
    e.g. accident photos, police report scan, repair quotes.
    """

    class DocumentType(models.TextChoices):
        ACCIDENT_PHOTO   = 'ACCIDENT_PHOTO',   'Accident Photo'
        VEHICLE_PHOTO    = 'VEHICLE_PHOTO',     'Vehicle Damage Photo'
        POLICE_REPORT    = 'POLICE_REPORT',     'Police Report'
        DRIVERS_LICENSE  = 'DRIVERS_LICENSE',   "Driver's License"
        REPAIR_QUOTE     = 'REPAIR_QUOTE',      'Repair Quotation'
        MEDICAL_REPORT   = 'MEDICAL_REPORT',    'Medical Report'
        THIRD_PARTY_DOC  = 'THIRD_PARTY_DOC',  'Third Party Document'
        OTHER            = 'OTHER',             'Other'

    id            = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    claim         = models.ForeignKey(Claim, on_delete=models.CASCADE, related_name='documents')
    uploaded_by   = models.ForeignKey(User, on_delete=models.CASCADE, related_name='uploaded_documents')
    document_type = models.CharField(max_length=20, choices=DocumentType.choices)
    file          = models.FileField(
        upload_to=claim_document_path,
        validators=[FileExtensionValidator(allowed_extensions=['jpg', 'jpeg', 'png', 'webp', 'pdf'])]
    )
    file_name     = models.CharField(max_length=255, blank=True)
    file_size     = models.PositiveIntegerField(default=0, help_text='Size in bytes')
    mime_type     = models.CharField(max_length=100, blank=True)
    description   = models.CharField(max_length=255, blank=True)

    # AI analysis results for images
    ai_analysis   = models.JSONField(null=True, blank=True, help_text='AI damage analysis result')
    is_processed  = models.BooleanField(default=False)

    created_at    = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'claim_documents'
        ordering = ['created_at']

    def __str__(self):
        return f'{self.get_document_type_display()} — {self.claim.claim_number}'

    def save(self, *args, **kwargs):
        if self.file and not self.file_name:
            self.file_name = os.path.basename(self.file.name)
        super().save(*args, **kwargs)


# ─────────────────────────────────────────────────────────────────────────────
# CLAIM ASSESSMENT MODEL
# ─────────────────────────────────────────────────────────────────────────────

class ClaimAssessment(models.Model):
    """
    Formal damage assessment report produced by an adjuster (or AI).
    One claim can have multiple assessments/revisions.
    """

    class AssessmentType(models.TextChoices):
        AI_INITIAL  = 'AI_INITIAL',  'AI Initial Assessment'
        ADJUSTER    = 'ADJUSTER',    'Adjuster Assessment'
        FINAL       = 'FINAL',       'Final Assessment'

    id              = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    claim           = models.ForeignKey(Claim, on_delete=models.CASCADE, related_name='assessments')
    assessor        = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='assessments')
    assessment_type = models.CharField(max_length=15, choices=AssessmentType.choices)

    # Damage details
    damage_description   = models.TextField()
    damage_severity      = models.CharField(
        max_length=10,
        choices=[('MINOR','Minor'), ('MODERATE','Moderate'), ('SEVERE','Severe'), ('TOTAL_LOSS','Total Loss')],
        default='MINOR'
    )
    damaged_parts        = models.JSONField(default=list, help_text='List of damaged vehicle parts')

    # Cost breakdown
    parts_cost     = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    labour_cost    = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    paint_cost     = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    other_costs    = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_estimate = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # Recommended workshop
    recommended_workshop = models.CharField(max_length=255, blank=True)
    workshop_contact     = models.CharField(max_length=100, blank=True)

    is_final    = models.BooleanField(default=False)
    notes       = models.TextField(blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'claim_assessments'
        ordering = ['-created_at']

    def __str__(self):
        return f'Assessment for {self.claim.claim_number} — {self.get_assessment_type_display()}'

    def calculate_total(self):
        self.total_estimate = self.parts_cost + self.labour_cost + self.paint_cost + self.other_costs
        return self.total_estimate


# ─────────────────────────────────────────────────────────────────────────────
# NOTIFICATION MODEL
# ─────────────────────────────────────────────────────────────────────────────

class Notification(models.Model):
    """
    Email notifications sent to clients/adjusters about claim events.
    Acts as a full audit log of all outbound communications.
    """

    class NotificationType(models.TextChoices):
        CLAIM_RECEIVED    = 'CLAIM_RECEIVED',    'Claim Received'
        CLAIM_UNDER_REVIEW = 'CLAIM_UNDER_REVIEW', 'Claim Under Review'
        CLAIM_APPROVED    = 'CLAIM_APPROVED',    'Claim Approved'
        CLAIM_DECLINED    = 'CLAIM_DECLINED',    'Claim Declined'
        CLAIM_SETTLED     = 'CLAIM_SETTLED',     'Claim Settled'
        DOCUMENT_REQUIRED = 'DOCUMENT_REQUIRED', 'Document Required'
        ADJUSTER_ASSIGNED = 'ADJUSTER_ASSIGNED', 'Adjuster Assigned'
        ACCOUNT_CREATED   = 'ACCOUNT_CREATED',   'Account Created'
        PASSWORD_RESET    = 'PASSWORD_RESET',    'Password Reset'
        GENERAL           = 'GENERAL',           'General Message'

    class Channel(models.TextChoices):
        EMAIL = 'EMAIL', 'Email'
        SMS   = 'SMS',   'SMS'

    class DeliveryStatus(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        SENT    = 'SENT',    'Sent'
        FAILED  = 'FAILED',  'Failed'

    id                = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    recipient         = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    claim             = models.ForeignKey(Claim, on_delete=models.SET_NULL, null=True, blank=True, related_name='notifications')
    notification_type = models.CharField(max_length=25, choices=NotificationType.choices)
    channel           = models.CharField(max_length=10, choices=Channel.choices, default=Channel.EMAIL)
    subject           = models.CharField(max_length=255)
    body              = models.TextField()
    delivery_status   = models.CharField(max_length=10, choices=DeliveryStatus.choices, default=DeliveryStatus.PENDING)
    sent_at           = models.DateTimeField(null=True, blank=True)
    error_message     = models.TextField(blank=True)
    is_read           = models.BooleanField(default=False)
    created_at        = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'notifications'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.get_notification_type_display()} → {self.recipient.email} ({self.delivery_status})'

    def mark_sent(self):
        self.delivery_status = self.DeliveryStatus.SENT
        self.sent_at = timezone.now()
        self.save(update_fields=['delivery_status', 'sent_at'])

    def mark_failed(self, error=''):
        self.delivery_status = self.DeliveryStatus.FAILED
        self.error_message = error
        self.save(update_fields=['delivery_status', 'error_message'])


# ─────────────────────────────────────────────────────────────────────────────
# AUDIT LOG MODEL
# ─────────────────────────────────────────────────────────────────────────────

class AuditLog(models.Model):
    """
    Tracks every important action in the system for accountability and
    anti-fraud purposes (who changed what and when).
    """

    class Action(models.TextChoices):
        CREATE   = 'CREATE',   'Created'
        UPDATE   = 'UPDATE',   'Updated'
        DELETE   = 'DELETE',   'Deleted'
        APPROVE  = 'APPROVE',  'Approved'
        DECLINE  = 'DECLINE',  'Declined'
        SUBMIT   = 'SUBMIT',   'Submitted'
        LOGIN    = 'LOGIN',    'Logged In'
        LOGOUT   = 'LOGOUT',   'Logged Out'
        UPLOAD   = 'UPLOAD',   'Uploaded Document'
        ASSIGN   = 'ASSIGN',   'Assigned Adjuster'
        GENERATE = 'GENERATE', 'Generated Report'

    id           = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor        = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='audit_logs')
    action       = models.CharField(max_length=15, choices=Action.choices)
    resource     = models.CharField(max_length=100, help_text='e.g. Claim, Policy, User')
    resource_id  = models.CharField(max_length=100, blank=True)
    description  = models.TextField(blank=True)
    old_value    = models.JSONField(null=True, blank=True, help_text='Previous state (for updates)')
    new_value    = models.JSONField(null=True, blank=True, help_text='New state (for updates)')
    ip_address   = models.GenericIPAddressField(null=True, blank=True)
    user_agent   = models.TextField(blank=True)
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'audit_logs'
        ordering = ['-created_at']

    def __str__(self):
        actor_name = self.actor.get_full_name() if self.actor else 'System'
        return f'[{self.created_at:%Y-%m-%d %H:%M}] {actor_name} — {self.action} {self.resource}'


# ─────────────────────────────────────────────────────────────────────────────
# REPAIR JOB MODEL
# ─────────────────────────────────────────────────────────────────────────────

class RepairJob(models.Model):
    """
    Repair job assigned to a garage after a claim is approved.
    Tracks the repair lifecycle from assignment to completion.
    """

    class Status(models.TextChoices):
        PENDING   = 'PENDING',   'Pending Acceptance'
        ACCEPTED  = 'ACCEPTED',  'Accepted by Garage'
        DECLINED  = 'DECLINED',  'Declined by Garage'
        IN_PROGRESS = 'IN_PROGRESS', 'Repair In Progress'
        COMPLETED = 'COMPLETED', 'Repair Completed'

    id          = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    claim       = models.OneToOneField(Claim, on_delete=models.CASCADE, related_name='repair_job')
    garage      = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='repair_jobs',
        limit_choices_to={'role': 'GARAGE'}
    )
    assigned_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='assigned_repair_jobs'
    )
    status          = models.CharField(max_length=15, choices=Status.choices, default=Status.PENDING)
    estimated_cost  = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    actual_cost     = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    garage_notes    = models.TextField(blank=True)
    decline_reason  = models.TextField(blank=True)
    assigned_at     = models.DateTimeField(auto_now_add=True)
    accepted_at     = models.DateTimeField(null=True, blank=True)
    completed_at    = models.DateTimeField(null=True, blank=True)
    updated_at      = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'repair_jobs'
        ordering = ['-assigned_at']

    def __str__(self):
        return f'RepairJob for {self.claim.claim_number} — {self.get_status_display()}'
