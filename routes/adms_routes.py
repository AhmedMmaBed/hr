from flask import Blueprint, request, Response, render_template, jsonify
from datetime import datetime
import logging
import os
import json
import re
from utils.db import get_db_connection, DATA_DIR
from utils.auth import login_required
from utils.rbac import require_permission
from utils.fingerprint_utils import queue_adms_set_time

# Import the Oracle sync helper
try:
    from utils.oracle_db import insert_attendance_to_oracle
    ORACLE_ENABLED = True
except ImportError:
    ORACLE_ENABLED = False
    print(" [ADMS] oracledb module not found. Oracle sync disabled.")

# Configure logging to shared log file
from logging import FileHandler as RotatingFileHandler
LOG_FILE = os.path.join(DATA_DIR, 'fingerprint_sync.log')
logger = logging.getLogger('adms')
if not logger.handlers:
    # 10MB per file, 5 backups
    fh = RotatingFileHandler(LOG_FILE, encoding='utf-8')
    fh.setFormatter(logging.Formatter('%(asctime)s - [ADMS] - %(levelname)s - %(message)s'))
    logger.addHandler(fh)
    logger.setLevel(logging.INFO)

# Protocol logger for raw data investigation
adms_protocol_logger = logging.getLogger('adms_protocol')
if not adms_protocol_logger.handlers:
    # Dedicated log for raw protocol data
    PROTO_LOG = os.path.join(DATA_DIR, 'adms_protocol.log')
    pfh = RotatingFileHandler(PROTO_LOG, encoding='utf-8')
    pfh.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
    adms_protocol_logger.addHandler(pfh)
    adms_protocol_logger.setLevel(logging.INFO)

adms_bp = Blueprint('adms_mgr', __name__)

import re
def device_gate(conn, sn):
    """بوّابة قبول الجهاز: مسجَّل؟ وضمن حدّ الرخصة؟

    كان هذا المسار يقبل أي جهاز يرسل، بلا تحقق من تسجيله ولا من حدّ
    الرخصة — بينما `fingerprint_sync` وحده يحترم الحدّ. أي أن المسار الذي
    تصل منه البصمات فعلًا كان بلا فرض.

    والفرض هنا بالرفض الصريح لا بالتجاهل الصامت: كان `LIMIT n` يتجاهل
    الأجهزة الزائدة، فتبدو للعميل عاملةً وبصماتها تضيع بلا رسالة — وهو
    أسوأ من المنع لأنه يظهر كعطل لا كحدّ ترخيص.

    يُرجع (مسموح، سبب_الرفض، صف_الجهاز).
    """
    if not sn:
        return False, 'no_serial', None

    # فحص الصحة ليس جهازًا: تسجيله في المرفوضين كل ثلاثين ثانية يغرق
    # الجدول والسجلّ فيخفي محاولات الأجهزة الحقيقية — وهو ما ضلّل التشخيص
    # فعلًا عند عميل.
    if sn == 'healthcheck':
        return False, 'healthcheck', None

    # المطابقة بالرقم التسلسلي أولًا ثم بعنوان IP: الأجهزة القائمة قد
    # تكون مسجَّلة باسم وصفي أو بعنوانها لا برقمها التسلسلي، لأن المسار
    # قبل هذه البوّابة كان يقبل `device_ip = ? OR device_name = ?`.
    # الاقتصار على الاسم يرفض أجهزةً عاملة عند كل عميل قائم — وهو ما وقع
    # فعلًا بعد التحديث.
    row = conn.execute(
        'SELECT * FROM fingerprint_devices WHERE device_name = ?',
        (sn,)).fetchone()

    if not row and request.remote_addr:
        row = conn.execute(
            'SELECT * FROM fingerprint_devices WHERE device_ip = ?',
            (request.remote_addr,)).fetchone()
        if row:
            # يُثبَّت الرقم التسلسلي على الجهاز المعروف: المطابقة بعنوان IP
            # هشّة — العنوان يتغيّر بإعادة تشغيل الشبكة، وقد يتشارك عملاء
            # خلف NAT عنوانًا واحدًا. فتُحفظ الهوية الصحيحة أول مرة.
            try:
                if not row['device_name'] or row['device_name'] != sn:
                    conn.execute(
                        'UPDATE fingerprint_devices SET device_name = ? WHERE id = ?',
                        (sn, row['id']))
                    conn.commit()
            except Exception:
                pass

    if not row:
        # غير مسجَّل: يُرفض ويُسجَّل. الرقم التسلسلي مطبوع على الجهاز
        # وقابل للتخمين، فقبول أي رقم يعني قبول بصمات ملفّقة.
        try:
            conn.execute(
                "INSERT INTO adms_rejected_devices (serial, remote_ip, reason) "
                "VALUES (?, ?, 'unregistered')",
                (sn, request.remote_addr))
            conn.commit()
        except Exception:
            pass
        return False, 'unregistered', None

    if not row['is_active']:
        return False, 'inactive', row

    # حدّ الرخصة: يُحتسب بترتيب التسجيل، فالأجهزة القائمة لا تُقطع
    # بأثر رجعي عند تقليص الحدّ — قطع بصمات موظفين عاملين يعني رواتب
    # خاطئة، وهو أسوأ من فقد رسوم اشتراك.
    try:
        from utils.license import get_effective_max_devices
        limit = get_effective_max_devices()
    except Exception:
        limit = 0

    if limit > 0:
        allowed = [r['id'] for r in conn.execute(
            "SELECT id FROM fingerprint_devices WHERE is_active = 1 "
            "ORDER BY created_at ASC LIMIT ?", (limit,)).fetchall()]
        if row['id'] not in allowed:
            try:
                conn.execute(
                    "INSERT INTO adms_rejected_devices (serial, remote_ip, reason) "
                    "VALUES (?, ?, 'over_license_limit')",
                    (sn, request.remote_addr))
                conn.commit()
            except Exception:
                pass
            return False, 'over_license_limit', row

    return True, None, row


