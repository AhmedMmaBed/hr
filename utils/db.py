import sqlite3
import os
from datetime import datetime

import sys

# Define the absolute path to the database file in a system directory
DATA_DIR = os.environ.get('HR_DATA_DIR')

if not DATA_DIR:
    if os.name == 'nt':
        DATA_DIR = r'C:\ProgramData\HRSystem'
    else:
        DATA_DIR = '/app/data'

if not os.path.exists(DATA_DIR):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
    except Exception as e:
        print(f"CRITICAL ERROR: Failed to create data directory {DATA_DIR}: {e}")
        pass

DB_PATH = os.path.join(DATA_DIR, 'hr_system.db')
print(f"DEBUG: Using Database Path: {DB_PATH}")

from flask import g, has_app_context

def _create_new_connection():
    # Add timeout to prevent "database is locked" errors when multiple connections access the DB
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    conn.row_factory = sqlite3.Row
    
    # Add datetime adapter to avoid warnings
    def adapt_datetime(dt):
        return dt.isoformat()
    
    def convert_datetime(s):
        try:
            return datetime.fromisoformat(s.decode())
        except:
            return None
    
    sqlite3.register_adapter(datetime, adapt_datetime)
    sqlite3.register_converter("datetime", convert_datetime)
    
    return conn

def get_db_connection():
    if has_app_context():
        if 'db' in g:
            try:
                # Test if the connection is still open
                g.db.execute("SELECT 1")
                return g.db
            except sqlite3.ProgrammingError:
                # Connection was closed manually by the code, let's create a new one
                pass
        
        # Create a new connection
        conn = _create_new_connection()
        
        # Keep track of all connections created in this request to close them all at teardown
        if 'all_dbs' not in g:
            g.all_dbs = []
        g.all_dbs.append(conn)
        
        g.db = conn
        return conn
    else:
        # For background tasks/threads without app context
        return _create_new_connection()

def close_db_connection(e=None):
    """Called at the end of each request to close any leaked connections"""
    all_dbs = g.pop('all_dbs', [])
    for db in all_dbs:
        try:
            db.close()
        except:
            pass

def get_setting(setting_key, default_value=None):
    """Retrieve a setting from the database, or return default."""
    try:
        conn = get_db_connection()
        try:
            result = conn.execute("SELECT setting_value FROM app_settings WHERE setting_key = ?", (setting_key,)).fetchone()
            if result:
                pass # conn.close() removed to prevent leak in Flask g
                return result['setting_value']
        except sqlite3.OperationalError:
            pass # Table doesn't exist yet
        pass # conn.close() removed to prevent leak in Flask g
        
        # If not found but default provided, save it to db
        if default_value is not None:
            set_setting(setting_key, str(default_value))
            return default_value
            
        return default_value
    except Exception as e:
        print(f"Error getting setting {setting_key}: {e}")
        return default_value

def set_setting(setting_key, setting_value):
    """Save or update a setting in the database."""
    try:
        conn = get_db_connection()
        # Validate if table exists
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='app_settings'")
        if not cursor.fetchone():
            cursor.execute('''
                CREATE TABLE app_settings (
                    setting_key TEXT PRIMARY KEY,
                    setting_value TEXT NOT NULL
                )
            ''')
            
        conn.execute('''
            INSERT INTO app_settings (setting_key, setting_value) 
            VALUES (?, ?)
            ON CONFLICT(setting_key) DO UPDATE SET setting_value = ?
        ''', (setting_key, setting_value, setting_value))
        conn.commit()
        pass # conn.close() removed to prevent leak in Flask g
        return True
    except Exception as e:
        print(f"Error setting {setting_key}: {e}")
        return False

# Bump this whenever a migration is added below. It is stamped into the
# database on every init_db() run, which makes "which build wrote this file"
# answerable after the fact — the single hardest question during a support
# call on a client machine.
SCHEMA_VERSION = 62


def get_schema_version(conn):
    """Version recorded in the file, or 0 for a pre-versioning database."""
    try:
        row = conn.execute(
            "SELECT setting_value FROM app_settings WHERE setting_key = 'schema_version'"
        ).fetchone()
        return int(row[0]) if row and row[0] else 0
    except Exception:
        return 0


