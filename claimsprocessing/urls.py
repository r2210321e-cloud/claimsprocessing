from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from . import views

urlpatterns = [
    # Django Admin
    path('admin/', admin.site.urls),

    # REST API
    path('api/', include('claims.urls')),

    # Frontend Pages
    path('',              views.login_view,        name='login'),
    path('dashboard/',    views.dashboard_view,    name='dashboard'),
    path('submit-claim/', views.submit_claim_view, name='submit_claim'),
    path('track-claim/',  views.track_claim_view,  name='track_claim'),
    path('track-claim.html/', views.track_claim_view, name='track_claim_html'),
    path('dashboard/submit-claim.html/', views.submit_claim_view, name='submit_claim_html'),
    path('track-claim.html/submit-claim.html/', views.submit_claim_view, name='submit_claim_html'),
    path('dashboard/track-claim.html/', views.track_claim_view, name='track_claim_dashboard_html'),
    path('adjuster/',     views.adjuster_view,     name='adjuster'),
    path('manage-users/', views.manage_users_view, name='manage_users'),
    path('garage/',       views.garage_view,       name='garage'),
    path('reports/', views.reports_view, name='reports'),
    path('monthly-reports/', views.monthly_reports_view, name='monthly_reports'),
    path('profile/', views.profile_view, name='profile'),
]

# Serve media and static files in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL,  document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)

# Admin branding
admin.site.site_header  = 'Zimnat Insurance — Claims Admin'
admin.site.site_title   = 'Claims Portal'
admin.site.index_title  = 'Motor Insurance Claims Management'
