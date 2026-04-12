"""
YouTube Viral Digest Bot
Finds videos from small channels (<100k subs) with high views (>300k)
and sends a daily digest to Telegram.
"""

import os
import re
import requests
from datetime import datetime, timedelta, timezone


# ─── Configuration ────────────────────────────────────────────────────────────

YOUTUBE_API_KEY   = os.environ["YOUTUBE_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID  = os.environ["TELEGRAM_CHAT_ID"]

# How far back to look for videos (days)
LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "30"))

# Channel subscriber ceiling
MAX_SUBSCRIBERS = int(os.getenv("MAX_SUBSCRIBERS", "100000"))

# Minimum views threshold
MIN_VIEWS = int(os.getenv("MIN_VIEWS", "300000"))

# Max results per keyword from YouTube search
RESULTS_PER_KEYWORD = 50


# ─── Keywords ─────────────────────────────────────────────────────────────────

def load_keywords():
    """Load keywords from keywords.txt, one per line. Lines starting with # are comments."""
    path = os.path.join(os.path.dirname(__file__), "keywords.txt")
    if not os.path.exists(path):
        return DEFAULT_KEYWORDS
    with open(path, encoding="utf-8") as f:
        lines = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    return lines if lines else DEFAULT_KEYWORDS

DEFAULT_KEYWORDS = [
    "longevity diet",
    "blue zones food",
    "anti-inflammatory diet",
    "healthy aging food",
    "centenarian lifestyle",
    "live to 100 food",
    "autophagy fasting",
    "inflammation diet",
]


# ─── YouTube API ──────────────────────────────────────────────────────────────

