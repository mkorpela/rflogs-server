from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple
import psycopg2
from rflogs_server.logging_config import get_logger
from rflogs_server.models import (
    FileInfo,
    ParsedRunStats,
    Project,
    RunCreate,
    RunInfo,
    TimingStats,
    User,
)
from rflogs_server.utils import TAG_KEY_PATTERN, TAG_VALUE_PATTERN, generate_urlsafe_id
from .connection import get_db_connection

logger = get_logger(__name__)

RESERVED_TAG_KEYS = {"limit", "offset", "verdict"}


def create_run_info(user: User, project: Project, run_data: RunCreate) -> RunInfo:
    conn = get_db_connection()
    cursor = conn.cursor()

    run_id = generate_urlsafe_id()

    # Insert the new run into the 'runs' table
    cursor.execute(
        """
        INSERT INTO runs (id, project_id, public_access, failed_tests)
        VALUES (%s, %s, %s, %s)
        RETURNING id, project_id, public_access, created_at
        """,
        (
            run_id,
            project.id,
            project.public_access,
            [],
        ),
    )
    run_row = cursor.fetchone()

    # Process and validate tags
    tags_dict: Dict[str, Tuple[str, str]] = {}
    if run_data.tags:
        for tag_str in run_data.tags:
            if ":" in tag_str:
                key, value = tag_str.split(":", 1)
            else:
                key = tag_str
                value = "true"
            key = key.strip()
            value = value.strip()

            key_lower = key.lower()

            # Check if the tag key is a reserved keyword
            if key_lower in RESERVED_TAG_KEYS:
                conn.rollback()
                raise ValueError(
                    f"'{key}' is a reserved keyword and cannot be used as a tag key"
                )

            # Validate tag key
            if not TAG_KEY_PATTERN.fullmatch(key):
                conn.rollback()
                raise ValueError(
                    f"Invalid tag key '{key}'. Must start with a letter, "
                    f"and be 1-50 characters long. Allowed characters: letters, numbers, '_', '-', '.'"
                )

            # Validate tag value
            if not TAG_VALUE_PATTERN.fullmatch(value):
                conn.rollback()
                raise ValueError(
                    f"Invalid tag value '{value}'. Must be 1-100 characters long. "
                    f"Allowed characters: letters, numbers, spaces, '_', '-', '.', '/'"
                )

            # Enforce case-insensitive uniqueness of tag keys within the run
            if key_lower in tags_dict:
                conn.rollback()
                raise ValueError(f"Duplicate tag key: '{key}' (case-insensitive)")

            tags_dict[key_lower] = (key, value)

        # Insert tags into the 'run_tags' table
        for _, (key, value) in tags_dict.items():
            cursor.execute(
                """
                INSERT INTO run_tags (run_id, key, value)
                VALUES (%s, %s, %s)
                """,
                (run_id, key, value),
            )

    conn.commit()
    conn.close()

    if run_row:
        # Construct the RunInfo object with tags
        run_info = RunInfo(
            id=run_row["id"],
            project_id=run_row["project_id"],
            project_name=project.name,
            public_access=run_row["public_access"],
            files=[],
            created_at=run_row["created_at"],
            total_tests=None,
            passed=None,
            failed=None,
            skipped=None,
            verdict=None,
            tags={key: value for key, value in tags_dict.values()},
            start_time=None,
            end_time=None,
            failed_test_names=[],
        )
        logger.info(f"Run created with ID: {run_info.id}")
        return run_info
    else:
        logger.error(f"Failed to create run for project: {project.id}")
        raise ValueError("Failed to create run")


