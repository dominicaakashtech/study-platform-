from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth import login, update_session_auth_hash, authenticate
from django.contrib.auth.models import User
from django.contrib import messages
from django.db.models import Q, Count
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from .models import (StudyGroup, Resource, Message, StudySession,
                     OTPVerification, UserProfile, VideoMeetRoom,
                     Announcement, DirectMessage)
from .forms import (RegisterForm, StudyGroupForm, ResourceForm, MessageForm,
                    StudySessionForm, OTPVerifyForm, AnnouncementForm,
                    UserUpdateForm, UserProfileForm, GroupJoinPasswordForm)
from django.utils import timezone
from django.core.mail import send_mail
from django.conf import settings
import os, uuid, json, threading


# ── Helpers ────────────────────────────────────────────────────────────────────

def send_otp_email(user, otp_code, purpose='registration'):
    def _send_task():
        subject_map = {
            'registration':   'LEARN HUB – Verify Your Email',
            'login':          'LEARN HUB – Login OTP',
            'password_reset': 'LEARN HUB – Password Reset OTP',
        }
        subject = subject_map.get(purpose, 'LEARN HUB – OTP Code')
        message = (
            f"Hello {user.username},\n\n"
            f"Your LEARN HUB verification code is: {otp_code}\n\n"
            f"This code expires in 10 minutes.\n\n"
            f"If you did not request this, please ignore.\n\n– LEARN HUB Team"
        )
        try:
            send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [user.email], fail_silently=True)
        except Exception:
            pass

    # Start sending in the background to avoid blocking the registration response
    threading.Thread(target=_send_task, daemon=True).start()


def get_or_create_profile(user):
    profile, _ = UserProfile.objects.get_or_create(user=user)
    return profile


def _issue_otp(user, purpose):
    """Create a new OTP record, send email, return the code."""
    otp_code = OTPVerification.generate_otp()
    OTPVerification.objects.create(user=user, otp_code=otp_code, purpose=purpose)
    if user.email:
        send_otp_email(user, otp_code, purpose)
    return otp_code


# ── Registration + OTP ─────────────────────────────────────────────────────────

def register(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    if request.method == 'POST':
        form = RegisterForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            user.is_active = True
            user.save()
            phone = form.cleaned_data.get('phone_number', '')
            profile = get_or_create_profile(user)
            if phone:
                profile.phone_number = phone
                profile.save()
            _issue_otp(user, 'registration')
            request.session['otp_user_id'] = user.pk
            request.session['otp_purpose'] = 'registration'
            messages.info(request, f'A 6-digit verification code has been sent to {user.email or "your registered contact"}.')
            return redirect('verify_otp')
    else:
        form = RegisterForm()
    return render(request, 'groups/register.html', {'form': form, 'hide_nav_footer': True})


def verify_otp(request):
    user_id = request.session.get('otp_user_id')
    purpose = request.session.get('otp_purpose', 'registration')

    # Guard: no session → redirect to appropriate page
    if not user_id:
        if purpose == 'password_reset':
            return redirect('forgot_password')
        return redirect('register')

    user = get_object_or_404(User, pk=user_id)

    if request.method == 'POST':
        form = OTPVerifyForm(request.POST)
        if form.is_valid():
            entered = form.cleaned_data['otp_code'].strip()
            latest_otp = OTPVerification.objects.filter(
                user=user, purpose=purpose, is_verified=False
            ).order_by('-created_at').first()

            if not latest_otp:
                messages.error(request, 'No OTP found. Please request a new one.')
            elif latest_otp.is_expired():
                messages.error(request, 'OTP has expired. Please request a new one.')
            elif latest_otp.otp_code != entered:
                messages.error(request, 'Invalid OTP code. Please try again.')
            else:
                latest_otp.is_verified = True
                latest_otp.save()

                # ── Registration / login OTP: mark email verified & log in ──
                if purpose in ('registration', 'login'):
                    profile = get_or_create_profile(user)
                    profile.email_verified = True
                    profile.save()
                    # Clean up session
                    request.session.pop('otp_user_id', None)
                    request.session.pop('otp_purpose', None)
                    login(request, user)
                    messages.success(request, f'Welcome to LEARN HUB, {user.username}!')
                    return redirect('dashboard')

                # ── Password reset OTP: let them set new password ──
                elif purpose == 'password_reset':
                    request.session.pop('otp_user_id', None)
                    request.session.pop('otp_purpose', None)
                    request.session['pwd_reset_user_id'] = user.pk  # carry forward
                    messages.success(request, 'OTP verified! Please set your new password.')
                    return redirect('reset_password')
    else:
        form = OTPVerifyForm()

    return render(request, 'groups/verify_otp.html', {
        'form': form,
        'user': user,
        'email': user.email,
        'purpose': purpose,
        'hide_nav_footer': True,
    })


def resend_otp(request):
    user_id = request.session.get('otp_user_id')
    if not user_id:
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'error': 'Session expired'}, status=400)
        return redirect('register')
    user = get_object_or_404(User, pk=user_id)
    purpose = request.session.get('otp_purpose', 'registration')
    from datetime import timedelta
    recent = OTPVerification.objects.filter(
        user=user, purpose=purpose,
        created_at__gte=timezone.now() - timedelta(seconds=60)
    ).exists()
    if recent:
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'error': 'Please wait 60 seconds before requesting a new OTP.'}, status=429)
        messages.error(request, 'Please wait 60 seconds before requesting a new OTP.')
        return redirect('verify_otp')
    _issue_otp(user, purpose)
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({'success': True, 'message': 'New OTP sent to your email.'})
    messages.success(request, 'A new OTP has been sent to your email.')
    return redirect('verify_otp')


