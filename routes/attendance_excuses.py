from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_babel import gettext
from utils.db import get_db_connection
from utils.auth import login_required, get_allowed_department_name
from utils.rbac import require_permission
from datetime import date, datetime

attendance_excuses_bp = Blueprint('attendance_excuses', __name__)

@attendance_excuses_bp.route('/attendance_excuses')
@login_required
@require_permission('page.contract')
def index():
    conn = get_db_connection()
    
    today = date.today()
    
    # Filters
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')
    emp_id_filter = request.args.get('emp_id', '')
    
    # Default Dates (Current Month)
    if not start_date:
        start_date = today.replace(day=1).strftime('%Y-%m-%d')
    if not end_date:
        end_date = today.strftime('%Y-%m-%d')
    
    allowed_dept = get_allowed_department_name()
    
    # --- 1. Potential Invalids Query (Invalid / Single Punch) ---
    query_invalids = '''
        SELECT 
            e.id as employee_id,
            e.name as employee_name,
            e.employee_number,
            e.department,
            DATE(ar.check_time) as date,
            COUNT(*) as punch_count,
            GROUP_CONCAT(TIME(ar.check_time)) as punches_str
        FROM attendance_records ar
        JOIN employees e ON ar.employee_id = e.id
        WHERE DATE(ar.check_time) BETWEEN ? AND ?
    '''
    params_invalids = [start_date, end_date]
    
    if allowed_dept:
        query_invalids += " AND e.department = ?"
        params_invalids.append(allowed_dept)
    
    if emp_id_filter:
        query_invalids += " AND e.id = ?"
        params_invalids.append(emp_id_filter)
        
    query_invalids += '''
        GROUP BY e.id, DATE(ar.check_time)
        HAVING punch_count < 2
    '''

    cursor = conn.execute(query_invalids, params_invalids)
    potential_invalids = cursor.fetchall()
    
    # Filter out already Excused days
    excuses_in_range_query = '''
        SELECT ae.employee_id, ae.date FROM attendance_excuses ae
        JOIN employees e ON ae.employee_id = e.id
        WHERE ae.date BETWEEN ? AND ?
    '''
    params_excuses_check = [start_date, end_date]
    
    if allowed_dept:
        excuses_in_range_query += " AND e.department = ?"
        params_excuses_check.append(allowed_dept)
        
    if emp_id_filter:
        excuses_in_range_query += " AND ae.employee_id = ?"
        params_excuses_check.append(emp_id_filter)

    excuses_in_range = conn.execute(excuses_in_range_query, params_excuses_check).fetchall()
    
    excused_map = set((e['employee_id'], e['date']) for e in excuses_in_range)
    
    invalid_days = []
    for row in potential_invalids:
        if (row['employee_id'], row['date']) not in excused_map:
            invalid_days.append(dict(row))
    
    # --- 2. Existing Excuses Query ---
    query_excuses = '''
        SELECT ae.*, e.name as employee_name, e.department, e.position
        FROM attendance_excuses ae
        JOIN employees e ON ae.employee_id = e.id
        WHERE ae.date BETWEEN ? AND ?
    '''
    params_excuses = [start_date, end_date]
    
    if allowed_dept:
        query_excuses += " AND e.department = ?"
        params_excuses.append(allowed_dept)
        
    if emp_id_filter:
        query_excuses += " AND e.id = ?"
        params_excuses.append(emp_id_filter)
        
    query_excuses += " ORDER BY ae.date DESC LIMIT 100"
    
    excuses = conn.execute(query_excuses, params_excuses).fetchall()
    
    # 3. Dropdown Data (Employees only)
    emp_dropdown_query = 'SELECT id, name, employee_number FROM employees WHERE is_active=1'
    emp_dropdown_params = []
    if allowed_dept:
        emp_dropdown_query += ' AND department = ?'
        emp_dropdown_params.append(allowed_dept)
    emp_dropdown_query += ' ORDER BY name'
    employees = conn.execute(emp_dropdown_query, emp_dropdown_params).fetchall()
    
    pass # conn.close() removed to prevent leak in Flask g
    
    return render_template('attendance_excuses.html', 
                           invalid_days=invalid_days, 
                           excuses=excuses, 
                           employees=employees,
                           today_date=today.strftime('%Y-%m-%d'))

@attendance_excuses_bp.route('/attendance_excuses/add', methods=['POST'])
@login_required
@require_permission('attendance.edit')
def add_excuse():
    try:
        employee_id = request.form['employee_id']
        date_str = request.form['date']
        type_ = request.form.get('type', 'mission')
        reason = request.form.get('reason', '')
        
        conn = get_db_connection()
        try:
            # Enforce department restriction
            allowed_dept = get_allowed_department_name()
            if allowed_dept:
                emp = conn.execute('SELECT department FROM employees WHERE id = ?', (employee_id,)).fetchone()
                if not emp or emp['department'] != allowed_dept:
                    flash(gettext('x.f_excuse_outside_dept'), 'error')
                    pass # conn.close() removed to prevent leak in Flask g
                    return redirect(url_for('attendance_excuses.index'))
                    
            from flask import session as _session
            conn.execute('''
                INSERT INTO attendance_excuses (employee_id, date, type, reason, created_by)
                VALUES (?, ?, ?, ?, ?)
            ''', (employee_id, date_str, type_, reason, _session.get('user_id')))
            conn.commit()
            flash(gettext('x.f_excuse_added'), 'success')
        except Exception as e:
            if 'UNIQUE' in str(e):
                flash(gettext('x.f_excuse_exists_for_date'), 'warning')
            else:
                flash(gettext('x.f_db_error') % {'p0': f'{e}'}, 'error')
        finally:
            pass # conn.close() removed to prevent leak in Flask g
            
    except Exception as e:
        flash(gettext('x.f_error_occurred') % {'p0': f'{e}'}, 'error')
        
    return redirect(url_for('attendance_excuses.index'))

@attendance_excuses_bp.route('/attendance_excuses/delete', methods=['POST'])
@login_required
@require_permission('attendance.edit')
def delete_excuse():
    try:
        excuse_id = request.form['excuse_id']
        conn = get_db_connection()
        conn.execute('DELETE FROM attendance_excuses WHERE id = ?', (excuse_id,))
        conn.commit()
        pass # conn.close() removed to prevent leak in Flask g
        flash(gettext('x.f_excuse_deleted'), 'success')
    except Exception as e:
        flash(gettext('x.f_delete_error') % {'p0': f'{e}'}, 'error')
        
    return redirect(url_for('attendance_excuses.index'))
