import os
import traceback
import requests
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from google import genai
from google.genai import types
from openai import OpenAI
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from dotenv import load_dotenv
from pypdf import PdfReader
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception, retry_if_result
import io

# Load environment variables
load_dotenv()

app = Flask(__name__)
CORS(app)

app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload

# --- AI Configuration ---
ACADEMIC_SYSTEM_INSTRUCTION = """You are RCB AI, a Senior Academic Research Assistant. Your goal is to provide high-level, scholarly, and professional responses. 
Always use formal academic language, avoid slang, and prioritize clarity, analytical depth, and accuracy. 
When summarizing, capture all theoretical and practical nuances. 
When answering questions, provide comprehensive and well structured responses as if writing for an academic journal or thesis project."""

LM_STUDIO_URL = "http://localhost:1234/v1"

def check_lm_studio():
    """Checks if LM Studio is running on the local port."""
    try:
        # Standard LM Studio /models check
        response = requests.get(f"{LM_STUDIO_URL}/models", timeout=0.8)
        return response.status_code == 200
    except:
        return False

def is_retryable_exception(exception):
    """Determines if the exception is due to a rate limit (429) or busy server (503)."""
    err_msg = str(exception).lower()
    return any(x in err_msg for x in ["429", "resource_exhausted", "quota", "503", "unavailable"])

# Retry decorator: Wait exponentially up to 3 attempts for transient external API issues
@retry(
    retry=retry_if_exception(is_retryable_exception),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True
)
def generate_with_gemini(client, model_id, contents, config):
    print(f"[DEBUG] Gemini Attempt: {model_id}")
    return client.models.generate_content(
        model=model_id,
        contents=contents,
        config=config
    )

def generate_with_lm_studio(prompt, system_instruction):
    """Generates content using the local LM Studio server."""
    client = OpenAI(base_url=LM_STUDIO_URL, api_key="lm-studio")
    print(f"[DEBUG] LM Studio Attempt")
    response = client.chat.completions.create(
        model="local-model", # LM Studio accepts any model name if only one is loaded
        messages=[
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": prompt}
        ],
        temperature=0.7
    )
    return response.choices[0].message.content

def calculate_similarity(text1, text2):
    """Calculates cosine similarity between two texts using TF-IDF."""
    if not text1.strip() or not text2.strip():
        return 0.0
    
    vectorizer = TfidfVectorizer()
    try:
        tfidf_matrix = vectorizer.fit_transform([text1, text2])
        similarity = cosine_similarity(tfidf_matrix[0:1], tfidf_matrix[1:2])[0][0]
        return float(similarity)
    except Exception as e:
        print(f"Error calculating similarity: {e}")
        return 0.0

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/paraphrase', methods=['POST'])
def paraphrase():
    data = request.json
    original_text = data.get('text', '')
    tone = data.get('tone', 'professional')
    mode = data.get('mode', 'paraphrase') 
    client_api_key = data.get('api_key')

    if not original_text:
        return jsonify({'error': 'No text provided'}), 400

    # API Keys from env/client
    gemini_key = os.getenv('GEMINI_API_KEY')
    actual_key = client_api_key if (client_api_key and len(client_api_key) > 30) else gemini_key

    # Prompt Prep
    if mode == 'summarize':
        prompt = f"Perform a comprehensive academic summary of this text. Tone: {tone}. Focus on key findings and scholarly implications.\n\nText: {original_text}"
    elif mode == 'grammar':
        prompt = f"Refine the following text for publication in an academic journal. Tone: {tone}. Correct errors and enhance scholarly tone.\n\nText: {original_text}"
    elif mode == 'qa':
        prompt = f"As a research expert, provide an academically structured answer to: {data.get('question','')}\n\nBased on this source context: {original_text}"
    elif mode == 'gen_qa':
        prompt = f"From an academic review perspective, generate 5-10 analytical questions and their detailed scholarly answers from: {original_text}"
    else:
        prompt = f"Paraphrase this text for a research paper. Maintain perfect academic integrity. Tone: {tone}.\n\nText: {original_text}"

    ai_source = "local"
    try:
        # 1. TRY LM STUDIO (LOCAL) FIRST
        if check_lm_studio():
            print(">>> Using LM Studio (Local Engine)")
            paraphrased_text = generate_with_lm_studio(prompt, ACADEMIC_SYSTEM_INSTRUCTION)
        
        # 2. FALLBACK TO GEMINI (CLOUD)
        elif actual_key and len(actual_key) > 30 and "YOUR_API_KEY" not in actual_key:
            print(">>> LM Studio offline. Falling back to Gemini API (Cloud Engine)")
            ai_source = "cloud"
            client = genai.Client(api_key=actual_key)
            config = types.GenerateContentConfig(system_instruction=ACADEMIC_SYSTEM_INSTRUCTION)
            try:
                # Primary Cloud Attempt
                response = generate_with_gemini(client, 'gemini-2.0-flash', prompt, config)
            except Exception as e:
                traceback.print_exc()
                if is_retryable_exception(e) or "404" in str(e):
                    # Final Cloud Fallback
                    response = generate_with_gemini(client, 'gemini-flash-latest', prompt, config)
                else: 
                    raise e
            paraphrased_text = response.text.replace('*', '').strip()
        
        else:
            # 3. DEMO MODE
            ai_source = "demo"
            paraphrased_text = f"**[Demo Mode]** LM Studio is offline and no Gemini Key was found. Here is a simulated response.\n\nOriginal Text: {original_text}"

        # Post-Process Result
        similarity = calculate_similarity(original_text, paraphrased_text)
        plagiarism_score = (similarity * 100) if similarity < 1.0 else 98.4

        return jsonify({
            'original': original_text,
            'paraphrased': paraphrased_text,
            'similarity_score': round(plagiarism_score, 2),
            'status': 'success',
            'engine': ai_source
        })

    except Exception as e:
        error_msg = str(e)
        print(f"Hybrid AI Error: {error_msg}")
        
        if "404" in error_msg:
            friendly_error = "Model not found. The API version or model requested may be temporarily unavailable."
        elif "429" in error_msg or "resource_exhausted" in error_msg.lower():
            friendly_error = "Free Tier Limit: Both engines are busy. Please wait 30s and try again."
        elif "503" in error_msg:
            friendly_error = "Service Unavailable: The AI server is experiencing high demand. Retrying locally..."
        else:
            friendly_error = f"Academic processing failed: {error_msg}"
            
        return jsonify({'error': friendly_error}), 500

@app.route('/api/extract-pdf', methods=['POST'])
def extract_pdf():
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    
    file = request.files['file']
    if file and file.filename.endswith('.pdf'):
        try:
            reader = PdfReader(io.BytesIO(file.read()))
            text = ""
            for page in reader.pages:
                text += page.extract_text() + "\n"
            return jsonify({'text': text.strip()})
        except Exception as e:
            return jsonify({'error': 'PDF extraction failed'}), 500
    return jsonify({'error': 'Invalid file type'}), 400

if __name__ == '__main__':
    app.run(debug=True, port=5000)
