from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

from .database import connect, now_text, setting, set_setting


MODEL_BINARY = Path(os.getenv("LOCAL_MODEL_BINARY", "/opt/tender-local-model/bin/llama-cli"))
MODEL_FILE = Path(os.getenv("LOCAL_MODEL_PATH", "/opt/tender-local-model/models/qwen2.5-coder-0.5b-instruct-q4_k_m.gguf"))
MODEL_TIMEOUT = max(30, min(int(os.getenv("LOCAL_MODEL_TIMEOUT", "90")), 180))
MAX_ATTEMPTS = 2
ALLOWED_CATEGORIES = ("信息化", "数字化", "软件实施", "人力外包", "其他")


def model_available() -> bool:
    return setting("local_model_enabled", "1") == "1" and MODEL_BINARY.is_file() and MODEL_FILE.is_file()


def model_status() -> dict:
    with connect() as db:
        counts = {row["status"]: row["count"] for row in db.execute(
            "SELECT status,COUNT(*) AS count FROM model_tasks GROUP BY status"
        )}
        latest = db.execute("SELECT * FROM model_tasks ORDER BY id DESC LIMIT 1").fetchone()
    return {
        "available": model_available(),
        "model_name": "Qwen2.5-Coder-0.5B-Instruct Q4_K_M",
        "queued": counts.get("queued", 0),
        "running": counts.get("running", 0),
        "completed": counts.get("completed", 0),
        "failed": counts.get("failed", 0),
        "latest": dict(latest) if latest else None,
        "message": setting("local_model_message", ""),
    }


def enqueue_task(task_type: str, reference_id: str, payload: dict) -> bool:
    if task_type not in {"adapt_site", "analyze_tender"}:
        raise ValueError("unsupported model task")
    if setting("local_model_enabled", "1") != "1":
        return False
    with connect() as db:
        exists = db.execute(
            "SELECT 1 FROM model_tasks WHERE task_type=? AND reference_id=? AND status IN ('queued','running')",
            (task_type, str(reference_id)),
        ).fetchone()
        if exists:
            return False
        db.execute(
            "INSERT INTO model_tasks(task_type,reference_id,payload_json,created_at) VALUES(?,?,?,?)",
            (task_type, str(reference_id), json.dumps(payload, ensure_ascii=False), now_text()),
        )
    return True


def enqueue_site_adaptation(site_id: int, reason: str) -> bool:
    return enqueue_task("adapt_site", str(site_id), {"reason": reason[:500]})


def enqueue_tender_analysis(fingerprint: str) -> bool:
    return enqueue_task("analyze_tender", fingerprint, {})


def _extract_json(text: str) -> dict:
    # Recent llama-cli builds may echo the prompt's JSON example before the
    # generated object. Decode every complete object and trust only the last
    # one; task-specific code still applies strict selector/category allowlists.
    decoder = json.JSONDecoder()
    objects = []
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            value, _end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            objects.append(value)
    if not objects:
        raise ValueError("model did not return a complete JSON object")
    return objects[-1]


def _infer(prompt: str, max_tokens: int = 256) -> dict:
    if not model_available():
        raise RuntimeError("local model is not installed or enabled")
    command = [
        str(MODEL_BINARY), "-m", str(MODEL_FILE), "-c", "2048", "-n", str(max_tokens),
        "-t", os.getenv("LOCAL_MODEL_THREADS", "1"), "--temp", "0.1", "--top-p", "0.8",
        "--no-display-prompt", "--simple-io", "--single-turn", "-p", prompt[:16000],
    ]
    def resource_limits() -> None:
        try:
            import resource
            os.nice(10)
            memory_limit = max(1050, min(int(os.getenv("LOCAL_MODEL_MEMORY_MB", "1050")), 1200)) * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (memory_limit, memory_limit))
        except (ImportError, OSError, ValueError):
            pass

    runtime_env = {**os.environ, "OMP_NUM_THREADS": os.getenv("LOCAL_MODEL_THREADS", "1")}
    runtime_env["LD_LIBRARY_PATH"] = str(MODEL_BINARY.parent) + (
        ":" + runtime_env["LD_LIBRARY_PATH"] if runtime_env.get("LD_LIBRARY_PATH") else ""
    )
    completed = subprocess.run(
        command, capture_output=True, text=True, timeout=MODEL_TIMEOUT,
        env=runtime_env,
        preexec_fn=resource_limits if os.name == "posix" else None,
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or "model process failed")[-300:])
    return _extract_json(completed.stdout)


def _selector_candidates(html: str) -> tuple[BeautifulSoup, list[dict]]:
    soup = BeautifulSoup(html[:1_500_000], "html.parser")
    for tag in soup(["script", "style", "svg", "noscript", "iframe"]):
        tag.decompose()
    selectors: list[str] = ["a[href]"]
    for link in soup.select("a[href]")[:200]:
        for node in (link, link.parent, link.find_parent(["li", "tr", "article", "div"])):
            if not node or not getattr(node, "name", None):
                continue
            if node is not link and node.name in {"li", "tr", "article"}:
                selectors.append(f"{node.name} a[href]")
            classes = [item for item in node.get("class", []) if re.fullmatch(r"[A-Za-z_-][\w-]{1,48}", item)]
            if classes:
                selectors.append(f"{node.name}.{'.'.join(classes[:2])} a[href]" if node is not link else f"a.{'.'.join(classes[:2])}")
    results = []
    for selector in dict.fromkeys(selectors):
        try:
            links = soup.select(selector)
        except Exception:
            continue
        usable = [link for link in links if len(" ".join(link.get_text(" ", strip=True).split())) >= 8]
        if len(usable) < 3:
            continue
        samples = [" ".join(item.get_text(" ", strip=True).split())[:120] for item in usable[:5]]
        results.append({"selector": selector, "count": len(usable), "samples": samples})
    results.sort(key=lambda item: (item["selector"] == "a[href]", -min(item["count"], 100)))
    return soup, results[:8]


