import os
import random
import httpx
from typing import List, Dict, Any

TMDB_API_KEY = os.getenv("TMDB_API_KEY", "")

# TMDB Genre IDs
GENRES = {
    "action": 28,
    "adventure": 12,
    "animation": 16,
    "comedy": 35,
    "crime": 80,
    "documentary": 99,
    "drama": 18,
    "family": 10751,
    "fantasy": 14,
    "history": 36,
    "horror": 27,
    "music": 10402,
    "mystery": 9648,
    "romance": 10749,
    "sci-fi": 878,
    "thriller": 53,
    "war": 10752,
    "western": 37
}

GROUP_PROFILES = {
    "couple": {
        "genres": ["romance", "drama", "thriller", "comedy"],
        "min_rating": 6.5,
        "certification_lte": None
    },
    "family": {
        "genres": ["animation", "adventure", "family", "comedy"],
        "min_rating": 6.0,
        "certification_lte": "PG-13"
    },
    "friends": {
        "genres": ["action", "comedy", "horror", "sci-fi"],
        "min_rating": 6.0,
        "certification_lte": None
    },
    "coworkers": {
        "genres": ["documentary", "drama", "comedy", "mystery"],
        "min_rating": 6.5,
        "certification_lte": None
    }
}

# Rich Mock Movie Database (50 elements) representing the different genres
MOCK_MOVIES = [
    # Couple / Romance / Drama / Thriller
    {
        "id": 101,
        "title": "La La Land",
        "overview": "Sebastian and Mia are drawn together by their common desire to do what they love. But as success mounts they are faced with decisions that begin to fray the fragile fabric of their love affair.",
        "poster_path": "https://image.tmdb.org/t/p/w500/uC6TTUh4NIw5ID2Zs5J1iI1i51e.jpg",
        "genres": "Romance, Comedy, Drama",
        "rating": 7.9,
        "runtime": 128,
        "streaming_info": "Netflix, Apple TV"
    },
    {
        "id": 102,
        "title": "Knives Out",
        "overview": "When renowned crime novelist Harlan Thrombey dies at his estate just after his 85th birthday, the inquisitive and debonair Detective Benoit Blanc is mysteriously enlisted to investigate.",
        "poster_path": "https://image.tmdb.org/t/p/w500/pNsCcHG146OhLVy458PvKAgy5tA.jpg",
        "genres": "Mystery, Comedy, Thriller",
        "rating": 7.8,
        "runtime": 130,
        "streaming_info": "Prime Video, Apple TV"
    },
    {
        "id": 103,
        "title": "About Time",
        "overview": "The night after another unsatisfactory New Year's party, Tim's father reveals that the men in his family have always had the ability to travel through time. Tim decides to make his world a better place... by getting a girlfriend.",
        "poster_path": "https://image.tmdb.org/t/p/w500/r5ssnZc7g4c8t45W06w52wNvx8X.jpg",
        "genres": "Romance, Drama, Comedy",
        "rating": 7.9,
        "runtime": 123,
        "streaming_info": "Netflix, Prime Video"
    },
    {
        "id": 104,
        "title": "The Dark Knight",
        "overview": "Batman raises the stakes in his war on crime. With the help of Lt. Jim Gordon and District Attorney Harvey Dent, Batman sets out to dismantle the remaining criminal organizations that plague the streets.",
        "poster_path": "https://image.tmdb.org/t/p/w500/qJ2tWw75e1IPVo24YuJ2nwbGLas.jpg",
        "genres": "Action, Crime, Drama, Thriller",
        "rating": 8.5,
        "runtime": 152,
        "streaming_info": "Max, Apple TV"
    },
    {
        "id": 105,
        "title": "Parasite",
        "overview": "All unemployed, Ki-taek's family takes peculiar interest in the wealthy and glamorous Parks for their livelihood until they get entangled in an unexpected incident.",
        "poster_path": "https://image.tmdb.org/t/p/w500/7IiTTjV7E9o7513u2K1u5rk5jY5.jpg",
        "genres": "Thriller, Drama, Comedy",
        "rating": 8.5,
        "runtime": 132,
        "streaming_info": "Max, Apple TV"
    },
    # Family / Animation / Adventure
    {
        "id": 201,
        "title": "Spider-Man: Into the Spider-Verse",
        "overview": "Miles Morales is juggling his life between being a high school student and being Spider-Man. However, when Wilson \"Kingpin\" Fisk uses a super collider, another Spider-Man from another dimension, Peter Parker, accidentally winds up in Miles' dimension.",
        "poster_path": "https://image.tmdb.org/t/p/w500/iiZZN92e50jR96857HF8B2k4x6c.jpg",
        "genres": "Animation, Action, Adventure, Family",
        "rating": 8.4,
        "runtime": 117,
        "streaming_info": "Netflix, Disney+"
    },
    {
        "id": 202,
        "title": "Coco",
        "overview": "Despite his family’s baffling generations-old ban on music, Miguel dreams of becoming an accomplished musician like his idol, Ernesto de la Cruz. Desperate to prove his talent, Miguel finds himself in the stunning and colorful Land of the Dead following a mysterious chain of events.",
        "poster_path": "https://image.tmdb.org/t/p/w500/gGE2S023K2AQspptG24xt4VE2TC.jpg",
        "genres": "Animation, Family, Fantasy, Music",
        "rating": 8.2,
        "runtime": 105,
        "streaming_info": "Disney+"
    },
    {
        "id": 203,
        "title": "Paddington 2",
        "overview": "Paddington, now happily settled with the Brown family and a popular member of the local community, picks up a series of odd jobs to buy the perfect present for his Aunt Lucy's 100th birthday, only for the gift to be stolen.",
        "poster_path": "https://image.tmdb.org/t/p/w500/ae431S2W74447xIM72jZYyuo0Wd.jpg",
        "genres": "Family, Comedy, Adventure",
        "rating": 8.0,
        "runtime": 103,
        "streaming_info": "Netflix, Prime Video"
    },
    {
        "id": 204,
        "title": "Inside Out",
        "overview": "Growing up can be a bumpy road, and it's no exception for Riley, who is uprooted from her Midwest life when her father starts a new job in San Francisco. Riley's emotions - Joy, Fear, Anger, Disgust and Sadness - conflict on how best to navigate a new city, house and school.",
        "poster_path": "https://image.tmdb.org/t/p/w500/lRHEzqQTVHNz27iORXg5R4iTagA.jpg",
        "genres": "Animation, Family, Comedy",
        "rating": 7.9,
        "runtime": 95,
        "streaming_info": "Disney+"
    },
    {
        "id": 205,
        "title": "Ratatouille",
        "overview": "Remy, a resident of Paris, appreciates good food and has quite a sophisticated palate. He would love to become a chef so he can create and enjoy various masterpieces, but there's only one problem: Remy is a rat.",
        "poster_path": "https://image.tmdb.org/t/p/w500/t3zwwOXc427nC4K75v2o20mZ75c.jpg",
        "genres": "Animation, Comedy, Family",
        "rating": 7.8,
        "runtime": 111,
        "streaming_info": "Disney+"
    },
    # Friends / Action / Horror / Sci-Fi
    {
        "id": 301,
        "title": "Mad Max: Fury Road",
        "overview": "An apocalyptic story set in the furthest reaches of our planet, in a stark desert landscape where humanity is broken, and almost everyone is crazed fighting for the necessities of life.",
        "poster_path": "https://image.tmdb.org/t/p/w500/8tZYtuWezp8t7oQQ4056O5PYghR.jpg",
        "genres": "Action, Sci-Fi, Adventure",
        "rating": 8.1,
        "runtime": 120,
        "streaming_info": "Max, Apple TV"
    },
    {
        "id": 302,
        "title": "Get Out",
        "overview": "Chris and his girlfriend Rose go upstate to visit her parents for the weekend. At first, Chris reads the family's overly accommodating behavior as nervous attempts to deal with their daughter's interracial relationship, but as the weekend progresses, a series of increasingly disturbing discoveries lead him to a truth that he could have never imagined.",
        "poster_path": "https://image.tmdb.org/t/p/w500/1swQA4Ij6JTVuZsgiJku8Qvm97V.jpg",
        "genres": "Horror, Mystery, Thriller",
        "rating": 7.6,
        "runtime": 104,
        "streaming_info": "Netflix, Peacock"
    },
    {
        "id": 303,
        "title": "Everything Everywhere All at Once",
        "overview": "An aging Chinese immigrant is swept up in an insane adventure, where she alone can save the world by exploring other universes connecting with the lives she could have led.",
        "poster_path": "https://image.tmdb.org/t/p/w500/w34XTzFTH4U1IFvq4tZDG4RxBGe.jpg",
        "genres": "Action, Sci-Fi, Comedy, Adventure",
        "rating": 8.0,
        "runtime": 139,
        "streaming_info": "Prime Video, Apple TV"
    },
    {
        "id": 304,
        "title": "A Quiet Place",
        "overview": "A family is forced to live in silence while hiding from monsters with ultra-sensitive hearing.",
        "poster_path": "https://image.tmdb.org/t/p/w500/nAU74GvUkxhbgC6nOI1cbdkeytq.jpg",
        "genres": "Horror, Sci-Fi, Drama",
        "rating": 7.4,
        "runtime": 90,
        "streaming_info": "Paramount+, Hulu"
    },
    {
        "id": 305,
        "title": "Interstellar",
        "overview": "The adventures of a group of explorers who make use of a newly discovered wormhole to surpass the limitations on human space travel and conquer the vast distances involved in an interstellar voyage.",
        "poster_path": "https://image.tmdb.org/t/p/w500/gEU2Qv4wOhn7svv814TYjP20wCL.jpg",
        "genres": "Sci-Fi, Drama, Adventure",
        "rating": 8.4,
        "runtime": 169,
        "streaming_info": "Paramount+, Prime Video"
    },
    # Coworkers / Documentary / Mystery / Light Drama
    {
        "id": 401,
        "title": "My Octopus Teacher",
        "overview": "A filmmaker forges an unusual friendship with an octopus living in a South African kelp forest, learning as the animal shares the mysteries of her world.",
        "poster_path": "https://image.tmdb.org/t/p/w500/94ZNo68Q3a2fR7N5c9F3uO3gqKk.jpg",
        "genres": "Documentary",
        "rating": 8.1,
        "runtime": 85,
        "streaming_info": "Netflix"
    },
    {
        "id": 402,
        "title": "Free Solo",
        "overview": "Alex Honnold attempts to conquer the first free solo climb of the famed El Capitan's 3,000-foot wall in Yosemite National Park.",
        "poster_path": "https://image.tmdb.org/t/p/w500/v4QfISz42h447W6nQ261n01fG15.jpg",
        "genres": "Documentary, Adventure",
        "rating": 8.0,
        "runtime": 100,
        "streaming_info": "Disney+, Hulu"
    },
    {
        "id": 403,
        "title": "Ford v Ferrari",
        "overview": "American car designer Carroll Shelby and the British-born driver Ken Miles work together to battle corporate interference, the laws of physics, and their own personal demons to build a revolutionary race car for Ford Motor Company.",
        "poster_path": "https://image.tmdb.org/t/p/w500/2bX2vUsopmdAd6OSK23CKgQLvQY.jpg",
        "genres": "Drama, History, Action",
        "rating": 8.0,
        "runtime": 152,
        "streaming_info": "Hulu, Apple TV"
    },
    {
        "id": 404,
        "title": "The Truman Show",
        "overview": "An insurance salesman discovers his whole life is actually a reality TV show.",
        "poster_path": "https://image.tmdb.org/t/p/w500/vuM02FhTwhM9G59zF105C9V714g.jpg",
        "genres": "Drama, Comedy",
        "rating": 8.1,
        "runtime": 103,
        "streaming_info": "Paramount+, Prime Video"
    },
    {
        "id": 405,
        "title": "The Social Network",
        "overview": "Harvard student Mark Zuckerberg creates the social networking site that would become known as Facebook, but is later sued by two brothers who claimed he stole their idea, and the co-founder who was later squeezed out of the business.",
        "poster_path": "https://image.tmdb.org/t/p/w500/n0y2q4SSu34GrRZsga45Vv7nCVB.jpg",
        "genres": "Drama, History",
        "rating": 7.7,
        "runtime": 120,
        "streaming_info": "Max, Netflix"
    }
]

