from rest_framework import generics, status, permissions, filters
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework.permissions import AllowAny

from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth import authenticate
from django.db.models import Count, Sum, Avg, Q
from django.utils import timezone
from django.shortcuts import get_object_or_404
from django.core.mail import send_mail
from django.conf import settings
from rest_framework.throttling import UserRateThrottle
from .ai_service import analyze_images
import logging
import uuid

from .models import User, Vehicle, Policy, Claim, ClaimDocument, ClaimAssessment, Notification, AuditLog
from .serializers import (
    UserRegistrationSerializer, UserLoginSerializer, UserProfileSerializer,
    ChangePasswordSerializer, PasswordResetRequestSerializer, PasswordResetConfirmSerializer,
    VehicleSerializer, PolicySerializer,
    ClaimListSerializer, ClaimDetailSerializer, ClaimSubmitSerializer, ClaimDecisionSerializer,
    ClaimDocumentSerializer, ClaimDocumentUploadSerializer,
    ClaimAssessmentSerializer, NotificationSerializer,
    DashboardStatsSerializer, ClaimReportSerializer,
)
from .permissions import IsClientUser, IsAdjusterOrAdmin, IsOwnerOrAdmin
from .tasks import send_claim_notification_email
from .ai_service import run_ai_assessment
from .email_service import send_email

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def get_tokens_for_user(user):
    refresh = RefreshToken.for_user(user)
    return {
        'refresh': str(refresh),
        'access':  str(refresh.access_token),
    }


def log_action(actor, action, resource, resource_id='', description='', request=None, old_value=None, new_value=None):
    ip = None
    agent = ''
    if request:
        ip    = request.META.get('REMOTE_ADDR')
        agent = request.META.get('HTTP_USER_AGENT', '')
    AuditLog.objects.create(
        actor=actor, action=action, resource=resource,
        resource_id=str(resource_id), description=description,
        old_value=old_value, new_value=new_value,
        ip_address=ip, user_agent=agent,
    )


# ─────────────────────────────────────────────────────────────────────────────
# AUTHENTICATION VIEWS
# ─────────────────────────────────────────────────────────────────────────────

class RegisterView(APIView):
    """POST /api/auth/register/ — Create a new client account."""
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = UserRegistrationSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        user = serializer.save()
        tokens = get_tokens_for_user(user)

        log_action(user, AuditLog.Action.CREATE, 'User', user.id, 'New user registered', request)

        # Send welcome email (async via Celery if available, else inline)
        try:
            send_claim_notification_email.delay(
                recipient_email=user.email,
                subject=f'Welcome to {settings.COMPANY_NAME}!',
                notification_type=Notification.NotificationType.ACCOUNT_CREATED,
                context={'user': user, 'claim': None},
            )
        except Exception:
            pass  # Celery not available in dev — email sent via console backend

        return Response({
            'message': 'Account created successfully. Please verify your email.',
            'user': UserProfileSerializer(user).data,
            'tokens': tokens,
        }, status=status.HTTP_201_CREATED)


class LoginView(APIView):
    """POST /api/auth/login/ — Authenticate and return JWT tokens."""
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = UserLoginSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        email    = serializer.validated_data['email']
        password = serializer.validated_data['password']

        user = authenticate(request, username=email, password=password)
        if not user:
            return Response({'detail': 'Invalid email or password.'}, status=status.HTTP_401_UNAUTHORIZED)

        if not user.is_active:
            return Response({'detail': 'Your account has been deactivated. Contact support.'}, status=status.HTTP_403_FORBIDDEN)

        tokens = get_tokens_for_user(user)
        log_action(user, AuditLog.Action.LOGIN, 'User', user.id, 'User logged in', request)

        redirect_map = {
            User.Role.ADJUSTER: '/adjuster/',
            User.Role.ADMIN:    '/manage-users/',
            User.Role.GARAGE:   '/garage/',
            User.Role.CLIENT:   '/dashboard/',
        }
        redirect_to = redirect_map.get(user.role, '/dashboard/')

        return Response({
            'message':     'Login successful.',
            'user':        UserProfileSerializer(user).data,
            'tokens':      tokens,
            'redirect_to': redirect_to,
        }, status=status.HTTP_200_OK)


class LogoutView(APIView):
    """POST /api/auth/logout/ — Blacklist the refresh token."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        try:
            refresh_token = request.data.get('refresh')
            token = RefreshToken(refresh_token)
            token.blacklist()
            log_action(request.user, AuditLog.Action.LOGOUT, 'User', request.user.id, 'User logged out', request)
            return Response({'message': 'Logged out successfully.'}, status=status.HTTP_200_OK)
        except TokenError:
            return Response({'detail': 'Invalid or expired token.'}, status=status.HTTP_400_BAD_REQUEST)


class ChangePasswordView(APIView):
    """POST /api/auth/change-password/ — Change own password."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        user = request.user
        if not user.check_password(serializer.validated_data['old_password']):
            return Response({'old_password': 'Incorrect current password.'}, status=status.HTTP_400_BAD_REQUEST)

        user.set_password(serializer.validated_data['new_password'])
        user.save()
        log_action(user, AuditLog.Action.UPDATE, 'User', user.id, 'Password changed', request)
        return Response({'message': 'Password changed successfully.'})


class PasswordResetRequestView(APIView):
    """POST /api/auth/password-reset/ — Send reset email."""
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        email = serializer.validated_data['email']
        try:
            user = User.objects.get(email=email)
            user.password_reset_token   = uuid.uuid4()
            user.password_reset_expires = timezone.now() + timezone.timedelta(hours=2)
            user.save(update_fields=['password_reset_token', 'password_reset_expires'])
            # In production: send email with reset link containing token
        except User.DoesNotExist:
            pass  # Don't reveal if email exists (security)

        return Response({'message': 'If that email exists, a password reset link has been sent.'})


class PasswordResetConfirmView(APIView):
    """POST /api/auth/password-reset/confirm/ — Set new password with token."""
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        try:
            user = User.objects.get(
                password_reset_token=serializer.validated_data['token'],
                password_reset_expires__gt=timezone.now()
            )
        except User.DoesNotExist:
            return Response({'token': 'Invalid or expired reset token.'}, status=status.HTTP_400_BAD_REQUEST)

        user.set_password(serializer.validated_data['new_password'])
        user.password_reset_token   = None
        user.password_reset_expires = None
        user.save()
        return Response({'message': 'Password reset successfully. You can now log in.'})


# ─────────────────────────────────────────────────────────────────────────────
# USER / PROFILE VIEWS
# ─────────────────────────────────────────────────────────────────────────────

