from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user
from ..forms import LoginForm, ProfileForm
from ..models import User
from ..utils.security import verify_password, hash_password
from .. import db

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard.index'))
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data.lower()).first()
        if user and verify_password(user.password_hash, form.password.data):
            login_user(user, remember=form.remember.data)
            return redirect(url_for('dashboard.index'))
        flash('Invalid credentials', 'danger')
    return render_template('auth/login.html', form=form)


@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('auth.login'))


@auth_bp.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    form = ProfileForm(obj=current_user)
    if form.validate_on_submit():
        # Email uniqueness check
        email_lower = form.email.data.lower()
        existing = User.query.filter(User.email == email_lower, User.id != current_user.id).first()
        if existing:
            flash('Email is already in use by another account.', 'danger')
            return render_template('auth/profile.html', form=form)

        # Update name, email, theme, and default tickets view
        current_user.name = form.name.data
        current_user.email = email_lower
        current_user.theme = form.theme.data
        current_user.tickets_view_pref = form.tickets_view_pref.data

        # Handle password change if provided
        if form.new_password.data:
            if not form.current_password.data or not verify_password(current_user.password_hash, form.current_password.data):
                flash('Current password is incorrect.', 'danger')
                return render_template('auth/profile.html', form=form)
            current_user.password_hash = hash_password(form.new_password.data)

        db.session.commit()
        flash('Profile updated.', 'success')
        return redirect(url_for('auth.profile'))
    # Ensure the form shows current selections on GET
    if request.method == 'GET':
        form.theme.data = getattr(current_user, 'theme', 'light')
        form.tickets_view_pref.data = getattr(current_user, 'tickets_view_pref', 'any')
    return render_template('auth/profile.html', form=form)
