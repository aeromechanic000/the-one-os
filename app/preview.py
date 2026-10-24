"""In-system file preview (IDEA.md §6.8).

/api/preview?source=local|remote&host_id=&path= dispatches by extension:
images/audio/video/PDF stream (video supports HTTP Range); text/Markdown
return JSON (source text, 2 MB truncation); Office converts via LibreOffice
to PDF cached by SHA-256; archives list entry names only. Every request
passes session validation (before_request) + path normalization; remote
reads go through the authorized SFTP client only.
"""
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile

from flask import Blueprint, Response, jsonify, request

from app import db, history, limits
from app.files_browser import safe_local_path, safe_remote_path
from app.i18n import t
from app.ssh_manager import SSHError, manager
from config import (LIBREOFFICE_BIN, LIBREOFFICE_TIMEOUT, PREVIEW_CACHE_DIR,
                    PREVIEW_TEXT_LIMIT, REMOTE_CACHE_DIR, REMOTE_CACHE_LIMIT,
                    STORAGE_DIR)

bp = Blueprint("preview", __name__)

EXT = {
    "image": {"jpg", "jpeg", "png", "gif", "webp", "svg", "bmp"},
    "video": {"mp4", "webm", "ogg", "mov"},
    "audio": {"mp3", "wav", "ogg", "flac"},
    "pdf": {"pdf"},
    "markdown": {"md", "markdown"},
    "office": {"doc", "docx", "xls", "xlsx", "ppt", "pptx"},
    "archive": {"zip", "tar", "gz", "tgz", "bz2", "xz"},
}
TEXT_EXTS = {
    "txt", "log", "conf", "cfg", "ini", "env", "toml", "yaml", "yml",
    "json", "py", "js", "ts", "sh", "bash", "c", "h", "cpp", "hpp", "go",
    "rs", "java", "rb", "php", "sql", "css", "xml", "html", "htm", "csv",
    "service", "properties", "md", "markdown",
}

MIME = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
    "gif": "image/gif", "webp": "image/webp", "svg": "image/svg+xml",
    "bmp": "image/bmp",
    "mp4": "video/mp4", "webm": "video/webm", "ogg": "video/ogg",
    "mov": "video/quicktime",
    "mp3": "audio/mpeg", "wav": "audio/wav", "flac": "audio/flac",
    "pdf": "application/pdf",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "ppt": "application/vnd.ms-powerpoint",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "txt": "text/plain",
}


def classify(name):
    ext = os.path.splitext(name)[1].lstrip(".").lower()
    for kind, exts in EXT.items():
        if ext in exts:
            return kind, ext
    if ext in TEXT_EXTS:
        return "text", ext
    return "unknown", ext


def _office_method():
    return db.get_setting("office_preview_method", "libreoffice")


def _stream_response(gen, mimetype, size=None, download_name=None,
                     status=200, headers=None):
    resp = Response(gen(), mimetype=mimetype, status=status)
    if size is not None:
        resp.headers["Content-Length"] = str(size)
    resp.headers["Content-Disposition"] = (
        f'inline; filename="{download_name}"' if download_name else "inline")
    resp.headers["Cache-Control"] = "no-store"
    for k, v in (headers or {}).items():
        resp.headers[k] = v
    return resp


def _parse_range_header(header, size):
    """Parse 'bytes=start-end' (single range). Returns (start, length)."""
    m = re.fullmatch(r"bytes=(\d*)-(\d*)", header.strip())
    if not m:
        return None
    start_s, end_s = m.groups()
    if not start_s and not end_s:
        return None
    if start_s == "":                       # suffix range: last N bytes
        length = int(end_s)
        return max(0, size - length), min(length, size)
    start = int(start_s)
    end = int(end_s) if end_s else size - 1
    end = min(end, size - 1)
    if start > end or start >= size:
        return None
    return start, end - start + 1


# --- remote file access helpers -------------------------------------------------

def _remote_cache_path(host_id, rpath, st):
    key = hashlib.sha256(
        f"{host_id}|{rpath}|{st.st_size}|{int(st.st_mtime)}".encode()
    ).hexdigest()[:24]
    ext = os.path.splitext(rpath)[1].lower()[:8]
    return os.path.join(REMOTE_CACHE_DIR, f"{key}{ext}")


def _fetch_remote(host_id, rpath, st, force=False):
    """Download small remote files to cache/remote/ (IDEA.md §6.8)."""
    cache = _remote_cache_path(host_id, rpath, st)
    if os.path.isfile(cache) and not force:
        return cache
    if st.st_size > REMOTE_CACHE_LIMIT:
        raise SSHError("file too large to cache")
    limits.check_single(st.st_size, os.path.basename(rpath) or rpath)
    tmp = cache + ".part"

    def do(sftp):
        sftp.get(rpath, tmp)
    manager.with_sftp(host_id, do)
    os.replace(tmp, cache)
    return cache


