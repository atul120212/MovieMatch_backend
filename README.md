# MovieMatch 🍿 — FastAPI Backend

This is the high-performance Python backend for MovieMatch, built on top of FastAPI, SQLAlchemy, and WebSockets. It acts as the orchestrator for rooms, compiles dynamic member recommendations, manages voting state machines, and broadcasts real-time updates.

---

## ✨ Features

- **Robust REST API & WebSockets:** Real-time bi-directional connection management handles joining players, voting state updates, and instant synchronization.
- **SQLAlchemy DB Schema:** Multi-table relational schema supporting anonymous users, rooms, matching memberships, swiping decks, individual votes, and final matches. Compatible with SQLite locally and PostgreSQL/Supabase in production.
- **Hybrid Recommendation Engine:** Combines room profile baselines with average user-selected vibes (genres) using a weighted scoring formula:
  $$\text{Score} = \text{Rating} \times 1.0 + \sum (\text{Matching Genre Weight}) \times 2.5$$
- **TMDB API Integration:** Pulls dynamic movie data based on group-type age ratings and vibes. Falls back to a rich mock database of 60+ movies if no TMDB API key is provided.
- **Dynamic Reveal Thresholds:** Optimized state machine that auto-reveals results early to keep session momentum alive:
  1. **3 unanimous matches** → Reveal immediately.
  2. **2 unanimous matches** → Reveal after everyone votes on 15 movies.
  3. **1 unanimous match** → Reveal after everyone votes on 10 movies.
  4. **Fallback** → Reveal all results once the entire 20-card deck is swiped.

---

## 📂 Project Structure

```
backend/
├── app/
│   ├── database.py    # SQLAlchemy models, db engines, and session connections
│   ├── main.py        # FastAPI routing endpoints and WebSocket broadcast manager
│   ├── recs.py        # Recommendation logic and TMDB discovery queries (OR logic)
│   └── schemas.py     # Pydantic v2 request/response validation models
├── .env.example       # Documentation template for secret configurations
├── Dockerfile         # Production container configuration for deployment
├── requirements.txt   # Core Python dependencies
├── run.py             # Server runner entry point
└── smoke_test.py      # Automated integration test validating recommendations and reveal rules
```

---

## 🛠️ Getting Started

### 1. Set Up Virtual Environment
Make sure Python 3.10+ is installed, then execute:
```bash
# Create a virtual environment
python -m venv .venv

# Activate on Windows:
.venv\Scripts\activate
# Activate on Linux/macOS:
source .venv/bin/activate
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Setup Secrets
Copy `.env.example` to `.env` and fill out your configurations:
```bash
cp .env.example .env
```
Key settings:
- `DATABASE_URL`: Connection string. Defaults to a local SQLite database (`sqlite:///./moviematch.db`). Supports Supabase PostgreSQL URLs.
- `TMDB_API_KEY`: *(Optional)* Your TMDB API key to fetch real popular movies.

### 4. Run Development Server
```bash
python run.py
```
* The API will be listening at: `http://localhost:8000`
* Interactive API documentation (Swagger): `http://localhost:8000/docs`

---

## 🧪 Testing

Run the integration smoke test script (ensure the server is running on `port 8000` first):
```bash
python smoke_test.py
```
This tests:
1. Room creation and member joins.
2. User vibe selection updates.
3. API deck generation blending preferences.
4. Auto-reveal rules (votes on 10 movies and asserts automatic transition to revealed state once a match is found).

---

## 🚀 Deployment

The backend is containerized and ready for one-click deployment to **Render** or **Railway**:
- **Dockerfile:** Automates package installation, exposes port `8000`, and dynamically binds uvicorn to `${PORT}`.
- **Render Blueprint (`render.yaml`):** Located at the project root to spin up the container web service under the `free` tier.
- Make sure to set `DATABASE_URL`, `TMDB_API_KEY`, and `SESSION_EXPIRY_HOURS` as environment variables on your hosting platform dashboard.
