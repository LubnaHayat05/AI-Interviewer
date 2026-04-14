from flask import Flask, render_template, request, redirect, url_for, jsonify, send_from_directory, flash
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3
import os
import PyPDF2
import google.generativeai as genai
from dotenv import load_dotenv
from piper.voice import PiperVoice, SynthesisConfig
import wave
import time
import json

load_dotenv()
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "supersecret")
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['AUDIO_FOLDER'] = 'static/audio'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['AUDIO_FOLDER'], exist_ok=True)

gemini_model = "gemini-2.5-flash-lite"

# DB + Login
def init_db():
    with sqlite3.connect("users.db") as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )""")
init_db()

login_manager = LoginManager(app)
login_manager.login_view = 'login'

class User(UserMixin):
    def __init__(self, id, name, email):
        self.id = id
        self.name = name
        self.email = email

@login_manager.user_loader
def load_user(user_id):
    with sqlite3.connect("users.db") as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, name, email FROM users WHERE id = ?", (user_id,))
        row = cur.fetchone()
        if row: return User(*row)
    return None

# Piper TTS
voice = None
try:
    voice = PiperVoice.load("en_US-amy-medium.onnx")
except: print("Piper not loaded")

SESSIONS = {}

def pdf_text(f): return "".join(p.extract_text() or "" for p in PyPDF2.PdfReader(f).pages)
def speak(text, name):
    path = os.path.join(app.config['AUDIO_FOLDER'], name)
    if voice:
        try:
            with wave.open(path, "wb") as f:
                voice.synthesize_wav(text, f, SynthesisConfig())
            return f"/static/audio/{name}"
        except: pass
    return None

def gen_questions(text, role, company=""):
    role_map = {
        "SDE": "Software Engineer", "DevOps": "DevOps Engineer", "HR": "HR Manager",
        "Marketing": "Marketing Specialist", "Product": "Product Manager", "Data Science": "Data Scientist"
    }
    full_role = role_map.get(role, "Tech")
    
    prompt = f"""
You are an expert interviewer. Generate 5 real interview questions for a {full_role} role.
Use this resume: {text}

"""
    if company.strip():
        prompt += f"""
