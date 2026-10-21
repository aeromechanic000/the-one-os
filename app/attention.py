"""Waiting-input detection engine (IDEA.md §6.10, §7).

SSH is a byte stream with no state field, so detection is heuristic: prompt
pattern + output silence + cursor position + command context + interrupt
marker. Evaluation runs inside the backend reader thread (frontend timing is
unreliable). This module only ever *hints* — it never auto-replies.
"""
import re
import threading
import time
from functools import lru_cache

from config import ATTENTION_LINE_LIMIT, REGEX_CACHE_SIZE

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b.")


def strip_ansi(text):
    return ANSI_RE.sub("", text)


@lru_cache(maxsize=REGEX_CACHE_SIZE)
def compile_regex(pattern, flags):
    """Compile with a bounded LRU cache (IDEA.md §11)."""
    f = re.IGNORECASE if "i" in flags else 0
    if "m" in flags:
        f |= re.MULTILINE
    return re.compile(pattern, f)


class SessionState:
    """Tracks what the rule engine knows about one terminal session."""

    def __init__(self):
        self.line_buffer = ""          # last ~2 KB of output
        self.last_output_at = 0.0
        self.last_command = ""         # last input line that looked like a command
        self.last_command_at = 0.0
        self.last_interrupt_at = 0.0   # last Ctrl+C seen in input

    def feed_output(self, text):
        self.line_buffer = (self.line_buffer + text)[-ATTENTION_LINE_LIMIT:]
        self.last_output_at = time.time()

    def feed_input(self, text):
        now = time.time()
        if "\x03" in text:
            self.last_interrupt_at = now
        # A line submitted while no attention is pending is likely a command.
        if text and text[-1:] in ("\r", "\n"):
            cmd = text.rstrip("\r\n").strip()
            if cmd:
                self.last_command = cmd
                self.last_command_at = now


def _last_line(buf):
    tail = buf.split("\n")[-1] if buf else ""
    return tail.rstrip("\r")


def match_feature(p, state, now):
    """Evaluate one pattern object. Returns (matched, position-or-None)."""
    ftype = p.get("type")
    if ftype == "regex":
        try:
            m = compile_regex(p["value"], p.get("flags", "")).search(
                state.line_buffer
            )
        except re.error:
            return False, None
        return bool(m), (m.start() if m else None)
    if ftype == "contains_any":
        buf = state.line_buffer.lower()
        best = None
        hit = False
        for kw in p.get("values", []):
            pos = buf.find(kw.lower())
            if pos >= 0:
                hit = True
                best = pos if best is None else min(best, pos)
        return hit, best
    if ftype == "line_end_no_newline":
        buf = state.line_buffer
        return bool(buf) and not buf.endswith("\n"), None
    if ftype == "cursor_not_at_col0":
        return bool(_last_line(state.line_buffer).strip()), None
    if ftype == "command_context":
        if not state.last_command or not state.last_command_at:
            return False, None
        try:
            m = compile_regex(p["value"], p.get("flags", "")).search(
                state.last_command
            )
        except re.error:
            return False, None
        return bool(m), None
    if ftype == "after_interrupt":
        return bool(state.last_interrupt_at), None
    return False, None


def evaluate_rule(rule, state, now=None):
    """Evaluate a rule dict against a session state.

    Returns (matched: bool, hit_features: [str]).
    """
    now = now if now is not None else time.time()
    window = rule.get("window_ms", 30000) / 1000.0
    results = []
    for p in rule.get("_patterns", []):
        # Context features are only valid inside their window.
        if p.get("type") == "command_context":
            if not state.last_command_at or now - state.last_command_at > window:
                results.append((p, False, None))
                continue
        if p.get("type") == "after_interrupt":
            if not state.last_interrupt_at or now - state.last_interrupt_at > window:
                results.append((p, False, None))
                continue
        results.append((p, *match_feature(p, state, now)))

    mode = rule.get("match_mode", "any")
    hits = [p["type"] for p, matched, _ in results if matched]
    if mode == "any":
        matched = any(m for _, m, _ in results)
    elif mode == "all":
        matched = all(m for _, m, _ in results)
    elif mode == "sequence":
        # Non-positioned features act as gates; positioned features must
        # appear in buffer order, in the order listed.
        matched = all(m for p, m, pos in results if pos is None and not _has_position(p))
        last_pos = -1
        for p, m, pos in results:
            if _has_position(p):
                if not m or pos is None or pos <= last_pos:
                    matched = False
                    break
                last_pos = pos
    else:
        matched = False
    return matched, hits


