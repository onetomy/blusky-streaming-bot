import json
import os
from datetime import datetime, timedelta
import requests
from atproto import Client, client_utils

TMDB_API_KEY = os.environ.get("TMDB_API_KEY")
BSKY_HANDLE = os.environ.get("BSKY_HANDLE")
BSKY_APP_PASSWORD = os.environ.get("BSKY_APP_PASSWORD")

STATE_FILE = "posted_movies.json"


def load_posted_ids():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            try:
                return set(json.load(f))
            except json.JSONDecodeError:
                return set()
    return set()


def save_posted_ids(posted_ids):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(list(posted_ids)[-200:], f, indent=2)


def get_streaming_providers(movie_id):
    url = f"https://api.themoviedb.org/3/movie/{movie_id}/watch/providers"
    params = {"api_key": TMDB_API_KEY}
    res = requests.get(url, params=params).json()

    ro_providers = res.get("results", {}).get("RO", {})
    flatrate = ro_providers.get("flatrate", [])
    return [p["provider_name"] for p in flatrate]


def fetch_candidate_movie(posted_ids):
    today = datetime.utcnow().date()
    start_date = today - timedelta(days=60)

    url = "https://api.themoviedb.org/3/discover/movie"
    params = {
        "api_key": TMDB_API_KEY,
        "watch_region": "RO",
        "with_watch_monetization_types": "flatrate",
        "primary_release_date.gte": start_date.strftime("%Y-%m-%d"),
        "primary_release_date.lte": today.strftime("%Y-%m-%d"),
        "sort_by": "primary_release_date.desc",
        "language": "ro-RO",
    }

    res = requests.get(url, params=params).json()
    results = res.get("results", [])

    for movie in results:
        m_id = movie["id"]
        if m_id not in posted_ids:
            providers = get_streaming_providers(m_id)
            if providers:
                return movie, providers
    return None, None


def post_to_bsky(movie, providers):
    title = movie.get("title") or movie.get("original_title")
    overview = movie.get("overview", "")
    if len(overview) > 130:
        overview = overview[:127] + "..."

    providers_str = ", ".join(providers[:3])
    tmdb_url = f"https://www.themoviedb.org/movie/{movie['id']}"

    tb = client_utils.TextBuilder()
    tb.text(f"🎬 Nou pe streaming în România:\n\n")
    tb.text(f"📌 {title}\n")
    tb.text(f"📺 Disponibil pe: {providers_str}\n")
    if overview:
        tb.text(f"📝 {overview}\n\n")
    else:
        tb.text("\n")
    tb.link("Detalii TMDB", tmdb_url)
    tb.text("\n#filme #streaming #romania #cinema")

    client = Client()
    client.login(BSKY_HANDLE, BSKY_APP_PASSWORD)
    client.send_post(tb)
    print(f"Postat cu succes: {title}")


def main():
    posted_ids = load_posted_ids()
    movie, providers = fetch_candidate_movie(posted_ids)

    if not movie:
        print("Nu au fost găsite noutăți nepostate.")
        return

    post_to_bsky(movie, providers)
    posted_ids.add(movie["id"])
    save_posted_ids(posted_ids)


if __name__ == "__main__":
    main()
