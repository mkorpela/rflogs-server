import os

import psycopg2
from psycopg2.extras import DictCursor

from rflogs_server.logging_config import get_logger

logger = get_logger(__name__)

# Database connection parameters
DB_NAME = os.getenv("DB_NAME", "rflogs")
DB_USER = os.getenv("DB_USER", "rflogs_user")
DB_PASSWORD = os.getenv("DB_PASSWORD", "rflogs_password")
DB_HOST = os.getenv("DB_HOST", "postgres")
DB_PORT = os.getenv("DB_PORT", "5432")


def get_db_connection():
    return psycopg2.connect(
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        host=DB_HOST,
        port=DB_PORT,
        cursor_factory=DictCursor,
    )


def get_highest_migration_version():
    migrations_dir = os.path.join(os.path.dirname(__file__), "migrations")
    versions = [
        int(f.split("_")[0]) for f in os.listdir(migrations_dir) if f.endswith(".sql")
    ]
    return max(versions) if versions else 0


def get_current_migration_version():
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_name = 'migrations'
            )
        """
        )
        table_exists = cursor.fetchone()[0]

        if not table_exists:
            return 0

        cursor.execute("SELECT MAX(version) FROM migrations")
        version = cursor.fetchone()[0]

        return version or 0
    except psycopg2.Error as e:
        logger.error(f"Error getting current migration version: {e}")
        return 0
    finally:
        conn.close()


def apply_migration(version: int, sql_file: str):
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        with open(sql_file, "r") as f:
            sql = f.read()

        cursor.execute(sql)
        cursor.execute("INSERT INTO migrations (version) VALUES (%s)", (version,))
        conn.commit()
        logger.info(f"Applied migration version {version}")
    except Exception as e:
        conn.rollback()
        logger.error(f"Error applying migration version {version}: {str(e)}")
        raise
    finally:
        conn.close()


def run_migrations():
    current_version = get_current_migration_version()
    migrations_dir = os.path.join(os.path.dirname(__file__), "migrations")

    for filename in sorted(os.listdir(migrations_dir)):
        if filename.endswith(".sql"):
            version = int(filename.split("_")[0])
            if version > current_version:
                apply_migration(version, os.path.join(migrations_dir, filename))
