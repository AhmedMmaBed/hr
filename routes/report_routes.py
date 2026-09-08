from flask import Blueprint, render_template, request, jsonify
from datetime import date, datetime, timedelta
import calendar
import math
from utils.db import get_db_connection
from utils.db import get_db_connection
from utils.auth import login_required, get_allowed_department_name
from utils.rbac import require_permission
from utils.fingerprint_utils import get_sync_logs
from utils.salary_utils import calculate_salary_for_employee, get_late_arrival_alerts
from smart_reports import SmartFingerprintReport

report_bp = Blueprint('report', __name__)

@report_bp.route('/reports')
@login_required
@require_permission('page.reports')
def reports():
    return render_template('general_reports.html')

@report_bp.route('/fingerprint/logs/api')
@login_required
@require_permission('report.view')
def get_logs_api():
    logs = get_sync_logs(limit=200)
    return jsonify({'logs': logs})

@report_bp.route('/fingerprint/logs')
@login_required
@require_permission('attendance.view')
def fingerprint_sync_logs():
    page = request.args.get('page', 1, type=int)
    per_page = 50
    offset = (page - 1) * per_page
    
    # Filters
    device_filter = request.args.get('device', '')
    level_filter = request.args.get('level', '')
    date_filter = request.args.get('date', '')
    
    conn = get_db_connection()
    
    try:
        # Base Query
        query = "SELECT * FROM sync_logs WHERE 1=1"
        count_query = "SELECT COUNT(*) as count FROM sync_logs WHERE 1=1"
        params = []
        
        if device_filter:
            query += " AND device_ip = ?"
            count_query += " AND device_ip = ?"
            params.append(device_filter)
            
        if level_filter:
            query += " AND level = ?"
            count_query += " AND level = ?"
            params.append(level_filter)
            
        if date_filter:
            query += " AND date(timestamp) = ?"
            count_query += " AND date(timestamp) = ?"
            params.append(date_filter)
            
        # Get Total Count
        total_logs = conn.execute(count_query, params).fetchone()['count']
        total_pages = math.ceil(total_logs / per_page)
        
        # Get Paginated Logs
        query += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        params.extend([per_page, offset])
        logs_rows = conn.execute(query, params).fetchall()
        logs = [dict(row) for row in logs_rows]
        
        # Get Devices for Filter
        devices_rows = conn.execute("SELECT DISTINCT device_ip FROM fingerprint_devices WHERE is_active = 1").fetchall()
        devices = [dict(row) for row in devices_rows]
        
        # Get Stats (Overall)
        # Note: These stats respect the current filters to be more relevant? 
        # Usually dashboard stats are global or filtered. The template implies global or filtered.
        # Let's make them respect filters for "current view" stats, or global?
        # The template has "Total Logs", "Info", "Warning", "Error". 
        # Let's calculate them based on current filters to be dynamic.
        
        # We need separate counts for levels based on current filters (excluding level filter itself if we want global breakdown?)
        # For simplicity and performance, let's do global stats if no filters, or filtered stats if filtered.
        # Actually, let's do simple aggregation on the filtered set logic.
        
        # Re-using the base WHERE clause for stats
        stats_query = """
            SELECT 
                COUNT(*) as total_logs,
                SUM(CASE WHEN level = 'INFO' THEN 1 ELSE 0 END) as info_count,
                SUM(CASE WHEN level = 'WARNING' THEN 1 ELSE 0 END) as warning_count,
                SUM(CASE WHEN level = 'ERROR' THEN 1 ELSE 0 END) as error_count
            FROM sync_logs WHERE 1=1
        """
        stats_params = []
        
        # Apply filters to stats too (except level, maybe? No, apply all to see "Errors in this date")
        if device_filter:
            stats_query += " AND device_ip = ?"
            stats_params.append(device_filter)
        if date_filter:
            stats_query += " AND date(timestamp) = ?"
            stats_params.append(date_filter)

        # Execute Stats Query
        stats = conn.execute(stats_query, stats_params).fetchone()
        
        pass # conn.close() removed to prevent leak in Flask g
        
        return render_template('fingerprint_logs.html', 
            logs=logs, 
            current_page=page, 
            per_page=per_page,
            total_pages=total_pages,
            total_logs=total_logs,
            devices=devices,
            stats=stats,
            filters={
                'device': device_filter,
                'level': level_filter,
                'date': date_filter
            }
        )
        
    except Exception as e:
        if conn:
            pass # conn.close() removed to prevent leak in Flask g
        return f"Error: {str(e)}"

@report_bp.route('/reports/detailed_salary')
@login_required
@require_permission('salary.view')
def detailed_salary_report():
    month = request.args.get('month', date.today().month, type=int)
    year = request.args.get('year', date.today().year, type=int)
    
    conn = get_db_connection()
    salaries = conn.execute('''
        SELECT s.*, e.name, e.arabic_name, e.employee_number 
        FROM salaries s
        JOIN employees e ON s.employee_id = e.id
        WHERE s.month = ? AND s.year = ?
        ORDER BY s.id DESC
    ''', (month, year)).fetchall()
    
    salaries_list = []
    for s in salaries:
        s_dict = dict(s)
        # Fetch Leave Deduction Details
        details = conn.execute('''
            SELECT sld.*, lt.name as leave_type
            FROM salary_leave_details sld
            LEFT JOIN leave_types lt ON sld.leave_type_id = lt.id
            WHERE sld.salary_id = ?
        ''', (s['id'],)).fetchall()
        
        s_dict['leave_details'] = [dict(d) for d in details]
        salaries_list.append(s_dict)
        
    pass # conn.close() removed to prevent leak in Flask g
    return render_template('reports/salary_deductions_report.html', salaries=salaries_list, current_month=month, current_year=year)


@report_bp.route('/fingerprint/smart_reports')
@login_required
@require_permission('report.view')
def smart_reports_page():
    return render_template('fingerprint_smart_reports.html')

@report_bp.route('/fingerprint/smart_daily')
@login_required
@require_permission('report.view')
def smart_reports_daily():
    target_date = request.args.get('date', date.today().strftime('%Y-%m-%d'))
    reporter = SmartFingerprintReport()
    report = reporter.get_daily_report(target_date)
    
    # حساب الإحصائيات لليوم
    stats = {
        'total_employees': len(report),
        'present': len([r for r in report if r['status'] == 'حاضر']),
        'absent': len([r for r in report if r['status'] == 'غائب']),
        'partial': len([r for r in report if r['status'] not in ['حاضر', 'غائب']])
    }
    
    return render_template('fingerprint_smart_daily.html', 
                         report=report, 
                         stats=stats,
                         target_date=target_date)

@report_bp.route('/fingerprint/smart_daily/api')
@login_required
@require_permission('report.view')
def smart_reports_daily_api():
    try:
        target_date = request.args.get('date', date.today().strftime('%Y-%m-%d'))
        reporter = SmartFingerprintReport()
        report = reporter.get_daily_report(target_date)
        return jsonify({
            'success': True,
            'data': report
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        })

@report_bp.route('/fingerprint/smart_monthly')
@login_required
@require_permission('report.view')
def smart_reports_monthly():
    today = date.today()
    month = int(request.args.get('month', today.month))
    year = int(request.args.get('year', today.year))
    
    reporter = SmartFingerprintReport()
    report_data = reporter.get_monthly_report(year, month) # Note: class method signature is (year, month)
    
    return render_template('fingerprint_smart_monthly.html',
                         summary=report_data['summary'],
                         daily_data=report_data['daily_data'],
                         month=month,
                         year=year)

@report_bp.route('/fingerprint/smart_range')
@login_required
@require_permission('report.view')
def smart_reports_range():
    start_date = request.args.get('start', date.today().strftime('%Y-%m-%d'))
    end_date = request.args.get('end', date.today().strftime('%Y-%m-%d'))
    q = request.args.get('q', '').strip()
    dept = request.args.get('dept', '').strip()
    
    reporter = SmartFingerprintReport()
    report_data = reporter.get_range_report(start_date, end_date, name_query=q, department=dept)
    
    return render_template('fingerprint_smart_range.html',
                         rows=report_data['rows'],
                         summary=report_data['summary'],
                         employees_summary=report_data['employees_summary'],
                         weekly_summaries=report_data['weekly_summaries'],
                         week_labels=report_data['week_labels'],
                         start_date=start_date,
                         end_date=end_date,
                         q=q,
                         dept=dept)

@report_bp.route('/fingerprint/presence_daily')
@login_required
@require_permission('report.view')
def presence_daily_report():
    target_date = request.args.get('date', date.today().strftime('%Y-%m-%d'))
    
    reporter = SmartFingerprintReport()
    report = reporter.get_presence_daily_report(target_date)
    
    return render_template('fingerprint_presence_daily.html', 
                         report=report,
                         target_date=target_date)