def update_run_info(run_id: str, stats: ParsedRunStats) -> None:
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            UPDATE runs
            SET total_tests = %s, passed = %s, failed = %s, skipped = %s,
                verdict = %s, start_time = %s, end_time = %s, failed_tests = %s
            WHERE id = %s
            """,
            (
                stats.total_tests,
                stats.passed,
                stats.failed,
                stats.skipped,
                stats.verdict,
                stats.start_time,
                stats.end_time,
                stats.failed_test_names,
                run_id,
            ),
        )

        # Store timing stats
        for element_type, elements in stats.timing_stats.items():
            for name, timing in elements.items():
                cursor.execute(
                    """
                    WITH element_insert AS (
                        INSERT INTO execution_elements (name, type)
                        VALUES (%s, %s)
                        ON CONFLICT (name, type) DO UPDATE SET name = EXCLUDED.name
                        RETURNING id
                    )
                    INSERT INTO execution_times (run_id, element_id, total_time, call_count, average_time, median_time, std_deviation)
                    VALUES (%s, (SELECT id FROM element_insert), %s, %s, %s, %s, %s)
                    """,
                    (
                        name,
                        element_type,
                        run_id,
                        timing.total_time,
                        timing.call_count,
                        timing.average_time,
                        timing.median_time,
                        timing.std_deviation,
                    ),
                )

        conn.commit()
        logger.info(f"Updated database with statistics for run {run_id}")
    except psycopg2.Error as e:
        conn.rollback()
        logger.error(f"Error updating run info for run {run_id}: {e}")
        raise
    finally:
        conn.close()


def get_runs_and_files_to_purge() -> List[Dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()

    now = datetime.utcnow()

    cursor.execute(
        """
        SELECT r.id AS run_id, f.path AS file_path, p.workspace_id, r.created_at, p.retention_days
        FROM runs r
        LEFT JOIN files f ON r.id = f.run_id
        JOIN projects p ON r.project_id = p.id
        WHERE p.retention_days > 0  -- Skip projects with no retention
        """
    )

    runs_and_files = cursor.fetchall()
    runs_to_purge = []

    for row in runs_and_files:
        retention_days = row["retention_days"]
        cutoff_date = now - timedelta(days=retention_days)

        if row["created_at"] < cutoff_date:
            runs_to_purge.append(
                {
                    "run_id": row["run_id"],
                    "file_path": row["file_path"],
                    "workspace_id": row["workspace_id"],
                }
            )

    conn.close()
    return runs_to_purge


def delete_runs_and_files(run_ids: List[str]):
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        DELETE FROM files
        WHERE run_id = ANY(%s)
    """,
        (run_ids,),
    )

    cursor.execute(
        """
        DELETE FROM runs
        WHERE id = ANY(%s)
    """,
        (run_ids,),
    )

    conn.commit()
    conn.close()


