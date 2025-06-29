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

    
    async def save_chat_message(
        self,
        user_id: int,
        session_id: str,
        message_text: str,
        is_user_message: bool,
        tool_calls: Optional[List[dict]] = None,
        tool_outputs: Optional[List[dict]] = None,
        ai_response: Optional[str] = None,
    ) -> int:
        """
        Saves a chat message to the database.
        """
        # Determine sender based on is_user_message
        sender = "user" if is_user_message else "ai"
        if tool_calls:
            sender = "tool_call"
        elif tool_outputs:
            sender = "tool_output"

        # Create ChatMessage object
        message = ChatMessage(
            id=str(uuid4()),
            user_id=user_id,
            session_id=session_id,
            sender=sender,
            message_text=message_text,
            timestamp=datetime.now(timezone.utc),
            tool_calls=tool_calls,
            tool_outputs=tool_outputs,
            ai_response=ai_response,
        )

        async with self._atomic() as conn:
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
        self.debug_print(f"Saved chat message: {message.id}")
        return 1 # Return a dummy ID for now, as the method signature expects int

    
    async def get_chat_messages(self, user_id: int, session_id: UUID, limit: int = 20) -> list[ChatMessage]:
        """
        Retrieves chat messages for a given user and session, ordered by timestamp.
        """
        async with self._get_conn() as conn:
            rows = await conn.fetch(
                """
                SELECT id, user_id, session_id, sender, message_text, timestamp, tool_calls, tool_outputs
                FROM chat_messages
                WHERE user_id = $1 AND session_id = $2
                ORDER BY timestamp ASC
                LIMIT $3
                """,
                user_id,
                session_id,
                limit,
            )
            return [
                ChatMessage(
                    id=str(row["id"]),
                    user_id=row["user_id"],
                    session_id=str(row["session_id"]),
                    sender=row["sender"],
                    message_text=row["message_text"],
                    timestamp=row["timestamp"],
                    tool_calls=row["tool_calls"],
                    tool_outputs=row["tool_outputs"],
                )
                for row in rows
            ]
