from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for, flash
from flask_babel import gettext as _
from datetime import datetime, date, timedelta
import os

from utils.db import get_db_connection
from utils.auth import login_required, get_current_user
from utils.leave_utils import calculate_leave_balance, save_leave_balance, calculate_actual_leave_days

portal_bp = Blueprint('portal', __name__, url_prefix='/portal')

def get_portal_employee_id():
    """Helper to resolve the current logged-in employee ID."""
    emp_id = session.get('employee_id')
    if emp_id:
        return emp_id
    user_id = session.get('user_id')
    if not user_id:
        return None
    conn = get_db_connection()
    row = conn.execute("SELECT employee_id, role FROM users WHERE id = ?", (user_id,)).fetchone()
    if row and row['employee_id']:
        session['employee_id'] = row['employee_id']
        return row['employee_id']
    
    # Fallback for admin previewing portal: pick first active employee
    if row and row['role'] == 'admin':
        first_emp = conn.execute("SELECT id FROM employees WHERE is_active = 1 LIMIT 1").fetchone()
        if first_emp:
            return first_emp['id']
    return None

@portal_bp.route('/')
@portal_bp.route('/dashboard')
@login_required
def dashboard():
    emp_id = get_portal_employee_id()
    conn = get_db_connection()
    
    employee = None
    if emp_id:
        employee = conn.execute('''
            SELECT e.*, d.name as department_name, p.name as position_name,
                   m.name as manager_name
            FROM employees e
            LEFT JOIN departments_master d ON e.department_id = d.id
            LEFT JOIN positions_master p ON e.position_id = p.id
            LEFT JOIN employees m ON e.manager_id = m.id
            WHERE e.id = ?
        ''', (emp_id,)).fetchone()
    
    # Check if this employee is a direct manager to others
    is_manager = False
    subordinates_count = 0
    if emp_id:
        cnt_row = conn.execute("SELECT COUNT(*) FROM employees WHERE manager_id = ? AND is_active = 1", (emp_id,)).fetchone()
        subordinates_count = cnt_row[0] if cnt_row else 0
        is_manager = (subordinates_count > 0)
    
    # If global admin without employee, also grant manager view for testing
    user = get_current_user()
    if user and user['role'] == 'admin':
        is_manager = True
    
    leave_types = conn.execute("SELECT id, name, is_hourly_permission FROM leave_types ORDER BY id ASC").fetchall()
    
    return render_template(
        'portal/index.html',
        employee=employee,
        is_manager=is_manager,
        subordinates_count=subordinates_count,
        leave_types=leave_types,
        today_date=date.today().strftime('%Y-%m-%d'),
        today_display=date.today().strftime('%A, %d %B %Y')
    )