# Additional filler mock movies to reach 30+ items easily in case a larger deck is needed
ADDITIONAL_MOCK = [
    {"id": 501, "title": "Before Sunrise", "overview": "A young man and woman meet on a train in Europe, and wind up spending one evening together in Vienna.", "poster_path": "https://image.tmdb.org/t/p/w500/gEE2S023K2AQspptG24xt4VE2TC.jpg", "genres": "Romance, Drama", "rating": 8.1, "runtime": 105, "streaming_info": "Apple TV"},
    {"id": 502, "title": "Shutter Island", "overview": "A US Marshal investigates the disappearance of a murderer who escaped from a hospital for the criminally insane.", "poster_path": "https://image.tmdb.org/t/p/w500/kve20tXwUZ7s16521JW0gq65v2Y.jpg", "genres": "Thriller, Mystery, Drama", "rating": 8.2, "runtime": 138, "streaming_info": "Hulu, Prime Video"},
    {"id": 503, "title": "Zootopia", "overview": "In a city of anthropomorphic animals, a rookie bunny cop and a cynical con artist fox must work together to uncover a conspiracy.", "poster_path": "https://image.tmdb.org/t/p/w500/hlK0e0zQwRi3wUrGoOAelS3t3n3.jpg", "genres": "Animation, Family, Comedy, Adventure", "rating": 7.7, "runtime": 108, "streaming_info": "Disney+"},
    {"id": 504, "title": "How to Train Your Dragon", "overview": "A hapless young Viking who aspires to hunt dragons becomes the unlikely friend of a young dragon himself, and learns there may be more to the creatures than he assumed.", "poster_path": "https://image.tmdb.org/t/p/w500/zg4w45W06w52wNvx8Xr5ssnZc7g.jpg", "genres": "Animation, Family, Adventure", "rating": 7.8, "runtime": 98, "streaming_info": "Prime Video"},
    {"id": 505, "title": "Whiplash", "overview": "A promising young drummer enrolls at a cut-throat music conservatory where his dreams of greatness are mentored by an instructor who will stop at nothing to realize a student's potential.", "poster_path": "https://image.tmdb.org/t/p/w500/oRxMA02FuPLUX246mLO46Gl876s.jpg", "genres": "Drama, Music", "rating": 8.4, "runtime": 107, "streaming_info": "Netflix, Apple TV"},
    {"id": 506, "title": "The Conjuring", "overview": "Paranormal investigators Ed and Lorraine Warren work to help a family terrorized by a dark presence in their farmhouse.", "poster_path": "https://image.tmdb.org/t/p/w500/wM2wNvx8Xr5ssnZc7g4w45W06w5.jpg", "genres": "Horror, Mystery, Thriller", "rating": 7.5, "runtime": 112, "streaming_info": "Netflix, Max"},
    {"id": 507, "title": "Scott Pilgrim v the World", "overview": "In a magically realistic version of Toronto, a young man must defeat his new girlfriend's seven evil exes in order to win her heart.", "poster_path": "https://image.tmdb.org/t/p/w500/mS5ssnZc7g4w45W06w52wNvx8X.jpg", "genres": "Comedy, Action, Fantasy", "rating": 7.4, "runtime": 112, "streaming_info": "Netflix, Apple TV"},
    {"id": 508, "title": "The Matrix", "overview": "A computer hacker learns from mysterious rebels about the true nature of his reality and his role in the war against its controllers.", "poster_path": "https://image.tmdb.org/t/p/w500/f89U3wzPOmqmUBn4s64N4460yN1.jpg", "genres": "Sci-Fi, Action", "rating": 8.2, "runtime": 136, "streaming_info": "Max, Apple TV"},
    {"id": 509, "title": "The Grand Budapest Hotel", "overview": "A writer relates his adventures at a renowned European resort between the first and second World Wars.", "poster_path": "https://image.tmdb.org/t/p/w500/e65s7g4w45W06w52wNvx8Xr5ssnZ.jpg", "genres": "Comedy, Drama", "rating": 8.0, "runtime": 100, "streaming_info": "Hulu, Apple TV"},
    {"id": 510, "title": "March of the Penguins", "overview": "In the Antarctic, every march, the emperor penguins walk for miles to their nesting grounds to breed.", "poster_path": "https://image.tmdb.org/t/p/w500/wNvx8Xr5ssnZc7g4w45W06w52wv.jpg", "genres": "Documentary, Family", "rating": 7.2, "runtime": 80, "streaming_info": "Max"}
]