def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    _version_before = get_schema_version(conn)
    
    # Employees table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS employees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_number TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            department TEXT NOT NULL,
            position TEXT NOT NULL,
            hire_date DATE NOT NULL,
            salary REAL NOT NULL,
            phone TEXT,
            email TEXT,
            address TEXT,
            default_start_time TEXT,
            default_end_time TEXT,
            is_active BOOLEAN DEFAULT 1,
            end_of_service_date DATE,
            manager_id INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (manager_id) REFERENCES employees (id)
        )
    ''')
    
    # Add new column to employees if it doesn't exist (for older databases)
    try:
        cursor.execute('ALTER TABLE employees ADD COLUMN end_of_service_date DATE')
    except Exception:
        pass
    
    # End of Service Records table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS end_of_service_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER NOT NULL,
            termination_date DATE NOT NULL,
            reason TEXT NOT NULL,
            years_of_service REAL DEFAULT 0,
            reward_amount REAL DEFAULT 0,
            net_payout REAL DEFAULT 0,
            notes TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            created_by INTEGER,
            FOREIGN KEY (employee_id) REFERENCES employees (id),
            FOREIGN KEY (created_by) REFERENCES users (id)
        )
    ''')
    # Salaries table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS salaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER NOT NULL,
            month INTEGER NOT NULL,
            year INTEGER NOT NULL,
            base_salary REAL NOT NULL,
            allowances REAL DEFAULT 0,
            deductions REAL DEFAULT 0,
            net_salary REAL NOT NULL,
            payment_date DATE,
            notes TEXT,
            FOREIGN KEY (employee_id) REFERENCES employees (id)
        )
    ''')
    
    # Salary settings table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS salary_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            setting_name TEXT UNIQUE NOT NULL,
            setting_value TEXT NOT NULL,
            description TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Attendance allowances table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS attendance_allowances (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER NOT NULL,
            month INTEGER NOT NULL,
            year INTEGER NOT NULL,
            early_arrival_days INTEGER DEFAULT 0,
            early_arrival_amount REAL DEFAULT 0,
            late_departure_days INTEGER DEFAULT 0,
            late_departure_amount REAL DEFAULT 0,
            total_allowance REAL DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (employee_id) REFERENCES employees (id)
        )
    ''')
    
    # Monthly leave balance table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS leave_balance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER NOT NULL,
            month INTEGER NOT NULL,
            year INTEGER NOT NULL,
            opening_balance REAL DEFAULT 0,
            earned_days REAL DEFAULT 0,
            used_days REAL DEFAULT 0,
            closing_balance REAL DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (employee_id) REFERENCES employees (id),
            UNIQUE(employee_id, month, year)
        )
    ''')
    
    # Leave deductions table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS leave_deductions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER NOT NULL,
            month INTEGER NOT NULL,
            year INTEGER NOT NULL,
            leave_type_id INTEGER NOT NULL,
            leave_days INTEGER DEFAULT 0,
            daily_salary REAL DEFAULT 0,
            deduction_amount REAL DEFAULT 0,
            is_paid_leave BOOLEAN DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (employee_id) REFERENCES employees (id),
            FOREIGN KEY (leave_type_id) REFERENCES leave_types (id)
        )
    ''')

    # Leave Types table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS leave_types (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            days_per_year INTEGER NOT NULL,
            is_paid BOOLEAN DEFAULT 1,
            is_carry_over BOOLEAN DEFAULT 0,
            requires_approval BOOLEAN DEFAULT 1,
            service_months_required INTEGER DEFAULT 0,
            requires_attachment BOOLEAN DEFAULT 0,
            is_hourly_permission BOOLEAN DEFAULT 0,
            monthly_hours_allowance REAL DEFAULT 0,
            allow_half_day BOOLEAN DEFAULT 0,
            allow_hourly BOOLEAN DEFAULT 0,
            global_id TEXT,
            updated_at TEXT,
            is_deleted INTEGER DEFAULT 0
        )
    ''')
    
    # Upgrade leave_types schema for older databases
    try:
        columns = cursor.execute("PRAGMA table_info(leave_types)").fetchall()
        column_names = {col['name'] for col in columns}
        if 'is_hourly_permission' not in column_names:
            cursor.execute("ALTER TABLE leave_types ADD COLUMN is_hourly_permission BOOLEAN DEFAULT 0")
        if 'monthly_hours_allowance' not in column_names:
            cursor.execute("ALTER TABLE leave_types ADD COLUMN monthly_hours_allowance REAL DEFAULT 0")
        if 'allow_half_day' not in column_names:
            cursor.execute("ALTER TABLE leave_types ADD COLUMN allow_half_day BOOLEAN DEFAULT 0")
        if 'allow_hourly' not in column_names:
            cursor.execute("ALTER TABLE leave_types ADD COLUMN allow_hourly BOOLEAN DEFAULT 0")
        if 'requires_attachment' not in column_names:
            cursor.execute("ALTER TABLE leave_types ADD COLUMN requires_attachment BOOLEAN DEFAULT 0")
        if 'global_id' not in column_names:
            cursor.execute("ALTER TABLE leave_types ADD COLUMN global_id TEXT")
        if 'updated_at' not in column_names:
            cursor.execute("ALTER TABLE leave_types ADD COLUMN updated_at TEXT")
        if 'is_deleted' not in column_names:
            cursor.execute("ALTER TABLE leave_types ADD COLUMN is_deleted INTEGER DEFAULT 0")
    except Exception:
        pass
    
    # Ensure name is unique to prevent duplicates on restart
    cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_leave_types_name ON leave_types (name)')
    
    # Leave Requests table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS leave_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER NOT NULL,
            leave_type_id INTEGER NOT NULL,
            start_date DATE NOT NULL,
            end_date DATE NOT NULL,
            days_count INTEGER NOT NULL,
            leave_duration_type TEXT DEFAULT 'full_day',
            start_time TEXT,
            end_time TEXT,
            attachment_path TEXT,
            reason TEXT,
            status TEXT DEFAULT 'pending',
            current_step INTEGER DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            is_paid_leave BOOLEAN DEFAULT 0,
            global_id TEXT,
            updated_at TEXT,
            is_deleted INTEGER DEFAULT 0,
            FOREIGN KEY (employee_id) REFERENCES employees (id),
            FOREIGN KEY (leave_type_id) REFERENCES leave_types (id)
        )
    ''')
    
    # Upgrade leave_requests schema for older databases
    try:
        columns = cursor.execute("PRAGMA table_info(leave_requests)").fetchall()
        column_names = {col['name'] for col in columns}
        if 'leave_duration_type' not in column_names:
            cursor.execute("ALTER TABLE leave_requests ADD COLUMN leave_duration_type TEXT DEFAULT 'full_day'")
        if 'start_time' not in column_names:
            cursor.execute("ALTER TABLE leave_requests ADD COLUMN start_time TEXT")
        if 'end_time' not in column_names:
            cursor.execute("ALTER TABLE leave_requests ADD COLUMN end_time TEXT")
        if 'attachment_path' not in column_names:
            cursor.execute("ALTER TABLE leave_requests ADD COLUMN attachment_path TEXT")
        if 'current_step' not in column_names:
            cursor.execute("ALTER TABLE leave_requests ADD COLUMN current_step INTEGER DEFAULT 1")
        if 'is_paid_leave' not in column_names:
            cursor.execute("ALTER TABLE leave_requests ADD COLUMN is_paid_leave BOOLEAN DEFAULT 0")
        if 'global_id' not in column_names:
            cursor.execute("ALTER TABLE leave_requests ADD COLUMN global_id TEXT")
        if 'updated_at' not in column_names:
            cursor.execute("ALTER TABLE leave_requests ADD COLUMN updated_at TEXT")
        if 'is_deleted' not in column_names:
            cursor.execute("ALTER TABLE leave_requests ADD COLUMN is_deleted INTEGER DEFAULT 0")
    except Exception:
        pass

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS leave_workflow_steps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            leave_type_id INTEGER,
            step_order INTEGER NOT NULL,
            approver_type TEXT NOT NULL, 
            approver_id INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (leave_type_id) REFERENCES leave_types(id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS leave_approvals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            leave_request_id INTEGER NOT NULL,
            step_order INTEGER NOT NULL,
            approver_user_id INTEGER,
            status TEXT DEFAULT 'pending',
            comments TEXT,
            action_date DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (leave_request_id) REFERENCES leave_requests(id)
        )
    ''')
    
    # Official Holidays table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS official_holidays (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            date DATE NOT NULL,
            type TEXT NOT NULL, -- 'رسمية', 'عيد', 'وطنية'
            is_paid BOOLEAN DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Add default official holidays
    count_result = cursor.execute('SELECT COUNT(*) as count FROM official_holidays').fetchone()
    if count_result and count_result['count'] == 0:
        cursor.execute('INSERT INTO official_holidays (name, date, type, is_paid) VALUES (?, ?, ?, ?)', ('رأس السنة الميلادية', '2025-01-01', 'رسمية', 1))
        cursor.execute('INSERT INTO official_holidays (name, date, type, is_paid) VALUES (?, ?, ?, ?)', ('عيد الفطر', '2025-03-31', 'عيد', 1))
        cursor.execute('INSERT INTO official_holidays (name, date, type, is_paid) VALUES (?, ?, ?, ?)', ('عيد الأضحى', '2025-06-17', 'عيد', 1))
        cursor.execute('INSERT INTO official_holidays (name, date, type, is_paid) VALUES (?, ?, ?, ?)', ('رأس السنة الهجرية', '2025-07-19', 'وطنية', 1))
        cursor.execute('INSERT INTO official_holidays (name, date, type, is_paid) VALUES (?, ?, ?, ?)', ('عيد المولد النبوي', '2025-09-15', 'وطنية', 1))
    
    # Salary Settings V2
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS salary_settings_v2 (
            setting_name TEXT PRIMARY KEY,
            setting_value TEXT NOT NULL,
            description TEXT,
            category TEXT DEFAULT 'general',
            is_editable BOOLEAN DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Insert default settings v2
    default_salary_settings = [
        ('currency_symbol', 'د.ك', 'رمز العملة', 'currency'),
        ('currency_position', 'right', 'موضع العملة (right/left)', 'currency'),
        ('rounding_internal_decimals', '3', 'عدد المنازل العشرية للحسابات الداخلية', 'calculation'),
        ('rounding_display_decimals', '2', 'عدد المنازل العشرية للعرض', 'calculation'),
        ('attendance_grace_minutes', '10', 'فترة السماح للحضور (دقائق)', 'attendance'),
        ('grace_early_minutes', '5', 'فترة السماح للانصراف المبكر (دقائق)', 'attendance'), # Deprecated logic, but keeping key for now as 'early deduction threshold' maybe? Or removal requested.
        # User requested clean up. Let's keep grace_early_minutes as "Early Exit Tolerance" if needed, but per spec "Early exit deducts". 
        # Actually user said "Remove unused settings". Let's stick to strict spec.
        # Spec: Late > 08:10 (10 mins grace). Early < 16:00 (Direct deduction? Or Grace?). 
        # Spec said: "Late time cannot be compensated... Grace Period: Start 10 mins...". 
        # Step 485: Remove/Deprecate early_arrival_bonus, late_departure_bonus.
        
        ('grace_late_arrival', '10', 'فترة السماح للتأخير الصباحي (دقائق)', 'attendance'),
        ('ot_start_buffer', '30', 'فترة الانتظار قبل احتساب الإضافي (دقائق)', 'overtime'), # 16:30 start
        
        ('overtime_round_to_minutes', '15', 'تقريب الساعات الإضافية (دقائق)', 'overtime'),
        ('overtime_cap_monthly_hours', '60', 'سقف الساعات الإضافية الشهري', 'overtime'),
        ('max_daily_ot_hours', '4', 'سقف الساعات الإضافية اليومي', 'overtime'),
        ('rounding_minutes', '1', 'تقريب الدقائق (1 = دقيقة بدقيقة)', 'calculation'),
        ('missing_punch_policy', 'invalid', 'سياسة البصمة الناقصة (invalid/absent)', 'attendance'),
        
        ('weekday_ot_multiplier', '1.25', 'مضاعف الساعات الإضافية في أيام العمل', 'overtime'),
        ('weekend_ot_multiplier', '1.5', 'مضاعف الساعات الإضافية في نهاية الأسبوع', 'overtime'),
        ('holiday_ot_multiplier', '2.0', 'مضاعف الساعات الإضافية في العطلات الرسمية', 'overtime'),
        
        ('overtime_hour_rate', '0', 'معدل ثابت للساعة الإضافية (0 = حسب الراتب)', 'overtime'), # Changed default to 0 to prefer calc from salary? Or keep 25? User said calc logic: Rate * Multiplier.
        
        ('include_paid_leaves_in_basic', '1', 'إدراج الإجازات المدفوعة في الراتب الأساسي', 'calculation'),
        ('leave_earned_per_month', '2.5', 'أيام الإجازة المكتسبة شهرياً', 'leave'),
        ('max_negative_leave_balance', '-10', 'أقصى رصيد إجازة سالب مسموح', 'leave')
    ]
    
    for setting_name, setting_value, description, category in default_salary_settings:
        cursor.execute('''
            INSERT OR IGNORE INTO salary_settings_v2 (setting_name, setting_value, description, category) 
            VALUES (?, ?, ?, ?)
        ''', (setting_name, setting_value, description, category))
    
    # Enhanced Holidays table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS holidays (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            type TEXT NOT NULL DEFAULT 'official',
            paid INTEGER NOT NULL DEFAULT 1,
            description TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Salary Anomalies table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS salary_anomalies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER NOT NULL,
            month INTEGER NOT NULL,
            year INTEGER NOT NULL,
            anomaly_type TEXT NOT NULL,
            date TEXT,
            message TEXT NOT NULL,
            severity TEXT DEFAULT 'warning',
            is_resolved BOOLEAN DEFAULT 0,
            resolved_at DATETIME,
            resolved_by INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (employee_id) REFERENCES employees (id),
            FOREIGN KEY (resolved_by) REFERENCES users (id)
        )
    ''')
    
    # Leave Cancellations table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS leave_cancellations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            leave_request_id INTEGER NOT NULL,
            employee_id INTEGER NOT NULL,
            original_start_date DATE NOT NULL,
            original_end_date DATE NOT NULL,
            original_days_count INTEGER NOT NULL,
            cancellation_date DATE NOT NULL,
            cancelled_days INTEGER NOT NULL,
            reason TEXT,
            cancelled_by INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (leave_request_id) REFERENCES leave_requests (id),
            FOREIGN KEY (employee_id) REFERENCES employees (id),
            FOREIGN KEY (cancelled_by) REFERENCES users (id)
        )
    ''')
    
    # Shifts table (deprecated)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS shifts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER NOT NULL,
            shift_date DATE NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            hours_worked REAL NOT NULL,
            notes TEXT,
            FOREIGN KEY (employee_id) REFERENCES employees (id)
        )
    ''')
    
    # Documents table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER NOT NULL,
            document_type TEXT NOT NULL,
            filename TEXT NOT NULL,
            original_filename TEXT NOT NULL,
            upload_date DATETIME DEFAULT CURRENT_TIMESTAMP,
            expiry_date DATE,
            is_expired BOOLEAN DEFAULT 0,
            expiry_notification_sent BOOLEAN DEFAULT 0,
            FOREIGN KEY (employee_id) REFERENCES employees (id)
        )
    ''')
    
    # Add new columns to documents if they don't exist
    try:
        cursor.execute('ALTER TABLE documents ADD COLUMN expiry_date DATE')
    except Exception:
        pass
    try:
        cursor.execute('ALTER TABLE documents ADD COLUMN is_expired BOOLEAN DEFAULT 0')
    except Exception:
        pass
    try:
        cursor.execute('ALTER TABLE documents ADD COLUMN expiry_notification_sent BOOLEAN DEFAULT 0')
    except Exception:
        pass
    
    # Document History table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS document_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER NOT NULL,
            action TEXT NOT NULL, -- 'created', 'updated', 'file_changed', 'deleted'
            old_filename TEXT,
            new_filename TEXT,
            old_document_type TEXT,
            new_document_type TEXT,
            old_expiry_date DATE,
            new_expiry_date DATE,
            old_employee_id INTEGER,
            new_employee_id INTEGER,
            change_date DATETIME DEFAULT CURRENT_TIMESTAMP,
            change_reason TEXT,
            FOREIGN KEY (document_id) REFERENCES documents (id),
            FOREIGN KEY (old_employee_id) REFERENCES employees (id),
            FOREIGN KEY (new_employee_id) REFERENCES employees (id)
        )
    ''')
    
    # Users table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            full_name TEXT NOT NULL,
            role TEXT DEFAULT 'admin',
            is_active BOOLEAN DEFAULT 1,
            managed_department_id INTEGER,
            employee_id INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (managed_department_id) REFERENCES departments_master (id),
            FOREIGN KEY (employee_id) REFERENCES employees (id)
        )
    ''')

    # Deduction Notices table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS deduction_notices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            amount REAL NOT NULL,
            reason TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            created_by INTEGER,
            FOREIGN KEY (employee_id) REFERENCES employees (id),
            FOREIGN KEY (created_by) REFERENCES users (id)
        )
    ''')
    
    # System Settings table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS system_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            app_name TEXT DEFAULT 'نظام إدارة الموارد البشرية',
            app_version TEXT DEFAULT '1.0.0',
            company_name TEXT DEFAULT 'شركتي',
            currency_name TEXT DEFAULT 'ريال سعودي',
            currency_symbol TEXT DEFAULT 'ر.س',
            currency_code TEXT DEFAULT 'SAR',
            currency_position TEXT DEFAULT 'right',
            working_hours_per_day INTEGER DEFAULT 8,
            company_address TEXT DEFAULT 'العنوان هنا',
            company_phone TEXT DEFAULT '',
            company_email TEXT DEFAULT '',
            default_language TEXT DEFAULT 'ar',
            timezone TEXT DEFAULT 'Asia/Riyadh',
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Upgrade settings schema
    try:
        columns = cursor.execute("PRAGMA table_info(system_settings)").fetchall()
        column_names = {col[1] for col in columns}
        if 'weekend_days' not in column_names:
            cursor.execute("ALTER TABLE system_settings ADD COLUMN weekend_days TEXT DEFAULT 'Fri,Sat'")
        if 'work_start_time' not in column_names:
            cursor.execute("ALTER TABLE system_settings ADD COLUMN work_start_time TEXT DEFAULT '08:00'")
        if 'work_end_time' not in column_names:
            cursor.execute("ALTER TABLE system_settings ADD COLUMN work_end_time TEXT DEFAULT '16:00'")
    except Exception:
        pass
    
    # Shift Types table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS shift_types (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            hours_per_day REAL DEFAULT 8.0,
            description TEXT,
            presence_start_time TEXT,
            presence_end_time TEXT,
            flex_mode TEXT DEFAULT 'none',
            is_active BOOLEAN DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Leave types table (Duplicate removed - see lines 144-150)
    # Ensuring schema consistency via migrations below

    # Attendance Excuses / Missions table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS attendance_excuses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER NOT NULL,
            date DATE NOT NULL,
            reason TEXT,
            type TEXT DEFAULT 'mission', -- mission, manual_override
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (employee_id) REFERENCES employees (id),
            UNIQUE(employee_id, date)
        )
    ''')


    # Departments Master table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS departments_master (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            is_active BOOLEAN DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Positions Master table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS positions_master (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            is_active BOOLEAN DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # --- RBAC Tables ---
    
    # Permissions Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS permissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL, -- e.g., 'employee.view'
            name TEXT NOT NULL,
            group_name TEXT, -- e.g., 'Employees'
            description TEXT
        )
    ''')
    
    # Roles Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS roles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            description TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Role-Permissions Mapping
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS role_permissions (
            role_id INTEGER NOT NULL,
            permission_id INTEGER NOT NULL,
            PRIMARY KEY (role_id, permission_id),
            FOREIGN KEY (role_id) REFERENCES roles (id) ON DELETE CASCADE,
            FOREIGN KEY (permission_id) REFERENCES permissions (id) ON DELETE CASCADE
        )
    ''')
    
    # User-Roles Mapping
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_roles (
            user_id INTEGER NOT NULL,
            role_id INTEGER NOT NULL,
            PRIMARY KEY (user_id, role_id),
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
            FOREIGN KEY (role_id) REFERENCES roles (id) ON DELETE CASCADE
        )
    ''')

    # Insert default shift types
    cursor.execute('''
        INSERT OR IGNORE INTO shift_types (name, start_time, end_time, hours_per_day, description) VALUES 
        ('صباحي', '08:00', '16:00', 8.0, 'شفت صباحي من 8 صباحاً إلى 4 عصراً'),
        ('مسائي', '16:00', '00:00', 8.0, 'شفت مسائي من 4 عصراً إلى 12 منتصف الليل'),
        ('ليلي', '00:00', '08:00', 8.0, 'شفت ليلي من 12 منتصف الليل إلى 8 صباحاً'),
        ('مرن', '09:00', '17:00', 8.0, 'شفت مرن حسب طبيعة العمل')
    ''')

    # Insert default departments and positions - REMOVED to prevent reappearing after delete
    # cursor.executemany('INSERT OR IGNORE INTO departments_master (name) VALUES (?)', [
    #     ('Board of Directors',),
    #     ('Human Resources',),
    #     ('Finance',),
    #     ('IT',),
    #     ('Marketing',),
    #     ('Sales',),
    #     ('Operations',),
    #     ('Procurement',),
    #     ('Customer Service',),
    #     ('Administration',)
    # ])
    # cursor.executemany('INSERT OR IGNORE INTO positions_master (name) VALUES (?)', [
    #     ('CEO.',),
    #     ('Board Member',),
    #     ('General Manager',),
    #     ('HR Manager',),
    #     ('Accountant',),
    #     ('IT Specialist',),
    #     ('Marketing Specialist',),
    #     ('Sales Representative',),
    #     ('Operations Supervisor',),
    #     ('Administrator',)
    # ])
    
    # Insert default leave types (Keep these as they are essential, but maybe user wants to edit them too? Leave for now)
    cursor.execute('''
        INSERT OR IGNORE INTO leave_types (name, days_per_year) VALUES 
        ('إجازة سنوية', 30),
        ('إجازة مرضية', 15), -- Nominal limit, but tiers handle the rest
        ('إجازة طارئة', 5),
        ('إجازة بدون راتب', 0)
    ''')
    cursor.execute('''INSERT OR IGNORE INTO leave_types
        (name, days_per_year, is_paid, requires_approval, is_hourly_permission, monthly_hours_allowance)
        VALUES ('استئذان', 0, 1, 0, 1, 8)''')


    
    # Insert default user
    # Insert default user
    from werkzeug.security import generate_password_hash
    admin_pw_hash = generate_password_hash('admin123')
    
    cursor.execute('INSERT OR IGNORE INTO users (username, password, full_name, role) VALUES (?, ?, ?, ?)', 
                   ('admin', admin_pw_hash, 'مدير النظام', 'admin'))
    
    # Insert default system settings
    cursor.execute('''
        INSERT OR IGNORE INTO system_settings (
            id, app_name, app_version, company_name, currency_name, 
            currency_symbol, currency_code, currency_position, working_hours_per_day,
            company_address, company_phone, company_email, default_language, timezone
        ) VALUES (
            1, 'نظام إدارة الموارد البشرية', '1.0.0', 'شركتي', 'ريال سعودي',
            'ر.س', 'SAR', 'right', 8,
            'العنوان هنا', '', '', 'ar', 'Asia/Riyadh'
        )
    ''')
    
    # --- App Settings & License Settings ---
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS app_settings (
            setting_key TEXT PRIMARY KEY,
            setting_value TEXT NOT NULL
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS license_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            license_key TEXT,
            last_check DATETIME,
            last_status_ok INTEGER DEFAULT 0
        )
    ''')
    cursor.execute("INSERT OR IGNORE INTO license_settings (id, license_key) VALUES (1, NULL)")
    
    # Initialize default max_devices if it's first run
    cursor.execute("INSERT OR IGNORE INTO app_settings (setting_key, setting_value) VALUES ('max_devices', '3')")

    
    # --- Fingerprint System Tables ---
    
    # 1. Fingerprint Devices
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS fingerprint_devices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_name TEXT NOT NULL,
            device_ip TEXT UNIQUE NOT NULL,
            device_port INTEGER DEFAULT 4370,
            is_active BOOLEAN DEFAULT 1,
            last_sync_time DATETIME,
            last_activity DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # 2. Fingerprint Users (Matches employees locally)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS fingerprint_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            device_id INTEGER NOT NULL,
            name TEXT,
            privilege INTEGER DEFAULT 0,
            password TEXT,
            group_id TEXT,
            card_number TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (device_id) REFERENCES fingerprint_devices (id),
            UNIQUE(user_id, device_id)
        )
    ''')
    
    # 2a. Fingerprint Templates (Biometric Data)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS fingerprint_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_sn TEXT,
            pin TEXT NOT NULL,
            finger_id INTEGER NOT NULL,
            valid INTEGER DEFAULT 1,
            duress INTEGER DEFAULT 0,
            template_type INTEGER DEFAULT 9,
            major_ver TEXT,
            minor_ver TEXT,
            format TEXT,
            template_data TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(pin, finger_id)
        )
    ''')

    # 2b. Facial Biometrics
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS fingerprint_faces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_sn TEXT,
            pin TEXT NOT NULL,
            face_id INTEGER DEFAULT 0,
            valid INTEGER DEFAULT 1,
            template_data TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(pin, face_id)
        )
    ''')
    
    # 2c. User Photos
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_sn TEXT,
            pin TEXT NOT NULL,
            photo_data TEXT NOT NULL, -- Base64 encoded or path
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(pin)
        )
    ''')
    
    # 3. Attendance Records (Raw logs from devices)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS attendance_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER NOT NULL,
            device_id INTEGER NOT NULL,
            check_time DATETIME NOT NULL,
            check_type INTEGER NOT NULL,
            verify_code INTEGER DEFAULT 0,
            sensor_id INTEGER DEFAULT 1,
            work_code INTEGER DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (employee_id) REFERENCES employees (id),
            FOREIGN KEY (device_id) REFERENCES fingerprint_devices (id)
        )
    ''')
    
    # 4. Sync Logs (For the Sync Logs page)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sync_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            level TEXT NOT NULL,
            device_ip TEXT,
            message TEXT NOT NULL,
            details TEXT
        )
    ''')

    # 5. ADMS Commands Queue
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS adms_commands (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id INTEGER NOT NULL,
            command_type TEXT NOT NULL,
            payload TEXT NOT NULL, -- JSON formatted
            status TEXT DEFAULT 'PENDING', -- PENDING, SENT, OK, FAILED
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (device_id) REFERENCES fingerprint_devices (id)
        )
    ''')
    
    # 6. Pending ADMS Devices (Discovery)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS pending_adms_devices (
            device_sn TEXT PRIMARY KEY,
            ip_address TEXT,
            device_name TEXT,
            last_activity DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # 7. Oracle Sync Queue (Offline Resilience)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS oracle_sync_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            check_time TEXT NOT NULL,
            check_type TEXT NOT NULL,
            verify_code TEXT,
            device_id INTEGER,
            retry_count INTEGER DEFAULT 0,
            status TEXT DEFAULT 'PENDING',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Migrations
    try:
        cursor.execute("ALTER TABLE oracle_sync_queue ADD COLUMN device_id INTEGER")
        conn.commit()
    except:
        pass # Already exists

    
    # Leave Tiers table (For tiered sick leave etc)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS leave_tiers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            leave_type_id INTEGER NOT NULL,
            start_day INTEGER NOT NULL,
            end_day INTEGER NOT NULL,
            payment_percentage REAL NOT NULL, -- 1.0, 0.75, 0.5, 0.25, 0.0
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (leave_type_id) REFERENCES leave_types (id)
        )
    ''')

    # Employee Compensation Days (For Holiday Work Credit)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS employee_comp_days (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER NOT NULL,
            date_earned DATE NOT NULL,
            reason TEXT DEFAULT 'Holiday Work',
            is_used BOOLEAN DEFAULT 0,
            used_date DATE,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (employee_id) REFERENCES employees (id)
        )
    ''')

    # Salary Leave Details Snapshot (Freezing calculation details)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS salary_leave_details (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            salary_id INTEGER NOT NULL,
            leave_type_id INTEGER NOT NULL,
            days_count INTEGER NOT NULL,
            tier_percentage REAL NOT NULL,
            deducted_amount REAL DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (salary_id) REFERENCES salaries (id),
            FOREIGN KEY (leave_type_id) REFERENCES leave_types (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS assets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER NOT NULL,
            asset_name TEXT NOT NULL,
            asset_code TEXT,
            issue_date TEXT,
            return_date TEXT,
            status TEXT,
            FOREIGN KEY (employee_id) REFERENCES employees (id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS dependents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            relation TEXT,
            dob TEXT,
            gender TEXT,
            FOREIGN KEY (employee_id) REFERENCES employees (id)
        )
    ''')

    # Put all ALTER TABLE statements here to ensure they run AFTER everything is created
    
    migrations = [
        ('users', 'managed_department_id', 'INTEGER'),
        ('users', 'employee_id', 'INTEGER'),
        ('employees', 'manager_id', 'INTEGER'),
        ('employees', 'national_id', 'TEXT'),
        ('employees', 'national_id_issue_date', 'DATE'),
        ('employees', 'national_id_expiry_date', 'DATE'),
        ('employees', 'national_id_place', 'TEXT'),
        ('employees', 'passport_number', 'TEXT'),
        ('employees', 'passport_expiry_date', 'DATE'),
        ('employees', 'dob', 'DATE'),
        ('employees', 'pob', 'TEXT'),
        ('employees', 'nationality', 'TEXT'),
        ('employees', 'religion', 'TEXT'),
        ('employees', 'gender', 'TEXT'),
        ('employees', 'marital_status', 'TEXT'),
        ('employees', 'military_status', 'TEXT'),
        ('employees', 'work_email', 'TEXT'),
        ('employees', 'work_phone', 'TEXT'),
        ('employees', 'work_ext', 'TEXT'),
        ('employees', 'emergency_contact_name', 'TEXT'),
        ('employees', 'emergency_contact_relation', 'TEXT'),
        ('employees', 'emergency_contact_phone', 'TEXT'),
        ('employees', 'contract_start_date', 'DATE'),
        ('employees', 'contract_end_date', 'DATE'),
        ('employees', 'contract_type', 'TEXT'),
        ('employees', 'employment_status', 'TEXT'),
        ('employees', 'grade_level', 'TEXT'),
        ('employees', 'branch_location', 'TEXT'),
        ('employees', 'bank_name', 'TEXT'),
        ('employees', 'bank_account_number', 'TEXT'),
        ('employees', 'bank_iban', 'TEXT'),
        ('employees', 'bank_swift', 'TEXT'),
        ('employees', 'social_security_number', 'TEXT'),
        ('employees', 'tax_id', 'TEXT'),
        ('employees', 'education_level', 'TEXT'),
        ('employees', 'university', 'TEXT'),
        ('employees', 'graduation_year', 'INTEGER'),
        ('employees', 'default_start_time', 'TEXT'),
        ('employees', 'default_end_time', 'TEXT'),
        ('employees', 'shift_type', 'TEXT DEFAULT "صباحي"'),
        ('employees', 'weekly_leave_days', 'INTEGER DEFAULT 2'),
        ('employees', 'weekly_leave_start', 'INTEGER DEFAULT 5'),
        ('employees', 'previous_leave_balance', 'REAL DEFAULT 0'),
        ('employees', 'weekly_leave_selected_days', 'TEXT DEFAULT "5,6"'),
        ('employees', 'cost_center', 'TEXT'),
        ('employees', 'is_active', 'BOOLEAN DEFAULT 1'),
        ('employees', 'card_number', 'TEXT'),
        ('employees', 'privilege', 'INTEGER DEFAULT 0'),
        ('employees', 'password', 'TEXT'),
        ('employees', 'group_id', 'TEXT'),
        ('employees', 'arabic_name', 'TEXT'),
        ('salaries', 'notes', 'TEXT'),
        ('salaries', 'is_locked', 'BOOLEAN DEFAULT 0'),
        ('leave_requests', 'is_paid_leave', 'BOOLEAN DEFAULT 0'),
        ('documents', 'expiry_date', 'DATE'),
        ('documents', 'is_expired', 'BOOLEAN DEFAULT 0'),
        ('documents', 'expiry_notification_sent', 'BOOLEAN DEFAULT 0'),
        ('official_holidays', 'type', "TEXT NOT NULL DEFAULT 'رسمية'"),
        ('official_holidays', 'is_paid', 'BOOLEAN DEFAULT 1'),
        ('leave_types', 'is_paid', 'BOOLEAN DEFAULT 1'),
        ('leave_types', 'is_carry_over', 'BOOLEAN DEFAULT 0'),
        ('leave_types', 'requires_approval', 'BOOLEAN DEFAULT 1'),
        ('leave_types', 'service_months_required', 'INTEGER DEFAULT 0'),
        ('fingerprint_devices', 'is_adms', 'BOOLEAN DEFAULT 0'),
        ('manpower_contracts', 'data_source', "TEXT DEFAULT 'raw'"),
        ('fingerprint_devices', 'serial_number', 'TEXT'),
        ('fingerprint_devices', 'last_activity', 'DATETIME'),
        ('system_settings', 'payroll_period_start_day', 'INTEGER DEFAULT 1'),
        ('shift_types', 'presence_start_time', 'TEXT'),
        ('shift_types', 'presence_end_time', 'TEXT'),
        ('shift_types', 'flex_mode', "TEXT DEFAULT 'none'"),
        ('shift_types', 'is_split', 'INTEGER DEFAULT 0'),
        # --- Payroll integrity: documented instruments + attested metrics ---
        ('attendance_excuses', 'created_by', 'INTEGER'),
        ('leave_requests', 'created_by', 'INTEGER'),
        ('attendance_excuses', 'side', "TEXT CHECK(side IN ('in','out'))"),
        ('attendance_records', 'source', "TEXT DEFAULT 'device'"),
        ('attendance_records', 'note', 'TEXT'),
        ('attendance_records', 'created_by', 'INTEGER'),
    ]
    
    for table, column, col_type in migrations:
        try:
            # Check if column exists first to be safe
            cursor.execute(f"PRAGMA table_info({table})")
            columns = [c[1] for c in cursor.fetchall()]


            if column not in columns:
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
        except Exception as e:
            print(f"Migration error for {table}.{column}: {e}")

    # --- Seeding Data (Post-Table Creation) ---
    try:
        # Seed Sick Leave Tiers (Kuwait Law)
        sick_leave_id_row = cursor.execute("SELECT id FROM leave_types WHERE name = 'إجازة مرضية'").fetchone()
        if sick_leave_id_row:
            sick_id = sick_leave_id_row['id']
            # Only checking table existence isn't enough, check if rows exist
            try:
                current_tiers = cursor.execute("SELECT COUNT(*) as count FROM leave_tiers WHERE leave_type_id = ?", (sick_id,)).fetchone()
                if current_tiers and current_tiers['count'] == 0:
                    cursor.execute("INSERT INTO leave_tiers (leave_type_id, start_day, end_day, payment_percentage) VALUES (?, 1, 15, 1.0)", (sick_id,))
                    cursor.execute("INSERT INTO leave_tiers (leave_type_id, start_day, end_day, payment_percentage) VALUES (?, 16, 25, 0.75)", (sick_id,))
                    cursor.execute("INSERT INTO leave_tiers (leave_type_id, start_day, end_day, payment_percentage) VALUES (?, 26, 35, 0.50)", (sick_id,))
                    cursor.execute("INSERT INTO leave_tiers (leave_type_id, start_day, end_day, payment_percentage) VALUES (?, 36, 45, 0.25)", (sick_id,))
                    cursor.execute("INSERT INTO leave_tiers (leave_type_id, start_day, end_day, payment_percentage) VALUES (?, 46, 365, 0.0)", (sick_id,))
                    print("✅ Seeded Kuwait Sick Leave Tiers")
            except Exception as e:
                print(f"Error seeding tiers (table might be missing?): {e}")

    except Exception as e:
        print(f"Seeding error: {e}")

    
        if 'work_end_time' not in columns:
            cursor.execute("ALTER TABLE system_settings ADD COLUMN work_end_time TEXT DEFAULT '16:00'")
    except Exception:
        pass
    
    # --- Payroll Foundation: item catalog, structures, transactions, loans ---
    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS payroll_item_types (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT UNIQUE NOT NULL,
                name_ar TEXT NOT NULL,
                name_en TEXT NOT NULL,
                kind TEXT NOT NULL CHECK(kind IN ('earning','deduction')),
                species TEXT NOT NULL CHECK(species IN ('fixed','computed','transaction')),
                affects TEXT NOT NULL DEFAULT 'monthly',
                is_system INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1,
                sort_order INTEGER DEFAULT 100
            )''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS employee_salary_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id INTEGER NOT NULL,
                item_type_id INTEGER NOT NULL,
                calc_method TEXT NOT NULL DEFAULT 'fixed' CHECK(calc_method IN ('fixed','percent_of_basic')),
                amount REAL DEFAULT 0,
                percent_value REAL DEFAULT 0,
                start_date TEXT,
                end_date TEXT,
                is_active INTEGER DEFAULT 1,
                notes TEXT,
                created_by INTEGER,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(employee_id) REFERENCES employees(id) ON DELETE CASCADE,
                FOREIGN KEY(item_type_id) REFERENCES payroll_item_types(id)
            )''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS payroll_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id INTEGER NOT NULL,
                item_type_id INTEGER NOT NULL,
                month INTEGER NOT NULL,
                year INTEGER NOT NULL,
                amount REAL NOT NULL,
                reason TEXT,
                reference TEXT,
                status TEXT DEFAULT 'active' CHECK(status IN ('active','cancelled')),
                entered_by INTEGER,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                cancelled_by INTEGER,
                cancelled_at DATETIME,
                FOREIGN KEY(employee_id) REFERENCES employees(id) ON DELETE CASCADE,
                FOREIGN KEY(item_type_id) REFERENCES payroll_item_types(id)
            )''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS employee_loans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id INTEGER NOT NULL,
                principal REAL NOT NULL,
                monthly_installment REAL NOT NULL,
                start_month INTEGER NOT NULL,
                start_year INTEGER NOT NULL,
                reason TEXT,
                status TEXT DEFAULT 'active' CHECK(status IN ('active','paused','settled')),
                created_by INTEGER,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                notes TEXT,
                FOREIGN KEY(employee_id) REFERENCES employees(id) ON DELETE CASCADE
            )''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS loan_payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                loan_id INTEGER NOT NULL,
                month INTEGER NOT NULL,
                year INTEGER NOT NULL,
                amount REAL NOT NULL,
                source TEXT DEFAULT 'manual' CHECK(source IN ('manual','payroll')),
                created_by INTEGER,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                notes TEXT,
                FOREIGN KEY(loan_id) REFERENCES employee_loans(id) ON DELETE CASCADE
            )''')
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS leave_balance_adjustments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id INTEGER NOT NULL,
                days REAL NOT NULL,
                reason TEXT NOT NULL,
                created_by INTEGER,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS shift_periods (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                shift_type_id INTEGER NOT NULL,
                seq INTEGER NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                UNIQUE (shift_type_id, seq),
                FOREIGN KEY (shift_type_id) REFERENCES shift_types (id)
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS manpower_contracts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_name TEXT NOT NULL,
                ministry_line1 TEXT,
                station_name TEXT,
                contract_no TEXT,
                work_description TEXT,
                payment_ref TEXT,
                company_logo TEXT,
                ot_normal REAL DEFAULT 1.25,
                ot_friday REAL DEFAULT 1.5,
                ot_holiday REAL DEFAULT 2.0,
                days_basis INTEGER DEFAULT 26,
                hours_per_day INTEGER DEFAULT 8,
                is_active INTEGER DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS adms_rejected_devices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                serial TEXT NOT NULL,
                remote_ip TEXT,
                reason TEXT NOT NULL,
                seen_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''CREATE INDEX IF NOT EXISTS idx_adms_rejected
            ON adms_rejected_devices (seen_at DESC)''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS employee_audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id INTEGER NOT NULL,
                field TEXT NOT NULL,
                old_value TEXT,
                new_value TEXT,
                category TEXT,
                source TEXT,
                note TEXT,
                changed_by INTEGER,
                changed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (employee_id) REFERENCES employees (id)
            )
        ''')
        cursor.execute('''CREATE INDEX IF NOT EXISTS idx_emp_audit
            ON employee_audit_log (employee_id, changed_at DESC)''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS payroll_ledger_postings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                month INTEGER NOT NULL,
                year INTEGER NOT NULL,
                period_start TEXT,
                period_end TEXT,
                run_id INTEGER,
                note TEXT,
                employees_count INTEGER,
                total_basic REAL,
                total_allowances REAL,
                total_deductions REAL,
                total_net REAL,
                entry_hash TEXT NOT NULL,
                prev_hash TEXT,
                chain_hash TEXT NOT NULL,
                posted_by INTEGER,
                posted_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS payroll_ledger_lines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                posting_id INTEGER NOT NULL,
                employee_id INTEGER,
                employee_number TEXT,
                name TEXT,
                arabic_name TEXT,
                basic REAL,
                total_allowances REAL,
                total_deductions REAL,
                net REAL,
                details_json TEXT,
                line_hash TEXT NOT NULL
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS payroll_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                month INTEGER NOT NULL,
                year INTEGER NOT NULL,
                status TEXT DEFAULT 'saved' CHECK(status IN ('saved','approved')),
                employees_count INTEGER DEFAULT 0,
                total_basic REAL DEFAULT 0,
                total_allowances REAL DEFAULT 0,
                total_deductions REAL DEFAULT 0,
                total_net REAL DEFAULT 0,
                period_start TEXT,
                period_end TEXT,
                created_by INTEGER,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                approved_by INTEGER,
                approved_at DATETIME,
                unlocked_by INTEGER,
                unlocked_at DATETIME,
                unlock_reason TEXT,
                UNIQUE(month, year)
            )''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS payroll_run_lines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                employee_id INTEGER NOT NULL,
                basic REAL DEFAULT 0,
                required_hours REAL DEFAULT 0,
                actual_hours REAL DEFAULT 0,
                total_allowances REAL DEFAULT 0,
                total_deductions REAL DEFAULT 0,
                net REAL DEFAULT 0,
                details_json TEXT,
                FOREIGN KEY(run_id) REFERENCES payroll_runs(id) ON DELETE CASCADE,
                FOREIGN KEY(employee_id) REFERENCES employees(id)
            )''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS payroll_hours_approvals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id INTEGER NOT NULL,
                month INTEGER NOT NULL,
                year INTEGER NOT NULL,
                required_hours REAL DEFAULT 0,
                computed_hours REAL DEFAULT 0,
                approved_hours REAL DEFAULT 0,
                required_days INTEGER DEFAULT 0,
                actual_days INTEGER DEFAULT 0,
                partial_days INTEGER DEFAULT 0,
                absent_days INTEGER DEFAULT 0,
                sick_days INTEGER DEFAULT 0,
                unpaid_days INTEGER DEFAULT 0,
                excuse_days INTEGER DEFAULT 0,
                weekly_off_days INTEGER DEFAULT 0,
                holiday_days INTEGER DEFAULT 0,
                late_mins INTEGER DEFAULT 0,
                early_mins INTEGER DEFAULT 0,
                presence_missing INTEGER DEFAULT 0,
                hourly_perm_hours REAL DEFAULT 0,
                ot_weekday_mins INTEGER DEFAULT 0,
                ot_weekend_mins INTEGER DEFAULT 0,
                ot_holiday_mins INTEGER DEFAULT 0,
                unentitled_rest_days INTEGER DEFAULT 0,
                period_start TEXT,
                period_end TEXT,
                notes TEXT,
                approved_by INTEGER,
                approved_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(employee_id, month, year),
                FOREIGN KEY(employee_id) REFERENCES employees(id) ON DELETE CASCADE
            )''')

        # Post-creation migrations for existing databases (v14/v15 upgrades)
        _post_migrations = [
            ('payroll_hours_approvals', 'required_days', 'INTEGER DEFAULT 0'),
            ('payroll_hours_approvals', 'actual_days', 'INTEGER DEFAULT 0'),
            ('payroll_hours_approvals', 'partial_days', 'INTEGER DEFAULT 0'),
            ('payroll_hours_approvals', 'absent_days', 'INTEGER DEFAULT 0'),
            ('payroll_hours_approvals', 'sick_days', 'INTEGER DEFAULT 0'),
            ('payroll_hours_approvals', 'unpaid_days', 'INTEGER DEFAULT 0'),
            ('payroll_hours_approvals', 'excuse_days', 'INTEGER DEFAULT 0'),
            ('payroll_hours_approvals', 'weekly_off_days', 'INTEGER DEFAULT 0'),
            ('payroll_hours_approvals', 'late_mins', 'INTEGER DEFAULT 0'),
            ('payroll_hours_approvals', 'presence_missing', 'INTEGER DEFAULT 0'),
            ('payroll_hours_approvals', 'hourly_perm_hours', 'REAL DEFAULT 0'),
            ('payroll_hours_approvals', 'ot_weekday_mins', 'INTEGER DEFAULT 0'),
            ('payroll_hours_approvals', 'ot_weekend_mins', 'INTEGER DEFAULT 0'),
            ('payroll_hours_approvals', 'ot_holiday_mins', 'INTEGER DEFAULT 0'),
            ('payroll_hours_approvals', 'unentitled_rest_days', 'INTEGER DEFAULT 0'),
            ('end_of_service_records', 'monthly_wage', "REAL"),
            ('end_of_service_records', 'daily_rate', "REAL"),
            ('end_of_service_records', 'basis', "TEXT DEFAULT '26'"),
            ('end_of_service_records', 'tier1_days', "REAL"),
            ('end_of_service_records', 'tier2_days', "REAL"),
            ('end_of_service_records', 'capped', "INTEGER DEFAULT 0"),
            ('end_of_service_records', 'fraction', "REAL"),
            ('end_of_service_records', 'gratuity_amount', "REAL"),
            ('end_of_service_records', 'leave_days', "REAL"),
            ('end_of_service_records', 'leave_amount', "REAL"),
            ('end_of_service_records', 'loans_deducted', "REAL"),
            ('end_of_service_records', 'breakdown_json', "TEXT"),
            ('payroll_hours_approvals', 'early_mins', 'INTEGER DEFAULT 0'),
            ('payroll_hours_approvals', 'holiday_days', 'INTEGER DEFAULT 0'),
            ('payroll_hours_approvals', 'period_start', 'TEXT'),
            ('payroll_hours_approvals', 'period_end', 'TEXT'),
            ('payroll_runs', 'period_start', 'TEXT'),
            ('payroll_runs', 'period_end', 'TEXT'),
            ('payroll_runs', 'approved_by', 'INTEGER'),
            ('payroll_runs', 'approved_at', 'DATETIME'),
            ('payroll_runs', 'unlocked_by', 'INTEGER'),
            ('payroll_runs', 'unlocked_at', 'DATETIME'),
            ('payroll_runs', 'unlock_reason', 'TEXT'),
        ]
        for _tbl, _col, _decl in _post_migrations:
            try:
                _cols = [c[1] for c in cursor.execute(f"PRAGMA table_info({_tbl})").fetchall()]
                if _col not in _cols:
                    cursor.execute(f"ALTER TABLE {_tbl} ADD COLUMN {_col} {_decl}")
            except Exception:
                pass
        # One-shot: widen loan_payments.source CHECK to accept 'eos'
        # (EOS settlement pays off loans through the settlement document).
        _lp_sql = cursor.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'loan_payments'"
        ).fetchone()
        if _lp_sql and "'eos'" not in _lp_sql[0]:
            _new_sql = _lp_sql[0].replace(
                "('manual','payroll')", "('manual','payroll','eos')").replace(
                "('manual', 'payroll')", "('manual', 'payroll', 'eos')")
            if "'eos'" in _new_sql:
                cursor.execute('ALTER TABLE loan_payments RENAME TO loan_payments_old')
                cursor.execute(_new_sql)
                _cols = [r[1] for r in cursor.execute(
                    'PRAGMA table_info(loan_payments)').fetchall()]
                _cl = ', '.join(_cols)
                cursor.execute(f'INSERT INTO loan_payments ({_cl}) '
                               f'SELECT {_cl} FROM loan_payments_old')
                cursor.execute('DROP TABLE loan_payments_old')

        # One-shot: shifts whose NAME implied flexibility get an explicit
        # flex_mode='any_time' once; after this, the name means nothing and
        # only the column decides (kills the silent naming trap).
        _done = cursor.execute(
            "SELECT 1 FROM salary_settings_v2 WHERE setting_name = '_flex_name_migrated'"
        ).fetchone()
        if not _done:
            for _tok in ('%*%', '%anytime%', '%any time%', '%anything%',
                         '%flexible%', '%حر%', '%مرن%', '%اني تايم%', '%أني تايم%'):
                cursor.execute('''UPDATE shift_types SET flex_mode = 'any_time'
                                  WHERE COALESCE(flex_mode, 'none') = 'none'
                                    AND (LOWER(name) LIKE ?
                                         OR LOWER(COALESCE(description, '')) LIKE ?)''',
                               (_tok, _tok))
            cursor.execute('''INSERT INTO salary_settings_v2
                              (setting_name, setting_value, description, category, is_editable)
                              VALUES ('_flex_name_migrated', '1',
                                      'internal one-shot marker', 'system', 0)''')


        payroll_catalog = [
            # code, name_ar, name_en, kind, species, affects, is_system, is_active, sort
            ('housing', 'بدل سكن', 'Housing Allowance', 'earning', 'fixed', 'monthly', 0, 1, 10),
            ('transport', 'بدل مواصلات', 'Transport Allowance', 'earning', 'fixed', 'monthly', 0, 1, 11),
            ('phone', 'بدل هاتف', 'Phone Allowance', 'earning', 'fixed', 'monthly', 0, 1, 12),
            ('hazard', 'بدل خطر', 'Hazard Allowance', 'earning', 'fixed', 'monthly', 0, 1, 13),
            ('nature_of_work', 'بدل طبيعة عمل', 'Nature of Work Allowance', 'earning', 'fixed', 'monthly', 0, 1, 14),
            ('night_shift', 'بدل وردية ليلية', 'Night Shift Allowance', 'earning', 'fixed', 'monthly', 0, 1, 15),
            ('education', 'بدل تعليم أبناء', 'Education Allowance', 'earning', 'fixed', 'monthly', 0, 1, 16),
            ('representation', 'بدل تمثيل', 'Representation Allowance', 'earning', 'fixed', 'monthly', 0, 1, 17),
            ('travel_ticket', 'بدل تذاكر سفر', 'Travel Ticket Allowance', 'earning', 'fixed', 'monthly', 0, 1, 18),
            ('overtime', 'العمل الإضافي', 'Overtime', 'earning', 'computed', 'monthly', 1, 1, 30),
            ('attendance_bonus', 'حافز انتظام الحضور', 'Attendance Bonus', 'earning', 'computed', 'monthly', 1, 1, 31),
            ('sick_leave_adjust', 'تسوية إجازة مرضية (شرائح)', 'Sick Leave Tier Adjustment', 'earning', 'computed', 'monthly', 1, 0, 32),
            ('performance_bonus', 'مكافأة أداء', 'Performance Bonus', 'earning', 'transaction', 'monthly', 0, 1, 40),
            ('incentive', 'حافز', 'Incentive', 'earning', 'transaction', 'monthly', 0, 1, 41),
            ('commission', 'عمولة مبيعات', 'Sales Commission', 'earning', 'transaction', 'monthly', 0, 1, 42),
            ('eid_bonus', 'مكافأة عيد', 'Eid Bonus', 'earning', 'transaction', 'monthly', 0, 1, 43),
            ('arrears', 'متأخرات / فروقات راتب', 'Salary Arrears', 'earning', 'transaction', 'monthly', 0, 1, 44),
            ('per_diem', 'بدل انتداب / مهمة', 'Per Diem', 'earning', 'transaction', 'monthly', 0, 1, 45),
            ('expense_reimb', 'استرداد مصاريف', 'Expense Reimbursement', 'earning', 'transaction', 'monthly', 0, 1, 46),
            ('thirteenth_salary', 'راتب الشهر الثالث عشر', '13th Month Salary', 'earning', 'transaction', 'annual', 0, 0, 47),
            ('profit_share', 'حصة أرباح سنوية', 'Profit Sharing', 'earning', 'transaction', 'annual', 0, 0, 48),
            ('leave_encashment', 'تصفية رصيد إجازات', 'Leave Encashment', 'earning', 'transaction', 'final', 0, 1, 60),
            ('eos_benefit', 'مكافأة نهاية الخدمة', 'End of Service Benefit', 'earning', 'computed', 'final', 1, 1, 61),
            ('absence', 'خصم غياب', 'Absence Deduction', 'deduction', 'computed', 'monthly', 1, 1, 70),
            ('lateness', 'خصم تأخير', 'Lateness Deduction', 'deduction', 'computed', 'monthly', 1, 1, 71),
            ('early_leave', 'خصم انصراف مبكر', 'Early Leave Deduction', 'deduction', 'computed', 'monthly', 1, 1, 72),
            ('unpaid_leave', 'إجازة غير مدفوعة', 'Unpaid Leave Deduction', 'deduction', 'computed', 'monthly', 1, 1, 73),
            ('missing_punch', 'عقوبة بصمة ناقصة', 'Missing Punch Penalty', 'deduction', 'computed', 'monthly', 1, 1, 74),
            ('social_insurance_emp', 'التأمينات الاجتماعية (حصة الموظف)', 'Social Insurance (Employee)', 'deduction', 'computed', 'monthly', 1, 0, 75),
            ('admin_penalty', 'جزاء إداري', 'Administrative Penalty', 'deduction', 'transaction', 'monthly', 0, 1, 80),
            ('advance_installment', 'قسط سلفة', 'Loan Installment', 'deduction', 'transaction', 'monthly', 1, 1, 81),
            ('court_garnishment', 'حجز قضائي', 'Court Garnishment', 'deduction', 'transaction', 'monthly', 0, 1, 82),
            ('damages', 'خصم عهدة / أضرار', 'Damages / Custody Deduction', 'deduction', 'transaction', 'monthly', 0, 1, 83),
            ('overpayment_recovery', 'استرداد صرف زائد', 'Overpayment Recovery', 'deduction', 'transaction', 'monthly', 0, 1, 84),
            ('medical_insurance_emp', 'تأمين طبي (حصة الموظف)', 'Medical Insurance (Employee)', 'deduction', 'transaction', 'monthly', 0, 1, 85),
            ('suspension_unpaid', 'إيقاف عن العمل بدون أجر', 'Unpaid Suspension', 'deduction', 'transaction', 'monthly', 0, 1, 86),
            ('tax', 'ضريبة دخل', 'Income Tax', 'deduction', 'transaction', 'monthly', 0, 0, 87),
        ]
        cursor.executemany('''
            INSERT OR IGNORE INTO payroll_item_types
            (code, name_ar, name_en, kind, species, affects, is_system, is_active, sort_order)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''', payroll_catalog)
    except Exception as e:
        print(f"Payroll foundation seeding error: {e}")

    # --- RBAC Seeding ---
    try:
        # 1. Define Default Permissions
        default_permissions = [
            # Dashboard
            
            # Employees
            # كشف قوى عاملة المقاولات — صلاحية مستقلّة عن تقارير الرواتب
            # لأنه يُتاح لعدد محدود من الشركات ولمستخدمين بعينهم، فربطه
            # بصلاحية عامة يكشفه لمن لا يعنيه.
            ('report.manpower', 'Manpower Contract Report',
             'Reports', 'View and export the ministry manpower payroll sheet'),
            ('employee.view', 'View Employees', 'Employees', 'View employee list and details'),
            ('employee.create', 'Create Employee', 'Employees', 'Add new employees'),
            ('employee.edit', 'Edit Employee', 'Employees', 'Edit employee details'),
            ('employee.delete', 'Delete Employee', 'Employees', 'Delete employees'),
            ('employee.documents', 'Manage Documents', 'Employees', 'Add/Edit documents'),
            ('document.manage', 'Manage Documents Module', 'Documents', 'Access and manage the documents module'),
            
            # Attendance
            ('attendance.view', 'View Attendance', 'Attendance', 'View attendance logs'),
            ('attendance.edit', 'Edit Attendance', 'Attendance', 'Manual adjust attendance'),
            ('attendance.devices', 'Manage Devices', 'Attendance', 'Add/Edit fingerprint devices'),
            
            # Leaves
            ('leave.view', 'View Leaves', 'Leaves', 'View leave requests'),
            ('leave.manage', 'Manage Leaves', 'Leaves', 'Approve/Reject leaves'),
            ('leave.settings', 'Leave Settings', 'Leaves', 'Configure leave types'),
            
            # Salaries
            ('salary.view', 'View Salaries', 'Salaries', 'View salary reports'),
            ('salary.calculate', 'Calculate Salary', 'Salaries', 'Run salary calculation'),
            ('salary.approve_hours', 'Approve Hours', 'Salaries', 'Review and attest monthly work hours before payroll'),
            ('salary.approve_run', 'Approve & Lock Payroll Run', 'Salaries', 'Final approval that locks a saved payroll run'),
            ('salary.settings', 'Salary Settings', 'Salaries', 'Configure salary rules'),
            
            # Reports
            ('report.view', 'View Reports', 'Reports', 'View general and detailed reports'),
            ('report.export', 'Export Reports', 'Reports', 'Show Excel export buttons inside report pages (requires report.view)'),

            # Admin / Settings
            ('admin.users', 'Manage Users', 'Administration', 'Manage system users and roles'),
            ('admin.settings', 'System Settings', 'Administration', 'Global system settings'),
            ('admin.roles', 'Manage Roles', 'Administration', 'Create/Edit roles and permissions'),
            
            # Pages
            ('page.dashboard', 'الرئيسية (Dashboard)', 'Pages', 'Access Dashboard page'),
            ('page.employees', 'قائمة الموظفين (Employees)', 'Pages', 'Access Employees List page'),
            ('page.employee_profile', 'ملف الموظف (Profile)', 'Pages', 'Access Employee Profile page'),
            ('page.contract', 'العقود (Contracts)', 'Pages', 'Access Contracts page'),
            ('page.manage_structure', 'إدارة الهيكل (Structure)', 'Pages', 'Access Manage Structure page'),
            ('page.org_chart', 'المخطط التنظيمي (Org Chart)', 'Pages', 'Access Org Chart page'),
            ('page.eos', 'نهاية الخدمة (End of Service)', 'Pages', 'Access EOS page'),
            ('page.first_last', 'الحضور أول/آخر (First/Last)', 'Pages', 'Access First/Last Print page'),
            ('page.manpower', 'القوى العاملة (Manpower)', 'Pages', 'Access Manpower Report page'),
            ('page.shift', 'إدارة الورديات (Shifts)', 'Pages', 'Access Manage Shifts page'),
            ('page.attendance', 'الحضور والانصراف (Attendance)', 'Pages', 'Access Fingerprint Dashboard page'),
            ('page.oracle_control', 'أوراكل كنترول (Oracle)', 'Pages', 'Access Oracle Control page'),
            ('page.leaves', 'طلبات الإجازة (Leave Requests)', 'Pages', 'Access Leaves page'),
            ('page.my_approvals', 'موافقاتي (My Approvals)', 'Pages', 'Access My Approvals page'),
            ('page.leave_settings', 'إعدادات الإجازات (Leave Settings)', 'Pages', 'Access Leave Settings page'),
            ('page.leave_balance', 'رصيد الإجازات (Leave Balance)', 'Pages', 'Access Leave Balance page'),
            ('page.hourly_perms', 'الأذونات بالساعة (Hourly Perms)', 'Pages', 'Access Hourly Permissions page'),
            ('page.hours_approval', 'اعتماد الساعات (Hours Approval)', 'Pages', 'Access Hours Approval page'),
            ('page.payroll_monthly', 'مسير الرواتب الشهري (Payroll Monthly)', 'Pages', 'Access Monthly Payroll page'),
            ('page.payroll_ledger', 'دفتر الأستاذ (Payroll Ledger)', 'Pages', 'Access Payroll Ledger page'),
            ('page.employee_history', 'سجل الراتب (Employee History)', 'Pages', 'Access Employee History page'),
            ('page.payroll_transactions', 'الحركات المالية (Transactions)', 'Pages', 'Access Transactions page'),
            ('page.loans', 'السلف (Loans)', 'Pages', 'Access Loans page'),
            ('page.salaries', 'الرواتب (Salaries)', 'Pages', 'Access Salaries page'),
            ('page.reports', 'التقارير العامة (General Reports)', 'Pages', 'Access Reports page'),
            ('page.custom_reports', 'التقارير المخصصة (Custom Reports)', 'Pages', 'Access Custom Reports page'),
            ('page.documents', 'المستندات (Documents)', 'Pages', 'Access Documents page'),
            ('page.roles', 'الصلاحيات (Roles)', 'Pages', 'Access Roles page'),
            ('page.users', 'المستخدمين (Users)', 'Pages', 'Access Users page'),
            ('page.settings', 'الإعدادات (Settings)', 'Pages', 'Access Settings page'),
            ('page.translations', 'الترجمات (Translations)', 'Pages', 'Access Translations page'),
        ]
        
        cursor.executemany('''
            INSERT OR IGNORE INTO permissions (code, name, group_name, description)
            VALUES (?, ?, ?, ?)
        ''', default_permissions)
        
        # 2. Create 'Super Admin' Role
        cursor.execute("DELETE FROM permissions WHERE code = 'dashboard.view'")
        cursor.execute("UPDATE permissions SET description = 'Manual adjustments + add tasks/excuses from inside reports (requires report pages access)' WHERE code = 'attendance.edit'")
        cursor.execute("INSERT OR IGNORE INTO roles (name, description) VALUES ('Super Admin', 'Full access to all features')")
        
        # 3. Assign ALL permissions to Super Admin
        super_admin_id = cursor.execute("SELECT id FROM roles WHERE name = 'Super Admin'").fetchone()['id']
        
        # Get all permission IDs
        all_perm_ids = [row['id'] for row in cursor.execute("SELECT id FROM permissions").fetchall()]
        
        # Link them (Insert or Ignore to avoid duplicates)
        for perm_id in all_perm_ids:
            cursor.execute("INSERT OR IGNORE INTO role_permissions (role_id, permission_id) VALUES (?, ?)", (super_admin_id, perm_id))
            
        # 4. Ensure default 'admin' user has 'Super Admin' role
        admin_user = cursor.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()
        if admin_user:
            cursor.execute("INSERT OR IGNORE INTO user_roles (user_id, role_id) VALUES (?, ?)", (admin_user['id'], super_admin_id))

        # 5. Heal legacy admin accounts (users.role = 'admin' string) that have no RBAC mapping
        legacy_admins = cursor.execute("SELECT id FROM users WHERE role = 'admin'").fetchall()
        for u in legacy_admins:
            cursor.execute("INSERT OR IGNORE INTO user_roles (user_id, role_id) VALUES (?, ?)", (u['id'], super_admin_id))
            
    except Exception as e:
        print(f"RBAC Seeding Error: {e}")

    # Stamp the schema version last: reaching this point means every
    # migration above completed without raising.
    try:
        cursor.execute('''CREATE TABLE IF NOT EXISTS app_settings (
            setting_key TEXT PRIMARY KEY, setting_value TEXT)''')
        cursor.execute(
            "INSERT INTO app_settings (setting_key, setting_value) "
            "VALUES ('schema_version', ?) "
            "ON CONFLICT(setting_key) DO UPDATE SET setting_value = excluded.setting_value",
            (str(SCHEMA_VERSION),))
        if _version_before and _version_before != SCHEMA_VERSION:
            print(f'DB schema upgraded: v{_version_before} -> v{SCHEMA_VERSION}')
        elif not _version_before:
            print(f'DB schema stamped at v{SCHEMA_VERSION}')
    except Exception as e:
        print(f'schema version stamp failed: {e}')

    conn.commit()
    pass # conn.close() removed to prevent leak in Flask g
