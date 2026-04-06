import os
import traceback
import requests
import json
from flask import Flask, render_template, request, jsonify, Response, stream_with_context
from flask_cors import CORS
from openai import OpenAI
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from dotenv import load_dotenv
from pypdf import PdfReader
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception
import io

# Load environment variables
load_dotenv()

app = Flask(__name__)
CORS(app)

app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload

# --- AI Configuration ---
ACADEMIC_SYSTEM_INSTRUCTION = """You are RCB AI, a Senior Academic Research Assistant. Your goal is to provide high-level, scholarly, and professional responses. 
Always use formal academic language, avoid slang, and prioritize clarity, analytical depth, and accuracy. 
When summarizing, focus on core arguments and scholarly implications, maintaining concision while preserving nuance. 
When answering questions, provide well-structured, authoritative responses that adhere to the highest research standards."""

# List of Free OpenRouter models to try (Fallback mechanism)
FREE_MODELS = [
    "openrouter/free",
    "google/gemini-2.0-flash-lite-preview-02-05:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "qwen/qwen-2.5-72b-instruct:free",
    "google/gemma-3-4b-it:free",
    "meta-llama/llama-3.2-3b-instruct:free"
]

def is_retryable_exception(exception):
    """Determines if the exception is due to a rate limit or busy server."""
    err_msg = str(exception).lower()
    return any(x in err_msg for x in ["429", "resource_exhausted", "quota", "503", "unavailable", "busy"])

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
    openrouter_key = (os.getenv('OPENROUTER_API_KEY') or "").strip()
    actual_key = (client_api_key or "").strip() if (client_api_key and len(client_api_key.strip()) > 30) else openrouter_key

    if not actual_key or any(x in actual_key for x in ["YOUR_OPENROUTER_API_KEY", "OPENROUTER_API_KEY"]):
         return jsonify({'error': 'OpenRouter API Key missing or invalid. Please provide a valid key.'}), 401

    # Prompt Prep
    if mode == 'summarize':
        prompt = f"Perform a concise yet scholarly academic summary. Focus on the core thesis, key findings, and scholarly implications. Tone: {tone}.\n\nText: {original_text}"
    elif mode == 'grammar':
        prompt = f"Refine the following text for academic publication. Correct errors and enhance the scholarly tone for an international journal. Tone: {tone}.\n\nText: {original_text}"
    elif mode == 'qa':
        prompt = f"As a research expert, provide a structured, authoritative answer to: {data.get('question','')}\n\nBased on this context: {original_text}"
    elif mode == 'gen_qa':
        prompt = f"From an academic review perspective, generate 5-10 high-level analytical research questions and their detailed scholarly answers from: {original_text}"
    else:
        prompt = f"Paraphrase this text for a research paper. Maintain perfect academic integrity while completely reshaping the syntax. Tone: {tone}.\n\nText: {original_text}"

    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=actual_key)

    def generate():
        full_response = ""
        ai_source = "openrouter"
        
        # Try models in order
        for model in FREE_MODELS:
            try:
                print(f"[DEBUG] Streaming Attempt: {model}")
                messages = [
                    {"role": "system", "content": ACADEMIC_SYSTEM_INSTRUCTION},
                    {"role": "user", "content": prompt}
                ]
                
                # First attempt with system message
                try:
                    stream = client.chat.completions.create(
                        model=model,
                        messages=messages,
                        extra_headers={
                            "HTTP-Referer": "https://rcb-ai.academic-assistant", 
                            "X-Title": "RCB AI Academic Assistant",
                        },
                        temperature=0.7,
                        stream=True
                    )
                    
                    found_content = False
                    for chunk in stream:
                        if chunk.choices[0].delta.content:
                            found_content = True
                            content = chunk.choices[0].delta.content
                            full_response += content
                            yield f"data: {json.dumps({'content': content})}\n\n"
                    
                    if found_content:
                        break # Success!
                        
                except Exception as e:
                    err_msg = str(e)
                    if "Developer instruction is not enabled" in err_msg or "system" in err_msg.lower():
                         print(f"[INFO] Model {model} does not support system role. Retrying with combined prompt...")
                         combined_prompt = f"{ACADEMIC_SYSTEM_INSTRUCTION}\n\nUSER REQUEST: {prompt}"
                         stream = client.chat.completions.create(
                            model=model,
                            messages=[{"role": "user", "content": combined_prompt}],
                            extra_headers={
                                "HTTP-Referer": "https://rcb-ai.academic-assistant", 
                                "X-Title": "RCB AI Academic Assistant",
                            },
                            temperature=0.7,
                            stream=True
                        )
                         for chunk in stream:
                            if chunk.choices[0].delta.content:
                                content = chunk.choices[0].delta.content
                                full_response += content
                                yield f"data: {json.dumps({'content': content})}\n\n"
                         break # Success on fallback
                    else:
                        raise e

            except Exception as e:
                print(f"[ERROR] Model {model} failed: {str(e)}")
                continue

        if not full_response:
             yield f"data: {json.dumps({'error': 'All models failed. Please wait 30s and try again.'})}\n\n"
        else:
             # Final metadata event
             similarity = calculate_similarity(original_text, full_response)
             plagiarism_score = (similarity * 100) if similarity < 1.0 else 98.4
             yield f"data: {json.dumps({'meta': True, 'engine': ai_source, 'similarity': round(plagiarism_score, 2)})}\n\n"

    return Response(stream_with_context(generate()), mimetype='text/event-stream')

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
                extracted = page.extract_text()
                if extracted:
                    text += extracted + "\n"
            return jsonify({'text': text.strip()})
        except Exception as e:
            return jsonify({'error': f'PDF extraction failed: {str(e)}'}), 500
    return jsonify({'error': 'Invalid file type'}), 400

if __name__ == '__main__':
    app.run(debug=True, port=5000)
