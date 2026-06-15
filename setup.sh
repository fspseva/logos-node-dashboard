#!/usr/bin/env bash
# Download the front-end assets the dashboard needs into ./static
# (kept out of git so the repo stays lean; run this once after cloning).
set -e
cd "$(dirname "$0")/static"

echo "Fetching Leaflet + Chart.js..."
wget -q https://unpkg.com/leaflet@1.9.4/dist/leaflet.js          -O leaflet.js
wget -q https://unpkg.com/leaflet@1.9.4/dist/leaflet.css         -O leaflet.css
wget -q https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.js -O chart.js

echo "Fetching Logos brand fonts (optional - the UI falls back to system fonts if these are missing)..."
B="https://build.logos.co/_next/static/media"
wget -q "$B/RhymesDisplay_Regular-s.p.31e5f386.woff2" -O RhymesDisplay.woff2     || echo "  Rhymes Display unavailable (URL may have changed) - will fall back to a serif font"
wget -q "$B/PublicSans_Regular-s.p.1b3681fe.woff2"    -O PublicSans.woff2        || echo "  Public Sans unavailable - will fall back to system sans"
wget -q "$B/FiraCode_Regular-s.p.2262fbe3.woff2"      -O FiraCode.woff2          || echo "  Fira Code unavailable - will fall back to system mono"
wget -q "$B/FiraCode_SemiBold-s.p.8331c218.woff2"     -O FiraCode-SemiBold.woff2 || true

echo "Done. Assets are in $(pwd)"
