"""
database.py — SQLAlchemy models matching the Supabase ERD exactly.

Tables
──────
users             – display name, email, avatar, genre prefs (JSON), notifications, pref metadata
push_tokens       – push notification token registrations
sessions          – room code, group type, host FK, state, invite token, auth toggles, expiry
session_members   – join table: session ↔ user, left/joined tracking
movie_decks       – ordered list of movies assigned to a session
votes             – per-user per-movie boolean votes with swipe duration
matches           – computed match rows written when reveal fires with voter statistics
movies            – normalized movie repository for custom watch uploads
video_chapters    – coordinates of video chapters in watch movies
watch_rooms       – LiveKit synchronized movie watch rooms
viewer_states     – active stream participant mic/camera and buffering synchronization status
watch_events      – playback telemetry logs (play, pause, seek, buffer)
chat_messages     – synchronized message comments overlaying the movie playback position
report_flags      – reports and content moderation flags
session_analytics – stats compiled at session completion
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
    BigInteger,
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
    from sqlalchemy import JSON as JSONType  # fallback

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
    User account (anonymous guests or future authenticated members).
    """
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    display_name = Column(String, nullable=False)
    email = Column(String, nullable=True)
    avatar_url = Column(String, nullable=True)
    timezone = Column(String, nullable=True)
    region_code = Column(String, nullable=True)
    language_code = Column(String, nullable=True)
    genre_prefs = Column(_JSON, nullable=True, default=dict)
    watch_history_vector = Column(_JSON, nullable=True)
    is_guest = Column(Boolean, default=True, nullable=False)
    notified = Column(Boolean, default=False, nullable=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    # Relationships
    hosted_sessions = relationship("Session", back_populates="host", cascade="all, delete-orphan")
    memberships = relationship("SessionMember", back_populates="user", cascade="all, delete-orphan")
    votes = relationship("Vote", back_populates="user", cascade="all, delete-orphan")
    push_tokens = relationship("PushToken", back_populates="user", cascade="all, delete-orphan")
    movies = relationship("Movie", back_populates="uploader", cascade="all, delete-orphan")
    chat_messages = relationship("ChatMessage", back_populates="user", cascade="all, delete-orphan")
    watch_rooms = relationship("WatchRoom", back_populates="host", cascade="all, delete-orphan")


# ── 2. push_tokens ──────────────────────────────────────────────────────────
class PushToken(Base):
    """
    Push notification registry tokens per user device.
    """
    __tablename__ = "push_tokens"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    token = Column(String, nullable=False)
    platform = Column(String, nullable=False)  # ios, android, web
    active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="push_tokens")