@report_bp.route('/fingerprint/attendance_summary')
@report_bp.route('/fingerprint/attendance') # Alias for compatibility
@login_required
@require_permission('report.view')
def fingerprint_attendance():
    """ملخص الحضور والانصراف (الشبكة الشهرية) - عرض الهيكل فقط"""
    today = date.today()
    try:
        month = int(request.args.get('month', today.month))
        year = int(request.args.get('year', today.year))
    except (ValueError, TypeError):
        month = today.month
        year = today.year
        
    if not (1 <= month <= 12):
        month = today.month
    if not (2000 <= year <= 2100):
        year = today.year
        
    days_in_month = calendar.monthrange(year, month)[1]
    month_name = calendar.month_name[month]
    
    # Render template immediately with basic context but NO heavy data
    return render_template('attendance_summary.html', 
                         rows=[], # Empty for async load
                         stats={}, 
                         month=month,
                         year=year,
                         month_name=month_name,
                         days_in_month=days_in_month,
                         q=request.args.get('q', ''),
                         dept=request.args.get('dept', ''))

@report_bp.route('/api/fingerprint/attendance_summary')
@login_required
@require_permission('report.view')
def fingerprint_attendance_api():
    """API endpoint for calculating attendance summary"""
    try:
        today = date.today()
        try:
            month = int(request.args.get('month', today.month))
            year = int(request.args.get('year', today.year))
        except ValueError:
            month = today.month
            year = today.year
            
        q = request.args.get('q', '').strip()
        dept_filter = request.args.get('dept', '').strip()
        
        allowed_dept = get_allowed_department_name()
        if allowed_dept:
            dept_filter = allowed_dept
            
        
        # 1. استخدام SmartFingerprintReport لجلب البيانات
        reporter = SmartFingerprintReport()
        monthly_report = reporter.get_monthly_report(year, month)
        summary_data = monthly_report['summary']
        monthly_daily_data = monthly_report['daily_data']
        
        days_in_month = calendar.monthrange(year, month)[1]
        
        # جلب العطلات الرسمية للشهر
        conn_h = get_db_connection()
        month_str = f"{year:04d}-{month:02d}"
        holidays_rows = conn_h.execute("SELECT date, name FROM official_holidays WHERE strftime('%Y-%m', date) = ?", (month_str,)).fetchall()
        conn_h.close()
        holidays_map = {row['date']: row['name'] for row in holidays_rows}
        
        # 2. تحضير قائمة الموظفين
        employees_map = {}
        conn = get_db_connection()
        all_emps_rows = conn.execute('SELECT id, name, arabic_name, employee_number, department, position, cost_center FROM employees ORDER BY CAST(employee_number AS INTEGER), name').fetchall()
        pass # conn.close() removed to prevent leak in Flask g
        
        for emp in all_emps_rows:
            if q:
                term = q.lower()
                if term not in emp['name'].lower() and term not in str(emp['employee_number']):
                    continue
            if dept_filter and dept_filter != emp['department']:
                continue
                
            employees_map[emp['id']] = {
                'employee_id': emp['id'],
                'name': emp['name'],
                'arabic_name': emp['arabic_name'],
                'employee_number': emp['employee_number'],
                'department': emp['department'],
                'position': emp['position'],
                'cost_center': emp['cost_center'] or '',
                'present_count': 0,
                'cells_map': {}
            }
        
        # ملء البيانات
        for date_key, records in monthly_daily_data.items():
            day_int = int(date_key.split('-')[-1])
            for record in records:
                emp_id = record['employee_id']
                if emp_id not in employees_map:
                    continue 
                
                status = 'absent'
                if record['status'] == 'حاضر':
                    status = 'present'
                elif record['status'] in ['دخول فقط', 'خروج فقط']:
                    status = 'present'
                elif 'عطلة رسمية' in record['status']:
                    status = 'holiday'
                elif 'إجازة أسبوعية' in record['status']:
                    status = 'weekly_off'
                
                tooltip_html = f"<strong>{record['date']}</strong><br>"
                tooltip_html += f"<strong>الشفت:</strong> {record['scheduled_start']} - {record['scheduled_end']}<br>"
                
                if status == 'present':
                    tooltip_html += f"<strong>دخول:</strong> {record['checkin_time']}<br>"
                    tooltip_html += f"<strong>خروج:</strong> {record['checkout_time']}<br>"
                    if not record.get('is_flexible'):
                        tooltip_html += f"<strong>ساعات:</strong> {record['work_hours']}<br>"
                    
                    if record.get('is_flexible'):
                         tooltip_html += f"<span class='text-success'><strong>شفت حر/مرن</strong></span><br>"
                    elif record.get('raw_late_minutes', 0) > 0:
                        tooltip_html += f"<div class='mt-1 border-top pt-1'>"
                        tooltip_html += f"<strong>تأخير البصمة:</strong> {record['raw_late_minutes']}د<br>"
                        tooltip_html += f"<strong>فترة السماح:</strong> {record['grace_minutes']}د<br>"
                        if record['late_minutes'] > 0:
                            tooltip_html += f"<span class='text-danger'><strong>صافي التأخير:</strong> {record['late_minutes']}د</span>"
                        else:
                            tooltip_html += f"<span class='text-success'><strong>صافي التأخير:</strong> 0د (ضمن السماح)</span>"
                        tooltip_html += f"</div>"
                elif status == 'holiday':
                    tooltip_html += f"<span class='text-primary'>{record['status']}</span>"
                elif status == 'weekly_off':
                    tooltip_html += f"<span class='text-secondary'>{record['status']}</span>"
                else:
                    tooltip_html += "<span class='text-danger'>غائب</span>"

                cell = {
                    'date': date_key,
                    'status': status,
                    'single_punch': record['status'] in ['دخول فقط', 'خروج فقط'] and not record.get('is_flexible'),
                    'late_after_grace': record['late_minutes'] > 0 and not record.get('is_flexible'),
                    'tooltip_html': tooltip_html
                }
                employees_map[emp_id]['cells_map'][day_int] = cell

        # 3. تحويل map إلى rows مرتبة
        rows = []
        weekends = {4, 5} # Friday, Saturday
        
        for emp_id, emp_data in employees_map.items():
            cells = []
            for d in range(1, days_in_month + 1):
                current_date = date(year, month, d)
                current_date_str = current_date.strftime('%Y-%m-%d')
                
                cell = emp_data['cells_map'].get(d)
                if not cell:
                    day_status = 'absent'
                    tooltip = f"<strong>{current_date}</strong><br>غائب"
                    
                    if current_date_str in holidays_map:
                        day_status = 'holiday'
                        tooltip = f"<strong>{current_date}</strong><br>عطلة رسمية: {holidays_map[current_date_str]}"
                    elif current_date.weekday() in weekends:
                        day_status = 'weekly_off'
                        tooltip = f"<strong>{current_date}</strong><br>عطلة أسبوعية"
                    
                    cell = {
                        'date': current_date_str,
                        'status': day_status,
                        'single_punch': False,
                        'late_after_grace': False,
                        'tooltip_html': tooltip
                    }
                cells.append(cell)
            
            emp_data['cells'] = cells
            # remove temp key
            del emp_data['cells_map'] 
            rows.append(emp_data)
        
        def _sort_key(x):
            try:
                return int(x.get('employee_number', 0))
            except (ValueError, TypeError):
                return 999999
        rows.sort(key=_sort_key)
        
        # Helper to strict sanitize float/NaN for JSON
        def safe_json(obj):
            if isinstance(obj, float):
                if obj != obj: # NaN
                    return 0
                if obj == float('inf') or obj == float('-inf'):
                    return 0
            return obj

        # Recursive sanitization (simple version for our known structure)
        # Or just sanitize specific fields we know might be float
        for r in rows:
            for c in r['cells']:
                # Ensure no complex objects
                pass
                
        # Use simplejson if available or ensure primitives
        # The structure is simple enough, but let's be safe about Flask's jsonify
        # wrapping it in a try-catch for the JSON dump specifically
        
        return jsonify({
            'success': True,
            'rows': rows,
            'stats': summary_data,
            'days_in_month': days_in_month
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'message': f"Server Error: {str(e)}"}), 500


@report_bp.route('/fingerprint/attendance_new')
@login_required
@require_permission('report.view')
def fingerprint_attendance_new():
    today = date.today()
    start_date = request.args.get('start_date', today.replace(day=1).strftime('%Y-%m-%d'))
    end_date = request.args.get('end_date', today.strftime('%Y-%m-%d'))
    try:
        start_d = datetime.strptime(start_date, '%Y-%m-%d').date()
        end_d = datetime.strptime(end_date, '%Y-%m-%d').date()
    except ValueError:
        start_date = today.replace(day=1).strftime('%Y-%m-%d')
        end_date = today.strftime('%Y-%m-%d')
        start_d = today.replace(day=1)
        end_d = today
    days_diff = (end_d - start_d).days + 1
    if days_diff < 1 or days_diff > 365:
        days_diff = 1
    days_names = []
    arabic_days = ['اثنين', 'ثلاثاء', 'أربعاء', 'خميس', 'جمعة', 'سبت', 'أحد']
    for i in range(days_diff):
        d_date = start_d + timedelta(days=i)
        days_names.append((d_date.strftime('%Y-%m-%d'), arabic_days[d_date.weekday()]))
    return render_template('attendance_new.html', start_date=start_date, end_date=end_date, days_diff=days_diff, days_names=days_names)

