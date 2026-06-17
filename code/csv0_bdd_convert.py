import csv
import os
import sys
from typing import Dict, List, Tuple, Optional


TEST_CASE_START = "TEST_CASE_START"
TEST_CASE_END = "TEST_CASE_END"

INDENT = "  "  # 2 spaces
DOUBLE_INDENT = f"{INDENT}{INDENT}"


# Columns to exclude from content (non-# fields)
CONTENT_EXCLUDE = {
    "Status", "ID", "PreviousID", "step_type", "Action", "User",
    "protocol", "startTime", "tomStartTime",
    "Symbol", "ContractCode",
    "Text",
}


def parse_single_cell_line(line: str) -> str:
    """
    Parse a line that is expected to contain a single CSV field, possibly quoted and containing commas.
    Returns the field value without surrounding quotes.
    """
    # Use CSV reader for correctness with quotes and embedded commas
    row = next(csv.reader([line], skipinitialspace=False))
    if not row:
        return ""
    # If more than one field exists unexpectedly, join them with commas to preserve original meaning
    if len(row) == 1:
        return row[0]
    return ",".join(row)


def is_blank_or_empty_marker(line: str) -> bool:
    s = line.strip()
    return s == "" or s == '""'


def normalize_step_type(value: str) -> str:
    v = (value or "").strip().lower()
    if v == "assertion":
        return "assertion"
    if v == "send":
        return "send"
    # Default as per requirement
    return "send"


def safe_get(row: Dict[str, str], key: str) -> str:
    return (row.get(key, "") or "").strip()


def build_tags_line(tags_cell: str) -> str:
    # tags_cell example: "storyId:...,StoryId:...,ZephyrId:..."
    parts = [p.strip() for p in tags_cell.split(",") if p.strip()]
    if not parts:
        raise ValueError("Tag line is empty but tags are required.")
    return " ".join(f"@{p}" for p in parts)


def resolve_security(row: Dict[str, str], last_security: str) -> Tuple[str, str]:
    """
    Resolve security for current row, with inheritance.
    Returns (security, updated_last_security).
    """
    symbol = safe_get(row, "Symbol")
    contract_code = safe_get(row, "ContractCode")
    security = symbol
    # if not security:
    #     security = last_security  # inherit
    # if not security:
    #     raise ValueError("Cannot resolve security: Symbol and ContractCode are empty and no inherited security exists.")
    # Update last_security only when we resolved a non-empty value (inherited counts too)
    return security, contract_code


def extract_identifier(row: Dict[str, str]) -> str:
    # Per requirement, only #_id is used
    return safe_get(row, "#_id")


def extract_previous_id_for_content(row: Dict[str, str]) -> str:
    # Per requirement, use #_previousID, and output as "PreviousID: <value>"
    return safe_get(row, "#_previousID")


def build_content(row: Dict[str, str], header: List[str]) -> str:
    items: List[Tuple[str, str]] = []

    # Add regular fields in header order
    for col in header:
        if col in CONTENT_EXCLUDE:
            continue
        if col.startswith("#"):
            continue  # auxiliary fields excluded from content
        val = safe_get(row, col)
        if not val:
            continue
        items.append((col, val))

    # Special handling: include PreviousID from #_previousID if present
    prev_aux = extract_previous_id_for_content(row)
    if prev_aux:
        items.append(("PreviousID", prev_aux))

    if not items:
        return "N/A"

    return ", ".join(f"{k}: {v}" for k, v in items)


