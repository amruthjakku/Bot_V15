import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, ConversationHandler
)
from telegram import Update
from config.config import TELEGRAM_TOKEN, GEMINI_API_KEY, SUPABASE_URL, SUPABASE_KEY, SUPPORTED_LANGUAGES
from utils.logger import log_info, log_error
import google.generativeai as genai
from supabase import create_client, Client
from langdetect import detect
import bcrypt
from collections import Counter

# Configure Gemini API
genai.configure(api_key=GEMINI_API_KEY)

# Initialize Supabase client
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# Define conversation states
LOGIN, REGISTER, USERNAME, PHONE, RESIDENCE, INCIDENT, FOLLOWUP_1, FOLLOWUP_2, FOLLOWUP_3, CONFIRM, POST_SUBMISSION = range(11)

### Command Handlers
async def start(update: Update, context):
    user_id = str(update.message.from_user.id)
    log_info(f"User {user_id} started the bot")
    await update.message.reply_text(
        "Welcome to the Cybercrime Reporting Bot!\n"
        "Please login with 'username|password' or type 'register' to sign up:"
    )
    return LOGIN

### State Handlers
async def login(update: Update, context):
    user_message = update.message.text.strip()
    user_id = str(update.message.from_user.id)
    if user_message.lower() == 'register':
        await update.message.reply_text("Please enter a username and password in 'username|password' format:")
        return REGISTER
    try:
        username, password = user_message.split('|', 1)
        if authenticate_user(username, password):
            context.user_data['user_id'] = get_user_id(username)
            await update.message.reply_text("Login successful! Please provide your name:")
            context.user_data['report'] = {'user_id': context.user_data['user_id']}
            return USERNAME
        else:
            await update.message.reply_text("Invalid credentials. Please try 'username|password' or 'register':")
            return LOGIN
    except ValueError:
        await update.message.reply_text("Please use 'username|password' format or type 'register':")
        return LOGIN

async def register(update: Update, context):
    user_message = update.message.text.strip()
    user_id = str(update.message.from_user.id)
    if '|' not in user_message:
        await update.message.reply_text("Please provide username and password in 'username|password' format:")
        return REGISTER
    username, password = user_message.split('|', 1)
    if register_user(username, password, telegram_id=user_id):
        context.user_data['user_id'] = get_user_id(username)
        await update.message.reply_text("Registration successful! Please provide your name:")
        context.user_data['report'] = {'user_id': context.user_data['user_id']}
        return USERNAME
    else:
        await update.message.reply_text("Username taken. Please try another username|password:")
        return REGISTER

async def get_username(update: Update, context):
    user_id = context.user_data['user_id']
    username = update.message.text.strip()
    context.user_data['report']['username'] = username
    # Automatically detect language from username input
    lang = detect(username)
    context.user_data['report']['language'] = lang if lang in SUPPORTED_LANGUAGES else "en"
    log_info(f"User {user_id} provided username: {username}, detected language: {context.user_data['report']['language']}")
    await update.message.reply_text("Great! Now, please provide your phone number:")
    return PHONE

async def get_phone(update: Update, context):
    user_id = context.user_data['user_id']
    phone = update.message.text.strip()
    if not phone.isdigit() or len(phone) < 7:
        await update.message.reply_text("Please enter a valid phone number (digits only, at least 7 digits):")
        return PHONE
    context.user_data['report']['phone'] = phone
    log_info(f"User {user_id} provided phone: {phone}")
    await update.message.reply_text("Thanks! Where do you reside (city/state)?")
    return RESIDENCE

async def get_residence(update: Update, context):
    user_id = context.user_data['user_id']
    residence = update.message.text.strip()
    context.user_data['report']['residence'] = residence
    log_info(f"User {user_id} provided residence: {residence}")
    await update.message.reply_text("Got it! Please describe the cybercrime incident:")
    return INCIDENT

async def get_incident(update: Update, context):
    user_id = context.user_data['user_id']
    incident = update.message.text.strip()
    context.user_data['report']['incident'] = incident
    log_info(f"User {user_id} provided incident: {incident}")

    # AI follow-up question 1
    followup_question = await generate_followup(incident, "Ask about the timing or method of the incident.")
    context.user_data['followup_question_1'] = followup_question
    await update.message.reply_text(followup_question)
    return FOLLOWUP_1

async def get_followup_1(update: Update, context):
    user_id = context.user_data['user_id']
    followup_1 = update.message.text.strip()
    context.user_data['report']['incident'] += f"\nDetails 1: {followup_1}"
    log_info(f"User {user_id} provided followup_1: {followup_1}")

    # AI follow-up question 2
    followup_question = await generate_followup(context.user_data['report']['incident'], 
                                               "Ask about the impact or evidence of the incident.")
    context.user_data['followup_question_2'] = followup_question
    await update.message.reply_text(followup_question)
    return FOLLOWUP_2

async def get_followup_2(update: Update, context):
    user_id = context.user_data['user_id']
    followup_2 = update.message.text.strip()
    context.user_data['report']['incident'] += f"\nDetails 2: {followup_2}"
    log_info(f"User {user_id} provided followup_2: {followup_2}")

    # AI follow-up question 3
    followup_question = await generate_followup(context.user_data['report']['incident'], 
                                               "Ask about any suspects or additional context of the incident.")
    context.user_data['followup_question_3'] = followup_question
    await update.message.reply_text(followup_question)
    return FOLLOWUP_3

