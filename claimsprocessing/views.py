from django.shortcuts import render

def login_view(request):
    return render(request, 'login.html')

def dashboard_view(request):
    return render(request, 'dashboard.html')

def submit_claim_view(request):
    return render(request, 'submit-claim.html')

def track_claim_view(request):
    return render(request, 'track-claim.html')

def adjuster_view(request):
    return render(request, 'adjuster-dashboard.html')

def manage_users_view(request):
    return render(request, 'manage-users.html')

def garage_view(request):
    return render(request, 'garage-dashboard.html')

def profile_view(request):
    return render(request, 'profile.html')

def reports_view(request):
    return render(request, 'reports.html')

def monthly_reports_view(request):
    return render(request, 'monthly-reports.html')
