import os
from google import genai # Updated import
from openai import OpenAI
from dotenv import load_dotenv
import sys

load_dotenv()

def debug_print(*args, **kwargs):
    print("[DEBUG AI_MODELS]", *args, file=sys.stderr, **kwargs)

# Google Gemini
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
gemini_client = None
if GOOGLE_API_KEY:
    gemini_client = genai.Client() # Initialize the new client
else:
    debug_print("GOOGLE_API_KEY not found.")

# OpenAI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
openai_client = None
if OPENAI_API_KEY:
    openai_client = OpenAI(api_key=OPENAI_API_KEY)
else:
    debug_print("OPENAI_API_KEY not found.")