# ── 3. sessions ─────────────────────────────────────────────────────────────
class Session(Base):
    """
    A matching room or sync watch session.
    """
    __tablename__ = "sessions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    room_code = Column(String(8), unique=True, index=True, nullable=False)
    group_type = Column(String, nullable=False)  # couple | family | friends | coworkers
    host_id = Column(String, ForeignKey("users.id"), nullable=False)
    state = Column(String, default="waiting", nullable=False)  # waiting, swiping, revealed
    invite_link_token = Column(String, nullable=True)
    max_members = Column(Integer, default=10, nullable=False)
    require_auth = Column(Boolean, default=False, nullable=False)
    expires_at = Column(DateTime(timezone=True), default=lambda: datetime.utcnow() + timedelta(hours=int(os.getenv("SESSION_EXPIRY_HOURS", "24"))), nullable=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    host = relationship("User", back_populates="hosted_sessions")
    members = relationship("SessionMember", back_populates="session", cascade="all, delete-orphan")
    movie_deck = relationship("MovieDeck", back_populates="session", cascade="all, delete-orphan")
    votes = relationship("Vote", back_populates="session", cascade="all, delete-orphan")
    matches = relationship("Match", back_populates="session", cascade="all, delete-orphan")
    watch_rooms = relationship("WatchRoom", back_populates="session", cascade="all, delete-orphan")
    analytics = relationship("SessionAnalytics", back_populates="session", uselist=False, cascade="all, delete-orphan")


# ── 4. session_members ──────────────────────────────────────────────────────
class SessionMember(Base):
    """
    Tracks which users joined which session.
    """
    __tablename__ = "session_members"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = Column(String, ForeignKey("sessions.id"), nullable=False)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    is_host = Column(Boolean, default=False, nullable=False)
    left_at = Column(DateTime(timezone=True), nullable=True)
    joined_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    session = relationship("Session", back_populates="members")
    user = relationship("User", back_populates="memberships")


# ── 5. movie_decks ──────────────────────────────────────────────────────────
class MovieDeck(Base):
    """
    An ordered movie card deck inside a session.
    """
    __tablename__ = "movie_decks"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = Column(String, ForeignKey("sessions.id"), nullable=False)
    tmdb_id = Column(String, nullable=False)
    position = Column(Integer, nullable=False)
    movie_metadata = Column("metadata", _JSON, nullable=True, default=dict)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    session = relationship("Session", back_populates="movie_deck")


# ── 6. votes ────────────────────────────────────────────────────────────────
class Vote(Base):
    """
    A single user vote on a card.
    """
    __tablename__ = "votes"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = Column(String, ForeignKey("sessions.id"), nullable=False)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    tmdb_id = Column(String, nullable=False)
    choice = Column(Boolean, nullable=False)  # True = yes, False = no
    swipe_duration_ms = Column(Integer, nullable=True)
    voted_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    session = relationship("Session", back_populates="votes")
    user = relationship("User", back_populates="votes")


# ── 7. matches ──────────────────────────────────────────────────────────────
class Match(Base):
    """
    A match found in a session.
    """
    __tablename__ = "matches"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = Column(String, ForeignKey("sessions.id"), nullable=False)
    tmdb_id = Column(String, nullable=False)
    yes_count = Column(Integer, default=0, nullable=False)
    total_voters = Column(Integer, default=0, nullable=False)
    unanimous = Column(Boolean, default=False, nullable=False)
    matched_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    session = relationship("Session", back_populates="matches")


# ── 8. movies ───────────────────────────────────────────────────────────────
class Movie(Base):
    """
    Represents custom uploaded/curated sync-watch video catalog entries.
    """
    __tablename__ = "movies"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    uploaded_by = Column(String, ForeignKey("users.id"), nullable=True)
    title = Column(String, nullable=False)
    duration_ms = Column(BigInteger, nullable=True)
    hls_master_url = Column(String, nullable=True)
    stream_url = Column(String, nullable=True)
    thumbnail_url = Column(String, nullable=True)
    status = Column(String, default="pending", nullable=False)  # pending, transcoding, ready, failed
    progress = Column(Integer, default=0, nullable=False)
    file_size_mb = Column(Integer, nullable=True)
    quality_levels = Column(_JSON, nullable=True, default=dict)
    subtitle_tracks = Column(String, nullable=True)
    content_hash = Column(String, nullable=True)
    rights_confirmed = Column(Boolean, default=False, nullable=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    uploader = relationship("User", back_populates="movies")
    chapters = relationship("VideoChapter", back_populates="movie", cascade="all, delete-orphan")
    watch_rooms = relationship("WatchRoom", back_populates="movie", cascade="all, delete-orphan")


# ── 9. video_chapters ───────────────────────────────────────────────────────
class VideoChapter(Base):
    """
    Chapter coordinates for scrubbing through custom movies.
    """
    __tablename__ = "video_chapters"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    movie_id = Column(String, ForeignKey("movies.id"), nullable=False)
    title = Column(String, nullable=False)
    start_ms = Column(BigInteger, nullable=False)
    end_ms = Column(BigInteger, nullable=False)
    chapter_number = Column(Integer, nullable=False)

    movie = relationship("Movie", back_populates="chapters")


# ── 10. watch_rooms ────────────────────────────────────────────────────────
class WatchRoom(Base):
    """
    Real-time streaming synchronizer session rooms (e.g. video play position syncing).
    """
    __tablename__ = "watch_rooms"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = Column(String, ForeignKey("sessions.id"), nullable=False)
    movie_id = Column(String, ForeignKey("movies.id"), nullable=True)
    host_id = Column(String, ForeignKey("users.id"), nullable=False)
    state = Column(String, default="stopped", nullable=False)  # stopped, playing, paused
    position_ms = Column(BigInteger, default=0, nullable=False)
    viewer_count = Column(Integer, default=0, nullable=False)
    livekit_room_name = Column(String, nullable=True)
    chat_channel_id = Column(String, nullable=True)
    allow_camera = Column(Boolean, default=True, nullable=False)
    allow_chat = Column(Boolean, default=True, nullable=False)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    session = relationship("Session", back_populates="watch_rooms")
    movie = relationship("Movie", back_populates="watch_rooms")
    host = relationship("User", back_populates="watch_rooms")
    viewer_states = relationship("ViewerState", back_populates="room", cascade="all, delete-orphan")
    watch_events = relationship("WatchEvent", back_populates="room", cascade="all, delete-orphan")
    chat_messages = relationship("ChatMessage", back_populates="room", cascade="all, delete-orphan")


# ── 11. viewer_states ──────────────────────────────────────────────────────
class ViewerState(Base):
    """
    Connection and playback status sync for active room viewers.
    """
    __tablename__ = "viewer_states"

    room_id = Column(String, ForeignKey("watch_rooms.id"), primary_key=True)
    user_id = Column(String, ForeignKey("users.id"), primary_key=True)
    position_ms = Column(BigInteger, default=0, nullable=False)
    is_buffering = Column(Boolean, default=False, nullable=False)
    camera_on = Column(Boolean, default=False, nullable=False)
    mic_on = Column(Boolean, default=False, nullable=False)
    quality_level = Column(String, nullable=True)
    last_seen = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    room = relationship("WatchRoom", back_populates="viewer_states")
    user = relationship("User")


# ── 12. watch_events ───────────────────────────────────────────────────────
class WatchEvent(Base):
    """
    Play, pause, seek, and buffer diagnostic logs.
    """
    __tablename__ = "watch_events"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    room_id = Column(String, ForeignKey("watch_rooms.id"), nullable=False)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    event_type = Column(String, nullable=False)  # play, pause, seek, buffer
    position_ms = Column(BigInteger, default=0, nullable=False)
    payload = Column(_JSON, nullable=True, default=dict)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    room = relationship("WatchRoom", back_populates="watch_events")
    user = relationship("User")


# ── 13. chat_messages ──────────────────────────────────────────────────────
class ChatMessage(Base):
    """
    Contextual room chat overlay sync messages.
    """
    __tablename__ = "chat_messages"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    room_id = Column(String, ForeignKey("watch_rooms.id"), nullable=False)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    body = Column(String, nullable=False)
    msg_type = Column(String, default="text", nullable=False)
    at_position_ms = Column(BigInteger, default=0, nullable=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    room = relationship("WatchRoom", back_populates="chat_messages")
    user = relationship("User", back_populates="chat_messages")


# ── 14. report_flags ───────────────────────────────────────────────────────
class ReportFlag(Base):
    """
    Content moderation and block flags.
    """
    __tablename__ = "report_flags"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    reported_by = Column(String, ForeignKey("users.id"), nullable=False)
    entity_type = Column(String, nullable=False)  # chat_message | movie
    entity_id = Column(String, nullable=False)
    reason = Column(String, nullable=False)
    status = Column(String, default="pending", nullable=False)  # pending, resolved, dismissed
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    reporter = relationship("User")


# ── 15. session_analytics ──────────────────────────────────────────────────
class SessionAnalytics(Base):
    """
    Post-round room engagement analytics.
    """
    __tablename__ = "session_analytics"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = Column(String, ForeignKey("sessions.id"), unique=True, nullable=False)
    total_members = Column(Integer, default=0, nullable=False)
    movies_swiped = Column(Integer, default=0, nullable=False)
    total_votes = Column(Integer, default=0, nullable=False)
    match_round = Column(Integer, default=0, nullable=False)
    time_to_match_s = Column(Integer, default=0, nullable=False)
    had_unanimous = Column(Boolean, default=False, nullable=False)
    completed_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    session = relationship("Session", back_populates="analytics")


# ── Helpers ──────────────────────────────────────────────────────────────────
def init_db() -> None:
    """Create all tables (idempotent) and run DDL migrations."""
    Base.metadata.create_all(bind=engine)
    
    # Run safe ALTER column migrations for backwards compatibility
    from sqlalchemy import text
    
    migrations = [
        # (table, column, ddl_type)
        ("users", "notified", "BOOLEAN DEFAULT FALSE NOT NULL"),
        ("users", "email", "VARCHAR NULL"),
        ("users", "timezone", "VARCHAR NULL"),
        ("users", "region_code", "VARCHAR NULL"),
        ("users", "language_code", "VARCHAR NULL"),
        ("users", "watch_history_vector", "TEXT NULL"),
        ("users", "is_guest", "BOOLEAN DEFAULT TRUE NOT NULL"),
        ("users", "deleted_at", "TIMESTAMP NULL"),
        ("users", "updated_at", "TIMESTAMP NULL"),
        
        ("sessions", "invite_link_token", "VARCHAR NULL"),
        ("sessions", "max_members", "INTEGER DEFAULT 10 NOT NULL"),
        ("sessions", "require_auth", "BOOLEAN DEFAULT FALSE NOT NULL"),
        ("sessions", "deleted_at", "TIMESTAMP NULL"),
        ("sessions", "updated_at", "TIMESTAMP NULL"),
        ("sessions", "created_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL"),
        
        ("session_members", "is_host", "BOOLEAN DEFAULT FALSE NOT NULL"),
        ("session_members", "left_at", "TIMESTAMP NULL"),
        
        ("votes", "swipe_duration_ms", "INTEGER NULL"),
        
        ("matches", "total_voters", "INTEGER DEFAULT 0 NOT NULL"),
        
        ("movie_decks", "created_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL"),
        
        # movies table migrations
        ("movies", "uploaded_by", "VARCHAR NULL"),
        ("movies", "duration_ms", "BIGINT NULL"),
        ("movies", "hls_master_url", "VARCHAR NULL"),
        ("movies", "thumbnail_url", "VARCHAR NULL"),
        ("movies", "file_size_mb", "INTEGER NULL"),
        ("movies", "quality_levels", "JSONB NULL"),
        ("movies", "subtitle_tracks", "VARCHAR NULL"),
        ("movies", "content_hash", "VARCHAR NULL"),
        ("movies", "rights_confirmed", "BOOLEAN DEFAULT FALSE NOT NULL"),
        ("movies", "deleted_at", "TIMESTAMP NULL"),
        ("movies", "updated_at", "TIMESTAMP NULL"),
        
        # watch_rooms table migrations
        ("watch_rooms", "viewer_count", "INTEGER DEFAULT 0 NOT NULL"),
        ("watch_rooms", "livekit_room_name", "VARCHAR NULL"),
        ("watch_rooms", "chat_channel_id", "VARCHAR NULL"),
        ("watch_rooms", "allow_camera", "BOOLEAN DEFAULT TRUE NOT NULL"),
        ("watch_rooms", "allow_chat", "BOOLEAN DEFAULT TRUE NOT NULL"),
        ("watch_rooms", "ended_at", "TIMESTAMP NULL"),
        ("watch_rooms", "created_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL"),
        ("watch_rooms", "updated_at", "TIMESTAMP NULL"),
    ]
    
    for table, col, ddl_type in migrations:
        db = SessionLocal()
        try:
            # Check column existence by querying it
            db.execute(text(f"SELECT {col} FROM {table} LIMIT 1"))
            db.rollback()  # Close the transaction immediately
        except Exception:
            db.rollback()
            try:
                db.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl_type}"))
                db.commit()
                print(f"Successfully added column '{col}' to table '{table}' via migration.")
            except Exception as e:
                print(f"Failed to add column '{col}' to table '{table}': {e}")
                db.rollback()
        finally:
            db.close()


def get_db():
    """FastAPI dependency — yields a DB session and closes it afterward."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
