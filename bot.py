import json
import os
import re
from datetime import datetime
import requests
from atproto import Client, client_utils

TMDB_API_KEY = os.environ.get("TMDB_API_KEY")
BSKY_HANDLE = os.environ.get("BSKY_HANDLE")
BSKY_APP_PASSWORD = os.environ.get("BSKY_APP_PASSWORD")

STATE_FILE = "posted_movies.json"

ALLOWED_PROVIDERS = {
    "Netflix",
    "Max",
    "HBO Max",
    "SkyShowtime",
    "Amazon Prime Video",
    "Disney Plus",
    "Apple TV Plus",
    "Apple TV+",
}

GENRES_MAP = {
    28: "Acțiune",
    12: "Aventură",
    16: "Animație",
    35: "Comedie",
    80: "Crimă",
    99: "Documentar",
    18: "Dramă",
    10751: "Familie",
    14: "Fantezie",
    36: "Istoric",
    27: "Horror",
    10402: "Muzică",
    9648: "Mister",
    10749: "Romantic",
    878: "SF",
    10770: "Film TV",
    53: "Thriller",
    10752: "Război",
    37: "Western",
}

COUNTRIES_MAP = {
    "US": "Statele Unite",
    "RO": "România",
    "GB": "Marea Britanie",
    "FR": "Franța",
    "DE": "Germania",
    "ES": "Spania",
    "IT": "Italia",
    "CA": "Canada",
    "JP": "Japonia",
    "KR": "Coreea de Sud",
    "AU": "Australia",
    "SE": "Suedia",
    "NO": "Norvegia",
    "DK": "Danemarca",
    "PL": "Polonia",
    "BE": "Belgia",
    "TR": "Turcia",
    "MX": "Mexic",
    "BR": "Brazilia",
    "IN": "India",
    "CN": "China",
}


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
                if isinstance(data, list):
                    # Compatibilitate cu versiunea anterioară unde se salvau doar ID-uri
                    posted_ids = {
                        item["id"] if isinstance(item, dict) else item
                        for item in data
                    }
                    history = [item for item in data if isinstance(item, dict)]
                    return posted_ids, history
            except json.JSONDecodeError:
                return set(), []
    return set(), []


def save_state(history):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(history[-200:], f, indent=2)


def get_streaming_providers(movie_id):
    url = f"https://api.themoviedb.org/3/movie/{movie_id}/watch/providers"
    params = {"api_key": TMDB_API_KEY}
    res = requests.get(url, params=params).json()

    ro_providers = res.get("results", {}).get("RO", {})
    flatrate = ro_providers.get("flatrate", [])
    
    matched = [
        p["provider_name"]
        for p in flatrate
        if p.get("provider_name") in ALLOWED_PROVIDERS
    ]
    return matched


def get_movie_extras(movie_id):
    headers_params = {"api_key": TMDB_API_KEY}

    cast_url = f"https://api.themoviedb.org/3/movie/{movie_id}/credits"
    cast_res = requests.get(cast_url, params=headers_params).json()
    cast = [actor["name"] for actor in cast_res.get("cast", [])[:3]]

    videos_url = f"https://api.themoviedb.org/3/movie/{movie_id}/videos"
    videos_res = requests.get(videos_url, params=headers_params).json()
    trailer_url = None

    for v in videos_res.get("results", []):
        if v.get("site") == "YouTube" and v.get("type") in [
            "Trailer",
            "Teaser",
        ]:
            trailer_url = f"https://www.youtube.com/watch?v={v.get('key')}"
            break

    return cast, trailer_url


def fetch_candidate_movie(posted_ids, history):
    # Luăm data de azi (fusul orar UTC folosit de acțiuni / TMDB)
    today_str = datetime.utcnow().strftime("%Y-%m-%d")

    url = "https://api.themoviedb.org/3/discover/movie"
    params = {
        "api_key": TMDB_API_KEY,
        "watch_region": "RO",
        "with_watch_monetization_types": "flatrate",
        "primary_release_date.gte": today_str,
        "primary_release_date.lte": today_str,
        "sort_by": "popularity.desc",
        "language": "ro-RO",
    }

    res = requests.get(url, params=params).json()
    results = res.get("results", [])

    # Colectăm toate filmele eligibile din ziua de azi
    today_candidates = []
    for movie in results:
        m_id = movie["id"]
        if m_id not in posted_ids:
            providers = get_streaming_providers(m_id)
            if providers:
                today_candidates.append((movie, providers))

    # Dacă nu există nicio premieră nouă azi pe platformele selectate
    if not today_candidates:
        return None, None

    # Determinăm ce platforme au fost postate cel mai recent azi
    last_providers = [
        h.get("provider")
        for h in reversed(history)
        if h.get("date") == today_str
    ]

    # Prioritizăm o platformă care nu a fost postată la rularea anterioară
    for movie, providers in today_candidates:
        main_p = providers[0]
        if not last_providers or main_p != last_providers[0]:
            return movie, providers

    # Dacă au rămas filme doar de la aceeași platformă, postăm ce e disponibil
    return today_candidates[0][0], today_candidates[0][1]


def download_poster(poster_path):
    if not poster_path:
        return None
    url = f"https://image.tmdb.org/t/p/w500{poster_path}"
    resp = requests.get(url)
    if resp.status_code == 200:
        return resp.content
    return None


def clean_hashtag(text):
    return "#" + re.sub(r"[^a-zA-Z0-9]", "", text)


def post_to_bsky(movie, providers):
    title = movie.get("title") or movie.get("original_title")
    main_provider = providers[0]

    release_date = movie.get("release_date", "")
    year = f" ({release_date[:4]})" if release_date else ""

    origin_countries = movie.get("origin_country", [])
    raw_country = origin_countries[0] if origin_countries else "US"
    country_name = COUNTRIES_MAP.get(raw_country, raw_country)

    genre_ids = movie.get("genre_ids", [])
    genres = [GENRES_MAP.get(gid) for gid in genre_ids if gid in GENRES_MAP]
    genres_str = ", ".join(genres[:2]) if genres else "Film"

    genre_country_line = f"{genres_str} , {country_name}"

    cast, trailer_url = get_movie_extras(movie["id"])
    cast_str = ", ".join(cast) if cast else ""

    tmdb_url = f"https://www.themoviedb.org/movie/{movie['id']}"

    provider_tag = clean_hashtag(main_provider)
    hashtags = f"#FilmeNoi #StreamingRomania #Cinema {provider_tag}"

    header = f"{main_provider} : Premieră Azi pe Streaming\n\n"
    movie_info = f"{title}{year}\n{genre_country_line}\n"
    cast_line = f"Cu: {cast_str}\n\n" if cast_str else "\n"

    fixed_len = (
        len(header)
        + len(movie_info)
        + len(cast_line)
        + len("Detalii\n")
        + len(hashtags)
    )
    available_overview_len = max(0, 300 - fixed_len - 10)

    raw_overview = (movie.get("overview") or "").strip().replace("\n", " ")
    if (
        len(raw_overview) > available_overview_len
        and available_overview_len > 20
    ):
        overview = (
            raw_overview[: available_overview_len - 3].rsplit(" ", 1)[0]
            + "...\n"
        )
    elif available_overview_len > 20 and raw_overview:
        overview = raw_overview + "\n"
    else:
        overview = ""

    tb = client_utils.TextBuilder()
    tb.text(header)
    tb.text(movie_info)
    if overview:
        tb.text(overview)
    if cast_str:
        tb.text(cast_line)

    tb.link("Detalii", tmdb_url)
    tb.text(f"\n{hashtags}")

    client = Client()
