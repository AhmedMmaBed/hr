import sqlite3
from flask import session, Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_babel import gettext
from datetime import datetime
from utils.db import get_db_connection
from utils.auth import login_required
from utils.rbac import require_permission

eos_bp = Blueprint('eos', __name__)

EOS_FULL_REASONS = ('termination', 'retirement', 'contract_end',
                    'death', 'disability')


def _eos_fraction(reason, years):
    """Kuwaiti entitlement fraction: resignation tiers, otherwise full."""
    if reason != 'resignation':
        return 1.0
    if years < 3:
        return 0.0
    if years < 5:
        return 0.5
    if years < 10:
        return 2.0 / 3.0
    return 1.0


def compute_kuwait_eos(conn, employee_id, termination_date, reason,
                       leave_days=None):
    """Kuwaiti end-of-service on the /26 basis: 15 days/year for the first
    five years, one month/year afterwards, 18-month cap, pro-rata
    fractions. Wage = basic + active fixed recurring earnings. Leave
    balance pays at the daily rate outside the resignation fraction.
    Outstanding loans are deducted and settled."""
    emp = conn.execute('SELECT * FROM employees WHERE id = ?',
                       (employee_id,)).fetchone()
    if not emp:
        return None
    try:
        hire = datetime.strptime(str(emp['hire_date'])[:10], '%Y-%m-%d')
        term = datetime.strptime(str(termination_date)[:10], '%Y-%m-%d')
    except (TypeError, ValueError):
        return None
    days = (term - hire).days
    if days <= 0:
        return None
    years = days / 365.0

    from utils.payroll_engine import fetch_fixed_earnings
    basic = float(emp['salary'] or 0)
    wage_lines = [{'name_ar': 'الراتب الأساسي', 'name_en': 'Basic salary',
                   'amount': round(basic, 3)}]
    wage = basic
    for it in fetch_fixed_earnings(conn, employee_id, basic, as_of=termination_date):
        wage += it['amount']
        wage_lines.append({'name_ar': it['name_ar'], 'name_en': it['name_en'],
                           'amount': it['amount']})
    daily = wage / 26.0

    tier1_days = min(years, 5.0) * 15.0
    tier2_days = max(0.0, years - 5.0) * 26.0
    raw_days = tier1_days + tier2_days
    cap_days = 18 * 26.0
    capped = raw_days > cap_days
    pay_days = min(raw_days, cap_days)
    gross = pay_days * daily
    fraction = _eos_fraction(reason, years)
    gratuity = gross * fraction

    from utils.leave_balance import compute_leave_balance
    _bal = compute_leave_balance(conn, employee_id, as_of=termination_date)
    suggested_leave = max(0.0, _bal['balance']) if _bal else 0.0
    use_leave = suggested_leave if leave_days is None else max(0.0,
                                                              float(leave_days))
    leave_amount = use_leave * daily

    loans = []
    loans_total = 0.0
    for ln in conn.execute("""SELECT id, principal FROM employee_loans
                              WHERE employee_id = ? AND status = 'active'""",
                           (employee_id,)).fetchall():
        paid = conn.execute('SELECT COALESCE(SUM(amount),0) FROM loan_payments '
                            'WHERE loan_id = ?', (ln['id'],)).fetchone()[0]
        rem = round(float(ln['principal'] or 0) - float(paid or 0), 3)
        if rem > 0:
            loans.append({'loan_id': ln['id'], 'remaining': rem})
            loans_total += rem

    net = gratuity + leave_amount - loans_total
    return {'employee_id': employee_id,
            'wage_lines': wage_lines,
            'monthly_wage': round(wage, 3),
            'daily_rate': round(daily, 3),
            'basis': '26',
            'service_days': days,
            'years': round(years, 4),
            'tier1_days': round(tier1_days, 2),
            'tier2_days': round(tier2_days, 2),
            'capped': capped,
            'pay_days': round(pay_days, 2),
            'gross_gratuity': round(gross, 3),
            'fraction': round(fraction, 4),
            'gratuity': round(gratuity, 3),
            'suggested_leave_days': suggested_leave,
            'leave_days': round(use_leave, 2),
            'leave_amount': round(leave_amount, 3),
            'loans': loans,
            'loans_total': round(loans_total, 3),
            'net': round(net, 3)}

