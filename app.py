from flask import Flask, render_template
import os
from datetime import datetime, timedelta
from utils.db import init_db, DATA_DIR, close_db_connection
from utils.common import time_to_minutes, format_currency
from utils.license import get_current_license_info
from utils.settings_utils import get_system_settings
import logging

# Import Blueprints
from routes.auth_routes import auth_bp
from routes.main_routes import main_bp
from routes.employee_routes import employee_bp
from routes.attendance_routes import attendance_bp
from routes.report_routes import report_bp
from routes.salary_routes import salary_bp
from routes.document_routes import document_bp
from routes.leave_routes import leave_bp
from routes.admin_routes import admin_bp
from routes.leave_settings_routes import leave_settings_bp
from routes.attendance_excuses import attendance_excuses_bp
from routes.translation_routes import translation_bp
from routes.shift_routes import shift_bp
from routes.adms_routes import adms_bp
from routes.eos_routes import eos_bp

import time
import threading
import sys

def background_license_checker():
    """
    Periodically checks license status online (every 2 minutes).
    If blocked, the key is revoked (deleted) by utils.license logic.
    """
    # Wait a bit before first check to allow startup to finish
    time.sleep(30) 
    
    while True:
        try:
            # Check every 120 seconds (2 minutes)
            time.sleep(120) 
            try:
                from utils.license import get_current_license_info
                # This triggers verify_license_full_flow -> check_online_license_secure
                # If blocked, it deletes the key from DB.
                get_current_license_info()
            except Exception as e:
                # Silent fail (e.g. no internet)
                pass 
        except Exception:
            pass

# Handle PyInstaller path for templates and static files
if getattr(sys, 'frozen', False):
    template_folder = os.path.join(sys._MEIPASS, 'templates')
    static_folder = os.path.join(sys._MEIPASS, 'static')
    app = Flask(__name__, template_folder=template_folder, static_folder=static_folder)
else:
    app = Flask(__name__)

# Register DB teardown
app.teardown_appcontext(close_db_connection)

# Configuration
# Configuration
app.secret_key = os.environ.get('SECRET_KEY') 
if not app.secret_key:
    # Persist the secret key to a file in DATA_DIR to avoid invalidating sessions on restart
    key_file = os.path.join(DATA_DIR, '.secret_key')
    if os.path.exists(key_file):
        with open(key_file, 'r') as f:
            app.secret_key = f.read().strip()
    else:
        import secrets
        app.secret_key = secrets.token_hex(32)
        try:
            with open(key_file, 'w') as f:
                f.write(app.secret_key)
        except Exception as e:
            logging.error(f"Failed to save SECRET_KEY to {key_file}: {e}")
    
    # Only Log warning once, not critical if we persisted it
    if not os.path.exists(key_file):
        logging.critical("CRITICAL: SECRET_KEY not set and could not be persisted. Sessions will be lost on restart.")

