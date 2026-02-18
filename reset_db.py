import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
import os
import sys

# DB Config - loads from .env ideally, but hardcoded based on user context for this script efficiency
# In a real app, we'd use python-dotenv here too.
DB_USER = "postgres"
DB_PASS = "0000"
DB_HOST = "localhost"
DB_PORT = "5432"
TARGET_DB = "clinic_db"


def reset_database():
    print(f"--- RESETTING DATABASE '{TARGET_DB}' ---")
    try:
        # Connect to 'postgres' db to drop target db
        conn = psycopg2.connect(
            user=DB_USER,
            password=DB_PASS,
            host=DB_HOST,
            port=DB_PORT,
            dbname="postgres",
        )
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cur = conn.cursor()

        # Terminate existing connections
        print(f"1. Terminating connections to {TARGET_DB}...")
        cur.execute(
            f"""
            SELECT pg_terminate_backend(pg_stat_activity.pid)
            FROM pg_stat_activity
            WHERE pg_stat_activity.datname = '{TARGET_DB}'
            AND pid <> pg_backend_pid();
        """
        )

        # Drop DB
        print(f"2. Dropping database {TARGET_DB}...")
        cur.execute(f"DROP DATABASE IF EXISTS {TARGET_DB};")

        # Create DB
        print(f"3. Creating database {TARGET_DB}...")
        cur.execute(f"CREATE DATABASE {TARGET_DB};")

        cur.close()
        conn.close()
        print("Database reset successfully.")

    except Exception as e:
        print(f"CRITICAL ERROR: Failed to reset database. {e}")
        # If we can't reset the DB, we probably shouldn't delete migrations, or maybe we should?
        # Let's pause.
        input(
            "Press Enter to continue removing migrations anyway, or Ctrl+C to abort..."
        )


def clear_migrations(base_dir):
    print("\n--- CLEARING MIGRATION FILES ---")
    count = 0
    # Walk through all directories
    for root, dirs, files in os.walk(base_dir):
        if "venv" in root or ".git" in root:
            continue

        if "migrations" in dirs:
            migrations_path = os.path.join(root, "migrations")
            # print(f"Checking {migrations_path}...")
            for filename in os.listdir(migrations_path):
                if filename.endswith(".py") and filename != "__init__.py":
                    file_path = os.path.join(migrations_path, filename)
                    try:
                        os.remove(file_path)
                        print(f"Deleted: {filename} in {os.path.basename(root)}")
                        count += 1
                    except Exception as e:
                        print(f"Failed to delete {file_path}: {e}")

    print(f"Done. Removed {count} migration files.")


if __name__ == "__main__":
    base_dir = os.getcwd()
    reset_database()
    clear_migrations(base_dir)
    print(
        "\n[SUCCESS] System is clean. You can now run 'python manage.py makemigrations' and 'python manage.py migrate'."
    )