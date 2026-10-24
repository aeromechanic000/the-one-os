"""Local/remote file browsing and operations (IDEA.md §6.8).

Local = the transfer storage directory (sandboxed: every path is resolved
against storage/ and checked for traversal). Remote = paths on a host,
accessed through the pooled SFTP client; only absolute or ~-relative paths,
no '..' after normalization.
"""
import os
import posixpath
import stat as statmod
import time

from flask import Blueprint, Response, jsonify, request, send_file
from werkzeug.utils import secure_filename

from app import db, history, limits
from app.i18n import t
from app.ssh_manager import SSHError, manager
from config import STORAGE_DIR

bp = Blueprint("files_browser", __name__)


# --- path safety ---------------------------------------------------------------

def safe_local_path(rel):
    base = os.path.realpath(STORAGE_DIR)
    p = os.path.realpath(os.path.join(base, (rel or "").lstrip("/")))
    if p != base and not p.startswith(base + os.sep):
        raise ValueError("path traversal")
    return p


def safe_remote_path(path):
    path = (path or "").strip()
    if not path or "\x00" in path:
        raise ValueError("invalid path")
    p = posixpath.normpath(path)
    if any(seg == ".." for seg in p.split("/")):
        raise ValueError("path traversal")
    return p


def local_relpath(full):
    base = os.path.realpath(STORAGE_DIR)
    rel = os.path.relpath(full, base).replace(os.sep, "/")
    return "" if rel == "." else rel


# --- listing ---------------------------------------------------------------------

def _entry(name, is_dir, size, mtime):
    return {
        "name": name,
        "type": "dir" if is_dir else "file",
        "size": size,
        "mtime": mtime,
        "ext": posixpath.splitext(name)[1].lstrip(".").lower()
        if not is_dir else "",
    }


def list_local(rel_path):
    root = safe_local_path(rel_path)
    try:
        names = os.listdir(root)
    except OSError:
        return []
    out = []
    for name in names:
        full = os.path.join(root, name)
        try:
            st = os.stat(full)
        except OSError:
            continue
        out.append(_entry(name, statmod.S_ISDIR(st.st_mode), st.st_size,
                          int(st.st_mtime)))
    out.sort(key=lambda e: (0 if e["type"] == "dir" else 1,
                            e["name"].lower()))
    return out


def list_remote(host_id, path):
    def op(sftp):
        if not path:
            path_ = sftp.normalize(".")
        else:
            path_ = safe_remote_path(path)
        out = []
        for attr in sftp.listdir_attr(path_):
            is_dir = statmod.S_ISDIR(attr.st_mode or 0)
            out.append(_entry(attr.filename, is_dir, attr.st_size or 0,
                              int(attr.st_mtime or 0)))
        out.sort(key=lambda e: (0 if e["type"] == "dir" else 1,
                                e["name"].lower()))
        return {"path": path_, "entries": out}

    return manager.with_sftp(host_id, op)


@bp.get("/api/files/local")
def get_local():
    rel = request.args.get("path", "")
    try:
        return jsonify({"path": rel, "entries": list_local(rel)})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@bp.get("/api/files/remote")
def get_remote():
    host_id = request.args.get("host_id", type=int)
    path = request.args.get("path", "")
    if not host_id:
        return jsonify({"error": "host_id is required"}), 400
    try:
        return jsonify(list_remote(host_id, path))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except SSHError as exc:
        return jsonify({"error": str(exc)}), 502


# --- file operations ---------------------------------------------------------------

@bp.post("/api/files/op")
def file_op():
    data = request.get_json(silent=True) or {}
    op = data.get("op")
    target = data.get("target")
    try:
        host_id = int(data.get("host_id")) if data.get("host_id") else None
    except (TypeError, ValueError):
        host_id = None
    path = data.get("path", "")
    new_path = data.get("new_path", "")
    recursive = bool(data.get("recursive"))
    if op not in ("mkdir", "rename", "delete") or target not in ("local", "remote"):
        return jsonify({"error": "bad request"}), 400
    if target == "remote" and not host_id:
        return jsonify({"error": "host_id is required"}), 400

    try:
        if target == "local":
            full = safe_local_path(path)
            if op == "mkdir":
                name = secure_filename(new_path or path)
                if not name:
                    return jsonify({"error": "invalid name"}), 400
                os.makedirs(os.path.join(full if os.path.isdir(full)
                                         else os.path.dirname(full), name),
                            exist_ok=True)
            elif op == "rename":
                dst = os.path.join(os.path.dirname(full),
                                   os.path.basename(safe_local_path(new_path)))
                os.rename(full, dst)
            else:  # delete
                import shutil
                if os.path.isdir(full):
                    if recursive:
                        shutil.rmtree(full)
                    else:
                        os.rmdir(full)
                elif os.path.isfile(full):
                    os.remove(full)
                    db.execute(
                        "DELETE FROM staged_files WHERE stored_path = ?",
                        (full,))
        else:
            def do(sftp):
                rpath = safe_remote_path(path)
                if op == "mkdir":
                    parent = posixpath.dirname(rpath) or "."
                    name = posixpath.basename(
                        safe_remote_path(new_path or path))
                    sftp.mkdir(posixpath.join(parent, name))
                elif op == "rename":
                    rnew = safe_remote_path(new_path)
                    sftp.rename(rpath, rnew)
                else:
                    st = sftp.stat(rpath)
                    if statmod.S_ISDIR(st.st_mode):
                        if not recursive:
                            sftp.rmdir(rpath)
                        else:
                            _rmtree(sftp, rpath)
                    else:
                        sftp.remove(rpath)
            manager.with_sftp(host_id, do)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except SSHError as exc:
        return jsonify({"error": str(exc)}), 502
    except OSError as exc:
        return jsonify({"error": str(exc)}), 400
    history.record(
        "file_op",
        history.host_label(host_id) if target == "remote" else "",
        "%s %s" % (op, posixpath.basename(path) or path))
    return jsonify({"ok": True})


