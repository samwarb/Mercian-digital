# merican-digital (GitHub Pages)

This repository is set up to be hosted via GitHub Pages.

## Website

- Entry point: `index.html` (repo root)
- Static assets (optional): `assets/`
- Screenshots / working files: `assets/screenshots/`

### Enable GitHub Pages

In GitHub: **Settings → Pages**

- **Build and deployment** → **Source**: “Deploy from a branch”
- **Branch**: `main`
- **Folder**: `/ (root)`

## monday.com Daily Report script (optional)

The standalone script now lives at `tools/monday_daily_report.py`.

### Requirements

- Python 3.9+
- A monday.com API token with access to the boards you want to report on

### Environment

Set these environment variables before running the script:

```bash
export MONDAY_API_TOKEN="your-monday-api-token"
export MONDAY_BOARD_IDS="123456789,987654321"
```

Optional:

```bash
export MONDAY_TIMEZONE_OFFSET_HOURS="0"
```

`MONDAY_TIMEZONE_OFFSET_HOURS` defaults to `0` (UTC). Set it to your local offset if you want “today” and “yesterday” evaluated in another timezone.

## Run

```bash
python3 tools/monday_daily_report.py
```

If access is unavailable, the script exits with a clear error that says exactly what is missing, such as:

- missing `MONDAY_API_TOKEN`
- missing `MONDAY_BOARD_IDS`
- invalid board IDs
- no permission to access a board
- inability to reach the monday.com API
