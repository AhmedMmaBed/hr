from utils.auth import login_required
from utils.rbac import require_permission
from flask import Blueprint, render_template, request, jsonify, current_app
import os
import subprocess
from babel.messages.pofile import read_po, write_po
from babel.messages.catalog import Catalog

translation_bp = Blueprint('translation', __name__, url_prefix='/translations')

TRANSLATIONS_DIR = os.path.join(os.getcwd(), 'translations')

def get_po_file_path(lang_code):
    return os.path.join(TRANSLATIONS_DIR, lang_code, 'LC_MESSAGES', 'messages.po')

@translation_bp.route('/')
@login_required
@require_permission('page.translations')
def index():
    return render_template('translation_manager.html')

@translation_bp.route('/api/list')
@login_required
@require_permission('admin.settings')
def list_translations():
    try:
        # Detect available languages
        languages = []
        if os.path.exists(TRANSLATIONS_DIR):
            languages = [d for d in os.listdir(TRANSLATIONS_DIR) if os.path.isdir(os.path.join(TRANSLATIONS_DIR, d))]
        
        translations = {}
        all_keys = set()

        for lang in languages:
            po_path = get_po_file_path(lang)
            if os.path.exists(po_path):
                with open(po_path, 'r', encoding='utf-8') as f:
                    catalog = read_po(f)
                    translations[lang] = {}
                    for message in catalog:
                        if message.id:
                            translations[lang][message.id] = message.string
                            all_keys.add(message.id)
        
        # Structure data for frontend: [{key: '...', en: '...', ar: '...'}, ...]
        result = []
        for key in sorted(all_keys):
            item = {'key': key}
            for lang in languages:
                item[lang] = translations.get(lang, {}).get(key, '')
            result.append(item)
            
        return jsonify({'success': True, 'languages': languages, 'translations': result})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@translation_bp.route('/api/save', methods=['POST'])
@login_required
@require_permission('admin.settings')
def save_translation():
    try:
        data = request.json
        key = data.get('key')
        lang = data.get('lang')
        value = data.get('value')
        
        if not key or not lang:
            return jsonify({'success': False, 'message': 'Missing data'})

        po_path = get_po_file_path(lang)
        if not os.path.exists(po_path):
             return jsonify({'success': False, 'message': 'Language file not found'})
             
        # Read existing catalog
        with open(po_path, 'r', encoding='utf-8') as f:
            catalog = read_po(f)

        # Update or add message
        message = catalog.get(key)
        if message:
            message.string = value
        else:
            from babel.messages.catalog import Message
            catalog.add(key, value)
            
        # Write back
        with open(po_path, 'wb') as f:
            write_po(f, catalog)
            
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@translation_bp.route('/api/compile', methods=['POST'])
@login_required
@require_permission('admin.settings')
def compile_translations():
    try:
        # Run pybabel compile
        result = subprocess.run(['pybabel', 'compile', '-d', 'translations'], 
                              cwd=os.getcwd(), 
                              capture_output=True, 
                              text=True)
        
        if result.returncode == 0:
            return jsonify({'success': True, 'message': 'تم تجميع الترجمات بنجاح. يرجى إعادة تشغيل التطبيق لتطبيق التغييرات.'})
        else:
            return jsonify({'success': False, 'message': f'Error: {result.stderr}'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@translation_bp.route('/api/add_language', methods=['POST'])
@login_required
@require_permission('admin.settings')
def add_language():
    try:
        data = request.json
        lang_code = data.get('code')
        
        if not lang_code:
            return jsonify({'success': False, 'message': 'Code is required'})
            
        # Run pybabel init
        cmd = ['pybabel', 'init', '-i', 'messages.pot', '-d', 'translations', '-l', lang_code]
        # Check if messages.pot exists, if not generate it
        if not os.path.exists('messages.pot'):
             subprocess.run(['pybabel', 'extract', '-F', 'babel.cfg', '-o', 'messages.pot', '.'], cwd=os.getcwd())

        result = subprocess.run(cmd, cwd=os.getcwd(), capture_output=True, text=True)
        
        if result.returncode == 0:
            return jsonify({'success': True, 'message': f'Language {lang_code} added successfully'})
        else:
            return jsonify({'success': False, 'message': f'Error: {result.stderr}'})
            
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@translation_bp.route('/api/delete_language', methods=['POST'])
@login_required
@require_permission('admin.settings')
def delete_language():
    try:
        data = request.json
        lang_code = data.get('code')
        
        if not lang_code:
            return jsonify({'success': False, 'message': 'Code is required'})
            
        lang_dir = os.path.join(TRANSLATIONS_DIR, lang_code)
        
        if not os.path.exists(lang_dir):
            return jsonify({'success': False, 'message': 'Language not found'})
            
        import shutil
        shutil.rmtree(lang_dir)
        
        return jsonify({'success': True, 'message': f'Language {lang_code} deleted successfully'})
            
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@translation_bp.route('/api/add_key', methods=['POST'])
@login_required
@require_permission('admin.settings')
def add_key():
    try:
        data = request.json
        key = data.get('key')
        
        if not key:
            return jsonify({'success': False, 'message': 'Key is required'})
            
        # Detect available languages
        languages = []
        if os.path.exists(TRANSLATIONS_DIR):
            languages = [d for d in os.listdir(TRANSLATIONS_DIR) if os.path.isdir(os.path.join(TRANSLATIONS_DIR, d))]
            
        added_count = 0
        
        for lang in languages:
            po_path = get_po_file_path(lang)
            if os.path.exists(po_path):
                # Read existing catalog
                with open(po_path, 'r', encoding='utf-8') as f:
                    catalog = read_po(f)
                
                # Check if key exists
                if key not in catalog:
                    catalog.add(key, '') # Add with empty translation
                    
                    # Write back
                    with open(po_path, 'wb') as f:
                        write_po(f, catalog)
                    added_count += 1
        
        if added_count > 0:
            return jsonify({'success': True, 'message': f'Key "{key}" added to {added_count} languages'})
        else:
            return jsonify({'success': True, 'message': f'Key "{key}" already exists (or no languages found)'})
            
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})