class MyProfileView(generics.RetrieveUpdateAPIView):
    """GET / PATCH /api/profile/ — View and update own profile."""
    serializer_class   = UserProfileSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes     = [MultiPartParser, FormParser, JSONParser]

    def get_object(self):
        return self.request.user

    def profile_view(request):
         return render(request, 'profile.html')    

    def perform_update(self, serializer):
        serializer.save()
        log_action(self.request.user, AuditLog.Action.UPDATE, 'User', self.request.user.id, 'Profile updated', self.request)


class UserListView(generics.ListAPIView):
    """GET /api/users/ — Admin only: list all users."""
    serializer_class   = UserProfileSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdjusterOrAdmin]
    filter_backends    = [filters.SearchFilter, filters.OrderingFilter]
    search_fields      = ['email', 'first_name', 'last_name', 'phone_number']
    ordering_fields    = ['date_joined', 'last_name']
    queryset           = User.objects.all()


# ─────────────────────────────────────────────────────────────────────────────
# VEHICLE VIEWS
# ─────────────────────────────────────────────────────────────────────────────

class VehicleListCreateView(generics.ListCreateAPIView):
    """GET / POST /api/vehicles/ — Client sees own vehicles; creates new."""
    serializer_class   = VehicleSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.is_adjuster or user.is_admin_user:
            return Vehicle.objects.select_related('owner').all()
        return Vehicle.objects.filter(owner=user)

    def perform_create(self, serializer):
        vehicle = serializer.save(owner=self.request.user)
        log_action(self.request.user, AuditLog.Action.CREATE, 'Vehicle', vehicle.id,
                   f'Vehicle registered: {vehicle.license_plate}', self.request)


class VehicleDetailView(generics.RetrieveUpdateDestroyAPIView):
    """GET / PATCH / DELETE /api/vehicles/<id>/"""
    serializer_class   = VehicleSerializer
    permission_classes = [permissions.IsAuthenticated, IsOwnerOrAdmin]

    def get_queryset(self):
        user = self.request.user
        if user.is_admin_user:
            return Vehicle.objects.all()
        return Vehicle.objects.filter(owner=user)


# ─────────────────────────────────────────────────────────────────────────────
# POLICY VIEWS
# ─────────────────────────────────────────────────────────────────────────────

class PolicyListView(generics.ListCreateAPIView):
    """GET /api/policies/ — Client sees own policies. POST /api/policies/ — Create a new policy."""
    serializer_class   = PolicySerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.is_adjuster or user.is_admin_user:
            return Policy.objects.select_related('client', 'vehicle').all()
        return Policy.objects.filter(client=user).select_related('vehicle')

    def perform_create(self, serializer):
        serializer.save(client=self.request.user)
        log_action(self.request.user, AuditLog.Action.CREATE, 'Policy', '', 'Policy created', self.request)


class PolicyDetailView(generics.RetrieveAPIView):
    """GET /api/policies/<id>/"""
    serializer_class   = PolicySerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.is_adjuster or user.is_admin_user:
            return Policy.objects.all()
        return Policy.objects.filter(client=user)


# ─────────────────────────────────────────────────────────────────────────────
# CLAIM VIEWS
# ─────────────────────────────────────────────────────────────────────────────

class ClaimListCreateView(generics.ListCreateAPIView):
    """
    GET  /api/claims/ — List claims (filtered by user role)
    POST /api/claims/ — Submit a new claim
    """
    permission_classes = [permissions.IsAuthenticated]
    filter_backends    = [filters.SearchFilter, filters.OrderingFilter]
    search_fields      = ['claim_number', 'incident_location', 'vehicle__license_plate']
    ordering_fields    = ['created_at', 'incident_date', 'status']

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return ClaimSubmitSerializer
        return ClaimListSerializer

    def get_queryset(self):
        user = self.request.user
        qs   = Claim.objects.select_related('client', 'vehicle', 'policy', 'assigned_adjuster')

        if user.is_client:
            qs = qs.filter(client=user)
        elif user.is_adjuster:
            qs = qs.filter(assigned_adjuster=user)
       

        
        status_filter = self.request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter.upper())

        return qs.order_by('-created_at')

    def perform_create(self, serializer):
        claim = serializer.save(client=self.request.user, status=Claim.Status.SUBMITTED, submitted_at=timezone.now())
        log_action(self.request.user, AuditLog.Action.SUBMIT, 'Claim', claim.id,
                   f'Claim submitted: {claim.claim_number}', self.request)

       
        _auto_assign_adjuster(claim)

        
        Notification.objects.create(
            recipient=self.request.user,
            claim=claim,
            notification_type=Notification.NotificationType.CLAIM_RECEIVED,
            subject=f'Claim {claim.claim_number} Received',
            body=f'Dear {self.request.user.get_full_name()}, your claim {claim.claim_number} has been received and is being processed.',
        )

        
        try:
            run_ai_assessment(claim)
        except Exception:
            pass  


class ClaimDetailView(generics.RetrieveUpdateAPIView):
    """GET / PATCH /api/claims/<id>/ — View or update a claim."""
    serializer_class   = ClaimDetailSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        qs   = Claim.objects.prefetch_related('documents', 'assessments')
        if user.is_client:
            return qs.filter(client=user)
        return qs.all()

    def get_serializer_context(self):
        # Ensures ClaimDocumentSerializer.get_file_url() receives the request
        # so it can build absolute URLs for document files (photos, police report, etc.)
        context = super().get_serializer_context()
        context['request'] = self.request
        return context


