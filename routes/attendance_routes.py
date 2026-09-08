from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_babel import gettext
import threading
import json
from utils.db import get_db_connection
from utils.db import get_db_connection
from utils.auth import login_required
from utils.rbac import require_permission
from utils.fingerprint_utils import test_fingerprint_device, test_duplicate_prevention, get_sync_logs, upload_user_to_device, clear_fingerprint_device_users, get_device_user_count, reboot_fingerprint_device, factory_reset_fingerprint_device
from fingerprint_sync import sync_all_fingerprint_devices, sync_users_to_employees
from utils.license import get_effective_max_devices
from utils.oracle_db import get_sync_queue_stats

try:
    from zk import ZK, const
    FINGERPRINT_AVAILABLE = True
except ImportError:
    FINGERPRINT_AVAILABLE = False

attendance_bp = Blueprint('attendance', __name__)

@attendance_bp.route('/fingerprint')
@login_required
@require_permission('page.attendance')
def fingerprint():
    """صفحة إدارة نظام البصمة"""
    try:
        if not FINGERPRINT_AVAILABLE:
            flash(gettext('x.f_fp_unavailable_pyzk'), 'error')
            return redirect(url_for('main.index'))
        
        conn = get_db_connection()
        try:
            devices = conn.execute('SELECT * FROM fingerprint_devices ORDER BY device_name').fetchall()
        except Exception as e:
            print(f"خطأ في جلب الأجهزة: {e}")
            devices = []
        finally:
            pass # conn.close() removed to prevent leak in Flask g
        
        try:
            sync_logs = get_sync_logs(50)
        except Exception as e:
            print(f"خطأ في جلب سجلات المزامنة: {e}")
            sync_logs = []
        
        return render_template(
            'fingerprint_dashboard.html',
            devices=[dict(d) for d in devices], 
            sync_logs=sync_logs,
            fingerprint_available=FINGERPRINT_AVAILABLE
        )
    except Exception as e:
        print(f"خطأ في route /fingerprint: {e}")
        flash(gettext('x.f_error_occurred') % {'p0': f'{str(e)}'}, 'error')
        return redirect(url_for('main.index'))

@attendance_bp.route('/fingerprint/devices')
@login_required
@require_permission('attendance.devices')
def devices():
    conn = get_db_connection()
    devices = conn.execute('SELECT * FROM fingerprint_devices').fetchall()
    current_active = conn.execute('SELECT COUNT(*) FROM fingerprint_devices WHERE is_active = 1').fetchone()[0]
    pass # conn.close() removed to prevent leak in Flask g
    
    try:
        max_devices = get_effective_max_devices()
    except:
        max_devices = 3
        
    return render_template('fingerprint_devices.html', devices=devices, max_devices=max_devices, current_active=current_active)

@attendance_bp.route('/fingerprint/devices/add', methods=['POST'])
@login_required
@require_permission('attendance.devices')
def add_device():
    conn = get_db_connection()
    
    # Enforce device limit on active devices
    try:
        max_devices = get_effective_max_devices()
        current_active = conn.execute('SELECT COUNT(*) FROM fingerprint_devices WHERE is_active = 1').fetchone()[0]
        
        if current_active >= max_devices:
            pass # conn.close() removed to prevent leak in Flask g
            flash(gettext('x.f_max_active_devices') % {'p0': f'{max_devices}'}, 'error')
            return redirect(url_for('attendance.devices'))
    except Exception as e:
        print(f"Error checking device limit: {e}")
        
    device_name = request.form.get('device_name')
    device_ip = request.form.get('device_ip')
    device_port = int(request.form.get('device_port', 4370))
    is_adms = 1 if 'is_adms' in request.form else 0
    
    try:
        conn.execute('''
            INSERT INTO fingerprint_devices (device_name, device_ip, device_port, is_active, is_adms)
            VALUES (?, ?, ?, 1, ?)
        ''', (device_name, device_ip, device_port, is_adms))
        conn.commit()
        
        # If ADMS, remove from pending list if exists
        if is_adms:
            # Assuming we match by IP if name is used as SN??
            # Or try to delete by IP
             conn.execute('DELETE FROM pending_adms_devices WHERE ip_address = ?', (device_ip,))
             conn.commit()
             
        flash(gettext('x.f_device_added'), 'success')
    except Exception as e:
        flash(gettext('x.f_error_colon') % {'p0': f'{e}'}, 'error')
    finally:
        pass # conn.close() removed to prevent leak in Flask g
        
    return redirect(url_for('attendance.devices'))

