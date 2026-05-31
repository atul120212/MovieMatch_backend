"""
main.py — FastAPI application entry point.

API surface
───────────
GET  /health
POST /api/rooms                    → create room + host user
GET  /api/rooms/{code}             → room detail + members
POST /api/rooms/{code}/join        → join as guest
GET  /api/rooms/{code}/recs        → movie deck (cached)
POST /api/rooms/{code}/start       → transition waiting → swiping
POST /api/rooms/{code}/vote        → record a vote
GET  /api/rooms/{code}/matches     → computed match results
WS   /ws/{code}/{user_id}          → real-time room channel
"""

import asyncio
import json
import os
import random
import string
import shutil
import subprocess
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect, UploadFile, File, Form, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session as DBSession

from .database import (
    Match,
    MovieDeck,
    Session,
    SessionMember,
    User,
    Vote,
    Movie,
    WatchRoom,
    get_db,
    init_db,
    SessionLocal,
)
from .recs import get_recommendations
from .schemas import (
    CreateRoomRequest,
    CreateRoomResponse,
    JoinRoomRequest,
    JoinRoomResponse,
    MatchOut,
    MatchesResponse,
    MovieCardOut,
    RoomDetailResponse,
    StartResponse,
    VoteRequest,
    VoteResponse,
    HealthResponse,
    MemberOut,
    UpdateGenresRequest,
    MovieUploadResponse,
    WatchRoomStatusResponse,
)

load_dotenv(override=True)

# ── App setup ────────────────────────────────────────────────────────────────

app = FastAPI(
    title="MovieMatch API",
    description="Real-time group movie matching backend",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Define and mount static watch directory for streaming media
watch_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "watch"))
try:
    os.makedirs(watch_dir, exist_ok=True)
except OSError:
    # Fallback to system temp directory (e.g., /tmp on Vercel serverless read-only filesystems)
    import tempfile
    watch_dir = os.path.join(tempfile.gettempdir(), "moviematch_watch")
    os.makedirs(watch_dir, exist_ok=True)

app.mount("/watch", StaticFiles(directory=watch_dir), name="watch")


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    t = threading.Thread(target=cleanup_expired_movies_loop, daemon=True)
    t.start()


# ── Room code generator ───────────────────────────────────────────────────────

def _generate_code(db: DBSession) -> str:
    """Generate a unique 6-char code like LION42 (4 letters + 2 digits)."""
    for _ in range(20):
        code = (
            "".join(random.choices(string.ascii_uppercase, k=4))
            + "".join(random.choices(string.digits, k=2))
        )
        if not db.query(Session).filter(Session.room_code == code).first():
            return code
    raise RuntimeError("Could not generate a unique room code")


# ── WebSocket connection manager ──────────────────────────────────────────────

class ConnectionManager:
    """Keeps track of active WebSocket connections keyed by room code."""

    def __init__(self) -> None:
        # room_code → list of {"user_id": str, "ws": WebSocket}
        self._rooms: Dict[str, List[Dict[str, Any]]] = {}

    async def connect(self, ws: WebSocket, code: str, user_id: str) -> None:
        await ws.accept()
        self._rooms.setdefault(code, []).append({"user_id": user_id, "ws": ws})

    def disconnect(self, ws: WebSocket, code: str) -> None:
        if code in self._rooms:
            self._rooms[code] = [c for c in self._rooms[code] if c["ws"] is not ws]
            if not self._rooms[code]:
                del self._rooms[code]

    async def broadcast(self, code: str, data: Dict[str, Any]) -> None:
        for conn in self._rooms.get(code, []):
            try:
                await conn["ws"].send_json(data)
            except Exception:
                pass

    async def send_to_user(self, code: str, user_id: str, data: Dict[str, Any]) -> bool:
        for conn in self._rooms.get(code, []):
            if conn["user_id"] == user_id:
                try:
                    await conn["ws"].send_json(data)
                    return True
                except Exception:
                    pass
        return False


manager = ConnectionManager()