def _adapt_site(reference_id: str) -> dict:
    from .connectors.custom import validate_public_url

    with connect() as db:
        site = db.execute("SELECT * FROM custom_sites WHERE id=?", (int(reference_id),)).fetchone()
    if not site or site["builtin_code"]:
        raise ValueError("site is missing or managed by a built-in collector")
    safe_url = validate_public_url(site["url"])
    request = Request(safe_url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(request, timeout=20) as response:
        html = response.read(2_000_000).decode(response.headers.get_content_charset() or "utf-8", "replace")
    soup, candidates = _selector_candidates(html)
    if not candidates:
        raise ValueError("no safe selector candidates")
    prompt = (
        "你是公告采集规则选择器。只能从候选列表选择一项，不得创建新选择器。"
        "优先选择包含真实招标、采购、公告标题且数量合理的候选。"
        "只输出JSON：{\"selector\":\"候选值\",\"confidence\":0到100,\"reason\":\"简短原因\"}。\n候选："
        + json.dumps(candidates, ensure_ascii=False)
    )
    result = _infer(prompt)
    allowed = {item["selector"] for item in candidates}
    selector = str(result.get("selector", ""))
    confidence = max(0, min(int(result.get("confidence", 0)), 100))
    if selector not in allowed or confidence < 65:
        raise ValueError("model confidence is too low")
    links = [item for item in soup.select(selector) if item.get("href") and len(item.get_text(" ", strip=True)) >= 8]
    if len(links) < 3:
        raise ValueError("selector replay validation failed")
    note = f"低资源本地模型从 {len(candidates)} 组候选中选择并回放验证：{len(links)} 条，置信度 {confidence}%。"
    with connect() as db:
        db.execute(
            "UPDATE custom_sites SET enabled=1,engine=?,status='已适配（静态列表）',list_selector=?,profile_note=?,failure_count=0,last_adapted_at=? WHERE id=?",
            ("规则引擎 + 本地小模型", selector, note, now_text(), int(reference_id)),
        )
    return {"selector": selector, "confidence": confidence, "validated_count": len(links)}


def _analyze_tender(reference_id: str) -> dict:
    with connect() as db:
        row = db.execute("SELECT title,excerpt,match_reason FROM tenders WHERE fingerprint=?", (reference_id,)).fetchone()
    if not row:
        raise ValueError("tender is missing")
    text = " ".join((row["title"], row["excerpt"], row["match_reason"]))[:2500]
    prompt = (
        "根据招采文本进行保守分类和一句话摘要。分类只能是：信息化、数字化、软件实施、人力外包、其他。"
        "只输出JSON：{\"category\":\"分类\",\"summary\":\"不超过60字\",\"confidence\":0到100}。\n文本：" + text
    )
    result = _infer(prompt, 160)
    category = str(result.get("category", "其他"))
    if category not in ALLOWED_CATEGORIES:
        category = "其他"
    summary = " ".join(str(result.get("summary", "")).split())[:120]
    confidence = max(0, min(int(result.get("confidence", 0)), 100))
    with connect() as db:
        db.execute("UPDATE tenders SET ai_category=?,ai_summary=?,ai_confidence=? WHERE fingerprint=?",
                   (category, summary, confidence, reference_id))
    return {"category": category, "summary": summary, "confidence": confidence}


def process_pending(limit: int = 5) -> int:
    if not model_available():
        return 0
    lock_handle = None
    try:
        import fcntl
        lock_path = Path(os.getenv("LOCAL_MODEL_LOCK", "/tmp/tender-local-model.lock"))
        lock_handle = lock_path.open("w")
        fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except ImportError:
        lock_handle = None
    except OSError:
        if lock_handle:
            lock_handle.close()
        return 0
    processed = 0
    limit = max(1, min(limit, 20))
    for _ in range(limit):
        with connect() as db:
            task = db.execute("SELECT * FROM model_tasks WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
            if not task:
                break
            claimed = db.execute(
                "UPDATE model_tasks SET status='running',started_at=?,attempts=attempts+1 WHERE id=? AND status='queued'",
                (now_text(), task["id"]),
            ).rowcount
        if not claimed:
            continue
        try:
            if task["task_type"] == "adapt_site":
                result = _adapt_site(task["reference_id"])
            else:
                result = _analyze_tender(task["reference_id"])
            with connect() as db:
                db.execute("UPDATE model_tasks SET status='completed',result_json=?,message=?,finished_at=? WHERE id=?",
                           (json.dumps(result, ensure_ascii=False), "自动处理完成", now_text(), task["id"]))
            processed += 1
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"[:500]
            retry = task["attempts"] + 1 < MAX_ATTEMPTS
            with connect() as db:
                db.execute("UPDATE model_tasks SET status=?,message=?,finished_at=? WHERE id=?",
                           ("queued" if retry else "failed", message, "" if retry else now_text(), task["id"]))
    set_setting("local_model_message", f"最近一次本地智能处理完成：{processed} 项，{datetime.now().astimezone():%H:%M:%S}")
    if lock_handle:
        lock_handle.close()
    return processed


def clear_finished_tasks() -> int:
    with connect() as db:
        cursor = db.execute("DELETE FROM model_tasks WHERE status IN ('completed','failed')")
        return cursor.rowcount


def recover_interrupted_tasks() -> int:
    with connect() as db:
        cursor = db.execute(
            "UPDATE model_tasks SET status='queued',message='服务重启后自动恢复排队',started_at='' WHERE status='running'"
        )
        return cursor.rowcount
