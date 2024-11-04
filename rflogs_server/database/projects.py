import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import argon2
import psycopg2

from rflogs_server.logging_config import get_logger
from rflogs_server.models import Project, ProjectInvitation, User, Workspace
from rflogs_server.utils import generate_urlsafe_id

from .connection import get_db_connection
from .users import get_workspace_by_id, get_workspace_by_owner_id

argon2_hasher = argon2.PasswordHasher()

logger = get_logger(__name__)


def check_project_access(project: Project, user: User) -> bool:
    workspace = get_workspace_by_owner_id(user.id)
    if not workspace:
        return False
    return bool(project.workspace_id == workspace.id)


def user_has_project_access(project: Project, user: User) -> bool:
    # First, check if the user is the project owner
    if check_project_access(project, user):
        logger.info("User has project access", user=user, project=project)
        return True

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # If not the owner, check if the user has been granted access
        cursor.execute(
            "SELECT 1 FROM project_users WHERE project_id = %s AND user_id = %s",
            (project.id, user.id),
        )
        return cursor.fetchone() is not None
    finally:
        conn.close()


def verify_api_key(api_key: str) -> Optional[Tuple[Project, Workspace]]:
    project_id_length = 22  # Length of the Base64-encoded UUID without padding
    key_prefix_length = 8  # Length of key_prefix

    if len(api_key) <= project_id_length + key_prefix_length:
        logger.warning("API key is too short")
        return None
    try:
        project_id = api_key[:project_id_length]
        key_prefix = api_key[project_id_length : project_id_length + key_prefix_length]

        conn = get_db_connection()
        cursor = conn.cursor()

        # Look up the api_keys table using project_id and key_prefix
        cursor.execute(
            """
            SELECT ak.hashed_key, p.id, p.name, p.workspace_id, p.public_access, 
                   p.created_at, p.retention_days
            FROM api_keys ak
            JOIN projects p ON ak.project_id = p.id
            WHERE ak.project_id = %s AND ak.key_prefix = %s
            """,
            (project_id, key_prefix),
        )
        result = cursor.fetchone()

        if result:
            hashed_key = result["hashed_key"]
            full_api_key = api_key

            # Verify the full API key against the stored hash using Argon2id
            try:
                argon2_hasher.verify(hashed_key, full_api_key)
                # Retrieve the workspace associated with the project
                workspace = get_workspace_by_id(result["workspace_id"])
                if not workspace:
                    logger.warning("Workspace not found for project")
                    return None

                # Return the associated project and workspace
                project = Project(
                    id=result["id"],
                    name=result["name"],
                    workspace_id=result["workspace_id"],
                    public_access=result["public_access"],
                    created_at=result["created_at"],
                    retention_days=result["retention_days"],
                )
                return project, workspace
            except argon2.exceptions.VerifyMismatchError:
                logger.warning("API key verification failed")
                return None
        else:
            logger.info("No result from API key query")
            return None
    except Exception as e:
        logger.error(f"Error verifying API key: {e}")
        return None
    finally:
        conn.close()


def add_user_to_project(project_id: str, user_id: str, role: str = "member") -> bool:
    conn = get_db_connection()
    cursor = conn.cursor()

    success = False
    try:
        cursor.execute(
            "INSERT INTO project_users (project_id, user_id, role) VALUES (%s, %s, %s)",
            (project_id, user_id, role),
        )
        conn.commit()
        success = True
    except psycopg2.Error as e:
        logger.error(f"Error adding user to project: {e}")
        conn.rollback()
        success = False

    conn.close()
    return success


def create_project_invitation(
    project_id: str, inviter_id: str, invitee_username: str
) -> Optional[ProjectInvitation]:
    conn = get_db_connection()
    cursor = conn.cursor()

    invitation_id = generate_urlsafe_id()
    created_at = datetime.utcnow()
    expires_at = created_at + timedelta(days=7)  # Set expiration to 7 days from now

    try:
        cursor.execute(
            """
            INSERT INTO project_invitations 
            (id, project_id, inviter_id, invitee_username, created_at, expires_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id, project_id, inviter_id, invitee_username, created_at, expires_at
            """,
            (
                invitation_id,
                project_id,
                inviter_id,
                invitee_username,
                created_at,
                expires_at,
            ),
        )
        invitation_data = cursor.fetchone()
        conn.commit()

        if invitation_data:
            return ProjectInvitation(**invitation_data)
        else:
            return None
    except psycopg2.Error as e:
        logger.error(f"Error creating project invitation: {e}")
        conn.rollback()
        return None
    finally:
        conn.close()


