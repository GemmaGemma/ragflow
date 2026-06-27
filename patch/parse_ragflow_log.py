import argparse
import ast
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}\b")
THREAD_RE = re.compile(r"^\S+ \S+\s+\S+\s+(\d+)\s+")
API_ID_PATTERNS = [
    re.compile(r"apiResponseId[:：](\d+)"),
    re.compile(r'"apiResponseId"\s*:\s*"(\d+)"'),
    re.compile(r"'apiResponseId'\s*:\s*'?(\\d+)'?"),
]


def extract_api_response_id(text: str) -> Optional[str]:
    for pattern in API_ID_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1)
    return None


def read_events(log_path: Path) -> List[str]:
    events: List[str] = []
    current: List[str] = []
    with log_path.open("r", encoding="utf-8", errors="replace") as f:
        for raw_line in f:
            line = raw_line.rstrip("\n")
            if TIMESTAMP_RE.match(line):
                if current:
                    events.append("\n".join(current))
                current = [line]
            else:
                if current:
                    current.append(line)
                else:
                    current = [line]
        if current:
            events.append("\n".join(current))
    return events


def safe_json_loads(text: str) -> Optional[Any]:
    try:
        return json.loads(text)
    except Exception:
        return None


def safe_literal_eval(text: str) -> Optional[Any]:
    try:
        return ast.literal_eval(text)
    except Exception:
        return None


def shorten(text: Optional[str], limit: int = 240) -> str:
    if not text:
        return ""
    one_line = re.sub(r"\s+", " ", text).strip()
    return one_line if len(one_line) <= limit else one_line[:limit] + "..."


def extract_thread_id(event: str) -> str:
    match = THREAD_RE.match(event)
    return match.group(1) if match else "unknown"


def extract_timestamp(event: str) -> str:
    first_line = event.splitlines()[0]
    return " ".join(first_line.split()[:2])


def parse_component_event(event: str) -> Dict[str, Any]:
    result: Dict[str, Any] = {"raw": event}
    component_match = re.search(r'"component_name":\s*"([^"]+)"', event)
    if component_match:
        result["component_name"] = component_match.group(1)

    params_match = re.search(r'"params":\s*(\{.*?\})\s*,\s*"output":', event, re.S)
    if params_match:
        params_text = params_match.group(1)
        params_obj = safe_json_loads(params_text)
        result["params"] = params_obj if isinstance(params_obj, dict) else None

    history_match = re.search(r", history: (.*?), kwargs:", event, re.S)
    if history_match:
        history_text = history_match.group(1).strip()
        result["history_raw"] = history_text
        history_obj = safe_json_loads(history_text)
        if history_obj is None:
            history_obj = safe_literal_eval(history_text)
        result["history"] = history_obj

    kwargs_match = re.search(r", kwargs: (.*)$", event, re.S)
    if kwargs_match:
        kwargs_text = kwargs_match.group(1).strip()
        result["kwargs_raw"] = kwargs_text
        kwargs_obj = safe_json_loads(kwargs_text)
        if kwargs_obj is None:
            kwargs_obj = safe_literal_eval(kwargs_text)
        result["kwargs"] = kwargs_obj

    return result


def parse_request_options(event: str) -> Dict[str, Any]:
    payload = event.split("Request options:", 1)[1].strip()
    parsed = safe_literal_eval(payload)
    return parsed if isinstance(parsed, dict) else {"raw": payload}


def parse_llm_response(event: str) -> Dict[str, Any]:
    payload = event.split("LLM response [", 1)[1]
    stage, rest = payload.split("]:", 1)
    parsed = safe_json_loads(rest.strip())
    result: Dict[str, Any] = {"stage": stage.strip(), "raw": rest.strip()}
    if isinstance(parsed, dict):
        result["json"] = parsed
    return result


def extract_user_message(history_obj: Any) -> Optional[str]:
    if not isinstance(history_obj, list):
        return None
    for item in reversed(history_obj):
        if isinstance(item, (list, tuple)) and len(item) >= 2 and item[0] == "user":
            return str(item[1])
        if isinstance(item, dict) and item.get("role") == "user":
            return str(item.get("content", ""))
    return None