# ── Login with OTP (replaces Django's built-in LoginView for OTP users) ────────

def login_view(request):
    """
    Custom login view:
    - Authenticates credentials.
    - If the user's email is verified, sends a login OTP before granting access.
    - If not yet verified (new user), logs in directly and redirects to OTP verify.
    """
    if request.user.is_authenticated:
        return redirect('dashboard')

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')
        user = authenticate(request, username=username, password=password)

        if user is None:
            return render(request, 'groups/login.html', {'error': True, 'hide_nav_footer': True})

        profile = get_or_create_profile(user)

        if profile.is_banned:
            return render(request, 'groups/login.html', {
                'banned': True,
                'ban_reason': profile.ban_reason or 'Your account has been suspended.',
                'hide_nav_footer': True,
            })

        if not user.is_active:
            return render(request, 'groups/login.html', {'inactive': True, 'hide_nav_footer': True})

        # Email verified → require login OTP
        if profile.email_verified and user.email:
            _issue_otp(user, 'login')
            request.session['otp_user_id'] = user.pk
            request.session['otp_purpose'] = 'login'
            messages.info(request, f'A login verification code has been sent to {user.email}.')
            return redirect('verify_otp')

        # Not yet verified (just registered, skipped OTP) → log in directly
        login(request, user)
        messages.success(request, f'Welcome back, {user.username}!')
        return redirect(request.GET.get('next', 'dashboard'))

    return render(request, 'groups/login.html', {'hide_nav_footer': True})


# ── Forgot Password ─────────────────────────────────────────────────────────────

def forgot_password(request):
    """Step 1 – user enters their email."""
    if request.user.is_authenticated:
        return redirect('dashboard')

    if request.method == 'POST':
        email = request.POST.get('email', '').strip()
        # Always show the same success message to prevent user-enumeration
        user = User.objects.filter(email__iexact=email).first()
        if user:
            _issue_otp(user, 'password_reset')
            request.session['otp_user_id'] = user.pk
            request.session['otp_purpose'] = 'password_reset'
        messages.success(request, 'If that email is registered, you will receive a reset code shortly.')
        return redirect('verify_otp')

    return render(request, 'groups/forgot_password.html', {'hide_nav_footer': True})


