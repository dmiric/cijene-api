# minimal_gemini_test.py

# This uses the same client library as your application
from google import genai
import os
import asyncio

# --- Configuration ---
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# The client is initialized globally, just like in your app's ai_models.py
# It will use the environment's Application Default Credentials (ADC)
gemini_client = genai.Client()

# The simplified, safe prompt we are testing
SYSTEM_PROMPT = """
    Ti si 'Cjenolovac Asistent'. Pomažeš korisnicima u Hrvatskoj pri kupovini. Komuniciraj prijateljski i jasno na hrvatskom jeziku. Korisnik se zove Damir.

    Postoje dvije situacije:

    1.  **Ako korisnik traži proizvod (npr. 'Limun', 'mlijeko'):**
        - Reci `TOOL_SEARCH`.

    2.  **Ako korisnik postavi bilo koje drugo pitanje (npr. o Eiffelovom tornju):**
        - Odgovori na pitanje izravno, bez korištenja alata.
    """

# The user's question that is failing
USER_QUESTION = "Koliko je visok Eifelov toranj?"

async def run_test():
    print("--- Running Minimal Gemini API Test ---")
    
    # --- THIS IS THE FIX ---
    # We must construct the history using the library's actual types,
    # exactly like the AIProvider does.
    history = [
        genai.types.Content(role='user', parts=[genai.types.Part(text=SYSTEM_PROMPT)]),
        genai.types.Content(role='model', parts=[genai.types.Part(text="Razumijem.")]),
        genai.types.Content(role='user', parts=[genai.types.Part(text=USER_QUESTION)])
    ]
    # --- END OF FIX ---
    
    # The model is specified by name in the call
    model = 'gemini-2.5-flash'

    print("\n>>> TEST 1: Calling the API WITH tools DISABLED...")
    try:
        # Create a config object to disable tools. Passing None to `config` is not valid.
        # We must pass a config object, even if it's empty.
        config_without_tools = genai.types.GenerateContentConfig()
        
        streaming_response = await gemini_client.aio.models.generate_content_stream(
            model=model, 
            contents=history, 
            config=config_without_tools
        )
        
        print("API call successful. Streaming response...")
        full_text = ""
        chunk_count = 0
        async for chunk in streaming_response:
            chunk_count += 1
            # Safely check for text in the chunk's parts
            if hasattr(chunk, 'candidates') and chunk.candidates:
                for candidate in chunk.candidates:
                    if hasattr(candidate.content, 'parts'):
                        for part in candidate.content.parts:
                            if part.text:
                                full_text += part.text
        
        print(f"Received {chunk_count} chunks.")
        if full_text:
            print("Response Text:")
            print(full_text)
        else:
            print("Response stream was empty. No text was generated.")

    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    asyncio.run(run_test())