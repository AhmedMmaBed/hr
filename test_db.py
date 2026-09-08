import sqlite3
try:
    conn=sqlite3.connect('data/hr_system.db')
    conn.row_factory=sqlite3.Row
    res = conn.execute('SELECT id, command_type, status, payload FROM adms_commands ORDER BY id DESC LIMIT 10').fetchall()
    print([dict(r) for r in res])
except Exception as e:
    print(e)