def reset_password(request):
    """Step 3 – user sets a new password after OTP verified."""
    user_id = request.session.get('pwd_reset_user_id')
    if not user_id:
        messages.error(request, 'Invalid or expired reset link. Please start again.')
        return redirect('forgot_password')

    user = get_object_or_404(User, pk=user_id)

    if request.method == 'POST':
        new_pwd = request.POST.get('new_password', '')
        confirm = request.POST.get('confirm_password', '')

        if len(new_pwd) < 8:
            messages.error(request, 'Password must be at least 8 characters.')
        elif new_pwd.isdigit():
            messages.error(request, 'Password cannot be entirely numeric.')
        elif new_pwd != confirm:
            messages.error(request, 'Passwords do not match.')
        else:
            user.set_password(new_pwd)
            user.save()
            request.session.pop('pwd_reset_user_id', None)
            # Auto-login after reset
            login(request, user)
            messages.success(request, 'Password reset successfully! You are now logged in.')
            return redirect('dashboard')

    return render(request, 'groups/reset_password.html', {'reset_user': user, 'hide_nav_footer': True})


# ── Core Pages ─────────────────────────────────────────────────────────────────

def home(request):
    featured_groups = StudyGroup.objects.filter(is_private=False).annotate(
        member_count=Count('members'))[:6]
    announcements = Announcement.objects.filter(is_active=True)[:3]
    return render(request, 'groups/home.html', {
        'featured_groups': featured_groups,
        'announcements': announcements,
    })


@login_required
def dashboard(request):
    my_groups = request.user.joined_groups.annotate(member_count=Count('members'))
    created_groups = request.user.created_groups.annotate(member_count=Count('members'))
    profile = get_or_create_profile(request.user)
    unread_dm_count = DirectMessage.objects.filter(recipient=request.user, is_read=False).count()
    return render(request, 'groups/dashboard.html', {
        'my_groups': my_groups,
        'created_groups': created_groups,
        'profile': profile,
        'unread_dm_count': unread_dm_count,
    })


def browse_groups(request):
    query = request.GET.get('q', '')
    subject = request.GET.get('subject', '')
    groups = StudyGroup.objects.filter(is_private=False).annotate(member_count=Count('members'))
    if query:
        groups = groups.filter(
            Q(name__icontains=query) | Q(description__icontains=query) | Q(subject__icontains=query))
    if subject:
        groups = groups.filter(subject__icontains=subject)
    subjects = StudyGroup.objects.values_list('subject', flat=True).distinct()
    return render(request, 'groups/browse.html', {
        'groups': groups, 'subjects': subjects, 'query': query, 'selected_subject': subject})


@login_required
def create_group(request):
    if request.method == 'POST':
        form = StudyGroupForm(request.POST, request.FILES)
        if form.is_valid():
            group = form.save(commit=False)
            group.created_by = request.user
            raw_pwd = form.cleaned_data.get('join_password', '').strip()
            group.join_password = raw_pwd if raw_pwd else None
            group.save()
            group.members.add(request.user)
            messages.success(request, 'Study group created successfully!')
            return redirect('group_detail', pk=group.pk)
    else:
        form = StudyGroupForm()
    return render(request, 'groups/create_group.html', {'form': form})


@login_required
def group_detail(request, pk):
    group = get_object_or_404(StudyGroup, pk=pk)
    is_member = request.user in group.members.all()

    if not is_member and group.is_private:
        messages.error(request, 'This is a private group.')
        return redirect('browse_groups')

    if request.method == 'POST' and request.POST.get('update_banner') and request.user == group.created_by:
        if 'cover_image' in request.FILES:
            if group.cover_image:
                try:
                    os.remove(group.cover_image.path)
                except Exception:
                    pass
            group.cover_image = request.FILES['cover_image']
            group.save()
            messages.success(request, 'Group banner updated!')
        return redirect('group_detail', pk=pk)

    resources = group.resources.all()
    chat_messages = group.messages.all().order_by('sent_at')[:50]
    upcoming_sessions = group.sessions.filter(scheduled_time__gte=timezone.now())
    active_room = group.meet_rooms.filter(is_active=True).first()
    is_admin = (request.user == group.created_by)

    return render(request, 'groups/group_detail.html', {
        'group': group,
        'is_member': is_member,
        'is_admin': is_admin,
        'resources': resources,
        'messages': chat_messages,
        'upcoming_sessions': upcoming_sessions,
        'message_form': MessageForm(),
        'resource_form': ResourceForm(),
        'session_form': StudySessionForm(),
        'active_room': active_room,
    })


