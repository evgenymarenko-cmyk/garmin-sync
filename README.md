# Garmin Sync Server

## Deploy to Render

1. Go to github.com and create a free account
2. Create a new repository called "garmin-sync"
3. Upload these 3 files: server.py, requirements.txt, render.yaml
4. Go to render.com, sign up free
5. New → Web Service → connect your GitHub repo
6. Render auto-detects the config and deploys
7. Your server URL will be: https://garmin-sync-XXXX.onrender.com

## Environment Variables (set in Render dashboard)
- GARMIN_EMAIL: your Garmin Connect email
- GARMIN_PASSWORD: your Garmin Connect password  
- API_SECRET: evgeny-health-2026

## Test
Visit: https://your-server.onrender.com/sync?secret=evgeny-health-2026