@report_bp.route('/fingerprint/custom_attendance_summary')
@login_required
@require_permission('report.view')
def custom_fingerprint_attendance():
    """ملخص الحضور المخصص بناء على نطاق تاريخ (الشبكة) - عرض الهيكل فقط"""
    today = date.today()
    start_date = request.args.get('start_date', today.replace(day=1).strftime('%Y-%m-%d'))
    end_date = request.args.get('end_date', today.strftime('%Y-%m-%d'))
    
    try:
        start_d = datetime.strptime(start_date, '%Y-%m-%d').date()
        end_d = datetime.strptime(end_date, '%Y-%m-%d').date()
    except ValueError:
        start_date = today.replace(day=1).strftime('%Y-%m-%d')
        end_date = today.strftime('%Y-%m-%d')
        start_d = today.replace(day=1)
        end_d = today
        
    days_diff = (end_d - start_d).days + 1
    if days_diff < 1 or days_diff > 365: # Hard limit for performance
        days_diff = 1
        
    # Generate days list for header
    days_names = []
    arabic_days = ['اثنين', 'ثلاثاء', 'أربعاء', 'خميس', 'جمعة', 'سبت', 'أحد']
    for i in range(days_diff):
        d_date = start_d + timedelta(days=i)
        days_names.append((d_date.strftime('%Y-%m-%d'), arabic_days[d_date.weekday()]))
    
    return render_template('custom_attendance_summary.html', 
                         rows=[], # Empty for async load
                         stats={}, 
                         start_date=start_date,
                         end_date=end_date,
                         days_names=days_names,
                         days_diff=days_diff,
                         q=request.args.get('q', ''),
                         dept=request.args.get('dept', ''))

@report_bp.route('/api/fingerprint/custom_attendance_summary')
@login_required
@require_permission('report.view')
def custom_fingerprint_attendance_api():
    """API endpoint for calculating custom attendance summary"""
    try:
        today = date.today()
        start_date = request.args.get('start_date', today.replace(day=1).strftime('%Y-%m-%d'))
        end_date = request.args.get('end_date', today.strftime('%Y-%m-%d'))
        
        try:
            start_d = datetime.strptime(start_date, '%Y-%m-%d').date()
            end_d = datetime.strptime(end_date, '%Y-%m-%d').date()
        except ValueError:
            return jsonify({'success': False, 'message': 'Invalid dates'}), 400
            
        days_diff = (end_d - start_d).days + 1
        if days_diff < 1 or days_diff > 365:
            return jsonify({'success': False, 'message': 'Date range must be between 1 and 365 days'}), 400
            
        q = request.args.get('q', '').strip()
        dept_filter = request.args.get('dept', '').strip()
        
        allowed_dept = get_allowed_department_name()
        if allowed_dept:
            dept_filter = allowed_dept
            
        
        reporter = SmartFingerprintReport()
        monthly_report = reporter.get_custom_date_range_report(start_date, end_date)
        summary_data = monthly_report['summary']
        monthly_daily_data = monthly_report['daily_data']
        
        # جلب العطلات الرسمية للشهر
        conn_h = get_db_connection()
        holidays_rows = conn_h.execute("SELECT date, name FROM official_holidays WHERE date BETWEEN ? AND ?", (start_date, end_date)).fetchall()
        conn_h.close()
        holidays_map = {row['date']: row['name'] for row in holidays_rows}
        
        # 2. تحضير قائمة الموظفين
        employees_map = {}
        conn = get_db_connection()
        all_emps_rows = conn.execute('SELECT id, name, arabic_name, employee_number, department, position, cost_center FROM employees ORDER BY CAST(employee_number AS INTEGER), name').fetchall()
        pass # conn.close() removed to prevent leak in Flask g
        
        for emp in all_emps_rows:
            if q:
                term = q.lower()
                if term not in emp['name'].lower() and term not in str(emp['employee_number']):
                    continue
            if dept_filter and dept_filter != emp['department']:
                continue
                
            employees_map[emp['id']] = {
                'employee_id': emp['id'],
                'name': emp['name'],
                'arabic_name': emp['arabic_name'],
                'employee_number': emp['employee_number'],
                'department': emp['department'],
                'position': emp['position'],
                'cost_center': emp['cost_center'] or '',
                'present_count': 0,
                'cells_map': {}
            }
        
        # ملء البيانات
        for date_key, records in monthly_daily_data.items():
            for record in records:
                emp_id = record['employee_id']
                if emp_id not in employees_map:
                    continue
                    
                status = 'absent'
                if record['status'] == 'حاضر':
                    status = 'present'
                    employees_map[emp_id]['present_count'] += 1
                elif 'دخول فقط' in record['status'] or 'خروج فقط' in record['status']:
                    status = 'partial'
                elif 'عطلة رسمية' in record['status']:
                    status = 'holiday'
                elif 'عطلة أسبوعية' in record['status']:
                    status = 'weekly_off'

                tooltip_html = f"<strong>{date_key}</strong><br>"
                if status == 'present' or status == 'partial':
                    tooltip_html += f"<strong>الحالة:</strong> {record['status']}<br>"
                    tooltip_html += f"<strong>دخول:</strong> {record.get('checkin_time', '--')}<br>"
                    tooltip_html += f"<strong>خروج:</strong> {record.get('checkout_time', '--')}<br>"
                    if record.get('late_minutes', 0) > 0 or record.get('raw_late_minutes', 0) > 0:
                        tooltip_html += f"<hr class='my-1'>"
                        tooltip_html += f"<div class='small'>"
                        tooltip_html += f"<strong>تأخير البصمة:</strong> {record.get('raw_late_minutes', 0)}د<br>"
                        tooltip_html += f"<strong>فترة السماح:</strong> {record.get('grace_minutes', 0)}د<br>"
                        if record.get('late_minutes', 0) > 0:
                            tooltip_html += f"<span class='text-danger'><strong>صافي التأخير:</strong> {record.get('late_minutes', 0)}د</span>"
                        else:
                            tooltip_html += f"<span class='text-success'><strong>صافي التأخير:</strong> 0د (ضمن السماح)</span>"
                        tooltip_html += f"</div>"
                elif status == 'holiday':
                    tooltip_html += f"<span class='text-primary'>{record['status']}</span>"
                elif status == 'weekly_off':
                    tooltip_html += f"<span class='text-secondary'>{record['status']}</span>"
                else:
                    tooltip_html += "<span class='text-danger'>غائب</span>"

                cell = {
                    'date': date_key,
                    'status': status,
                    'single_punch': record['status'] in ['دخول فقط', 'خروج فقط'] and not record.get('is_flexible'),
                    'late_after_grace': record.get('late_minutes', 0) > 0 and not record.get('is_flexible'),
                    'tooltip_html': tooltip_html,
                    'check_in': record.get('checkin_time', ''),
                    'check_out': record.get('checkout_time', ''),
                    'work_hours': record.get('work_hours', 0),
                    'late_minutes': record.get('late_minutes', 0)
                }
                employees_map[emp_id]['cells_map'][date_key] = cell

        # 3. تحويل map إلى rows مرتبة
        rows = []
        weekends = {4, 5} # Friday, Saturday
        
        for emp_id, emp_data in employees_map.items():
            cells = []
            for i in range(days_diff):
                current_date = start_d + timedelta(days=i)
                current_date_str = current_date.strftime('%Y-%m-%d')
                
                cell = emp_data['cells_map'].get(current_date_str)
                if not cell:
                    day_status = 'absent'
                    tooltip = f"<strong>{current_date_str}</strong><br>غائب"
                    
                    if current_date_str in holidays_map:
                        day_status = 'holiday'
                        tooltip = f"<strong>{current_date_str}</strong><br>عطلة رسمية: {holidays_map.get(current_date_str, '')}"
                    elif current_date.weekday() in weekends:
                        day_status = 'weekly_off'
                        tooltip = f"<strong>{current_date_str}</strong><br>عطلة أسبوعية"
                    
                    cell = {
                        'date': current_date_str,
                        'status': day_status,
                        'single_punch': False,
                        'late_after_grace': False,
                        'tooltip_html': tooltip
                    }
                cells.append(cell)
            
            emp_data['cells'] = cells
            if 'cells_map' in emp_data:
                del emp_data['cells_map'] 
            rows.append(emp_data)
        
        def _sort_key(x):
            try:
                return int(x.get('employee_number', 0))
            except (ValueError, TypeError):
                return 999999
        rows.sort(key=_sort_key)
        
        return jsonify({
            'success': True,
            'rows': rows,
            'stats': summary_data,
            'days_diff': days_diff
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'message': f"Server Error: {str(e)}"}), 500