@dataclass
class RequestTrace:
    api_response_id: str
    thread_id: Optional[str] = None
    first_seen: Optional[str] = None
    generate_event: Optional[Dict[str, Any]] = None
    request_options: List[Dict[str, Any]] = field(default_factory=list)
    llm_responses: List[Dict[str, Any]] = field(default_factory=list)
    component_events: List[Dict[str, Any]] = field(default_factory=list)
    http_requests: List[str] = field(default_factory=list)
    http_responses: List[str] = field(default_factory=list)
    raw_events: List[str] = field(default_factory=list)

    def to_summary_dict(self) -> Dict[str, Any]:
        generate_params = self.generate_event.get("params") if self.generate_event else None
        llm_id = None
        if isinstance(generate_params, dict):
            llm_id = generate_params.get("llm_id")

        history_obj = self.generate_event.get("history") if self.generate_event else None
        kwargs_obj = self.generate_event.get("kwargs") if self.generate_event else None
        llm_outputs: List[Dict[str, Any]] = []
        for item in self.llm_responses:
            payload = item.get("json")
            content = None
            if isinstance(payload, dict):
                try:
                    content = payload["choices"][0]["message"]["content"]
                except Exception:
                    content = None
            llm_outputs.append(
                {
                    "stage": item.get("stage"),
                    "content": content,
                }
            )

        return {
            "apiResponseId": self.api_response_id,
            "thread_id": self.thread_id,
            "first_seen": self.first_seen,
            "llm_id": llm_id,
            "kwargs": kwargs_obj,
            "request_options": self.request_options,
            "llm_outputs": llm_outputs,
            "http_requests": self.http_requests,
            "http_responses": self.http_responses,
        }


def parse_log(log_path: Path) -> Dict[str, RequestTrace]:
    events = read_events(log_path)
    traces: Dict[str, RequestTrace] = {}
    current_api_by_thread: Dict[str, str] = {}

    for event in events:
        thread_id = extract_thread_id(event)
        timestamp = extract_timestamp(event)
        api_response_id = extract_api_response_id(event)

        if "component_name" in event and "history:" in event and "kwargs:" in event:
            component = parse_component_event(event)
            component_api_id = api_response_id
            if not component_api_id and component.get("history_raw"):
                component_api_id = extract_api_response_id(component["history_raw"])

            if component_api_id:
                current_api_by_thread[thread_id] = component_api_id
                trace = traces.setdefault(component_api_id, RequestTrace(api_response_id=component_api_id))
                trace.thread_id = thread_id
                trace.first_seen = trace.first_seen or timestamp
                trace.component_events.append(component)
                trace.raw_events.append(event)
                if component.get("component_name") == "Generate" and trace.generate_event is None:
                    trace.generate_event = component
            continue

        trace_api_id = api_response_id or current_api_by_thread.get(thread_id)
        if not trace_api_id:
            continue

        trace = traces.setdefault(trace_api_id, RequestTrace(api_response_id=trace_api_id))
        trace.thread_id = trace.thread_id or thread_id
        trace.first_seen = trace.first_seen or timestamp
        trace.raw_events.append(event)

        if "Request options:" in event:
            trace.request_options.append(parse_request_options(event))
        elif "LLM response [" in event:
            trace.llm_responses.append(parse_llm_response(event))
        elif "Sending HTTP Request:" in event or 'HTTP Request: POST ' in event:
            trace.http_requests.append(event)
        elif "HTTP Response:" in event:
            trace.http_responses.append(event)

    return traces


def write_markdown(traces: Dict[str, RequestTrace], output_path: Path) -> None:
    lines: List[str] = []
    lines.append("# RAGFlow Generate Input / LLM Output Pairs")
    lines.append("")
    lines.append(f"Total grouped requests: {len(traces)}")
    lines.append("")

    for api_id in sorted(traces):
        summary = traces[api_id].to_summary_dict()
        lines.append(f"## apiResponseId `{api_id}`")
        lines.append("")
        lines.append(f"- First seen: `{summary['first_seen']}`")
        lines.append(f"- Thread: `{summary['thread_id']}`")
        lines.append(f"- LLM ID: `{summary['llm_id']}`")
        lines.append("### LLM Output")
        lines.append("")
        if summary["llm_outputs"]:
            for idx, item in enumerate(summary["llm_outputs"], start=1):
                lines.append(f"#### Output {idx} (`{item.get('stage')}`)")
                lines.append("")
                lines.append("```json")
                lines.append(item.get("content") or "")
                lines.append("```")
                lines.append("")
        else:
            lines.append("_No LLM response log found for this apiResponseId._")
            lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")


def write_json(traces: Dict[str, RequestTrace], output_path: Path) -> None:
    payload = {api_id: trace.to_summary_dict() for api_id, trace in sorted(traces.items())}
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Group RAGFlow logs by apiResponseId and pair Generate input with LLM output.")
    parser.add_argument("--log", default="ragflow_server.log", help="Path to the ragflow log file")
    parser.add_argument("--out-md", default="ragflow_generate_llm_pairs.md", help="Output markdown path")
    parser.add_argument("--out-json", default="ragflow_generate_llm_pairs.json", help="Output json path")
    parser.add_argument("--api-id", default=None, help="Only export one apiResponseId")
    args = parser.parse_args()

    traces = parse_log(Path(args.log))
    traces = {k: v for k, v in traces.items() if v.llm_responses}
    if args.api_id:
        traces = {k: v for k, v in traces.items() if k == args.api_id}

    write_markdown(traces, Path(args.out_md))
    write_json(traces, Path(args.out_json))
    print("grouped requests:", len(traces))
    print("markdown:", Path(args.out_md).resolve())
    print("json:", Path(args.out_json).resolve())


if __name__ == "__main__":
    main()