# ── Helper: build members list ───────────────────────────────────────────────

def _members(session: Session) -> List[Dict[str, str]]:
    return [
        {"id": m.user_id, "name": m.user.display_name}
        for m in session.members
    ]


# ── Helper: build movie card out from MovieDeck row ──────────────────────────

def _card_out(deck_row: MovieDeck) -> Dict[str, Any]:
    meta: Dict[str, Any] = deck_row.movie_metadata or {}
    return {
        "tmdb_id": deck_row.tmdb_id,
        "position": deck_row.position,
        "title": meta.get("title", "Unknown"),
        "overview": meta.get("overview"),
        "poster_path": meta.get("poster_path"),
        "genres": meta.get("genres"),
        "rating": meta.get("rating"),
        "runtime": meta.get("runtime"),
        "streaming_info": meta.get("streaming_info"),
    }


# ── Helper: tally votes and write/update matches table ───────────────────────

def _tally_and_save_matches(
    session: Session, db: DBSession
) -> Dict[str, Any]:
    """
    Count yes votes per tmdb_id, determine unanimous hits,
    persist rows to the matches table, and return the payload.
    """
    total_members = len(session.members)
    deck = {d.tmdb_id: d for d in session.movie_deck}

    # Count yes votes per movie
    yes_counts: Dict[str, int] = {}
    for vote in session.votes:
        if vote.choice:  # True = yes
            yes_counts[vote.tmdb_id] = yes_counts.get(vote.tmdb_id, 0) + 1

    # Delete old match rows for this session (re-tally is idempotent)
    db.query(Match).filter(Match.session_id == session.id).delete()

    match_rows: List[Dict[str, Any]] = []
    for tmdb_id, yes_count in yes_counts.items():
        if tmdb_id not in deck:
            continue
        is_unanimous = yes_count == total_members
        match = Match(
            session_id=session.id,
            tmdb_id=tmdb_id,
            yes_count=yes_count,
            unanimous=is_unanimous,
            matched_at=datetime.utcnow(),
        )
        db.add(match)

        meta = deck[tmdb_id].movie_metadata or {}
        match_rows.append(
            {
                "tmdb_id": tmdb_id,
                "title": meta.get("title", "Unknown"),
                "overview": meta.get("overview"),
                "poster_path": meta.get("poster_path"),
                "genres": meta.get("genres"),
                "rating": meta.get("rating"),
                "runtime": meta.get("runtime"),
                "streaming_info": meta.get("streaming_info"),
                "yes_count": yes_count,
                "is_unanimous": is_unanimous,
            }
        )

    db.commit()

    # Sort: unanimous first, then by yes_count desc, then rating desc
    match_rows.sort(
        key=lambda x: (x["is_unanimous"], x["yes_count"], x.get("rating") or 0),
        reverse=True,
    )

    return {"total_members": total_members, "matches": match_rows}


# ── REST endpoints ────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse)
def health_check() -> HealthResponse:
    return HealthResponse(status="ok")


@app.post("/api/rooms", response_model=CreateRoomResponse)
def create_room(
    body: CreateRoomRequest, db: DBSession = Depends(get_db)
) -> CreateRoomResponse:
    """Create a new session and register the host as a user + member."""
    code = _generate_code(db)

    # Create host user
    host = User(display_name=body.host_name)
    db.add(host)
    db.flush()  # get host.id without committing

    # Create session
    session = Session(
        room_code=code,
        group_type=body.group_type,
        host_id=host.id,
        state="waiting",
    )
    db.add(session)
    db.flush()

    # Add host as first member
    member = SessionMember(session_id=session.id, user_id=host.id)
    db.add(member)
    db.commit()

    return CreateRoomResponse(
        session_id=session.id,
        room_code=session.room_code,
        group_type=session.group_type,
        state=session.state,
        user_id=host.id,
        user_name=host.display_name,
    )