def _open_remote(host_id, rpath):
    """Open a remote file for streaming; returns (fileobj, size, closer)."""
    sftp = manager.get_client(host_id).open_sftp()
    st = sftp.stat(rpath)
    f = sftp.open(rpath, "rb")

    def closer():
        try:
            f.close()
        finally:
            sftp.close()
    return f, st.st_size, closer


# --- Office → PDF (LibreOffice, cached by SHA-256) ---------------------------------

def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def convert_office_pdf(path):
    cached = os.path.join(PREVIEW_CACHE_DIR, sha256_file(path) + ".pdf")
    if os.path.isfile(cached):
        return cached
    if not shutil.which(LIBREOFFICE_BIN) and not os.path.exists(LIBREOFFICE_BIN):
        raise RuntimeError("LibreOffice is not installed on the server")
    tmpdir = tempfile.mkdtemp(prefix="office-", dir=PREVIEW_CACHE_DIR)
    try:
        subprocess.run(
            [LIBREOFFICE_BIN, "--headless", "--convert-to", "pdf",
             "--outdir", tmpdir, path],
            timeout=LIBREOFFICE_TIMEOUT, capture_output=True, check=True,
        )
        produced = os.path.join(
            tmpdir, os.path.splitext(os.path.basename(path))[0] + ".pdf")
        if not os.path.isfile(produced):
            raise RuntimeError(t("pv_conversion_failed"))
        os.replace(produced, cached)
        return cached
    except subprocess.TimeoutExpired:
        raise RuntimeError(t("pv_conversion_failed"))
    except subprocess.CalledProcessError:
        raise RuntimeError(t("pv_conversion_failed"))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# --- per-kind responses --------------------------------------------------------------

def _json_payload(payload, status=200):
    resp = jsonify(payload)
    resp.status_code = status
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _serve_local_media(path, kind, ext, name):
    if kind == "video" and "Range" in request.headers:
        return _serve_file_range(path, name, MIME.get(ext, "video/mp4"))
    from flask import send_file
    return send_file(path, mimetype=MIME.get(ext, "application/octet-stream"),
                     conditional=True, download_name=name)