@report_bp.route('/fingerprint/monthly_employees_report_new')
@login_required
@require_permission('report.view')
def monthly_employees_report_new_page():
    today = date.today()
    start_date = request.args.get('start_date', today.replace(day=1).strftime('%Y-%m-%d'))
    end_date = request.args.get('end_date', today.strftime('%Y-%m-%d'))
    try:
        start_d = datetime.strptime(start_date, '%Y-%m-%d').date()
        end_d = datetime.strptime(end_date, '%Y-%m-%d').date()
    except ValueError:
        start_date = today.replace(day=1).strftime('%Y-%m-%d')
        end_date = today.strftime('%Y-%m-%d')
        start_d = today.replace(day=1)
        end_d = today
    days_diff = (end_d - start_d).days + 1
    if days_diff < 1 or days_diff > 365:
        days_diff = 1
    arabic_days = ['اثنين', 'ثلاثاء', 'أربعاء', 'خميس', 'جمعة', 'سبت', 'أحد']
    days_names = []
    for i in range(days_diff):
        d_date = start_d + timedelta(days=i)
        days_names.append(arabic_days[d_date.weekday()])
    conn = get_db_connection()
    try:
        allowed_dept = get_allowed_department_name()
        if allowed_dept:
            departments = [allowed_dept]
        else:
            depts_rows = conn.execute("SELECT DISTINCT department FROM employees WHERE department IS NOT NULL AND department != '' ORDER BY department").fetchall()
            departments = [r['department'] for r in depts_rows]
            
        pos_query = "SELECT DISTINCT position FROM employees WHERE position IS NOT NULL AND position != ''"
        pos_params = []
        if allowed_dept:
            pos_query += " AND department = ?"
            pos_params.append(allowed_dept)
        pos_query += " ORDER BY position"
        
        pos_rows = conn.execute(pos_query, pos_params).fetchall()
        positions = [r['position'] for r in pos_rows]
        
        leave_types = conn.execute("SELECT id, name FROM leave_types ORDER BY name").fetchall()
    finally:
        pass # conn.close() removed to prevent leak in Flask g
        
    selected_dept = allowed_dept if allowed_dept else request.args.get('dept', '')
    return render_template('monthly_employees_report_new.html', start_date=start_date, end_date=end_date, days_diff=days_diff, days_names=days_names, departments=departments, positions=positions, leave_types=leave_types, selected_dept=selected_dept, selected_pos=request.args.get('pos', ''))

@report_bp.route('/fingerprint/custom_employees_report')
@login_required
@require_permission('report.view')
def custom_employees_report_page():
    """صفحة التقرير الشامل المخصص للموظفين (للتصدير)"""
    today = date.today()
    start_date = request.args.get('start_date', today.replace(day=1).strftime('%Y-%m-%d'))
    end_date = request.args.get('end_date', today.strftime('%Y-%m-%d'))
    
    try:
        start_d = datetime.strptime(start_date, '%Y-%m-%d').date()
        end_d = datetime.strptime(end_date, '%Y-%m-%d').date()
    except ValueError:
        start_date = today.replace(day=1).strftime('%Y-%m-%d')
        end_date = today.strftime('%Y-%m-%d')
        start_d = today.replace(day=1)
        end_d = today

    days_diff = (end_d - start_d).days + 1
    if days_diff < 1 or days_diff > 365:
        days_diff = 1

    # أسماء الأيام بالعربي
    arabic_days = ['اثنين', 'ثلاثاء', 'أربعاء', 'خميس', 'جمعة', 'سبت', 'أحد']
    days_names = []
    for i in range(days_diff):
        d_date = start_d + timedelta(days=i)
        days_names.append((d_date.strftime('%Y-%m-%d'), arabic_days[d_date.weekday()]))

    # جلب الأقسام والوظائف للفلترة
    conn = get_db_connection()
    try:
        allowed_dept = get_allowed_department_name()
        if allowed_dept:
            departments = [allowed_dept]
        else:
            depts_rows = conn.execute('SELECT DISTINCT department FROM employees WHERE department IS NOT NULL AND department != "" ORDER BY department').fetchall()
            departments = [r['department'] for r in depts_rows]
            
        pos_query = 'SELECT DISTINCT position FROM employees WHERE position IS NOT NULL AND position != ""'
        pos_params = []
        if allowed_dept:
            pos_query += " AND department = ?"
            pos_params.append(allowed_dept)
        pos_query += " ORDER BY position"
        
        pos_rows = conn.execute(pos_query, pos_params).fetchall()
        positions = [r['position'] for r in pos_rows]
        
        leave_types = conn.execute('SELECT id, name FROM leave_types ORDER BY name').fetchall()
    finally:
        pass # conn.close() removed to prevent leak in Flask g
    
    selected_dept = allowed_dept if allowed_dept else request.args.get('dept', '')
    return render_template('custom_employees_report.html',
                         start_date=start_date,
                         end_date=end_date,
                         days_diff=days_diff,
                         days_names=days_names,
                         departments=departments,
                         positions=positions,
                         leave_types=leave_types,
                         selected_dept=selected_dept,
                         selected_pos=request.args.get('pos', ''))

