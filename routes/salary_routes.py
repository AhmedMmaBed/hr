from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_babel import gettext, _
from datetime import datetime
from utils.db import get_db_connection
from utils.db import get_db_connection
from utils.auth import login_required, get_allowed_department_name
from utils.rbac import require_permission
from utils.settings_utils import get_salary_settings_v2
from utils.salary_utils import calculate_salary_for_employee, calculate_leave_deductions_v2, calculate_attendance_allowance_v2
from utils.leave_policy_utils import process_holiday_work
from datetime import date
import calendar

salary_bp = Blueprint('salary', __name__)

@salary_bp.route('/salaries')
@login_required
@require_permission('page.salaries')
def salaries():
    """Retired. This screen wrote hand-entered salaries into the legacy
    `salaries` table, which the payroll engine and the ledger never read —
    so anything recorded here was invisible to actual disbursement while
    still looking authoritative on screen. Kept as a redirect so old links
    and bookmarks land on the real monthly payroll instead of a dead page."""
    flash(gettext('x.sal_legacy_moved'), 'info')
    return redirect(url_for('payroll.monthly_sheet'))


@salary_bp.route('/salaries/add', methods=['POST'])
@login_required
@require_permission('salary.calculate')
def add_salary():
    # Retired: manual salary entry bypassed the attestation and
    # ledger chain entirely. Use the monthly payroll screen.
    flash(gettext('x.sal_legacy_moved'), 'warning')
    return redirect(url_for('payroll.monthly_sheet'))


@salary_bp.route('/salaries/add_from_report', methods=['POST'])
@login_required
@require_permission('salary.calculate')
def add_salary_from_report():
    # Retired: manual salary entry bypassed the attestation and
    # ledger chain entirely. Use the monthly payroll screen.
    flash(gettext('x.sal_legacy_moved'), 'warning')
    return redirect(url_for('payroll.monthly_sheet'))


@salary_bp.route('/shift_types')
@login_required
@require_permission('salary.settings')
def shift_types():
    conn = get_db_connection()
    shift_types_rows = conn.execute('SELECT * FROM shift_types').fetchall()
    shift_types = [dict(row) for row in shift_types_rows]
    # فترات كل شفت مقسّم: تُعرض في الجدول وتُحمَّل في نموذج التعديل، وإلا
    # صار الشفت المقسّم غير مرئيّ بعد إنشائه وغير قابل للتعديل.
    for sh in shift_types:
        sh['periods'] = []
        if sh.get('is_split'):
            try:
                sh['periods'] = [dict(p) for p in conn.execute(
                    'SELECT seq, start_time, end_time FROM shift_periods '
                    'WHERE shift_type_id = ? ORDER BY seq', (sh['id'],)).fetchall()]
            except Exception:
                pass
    pass # conn.close() removed to prevent leak in Flask g
    return render_template('shift_types.html', shift_types=shift_types)

def _save_shift_periods(conn, shift_id, form):
    """حفظ فترات الشفت المقسّم من حقول النموذج.

    الحقول تصل كمصفوفتين متوازيتين period_start[] وperiod_end[]، وتُخزَّن
    فترةً فترة بترتيبها. وتُحذف الفترات القديمة أولًا ليعكس الحفظُ ما في
    النموذج بالضبط لا تراكمًا عليه.

    ويُعاد عددها: الشفت المقسّم يوجب بصمتين لكل فترة، وهذا العدد هو ما
    يقيس عليه المحرّك اكتمال بصمات اليوم.
    """
    conn.execute('DELETE FROM shift_periods WHERE shift_type_id = ?', (shift_id,))
    starts = form.getlist('period_start[]')
    ends = form.getlist('period_end[]')
    seq = 0
    for a, b in zip(starts, ends):
        a, b = (a or '').strip(), (b or '').strip()
        if not a or not b or a == b:
            continue
        seq += 1
        conn.execute(
            'INSERT INTO shift_periods (shift_type_id, seq, start_time, end_time) '
            'VALUES (?, ?, ?, ?)', (shift_id, seq, a, b))
    return seq