def _serve_file_range(path, name, mime):
    size = os.path.getsize(path)
    rng = _parse_range_header(request.headers.get("Range", ""), size)
    if rng is None:
        return _stream_response(
            lambda: iter(lambda: b"", b""), mime, status=416,
            headers={"Content-Range": f"bytes */{size}"})
    start, length = rng

    def gen():
        with open(path, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk
    return _stream_response(
        gen, mime, size=length, download_name=name, status=206,
        headers={"Content-Range": f"bytes {start}-{start + length - 1}/{size}",
                 "Accept-Ranges": "bytes"})


def _serve_remote_range(host_id, rpath, name, mime):
    f, size, closer = _open_remote(host_id, rpath)
    rng = _parse_range_header(request.headers.get("Range", ""), size)
    start, length = rng if rng else (0, size)

    def gen():
        try:
            f.seek(start)   # paramiko SFTPFile supports seek
            remaining = length
            while remaining > 0:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk
        finally:
            closer()
    status = 206 if rng else 200
    headers = {"Accept-Ranges": "bytes"}
    if rng:
        headers["Content-Range"] = \
            f"bytes {start}-{start + length - 1}/{size}"
    return _stream_response(gen, mime, size=length, download_name=name,
                            status=status, headers=headers)


def _serve_remote_media(host_id, rpath, kind, ext, name):
    mime = MIME.get(ext, "application/octet-stream")
    if kind == "video":
        return _serve_remote_range(host_id, rpath, name, mime)
    # images / audio / pdf: small enough to cache, otherwise stream whole
    sftp = manager.get_client(host_id).open_sftp()
    try:
        st = sftp.stat(rpath)
    finally:
        sftp.close()
    if st.st_size <= REMOTE_CACHE_LIMIT:
        cache = _fetch_remote(host_id, rpath, st)
        from flask import send_file
        return send_file(cache, mimetype=mime, conditional=True,
                         download_name=name)

    f, size, closer = _open_remote(host_id, rpath)

    def gen():
        try:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                yield chunk
        finally:
            closer()
    return _stream_response(gen, mime, size=size, download_name=name)


def _serve_text(full_path, name):
    with open(full_path, "rb") as f:
        raw = f.read(PREVIEW_TEXT_LIMIT + 1)
    truncated = len(raw) > PREVIEW_TEXT_LIMIT
    if truncated:
        raw = raw[:PREVIEW_TEXT_LIMIT]
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        content = raw.decode("utf-8", "replace")
    kind, _ = classify(name)
    return _json_payload({
        "type": "markdown" if kind == "markdown" else "text",
        "name": name,
        "content": content,
        "truncated": truncated,
        "truncated_hint": t("pv_too_large") if truncated else "",
    })


def _serve_archive(full_path):
    names = []
    try:
        if zipfile.is_zipfile(full_path):
            with zipfile.ZipFile(full_path) as zf:
                names = zf.namelist()
        else:
            import tarfile
            with tarfile.open(full_path, "r:*") as tf:
                names = [m.name for m in tf.getmembers()]
    except Exception as exc:
        return _json_payload({"type": "archive", "entries": [],
                              "error": str(exc)})
    return _json_payload({"type": "archive",
                          "entries": names[:1000]})


def _serve_office(full_path, name, ext):
    method = _office_method()
    if method == "disabled":
        return _json_payload(
            {"type": "unsupported", "name": name,
             "hint": t("pv_unsupported_hint")}, status=415)
    if method == "frontend":
        return _json_payload({"type": "office-frontend", "name": name,
                              "ext": ext}, status=200)
    try:
        pdf = convert_office_pdf(full_path)
    except RuntimeError as exc:
        return _json_payload({"type": "error", "error": str(exc) or
                              t("pv_conversion_failed"),
                              "name": name}, status=503)
    from flask import send_file
    return send_file(pdf, mimetype="application/pdf", conditional=True,
                     download_name=os.path.splitext(name)[0] + ".pdf")


# --- endpoint ------------------------------------------------------------------------

@bp.get("/api/preview")
def preview():
    source = request.args.get("source", "local")
    host_id = request.args.get("host_id", type=int)
    path = request.args.get("path", "")
    try:
        if source == "local":
            full = safe_local_path(path)
            if not os.path.isfile(full):
                return _json_payload({"error": t("not_found")}), 404
            name = os.path.basename(full)
            kind, ext = classify(name)
            if kind in ("image", "audio", "video", "pdf", "archive", "office"):
                limits.check_single(os.path.getsize(full), name)
            history.record("preview", "", "previewed %s (%s)" % (name, kind))
            if kind in ("image", "audio", "video", "pdf"):
                return _serve_local_media(full, kind, ext, name)
            if kind in ("text", "markdown"):
                return _serve_text(full, name)
            if kind == "archive":
                return _serve_archive(full)
            if kind == "office":
                return _serve_office(full, name, ext)
            return _json_payload(
                {"type": "unsupported", "name": name,
                 "hint": t("pv_unsupported_hint")}, status=415)

        # remote
        if not host_id:
            return _json_payload({"error": "host_id is required"}), 400
        rpath = safe_remote_path(path)
        name = os.path.basename(rpath) or rpath
        kind, ext = classify(name)
        history.record("preview", history.host_label(host_id),
                       "previewed %s (%s)" % (name, kind))

        if kind in ("text", "markdown"):
            def read_text(sftp):
                st = sftp.stat(rpath)
                limit = min(st.st_size, PREVIEW_TEXT_LIMIT + 1)
                with sftp.open(rpath, "rb") as f:
                    raw = f.read(limit)
                return raw, st.st_size
            raw, real_size = manager.with_sftp(host_id, read_text)
            truncated = len(raw) > PREVIEW_TEXT_LIMIT or real_size > len(raw)
            if len(raw) > PREVIEW_TEXT_LIMIT:
                raw = raw[:PREVIEW_TEXT_LIMIT]
            content = raw.decode("utf-8", "replace")
            return _json_payload({
                "type": "markdown" if kind == "markdown" else "text",
                "name": name, "content": content, "truncated": truncated,
                "truncated_hint": t("pv_too_large") if truncated else "",
            })
        if kind == "archive":
            sftp = manager.get_client(host_id).open_sftp()
            try:
                st = sftp.stat(rpath)
            finally:
                sftp.close()
            if st.st_size > REMOTE_CACHE_LIMIT:
                return _json_payload(
                    {"type": "archive", "entries": [],
                     "error": "archive too large to list"}, status=200)
            cache = _fetch_remote(host_id, rpath, st)
            return _serve_archive(cache)
        if kind == "office":
            sftp = manager.get_client(host_id).open_sftp()
            try:
                st = sftp.stat(rpath)
            finally:
                sftp.close()
            if st.st_size > 200 * 1024 * 1024:
                return _json_payload(
                    {"type": "error", "error": t("pv_conversion_failed")},
                    status=413)
            cache = _fetch_remote(host_id, rpath, st)
            return _serve_office(cache, name, ext)
        if kind in ("image", "audio", "video", "pdf"):
            return _serve_remote_media(host_id, rpath, kind, ext, name)
        return _json_payload(
            {"type": "unsupported", "name": name,
             "hint": t("pv_unsupported_hint")}, status=415)
    except ValueError as exc:
        return _json_payload({"error": str(exc)}), 400
    except limits.LimitError as exc:
        return _json_payload({"error": str(exc)}), 413
    except SSHError as exc:
        return _json_payload({"error": str(exc)}), 502


# expose for settings page diagnostics
def office_available():
    return bool(shutil.which(LIBREOFFICE_BIN) or
                os.path.exists(LIBREOFFICE_BIN))