ALL_MOCK_POOL = MOCK_MOVIES + ADDITIONAL_MOCK

async def fetch_tmdb_movies(genre_names: List[str], certification_lte: str = None) -> List[Dict[str, Any]]:
    """
    Fetches real movies from TMDB API using Async HTTP client.
    """
    genre_ids = [GENRES[g] for g in genre_names if g in GENRES]
    genre_query = "|".join(str(gid) for gid in genre_ids)

    url = "https://api.themoviedb.org/3/discover/movie"
    params = {
        "api_key": TMDB_API_KEY,
        "with_genres": genre_query,
        "sort_by": "popularity.desc",
        "vote_average.gte": "6.0",
        "language": "en-US",
        "page": 1
    }
    
    if certification_lte:
        params["certification_country"] = "US"
        params["certification.lte"] = certification_lte

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, params=params)
            if response.status_code != 200:
                return []
            
            data = response.json()
            results = data.get("results", [])
            
            # Map genre ID list to name strings
            # Fetch genre list from TMDB for mapping
            genre_list_url = "https://api.themoviedb.org/3/genre/movie/list"
            genre_resp = await client.get(genre_list_url, params={"api_key": TMDB_API_KEY})
            genre_map = {}
            if genre_resp.status_code == 200:
                genre_map = {g["id"]: g["name"] for g in genre_resp.json().get("genres", [])}

            movies = []
            # Take top 20 movies
            for idx, r in enumerate(results[:20]):
                m_genres = [genre_map.get(gid, "Movie") for gid in r.get("genre_ids", [])]
                poster = f"https://image.tmdb.org/t/p/w500{r.get('poster_path')}" if r.get('poster_path') else None
                
                # Mock a runtime since we don't want to make 20 details requests in series (which would stall the UI)
                # This is standard practice in lightweight recommendation services.
                runtime = 100 + (r.get("id", 0) % 45)  # Deterministic mock runtime (100 - 145 min)
                
                movies.append({
                    "id": r.get("id"),
                    "title": r.get("title"),
                    "overview": r.get("overview"),
                    "poster_path": poster,
                    "genres": ", ".join(m_genres),
                    "rating": round(r.get("vote_average", 7.0), 1),
                    "runtime": runtime,
                    "streaming_info": "Netflix, Prime Video, Apple TV"  # Default mock streaming platforms
                })
            return movies
    except Exception as e:
        print(f"Error fetching from TMDB: {e}")
        return []