@app.get("/api/rooms/{code}", response_model=RoomDetailResponse)
def get_room(code: str, db: DBSession = Depends(get_db)) -> RoomDetailResponse:
    session = db.query(Session).filter(Session.room_code == code.upper()).first()
    if not session:
        raise HTTPException(status_code=404, detail="Room not found")

    voted_counts: Dict[str, int] = {}
    if session.state in ("swiping", "revealed"):
        for vote in session.votes:
            voted_counts[vote.user_id] = voted_counts.get(vote.user_id, 0) + 1

    return RoomDetailResponse(
        session_id=session.id,
        room_code=session.room_code,
        group_type=session.group_type,
        state=session.state,
        members=[
            MemberOut(
                id=m.user_id,
                name=m.user.display_name,
                voted_count=voted_counts.get(m.user_id, 0) if session.state in ("swiping", "revealed") else None,
                genres=list(m.user.genre_prefs.keys()) if m.user and m.user.genre_prefs else [],
                notified=m.user.notified
            )
            for m in session.members
        ],
    )


@app.post("/api/rooms/{code}/join", response_model=JoinRoomResponse)
def join_room(
    code: str, body: JoinRoomRequest, db: DBSession = Depends(get_db)
) -> JoinRoomResponse:
    session = db.query(Session).filter(Session.room_code == code.upper()).first()
    if not session:
        raise HTTPException(status_code=404, detail="Room not found")


    # Create guest user
    user = User(display_name=body.name)
    db.add(user)
    db.flush()

    # Add as member
    member = SessionMember(session_id=session.id, user_id=user.id)
    db.add(member)
    db.commit()

    return JoinRoomResponse(
        session_id=session.id,
        room_code=session.room_code,
        state=session.state,
        user_id=user.id,
        user_name=user.display_name,
    )


@app.put("/api/users/{user_id}/genres")
async def update_user_genres(
    user_id: str, body: UpdateGenresRequest, db: DBSession = Depends(get_db)
):
    """Update a user's selected genre preferences."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    genre_prefs = {genre: 1.0 for genre in body.genres}
    user.genre_prefs = genre_prefs
    if body.genres:
        user.notified = False
    db.commit()

    # Broadcast updated participant list to the room
    member = db.query(SessionMember).filter(SessionMember.user_id == user_id).first()
    if member and member.session:
        await manager.broadcast(
            member.session.room_code,
            {
                "event": "participant_joined",
                "participants": [
                    {
                        "id": sm.user_id,
                        "name": sm.user.display_name,
                        "genres": list(sm.user.genre_prefs.keys()) if sm.user and sm.user.genre_prefs else [],
                        "notified": sm.user.notified
                    }
                    for sm in member.session.members
                ]
            }
        )

    return {"status": "ok", "genres": body.genres}


@app.post("/api/users/{user_id}/notify")
def notify_user(
    user_id: str, db: DBSession = Depends(get_db)
):
    """Host notifies player to select genres."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.notified = True
    db.commit()
    return {"status": "ok", "notified": True}


async def _ensure_deck(session: Session, db: DBSession) -> List[MovieDeck]:
    """Build the movie deck for a session on first call; return cached rows after. Deduplicates dynamically."""
    deck = (
        db.query(MovieDeck)
        .filter(MovieDeck.session_id == session.id)
        .order_by(MovieDeck.position)
        .all()
    )
    if not deck:
        member_prefs = []
        for m in session.members:
            if m.user and m.user.genre_prefs:
                member_prefs.append(m.user.genre_prefs)
        movies = await get_recommendations(session.group_type, member_prefs)
        
        # Concurrency check: check if another thread/worker generated the deck while we were fetching recommendations
        deck_check = (
            db.query(MovieDeck)
            .filter(MovieDeck.session_id == session.id)
            .order_by(MovieDeck.position)
            .all()
        )
        if deck_check:
            return deck_check

        # Deduplicate movies list by tmdb_id to guarantee no duplicate movies in the session deck
        seen_ids = set()
        unique_movies = []
        for m in movies:
            m_id = str(m["id"])
            if m_id not in seen_ids:
                seen_ids.add(m_id)
                unique_movies.append(m)

        deck = []
        for idx, m in enumerate(unique_movies):
            row = MovieDeck(
                session_id=session.id,
                tmdb_id=str(m["id"]),
                position=idx,
                movie_metadata={
                    "title": m["title"],
                    "overview": m.get("overview"),
                    "poster_path": m.get("poster_path"),
                    "genres": m.get("genres"),
                    "rating": m.get("rating"),
                    "runtime": m.get("runtime"),
                    "streaming_info": m.get("streaming_info"),
                },
            )
            db.add(row)
            deck.append(row)
        try:
            db.commit()
        except Exception:
            db.rollback()
            # If commit fails (e.g. concurrency unique violation), return the existing deck generated by the other request
            deck = (
                db.query(MovieDeck)
                .filter(MovieDeck.session_id == session.id)
                .order_by(MovieDeck.position)
                .all()
            )
    return deck


