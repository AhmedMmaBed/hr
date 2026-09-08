from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_babel import gettext
from utils.db import get_db_connection
from utils.auth import login_required
from utils.rbac import require_permission

leave_settings_bp = Blueprint('leave_settings', __name__)

@leave_settings_bp.route('/leave_settings')
@login_required
@require_permission('page.contract')
def index():
    conn = get_db_connection()
    leave_types = conn.execute("SELECT * FROM leave_types ORDER BY id").fetchall()
    
    # Get tiers for each leave type
    types_with_tiers = []
    for lt in leave_types:
        tiers = conn.execute("SELECT * FROM leave_tiers WHERE leave_type_id = ? ORDER BY start_day", (lt['id'],)).fetchall()
        types_with_tiers.append({
            'type': lt,
            'tiers': tiers
        })
        
    pass # conn.close() removed to prevent leak in Flask g
    return render_template('leave_settings.html', leave_types=types_with_tiers)

@leave_settings_bp.route('/leave_settings/update', methods=['POST'])
@login_required
@require_permission('leave.settings')
def update_leave_type():
    try:
        type_id = request.form['type_id']
        days_per_year = request.form['days_per_year']
        # Handle checkboxes (if unchecked, they don't appear in form)
        is_paid = 1 if 'is_paid' in request.form else 0
        requires_approval = 1 if 'requires_approval' in request.form else 0
        is_carry_over = 1 if 'is_carry_over' in request.form else 0
        service_months_required = request.form.get('service_months_required', 0)
        
        requires_attachment = 1 if 'requires_attachment' in request.form else 0
        is_hourly_permission = 1 if 'is_hourly_permission' in request.form else 0
        allow_half_day = 1 if 'allow_half_day' in request.form else 0
        allow_hourly = 1 if 'allow_hourly' in request.form else 0
        monthly_hours_allowance = request.form.get('monthly_hours_allowance', 0)
        
        conn = get_db_connection()
        conn.execute('''
            UPDATE leave_types 
            SET days_per_year = ?, is_paid = ?, requires_approval = ?, is_carry_over = ?, service_months_required = ?,
                requires_attachment = ?, is_hourly_permission = ?, allow_half_day = ?, allow_hourly = ?, monthly_hours_allowance = ?
            WHERE id = ?
        ''', (days_per_year, is_paid, requires_approval, is_carry_over, service_months_required,
              requires_attachment, is_hourly_permission, allow_half_day, allow_hourly, monthly_hours_allowance, type_id))
        conn.commit()
        pass # conn.close() removed to prevent leak in Flask g
        
        flash(gettext('x.f_leave_type_updated'), 'success')
    except Exception as e:
        flash(gettext('x.f_error_colon') % {'p0': f'{e}'}, 'error')
        
    return redirect(url_for('leave_settings.index'))

@leave_settings_bp.route('/leave_settings/add_tier', methods=['POST'])
@login_required
@require_permission('leave.settings')
def add_tier():
    try:
        leave_type_id = request.form['leave_type_id']
        start_day = request.form['start_day']
        end_day = request.form['end_day']
        payment_percentage = float(request.form['payment_percentage']) / 100.0 # Input is 100, 75 etc
        
        conn = get_db_connection()
        conn.execute('''
            INSERT INTO leave_tiers (leave_type_id, start_day, end_day, payment_percentage)
            VALUES (?, ?, ?, ?)
        ''', (leave_type_id, start_day, end_day, payment_percentage))
        conn.commit()
        pass # conn.close() removed to prevent leak in Flask g
        
        flash(gettext('x.f_tier_added'), 'success')
    except Exception as e:
        flash(gettext('x.f_error_colon') % {'p0': f'{e}'}, 'error')
        
    return redirect(url_for('leave_settings.index'))

@leave_settings_bp.route('/leave_settings/delete_tier/<int:tier_id>', methods=['POST'])
@login_required
@require_permission('leave.settings')
def delete_tier(tier_id):
    try:
        conn = get_db_connection()
        conn.execute('DELETE FROM leave_tiers WHERE id = ?', (tier_id,))
        conn.commit()
        pass # conn.close() removed to prevent leak in Flask g
        flash(gettext('x.f_tier_deleted'), 'success')
    except Exception as e:
        flash(gettext('x.f_error_colon') % {'p0': f'{e}'}, 'error')
        
    return redirect(url_for('leave_settings.index'))


@leave_settings_bp.route('/leave_settings/add_type', methods=['POST'])
@login_required
@require_permission('leave.settings')
def add_leave_type():
    try:
        name = request.form['name']
        days_per_year = request.form.get('days_per_year', 0)
        
        # Defaults
        is_paid = 1 if 'is_paid' in request.form else 0
        requires_approval = 1 if 'requires_approval' in request.form else 0 
        
        requires_attachment = 1 if 'requires_attachment' in request.form else 0
        is_hourly_permission = 1 if 'is_hourly_permission' in request.form else 0
        allow_half_day = 1 if 'allow_half_day' in request.form else 0
        allow_hourly = 1 if 'allow_hourly' in request.form else 0
        monthly_hours_allowance = request.form.get('monthly_hours_allowance', 0)
        
        conn = get_db_connection()
        conn.execute('''
            INSERT INTO leave_types (name, days_per_year, is_paid, requires_approval, 
                                     requires_attachment, is_hourly_permission, allow_half_day, allow_hourly, monthly_hours_allowance)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (name, days_per_year, is_paid, requires_approval, 
              requires_attachment, is_hourly_permission, allow_half_day, allow_hourly, monthly_hours_allowance))
        conn.commit()
        pass # conn.close() removed to prevent leak in Flask g
        
        flash(gettext('x.f_leave_type_added'), 'success')
    except Exception as e:
        flash(gettext('x.f_error_colon') % {'p0': f'{e}'}, 'error')
        
    return redirect(url_for('leave_settings.index'))

@leave_settings_bp.route('/leave_settings/delete_type/<int:type_id>', methods=['POST'])
@login_required
@require_permission('leave.settings')
def delete_leave_type(type_id):
    try:
        conn = get_db_connection()
        # Check usage first?
        count = conn.execute("SELECT COUNT(*) FROM leave_requests WHERE leave_type_id = ?", (type_id,)).fetchone()[0]
        if count > 0:
            flash(gettext('x.f_leave_type_in_use') % {'p0': f'{count}'}, 'warning')
        else:
            conn.execute("DELETE FROM leave_types WHERE id = ?", (type_id,))
            conn.execute("DELETE FROM leave_tiers WHERE leave_type_id = ?", (type_id,))
            conn.commit()
            flash(gettext('x.f_leave_type_deleted'), 'success')
        pass # conn.close() removed to prevent leak in Flask g
    except Exception as e:
        flash(gettext('x.f_error_colon') % {'p0': f'{e}'}, 'error')
        
    return redirect(url_for('leave_settings.index'))
