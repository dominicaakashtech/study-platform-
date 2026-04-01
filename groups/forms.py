from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from .models import StudyGroup, Resource, Message, StudySession, Announcement, UserProfile, DirectMessage


class RegisterForm(UserCreationForm):
    email = forms.EmailField(required=True,
        widget=forms.EmailInput(attrs={'class':'form-control','placeholder':'Enter your email'}))
    first_name = forms.CharField(max_length=30, required=False,
        widget=forms.TextInput(attrs={'class':'form-control','placeholder':'First name'}))
    last_name = forms.CharField(max_length=30, required=False,
        widget=forms.TextInput(attrs={'class':'form-control','placeholder':'Last name'}))
    phone_number = forms.CharField(max_length=20, required=False,
        widget=forms.TextInput(attrs={'class':'form-control','placeholder':'+91 98765 43210'}))

    class Meta:
        model = User
        fields = ['username','email','first_name','last_name','password1','password2']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].widget.attrs.update({'class':'form-control','placeholder':'Choose a username'})
        self.fields['password1'].widget.attrs.update({'class':'form-control','placeholder':'Create a password'})
        self.fields['password2'].widget.attrs.update({'class':'form-control','placeholder':'Confirm your password'})

    def clean_email(self):
        email = self.cleaned_data.get('email')
        if email and User.objects.filter(email=email).exists():
            raise forms.ValidationError('An account with this email already exists.')
        return email


class OTPVerifyForm(forms.Form):
    otp_code = forms.CharField(max_length=6, min_length=6,
        widget=forms.TextInput(attrs={
            'class':'form-control form-control-lg text-center fw-bold',
            'placeholder':'000000','maxlength':'6',
            'autocomplete':'one-time-code','inputmode':'numeric'}))


class UserUpdateForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['first_name','last_name','email']
        widgets = {
            'first_name': forms.TextInput(attrs={'class':'form-control','placeholder':'First name'}),
            'last_name':  forms.TextInput(attrs={'class':'form-control','placeholder':'Last name'}),
            'email':      forms.EmailInput(attrs={'class':'form-control','placeholder':'Email address'}),
        }


class UserProfileForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = ['avatar','bio','phone_number','location','website','date_of_birth','study_interests']
        widgets = {
            'avatar':          forms.ClearableFileInput(attrs={'class':'form-control','accept':'image/*'}),
            'bio':             forms.Textarea(attrs={'class':'form-control','rows':3,
                                                     'placeholder':'Tell others about yourself…'}),
            'phone_number':    forms.TextInput(attrs={'class':'form-control','placeholder':'+91 98765 43210'}),
            'location':        forms.TextInput(attrs={'class':'form-control','placeholder':'City, Country'}),
            'website':         forms.URLInput(attrs={'class':'form-control','placeholder':'https://yoursite.com'}),
            'date_of_birth':   forms.DateInput(attrs={'class':'form-control','type':'date'}),
            'study_interests': forms.TextInput(attrs={'class':'form-control',
                                                      'placeholder':'Math, Physics, AI, Chemistry'}),
        }


class StudyGroupForm(forms.ModelForm):
    # Shown in plain-text so creator can see what they're setting
    join_password = forms.CharField(
        max_length=128, required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Leave blank for no password',
            'autocomplete': 'new-password',
        }),
        help_text='Members will need this password to join. Leave blank for open access.'
    )

    class Meta:
        model = StudyGroup
        fields = ['name','subject','description','max_members','is_private','join_password','cover_image']
        widgets = {
            'name':        forms.TextInput(attrs={'class':'form-control','placeholder':'Enter group name'}),
            'subject':     forms.TextInput(attrs={'class':'form-control','placeholder':'e.g., Mathematics, Physics'}),
            'description': forms.Textarea(attrs={'class':'form-control','rows':4,
                                                 'placeholder':'Describe your study group'}),
            'max_members': forms.NumberInput(attrs={'class':'form-control','min':1,'placeholder':'Leave blank / 0 for unlimited'}),
            'is_private':  forms.CheckboxInput(attrs={'class':'form-check-input'}),
            'cover_image': forms.ClearableFileInput(attrs={'class':'form-control','accept':'image/*'}),
        }


class GroupJoinPasswordForm(forms.Form):
    """Presented to users trying to join a password-protected group."""
    password = forms.CharField(
        max_length=128,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control form-control-lg',
            'placeholder': 'Enter group password',
            'autofocus': True,
            'autocomplete': 'current-password',
        })
    )


class ResourceForm(forms.ModelForm):
    class Meta:
        model = Resource
        fields = ['title','resource_type','description','file','link']
        widgets = {
            'title':         forms.TextInput(attrs={'class':'form-control'}),
            'resource_type': forms.Select(attrs={'class':'form-control'}),
            'description':   forms.Textarea(attrs={'class':'form-control','rows':3}),
            'file':          forms.FileInput(attrs={'class':'form-control'}),
            'link':          forms.URLInput(attrs={'class':'form-control'}),
        }


class MessageForm(forms.ModelForm):
    class Meta:
        model = Message
        fields = ['content']
        widgets = {'content': forms.Textarea(attrs={'class':'form-control','rows':2,
                                                     'placeholder':'Type your message...'})}


class StudySessionForm(forms.ModelForm):
    class Meta:
        model = StudySession
        fields = ['title','description','scheduled_time','duration_minutes']
        widgets = {
            'title':            forms.TextInput(attrs={'class':'form-control'}),
            'description':      forms.Textarea(attrs={'class':'form-control','rows':3}),
            'scheduled_time':   forms.DateTimeInput(attrs={'class':'form-control','type':'datetime-local'}),
            'duration_minutes': forms.NumberInput(attrs={'class':'form-control','min':15}),
        }


class AnnouncementForm(forms.ModelForm):
    class Meta:
        model = Announcement
        fields = ['title','content','priority','is_active']
        widgets = {
            'title':     forms.TextInput(attrs={'class':'form-control'}),
            'content':   forms.Textarea(attrs={'class':'form-control','rows':4}),
            'priority':  forms.Select(attrs={'class':'form-control'}),
            'is_active': forms.CheckboxInput(attrs={'class':'form-check-input'}),
        }