from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify
from flask_babel import _
from datetime import datetime, date
from utils.db import get_db_connection
from utils.auth import login_required
from utils.rbac import require_permission
from utils.document_utils import get_document_expiry_alerts
from utils.settings_utils import get_system_settings
from utils.license import verify_license_key, get_current_license_info, save_license_key

main_bp = Blueprint('main', __name__)

@main_bp.route('/set_language/<code>')
def set_language(code):
    """
    Route to switch language (ar/en) and redirect back.
    """
    if code in ['ar', 'en']:
        session['lang'] = code
    return redirect(request.referrer or url_for('main.index'))

@main_bp.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))
    
    conn = get_db_connection()
    current_month = datetime.now().month
    current_year = datetime.now().year
    
    # التحقق من الترخيص
    license_info = get_current_license_info()
    license_days_left = license_info.get('days_left', 0)
    
    # تنبيهات انتهاء الصلاحية
    expiry_alerts = get_document_expiry_alerts()
    
    # إحصائيات سريعة
    stats = {
        'total_employees': conn.execute('SELECT COUNT(*) FROM employees').fetchone()[0],
        'on_leave': conn.execute('''
            SELECT COUNT(*) FROM leave_requests 
            WHERE status = 'approved' 
            AND DATE('now') BETWEEN start_date AND end_date
        ''').fetchone()[0],
        'attendance_today': conn.execute('''
            SELECT COUNT(DISTINCT employee_id) FROM attendance_records 
            WHERE DATE(check_time) = DATE('now')
        ''').fetchone()[0]
    }
    
    pass # conn.close() removed to prevent leak in Flask g
    
    return render_template('index.html', 
                         current_month=current_month, 
                         current_year=current_year,
                         license_days_left=license_days_left,
                         expiry_alerts=expiry_alerts,
                         stats=stats)

@main_bp.route('/settings')
@login_required
@require_permission('page.settings')
def settings():
    from utils.settings_utils import get_salary_settings_v2
    settings_data = get_system_settings() or {}
    conn = get_db_connection()
    sal = get_salary_settings_v2(conn)
    from utils.payroll_engine import resolve_period
    from datetime import datetime as _dt
    _now = _dt.now()
    _ps, _pe = resolve_period(conn, _now.month, _now.year)
    pp_now = {'month': _now.month, 'year': _now.year,
              'start': _ps.isoformat(), 'end': _pe.isoformat()}
    return render_template('settings.html', settings=settings_data, sal=sal,
                           pp_now=pp_now)

@main_bp.route('/settings/update', methods=['POST'])
@login_required
@require_permission('admin.settings')
def update_settings():
    conn = get_db_connection()
    sys_fields = ['app_name', 'company_name', 'currency_name', 'currency_symbol', 'currency_code',
                  'currency_position', 'company_address', 'company_phone', 'company_email']
    values = [request.form.get(k, '').strip() for k in sys_fields]
    conn.execute(
        'UPDATE system_settings SET ' + ', '.join(f'{k}=?' for k in sys_fields) +
        ', updated_at=CURRENT_TIMESTAMP WHERE id=1', values)

    ppd = request.form.get('payroll_period_start_day', '').strip()
    if ppd:
        try:
            conn.execute('UPDATE system_settings SET payroll_period_start_day = ? WHERE id = 1',
                         (max(1, min(int(ppd), 28)),))
        except ValueError:
            pass

    live_keys = ['grace_late_minutes', 'grace_early_minutes', 'early_departure_grace_minutes',
                 'weekday_ot_multiplier', 'weekend_ot_multiplier', 'holiday_ot_multiplier',
                 'overtime_round_to_minutes', 'overtime_cap_monthly_hours',
                 'rounding_display_decimals', 'daily_rate_basis',
                 'late_arrival_policy', 'early_departure_policy', 'missing_punch_policy',
                 'leave_earned_per_month', 'leave_migration_cutoff_date', 'sick_tier_year_basis', 'missing_punch_penalty_1', 'missing_punch_penalty_2', 'missing_punch_penalty_3', 'presence_missing_policy', 'presence_penalty_day_fraction', 'presence_penalty_1', 'presence_penalty_2', 'presence_penalty_3', 'presence_penalty_monthly_cap_days', 'hourly_perm_max_hours_per_day', 'hourly_perm_max_days_per_month']
    for k in live_keys:
        v = request.form.get(k, '').strip()
        if v == '':
            continue
        conn.execute('INSERT INTO salary_settings_v2(setting_name, setting_value) VALUES(?, ?) '
                     'ON CONFLICT(setting_name) DO UPDATE SET setting_value=excluded.setting_value', (k, v))
    conn.commit()
    import utils.settings_utils as _su
    _su._SETTINGS_CACHE = None
    flash(_('msg.settings_saved'), 'success')
    return redirect(url_for('main.settings'))

