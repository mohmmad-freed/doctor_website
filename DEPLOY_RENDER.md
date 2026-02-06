# Deploy to Render

## Build Command
```bash
pip install -r requirements.txt && python manage.py collectstatic --noinput && python manage.py migrate
```

## Start Command
```bash
gunicorn clinic_website.wsgi:application
```

## Required Environment Variables
Ensure these are set in the Render Dashboard:

*   `SECRET_KEY`
*   `DEBUG` (Set to 0 for production)
*   `ALLOWED_HOSTS` (e.g., `your-app.onrender.com`)
*   `DATABASE_URL` (Internal connection string from Render PostgreSQL)
*   `REDIS_URL` (Internal connection string from Render Redis)
*   `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_VERIFY_SID`
*   `BREVO_SMTP_USER`, `BREVO_SMTP_PASS`
*   `CSRF_TRUSTED_ORIGINS` (e.g., `https://your-app.onrender.com`)
