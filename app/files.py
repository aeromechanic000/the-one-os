"""File transfer and relay (IDEA.md §6.7).

Three modes:
  A "relay"  — through the storage/ staging dir (default; also the only mode
               when either side is local).
  B "direct" — remote→remote streamed end-to-end, no disk write.
  C "scp"    — source-side scp via exec (requires key trust between hosts).

Async queue with a worker thread; progress pushed via SocketIO
(Queued / Transferring n% / Completed / Failed). Staged files expire after 24h
(cleanup.py).
"""
import os
import posixpath
import threading
import time
import uuid

from flask import Blueprint, jsonify, request

from app import db
from app.files_browser import safe_local_path, safe_remote_path, stage_file
from app.sockets import emit
from app.ssh_manager import SSHError, manager
from config import STORAGE_DIR

bp = Blueprint("files", __name__)

CHUNK = 64 * 1024
PROGRESS_EVERY = 0.5  # seconds between progress pushes


class TransferTask:
    def __init__(self, src, dst, mode):
        self.id = uuid.uuid4().hex[:10]
        self.src = src   # {kind, host_id, path}
        self.dst = dst
        self.mode = mode
        self.state = "queued"
        self.percent = 0
        self.error = ""
        self.size = 0
        self.done = 0
        self.created_at = time.time()
        self.name = os.path.basename(self.src.get("path") or "") or "transfer"

    def progress(self, state=None, percent=None, error=None):
        if state:
            self.state = state
        if percent is not None:
            self.percent = percent
        if error is not None:
            self.error = error
        emit("transfer_progress", self.meta())

    def meta(self):
        return {
            "id": self.id, "name": self.name, "state": self.state,
            "percent": self.percent, "error": self.error,
            "size": self.size, "mode": self.mode,
        }


_queue = []
_cond = threading.Condition()
_started = False


def enqueue(src, dst, mode):
    task = TransferTask(src, dst, mode)
    with _cond:
        _queue.append(task)
        _cond.notify()
    task.progress()
    return task


def _start_worker():
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_worker, daemon=True, name="transfer").start()


_finished = []  # most recent completed/failed tasks, for the UI queue view


def _worker():
    while True:
        with _cond:
            while not _queue:
                _cond.wait()
            task = _queue.pop(0)
        task.progress(state="running")
        try:
            _run(task)
            task.progress(state="completed", percent=100)
        except Exception as exc:  # noqa: BLE001 — report, never kill worker
            task.progress(state="failed", error=str(exc))
        with _cond:
            _finished.insert(0, task)
            del _finished[50:]


# --- endpoint resolution -----------------------------------------------------

def _check(task, ep):
    if ep.get("kind") not in ("local", "remote"):
        raise SSHError("bad endpoint")
    if ep["kind"] == "remote" and not ep.get("host_id"):
        raise SSHError("host_id required for remote endpoint")
    if not ep.get("path"):
        raise SSHError("path required")

def _local(ep):
    return safe_local_path(ep["path"])

def _remote(ep):
    return safe_remote_path(ep["path"]), int(ep["host_id"])


# --- mode A: relay via storage/ ------------------------------------------------

def _size_of(ep):
    if ep["kind"] == "local":
        return os.path.getsize(_local(ep))
    rpath, host_id = _remote(ep)
    return manager.with_sftp(
        host_id, lambda sftp: sftp.stat(rpath).st_size)


def _relay(task):
    src, dst = task.src, task.dst
    task.size = _size_of(src) or 0
    last = [0.0]

    def cb(transferred, total):
        now = time.time()
        task.done = transferred
        if now - last[0] > PROGRESS_EVERY:
            last[0] = now
            pct = int(transferred * 100 / total) if total else 100
            task.progress(state="transferring", percent=pct)

    staging_name = f"relay-{task.id}-{os.path.basename(src['path'])}"
    local_stage = os.path.join(STORAGE_DIR, staging_name)

    if src["kind"] == "remote":
        s_path, s_host = _remote(src)
        manager.with_sftp(
            s_host,
            lambda sftp: sftp.get(s_path, local_stage, callback=cb))
        if dst["kind"] == "remote":
            d_path, d_host = _remote(dst)
            last[0] = 0.0

            def put_cb(transferred, t):
                now = time.time()
                if now - last[0] > PROGRESS_EVERY:
                    last[0] = now
                    task.progress(
                        state="transferring",
                        percent=int(transferred * 100 / t) if t else 100)

            manager.with_sftp(
                d_host,
                lambda sftp: sftp.put(local_stage, d_path, callback=put_cb))
            # relay intermediate is no longer needed
            try:
                os.remove(local_stage)
            except OSError:
                pass
            db.execute("DELETE FROM staged_files WHERE stored_path = ?",
                       (local_stage,))
        else:
            # remote -> local: land the file at the requested destination
            import shutil
            dst_path = _local(dst)
            os.makedirs(os.path.dirname(dst_path) or STORAGE_DIR,
                        exist_ok=True)
            shutil.move(local_stage, dst_path)
            stage_file(dst_path, os.path.basename(dst_path),
                       source_host=str(s_host))
    else:  # local -> (local | remote)
        src_path = _local(src)
        if not os.path.isfile(src_path):
            raise SSHError("source file not found")
        task.size = os.path.getsize(src_path)
        if dst["kind"] == "remote":
            d_path, d_host = _remote(dst)
            manager.with_sftp(
                d_host,
                lambda sftp: sftp.put(src_path, d_path, callback=cb))
        else:
            import shutil
            dst_path = _local(dst)
            os.makedirs(os.path.dirname(dst_path) or STORAGE_DIR,
                        exist_ok=True)
            shutil.copyfile(src_path, dst_path)
            stage_file(dst_path, os.path.basename(dst_path))