@attendance_bp.route('/fingerprint/devices/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@require_permission('attendance.devices')
def edit_device(id):
    conn = get_db_connection()
    
    if request.method == 'POST':
        device_name = request.form.get('device_name')
        device_ip = request.form.get('device_ip')
        device_port = int(request.form.get('device_port', 4370))
        is_active = 1 if 'is_active' in request.form else 0
        is_adms = 1 if 'is_adms' in request.form else 0
        
        # Enforce limit if activating
        if is_active == 1:
            try:
                from utils.db import get_setting
                max_devices = get_effective_max_devices()
                current_active = conn.execute('SELECT COUNT(*) FROM fingerprint_devices WHERE is_active = 1 AND id != ?', (id,)).fetchone()[0]
                if current_active >= max_devices:
                    is_active = 0
                    flash(gettext('x.f_device_saved_inactive') % {'p0': f'{max_devices}'}, 'warning')
            except Exception as e:
                print(e)
                
        try:
            conn.execute('''
                UPDATE fingerprint_devices 
                SET device_name = ?, device_ip = ?, device_port = ?, is_active = ?, is_adms = ?
                WHERE id = ?
            ''', (device_name, device_ip, device_port, is_active, is_adms, id))
            conn.commit()
            flash(gettext('x.f_device_updated'), 'success')
        except Exception as e:
            flash(gettext('x.f_error_colon') % {'p0': f'{e}'}, 'error')
        finally:
            pass # conn.close() removed to prevent leak in Flask g
        return redirect(url_for('attendance.devices'))
        
    device = conn.execute('SELECT * FROM fingerprint_devices WHERE id = ?', (id,)).fetchone()
    pass # conn.close() removed to prevent leak in Flask g
    
    # Return JSON for AJAX requests (used by the modal in fingerprint_devices.html)
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.headers.get('Accept') == 'application/json':
        return jsonify(dict(device))
        
    return render_template('edit_device.html', device=device)

@attendance_bp.route('/fingerprint/devices/toggle/<int:id>')
@login_required
@require_permission('attendance.devices')
def toggle_device(id):
    conn = get_db_connection()
    device = conn.execute('SELECT is_active FROM fingerprint_devices WHERE id = ?', (id,)).fetchone()
    
    if device and not device['is_active']:
        try:
            from utils.db import get_setting
            max_devices = get_effective_max_devices()
            current_active = conn.execute('SELECT COUNT(*) FROM fingerprint_devices WHERE is_active = 1').fetchone()[0]
            if current_active >= max_devices:
                pass # conn.close() removed to prevent leak in Flask g
                flash(gettext('x.f_cannot_activate_device') % {'p0': f'{max_devices}'}, 'error')
                return redirect(url_for('attendance.devices'))
        except Exception as e:
            print(e)
            
    conn.execute('UPDATE fingerprint_devices SET is_active = NOT is_active WHERE id = ?', (id,))
    conn.commit()
    pass # conn.close() removed to prevent leak in Flask g
    flash(gettext('x.f_device_status_changed'), 'success')
    return redirect(url_for('attendance.devices'))

@attendance_bp.route('/fingerprint/devices/delete/<int:id>')
@login_required
@require_permission('attendance.devices')
def delete_device(id):
    conn = get_db_connection()
    conn.execute('DELETE FROM fingerprint_devices WHERE id = ?', (id,))
    conn.commit()
    pass # conn.close() removed to prevent leak in Flask g
    flash(gettext('x.f_device_deleted'), 'success')
    return redirect(url_for('attendance.devices'))

@attendance_bp.route('/fingerprint/test/<int:device_id>')
@login_required
@require_permission('attendance.devices')
def test_device_api(device_id):
    success, result = test_fingerprint_device(device_id)
    
    if success:
        return jsonify({
            'success': True,
            'message': 'تم الاتصال بالجهاز بنجاح',
            'device_info': result
        })
    else:
        return jsonify({
            'success': False,
            'message': result
        })

@attendance_bp.route('/fingerprint/devices/<int:device_id>/test')
@login_required
@require_permission('attendance.devices')
def test_device_route(device_id):
    success, message = test_fingerprint_device(device_id)
    if success:
        flash(message, 'success')
    else:
        flash(message, 'error')
    return redirect(url_for('attendance.devices'))

@attendance_bp.route('/fingerprint/sync', methods=['POST'])
@login_required
@require_permission('attendance.devices')
def sync_fingerprint():
    # يمكن تحديد جهاز معين أو المزامنة للكل
    device_id = request.form.get('device_id')
    
    # تشغيل المزامنة
    # ملاحظة: هذا قد يأخذ وقتاً طويلاً، يفضل نقله لـ thread منفصل وإرجاع ID للمتابعة
    # ولكن للتبسيط سنبقيه كما هو حالياً مع استدعاء دالة المزامنة
    try:
        # إذا تم تحديد جهاز نمرره للدالة (تحتاج تعديل لاستقباله)
        # حالياً الدالة sync_all_fingerprint_devices تزامن الكل
        result = sync_all_fingerprint_devices()
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'message': f'خطأ: {str(e)}'})

