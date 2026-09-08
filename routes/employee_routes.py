from flask import Blueprint, render_template, request, redirect, url_for, flash, send_file, jsonify, session
from flask_babel import gettext
import sqlite3
import pandas as pd
import io
import openpyxl
from datetime import datetime
from utils.db import get_db_connection
from utils.auth import login_required, get_allowed_department_name
from utils.rbac import require_permission
from utils.common import allowed_file
from utils.fingerprint_utils import queue_adms_user_update, queue_adms_user_delete

employee_bp = Blueprint('employee', __name__)

@employee_bp.route('/employees')
@login_required
@require_permission('page.employees')
def employees():
    # Render template immediately (Lazy Loading)
    return render_template('employees.html')

@employee_bp.route('/api/employees')
@login_required
@require_permission('employee.view')
def get_employees_api():
    try:
        conn = get_db_connection()
        allowed_dept = get_allowed_department_name()
        query = '''
            SELECT e.*, m.name as manager_name 
            FROM employees e
            LEFT JOIN employees m ON e.manager_id = m.id
        '''
        params = []
        if allowed_dept:
            query += ' WHERE e.department = ?'
            params.append(allowed_dept)
        query += ' ORDER BY CAST(e.employee_number AS INTEGER), e.employee_number'
        
        employees = conn.execute(query, params).fetchall()
        
        # Convert rows to dict list
        employees_list = []
        for emp in employees:
            employees_list.append({
                'id': emp['id'],
                'employee_number': emp['employee_number'],
                'name': emp['name'],
                'email': emp['email'],
                'phone': emp['phone'],
                'department': emp['department'],
                'position': emp['position'],
                'hire_date': emp['hire_date'],
                'salary': emp['salary'],
                'cost_center': emp['cost_center'],
                'shift_type': emp['shift_type'],
                'arabic_name': emp['arabic_name'] if 'arabic_name' in emp.keys() else '',
                'manager_id': emp['manager_id'],
                'manager_name': emp['manager_name']
            })
            
        pass # conn.close() removed to prevent leak in Flask g
        return jsonify({'success': True, 'employees': employees_list})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@employee_bp.route('/employees/add', methods=['GET', 'POST'])
@login_required
@require_permission('employee.create')
def add_employee():
    conn = get_db_connection()
    if request.method == 'POST':
        try:
            # Required fields validation
            name = request.form.get('name', '').strip()
            employee_number = request.form.get('employee_number', '').strip()
            
            allowed_dept = get_allowed_department_name()
            if allowed_dept:
                department = allowed_dept
            else:
                department = request.form.get('department', '').strip()
                
            position = request.form.get('position', '').strip()
            hire_date = request.form.get('hire_date', '').strip()
            salary_raw = request.form.get('salary', '0').strip()

            if not name or not employee_number or not department or not position:
                flash(gettext('x.f_fill_required_fields'), 'error')
                return redirect(url_for('employee.add_employee'))

            try:
                salary = float(salary_raw) if salary_raw else 0.0
            except ValueError:
                flash(gettext('x.f_salary_must_be_number'), 'error')
                return redirect(url_for('employee.add_employee'))

            cost_center = (request.form.get('cost_center') or '').strip()
            phone = (request.form.get('phone') or '').strip()
            email = (request.form.get('email') or '').strip()
            address = (request.form.get('address') or '').strip()
            arabic_name = (request.form.get('arabic_name') or '').strip()
            shift_type = request.form.get('shift_type', 'صباحي')
            is_active = int(request.form.get('is_active', 1))
            
            # New 35 Fields
            national_id = request.form.get('national_id', '').strip()
            national_id_issue_date = request.form.get('national_id_issue_date', '').strip() or None
            national_id_expiry_date = request.form.get('national_id_expiry_date', '').strip() or None
            national_id_place = request.form.get('national_id_place', '').strip()
            passport_number = request.form.get('passport_number', '').strip()
            passport_expiry_date = request.form.get('passport_expiry_date', '').strip() or None
            dob = request.form.get('dob', '').strip() or None
            pob = request.form.get('pob', '').strip()
            nationality = request.form.get('nationality', '').strip()
            religion = request.form.get('religion', '').strip()
            gender = request.form.get('gender', '').strip()
            marital_status = request.form.get('marital_status', '').strip()
            military_status = request.form.get('military_status', '').strip()
            work_email = request.form.get('work_email', '').strip()
            work_phone = request.form.get('work_phone', '').strip()
            work_ext = request.form.get('work_ext', '').strip()
            emergency_contact_name = request.form.get('emergency_contact_name', '').strip()
            emergency_contact_relation = request.form.get('emergency_contact_relation', '').strip()
            emergency_contact_phone = request.form.get('emergency_contact_phone', '').strip()
            contract_start_date = request.form.get('contract_start_date', '').strip() or None
            contract_end_date = request.form.get('contract_end_date', '').strip() or None
            contract_type = request.form.get('contract_type', '').strip()
            employment_status = request.form.get('employment_status', '').strip()
            manager_id_raw = request.form.get('manager_id', '').strip()
            manager_id = int(manager_id_raw) if manager_id_raw else None
            _mgr_err = _validate_manager_chain(get_db_connection(), id, manager_id)
            if _mgr_err:
                flash(_mgr_err, 'error')
                return redirect(url_for('employee.edit_employee', id=id))
            grade_level = request.form.get('grade_level', '').strip()
            branch_location = request.form.get('branch_location', '').strip()
            bank_name = request.form.get('bank_name', '').strip()
            bank_account_number = request.form.get('bank_account_number', '').strip()
            bank_iban = request.form.get('bank_iban', '').strip()
            bank_swift = request.form.get('bank_swift', '').strip()
            social_security_number = request.form.get('social_security_number', '').strip()
            tax_id = request.form.get('tax_id', '').strip()
            education_level = request.form.get('education_level', '').strip()
            university = request.form.get('university', '').strip()
            graduation_year_raw = request.form.get('graduation_year', '').strip()
            graduation_year = int(graduation_year_raw) if graduation_year_raw else None
            
            shift_data = conn.execute('SELECT start_time, end_time FROM shift_types WHERE name = ?', (shift_type,)).fetchone()
            
            if shift_data:
                default_start_time = shift_data[0]
                default_end_time = shift_data[1]
            else:
                default_start_time = (request.form.get('default_start_time') or '08:00').strip()
                default_end_time = (request.form.get('default_end_time') or '16:00').strip()
            
            try:
                weekly_leave_days = int(request.form.get('weekly_leave_days', 0))
                weekly_leave_start = int(request.form.get('weekly_leave_start', 5))
            except ValueError:
                weekly_leave_days = 0
                weekly_leave_start = 5
            
            weekly_leave_checkboxes = request.form.getlist('weekly_leave_days_checkboxes')
            weekly_leave_selected_days = ""
            
            if weekly_leave_checkboxes:
                try:
                    selected_days = sorted([int(day) for day in weekly_leave_checkboxes])
                    if selected_days:
                        weekly_leave_days = len(selected_days)
                        weekly_leave_start = selected_days[0]
                        weekly_leave_selected_days = ",".join(map(str, selected_days))
                except ValueError:
                    pass
            elif weekly_leave_days > 0:
                 if weekly_leave_days == 2:
                     weekly_leave_selected_days = "5,6"
                 elif weekly_leave_days == 1:
                     weekly_leave_selected_days = "5"
                 else:
                     weekly_leave_selected_days = "5,6"
            
            try:
                previous_leave_balance = float(request.form.get('previous_leave_balance', 0))
            except ValueError:
                previous_leave_balance = 0.0
            
            # بيانات البصمة
            card_number = (request.form.get('card_number') or '').strip()
            try:
                privilege = int(request.form.get('privilege', 0))
            except ValueError:
                privilege = 0
            password = (request.form.get('password') or '').strip()
            group_id = (request.form.get('group_id') or '').strip()
            
            conn.execute('''
                INSERT INTO employees (
                    name, employee_number, department, position, hire_date, salary, cost_center, phone, email, address, arabic_name,
                    default_start_time, default_end_time, shift_type, weekly_leave_days, weekly_leave_start, previous_leave_balance,
                    weekly_leave_selected_days, card_number, privilege, password, group_id, is_active,
                    national_id, national_id_issue_date, national_id_expiry_date, national_id_place, passport_number, passport_expiry_date,
                    dob, pob, nationality, religion, gender, marital_status, military_status, work_email, work_phone, work_ext,
                    emergency_contact_name, emergency_contact_relation, emergency_contact_phone, contract_start_date, contract_end_date,
                    contract_type, employment_status, manager_id, grade_level, branch_location, bank_name, bank_account_number,
                    bank_iban, bank_swift, social_security_number, tax_id, education_level, university, graduation_year
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?
                )
            ''', (
                name, employee_number, department, position, hire_date, salary, cost_center, phone, email, address, arabic_name,
                default_start_time, default_end_time, shift_type, weekly_leave_days, weekly_leave_start, previous_leave_balance,
                weekly_leave_selected_days, card_number, privilege, password, group_id, is_active,
                national_id, national_id_issue_date, national_id_expiry_date, national_id_place, passport_number, passport_expiry_date,
                dob, pob, nationality, religion, gender, marital_status, military_status, work_email, work_phone, work_ext,
                emergency_contact_name, emergency_contact_relation, emergency_contact_phone, contract_start_date, contract_end_date,
                contract_type, employment_status, manager_id, grade_level, branch_location, bank_name, bank_account_number,
                bank_iban, bank_swift, social_security_number, tax_id, education_level, university, graduation_year
            ))
            
            employee_id = conn.execute('SELECT last_insert_rowid()').fetchone()[0]

            # ADMS SYNC
            if is_active == 1:
                employee_data = {
                    'employee_number': employee_number,
                    'name': name,
                    'card_number': card_number,
                    'privilege': privilege,
                    'password': password,
                    'group_id': group_id
                }
                queue_adms_user_update(employee_data)
            
            conn.commit()
            flash(gettext('x.f_employee_added'), 'success')
            return redirect(url_for('employee.employees'))
        except Exception as e:
            if 'UNIQUE constraint failed' in str(e):
                flash(gettext('x.f_employee_number_exists'), 'error')
            else:
                flash(gettext('x.f_add_error') % {'p0': f'{str(e)}'}, 'error')
            conn.rollback()

    shift_types = conn.execute('SELECT * FROM shift_types WHERE is_active = 1 ORDER BY name').fetchall()
    managers = conn.execute('SELECT id, name, employee_number FROM employees WHERE is_active = 1 ORDER BY name').fetchall()
    departments = conn.execute('''
        SELECT name FROM departments_master 
        UNION 
        SELECT DISTINCT department as name FROM employees WHERE department IS NOT NULL AND department != '' 
        ORDER BY name
    ''').fetchall()
    positions = conn.execute('''
        SELECT name FROM positions_master 
        UNION 
        SELECT DISTINCT position as name FROM employees WHERE position IS NOT NULL AND position != '' 
        ORDER BY name
    ''').fetchall()
    pass # conn.close() removed to prevent leak in Flask g
    return render_template('add_employee.html', shift_types=shift_types, managers=managers, departments=departments, positions=positions)