# --- mode B: remote→remote streaming, no disk write ---------------------------------

def _direct(task):
    src, dst = task.src, task.dst
    if src["kind"] != "remote" or dst["kind"] != "remote":
        return _relay(task)  # direct only makes sense remote→remote
    s_path, s_host = _remote(src)
    d_path, d_host = _remote(dst)
    task.size = manager.with_sftp(
        s_host, lambda sftp: sftp.stat(s_path).st_size) or 0
    client_s = manager.get_client(s_host)
    client_d = manager.get_client(d_host)
    sftp_s = client_s.open_sftp()
    sftp_d = client_d.open_sftp()
    last = 0.0
    try:
        rf = sftp_s.open(s_path, "rb")
        wf = sftp_d.open(d_path, "wb")
        try:
            while True:
                chunk = rf.read(CHUNK)
                if not chunk:
                    break
                wf.write(chunk)
                task.done += len(chunk)
                now = time.time()
                if now - last > PROGRESS_EVERY:
                    last = now
                    pct = int(task.done * 100 / task.size) if task.size else 100
                    task.progress(state="transferring", percent=pct)
        finally:
            rf.close()
            wf.close()
    finally:
        sftp_s.close()
        sftp_d.close()


# --- mode C: source-side scp --------------------------------------------------------

def _source_scp(task):
    src, dst = task.src, task.dst
    if src["kind"] != "remote" or dst["kind"] != "remote":
        raise SSHError("source scp requires remote→remote")
    s_path, s_host = _remote(src)
    d_path, d_host = _remote(dst)
    dhost_row = db.query_one("SELECT * FROM hosts WHERE id = ?", (d_host,))
    if not dhost_row:
        raise SSHError("target host not found")
    task.progress(state="transferring", percent=0)
    for p in (s_path, d_path):
        if "'" in p:
            raise SSHError("quote character in path")
    cmd = (f"scp -P {int(dhost_row['port'] or 22)} "
           f"'{s_path}' "
           f"'{dhost_row['username']}@{dhost_row['host']}:{d_path}'")
    output, status = manager.run_exec(s_host, cmd, timeout=300)
    if status != 0:
        raise SSHError(output.strip()[-500:] or f"scp exited {status}")
    task.progress(state="transferring", percent=100)


RUNNERS = {"A": _relay, "B": _direct, "C": _source_scp}


def _run(task):
    _check(task, task.src)
    _check(task, task.dst)
    runner = RUNNERS.get(task.mode.upper())
    if not runner:
        raise SSHError("unknown transfer mode")
    # Direct remote→remote with a local side falls back to relay.
    if runner is _direct and (
            task.src["kind"] == "local" or task.dst["kind"] == "local"):
        runner = _relay
    runner(task)


# --- REST ----------------------------------------------------------------------------

def _parse_ep(data):
    if not isinstance(data, dict):
        raise ValueError("bad endpoint")
    return {
        "kind": data.get("kind"),
        "host_id": data.get("host_id"),
        "path": data.get("path", ""),
    }


@bp.post("/api/transfer/run")
def run_transfer():
    data = request.get_json(silent=True) or {}
    try:
        src = _parse_ep(data.get("src"))
        dst = _parse_ep(data.get("dst"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    mode = (data.get("mode") or "A").upper()
    if mode not in RUNNERS:
        return jsonify({"error": "bad mode"}), 400
    task = enqueue(src, dst, mode)
    return jsonify(task.meta()), 202


@bp.get("/api/transfer/queue")
def queue():
    with _cond:
        tasks = [t.meta() for t in _queue] + [t.meta() for t in _finished]
    return jsonify(tasks)


@bp.get("/api/transfer/files")
def staged():
    rows = db.query(
        "SELECT * FROM staged_files ORDER BY created_at DESC LIMIT 200")
    out = []
    for r in rows:
        if not os.path.isfile(r["stored_path"]):
            db.execute("DELETE FROM staged_files WHERE id = ?", (r["id"],))
            continue
        out.append({
            "name": r["filename"],
            "path": os.path.relpath(r["stored_path"], STORAGE_DIR),
            "size": r["size"],
            "mtime": int(os.path.getmtime(r["stored_path"])),
            "source_host": r["source_host"],
        })
    return jsonify(out)


def start_worker():
    _start_worker()