@report_bp.route('/api/fingerprint/custom_employees_report')
@login_required
@require_permission('report.view')
def custom_employees_report_api():
    """API لجلب بيانات التقرير المخصص الشامل مع الرواتب"""
    try:
        today = date.today()
        start_date = request.args.get('start_date', today.replace(day=1).strftime('%Y-%m-%d'))
        end_date = request.args.get('end_date', today.strftime('%Y-%m-%d'))
        
        dept_filter = request.args.get('dept', '').strip()
        pos_filter = request.args.get('pos', '').strip()

        try:
            start_d = datetime.strptime(start_date, '%Y-%m-%d').date()
            end_d = datetime.strptime(end_date, '%Y-%m-%d').date()
        except ValueError:
            return jsonify({'success': False, 'message': 'Invalid dates'}), 400

        days_diff = (end_d - start_d).days + 1
        if days_diff < 1 or days_diff > 365:
            return jsonify({'success': False, 'message': 'Date range too large'}), 400

        # جلب جميع الموظفين النشطين مع الراتب الأساسي (والموظفين غير النشطين الذين لديهم بيانات، يمكن تصفيتهم لاحقا أو جلب الكل)
        conn = get_db_connection()
        query = '''
            SELECT e.id, e.name, e.arabic_name, e.employee_number, e.department, e.position, e.salary,
                   e.end_of_service_date, e.hire_date, e.weekly_leave_selected_days, e.weekly_leave_days,
                   st.hours_per_day AS shift_hours_per_day
            FROM employees e
            LEFT JOIN shift_types st ON st.name = e.shift_type
            WHERE e.is_active = 1 OR (e.end_of_service_date IS NOT NULL AND DATE(e.end_of_service_date) >= ?)
        '''
        params = [start_date]
        
        allowed_dept = get_allowed_department_name()
        if allowed_dept:
            dept_filter = allowed_dept
            
        if dept_filter:
            query += ' AND e.department = ?'
            params.append(dept_filter)
        if pos_filter:
            query += ' AND e.position = ?'
            params.append(pos_filter)
            
        query += ' ORDER BY CAST(e.employee_number AS INTEGER), e.name'
        
        emps_rows = conn.execute(query, params).fetchall()
        
        # جلب الإجازات خلال الفترة لمعرفة تفاصيلها
        leaves_query = '''
            SELECT employee_id as emp_id, start_date, end_date, COALESCE(is_paid_leave, 0) as is_paid
            FROM leave_requests 
            WHERE status = 'approved' AND start_date <= ? AND end_date >= ?
        '''
        leaves_rows = conn.execute(leaves_query, (end_date, start_date)).fetchall()
        pass # conn.close() removed to prevent leak in Flask g
        
        emp_leaves_map = {}
        for r in leaves_rows:
            try:
                s_d_leave = datetime.strptime(r['start_date'], '%Y-%m-%d').date()
                e_d_leave = datetime.strptime(r['end_date'], '%Y-%m-%d').date()
            except ValueError:
                continue
                
            s_d_leave = max(s_d_leave, start_d)
            e_d_leave = min(e_d_leave, end_d)
            
            emp_id = r['emp_id']
            if emp_id not in emp_leaves_map:
                emp_leaves_map[emp_id] = {}
            paid = bool(r['is_paid'])
            curr = s_d_leave
            while curr <= e_d_leave:
                d_key = curr.strftime('%Y-%m-%d')
                emp_leaves_map[emp_id][d_key] = paid or emp_leaves_map[emp_id].get(d_key, False)
                curr += timedelta(days=1)

        # تشغيل تقرير البصمات الذكي للفترة المخصصة
        reporter = SmartFingerprintReport()
        monthly_report = reporter.get_custom_date_range_report(start_date, end_date)
        monthly_daily_data = monthly_report['daily_data']

        from utils.settings_utils import get_salary_settings_v2
        _sal_settings = get_salary_settings_v2(conn)
        weekend_mult = float(_sal_settings.get('weekend_ot_multiplier', 1.5))
        holiday_mult = float(_sal_settings.get('holiday_ot_multiplier', 2.0))
        
        rows = []
        for emp in emps_rows:
            emp_id = emp['id']
            daily_cells = []
            
            present_days = 0
            absent_days = 0
            weekly_off_days = 0
            holiday_days = 0
            total_late_mins = 0
            total_early_leave_mins = 0
            total_work_hours = 0.0
            total_overtime_mins = 0
            unpaid_leave_days = 0
            ot_weekend_mins = 0
            ot_holiday_mins = 0

            hire_dt = None
            if emp['hire_date']:
                try:
                    hire_dt = datetime.strptime(str(emp['hire_date'])[:10], '%Y-%m-%d').date()
                except ValueError:
                    hire_dt = None
            _wl_raw = emp['weekly_leave_selected_days'] or ''
            try:
                weekly_off_set = {(int(x) + 6) % 7 for x in _wl_raw.split(',') if x.strip() != ''}
            except ValueError:
                weekly_off_set = set()
            if not weekly_off_set:
                weekly_off_set = {4, 5}

            emp_leave_info = emp_leaves_map.get(emp_id, {})

            for i in range(days_diff):
                current_date = start_d + timedelta(days=i)
                date_str = current_date.strftime('%Y-%m-%d')

                if hire_dt and current_date < hire_dt:
                    daily_cells.append({'date': date_str, 'status': 'not_hired', 'check_in': '--', 'check_out': '--',
                                        'work_hours': 0, 'late_minutes': 0, 'early_leave_minutes': 0,
                                        'overtime_minutes': 0, 'is_leave': False})
                    continue

                day_records = monthly_daily_data.get(date_str, [])
                emp_record = next((r for r in day_records if r['employee_id'] == emp_id), None)
                
                cell_data = {
                    'date': date_str,
                    'status': 'absent',
                    'check_in': '--',
                    'check_out': '--',
                    'work_hours': 0,
                    'late_minutes': 0,
                    'early_leave_minutes': 0,
                    'overtime_minutes': 0,
                    'is_leave': date_str in emp_leave_info
                }
                
                if emp_record:
                    cell_data['check_in'] = emp_record.get('checkin_time', '--')
                    cell_data['check_out'] = emp_record.get('checkout_time', '--')
                    cell_data['work_hours'] = emp_record.get('work_hours', 0)
                    cell_data['late_minutes'] = emp_record.get('late_minutes', 0)
                    cell_data['early_leave_minutes'] = emp_record.get('early_leave_minutes', 0)
                    
                    status_text = emp_record['status']
                    
                    if cell_data['is_leave']:
                        cell_data['status'] = 'leave'
                        if not emp_leave_info.get(date_str, True) and current_date.weekday() not in weekly_off_set:
                            unpaid_leave_days += 1
                    elif status_text == 'حاضر':
                        cell_data['status'] = 'present'
                        present_days += 1
                        total_work_hours += emp_record.get('work_hours', 0)
                        total_late_mins += emp_record.get('late_minutes', 0)
                        total_early_leave_mins += emp_record.get('early_leave_minutes', 0)
                    elif 'دخول فقط' in status_text or 'خروج فقط' in status_text:
                        cell_data['status'] = 'partial'
                        present_days += 1
                        total_work_hours += emp_record.get('work_hours', 0)
                        total_late_mins += emp_record.get('late_minutes', 0)
                        total_early_leave_mins += emp_record.get('early_leave_minutes', 0)
                    elif 'عطلة أسبوعية' in status_text:
                        cell_data['status'] = 'weekly_off'
                        weekly_off_days += 1
                        if emp_record.get('checkin_time') and emp_record.get('checkin_time') != '--' and emp_record.get('checkin_time') != '00:00':
                            cell_data['overtime_minutes'] = int(emp_record.get('work_hours', 0) * 60)
                            total_overtime_mins += cell_data['overtime_minutes']
                            ot_weekend_mins += cell_data['overtime_minutes']
                    elif 'عطلة رسمية' in status_text:
                        cell_data['status'] = 'holiday'
                        holiday_days += 1
                        if emp_record.get('checkin_time') and emp_record.get('checkin_time') != '--' and emp_record.get('checkin_time') != '00:00':
                            cell_data['overtime_minutes'] = int(emp_record.get('work_hours', 0) * 60)
                            total_overtime_mins += cell_data['overtime_minutes']
                            ot_holiday_mins += cell_data['overtime_minutes']
                    else:
                        if emp['end_of_service_date'] and current_date > datetime.strptime(emp['end_of_service_date'], '%Y-%m-%d').date():
                            cell_data['status'] = 'terminated'
                        else:
                            cell_data['status'] = 'absent'
                            absent_days += 1
                else:
                    if cell_data['is_leave']:
                        cell_data['status'] = 'leave'
                        if not emp_leave_info.get(date_str, True) and current_date.weekday() not in weekly_off_set:
                            unpaid_leave_days += 1
                    elif current_date.weekday() in weekly_off_set:
                        cell_data['status'] = 'weekly_off'
                        weekly_off_days += 1
                    else:
                        if emp['end_of_service_date'] and current_date > datetime.strptime(emp['end_of_service_date'], '%Y-%m-%d').date():
                            cell_data['status'] = 'terminated'
                        else:
                            cell_data['status'] = 'absent'
                            absent_days += 1
                        
                daily_cells.append(cell_data)

            hours_per_day = float(emp['shift_hours_per_day'] or 8)
            daily_rate = float(emp['salary'] or 0) / 30.0
            hour_rate = (daily_rate / hours_per_day) if hours_per_day > 0 else 0
            absent_deduction = absent_days * daily_rate
            unpaid_leave_deduction = unpaid_leave_days * daily_rate
            late_deduction = (total_late_mins / 60.0) * hour_rate
            early_leave_deduction = (total_early_leave_mins / 60.0) * hour_rate
            overtime_reward = ((ot_weekend_mins / 60.0) * weekend_mult + (ot_holiday_mins / 60.0) * holiday_mult) * hour_rate

            total_deduction = absent_deduction + unpaid_leave_deduction + late_deduction + early_leave_deduction
            net_salary = float(emp['salary'] or 0) - total_deduction + overtime_reward

            rows.append({
                'id': emp_id,
                'name': emp['name'],
                'arabic_name': emp['arabic_name'] or emp['name'],
                'employee_number': emp['employee_number'],
                'department': emp['department'] or '',
                'position': emp['position'] or '',
                'salary': float(emp['salary'] or 0),
                'stats': {
                    'present_days': present_days,
                    'absent_days': absent_days,
                    'weekly_off_days': weekly_off_days,
                    'holiday_days': holiday_days,
                    'total_work_hours': round(total_work_hours, 2),
                    'total_late_mins': total_late_mins,
                    'total_early_leave_mins': total_early_leave_mins,
                    'total_overtime_mins': total_overtime_mins,
                    'unpaid_leave_days': unpaid_leave_days
                },
                'financials': {
                    'daily_rate': round(daily_rate, 2),
                    'absent_deduction': round(absent_deduction, 2),
                    'unpaid_leave_deduction': round(unpaid_leave_deduction, 2),
                    'late_deduction': round(late_deduction, 2),
                    'early_leave_deduction': round(early_leave_deduction, 2),
                    'total_deduction': round(total_deduction, 2),
                    'overtime_reward': round(overtime_reward, 2),
                    'net_salary': round(net_salary, 2)
                },
                'daily_cells': daily_cells
            })

        return jsonify({
            'success': True,
            'rows': rows,
            'days_diff': days_diff,
            'start_date': start_date,
            'end_date': end_date
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'message': f"Server Error: {str(e)}"}), 500

@report_bp.route('/fingerprint/employee_report')
@login_required
@require_permission('report.view')
def employee_report_page():
    conn = get_db_connection()
    
    query = 'SELECT id, name, arabic_name, employee_number FROM employees'
    params = []
    
    allowed_dept = get_allowed_department_name()
    if allowed_dept:
        query += ' WHERE department = ?'
        params.append(allowed_dept)
        
    query += ' ORDER BY CAST(employee_number AS INTEGER), name'
    employees_rows = conn.execute(query, params).fetchall()
    employees = []
    for row in employees_rows:
        employees.append({
            'id': row['id'],
            'name': row['name'],
            'arabic_name': row['arabic_name'],
            'employee_number': row['employee_number']
        })
    
    pass # conn.close() removed to prevent leak in Flask g
    return render_template('employee_detailed_report.html', employees=employees)

