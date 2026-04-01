from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
import random
import string


class StudyGroup(models.Model):
    name = models.CharField(max_length=200)
    subject = models.CharField(max_length=100)
    description = models.TextField()
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='created_groups')
    members = models.ManyToManyField(User, related_name='joined_groups', blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    max_members = models.IntegerField(default=0, help_text='0 means unlimited')
    is_private = models.BooleanField(default=False)
    join_password = models.CharField(
        max_length=128, blank=True, null=True,
        help_text='Optional password required to join this group. Leave blank for open access.'
    )
    cover_image = models.ImageField(upload_to='group_covers/', blank=True, null=True)

    def __str__(self): return self.name
    class Meta: ordering = ['-created_at']


class Resource(models.Model):
    RESOURCE_TYPES = [('document','Document'),('link','Link'),('note','Note')]
    study_group = models.ForeignKey(StudyGroup, on_delete=models.CASCADE, related_name='resources')
    title = models.CharField(max_length=200)
    resource_type = models.CharField(max_length=20, choices=RESOURCE_TYPES)
    description = models.TextField(blank=True)
    file = models.FileField(upload_to='resources/', blank=True, null=True)
    link = models.URLField(blank=True, null=True)
    uploaded_by = models.ForeignKey(User, on_delete=models.CASCADE)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    def __str__(self): return self.title
    class Meta: ordering = ['-uploaded_at']


class Message(models.Model):
    study_group = models.ForeignKey(StudyGroup, on_delete=models.CASCADE, related_name='messages')
    sender = models.ForeignKey(User, on_delete=models.CASCADE)
    content = models.TextField()
    sent_at = models.DateTimeField(auto_now_add=True)
    def __str__(self): return f"{self.sender.username}: {self.content[:50]}"
    class Meta: ordering = ['sent_at']


class DirectMessage(models.Model):
    """Private 1-to-1 messages between users."""
    sender    = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sent_dms')
    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name='received_dms')
    content   = models.TextField()
    sent_at   = models.DateTimeField(auto_now_add=True)
    is_read   = models.BooleanField(default=False)
    def __str__(self): return f"DM {self.sender} → {self.recipient}: {self.content[:40]}"
    class Meta: ordering = ['sent_at']


class StudySession(models.Model):
    study_group = models.ForeignKey(StudyGroup, on_delete=models.CASCADE, related_name='sessions')
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    scheduled_time = models.DateTimeField()
    duration_minutes = models.IntegerField(default=60)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    meet_link = models.CharField(max_length=200, blank=True, null=True)
    def __str__(self): return self.title
    class Meta: ordering = ['scheduled_time']


class OTPVerification(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='otp_verifications')
    otp_code = models.CharField(max_length=6)
    created_at = models.DateTimeField(auto_now_add=True)
    is_verified = models.BooleanField(default=False)
    purpose = models.CharField(max_length=50, default='registration')

    def is_expired(self):
        from datetime import timedelta
        return timezone.now() > self.created_at + timedelta(minutes=10)

    @staticmethod
    def generate_otp():
        return ''.join(random.choices(string.digits, k=6))

    def __str__(self): return f"OTP for {self.user.username} [{self.purpose}]"
    class Meta: ordering = ['-created_at']


class UserProfile(models.Model):
    user           = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    phone_number   = models.CharField(max_length=20, blank=True, null=True)
    email_verified = models.BooleanField(default=False)
    phone_verified = models.BooleanField(default=False)
    avatar         = models.ImageField(upload_to='avatars/', blank=True, null=True)
    bio            = models.TextField(blank=True)
    is_banned      = models.BooleanField(default=False)
    ban_reason     = models.TextField(blank=True)
    created_at     = models.DateTimeField(auto_now_add=True)
    # NEW extended fields
    location        = models.CharField(max_length=100, blank=True)
    website         = models.URLField(blank=True)
    date_of_birth   = models.DateField(blank=True, null=True)
    study_interests = models.CharField(max_length=300, blank=True,
                                        help_text='Comma-separated, e.g. Math, AI, Chemistry')

    def get_initials(self):
        u = self.user
        if u.first_name and u.last_name:
            return f"{u.first_name[0]}{u.last_name[0]}".upper()
        return u.username[:2].upper()

    def interests_list(self):
        return [i.strip() for i in self.study_interests.split(',') if i.strip()]

    def __str__(self): return f"Profile of {self.user.username}"


class VideoMeetRoom(models.Model):
    """Video meeting — ONLY group admin (created_by) can create. All members join."""
    study_group = models.ForeignKey(StudyGroup, on_delete=models.CASCADE, related_name='meet_rooms')
    room_id     = models.CharField(max_length=100, unique=True)
    created_by  = models.ForeignKey(User, on_delete=models.CASCADE)
    created_at  = models.DateTimeField(auto_now_add=True)
    is_active   = models.BooleanField(default=True)
    title       = models.CharField(max_length=200, default='Study Session')
    def __str__(self): return f"Room {self.room_id} – {self.study_group.name}"
    class Meta: ordering = ['-created_at']


class Announcement(models.Model):
    title      = models.CharField(max_length=200)
    content    = models.TextField()
    created_by = models.ForeignKey(User, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    is_active  = models.BooleanField(default=True)
    priority   = models.CharField(max_length=20,
                                   choices=[('info','Info'),('warning','Warning'),('danger','Danger')],
                                   default='info')
    def __str__(self): return self.title
    class Meta: ordering = ['-created_at']