@attendance_bp.route('/fingerprint/sync_users', methods=['POST'])
@login_required
@require_permission('attendance.devices')
def sync_users():
    # تشغيل في thread منفصل لتجنب تجميد الواجهة
    thread = threading.Thread(target=sync_users_to_employees)
    thread.start()
    
    return jsonify({
        'success': True, 
        'message': 'بدأت عملية مزامنة المستخدمين في الخلفية'
    })

@attendance_bp.route('/fingerprint/test_duplicate_prevention')
@login_required
@require_permission('attendance.devices')
def test_duplicate_route():
    result = test_duplicate_prevention()
    return jsonify(result)
@attendance_bp.route('/fingerprint/upload_users')
@login_required
@require_permission('attendance.devices')
def upload_users_page():
    conn = get_db_connection()
    # Fetch employees with their template counts
    employees = conn.execute('''
        SELECT e.*, 
        (SELECT COUNT(*) FROM fingerprint_templates WHERE pin = e.employee_number) as fp_count,
        (SELECT COUNT(*) FROM fingerprint_faces WHERE pin = e.employee_number) as face_count
        FROM employees e 
        WHERE e.is_active = 1 
        ORDER BY e.name
    ''').fetchall()
    devices = conn.execute('SELECT * FROM fingerprint_devices WHERE is_active = 1 ORDER BY device_name').fetchall()
    pass # conn.close() removed to prevent leak in Flask g
    return render_template('fingerprint_device_users.html', employees=employees, devices=devices, active_tab='upload')

@attendance_bp.route('/fingerprint/api/upload_users', methods=['POST'])
@login_required
@require_permission('attendance.devices')
def upload_users_api():
    data = request.json
    user_ids = data.get('user_ids', [])
    device_ids = data.get('device_ids', [])
    
    if not user_ids or not device_ids:
        return jsonify({'success': False, 'message': 'يجب اختيار مستخدم واحد وجهاز واحد على الأقل'})
        
    conn = get_db_connection()
    results = []
    
    # Pre-fetch employees to avoid repeated DB calls
    placeholders = ','.join('?' * len(user_ids))
    employees = conn.execute(f'SELECT * FROM employees WHERE id IN ({placeholders})', user_ids).fetchall()
    pass # conn.close() removed to prevent leak in Flask g
    
    for device_id in device_ids:
        device_results = {'device_id': device_id, 'success_count': 0, 'fail_count': 0, 'details': []}
        
        # Check device type
        conn = get_db_connection()
        device_info = conn.execute('SELECT is_adms FROM fingerprint_devices WHERE id = ?', (device_id,)).fetchone()
        pass # conn.close() removed to prevent leak in Flask g
        
        is_adms = device_info['is_adms'] if device_info else 0
        
        for emp in employees:
            # Prepare user data
            try:
                card_num = int(emp['card_number']) if emp['card_number'] and str(emp['card_number']).isdigit() else 0
            except:
                card_num = 0
                
            user_data = {
                'uid': int(emp['id']), # Try to use DB ID as internal UID
                'user_id': str(emp['employee_number']), 
                'name': emp['name'],
                'privilege': int(emp['privilege']) if emp['privilege'] is not None else 0,
                'password': str(emp['password']) if emp['password'] else '',
                'card': card_num,
                'group_id': str(emp['group_id']) if emp['group_id'] else '1'
            }
            
            if is_adms:
                # Queue Command
                import json
                payload = {
                    'PIN': user_data['user_id'],
                    'Name': user_data['name'],
                    'Pri': user_data['privilege'],
                    'Passwd': user_data['password'],
                    'Card': user_data['card'],
                    'Grp': user_data['group_id']
                }
                
                try:
                    conn = get_db_connection()
                    
                    # 1. Queue User Info
                    conn.execute('''
                        INSERT INTO adms_commands (device_id, command_type, payload, status)
                        VALUES (?, 'DATA UPDATE USERINFO', ?, 'PENDING')
                    ''', (device_id, json.dumps(payload)))
                    
                    # 2. Queue Templates (Fingerprint/Face)
                    # Fetch stored templates for this user (PIN)
                    # Note: We use the employee_number (user_id) as PIN
                    user_pin = user_data['user_id']
                    templates = conn.execute('SELECT * FROM fingerprint_templates WHERE pin = ?', (user_pin,)).fetchall()
                    
                    for t in templates:
                        # Construct payload for template
                        # Command: DATA UPDATE FINGERTMP PIN=... FingerID=... Size=... Valid=... Template=...
                        # We use generic ADMS command type 'UPDATE_TEMPLATE' or raw construction in adms_routes?
                        # adms_routes.py currently handles ADD_USER and DELETE_USER and otherwise sends raw.
                        # So we can set command_type to 'DATA UPDATE FINGERTMP ...' directly or handle it.
                        # Let's use 'RAW' or specific type.
                        # Let's use command_type='UPDATE_TEMPLATE' and handle in adms_routes OR just build payload here?
                        # adms_routes handles: if type == 'ADD_USER'... else: cmd_str = f"C:{id}:{type}"
                        
                        # So if we put the full command body in `command_type`, it will work.
                        # Body: DATA UPDATE FINGERTMP PIN=...
                        
                        tmp_data = t['template_data']
                        size = len(tmp_data) 
                        
                        fp_payload = {
                            'PIN': t['pin'],
                            'FingerID': t['finger_id'],
                            'Size': size,
                            'Template': tmp_data,
                            'Type': t['template_type'] or '1'
                        }
                        
                        conn.execute('''
                            INSERT INTO adms_commands (device_id, command_type, payload, status)
                            VALUES (?, 'DATA UPDATE BIODATA', ?, 'PENDING')
                        ''', (device_id, json.dumps(fp_payload)))

                    conn.commit()
                    pass # conn.close() removed to prevent leak in Flask g
                    
                    device_results['success_count'] += 1
                    device_results['details'].append({
                        'user': emp['name'],
                        'status': 'success',
                        'message': f'User & {len(templates)} Templates Queued'
                    })
                except Exception as e:
                    # Closing conn if error occurred before commit/close?
                    # Python sqlite3 context manager or manual close 
                    # Use separated try/finally if strict, but here local scope.
                    try: pass # conn.close() removed to prevent leak in Flask g 
                    except: pass
                    
                    device_results['fail_count'] += 1
                    device_results['details'].append({
                        'user': emp['name'],
                        'status': 'error',
                        'message': f'Queue Error: {str(e)}'
                    })
            else:
                # Normal Direct Connection
                res = upload_user_to_device(device_id, user_data)
                status = 'success' if res['success'] else 'error'
                if res['success']:
                    device_results['success_count'] += 1
                else:
                    device_results['fail_count'] += 1
                    
                device_results['details'].append({
                    'user': emp['name'],
                    'status': status,
                    'message': res['message']
                })
            
        results.append(device_results)
        
    return jsonify({'success': True, 'results': results})