@main_bp.route('/license', methods=['GET', 'POST'])
def license_page():
    if request.method == 'POST':
        req_type = request.form.get('type')
        
        if req_type == 'logout':
            from utils.license import logout_license
            logout_license()
            flash(gettext('x.f_logout_deactivated'), 'success')
            return redirect(url_for('main.license_page'))

        if req_type == 'login':
            # Activation by Login
            username = request.form.get('username')
            password = request.form.get('password')
            
            from utils.license import activate_license_by_login
            ok, msg = activate_license_by_login(username, password)
            
            if ok:
                flash(_('msg.license_activated'), 'success')
                return redirect(url_for('main.index'))
            else:
                flash(_('error.license_activation_failed') + f': {msg}', 'error')
                
        else:
            # Manual Key Activation
            key = request.form.get('license_key', '').strip()
            from utils.license import verify_license_full_flow
            # Unpack safely (ok, msg)
            res = verify_license_full_flow(key)
            ok = res[0]
            msg = res[1] if len(res) > 1 else "Unknown"
            
            if ok:
                save_license_key(key)
                # إبطال صريح قبل التوجيه: الحارس في before_request يقرأ
                # الكاش، فبدونه يرى النتيجة السابقة ويعيد التوجيه إلى هذه
                # الصفحة نفسها.
                try:
                    from utils.license import invalidate_license_cache
                    invalidate_license_cache()
                except Exception:
                    pass
                flash(_('msg.license_activated'), 'success')
                return redirect(url_for('main.index'))
            else:
                flash(_('error.license_activation_failed') + f': {msg}', 'error')
    
    info = get_current_license_info()
    return render_template('license.html', info=info)

# نقاط النهاية API
@main_bp.route('/api/stats')
@login_required
def api_stats():
    conn = get_db_connection()
    
    # إحصائيات سريعة
    total_employees = conn.execute('SELECT COUNT(*) as count FROM employees').fetchone()['count']
    total_salaries = conn.execute('SELECT COUNT(*) as count FROM salaries').fetchone()['count']
    pending_leaves = conn.execute('SELECT COUNT(*) as count FROM leave_requests WHERE status = "pending"').fetchone()['count']
    total_documents = conn.execute('SELECT COUNT(*) as count FROM documents').fetchone()['count']
    
    # إجمالي الرواتب
    total_salary_amount = conn.execute('SELECT SUM(net_salary) as total FROM salaries').fetchone()['total'] or 0
    
    # عدد الموظفين حسب القسم
    dept_stats = conn.execute('''
        SELECT department, COUNT(*) as count 
        FROM employees 
        GROUP BY department
    ''').fetchall()
    
    pass # conn.close() removed to prevent leak in Flask g
    
    return jsonify({
        'total_employees': total_employees,
        'total_salaries': total_salaries,
        'pending_leaves': pending_leaves,
        'total_documents': total_documents,
        'total_salary_amount': total_salary_amount,
        'department_stats': [dict(row) for row in dept_stats]
    })

@main_bp.route('/api/recent-activities')
@login_required
def api_recent_activities():
    conn = get_db_connection()
    
    # آخر الموظفين المضافين
    recent_employees = conn.execute('''
        SELECT name, arabic_name, employee_number, department, created_at 
        FROM employees 
        ORDER BY created_at DESC 
        LIMIT 5
    ''').fetchall()
    
    # آخر الرواتب المسجلة
    recent_salaries = conn.execute('''
        SELECT s.month, s.year, s.net_salary, e.name, e.arabic_name 
        FROM salaries s 
        JOIN employees e ON s.employee_id = e.id 
        ORDER BY s.id DESC 
        LIMIT 5
    ''').fetchall()
    
    # آخر طلبات الإجازات
    recent_leaves = conn.execute('''
        SELECT lr.start_date, lr.days_count, e.name, e.arabic_name, lt.name as leave_type 
        FROM leave_requests lr 
        JOIN employees e ON lr.employee_id = e.id 
        JOIN leave_types lt ON lr.leave_type_id = lt.id 
        ORDER BY lr.created_at DESC 
        LIMIT 5
    ''').fetchall()
    
    pass # conn.close() removed to prevent leak in Flask g
    
    return jsonify({
        'recent_employees': [dict(row) for row in recent_employees],
        'recent_salaries': [dict(row) for row in recent_salaries],
        'recent_leaves': [dict(row) for row in recent_leaves]
    })