@eos_bp.route('/eos/terminate', methods=['GET', 'POST'])
@login_required
@require_permission('page.eos')
def terminate_employee():
    conn = get_db_connection()
    if request.method == 'POST':
        employee_id = request.form.get('employee_id')
        termination_date = request.form.get('termination_date')
        reason = request.form.get('reason')
        notes = request.form.get('notes')
        
        emp = conn.execute("SELECT * FROM employees WHERE id = ?", (employee_id,)).fetchone()
        if not emp:
            flash(gettext('x.f_employee_not_found'), "danger")
            return redirect(url_for('eos.terminate_employee'))
            
        ld = request.form.get('leave_days')
        calc = compute_kuwait_eos(conn, int(employee_id), termination_date,
                                  reason,
                                  leave_days=(None if ld in (None, '')
                                              else ld))
        if not calc:
            flash(gettext('x.f_eos_bad_dates'), "danger")
            return redirect(url_for('eos.terminate_employee'))

        import json as _j
        cur = conn.execute('''
            INSERT INTO end_of_service_records
            (employee_id, termination_date, reason, years_of_service,
             reward_amount, net_payout, notes, created_by,
             monthly_wage, daily_rate, basis, tier1_days, tier2_days,
             capped, fraction, gratuity_amount, leave_days, leave_amount,
             loans_deducted, breakdown_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (employee_id, termination_date, reason, calc['years'],
              calc['gratuity'], calc['net'], notes, session.get('user_id'),
              calc['monthly_wage'], calc['daily_rate'], calc['basis'],
              calc['tier1_days'], calc['tier2_days'],
              1 if calc['capped'] else 0, calc['fraction'],
              calc['gratuity'], calc['leave_days'], calc['leave_amount'],
              calc['loans_total'], _j.dumps(calc, ensure_ascii=False)))
        for ln in calc['loans']:
            conn.execute('''INSERT INTO loan_payments
                            (loan_id, month, year, amount, source, created_by)
                            VALUES (?, ?, ?, ?, 'eos', ?)''',
                         (ln['loan_id'], int(str(termination_date)[5:7]),
                          int(str(termination_date)[:4]), ln['remaining'],
                          session.get('user_id')))
            conn.execute("UPDATE employee_loans SET status = 'settled' "
                         "WHERE id = ?", (ln['loan_id'],))
        conn.execute("UPDATE employees SET is_active = 0, "
                     "end_of_service_date = ? WHERE id = ?",
                     (termination_date, employee_id))
        try:
            from utils.payroll_engine import log_employee_event
            log_employee_event(conn, int(employee_id), 'end_of_service_date',
                               '', str(termination_date), 'status',
                               user_id=session.get('user_id'),
                               source='eos_settlement',
                               note=gettext('x.' + reason) if reason else None)
        except Exception as _e:
            print(f'audit log failed on EOS: {_e}')
        conn.commit()
        pass # conn.close() removed to prevent leak in Flask g
        
        flash(gettext('x.f_eos_saved'), "success")
        return redirect(url_for('eos.list_eos'))
        
    # GET Request
    employees = conn.execute("SELECT * FROM employees WHERE is_active = 1").fetchall()
    pass # conn.close() removed to prevent leak in Flask g
    return render_template('eos/terminate.html', employees=employees)

@eos_bp.route('/api/eos/calculate')
@login_required
@require_permission('salary.calculate')
def api_calculate_eos():
    emp_id = request.args.get('employee_id')
    term_date = request.args.get('termination_date')
    reason = request.args.get('reason')
    
    if not emp_id or not term_date or not reason:
        return jsonify({"error": "Missing parameters"}), 400
        
    conn = get_db_connection()
    ld = request.args.get('leave_days')
    calc = compute_kuwait_eos(conn, int(emp_id), term_date, reason,
                              leave_days=(None if ld in (None, '') else ld))
    if not calc:
        return jsonify({"error": "Employee not found or bad dates"}), 404
    return jsonify({"years_worked": calc['years'],
                    "reward_amount": calc['gratuity'],
                    "calc": calc})

@eos_bp.route('/eos/list')
@login_required
@require_permission('page.eos')
def list_eos():
    conn = get_db_connection()
    records = conn.execute('''
        SELECT eos.*, e.name, e.employee_number, e.department 
        FROM end_of_service_records eos
        JOIN employees e ON eos.employee_id = e.id
        ORDER BY eos.termination_date DESC
    ''').fetchall()
    pass # conn.close() removed to prevent leak in Flask g
    return render_template('eos/list.html', records=records)


@eos_bp.route('/eos/print/<int:rec_id>')
@login_required
@require_permission('salary.view')
def print_eos(rec_id):
    import json as _j
    conn = get_db_connection()
    rec = conn.execute("""SELECT eos.*, e.name, e.arabic_name,
                                 e.employee_number, e.department, e.position,
                                 e.hire_date
                          FROM end_of_service_records eos
                          JOIN employees e ON e.id = eos.employee_id
                          WHERE eos.id = ?""", (rec_id,)).fetchone()
    if not rec:
        return 'record not found', 404
    keys = rec.keys()
    raw = rec['breakdown_json'] if 'breakdown_json' in keys else None
    calc = _j.loads(raw or '{}')
    # A record saved before the detailed-breakdown schema existed carries only
    # its headline figures. Printing NULLs as 0.000 next to a real net payout
    # produces a self-contradicting document, so such records are flagged and
    # the empty detail sections are suppressed instead.
    legacy = not calc or (('monthly_wage' not in keys) or rec['monthly_wage'] is None)
    from utils.settings_utils import get_system_settings
    sysx = get_system_settings() or {}
    return render_template('eos/print.html', r=rec, c=calc, legacy=legacy,
                           company=sysx.get('company_name', ''),
                           currency=sysx.get('currency_symbol', ''))