@app.get("/api/rooms/{code}/recs")
async def get_recs(
    code: str, db: DBSession = Depends(get_db)
) -> List[Dict[str, Any]]:
    """Return the session's movie deck (generated on first call)."""
    session = db.query(Session).filter(Session.room_code == code.upper()).first()
    if not session:
        raise HTTPException(status_code=404, detail="Room not found")
    deck = await _ensure_deck(session, db)
    return [_card_out(row) for row in deck]


@app.post("/api/rooms/{code}/start", response_model=StartResponse)
async def start_session(
    code: str, db: DBSession = Depends(get_db)
) -> StartResponse:
    """Host triggers this to move the room from waiting → swiping."""
    session = db.query(Session).filter(Session.room_code == code.upper()).first()
    if not session:
        raise HTTPException(status_code=404, detail="Room not found")
    if session.state != "waiting":
        raise HTTPException(status_code=400, detail="Session is not in waiting state")

    session.state = "swiping"
    db.commit()

    # Pre-generate deck so all clients can fetch immediately
    await _ensure_deck(session, db)

    await manager.broadcast(
        session.room_code, {"event": "state_changed", "state": "swiping"}
    )
    return StartResponse(status="started", state="swiping")


@app.post("/api/rooms/{code}/vote", response_model=VoteResponse)
async def register_vote(
    code: str, body: VoteRequest, db: DBSession = Depends(get_db)
) -> VoteResponse:
    session = db.query(Session).filter(Session.room_code == code.upper()).first()
    if not session:
        raise HTTPException(status_code=404, detail="Room not found")

    # Validate user is a member
    membership = (
        db.query(SessionMember)
        .filter(
            SessionMember.session_id == session.id,
            SessionMember.user_id == body.user_id,
        )
        .first()
    )
    if not membership:
        raise HTTPException(status_code=403, detail="User is not a member of this session")

    # Validate movie is in session deck
    in_deck = (
        db.query(MovieDeck)
        .filter(
            MovieDeck.session_id == session.id,
            MovieDeck.tmdb_id == body.tmdb_id,
        )
        .first()
    )
    if not in_deck:
        raise HTTPException(status_code=400, detail="Movie is not in this session's deck")

    # Upsert vote
    existing = (
        db.query(Vote)
        .filter(
            Vote.session_id == session.id,
            Vote.user_id == body.user_id,
            Vote.tmdb_id == body.tmdb_id,
        )
        .first()
    )
    if existing:
        existing.choice = body.choice
        existing.voted_at = datetime.utcnow()
    else:
        db.add(
            Vote(
                session_id=session.id,
                user_id=body.user_id,
                tmdb_id=body.tmdb_id,
                choice=body.choice,
            )
        )
    db.commit()

    # Progress: how many movies has this user voted on?
    total_in_deck = (
        db.query(MovieDeck).filter(MovieDeck.session_id == session.id).count()
    )
    user_vote_count = (
        db.query(Vote)
        .filter(Vote.session_id == session.id, Vote.user_id == body.user_id)
        .count()
    )

    user_obj = db.query(User).filter(User.id == body.user_id).first()
    await manager.broadcast(
        session.room_code,
        {
            "event": "vote_progress",
            "user_id": body.user_id,
            "user_name": user_obj.display_name if user_obj else "Unknown",
            "voted_count": user_vote_count,
            "total_count": total_in_deck,
        },
    )

    # Check for reveal criteria:
    # 1. 3 unanimous matches -> reveal immediately
    # 2. 2 unanimous matches after 15 choices -> reveal
    # 3. 1 unanimous match after 10 choices -> reveal
    # 4. End of deck (20 choices) -> reveal
    total_members = len(session.members)
    
    # Get all votes for this session
    all_votes = db.query(Vote).filter(Vote.session_id == session.id).all()
    
    # Group votes by tmdb_id
    votes_by_movie: Dict[str, List[Vote]] = {}
    for v in all_votes:
        votes_by_movie.setdefault(v.tmdb_id, []).append(v)
        
    # Count how many movies have been voted on by ALL members, and how many are unanimous YES
    voted_by_all_count = 0
    unanimous_match_count = 0
    for tmdb_id, votes_list in votes_by_movie.items():
        if len(votes_list) == total_members:
            voted_by_all_count += 1
            # Check if all votes in list are YES (choice == True)
            if all(v.choice for v in votes_list):
                unanimous_match_count += 1
                
    should_reveal = False
    if total_members > 0 and total_in_deck > 0:
        if unanimous_match_count >= 3:
            should_reveal = True
        elif voted_by_all_count >= 15 and unanimous_match_count >= 2:
            should_reveal = True
        elif voted_by_all_count >= 10 and unanimous_match_count >= 1:
            should_reveal = True
        elif voted_by_all_count >= total_in_deck:
            should_reveal = True

    if should_reveal:
        session.state = "revealed"
        db.commit()

        result = _tally_and_save_matches(session, db)
        await manager.broadcast(
            session.room_code,
            {"event": "state_changed", "state": "revealed", "matches": result},
        )

    return VoteResponse(status="ok")