def get_file_info(run_id: str, file_path: str) -> Optional[FileInfo]:
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id, name, path, size, created_at
        FROM files
        WHERE run_id = %s AND name = %s
        """,
        (run_id, file_path),
    )
    row = cursor.fetchone()
    conn.close()

    if row:
        return FileInfo(
            id=row["id"],
            name=row["name"],
            path=row["path"],
            size=row["size"],
            created_at=row["created_at"],
        )
    else:
        return None


def get_run_info(run_id: str) -> Optional[RunInfo]:
    conn = get_db_connection()
    cursor = conn.cursor()

    # Retrieve run information
    cursor.execute(
        """
        SELECT r.id, r.project_id, p.name AS project_name, r.public_access, r.created_at, 
               r.total_tests, r.passed, r.failed, r.skipped, r.verdict,
               r.start_time, r.end_time, r.failed_tests,
               f.id AS file_id, f.name, f.path, f.size, f.created_at AS file_created_at
        FROM runs r
        JOIN projects p ON r.project_id = p.id
        LEFT JOIN files f ON r.id = f.run_id
        WHERE r.id = %s
        """,
        (run_id,),
    )

    rows = cursor.fetchall()

    if not rows:
        conn.close()
        logger.error(f"No run found for run_id: {run_id}")
        return None

    # Initialize variables to collect run data and files
    files = []
    run_data = None

    for row in rows:
        if run_data is None:
            # Extract run data from the first row
            run_data = {
                "id": row["id"],
                "project_id": row["project_id"],
                "project_name": row["project_name"],
                "public_access": row["public_access"],
                "created_at": row["created_at"],
                "total_tests": row["total_tests"],
                "passed": row["passed"],
                "failed": row["failed"],
                "skipped": row["skipped"],
                "verdict": row["verdict"],
                "start_time": row["start_time"],
                "end_time": row["end_time"],
                "failed_tests": row["failed_tests"],
            }

        if row["file_id"]:
            # Collect file information
            files.append(
                FileInfo(
                    id=row["file_id"],
                    name=row["name"],
                    path=row["path"],
                    size=row["size"],
                    created_at=row["file_created_at"],
                )
            )

    # Retrieve tags associated with the run
    cursor.execute(
        """
        SELECT key, value
        FROM run_tags
        WHERE run_id = %s
        """,
        (run_id,),
    )
    tags_rows = cursor.fetchall()

    # Retrieve timing stats
    cursor.execute(
        """
        SELECT ee.type, ee.name, et.total_time, et.call_count, et.average_time, et.median_time, et.std_deviation
        FROM execution_times et
        JOIN execution_elements ee ON et.element_id = ee.id
        WHERE et.run_id = %s
        """,
        (run_id,),
    )
    timing_rows = cursor.fetchall()

    timing_stats: Dict[str, Dict[str, TimingStats]] = defaultdict(dict)
    for row in timing_rows:
        (
            element_type,
            name,
            total_time,
            call_count,
            average_time,
            median_time,
            std_deviation,
        ) = row
        timing_stats[element_type][name] = TimingStats(
            total_time=total_time,
            call_count=call_count,
            average_time=average_time,
            median_time=median_time,
            std_deviation=std_deviation,
        )

    conn.close()

    # Process tags into a dictionary
    tags = {row["key"]: row["value"] for row in tags_rows}

    if run_data:
        try:
            # Construct the RunInfo object with files and tags
            run_info = RunInfo(
                id=run_data["id"],
                project_id=run_data["project_id"],
                project_name=run_data["project_name"],
                public_access=run_data["public_access"],
                files=files,
                created_at=run_data["created_at"],
                total_tests=run_data["total_tests"],
                passed=run_data["passed"],
                failed=run_data["failed"],
                skipped=run_data["skipped"],
                verdict=run_data["verdict"],
                tags=tags,
                start_time=run_data["start_time"],
                end_time=run_data["end_time"],
                failed_test_names=run_data["failed_tests"] or [],
                timing_stats=dict(timing_stats),
            )
            logger.info(f"Retrieved run data for run_id: {run_id}")
            return run_info
        except ValueError as e:
            logger.error(f"Failed to create RunInfo object: {e}")
            logger.error(f"Run data: {run_data}")
            return None
    else:
        logger.error(f"Invalid run data for run_id: {run_id}")
        return None


def delete_run_info(run_id: str) -> bool:
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # Delete the run (this will cascade delete associated files and tags)
        cursor.execute("DELETE FROM runs WHERE id = %s", (run_id,))
        affected_rows: int = cursor.rowcount
        conn.commit()
        conn.close()
        return affected_rows > 0
    except psycopg2.Error as e:
        conn.rollback()
        logger.error(f"Error deleting run {run_id}: {e}")
        conn.close()
        return False


def list_project_runs(
    project_id: str,
    tag_filters: Optional[Dict[str, str]] = None,
    limit: Optional[int] = 10,
    offset: Optional[int] = 0,
) -> Tuple[List[RunInfo], int]:
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    params = [project_id]  # Start with project_id as the first parameter
    tag_joins = ""
    where_clauses = ["r.project_id = %s"]

    if tag_filters:
        index = 1
        for key, value in tag_filters.items():
            if key.lower() == "verdict":
                where_clauses.append("LOWER(r.verdict) = LOWER(%s)")
                params.append(value)
            else:
                tag_alias = f"rt{index}"
                tag_joins += f"""
                LEFT JOIN run_tags {tag_alias} ON r.id = {tag_alias}.run_id
                """
                where_clauses.append(
                    f"({tag_alias}.key = %s AND {tag_alias}.value = %s)"
                )
                params.extend([key, value])
                index += 1

    # Build the base query for reuse
    base_query = f"""
    FROM runs r
    {tag_joins}
    WHERE {' AND '.join(where_clauses)}
    """

    # First, get the total count of runs matching the filters
    count_query = f"SELECT COUNT(DISTINCT r.id) {base_query}"
    cursor.execute(count_query, params)
    total_results = cursor.fetchone()[0]

    logger.info(f"Total results: {total_results}")
    logger.info(f"Count query: {cursor.query.decode()}")
    logger.info(f"Query parameters: {params}")

    if total_results == 0:
        conn.close()
        return [], total_results

    # Now, get the run IDs with limit and offset
    run_ids_query = f"""
    SELECT DISTINCT r.id, r.created_at
    {base_query}
    ORDER BY r.created_at DESC
    LIMIT %s OFFSET %s
    """
    run_ids_params = params + [limit, offset]
    cursor.execute(run_ids_query, run_ids_params)
    logger.info(f"Run IDs query: {cursor.query.decode()}")
    logger.info(f"Run IDs query parameters: {run_ids_params}")
    run_id_rows = cursor.fetchall()
    run_ids = [row["id"] for row in run_id_rows]

    if not run_ids:
        conn.close()
        return [], total_results

    # Prepare placeholders for run IDs
    run_ids_placeholders = ",".join(["%s"] * len(run_ids))

    # Fetch runs data
    runs_query = f"""
    SELECT r.id, r.project_id, p.name AS project_name, r.public_access, r.created_at,
           r.total_tests, r.passed, r.failed, r.skipped, r.verdict,
           r.start_time, r.end_time, r.failed_tests
    FROM runs r
    JOIN projects p ON r.project_id = p.id
    WHERE r.id IN ({run_ids_placeholders})
    """
    cursor.execute(runs_query, run_ids)
    runs_rows = cursor.fetchall()

    # Build runs dictionary
    runs: Dict[str, RunInfo] = {}
    for row in runs_rows:
        run_id = row["id"]
        runs[run_id] = RunInfo(
            id=run_id,
            project_id=row["project_id"],
            project_name=row["project_name"],
            public_access=row["public_access"],
            files=[],
            created_at=row["created_at"],
            total_tests=row["total_tests"],
            passed=row["passed"],
            failed=row["failed"],
            skipped=row["skipped"],
            verdict=row["verdict"],
            tags={},
            start_time=row["start_time"],
            end_time=row["end_time"],
            failed_test_names=row["failed_tests"] or [],
            timing_stats={},  # Leave this empty for list views
        )

    # Fetch files associated with these runs
    files_query = f"""
    SELECT f.id AS file_id, f.name, f.path, f.size, f.created_at AS file_created_at, f.run_id
    FROM files f
    WHERE f.run_id IN ({run_ids_placeholders})
    """
    cursor.execute(files_query, run_ids)
    files_rows = cursor.fetchall()
    for row in files_rows:
        run_id = row["run_id"]
        if run_id in runs:
            runs[run_id].files.append(
                FileInfo(
                    id=row["file_id"],
                    name=row["name"],
                    path=row["path"],
                    size=row["size"],
                    created_at=row["file_created_at"],
                )
            )

    # Retrieve tags for the collected runs
    tags_query = f"""
    SELECT rt.run_id, rt.key, rt.value
    FROM run_tags rt
    WHERE rt.run_id IN ({run_ids_placeholders})
    """
    cursor.execute(tags_query, run_ids)
    tag_rows = cursor.fetchall()
    for row in tag_rows:
        run_id = row["run_id"]
        if run_id in runs:
            runs[run_id].tags[row["key"]] = row["value"]

    conn.close()

    # Since we fetched runs in order, but stored in a dict, we need to return them in the same order
    # Use the order from run_id_rows
    ordered_runs = [runs[row["id"]] for row in run_id_rows]

    return ordered_runs, total_results


def get_project_tags(project_id: str) -> Dict[str, List[str]]:
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT rt.key, rt.value
        FROM run_tags rt
        JOIN runs r ON rt.run_id = r.id
        WHERE r.project_id = %s
        """,
        (project_id,),
    )

    rows = cursor.fetchall()

    # Fetch unique verdicts
    cursor.execute(
        """
        SELECT DISTINCT verdict
        FROM runs
        WHERE project_id = %s AND verdict IS NOT NULL
        """,
        (project_id,),
    )
    verdict_rows = cursor.fetchall()

    conn.close()

    tags: Dict[str, Set[str]] = {
        "verdict": set(row["verdict"].lower() for row in verdict_rows if row["verdict"])
    }
    for row in rows:
        key = row["key"]
        value = row["value"]
        tags.setdefault(key, set()).add(value)

    # Convert sets to sorted lists
    tags_result: Dict[str, List[str]] = {k: sorted(v) for k, v in tags.items()}
    return tags_result


def add_file_to_run(
    run: RunInfo, file_path: str, object_name: str, file_size: int
) -> None:
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            INSERT INTO files (id, name, path, run_id, size)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (generate_urlsafe_id(), file_path, object_name, run.id, file_size),
        )
        conn.commit()
    except psycopg2.IntegrityError as e:
        conn.rollback()
        logger.error(f"File already exists in this run: {e}")
        raise
    finally:
        conn.close()
