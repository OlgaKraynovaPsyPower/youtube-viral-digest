"""
YouTube Viral Digest Bot
Finds videos where views/subs ratio >= 3 (viral signal)
and sends a daily digest to Telegram.
"""

import os
import re
import requests
from datetime import datetime, timedelta, timezone


# ─── Configuration ────────────────────────────────────────────────────────────

YOUTUBE_API_KEY    = os.environ["YOUTUBE_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID   = os.environ["TELEGRAM_CHAT_ID"]

LOOKBACK_DAYS    = int(os.getenv("LOOKBACK_DAYS", "30"))
MIN_VIEWS        = int(os.getenv("MIN_VIEWS", "10000"))
MIN_RATIO        = float(os.getenv("MIN_RATIO", "3"))
MIN_DURATION_SEC = 180  # skip Shorts (< 3 minutes)

RESULTS_PER_KEYWORD = 50


# ─── Keywords ─────────────────────────────────────────────────────────────────

def load_keywords():
    path = os.path.join(os.path.dirname(__file__), "keywords.txt")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        lines = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    return lines


# ─── YouTube API ──────────────────────────────────────────────────────────────

def search_videos(keyword: str, published_after: str) -> list:
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


def duration_to_seconds(iso: str) -> int:
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 0
    h, mn, s = m.groups()
    return int(h or 0) * 3600 + int(mn or 0) * 60 + int(s or 0)


def format_duration(iso: str) -> str:
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
    return f"{n:,}".replace(",", " ")


# ─── Telegram ─────────────────────────────────────────────────────────────────

def send_telegram(text: str):
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
    if not keywords:
        send_telegram("⚠️ keywords.txt is empty or missing.")
        return

    published_after = (
        datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    print(f"Keywords: {len(keywords)} | Lookback: {LOOKBACK_DAYS}d | "
          f"Min views: {MIN_VIEWS:,} | Min ratio: {MIN_RATIO}x | "
          f"Min duration: {MIN_DURATION_SEC}s")

    # Step 1: collect candidate video IDs
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

    # Step 2: batch-fetch video stats
    all_video_ids = list(candidates.keys())
    video_stats: dict = {}
    for i in range(0, len(all_video_ids), 50):
        video_stats.update(get_videos_stats(all_video_ids[i:i+50]))

    # Step 3: filter by min views AND duration (skip Shorts)
    view_filtered = {}
    for vid_id, vid_data in video_stats.items():
        views = int(vid_data.get("statistics", {}).get("viewCount", 0))
        if views < MIN_VIEWS:
            continue
        duration_sec = duration_to_seconds(
            vid_data.get("contentDetails", {}).get("duration", "")
        )
        if duration_sec < MIN_DURATION_SEC:
            continue
        view_filtered[vid_id] = vid_data

    if not view_filtered:
        send_telegram(
            f"🔍 <b>Viral Digest</b>\n\n"
            f"Searched {len(candidates)} videos — none passed filters."
        )
        return

    # Step 4: batch-fetch channel stats
    channel_ids = list({v["snippet"]["channelId"] for v in view_filtered.values()})
    channel_stats: dict = {}
    for i in range(0, len(channel_ids), 50):
        channel_stats.update(get_channels_stats(channel_ids[i:i+50]))

    # Step 5: apply ratio filter
    results = []
    for vid_id, vid_data in view_filtered.items():
        channel_id = vid_data["snippet"]["channelId"]
        ch = channel_stats.get(channel_id, {})
        subs = int(ch.get("statistics", {}).get("subscriberCount", 0))

        if subs == 0:
            continue

        views = int(vid_data["statistics"].get("viewCount", 0))
        ratio = views / subs

        if ratio < MIN_RATIO:
            continue

        results.append({
            "title":       vid_data["snippet"]["title"],
            "channel":     vid_data["snippet"]["channelTitle"],
            "subscribers": subs,
            "views":       views,
            "ratio":       ratio,
            "published":   vid_data["snippet"]["publishedAt"][:10],
            "duration":    format_duration(vid_data["contentDetails"].get("duration", "")),
            "url":         f"https://youtube.com/watch?v={vid_id}",
            "keyword":     candidates.get(vid_id, ""),
        })

    if not results:
        send_telegram(
            f"🔍 <b>Viral Digest</b>\n\n"
            f"No videos matched ratio ≥ {MIN_RATIO}x today."
        )
        return

    # Sort by ratio descending
    results.sort(key=lambda x: x["ratio"], reverse=True)

    date_str = datetime.now().strftime("%d.%m.%Y")
    header = (
        f"🔥 <b>Viral Digest — {date_str}</b>\n"
        f"Ratio ≥ {MIN_RATIO}x · min {fmt_number(MIN_VIEWS)} views · no Shorts\n\n"
        f"Found <b>{len(results)}</b> videos:\n\n"
    )

    body = ""
    for i, v in enumerate(results, 1):
        body += (
            f"<b>{i}. {v['title']}</b>\n"
            f"📺 {v['channel']}\n"
            f"👥 {fmt_number(v['subscribers'])} subs  ·  "
            f"👁 {fmt_number(v['views'])} views  ·  "
            f"⚡ {v['ratio']:.1f}x ratio\n"
            f"⏱ {v['duration']}  ·  📅 {v['published']}\n"
            f"🔑 {v['keyword']}\n"
            f"🔗 {v['url']}\n\n"
        )

    send_in_chunks(header + body)
    print(f"Done. Sent {len(results)} videos to Telegram.")


if __name__ == "__main__":
    main()
