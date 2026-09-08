from flask import Blueprint, render_template, request, redirect, url_for, flash, send_from_directory, current_app, jsonify
from flask_babel import gettext
import os
import time
import shutil
from werkzeug.utils import secure_filename
from utils.db import get_db_connection
from utils.auth import login_required
from utils.common import allowed_file
from utils.document_utils import get_document_expiry_alerts
from utils.rbac import require_permission

document_bp = Blueprint('document', __name__)

@document_bp.route('/documents')
@login_required
@require_permission('page.documents')
def documents():
    conn = get_db_connection()
    
    # جلب الوثائق مع معلومات انتهاء الصلاحية
    documents = conn.execute('''
        SELECT d.*, e.name, e.employee_number,
               CASE 
                   WHEN d.expiry_date IS NULL THEN 'لا يوجد تاريخ انتهاء'
                   WHEN d.expiry_date < DATE('now') THEN 'منتهية'
                   WHEN d.expiry_date <= DATE('now', '+10 days') THEN 'تنتهي قريباً'
                   ELSE 'صالحة'
               END as expiry_status,
               CASE 
                   WHEN d.expiry_date IS NULL THEN NULL
                   WHEN d.expiry_date < DATE('now') THEN 0
                   ELSE JULIANDAY(d.expiry_date) - JULIANDAY('now')
               END as days_remaining
        FROM documents d 
        JOIN employees e ON d.employee_id = e.id 
        ORDER BY d.upload_date DESC
    ''').fetchall()
    
    employees = conn.execute('SELECT id, name, employee_number FROM employees').fetchall()
    
    # جلب تنبيهات انتهاء الصلاحية
    expiry_alerts = get_document_expiry_alerts()
    
    pass # conn.close() removed to prevent leak in Flask g
    return render_template('documents.html', documents=documents, employees=employees, expiry_alerts=expiry_alerts)

