import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))
from flask import Flask, render_template, request, jsonify, session
from supabase import create_client, Client
from dotenv import load_dotenv
import google.generativeai as genai
from config.config import GEMINI_API_KEY, SUPABASE_URL, SUPABASE_KEY, SUPPORTED_LANGUAGES
from utils.logger import log_info, log_error
from langdetect import detect
import bcrypt
import uuid
from collections import Counter

app = Flask(__name__)
app.secret_key = os.urandom(24)

# Configure Gemini API
genai.configure(api_key=GEMINI_API_KEY)

# Initialize Supabase client
load_dotenv()
supabase = create_client(os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_KEY'))

# Define conversation states
LOGIN, REGISTER, USERNAME, PHONE, RESIDENCE, INCIDENT, FOLLOWUP_1, FOLLOWUP_2, FOLLOWUP_3, CONFIRM, POST_SUBMISSION = range(11)

@app.route('/')
def index():
    """Initialize session and render the web interface."""
    session.clear()
    session['state'] = LOGIN
    log_info("Web session started")
    return render_template('index.html')

@app.route('/chat', methods=['POST'])
def chat():
    """Handle user input and manage conversation flow."""
    data = request.get_json()
    user_message = data.get('message', '').strip()
    current_state = session.get('state', LOGIN)
    user_id = session.get('user_id', None)

    if 'report' not in session:
        session['report'] = {'user_id': user_id or 'web_' + str(uuid.uuid4())[:8], 'language': 'en'}

    # Process user input based on current state
    if current_state == LOGIN:
        if user_message.startswith('register'):
            response = "Please enter a username and password in 'username|password' format:"
            session['state'] = REGISTER
        else:
            username, password = (user_message.split('|') + [''])[:2]
            if authenticate_user(username, password):
                session['user_id'] = get_user_id(username)
                response = "Login successful! Welcome to the Cybercrime Reporting Service. Please provide your name:"
                session['state'] = USERNAME
            else:
                response = "Invalid credentials. Type 'username|password' to login or 'register' to sign up:"
                
    elif current_state == REGISTER:
        if '|' not in user_message:
            response = "Please provide a password after a '|' (e.g., username|password):"
        else:
            username, password = user_message.split('|', 1)
            if register_user(username, password):
                session['user_id'] = get_user_id(username)
                response = "Registration successful! Welcome to the Cybercrime Reporting Service. Please provide your name:"
                session['state'] = USERNAME
            else:
                response = "Username taken. Please try another username|password:"

    elif current_state == USERNAME:
        session['report']['username'] = user_message
        # Automatic language detection from username input (initial)
        lang = detect(user_message)
        session['report']['language'] = lang if (lang in SUPPORTED_LANGUAGES and len(user_message) > 2) else "en"
        log_info(f"User {user_id} provided username: {user_message}, detected language: {session['report']['language']}")
        response = "Great! Now, please provide your phone number (you can change language with 'lang [code]', e.g., 'lang hi'):"
        session['state'] = PHONE

    elif current_state == PHONE:
        if user_message.startswith('lang '):
            lang_code = user_message.split(' ')[1].lower()
            if lang_code in SUPPORTED_LANGUAGES:
                session['report']['language'] = lang_code
                response = f"Language set to {lang_code}. Please provide your phone number:"
            else:
                response = f"Unsupported language '{lang_code}'. Supported: {', '.join(SUPPORTED_LANGUAGES)}. Please provide your phone number:"
        elif not user_message.isdigit() or len(user_message) < 7:
            response = "Please enter a valid phone number (digits only, at least 7 digits):"
        else:
            session['report']['phone'] = user_message
            log_info(f"User {user_id} provided phone: {user_message}")
            response = "Thanks! Where do you reside (city/state)?"
            session['state'] = RESIDENCE

    elif current_state == RESIDENCE:
        if user_message.startswith('lang '):
            lang_code = user_message.split(' ')[1].lower()
            if lang_code in SUPPORTED_LANGUAGES:
                session['report']['language'] = lang_code
                response = f"Language set to {lang_code}. Please provide your residence:"
            else:
                response = f"Unsupported language '{lang_code}'. Supported: {', '.join(SUPPORTED_LANGUAGES)}. Please provide your residence:"
        else:
            session['report']['residence'] = user_message
            log_info(f"User {user_id} provided residence: {user_message}")
            response = "Got it! Please describe the cybercrime incident:"
            session['state'] = INCIDENT

    elif current_state == INCIDENT:
        if user_message.startswith('lang '):
            lang_code = user_message.split(' ')[1].lower()
            if lang_code in SUPPORTED_LANGUAGES:
                session['report']['language'] = lang_code
                response = f"Language set to {lang_code}. Please describe the cybercrime incident:"
            else:
                response = f"Unsupported language '{lang_code}'. Supported: {', '.join(SUPPORTED_LANGUAGES)}. Please describe the incident:"
        else:
            session['report']['incident'] = user_message
            # Refine language detection from incident description if username was inconclusive
            if len(user_message) > 10:  # Ensure sufficient text for reliable detection
                lang = detect(user_message)
                if lang in SUPPORTED_LANGUAGES and session['report']['language'] == 'en':
                    session['report']['language'] = lang
                    log_info(f"Updated language to {lang} based on incident: {user_message}")
            log_info(f"User {user_id} provided incident: {user_message}, language: {session['report']['language']}")
            followup_question = generate_followup(user_message, "Ask about the timing or method of the incident.")
            session['report']['followup_question_1'] = followup_question
            response = followup_question
            session['state'] = FOLLOWUP_1

    elif current_state == FOLLOWUP_1:
        if user_message.startswith('lang '):
            lang_code = user_message.split(' ')[1].lower()
            if lang_code in SUPPORTED_LANGUAGES:
                session['report']['language'] = lang_code
                response = f"Language set to {lang_code}. {session['report']['followup_question_1']}"
            else:
                response = f"Unsupported language '{lang_code}'. Supported: {', '.join(SUPPORTED_LANGUAGES)}. {session['report']['followup_question_1']}"
        else:
            session['report']['incident'] += f"\nDetails 1: {user_message}"
            log_info(f"User {user_id} provided followup_1: {user_message}")
            followup_question = generate_followup(session['report']['incident'], "Ask about the impact or evidence of the incident.")
            session['report']['followup_question_2'] = followup_question
            response = followup_question
            session['state'] = FOLLOWUP_2

    elif current_state == FOLLOWUP_2:
        if user_message.startswith('lang '):
            lang_code = user_message.split(' ')[1].lower()
            if lang_code in SUPPORTED_LANGUAGES:
                session['report']['language'] = lang_code
                response = f"Language set to {lang_code}. {session['report']['followup_question_2']}"
            else:
                response = f"Unsupported language '{lang_code}'. Supported: {', '.join(SUPPORTED_LANGUAGES)}. {session['report']['followup_question_2']}"
        else:
            session['report']['incident'] += f"\nDetails 2: {user_message}"
            log_info(f"User {user_id} provided followup_2: {user_message}")
            followup_question = generate_followup(session['report']['incident'], "Ask about any suspects or additional context of the incident.")
            session['report']['followup_question_3'] = followup_question
            response = followup_question
            session['state'] = FOLLOWUP_3

    elif current_state == FOLLOWUP_3:
        if user_message.startswith('lang '):
            lang_code = user_message.split(' ')[1].lower()
            if lang_code in SUPPORTED_LANGUAGES:
                session['report']['language'] = lang_code
                response = f"Language set to {lang_code}. {session['report']['followup_question_3']}"
            else:
                response = f"Unsupported language '{lang_code}'. Supported: {', '.join(SUPPORTED_LANGUAGES)}. {session['report']['followup_question_3']}"
        else:
            session['report']['incident'] += f"\nDetails 3: {user_message}"
            log_info(f"User {user_id} provided followup_3: {user_message}")
            crime_type = classify_incident(session['report']['incident'])
            session['report']['crime_type'] = crime_type
            summary = (
                f"Please confirm your report:\n"
                f"Name: {session['report']['username']}\n"
                f"Phone: {session['report']['phone']}\n"
                f"Residence: {session['report']['residence']}\n"
                f"Incident: {session['report']['incident']}\n"
                f"Type: {crime_type}\n\n"
                "Type 'yes' to submit, 'no' to cancel, 'track' to view past reports, 'safety' for precautions, 'trends' for awareness, 'lang [code]' to change language, or 'signout' to log out:"
            )
            response = summary
            session['state'] = CONFIRM

    elif current_state == CONFIRM:
        confirmation = user_message.lower().strip()
        if confirmation == "yes":
            report = session['report']
            response_text = generate_response(report['crime_type'], report['language'])
            if save_report(report):
                response = (
                    f"Report submitted successfully!\n\n"
                    f"Summary:\n"
                    f"Name: {report['username']}\n"
                    f"Phone: {report['phone']}\n"
                    f"Residence: {report['residence']}\n"
                    f"Incident: {report['incident']}\n"
                    f"Type: {report['crime_type']}\n\n"
                    f"Response: {response_text}\n\n"
                    "Type 'track' for past reports, 'safety' for precautions, 'trends' for awareness, 'new' to start a new report, 'lang [code]' to change language, or 'signout' to log out:"
                )
                session['report'] = {'user_id': user_id, 'language': session['report']['language']}
                session['state'] = POST_SUBMISSION
            else:
                response = (
                    "Error saving report. Please try again.\n"
                    "Type 'track' for past reports, 'safety' for precautions, 'trends' for awareness, 'new' to start a new report, 'lang [code]' to change language, or 'signout' to log out:"
                )
                session['state'] = POST_SUBMISSION
        elif confirmation == "no":
            response = (
                "Report canceled.\n"
                "Type 'track' for past reports, 'safety' for precautions, 'trends' for awareness, 'new' to start a new report, 'lang [code]' to change language, or 'signout' to log out:"
            )
            session['report'] = {'user_id': user_id, 'language': session['report']['language']}
            session['state'] = POST_SUBMISSION
        elif confirmation == "track":
            response = track_reports(user_id)
        elif confirmation == "safety":
            response = safety_measures()
        elif confirmation == "trends":
            response = get_trends()
        elif confirmation == "signout":
            session.clear()
            response = "You have been signed out. Please login (username|password) or type 'register' to sign up:"
            session['state'] = LOGIN
        elif confirmation.startswith('lang '):
            lang_code = confirmation.split(' ')[1].lower()
            if lang_code in SUPPORTED_LANGUAGES:
                session['report']['language'] = lang_code
                response = f"Language set to {lang_code}. Type 'yes', 'no', 'track', 'safety', 'trends', 'lang [code]', or 'signout':"
            else:
                response = f"Unsupported language '{lang_code}'. Supported: {', '.join(SUPPORTED_LANGUAGES)}. Type 'yes', 'no', 'track', 'safety', 'trends', 'lang [code]', or 'signout':"
        elif confirmation == "lang":  # Handle standalone 'lang' from button
            response = "Please specify a language code (e.g., 'lang hi' for Hindi):"
        else:
            response = "Please type 'yes' to submit, 'no' to cancel, 'track' to view past reports, 'safety' for precautions, 'trends' for awareness, 'lang [code]' to change language, or 'signout' to log out:"

    elif current_state == POST_SUBMISSION:
        command = user_message.lower().strip()
        if command == "track":
            response = track_reports(user_id)
        elif command == "safety":
            response = safety_measures()
        elif command == "trends":
            response = get_trends()
        elif command == "new":
            response = "Starting a new report. Please provide your name:"
            session['state'] = USERNAME
            session['report'] = {'user_id': user_id, 'language': session['report']['language']}
        elif command == "signout":
            session.clear()
            response = "You have been signed out. Please login (username|password) or type 'register' to sign up:"
            session['state'] = LOGIN
        elif command.startswith('lang '):
            lang_code = command.split(' ')[1].lower()
            if lang_code in SUPPORTED_LANGUAGES:
                session['report']['language'] = lang_code
                response = f"Language set to {lang_code}. Type 'track', 'safety', 'trends', 'new', 'lang [code]', or 'signout':"
            else:
                response = f"Unsupported language '{lang_code}'. Supported: {', '.join(SUPPORTED_LANGUAGES)}. Type 'track', 'safety', 'trends', 'new', 'lang [code]', or 'signout':"
        elif command == "lang":  # Handle standalone 'lang' from button
            response = "Please specify a language code (e.g., 'lang hi' for Hindi):"
        else:
            response = "Type 'track' for past reports, 'safety' for precautions, 'trends' for awareness, 'new' to start a new report, 'lang [code]' to change language, or 'signout' to log out:"

    return jsonify({
        'response': response,
        'state': session['state']
    })

# Authentication and Registration
def authenticate_user(username, password):
    try:
        user = supabase.table('users').select('username', 'password_hash').eq('username', username).execute()
        if user.data and len(user.data) > 0:
            stored_hash = user.data[0]['password_hash'].encode('utf-8')
            return bcrypt.checkpw(password.encode('utf-8'), stored_hash)
        return False
    except Exception as e:
        log_error(f"Authentication error: {str(e)}")
        return False

def register_user(username, password):
    try:
        password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        supabase.table('users').insert({'username': username, 'password_hash': password_hash}).execute()
        return True
    except Exception as e:
        log_error(f"Registration error: {str(e)}")
        return False

def get_user_id(username):
    try:
        user = supabase.table('users').select('user_id').eq('username', username).execute()
        return user.data[0]['user_id'] if user.data else None
    except Exception as e:
        log_error(f"Get user ID error: {str(e)}")
        return None

# Helper Functions
def generate_followup(incident: str, instruction: str) -> str:
    try:
        model = genai.GenerativeModel("gemini-1.5-pro")
        prompt = f"Incident: '{incident}'. {instruction} Return a concise question."
        response = model.generate_content(prompt)
        log_info(f"Generated follow-up question: {response.text.strip()}")
        return response.text.strip()
    except Exception as e:
        log_error(f"Gemini API failed for follow-up: {str(e)}")
        return "Can you provide more details about what happened?"

def classify_incident(incident: str) -> str:
    try:
        model = genai.GenerativeModel("gemini-1.5-pro")
        prompt = ("You are a seasoned cybercrime classification assistant. "
    "Your job is to read the incident description below and assign it to *exactly one* of the categories in the taxonomy. "
    "Choose the one that most precisely captures the attacker’s technique or victim impact. "
    "If several fit, pick the most specific. "
    "Do *not* output any explanations or extra text—just the category name exactly as listed.\n\n"
    "📋 *Categories:*\n"
    "- Phishing\n"
    "- Hacking\n"
    "- Fraud\n"
    "- Cyberbullying\n"
    "- Malware\n"
    "- Ransomware\n"
    "- Identity Theft\n"
    "- Online Harassment\n"
    "- Data Breach\n"
    "- Social Engineering\n"
    "- Other\n\n"
    f"🔍 *Incident Description:*\n\"{incident}\"\n\n"
    "⏺️ *Output:*\n"
    "Category:"
        )
        response = model.generate_content(prompt)
        crime_type = response.text.strip().lower()
        log_info(f"Classified incident as: {crime_type}")
        return crime_type if crime_type else "unknown"
    except Exception as e:
        log_error(f"Classification failed: {str(e)}")
        return "unknown"

def generate_response(crime_type: str, language: str) -> str:
    RESPONSE_TEMPLATES = {
        "phishing": {
            "en": "This seems like phishing. Report it to cybercrime.gov.in. Legal: IT Act Section 66C.",
            "hi": "यह फ़िशिंग जैसा है। cybercrime.gov.in पर रिपोर्ट करें। कानूनी: आईटी अधिनियम धारा 66C।",
            "te": "ఇది ఫిషింగ్ లాగా ఉంది. cybercrime.gov.inకు రిపోర్ట్ చేయండి। చట్టం: ఐటీ చట్టం సెక్షన్ 66C."
        },
        "hacking": {
            "en": "This may be hacking. Report to cybercrime.gov.in. Legal: IT Act Section 66.",
            "hi": "यह हैकिंग हो सकती है। cybercrime.gov.in पर रिपोर्ट करें। कानूनी: आईटी अधिनियम धारा 66।",
            "te": "ఇది హ్యాకింగ్ కావచ్చు. cybercrime.gov.inకు రిపోర్ట్ చేయండి। చట్టం: ఐటీ చట్టం సెక్షన్ 66."
        },
        "fraud": {
            "en": "This looks like fraud. Report to cybercrime.gov.in or your bank. Legal: IT Act Section 66D.",
            "hi": "यह धोखाधड़ी जैसा है। cybercrime.gov.in या बैंक को रिपोर्ट करें। कानूनी: आईटी अधिनियम धारा 66D।",
            "te": "ఇది మోసం లాగా ఉంది. cybercrime.gov.in లేదా మీ బ్యాంకుకు రిపోర్ట్ చేయండి। చట్టం: ఐటీ చట్టం సెక్షన్ 66D."
        },
        "cyberbullying": {
            "en": "This is cyberbullying. Report to the platform and cybercrime.gov.in. Legal: IT Act Section 67.",
            "hi": "यह साइबरबुलिंग है। प्लेटफॉर्म और cybercrime.gov.in पर रिपोर्ट करें। कानूनी: आईटी अधिनियम धारा 67।",
            "te": "ఇది సైబర్‌బుల్లింగ్. ప్లాట్‌ఫారమ్ మరియు cybercrime.gov.inకు రిపోర్ట్ చేయండి। చట్టం: ఐటీ చట్టం సెక్షన్ 66."
        },
        "malware": {
            "en": "This may involve malware. Report to cybercrime.gov.in. Legal: IT Act Section 66.",
            "hi": "यह मैलवेयर से संबंधित हो सकता है। cybercrime.gov.in पर रिपोर्ट करें। कानूनी: आईटी अधिनियम धारा 66।",
            "te": "ఇది మాల్వేర్‌తో సంబంధం కలిగి ఉండవచ్చు. cybercrime.gov.inకు రిపోర్ట్ చేయండి। చట్టం: ఐటీ చట్టం సెక్షన్ 66."
        },
        "identity theft": {
            "en": "This looks like identity theft. Report to cybercrime.gov.in and your bank. Legal: IT Act Section 66C.",
            "hi": "यह पहचान चोरी जैसा है। cybercrime.gov.in और अपने बैंक को रिपोर्ट करें। कानूनी: आईटी अधिनियम धारा 66C।",
            "te": "ఇది గుర్తింపు దొంగతనం లాగా ఉంది. cybercrime.gov.in మరియు మీ బ్యాంకుకు రిపోర్ట్ చేయండి। చట్టం: ఐటీ చట్టం సెక్షన్ 66C."
        },
        "ransomware": {
            "en": "This may be ransomware. Report to cybercrime.gov.in immediately. Legal: IT Act Section 66.",
            "hi": "यह रैंसमवेयर हो सकता है। तुरंत cybercrime.gov.in पर रिपोर्ट करें। कानूनी: आईटी अधिनियम धारा 66।",
            "te": "ఇది రాన్సమ్‌వేర్ కావచ్చు. వెంటనే cybercrime.gov.inకు రిపోర్ట్ చేయండి। చట్టం: ఐటీ చట్టం సెక్షన్ 66."
        },
        "unknown": {
            "en": "Couldn’t classify this. Report to cybercrime.gov.in with more details.",
            "hi": "इसे वर्गीकृत नहीं कर सका। cybercrime.gov.in पर अधिक विवरण के साथ रिपोर्ट करें।",
            "te": "దీనిని వర్గీకరించలేకపోయాను. cybercrime.gov.inకు మరిన్ని వివరాలతో రిపోర్ట్ చేయండి."
        }
    }
    return RESPONSE_TEMPLATES.get(crime_type, RESPONSE_TEMPLATES["unknown"]).get(language, RESPONSE_TEMPLATES["unknown"]["en"])

def save_report(report: dict) -> bool:
    try:
        data = {
            "user_id": report['user_id'],
            "language": report['language'],
            "crime_type": report['crime_type'],
            "username": report['username'],
            "phone": report['phone'],
            "residence": report['residence'],
            "user_input": report['incident'],
            "created_at": "now()"
        }
        log_info(f"Attempting to save report: {data}")
        response = supabase.table("reports").insert(data).execute()
        log_info(f"Report saved successfully: {response.data}")
        return True
    except Exception as e:
        log_error(f"Failed to save report: {str(e)}")
        return False

def track_reports(user_id: str) -> str:
    try:
        reports = supabase.table('reports').select('username', 'phone', 'residence', 'user_input', 'crime_type', 'created_at').eq('user_id', user_id).execute()
        if not reports.data:
            return "No previous reports found."
        summary = "Your Previous Reports:\n"
        for report in reports.data[:5]:  # Limit to last 5 reports
            summary += (
                f"- Submitted on {report['created_at']}:\n"
                f"  Name: {report['username']}\n"
                f"  Phone: {report['phone']}\n"
                f"  Residence: {report['residence']}\n"
                f"  Incident: {report['user_input']}\n"
                f"  Type: {report['crime_type']}\n\n"
            )
        return summary
    except Exception as e:
        log_error(f"Track reports error: {str(e)}")
        return "Error retrieving reports. Please try again later."

def safety_measures() -> str:
    return (
        "Cybercrime Safety Measures:\n"
        "- Use strong, unique passwords for all accounts.\n"
        "- Enable two-factor authentication (2FA) wherever possible.\n"
        "- Avoid clicking suspicious links or downloading unknown attachments.\n"
        "- Regularly update your software to patch security vulnerabilities.\n"
        "- Be cautious about sharing personal information online."
    )

def get_trends() -> str:
    try:
        reports = supabase.table('reports').select('crime_type').order('created_at', desc=True).limit(100).execute()
        if not reports.data:
            return "No recent cybercrime trends available."
        crime_counts = Counter(report['crime_type'] for report in reports.data)
        total = sum(crime_counts.values())
        trends = "Recent Cybercrime Trends (Last 100 Reports):\n"
        for crime_type, count in crime_counts.most_common(3):
            percentage = (count / total) * 100
            trends += f"- {crime_type}: {count} reports ({percentage:.1f}%)\n"
        return trends
    except Exception as e:
        log_error(f"Get trends error: {str(e)}")
        return "Error retrieving trends. Please try again later."

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