Also, search the internet and include 2-3 REAL interview questions frequently asked at {company}.
Sources: Glassdoor, LeetCode, Levels.fyi, AmbitionBox, TeamBlind.
Prioritize recent questions (2024-2025).
If no real questions found, use your knowledge of common {company} questions.
"""

    prompt += "Return exactly 5 questions, one per line. No numbering. No explanations."

    m = genai.GenerativeModel(gemini_model)
    r = m.generate_content(prompt)
    qs = [q.strip() for q in r.text.strip().split("\n") if q.strip() and not q.startswith("Note")]
    return qs[:5] or ["Tell me about yourself.", "Why do you want to work here?", "What is your biggest strength?", "Describe a challenge you faced.", "Where do you see yourself in 5 years?"]

# ROUTES
@app.route('/')
def home(): return redirect(url_for('login'))
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email, pwd = request.form['email'], request.form['password']
        with sqlite3.connect("users.db") as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, name, email, password FROM users WHERE email = ?", (email,))
            user = cur.fetchone()
            if user and check_password_hash(user[3], pwd):
                login_user(User(*user[:3]))
                return redirect(url_for('dashboard'))
        flash("Invalid credentials")
    return render_template('login.html')

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        pwd = generate_password_hash(request.form['password'])
        try:
            with sqlite3.connect("users.db") as conn:
                conn.execute("INSERT INTO users (name, email, password) VALUES (?, ?, ?)", (name, email, pwd))
            flash("Account created!")
            return redirect(url_for('login'))
        except: flash("Email exists")
    return render_template('signup.html')

@app.route('/logout')
@login_required
def logout(): logout_user(); return redirect(url_for('login'))

@app.route('/scheduled')
@app.route('/all')
@app.route('/billing')
@app.route('/settings')
@app.route('/dashboard')
@login_required
def dashboard():
    return render_template('dashboard.html', user=current_user)

@app.route('/register_interview_page')
@login_required
def register_interview_page():
    return render_template('register.html', user=current_user)

@app.route('/interview')
@login_required
def interview():
    if current_user.id not in SESSIONS:
        return redirect(url_for('dashboard'))
    return render_template('interview.html', user=current_user)

@app.route('/results')
@login_required
def results():
    s = SESSIONS.get(current_user.id, {})
    if "final_result" not in s:
        return redirect(url_for('interview'))
    return render_template('results.html', result=s["final_result"], user=current_user)

# API
@app.route('/register_interview', methods=['POST'])
@login_required
def reg_int():
    name = request.form['name']
    uid = request.form['uid']
    role = request.form['role']
    company = request.form.get('company', '').strip()
    pdf = request.files['resume']
    path = os.path.join(app.config['UPLOAD_FOLDER'], f"r_{current_user.id}.pdf")
    pdf.save(path)
    qs = gen_questions(pdf_text(path), role, company)
    ts = int(time.time())
    urls = [speak(q, f"q_{current_user.id}_{ts}_{i}.wav") for i, q in enumerate(qs)]
    SESSIONS[current_user.id] = {
        "user_info": {"name": name, "uid": uid, "role": role, "company": company or "General"},
        "questions": qs,
        "audio_urls": [u or "#" for u in urls],
        "current": 0,
        "answers": [None]*5
    }
    return jsonify({"success": True, "redirect": "/interview"})

@app.route('/api/question')
@login_required
def q():
    s = SESSIONS[current_user.id]
    i = s["current"]
    return jsonify({
        "question": s["questions"][i],
        "audio": s["audio_urls"][i],
        "index": i+1,
        "total": 5,
        "user_info": s["user_info"],
        "answers": s["answers"]
    })

@app.route('/api/submit', methods=['POST'])
@login_required
def submit():
    s = SESSIONS[current_user.id]
    audio = request.files['audio']
    path = os.path.join(app.config['AUDIO_FOLDER'], f"a_{current_user.id}_{s['current']}_{int(time.time())}.wav")
    audio.save(path)
    s["answers"][s["current"]] = path
    return jsonify({"ok": True})

@app.route('/api/next')
@login_required
def next(): 
    s = SESSIONS[current_user.id]
    if s["current"] < 4: s["current"] += 1
    return jsonify({"ok": True})

@app.route('/api/prev')
@login_required
def prev():
    s = SESSIONS[current_user.id]
    if s["current"] > 0: s["current"] -= 1
    return jsonify({"ok": True})

@app.route('/api/submit_all', methods=['POST'])
@login_required
def submit_all():
    s = SESSIONS[current_user.id]
    uploaded = []
    answered_questions = []

    # Build list of actual answered questions
    for i, path in enumerate(s["answers"]):
        if path and os.path.exists(path):
            try:
                file = genai.upload_file(path)
                uploaded.append({"index": i, "file": file})
                answered_questions.append(i+1)
            except:
                pass

    # CRITICAL: Tell Gemini EXACTLY which questions were answered
    prompt = f"""
You are a senior interviewer at {s['user_info']['company']}.
Role: {s['user_info']['role']}

Candidate answered ONLY these questions: {answered_questions or "NONE"}

Questions asked:
"""
    for i, q in enumerate(s["questions"]):
        status = "ANSWERED" if (i+1) in answered_questions else "SKIPPED"
        prompt += f"Q{i+1}: {q} [{status}]\n"

    prompt += """
RULES:
- ONLY transcribe and score questions that were actually answered.
- For SKIPPED questions: transcription = null, score = 0, comment = "No answer recorded."
- DO NOT hallucinate or make up answers.
- Return EXACTLY 5 feedback entries.
- Return ONLY valid JSON. No extra text.

FORMAT:
{
  "total_score": 28,
  "max_score": 50,
  "summary": "Partial attempt. Only 3/5 answered.",
  "strengths": ["Confident tone"],
  "improvements": ["Complete all questions", "Speak slower"],
  "feedback": [
    {"question": 1, "transcription": "Actual words...", "score": 9, "max": 10, "comment": "Great!"},
    {"question": 2, "transcription": null, "score": 0, "max": 10, "comment": "No answer recorded."},
    ...
  ]
}
"""

    try:
        # Only send real audio files
        audio_files = [item["file"] for item in uploaded]
        model = genai.GenerativeModel(gemini_model)
        resp = model.generate_content(
            [prompt] + audio_files,
            generation_config={
                "response_mime_type": "application/json",
                "temperature": 0.3  # Lower = less hallucination
            }
        )
        result = json.loads(resp.text)
        s["final_result"] = result

        # Cleanup
        for item in uploaded:
            try: genai.delete_file(item["file"].name)
            except: pass

        return jsonify({"success": True})

    except Exception as e:
        return jsonify({"error": f"Evaluation failed: {str(e)}"}), 500

@app.route('/static/audio/<path:name>')
def audio(name): return send_from_directory(app.config['AUDIO_FOLDER'], name)



if __name__ == '__main__':
    app.run(debug=False, port=5000)