@employee_bp.route('/employees/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@require_permission('employee.edit')
def edit_employee(id):
    conn = get_db_connection()
    employee = conn.execute('SELECT * FROM employees WHERE id = ?', (id,)).fetchone()
    if not employee:
        flash(gettext('x.f_employee_not_found'), 'error')
        pass # conn.close() removed to prevent leak in Flask g
        return redirect(url_for('employee.employees'))

    if request.method == 'POST':
        try:
            name = request.form.get('name', '').strip()
            employee_number = request.form.get('employee_number', '').strip()
            
            allowed_dept = get_allowed_department_name()
            if allowed_dept:
                department = allowed_dept
            else:
                department = request.form.get('department', '').strip()
                
            position = request.form.get('position', '').strip()
            hire_date = request.form.get('hire_date', '').strip()
            salary_raw = request.form.get('salary', '0').strip()

            if not name or not employee_number or not department or not position:
                flash(gettext('x.f_fill_required_fields'), 'error')
                return redirect(url_for('employee.add_employee'))

            try:
                salary = float(salary_raw) if salary_raw else 0.0
            except ValueError:
                flash(gettext('x.f_salary_must_be_number'), 'error')
                return redirect(url_for('employee.add_employee'))

            cost_center = (request.form.get('cost_center') or '').strip()
            phone = (request.form.get('phone') or '').strip()
            email = (request.form.get('email') or '').strip()
            address = (request.form.get('address') or '').strip()
            arabic_name = (request.form.get('arabic_name') or '').strip()
            shift_type = request.form.get('shift_type', 'صباحي')
            is_active = int(request.form.get('is_active', 0))
            
            # New 35 Fields
            national_id = request.form.get('national_id', '').strip()
            national_id_issue_date = request.form.get('national_id_issue_date', '').strip() or None
            national_id_expiry_date = request.form.get('national_id_expiry_date', '').strip() or None
            national_id_place = request.form.get('national_id_place', '').strip()
            passport_number = request.form.get('passport_number', '').strip()
            passport_expiry_date = request.form.get('passport_expiry_date', '').strip() or None
            dob = request.form.get('dob', '').strip() or None
            pob = request.form.get('pob', '').strip()
            nationality = request.form.get('nationality', '').strip()
            religion = request.form.get('religion', '').strip()
            gender = request.form.get('gender', '').strip()
            marital_status = request.form.get('marital_status', '').strip()
            military_status = request.form.get('military_status', '').strip()
            work_email = request.form.get('work_email', '').strip()
            work_phone = request.form.get('work_phone', '').strip()
            work_ext = request.form.get('work_ext', '').strip()
            emergency_contact_name = request.form.get('emergency_contact_name', '').strip()
            emergency_contact_relation = request.form.get('emergency_contact_relation', '').strip()
            emergency_contact_phone = request.form.get('emergency_contact_phone', '').strip()
            contract_start_date = request.form.get('contract_start_date', '').strip() or None
            contract_end_date = request.form.get('contract_end_date', '').strip() or None
            contract_type = request.form.get('contract_type', '').strip()
            employment_status = request.form.get('employment_status', '').strip()
            manager_id_raw = request.form.get('manager_id', '').strip()
            manager_id = int(manager_id_raw) if manager_id_raw else None
            grade_level = request.form.get('grade_level', '').strip()
            branch_location = request.form.get('branch_location', '').strip()
            bank_name = request.form.get('bank_name', '').strip()
            bank_account_number = request.form.get('bank_account_number', '').strip()
            bank_iban = request.form.get('bank_iban', '').strip()
            bank_swift = request.form.get('bank_swift', '').strip()
            social_security_number = request.form.get('social_security_number', '').strip()
            tax_id = request.form.get('tax_id', '').strip()
            education_level = request.form.get('education_level', '').strip()
            university = request.form.get('university', '').strip()
            graduation_year_raw = request.form.get('graduation_year', '').strip()
            graduation_year = int(graduation_year_raw) if graduation_year_raw else None
            
            shift_data = conn.execute('SELECT start_time, end_time FROM shift_types WHERE name = ?', (shift_type,)).fetchone()
            
            if shift_data:
                default_start_time = shift_data[0]
                default_end_time = shift_data[1]
            else:
                default_start_time = (request.form.get('default_start_time') or '08:00').strip()
                default_end_time = (request.form.get('default_end_time') or '16:00').strip()
            
            try:
                weekly_leave_days = int(request.form.get('weekly_leave_days', 0))
                weekly_leave_start = int(request.form.get('weekly_leave_start', 5))
            except ValueError:
                weekly_leave_days = 0
                weekly_leave_start = 5
            
            weekly_leave_checkboxes = request.form.getlist('weekly_leave_days_checkboxes')
            weekly_leave_selected_days = ""
            
            if weekly_leave_checkboxes:
                try:
                    selected_days = sorted([int(day) for day in weekly_leave_checkboxes])
                    if selected_days:
                        weekly_leave_days = len(selected_days)
                        weekly_leave_start = selected_days[0]
                        weekly_leave_selected_days = ",".join(map(str, selected_days))
                except ValueError:
                    pass
            elif weekly_leave_days > 0:
                 if weekly_leave_days == 2:
                     weekly_leave_selected_days = "5,6"
                 elif weekly_leave_days == 1:
                     weekly_leave_selected_days = "5"
                 else:
                     weekly_leave_selected_days = "5,6"
            
            try:
                previous_leave_balance = float(request.form.get('previous_leave_balance', 0))
            except ValueError:
                previous_leave_balance = 0.0
            
            # بيانات البصمة
            card_number = (request.form.get('card_number') or '').strip()
            try:
                privilege = int(request.form.get('privilege', 0))
            except ValueError:
                privilege = 0
            password = (request.form.get('password') or '').strip()
            group_id = (request.form.get('group_id') or '').strip()
            
            _before = conn.execute('SELECT * FROM employees WHERE id = ?',
                                   (id,)).fetchone()
            conn.execute('''
                UPDATE employees SET 
                    name=?, department=?, position=?, hire_date=?, salary=?, cost_center=?, phone=?, email=?, address=?, arabic_name=?, 
                    default_start_time=?, default_end_time=?, shift_type=?, weekly_leave_days=?, weekly_leave_start=?, previous_leave_balance=?, 
                    weekly_leave_selected_days=?, card_number=?, privilege=?, password=?, group_id=?, is_active=?,
                    national_id=?, national_id_issue_date=?, national_id_expiry_date=?, national_id_place=?, passport_number=?, passport_expiry_date=?,
                    dob=?, pob=?, nationality=?, religion=?, gender=?, marital_status=?, military_status=?, work_email=?, work_phone=?, work_ext=?,
                    emergency_contact_name=?, emergency_contact_relation=?, emergency_contact_phone=?, contract_start_date=?, contract_end_date=?,
                    contract_type=?, employment_status=?, manager_id=?, grade_level=?, branch_location=?, bank_name=?, bank_account_number=?,
                    bank_iban=?, bank_swift=?, social_security_number=?, tax_id=?, education_level=?, university=?, graduation_year=?
                WHERE id=?
            ''', (
                name, department, position, hire_date, salary, cost_center, phone, email, address, arabic_name, 
                default_start_time, default_end_time, shift_type, weekly_leave_days, weekly_leave_start, previous_leave_balance, 
                weekly_leave_selected_days, card_number, privilege, password, group_id, is_active,
                national_id, national_id_issue_date, national_id_expiry_date, national_id_place, passport_number, passport_expiry_date,
                dob, pob, nationality, religion, gender, marital_status, military_status, work_email, work_phone, work_ext,
                emergency_contact_name, emergency_contact_relation, emergency_contact_phone, contract_start_date, contract_end_date,
                contract_type, employment_status, manager_id, grade_level, branch_location, bank_name, bank_account_number,
                bank_iban, bank_swift, social_security_number, tax_id, education_level, university, graduation_year,
                id
            ))
            try:
                from utils.payroll_engine import log_employee_changes
                _after = conn.execute('SELECT * FROM employees WHERE id = ?',
                                      (id,)).fetchone()
                log_employee_changes(conn, id, _before, _after,
                                     user_id=session.get('user_id'),
                                     source='edit')
            except Exception as _e:
                print(f'audit log failed for employee {id}: {_e}')
            conn.commit()
            
            # ADMS SYNC
            if is_active == 1:
                # Update/Add on device
                employee_data = {
                    'employee_number': employee['employee_number'],
                    'name': name,
                    'card_number': card_number,
                    'privilege': privilege,
                    'password': password,
                    'group_id': group_id
                }
                queue_adms_user_update(employee_data)
            else:
                # If changed to inactive, delete from device
                queue_adms_user_delete(employee['employee_number'])

            flash(gettext('x.f_employee_updated'), 'success')
            pass # conn.close() removed to prevent leak in Flask g
            return redirect(url_for('employee.employees'))
        except Exception as e:
            conn.rollback()
            print(f"Error updating employee {id}: {e}")
            flash(gettext('x.f_update_error') % {'p0': f'{str(e)}'}, 'error')
    
    shift_types = conn.execute('SELECT * FROM shift_types WHERE is_active = 1 ORDER BY name').fetchall()
    
    # جلب بيانات البصمة والوجه والصورة
    emp_number = employee['employee_number']
    fingerprints_count = conn.execute('SELECT COUNT(*) FROM fingerprint_templates WHERE pin = ?', (emp_number,)).fetchone()[0]
    
    has_face = conn.execute('SELECT COUNT(*) FROM fingerprint_faces WHERE pin = ?', (emp_number,)).fetchone()[0] > 0
    
    photo_record = conn.execute('SELECT photo_data FROM user_photos WHERE pin = ?', (emp_number,)).fetchone()
    has_photo = photo_record is not None
    photo_data = photo_record['photo_data'] if photo_record else None
    
    managers = conn.execute('SELECT id, name, employee_number FROM employees WHERE is_active = 1 AND id != ? ORDER BY name', (id,)).fetchall()
    departments = conn.execute('''
        SELECT name FROM departments_master 
        UNION 
        SELECT DISTINCT department as name FROM employees WHERE department IS NOT NULL AND department != '' 
        ORDER BY name
    ''').fetchall()
    positions = conn.execute('''
        SELECT name FROM positions_master 
        UNION 
        SELECT DISTINCT position as name FROM employees WHERE position IS NOT NULL AND position != '' 
        ORDER BY name
    ''').fetchall()
    
    pass # conn.close() removed to prevent leak in Flask g
    return render_template('edit_employee.html', 
                           employee=employee, 
                           shift_types=shift_types,
                           managers=managers,
                           departments=departments,
                           positions=positions,
                           fingerprints_count=fingerprints_count,
                           has_face=has_face,
                           has_photo=has_photo,
                           photo_data=photo_data)

@employee_bp.route('/employees/delete/<int:id>')
@login_required
@require_permission('employee.delete')
def delete_employee(id):
    conn = get_db_connection()
    employee = conn.execute('SELECT employee_number FROM employees WHERE id = ?', (id,)).fetchone()
    if employee:
        # Queue deletion for ADMS
        queue_adms_user_delete(employee['employee_number'])
        
        conn.execute('DELETE FROM employees WHERE id = ?', (id,))
        conn.commit()
    pass # conn.close() removed to prevent leak in Flask g
    flash(gettext('x.f_employee_deleted'), 'success')
    return redirect(url_for('employee.employees'))

@employee_bp.route('/employees/delete_all', methods=['POST'])
@login_required
@require_permission('admin.settings')
def delete_all_employees():
    try:
        conn = get_db_connection()
        # Get all PINs before deleting
        employees = conn.execute('SELECT employee_number FROM employees').fetchall()
        for emp in employees:
            queue_adms_user_delete(emp['employee_number'])
            
        conn.execute('DELETE FROM employees')
        conn.execute('DELETE FROM sqlite_sequence WHERE name="employees"')
        conn.commit()
        pass # conn.close() removed to prevent leak in Flask g
        flash(gettext('x.f_all_employees_deleted'), 'success')
    except Exception as e:
        flash(gettext('x.f_delete_error') % {'p0': f'{str(e)}'}, 'error')
    return redirect(url_for('employee.employees'))

# Optional employee fields accepted by the Excel import. One declarative list
# feeds the column-alias map, the downloadable template and the INSERT/UPDATE,
# so adding a field later means editing exactly one place.
# (key, db_column, [accepted header names], example value)
IMPORT_OPTIONAL_FIELDS = [
    ('national_id', 'national_id',
     ['Civil ID', 'National ID', 'الرقم المدني', 'رقم الهوية', 'الرقم القومي'], '284091500123'),
    ('nid_issue', 'national_id_issue_date',
     ['Civil ID Issue Date', 'تاريخ إصدار البطاقة'], ''),
    ('nid_expiry', 'national_id_expiry_date',
     ['Civil ID Expiry Date', 'تاريخ انتهاء البطاقة'], ''),
    ('nid_place', 'national_id_place',
     ['Civil ID Place', 'مكان الإصدار'], ''),
    ('passport_number', 'passport_number',
     ['Passport Number', 'رقم الجواز'], ''),
    ('passport_expiry', 'passport_expiry_date',
     ['Passport Expiry Date', 'تاريخ انتهاء الجواز'], ''),
    ('dob', 'dob', ['Date of Birth', 'تاريخ الميلاد'], ''),
    ('pob', 'pob', ['Place of Birth', 'مكان الميلاد'], ''),
    ('nationality', 'nationality', ['Nationality', 'الجنسية'], 'كويتي'),
    ('religion', 'religion', ['Religion', 'الديانة'], ''),
    ('gender', 'gender', ['Gender', 'الجنس'], 'ذكر'),
    ('marital_status', 'marital_status', ['Marital Status', 'الحالة الاجتماعية'], ''),
    ('military_status', 'military_status', ['Military Status', 'الحالة العسكرية'], ''),
    ('work_email', 'work_email', ['Work Email', 'البريد الوظيفي'], ''),
    ('work_phone', 'work_phone', ['Work Phone', 'هاتف العمل'], ''),
    ('work_ext', 'work_ext', ['Work Extension', 'التحويلة'], ''),
    ('emg_name', 'emergency_contact_name',
     ['Emergency Contact Name', 'اسم جهة الاتصال للطوارئ'], ''),
    ('emg_relation', 'emergency_contact_relation',
     ['Emergency Contact Relation', 'صلة القرابة'], ''),
    ('emg_phone', 'emergency_contact_phone',
     ['Emergency Contact Phone', 'هاتف الطوارئ'], ''),
    ('contract_start', 'contract_start_date',
     ['Contract Start Date', 'تاريخ بداية العقد'], ''),
    ('contract_end', 'contract_end_date',
     ['Contract End Date', 'تاريخ نهاية العقد'], ''),
    ('contract_type', 'contract_type', ['Contract Type', 'نوع العقد'], ''),
    ('employment_status', 'employment_status',
     ['Employment Status', 'حالة التوظيف'], ''),
    ('grade_level', 'grade_level', ['Grade Level', 'الدرجة الوظيفية'], ''),
    ('branch_location', 'branch_location', ['Branch', 'الفرع', 'الموقع'], ''),
    ('bank_name', 'bank_name', ['Bank Name', 'اسم البنك'], ''),
    ('bank_account', 'bank_account_number',
     ['Bank Account Number', 'رقم الحساب البنكي'], ''),
    ('bank_iban', 'bank_iban', ['IBAN', 'الآيبان'], ''),
    ('bank_swift', 'bank_swift', ['SWIFT', 'سويفت'], ''),
    ('social_security', 'social_security_number',
     ['Social Security Number', 'رقم التأمينات'], ''),
    ('tax_id', 'tax_id', ['Tax ID', 'الرقم الضريبي'], ''),
    ('education_level', 'education_level', ['Education Level', 'المؤهل'], ''),
    ('university', 'university', ['University', 'الجامعة'], ''),
    ('graduation_year', 'graduation_year', ['Graduation Year', 'سنة التخرج'], ''),
    ('eos_date', 'end_of_service_date',
     ['End of Service Date', 'تاريخ انتهاء الخدمة'], ''),
]

@employee_bp.route('/employees/import', methods=['GET', 'POST'])
@login_required
@require_permission('employee.create')
def import_employees():
    if request.method == 'POST':
        if 'file' not in request.files:
            flash(gettext('x.f_no_file_chosen'), 'error')
            return redirect(request.url)
        
        file = request.files['file']
        if file.filename == '':
            flash(gettext('x.f_no_file_chosen'), 'error')
            return redirect(request.url)
        
        if file and allowed_file(file.filename):
            try:
                # قراءة ملف Excel
                df = pd.read_excel(file)
                
                print(f"\n--- Starting Employee Import ---")
                print(f"File: {file.filename}")
                print(f"Columns in file: {df.columns.tolist()}")

                # ذكاء اصطناعي بسيط لتطابق أسماء الأعمدة لمرونة أكبر
                column_mapping = {}
                # تنظيف أسماء الأعمدة من المسافات الزائدة
                df.columns = [str(c).strip() for c in df.columns]
                
                possible_cols = {
                    'name': ['Name', 'الاسم', 'اسم الموظف', 'الاسم الكامل'],
                    'arabic_name': ['Arabic Name', 'الاسم بالعربية', 'الاسم باللغة العربية'],
                    'employee_number': ['Employee Number (Fingerprint ID)', 'Employee Number', 'الرقم الوظيفي', 'رقم البصمة', 'ID'],
                    'department': ['Department', 'القسم'],
                    'position': ['Position', 'المنصب', 'الوظيفة', 'المسمى الوظيفي'],
                    'hire_date': ['Hire Date', 'تاريخ التعيين'],
                    'salary': ['Salary', 'الراتب', 'الراتب الأساسي', 'Total Salary', 'راتب الموظف'],
                    'cost_center': ['Cost Center', 'مركز التكلفة'],
                    'allowance_start': ['Allowance Start Date', 'تاريخ بدء البدل'],
                    'manager_number': ['Manager Employee Number', 'رقم المدير المباشر',
                                       'المدير المباشر'],
                    'phone': ['Phone', 'الهاتف', 'رقم الهاتف'],
                    'email': ['Email', 'البريد', 'البريد الإلكتروني'],
                    'address': ['Address', 'العنوان'],
                    'shift_name': ['Shift Name', 'نوع الشفت', 'نوع المناوبة'],
                    'shift_start': ['Shift Start Time', 'وقت البداية'],
                    'shift_end': ['Shift End Time', 'وقت النهاية'],
                    'card_number': ['Card Number', 'رقم البطاقة'],
                    'privilege': ['Privilege', 'الصلاحية'], # 0=User
                    'password': ['Password', 'كلمة مرور الجهاز', 'كلمة المرور'],
                    'group_id': ['Group ID', 'رقم المجموعة'],
                    # Weekend Settings
                    'weekend_type': ['Weekend Type', 'نوع العطلة', 'نوع الإجازة الأسبوعية'], 
                    'weekend_days': ['Weekend Days', 'أيام العطلة'],
                    # Initial Salary & Balance
                    'previous_balance': ['Previous Balance', 'رصيد سابق', 'Opening Leave Balance', 'الرصيد الافتتاحي'],
                    'init_salary_month': ['Initial Salary Month', 'راتب أولي - الشهر', 'الشهر'],
                    'init_salary_year': ['Initial Salary Year', 'راتب أولي - السنة', 'السنة'],
                    'allowances': ['Allowances', 'البدلات'],
                    'deductions': ['Deductions', 'الخصومات'],
                }
                
                # البحث عن أفضل مطابقة لكل عمود مطلوب
                for _k, _dbcol, _aliases, _ex in IMPORT_OPTIONAL_FIELDS:
                    possible_cols[_k] = _aliases

                for key, possibilities in possible_cols.items():
                    for col in possibilities:
                        if col in df.columns:
                            column_mapping[key] = col
                            break
                
                print(f"Mapped Columns Result: {column_mapping}")

                # التحقق من وجود الأعمدة الأساسية
                required_keys = ['name', 'employee_number', 'department', 'position', 'hire_date', 'salary']
                missing_keys = [k for k in required_keys if k not in column_mapping]
                
                if missing_keys:
                    friendly_names = {'name': 'Name', 'employee_number': 'Employee Number', 'department': 'Department', 'position': 'Position', 'hire_date': 'Hire Date', 'salary': 'Salary'}
                    found_cols = ", ".join(df.columns.tolist())
                    error_msg = f'الملف يفتقد الأعمدة الأساسية التالية: {", ".join([friendly_names[k] for k in missing_keys])}. الأعمدة المكتشفة: {found_cols}'
                    print(f"Import aborted: {error_msg}")
                    flash(error_msg, 'error')
                    return redirect(request.url)
                
                conn = get_db_connection()
                success_count = 0
                error_count = 0
                updated_count = 0
                total_rows = len(df)
                print(f"Starting to process {total_rows} rows...")
                
                def _clean_id(v):
                    """Excel numeric coercion turns '906' into 906.0 whenever any
                    cell in the column is blank, which would store the employee
                    number as '906.0' and silently break fingerprint matching
                    and duplicate detection. Normalise back to a plain string."""
                    if v is None:
                        return ''
                    txt = str(v).strip()
                    if txt.endswith('.0') and txt[:-2].replace('-', '').isdigit():
                        return txt[:-2]
                    return txt

                row_errors = []
                pending_managers = []
                for index, row in df.iterrows():
                    try:
                        # Mandatory cells must actually carry a value. A blank
                        # here previously produced an employee row reading
                        # "nan" with a today's hire date and zero salary, which
                        # then flows straight into payroll — so the row is
                        # rejected with a precise reason instead.
                        _blank = []
                        for _rk, _label in (('employee_number', 'الرقم الوظيفي'),
                                            ('name', 'الاسم'),
                                            ('department', 'القسم'),
                                            ('position', 'المنصب'),
                                            ('hire_date', 'تاريخ التعيين'),
                                            ('salary', 'الراتب')):
                            _val = row.get(column_mapping[_rk])
                            if pd.isnull(_val) or str(_val).strip() in ('', 'nan', 'NaT'):
                                _blank.append(_label)
                        if _blank:
                            error_count += 1
                            row_errors.append(f'سطر {index + 2}: حقول إلزامية فارغة — '
                                              + '، '.join(_blank))
                            continue
                        try:
                            if float(row[column_mapping['salary']]) <= 0:
                                error_count += 1
                                row_errors.append(f'سطر {index + 2}: الراتب يجب أن يكون أكبر من صفر')
                                continue
                        except (TypeError, ValueError):
                            error_count += 1
                            row_errors.append(f'سطر {index + 2}: الراتب غير رقمي')
                            continue

                        # تحويل التاريخ للتنسيق الصحيح (Fix NaTType error)
                        h_date_val = row[column_mapping['hire_date']]
                        try:
                            if pd.isnull(h_date_val):
                                # Default to today if missing (or handle as error if strict)
                                from datetime import date
                                hire_date = date.today().strftime('%Y-%m-%d')
                            else:
                                hire_date = pd.to_datetime(h_date_val).strftime('%Y-%m-%d')
                        except:
                            from datetime import date
                            hire_date = date.today().strftime('%Y-%m-%d')
                        
                        # معالجة الشفت
                        shift_name = None
                        if 'shift_name' in column_mapping:
                            shift_name = str(row[column_mapping['shift_name']]).strip()
                        if shift_name and shift_name.lower() not in ('nan', 'none', ''):
                            # التحقق مما إذا كان الشفت موجوداً
                            existing_shift = conn.execute('SELECT name FROM shift_types WHERE name = ?', (shift_name,)).fetchone()
                            
                            if not existing_shift:
                                # إنشاء شفت جديد
                                start_col = column_mapping.get('shift_start')
                                end_col = column_mapping.get('shift_end')
                                
                                start_time = str(row.get(start_col, '09:00')).strip() if start_col else '09:00'
                                end_time = str(row.get(end_col, '17:00')).strip() if end_col else '17:00'
                                
                                if start_time.lower() in ('nan', 'none', ''): start_time = '09:00'
                                if end_time.lower() in ('nan', 'none', ''): end_time = '17:00'

                                # تنظيف تنسيق الوقت (مثال: 9:00 -> 09:00)
                                try:
                                    if ':' in start_time:
                                        parts = start_time.split(':')
                                        start_time = f"{int(float(parts[0])):02d}:{int(float(parts[1])):02d}"
                                except:
                                    start_time = '09:00'
                                    
                                try:
                                    if ':' in end_time:
                                        parts = end_time.split(':')
                                        end_time = f"{int(float(parts[0])):02d}:{int(float(parts[1])):02d}"
                                except:
                                    end_time = '17:00'

                                conn.execute('''
                                    INSERT INTO shift_types (name, start_time, end_time, is_active)
                                    VALUES (?, ?, ?, 1)
                                ''', (shift_name, start_time, end_time))
                                print(f"Created new shift: {shift_name} ({start_time}-{end_time})")

                        # معالجة القسم (تأكد من وجوده في جدول الأقسام الرئيسي ليظهر في القوائم)
                        dept_name = str(row[column_mapping['department']]).strip()
                        if dept_name and dept_name.lower() not in ('nan', 'none', ''):
                            try:
                                existing_dept = conn.execute('SELECT name FROM departments_master WHERE name = ?', (dept_name,)).fetchone()
                                if not existing_dept:
                                    conn.execute('INSERT INTO departments_master (name, is_active) VALUES (?, 1)', (dept_name,))
                            except: pass

                        # معالجة المنصب (تأكد من وجوده في جدول المناصب الرئيسي ليظهر في القوائم)
                        pos_name = str(row[column_mapping['position']]).strip()
                        if pos_name and pos_name.lower() not in ('nan', 'none', ''):
                            try:
                                existing_pos = conn.execute('SELECT name FROM positions_master WHERE name = ?', (pos_name,)).fetchone()
                                if not existing_pos:
                                    conn.execute('INSERT INTO positions_master (name, is_active) VALUES (?, 1)', (pos_name,))
                            except: pass

                        # إدراج بيانات الموظف في قاعدة البيانات باستخدام المسميات المرنة
                        # --- Extract Extra Fields ---
                        def get_row_val(key, default=''):
                            col = column_mapping.get(key)
                            if not col: return default
                            val = row.get(col)
                            if pd.isnull(val): return default
                            # Same float-coercion guard as the employee number:
                            # card numbers, group ids, civil ids and phone
                            # numbers must not become '1234.0'.
                            return _clean_id(val)

                        cost_center = get_row_val('cost_center')
                        arabic_name = get_row_val('arabic_name')
                        card_number = get_row_val('card_number')
                        password = get_row_val('password')
                        group_id = get_row_val('group_id')
                        phone = get_row_val('phone')
                        email = get_row_val('email')
                        address = get_row_val('address')
                        
                        try:
                            priv_col = column_mapping.get('privilege')
                            priv_val = row.get(priv_col, 0) if priv_col else 0
                            privilege = int(float(priv_val)) if pd.notnull(priv_val) and str(priv_val).strip() != '' else 0
                        except:
                            privilege = 0

                        # Weekend Logic
                        weekly_leave_days = 2
                        weekly_leave_start = 5
                        weekly_leave_selected_days = "5,6"
                        
                        w_type = get_row_val('weekend_type')
                        if w_type:
                            if 'جمعة' in w_type and 'سبت' in w_type:
                                weekly_leave_selected_days = "5,6"
                                weekly_leave_days = 2
                            elif 'جمعة' in w_type:
                                weekly_leave_selected_days = "5"
                                weekly_leave_days = 1
                        
                        # Initial Balance
                        try:
                            pb_col = column_mapping.get('previous_balance')
                            pb_val = row.get(pb_col, 0) if pb_col else 0
                            previous_leave_balance = float(pb_val) if pd.notnull(pb_val) and str(pb_val).strip() != '' else 0.0
                        except:
                            previous_leave_balance = 0.0

                        _mgr_no = get_row_val('manager_number')
                        if _mgr_no:
                            pending_managers.append(
                                (_clean_id(row[column_mapping['employee_number']]),
                                 _mgr_no))

                        # Optional fields: blank cells simply leave the value
                        # untouched — only the mandatory columns are enforced.
                        extra = {}
                        for _k, _dbcol, _aliases, _ex in IMPORT_OPTIONAL_FIELDS:
                            _v = get_row_val(_k)
                            if _v != '':
                                extra[_dbcol] = _v

                        base_cols = ['name', 'department', 'position', 'hire_date',
                                     'salary', 'phone', 'email', 'address',
                                     'shift_type', 'cost_center', 'arabic_name',
                                     'card_number', 'privilege', 'password',
                                     'group_id', 'weekly_leave_days',
                                     'weekly_leave_start',
                                     'weekly_leave_selected_days',
                                     'previous_leave_balance']
                        base_vals = [
                            str(row[column_mapping['name']]),
                            str(row[column_mapping['department']]),
                            str(row[column_mapping['position']]),
                            hire_date,
                            float(row[column_mapping['salary']]),
                            phone, email, address,
                            shift_name if shift_name and shift_name.lower() not in ('nan', 'none', '') else None,
                            cost_center, arabic_name, card_number, privilege,
                            password, group_id, weekly_leave_days,
                            weekly_leave_start, weekly_leave_selected_days,
                            previous_leave_balance]

                        cursor = conn.cursor()
                        emp_no = _clean_id(row[column_mapping['employee_number']])
                        existing = conn.execute(
                            'SELECT id FROM employees WHERE employee_number = ?',
                            (emp_no,)).fetchone()
                        if existing:
                            _before_row = conn.execute(
                                'SELECT * FROM employees WHERE id = ?',
                                (existing['id'],)).fetchone()
                            cols = base_cols + list(extra.keys())
                            vals = base_vals + list(extra.values())
                            sets = ', '.join(f'{c}=?' for c in cols)
                            cursor.execute(f'UPDATE employees SET {sets} WHERE id=?',
                                           vals + [existing['id']])
                            emp_id = existing['id']
                            updated_count += 1
                            try:
                                from utils.payroll_engine import log_employee_changes
                                _after_row = conn.execute(
                                    'SELECT * FROM employees WHERE id = ?',
                                    (emp_id,)).fetchone()
                                log_employee_changes(conn, emp_id, _before_row,
                                                     _after_row,
                                                     user_id=session.get('user_id'),
                                                     source='import')
                            except Exception as _e:
                                print(f'audit log failed on import: {_e}')
                        else:
                            cols = ['employee_number'] + base_cols + list(extra.keys())
                            vals = [emp_no] + base_vals + list(extra.values())
                            ph = ', '.join('?' for _ in cols)
                            cursor.execute(
                                f"INSERT INTO employees ({', '.join(cols)}) VALUES ({ph})",
                                vals)
                            emp_id = cursor.lastrowid
                        
                        # Imported allowance -> a REAL fixed payroll item.
                        # The legacy `salaries` table below is a historic
                        # record only; the payroll engine reads
                        # employee_salary_items, so an allowance that lands
                        # only in `salaries` never reaches anyone's payslip.
                        try:
                            adds_col0 = column_mapping.get('allowances')
                            adds_raw0 = row.get(adds_col0) if adds_col0 else None
                            adds0 = (float(adds_raw0)
                                     if pd.notnull(adds_raw0) and str(adds_raw0).strip() != ''
                                     else 0.0)
                            if adds0 > 0:
                                it = conn.execute(
                                    "SELECT id FROM payroll_item_types WHERE code = 'imported_allowance'"
                                ).fetchone()
                                if not it:
                                    conn.execute(
                                        """INSERT INTO payroll_item_types
                                           (code, name_ar, name_en, kind, species,
                                            affects, is_system, sort_order)
                                           VALUES ('imported_allowance', 'بدل مستورد',
                                                   'Imported Allowance', 'earning',
                                                   'fixed', 'monthly', 0, 90)""")
                                    it = conn.execute(
                                        "SELECT id FROM payroll_item_types WHERE code = 'imported_allowance'"
                                    ).fetchone()
                                start_val = get_row_val('allowance_start') or hire_date
                                dup_item = conn.execute(
                                    """SELECT id FROM employee_salary_items
                                       WHERE employee_id = ? AND item_type_id = ?
                                         AND is_active = 1""",
                                    (emp_id, it['id'])).fetchone()
                                if dup_item:
                                    conn.execute(
                                        'UPDATE employee_salary_items SET amount = ? WHERE id = ?',
                                        (adds0, dup_item['id']))
                                else:
                                    conn.execute(
                                        """INSERT INTO employee_salary_items
                                           (employee_id, item_type_id, calc_method,
                                            amount, percent_value, start_date, created_by)
                                           VALUES (?, ?, 'fixed', ?, 0, ?, ?)""",
                                        (emp_id, it['id'], adds0, str(start_val)[:10],
                                         session.get('user_id')))
                        except Exception as e:
                            print(f"Skipped allowance item for row {index}: {e}")

                        # Handle Initial Salary
                        try:
                            s_month_col = column_mapping.get('init_salary_month')
                            s_year_col = column_mapping.get('init_salary_year')
                            
                            s_month_raw = row.get(s_month_col) if s_month_col else None
                            s_year_raw = row.get(s_year_col) if s_year_col else None
                            
                            # Convert to int safely
                            s_month = int(float(s_month_raw)) if pd.notnull(s_month_raw) and str(s_month_raw).strip() != '' else None
                            s_year = int(float(s_year_raw)) if pd.notnull(s_year_raw) and str(s_year_raw).strip() != '' else None
                            
                            if s_month and s_year:
                                base_sal = float(row[column_mapping['salary']])
                                adds_col = column_mapping.get('allowances')
                                adds_raw = row.get(adds_col, 0) if adds_col else 0
                                adds = float(adds_raw) if pd.notnull(adds_raw) and str(adds_raw).strip() != '' else 0.0
                                
                                deds_col = column_mapping.get('deductions')
                                deds_raw = row.get(deds_col, 0) if deds_col else 0
                                deds = float(deds_raw) if pd.notnull(deds_raw) and str(deds_raw).strip() != '' else 0.0
                                
                                net = base_sal + adds - deds
                                conn.execute('''
                                    INSERT INTO salaries (employee_id, month, year, base_salary, allowances, deductions, net_salary, payment_date)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, DATE('now'))
                                ''', (emp_id, s_month, s_year, base_sal, adds, deds, net))
                        except Exception as e:
                            print(f"Skipped initial salary for row {index}: {e}")
                        success_count += 1
                        
                        # Trigger ADMS Sync
                        try:
                            queue_adms_user_update(emp_id)
                        except Exception as e:
                            print(f"Failed to queue ADMS sync for employee {emp_id}: {e}")

                    except Exception as e:
                        error_count += 1
                        print(f"Error importing row {index}: {e}")
                
                # Managers are linked in a second pass: a manager may appear
                # further down the same sheet than their subordinate, and the
                # link must never create a self-reference or a cycle.
                for _eno, _mno in pending_managers:
                    try:
                        emp_row = conn.execute(
                            'SELECT id FROM employees WHERE employee_number = ?',
                            (_eno,)).fetchone()
                        mgr_row = conn.execute(
                            'SELECT id FROM employees WHERE employee_number = ?',
                            (_mno,)).fetchone()
                        if not emp_row or not mgr_row:
                            row_errors.append(f'{_eno}: '
                                              + gettext('x.mgr_not_found') % {'p0': _mno})
                            continue
                        _err = _validate_manager_chain(conn, emp_row['id'], mgr_row['id'])
                        if _err:
                            row_errors.append(f'{_eno}: {_err}')
                            continue
                        conn.execute('UPDATE employees SET manager_id = ? WHERE id = ?',
                                     (mgr_row['id'], emp_row['id']))
                    except Exception as e:
                        print(f'manager link failed for {_eno}: {e}')

                # CRITICAL: Commit changes to database
                conn.commit()
                
                summary = {
                    'total_rows': total_rows,
                    'inserted': success_count - updated_count,
                    'updated': updated_count,
                    'skipped': error_count,
                    'preview_rows': total_rows if total_rows < 5 else 5,
                    'duplicates_in_file': 0,
                    'detected_headers': list(column_mapping.keys()),
                    'used_mapping': column_mapping,
                    'errors': row_errors[:50]
                }
                
                print(f"Import Summary: Successfully imported {success_count}, failed {error_count}")
                
                if success_count > 0:
                    flash(gettext('x.f_imported_count') % {'p0': f'{success_count}'}, 'success')
                if error_count > 0:
                    flash(gettext('x.f_import_failed_count') % {'p0': f'{error_count}'}, 'warning')
                    
                # return redirect(url_for('employee.employees')) # Old behavior
                return render_template('import_employees.html', summary=summary)
                
            except Exception as e:
                import traceback
                error_traceback = traceback.format_exc()
                print(f"CRITICAL ERROR during file processing:\n{error_traceback}")
                flash(gettext('x.f_file_processing_error') % {'p0': f'{str(e)}'}, 'error')
                return redirect(request.url)
            finally:
                if 'conn' in locals():
                    pass # conn.close() removed to prevent leak in Flask g
    
    return render_template('import_employees.html')

@employee_bp.route('/employees/import/template')
@login_required
@require_permission('employee.create')
def download_template():
    # إنشاء ملف Excel فارغ مع الأعمدة المطلوبة
    # Mandatory first, then everything optional — generated from the same
    # catalogue the importer reads, so template and parser can never drift.
    columns = [
        'Employee Number (Fingerprint ID)', 'Name', 'Department', 'Position',
        'Hire Date', 'Salary',
        'Arabic Name', 'Cost Center', 'Phone', 'Email', 'Address',
        'Shift Name', 'Shift Start Time', 'Shift End Time',
        'Card Number', 'Privilege', 'Password', 'Group ID',
        'Weekend Type', 'Opening Leave Balance',
        'Initial Salary Month', 'Initial Salary Year',
        'Allowances', 'Allowance Start Date', 'Deductions',
        'Manager Employee Number'
    ] + [aliases[0] for _k, _c, aliases, _ex in IMPORT_OPTIONAL_FIELDS]
    df = pd.DataFrame(columns=columns)
    
    # إضافة مثال
    example_row = {
        'Employee Number (Fingerprint ID)': '1001',
        'Name': 'مثال: محمد أحمد',
        'Arabic Name': 'محمد أحمد',
        'Department': 'IT',
        'Position': 'Developer', 
        'Hire Date': '2023-01-01',
        'Salary': 5000,
        'Cost Center': 'CC-001',
        'Phone': '05xxxxxxxx',
        'Email': 'email@example.com',
        'Address': 'Riyadh',
        'Shift Name': 'صباحي',
        'Shift Start Time': '09:00',
        'Shift End Time': '17:00',
        'Card Number': '123456',
        'Privilege': 0,
        'Password': '123',
        'Group ID': '1',
        'Weekend Type': 'جمعة وسبت',
        'Opening Leave Balance': 0,
        'Initial Salary Month': '',
        'Initial Salary Year': '',
        'Allowances': 0,
        'Allowance Start Date': '',
        'Deductions': 0,
        'Manager Employee Number': ''
    }
    for _k, _c, _aliases, _ex in IMPORT_OPTIONAL_FIELDS:
        example_row[_aliases[0]] = _ex
    df.loc[0] = example_row
    
    # حفظ الملف في الذاكرة
    guide = pd.DataFrame([
        {'العمود': 'Employee Number (Fingerprint ID)', 'إلزامي؟': 'نعم',
         'ملاحظة': 'المفتاح الفريد — تكراره في ملف لاحق يعني تحديث الموظف نفسه لا إنشاء نسخة'},
        {'العمود': 'Name', 'إلزامي؟': 'نعم', 'ملاحظة': ''},
        {'العمود': 'Department', 'إلزامي؟': 'نعم', 'ملاحظة': 'يُنشأ تلقائيًا إن لم يكن موجودًا'},
        {'العمود': 'Position', 'إلزامي؟': 'نعم', 'ملاحظة': 'يُنشأ تلقائيًا إن لم يكن موجودًا'},
        {'العمود': 'Hire Date', 'إلزامي؟': 'نعم', 'ملاحظة': 'YYYY-MM-DD'},
        {'العمود': 'Salary', 'إلزامي؟': 'نعم', 'ملاحظة': 'الأساسي فقط، أكبر من صفر'},
        {'العمود': 'Opening Leave Balance', 'إلزامي؟': 'لا',
         'ملاحظة': 'رصيد الإجازات المعتمد عند تاريخ القطع — مهم للشركات القائمة'},
        {'العمود': 'Allowances', 'إلزامي؟': 'لا',
         'ملاحظة': 'يُسجَّل كبند ثابت باسم «بدل مستورد» ويدخل في احتساب الراتب'},
        {'العمود': 'Allowance Start Date', 'إلزامي؟': 'لا',
         'ملاحظة': 'تاريخ سريان البدل — الافتراضي تاريخ التعيين'},
        {'العمود': 'باقي الأعمدة', 'إلزامي؟': 'لا',
         'ملاحظة': 'اتركها فارغة بلا مشكلة؛ الفارغ لا يمسّ القيمة المحفوظة عند التحديث'},
    ])
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Employees')
        guide.to_excel(writer, index=False, sheet_name='دليل الأعمدة')
    output.seek(0)
    
    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name='employees_template.xlsx'
    )

