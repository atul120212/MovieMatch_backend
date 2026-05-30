"""
database.py — SQLAlchemy models matching the Supabase ERD exactly.

Tables
──────
users            – display name, avatar, genre prefs (JSON)
sessions         – room code, group type, host FK, state, expiry
session_members  – join table: session ↔ user
movie_decks      – ordered list of movies assigned to a session
votes            – per-user per-movie boolean votes
matches          – computed match rows written when reveal fires
"""

import os
import uuid
from datetime import datetime, timedelta
from typing import Optional

from dotenv import load_dotenv
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, relationship, sessionmaker

# ── JSON / JSONB ────────────────────────────────────────────────────────────
# SQLite does not have a native JSONB type; we store JSON as TEXT.
# PostgreSQL / Supabase supports the real JSONB type.
try:
    from sqlalchemy.dialects.postgresql import JSONB as JSONType
except ImportError:
    from sqlalchemy import JSON as JSONType  # fallback (never reached)

from sqlalchemy import JSON as _JSON  # always available

load_dotenv(override=True)

DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./moviematch.db")

_is_sqlite = DATABASE_URL.startswith("sqlite")
_connect_args = {"check_same_thread": False} if _is_sqlite else {}

engine = create_engine(DATABASE_URL, connect_args=_connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


# ── 1. users ────────────────────────────────────────────────────────────────
class User(Base):
    """
    Anonymous guest user created on room create / join.
    display_name  – the name they typed in
    avatar_url    – optional (future: generated avatar)
    genre_prefs   – JSON dict, e.g. {"action": 1.4, "romance": 0.6}
    """

    __tablename__ = "users"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    display_name = Column(String, nullable=False)
    avatar_url = Column(String, nullable=True)
    genre_prefs = Column(_JSON, nullable=True, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)

    # A user can host many sessions
    hosted_sessions = relationship(
        "Session", back_populates="host", cascade="all, delete-orphan"
    )
    # A user can be a member of many sessions
    memberships = relationship(
        "SessionMember", back_populates="user", cascade="all, delete-orphan"
    )
    # A user casts votes
    votes = relationship("Vote", back_populates="user", cascade="all, delete-orphan")


# ── 2. sessions ─────────────────────────────────────────────────────────────
class Session(Base):
    """
    A matching room.
    state machine: waiting → swiping → revealed
    """

    __tablename__ = "sessions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    room_code = Column(String(8), unique=True, index=True, nullable=False)
    group_type = Column(String, nullable=False)  # couple | family | friends | coworkers
    host_id = Column(String, ForeignKey("users.id"), nullable=False)
    state = Column(String, default="waiting", nullable=False)
    expires_at = Column(
        DateTime,
        default=lambda: datetime.utcnow()
        + timedelta(hours=int(os.getenv("SESSION_EXPIRY_HOURS", "24"))),
    )

    host = relationship("User", back_populates="hosted_sessions")
    members = relationship(
        "SessionMember", back_populates="session", cascade="all, delete-orphan"
    )
    movie_deck = relationship(
        "MovieDeck", back_populates="session", cascade="all, delete-orphan"
    )
    votes = relationship("Vote", back_populates="session", cascade="all, delete-orphan")
    matches = relationship(
        "Match", back_populates="session", cascade="all, delete-orphan"
    )


# ── 3. session_members ──────────────────────────────────────────────────────
class SessionMember(Base):
    """Join table: tracks who joined which session and when."""

    __tablename__ = "session_members"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = Column(String, ForeignKey("sessions.id"), nullable=False)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    joined_at = Column(DateTime, default=datetime.utcnow)

    session = relationship("Session", back_populates="members")
    user = relationship("User", back_populates="memberships")


# ── 4. movie_decks ──────────────────────────────────────────────────────────
class MovieDeck(Base):
    """
    An ordered movie card inside a session's deck.
    tmdb_id   – TMDB numeric id stored as text (e.g. "550")
    position  – card order (0-based)
    metadata  – full movie info as JSON blob:
                {title, overview, poster_path, genres, rating, runtime, streaming_info}
    """

    __tablename__ = "movie_decks"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = Column(String, ForeignKey("sessions.id"), nullable=False)
    tmdb_id = Column(String, nullable=False)
    position = Column(Integer, nullable=False)
    movie_metadata = Column("metadata", _JSON, nullable=True, default=dict)

    session = relationship("Session", back_populates="movie_deck")


# ── 5. votes ────────────────────────────────────────────────────────────────
class Vote(Base):
    """
    One user's yes/no on one movie inside a session.
    choice – True = yes (like), False = no (dislike)
    """

    __tablename__ = "votes"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = Column(String, ForeignKey("sessions.id"), nullable=False)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    tmdb_id = Column(String, nullable=False)
    choice = Column(Boolean, nullable=False)  # True = yes, False = no
    voted_at = Column(DateTime, default=datetime.utcnow)

    session = relationship("Session", back_populates="votes")
    user = relationship("User", back_populates="votes")


# ── 6. matches ──────────────────────────────────────────────────────────────
class Match(Base):
    """
    Computed match result written at reveal time.
    yes_count   – how many members voted yes
    unanimous   – True if every member voted yes
    matched_at  – when the match row was created
    """

    __tablename__ = "matches"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = Column(String, ForeignKey("sessions.id"), nullable=False)
    tmdb_id = Column(String, nullable=False)
    yes_count = Column(Integer, nullable=False, default=0)
    unanimous = Column(Boolean, nullable=False, default=False)
    matched_at = Column(DateTime, default=datetime.utcnow)

    session = relationship("Session", back_populates="matches")


# ── Helpers ──────────────────────────────────────────────────────────────────
def init_db() -> None:
    """Create all tables (idempotent)."""
    Base.metadata.create_all(bind=engine)


def get_db():
    """FastAPI dependency — yields a DB session and closes it afterward."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
