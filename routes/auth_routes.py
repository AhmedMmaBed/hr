from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from flask_babel import gettext
from werkzeug.security import check_password_hash, generate_password_hash
from utils.db import get_db_connection

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = (request.form.get('password') or '').strip()
        remember = request.form.get('remember')
        
        conn = get_db_connection()
        user = conn.execute('SELECT * FROM users WHERE LOWER(username) = LOWER(?) AND is_active = 1', 
                          (username,)).fetchone()
        
        # Verify password (supports hash with fallback for plain text + auto upgrade)
        is_authenticated = False
        if user and user['password']:
            try:
                if check_password_hash(user['password'], password):
                    is_authenticated = True
            except Exception:
                pass
            
            # Fallback if stored as plain text
            if not is_authenticated and user['password'] == password:
                is_authenticated = True
                try:
                    # Auto upgrade plain text to secure hash in DB
                    conn.execute('UPDATE users SET password = ? WHERE id = ?', 
                                 (generate_password_hash(password), user['id']))
                    conn.commit()
                except Exception:
                    pass
        
        if is_authenticated:
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['full_name'] = user['full_name']
            session['role'] = user['role']
            
            if remember:
                session.permanent = True
            
            flash(gettext('x.f_welcome_login') % {'p0': f'{user["full_name"]}'}, 'success')
            return redirect(url_for('main.index'))
        else:
            flash(gettext('x.f_bad_credentials'), 'error')
    
    return render_template('login.html')

@auth_bp.route('/logout')
def logout():
    session.clear()
    flash(gettext('x.f_logged_out'), 'success')
    return redirect(url_for('auth.login'))