@app.get("/api/rooms/{code}/matches", response_model=MatchesResponse)
def get_matches(
    code: str, db: DBSession = Depends(get_db)
) -> MatchesResponse:
    session = db.query(Session).filter(Session.room_code == code.upper()).first()
    if not session:
        raise HTTPException(status_code=404, detail="Room not found")

    # If matches table is empty but session is revealed, tally on-demand
    if session.state == "revealed" and not session.matches:
        result = _tally_and_save_matches(session, db)
        return MatchesResponse(**result)

    deck = {d.tmdb_id: d for d in session.movie_deck}
    match_rows: List[Dict[str, Any]] = []
    for m in session.matches:
        meta = (deck[m.tmdb_id].movie_metadata or {}) if m.tmdb_id in deck else {}
        match_rows.append(
            {
                "tmdb_id": m.tmdb_id,
                "title": meta.get("title", "Unknown"),
                "overview": meta.get("overview"),
                "poster_path": meta.get("poster_path"),
                "genres": meta.get("genres"),
                "rating": meta.get("rating"),
                "runtime": meta.get("runtime"),
                "streaming_info": meta.get("streaming_info"),
                "yes_count": m.yes_count,
                "is_unanimous": m.unanimous,
            }
        )

    match_rows.sort(
        key=lambda x: (x["is_unanimous"], x["yes_count"], x.get("rating") or 0),
        reverse=True,
    )

    return MatchesResponse(
        total_members=len(session.members), matches=match_rows
    )


@app.post("/api/rooms/{code}/reveal", response_model=MatchesResponse)
async def force_reveal(
    code: str, db: DBSession = Depends(get_db)
) -> MatchesResponse:
    """Force reveal matches based on current votes."""
    session = db.query(Session).filter(Session.room_code == code.upper()).first()
    if not session:
        raise HTTPException(status_code=404, detail="Room not found")
    if session.state != "swiping":
        raise HTTPException(status_code=400, detail="Session is not in swiping state")

    session.state = "revealed"
    db.commit()

    result = _tally_and_save_matches(session, db)
    await manager.broadcast(
        session.room_code,
        {"event": "state_changed", "state": "revealed", "matches": result},
    )
    return MatchesResponse(**result)