@login_required
def join_group(request, pk):
    group = get_object_or_404(StudyGroup, pk=pk)
    if request.user in group.members.all():
        messages.info(request, 'You are already a member.')
        return redirect('group_detail', pk=pk)
    if group.max_members and group.members.count() >= group.max_members:
        messages.error(request, 'This group is full.')
        return redirect('group_detail', pk=pk)
    if group.join_password:
        if request.method == 'POST':
            pwd_form = GroupJoinPasswordForm(request.POST)
            if pwd_form.is_valid():
                entered = pwd_form.cleaned_data['password']
                if entered == group.join_password:
                    group.members.add(request.user)
                    messages.success(request, f'You joined {group.name}!')
                    return redirect('group_detail', pk=pk)
                else:
                    pwd_form.add_error('password', 'Incorrect password. Please try again.')
            return render(request, 'groups/join_group_password.html', {'group': group, 'form': pwd_form})
        else:
            pwd_form = GroupJoinPasswordForm()
            return render(request, 'groups/join_group_password.html', {'group': group, 'form': pwd_form})
    group.members.add(request.user)
    messages.success(request, f'You joined {group.name}!')
    return redirect('group_detail', pk=pk)


@login_required
def leave_group(request, pk):
    group = get_object_or_404(StudyGroup, pk=pk)
    if request.user == group.created_by:
        messages.error(request, 'Group creator cannot leave. Delete the group instead.')
    elif request.user not in group.members.all():
        messages.error(request, 'You are not a member.')
    else:
        group.members.remove(request.user)
        messages.success(request, f'You left {group.name}.')
        return redirect('dashboard')
    return redirect('group_detail', pk=pk)


@login_required
def add_resource(request, pk):
    group = get_object_or_404(StudyGroup, pk=pk)
    if request.user not in group.members.all():
        messages.error(request, 'You must be a member to add resources.')
        return redirect('group_detail', pk=pk)
    if request.method == 'POST':
        form = ResourceForm(request.POST, request.FILES)
        if form.is_valid():
            resource = form.save(commit=False)
            resource.study_group = group
            resource.uploaded_by = request.user
            resource.save()
            messages.success(request, 'Resource added successfully!')
    return redirect('group_detail', pk=pk)


def _detect_media_type(uploaded_file):
    """Derive media_type from MIME. Returns: image|video|audio|pdf|file"""
    mime = getattr(uploaded_file, 'content_type', '') or ''
    name = (uploaded_file.name or '').lower()
    if mime.startswith('image/'):
        return 'image'
    if mime.startswith('video/'):
        return 'video'
    if mime.startswith('audio/'):
        return 'audio'
    if mime == 'application/pdf' or name.endswith('.pdf'):
        return 'pdf'
    return 'file'


@login_required
@require_POST
def send_message(request, pk):
    group = get_object_or_404(StudyGroup, pk=pk)
    if request.user not in group.members.all():
        return JsonResponse({'error': 'Not a member'}, status=403)

    content    = request.POST.get('content', '').strip()
    media_file = request.FILES.get('media_file')

    if not content and not media_file:
        return JsonResponse({'error': 'Empty message'}, status=400)

    media_url  = None
    media_name = None
    media_type = request.POST.get('media_type', 'file').strip().lower()
    resource   = None

    if media_file:
        # Determine real type from MIME, not just client hint
        media_type = _detect_media_type(media_file)

        # 50 MB cap
        if media_file.size > 50 * 1024 * 1024:
            return JsonResponse({'error': 'File too large (max 50 MB).'}, status=413)

        try:
            resource = Resource.objects.create(
                study_group=group,
                title=media_file.name,
                resource_type='document',
                file=media_file,
                uploaded_by=request.user,
                description=f'__CHAT_MEDIA_{media_type.upper()}__',
            )
            media_url  = resource.file.url
            media_name = media_file.name
        except Exception as exc:
            return JsonResponse({'error': f'Upload failed: {exc}'}, status=500)

    if resource:
        stored_content = f'__MEDIA__{media_type}__{resource.pk}' + (f'|{content}' if content else '')
    else:
        stored_content = content

    msg = Message.objects.create(
        study_group=group,
        sender=request.user,
        content=stored_content,
    )
    return JsonResponse({
        'success': True,
        'message': _serialize_message(msg, request.user, media_url, media_name, media_type, content),
    })


