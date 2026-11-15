"""
Simple logbook for recording daily lab notes
Install: pip install flask flask-cors
Run:     python logbook.py
"""

from flask import Flask, request, jsonify, render_template, send_from_directory, redirect
from werkzeug.utils import secure_filename
from markupsafe import escape
from flask_cors import CORS
import datetime as dt
from dateutil.parser import parse
import sqlite3
import markdown
import os


app = Flask(__name__, static_folder='static', template_folder="pub")
CORS(app)  # Allow requests from the HTML file opened locally

DB_PATH = "logbook.db"

UPLOAD_FOLDER = 'static/upload'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

SECTIONS = {
    'borexino': 'Borexino',
    'darkside': 'Darkside',
    'exo': 'EXO Analysis',
    'lxe': 'LXe System',
    'kumar': 'Kumar Lab',
    'hfe': 'HFE',
    'misc': 'Misc',
}

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row # Support row access by column name
    return conn

def init_db():
    with get_db() as conn:
        for sec in SECTIONS:
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS log_{sec} (
                    num       INTEGER PRIMARY KEY AUTOINCREMENT,
                    authors   TEXT       NOT NULL,
                    date      INT        DEFAULT (unixepoch('now')),
                    inked     INT        DEFAULT (unixepoch('now')),
                    note      TEXT       NOT NULL,
                    files     TEXT       DEFAULT NULL,
                    crossout  tinyint(1) NOT NULL DEFAULT '0',
                )
            """)

IMG_EXTS = {'png', 'apng', 'avif', 'jpg', 'jpeg', 'jfif',
            'pjpeg', 'pjp', 'jxl', 'gif', 'svg', 'webp'}

def is_file_img(filename):
    ext = filename.split('.')[-1].lower()
    return ext in IMG_EXTS
app.jinja_env.globals.update(is_file_img=is_file_img)

@app.template_filter('ts')
def fmt_ts(ts, fmt="%Y-%m-%d"):
    return dt.datetime.fromtimestamp(ts).strftime(fmt)

# Static files
@app.route('/<path:path>')
def serve_static(path):
    return send_from_directory('static', path)

# TODO markdown to html files in wiki/

def parse_date(date):
    try:
        return parse(date.strip())
    except (ValueError, AttributeError):
        return None

def date_title(before, after, fmt="%Y-%m-%d"):
    if not before and not after:
        return ""
    if not before:
        return f"since {after:{fmt}} "
    if not after:
        return f"until {before:{fmt}} "
    today = dt.datetime.now(before.tzinfo).date()
    if before.date() == today and after.date() == today:
        return "from Today "
    if before.date() == after.date():
        return f"from {before:{fmt}} "
    return f"from {after:{fmt}} to {before:{fmt}} "

# Home page
@app.route('/<string:sec>/log')
def logs(sec):
    if sec not in SECTIONS:
        abort(404)
    num = request.args.get('num', '').strip() # TODO strip leading #
    author = request.args.get('author', '').strip()
    before = parse_date(request.args.get('before', ''))
    after = parse_date(request.args.get('after', ''))
    text = request.args.get('q', '').strip()

    today = dt.datetime.now()
    if not request.args:
        before = after = today

    # if before and after and after > before:   # correct for reversed range
    #     before, after = after, before

    # TODO pull out to func
    title = "Logs "
    query = f"SELECT * FROM log_{sec} WHERE "
    params = []
    if num:
        # TODO change to anchor on date
        query += "num = ? AND " # TODO search range if hypen
        title += f"#{num} "
        params.append(num)
    if author:
        query += "authors = ? AND " # TODO any author in authors list
        title += f"by {author} "
        params.append(author)
    if before:
        query += "date < ? AND "
        end = before.replace(hour=0, minute=0, second=0, microsecond=0) + dt.timedelta(1)
        params.append(int(end.timestamp()))
    if after:
        query += "date >= ? AND "
        start = after.replace(hour=0, minute=0, second=0, microsecond=0)
        params.append(int(start.timestamp()))
    if text:
        query += "note LIKE ? AND " # TODO replace with MATCH
        title += f"containing '{text}' "
        params.append(f"%{text}%")
    query += " 1=1 ORDER BY num ASC" # TODO ability to order by date
    # query += " 1=1 ORDER BY date ASC, num ASC"

    title += date_title(before, after)

    rows = get_db().execute(query, params).fetchall()
    prev_date = f"{(after or today) - dt.timedelta(1):%Y-%m-%d}"
    next_date = f"{(before or today) + dt.timedelta(1):%Y-%m-%d}"
    return render_template('index.html', logs=rows, title=title, # SECTIONS[sec] + title
                           sec=sec, sections=SECTIONS,
                           curr_date=f"{today:%Y-%m-%d}", prev_date=prev_date, next_date=next_date)

def upload_files(sec, files):
    # TODO dont override file of same name
    # TODO put in date folder
    # TODO compress image / block files over 2MB
    # TODO error if file name contains :
    files_str = []
    for f in files:
        file_name = secure_filename(f.filename)
        if not file_name:
            continue
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], sec, file_name) # TODO date,
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        f.save(file_path)
        files_str.append(file_name)
    return ":".join(files_str)

# Upload new logbook post
@app.post('/<string:sec>/log')
def log(sec):
    if sec not in SECTIONS:
        abort(404)
    name = request.form['name'].strip() # TODO split into multiple authors
    msg = request.form['message'].strip()
    # TODO only set to now if datetime empty, otherwise error on parse exception
    # TODO if no time given replace with current time, not midnight
    date = parse_date(request.form['datetime']) or dt.datetime.now()

    files = upload_files(sec, request.files.getlist("files"))

    with get_db() as conn:
        cur = conn.execute(
            f"INSERT INTO log_{sec} (date, authors, files, note) VALUES (?, ?, ?, ?)",
            (int(date.timestamp()), name, files, msg),
        )
        date_str = f"{date:%Y-%m-%d}"
        # TODO on failure to upload file etc send warning pop up
        return redirect(f"/{sec}/log?before={date_str}&after={date_str}#{cur.lastrowid}")

@app.post('/<string:sec>/crossout/<int:num>')
def crossout(sec, num):
    "Mark the given log post as crossed out"
    if sec not in SECTIONS:
        abort(404)
    with get_db() as conn:
        conn.execute(f"UPDATE log_{sec} SET crossout = 1 WHERE num = ?", (num,))
    target = request.referrer or f'/?num={num}'
    return redirect(f"{target}#{num}")


if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5000)
