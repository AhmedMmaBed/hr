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
            session['employee_id'] = user['employee_id']
            
            if remember:
                session.permanent = True
            
            flash(gettext('x.f_welcome_login') % {'p0': f'{user["full_name"]}'}, 'success')
            
            # If regular employee, redirect straight to Employee Self-Service Portal
            if user['role'] == 'employee':
                return redirect(url_for('portal.dashboard'))
                
            return redirect(url_for('main.index'))
        else:
            flash(gettext('x.f_bad_credentials'), 'error')
    
    return render_template('login.html')

@auth_bp.route('/logout')
def logout():
    session.clear()
    flash(gettext('x.f_logged_out'), 'success')
    return redirect(url_for('auth.login'))

@auth_bp.route('/reset-password', methods=['GET', 'POST'])
def reset_password():
    from datetime import datetime
    token = request.args.get('token') or request.form.get('token')
    if not token:
        return render_template('reset_password.html', error='الرابط غير صالح أو ناقص.')
    
    conn = get_db_connection()
    now_iso = datetime.now().isoformat()
    
    # Check token
    reset_row = conn.execute('''
        SELECT pr.*, u.username, u.full_name, u.role, u.employee_id
        FROM password_reset_tokens pr
        JOIN users u ON pr.user_id = u.id
        WHERE pr.token = ? AND pr.is_used = 0 AND pr.expires_at > ?
    ''', (token, now_iso)).fetchone()
    
    if not reset_row:
        return render_template('reset_password.html', error='رابط تعيين كلمة المرور منتهي الصلاحية أو تم استخدامه مسبقاً. يرجى طلب رابط جديد من المسؤول.')
    
    if request.method == 'POST':
        new_pw = (request.form.get('password') or '').strip()
        confirm_pw = (request.form.get('confirm_password') or '').strip()
        
        if len(new_pw) < 4:
            return render_template('reset_password.html', token=token, user=reset_row, error='كلمة المرور يجب ألا تقل عن 4 خانات.')
        if new_pw != confirm_pw:
            return render_template('reset_password.html', token=token, user=reset_row, error='كلمتا المرور غير متطابقتين.')
            
        hashed = generate_password_hash(new_pw)
        conn.execute('UPDATE users SET password = ?, is_active = 1 WHERE id = ?', (hashed, reset_row['user_id']))
        conn.execute('UPDATE password_reset_tokens SET is_used = 1 WHERE token = ?', (token,))
        conn.commit()
        
        # Auto-login after password set
        session['user_id'] = reset_row['user_id']
        session['username'] = reset_row['username']
        session['full_name'] = reset_row['full_name']
        session['role'] = reset_row['role']
        session['employee_id'] = reset_row['employee_id']
        
        flash('تم تعيين كلمة المرور بنجاح مرحباً بك!', 'success')
        if reset_row['role'] == 'employee':
            return redirect(url_for('portal.dashboard'))
        return redirect(url_for('main.index'))
        
    return render_template('reset_password.html', token=token, user=reset_row)