def _parse_media_content(content):
    """
    Format stored in Message.content for media messages:
        __MEDIA__<type>__<resource_pk>__          (no caption)
        __MEDIA__<type>__<resource_pk>__|<caption> (with caption)
    Returns (media_type, resource_pk, caption_text)
    """
    if not content.startswith('__MEDIA__'):
        return None, None, content

    # Strip the prefix
    rest = content[len('__MEDIA__'):]   # e.g. "image__42__" or "image__42__|hello"

    # Split on the first __ to get type, then the remainder
    if '__' not in rest:
        return rest, None, ''

    mtype, remainder = rest.split('__', 1)  # remainder = "42__" or "42__|hello"

    # Remainder may end with __ (no caption) or contain |caption
    # Strip a trailing __ if present before the pipe
    # Examples:  "42__"  →  rpk=42, text=""
    #            "42__|hello" →  rpk=42, text="hello"
    #            "42|hello"   →  rpk=42, text="hello"
    if '|' in remainder:
        rpk_part, text = remainder.split('|', 1)
        rpk_part = rpk_part.rstrip('_')
    else:
        rpk_part = remainder.rstrip('_')
        text = ''

    try:
        rpk = int(rpk_part)
    except (ValueError, TypeError):
        rpk = None

    return mtype.strip(), rpk, text


def _serialize_message(msg, current_user, media_url=None, media_name=None, media_type=None, text_override=None):
    content = msg.content
    mtype, rpk, text = _parse_media_content(content)
    display_text = text_override if text_override is not None else (text or '')

    # Resolve media_url / media_name from the Resource record when not supplied directly
    # (needed for polled/fetched messages that were stored as __MEDIA__ tokens)
    resolved_url  = media_url
    resolved_name = media_name
    resolved_type = mtype or media_type
    if rpk and not resolved_url:
        try:
            from .models import Resource as _Resource
            res = _Resource.objects.get(pk=rpk)
            if res.file:
                resolved_url  = res.file.url
                resolved_name = res.title
        except Exception:
            pass

    return {
        'id':         msg.pk,
        'sender':     msg.sender.username,
        'content':    display_text,
        'sent_at':    msg.sent_at.strftime('%b %d, %H:%M'),
        'sent_at_iso': msg.sent_at.isoformat(),
        'is_own':     msg.sender == current_user,
        'media_url':  resolved_url,
        'media_name': resolved_name,
        'media_type': resolved_type,
    }


@login_required
def get_messages(request, pk):
    group = get_object_or_404(StudyGroup, pk=pk)
    if request.user not in group.members.all():
        return JsonResponse({'error': 'Not a member'}, status=403)
    since_id = int(request.GET.get('since', 0))
    msgs = list(
        group.messages
        .filter(pk__gt=since_id)
        .select_related('sender')
        .order_by('sent_at')
    )
    serialized = [_serialize_message(m, request.user) for m in msgs]
    last_id = msgs[-1].pk if msgs else since_id
    return JsonResponse({'messages': serialized, 'last_id': last_id})


@login_required
def add_session(request, pk):
    group = get_object_or_404(StudyGroup, pk=pk)
    if request.user not in group.members.all():
        messages.error(request, 'You must be a member to add sessions.')
        return redirect('group_detail', pk=pk)
    if request.method == 'POST':
        form = StudySessionForm(request.POST)
        if form.is_valid():
            session = form.save(commit=False)
            session.study_group = group
            session.created_by = request.user
            session.save()
            messages.success(request, 'Study session scheduled!')
    return redirect('group_detail', pk=pk)


# ── Profile ────────────────────────────────────────────────────────────────────

@login_required
def profile_view(request):
    profile = get_or_create_profile(request.user)
    return render(request, 'groups/profile.html', {'profile': profile})