@report_bp.route('/fingerprint/employee_report/api')
@report_bp.route('/fingerprint/salary_reports/api')
@login_required
@require_permission('salary.view')
def salary_report_api():
    try:
        employee_id = request.args.get('employee_id')
        month = request.args.get('month', type=int)
        year = request.args.get('year', type=int)
        
        if not all([employee_id, month, year]):
            return jsonify({'success': False, 'message': 'بيانات غير مكتملة'})
            
        conn = get_db_connection()
        
        # Get Employee Info
        # Adjusted query to not use joins for department/position as they are in employees table
        employee = conn.execute('''
            SELECT e.*, st.name as shift_type_name, st.start_time, st.end_time, st.hours_per_day
            FROM employees e
            LEFT JOIN shift_types st ON e.shift_type = st.name
            WHERE e.id = ?
        ''', (employee_id,)).fetchone()
        
        if not employee:
            pass # conn.close() removed to prevent leak in Flask g
            return jsonify({'success': False, 'message': 'الموظف غير موجود'})
            
        allowed_dept = get_allowed_department_name()
        if allowed_dept and employee['department'] != allowed_dept:
            pass # conn.close() removed to prevent leak in Flask g
            return jsonify({'success': False, 'message': 'ليس لديك صلاحية لعرض بيانات هذا الموظف'})
            
        # Use V3 Strict Calculation
        # V3 handles fetching punches, leaves, holidays, and aggregating stats.
        from utils.salary_utils import calculate_salary_for_employee_v3
        salary_calculations = calculate_salary_for_employee_v3(conn, employee_id, month, year)
        
        # Get existing leaves for separate list display if needed (although V3 has details)
        # But frontend expects `leaves` array for the "Leaves in Month" card.
        # V3 returns `details` which includes daily status.
        # But let's keep the existing `leaves` query for the specific "Leave List" card for backward compat details
        # OR better: Extract it from V3 details? 
        # V3 doesn't return the raw leave request objects, just the daily status.
        # So I will keep the leave fetching query for the UI list.
        
        leaves_in_month = conn.execute('''
            SELECT lr.*, lt.name as leave_type_name
            FROM leave_requests lr
            JOIN leave_types lt ON lr.leave_type_id = lt.id
            WHERE lr.employee_id = ? 
            AND lr.status = 'approved'
            AND (strftime('%Y-%m', lr.start_date) = ? OR strftime('%Y-%m', lr.end_date) = ?)
            ORDER BY lr.start_date
        ''', (employee_id, f"{year:04d}-{month:02d}", f"{year:04d}-{month:02d}")).fetchall()
        
        leave_details = []
        total_leave_days = 0
        for leave in leaves_in_month:
             # Calculate intersection days using existing logic or simplier
             s_date = datetime.strptime(leave['start_date'], '%Y-%m-%d').date()
             e_date = datetime.strptime(leave['end_date'], '%Y-%m-%d').date()
             # ... intersection logic ...
             # We can reuse the loop I replaced or just simplified one
             m_start = date(year, month, 1)
             next_m = month + 1 if month < 12 else 1
             next_y = year if month < 12 else year + 1
             m_end = date(next_y, next_m, 1) - timedelta(days=1)
             
             act_s = max(s_date, m_start)
             act_e = min(e_date, m_end)
             if act_s <= act_e:
                 days = (act_e - act_s).days + 1
                 total_leave_days += days
                 leave_details.append({
                     'leave_type': leave['leave_type_name'],
                     'start_date': leave['start_date'],
                     'end_date': leave['end_date'],
                     'days_in_month': days,
                     'reason': leave['reason']
                 })

        pass # conn.close() removed to prevent leak in Flask g
        
        if not salary_calculations:
             return jsonify({'success': False, 'message': 'فشل في حساب الراتب'})

        # Adapt V3 output to frontend expectation "attendance_data"
        stats = salary_calculations['stats']
        attendance_data = {
            'days_worked': stats['days_worked'], # Or present_days
            'total_hours': sum(d['work_hours'] for d in salary_calculations['details']), # Sum from details
            'attendance_rate': 0 # Optional
        }
        
        total_work_days = calendar.monthrange(year, month)[1] # Simple day count

        return jsonify({
            'success': True,
            'employee': dict(employee),
            'attendance_data': attendance_data,
            'leaves': leave_details,
            'total_leave_days': total_leave_days,
            'total_work_days': total_work_days,
            'month': month,
            'year': year,
            'salary_calculations': salary_calculations # This is now V3 structure
        })
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'خطأ في جلب البيانات: {str(e)}'})

@report_bp.route('/fingerprint/salary_reports')
@login_required
@require_permission('salary.view')
def salary_reports():
    """تقارير الرواتب - نسخة محسنة من تقرير الموظف"""
    conn = get_db_connection()
    try:
        query = 'SELECT id, name, arabic_name, employee_number FROM employees'
        params = []
        
        allowed_dept = get_allowed_department_name()
        if allowed_dept:
            query += ' WHERE department = ?'
            params.append(allowed_dept)
            
        query += ' ORDER BY CAST(employee_number AS INTEGER), name'
        rows = conn.execute(query, params).fetchall()
    finally:
        pass # conn.close() removed to prevent leak in Flask g
    
    employees = []
    for row in rows:
        employees.append({
            'id': row['id'],
            'name': row['name'],
            'arabic_name': row['arabic_name'],
            'employee_number': row['employee_number']
        })
    
    
    return render_template('salary_reports.html', employees=employees)

@report_bp.route('/api/late_arrival_alerts')
@login_required
@require_permission('attendance.view')
def api_late_arrival_alerts():
    """API لجلب تنبيهات التأخير"""
    try:
        conn = get_db_connection()
        alerts = get_late_arrival_alerts(conn)
        pass # conn.close() removed to prevent leak in Flask g
        
        allowed_dept = get_allowed_department_name()
        if allowed_dept:
            alerts = [a for a in alerts if a.get('department') == allowed_dept]
        
        return jsonify({
            'success': True,
            'alerts': alerts,
            'count': len(alerts)
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        })

@report_bp.route('/fingerprint/late_two_days')
@login_required
@require_permission('report.view')
def late_two_days():
    """تقرير المتأخرين (يوم واحد)"""
    date_str = request.args.get('date', date.today().strftime('%Y-%m-%d'))
    q = request.args.get('q', '').strip()
    dept = request.args.get('dept', '').strip()
    
    allowed_dept = get_allowed_department_name()
    if allowed_dept:
        dept = allowed_dept
    
    conn = get_db_connection()
    
    # 1. جلب التنبيهات
    alerts = get_late_arrival_alerts(conn, date_str)
    
    # 2. جلب الأقسام للفلتر
    if allowed_dept:
        departments = [allowed_dept]
    else:
        departments_rows = conn.execute('SELECT DISTINCT department FROM employees WHERE department IS NOT NULL').fetchall()
        departments = [r['department'] for r in departments_rows]
    
    # تحويل التنبيهات للشكل المطلوب في القالب
    rows = []
    for alert in alerts:
        # تصفية حسب الاسم/الرقم
        if q:
            term = q.lower()
            if term not in alert['name'].lower() and term not in str(alert['employee_number']):
                continue
        
        # تصفية حسب القسم
        emp_dept = alert.get('department', '')
        if dept and dept != emp_dept:
            continue
            
        rows.append({
            'name': alert['name'],
            'employee_number': alert['employee_number'],
            'department': emp_dept,
            'late': alert['late_minutes'],
            'checkin': alert['check_in'],
            'scheduled': alert['shift_start']
        })
    
    pass # conn.close() removed to prevent leak in Flask g
    
    return render_template('late_two_days.html', 
                         rows=rows, 
                         date=date_str, 
                         q=q, 
                         dept=dept, 
                         departments=departments)