app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)
app.config['UPLOAD_FOLDER'] = os.path.join(DATA_DIR, 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max limit

# Ensure upload directory exists
if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

# Configure Logging
from logging import FileHandler as RotatingFileHandler
log_file = os.path.join(DATA_DIR, 'error.log')
handler = RotatingFileHandler(log_file)
handler.setLevel(logging.ERROR)
formatter = logging.Formatter('%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]')
handler.setFormatter(formatter)

app.logger.addHandler(handler)
# Standard logging also to the same file
logging.getLogger().addHandler(handler)
logging.getLogger().setLevel(logging.ERROR)

# Register Blueprints
app.register_blueprint(auth_bp)
app.register_blueprint(main_bp)
app.register_blueprint(employee_bp)
app.register_blueprint(leave_bp)
app.register_blueprint(salary_bp)
app.register_blueprint(document_bp)
app.register_blueprint(attendance_bp)
app.register_blueprint(report_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(leave_settings_bp)
app.register_blueprint(attendance_excuses_bp)
app.register_blueprint(translation_bp)
app.register_blueprint(shift_bp)
from routes.first_last_routes import fl_bp
app.register_blueprint(fl_bp)
from routes.manpower_routes import manpower_bp
app.register_blueprint(manpower_bp)
app.register_blueprint(adms_bp)
app.register_blueprint(eos_bp)
from routes.payroll_routes import payroll_bp
app.register_blueprint(payroll_bp)
from routes.contract_routes import contract_bp
app.register_blueprint(contract_bp)
from routes.portal_routes import portal_bp
app.register_blueprint(portal_bp)

# --- Internationalization (i18n) Setup ---
from flask_babel import Babel

def get_locale():
    from flask import session, request
    # 1. Check Session (Explicit Switch)
    if 'lang' in session:
        return session['lang']
    # 2. Check User DB Profile (if logged in)
    # (Optional: Load this into session on login to avoid DB hits)
    
    # 3. Browser Header or Default
    return request.accept_languages.best_match(['ar', 'en']) or 'ar'

def get_timezone():
    return 'Asia/Kuwait'

babel = Babel(app, locale_selector=get_locale, timezone_selector=get_timezone)

@app.context_processor
def inject_i18n():
    from flask_babel import get_locale
    return dict(
        get_locale=get_locale,
        get_direction=lambda: get_locale().text_direction
    )


@app.context_processor
def inject_version():
    """الإصدار متاح لكل قالب.

    يُعرض في التذييل لأن أول سؤال في أي مكالمة دعم فني هو: ما النسخة التي
    تشغّلها؟ وإخفاؤه يجعل الإجابة تخمينًا.
    """
    try:
        from utils.version_info import CURRENT_VERSION, RELEASE_NAME, BUILD_DATE
        return dict(app_version=CURRENT_VERSION,
                    app_release_name=RELEASE_NAME,
                    app_build_date=BUILD_DATE)
    except Exception:
        return dict(app_version='', app_release_name='', app_build_date='')




from flask import request, redirect, url_for, flash, render_template
@app.before_request
def check_license_globally():
    # Allow assets, static files, login/logout, and the license page itself
    if request.endpoint in ['main.license_page', 'auth.login', 'auth.logout', 'auth.reset_password', 'static'] or request.path.startswith('/static/'):
        return

    # Check license
    from flask import g
    if 'license_info' not in g:
        g.license_info = get_current_license_info()
    license_info = g.license_info
    if not license_info.get('ok', False):
        return redirect(url_for('main.license_page'))

@app.route('/')
def index():
    return redirect(url_for('main.index'))

@app.route('/test-trans')
def test_trans():
    from flask_babel import gettext
    return f"Key: placeholder.arabic_name <br> Trans: {gettext('placeholder.arabic_name')} <br> Locale: {get_locale()}"


# Register Template Filters
app.jinja_env.filters['to_minutes'] = time_to_minutes

@app.template_filter('datetime_parse')
def datetime_parse_filter(value):
    if not value: return None
    try:
        if isinstance(value, str):
            return datetime.strptime(value, '%Y-%m-%d %H:%M:%S')
        return value
    except:
        return None

@app.context_processor
def inject_now():
    return {'now_datetime': datetime.now}

# Check if pyzk is installed for context processor
try:
    from zk import ZK
    FINGERPRINT_AVAILABLE = True
except ImportError:
    FINGERPRINT_AVAILABLE = False

# Context Processors
@app.context_processor
def inject_license_info():
    from flask import g
    if 'license_info' not in g:
        g.license_info = get_current_license_info()
    return dict(
        license_info=g.license_info,
        fingerprint_available=FINGERPRINT_AVAILABLE
    )

@app.context_processor
def inject_settings():
    from flask import g
    if 'system_settings' not in g:
        g.system_settings = get_system_settings() or {}
    settings = g.system_settings
    from utils.rbac import RBACService
    from flask import session
    
    def has_permission(code):
        if not session.get('user_id'):
            return False
        return RBACService.has_permission(session['user_id'], code)

    return dict(
        settings=settings,
        format_currency=lambda amount: format_currency(amount, settings),
        has_permission=has_permission
    )

@app.template_filter('format_currency')
def format_currency_filter(amount):
    settings = get_system_settings() or {}
    return format_currency(amount, settings)

# Error Handlers
@app.errorhandler(404)
def page_not_found(e):
    return "الصفحة غير موجودة", 404

@app.errorhandler(500)
def internal_server_error(e):
    import traceback
    logging.error(f"Internal Server Error: {e}\n{traceback.format_exc()}")
    # Show a clean Arabic error message for production
    return render_template('errors/500.html', error=str(e)), 500

def main():
    """تشغيل التطبيق مع تهيئة القاعدة وقراءة الإعدادات."""
    # Initialize Database
    init_db()

    default_host = '0.0.0.0'
    default_port = 5000

    host = os.environ.get('APP_HOST', os.environ.get('HOST', default_host))
    try:
        port = int(os.environ.get('APP_PORT', os.environ.get('PORT', default_port)))
    except ValueError:
        port = default_port

    # debug = os.environ.get('FLASK_DEBUG', '').lower() in {'1', 'true', 'yes', 'on'}
    debug = True # ENABLE DEBUG FOR TROUBLESHOOTING
    # Startup License Check
    print("--- STARTUP LICENSE CHECK ---")
    try:
        from utils.license import get_current_license_info
        info = get_current_license_info()
        print(f"Startup Check Result: {info}")
    except Exception as e:
        print(f"Startup Check Error: {e}")
    print("--- END STARTUP CHECK ---")

    # Start Background License Checker
    checker_thread = threading.Thread(target=background_license_checker, daemon=True)
    checker_thread.start()

    try:
        print(f"تشغيل خادم HR System على http://{host}:{port} (debug={debug})")
    except UnicodeEncodeError:
        print(f"Starting HR System server on http://{host}:{port} (debug={debug})")
    
    try:
        from waitress import serve
        serve(app, host=host, port=port, threads=8)
    except Exception as e:
        try:
            print(f"خطأ في تشغيل الخادم: {e}")
        except UnicodeEncodeError:
            print(f"Error starting server: {e}")
        raise

if __name__ == '__main__':
    main()