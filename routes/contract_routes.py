from flask import Blueprint, request, jsonify, render_template, current_app, send_file, flash, redirect, url_for
import os
from datetime import datetime
import pandas as pd
from utils.db import get_db_connection, DATA_DIR
from utils.auth import login_required
from utils.rbac import require_permission
from werkzeug.utils import secure_filename
from docxtpl import DocxTemplate
import json

contract_bp = Blueprint('contract', __name__, url_prefix='/contracts')

# Ensure directories exist
TEMPLATE_DIR = os.path.join(DATA_DIR, 'templates')
CONTRACT_OUTPUT_DIR = os.path.join(DATA_DIR, 'generated_contracts')

for d in [TEMPLATE_DIR, CONTRACT_OUTPUT_DIR]:
    if not os.path.exists(d):
        os.makedirs(d, exist_ok=True)

DEFAULT_TEMPLATE = os.path.join(TEMPLATE_DIR, 'contract_template.docx')

@contract_bp.route('/')
@login_required
@require_permission('page.contract')
def index():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Ensure table exists (fallback if init_db was not called)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS contract_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_name TEXT NOT NULL,
            employee_name_en TEXT,
            civil_id TEXT,
            nationality TEXT,
            nationality_en TEXT,
            profession TEXT,
            profession_en TEXT,
            salary REAL,
            start_date TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    contracts = cursor.execute("SELECT * FROM contract_data ORDER BY id DESC").fetchall()
    return render_template('contracts/index.html', contracts=contracts)


@contract_bp.route('/upload', methods=['POST'])
@login_required
@require_permission('employee.documents')
def upload_excel():
    if 'file' not in request.files:
        flash("لم يتم تحديد ملف", "danger")
        return redirect(url_for('contract.index'))
        
    file = request.files['file']
    if file.filename == '':
        flash("لم يتم تحديد ملف", "danger")
        return redirect(url_for('contract.index'))
        
    if file and (file.filename.endswith('.xlsx') or file.filename.endswith('.xls')):
        filename = secure_filename(file.filename)
        filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        
        try:
            df = pd.read_excel(filepath)
            conn = get_db_connection()
            cursor = conn.cursor()
            
            # Map Excel columns to DB columns. 
            # We assume the Excel has headers like: Name, CivilID, Nationality, Profession, Salary, StartDate, NameEn, NationalityEn, ProfessionEn
            # To be robust, we'll try to map common names or just expect specific ones.
            # Example mapping:
            col_map = {
                'الاسم': 'employee_name',
                'الرقم المدني': 'civil_id',
                'الجنسية': 'nationality',
                'المهنة': 'profession',
                'الراتب': 'salary',
                'تاريخ البدء': 'start_date',
                'Name (EN)': 'employee_name_en',
                'Nationality (EN)': 'nationality_en',
                'Profession (EN)': 'profession_en'
            }
            
            # If columns match exactly the DB names, use them, otherwise try mapping
            for index, row in df.iterrows():
                data = {}
                for excel_col, db_col in col_map.items():
                    if excel_col in df.columns:
                        val = row[excel_col]
                        # Handle NaNs
                        data[db_col] = None if pd.isna(val) else str(val).strip()
                    elif db_col in df.columns:
                        val = row[db_col]
                        data[db_col] = None if pd.isna(val) else str(val).strip()
                    else:
                        data[db_col] = None
                
                # Check if at least employee_name is present
                if data.get('employee_name'):
                    cursor.execute('''
                        INSERT INTO contract_data 
                        (employee_name, employee_name_en, civil_id, nationality, nationality_en, profession, profession_en, salary, start_date)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        data.get('employee_name'), data.get('employee_name_en'), 
                        data.get('civil_id'), data.get('nationality'), 
                        data.get('nationality_en'), data.get('profession'), 
                        data.get('profession_en'), data.get('salary'), data.get('start_date')
                    ))
            
            conn.commit()
            flash("تم رفع البيانات بنجاح", "success")
        except Exception as e:
            flash(f"حدث خطأ أثناء معالجة الملف: {e}", "danger")
    else:
        flash("يرجى رفع ملف Excel صحيح", "danger")
        
    return redirect(url_for('contract.index'))


@contract_bp.route('/save', methods=['POST'])
@login_required
@require_permission('employee.documents')
def save_contract():
    data = request.form
    contract_id = data.get('id')
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    if contract_id:
        cursor.execute('''
            UPDATE contract_data 
            SET employee_name=?, employee_name_en=?, civil_id=?, nationality=?, nationality_en=?, profession=?, profession_en=?, salary=?, start_date=?
            WHERE id=?
        ''', (
            data.get('employee_name'), data.get('employee_name_en'), 
            data.get('civil_id'), data.get('nationality'), 
            data.get('nationality_en'), data.get('profession'), 
            data.get('profession_en'), data.get('salary'), data.get('start_date'),
            contract_id
        ))
    else:
        cursor.execute('''
            INSERT INTO contract_data 
            (employee_name, employee_name_en, civil_id, nationality, nationality_en, profession, profession_en, salary, start_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            data.get('employee_name'), data.get('employee_name_en'), 
            data.get('civil_id'), data.get('nationality'), 
            data.get('nationality_en'), data.get('profession'), 
            data.get('profession_en'), data.get('salary'), data.get('start_date')
        ))
        
    conn.commit()
    flash("تم حفظ البيانات بنجاح", "success")
    return redirect(url_for('contract.index'))


@contract_bp.route('/delete/<int:id>', methods=['POST'])
@login_required
@require_permission('employee.documents')
def delete_contract(id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM contract_data WHERE id=?", (id,))
    conn.commit()
    return jsonify({"success": True})


@contract_bp.route('/generate/<int:id>')
@login_required
@require_permission('employee.documents')
def generate_contract(id):
    conn = get_db_connection()
    contract = conn.execute("SELECT * FROM contract_data WHERE id=?", (id,)).fetchone()
    
    if not contract:
        flash("لم يتم العثور على العقد", "danger")
        return redirect(url_for('contract.index'))
        
    if not os.path.exists(DEFAULT_TEMPLATE):
        flash("قالب العقد غير موجود. يرجى التأكد من رفع القالب.", "danger")
        return redirect(url_for('contract.index'))
        
    try:
        doc = DocxTemplate(DEFAULT_TEMPLATE)
        
        # Prepare context
        context = {
            'employee_name': contract['employee_name'] or '',
            'employee_name_en': contract['employee_name_en'] or '',
            'civil_id': contract['civil_id'] or '',
            'nationality': contract['nationality'] or '',
            'nationality_en': contract['nationality_en'] or '',
            'profession': contract['profession'] or '',
            'profession_en': contract['profession_en'] or '',
            'salary': contract['salary'] or '',
            'start_date': contract['start_date'] or ''
        }
        
        doc.render(context)
        
        filename = f"Contract_{contract['civil_id'] or contract['id']}.docx"
        output_path = os.path.join(CONTRACT_OUTPUT_DIR, filename)
        doc.save(output_path)
        
        return send_file(output_path, as_attachment=True, download_name=filename)
    except Exception as e:
        flash(f"حدث خطأ أثناء توليد العقد: {e}", "danger")
        return redirect(url_for('contract.index'))
