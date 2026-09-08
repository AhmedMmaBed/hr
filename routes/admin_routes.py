from flask import Blueprint, render_template, request, jsonify, session
from flask_babel import _
from werkzeug.security import generate_password_hash
from utils.db import get_db_connection
from utils.auth import login_required
from utils.rbac import require_permission

admin_bp = Blueprint('admin', __name__)

@admin_bp.route('/system/restart', methods=['POST'])
@login_required
@require_permission('admin.settings')
def system_restart():
    """إعادة تشغيل التطبيق: يطلق نسخة جديدة ثم ينهي الحالية"""
    from utils.app_control import restart_application
    restart_application(delay=1.2)
    return jsonify({'success': True})

@admin_bp.route('/admin/cleanup', methods=['GET', 'POST'])
@login_required
@require_permission('admin.settings')
def admin_cleanup():
    # Only allow if user logic permits, or just rely on the hardcoded password as requested
    
    if request.method == 'POST':
        data = request.get_json()
        password = data.get('password')
        actions = data.get('actions', [])
        
        if password != '11225588':
            return jsonify({'success': False, 'message': _('error.incorrect_password')}), 403
            
        conn = get_db_connection()
        try:
            msgs = []
            if 'clear_attendance' in actions:
                conn.execute('DELETE FROM attendance_records')
                # Reset last_sync_time so sync fetches all logs again
                conn.execute('UPDATE fingerprint_devices SET last_sync_time = NULL')
                msgs.append(_('msg.attendance_cleared'))
                
            if 'clear_logs' in actions:
                conn.execute('DELETE FROM sync_logs')
                msgs.append(_('msg.logs_cleared'))

            if 'clear_fingerprint_users' in actions:
                conn.execute('DELETE FROM fingerprint_users')
                msgs.append(_('msg.fingerprint_users_cleared'))

            if 'clear_employees' in actions:
                conn.execute('DELETE FROM employees')
                msgs.append(_('msg.employees_cleared'))
                
            conn.commit()
            return jsonify({'success': True, 'message': ' '.join(msgs)})
            
        except Exception as e:
            return jsonify({'success': False, 'message': str(e)}), 500
        finally:
            pass # conn.close() removed to prevent leak in Flask g
            
    return render_template('admin_cleanup.html')

# --- Role Management Routes ---

@admin_bp.route('/admin/roles')
@login_required
@require_permission('page.roles')
def roles_list():
    conn = get_db_connection()
    roles = conn.execute('SELECT * FROM roles ORDER BY id').fetchall()
    
    # Get counts
    roles_data = []
    for role in roles:
        user_count = conn.execute('SELECT COUNT(*) FROM user_roles WHERE role_id = ?', (role['id'],)).fetchone()[0]
        perm_count = conn.execute('SELECT COUNT(*) FROM role_permissions WHERE role_id = ?', (role['id'],)).fetchone()[0]
        roles_data.append({
            'id': role['id'],
            'name': role['name'],
            'description': role['description'],
            'user_count': user_count,
            'perm_count': perm_count
        })
        
    pass # conn.close() removed to prevent leak in Flask g
    return render_template('admin_roles.html', roles=roles_data)

@admin_bp.route('/admin/roles/create', methods=['POST'])
@login_required
@require_permission('admin.roles')
def create_role():
    try:
        name = request.form['name']
        description = request.form.get('description', '')
        
        conn = get_db_connection()
        conn.execute('INSERT INTO roles (name, description) VALUES (?, ?)', (name, description))
        conn.commit()
        pass # conn.close() removed to prevent leak in Flask g
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@admin_bp.route('/admin/roles/delete/<int:role_id>', methods=['POST'])
@login_required
@require_permission('admin.roles')
def delete_role(role_id):
    try:
        conn = get_db_connection()
        # Prevent deleting Super Admin or self role?
        # Check if role has users? or just cascade delete (handled by DB FK)
        conn.execute('DELETE FROM roles WHERE id = ?', (role_id,))
        conn.commit()
        pass # conn.close() removed to prevent leak in Flask g
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@admin_bp.route('/admin/roles/<int:role_id>/permissions', methods=['GET', 'POST'])
@login_required
@require_permission('admin.roles')
def role_permissions(role_id):
    conn = get_db_connection()
    
    if request.method == 'POST':
        try:
            perms = request.get_json().get('permissions', [])
            
            # Wipe and Insert
            conn.execute('DELETE FROM role_permissions WHERE role_id = ?', (role_id,))
            for p_id in perms:
                conn.execute('INSERT INTO role_permissions (role_id, permission_id) VALUES (?, ?)', (role_id, p_id))
                
            conn.commit()
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'success': False, 'message': str(e)}), 500
        finally:
            pass # conn.close() removed to prevent leak in Flask g
    
    # GET
    role = conn.execute('SELECT * FROM roles WHERE id = ?', (role_id,)).fetchone()
    permissions = conn.execute('SELECT * FROM permissions ORDER BY group_name, name').fetchall()
    
    # Group permissions
    grouped_perms = {}
    for p in permissions:
        g = p['group_name'] or 'Other'
        if g not in grouped_perms:
            grouped_perms[g] = []
        grouped_perms[g].append(p)
        
    # Get current role permissions
    current_perms = conn.execute('SELECT permission_id FROM role_permissions WHERE role_id = ?', (role_id,)).fetchall()
    current_ids = {row[0] for row in current_perms}
    
    pass # conn.close() removed to prevent leak in Flask g
    
    return render_template('admin_role_permissions.html', 
                         role=role, 
                         grouped_perms=grouped_perms, 
                         current_ids=current_ids)