# ── WebSocket endpoint ────────────────────────────────────────────────────────

@app.websocket("/ws/{code}/{user_id}")
async def ws_endpoint(
    ws: WebSocket, code: str, user_id: str
) -> None:
    code_upper = code.upper()
    with SessionLocal() as db:
        session = db.query(Session).filter(Session.room_code == code_upper).first()
        if not session:
            await ws.close(code=4004)
            return

        member = (
            db.query(SessionMember)
            .filter(
                SessionMember.session_id == session.id,
                SessionMember.user_id == user_id,
            )
            .first()
        )
        if not member:
            await ws.close(code=4003)
            return

        participants = [
            {
                "id": m.user_id,
                "name": m.user.display_name,
                "genres": list(m.user.genre_prefs.keys()) if m.user and m.user.genre_prefs else [],
                "notified": m.user.notified
            }
            for m in session.members
        ]

    await manager.connect(ws, code_upper, user_id)

    # Send current member list to everyone in the room
    await manager.broadcast(
        code_upper,
        {
            "event": "participant_joined",
            "participants": participants,
        },
    )

    try:
        while True:
            raw = await ws.receive_text()
            try:
                payload = json.loads(raw)
                if payload.get("type") == "ping":
                    await ws.send_json({"type": "pong"})
                    continue
                
                event_type = payload.get("event")
                if event_type:
                    # Update database playback states on sync actions
                    if event_type in ("video_play", "video_pause", "video_seek", "video_heartbeat"):
                        with SessionLocal() as db:
                            session = db.query(Session).filter(Session.room_code == code_upper).first()
                            if session and session.watch_room:
                                wr = session.watch_room
                                if event_type == "video_play":
                                    wr.state = "playing"
                                elif event_type == "video_pause":
                                    wr.state = "paused"
                                
                                if "time" in payload:
                                    wr.position_ms = int(payload["time"] * 1000)
                                db.commit()
                    
                    # Handle WebRTC signaling targeting specific peers
                    if event_type == "webrtc_signal":
                        target_id = payload.get("target_id")
                        if target_id:
                            await manager.send_to_user(code_upper, target_id, payload)
                    else:
                        # General broadcast for all chat, reactions, and sync events
                        await manager.broadcast(code_upper, payload)
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        manager.disconnect(ws, code_upper)
        with SessionLocal() as db:
            session = db.query(Session).filter(Session.room_code == code_upper).first()
            if session:
                participants = [
                    {
                        "id": m.user_id,
                        "name": m.user.display_name,
                        "genres": list(m.user.genre_prefs.keys()) if m.user and m.user.genre_prefs else [],
                        "notified": m.user.notified
                    }
                    for m in session.members
                ]
            else:
                participants = []
        await manager.broadcast(
            code_upper,
            {
                "event": "participant_left",
                "user_id": user_id,
                "participants": participants,
            },
        )


# ── Co-Watching Subsystems ───────────────────────────────────────────────────

def asyncio_run(coro):
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
    if loop.is_running():
        asyncio.run_coroutine_threadsafe(coro, loop)
    else:
        loop.run_until_complete(coro)