@report_bp.route('/fingerprint/monthly_employees_report')
@login_required
@require_permission('report.view')
def monthly_employees_report_page():
    """صفحة التقرير الشهري الشامل للموظفين (للتصدير)"""
    today = date.today()
    try:
        month = int(request.args.get('month', today.month))
        year = int(request.args.get('year', today.year))
    except (ValueError, TypeError):
        month = today.month
        year = today.year
    
    # أسماء الأشهر
    month_name = calendar.month_name[month]
    days_in_month = calendar.monthrange(year, month)[1]

    # أسماء الأيام بالعربي
    arabic_days = ['اثنين', 'ثلاثاء', 'أربعاء', 'خميس', 'جمعة', 'سبت', 'أحد']
    days_names = []
    for d in range(1, days_in_month + 1):
        d_date = date(year, month, d)
        days_names.append(arabic_days[d_date.weekday()])

    # جلب الأقسام والوظائف للفلترة
    conn = get_db_connection()
    try:
        allowed_dept = get_allowed_department_name()
        if allowed_dept:
            departments = [allowed_dept]
        else:
            depts_rows = conn.execute('SELECT DISTINCT department FROM employees WHERE department IS NOT NULL AND department != "" ORDER BY department').fetchall()
            departments = [r['department'] for r in depts_rows]
            
        pos_query = 'SELECT DISTINCT position FROM employees WHERE position IS NOT NULL AND position != ""'
        pos_params = []
        if allowed_dept:
            pos_query += " AND department = ?"
            pos_params.append(allowed_dept)
        pos_query += " ORDER BY position"
        
        pos_rows = conn.execute(pos_query, pos_params).fetchall()
        positions = [r['position'] for r in pos_rows]
        
        leave_types = conn.execute('SELECT id, name FROM leave_types ORDER BY name').fetchall()
    finally:
        pass # conn.close() removed to prevent leak in Flask g
    
    selected_dept = allowed_dept if allowed_dept else request.args.get('dept', '')
    return render_template('monthly_employees_report.html',
                         month=month,
                         year=year,
                         month_name=month_name,
                         days_in_month=days_in_month,
                         days_names=days_names,
                         departments=departments,
                         positions=positions,
                         leave_types=leave_types,
                         selected_dept=selected_dept,
                         selected_pos=request.args.get('pos', ''))