@attendance_bp.route('/fingerprint/sync_from_device')
@login_required
@require_permission('attendance.edit')
def sync_from_device():
    """صفحة سحب البيانات من الأجهزة"""
    conn = get_db_connection()
    devices = conn.execute('SELECT * FROM fingerprint_devices WHERE is_adms = 1').fetchall()
    
    # Fetch employees to allow specific selection
    employees = conn.execute('''
        SELECT e.*, 
               (SELECT COUNT(*) FROM fingerprint_templates WHERE pin = e.employee_number AND template_type = 1) as fp_count,
               (SELECT COUNT(*) FROM fingerprint_templates WHERE pin = e.employee_number AND template_type = 9) as face_count
        FROM employees e 
        WHERE is_active = 1 
        ORDER BY employee_number
    ''').fetchall()
    
    pass # conn.close() removed to prevent leak in Flask g
    return render_template('fingerprint_device_users.html', devices=devices, employees=[dict(e) for e in employees], active_tab='pull')

@attendance_bp.route('/fingerprint/api/sync_from_device', methods=['POST'])
@login_required
@require_permission('attendance.edit')
def sync_from_device_api():
    """جدولة أوامر سحب البيانات من أجهزة ADMS"""
    data = request.get_json()
    device_ids = data.get('device_ids', [])
    user_pins = data.get('user_pins', []) # New: specific pins to sync
    
    if not device_ids:
        return jsonify({'success': False, 'message': 'لم يتم اختيار أي أجهزة'})
        
    conn = get_db_connection()
    results = []
    
    try:
        for dev_id in device_ids:
            if user_pins:
                # Sync specific users
                for pin in user_pins:
                    # Query UserInfo for this pin
                    conn.execute('''
                        INSERT INTO adms_commands (device_id, command_type, payload, status)
                        VALUES (?, 'DATA QUERY USERINFO', ?, 'PENDING')
                    ''', (dev_id, json.dumps({"PIN": str(pin)})))
                    
                    # Query Templates for this pin
                    conn.execute('''
                        INSERT INTO adms_commands (device_id, command_type, payload, status)
                        VALUES (?, 'DATA QUERY FINGERTMP', ?, 'PENDING')
                    ''', (dev_id, json.dumps({"PIN": str(pin)})))
                    
                    # Query Face for this pin
                    conn.execute('''
                        INSERT INTO adms_commands (device_id, command_type, payload, status)
                        VALUES (?, 'DATA QUERY FACE', ?, 'PENDING')
                    ''', (dev_id, json.dumps({"PIN": str(pin)})))
                
                msg = f'تمت جدولة سحب لـ {len(user_pins)} موظف'
            else:
                # Bulk sync (All users)
                conn.execute('''
                    INSERT INTO adms_commands (device_id, command_type, payload, status)
                    VALUES (?, 'DATA QUERY USERINFO', '{"PIN": ""}', 'PENDING')
                ''', (dev_id,))
                conn.execute('''
                    INSERT INTO adms_commands (device_id, command_type, payload, status)
                    VALUES (?, 'DATA QUERY FINGERTMP', '{"PIN": ""}', 'PENDING')
                ''', (dev_id,))
                conn.execute('''
                    INSERT INTO adms_commands (device_id, command_type, payload, status)
                    VALUES (?, 'DATA QUERY FACE', '{"PIN": ""}', 'PENDING')
                ''', (dev_id,))
                msg = 'تمت جدولة سحب كافة الموظفين والبصمات'
            
            results.append({
                'device_id': dev_id,
                'success': True,
                'message': msg
            })
            
        conn.commit()
        return jsonify({'success': True, 'results': results})
    except Exception as e:
        return jsonify({'success': False, 'message': f'خطأ في الجدولة: {str(e)}'})
    finally:
        pass # conn.close() removed to prevent leak in Flask g

