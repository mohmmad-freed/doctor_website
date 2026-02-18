@echo off
echo ==========================================
echo      FULL SYSTEM RESET AND MIGRATION
echo ==========================================
echo.

echo [1/3] Cleaning Database and Migrations...
python reset_db.py
if %errorlevel% neq 0 exit /b %errorlevel%

echo.
echo [2/3] Making Migrations...
python manage.py makemigrations
if %errorlevel% neq 0 exit /b %errorlevel%

echo.
echo [3/3] Migrating Database...
python manage.py migrate
if %errorlevel% neq 0 exit /b %errorlevel%

echo.
echo ==========================================
echo           SUCCESSFULLY RESET!
echo ==========================================
pause