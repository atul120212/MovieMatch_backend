"""
schemas.py — Pydantic v2 request / response models for all API endpoints.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ── Request bodies ───────────────────────────────────────────────────────────

class CreateRoomRequest(BaseModel):
    host_name: str = Field(..., min_length=1, max_length=64)
    group_type: str = Field("friends", pattern="^(couple|family|friends|coworkers)$")


class JoinRoomRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)


class VoteRequest(BaseModel):
    user_id: str
    tmdb_id: str
    choice: bool  # True = yes / like, False = no / dislike


class UpdateGenresRequest(BaseModel):
    genres: List[str]


# ── Shared sub-models ────────────────────────────────────────────────────────

class MemberOut(BaseModel):
    id: str
    name: str
    voted_count: Optional[int] = None
    genres: Optional[List[str]] = None


class MovieCardOut(BaseModel):
    tmdb_id: str
    position: int
    # Flattened from the metadata JSON blob for easy consumption
    title: str
    overview: Optional[str] = None
    poster_path: Optional[str] = None
    genres: Optional[str] = None
    rating: Optional[float] = None
    runtime: Optional[int] = None
    streaming_info: Optional[str] = None


class MatchOut(BaseModel):
    tmdb_id: str
    title: str
    overview: Optional[str] = None
    poster_path: Optional[str] = None
    genres: Optional[str] = None
    rating: Optional[float] = None
    runtime: Optional[int] = None
    streaming_info: Optional[str] = None
    yes_count: int
    is_unanimous: bool


# ── Response bodies ──────────────────────────────────────────────────────────

class CreateRoomResponse(BaseModel):
    session_id: str
    room_code: str
    group_type: str
    state: str
    user_id: str
    user_name: str


class JoinRoomResponse(BaseModel):
    session_id: str
    room_code: str
    state: str
    user_id: str
    user_name: str


class RoomDetailResponse(BaseModel):
    session_id: str
    room_code: str
    group_type: str
    state: str
    members: List[MemberOut]


class StartResponse(BaseModel):
    status: str
    state: str


class VoteResponse(BaseModel):
    status: str


class MatchesResponse(BaseModel):
    total_members: int
    matches: List[MatchOut]


class HealthResponse(BaseModel):
    status: str
    version: str = "1.0.0"
