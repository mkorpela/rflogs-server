from datetime import datetime, timedelta
import hashlib
import os
import re
import secrets
import string
import time
from typing import Optional, Tuple

import psycopg2
from psycopg2.extras import RealDictCursor

from rflogs_server.utils import generate_urlsafe_id, get_s3_client
from .connection import get_db_connection
from rflogs_server.models import WORKSPACE_PLAN, User, Workspace
from rflogs_server.logging_config import get_logger

from botocore.exceptions import ClientError

logger = get_logger(__name__)


def get_workspace_by_owner_id(owner_id: str) -> Optional[Workspace]:
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT * FROM workspaces WHERE owner_id = %s
        """,
        (owner_id,),
    )
    workspace_data = cursor.fetchone()
    conn.close()

    if workspace_data:
        return Workspace(**workspace_data)
    return None


def get_workspace_by_id(id: str) -> Optional[Workspace]:
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT * FROM workspaces WHERE id = %s
        """,
        (id,),
    )
    workspace_data = cursor.fetchone()
    conn.close()

    if workspace_data:
        return Workspace(**workspace_data)
    return None


def update_workspace(workspace_id: str, update_fields: dict):
    conn = get_db_connection()
    cursor = conn.cursor()

    # Dynamically build the SET clause
    set_clause = ", ".join([f"{key} = %s" for key in update_fields.keys()])
    values = list(update_fields.values())
    values.append(workspace_id)

    query = f"""
    UPDATE workspaces
    SET {set_clause}
    WHERE id = %s
    """

    try:
        cursor.execute(query, values)
        conn.commit()
    except psycopg2.Error as e:
        logger.error(f"Error updating workspace: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()


def update_workspace_expiry(
    workspace_id: str,
    expiry_date: Optional[datetime],
    subscription_id: Optional[str] = None,
):
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            UPDATE workspaces
            SET expiry_date = %s, stripe_subscription_id = %s
            WHERE id = %s
            """,
            (expiry_date, subscription_id, workspace_id),
        )
        conn.commit()
    except psycopg2.Error as e:
        logger.error(f"Error updating workspace expiry: {e}")
        conn.rollback()
    finally:
        conn.close()


def get_workspace_storage_usage(workspace_id: str) -> int:
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT COALESCE(SUM(f.size), 0) AS total_size
        FROM files f
        JOIN runs r ON f.run_id = r.id
        JOIN projects p ON r.project_id = p.id
        WHERE p.workspace_id = %s
        """,
        (workspace_id,),
    )
    total_size_bytes: int = cursor.fetchone()["total_size"]
    conn.close()
    return total_size_bytes


