from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from . import views

urlpatterns = [

    # ── Authentication ────────────────────────────────────────────────────────
    path('auth/register/',              views.RegisterView.as_view(),             name='register'),
    path('auth/register-adjuster/',     views.AdjusterRegisterView.as_view(),     name='register_adjuster'), 
    path('auth/login/',                 views.LoginView.as_view(),                name='login'),
    path('auth/logout/',                views.LogoutView.as_view(),               name='logout'),
    path('auth/token/refresh/',         TokenRefreshView.as_view(),               name='token_refresh'),
    path('auth/change-password/',       views.ChangePasswordView.as_view(),       name='change_password'),
    path('auth/password-reset/',        views.PasswordResetRequestView.as_view(), name='password_reset_request'),
    path('auth/password-reset/confirm/',views.PasswordResetConfirmView.as_view(), name='password_reset_confirm'),

    # ── Profile ───────────────────────────────────────────────────────────────
    path('profile/',                    views.MyProfileView.as_view(),            name='my_profile'),
    path('users/',                      views.UserListView.as_view(),             name='user_list'),
   
    # ── Vehicles ──────────────────────────────────────────────────────────────
    path('vehicles/',                   views.VehicleListCreateView.as_view(),    name='vehicle_list_create'),
    path('vehicles/<uuid:pk>/',         views.VehicleDetailView.as_view(),        name='vehicle_detail'),

    # ── Policies ──────────────────────────────────────────────────────────────
    path('policies/',                   views.PolicyListView.as_view(),           name='policy_list'),
    path('policies/<uuid:pk>/',         views.PolicyDetailView.as_view(),         name='policy_detail'),

    # ── AI Assessment Proxy ──────────────────────────────────────────────────────
    path('claims/ai-assess/',           views.AIAssessmentProxyView.as_view(),     name='ai_assess'),

    # ── Claims ────────────────────────────────────────────────────────────────
    path('claims/',                     views.ClaimListCreateView.as_view(),      name='claim_list_create'),
    path('claims/<uuid:pk>/',           views.ClaimDetailView.as_view(),          name='claim_detail'),
    path('claims/<uuid:pk>/decision/',  views.ClaimDecisionView.as_view(),        name='claim_decision'),
    path('claims/<uuid:pk>/assign/',    views.ClaimAssignAdjusterView.as_view(),  name='claim_assign'),
    path('claims/<uuid:pk>/report/',    views.ClaimReportView.as_view(),          name='claim_report'),

    # ── Claim Documents ───────────────────────────────────────────────────────
    path('claims/<uuid:claim_id>/documents/',        views.ClaimDocumentListView.as_view(),   name='claim_docs_list'),
    path('claims/<uuid:claim_id>/documents/upload/', views.ClaimDocumentUploadView.as_view(), name='claim_doc_upload'),

    # ── Claim Assessments ─────────────────────────────────────────────────────
    path('claims/<uuid:claim_id>/assessments/',      views.ClaimAssessmentCreateView.as_view(), name='claim_assessment_create'),

    # ── Dashboard ─────────────────────────────────────────────────────────────
    path('dashboard/',                  views.DashboardView.as_view(),            name='dashboard'),

    # ── Notifications ─────────────────────────────────────────────────────────
    path('notifications/',              views.NotificationListView.as_view(),     name='notification_list'),
    path('notifications/<uuid:pk>/read/', views.NotificationMarkReadView.as_view(), name='notification_read'),

      # ── Admin User Management ────────────────────────────────────────────────────
    path('admin/users/',              views.UserManagementListView.as_view(),   name='admin_users'),
    path('admin/users/<uuid:pk>/',    views.UserManagementDetailView.as_view(), name='admin_user_detail'),

    # ── Garage ────────────────────────────────────────────────────────────────────
    path('garage/jobs/',                  views.GarageJobListView.as_view(),     name='garage_jobs'),
    path('garage/jobs/<uuid:pk>/action/', views.GarageJobActionView.as_view(),   name='garage_job_action'),
    path('claims/<uuid:pk>/assign-garage/', views.AdminAssignGarageView.as_view(), name='assign_garage'),

    # ── Adjuster ──────────────────────────────────────────────────────────────
    path('adjuster/dashboard/',         views.AdjusterDashboardView.as_view(),   name='adjuster_dashboard'),  
    path('adjuster/claims/',            views.AdjusterClaimListView.as_view(),   name='adjuster_claims'),     

    # ── Reports ───────────────────────────────────────────────────────────────
    path('reports/monthly/',            views.MonthlyReportView.as_view(),       name='monthly_report'),
   
]