def parse_zk_kv(text):
    """Robust ZK Key=Value parser using regex."""
    matches = re.findall(r'(\w+)=((?:(?!\s+\w+=).)*)', text)
    return {k: v.strip() for k, v in matches}

@adms_bp.route('/cdata', methods=['GET', 'POST'])
def cdata():
    """
    Device sending data (Logs, User info, etc.)
    """
    sn = request.args.get('SN')
    table = request.args.get('table')
    
    # Get raw data for logging as suggested by user
    raw_data = request.get_data(as_text=True)
    
    # Record raw data to a separate file for investigation
    if raw_data:
        adms_protocol_logger.info(f"Table: {table} | SN: {sn} | IP: {request.remote_addr}\n{raw_data}")
        # Print to console for real-time debugging as requested
        print(f"\n[ADMS DATA] Received from {sn} (Table: {table}, IP: {request.remote_addr}):\n{raw_data[:200]}...\n")

    # بوّابة القبول — بعد تسجيل الاكتشاف لا قبله.
    #
    # الجهاز المجهول يجب أن يظهر في قائمة الاكتشاف (pending_adms_devices)
    # ليعتمده المستخدم من شاشة ADMS. البوّابة كانت ترجع قبل الوصول إلى
    # ذلك السطر، فاختفى الجهاز من القائمة تمامًا ولم يعد ممكنًا إضافته
    # إطلاقًا — أي أن الحماية عطّلت الوظيفة التي تجعلها قابلة للاستخدام.
    #
    # فالمنع الآن مقصور على كتابة بيانات الحضور والمستخدمين، والاكتشاف
    # يمضي: يُرى الجهاز، ويُعتمد بقرار، ثم تُقبل بياناته.
    _gate_ok = True
    _gate_reason = None
    try:
        _gconn = get_db_connection()
        _gate_ok, _gate_reason, _gate_dev = device_gate(_gconn, sn)
        if not _gate_ok and _gate_reason != 'healthcheck':
            logger.warning(
                f"ADMS pending | SN={sn} | reason={_gate_reason} | "
                f"ip={request.remote_addr} — الجهاز في قائمة الاكتشاف بانتظار الاعتماد")
    except Exception as _ge:
        logger.error(f"device_gate error: {_ge}")

    # DEBUG LOGGING (existing, now using the already fetched raw_data)
    logger.info(f"CDATA Received | SN: {sn} | Table: {table}")
    logger.info(f"Params: {request.args}")
    logger.debug(f"Body Preview: {raw_data[:500]}") # Show more
    try:
        
        # 1. Update/Add Device to Pending List (Discovery)
        # فحص الصحة ليس جهازًا فلا يدخل قائمة الاكتشاف: ظهوره فيها كل
        # ثلاثين ثانية يوهم المستخدم بجهاز ينتظر الاعتماد.
        if sn and sn != 'healthcheck':
            conn = get_db_connection()
            # Update last activity
            conn.execute('''
                INSERT INTO pending_adms_devices (device_sn, ip_address, last_activity)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(device_sn) DO UPDATE SET
                ip_address = excluded.ip_address,
                last_activity = CURRENT_TIMESTAMP
            ''', (sn, request.remote_addr))

            # الاكتشاف تمّ: الجهاز صار مرئيًّا في شاشة ADMS ليُعتمد.
            # وبياناته لا تُكتب قبل الاعتماد، فلا تدخل بصمات ملفّقة بمجرّد
            # معرفة رقم تسلسلي مطبوع على الجهاز.
            if not _gate_ok:
                conn.commit()
                return "OK"

            
            # Also update fingerprint_devices if exists (Heartbeat)
            conn.execute('''
                UPDATE fingerprint_devices 
                SET last_sync_time = CURRENT_TIMESTAMP, 
                    last_activity = CURRENT_TIMESTAMP,
                    is_active = 1
                WHERE device_ip = ? OR device_name = ?
            ''', (request.remote_addr, sn))
            
            # جهازٌ يتصل عبر بروتوكول ADMS هو جهاز ADMS بحكم اتصاله، فتُضبط
            # الراية تلقائيًّا. كانت تُضبط من خانة اختيار في نموذج الإضافة
            # وحدها، فأي جهاز يُعتمد بدونها يبقى is_adms = 0 — ودالة إرسال
            # المستخدمين تشترطها، فتجد صفر أجهزة وترجع صامتةً بلا أمر
            # واحد. أي أن الإضافة تنجح والإرسال لا يعمل بلا رسالة خطأ.
            try:
                conn.execute(
                    'UPDATE fingerprint_devices SET is_adms = 1 '
                    'WHERE device_name = ? AND COALESCE(is_adms, 0) = 0', (sn,))
            except Exception:
                pass

            # 1a. Automatic Time Sync (Maintenance)
            device_row = conn.execute('SELECT id FROM fingerprint_devices WHERE device_ip = ? OR device_name = ?', (request.remote_addr, sn)).fetchone()
            device_id = device_row['id'] if device_row else None
            
            if device_id:
                # Check if we sent a SET TIME command in the last 24 hours
                last_time_cmd = conn.execute('''
                    SELECT 1 FROM adms_commands 
                    WHERE device_id = ? AND command_type = 'SET TIME'
                    AND created_at > datetime('now', '-1 day')
                ''', (device_id,)).fetchone()
                
                if not last_time_cmd:
                    logger.info(f" [ADMS] Queuing Automatic Time Sync for Device ID: {device_id}")
                    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    conn.execute('''
                        INSERT INTO adms_commands (device_id, command_type, payload, status)
                        VALUES (?, 'SET TIME', ?, 'PENDING')
                    ''', (device_id, json.dumps(now_str)))

            # 1b. Command Cleanup (Maintenance)
            # Delete OK/FAILED commands older than 7 days
            conn.execute('''
                DELETE FROM adms_commands 
                WHERE status IN ('OK', 'FAILED') 
                AND created_at < datetime('now', '-7 days')
            ''')

            conn.commit()
            pass # conn.close() removed to prevent leak in Flask g

        if table == 'ATTLOG':
            # Attendance Log
            # ... (Existing ATTLOG logic)
            # Format: ID\tTime\tState\tVerify\n...
            data = request.get_data(as_text=True)
            lines = data.split('\n')
            
            conn = get_db_connection()
            
            # Find device ID
            device = conn.execute('SELECT id FROM fingerprint_devices WHERE device_name = ? OR device_ip = ?', (sn, request.remote_addr)).fetchone()
            device_id = device['id'] if device else 0
            
            for line in lines:
                if not line.strip(): continue
                parts = line.split('\t')
                if len(parts) >= 4:
                    user_id = parts[0]
                    time_str = parts[1]
                    status = parts[2]
                    verify = parts[3]
                    
                    emp = conn.execute('SELECT id FROM employees WHERE employee_number = ?', (user_id,)).fetchone()
                    emp_id = emp['id'] if emp else 0
                    
                    if emp_id:
                        # Duplicate Prevention: Check if this exact record already exists
                        exists = conn.execute('''
                            SELECT 1 FROM attendance_records 
                            WHERE employee_id = ? AND check_time = ?
                        ''', (emp_id, time_str)).fetchone()
                        
                        if not exists:
                            conn.execute('''
                                INSERT INTO attendance_records (employee_id, device_id, check_time, check_type, verify_code)
                                VALUES (?, ?, ?, ?, ?)
                            ''', (emp_id, device_id, time_str, status, verify))
                            
                            # Sync to Oracle database alongside SQLite
                            if ORACLE_ENABLED:
                                # DO NOT start a thread per record anymore (Thundering Herd bottleneck)
                                # Instead, just add to the local sync queue and let the background 
                                # heartbeat in utils/oracle_db handle the flushing.
                                from utils.oracle_db import add_to_sync_queue
                                add_to_sync_queue(user_id, time_str, status, verify, request.remote_addr, sqlite_conn=conn)
            
            conn.commit()
            pass # conn.close() removed to prevent leak in Flask g
            return "OK"
            
            conn.commit()
            pass # conn.close() removed to prevent leak in Flask g
            return "OK"
            
        # [EXISTING ATTLOG BLOCK ENDS HERE, INSERTING OPERLOG BEFORE BIODATA OR AFTER]
        
        elif table == 'OPERLOG':
            # Operation Log (Contains Users, FP, Face in some firmwares)
            # Format: USER PIN=1 Name=...
            # Format: BIOPHOTO PIN=...
            # Format: BIODATA ...
            data = request.get_data(as_text=True)
            lines = data.split('\n')
            
            conn = get_db_connection()
            device = conn.execute('SELECT id FROM fingerprint_devices WHERE device_name = ? OR device_ip = ?', (sn, request.remote_addr)).fetchone()
            device_id = device['id'] if device else 0
            
            if device_id:
                for line in lines:
                    if 'PIN=' in line: # generic check
                        # Parse KV
                        # Regex to capture Key=Value where Value can contain spaces but stops before next Key=
                        # Pattern: Word=Value (until next Word= or end)
                        # Clean prefix identifiers like "USER " or "BIOPHOTO " if attached without =
                        clean_line = line.replace('USER PIN=', 'PIN=').replace('BIOPHOTO PIN=', 'PIN=')
                        
                        # Fallback simple split if clean replacement worked
                        # But standard regex is safer for "Name=First Last Pri=..."
                        matches = re.findall(r'(\w+)=((?:(?!\s+\w+=).)*)', clean_line)
                        info = {k: v.strip() for k, v in matches}
                        
                        if 'PIN' in info:
                            # Check if it contains Name (User Info)
                            if 'Name' in info or 'Pri' in info:
                                try:
                                    # 1. Update Fingerprint Users
                                    conn.execute('''
                                        INSERT OR REPLACE INTO fingerprint_users 
                                        (user_id, device_id, name, privilege, password, group_id, card_number, created_at)
                                        VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                                    ''', (
                                        info.get('PIN'),
                                        device_id,
                                        info.get('Name', ''),
                                        info.get('Pri', 0),
                                        info.get('Passwd', ''),
                                        info.get('Grp', ''),
                                        info.get('Card', '')
                                    ))
                                    
                                    # 2. Update Main Employees Table (Auto-Sync)
                                    # Ensure record exists so attendance can be logged
                                    conn.execute('''
                                        INSERT INTO employees (
                                            employee_number, name, department, position, 
                                            hire_date, salary, default_start_time, default_end_time, 
                                            is_active, password, group_id, card_number, privilege
                                        ) VALUES (?, ?, 'General', 'Employee', CURRENT_DATE, 0, '09:00', '17:00', 1, ?, ?, ?, ?)
                                        ON CONFLICT(employee_number) DO UPDATE SET
                                        card_number = excluded.card_number,
                                        privilege = excluded.privilege
                                    ''', (
                                        info.get('PIN'),
                                        info.get('Name', 'New User'),
                                        info.get('Passwd', ''),
                                        info.get('Grp', ''),
                                        info.get('Card', 0),
                                        info.get('Pri', 0)
                                    ))
                                    
                                except Exception as e:
                                    logger.error(f"OPERLOG User Save Error: {e}", exc_info=True)
                                    
            conn.commit()
            pass # conn.close() removed to prevent leak in Flask g
            return "OK"

        elif table == 'BIODATA' or table == 'biodata':
            data = request.get_data(as_text=True)
            # Use regex for robust key=value parsing
            matches = re.findall(r'(\w+)=((?:(?!\s+\w+=).)*)', data)
            info = {k: v.strip() for k, v in matches}
            
            pin = info.get('Pin') or info.get('PIN')
            tmp = info.get('Tmp') or info.get('TMP') or info.get('Template')
            
            if pin and tmp:
                conn = get_db_connection()
                try:
                    conn.execute('''
                        INSERT OR REPLACE INTO fingerprint_templates 
                        (device_sn, pin, finger_id, valid, duress, template_type, major_ver, minor_ver, format, template_data)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        sn,
                        pin,
                        info.get('No', info.get('Index', 0)),
                        info.get('Valid', 1),
                        info.get('Duress', 0),
                        info.get('Type', 1), # Default to finger if missing
                        info.get('MajorVer'),
                        info.get('MinorVer'),
                        info.get('Format'),
                        tmp
                    ))
                    conn.commit()
                    print(f" [ADMS] Saved BIODATA for PIN: {pin} (Type: {info.get('Type', 1)})")
                except Exception as e:
                    logger.error(f"Template Save Error: {e}", exc_info=True)
                finally:
                    pass # conn.close() removed to prevent leak in Flask g
            return "OK"

        elif table == 'USERINFO':
            # User Info
            # Format: PIN=1 Name=...
            data = request.get_data(as_text=True)
            
            # Cleanup prefix if exists "USERINFO "
            data = data.replace('USERINFO ', '').replace('USER ', '')
            
            info = parse_zk_kv(data)
            
            if 'PIN' in info:
                conn = get_db_connection()
                try:
                    # Find Device
                    device = conn.execute('SELECT id FROM fingerprint_devices WHERE device_name = ? OR device_ip = ?', (sn, request.remote_addr)).fetchone()
                    device_id = device['id'] if device else 0
                    
                    if device_id:
                        conn.execute('''
                            INSERT OR REPLACE INTO fingerprint_users 
                            (user_id, device_id, name, privilege, password, group_id, card_number, created_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                        ''', (
                            info.get('PIN'),
                            device_id,
                            info.get('Name', ''),
                            info.get('Pri', 0),
                            info.get('Passwd', ''),
                            info.get('Grp', ''),
                            info.get('Card', '')
                        ))
                        conn.commit()
                except Exception as e:
                    logger.error(f"UserInfo Save Error: {e}", exc_info=True)
                finally:
                    pass # conn.close() removed to prevent leak in Flask g
            
            return "OK"
            
        elif table == 'USERFACE' or table == 'FACE':
            data = str(request.get_data(as_text=True))
            def parse_kv(text):
                res = {}
                tokens = text.split() 
                for token in tokens:
                    if '=' in token:
                        k, v = token.split('=', 1)
                        res[k] = v
                return res

            if data.startswith('USERFACE ') or data.startswith('FACE '):
                data = data.split(' ', 1)[1]
            
            info = parse_kv(data)
            
            if 'PIN' in info and any(k.lower() == 'tmp' for k in info):
                # Find the actual template key
                tmp_key = next((k for k in info if k.lower() == 'tmp'), None)
                if tmp_key:
                    conn = get_db_connection()
                    try:
                        conn.execute('''
                            INSERT OR REPLACE INTO fingerprint_faces 
                            (device_sn, pin, face_id, valid, template_data)
                            VALUES (?, ?, ?, ?, ?)
                        ''', (
                            sn,
                            info.get('PIN'),
                            info.get('FID', info.get('FaceID', 0)),
                            info.get('Valid', 1),
                            info.get(tmp_key)
                        ))
                        conn.commit()
                    except Exception as e:
                        logger.error(f"Face Save Error: {e}", exc_info=True)
                    finally:
                        pass # conn.close() removed to prevent leak in Flask g
            return "OK"

        elif table in ['PHOTO', 'USERPIC', 'BIOPHOTO']:
            data = request.get_data(as_text=True)
            # Use regex for robust key=value parsing even with long content
            matches = re.findall(r'(\w+)=((?:(?!\s+\w+=).)*)', data)
            info = {k: v.strip() for k, v in matches}
            
            pin = info.get('PIN') or info.get('pin')
            # Look for image content in various keys (Content, Photo, Tmp)
            photo_data = info.get('Content') or info.get('Photo') or info.get('Tmp')
            
            if pin and photo_data:
                conn = get_db_connection()
                try:
                    conn.execute('''
                        INSERT OR REPLACE INTO user_photos 
                        (device_sn, pin, photo_data)
                        VALUES (?, ?, ?)
                    ''', (sn, pin, photo_data))
                    conn.commit()
                    print(f" [ADMS] Saved photo for PIN: {pin} (Table: {table})")
                except Exception as e:
                    logger.error(f"Photo Save Error: {e}", exc_info=True)
                finally:
                    pass # conn.close() removed to prevent leak in Flask g
            return "OK"
            
        return "OK"
    except Exception as e:
        logger.error(f"ADMS Error: {e}", exc_info=True)
        return "ERROR"

@adms_bp.route('/getrequest', methods=['GET'])
def get_request():
    """
    Device asking for commands.
    Url: /iclock/getrequest?SN=...
    """
    sn = request.args.get('SN')
    conn = get_db_connection()
    
    # Check if we know this device (by IP or we need SN in DB)
    # Let's try to find device by matching SN in pending or registered?
    # Ideally, `fingerprint_devices` should have `device_sn`. 
    # Current schema: device_name, device_ip. 
    # We will assume user puts SN in Name or we limit by IP.
    # BETTER: Use valid device lookup by IP for now.
    
    # Improved Device Lookup: Use SN from params first, then fallback to IP
    # Standard ZK protocol sends SN in the query string
    sn = request.args.get('SN')
    
    if sn:
        device = conn.execute('SELECT id FROM fingerprint_devices WHERE device_name = ?', (sn,)).fetchone()
    else:
        # Fallback to IP identification if SN is missing
        device = conn.execute('SELECT id FROM fingerprint_devices WHERE device_ip = ?', (request.remote_addr,)).fetchone()
    
    # Second fallback: If SN lookup failed, try IP for this device anyway
    if sn and not device:
         device = conn.execute('SELECT id FROM fingerprint_devices WHERE device_ip = ?', (request.remote_addr,)).fetchone()
    
    commands = []
    if device:
        # Fetch pending commands
        rows = conn.execute('''
            SELECT id, command_type, payload FROM adms_commands 
            WHERE device_id = ? AND status = 'PENDING'
            ORDER BY id ASC LIMIT 10
        ''', (device['id'],)).fetchall()
        
        for row in rows:
            cmd_id = row['id']
            cmd_type = row['command_type']
            payload = json.loads(row['payload'])
            
            # Construct ZK protocol string
            # DATA UPDATE USER PIN=...
            cmd_str = ""
            if cmd_type == 'DATA UPDATE USERINFO':
                pin = payload.get('PIN')
                parts = [f"PIN={pin}"]
                if 'Name' in payload: parts.append(f"Name={payload['Name']}")
                if 'Pri' in payload: parts.append(f"Pri={payload['Pri']}")
                if 'Passwd' in payload: parts.append(f"Passwd={payload['Passwd']}")
                if 'Card' in payload: parts.append(f"Card={payload['Card']}")
                if 'Grp' in payload: parts.append(f"Grp={payload['Grp']}")
                
                parts.append("Enabled=1")
                cmd_str = f"C:{cmd_id}:DATA UPDATE USERINFO\t" + "\t".join(parts)
            
            elif cmd_type == 'DATA UPDATE FINGERTMP':
                pin = payload.get('PIN')
                fid = payload.get('FingerID') or payload.get('FID')
                size = payload.get('Size')
                tmp = payload.get('TMP') or payload.get('Template')
                # ZKTeco standard: Pin, FingerID/FID, Size, Valid, Template/TMP, Type
                parts = [
                    f"Pin={pin}",
                    f"FingerID={fid}",
                    f"Size={size}",
                    f"Valid=1",
                    f"Template={tmp}",
                    "Type=1"
                ]
                cmd_str = f"C:{cmd_id}:DATA UPDATE FINGERTMP\t" + "\t".join(parts)
            
            elif cmd_type == 'DATA UPDATE FACE':
                pin = payload.get('PIN')
                fmt = payload.get('Format')
                size = payload.get('Size')
                tmp = payload.get('TMP') or payload.get('Template')
                cmd_str = f"C:{cmd_id}:DATA UPDATE FACE\tPin={pin}\tFormat={fmt}\tSize={size}\tTemplate={tmp}\tValid=1"

            elif cmd_type == 'DATA UPDATE BIODATA':
                pin = payload.get('PIN')
                no = payload.get('FingerID') or payload.get('FID') or payload.get('No')
                size = payload.get('Size')
                tmp = payload.get('TMP') or payload.get('Template')
                cmd_str = f"C:{cmd_id}:DATA UPDATE BIODATA\tPin={pin}\tNo={no}\tIndex=0\tValid=1\tDuress=0\tType=1\tMajorVer=13\tMinorVer=0\tFormat=0\tTmp={tmp}"

            elif cmd_type == 'DATA DELETE USERINFO':
                 cmd_str = f"C:{cmd_id}:DATA DELETE USERINFO PIN={payload.get('PIN')}"
            
            elif cmd_type == 'SET TIME':
                 # الوقت يُحسب لحظة الإرسال لا لحظة إضافة الأمر للطابور.
                 #
                 # كان يُخزَّن في الحمولة عند الإدراج، والأمر ينتظر حتى
                 # يسأل عنه الجهاز — وقد يتأخّر دقائق أو ساعات إن كان
                 # مفصولًا. فتصل الساعةُ قديمةً ويضبط الجهاز نفسه عليها،
                 # وساعته هي مصدر طوابع الحضور: انحرافها يزيح الأجر
                 # تأخيرًا وهميًّا أو عملًا إضافيًّا وهميًّا.
                 # Use standard ADMS format for time sync: SET OPTIONS DateTime=UnixTimestamp
                 unix_time = int(datetime.now().timestamp())
                 cmd_str = f"C:{cmd_id}:SET OPTIONS DateTime={unix_time}"
            
            elif cmd_type.startswith('DATA QUERY'):
                # Many DATA QUERY commands (USERINFO, FINGERTMP) can take PIN=... or other params
                params = []
                if isinstance(payload, dict):
                    for k, v in payload.items():
                        params.append(f"{k}={v}")
                
                param_str = " ".join(params)
                if param_str:
                    # Switch back to space based on tester results
                    cmd_str = f"C:{cmd_id}:{cmd_type} {param_str}"
                else:
                    cmd_str = f"C:{cmd_id}:{cmd_type}"

            # LEGACY / FALLBACK Support
            elif cmd_type == 'ADD_USER':
                pin = payload.get('PIN')
                name = payload.get('Name', '')
                pri = payload.get('Pri', 0)
                passwd = payload.get('Passwd', '')
                card = payload.get('Card', '0')
                
                parts = [f"PIN={pin}", f"Name={name}", f"Pri={pri}", "Enabled=1"]
                if passwd: parts.append(f"Passwd={passwd}")
                if card: parts.append(f"Card={card}")
                
                cmd_str = f"C:{cmd_id}:DATA UPDATE USERINFO\t" + "\t".join(parts)
            elif cmd_type == 'DELETE_USER':
                 cmd_str = f"C:{cmd_id}:DATA DELETE USERINFO PIN={payload.get('PIN')}"
            else:
                 cmd_str = f"C:{cmd_id}:{cmd_type}"
            
            print(f" [ADMS] Sending Command to {sn}: {cmd_str}")
            commands.append(cmd_str)
            
            # Update status to SENT
            conn.execute('UPDATE adms_commands SET status = "SENT", updated_at = CURRENT_TIMESTAMP WHERE id = ?', (cmd_id,))
            
        conn.commit()
    
    pass # conn.close() removed to prevent leak in Flask g
    
    if commands:
        return "\n".join(commands)
    
    return "OK"

@adms_bp.route('/devicecmd', methods=['POST'])
def device_cmd():
    """
    Device reporting command result.
    """
    try:
        data = request.get_data(as_text=True)
        if not data:
            return "OK"

        print(f" [ADMS] Received Command Results: {data}")
        
        # Split by newline (standard for multiple results)
        # Some devices also use & to separate lines? Usually \n
        results = data.strip().split('\n')
        
        conn = get_db_connection()
        for res_line in results:
            if not res_line.strip(): continue
            
            # Parse ID=1&Return=0
            params = {}
            for part in res_line.strip().split('&'):
                if '=' in part:
                    k, v = part.split('=', 1)
                    params[k] = v
            
            cmd_id = params.get('ID')
            ret_val = params.get('Return')
            
            if cmd_id and ret_val:
                status = 'OK' if ret_val == '0' else 'FAILED'
                display_status = status if status == 'OK' else f"FAILED (Code: {ret_val})"
                
                print(f" [ADMS] Updating Command {cmd_id} result: {display_status}")
                conn.execute('UPDATE adms_commands SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?', 
                             (display_status, cmd_id))
        
        conn.commit()
        pass # conn.close() removed to prevent leak in Flask g
        return "OK"
    except Exception as e:
        logger.error(f"CMD Error: {e}", exc_info=True)
        return "ERROR"

@adms_bp.route('/registry', methods=['POST', 'GET'])
def registry():
    return "Registry=OK"

@adms_bp.route('/adms/commands')
@login_required
@require_permission('attendance.devices')
def adms_commands_view():
    """View to list ADMS commands"""
    return render_template('adms_commands.html')

@adms_bp.route('/api/adms/commands')
@login_required
@require_permission('attendance.devices')
def get_adms_commands_api():
    """API to fetch ADMS commands for the table"""
    limit = request.args.get('limit', 100, type=int)
    offset = request.args.get('offset', 0, type=int)
    
    conn = get_db_connection()
    # Join with fingerprint_devices to get device names
    query = '''
        SELECT c.*, d.device_name, d.device_ip
        FROM adms_commands c
        LEFT JOIN fingerprint_devices d ON c.device_id = d.id
        ORDER BY c.created_at DESC
        LIMIT ? OFFSET ?
    '''
    rows = conn.execute(query, (limit, offset)).fetchall()
    
    commands = []
    for row in rows:
        commands.append({
            'id': row['id'],
            'device_name': row['device_name'] or 'Unknown',
            'device_ip': row['device_ip'] or 'N/A',
            'command_type': row['command_type'],
            'payload': row['payload'],
            'status': row['status'],
            'created_at': row['created_at'],
            'updated_at': row['updated_at']
        })
    
    total_row = conn.execute('SELECT COUNT(*) as count FROM adms_commands').fetchone()
    total = total_row['count'] if total_row else 0
    pass # conn.close() removed to prevent leak in Flask g
    
    return jsonify({
        'success': True,
        'commands': commands,
        'total': total
    })

@adms_bp.route('/api/adms/commands/clear', methods=['POST'])
@login_required
@require_permission('attendance.edit')
def clear_adms_commands():
    """Clear all ADMS commands"""
    conn = get_db_connection()
    conn.execute('DELETE FROM adms_commands')
    conn.commit()
    pass # conn.close() removed to prevent leak in Flask g
    return jsonify({'success': True, 'message': 'تم مسح جميع الأوامر بنجاح'})

@adms_bp.route('/api/adms/sync_time_all', methods=['POST'])
@login_required
@require_permission('attendance.edit')
def sync_time_all_adms_devices():
    """Queue SET TIME commands for all active ADMS devices"""
    from utils.fingerprint_utils import queue_adms_set_time
    
    conn = get_db_connection()
    try:
        devices = conn.execute('SELECT id FROM fingerprint_devices WHERE is_active = 1 AND is_adms = 1').fetchall()
        if not devices:
            return jsonify({'success': False, 'message': 'لا يوجد أجهزة ADMS نشطة'})
            
        count = 0
        for device in devices:
            if queue_adms_set_time(device['id']):
                count += 1
                
        return jsonify({'success': True, 'message': f'تمت جدولة تحديث الوقت لـ {count} أجهزة'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'خطأ: {str(e)}'})
    finally:
        pass # conn.close() removed to prevent leak in Flask g
@adms_bp.route('/api/adms/sync_all_to_device/<int:device_id>', methods=['POST'])
@login_required
@require_permission('attendance.edit')
def sync_all_to_adms_device(device_id):
    """Queue all active employees to a specific ADMS device"""
    from utils.fingerprint_utils import queue_adms_user_update
    
    conn = get_db_connection()
    try:
        # Check if device is ADMS
        device = conn.execute('SELECT id, is_adms FROM fingerprint_devices WHERE id = ?', (device_id,)).fetchone()
        if not device or not device['is_adms']:
            return jsonify({'success': False, 'message': 'الجهاز غير موجود أو ليس بنظام ADMS'})
        
        # Get all active employees
        employees = conn.execute('SELECT employee_number, name, privilege, password, group_id, card_number FROM employees WHERE is_active = 1').fetchall()
        
        if not employees:
            return jsonify({'success': False, 'message': 'لا يوجد موظفين نشطين للمزامنة'})
        
        # We manually queue for THIS specific device instead of all devices
        cursor = conn.cursor()
        count = 0
        for emp in employees:
            # Reusing payload logic from queue_adms_user_update but restricted to one device
            pin = str(emp['employee_number'])
            name = str(emp['name'])
            
            user_payload = {
                'PIN': pin,
                'Name': name,
                'Pri': int(emp['privilege'] or 0),
                'Passwd': str(emp['password'] or ''),
                'Card': str(emp['card_number'] or '0'),
                'Grp': str(emp['group_id'] or '1')
            }
            
            # 1. User Info
            cursor.execute('''
                INSERT INTO adms_commands (device_id, command_type, payload, status)
                VALUES (?, ?, ?, 'PENDING')
            ''', (device_id, 'DATA UPDATE USERINFO', json.dumps(user_payload)))
            count += 1
            
            # 2. Fingerprints
            try:
                fingerprints = conn.execute('SELECT finger_id, size, template FROM user_fingerprints WHERE user_id = ?', (pin,)).fetchall()
                for fp in fingerprints:
                    fp_payload = {'PIN': pin, 'FingerID': fp['finger_id'], 'Size': fp['size'], 'TMP': fp['template']}
                    cursor.execute('''
                        INSERT INTO adms_commands (device_id, command_type, payload, status)
                        VALUES (?, ?, ?, 'PENDING')
                    ''', (device_id, 'DATA UPDATE FINGERTMP', json.dumps(fp_payload)))
            except:
                pass
            
            # 3. Faces
            try:
                faces = conn.execute('SELECT format, size, template FROM user_faces WHERE user_id = ?', (pin,)).fetchall()
                for face in faces:
                    face_payload = {'PIN': pin, 'Format': face['format'], 'Size': face['size'], 'TMP': face['template']}
                    cursor.execute('''
                        INSERT INTO adms_commands (device_id, command_type, payload, status)
                        VALUES (?, ?, ?, 'PENDING')
                    ''', (device_id, 'DATA UPDATE FACE', json.dumps(face_payload)))
            except:
                pass

        conn.commit()
        return jsonify({'success': True, 'message': f'تمت جدولة {count} موظف للمزامنة لهذا الجهاز'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'خطأ أثناء الجدولة: {str(e)}'})
    finally:
        pass # conn.close() removed to prevent leak in Flask g