def _rmtree(sftp, rpath):
    # Depth-limited recursive delete over SFTP.
    for attr in sftp.listdir_attr(rpath):
        child = posixpath.join(rpath, attr.filename)
        if statmod.S_ISDIR(attr.st_mode or 0):
            _rmtree(sftp, child)
        else:
            sftp.remove(child)
    sftp.rmdir(rpath)


# --- upload / download ---------------------------------------------------------------

@bp.post("/api/files/upload")
def upload():
    target = request.form.get("target", "local")
    host_id = request.form.get("host_id", type=int)
    rel_dir = request.form.get("path", "")
    uploaded = request.files.get("file")
    if not uploaded or not uploaded.filename:
        return jsonify({"error": "no file"}), 400
    name = secure_filename(uploaded.filename) or "upload.bin"
    try:
        if target == "local":
            dest_dir = safe_local_path(rel_dir)
            if not os.path.isdir(dest_dir):
                dest_dir = os.path.dirname(dest_dir)
            dest = os.path.join(dest_dir, name)
            limits.check_single(request.content_length or 0, name)
            limits.check_quota(request.content_length or 0)
            uploaded.save(dest)
            try:  # exact check once the real size is known
                limits.check_single(os.path.getsize(dest), name)
                limits.check_quota(0)
            except limits.LimitError:
                try:
                    os.remove(dest)
                except OSError:
                    pass
                raise
            stage_file(dest, name, source_host="")
        else:
            if not host_id:
                return jsonify({"error": "host_id is required"}), 400
            limits.check_single(request.content_length or 0, name)

            def do(sftp):
                rdir = safe_remote_path(rel_dir or ".")
                sftp.putfo(uploaded.stream, posixpath.join(rdir, name))

            manager.with_sftp(host_id, do)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except limits.LimitError as exc:
        return jsonify({"error": str(exc)}), 413
    except SSHError as exc:
        return jsonify({"error": str(exc)}), 502
    history.record(
        "upload",
        history.host_label(host_id) if target != "local" else "",
        "%s (%.1f MB)" % (name, (request.content_length or 0) / 1048576.0))
    return jsonify({"ok": True, "name": name})


@bp.get("/api/files/download")
def download():
    target = request.args.get("target", "local")
    host_id = request.args.get("host_id", type=int)
    path = request.args.get("path", "")
    try:
        if target == "local":
            full = safe_local_path(path)
            if not os.path.isfile(full):
                return jsonify({"error": t("not_found")}), 404
            return send_file(full, as_attachment=True,
                             download_name=os.path.basename(full))
        if not host_id:
            return jsonify({"error": "host_id is required"}), 400
        rpath = safe_remote_path(path)

        # NB: the response body streams lazily, so the SFTP session must
        # outlive this request handler — it is closed by the generator's
        # finally, not by with_sftp's scope.
        sftp = manager.get_client(host_id).open_sftp()
        try:
            st = sftp.stat(rpath)
            f = sftp.open(rpath, "rb")
        except Exception as exc:
            try:
                sftp.close()
            except Exception:
                pass
            raise SSHError(str(exc))

        def gen():
            try:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    yield chunk
            finally:
                try:
                    f.close()
                finally:
                    try:
                        sftp.close()
                    except Exception:
                        pass

        resp = Response(gen(), mimetype="application/octet-stream")
        resp.headers["Content-Disposition"] = \
            f'attachment; filename="{posixpath.basename(rpath)}"'
        resp.headers["Content-Length"] = str(st.st_size)
        history.record("download", history.host_label(host_id),
                       posixpath.basename(rpath) or rpath)
        return resp
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except SSHError as exc:
        return jsonify({"error": str(exc)}), 502


# --- staging records (shared with files.py) ------------------------------------------

def stage_file(stored_path, filename, source_host=""):
    try:
        st = os.stat(stored_path)
    except OSError:
        return
    db.execute(
        "INSERT INTO staged_files (filename, stored_path, size, sha256, "
        "source_host) VALUES (?, ?, ?, '', ?)",
        (filename, stored_path, st.st_size, source_host),
    )
