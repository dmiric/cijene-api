import os
from google import genai # Updated import
from openai import OpenAI
from dotenv import load_dotenv
import sys
import structlog # Import structlog

load_dotenv()

log = structlog.get_logger(__name__) # Initialize structlog logger

# Google Gemini
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
gemini_client = None
if GOOGLE_API_KEY:
    gemini_client = genai.Client() # Initialize the new client
else:
    log.debug("GOOGLE_API_KEY not found.")

# OpenAI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
openai_client = None
if OPENAI_API_KEY:
    openai_client = OpenAI(api_key=OPENAI_API_KEY)
else:
    log.debug("OPENAI_API_KEY not found.")
