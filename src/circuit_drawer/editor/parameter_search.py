"""Editor-owned batch results; the browser selects an ID, never supplies a result."""
import threading
import time
import uuid


class ParameterSearchJob:
    def __init__(self, search, out_dir):
        self.id, self.kind = uuid.uuid4().hex[:12], "parameter-search"
        self.search, self.cancel = search, threading.Event()
        self.state, self.result, self.error = "running", None, None
        self.progress, self.total = [], len(search.updates())
        self.started, self.finished = time.monotonic(), None
        threading.Thread(target=self._run, args=(out_dir,), daemon=True).start()

    def _run(self, out_dir):
        def progress(point, total):
            self.progress = [*self.progress, point]
        try:
            self.result = self.search.run(out_dir, should_stop=self.cancel.is_set, on_progress=progress)
            self.state = "cancelled" if self.cancel.is_set() or self.result["cancelled"] else "done"
        except Exception as exc:
            self.error, self.state = str(exc), "error"
        self.finished = time.monotonic()

    def status(self):
        return {"id": self.id, "state": self.state, "result": self.result, "error": self.error,
                "points": self.progress, "total": self.total,
                "elapsed_s": round((self.finished or time.monotonic()) - self.started)}
