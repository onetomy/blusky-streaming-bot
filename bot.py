import json
import os
import re
from datetime import datetime, timedelta
import requests
from atproto import Client, client_utils

TMDB_API_KEY = os.environ.get("TMDB_API_KEY")
BSKY_HANDLE = os.environ.get("BSKY_HANDLE")
BSKY_APP_PASSWORD = os.environ.get("BSKY_APP_PASSWORD")

STATE_FILE = "posted_movies.json"

# ID-urile oficiale JustWatch / TMDB pentru platformele din România
PROVIDER_IDS = {
    8: "Netflix",
    1899: "Max",
    1773: "SkyShowtime",
    119: "Amazon Prime Video",
    337: "Disney Plus",
    350: "Apple TV Plus",
}

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

APPLE_VARIANTS = {"Apple TV Plus", "Apple TV+"}

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
    res = requests.get(url, params=params, timeout=15).json()

    ro_providers = res.get("results", {}).get("RO", {})
    flatrate = ro_providers.get("flatrate", [])

    matched = [
        p["provider_name"]
        for p in flatrate
        if p.get("provider_name") in ALLOWED_PROVIDERS
    ]

    # Prioritizăm denumirea Apple TV în lista returnată
    matched.sort(key=lambda p: 0 if p in APPLE_VARIANTS else 1)
    return matched


def get_movie_extras(movie_id):
    headers_params = {"api_key": TMDB_API_KEY}

    cast_url = f"https://api.themoviedb.org/3/movie/{movie_id}/credits"
    cast_res = requests.get(cast_url, params=headers_params, timeout=15).json()
    cast = [actor["name"] for actor in cast_res.get("cast", [])[:3]]

    videos_url = f"https://api.themoviedb.org/3/movie/{movie_id}/videos"
    videos_res = requests.get(videos_url, params=headers_params, timeout=15).json()
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
    today = datetime.utcnow().date()
    # Fereastră de 10 zile pentru a prinde premierele recente ale săptămânii
    start_date = today - timedelta(days=10)

    provider_ids_str = "|".join(str(pid) for pid in PROVIDER_IDS.keys())

    url = "https://api.themoviedb.org/3/discover/movie"
    params = {
        "api_key": TMDB_API_KEY,
        "watch_region": "RO",
        "with_watch_providers": provider_ids_str,
        "with_watch_monetization_types": "flatrate",
        "primary_release_date.gte": start_date.strftime("%Y-%m-%d"),
        "primary_release_date.lte": today.strftime("%Y-%m-%d"),
        "sort_by": "primary_release_date.desc",  # Pune cele mai noi pe primele locuri
        "language": "ro-RO",
    }

    res = requests.get(url, params=params, timeout=15).json()
    results = res.get("results", [])

    candidates = []
    for movie in results:
        m_id = movie["id"]
        if m_id not in posted_ids:
            providers = get_streaming_providers(m_id)
            if providers:
                # Dacă este titlu pe Apple TV, îl prioritizează imediat
                if any(p in APPLE_VARIANTS for p in providers):
                    return movie, providers
                candidates.append((movie, providers))

    if not candidates:
        return None, None

    # Alternare între servicii: verifică platforma postată anterior
    last_provider = history[-1].get("provider") if history else None

    for movie, providers in candidates:
        main_p = providers[0]
        if not last_provider or main_p != last_provider:
            return movie, providers

    return candidates[0][0], candidates[0][1]


def download_poster(poster_path):
    if not poster_path:
        return None
    url = f"https://image.tmdb.org/t/p/w500{poster_path}"
    resp = requests.get(url, timeout=15)
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

    header = f"{main_provider} : Premieră Streaming România\n\n"
    movie_info = f"{title}{year}\n{genre_country_line}\n"
    cast_line = f"Cu: {cast_str}\n\n" if cast_str else "\n"

    # Calcul pentru plafonul de 300 de caractere din Bluesky
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
    client.login(BSKY_HANDLE, BSKY_APP_PASSWORD)

    if trailer_url:
        client.send_post(
            text=tb,
            embed={
                "$type": "app.bsky.embed.external",
                "external": {
                    "uri": trailer_url,
                    "title": f"{title} - Trailer Oficial",
                    "description": f"Trailer oficial pentru {title}",
                },
            },
            langs=["ro"],
        )
        print(f"Postat cu player video YouTube integrat: {title} ({main_provider})")
    else:
        poster_bytes = download_poster(movie.get("poster_path"))
        if poster_bytes:
            upload_resp = client.upload_blob(poster_bytes)
            client.send_post(
                text=tb,
                embed={
                    "$type": "app.bsky.embed.images",
                    "images": [
                        {
                            "alt": f"Poster oficial {title}",
                            "image": upload_resp.blob,
                        }
                    ],
                },
                langs=["ro"],
            )
        else:
            client.send_post(text=tb, langs=["ro"])
        print(f"Postat cu poster: {title} ({main_provider})")


def main():
    posted_ids, history = load_state()
    movie, providers = fetch_candidate_movie(posted_ids, history)

    if not movie:
        print("Nu au fost găsite noutăți pe platformele selectate.")
        return

    post_to_bsky(movie, providers)

    history.append({
        "id": movie["id"],
        "provider": providers[0],
        "date": datetime.utcnow().strftime("%Y-%m-%d"),
    })
    save_state(history)


if __name__ == "__main__":
    main()