def get_workspace_by_subscription_id(subscription_id: str) -> Optional[Workspace]:
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT * FROM workspaces
        WHERE stripe_subscription_id = %s
        """,
        (subscription_id,),
    )
    workspace_data = cursor.fetchone()
    conn.close()

    if workspace_data:
        return Workspace(**workspace_data)
    return None


def generate_unique_bucket_name(base_name: str) -> str:
    for _ in range(10):  # Try up to 10 times
        # Normalize base_name: lowercase, remove invalid characters
        normalized_name = re.sub(r"[^a-z0-9-]", "", base_name.lower())

        # Generate a cryptographically strong random string
        random_string = "".join(
            secrets.choice(string.ascii_lowercase + string.digits) for _ in range(8)
        )

        # Use current timestamp for additional uniqueness
        timestamp = int(time.time() * 1000)

        # Combine normalized name, random string, and timestamp for hashing
        hash_input = f"{normalized_name}{random_string}{timestamp}"
        hash_digest = hashlib.sha256(hash_input.encode("utf-8")).hexdigest()[:8]

        # Calculate the maximum length for the normalized_name
        max_base_length = 63 - (
            len(random_string) + len(hash_digest) + 2
        )  # 2 for separators

        # Truncate normalized_name if necessary
        normalized_name = normalized_name[:max_base_length]

        # Ensure the name starts with a letter or number
        if not normalized_name or not normalized_name[0].isalnum():
            normalized_name = "w" + normalized_name

        # Combine all parts
        bucket_name = f"{normalized_name}-{random_string}-{hash_digest}"

        if is_bucket_name_available(
            bucket_name, backend=os.getenv("STORAGE_BACKEND", "s3")
        ):
            return bucket_name

    # If all attempts fail, raise an exception
    raise ValueError("Unable to generate a unique bucket name.")


def is_bucket_name_available(bucket_name: str, backend="s3") -> bool:
    s3_client = get_s3_client(backend=backend)
    try:
        s3_client.head_bucket(Bucket=bucket_name)
        # If no exception, the bucket exists
        return False
    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        if error_code in ["404", "NoSuchBucket"]:
            # Bucket does not exist
            return True
        elif error_code in ["403", "AccessDenied"]:
            # Access denied implies the bucket exists but is not accessible
            return False
        elif error_code in ["400", "InvalidBucketName"]:
            # Invalid bucket name
            return False
        else:
            # Some other error occurred
            return False


def create_or_update_github_user(
    github_id: str, github_username: str, github_email: Optional[str]
) -> Tuple[User, Workspace]:
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    try:
        # Check if the user already exists
        cursor.execute("SELECT * FROM users WHERE github_id = %s", (github_id,))
        existing_user = cursor.fetchone()

        if existing_user:
            # Update existing user
            cursor.execute(
                """
                UPDATE users
                SET github_username = %s, github_email = %s
                WHERE github_id = %s
                RETURNING *
                """,
                (github_username, github_email, github_id),
            )
            user_data = cursor.fetchone()
            conn.commit()

            # Fetch existing workspace
            cursor.execute(
                "SELECT * FROM workspaces WHERE owner_id = %s", (user_data["id"],)
            )
            workspace_data = cursor.fetchone()

            # If workspace doesn't have bucket_name, generate one
            if "bucket_name" not in workspace_data or not workspace_data["bucket_name"]:
                bucket_name = generate_unique_bucket_name(github_username)
                # Update workspace with bucket_name
                cursor.execute(
                    """
                    UPDATE workspaces
                    SET bucket_name = %s
                    WHERE id = %s
                    RETURNING *
                    """,
                    (bucket_name, workspace_data["id"]),
                )
                workspace_data = cursor.fetchone()
                conn.commit()

        else:
            # Start a transaction for creating new user and workspace
            cursor.execute("BEGIN")
            try:
                # Create new user
                user_id = generate_urlsafe_id()

                cursor.execute(
                    """
                    INSERT INTO users (id, github_id, github_username, github_email)
                    VALUES (%s, %s, %s, %s)
                    RETURNING *
                    """,
                    (
                        user_id,
                        github_id,
                        github_username,
                        github_email,
                    ),
                )
                user_data = cursor.fetchone()

                # Generate bucket_name
                bucket_name = generate_unique_bucket_name(github_username)

                # Create new workspace
                workspace_id = generate_urlsafe_id()
                workspace_name = f"{github_username}'s Workspace"
                workspace_expiry_date = datetime.utcnow() + timedelta(
                    days=30
                )  # 30-day trial period

                cursor.execute(
                    """
                    INSERT INTO workspaces (id, name, owner_id, storage_limit_bytes,
                                            active_projects_limit, expiry_date, bucket_name)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (
                        workspace_id,
                        workspace_name,
                        user_data["id"],
                        WORKSPACE_PLAN["storage_limit_bytes"],
                        WORKSPACE_PLAN["active_projects_limit"],
                        workspace_expiry_date,
                        bucket_name,
                    ),
                )
                workspace_data = cursor.fetchone()

                # Commit the transaction
                cursor.execute("COMMIT")
                logger.info("Created user", user_id=user_id, workspace_id=workspace_id)
            except psycopg2.Error as e:
                cursor.execute("ROLLBACK")
                logger.error(f"Error creating new user and workspace: {e}")
                raise

        user = User(**user_data)
        workspace = Workspace(**workspace_data)

        # Handle pending invitations outside the transaction
        handle_pending_invitations(user)

        return user, workspace
    except psycopg2.Error as e:
        logger.error(f"Error creating or updating user: {e}")
        raise
    finally:
        conn.close()


def get_user_by_username(username: str) -> Optional[User]:
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT *
        FROM users
        WHERE github_username = %s
        """,
        (username,),
    )
    user_data = cursor.fetchone()
    conn.close()

    if user_data:
        return User(**user_data)
    return None


def get_user_by_github_id(github_id: str) -> Optional[User]:
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT *
        FROM users
        WHERE github_id = %s
        """,
        (github_id,),
    )
    user_data = cursor.fetchone()
    conn.close()

    if user_data:
        return User(**user_data)
    return None


def handle_pending_invitations(user: User) -> None:
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Fetch pending invitations
        cursor.execute(
            """
            SELECT id, project_id
            FROM project_invitations
            WHERE invitee_username = %s AND expires_at > NOW()
            """,
            (user.github_username,),
        )
        invitations = cursor.fetchall()

        for invitation in invitations:
            # Add user to project
            cursor.execute(
                """
                INSERT INTO project_users (project_id, user_id, role)
                VALUES (%s, %s, 'member')
                ON CONFLICT (project_id, user_id) DO NOTHING
                """,
                (invitation["project_id"], user.id),
            )

            # Delete the invitation
            cursor.execute(
                "DELETE FROM project_invitations WHERE id = %s", (invitation["id"],)
            )

            logger.info(
                f"Converted invitation to project share for user {user.github_username} in project {invitation['project_id']}"
            )

        conn.commit()
    except psycopg2.Error as e:
        logger.error(f"Error handling pending invitations: {e}")
        conn.rollback()
    finally:
        conn.close()


def get_user_by_id(user_id: str) -> Optional[User]:
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT *
        FROM users
        WHERE id = %s
        """,
        (user_id,),
    )
    user_data = cursor.fetchone()
    conn.close()

    if user_data:
        return User(**user_data)
    return None


def get_user_by_subscription_id(subscription_id: str) -> Optional[User]:
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT u.* FROM users u
        JOIN workspaces w ON u.id = w.owner_id
        WHERE w.stripe_subscription_id = %s
        """,
        (subscription_id,),
    )
    user_data = cursor.fetchone()
    conn.close()

    if user_data:
        return User(**user_data)
    return None