def remove_user_project_access(project_id: str, username: str) -> bool:
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # First, try to remove direct access
        cursor.execute(
            """
            DELETE FROM project_users
            WHERE project_id = %s AND user_id = (
                SELECT id FROM users WHERE github_username = %s
            )
            RETURNING 1
        """,
            (project_id, username),
        )

        if cursor.fetchone():
            conn.commit()
            logger.info(
                f"Removed direct access for user {username} from project {project_id}"
            )
            return True

        # If no direct access was removed, try to remove invitation
        cursor.execute(
            """
            DELETE FROM project_invitations
            WHERE project_id = %s AND invitee_username = %s
            RETURNING 1
        """,
            (project_id, username),
        )

        if cursor.fetchone():
            conn.commit()
            logger.info(
                f"Removed invitation for user {username} from project {project_id}"
            )
            return True

        # If we get here, no access or invitation was found
        logger.warning(
            f"No access or invitation found for user {username} in project {project_id}"
        )
        return False

    except psycopg2.Error as e:
        logger.error(f"Error removing user access: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()


def create_project(project: Project) -> Tuple[Project, str]:
    conn = get_db_connection()
    cursor = conn.cursor()

    project.id = generate_urlsafe_id()
    random_part = secrets.token_urlsafe(32)
    api_key = f"{project.id}{random_part}"
    key_prefix = random_part[:8]

    hashed_key = argon2_hasher.hash(api_key)

    try:
        cursor.execute(
            """
            INSERT INTO projects (id, name, workspace_id, public_access, created_at, retention_days)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id, name, workspace_id, public_access, created_at, retention_days
            """,
            (
                project.id,
                project.name,
                project.workspace_id,
                project.public_access,
                project.created_at,
                project.retention_days,
            ),
        )
        project_data = cursor.fetchone()

        cursor.execute(
            """
            INSERT INTO api_keys (id, project_id, key_prefix, hashed_key)
            VALUES (%s, %s, %s, %s)
            """,
            (generate_urlsafe_id(), project.id, key_prefix, hashed_key),
        )

        conn.commit()
    except psycopg2.Error as e:
        conn.rollback()
        logger.error(f"Error creating project: {e}")
        raise
    finally:
        conn.close()

    return Project(**project_data), api_key


def update_project_in_db(project_id: str, update_data: Dict[str, Any]) -> Project:
    conn = get_db_connection()
    try:
        cursor = conn.cursor()

        update_fields = []
        update_values = []
        for key, value in update_data.items():
            update_fields.append(f"{key} = %s")
            update_values.append(value)

        update_query = f"""
            UPDATE projects
            SET {', '.join(update_fields)}
            WHERE id = %s
            RETURNING *
        """
        update_values.append(project_id)

        cursor.execute(update_query, update_values)
        updated_project_data = cursor.fetchone()

        if not updated_project_data:
            raise ValueError("Project not found")

        conn.commit()

        return Project(**updated_project_data)
    except Exception as e:
        conn.rollback()
        logger.error(f"Error updating project: {e}")
        raise
    finally:
        conn.close()


def recreate_api_key(project_id: str) -> Optional[str]:
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Generate new API key
        random_part = secrets.token_urlsafe(32)
        new_api_key = f"{project_id}{random_part}"
        hashed_key = argon2_hasher.hash(new_api_key)
        key_prefix = random_part[:8]

        # Delete old API keys
        cursor.execute(
            "DELETE FROM api_keys WHERE project_id = %s",
            (project_id,),
        )

        # Insert new API key
        api_key_id = generate_urlsafe_id()
        cursor.execute(
            """
            INSERT INTO api_keys (id, project_id, key_prefix, hashed_key)
            VALUES (%s, %s, %s, %s)
            """,
            (api_key_id, project_id, key_prefix, hashed_key),
        )

        conn.commit()
        logger.info(f"Regenerated API key for project {project_id}")

        return new_api_key
    except Exception as e:
        conn.rollback()
        logger.error(f"Error regenerating API key: {e}")
        return None
    finally:
        conn.close()


def delete_project(project_id: str) -> Tuple[bool, List[str]]:
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Get all file paths associated with the project before deletion
        cursor.execute(
            """
            SELECT f.path
            FROM files f
            JOIN runs r ON f.run_id = r.id
            WHERE r.project_id = %s
            """,
            (project_id,),
        )
        file_paths = [row["path"] for row in cursor.fetchall()]

        # Delete the project (this will cascade delete runs and files)
        cursor.execute("DELETE FROM projects WHERE id = %s", (project_id,))
        affected_rows = cursor.rowcount

        conn.commit()
        conn.close()
        return affected_rows > 0, file_paths
    except psycopg2.Error as e:
        logger.error(f"Error deleting project: {e}")
        conn.rollback()
        conn.close()
        return False, []


def get_project_by_id(project_id: str) -> Optional[Project]:
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT *
        FROM projects WHERE id = %s
        """,
        (project_id,),
    )
    project_data = cursor.fetchone()

    conn.close()

    if project_data:
        return Project(**project_data)
    return None


def get_project_storage_usage(project_id: str) -> int:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT COALESCE(SUM(f.size), 0) AS total_size
        FROM files f
        JOIN runs r ON f.run_id = r.id
        WHERE r.project_id = %s
        """,
        (project_id,),
    )
    total_size: int = cursor.fetchone()["total_size"]
    conn.close()
    return total_size


def get_project_shared_users(project_id: str) -> List[str]:
    conn = get_db_connection()
    cursor = conn.cursor()

    shared_users = []

    # Get users with direct access (including members)
    cursor.execute(
        """
        SELECT u.github_username 
        FROM project_users pu
        JOIN users u ON pu.user_id = u.id
        WHERE pu.project_id = %s AND pu.role != 'owner'
    """,
        (project_id,),
    )
    shared_users.extend([row[0] for row in cursor.fetchall()])

    # Get invited users
    cursor.execute(
        """
        SELECT invitee_username 
        FROM project_invitations 
        WHERE project_id = %s AND expires_at > NOW()
    """,
        (project_id,),
    )
    shared_users.extend([row[0] for row in cursor.fetchall()])

    conn.close()
    return shared_users


def list_user_projects(user: User) -> List[Project]:
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT p.*
        FROM projects p
        JOIN workspaces w ON p.workspace_id = w.id
        WHERE w.owner_id = %s
        ORDER BY p.created_at DESC
        """,
        (user.id,),
    )

    rows = cursor.fetchall()
    conn.close()

    return [
        Project(**row, shared_with=get_project_shared_users(row["id"])) for row in rows
    ]


def get_active_projects_count(workspace_id: str) -> int:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT COUNT(*) FROM projects WHERE workspace_id = %s
        """,
        (workspace_id,),
    )
    count: int = cursor.fetchone()[0]
    conn.close()
    return count or 0
