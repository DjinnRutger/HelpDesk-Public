from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField, BooleanField, TextAreaField, SelectField, IntegerField, HiddenField
from wtforms.validators import DataRequired, Email, Length, Optional, NumberRange, EqualTo

class LoginForm(FlaskForm):
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired()])
    remember = BooleanField('Remember me')
    submit = SubmitField('Sign in')

class MSGraphForm(FlaskForm):
    client_id = StringField('Client ID', validators=[DataRequired(), Length(max=200)])
    client_secret = StringField('Client Secret', validators=[DataRequired(), Length(max=200)])
    tenant_id = StringField('Tenant ID', validators=[DataRequired(), Length(max=100)])
    user_email = StringField('Mailbox Email', validators=[DataRequired(), Email()])
    poll_interval = IntegerField('Poll Interval (seconds)', validators=[DataRequired(), NumberRange(min=10, max=86400)])
    submit = SubmitField('Save Settings')

class ClientApiForm(FlaskForm):
    enabled = BooleanField('Enable client intake')
    auth_scheme = SelectField('Auth scheme', choices=[
        ('Bearer', 'Bearer token (recommended)'),
        ('ApiKeyHeader', 'API key header'),
        ('None', 'None (lab/testing only)'),
    ], validators=[DataRequired()])
    header_name = StringField('Header name', validators=[Optional(), Length(max=100)])
    max_upload_mb = IntegerField('Max upload (MB)', validators=[DataRequired(), NumberRange(min=1, max=100)])
    require_https = BooleanField('Require HTTPS')
    default_priority = SelectField('Default priority', choices=[
        ('low', 'Low'), ('medium', 'Medium'), ('high', 'High'),
    ], default='medium')
    default_assignee_id = SelectField('Default assignee', coerce=int, validators=[Optional()])
    base_url = StringField('API base URL override', validators=[Optional(), Length(max=500)])
    submit = SubmitField('Save Settings')

    def __init__(self, *args, **kwargs):
        super(ClientApiForm, self).__init__(*args, **kwargs)
        from .models import User
        techs = User.query.filter_by(is_active=True).order_by(User.name.asc()).all()
        self.default_assignee_id.choices = [(0, '— Unassigned —')] + [(u.id, u.name) for u in techs]


class TechForm(FlaskForm):
    name = StringField('Name', validators=[DataRequired(), Length(max=120)])
    email = StringField('Email', validators=[DataRequired(), Email(), Length(max=255)])
    password = PasswordField('Password', validators=[Optional()])
    role = SelectField('Role', choices=[('tech','Tech'), ('admin','Admin')], validators=[DataRequired()])
    is_active = BooleanField('Active')
    submit = SubmitField('Save')

class TicketForm(FlaskForm):
    subject = StringField('Subject', validators=[DataRequired(), Length(max=300)])
    requester = StringField('Requester', validators=[Optional(), Email()])
    body = TextAreaField('Body', validators=[Optional()])
    assignee_id = SelectField('Assignee', coerce=int, validators=[Optional()])
    co_assignee_id = SelectField('Co-Tech', coerce=int, validators=[Optional()])
    priority = SelectField('Priority', choices=[('low','Low'), ('medium','Medium'), ('high','High')], default='medium')
    source = SelectField('Source', choices=[('email','Email'), ('zoom','Zoom'), ('walk_in','Walk In'), ('phone','Phone')], default='email')
    asset_id = SelectField('Asset', coerce=int, validators=[Optional()], choices=[])
    submit = SubmitField('Save')

    def __init__(self, *args, **kwargs):
        super(TicketForm, self).__init__(*args, **kwargs)
        from .models import User
        techs = User.query.filter_by(is_active=True).order_by(User.name.asc()).all()
        self.assignee_id.choices = [(0, '— Unassigned —')] + [(u.id, u.name) for u in techs]
        self.co_assignee_id.choices = [(0, '— None —')] + [(u.id, u.name) for u in techs]

class NoteForm(FlaskForm):
    content = TextAreaField('Add note', validators=[DataRequired(), Length(min=1)])
    private = BooleanField('Private', default=True)
    submit = SubmitField('Add note')