@employee_bp.route('/api/search-employees')
@login_required
@require_permission('employee.view')
def api_search_employees():
    """API للبحث عن الموظفين بالاسم أو رقم الموظف"""
    try:
        query = request.args.get('q', '').strip()
        
        if len(query) < 2:
            return jsonify({
                'success': True,
                'employees': []
            })
        
        conn = get_db_connection()
        
        # البحث بالاسم أو رقم الموظف
        employees = conn.execute('''
            SELECT id, name, employee_number, department, position
            FROM employees 
            WHERE name LIKE ? OR employee_number LIKE ? OR name LIKE ? OR employee_number LIKE ?
            ORDER BY name ASC
            LIMIT 10
        ''', (f'%{query}%', f'%{query}%', f'{query}%', f'{query}%')).fetchall()
        
        pass # conn.close() removed to prevent leak in Flask g
        
        # تحويل النتائج إلى قائمة
        employees_list = []
        for emp in employees:
            employees_list.append({
                'id': emp['id'],
                'name': emp['name'],
                'employee_number': emp['employee_number'],
                'department': emp['department'],
                'position': emp['position']
            })
        
        return jsonify({
            'success': True,
            'employees': employees_list
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        })

@employee_bp.route('/api/employees')
@login_required
@require_permission('employee.view')
def api_employees():
    """API لجلب قائمة الموظفين للبحث"""
    try:
        conn = get_db_connection()
        
        # التحقق من وجود عمود active
        try:
            employees = conn.execute('''
                SELECT id, name, employee_number, department 
                FROM employees 
                WHERE active = 1 
                ORDER BY name
            ''').fetchall()
        except:
            # إذا لم يكن عمود active موجود، جلب جميع الموظفين
            employees = conn.execute('''
                SELECT id, name, employee_number, department 
                FROM employees 
                ORDER BY name
            ''').fetchall()
        
        pass # conn.close() removed to prevent leak in Flask g
        
        return jsonify({
            'success': True,
            'employees': [dict(employee) for employee in employees]
        })
        
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@employee_bp.route('/api/departments')
@login_required
@require_permission('employee.view')
def api_departments():
    """API لجلب الأقسام من جدول departments_master"""
    try:
        conn = get_db_connection()
        try:
            rows = conn.execute('''
                SELECT name FROM departments_master WHERE is_active = 1 ORDER BY name
            ''').fetchall()
            departments = [row['name'] if isinstance(row, sqlite3.Row) else row[0] for row in rows]
        except:
            # Fallback if table issues
            rows = conn.execute('''
                SELECT DISTINCT department 
                FROM employees 
                WHERE department IS NOT NULL AND TRIM(department) <> ''
                ORDER BY department
            ''').fetchall()
            departments = [row['department'] if isinstance(row, sqlite3.Row) else row[0] for row in rows]
            
        # Detailed rows (id + live employee count) for the structure screen;
        # the flat `departments` list is kept because other screens consume it.
        try:
            items = [dict(r) for r in conn.execute('''
                SELECT d.id, d.name,
                       (SELECT COUNT(*) FROM employees e
                        WHERE e.department = d.name) AS employees_count
                FROM departments_master d
                WHERE d.is_active = 1 ORDER BY d.name''').fetchall()]
        except Exception:
            items = []
        pass # conn.close() removed to prevent leak in Flask g
        return jsonify({'success': True, 'departments': departments,
                        'items': items})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@employee_bp.route('/api/positions')
