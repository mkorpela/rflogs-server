from collections import defaultdict
import datetime
import gzip
from io import BytesIO
import os
import statistics
from typing import Dict, List, Optional

from .database.runs import update_run_info
from .storage import StorageManager
import xml.etree.ElementTree as ET
from .models import ParsedRunStats, Workspace, TimingStats
from .logging_config import get_logger

logger = get_logger(__name__)


def parse_output_xml_background(workspace: Workspace, run_id: str, object_name: str):
    stats = parse_output_xml(workspace, run_id, object_name)
    if stats and stats.total_tests > 0:
        update_run_info(run_id, stats)


def calculate_timing_stats(times: List[float]) -> TimingStats:
    total_time = sum(times)
    call_count = len(times)
    average_time = total_time / call_count if call_count > 0 else 0
    median_time = statistics.median(times) if times else 0
    std_deviation = statistics.stdev(times) if len(times) > 1 else 0

    return TimingStats(
        total_time=total_time,
        call_count=call_count,
        average_time=average_time,
        median_time=median_time,
        std_deviation=std_deviation,
    )


def process_timing_data(timing_data: Dict[str, List[float]]) -> Dict[str, TimingStats]:
    return {name: calculate_timing_stats(times) for name, times in timing_data.items()}


def parse_output_xml(
    workspace: Workspace, run_id: str, object_name: str
) -> Optional[ParsedRunStats]:
    logger.info("PARSING", run_id=run_id, object_name=object_name)
    storage_manager = StorageManager(
        workspace, backend=os.getenv("STORAGE_BACKEND", "s3")
    )
    file_obj = storage_manager.download_file(object_name)

    if not file_obj:
        logger.error(f"Failed to download file {object_name} for parsing")
        return None

    stats: ParsedRunStats = ParsedRunStats(
        total_tests=0,
        passed=0,
        failed=0,
        skipped=0,
        verdict="unknown",
        start_time=None,
        end_time=None,
        failed_test_names=[],
    )

    def iterfile():
        file_obj.seek(0)
        is_gzipped = file_obj.read(2) == b"\x1f\x8b"
        file_obj.seek(0)

        if is_gzipped:
            with gzip.GzipFile(fileobj=file_obj, mode="rb") as gz_file:
                while chunk := gz_file.read(8192):
                    yield chunk
        else:
            while chunk := file_obj.read(8192):
                yield chunk

    timing_stats: Dict[str, Dict[str, List[float]]] = {
        "suite": defaultdict(list),
        "test": defaultdict(list),
        "keyword": defaultdict(list),
    }

    suite_stack: List[str] = []

    def construct_keyword_name(elem: ET.Element) -> str:
        name = elem.get("name", "")
        owner = elem.get("owner", "")
        return f"{owner}.{name}" if owner else name

    try:
        context = ET.iterparse(BytesIO(b"".join(iterfile())), events=("start", "end"))
        inside_statistics = False
        inside_total = False
        start_time_str = None
        elapsed_time = 0.0
        current_test_name = None
        current_test_status = None

        for event, elem in context:
            if event == "start":
                if elem.tag == "statistics":
                    inside_statistics = True
                elif inside_statistics and elem.tag == "total":
                    inside_total = True
                elif elem.tag == "test":
                    current_test_name = elem.get("name")
                    current_test_status = None
                elif elem.tag == "suite":
                    suite_stack.append(elem.get("name", ""))
            elif event == "end":
                if elem.tag == "suite":
                    full_suite_name = ".".join(suite_stack)
                    if full_suite_name:
                        timing_stats["suite"][full_suite_name].append(elapsed_time)
                    suite_stack.pop()
                elif elem.tag == "test":
                    full_test_name = ".".join(suite_stack + [elem.get("name", "")])
                    timing_stats["test"][full_test_name].append(elapsed_time)
                    if current_test_status == "FAIL" and current_test_name:
                        stats.failed_test_names.append(current_test_name)
                    current_test_name = None
                    current_test_status = None
                elif elem.tag == "kw":
                    keyword_name = construct_keyword_name(elem)
                    timing_stats["keyword"][keyword_name].append(elapsed_time)
                elif elem.tag == "status":
                    current_test_status = elem.get("status")
                    start_time_str = elem.get("start")
                    elapsed_time = float(elem.get("elapsed", 0))
                elif elem.tag == "statistics":
                    inside_statistics = False
                elif inside_statistics and elem.tag == "total":
                    inside_total = False
                elif inside_total and elem.tag == "stat":
                    if elem.text == "All Tests":
                        print("Processing 'All Tests' stat")
                        stats.total_tests = (
                            int(elem.attrib.get("pass", 0))
                            + int(elem.attrib.get("fail", 0))
                            + int(elem.attrib.get("skip", 0))
                        )
                        stats.passed = int(elem.attrib.get("pass", 0))
                        stats.failed = int(elem.attrib.get("fail", 0))
                        stats.skipped = int(elem.attrib.get("skip", 0))
                elem.clear()

        # Process start and end times
        if start_time_str:
            stats.start_time = datetime.datetime.fromisoformat(
                start_time_str.replace("Z", "+00:00")
            )
            stats.end_time = stats.start_time + datetime.timedelta(seconds=elapsed_time)

        stats.verdict = "pass" if stats.failed == 0 else "fail"
        calculated_timing_stats = {}
        for element_type, elements in timing_stats.items():
            calculated_timing_stats[element_type] = {
                name: calculate_timing_stats(times) for name, times in elements.items()
            }

        stats.timing_stats = calculated_timing_stats

    except Exception as e:
        logger.error(f"Error parsing output XML for run {run_id}: {str(e)}")
        stats.verdict = "error"

    return stats