@portal_bp.route('/api/my-data')
@login_required
def api_my_data():
    emp_id = get_portal_employee_id()
    if not emp_id:
        return jsonify({'success': False, 'message': 'لا يوجد حساب موظف مرتبط'}), 404
    
    conn = get_db_connection()
    today_str = date.today().strftime('%Y-%m-%d')
    now = datetime.now()
    cur_month = now.month
    cur_year = now.year
    
    # 1. Employee Info
    emp_row = conn.execute('''
        SELECT e.id, e.name, e.arabic_name, e.employee_number, e.department, e.position,
               e.hire_date, e.salary, e.phone, e.civil_id
        FROM employees e WHERE e.id = ?
    ''', (emp_id,)).fetchone()
    if not emp_row:
        return jsonify({'success': False, 'message': 'الموظف غير موجود'}), 404
    emp = dict(emp_row)
    
    # 2. Today's Punches
    punches = conn.execute('''
        SELECT check_time FROM attendance_records
        WHERE employee_id = ? AND DATE(check_time) = ?
        ORDER BY check_time ASC
    ''', (emp_id, today_str)).fetchall()
    
    punch_times = [p['check_time'][11:16] for p in punches] # 'HH:MM'
    check_in = punch_times[0] if punch_times else None
    check_out = punch_times[-1] if len(punch_times) > 1 else None
    is_present = bool(punches)
    
    # 3. Current Month Stats
    month_days_worked = conn.execute('''
        SELECT COUNT(DISTINCT DATE(check_time)) FROM attendance_records
        WHERE employee_id = ? AND strftime('%m', check_time) = ? AND strftime('%Y', check_time) = ?
    ''', (emp_id, f'{cur_month:02d}', str(cur_year))).fetchone()[0]
    
    # 4. Leave Balance
    try:
        bal = calculate_leave_balance(conn, emp_id, cur_month, cur_year)
        annual_balance = bal.get('closing_balance', 0) if bal else 30
        annual_accrued = bal.get('accrued_days', 0) if bal else 30
        annual_taken = bal.get('consumed_days', 0) if bal else 0
    except Exception:
        annual_balance = 30
        annual_accrued = 30
        annual_taken = 0
    
    # 5. Recent Leave Requests (Last 5)
    recent_leaves = conn.execute('''
        SELECT lr.id, lr.start_date, lr.end_date, lr.days_count, lr.status, lr.reason,
               lt.name as leave_type_name
        FROM leave_requests lr
        LEFT JOIN leave_types lt ON lr.leave_type_id = lt.id
        WHERE lr.employee_id = ?
        ORDER BY lr.created_at DESC LIMIT 5
    ''', (emp_id,)).fetchall()
    
    # 6. Active Loans
    loans = conn.execute('''
        SELECT principal, monthly_installment, (principal - COALESCE(paid_amount, 0)) as remaining
        FROM employee_loans
        WHERE employee_id = ? AND (principal - COALESCE(paid_amount, 0)) > 0
    ''', (emp_id,)).fetchall()
    
    total_loan_remaining = sum(float(l['remaining']) for l in loans) if loans else 0
    monthly_installment = sum(float(l['monthly_installment']) for l in loans) if loans else 0
    
    return jsonify({
        'success': True,
        'employee': emp,
        'today': {
            'date': today_str,
            'is_present': is_present,
            'check_in': check_in,
            'check_out': check_out,
            'punches_count': len(punches),
            'punches': punch_times
        },
        'stats': {
            'days_worked': month_days_worked,
            'annual_balance': round(annual_balance, 1),
            'annual_accrued': round(annual_accrued, 1),
            'annual_taken': round(annual_taken, 1),
            'loan_remaining': round(total_loan_remaining, 2),
            'monthly_installment': round(monthly_installment, 2)
        },
        'recent_leaves': [dict(r) for r in recent_leaves]
    })

@portal_bp.route('/api/attendance')
@login_required
def api_attendance():
    emp_id = get_portal_employee_id()
    if not emp_id:
        return jsonify({'success': False, 'message': 'لا يوجد موظف'}), 404
    
    conn = get_db_connection()
    now = datetime.now()
    month = request.args.get('month', type=int) or now.month
    year = request.args.get('year', type=int) or now.year
    
    # Fetch punches for this month
    records = conn.execute('''
        SELECT DATE(check_time) as date, GROUP_CONCAT(TIME(check_time)) as punches
        FROM attendance_records
        WHERE employee_id = ? AND strftime('%m', check_time) = ? AND strftime('%Y', check_time) = ?
        GROUP BY DATE(check_time)
        ORDER BY date DESC
    ''', (emp_id, f'{month:02d}', str(year))).fetchall()
    
    data = []
    for r in records:
        all_p = r['punches'].split(',') if r['punches'] else []
        t_in = all_p[0][:5] if all_p else '-'
        t_out = all_p[-1][:5] if len(all_p) > 1 else '-'
        data.append({
            'date': r['date'],
            'check_in': t_in,
            'check_out': t_out,
            'punches_count': len(all_p),
            'status': 'حاضر' if len(all_p) >= 2 else ('بصمة واحدة' if len(all_p) == 1 else 'غياب')
        })
    
    return jsonify({'success': True, 'month': month, 'year': year, 'records': data})

