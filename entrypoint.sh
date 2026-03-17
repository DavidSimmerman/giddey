#!/bin/sh
set -e

echo "Running migrations..."
python manage.py migrate --noinput

echo "Running player scrape..."
python manage.py scrape_nba

# Set up daily cron job at 4 AM UTC
echo "0 4 * * * cd /app && python manage.py scrape_nba >> /var/log/cron.log 2>&1" | crontab -

# Start cron in the background
cron

echo "Starting gunicorn..."
exec gunicorn giddey.wsgi:application --bind 0.0.0.0:8000