@main_bp.route('/api/dashboard/analytics')
@login_required
def api_dashboard_analytics():
    """Dashboard analytics drawn from the authoritative sources.

    Deliberately NOT from the legacy `salaries` table: disbursement truth
    lives in the payroll ledger, and headcount must exclude staff whose
    service has ended — otherwise the dashboard reports money that was never
    paid and people who no longer work here.
    """
    from utils.payroll_engine import resolve_period
    conn = get_db_connection()
    now = datetime.now()

    # ---- headcount ----
    head = conn.execute("""
        SELECT
          SUM(CASE WHEN is_active = 1 THEN 1 ELSE 0 END) AS active,
          SUM(CASE WHEN is_active = 0 THEN 1 ELSE 0 END) AS ended,
          COUNT(*) AS total
        FROM employees""").fetchone()

    joined_12m = conn.execute("""
        SELECT COUNT(*) AS n FROM employees
        WHERE DATE(hire_date) >= DATE('now', '-12 months')""").fetchone()['n']
    left_12m = conn.execute("""
        SELECT COUNT(*) AS n FROM employees
        WHERE end_of_service_date IS NOT NULL
          AND DATE(end_of_service_date) >= DATE('now', '-12 months')""").fetchone()['n']

    by_dept = [dict(r) for r in conn.execute("""
        SELECT COALESCE(NULLIF(TRIM(department), ''), '—') AS label,
               COUNT(*) AS value
        FROM employees WHERE is_active = 1
        GROUP BY label ORDER BY value DESC""").fetchall()]

    by_nat = [dict(r) for r in conn.execute("""
        SELECT COALESCE(NULLIF(TRIM(nationality), ''), '—') AS label,
               COUNT(*) AS value
        FROM employees WHERE is_active = 1
        GROUP BY label ORDER BY value DESC LIMIT 8""").fetchall()]

    # Service-length bands say more about retention than an average does.
    bands = [('< 1', 0, 1), ('1-3', 1, 3), ('3-5', 3, 5),
             ('5-10', 5, 10), ('10+', 10, 100)]
    tenure = []
    for label, lo, hi in bands:
        n = conn.execute("""
            SELECT COUNT(*) AS n FROM employees
            WHERE is_active = 1 AND hire_date IS NOT NULL
              AND (JULIANDAY('now') - JULIANDAY(hire_date)) / 365.0 >= ?
              AND (JULIANDAY('now') - JULIANDAY(hire_date)) / 365.0 < ?""",
            (lo, hi)).fetchone()['n']
        tenure.append({'label': label, 'value': n})

    # ---- payroll trend: ledger postings only (what was actually disbursed) ----
    trend = []
    for p in conn.execute("""
            SELECT month, year, MAX(id) AS pid
            FROM payroll_ledger_postings
            GROUP BY year, month ORDER BY year DESC, month DESC LIMIT 12""").fetchall():
        row = conn.execute("""SELECT total_net, total_basic, total_allowances,
                                     total_deductions, employees_count
                              FROM payroll_ledger_postings WHERE id = ?""",
                           (p['pid'],)).fetchone()
        trend.append({'label': f"{p['month']:02d}/{p['year']}",
                      'month': p['month'], 'year': p['year'],
                      'net': row['total_net'] or 0,
                      'basic': row['total_basic'] or 0,
                      'allowances': row['total_allowances'] or 0,
                      'deductions': row['total_deductions'] or 0,
                      'employees': row['employees_count'] or 0})
    trend.reverse()

    # ---- attendance today ----
    today = now.strftime('%Y-%m-%d')
    present_today = conn.execute("""
        SELECT COUNT(DISTINCT employee_id) AS n FROM attendance_records
        WHERE DATE(check_time) = ?""", (today,)).fetchone()['n']
    on_leave_today = conn.execute("""
        SELECT COUNT(DISTINCT lr.employee_id) AS n FROM leave_requests lr
        WHERE lr.status = 'approved' AND COALESCE(lr.is_deleted, 0) = 0
          AND ? BETWEEN DATE(lr.start_date) AND DATE(lr.end_date)""",
        (today,)).fetchone()['n']

    # ---- open items needing attention ----
    pending_leaves = conn.execute(
        "SELECT COUNT(*) AS n FROM leave_requests WHERE status = 'pending' "
        "AND COALESCE(is_deleted, 0) = 0").fetchone()['n']
    active_loans = conn.execute("""
        SELECT COUNT(*) AS n,
               COALESCE(SUM(l.principal), 0)
               - COALESCE((SELECT SUM(p.amount) FROM loan_payments p
                           JOIN employee_loans l2 ON l2.id = p.loan_id
                           WHERE l2.status = 'active'), 0) AS outstanding
        FROM employee_loans l WHERE l.status = 'active'""").fetchone()

    ps, pe = resolve_period(conn, now.month, now.year)
    current_run = conn.execute(
        'SELECT status FROM payroll_runs WHERE month = ? AND year = ?',
        (now.month, now.year)).fetchone()

    return jsonify({
        'success': True,
        'headcount': {'active': head['active'] or 0,
                      'ended': head['ended'] or 0,
                      'total': head['total'] or 0,
                      'joined_12m': joined_12m, 'left_12m': left_12m},
        'today': {'present': present_today, 'on_leave': on_leave_today,
                  'date': today},
        'open_items': {'pending_leaves': pending_leaves,
                       'active_loans': active_loans['n'] or 0,
                       'loans_outstanding': round(active_loans['outstanding'] or 0, 3)},
        'current_period': {'month': now.month, 'year': now.year,
                           'start': ps.isoformat(), 'end': pe.isoformat(),
                           'status': (current_run['status'] if current_run else None)},
        'charts': {'by_department': by_dept, 'by_nationality': by_nat,
                   'tenure': tenure, 'payroll_trend': trend},
    })


