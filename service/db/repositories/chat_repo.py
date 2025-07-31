from contextlib import asynccontextmanager
import asyncpg
from typing import (
    AsyncGenerator,
    AsyncIterator,
    List,
    Any,
    Optional,
)
import os
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4
import sys
import json
import pgvector.asyncpg
from service.utils.timing import timing_decorator # Import the decorator

from service.db.base import BaseRepository # Changed from Database as DBConnectionManager
from service.db.models import (
    ChatMessage,
)


class ChatRepository(BaseRepository):
    """
    Contains all logic for interacting with chat-related tables
    (chat_messages).
    """

    def __init__(self):
        self.pool = None
        def debug_print_db(*args, **kwargs):
            print("[DEBUG chat_repo]", *args, file=sys.stderr, **kwargs)
        self.debug_print = debug_print_db

    async def connect(self, pool: asyncpg.Pool) -> None:
        """
        Initializes the repository with an existing connection pool.
        This repository does not create its own pool.
        """
        self.pool = pool

    @asynccontextmanager
    async def _get_conn(self) -> AsyncGenerator[asyncpg.Connection, None]:
        if not self.pool:
            raise RuntimeError("Database pool is not initialized for ChatRepository")
        async with self.pool.acquire() as conn:
            yield conn

    @asynccontextmanager
    async def _atomic(self) -> AsyncIterator[asyncpg.Connection]:
        async with self._get_conn() as conn:
            async with conn.transaction():
                yield conn

    async def close(self) -> None:
        if self.pool:
            await self.pool.close()

    async def _fetchval(self, query: str, *args: Any) -> Any:
        async with self._get_conn() as conn:
            return await conn.fetchval(query, *args)
        
    async def save_chat_message_from_object(self, message: ChatMessage) -> None:
        """
        Saves a chat message to the DB directly from a ChatMessage Pydantic object.
        This is the new preferred method for the ChatOrchestrator.
        """
        # The 'message' object is already fully populated with id, timestamp, sender, etc.
        # We just need to insert its values into the database.

        async with self._atomic() as conn:
            try:
                # The query is the same as in your other method, but we get values from the object.
                await conn.execute(
                    """
                    INSERT INTO chat_messages (id, user_id, session_id, sender, message_text, timestamp, tool_calls, tool_outputs, ai_response)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    """,
                    message.id,
                    message.user_id,
                    message.session_id,
                    message.sender,
                    message.message_text,
                    message.timestamp,
                    json.dumps(message.tool_calls) if message.tool_calls else None,
                    json.dumps(message.tool_outputs) if message.tool_outputs else None,
                    message.ai_response,
                )
                self.debug_print(f"Saved chat message: {message.id} (Sender: {message.sender})")
            except asyncpg.exceptions.PostgresError as e:
                self.debug_print(f"ERROR: PostgresError saving chat message object {message.id}: {e}")
                raise
            except Exception as e:
                self.debug_print(f"ERROR: Unexpected error saving chat message object {message.id}: {e}")
                raise 
        
    
    async def get_chat_messages_by_session(self, user_id: UUID, session_id: UUID, limit: int = 20) -> list[ChatMessage]:
        """
        Retrieves chat messages for a given user and session, ordered by timestamp.
        """
        async with self._get_conn() as conn:
            rows = await conn.fetch(
                """
                SELECT id, user_id, session_id, sender, message_text, timestamp, tool_calls, tool_outputs, ai_response
                FROM chat_messages
                WHERE user_id = $1 AND session_id = $2
                ORDER BY timestamp ASC
                LIMIT $3
                """,
                user_id,
                session_id,
                limit,
            )
            chat_messages = []
            for row in rows:
                tool_calls_data = row["tool_calls"]
                if isinstance(tool_calls_data, str):
                    try:
                        tool_calls_data = json.loads(tool_calls_data)
                    except json.JSONDecodeError:
                        self.debug_print(f"Error decoding tool_calls string from DB: {tool_calls_data}")
                        tool_calls_data = None

                tool_outputs_data = row["tool_outputs"]
                if isinstance(tool_outputs_data, str):
                    try:
                        tool_outputs_data = json.loads(tool_outputs_data)
                    except json.JSONDecodeError:
                        self.debug_print(f"Error decoding tool_outputs string from DB: {tool_outputs_data}")
                        tool_outputs_data = None

                chat_messages.append(
                    ChatMessage(
                        id=row["id"],
                        user_id=row["user_id"],
                        session_id=row["session_id"],
                        sender=row["sender"],
                        message_text=row["message_text"],
                        timestamp=row["timestamp"],
                        tool_calls=tool_calls_data,
                        tool_outputs=tool_outputs_data,
                        ai_response=row["ai_response"], # Fetch ai_response
                    )
                )
            return chat_messages

    async def get_latest_chat_messages(self, user_id: UUID, limit: int = 20) -> list[ChatMessage]:
        """
        Retrieves the latest chat messages for a given user, ordered by timestamp descending.
        The list is then reversed to maintain chronological order for the AI.
        """
        async with self._get_conn() as conn:
            rows = await conn.fetch(
                """
                SELECT id, user_id, session_id, sender, message_text, timestamp, tool_calls, tool_outputs, ai_response
                FROM chat_messages
                WHERE user_id = $1
                ORDER BY timestamp DESC
                LIMIT $2
                """,
                user_id,
                limit,
            )
            chat_messages = []
            for row in rows:
                tool_calls_data = row["tool_calls"]
                if isinstance(tool_calls_data, str):
                    try:
                        tool_calls_data = json.loads(tool_calls_data)
                    except json.JSONDecodeError:
                        self.debug_print(f"Error decoding tool_calls string from DB: {tool_calls_data}")
                        tool_calls_data = None

                tool_outputs_data = row["tool_outputs"]
                if isinstance(tool_outputs_data, str):
                    try:
                        tool_outputs_data = json.loads(tool_outputs_data)
                    except json.JSONDecodeError:
                        self.debug_print(f"Error decoding tool_outputs string from DB: {tool_outputs_data}")
                        tool_outputs_data = None

                chat_messages.append(
                    ChatMessage(
                        id=row["id"],
                        user_id=row["user_id"],
                        session_id=row["session_id"],
                        sender=row["sender"],
                        message_text=row["message_text"],
                        timestamp=row["timestamp"],
                        tool_calls=tool_calls_data,
                        tool_outputs=tool_outputs_data,
                        ai_response=row["ai_response"],
                    )
                )
            return list(reversed(chat_messages)) # Reverse to maintain chronological order