@attendance_bp.route('/fingerprint/api/adms/search')
@login_required
@require_permission('attendance.devices')
def search_adms_devices_api():
    """
    Return list of pending ADMS devices that contacted the server.
    """
    conn = get_db_connection()
    devices = conn.execute('SELECT * FROM pending_adms_devices ORDER BY last_activity DESC').fetchall()
    pass # conn.close() removed to prevent leak in Flask g
    
    return jsonify({
        'success': True,
        'devices': [dict(d) for d in devices]
    })

@attendance_bp.route('/fingerprint/api/scan_network')
@login_required
@require_permission('attendance.devices')
def scan_network_api():
    from utils.find_device_utils import NetworkScanner
    from flask import Response, stream_with_context
    import json
    
    mode = request.args.get('mode', 'local')
    scanner = NetworkScanner()
    
    subnets = None
    if mode == 'wide':
        subnets = ['192.168.0.0/16']
    
    def generate():
        # Generator for SSE
        for event in scanner.scan_yield(subnets=subnets):
            yield f"data: {json.dumps(event)}\n\n"
            
    return Response(stream_with_context(generate()), mimetype='text/event-stream')

@attendance_bp.route('/fingerprint/api/devices/bulk_add', methods=['POST'])
@login_required
@require_permission('attendance.devices')
def add_devices_bulk():
    data = request.json
    # Support both 'ips' (list of strings) and 'devices' (list of dicts {ip, name})
    devices = data.get('devices', [])
    if not devices and 'ips' in data:
        # Backwards compatibility or simple format
        devices = [{'ip': ip, 'name': f"Device {ip}"} for ip in data['ips']]
    
    if not devices:
        return jsonify({'success': False, 'message': 'لم يتم اختيار أي جهاز'})
        
    conn = get_db_connection()
    success_count = 0
    errors = []
    
    # Check limit before starting
    try:
        max_limit = get_effective_max_devices()
        current_active = conn.execute('SELECT COUNT(*) FROM fingerprint_devices WHERE is_active = 1').fetchone()[0]
        
        if current_active >= max_limit:
            pass # conn.close() removed to prevent leak in Flask g
            return jsonify({'success': False, 'message': f'عفواً، لقد وصلت للحد الأقصى المسموح به للأجهزة ({max_limit})'})
    except Exception as e:
        print(f"Error checking limit in bulk add: {e}")
        max_limit = 3 # Fallback

    for dev in devices:
        # Check limit inside loop too in case multiple were selected
        try:
             count_now = conn.execute('SELECT COUNT(*) FROM fingerprint_devices WHERE is_active = 1').fetchone()[0]
             if count_now >= max_limit:
                 errors.append(f"تم الوصول للحد الأقصى ({max_limit}). لم يتم إضافة باقي الأجهزة المختارة.")
                 break
        except: pass

        ip = dev.get('ip')
        name = dev.get('name') or f"Device {ip}"
        
        try:
            # Check if exists
            existing = conn.execute('SELECT id FROM fingerprint_devices WHERE device_ip = ?', (ip,)).fetchone()
            if not existing:
                conn.execute('''
                    INSERT INTO fingerprint_devices (device_name, device_ip, device_port, is_active)
                    VALUES (?, ?, ?, 1)
                ''', (name, ip, 4370))
                success_count += 1
            else:
                errors.append(f"{ip}: موجود مسبقاً")
        except Exception as e:
            errors.append(f"{ip}: {str(e)}")
            
    conn.commit()
    pass # conn.close() removed to prevent leak in Flask g
    return jsonify({
        'success': success_count > 0, 
        'count': success_count, 
        'message': f'تم إضافة {success_count} جهاز بنجاح',
        'errors': errors
    })