@salary_bp.route('/shift_types/add', methods=['POST'])
@login_required
@require_permission('salary.settings')
def add_shift_type():
    try:
        name = request.form['name']
        start_time = request.form['start_time']
        end_time = request.form['end_time']
        
        # حساب عدد الساعات تلقائياً
        try:
            from datetime import datetime
            fmt = '%H:%M'
            t1 = datetime.strptime(start_time, fmt)
            t2 = datetime.strptime(end_time, fmt)
            # إذا كان وقت النهاية أصغر من البداية، يعني دخلنا في اليوم التالي
            if t2 < t1:
                from datetime import timedelta
                t2 += timedelta(days=1)
                
            hours_diff = (t2 - t1).total_seconds() / 3600
            hours_per_day = round(hours_diff, 2)
        except Exception as e:
            print(f"Error calculating hours: {e}")
            hours_per_day = 8 # افتراضي
        
        # السماح للمستخدم بتعديل الساعات يدوياً إذا رغب
        if 'hours_per_day' in request.form and request.form['hours_per_day']:
            hours_per_day = float(request.form['hours_per_day'])
            
        description = request.form.get('description', '')
        
        is_split = 1 if request.form.get('is_split') else 0

        conn = get_db_connection()
        cur = conn.execute('''
            INSERT INTO shift_types (name, start_time, end_time, hours_per_day, description, is_split)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (name, start_time, end_time, hours_per_day, description, is_split))
        if is_split:
            n = _save_shift_periods(conn, cur.lastrowid, request.form)
            if n < 2:
                # شفت موسوم مقسّمًا بفترة واحدة أو بلا فترات لا معنى له،
                # ويجعل المحرّك يطالب ببصمات لا مقابل لها.
                conn.execute('UPDATE shift_types SET is_split = 0 WHERE id = ?',
                             (cur.lastrowid,))
            else:
                # ساعات اليوم مجموع الفترات لا المدى بينها
                total = 0
                for p in conn.execute(
                        'SELECT start_time, end_time FROM shift_periods '
                        'WHERE shift_type_id = ?', (cur.lastrowid,)).fetchall():
                    sh, sm = map(int, p['start_time'].split(':')[:2])
                    eh, em = map(int, p['end_time'].split(':')[:2])
                    diff = (eh * 60 + em) - (sh * 60 + sm)
                    if diff < 0:
                        diff += 24 * 60
                    total += diff
                conn.execute('UPDATE shift_types SET hours_per_day = ? WHERE id = ?',
                             (round(total / 60.0, 2), cur.lastrowid))
        conn.commit()
        pass # conn.close() removed to prevent leak in Flask g
        
        flash(gettext('x.f_shift_type_added'), 'success')
    except Exception as e:
        flash(gettext('x.f_error_colon') % {'p0': f'{e}'}, 'error')
        
    return redirect(url_for('salary.shift_types'))

@salary_bp.route('/shift_types/edit', methods=['POST'])
@login_required
@require_permission('salary.settings')
def edit_shift_type():
    try:
        shift_id = request.form['shift_id']
        name = request.form['name']
        start_time = request.form['start_time']
        end_time = request.form['end_time']
        
        hours_per_day = 8
        # حساب عدد الساعات تلقائياً
        try:
            from datetime import datetime, timedelta
            fmt = '%H:%M'
            t1 = datetime.strptime(start_time, fmt)
            t2 = datetime.strptime(end_time, fmt)
            if t2 < t1:
                t2 += timedelta(days=1)
            hours_per_day = round((t2 - t1).total_seconds() / 3600, 2)
        except Exception as e:
            print(f"Error calculating hours: {e}")

        # السماح للمستخدم بتعديل الساعات يدوياً إذا رغب
        if request.form.get('hours_per_day'):
             hours_per_day = float(request.form['hours_per_day'])
             
        description = request.form.get('description', '')
        
        conn = get_db_connection()
        conn.execute('''
            UPDATE shift_types 
            SET name=?, start_time=?, end_time=?, hours_per_day=?, description=?
            WHERE id=?
        ''', (name, start_time, end_time, hours_per_day, description, shift_id))
        # الشفت المقسّم: الحفظ يعكس النموذج بالضبط. وشفتٌ يُوسم مقسّمًا
        # بأقلّ من فترتين يُلغى تقسيمه، لأنه يجعل المحرّك يطالب ببصمات لا
        # مقابل لها.
        is_split = 1 if request.form.get('is_split') else 0
        conn.execute('UPDATE shift_types SET is_split = ? WHERE id = ?',
                     (is_split, shift_id))
        if is_split:
            n = _save_shift_periods(conn, shift_id, request.form)
            if n < 2:
                conn.execute('UPDATE shift_types SET is_split = 0 WHERE id = ?',
                             (shift_id,))
            else:
                total = 0
                for p in conn.execute(
                        'SELECT start_time, end_time FROM shift_periods '
                        'WHERE shift_type_id = ?', (shift_id,)).fetchall():
                    sh_, sm_ = map(int, p['start_time'].split(':')[:2])
                    eh_, em_ = map(int, p['end_time'].split(':')[:2])
                    diff = (eh_ * 60 + em_) - (sh_ * 60 + sm_)
                    if diff < 0:
                        diff += 24 * 60
                    total += diff
                conn.execute('UPDATE shift_types SET hours_per_day = ? WHERE id = ?',
                             (round(total / 60.0, 2), shift_id))
        else:
            conn.execute('DELETE FROM shift_periods WHERE shift_type_id = ?',
                         (shift_id,))
        conn.commit()
        pass # conn.close() removed to prevent leak in Flask g
        flash(gettext('x.f_shift_updated'), 'success')
    except Exception as e:
        flash(gettext('x.f_edit_error') % {'p0': f'{e}'}, 'error')
        
    return redirect(url_for('salary.shift_types'))

@salary_bp.route('/salary_settings_v2', methods=['GET', 'POST'])
@login_required
def salary_settings_v2_route():
    """تم توحيد الإعدادات: تحويل للصفحة الموحدة"""
    return redirect(url_for('main.settings'))