# --- User Management Routes ---

@admin_bp.route('/admin/users')
@login_required
@require_permission('page.users')
def users_list():
    conn = get_db_connection()
    fetched_users = conn.execute('''
        SELECT u.*, 
               GROUP_CONCAT(r.name, ', ') as roles_list,
               GROUP_CONCAT(r.id, ',') as role_ids,
               d.name as managed_department_name,
               e.name as employee_name
        FROM users u
        LEFT JOIN user_roles ur ON u.id = ur.user_id
        LEFT JOIN roles r ON ur.role_id = r.id
        LEFT JOIN departments_master d ON u.managed_department_id = d.id
        LEFT JOIN employees e ON u.employee_id = e.id
        GROUP BY u.id
        ORDER BY u.id
    ''').fetchall()
    
    # Convert Rows to Dicts for JSON serialization
    users = [dict(row) for row in fetched_users]
    
    # Get available roles for the modal
    roles = conn.execute('SELECT * FROM roles').fetchall()
    
    # Get available departments for the modal
    departments = conn.execute('SELECT * FROM departments_master ORDER BY name').fetchall()
    
    # Get available employees for the modal
    employees = conn.execute('SELECT id, name FROM employees WHERE is_active=1 ORDER BY name').fetchall()
    
    pass # conn.close() removed to prevent leak in Flask g
    
    return render_template('admin_users.html', users=users, roles=roles, departments=departments, employees=employees)

@admin_bp.route('/admin/users/save', methods=['POST'])
@login_required
@require_permission('admin.users')
def save_user():
    try:
        user_id = request.form.get('user_id')
        current_user_id = session.get('user_id')
        
        # IDOR Protection: Prevent changing another user's profile without admin.users
        from utils.rbac import RBACService
        if str(user_id) != str(current_user_id):
            if not RBACService.has_permission(current_user_id, 'admin.users'):
                return jsonify({'success': False, 'message': 'لا تملك صلاحية تعديل حسابات الآخرين (IDOR Protected)'}), 403
                
        username = request.form['username']
        password = request.form.get('password')
        full_name = request.form['full_name']
        role = request.form.get('role', 'admin')
        managed_department_id = request.form.get('managed_department_id') or None
        employee_id = request.form.get('employee_id') or None
        role_ids = request.form.getlist('roles') # List of role IDs
        
        # Validate department manager
        if role == 'department_manager' and not managed_department_id:
            return jsonify({'success': False, 'message': 'يجب اختيار القسم لمدير القسم'}), 400
            
        if role == 'admin':
            managed_department_id = None
        
        conn = get_db_connection()
        
        if user_id: # Update
            if password: # Only update password if provided
                hashed_pw = generate_password_hash(password)
                conn.execute('UPDATE users SET username=?, password=?, full_name=?, role=?, managed_department_id=?, employee_id=? WHERE id=?', 
                           (username, hashed_pw, full_name, role, managed_department_id, employee_id, user_id))
            else:
                conn.execute('UPDATE users SET username=?, full_name=?, role=?, managed_department_id=?, employee_id=? WHERE id=?', 
                           (username, full_name, role, managed_department_id, employee_id, user_id))
        else: # Create
            if not password:
                return jsonify({'success': False, 'message': _('error.password_required_for_new_user')}), 400
            hashed_pw = generate_password_hash(password)
            cursor = conn.execute('INSERT INTO users (username, password, full_name, role, managed_department_id, employee_id) VALUES (?, ?, ?, ?, ?, ?)', 
                                (username, hashed_pw, full_name, role, managed_department_id, employee_id))
            user_id = cursor.lastrowid
            
        # Update Roles (Wipe and Insert)
        conn.execute('DELETE FROM user_roles WHERE user_id = ?', (user_id,))
        for r_id in role_ids:
            conn.execute('INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)', (user_id, r_id))
            
        conn.commit()
        pass # conn.close() removed to prevent leak in Flask g
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@admin_bp.route('/admin/users/delete/<int:user_id>', methods=['POST'])
@login_required
@require_permission('admin.users')
def delete_user(user_id):
    try:
        conn = get_db_connection()
        # Prevent deleting self?
        if session.get('user_id') == user_id:
             return jsonify({'success': False, 'message': _('error.cannot_delete_self_account')}), 400
             
        conn.execute('DELETE FROM users WHERE id = ?', (user_id,))
        conn.commit()
        pass # conn.close() removed to prevent leak in Flask g
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500