@attendance_bp.route('/api/devices/limit', methods=['GET', 'POST'])
@login_required
@require_permission('attendance.devices')
def manage_device_limit():
    """API endpoint to view and control the allowed number of fingerprint devices"""
    from utils.db import get_setting, set_setting
    
    if request.method == 'POST':
        data = request.get_json()
        if not data or 'max_devices' not in data:
            return jsonify({'success': False, 'message': 'بيانات غير مكتملة'}), 400
            
        try:
            new_limit = int(data['max_devices'])
            if new_limit < 1:
                return jsonify({'success': False, 'message': 'الحد الأدنى هو 1'}), 400
                
            set_setting('max_devices', str(new_limit))
            return jsonify({'success': True, 'message': f'تم تحديث الحد الأقصى للأجهزة إلى {new_limit}'})
        except ValueError:
            return jsonify({'success': False, 'message': 'قيمة غير صالحة'}), 400
            
    # GET Request
    conn = get_db_connection()
    total_devices = conn.execute('SELECT COUNT(*) FROM fingerprint_devices').fetchone()[0]
    active_devices = conn.execute('SELECT COUNT(*) FROM fingerprint_devices WHERE is_active = 1').fetchone()[0]
    pass # conn.close() removed to prevent leak in Flask g
    
    max_devices = get_effective_max_devices()
    
    return jsonify({
        'success': True,
        'total_devices': total_devices,
        'active_devices': active_devices,
        'max_devices': max_devices,
        'available_slots': max(0, max_devices - total_devices),
        'can_add_more': total_devices < max_devices
    })

@attendance_bp.route('/attendance/oracle')
@login_required
@require_permission('page.oracle_control')
def oracle_control():
    """مركز التحكم في تكامل أوراكل"""
    import os as _os
    from utils.db import get_setting
    from utils.oracle_db import get_sync_queue_stats
    enabled = str(get_setting('oracle_enabled', '1')).strip() == '1'
    stats = get_sync_queue_stats()
    from utils.oracle_db import _oracle_conf
    cfg = {
        'host': _oracle_conf('oracle_host', 'ORACLE_HOST', 'localhost'),
        'port': _oracle_conf('oracle_port', 'ORACLE_PORT', '1521'),
        'service': _oracle_conf('oracle_service', 'ORACLE_SERVICE', 'XEPDB1'),
        'user': _oracle_conf('oracle_user', 'ORACLE_USER', 'HR'),
        'table': _oracle_conf('oracle_table', 'ORACLE_TABLE_NAME', 'ATTENDANCE_LOGS'),
        'lib_dir': str(get_setting('oracle_lib_dir', '') or ''),
        'password_set': bool(str(get_setting('oracle_password', '') or '').strip()),
    }
    return render_template('oracle_control.html', enabled=enabled, stats=stats, cfg=cfg)

@attendance_bp.route('/attendance/oracle/toggle', methods=['POST'])
@login_required
@require_permission('attendance.edit')
def oracle_toggle():
    from utils.db import set_setting
    from utils import oracle_db as _odb
    new_state = '1' if request.form.get('enable') == '1' else '0'
    set_setting('oracle_enabled', new_state)
    _odb._oracle_enabled_cache['ts'] = 0
    if new_state == '1':
        flash(gettext('x.f_oracle_enabled'), 'success')
    else:
        flash(gettext('x.f_oracle_disabled'), 'success')
    return redirect(url_for('attendance.oracle_control'))

@attendance_bp.route('/attendance/oracle/settings', methods=['POST'])
@login_required
@require_permission('attendance.edit')
def oracle_save_settings():
    from utils.db import set_setting
    for form_key, setting_key in [('host', 'oracle_host'), ('port', 'oracle_port'),
                                  ('service', 'oracle_service'), ('user', 'oracle_user'),
                                  ('table', 'oracle_table'), ('lib_dir', 'oracle_lib_dir')]:
        set_setting(setting_key, request.form.get(form_key, '').strip())
    pw = request.form.get('password', '')
    if pw.strip():
        set_setting('oracle_password', pw.strip())
    flash(gettext('x.f_oracle_settings_saved'), 'success')
    return redirect(url_for('attendance.oracle_control'))

