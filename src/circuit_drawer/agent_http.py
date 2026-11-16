"""Shared local HTTP boundary for the Agent panel and result tabs."""

import mimetypes
from urllib.parse import parse_qs, quote, urlsplit


class AgentRoutes:
    def _agent(self, req):
        return self.agent_tasks.request(req)

    def _agent_post(self, req):
        try:
            self._json(self._agent(req))
        except ConnectionError:
            return
        except (ValueError, TypeError, KeyError, OSError) as exc:
            from .agent import safe_text
            self._json({"error": safe_text(exc)}, 400)

    def _agent_asset(self):
        query = parse_qs(urlsplit(self.path).query)
        try:
            path = self.agent_tasks.asset(query.get("task", [""])[0], query.get("path", [""])[0])
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            body = path.read_bytes()
            disposition = "attachment" if path.suffix.lower() == ".csv" else "inline"
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Disposition", f"{disposition}; filename*=UTF-8''{quote(path.name, safe='')}")
            self.end_headers()
            self.wfile.write(body)
        except ConnectionError:
            return
        except (ValueError, OSError) as exc:
            self._json({"error": str(exc)}, 404)
