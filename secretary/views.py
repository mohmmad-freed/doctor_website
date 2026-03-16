from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse


@login_required
def dashboard(request):
    return HttpResponse("Secretary Dashboard - Coming Soon!")


@login_required
def appointments_list(request):
    return HttpResponse("Secretary Appointments List - Coming Soon!")


@login_required
def create_appointment(request):
    return HttpResponse("Create Appointment - Coming Soon!")


@login_required
def edit_appointment(request, appointment_id):
    return HttpResponse(f"Edit Appointment {appointment_id} - Coming Soon!")


# ============================================
# SECRETARY INVITATIONS FLOW
# ============================================

from django.contrib import messages
from django.shortcuts import redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from clinics.models import ClinicInvitation
from clinics.services import accept_invitation, reject_invitation
from accounts.backends import PhoneNumberAuthBackend


@login_required
def secretary_invitations_inbox(request):
    """View pending invitations for the logged-in secretary."""
    user = request.user
    normalized_phone = PhoneNumberAuthBackend.normalize_phone_number(user.phone)
    
    invitations = ClinicInvitation.objects.filter(
        doctor_phone=normalized_phone, 
        role="SECRETARY",
        status="PENDING"
    ).select_related('clinic', 'invited_by').order_by('-created_at')
    
    return render(request, "secretary/invitations_inbox.html", {
        "invitations": invitations,
    })


@login_required
def accept_invitation_view(request, invitation_id):
    """Action to accept a secretary invitation."""
    invitation = get_object_or_404(ClinicInvitation, id=invitation_id, role="SECRETARY")
    
    if request.method == "POST":
        try:
            staff = accept_invitation(invitation, request.user)
            messages.success(request, f"تم الانضمام بنجاح إلى عيادة {staff.clinic.name} بصفة سكرتير/ة.")
        except Exception as e:
            err_msg = str(e)
            if hasattr(e, 'messages'):
                err_msg = " ".join(e.messages)
            messages.error(request, f"خطأ: {err_msg}")
            
    return redirect(reverse("secretary:secretary_invitations_inbox"))


@login_required
def reject_invitation_view(request, invitation_id):
    """Action to reject a secretary invitation."""
    invitation = get_object_or_404(ClinicInvitation, id=invitation_id, role="SECRETARY")
    
    if request.method == "POST":
        try:
            reject_invitation(invitation, request.user)
            messages.success(request, "تم رفض الدعوة.")
        except Exception as e:
            err_msg = str(e)
            if hasattr(e, 'messages'):
                err_msg = " ".join(e.messages)
            messages.error(request, f"خطأ: {err_msg}")
            
    return redirect(reverse("secretary:secretary_invitations_inbox"))


def guest_accept_invitation_view(request, token):
    """
    Public endpoint for SMS link. 
    Shows generic error if token invalid/expired.
    If valid, redirects to login/reg storing token in session.
    """
    try:
        invitation = ClinicInvitation.objects.get(token=token, role="SECRETARY")
    except ClinicInvitation.DoesNotExist:
        return render(request, "secretary/invalid_invitation.html", {
            "error": "رابط الدعوة غير صالح أو قد تم استخدامه مسبقاً."
        })
        
    if invitation.status != "PENDING" or invitation.is_expired:
         return render(request, "secretary/invalid_invitation.html", {
            "error": "انتهت صلاحية هذه الدعوة أو لم تعد متاحة."
        })
        
    if request.user.is_authenticated:
        normalized_user_phone = PhoneNumberAuthBackend.normalize_phone_number(request.user.phone)
        if normalized_user_phone == invitation.doctor_phone:
             # Already logged in as the right user, redirect to inbox to accept
             return redirect(reverse("secretary:secretary_invitations_inbox"))
        else:
             # Logged in as someone else (wrong phone)
             return render(request, "secretary/invalid_invitation.html", {
                "error": "لا تملك الصلاحية للوصول إلى هذه الدعوة. يرجى تسجيل الدخول بالحساب الصحيح."
            })
            
    # Unauthenticated but token is valid: store generic next url and redirect to login
    request.session["pending_invitation_token"] = token
    request.session["pending_invitation_app"] = "secretary"
    
    messages.info(request, "يرجى تسجيل الدخول أو إنشاء حساب جديد لقبول دعوة الانضمام للعيادة كـ سكرتير/ة.")
    return redirect(reverse("accounts:login"))