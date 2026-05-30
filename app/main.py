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

import json
import os
import random
import string
from datetime import datetime
from typing import Any, Dict, List

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session as DBSession

from .database import (
    Match,
    MovieDeck,
    Session,
    SessionMember,
    User,
    Vote,
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
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup() -> None:
    init_db()


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
                genres=list(m.user.genre_prefs.keys()) if m.user and m.user.genre_prefs else []
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
    if session.state != "waiting":
        raise HTTPException(
            status_code=400, detail="Session already started — cannot join now."
        )

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
                        "genres": list(sm.user.genre_prefs.keys()) if sm.user and sm.user.genre_prefs else []
                    }
                    for sm in member.session.members
                ]
            }
        )

    return {"status": "ok", "genres": body.genres}


async def _ensure_deck(session: Session, db: DBSession) -> List[MovieDeck]:
    """Build the movie deck for a session on first call; return cached rows after."""
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
        deck = []
        for idx, m in enumerate(movies):
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
        db.commit()
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
                "genres": list(m.user.genre_prefs.keys()) if m.user and m.user.genre_prefs else []
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
                        "genres": list(m.user.genre_prefs.keys()) if m.user and m.user.genre_prefs else []
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