class TicketUpdateForm(FlaskForm):
    status = SelectField('Status', choices=[], validators=[DataRequired()])  # Populated dynamically
    priority = SelectField('Priority', choices=[('low','Low'), ('medium','Medium'), ('high','High')], validators=[DataRequired()])
    assignee_id = SelectField('Assignee', coerce=int, validators=[Optional()])
    co_assignee_id = SelectField('Co-Tech', coerce=int, validators=[Optional()])
    source = SelectField('Source', choices=[('email','Email'), ('zoom','Zoom'), ('walk_in','Walk In'), ('phone','Phone')])
    submit_update = SubmitField('Update')
    
    def __init__(self, *args, **kwargs):
        super(TicketUpdateForm, self).__init__(*args, **kwargs)
        # Load status choices from database
        from .models import TicketStatus
        self.status.choices = TicketStatus.get_choices()


class ProcessTemplateForm(FlaskForm):
    name = StringField('Name', validators=[DataRequired(), Length(max=200)])
    submit = SubmitField('Save')


class ProcessTemplateItemForm(FlaskForm):
    type = SelectField('Type', choices=[('checkbox', 'Checkbox'), ('text', 'Text')], validators=[DataRequired()])
    label = StringField('Label', validators=[DataRequired(), Length(max=300)])
    assigned_tech_id = SelectField('Assigned Tech', coerce=int, validators=[Optional()])
    position = IntegerField('Position', validators=[Optional(), NumberRange(min=0)])
    submit = SubmitField('Add Item')


class ProcessAssignForm(FlaskForm):
    template_id = SelectField('Process Template', coerce=int, validators=[DataRequired()])
    submit_assign = SubmitField('Assign')


class TaskAssignForm(FlaskForm):
    list_name = StringField('Task List Name', validators=[Optional(), Length(max=200)])
    tasks_text = TextAreaField('Tasks (one per line)', validators=[DataRequired(), Length(min=1)])
    assigned_tech_id = SelectField('Assign to', coerce=int, validators=[Optional()])
    submit_tasks = SubmitField('Create Tasks')


class AllowedDomainForm(FlaskForm):
    domain = StringField('Domain', validators=[DataRequired(), Length(max=255)])
    submit = SubmitField('Add Domain')


class DenyFilterForm(FlaskForm):
    phrase = StringField('Subject contains', validators=[DataRequired(), Length(max=255)])
    submit = SubmitField('Add Phrase')


class ProfileForm(FlaskForm):
    name = StringField('Name', validators=[DataRequired(), Length(max=120)])
    email = StringField('Email', validators=[DataRequired(), Email(), Length(max=255)])
    current_password = PasswordField('Current Password', validators=[Optional()])
    new_password = PasswordField('New Password', validators=[Optional(), Length(min=6)])
    confirm_password = PasswordField('Confirm New Password', validators=[Optional(), EqualTo('new_password', message='Passwords must match')])
    theme = SelectField('Theme', choices=[('light','Light Mode'), ('dark','Dark Mode'), ('ocean','Ocean'), ('fallout','Fallout Terminal')], validators=[DataRequired()])
    tickets_view_pref = SelectField('Default Tickets View', choices=[('any','Any'), ('me','Assigned to me'), ('me_or_unassigned','Unassigned and Assigned to me')], validators=[DataRequired()])
    signature = StringField('Email Signature', validators=[Optional(), Length(max=500)])
    submit = SubmitField('Save Changes')


# --- Orders / Order Items ---
class OrderItemForm(FlaskForm):
    description = StringField('Description', validators=[DataRequired(), Length(max=500)])
    quantity = IntegerField('Qty', validators=[Optional(), NumberRange(min=1)], default=1)
    target_vendor = StringField('Vendor', validators=[Optional(), Length(max=255)])
    source_url = StringField('Source URL', validators=[Optional(), Length(max=1000)])
    est_unit_cost = StringField('Est Unit Cost', validators=[Optional(), Length(max=50)])
    needed_by = StringField('Needed By (YYYY-MM-DD)', validators=[Optional(), Length(max=20)])
    ticket_id = HiddenField('Ticket ID')
    submit_item = SubmitField('Add Item')

class POFInalizeForm(FlaskForm):  # finalize existing draft PO
    submit_finalize = SubmitField('Finalize & Send')

# --- Projects ---
class MergeToProjectForm(FlaskForm):
    project_id = SelectField('Select Project', coerce=int, validators=[DataRequired()])
    submit_merge = SubmitField('Merge')