@login_required
@require_permission('employee.view')
def api_positions():
    """API لجلب الوظائف من جدول positions_master"""
    try:
        conn = get_db_connection()
        try:
            rows = conn.execute('''
                SELECT name FROM positions_master WHERE is_active = 1 ORDER BY name
            ''').fetchall()
            positions = [row['name'] if isinstance(row, sqlite3.Row) else row[0] for row in rows]
        except:
             positions = []

        try:
            items = [dict(r) for r in conn.execute('''
                SELECT p.id, p.name,
                       (SELECT COUNT(*) FROM employees e
                        WHERE e.position = p.name) AS employees_count
                FROM positions_master p
                WHERE p.is_active = 1 ORDER BY p.name''').fetchall()]
        except Exception:
            items = []
        pass # conn.close() removed to prevent leak in Flask g
        return jsonify({'success': True, 'positions': positions,
                        'items': items})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@employee_bp.route('/api/departments/add', methods=['POST'])
@login_required
@require_permission('employee.edit')
def add_department_api():
    try:
        data = request.get_json()
        name = data.get('name')
        if not name:
            return jsonify({'success': False, 'message': 'اسم القسم مطلوب'})
            
        conn = get_db_connection()
        try:
            # Check if exists
            exists = conn.execute('SELECT 1 FROM departments_master WHERE name = ?', (name,)).fetchone()
            if exists:
                 return jsonify({'success': False, 'message': 'هذا القسم موجود بالفعل'})

            conn.execute('INSERT INTO departments_master (name, is_active, created_at) VALUES (?, 1, CURRENT_TIMESTAMP)', (name,))
            conn.commit()
            return jsonify({'success': True, 'message': 'تم إضافة القسم بنجاح'})
        except Exception as e:
             return jsonify({'success': False, 'message': str(e)})
        finally:
            pass # conn.close() removed to prevent leak in Flask g
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@employee_bp.route('/api/positions/add', methods=['POST'])
@login_required
@require_permission('employee.edit')
def add_position_api():
    try:
        data = request.get_json()
        name = data.get('name')
        if not name:
            return jsonify({'success': False, 'message': 'اسم المنصب مطلوب'})
            
        conn = get_db_connection()
        try:
            # Check if exists
            exists = conn.execute('SELECT 1 FROM positions_master WHERE name = ?', (name,)).fetchone()
            if exists:
                 return jsonify({'success': False, 'message': 'هذا المنصب موجود بالفعل'})

            conn.execute('INSERT INTO positions_master (name, is_active, created_at) VALUES (?, 1, CURRENT_TIMESTAMP)', (name,))
            conn.commit()
            return jsonify({'success': True, 'message': 'تم إضافة المنصب بنجاح'})
        except Exception as e:
             return jsonify({'success': False, 'message': str(e)})
        finally:
            pass # conn.close() removed to prevent leak in Flask g
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@employee_bp.route('/structure')
@login_required
@require_permission('page.manage_structure')
def manage_structure():
    return render_template('manage_structure.html')

