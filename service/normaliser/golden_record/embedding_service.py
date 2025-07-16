import os
import json
import sys # Added import for sys
from typing import Optional, List
from dotenv import load_dotenv
from google import genai
from google.genai import types
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.extensions import connection as PgConnection, cursor as PgCursor

# Import database utility functions
from .db_utils import get_db_connection

# Load environment variables
load_dotenv()

# Configure Google Gemini API
client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))

def get_embedding(text: str, embedder_type: str = "gemini") -> Optional[List[float]]:
    """Generates a vector embedding for the given text using the specified embedder type."""
    if embedder_type == "gemini":
        model_name = os.getenv("GEMINI_EMBEDDING_MODEL", "models/embedding-001")
        try:
            response = client.models.embed_content(
                model=model_name,
                contents=[text],
                config=types.EmbedContentConfig(output_dimensionality=768)
            )
            if 'usage_metadata' in response and response['usage_metadata']:
                total_tokens = response['usage_metadata']['total_token_count']
                # In a real service, you might log this or send it to a monitoring system
                pass
            return response.embeddings[0].values # Changed to .values
        except Exception as e:
            # In a real service, you might log this error more robustly
            print(f"Error generating embedding with Gemini for text '{text}': {e}", file=sys.stderr) # Changed to sys.stderr
            if hasattr(e, 'response') and hasattr(e.response, 'text'):
                print(f"Gemini Embedding API error response text: {e.response.text}", file=sys.stderr) # Changed to sys.stderr
            return None
    else:
        print(f"Unsupported embedder type: {embedder_type}", file=sys.stderr) # Changed to sys.stderr
        return None

def process_missing_embeddings() -> None:
    """
    Queries g_products for records missing embeddings, generates them,
    and updates the database.
    """
    conn: Optional[PgConnection] = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # Query for products missing embeddings
        cur.execute("""
            SELECT id, text_for_embedding
            FROM g_products
            WHERE embedding IS NULL
            LIMIT %s;
        """, (int(os.getenv("EMBEDDING_BATCH_LIMIT", 100)),)) # Use a batch limit for processing
        
        products_to_embed = cur.fetchall()

        if not products_to_embed:
            print("No products found missing embeddings. Exiting.")
            return

        print(f"Found {len(products_to_embed)} products missing embeddings. Processing...")

        for product in products_to_embed:
            product_id = product['id']
            text_for_embedding = product['text_for_embedding']

            if not text_for_embedding:
                print(f"Skipping product ID {product_id}: 'text_for_embedding' is NULL or empty.")
                continue

            # Assuming default embedder type is 'gemini' for this standalone process
            embedding = get_embedding(text_for_embedding, "gemini") 
            
            if embedding:
                try:
                    # Update the g_products table with the new embedding
                    cur.execute("""
                        UPDATE g_products
                        SET embedding = %s
                        WHERE id = %s;
                    """, (json.dumps(embedding), product_id)) # Store as JSONB
                    conn.commit()
                    print(f"Successfully updated embedding for product ID {product_id}.")
                except Exception as update_e:
                    conn.rollback()
                    print(f"Error updating embedding for product ID {product_id}: {update_e}. Transaction rolled back.")
            else:
                print(f"Failed to generate embedding for product ID {product_id}.")
        
        print("Finished processing missing embeddings.")

    except Exception as e:
        print(f"An error occurred during the main embedding processing loop: {e}", file=sys.stderr) # Changed to sys.stderr
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    # This script is primarily for backfilling missing embeddings,
    # so it doesn't need to take embedder_type as a command-line arg for now.
    # It will just use the default 'gemini' as configured.
    process_missing_embeddings()
