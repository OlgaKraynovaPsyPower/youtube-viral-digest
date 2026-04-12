name: 🔥 YouTube Viral Digest

on:
  schedule:
    - cron: "0 5 * * *"

  workflow_dispatch:
    inputs:
      lookback_days:
        description: "Дней назад искать (default: 30)"
        required: false
        default: "30"
      min_views:
        description: "Минимум просмотров (default: 10000)"
        required: false
        default: "10000"
      min_ratio:
        description: "Минимальный ratio просмотры/подписчики (default: 1.5)"
        required: false
        default: "1.5"

jobs:
  send-digest:
    name: Fetch & Send Digest
    runs-on: ubuntu-latest

    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Set up Python 3.11
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: pip install requests

      - name: Run digest
        env:
          YOUTUBE_API_KEY:    ${{ secrets.YOUTUBE_API_KEY }}
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID:   ${{ secrets.TELEGRAM_CHAT_ID }}
          LOOKBACK_DAYS:      ${{ github.event.inputs.lookback_days || '30' }}
          MIN_VIEWS:          ${{ github.event.inputs.min_views || '10000' }}
          MIN_RATIO:          ${{ github.event.inputs.min_ratio || '1.5' }}
        run: python digest.py