@portal_bp.route('/api/request-leave', methods=['POST'])
@login_required
def api_request_leave():
    emp_id = get_portal_employee_id()
    if not emp_id:
        return jsonify({'success': False, 'message': 'حساب الموظف غير محدد'}), 400
    
    data = request.get_json(silent=True) or request.form
    leave_type_id = data.get('leave_type_id')
    start_date = data.get('start_date')
    end_date = data.get('end_date')
    reason = data.get('reason', '').strip()
    
    if not all([leave_type_id, start_date, end_date]):
        return jsonify({'success': False, 'message': 'يرجى تعبئة كافة الحقول الإلزامية'}), 400
    
    conn = get_db_connection()
    try:
        days_count = calculate_actual_leave_days(conn, start_date, end_date)
        if days_count <= 0:
            days_count = 1
        
        # Check leave balance
        start_dt = datetime.strptime(start_date, '%Y-%m-%d')
        bal = calculate_leave_balance(conn, emp_id, start_dt.month, start_dt.year)
        available = bal.get('closing_balance', 0) if bal else 0
        is_paid = (available >= days_count)
        
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO leave_requests (
                employee_id, leave_type_id, start_date, end_date,
                days_count, reason, is_paid_leave, status, current_step
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', 1)
        ''', (emp_id, leave_type_id, start_date, end_date, days_count, reason, is_paid))
        req_id = cursor.lastrowid
        
        # Check if employee has a direct manager
        emp_info = conn.execute("SELECT manager_id FROM employees WHERE id = ?", (emp_id,)).fetchone()
        if emp_info and emp_info['manager_id']:
            cursor.execute('''
                INSERT INTO leave_approvals (leave_request_id, step_order, approver_user_id, status)
                VALUES (?, 1, ?, 'pending')
            ''', (req_id, emp_info['manager_id']))
        
        save_leave_balance(conn, emp_id, start_dt.month, start_dt.year)
        conn.commit()
        return jsonify({'success': True, 'message': 'تم تقديم طلب الإجازة بنجاح وهو بانتظار الاعتماد.'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'حدث خطأ: {e}'}), 500

@portal_bp.route('/api/request-excuse', methods=['POST'])
@login_required
def api_request_excuse():
    emp_id = get_portal_employee_id()
    if not emp_id:
        return jsonify({'success': False, 'message': 'حساب الموظف غير محدد'}), 400
    
    data = request.get_json(silent=True) or request.form
    date_str = data.get('date') or date.today().strftime('%Y-%m-%d')
    type_ = data.get('type', 'mission')
    reason = data.get('reason', '').strip()
    
    conn = get_db_connection()
    try:
        conn.execute('''
            INSERT INTO attendance_excuses (employee_id, date, type, reason, created_by)
            VALUES (?, ?, ?, ?, ?)
        ''', (emp_id, date_str, type_, reason, session.get('user_id')))
        conn.commit()
        return jsonify({'success': True, 'message': 'تم تسجيل طلب الاستئذان/المهمة بنجاح.'})
    except Exception as e:
        if 'UNIQUE' in str(e):
            return jsonify({'success': False, 'message': 'يوجد استئذان مسجل بالفعل لهذا التاريخ'}), 400
        return jsonify({'success': False, 'message': f'خطأ: {e}'}), 500

# =========================================================================
# DIRECT MANAGER (MY TEAM) ENDPOINTS
# =========================================================================

@portal_bp.route('/api/team-summary')
@login_required
def api_team_summary():
    emp_id = get_portal_employee_id()
    user = get_current_user()
    is_admin = (user and user['role'] == 'admin')
    
    conn = get_db_connection()
    today_str = date.today().strftime('%Y-%m-%d')
    
    if is_admin and not emp_id:
        # Admin gets full active team
        members = conn.execute('''
            SELECT id, name, arabic_name, employee_number, position, department, phone
            FROM employees WHERE is_active = 1 ORDER BY name ASC LIMIT 30
        ''').fetchall()
    else:
        # Direct subordinates only
        members = conn.execute('''
            SELECT id, name, arabic_name, employee_number, position, department, phone
            FROM employees WHERE manager_id = ? AND is_active = 1 ORDER BY name ASC
        ''', (emp_id,)).fetchall()
    
    team_data = []
    present_count = 0
    
    for m in members:
        mid = m['id']
        punches = conn.execute('''
            SELECT check_time FROM attendance_records
            WHERE employee_id = ? AND DATE(check_time) = ?
            ORDER BY check_time ASC
        ''', (mid, today_str)).fetchall()
        
        is_in = bool(punches)
        if is_in:
            present_count += 1
        
        first_punch = punches[0]['check_time'][11:16] if punches else None
        last_punch = punches[-1]['check_time'][11:16] if len(punches) > 1 else None
        
        team_data.append({
            'id': mid,
            'name': m['name'] or m['arabic_name'],
            'employee_number': m['employee_number'],
            'position': m['position'] or 'موظف',
            'department': m['department'] or 'عام',
            'is_present': is_in,
            'first_punch': first_punch,
            'last_punch': last_punch,
            'punches_count': len(punches)
        })
    
    # Count pending approvals
    if is_admin and not emp_id:
        pending_cnt = conn.execute("SELECT COUNT(*) FROM leave_requests WHERE status = 'pending'").fetchone()[0]
    else:
        pending_cnt = conn.execute('''
            SELECT COUNT(*) FROM leave_requests lr
            JOIN employees e ON lr.employee_id = e.id
            WHERE e.manager_id = ? AND lr.status = 'pending'
        ''', (emp_id,)).fetchone()[0]
    
    return jsonify({
        'success': True,
        'total_team': len(team_data),
        'present_today': present_count,
        'absent_today': len(team_data) - present_count,
        'pending_approvals_count': pending_cnt,
        'team_members': team_data
    })

@portal_bp.route('/api/team-approvals')
@login_required
def api_team_approvals():
    emp_id = get_portal_employee_id()
    user = get_current_user()
    is_admin = (user and user['role'] == 'admin')
    
    conn = get_db_connection()
    
    if is_admin and not emp_id:
        reqs = conn.execute('''
            SELECT lr.id, lr.start_date, lr.end_date, lr.days_count, lr.reason, lr.created_at, lr.status,
                   e.name as employee_name, e.employee_number, lt.name as leave_type_name
            FROM leave_requests lr
            JOIN employees e ON lr.employee_id = e.id
            JOIN leave_types lt ON lr.leave_type_id = lt.id
            WHERE lr.status = 'pending'
            ORDER BY lr.created_at DESC
        ''').fetchall()
    else:
        reqs = conn.execute('''
            SELECT lr.id, lr.start_date, lr.end_date, lr.days_count, lr.reason, lr.created_at, lr.status,
                   e.name as employee_name, e.employee_number, lt.name as leave_type_name
            FROM leave_requests lr
            JOIN employees e ON lr.employee_id = e.id
            JOIN leave_types lt ON lr.leave_type_id = lt.id
            WHERE e.manager_id = ? AND lr.status = 'pending'
            ORDER BY lr.created_at DESC
        ''', (emp_id,)).fetchall()
    
    return jsonify({
        'success': True,
        'requests': [dict(r) for r in reqs]
    })

@portal_bp.route('/api/approve-request', methods=['POST'])
@login_required
def api_approve_request():
    emp_id = get_portal_employee_id()
    user = get_current_user()
    is_admin = (user and user['role'] == 'admin')
    
    data = request.get_json(silent=True) or request.form
    req_id = data.get('request_id')
    action = data.get('action') # 'approve' or 'reject'
    
    if not req_id or action not in ['approve', 'reject']:
        return jsonify({'success': False, 'message': 'بيانات الإجراء غير صحيحة'}), 400
    
    conn = get_db_connection()
    req = conn.execute('''
        SELECT lr.*, e.manager_id
        FROM leave_requests lr
        JOIN employees e ON lr.employee_id = e.id
        WHERE lr.id = ?
    ''', (req_id,)).fetchone()
    
    if not req:
        return jsonify({'success': False, 'message': 'طلب الإجازة غير موجود'}), 404
    
    # Permission check: must be direct manager or admin
    if not is_admin and req['manager_id'] != emp_id:
        return jsonify({'success': False, 'message': 'لا تملك صلاحية اعتماد هذا الطلب'}), 403
    
    new_status = 'approved' if action == 'approve' else 'rejected'
    conn.execute("UPDATE leave_requests SET status = ? WHERE id = ?", (new_status, req_id))
    
    # Update approvals table if exists
    try:
        conn.execute("UPDATE leave_approvals SET status = ? WHERE leave_request_id = ?", (new_status, req_id))
    except Exception:
        pass
    
    conn.commit()
    msg = 'تمت الموافقة على الطلب بنجاح ✅' if action == 'approve' else 'تم رفض الطلب ❌'
    return jsonify({'success': True, 'message': msg, 'new_status': new_status})