@report_bp.route('/api/fingerprint/monthly_employees_report')
@login_required
@require_permission('report.view')
def monthly_employees_report_api():
    """API لجلب بيانات التقرير الشهري الشامل مع الرواتب - V3 Logic"""
    try:
        today = date.today()
        month = int(request.args.get('month', today.month))
        year = int(request.args.get('year', today.year))
        
        dept_filter = request.args.get('dept', '').strip()
        pos_filter = request.args.get('pos', '').strip()

        # جلب الموظفين مع الفلاتر
        query = '''
            SELECT e.id, e.name, e.arabic_name, e.employee_number, e.department, e.position, 
                   e.salary as base_salary, st.hours_per_day,
                   e.weekly_leave_start, e.weekly_leave_days 
            FROM employees e
            LEFT JOIN shift_types st ON e.shift_type = st.name
            WHERE (e.hire_date IS NULL OR e.hire_date <= ?)
            AND (e.end_of_service_date IS NULL OR e.end_of_service_date >= ?) 
        '''
        
        from calendar import monthrange
        last_day = monthrange(year, month)[1]
        month_end_date = f"{year}-{month:02d}-{last_day:02d}"
        month_start_date = f"{year}-{month:02d}-01"
        
        params = [month_end_date, month_start_date]
        
        allowed_dept = get_allowed_department_name()
        if allowed_dept:
            dept_filter = allowed_dept
            
        if dept_filter:
            query += ' AND e.department = ?'
            params.append(dept_filter)
            
        if pos_filter:
            query += ' AND e.position = ?'
            params.append(pos_filter)
            
        query += ' ORDER BY CAST(e.employee_number AS INTEGER), e.name'

        conn = get_db_connection()
        
        # Fetch Grace Period
        grace_row = conn.execute("SELECT setting_value FROM salary_settings_v2 WHERE setting_name='grace_late_minutes'").fetchone()
        try:
            val = grace_row['setting_value'] if grace_row else None
            grace_period = int(val) if val is not None and str(val).strip() != '' else 15
        except:
            grace_period = 15
        
        employees_rows = conn.execute(query, params).fetchall()
        
        # استيراد دالة V3
        from utils.salary_utils import calculate_salary_for_employee_v3
        
        rows = []
        days_in_month_count = calendar.monthrange(year, month)[1]
        
        # Calculate Required Days (Standard) for the whole month
        # Assuming fixed weekends (Fri/Sat) and Official Holidays
        # This is an approximation if shift settings vary per employee, but acceptable for report.
        # Ideally we check each day.
        
        # Get Holidays
        month_str = f"{year:04d}-{month:02d}"
        holidays_res = conn.execute("SELECT date FROM official_holidays WHERE strftime('%Y-%m', date) = ?", (month_str,)).fetchall()
        holidays_set = {h['date'] for h in holidays_res}
        
        for emp in employees_rows:
            emp_id = emp['id']
            hours_per_day = emp['hours_per_day'] or 8
            
            # حساب الراتب والحضور باستخدام V3
            salary_data = calculate_salary_for_employee_v3(conn, emp_id, month, year)
            
            if not salary_data:
                salary_data = {
                    'stats': {}, 
                    'deductions': {}, 
                    'details': [], 
                    'net_salary': 0, 
                    'ot_amount': 0, 
                    'base_salary': emp['base_salary'] or 0
                }
                
            # The original `if not salary_data: continue` is now redundant if salary_data is always initialized.
            # However, if the intention was to skip employees for whom salary calculation *truly* fails
            # (e.g., if the default dictionary is not sufficient for subsequent logic),
            # then the `continue` might still be desired.
            # Based on the instruction, the default dictionary should make it proceed.
            
            stats = salary_data.get('stats', {})
            deductions = salary_data.get('deductions', {})
            details = salary_data.get('details', [])
            
            required_work_hours = 0
            non_working_statuses = ['Weekend', 'Holiday', 'Leave', 'Weekly Off', 'Holiday Work'] 
            
            for day_record in details:
                status = day_record.get('status', '')
                if status not in non_working_statuses:
                    required_work_hours += hours_per_day
            
            daily_map = {d['date']: d for d in details}
            
            days_arr = []
            for day in range(1, days_in_month_count + 1):
                d_date = date(year, month, day)
                d_str = d_date.strftime('%Y-%m-%d')
                
                record = daily_map.get(d_str)
                val = 0
                tooltip = ''
                
                if record:
                    status = record.get('status', '')
                    work_hours = record.get('work_hours', 0)
                    
                    # تحديد القيمة المعروضة بناءً على الحالة
                    if status == 'Weekend':
                        val = {'code': 'W', 'tooltip': 'إجازة أسبوعية'}
                    elif status == 'Holiday':
                        val = {'code': 'H', 'tooltip': 'عطلة رسمية'}
                    elif status == 'Leave':
                        val = {'code': 'L', 'tooltip': f"إجازة: {record.get('leave_type','')}"}
                    elif 'Absent' in status:
                        val = {'code': 'A', 'tooltip': 'غياب'}
                    elif 'Invalid' in status: # Catch Invalid or similar
                        val = {'code': 'I', 'tooltip': 'بصمة ناقصة (للمراجعة)'}
                    else:
                        # الحالات الطبيعية (حضور، انصراف مبكر، عمل في عطلة، مأمورية)
                        # نعرض عدد الساعات دائماً
                        val = round(work_hours, 2)
                
                days_arr.append(val)

            total_late_hours = round(stats.get('late_mins', 0) / 60.0, 2)
            ot_hours = round(stats.get('ot_mins', 0) / 60.0, 2)
            total_work_h = round(sum(d['work_hours'] for d in details), 2)
            basic_work_hours = max(0, total_work_h - ot_hours) 
            
            # Missing Hours (Deficit)
            # Difference between what was required and what was actually worked (basic only)
            missing_hours = max(0, required_work_hours - basic_work_hours)
            
            total_deduction_amount = (deductions.get('late', 0) + 
                                    deductions.get('early', 0) + 
                                    deductions.get('absent', 0))

            base_salary = salary_data.get('base_salary', 0)
            if hours_per_day > 0:
                calc_hourly_rate = (base_salary / 30) / hours_per_day
            else:
                calc_hourly_rate = 0
            
            single_punch_count = sum(1 for d in details if d.get('status') == 'Invalid')
            
            # 1. Calculate Expected Work Days (Total - Weekends - Holidays)
            # This follows the user's rule: e.g if 2 days weekend -> 22 days work.
            w_start = emp['weekly_leave_start']
            if w_start is None: w_start = 5 # Default Friday
            w_days = emp['weekly_leave_days']
            if w_days is None: w_days = 1 # Default 1 day
            
            # Map DB(0=Sun...6=Sat) to Python(0=Mon...6=Sun)
            # DB Sun(0) -> Py 6
            # DB Mon(1) -> Py 0 ...
            # Formula: (DB - 1) % 7 ?? 
            # 0-1 = -1%7 = 6 (Sun) Correct.
            # 1-1 = 0 (Mon) Correct.
            
            weekend_days_py = []
            py_start = (w_start - 1) % 7
            weekend_days_py.append(py_start)
            for i in range(1, int(w_days)):
                weekend_days_py.append((py_start + i) % 7)
                
            expected_work_days = 0
            for day in range(1, days_in_month_count + 1):
                d_date = date(year, month, day)
                d_str = d_date.strftime('%Y-%m-%d')
                
                is_weekend = d_date.weekday() in weekend_days_py
                is_holiday = d_str in holidays_set
                
                # If it's a working day (Not Weekend AND Not Holiday)
                # Note: Holidays on Weekends are already excluded by "Not Weekend" check first?
                # or should we exclude Holiday even if it's a weekday?
                # User: "Work Days = Total - Weekends - Holidays"
                if not is_weekend and not is_holiday:
                    expected_work_days += 1
            
            # 2. Count Sick/Emergency Leaves
            sick_emergency_leaves = 0
            target_leaves = ['إجازة مرضية', 'إجازة طارئة']
            for d in details:
                if d.get('status') == 'Leave':
                    l_type = d.get('leave_type', '')
                    if any(t in l_type for t in target_leaves):
                        sick_emergency_leaves += 1

            row = {
                'id': emp['id'],
                'file_number': emp['employee_number'],
                'expected_work_days': expected_work_days,
                'sick_emergency_leaves': sick_emergency_leaves,
                'name': emp['name'],
                'arabic_name': emp['arabic_name'],
                'department': emp['department'],
                'position': emp['position'],
                'salary': round(base_salary, 2),
                'hourly_rate': round(calc_hourly_rate, 3), 
                
                'required_hours': round(required_work_hours, 2),
                'basic_work_hours': round(basic_work_hours, 2),
                'missing_hours': round(missing_hours, 2),
                'ot_hours': ot_hours,
                'total_work_hours': total_work_h,
                
                'single_punch_count': single_punch_count,
                'grace_period': grace_period,
                
                'total_late_hours': total_late_hours, 
                'present_days': stats.get('present_days', 0),
                'absent_days': stats.get('absent_days', 0),
                
                'total_deduction': round(total_deduction_amount, 3),
                'deduction_details': salary_data.get('deductions', {}), # Pass details
                'ot_amount': round(salary_data.get('ot_amount', 0), 3),
                
                'net_salary': round(salary_data.get('net_salary', 0), 3),
                'daily_hours': days_arr
            }
            rows.append(row)
            
        pass # conn.close() removed to prevent leak in Flask g
        return jsonify({'success': True, 'rows': rows})
        
    except Exception as e:
        print(f"API ERROR: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'message': str(e)}), 500

@report_bp.route('/api/fingerprint/monthly_presence_report')
@login_required
@require_permission('report.view')
def api_monthly_presence_report():
    try:
        year = request.args.get('year', date.today().year, type=int)
        month = request.args.get('month', date.today().month, type=int)
        
        dept = request.args.get('dept')
        pos = request.args.get('pos')
        
        allowed_dept = get_allowed_department_name()
        if allowed_dept:
            dept = allowed_dept
            
        reporter = SmartFingerprintReport()
        report_data = reporter.get_monthly_presence_integrated_report(year, month)
        
        daily_map = report_data['daily_data']
        days_in_month = report_data['summary']['total_days']
        
        # Get DB connection for filters
        conn = get_db_connection()
        
        # Collect all employee IDs
        all_emp_ids = set()
        for d_list in daily_map.values():
            for r in d_list:
                all_emp_ids.add(r['employee_id'])
                
        # Fetch detailed employee info (for filters)
        if not all_emp_ids:
             return jsonify({'success': True, 'rows': []})
             
        placeholders = ','.join(['?'] * len(all_emp_ids))
        emp_details_query = f"SELECT id, name, employee_number, department, position, arabic_name FROM employees WHERE id IN ({placeholders})"
        emp_details_rows = conn.execute(emp_details_query, list(all_emp_ids)).fetchall()
        
        emp_lookup = {row['id']: dict(row) for row in emp_details_rows}
        pass # conn.close() removed to prevent leak in Flask g

        final_rows = []
        
        # Sort employees by employee_number
        def _sort_emp_lookup(x):
            try:
                return int(emp_lookup[x].get('employee_number', 0))
            except (ValueError, TypeError):
                return 999999
        sorted_emp_ids = sorted(emp_lookup.keys(), key=_sort_emp_lookup)
        
        for e_id in sorted_emp_ids:
            emp = emp_lookup[e_id]
            
            # Apply Filters (Department / Position)
            if dept and emp['department'] != dept: continue
            if pos and emp['position'] != pos: continue
            
            daily_cells = []
            
            # Aggregate totals (Simplified for now, as we focus on presence visualization)
            # We iterate day 1 to days_in_month
            present_days = 0
            absent_days = 0
            presence_ok_days = 0
            presence_missing_days = 0
            
            for day_num in range(1, days_in_month + 1):
                d_str = f"{year}-{month:02d}-{day_num:02d}"
                day_record = None
                
                # O(N) search in that day's list - acceptable for monthly report scale
                if d_str in daily_map:
                    for r in daily_map[d_str]:
                        if r['employee_id'] == e_id:
                            day_record = r
                            break
                            
                cell_data = {'code': '-', 'tooltip': '', 'presence_status': 'none', 'check_in': '', 'check_out': '', 'presence_time': ''}
                
                if day_record:
                    st = day_record['status']
                    wh = day_record['work_hours']
                    all_times = day_record.get('all_times', [])
                    
                    # Extract check-in and check-out from all_times
                    if all_times:
                        cell_data['check_in'] = all_times[0] if len(all_times) > 0 else ''
                        cell_data['check_out'] = all_times[-1] if len(all_times) > 1 else ''
                    
                    if st == 'حاضر':
                        cell_data['code'] = wh
                        present_days += 1
                    elif st == 'غائب':
                        cell_data['code'] = 'A'
                        absent_days += 1
                    elif 'إجازة' in st:
                         cell_data['code'] = 'L'
                         cell_data['tooltip'] = st
                    elif 'عطلة' in st:
                         cell_data['code'] = 'H'
                         cell_data['tooltip'] = st
                    elif st == 'إجازة أسبوعية':
                         cell_data['code'] = 'W'
                    else:
                         cell_data['code'] = wh if wh > 0 else 'I'
                         if wh > 0: present_days += 1
                         
                    # Presence Info
                    p_info = day_record.get('presence_info', {})
                    cell_data['presence_status'] = p_info.get('status', 'none')
                    cell_data['presence_time'] = p_info.get('time', '')
                    if p_info.get('time'):
                        cell_data['tooltip'] += f"\nبصمة تواجد: {p_info['time']}"
                    
                    # Count presence days
                    if cell_data['presence_status'] == 'ok':
                        presence_ok_days += 1
                    elif cell_data['presence_status'] == 'missing':
                        # Absent days are counted as ABSENCE elsewhere; counting
                        # them here too would double-penalize the same day.
                        presence_missing_days += 1

                daily_cells.append(cell_data)

            row_dict = {
                'id': emp['id'],
                'name': emp['name'],
                'file_number': emp['employee_number'],
                'department': emp['department'],
                'position': emp['position'],
                'arabic_name': emp['arabic_name'],
                'daily_hours': daily_cells,
                # Totals (Simplified placeholders or calculated)
                'required_hours': 0, 
                'basic_work_hours': 0,
                'missing_hours': 0,
                'ot_hours': 0,
                'total_work_hours': 0,
                'total_late_hours': 0,
                'present_days': present_days,
                'absent_days': absent_days,
                'presence_ok_days': presence_ok_days,
                'presence_missing_days': presence_missing_days,
                'salary': 0,
                'ot_amount': 0,
                'total_deduction': 0,
                'net_salary': 0
            }
            final_rows.append(row_dict)

        return jsonify({'success': True, 'rows': final_rows})
    except Exception as e:
        print(f"API ERROR: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@report_bp.route('/fingerprint/monthly_presence_report')
@login_required
@require_permission('report.view')
def monthly_presence_report_page():
    try:
        conn = get_db_connection()
        try:
            depts = [r[0] for r in conn.execute("SELECT DISTINCT department FROM employees WHERE department IS NOT NULL AND department != ''").fetchall()]
            positions = [r[0] for r in conn.execute("SELECT DISTINCT position FROM employees WHERE position IS NOT NULL AND position != ''").fetchall()]
        except:
            depts = []
            positions = []
        finally:
            pass # conn.close() removed to prevent leak in Flask g
        
        # Days Names for the header (Sat, Sun...)
        today = date.today()
        y = request.args.get('year', today.year, type=int)
        m = request.args.get('month', today.month, type=int)
        _, days_in_month = calendar.monthrange(y, m)
        
        # Safe Day Names Key Map (0=Monday)
        day_keys = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
        
        days_names = []
        for d in range(1, days_in_month + 1):
            dt = date(y, m, d)
            # dt.weekday() -> 0=Mon, 6=Sun
            days_names.append(day_keys[dt.weekday()])
        
        return render_template('fp_monthly_presence_v2.html', 
                               year=y,
                               month=m,
                               departments=depts,
                               positions=positions,
                               days_in_month=days_in_month,
                               days_names=days_names,
                               month_name=calendar.month_name[m]
                               )
    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"Server Error: {str(e)}", 500