def render_send_step(
    row: Dict[str, str],
    header: List[str],
    is_first_market_session: bool,
    when_started: bool,
    last_security: str,
) -> Tuple[Optional[str], bool, bool, str]:
    """
    Render a send step.
    Returns (rendered_line_or_None, updated_is_first_market_session_flag, updated_when_started, updated_last_security)
    """
    action = safe_get(row, "Action")
    if not action:
        raise ValueError("Action is empty in a send step.")

    if action == "MncCreateSchedule":
        # Skip as required
        # security, contract_code = resolve_security(row, last_security)  # keep inheritance updated if possible
        return None, is_first_market_session, when_started, last_security

    security, contract_code = resolve_security(row, last_security)

    if action == "MarketSession":
        status = safe_get(row, "Status")
        if not status:
            raise ValueError("MarketSession step requires non-empty Status.")
        if is_first_market_session:
            keyword = "Given"
            is_first_market_session = False
        else:
            # If additional MarketSession exists, treat it as And once the scenario has started
            keyword = "And" if when_started else "Given"
        if security:
            text = f"session transit to {status} for security {security}"
        elif contract_code:
            text = f"session transit to {status} for contract {contract_code}"
        return f"{DOUBLE_INDENT}{keyword} {text}", is_first_market_session, when_started, last_security

    # Non-MarketSession send action
    user = safe_get(row, "User")
    if not user:
        raise ValueError(f"Send step '{action}' requires non-empty User.")

    keyword = "When" if not when_started else "And"
    when_started = True

    content = build_content(row, header)
    identifier = extract_identifier(row)
    text = f"{user} sends a {action} with content {content} for security {security}"
    if action == "MassCancel" and contract_code:
        text = f"{user} sends a {action} with content {content} for contract {contract_code}"
    if identifier:
        text += f" identified as {identifier}"

    return f"{DOUBLE_INDENT}{keyword} {text}", is_first_market_session, when_started, last_security


def render_assertion_step(
    row: Dict[str, str],
    header: List[str],
    is_first_assertion: bool,
    last_security: str,
) -> Tuple[str, bool, str]:
    """
    Render an assertion step.
    Returns (rendered_line, updated_is_first_assertion, updated_last_security)
    """
    action = safe_get(row, "Action")
    if not action:
        raise ValueError("Action is empty in an assertion step.")

    # security, contract_code = resolve_security(row, last_security)

    keyword = "Then" if is_first_assertion else "And"
    is_first_assertion = False

    protocol = safe_get(row, "protocol").upper()
    content = build_content(row, header)
    identifier = extract_identifier(row)

    if protocol in {"OMD", "Kafka"}:
        system = "MarketData" if protocol == "OMD" else "Kafka"
        not_exist = safe_get(row, "#_not_exist").upper() == "Y"

        ch = safe_get(row, "#_channel")
        t_from = safe_get(row, "#_from_time")
        t_to = safe_get(row, "#_to_time")

        if not ch or not t_from or not t_to:
            raise ValueError("OMD/Kafka assertion requires #_channel, #_from_time, #_to_time to be non-empty.")

        verb = "not publish" if not_exist else "publish"
        text = (
            f"{system} will {verb} {action} with content {content} "
            f"from {t_from} to {t_to} in channel {ch}"
        )
        if identifier:
            text += f" identified as {identifier}"

        return f"{DOUBLE_INDENT}{keyword} {text}", is_first_assertion, last_security

    # Fallback for other protocols: "will receive"
    user = safe_get(row, "User") or "System"
    text = f"{user} will receive {action} with content {content}"
    if identifier:
        text += f" identified as {identifier}"
    return f"{DOUBLE_INDENT}{keyword} {text}", is_first_assertion, last_security