@employee_bp.route('/org-chart')
@login_required
@require_permission('page.org_chart')
def org_chart():
    return render_template('org_chart.html')

def _validate_manager_chain(conn, employee_id, manager_id):
    """Reject a manager assignment that would make an employee their own
    manager or close a reporting cycle. Returns an error message or None."""
    if not manager_id:
        return None
    if employee_id and int(manager_id) == int(employee_id):
        return gettext('x.mgr_self')
    seen = {int(employee_id)} if employee_id else set()
    cur = int(manager_id)
    while cur is not None:
        if cur in seen:
            return gettext('x.mgr_cycle')
        seen.add(cur)
        row = conn.execute('SELECT manager_id FROM employees WHERE id = ?',
                           (cur,)).fetchone()
        cur = int(row['manager_id']) if row and row['manager_id'] else None
    return None


@employee_bp.route('/api/org-chart-data')
@login_required
@require_permission('employee.view')
def org_chart_data():
    """Hierarchy data, sanitised so the chart can always render.

    Three real-world conditions used to break the old version outright:
      * a manager whose service ended — subordinates pointed at a node no
        longer in the dataset, and the whole chart failed to draw;
      * a cycle (A manages B, B manages A) — infinite recursion;
      * an employee set as their own manager.
    Each is detected here, the link is dropped, and the affected employee is
    surfaced as a root with a flag rather than silently vanishing.
    """
    conn = get_db_connection()
    rows = conn.execute("""
        SELECT id, name, arabic_name, employee_number, manager_id,
               position, department
        FROM employees WHERE is_active = 1
        ORDER BY CAST(employee_number AS INTEGER), employee_number""").fetchall()

    people = {r['id']: dict(r) for r in rows}
    issues = []

    for pid, p in people.items():
        mid = p.get('manager_id')
        if mid is None:
            continue
        mid = int(mid)
        if mid == pid:
            p['manager_id'] = None
            p['issue'] = 'self'
            issues.append({'id': pid, 'name': p['name'], 'type': 'self'})
        elif mid not in people:
            p['manager_id'] = None
            p['issue'] = 'missing_manager'
            issues.append({'id': pid, 'name': p['name'],
                           'type': 'missing_manager'})
        else:
            p['manager_id'] = mid

    # Break any cycle by detaching the employee that closes it.
    for pid in list(people):
        seen = {pid}
        cur = people[pid].get('manager_id')
        while cur is not None:
            if cur in seen:
                people[pid]['manager_id'] = None
                people[pid]['issue'] = 'cycle'
                issues.append({'id': pid, 'name': people[pid]['name'],
                               'type': 'cycle'})
                break
            seen.add(cur)
            cur = people[cur].get('manager_id')

    children = {}
    for pid, p in people.items():
        children.setdefault(p.get('manager_id'), []).append(pid)

    def build(pid):
        p = people[pid]
        kids = [build(k) for k in children.get(pid, [])]
        return {'id': pid,
                'name': p['arabic_name'] or p['name'],
                'employee_number': p['employee_number'],
                'position': p['position'] or '',
                'department': p['department'] or '',
                'issue': p.get('issue'),
                'reports': len(kids),
                'children': kids}

    roots = [build(r) for r in children.get(None, [])]
    return jsonify({'success': True, 'roots': roots,
                    'total': len(people), 'issues': issues,
                    'unassigned': len(children.get(None, []))})