class ClaimDecisionView(APIView):
    """
    POST /api/claims/<id>/decision/
    Adjuster or Admin approves or declines a claim.
 
    Request body:
    {
        "decision":        "APPROVED" | "DECLINED",
        "approved_amount": 5000.00,        // required if APPROVED
        "excess_applied":  500.00,         // optional, defaults to policy excess
        "decline_reason":  "...",          // required if DECLINED
        "adjuster_notes":  "..."           // optional internal notes
    }
 
    On success:
      - Claim status is updated
      - Client's dashboard will reflect the new status immediately
      - Client receives a Gmail email notification
      - A Notification record is created for audit trail
      - AuditLog entry is created
    """
    permission_classes = [permissions.IsAuthenticated, IsAdjusterOrAdmin]
 
    def post(self, request, pk):
        claim = get_object_or_404(Claim, pk=pk)
 
        
        if claim.status not in [Claim.Status.SUBMITTED, Claim.Status.UNDER_REVIEW]:
            return Response(
                {'detail': f'Cannot decide on a claim with status: {claim.get_status_display()}'},
                status=status.HTTP_400_BAD_REQUEST
            )
 
        serializer = ClaimDecisionSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
 
        data       = serializer.validated_data
        decision   = data['decision']
        old_status = claim.status

        # If no adjuster assigned yet, assign the one making the decision
        if not claim.assigned_adjuster:
            claim.assigned_adjuster = request.user
            if not claim.reviewed_at:
                claim.reviewed_at = timezone.now()
 
        if decision == 'APPROVED':
            approved_amount         = data.get('approved_amount')
            if not approved_amount:
                return Response(
                    {'approved_amount': 'This field is required when approving a claim.'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            claim.status            = Claim.Status.APPROVED
            claim.approved_amount   = approved_amount
            claim.excess_applied    = data.get('excess_applied', claim.policy.excess_amount)
            claim.settlement_amount = claim.approved_amount - (claim.excess_applied or 0)
 
            notification_type = Notification.NotificationType.CLAIM_APPROVED
            subject = f'✅ Claim {claim.claim_number} Approved — {settings.COMPANY_NAME}'
            body    = (
                f'Dear {claim.client.get_full_name()},\n\n'
                f'We are pleased to inform you that your claim has been APPROVED.\n\n'
                f'Claim Number  : {claim.claim_number}\n'
                f'Approved Amount : USD {claim.approved_amount:,.2f}\n'
                f'Excess Applied  : USD {claim.excess_applied:,.2f}\n'
                f'Settlement Amount: USD {claim.settlement_amount:,.2f}\n\n'
                f'Our team will contact you shortly to finalise the settlement.\n\n'
                f'You can also log in to your dashboard to track the status of your claim.\n\n'
                f'Regards,\n'
                f'{settings.COMPANY_NAME} Claims Team'
            )
 
        else:  # DECLINED
            decline_reason = data.get('decline_reason', '').strip()
            if not decline_reason:
                return Response(
                    {'decline_reason': 'A reason must be provided when declining a claim.'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            claim.status         = Claim.Status.DECLINED
            claim.decline_reason = decline_reason
 
            notification_type = Notification.NotificationType.CLAIM_DECLINED
            subject = f'❌ Claim {claim.claim_number} Declined — {settings.COMPANY_NAME}'
            body    = (
                f'Dear {claim.client.get_full_name()},\n\n'
                f'We regret to inform you that your claim has been DECLINED.\n\n'
                f'Claim Number : {claim.claim_number}\n'
                f'Reason       : {claim.decline_reason}\n\n'
                f'If you disagree with this decision, you may appeal within 30 days by '
                f'contacting our support team or replying to this email.\n\n'
                f'You can also log in to your dashboard to view the full details of your claim.\n\n'
                f'Regards,\n'
                f'{settings.COMPANY_NAME} Claims Team'
            )
 
        claim.adjuster_notes = data.get('adjuster_notes', '')
        claim.decided_at     = timezone.now()
        claim.save()
 
        # Audit log
        log_action(
            request.user,
            AuditLog.Action.APPROVE if decision == 'APPROVED' else AuditLog.Action.DECLINE,
            'Claim', claim.id,
            f'Claim {decision} by {request.user.get_full_name()}: {claim.claim_number}',
            request,
            old_value={'status': old_status},
            new_value={'status': claim.status},
        )
 
        # Send email notification to client (Gmail SMTP) + create Notification record
        _send_email_to_user(
            recipient=claim.client,
            subject=subject,
            body=body,
            notification_type=notification_type,
            claim=claim,
        )
 
        return Response(
            ClaimDetailSerializer(claim, context={'request': request}).data,
            status=status.HTTP_200_OK
        )
 
 
# ── 6. UPDATED ClaimAssignAdjusterView (replace your existing one) ────────────
#
# Key change: emails the adjuster when a claim is assigned to them.
#
 
class ClaimAssignAdjusterView(APIView):
    """
    POST /api/claims/<id>/assign/
    Admin assigns (or reassigns) a claim to an adjuster.
 
    Request body:
    {
        "adjuster_id": "<uuid>"
    }
 
    On success:
      - Claim status moves from SUBMITTED → UNDER_REVIEW
      - Adjuster receives a Gmail email notification
      - Client is notified that an adjuster has been assigned
    """
    permission_classes = [permissions.IsAuthenticated, IsAdjusterOrAdmin]
 
    def post(self, request, pk):
        claim       = get_object_or_404(Claim, pk=pk)
        adjuster_id = request.data.get('adjuster_id')
 
        if not adjuster_id:
            return Response(
                {'detail': 'adjuster_id is required.'},
                status=status.HTTP_400_BAD_REQUEST
            )
 
        adjuster = get_object_or_404(User, pk=adjuster_id, role=User.Role.ADJUSTER)
 
        if not adjuster.is_active:
            return Response(
                {'detail': 'This adjuster account is inactive.'},
                status=status.HTTP_400_BAD_REQUEST
            )
 
        old_adjuster = claim.assigned_adjuster
        claim.assigned_adjuster = adjuster
 
        if claim.status == Claim.Status.SUBMITTED:
            claim.status      = Claim.Status.UNDER_REVIEW
            claim.reviewed_at = timezone.now()
 
        claim.save()
 
        log_action(
            request.user, AuditLog.Action.ASSIGN, 'Claim', claim.id,
            f'Assigned to {adjuster.get_full_name()}', request
        )
 
        # Notify the adjuster by email
        _send_email_to_user(
            recipient=adjuster,
            subject=f'📋 New Claim Assigned — {claim.claim_number}',
            body=(
                f'Dear {adjuster.get_full_name()},\n\n'
                f'A new claim has been assigned to you for review.\n\n'
                f'Claim Number  : {claim.claim_number}\n'
                f'Client        : {claim.client.get_full_name()}\n'
                f'Incident Type : {claim.get_incident_type_display()}\n'
                f'Incident Date : {claim.incident_date}\n'
                f'Location      : {claim.incident_location}\n'
                f'Claimed Amount: USD {claim.claimed_amount or "Not specified"}\n\n'
                f'Please log in to your dashboard to review the claim and supporting documents.\n\n'
                f'Regards,\n'
                f'{settings.COMPANY_NAME} Admin Team'
            ),
            notification_type=Notification.NotificationType.ADJUSTER_ASSIGNED,
            claim=claim,
        )
 
        # Notify the client
        _send_email_to_user(
            recipient=claim.client,
            subject=f'Claim {claim.claim_number} — Adjuster Assigned',
            body=(
                f'Dear {claim.client.get_full_name()},\n\n'
                f'Your claim {claim.claim_number} is now being reviewed by '
                f'{adjuster.get_full_name()}.\n\n'
                f'You will receive further updates by email once a decision has been made.\n\n'
                f'Regards,\n'
                f'{settings.COMPANY_NAME} Claims Team'
            ),
            notification_type=Notification.NotificationType.ADJUSTER_ASSIGNED,
            claim=claim,
        )
 
        return Response({
            'message':  f'Claim assigned to {adjuster.get_full_name()}.',
            'claim_id': str(claim.id),
            'status':   claim.status,
            'adjuster': {
                'id':        str(adjuster.id),
                'full_name': adjuster.get_full_name(),
                'email':     adjuster.email,
            }
        })

# ─────────────────────────────────────────────────────────────────────────────
# DOCUMENT VIEWS
# ─────────────────────────────────────────────────────────────────────────────

class ClaimDocumentUploadView(generics.CreateAPIView):
    """POST /api/claims/<claim_id>/documents/upload/ — Upload a document."""
    serializer_class   = ClaimDocumentUploadSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes     = [MultiPartParser, FormParser]

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['request'] = self.request
        return context

    def perform_create(self, serializer):
        claim = get_object_or_404(Claim, pk=self.kwargs['claim_id'])

        # Clients can only upload to their own claims
        if self.request.user.is_client and claim.client != self.request.user:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied('You do not have access to this claim.')

        file = serializer.validated_data['file']
        doc  = serializer.save(
            claim=claim,
            uploaded_by=self.request.user,
            file_name=file.name,
            file_size=file.size,
            mime_type=getattr(file, 'content_type', ''),
        )
        log_action(self.request.user, AuditLog.Action.UPLOAD, 'ClaimDocument', doc.id,
                   f'Document uploaded for {claim.claim_number}', self.request)

        # Re-run AI assessment when a damage photo is added
        if doc.document_type in ('ACCIDENT_PHOTO', 'VEHICLE_PHOTO'):
            try:
                run_ai_assessment(claim)
            except Exception:
                pass


class ClaimDocumentListView(generics.ListAPIView):
    """GET /api/claims/<claim_id>/documents/ — List claim documents."""
    serializer_class   = ClaimDocumentSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        claim_id = self.kwargs['claim_id']
        claim    = get_object_or_404(Claim, pk=claim_id)
        user     = self.request.user
        if user.is_client and claim.client != user:
            return ClaimDocument.objects.none()
        return ClaimDocument.objects.filter(claim=claim)

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context['request'] = self.request
        return context


# ─────────────────────────────────────────────────────────────────────────────
# ASSESSMENT VIEWS
# ─────────────────────────────────────────────────────────────────────────────

class ClaimAssessmentCreateView(generics.ListCreateAPIView):
    """
    GET  /api/claims/<claim_id>/assessments/ — List assessments (all authenticated users)
    POST /api/claims/<claim_id>/assessments/ — Create assessment (adjuster/admin only)
    """
    serializer_class   = ClaimAssessmentSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        claim = get_object_or_404(Claim, pk=self.kwargs['claim_id'])
        user  = self.request.user
        # Clients can only see their own claim assessments
        if user.is_client and claim.client != user:
            return ClaimAssessment.objects.none()
        return ClaimAssessment.objects.filter(claim=claim).order_by('-created_at')

    def perform_create(self, serializer):
        # Only adjusters/admins can create assessments
        if not (self.request.user.is_adjuster or self.request.user.is_admin_user):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied('Only adjusters or admins can create assessments.')
        claim      = get_object_or_404(Claim, pk=self.kwargs['claim_id'])
        assessment = serializer.save(claim=claim, assessor=self.request.user)
        assessment.calculate_total()
        assessment.save(update_fields=['total_estimate'])
        log_action(self.request.user, AuditLog.Action.CREATE, 'ClaimAssessment', assessment.id,
                   f'Assessment created for {claim.claim_number}', self.request)


# ─────────────────────────────────────────────────────────────────────────────
# NOTIFICATION VIEWS
# ─────────────────────────────────────────────────────────────────────────────

class NotificationListView(generics.ListAPIView):
    """GET /api/notifications/ — Own notifications."""
    serializer_class   = NotificationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Notification.objects.filter(recipient=self.request.user).order_by('-created_at')


class NotificationMarkReadView(APIView):
    """POST /api/notifications/<id>/read/ — Mark notification as read."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        notification = get_object_or_404(Notification, pk=pk, recipient=request.user)
        notification.is_read = True
        notification.save(update_fields=['is_read'])
        return Response({'message': 'Marked as read.'})


# ─────────────────────────────────────────────────────────────────────────────
# DASHBOARD VIEWS
# ─────────────────────────────────────────────────────────────────────────────

class DashboardView(APIView):
    """GET /api/dashboard/ — Statistics for the home dashboard."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user

        if user.is_client:
            qs = Claim.objects.filter(client=user)
        elif user.is_adjuster:
            qs = Claim.objects.filter(assigned_adjuster=user)
        else:
            qs = Claim.objects.all()

        now         = timezone.now()
        month_start = now.replace(day=1, hour=0, minute=0, second=0)

        stats = {
            'total_claims':           qs.count(),
            'pending_claims':         qs.filter(status__in=[Claim.Status.SUBMITTED, Claim.Status.UNDER_REVIEW]).count(),
            'approved_claims':        qs.filter(status=Claim.Status.APPROVED).count(),
            'declined_claims':        qs.filter(status=Claim.Status.DECLINED).count(),
            'settled_claims':         qs.filter(status=Claim.Status.SETTLED).count(),
            'total_claimed_amount':   qs.aggregate(t=Sum('claimed_amount'))['t'] or 0,
            'total_approved_amount':  qs.filter(status__in=[Claim.Status.APPROVED, Claim.Status.SETTLED])
                                        .aggregate(t=Sum('approved_amount'))['t'] or 0,
            'avg_processing_days':    0,
            'claims_this_month':      qs.filter(submitted_at__gte=month_start).count(),
        }

        # Recent claims
        recent_claims = ClaimListSerializer(
            qs.order_by('-created_at')[:5],
            many=True, context={'request': request}
        ).data

        # Unread notifications
        unread_count = Notification.objects.filter(recipient=user, is_read=False).count()

        return Response({
            'stats': stats,
            'recent_claims': recent_claims,
            'unread_notifications': unread_count,
        })


# ─────────────────────────────────────────────────────────────────────────────
# REPORT VIEW
# ─────────────────────────────────────────────────────────────────────────────

class ClaimReportView(APIView):
    """
    GET /api/claims/<id>/report/
    Generates a JSON summary report for a claim (used to render PDF).
    """
    permission_classes = [permissions.IsAuthenticated, IsAdjusterOrAdmin]

    def get(self, request, pk):
        claim = get_object_or_404(
            Claim.objects.prefetch_related('assessments', 'documents'),
            pk=pk
        )

        final_assessment = claim.assessments.filter(is_final=True).first()
        damage_desc      = final_assessment.damage_description if final_assessment else claim.ai_damage_summary

        report = {
            'claim_number':      claim.claim_number,
            'client_name':       claim.client.get_full_name(),
            'policy_number':     claim.policy.policy_number,
            'vehicle':           str(claim.vehicle),
            'incident_date':     claim.incident_date,
            'incident_type':     claim.get_incident_type_display(),
            'incident_location': claim.incident_location,
            'status':            claim.get_status_display(),
            'claimed_amount':    claim.claimed_amount,
            'approved_amount':   claim.approved_amount,
            'settlement_amount': claim.settlement_amount,
            'damage_description': damage_desc or '',
            'estimated_repair':  final_assessment.total_estimate if final_assessment else claim.ai_estimated_repair,
            'adjuster_notes':    claim.adjuster_notes,
            'submitted_at':      claim.submitted_at,
            'decided_at':        claim.decided_at,
        }

        log_action(request.user, AuditLog.Action.GENERATE, 'ClaimReport', claim.id,
                   f'Report generated for {claim.claim_number}', request)

        serializer = ClaimReportSerializer(data=report)
        serializer.is_valid()
        return Response(serializer.data)

class AdjusterRegisterView(APIView):
    """
    POST /api/auth/register-adjuster/
    Admin-only endpoint to create a new adjuster account.
 
    Request body:
    {
        "email":        "adjuster@example.com",
        "password":     "SecurePass123!",
        "first_name":   "John",
        "last_name":    "Doe",
        "phone_number": "+263771234567",   // optional
        "national_id":  "63-123456-A-00"   // optional
    }
    """
    permission_classes = [permissions.IsAuthenticated]
 
    def post(self, request):
        # Only admins can create adjuster accounts
        if not request.user.is_admin_user:
            return Response(
                {'detail': 'Only administrators can register adjusters.'},
                status=status.HTTP_403_FORBIDDEN
            )
 
        # Reuse your existing registration serializer but override the role
        serializer = UserRegistrationSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
 
        # Force role to ADJUSTER regardless of what was sent
        adjuster = serializer.save(role=User.Role.ADJUSTER, is_email_verified=True)
 
        log_action(
            request.user, AuditLog.Action.CREATE, 'User', adjuster.id,
            f'Adjuster account created for {adjuster.email}', request
        )
 
        # Send welcome email to the new adjuster
        _send_email_to_user(
            recipient=adjuster,
            subject=f'Your Adjuster Account — {settings.COMPANY_NAME}',
            body=(
                f'Dear {adjuster.get_full_name()},\n\n'
                f'An adjuster account has been created for you on {settings.COMPANY_NAME}.\n\n'
                f'Login Email: {adjuster.email}\n'
                f'Please use the password set by your administrator to log in.\n\n'
                f'You will be notified by email each time a claim is assigned to you.\n\n'
                f'Regards,\n{settings.COMPANY_NAME} Team'
            ),
            notification_type=Notification.NotificationType.ACCOUNT_CREATED,
            claim=None,
        )
 
        return Response({
            'message': f'Adjuster account created for {adjuster.get_full_name()}.',
            'adjuster': {
                'id':         str(adjuster.id),
                'email':      adjuster.email,
                'full_name':  adjuster.get_full_name(),
                'role':       adjuster.role,
                'is_active':  adjuster.is_active,
                'date_joined': adjuster.date_joined,
            }
        }, status=status.HTTP_201_CREATED)
 
 
# ── 2. ADJUSTER DASHBOARD ─────────────────────────────────────────────────────
 
class AdjusterDashboardView(APIView):
    """
    GET /api/adjuster/dashboard/
    Returns statistics and recent claims for the logged-in adjuster.
 
    Response:
    {
        "adjuster": { ... profile ... },
        "stats": {
            "total_assigned":    12,
            "pending_review":     4,
            "approved":           5,
            "declined":           3,
            "settled":            0,
            "this_month":         2,
            "total_approved_usd": 45000.00
        },
        "recent_claims":     [ ... last 10 assigned claims ... ],
        "unread_notifications": 3
    }
    """
    permission_classes = [permissions.IsAuthenticated]
 
    def get(self, request):
        user = request.user
 
        # Adjusters see their own dashboard; admins can pass ?adjuster_id= to inspect any
        if user.is_admin_user:
            adjuster_id = request.query_params.get('adjuster_id')
            if adjuster_id:
                adjuster = get_object_or_404(User, pk=adjuster_id, role=User.Role.ADJUSTER)
            else:
                # Admin with no filter gets system-wide stats via the existing DashboardView
                return Response(
                    {'detail': 'Admins: use /api/dashboard/ for system-wide stats, '
                               'or pass ?adjuster_id=<uuid> to view a specific adjuster.'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        elif user.is_adjuster:
            adjuster = user
        else:
            return Response(
                {'detail': 'Only adjusters or admins can access this endpoint.'},
                status=status.HTTP_403_FORBIDDEN
            )
 
        qs          = Claim.objects.filter(assigned_adjuster=adjuster)
        now         = timezone.now()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
 
        stats = {
            'total_assigned':    qs.count(),
            'pending_review':    qs.filter(
                                    status__in=[Claim.Status.SUBMITTED, Claim.Status.UNDER_REVIEW]
                                 ).count(),
            'approved':          qs.filter(status=Claim.Status.APPROVED).count(),
            'declined':          qs.filter(status=Claim.Status.DECLINED).count(),
            'settled':           qs.filter(status=Claim.Status.SETTLED).count(),
            'this_month':        qs.filter(submitted_at__gte=month_start).count(),
            'total_approved_usd': float(
                                    qs.filter(
                                        status__in=[Claim.Status.APPROVED, Claim.Status.SETTLED]
                                    ).aggregate(t=Sum('approved_amount'))['t'] or 0
                                 ),
        }
 
        recent_claims = ClaimListSerializer(
            qs.select_related('client', 'vehicle', 'policy')
              .order_by('-created_at')[:10],
            many=True, context={'request': request}
        ).data
 
        unread_count = Notification.objects.filter(
            recipient=adjuster, is_read=False
        ).count()
 
        return Response({
            'adjuster': {
                'id':          str(adjuster.id),
                'full_name':   adjuster.get_full_name(),
                'email':       adjuster.email,
                'phone':       adjuster.phone_number,
            },
            'stats':               stats,
            'recent_claims':       recent_claims,
            'unread_notifications': unread_count,
        })
 
 
# ── 3. ADJUSTER CLAIM LIST (filtered + paginated) ─────────────────────────────
 
class AdjusterClaimListView(generics.ListAPIView):
    """
    GET /api/adjuster/claims/
    Returns all claims assigned to the logged-in adjuster.
 
    Query params:
        ?status=SUBMITTED|UNDER_REVIEW|APPROVED|DECLINED|SETTLED
        ?search=<claim_number or plate>
        ?ordering=created_at|-created_at|incident_date|status
 
    Only adjusters (and admins) can call this.
    """
    serializer_class   = ClaimListSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends    = [filters.SearchFilter, filters.OrderingFilter]
    search_fields      = ['claim_number', 'incident_location', 'vehicle__license_plate',
                          'client__first_name', 'client__last_name', 'client__email']
    ordering_fields    = ['created_at', 'incident_date', 'status', 'submitted_at']
 
    def get_queryset(self):
        user = self.request.user
 
        if not (user.is_adjuster or user.is_admin_user):
            return Claim.objects.none()
 
        # Adjusters only see their own; admins can add ?adjuster_id=
        if user.is_adjuster:
            qs = Claim.objects.filter(
                Q(assigned_adjuster=user) |
                Q(status=Claim.Status.SUBMITTED, assigned_adjuster=None)
            )
        else:
            adjuster_id = self.request.query_params.get('adjuster_id')
            if adjuster_id:
                qs = Claim.objects.filter(assigned_adjuster__id=adjuster_id)
            else:
                qs = Claim.objects.all()
 
        # Optional status filter
        status_filter = self.request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter.upper())
 
        return qs.select_related('client', 'vehicle', 'policy', 'assigned_adjuster').order_by('-created_at')
 
 
# ── AUTO-ASSIGN HELPER ────────────────────────────────────────────────────────

def _auto_assign_adjuster(claim):
    """
    Picks the active adjuster with the fewest open (SUBMITTED + UNDER_REVIEW) claims
    and assigns the claim to them.  If no adjusters exist, the claim stays SUBMITTED.
    """
    adjuster = (
        User.objects
        .filter(role=User.Role.ADJUSTER, is_active=True)
        .annotate(
            open_claims=Count(
                'assigned_claims',
                filter=Q(assigned_claims__status__in=[
                    Claim.Status.SUBMITTED, Claim.Status.UNDER_REVIEW
                ])
            )
        )
        .order_by('open_claims')
        .first()
    )

    if not adjuster:
        return  # no adjusters registered yet — admin can assign manually

    claim.assigned_adjuster = adjuster
    claim.status            = Claim.Status.UNDER_REVIEW
    claim.reviewed_at       = timezone.now()
    claim.save(update_fields=['assigned_adjuster', 'status', 'reviewed_at'])

    _send_email_to_user(
        recipient=adjuster,
        subject=f'📋 New Claim Assigned — {claim.claim_number}',
        body=(
            f'Dear {adjuster.get_full_name()},\n\n'
            f'A new claim has been automatically assigned to you.\n\n'
            f'Claim Number  : {claim.claim_number}\n'
            f'Client        : {claim.client.get_full_name()}\n'
            f'Incident Type : {claim.get_incident_type_display()}\n'
            f'Incident Date : {claim.incident_date}\n'
            f'Location      : {claim.incident_location}\n'
            f'Claimed Amount: USD {claim.claimed_amount or "Not specified"}\n\n'
            f'Please log in to your dashboard to review the claim.\n\n'
            f'Regards,\n'
            f'{settings.COMPANY_NAME} Claims System'
        ),
        notification_type=Notification.NotificationType.ADJUSTER_ASSIGNED,
        claim=claim,
    )


# ── MONTHLY REPORT VIEW ───────────────────────────────────────────────────────

class MonthlyReportView(APIView):
    """
    GET /api/reports/monthly/?year=2025&month=4
    Returns an aggregate monthly report for admins/adjusters.
    """
    permission_classes = [permissions.IsAuthenticated, IsAdjusterOrAdmin]

    def get(self, request):
        now   = timezone.now()
        year  = int(request.query_params.get('year',  now.year))
        month = int(request.query_params.get('month', now.month))

        import calendar
        _, last_day = calendar.monthrange(year, month)
        from django.utils.timezone import make_aware
        from datetime import datetime
        period_start = make_aware(datetime(year, month, 1))
        period_end   = make_aware(datetime(year, month, last_day, 23, 59, 59))

        qs = Claim.objects.filter(submitted_at__range=(period_start, period_end))

        total        = qs.count()
        approved     = qs.filter(status=Claim.Status.APPROVED).count()
        declined     = qs.filter(status=Claim.Status.DECLINED).count()
        under_review = qs.filter(status=Claim.Status.UNDER_REVIEW).count()
        settled      = qs.filter(status=Claim.Status.SETTLED).count()

        total_claimed  = float(qs.aggregate(t=Sum('claimed_amount'))['t']  or 0)
        total_approved = float(qs.filter(status__in=[Claim.Status.APPROVED, Claim.Status.SETTLED])
                                 .aggregate(t=Sum('approved_amount'))['t'] or 0)
        total_settled  = float(qs.filter(status=Claim.Status.SETTLED)
                                 .aggregate(t=Sum('settlement_amount'))['t'] or 0)

        # Incident type breakdown
        type_breakdown = list(
            qs.values('incident_type')
              .annotate(count=Count('id'))
              .order_by('-count')
        )

        # Adjuster performance
        adjuster_stats = []
        for adj in User.objects.filter(role=User.Role.ADJUSTER, is_active=True):
            adj_qs = qs.filter(assigned_adjuster=adj)
            adjuster_stats.append({
                'adjuster':  adj.get_full_name(),
                'email':     adj.email,
                'assigned':  adj_qs.count(),
                'approved':  adj_qs.filter(status=Claim.Status.APPROVED).count(),
                'declined':  adj_qs.filter(status=Claim.Status.DECLINED).count(),
                'pending':   adj_qs.filter(status__in=[Claim.Status.SUBMITTED, Claim.Status.UNDER_REVIEW]).count(),
                'total_approved_usd': float(
                    adj_qs.filter(status__in=[Claim.Status.APPROVED, Claim.Status.SETTLED])
                          .aggregate(t=Sum('approved_amount'))['t'] or 0
                ),
            })

        # Avg processing days (submitted → decided)
        decided = qs.exclude(decided_at=None).exclude(submitted_at=None)
        avg_days = 0
        if decided.exists():
            from django.db.models import F, ExpressionWrapper, DurationField
            deltas = decided.annotate(
                delta=ExpressionWrapper(F('decided_at') - F('submitted_at'), output_field=DurationField())
            ).aggregate(avg=Avg('delta'))['avg']
            if deltas:
                avg_days = round(deltas.total_seconds() / 86400, 1)

        log_action(request.user, AuditLog.Action.GENERATE, 'MonthlyReport', '',
                   f'Monthly report generated for {year}-{month:02d}', request)

        return Response({
            'period':          f'{year}-{month:02d}',
            'period_label':    f'{calendar.month_name[month]} {year}',
            'volume': {
                'total':        total,
                'approved':     approved,
                'declined':     declined,
                'under_review': under_review,
                'settled':      settled,
            },
            'financials': {
                'total_claimed_usd':  total_claimed,
                'total_approved_usd': total_approved,
                'total_settled_usd':  total_settled,
                'approval_rate_pct':  round((approved / total * 100) if total else 0, 1),
                'decline_rate_pct':   round((declined / total * 100) if total else 0, 1),
            },
            'avg_processing_days': avg_days,
            'incident_type_breakdown': type_breakdown,
            'adjuster_performance':    adjuster_stats,
        })


# ── 4. EMAIL HELPER (used by decision view + registration) ────────────────────
def _send_email_to_user(recipient, subject, body, **kwargs):
    send_email(
        recipient_email=recipient.email,
        subject=subject,
        body=body
    )


# ─────────────────────────────────────────────────────────────────────────────
# AI ASSESSMENT PROXY VIEW
# POST /api/claims/ai-assess/
# Called by the frontend on Step 4 to analyse damage photos BEFORE submission.
# Receives base64 images, calls Anthropic, returns structured JSON.
# ─────────────────────────────────────────────────────────────────────────────

logger = logging.getLogger(__name__)


class AIAssessmentThrottle(UserRateThrottle):
    rate = "5/min"

@method_decorator(csrf_exempt, name='dispatch') 
class AIAssessmentProxyView(APIView):
    """
    POST /api/claims/ai-assess/

    Runs AI damage analysis BEFORE claim submission.
    Uses centralized AI service (rate-safe + cached).
    """
    permission_classes = [AllowAny]
    authentication_classes = []
    def post(self, request):
        images = request.data.get("images", [])

        if not images:
            return Response(
                {"detail": "No images provided."},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            result = analyze_images(images)

            return Response({
            "damages": result.get("damaged_parts", []),
            "total_cost_usd": result.get("total_estimate_usd"),
            "summary": result.get("damage_description")
    }, status=status.HTTP_200_OK)

        except ValueError as e:
            return Response(
                {"detail": str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )

        except Exception as e:
            logger.error(f"AIAssessmentProxyView error: {e}")

            
            return Response({
                "damages": [],
                "total_cost_usd": None,
                "summary": "AI service temporarily busy. Please retry shortly."
            }, status=status.HTTP_200_OK)

# ─────────────────────────────────────────────────────────────────────────────
# USER MANAGEMENT VIEWS (Admin only)
# ─────────────────────────────────────────────────────────────────────────────

class UserManagementListView(APIView):
    """
    GET  /api/admin/users/         — List all users with filters
    POST /api/admin/users/         — Create adjuster or garage user
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if not request.user.is_admin_user:
            return Response({'detail': 'Admin only.'}, status=status.HTTP_403_FORBIDDEN)

        role_filter = request.query_params.get('role')
        qs = User.objects.all().order_by('-date_joined')
        if role_filter:
            qs = qs.filter(role=role_filter.upper())

        search = request.query_params.get('search', '').strip()
        if search:
            qs = qs.filter(
                Q(first_name__icontains=search) |
                Q(last_name__icontains=search) |
                Q(email__icontains=search)
            )

        data = []
        for u in qs:
            data.append({
                'id':          str(u.id),
                'full_name':   u.get_full_name(),
                'email':       u.email,
                'role':        u.role,
                'phone':       u.phone_number,
                'is_active':   u.is_active,
                'date_joined': u.date_joined,
                'national_id': u.national_id,
            })
        return Response(data)

    def post(self, request):
        if not request.user.is_admin_user:
            return Response({'detail': 'Admin only.'}, status=status.HTTP_403_FORBIDDEN)

        role = request.data.get('role', '').upper()
        if role not in ['ADJUSTER', 'GARAGE']:
            return Response({'detail': 'Role must be ADJUSTER or GARAGE.'}, status=status.HTTP_400_BAD_REQUEST)

        serializer = UserRegistrationSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        user = serializer.save(role=role, is_email_verified=True)
        log_action(request.user, AuditLog.Action.CREATE, 'User', user.id,
                   f'{role} account created for {user.email}', request)

        _send_email_to_user(
            recipient=user,
            subject=f'Your {role.title()} Account — {settings.COMPANY_NAME}',
            body=(
                f'Dear {user.get_full_name()},\n\n'
                f'An account has been created for you on {settings.COMPANY_NAME}.\n'
                f'Role: {role.title()}\n'
                f'Email: {user.email}\n\n'
                f'Please log in with the password provided by your administrator.\n\n'
                f'Regards,\n{settings.COMPANY_NAME} Team'
            ),
            notification_type=Notification.NotificationType.ACCOUNT_CREATED,
        )

        return Response({
            'id':        str(user.id),
            'full_name': user.get_full_name(),
            'email':     user.email,
            'role':      user.role,
            'is_active': user.is_active,
        }, status=status.HTTP_201_CREATED)


class UserManagementDetailView(APIView):
    """
    PATCH /api/admin/users/<id>/  — Activate / deactivate user
    DELETE /api/admin/users/<id>/ — Delete user
    """
    permission_classes = [permissions.IsAuthenticated]

    def patch(self, request, pk):
        if not request.user.is_admin_user:
            return Response({'detail': 'Admin only.'}, status=status.HTTP_403_FORBIDDEN)
        user = get_object_or_404(User, pk=pk)
        is_active = request.data.get('is_active')
        if is_active is not None:
            user.is_active = bool(is_active)
            user.save(update_fields=['is_active'])
        return Response({'id': str(user.id), 'is_active': user.is_active})

    def delete(self, request, pk):
        if not request.user.is_admin_user:
            return Response({'detail': 'Admin only.'}, status=status.HTTP_403_FORBIDDEN)
        user = get_object_or_404(User, pk=pk)
        if user == request.user:
            return Response({'detail': 'You cannot delete your own account.'}, status=status.HTTP_400_BAD_REQUEST)
        user.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ─────────────────────────────────────────────────────────────────────────────
# GARAGE VIEWS
# ─────────────────────────────────────────────────────────────────────────────

class GarageJobListView(APIView):
    """
    GET /api/garage/jobs/
    Returns all repair jobs assigned to the logged-in garage user.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from .models import RepairJob
        user = request.user
        if not (user.is_garage or user.is_admin_user):
            return Response({'detail': 'Garage access only.'}, status=status.HTTP_403_FORBIDDEN)

        qs = RepairJob.objects.filter(garage=user).select_related(
            'claim', 'claim__client', 'claim__vehicle', 'claim__policy'
        ).order_by('-assigned_at')

        status_filter = request.query_params.get('status')
        if status_filter:
            qs = qs.filter(status=status_filter.upper())

        jobs = []
        for job in qs:
            c = job.claim
            jobs.append({
                'id':              str(job.id),
                'status':          job.status,
                'assigned_at':     job.assigned_at,
                'accepted_at':     job.accepted_at,
                'completed_at':    job.completed_at,
                'estimated_cost':  float(job.estimated_cost or 0),
                'actual_cost':     float(job.actual_cost or 0),
                'garage_notes':    job.garage_notes,
                'decline_reason':  job.decline_reason,
                'claim': {
                    'id':                  str(c.id),
                    'claim_number':        c.claim_number,
                    'incident_type':       c.incident_type,
                    'incident_date':       c.incident_date,
                    'incident_location':   c.incident_location,
                    'incident_description': c.incident_description,
                    'approved_amount':     float(c.approved_amount or 0),
                    'ai_damage_summary':   c.ai_damage_summary,
                    'ai_estimated_repair': float(c.ai_estimated_repair or 0),
                    'client_name':         c.client.get_full_name(),
                    'client_phone':        c.client.phone_number,
                    'vehicle': {
                        'year':          c.vehicle.year,
                        'make':          c.vehicle.make,
                        'model':         c.vehicle.model,
                        'color':         c.vehicle.color,
                        'license_plate': c.vehicle.license_plate,
                        'body_type':     c.vehicle.body_type,
                    },
                },
            })

        now         = timezone.now()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        all_jobs    = RepairJob.objects.filter(garage=user)

        stats = {
            'total':       all_jobs.count(),
            'pending':     all_jobs.filter(status='PENDING').count(),
            'accepted':    all_jobs.filter(status__in=['ACCEPTED','IN_PROGRESS']).count(),
            'completed':   all_jobs.filter(status='COMPLETED').count(),
            'declined':    all_jobs.filter(status='DECLINED').count(),
            'this_month':  all_jobs.filter(assigned_at__gte=month_start).count(),
        }

        return Response({'jobs': jobs, 'stats': stats})


class GarageJobActionView(APIView):
    """
    POST /api/garage/jobs/<id>/action/
    Garage accepts, declines, starts, or completes a repair job.

    Body: { "action": "ACCEPT" | "DECLINE" | "START" | "COMPLETE",
            "garage_notes": "...",
            "actual_cost": 1200.00,
            "decline_reason": "..." }
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        from .models import RepairJob
        user = request.user
        if not (user.is_garage or user.is_admin_user):
            return Response({'detail': 'Garage access only.'}, status=status.HTTP_403_FORBIDDEN)

        job    = get_object_or_404(RepairJob, pk=pk, garage=user)
        action = request.data.get('action', '').upper()

        if action == 'ACCEPT':
            if job.status != 'PENDING':
                return Response({'detail': 'Job is not pending.'}, status=status.HTTP_400_BAD_REQUEST)
            job.status      = 'ACCEPTED'
            job.accepted_at = timezone.now()
            job.garage_notes = request.data.get('garage_notes', '')
            msg = 'Job accepted.'

        elif action == 'DECLINE':
            if job.status not in ['PENDING', 'ACCEPTED']:
                return Response({'detail': 'Cannot decline at this stage.'}, status=status.HTTP_400_BAD_REQUEST)
            reason = request.data.get('decline_reason', '').strip()
            if not reason:
                return Response({'detail': 'A reason is required when declining.'}, status=status.HTTP_400_BAD_REQUEST)
            job.status         = 'DECLINED'
            job.decline_reason = reason
            job.garage_notes   = request.data.get('garage_notes', '')
            msg = 'Job declined.'

        elif action == 'START':
            if job.status != 'ACCEPTED':
                return Response({'detail': 'Job must be accepted before starting.'}, status=status.HTTP_400_BAD_REQUEST)
            job.status       = 'IN_PROGRESS'
            job.garage_notes = request.data.get('garage_notes', '')
            msg = 'Repair started.'

        elif action == 'COMPLETE':
            if job.status != 'IN_PROGRESS':
                return Response({'detail': 'Job must be in progress to complete.'}, status=status.HTTP_400_BAD_REQUEST)
            actual_cost = request.data.get('actual_cost')
            if not actual_cost:
                return Response({'detail': 'Actual repair cost is required.'}, status=status.HTTP_400_BAD_REQUEST)
            job.status        = 'COMPLETED'
            job.completed_at  = timezone.now()
            job.actual_cost   = actual_cost
            job.garage_notes  = request.data.get('garage_notes', '')
            # Update claim status to SETTLED
            job.claim.status     = 'SETTLED'
            job.claim.settled_at = timezone.now()
            job.claim.save(update_fields=['status', 'settled_at'])
            msg = 'Repair completed. Claim marked as settled.'

        else:
            return Response({'detail': 'Invalid action. Use ACCEPT, DECLINE, START, or COMPLETE.'}, status=status.HTTP_400_BAD_REQUEST)

        job.save()
        log_action(user, AuditLog.Action.UPDATE, 'RepairJob', job.id,
                   f'Garage action: {action} on {job.claim.claim_number}', request)

        return Response({'message': msg, 'status': job.status})


class AdminAssignGarageView(APIView):
    """
    POST /api/claims/<id>/assign-garage/
    Admin assigns an approved claim to a garage for repair.

    Body: { "garage_id": "<uuid>", "estimated_cost": 1200.00 }
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        from .models import RepairJob
        if not request.user.is_admin_user:
            return Response({'detail': 'Admin only.'}, status=status.HTTP_403_FORBIDDEN)

        claim = get_object_or_404(Claim, pk=pk)
        if claim.status != 'APPROVED':
            return Response({'detail': 'Only approved claims can be assigned to a garage.'}, status=status.HTTP_400_BAD_REQUEST)

        garage_id = request.data.get('garage_id')
        if not garage_id:
            return Response({'detail': 'garage_id is required.'}, status=status.HTTP_400_BAD_REQUEST)

        garage = get_object_or_404(User, pk=garage_id, role=User.Role.GARAGE)
        if not garage.is_active:
            return Response({'detail': 'This garage account is inactive.'}, status=status.HTTP_400_BAD_REQUEST)

        # Create or update repair job
        job, created = RepairJob.objects.update_or_create(
            claim=claim,
            defaults={
                'garage':         garage,
                'assigned_by':    request.user,
                'status':         'PENDING',
                'estimated_cost': request.data.get('estimated_cost') or claim.approved_amount,
            }
        )

        log_action(request.user, AuditLog.Action.ASSIGN, 'RepairJob', job.id,
                   f'Claim {claim.claim_number} assigned to garage {garage.get_full_name()}', request)

        # Notify garage by email
        _send_email_to_user(
            recipient=garage,
            subject=f'🔧 New Repair Job — {claim.claim_number}',
            body=(
                f'Dear {garage.get_full_name()},\n\n'
                f'A new repair job has been assigned to you.\n\n'
                f'Claim Number : {claim.claim_number}\n'
                f'Vehicle      : {claim.vehicle.year} {claim.vehicle.make} {claim.vehicle.model}\n'
                f'Plate        : {claim.vehicle.license_plate}\n'
                f'Client       : {claim.client.get_full_name()}\n'
                f'Est. Cost    : USD {float(job.estimated_cost or 0):,.2f}\n\n'
                f'Please log in to your garage dashboard to accept or decline this job.\n\n'
                f'Regards,\n{settings.COMPANY_NAME} Admin Team'
            ),
            notification_type=Notification.NotificationType.GENERAL,
            claim=claim,
        )

        return Response({
            'message': f'Claim assigned to {garage.get_full_name()}.',
            'job_id':  str(job.id),
            'status':  job.status,
        }, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)