@attendance_bp.route('/attendance/api/oracle/test')
@login_required
@require_permission('attendance.view')
def oracle_test():
    from utils.oracle_db import check_oracle_connection, is_oracle_enabled
    if not is_oracle_enabled():
        return jsonify({'success': True, 'enabled': False, 'alive': False})
    return jsonify({'success': True, 'enabled': True, 'alive': bool(check_oracle_connection())})

@attendance_bp.route('/attendance/oracle/history')
@login_required
@require_permission('attendance.view')
def oracle_history():
    """Oracle Sync History Page"""
    import os
    conn = get_db_connection()
    # Get last 100 records
    history = conn.execute('''
        SELECT id, user_id, check_time, check_type, verify_code, device_ip, retry_count, status, created_at 
        FROM oracle_sync_queue 
        ORDER BY created_at DESC 
        LIMIT 100
    ''').fetchall()
    pass # conn.close() removed to prevent leak in Flask g
    
    table_name = os.environ.get("ORACLE_TABLE_NAME", "ATTENDANCE_LOGS")
    # For sync interval, we'll use a hardcoded 5 for now as per current logic, 
    # but we could also get it from settings if we added one.
    sync_interval = "5 دقائق"
    
    return render_template('oracle_history.html', 
                          history=history, 
                          table_name=table_name,
                          sync_interval=sync_interval)

@attendance_bp.route('/attendance/api/oracle/stats')
@login_required
@require_permission('attendance.view')
def oracle_stats():
    """API for Oracle Sync Dashboard"""
    from utils.oracle_db import check_oracle_connection
    conn = get_db_connection()
    
    stats = conn.execute('''
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN status = 'PENDING' THEN 1 ELSE 0 END) as pending,
            SUM(CASE WHEN status = 'SYNCED' THEN 1 ELSE 0 END) as synced,
            SUM(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END) as failed
        FROM oracle_sync_queue
    ''').fetchone()
    
    pass # conn.close() removed to prevent leak in Flask g
    
    is_online = check_oracle_connection()
    
    return jsonify({
        'success': True,
        'is_online': is_online,
        'stats': {
            'total': stats['total'] or 0,
            'pending': stats['pending'] or 0,
            'synced': stats['synced'] or 0,
            'failed': stats['failed'] or 0
        }
    })

@attendance_bp.route('/attendance/api/oracle/sync_missing', methods=['POST'])
@login_required
@require_permission('attendance.edit')
def sync_missing_to_oracle():
    """Find records in attendance_records not yet in oracle_sync_queue and add them"""
    from utils.oracle_db import is_oracle_enabled
    if not is_oracle_enabled():
        return jsonify({'success': False, 'message': gettext('x.oracle_is_disabled')})
    from utils.oracle_db import process_sync_queue
    import threading
    
    conn = get_db_connection()
    try:
        # Find records in attendance_records that don't exist in oracle_sync_queue
        # We join on employee_number as user_id, check_time, check_type
        missing_records = conn.execute('''
            SELECT 
                e.employee_number,
                ar.check_time, 
                ar.check_type, 
                ar.verify_code, 
                fd.device_ip
            FROM attendance_records ar
            JOIN employees e ON ar.employee_id = e.id
            LEFT JOIN oracle_sync_queue osq ON 
                osq.user_id = e.employee_number AND 
                osq.check_time = ar.check_time AND 
                osq.check_type = ar.check_type
            WHERE osq.id IS NULL
        ''').fetchall()
        
        added_count = 0
        for row in missing_records:
            conn.execute('''
                INSERT INTO oracle_sync_queue (user_id, check_time, check_type, verify_code, device_id, status)
                VALUES (?, ?, ?, ?, ?, 'PENDING')
            ''', (row['employee_number'], row['check_time'], row['check_type'], row['verify_code'], row['device_id']))
            added_count += 1
        
        conn.commit()
        
        if added_count > 0:
            # Trigger sync process in background
            thread = threading.Thread(target=process_sync_queue)
            thread.daemon = True
            thread.start()
            
        return jsonify({
            'success': True, 
            'message': f'تم إضافة {added_count} حركة جديدة إلى طابور المزامنة',
            'added_count': added_count
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})
    finally:
        pass # conn.close() removed to prevent leak in Flask g

@attendance_bp.route('/attendance/api/oracle/retry', methods=['POST'])
@login_required
@require_permission('attendance.edit')
def oracle_retry():
    """Trigger manual retry of pending records"""

    from utils.oracle_db import is_oracle_enabled
    if not is_oracle_enabled():
        return jsonify({'success': False, 'message': gettext('x.oracle_is_disabled')})
    from utils.oracle_db import process_sync_queue
    
    # Run in a separate thread so it doesn't block the UI
    import threading
    thread = threading.Thread(target=process_sync_queue)
    thread.daemon = True
    thread.start()
    
    return jsonify({'success': True, 'message': 'تم بدء محاولة المزامنة في الخلفية'})