def parse_test_cases(lines: List[str]) -> List[Dict]:
    """
    Parse raw lines into a list of test case objects:
    {
        "scenario": str,
        "tags": str,
        "header": [col...],
        "rows": [dict...]
    }
    """
    cases: List[Dict] = []
    i = 0
    n = len(lines)

    def strip_nl(s: str) -> str:
        return s.rstrip("\r\n")

    while i < n:
        line = strip_nl(lines[i])
        if line.strip() != TEST_CASE_START:
            i += 1
            continue

        # Start a new test case
        i += 1
        if i >= n:
            raise ValueError("Unexpected EOF after TEST_CASE_START.")

        # Ignore numeric line after TEST_CASE_START
        # (If not numeric, still ignore as per requirement: a single useless line exists)
        _ignored = strip_nl(lines[i])
        i += 1
        if i >= n:
            raise ValueError("Unexpected EOF while reading test case header.")

        # Ignore the optional single blank/"" line (as per earlier clarification)
        if is_blank_or_empty_marker(strip_nl(lines[i])):
            i += 1
            if i >= n:
                raise ValueError("Unexpected EOF after blank/empty marker line.")

        # Ignore "TC Symbol" line
        if strip_nl(lines[i]).strip() != "TC Symbol":
            raise ValueError(f"Expected 'TC Symbol' line, but got: {strip_nl(lines[i])}")
        i += 1
        if i >= n:
            raise ValueError("Unexpected EOF after TC Symbol.")

        # Scenario line (single CSV field, keep full text like "Description,xxx")
        scenario_line = strip_nl(lines[i])
        scenario = parse_single_cell_line(scenario_line).strip()
        if not scenario:
            raise ValueError("Scenario name is empty.")
        i += 1
        if i >= n:
            raise ValueError("Unexpected EOF after Scenario line.")

        # Tags line (single CSV field)
        tags_line_raw = strip_nl(lines[i])
        tags_cell = parse_single_cell_line(tags_line_raw).strip()
        tags = build_tags_line(tags_cell)
        i += 1
        if i >= n:
            raise ValueError("Unexpected EOF after Tags line.")

        # Header line
        header_line = strip_nl(lines[i])
        header = next(csv.reader([header_line]))
        header = [h.strip() for h in header if h is not None]
        if not header:
            raise ValueError("Header line is empty.")
        i += 1
        if i >= n:
            raise ValueError("Unexpected EOF after Header line.")

        # Step rows until TEST_CASE_END
        rows: List[Dict[str, str]] = []
        while i < n:
            cur = strip_nl(lines[i])
            if cur.strip() == TEST_CASE_END:
                i += 1
                break
            if cur.strip() == "":
                i += 1
                continue

            values = next(csv.reader([cur], skipinitialspace=False))
            # Normalize row length to header length (pad with empty strings if needed)
            if len(values) < len(header):
                values = values + [""] * (len(header) - len(values))
            elif len(values) > len(header):
                # Keep extra fields by appending them to the last column (rare, but safer than dropping)
                tail = values[len(header) - 1:]
                values = values[: len(header) - 1] + [",".join(tail)]

            row = {header[idx]: (values[idx] if idx < len(values) else "") for idx in range(len(header))}
            rows.append(row)
            i += 1

        else:
            # Loop ended without TEST_CASE_END
            raise ValueError("Missing TEST_CASE_END for a test case.")

        cases.append({
            "scenario": scenario,
            "tags": tags,
            "header": header,
            "rows": rows,
        })

    return cases


def convert_to_feature(cases: List[Dict], feature_name: str) -> str:
    out_lines: List[str] = []
    out_lines.append(f"Feature: {feature_name}")

    for case in cases:
        tags = case["tags"]
        scenario = case["scenario"]
        header = case["header"]
        rows = case["rows"]

        out_lines.append(f"{INDENT}{tags}")
        out_lines.append(f"{INDENT}Scenario: {scenario}")

        is_first_market_session = True
        when_started = False
        is_first_assertion = True
        last_security = ""

        for row in rows:
            step_type = normalize_step_type(safe_get(row, "step_type"))
            action = safe_get(row, "Action")
            if not action:
                raise ValueError("Action is empty in step row.")

            if step_type == "send":
                rendered, is_first_market_session, when_started, last_security = render_send_step(
                    row=row,
                    header=header,
                    is_first_market_session=is_first_market_session,
                    when_started=when_started,
                    last_security=last_security,
                )
                if rendered:
                    out_lines.append(rendered)
            else:
                rendered, is_first_assertion, last_security = render_assertion_step(
                    row=row,
                    header=header,
                    is_first_assertion=is_first_assertion,
                    last_security=last_security,
                )
                out_lines.append(rendered)
        out_lines.append("\n")

    # Ensure ending newline
    return "\n".join(out_lines) + "\n"


def csv_bdd(in_path:str):
    base = os.path.basename(in_path)
    feature_name = os.path.splitext(base)[0]  # no extension
    dir_path = os.path.dirname(in_path)
    out_path = os.path.join(dir_path, f"{feature_name}.feature")

    with open(in_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    cases = parse_test_cases(lines)
    feature_text = convert_to_feature(cases, feature_name)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(feature_text)

if __name__ == "__main__":
    csv_bdd(fr"C:\Users\junfeng.ye\Documents\code\tools\zephyr\files\bdd_example.csv")