def search_videos(keyword: str, published_after: str) -> list:
    """Search YouTube for videos matching a keyword, published after a given date."""
    url = "https://www.googleapis.com/youtube/v3/search"
    params = {
        "key": YOUTUBE_API_KEY,
        "q": keyword,
        "part": "snippet",
        "type": "video",
        "publishedAfter": published_after,
        "relevanceLanguage": "en",
        "maxResults": RESULTS_PER_KEYWORD,
        "order": "viewCount",
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        return r.json().get("items", [])
    except Exception as e:
        print(f"[WARN] search_videos({keyword!r}): {e}")
        return []


def get_videos_stats(video_ids: list) -> dict:
    """Batch-fetch video stats for up to 50 IDs. Returns {video_id: item}."""
    if not video_ids:
        return {}
    url = "https://www.googleapis.com/youtube/v3/videos"
    params = {
        "key": YOUTUBE_API_KEY,
        "id": ",".join(video_ids),
        "part": "statistics,snippet,contentDetails",
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        return {item["id"]: item for item in r.json().get("items", [])}
    except Exception as e:
        print(f"[WARN] get_videos_stats: {e}")
        return {}


def get_channels_stats(channel_ids: list) -> dict:
    """Batch-fetch channel stats for up to 50 IDs. Returns {channel_id: item}."""
    if not channel_ids:
        return {}
    url = "https://www.googleapis.com/youtube/v3/channels"
    params = {
        "key": YOUTUBE_API_KEY,
        "id": ",".join(channel_ids),
        "part": "statistics",
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        return {item["id"]: item for item in r.json().get("items", [])}
    except Exception as e:
        print(f"[WARN] get_channels_stats: {e}")
        return {}


def parse_duration(iso: str) -> str:
    """Convert ISO 8601 duration (PT1H2M3S) to human-readable string."""
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return "?"
    h, mn, s = m.groups()
    parts = []
    if h:  parts.append(f"{h}h")
    if mn: parts.append(f"{mn}m")
    if s:  parts.append(f"{s}s")
    return " ".join(parts) or "0s"


def fmt_number(n: int) -> str:
    """Format large numbers: 1234567 → 1 234 567"""
    return f"{n:,}".replace(",", " ")


# ─── Telegram ─────────────────────────────────────────────────────────────────

def send_telegram(text: str):
    """Send a message via Telegram Bot API."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        r = requests.post(url, json=payload, timeout=15)
        r.raise_for_status()
    except Exception as e:
        print(f"[ERROR] Telegram send failed: {e}")


def send_in_chunks(text: str, chunk_size: int = 4000):
    """Split long messages and send each chunk separately."""
    blocks = text.split("\n\n")
    current = ""
    for block in blocks:
        addition = block + "\n\n"
        if len(current) + len(addition) > chunk_size:
            if current.strip():
                send_telegram(current.strip())
            current = addition
        else:
            current += addition
    if current.strip():
        send_telegram(current.strip())


# ─── Main logic ───────────────────────────────────────────────────────────────

def main():
    keywords = load_keywords()
    published_after = (
        datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    print(f"Searching {len(keywords)} keywords | lookback: {LOOKBACK_DAYS}d | "
          f"min views: {MIN_VIEWS:,} | max subs: {MAX_SUBSCRIBERS:,}")

    candidates: dict[str, str] = {}
    for kw in keywords:
        items = search_videos(kw, published_after)
        for item in items:
            vid = item["id"].get("videoId")
            if vid and vid not in candidates:
                candidates[vid] = kw
        print(f"  [{kw}] → {len(items)} results")

    if not candidates:
        send_telegram("🔍 <b>Viral Digest</b>\n\nNo videos found today.")
        return

    all_video_ids = list(candidates.keys())
    video_stats: dict = {}
    for i in range(0, len(all_video_ids), 50):
        batch = all_video_ids[i:i+50]
        video_stats.update(get_videos_stats(batch))

    view_filtered = {}
    for vid_id, vid_data in video_stats.items():
        views = int(vid_data.get("statistics", {}).get("viewCount", 0))
        if views >= MIN_VIEWS:
            view_filtered[vid_id] = vid_data

    if not view_filtered:
        send_telegram(
            f"🔍 <b>Viral Digest</b>\n\n"
            f"Searched {len(candidates)} videos — none reached {fmt_number(MIN_VIEWS)} views yet."
        )
        return

    channel_ids = list({v["snippet"]["channelId"] for v in view_filtered.values()})
    channel_stats: dict = {}
    for i in range(0, len(channel_ids), 50):
        batch = channel_ids[i:i+50]
        channel_stats.update(get_channels_stats(batch))

    results = []
    for vid_id, vid_data in view_filtered.items():
        channel_id = vid_data["snippet"]["channelId"]
        ch = channel_stats.get(channel_id, {})
        subs = int(ch.get("statistics", {}).get("subscriberCount", 999_999_999))
        if subs > MAX_SUBSCRIBERS:
            continue

        views = int(vid_data["statistics"].get("viewCount", 0))
        results.append({
            "title":      vid_data["snippet"]["title"],
            "channel":    vid_data["snippet"]["channelTitle"],
            "subscribers": subs,
            "views":      views,
            "published":  vid_data["snippet"]["publishedAt"][:10],
            "duration":   parse_duration(vid_data["contentDetails"].get("duration", "")),
            "url":        f"https://youtube.com/watch?v={vid_id}",
            "keyword":    candidates.get(vid_id, ""),
        })

    if not results:
        send_telegram(
            f"🔍 <b>Viral Digest</b>\n\n"
            f"Found {len(view_filtered)} videos with {fmt_number(MIN_VIEWS)}+ views, "
            f"but all are from channels with {fmt_number(MAX_SUBSCRIBERS)}+ subscribers."
        )
        return

    results.sort(key=lambda x: x["views"], reverse=True)

    date_str = datetime.now().strftime("%d.%m.%Y")
    ratio_note = "views >> subscribers = viral signal 🔥"

    header = (
        f"🔥 <b>Viral Content Digest — {date_str}</b>\n"
        f"Channels under {fmt_number(MAX_SUBSCRIBERS)} subs · {fmt_number(MIN_VIEWS)}+ views\n"
        f"<i>{ratio_note}</i>\n\n"
        f"Found <b>{len(results)}</b> videos:\n\n"
    )

    body = ""
    for i, v in enumerate(results, 1):
        views_subs_ratio = round(v["views"] / max(v["subscribers"], 1))
        body += (
            f"<b>{i}. {v['title']}</b>\n"
            f"📺 {v['channel']}\n"
            f"👥 {fmt_number(v['subscribers'])} subs  ·  "
            f"👁 {fmt_number(v['views'])} views  ·  "
            f"⚡ {views_subs_ratio}x ratio\n"
            f"⏱ {v['duration']}  ·  📅 {v['published']}\n"
            f"🔑 {v['keyword']}\n"
            f"🔗 {v['url']}\n\n"
        )

    send_in_chunks(header + body)
    print(f"Done. Sent {len(results)} videos to Telegram.")


if __name__ == "__main__":
    main()