def _has_position(p):
    return p.get("type") in ("regex", "contains_any")


def derive_hint(state):
    """Human-readable tail of the output, ANSI-stripped, for the badge."""
    text = strip_ansi(state.line_buffer)
    tail = _last_line(text) or text[-40:]
    tail = tail.strip()
    return tail[-60:] if tail else "..."


class AttentionEngine:
    """One instance per terminal session; driven by the reader thread."""

    def __init__(self, sid, emit_fn, on_hit=None):
        self.sid = sid
        self.emit_fn = emit_fn          # sockets.emit
        self.on_hit = on_hit            # callback(rule_id) for hit counter
        self.state = SessionState()
        self.active = None              # currently signalled rule dict
        self._timer = None
        self._pending = None            # candidate rule waiting out silence
        self._lock = threading.Lock()

    # -- driven by ssh_manager ------------------------------------------------
    def on_input(self, text):
        self.state.feed_input(text)
        # Typing means the user is already answering: clear the badge.
        if self.active and text.strip("\r\n") != "":
            self._clear()

    def on_output(self, text):
        with self._lock:
            self.state.feed_output(text)
            if self.active:
                matched, _ = evaluate_rule(self.active, self.state)
                if not matched:
                    self.active = None
                    self.emit_fn(
                        "session_attention_cleared", {"sid": self.sid}
                    )
                else:
                    self._cancel_timer()
                    return
            self._evaluate()

    def on_close(self):
        with self._lock:
            self._cancel_timer()
            if self.active:
                self.active = None
                self.emit_fn("session_attention_cleared", {"sid": self.sid})

    # -- internals --------------------------------------------------------------
    def _evaluate(self):
        from app.attention_rules import enabled_rules_cached

        now = time.time()
        best = None
        for rule in enabled_rules_cached():
            matched, _ = evaluate_rule(rule, self.state, now)
            if matched and (best is None or rule["priority"] > best["priority"]):
                best = rule
        if best is None:
            self._cancel_timer()
            self._pending = None
            return
        if self._pending and self._pending["id"] == best["id"]:
            return  # already counting down for this rule
        self._pending = best
        self._cancel_timer()
        silence = max(0, best.get("silence_ms", 400)) / 1000.0
        fired_at_output = self.state.last_output_at
        timer = threading.Timer(silence, self._fire, args=(best, fired_at_output))
        timer.daemon = True
        self._timer = timer
        timer.start()

    def _fire(self, rule, fired_at_output):
        with self._lock:
            # Silence condition: no new output since the timer was armed, and
            # the rule still matches.
            if self.state.last_output_at != fired_at_output:
                return
            matched, _ = evaluate_rule(rule, self.state)
            if not matched:
                return
            self.active = rule
            self._pending = None
            hint = derive_hint(self.state)
            self.emit_fn(
                "session_attention",
                {
                    "sid": self.sid,
                    "name": rule["name"],
                    "confidence": rule["confidence"],
                    "hint": hint,
                },
            )
            if self.on_hit:
                try:
                    self.on_hit(rule["id"])
                except Exception:
                    pass

    def _clear(self):
        with self._lock:
            if self.active:
                self.active = None
                self.emit_fn("session_attention_cleared", {"sid": self.sid})

    def _cancel_timer(self):
        if self._timer:
            self._timer.cancel()
            self._timer = None
