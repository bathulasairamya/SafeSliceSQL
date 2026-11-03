"""Run read-only view actions without blocking Tk or calling Tk from workers."""

import queue
import threading

from safeslicesql.runtime import execution_context
from safeslicesql.security import safe_error


def background(widget, task, done, failed):
    events = queue.Queue()
    cancelled = threading.Event()

    def worker():
        try:
            with execution_context(cancellation=cancelled):
                value = task()
            events.put((True, value))
        except Exception as exc:
            events.put((False, safe_error(exc)))

    def poll():
        if not widget.winfo_exists():
            cancelled.set()
            return
        try:
            success, value = events.get_nowait()
        except queue.Empty:
            widget.after(100, poll)
            return
        (done if success else failed)(value)

    widget.bind(
        "<Destroy>", lambda event: cancelled.set() if event.widget == widget else None, add="+"
    )
    threading.Thread(target=worker, daemon=True).start()
    widget.after(100, poll)