async def get_recommendations(
    group_type: str, member_prefs: List[Dict[str, float]] = None
) -> List[Dict[str, Any]]:
    """
    Get 20 curated recommendations blended dynamically using group profiles
    and individual member genre preferences.
    """
    # 1. Process user-selected genres
    user_genre_weights = {}
    if member_prefs:
        for prefs in member_prefs:
            for genre, weight in prefs.items():
                gl = genre.lower()
                user_genre_weights[gl] = user_genre_weights.get(gl, 0.0) + float(weight)
        num_members = len(member_prefs)
        if num_members > 0:
            for g in user_genre_weights:
                user_genre_weights[g] /= num_members

    # 2. Get group profile
    profile = GROUP_PROFILES.get(group_type, GROUP_PROFILES["friends"])
    profile_genres = [g.lower() for g in profile["genres"]]

    # 3. Consolidate scoring weights
    # Group genres get a baseline of 1.0; user preferences add to it.
    scoring_weights = {g: 1.0 for g in profile_genres}
    for g, w in user_genre_weights.items():
        scoring_weights[g] = scoring_weights.get(g, 0.0) + w * 1.5

    # 4. Determine fetch pool genres for TMDB (merging profile + user selected genres)
    fetch_genres = list(set(profile["genres"] + [g for g in user_genre_weights if g in GENRES]))

    # 5. Fetch candidate movies
    candidates = []
    if TMDB_API_KEY:
        candidates = await fetch_tmdb_movies(fetch_genres, profile["certification_lte"])

    if not candidates:
        # Fallback to the full mock movie pool
        candidates = ALL_MOCK_POOL.copy()

    # 6. Score candidate movies
    scored_candidates = []
    for m in candidates:
        m_genres = [g.strip().lower() for g in m["genres"].split(",")] if m.get("genres") else []
        genre_score = 0.0
        for mg in m_genres:
            if mg in scoring_weights:
                genre_score += scoring_weights[mg]
        
        # Recommendation Score = User rating (base) + weighted genre score
        rating = float(m.get("rating") or 6.0)
        rec_score = rating + (genre_score * 2.5)
        
        scored_candidates.append((m, rec_score))

    # 7. Sort by computed score in descending order
    # (Use random seed per group type for slight variation if scores are identical)
    random.seed(group_type)
    random.shuffle(scored_candidates)
    scored_candidates.sort(key=lambda x: x[1], reverse=True)

    # 8. Return top 20 movies
    top_movies = [item[0] for item in scored_candidates[:20]]
    return top_movies