@main_bp.route('/api/license/unbind', methods=['POST'])
@login_required
def unbind_license_hwid():
    """فكّ ارتباط التركيب بالجهاز.

    يلزم عند نقل النظام إلى خادم جديد: الرخصة مربوطة بمعرّف التركيب، وبعد
    النقل يبقى المعرّف القديم في رخصة لا تطابق التركيب الجديد فيتوقّف عميل
    شرعيّ وهو دافع. الفكّ يولّد معرّفًا جديدًا ليُصدَر عليه ترخيص جديد.

    العملية مسجَّلة لأنها قد تُستعمل للالتفاف على الربط: من يفكّ ثم يفعّل
    على خادم آخر ينتج تركيبين. الكشف على الخادم لا هنا — لكن لا بدّ من
    أثر.
    """
    from utils.system_id import reset_system_hwid, get_system_hwid
    old = None
    try:
        old = get_system_hwid()
    except Exception:
        pass
    ok = reset_system_hwid()
    new = None
    try:
        new = get_system_hwid()
    except Exception:
        pass
    try:
        conn = get_db_connection()
        conn.execute(
            "INSERT INTO employee_audit_log "
            "(employee_id, field, old_value, new_value, category, source, "
            " note, changed_by) VALUES (0, 'license_hwid', ?, ?, 'status', "
            "'license_unbind', ?, ?)",
            ((old or '')[:16], (new or '')[:16],
             'فكّ ارتباط الترخيص بالجهاز', session.get('user_id')))
        conn.commit()
    except Exception as e:
        print(f'unbind audit failed: {e}')
    return jsonify({'success': bool(ok), 'new_hwid': new})


@main_bp.route('/api/license/state')
@login_required
def license_state_api():
    """حالة الرخصة ومعرّف التركيب — للعرض والدعم الفني."""
    from utils.license import get_license_state
    from utils.system_id import get_system_hwid, get_hardware_fingerprint
    st = get_license_state()
    try:
        st['hwid'] = get_system_hwid()
        # بصمة العتاد تُعرض للتشخيص فقط: تتغيّر مع إعادة بناء الحاوية،
        # ولذلك لم تعد أساس الربط.
        st['hardware_fingerprint'] = get_hardware_fingerprint()
    except Exception:
        pass
    return jsonify(st)


@main_bp.route('/api/version')
@login_required
def version_api():
    """معلومات الإصدار — للدعم الفني ولفحص التحديث."""
    from utils.version_info import version_info
    return jsonify(version_info())
