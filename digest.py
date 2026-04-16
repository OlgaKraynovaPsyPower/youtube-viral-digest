"""
YouTube Viral Digest Bot
Finds videos where views/subs ratio >= MIN_RATIO
Skips videos already seen in previous runs (stored in seen_videos.txt)
"""

import os
import re
import requests
from datetime import datetime, timedelta, timezone
from pathlib import Path


YOUTUBE_API_KEY    = os.environ["YOUTUBE_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID   = os.environ["TELEGRAM_CHAT_ID"]
GITHUB_TOKEN       = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO        = os.environ.get("GITHUB_REPO", "")

LOOKBACK_DAYS    = int(os.getenv("LOOKBACK_DAYS", "90"))
MIN_VIEWS        = int(os.getenv("MIN_VIEWS", "15000"))
MIN_RATIO        = float(os.getenv("MIN_RATIO", "2"))
MIN_DURATION_SEC = 180

RESULTS_PER_KEYWORD = 50
SEEN_FILE = "seen_videos.txt"


def load_seen():
    path = Path(SEEN_FILE)
    if not path.exists():
        return set()
    with open(path, encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())


def save_seen(seen_ids):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        for vid_id in sorted(seen_ids):
            f.write(vid_id + "\n")


def push_seen_to_github(seen_ids):
    if not GITHUB_TOKEN or not GITHUB_REPO:
        print("[WARN] GITHUB_TOKEN or GITHUB_REPO not set, skipping seen file push")
        return

    content_str = "\n".join(sorted(seen_ids)) + "\n"
    import base64

    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{SEEN_FILE}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }

    get_resp = requests.get(api_url, headers=headers)
    sha = None
    if get_resp.status_code == 200:
        sha = get_resp.json().get("sha")

    content_b64 = base64.b64encode(content_str.encode()).decode()
    payload = {
        "message": "Update seen_videos.txt",
        "content": content_b64,
    }
    if sha:
        payload["sha"] = sha

    put_resp = requests.put(api_url, headers=headers, json=payload)
    if put_resp.status_code in (200, 201):
        print(f"[OK] seen_videos.txt updated on GitHub ({len(seen_ids)} entries)")
    else:
        print(f"[ERROR] Failed to push seen_videos.txt: {put_resp.status_code} {put_resp.text[:200]}")


def load_keywords():
    path = Path("keywords.txt")
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return [l.strip() for l in f if l.strip() and not l.startswith("#")]


def search_videos(keyword, published_after):
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


def get_videos_stats(video_ids):
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


def get_channels_stats(channel_ids):
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


def duration_to_seconds(iso):
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 0
    h, mn, s = m.groups()
    return int(h or 0) * 3600 + int(mn or 0) * 60 + int(s or 0)


def format_duration(iso):
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return "?"
    h, mn, s = m.groups()
    parts = []
    if h:  parts.append(f"{h}h")
    if mn: parts.append(f"{mn}m")
    if s:  parts.append(f"{s}s")
    return " ".join(parts) or "0s"


def fmt_number(n):
    return f"{n:,}".replace(",", " ")


def send_telegram(text):
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


def send_in_chunks(text, chunk_size=4000):
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


def main():
    keywords = load_keywords()
    if not keywords:
        send_telegram("keywords.txt is empty or missing.")
        return

    seen = load_seen()
    print(f"Loaded {len(seen)} seen video IDs")

    published_after = (
        datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    print(f"Keywords: {len(keywords)} | Lookback: {LOOKBACK_DAYS}d | "
          f"Min views: {MIN_VIEWS:,} | Min ratio: {MIN_RATIO}x | "
          f"Min duration: {MIN_DURATION_SEC}s")

    candidates = {}
    for kw in keywords:
        items = search_videos(kw, published_after)
        for item in items:
            vid = item["id"].get("videoId")
            if vid and vid not in candidates and vid not in seen:
                candidates[vid] = kw
        print(f"  [{kw}] -> {len(items)} results")

    if not candidates:
        send_telegram("Viral Digest: No new videos found today.")
        return

    all_video_ids = list(candidates.keys())
    video_stats = {}
    for i in range(0, len(all_video_ids), 50):
        video_stats.update(get_videos_stats(all_video_ids[i:i+50]))

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
        send_telegram("Viral Digest: No videos passed filters today.")
        return

    channel_ids = list({v["snippet"]["channelId"] for v in view_filtered.values()})
    channel_stats = {}
    for i in range(0, len(channel_ids), 50):
        channel_stats.update(get_channels_stats(channel_ids[i:i+50]))

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
            "id":          vid_id,
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
        send_telegram(f"Viral Digest: No new videos matched ratio >= {MIN_RATIO}x today.")
        return

    results.sort(key=lambda x: x["ratio"], reverse=True)

    date_str = datetime.now().strftime("%d.%m.%Y")
    header = (
        f"<b>Viral Digest - {date_str}</b>\n"
        f"Ratio >= {MIN_RATIO}x | min {fmt_number(MIN_VIEWS)} views | no Shorts\n\n"
        f"Found <b>{len(results)}</b> new videos:\n\n"
    )

    body = ""
    for i, v in enumerate(results, 1):
        body += (
            f"<b>{i}. {v['title']}</b>\n"
            f"Channel: {v['channel']}\n"
            f"Subs: {fmt_number(v['subscribers'])} | "
            f"Views: {fmt_number(v['views'])} | "
            f"Ratio: {v['ratio']:.1f}x\n"
            f"Duration: {v['duration']} | Date: {v['published']}\n"
            f"Keyword: {v['keyword']}\n"
            f"Link: {v['url']}\n\n"
        )

    send_in_chunks(header + body)

    new_seen = seen | {v["id"] for v in results}
    save_seen(new_seen)
    push_seen_to_github(new_seen)

    print(f"Done. Sent {len(results)} videos. Total seen: {len(new_seen)}")


if __name__ == "__main__":
    main()