@document_bp.route('/documents/upload', methods=['POST'])
@login_required
@require_permission('document.manage')
def upload_document():
    if 'document' not in request.files:
        flash(gettext('x.f_no_file_chosen'), 'error')
        return redirect(url_for('document.documents'))
    
    file = request.files['document']
    if file.filename == '':
        flash(gettext('x.f_no_file_chosen'), 'error')
        return redirect(url_for('document.documents'))
    
    if file and allowed_file(file.filename):
        employee_id = request.form['employee_id']
        document_type = request.form['document_type']
        expiry_date = request.form.get('expiry_date')
        
        conn = get_db_connection()
        
        # جلب رقم الموظف
        employee = conn.execute('SELECT employee_number FROM employees WHERE id = ?', (employee_id,)).fetchone()
        if not employee:
            flash(gettext('x.f_employee_not_found'), 'error')
            return redirect(url_for('document.documents'))
        
        # إنشاء مجلد للموظف
        employee_folder = os.path.join(current_app.config['UPLOAD_FOLDER'], str(employee['employee_number']))
        if not os.path.exists(employee_folder):
            os.makedirs(employee_folder)
        
        # إنشاء اسم فريد للملف
        timestamp = int(time.time())
        original_filename = secure_filename(file.filename)
        file_extension = os.path.splitext(original_filename)[1]
        new_filename = f"{employee['employee_number']}_{timestamp}{file_extension}"
        
        # حفظ الملف في مجلد الموظف
        file_path = os.path.join(employee_folder, new_filename)
        file.save(file_path)
        
        # حفظ معلومات الوثيقة في قاعدة البيانات
        cursor = conn.execute('''
            INSERT INTO documents (employee_id, document_type, filename, original_filename, expiry_date)
            VALUES (?, ?, ?, ?, ?)
        ''', (employee_id, document_type, new_filename, original_filename, expiry_date))
        
        document_id = cursor.lastrowid
        
        # تسجيل في سجل الوثائق
        conn.execute('''
            INSERT INTO document_history 
            (document_id, action, new_filename, new_document_type, new_expiry_date, new_employee_id, change_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (document_id, 'created', new_filename, document_type, expiry_date, employee_id, 'إنشاء وثيقة جديدة'))
        
        conn.commit()
        pass # conn.close() removed to prevent leak in Flask g
        
        flash(gettext('x.f_document_uploaded'), 'success')
    else:
        flash(gettext('x.f_file_type_not_allowed'), 'error')
    
    return redirect(url_for('document.documents'))

@document_bp.route('/documents/download/<filename>')
@login_required
@require_permission('document.manage')
def download_document(filename):
    conn = get_db_connection()
    document = conn.execute('''
        SELECT d.*, e.employee_number 
        FROM documents d 
        JOIN employees e ON d.employee_id = e.id 
        WHERE d.filename = ?
    ''', (filename,)).fetchone()
    pass # conn.close() removed to prevent leak in Flask g
    
    if document:
        employee_folder = os.path.join(current_app.config['UPLOAD_FOLDER'], str(document['employee_number']))
        file_path = os.path.join(employee_folder, filename)
        
        if os.path.exists(file_path):
            return send_from_directory(employee_folder, filename)
        else:
            return send_from_directory(current_app.config['UPLOAD_FOLDER'], filename)
    
    return "الملف غير موجود", 404

@document_bp.route('/documents/edit/<int:document_id>', methods=['GET', 'POST'])
@login_required
@require_permission('document.manage')
def edit_document(document_id):
    conn = get_db_connection()
    
    if request.method == 'GET':
        document = conn.execute('''
            SELECT d.*, e.name as employee_name, e.employee_number
            FROM documents d
            JOIN employees e ON d.employee_id = e.id
            WHERE d.id = ?
        ''', (document_id,)).fetchone()
        
        if not document:
            flash(gettext('x.f_document_not_found'), 'error')
            return redirect(url_for('document.documents'))
        
        history = conn.execute('''
            SELECT dh.*, 
                   e1.name as old_employee_name, e1.employee_number as old_employee_number,
                   e2.name as new_employee_name, e2.employee_number as new_employee_number
            FROM document_history dh
            LEFT JOIN employees e1 ON dh.old_employee_id = e1.id
            LEFT JOIN employees e2 ON dh.new_employee_id = e2.id
            WHERE dh.document_id = ?
            ORDER BY dh.change_date DESC
        ''', (document_id,)).fetchall()
        
        employees = conn.execute('SELECT id, name, employee_number FROM employees').fetchall()
        pass # conn.close() removed to prevent leak in Flask g
        
        return render_template('edit_document.html', document=document, employees=employees, history=history)
    
    elif request.method == 'POST':
        document_type = request.form['document_type']
        expiry_date = request.form.get('expiry_date')
        employee_id = request.form['employee_id']
        change_reason = request.form.get('change_reason', '')
        
        current_doc = conn.execute('SELECT * FROM documents WHERE id = ?', (document_id,)).fetchone()
        
        new_file = request.files.get('new_document')
        file_changed = False
        
        if new_file and new_file.filename != '':
            if allowed_file(new_file.filename):
                file_changed = True
                
                old_filename = current_doc['filename']
                archive_folder = os.path.join(current_app.config['UPLOAD_FOLDER'], 'archive')
                if not os.path.exists(archive_folder):
                    os.makedirs(archive_folder)
                
                old_path = os.path.join(current_app.config['UPLOAD_FOLDER'], old_filename)
                if os.path.exists(old_path):
                    archive_filename = f"old_{int(time.time())}_{old_filename}"
                    archive_path = os.path.join(archive_folder, archive_filename)
                    shutil.move(old_path, archive_path)
                
                employee = conn.execute('SELECT employee_number FROM employees WHERE id = ?', (employee_id,)).fetchone()
                employee_folder = os.path.join(current_app.config['UPLOAD_FOLDER'], str(employee['employee_number']))
                if not os.path.exists(employee_folder):
                    os.makedirs(employee_folder)
                
                timestamp = int(time.time())
                original_filename = secure_filename(new_file.filename)
                file_extension = os.path.splitext(original_filename)[1]
                new_filename = f"{employee['employee_number']}_{timestamp}{file_extension}"
                
                file_path = os.path.join(employee_folder, new_filename)
                new_file.save(file_path)
                
                conn.execute('''
                    UPDATE documents 
                    SET document_type = ?, expiry_date = ?, employee_id = ?, filename = ?, original_filename = ?
                    WHERE id = ?
                ''', (document_type, expiry_date, employee_id, new_filename, original_filename, document_id))
                
                conn.execute('''
                    INSERT INTO document_history 
                    (document_id, action, old_filename, new_filename, old_document_type, new_document_type, 
                     old_expiry_date, new_expiry_date, old_employee_id, new_employee_id, change_reason)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (document_id, 'file_changed', old_filename, new_filename, 
                      current_doc['document_type'], document_type, current_doc['expiry_date'], 
                      expiry_date, current_doc['employee_id'], employee_id, change_reason))
            else:
                flash(gettext('x.f_file_type_not_allowed'), 'error')
                return redirect(url_for('document.edit_document', document_id=document_id))
        else:
            conn.execute('''
                UPDATE documents 
                SET document_type = ?, expiry_date = ?, employee_id = ?
                WHERE id = ?
            ''', (document_type, expiry_date, employee_id, document_id))
            
            conn.execute('''
                INSERT INTO document_history 
                (document_id, action, old_document_type, new_document_type, 
                 old_expiry_date, new_expiry_date, old_employee_id, new_employee_id, change_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (document_id, 'updated', current_doc['document_type'], document_type, 
                  current_doc['expiry_date'], expiry_date, current_doc['employee_id'], employee_id, change_reason))
        
        conn.commit()
        pass # conn.close() removed to prevent leak in Flask g
        
        if file_changed:
            flash(gettext('x.f_document_updated_uploaded'), 'success')
        else:
            flash(gettext('x.f_document_data_updated'), 'success')
        
        return redirect(url_for('document.documents'))

@document_bp.route('/documents/delete/<int:document_id>', methods=['POST'])
@login_required
@require_permission('document.manage')
def delete_document(document_id):
    conn = get_db_connection()
    
    document = conn.execute('''
        SELECT d.*, e.employee_number 
        FROM documents d 
        JOIN employees e ON d.employee_id = e.id 
        WHERE d.id = ?
    ''', (document_id,)).fetchone()
    
    if document:
        employee_folder = os.path.join(current_app.config['UPLOAD_FOLDER'], str(document['employee_number']))
        file_path = os.path.join(employee_folder, document['filename'])
        
        if os.path.exists(file_path):
            os.remove(file_path)
        else:
            old_file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], document['filename'])
            if os.path.exists(old_file_path):
                os.remove(old_file_path)
        
        conn.execute('''
            INSERT INTO document_history 
            (document_id, action, old_filename, old_document_type, old_expiry_date, old_employee_id, change_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (document_id, 'deleted', document['filename'], document['document_type'], 
              document['expiry_date'], document['employee_id'], 'حذف الوثيقة'))
        
        conn.execute('DELETE FROM documents WHERE id = ?', (document_id,))
        conn.commit()
        
        flash(gettext('x.f_document_deleted'), 'success')
    else:
        flash(gettext('x.f_document_not_found'), 'error')
    
    pass # conn.close() removed to prevent leak in Flask g
    return redirect(url_for('document.documents'))

@document_bp.route('/api/document-expiry-alerts')
@login_required
@require_permission('document.manage')
def api_document_expiry_alerts():
    """API لجلب تنبيهات انتهاء صلاحية الوثائق"""
    try:
        alerts = get_document_expiry_alerts()
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