@employee_bp.route('/api/employees/dependents', methods=['GET'])
@login_required
@require_permission('employee.view')
def get_dependents():
    employee_id = request.args.get('employee_id')
    if not employee_id:
        return jsonify({"success": False, "message": "Employee ID required"})
    
    conn = get_db_connection()
    deps = conn.execute('SELECT * FROM dependents WHERE employee_id = ?', (employee_id,)).fetchall()
    pass # conn.close() removed to prevent leak in Flask g
    
    return jsonify({"success": True, "data": [dict(d) for d in deps]})

@employee_bp.route('/api/employees/dependents', methods=['POST'])
@login_required
@require_permission('employee.edit')
def add_dependent():
    try:
        data = request.json
        employee_id = data.get('employee_id')
        name = data.get('name')
        relation = data.get('relation')
        
        if not employee_id or not name or not relation:
            return jsonify({"success": False, "message": "Missing required fields"})
            
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO dependents (employee_id, name, relation, dob, gender)
            VALUES (?, ?, ?, ?, ?)
        ''', (employee_id, name, relation, data.get('dob'), data.get('gender')))
        conn.commit()
        
        dep_id = cursor.lastrowid
        try:
            from utils.payroll_engine import log_employee_event
            log_employee_event(conn, int(data['employee_id']),
                               data.get('relation') or 'dependent', '',
                               data.get('name') or '', 'family',
                               user_id=session.get('user_id'),
                               source='dependent_add')
            conn.commit()
        except Exception as _e:
            print(f'audit log failed on dependent add: {_e}')
        new_dep = conn.execute('SELECT * FROM dependents WHERE id = ?', (dep_id,)).fetchone()
        pass # conn.close() removed to prevent leak in Flask g
        
        return jsonify({"success": True, "data": dict(new_dep)})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})

@employee_bp.route('/api/employees/dependents/<int:id>', methods=['DELETE'])
@login_required
@require_permission('employee.edit')
def delete_dependent(id):
    try:
        conn = get_db_connection()
        conn.execute('DELETE FROM dependents WHERE id = ?', (id,))
        conn.commit()
        pass # conn.close() removed to prevent leak in Flask g
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})

# ==========================================
# ASSETS APIs (For Desktop UI)
# ==========================================
@employee_bp.route('/api/employees/assets', methods=['GET'])
@login_required
@require_permission('employee.view')
def get_assets():
    employee_id = request.args.get('employee_id')
    if not employee_id:
        return jsonify({"success": False, "message": "Employee ID required"})
    
    conn = get_db_connection()
    assets = conn.execute('SELECT * FROM assets WHERE employee_id = ?', (employee_id,)).fetchall()
    pass # conn.close() removed to prevent leak in Flask g
    
    return jsonify({"success": True, "data": [dict(a) for a in assets]})

@employee_bp.route('/api/employees/assets', methods=['POST'])
@login_required
@require_permission('employee.edit')
def add_asset():
    try:
        data = request.json
        employee_id = data.get('employee_id')
        asset_name = data.get('asset_name')
        
        if not employee_id or not asset_name:
            return jsonify({"success": False, "message": "Missing required fields"})
            
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO assets (employee_id, asset_name, asset_code, issue_date, return_date, status)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (employee_id, asset_name, data.get('asset_code'), data.get('issue_date'), data.get('return_date'), data.get('status', 'active')))
        conn.commit()
        
        asset_id = cursor.lastrowid
        try:
            from utils.payroll_engine import log_employee_event
            log_employee_event(conn, int(data['employee_id']),
                               data.get('asset_name') or 'asset', '',
                               data.get('asset_code') or gettext('x.asset_held'),
                               'custody', user_id=session.get('user_id'),
                               source='asset_issue',
                               note=data.get('issue_date'))
            conn.commit()
        except Exception as _e:
            print(f'audit log failed on asset issue: {_e}')
        new_asset = conn.execute('SELECT * FROM assets WHERE id = ?', (asset_id,)).fetchone()
        pass # conn.close() removed to prevent leak in Flask g
        
        return jsonify({"success": True, "data": dict(new_asset)})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})

@employee_bp.route('/api/employees/assets/<int:id>', methods=['DELETE'])
@login_required
@require_permission('employee.edit')
def delete_asset():
    # ID passed in route
    pass # Wait, let's fix route

@employee_bp.route('/employees/profile')
@login_required
@require_permission('page.employee_profile')
def employee_profile():
    from utils.payroll_engine import fetch_payroll_employees
    conn = get_db_connection()
    emps = fetch_payroll_employees(conn, active_only=False)
    selected = request.args.get('employee_id', type=int)
    return render_template('employee_profile.html', employees=emps, selected=selected)


@employee_bp.route('/api/employees/profile')
@login_required
@require_permission('employee.view')
def api_employee_profile():
    """One comprehensive, reusable employee dossier: master data, salary
    structure, leave balance + full leave history, loans with payment
    history, the latest EOS settlement if one exists, and attendance for
    a month (current month by default, or ?month=&year= for another).
    Built for reuse — any screen that needs 'everything about employee X'
    calls this one endpoint instead of assembling it from parts."""
    from datetime import datetime as _dt
    from utils.payroll_engine import (fetch_payroll_employees,
                                      fetch_fixed_earnings,
                                      fetch_employee_loans_detail,
                                      compute_employee_days)
    from utils.leave_balance import compute_leave_balance

    id = request.args.get('employee_id', type=int)
    if not id:
        return jsonify({'success': False, 'message': 'employee_id required'}), 400
    conn = get_db_connection()
    rows = fetch_payroll_employees(conn, employee_id=id, active_only=False)
    if not rows:
        return jsonify({'success': False, 'code': 'not_found'}), 404
    emp = dict(rows[0])
    basic = float(emp['salary'] or 0)

    now = _dt.now()
    month = request.args.get('month', type=int) or now.month
    year = request.args.get('year', type=int) or now.year
    attendance = compute_employee_days(conn, id, month, year)

    bal = compute_leave_balance(conn, id)

    leaves = [dict(r) for r in conn.execute("""
        SELECT lr.id, lr.start_date, lr.end_date, lr.days_count,
               lr.leave_duration_type, lr.status, lr.is_paid_leave, lr.reason,
               lt.name AS type_name, lt.is_paid AS type_is_paid
        FROM leave_requests lr
        JOIN leave_types lt ON lt.id = lr.leave_type_id
        WHERE lr.employee_id = ? AND COALESCE(lr.is_deleted, 0) = 0
        ORDER BY lr.start_date DESC""", (id,)).fetchall()]

    by_type = {}
    for lv in leaves:
        if lv['status'] == 'approved' and lv['leave_duration_type'] != 'hourly':
            by_type.setdefault(lv['type_name'], 0.0)
            by_type[lv['type_name']] += float(lv['days_count'] or 0)
    type_summary = [{'name': k, 'days': round(v, 2)} for k, v in by_type.items()]

    loans = fetch_employee_loans_detail(conn, id)
    fixed_earnings = fetch_fixed_earnings(conn, id, basic)

    dependents = [dict(r) for r in conn.execute(
        """SELECT id, name, relation, dob, gender FROM dependents
           WHERE employee_id = ? ORDER BY id""", (id,)).fetchall()]
    assets = [dict(r) for r in conn.execute(
        """SELECT id, asset_name, asset_code, issue_date, return_date, status
           FROM assets WHERE employee_id = ?
           ORDER BY (status != 'active'), id""", (id,)).fetchall()]

    # Field-level change history, newest first, with who made it.
    audit = [dict(r) for r in conn.execute("""
        SELECT a.field, a.old_value, a.new_value, a.category, a.source,
               a.note, a.changed_at,
               COALESCE(u.full_name, u.username, '-') AS changed_by_name
        FROM employee_audit_log a
        LEFT JOIN users u ON u.id = a.changed_by
        WHERE a.employee_id = ?
        ORDER BY a.id DESC LIMIT 200""", (id,)).fetchall()]

    import json as _j
    eos_row = conn.execute("""SELECT * FROM end_of_service_records
                              WHERE employee_id = ?
                              ORDER BY id DESC LIMIT 1""", (id,)).fetchone()
    eos = None
    if eos_row:
        eos = dict(eos_row)
        eos['breakdown'] = _j.loads(eos.pop('breakdown_json', None) or '{}')

    return jsonify({'success': True,
                    'employee': {'id': emp['id'],
                                'employee_number': emp['employee_number'],
                                'name': emp['name'], 'arabic_name': emp['arabic_name'],
                                'department': emp['department'],
                                'position': emp['position'],
                                'hire_date': str(emp['hire_date'] or '')[:10],
                                'end_of_service_date':
                                    str(emp['end_of_service_date'] or '')[:10],
                                'is_active': emp['is_active'],
                                'shift_type': emp.get('shift_type'),
                                'shift_name': emp.get('shift_name'),
                                'weekly_leave_selected_days':
                                    emp.get('weekly_leave_selected_days'),
                                'hours_per_day': emp.get('hours_per_day'),
                                'basic': basic},
                    'salary_structure': {'basic': basic,
                                        'fixed_earnings': fixed_earnings,
                                        'monthly_total':
                                            round(basic + sum(
                                                x['amount'] for x in fixed_earnings), 3)},
                    'leave_balance': bal,
                    'leaves': leaves,
                    'leave_type_summary': type_summary,
                    'loans': loans,
                    'eos': eos,
                    'dependents': dependents,
                    'assets': assets,
                    'audit': audit,
                    'attendance': attendance})


@employee_bp.route('/api/employees/profile/attendance')
@login_required
@require_permission('employee.view')
def api_employee_profile_attendance():
    from utils.payroll_engine import compute_employee_days
    conn = get_db_connection()
    try:
        id = int(request.args.get('employee_id'))
        month = int(request.args.get('month'))
        year = int(request.args.get('year'))
        data = compute_employee_days(conn, id, month, year)
    except (TypeError, ValueError):
        return jsonify({'success': False, 'message': 'bad params'}), 400
    if not data:
        return jsonify({'success': False, 'code': 'not_found'}), 404
    return jsonify({'success': True, **data})


@employee_bp.route('/api/employees/picker')
@login_required
@require_permission('employee.view')
def api_employee_picker():
    """Single source for every employee selector in the system.

    Searches Arabic name, English name, employee number and civil ID at
    once. Always applies the caller's department scope, so a department
    manager can never pick someone outside their department — the scope
    lives here instead of being re-implemented (and forgotten) in each
    screen's own query.

    Query params:
      q            free text across ar/en name, employee_number, national_id
      scope        'active' (default) | 'all' | 'period'
      month, year  required when scope='period' — service-window filtering
      limit        default 50
    """
    from utils.payroll_engine import fetch_payroll_employees, resolve_period
    conn = get_db_connection()
    q = (request.args.get('q') or '').strip()
    scope = (request.args.get('scope') or 'active').strip()
    limit = min(request.args.get('limit', type=int) or 50, 200)

    period = None
    if scope == 'period':
        try:
            period = resolve_period(conn, int(request.args.get('month')),
                                    int(request.args.get('year')))
        except (TypeError, ValueError):
            return jsonify({'success': False, 'message': 'month/year required'}), 400

    rows = fetch_payroll_employees(conn, active_only=(scope == 'active'),
                                   period=period)

    # National IDs in one pass — a per-row lookup would be N+1 queries.
    nid_map = {r['id']: (r['national_id'] or '')
               for r in conn.execute('SELECT id, national_id FROM employees')}

    dept_scope = get_allowed_department_name()
    out = []
    needle = q.lower()
    for r in rows:
        e = dict(r)
        if dept_scope and (e.get('department') or '') != dept_scope:
            continue
        if needle:
            hay = ' '.join(str(e.get(k) or '') for k in
                           ('arabic_name', 'name', 'employee_number')).lower()
            if needle not in hay and needle not in str(nid_map.get(e['id'], '')).lower():
                continue
        out.append({'id': e['id'],
                    'employee_number': e['employee_number'],
                    'name': e['name'],
                    'arabic_name': e['arabic_name'],
                    'department': e.get('department'),
                    'position': e.get('position'),
                    'salary': e.get('salary'),
                    'hire_date': (str(e.get('hire_date'))[:10]
                                  if e.get('hire_date') else None),
                    'is_active': e.get('is_active'),
                    'end_of_service_date': (str(e.get('end_of_service_date'))[:10]
                                            if e.get('end_of_service_date') else None)})
        if len(out) >= limit:
            break
    return jsonify({'success': True, 'employees': out,
                    'scoped_to_department': dept_scope})


def _structure_guard(kind, name):
    """Departments and positions are referenced by NAME on the employee row,
    so a rename must carry the employees with it and a delete must never
    silently orphan anyone. Returns (table, employee_column)."""
    if kind == 'department':
        return 'departments_master', 'department'
    return 'positions_master', 'position'


@employee_bp.route('/api/structure/<kind>/<int:item_id>', methods=['PUT'])
@login_required
@require_permission('employee.edit')
def rename_structure_item(kind, item_id):
    """Rename a department/position and cascade the new name onto every
    employee currently carrying the old one, inside one transaction."""
    if kind not in ('department', 'position'):
        return jsonify({'success': False, 'message': 'bad kind'}), 400
    table, emp_col = _structure_guard(kind, None)
    data = request.get_json(silent=True) or {}
    new_name = (data.get('name') or '').strip()
    if len(new_name) < 2:
        return jsonify({'success': False,
                        'message': gettext('x.st_name_required')}), 400
    conn = get_db_connection()
    row = conn.execute(f'SELECT name FROM {table} WHERE id = ?',
                       (item_id,)).fetchone()
    if not row:
        return jsonify({'success': False, 'code': 'not_found'}), 404
    old_name = row['name']
    if old_name == new_name:
        return jsonify({'success': True, 'moved': 0})
    clash = conn.execute(f'SELECT 1 FROM {table} WHERE name = ? AND id != ?',
                         (new_name, item_id)).fetchone()
    if clash:
        return jsonify({'success': False,
                        'message': gettext('x.st_name_exists')}), 400
    try:
        conn.execute(f'UPDATE {table} SET name = ? WHERE id = ?',
                     (new_name, item_id))
        affected = [r['id'] for r in conn.execute(
            f'SELECT id FROM employees WHERE {emp_col} = ?', (old_name,)).fetchall()]
        cur = conn.execute(
            f'UPDATE employees SET {emp_col} = ? WHERE {emp_col} = ?',
            (new_name, old_name))
        moved = cur.rowcount
        try:
            from utils.payroll_engine import log_employee_event
            for _eid in affected:
                log_employee_event(conn, _eid, emp_col, old_name, new_name,
                                   'org', user_id=session.get('user_id'),
                                   source='structure_rename')
        except Exception as _e:
            print(f'audit log failed on rename: {_e}')
        conn.commit()
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500
    return jsonify({'success': True, 'moved': moved,
                    'old_name': old_name, 'new_name': new_name})


@employee_bp.route('/api/structure/<kind>/<int:item_id>', methods=['DELETE'])
@login_required
@require_permission('employee.edit')
def delete_structure_item(kind, item_id):
    """Delete a single department/position. Refused while any employee still
    references it — reassign them first; an orphaned employee row breaks
    department-scoped permissions and payroll filtering."""
    if kind not in ('department', 'position'):
        return jsonify({'success': False, 'message': 'bad kind'}), 400
    table, emp_col = _structure_guard(kind, None)
    conn = get_db_connection()
    row = conn.execute(f'SELECT name FROM {table} WHERE id = ?',
                       (item_id,)).fetchone()
    if not row:
        return jsonify({'success': False, 'code': 'not_found'}), 404
    used = conn.execute(
        f'SELECT COUNT(*) AS n FROM employees WHERE {emp_col} = ?',
        (row['name'],)).fetchone()['n']
    if used:
        return jsonify({'success': False, 'code': 'in_use', 'count': used,
                        'message': gettext('x.st_in_use') % {'p0': used}}), 409
    conn.execute(f'DELETE FROM {table} WHERE id = ?', (item_id,))
    conn.commit()
    return jsonify({'success': True})


@employee_bp.route('/api/employees/assets/<int:id>/return', methods=['POST'])
@login_required
@require_permission('employee.edit')
def return_asset(id):
    """Mark custody as returned instead of deleting the row.

    Deleting erases the fact that the employee ever held the item, which is
    exactly what a custody record exists to prove. Returning keeps the
    history and stops it counting against the employee at clearance.
    """
    conn = get_db_connection()
    row = conn.execute('SELECT * FROM assets WHERE id = ?', (id,)).fetchone()
    if not row:
        return jsonify({'success': False, 'code': 'not_found'}), 404
    if row['status'] == 'returned':
        return jsonify({'success': False, 'code': 'already_returned'}), 400
    data = request.get_json(silent=True) or {}
    return_date = (data.get('return_date') or '').strip()
    try:
        datetime.strptime(return_date, '%Y-%m-%d')
    except ValueError:
        return jsonify({'success': False,
                        'message': gettext('x.f_end_date_required')}), 400
    if row['issue_date'] and return_date < str(row['issue_date'])[:10]:
        return jsonify({'success': False,
                        'message': gettext('x.f_end_before_start')}), 400
    conn.execute("UPDATE assets SET status = 'returned', return_date = ? "
                 "WHERE id = ?", (return_date, id))
    try:
        from utils.payroll_engine import log_employee_event
        log_employee_event(conn, row['employee_id'], row['asset_name'],
                           gettext('x.asset_held'), gettext('x.asset_returned'),
                           'custody', user_id=session.get('user_id'),
                           source='asset_return', note=return_date)
    except Exception as e:
        print(f'audit log failed on asset return: {e}')
    conn.commit()
    return jsonify({'success': True})