async def get_followup_3(update: Update, context):
    user_id = context.user_data['user_id']
    followup_3 = update.message.text.strip()
    context.user_data['report']['incident'] += f"\nDetails 3: {followup_3}"
    log_info(f"User {user_id} provided followup_3: {followup_3}")

    # Classify the incident
    crime_type = classify_incident(context.user_data['report']['incident'])
    context.user_data['report']['crime_type'] = crime_type

    # Show summary for confirmation
    report = context.user_data['report']
    summary = (
        f"Please confirm your report:\n"
        f"Name: {report['username']}\n"
        f"Phone: {report['phone']}\n"
        f"Residence: {report['residence']}\n"
        f"Incident: {report['incident']}\n"
        f"Type: {crime_type}\n\n"
        "Reply 'yes' to submit, 'no' to cancel, 'track' for past reports, 'safety' for precautions, or 'trends' for awareness."
    )
    await update.message.reply_text(summary)
    return CONFIRM

async def confirm_report(update: Update, context):
    user_id = context.user_data['user_id']
    confirmation = update.message.text.lower().strip()
    
    if confirmation == "yes":
        report = context.user_data['report']
        response = generate_response(report['crime_type'], report['language'])
        success = await save_report(report)
        if success:
            await update.message.reply_text(
                f"Report submitted successfully!\n\n"
                f"Summary:\n"
                f"Name: {report['username']}\n"
                f"Phone: {report['phone']}\n"
                f"Residence: {report['residence']}\n"
                f"Incident: {report['incident']}\n"
                f"Type: {report['crime_type']}\n\n"
                f"Response: {response}\n\n"
                "You can type 'track' for past reports, 'safety' for precautions, 'trends' for awareness, or 'new' to start a new report."
            )
            context.user_data['report'] = {'user_id': user_id}  # Reset report but keep user_id
            return POST_SUBMISSION
        else:
            await update.message.reply_text(
                "Error saving report. Please try again.\n"
                "You can type 'track', 'safety', 'trends', or 'new'."
            )
            return POST_SUBMISSION
    elif confirmation == "no":
        await update.message.reply_text(
            "Report canceled.\n"
            "You can type 'track' for past reports, 'safety' for precautions, 'trends' for awareness, or 'new' to start a new report."
        )
        context.user_data['report'] = {'user_id': user_id}  # Reset report but keep user_id
        return POST_SUBMISSION
    elif confirmation == "track":
        await update.message.reply_text(track_reports(user_id))
        return CONFIRM
    elif confirmation == "safety":
        await update.message.reply_text(safety_measures())
        return CONFIRM
    elif confirmation == "trends":
        await update.message.reply_text(get_trends())
        return CONFIRM
    else:
        await update.message.reply_text(
            "Please reply 'yes' to submit, 'no' to cancel, 'track' for past reports, 'safety' for precautions, or 'trends' for awareness."
        )
        return CONFIRM

async def post_submission(update: Update, context):
    user_id = context.user_data['user_id']
    command = update.message.text.lower().strip()

    if command == "track":
        await update.message.reply_text(track_reports(user_id))
    elif command == "safety":
        await update.message.reply_text(safety_measures())
    elif command == "trends":
        await update.message.reply_text(get_trends())
    elif command == "new":
        await update.message.reply_text("Starting a new report. Please provide your name:")
        context.user_data['report'] = {'user_id': user_id, 'language': context.user_data['report'].get('language', 'en')}
        return USERNAME
    else:
        await update.message.reply_text(
            "You can type 'track' for past reports, 'safety' for precautions, 'trends' for awareness, or 'new' to start a new report."
        )
    return POST_SUBMISSION

async def cancel(update: Update, context):
    user_id = str(update.message.from_user.id)
    log_info(f"User {user_id} canceled the report")
    await update.message.reply_text(
        "Report canceled.\n"
        "You can type 'track' for past reports, 'safety' for precautions, 'trends' for awareness, or 'new' to start a new report."
    )
    context.user_data['report'] = {'user_id': context.user_data.get('user_id', user_id)}  # Reset but keep user_id
    return POST_SUBMISSION

### Helper Functions
async def generate_followup(incident: str, instruction: str) -> str:
    try:
        model = genai.GenerativeModel("gemini-1.5-pro")
        prompt = f"Incident: '{incident}'. {instruction} Return a concise question."
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        log_error(f"Gemini API failed: {str(e)}")
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
        return crime_type
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

async def save_report(report: dict) -> bool:
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

# Authentication and Registration Helpers
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

def register_user(username, password, telegram_id=None):
    try:
        password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        supabase.table('users').insert({'username': username, 'password_hash': password_hash, 'telegram_id': telegram_id}).execute()
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

# Additional Features
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

### Main Function
def main():
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            LOGIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, login)],
            REGISTER: [MessageHandler(filters.TEXT & ~filters.COMMAND, register)],
            USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_username)],
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone)],
            RESIDENCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_residence)],
            INCIDENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_incident)],
            FOLLOWUP_1: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_followup_1)],
            FOLLOWUP_2: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_followup_2)],
            FOLLOWUP_3: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_followup_3)],
            CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_report)],
            POST_SUBMISSION: [MessageHandler(filters.TEXT & ~filters.COMMAND, post_submission)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application.add_handler(conv_handler)
    log_info("Telegram bot starting...")
    application.run_polling()

if __name__ == "__main__":
    main()