def transcode_movie(room_code: str, movie_id: str, input_path: str, output_dir: str):
    db = SessionLocal()
    movie = db.query(Movie).filter(Movie.id == movie_id).first()
    if not movie:
        db.close()
        return

    try:
        os.makedirs(output_dir, exist_ok=True)
        ffmpeg_bin = shutil.which("ffmpeg")

        if not ffmpeg_bin:
            # Fallback 1: No FFmpeg installed -> Copy MP4 directly
            print("FFmpeg not found. Falling back to direct MP4 streaming.")
            dest_path = os.path.join(output_dir, "movie.mp4")
            shutil.copy2(input_path, dest_path)
            
            # Update database
            movie.stream_url = f"/watch/{movie_id}/movie.mp4"
            movie.status = "ready"
            movie.progress = 100
            db.commit()
            
            # Broadcast ready state
            asyncio_run(manager.broadcast(room_code, {
                "event": "movie_status",
                "movie_id": movie_id,
                "status": "ready",
                "progress": 100,
                "stream_url": movie.stream_url
            }))
            db.close()
            # Clean up temp file
            try:
                os.remove(input_path)
            except Exception:
                pass
            return

        # Attempt 1: Fast HLS Codec Copy
        print("FFmpeg found. Attempting fast HLS segmenting...")
        hls_index = os.path.join(output_dir, "index.m3u8")
        cmd_copy = [
            ffmpeg_bin, "-y", "-i", input_path,
            "-codec", "copy",
            "-hls_time", "10", "-hls_playlist_type", "vod",
            "-hls_segment_filename", os.path.join(output_dir, "seg%03d.ts"),
            hls_index
        ]
        
        proc = subprocess.run(cmd_copy, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if proc.returncode == 0:
            print("Fast HLS segmenting successful!")
            movie.stream_url = f"/watch/{movie_id}/index.m3u8"
            movie.status = "ready"
            movie.progress = 100
            db.commit()
            
            asyncio_run(manager.broadcast(room_code, {
                "event": "movie_status",
                "movie_id": movie_id,
                "status": "ready",
                "progress": 100,
                "stream_url": movie.stream_url
            }))
        else:
            # Fallback 2: Transcode to HLS
            print("Fast segmenting failed or incompatible codecs. Transcoding...")
            cmd_transcode = [
                ffmpeg_bin, "-y", "-i", input_path,
                "-c:v", "libx264", "-preset", "ultrafast",
                "-c:a", "aac", "-b:a", "128k",
                "-hls_time", "6", "-hls_playlist_type", "vod",
                "-hls_segment_filename", os.path.join(output_dir, "seg%03d.ts"),
                hls_index
            ]
            
            movie.status = "processing"
            movie.progress = 10
            db.commit()
            
            asyncio_run(manager.broadcast(room_code, {
                "event": "movie_status",
                "movie_id": movie_id,
                "status": "processing",
                "progress": 10
            }))
            
            proc = subprocess.run(cmd_transcode, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if proc.returncode == 0:
                print("Transcoding successful!")
                movie.stream_url = f"/watch/{movie_id}/index.m3u8"
                movie.status = "ready"
                movie.progress = 100
                db.commit()
                
                asyncio_run(manager.broadcast(room_code, {
                    "event": "movie_status",
                    "movie_id": movie_id,
                    "status": "ready",
                    "progress": 100,
                    "stream_url": movie.stream_url
                }))
            else:
                print("Transcoding failed. Falling back to direct MP4 copy.")
                dest_path = os.path.join(output_dir, "movie.mp4")
                shutil.copy2(input_path, dest_path)
                
                movie.stream_url = f"/watch/{movie_id}/movie.mp4"
                movie.status = "ready"
                movie.progress = 100
                db.commit()
                
                asyncio_run(manager.broadcast(room_code, {
                    "event": "movie_status",
                    "movie_id": movie_id,
                    "status": "ready",
                    "progress": 100,
                    "stream_url": movie.stream_url
                }))

    except Exception as e:
        print(f"Exception during transcode: {e}")
        movie.status = "error"
        db.commit()
        asyncio_run(manager.broadcast(room_code, {
            "event": "movie_status",
            "movie_id": movie_id,
            "status": "error"
        }))
    finally:
        db.close()
        try:
            os.remove(input_path)
        except Exception:
            pass


@app.post("/api/rooms/{code}/upload", response_model=MovieUploadResponse)
async def upload_movie(
    code: str,
    title: str = Form(...),
    host_id: str = Form(...),
    file: UploadFile = File(...),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    db: DBSession = Depends(get_db)
) -> MovieUploadResponse:
    code_upper = code.upper()
    session = db.query(Session).filter(Session.room_code == code_upper).first()
    if not session:
        raise HTTPException(status_code=404, detail="Room not found")

    # Create movie record
    movie = Movie(title=title)
    db.add(movie)
    db.flush()

    # Create watch room (or update existing)
    wr = db.query(WatchRoom).filter(WatchRoom.session_id == session.id).first()
    if not wr:
        wr = WatchRoom(session_id=session.id, movie_id=movie.id, host_id=host_id, state="paused")
        db.add(wr)
    else:
        wr.movie_id = movie.id
        wr.host_id = host_id
        wr.state = "paused"
        wr.position_ms = 0
    
    # Change session state to streaming
    session.state = "streaming"
    db.commit()

    # Save temp uploaded file
    temp_dir = os.path.join(os.path.dirname(__file__), "..", "temp")
    os.makedirs(temp_dir, exist_ok=True)
    temp_file_path = os.path.join(temp_dir, f"upload_{movie.id}.mp4")
    
    with open(temp_file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    # Save transcoded files to the backend's own watch directory
    output_dir = os.path.join(watch_dir, movie.id)
    
    # Run FFmpeg transcode as a background task
    background_tasks.add_task(transcode_movie, code_upper, movie.id, temp_file_path, output_dir)

    # Broadcast that the session is now streaming!
    await manager.broadcast(code_upper, {
        "event": "state_changed",
        "state": "streaming",
        "movie_id": movie.id,
        "movie_title": title
    })

    return MovieUploadResponse(
        movie_id=movie.id,
        title=title,
        status=movie.status,
        progress=movie.progress
    )


@app.get("/api/rooms/{code}/watch_status", response_model=WatchRoomStatusResponse)
def get_watch_status(code: str, db: DBSession = Depends(get_db)) -> WatchRoomStatusResponse:
    code_upper = code.upper()
    session = db.query(Session).filter(Session.room_code == code_upper).first()
    if not session:
        raise HTTPException(status_code=404, detail="Room not found")

    wr = db.query(WatchRoom).filter(WatchRoom.session_id == session.id).first()
    if not wr:
        # Create a default watch room if state is streaming
        if session.state == "streaming":
            wr = WatchRoom(session_id=session.id, host_id=session.host_id, state="paused")
            db.add(wr)
            db.commit()
        else:
            raise HTTPException(status_code=400, detail="Watch Room is not active yet")

    return WatchRoomStatusResponse(
        room_id=wr.id,
        state=wr.state,
        position_ms=wr.position_ms,
        movie_id=wr.movie_id,
        movie_title=wr.movie.title if wr.movie else None,
        stream_url=wr.movie.stream_url if wr.movie else None,
        transcode_status=wr.movie.status if wr.movie else None,
        transcode_progress=wr.movie.progress if wr.movie else None
    )


def cleanup_expired_movies():
    cutoff = datetime.utcnow() - timedelta(hours=24)
    with SessionLocal() as db:
        expired_movies = db.query(Movie).filter(Movie.created_at < cutoff).all()
        if not expired_movies:
            return

        for movie in expired_movies:
            print(f"Cleaning up expired movie: {movie.title} (ID: {movie.id})")
            output_dir = os.path.join(watch_dir, movie.id)
            if os.path.exists(output_dir):
                try:
                    shutil.rmtree(output_dir)
                    print(f"Deleted directory: {output_dir}")
                except Exception as e:
                    print(f"Error deleting directory {output_dir}: {e}")
            db.delete(movie)
        db.commit()


def cleanup_expired_movies_loop():
    # Give the app some time to boot before first check
    time.sleep(10)
    while True:
        try:
            cleanup_expired_movies()
        except Exception as e:
            print(f"Error during expired movies cleanup: {e}")
        time.sleep(3600)  # Check every hour