@login_required
def profile_edit(request):
    profile = get_or_create_profile(request.user)
    if request.method == 'POST':
        u_form = UserUpdateForm(request.POST, instance=request.user)
        p_form = UserProfileForm(request.POST, request.FILES, instance=profile)
        if u_form.is_valid() and p_form.is_valid():
            u_form.save()
            p_form.save()
            messages.success(request, 'Profile updated!')
            return redirect('profile')
    else:
        u_form = UserUpdateForm(instance=request.user)
        p_form = UserProfileForm(instance=profile)
    return render(request, 'groups/profile_edit.html', {'u_form': u_form, 'p_form': p_form, 'profile': profile})


def public_profile(request, username):
    target = get_object_or_404(User, username=username)
    profile = get_or_create_profile(target)
    return render(request, 'groups/public_profile.html', {'target': target, 'profile': profile})


# ── Direct Messages ────────────────────────────────────────────────────────────

@login_required
def dm_inbox(request):
    user = request.user
    sent_to     = DirectMessage.objects.filter(sender=user).values_list('recipient_id', flat=True).distinct()
    received_from = DirectMessage.objects.filter(recipient=user).values_list('sender_id', flat=True).distinct()
    partner_ids = set(sent_to) | set(received_from)
    partners    = User.objects.filter(pk__in=partner_ids)

    conversations = []
    for partner in partners:
        last_msg = DirectMessage.objects.filter(
            Q(sender=user, recipient=partner) | Q(sender=partner, recipient=user)
        ).order_by('-sent_at').first()
        unread = DirectMessage.objects.filter(sender=partner, recipient=user, is_read=False).count()
        conversations.append({'partner': partner, 'last_msg': last_msg, 'unread': unread,
                               'profile': get_or_create_profile(partner)})
    conversations.sort(key=lambda x: x['last_msg'].sent_at if x['last_msg'] else timezone.now(), reverse=True)
    return render(request, 'groups/dm_inbox.html', {'conversations': conversations})


@login_required
def dm_conversation(request, username):
    other = get_object_or_404(User, username=username)
    if other == request.user:
        return redirect('dm_inbox')
    DirectMessage.objects.filter(sender=other, recipient=request.user, is_read=False).update(is_read=True)
    conversation = DirectMessage.objects.filter(
        Q(sender=request.user, recipient=other) | Q(sender=other, recipient=request.user)
    ).order_by('sent_at')
    other_profile = get_or_create_profile(other)
    return render(request, 'groups/dm_conversation.html', {
        'other': other, 'other_profile': other_profile, 'conversation': conversation})


@login_required
def dm_send(request, username):
    if request.method != 'POST':
        return redirect('dm_inbox')
    other = get_object_or_404(User, username=username)
    content = request.POST.get('content', '').strip()
    if content:
        DirectMessage.objects.create(sender=request.user, recipient=other, content=content)
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'status': 'ok'})
    return redirect('dm_conversation', username=username)


@login_required
def dm_unread_count(request):
    count = DirectMessage.objects.filter(recipient=request.user, is_read=False).count()
    return JsonResponse({'count': count})


@login_required
def dm_fetch_new(request, username):
    other = get_object_or_404(User, username=username)
    since_id = int(request.GET.get('since', 0))
    msgs = DirectMessage.objects.filter(
        Q(sender=other, recipient=request.user) | Q(sender=request.user, recipient=other),
        pk__gt=since_id
    ).order_by('sent_at')
    msgs.filter(sender=other, is_read=False).update(is_read=True)
    data = [{
        'id': m.pk, 'content': m.content,
        'is_mine': m.sender == request.user,
        'sent_at': m.sent_at.strftime('%b %d, %H:%M'),
    } for m in msgs]
    return JsonResponse({'messages': data})


# ── Admin ──────────────────────────────────────────────────────────────────────

def is_staff(user):
    return user.is_authenticated and user.is_staff