@attendance_bp.route('/api/adms/sync_users/<int:device_id>', methods=['POST'])
@login_required
@require_permission('attendance.edit')
def adms_sync_users(device_id):
    """Queue commands to sync users for an ADMS device"""
    from utils.db import get_db_connection
    conn = get_db_connection()
    try:
        device = conn.execute('SELECT * FROM fingerprint_devices WHERE id = ?', (device_id,)).fetchone()
        if not device:
            return jsonify({'success': False, 'message': 'Device not found'})
        
        if not device['is_adms']:
            return jsonify({'success': False, 'message': 'Device is not an ADMS device'})

        import json
        
        # Get all employees
        employees = conn.execute('SELECT employee_number FROM employees WHERE is_active = 1').fetchall()
        
        if not employees:
            return jsonify({'success': False, 'message': 'لا يوجد موظفين نشطين لعمل مزامنة لهم'})
            
        count = 0
        for emp in employees:
            pin = str(emp['employee_number'])
            payload = json.dumps({"PIN": pin})
            
            # Request User Info
            conn.execute('''
                INSERT INTO adms_commands (device_id, command_type, payload, status)
                VALUES (?, 'DATA QUERY USERINFO', ?, 'PENDING')
            ''', (device_id, payload))
            
            # Request Fingerprints
            conn.execute('''
                INSERT INTO adms_commands (device_id, command_type, payload, status)
                VALUES (?, 'DATA QUERY FINGERTMP', ?, 'PENDING')
            ''', (device_id, payload))
            
            count += 2
            
        # Optional: Ask the device to re-upload attendance logs just in case
        conn.execute('''
            INSERT INTO adms_commands (device_id, command_type, payload, status)
            VALUES (?, 'DATA QUERY ATTLOG', '{}', 'PENDING')
        ''', (device_id,))
        
        conn.commit()
        return jsonify({'success': True, 'message': f'تم إدراج {count} أمر مزامنة بنجاح لجميع الموظفين. سيتم التنفيذ تباعاً.'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})
    finally:
        pass # conn.close() removed to prevent leak in Flask g

@attendance_bp.route('/attendance/sync_queue')
@login_required
@require_permission('attendance.view')
def sync_queue():
    """View pending records in the Oracle sync queue"""
    stats = get_sync_queue_stats()
    return render_template('sync_queue.html', stats=stats)

@attendance_bp.route('/api/attendance/sync_queue/data')
@login_required
@require_permission('attendance.view')
def sync_queue_data_api():
    """API endpoint to get pending records for the UI"""
    try:
        conn = get_db_connection()
        records = conn.execute("SELECT user_id, check_time, check_type, status, retry_count FROM oracle_sync_queue WHERE status = 'PENDING' ORDER BY id DESC LIMIT 100").fetchall()
        pass # conn.close() removed to prevent leak in Flask g
        return jsonify([dict(r) for r in records])
    except Exception as e:
        return jsonify({'error': str(e)}), 500
@attendance_bp.route('/fingerprint/api/devices/clear_users/<int:device_id>', methods=['POST'])
@login_required
@require_permission('attendance.edit')
def clear_device_users_api(device_id):
    """API endpoint to clear all users from a device"""
    success, message = clear_fingerprint_device_users(device_id)
    return jsonify({
        'success': success,
        'message': message
    })
@attendance_bp.route('/fingerprint/api/devices/user_count/<int:device_id>', methods=['GET'])
@login_required
@require_permission('attendance.view')
def get_device_user_count_api(device_id):
    """API endpoint to get the number of users on a device"""
    success, result = get_device_user_count(device_id)
    if success:
        return jsonify({
            'success': True,
            'count': result['count'],
            'message': result['message'],
            'is_adms': result['is_adms']
        })
    else:
        return jsonify({
            'success': False,
            'message': result
        })

@attendance_bp.route('/fingerprint/api/devices/reboot/<int:device_id>', methods=['POST'])
@login_required
@require_permission('attendance.edit')
def reboot_device_api(device_id):
    """API endpoint to reboot a device"""
    success, message = reboot_fingerprint_device(device_id)
    return jsonify({
        'success': success,
        'message': message
    })

@attendance_bp.route('/fingerprint/api/devices/factory_reset/<int:device_id>', methods=['POST'])
@login_required
@require_permission('attendance.edit')
def factory_reset_device_api(device_id):
    """API endpoint to factory reset a device"""
    success, message = factory_reset_fingerprint_device(device_id)
    return jsonify({
        'success': success,
        'message': message
    })
