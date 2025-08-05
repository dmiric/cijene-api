from dotenv import load_dotenv
load_dotenv() # Load environment variables at the very top

import os
import json
import sys
import logging
import structlog
from typing import Optional, List
from google import genai
from google.genai import types
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.extensions import connection as PgConnection, cursor as PgCursor

# Import database utility functions
from ..db_utils import get_db_connection
from service.main import configure_logging # Import configure_logging

# Global client variable, initialized lazily
_gemini_embedding_client = None

def _initialize_gemini_embedding_client():
    """Initializes the Google Gemini embedding client lazily."""
    global _gemini_embedding_client
    if _gemini_embedding_client is None:
        _gemini_embedding_client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
        log.info("Google Gemini embedding client initialized.")

# Configure Google Gemini API
client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))

def get_embedding(text: str, embedder_type: str = "gemini") -> Optional[List[float]]:
    """Generates a vector embedding for the given text using the specified embedder type."""
    _initialize_gemini_embedding_client() # Ensure client is initialized
    if embedder_type == "gemini":
        model_name = os.getenv("GEMINI_EMBEDDING_MODEL", "models/embedding-001")
        try:
            response = _gemini_embedding_client.models.embed_content( # Use the global client
                model=model_name,
                contents=[text],
                config=types.EmbedContentConfig(output_dimensionality=768)
            )
            if 'usage_metadata' in response and response['usage_metadata']:
                total_tokens = response['usage_metadata']['total_token_count']
                # In a real service, you might log this or send it to a monitoring system
                pass
            return response.embeddings[0].values
        except Exception as e:
            log.error("Error generating embedding with Gemini", text=text, error=str(e))
            if hasattr(e, 'response') and hasattr(e.response, 'text'):
                log.error("Gemini Embedding API error response text", response_text=e.response.text)
            return None
    else:
        log.error("Unsupported embedder type", embedder_type=embedder_type)
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
        """, (int(os.getenv("EMBEDDING_BATCH_LIMIT", 100)),))
        
        products_to_embed = cur.fetchall()

        if not products_to_embed:
            log.info("No products found missing embeddings. Exiting.")
            return

        log.info("Found products missing embeddings. Processing...", num_products=len(products_to_embed))

        for product in products_to_embed:
            product_id = product['id']
            text_for_embedding = product['text_for_embedding']

            if not text_for_embedding:
                log.info("Skipping product: 'text_for_embedding' is NULL or empty.", product_id=product_id)
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
                    """, (json.dumps(embedding), product_id))
                    conn.commit()
                    log.info("Successfully updated embedding for product ID.", product_id=product_id)
                except Exception as update_e:
                    conn.rollback()
                    log.error("Error updating embedding for product ID. Transaction rolled back.", product_id=product_id, error=str(update_e))
            else:
                log.error("Failed to generate embedding for product ID.", product_id=product_id)
        
        log.info("Finished processing missing embeddings.")

    except Exception as e:
        log.error("An error occurred during the main embedding processing loop", error=str(e))
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    configure_logging() # Configure logging at the start of the script
    log = structlog.get_logger() # Initialize structlog logger AFTER configuration
    # This script is primarily for backfilling missing embeddings,
    # so it doesn't need to take embedder_type as a command-line arg for now.
    # It will just use the default 'gemini' as configured.
    process_missing_embeddings()