@login_required
@user_passes_test(is_staff, login_url='home')
def admin_dashboard(request):
    pwd_success = False
    pwd_error = None
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'change_password':
            current = request.POST.get('current_password', '')
            new_pwd = request.POST.get('new_password', '')
            confirm = request.POST.get('confirm_password', '')
            if not request.user.check_password(current):
                pwd_error = 'Current password is incorrect.'
            elif len(new_pwd) < 8:
                pwd_error = 'New password must be at least 8 characters.'
            elif new_pwd != confirm:
                pwd_error = 'New passwords do not match.'
            else:
                request.user.set_password(new_pwd)
                request.user.save()
                update_session_auth_hash(request, request.user)
                pwd_success = True
        elif action == 'create_announcement':
            ann_form = AnnouncementForm(request.POST)
            if ann_form.is_valid():
                ann = ann_form.save(commit=False)
                ann.created_by = request.user
                ann.save()
                messages.success(request, 'Announcement created!')
                return redirect('admin_dashboard')

    context = {
        'total_users':    User.objects.count(),
        'total_groups':   StudyGroup.objects.count(),
        'total_messages': Message.objects.count(),
        'total_resources': Resource.objects.count(),
        'total_sessions': StudySession.objects.count(),
        'private_groups': StudyGroup.objects.filter(is_private=True).count(),
        'total_meet_rooms': VideoMeetRoom.objects.count(),
        'active_meets':   VideoMeetRoom.objects.filter(is_active=True).count(),
        'verified_users': UserProfile.objects.filter(email_verified=True).count(),
        'banned_users':   UserProfile.objects.filter(is_banned=True).count(),
        'all_users':      User.objects.order_by('-date_joined')[:50],
        'all_groups':     StudyGroup.objects.annotate(member_count=Count('members')).order_by('-created_at')[:50],
        'recent_messages': Message.objects.select_related('sender', 'study_group').order_by('-sent_at')[:20],
        'announcements':  Announcement.objects.all()[:20],
        'ann_form':       AnnouncementForm(),
        'pwd_success':    pwd_success,
        'pwd_error':      pwd_error,
    }
    return render(request, 'groups/admin_dashboard.html', context)


@login_required
@user_passes_test(is_staff, login_url='home')
def admin_toggle_user(request, user_id):
    target = get_object_or_404(User, pk=user_id)
    if target == request.user or target.is_superuser:
        messages.error(request, 'Cannot modify this account.')
        return redirect('admin_dashboard')
    profile = get_or_create_profile(target)
    profile.is_banned = not profile.is_banned
    if profile.is_banned:
        profile.ban_reason = request.POST.get('ban_reason', 'Violated platform rules.')
        target.is_active = False
    else:
        target.is_active = True
        profile.ban_reason = ''
    profile.save()
    target.save()
    action = 'banned' if profile.is_banned else 'unbanned'
    messages.success(request, f'User {target.username} has been {action}.')
    return redirect('admin_dashboard')


@login_required
@user_passes_test(is_staff, login_url='home')
def admin_delete_group(request, group_id):
    group = get_object_or_404(StudyGroup, pk=group_id)
    name = group.name
    group.delete()
    messages.success(request, f'Group "{name}" deleted.')
    return redirect('admin_dashboard')


@login_required
@user_passes_test(is_staff, login_url='home')
def admin_delete_message(request, msg_id):
    msg = get_object_or_404(Message, pk=msg_id)
    msg.delete()
    messages.success(request, 'Message deleted.')
    return redirect('admin_dashboard')


@login_required
@user_passes_test(is_staff, login_url='home')
def admin_create_announcement(request):
    if request.method == 'POST':
        form = AnnouncementForm(request.POST)
        if form.is_valid():
            ann = form.save(commit=False)
            ann.created_by = request.user
            ann.save()
            messages.success(request, 'Announcement published!')
    return redirect('admin_dashboard')


@login_required
@user_passes_test(is_staff, login_url='home')
def admin_delete_announcement(request, ann_id):
    ann = get_object_or_404(Announcement, pk=ann_id)
    ann.delete()
    messages.success(request, 'Announcement deleted.')
    return redirect('admin_dashboard')

@login_required
def profile_view(request):
    profile = get_or_create_profile(request.user)
    return render(request, 'groups/profile.html', {
        'profile': profile,
        'target_user': request.user,  # add this
    })