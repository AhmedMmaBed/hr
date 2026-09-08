from flask import Flask
from routes.adms_routes import adms_bp
from utils.db import init_db
import os
import sys

app = Flask(__name__)
app.secret_key = os.environ.get('ADMS_SECRET_KEY', os.urandom(24))
app.register_blueprint(adms_bp, url_prefix='/iclock')

if __name__ == '__main__':
    # Initialize DB (Validation)
    init_db()
    
    # Get Port from Env or Default
    port = int(os.environ.get('ADMS_PORT', 8081))
    host = '0.0.0.0'
    
    print(f"🚀 Starting ADMS Server on {host}:{port} using Waitress...")
    try:
        from waitress import serve
        serve(app, host=host, port=port, threads=8)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
