"""The local HTTP server behind the editors and the launcher."""
import os
import socket
from http.server import ThreadingHTTPServer


class LocalServer(ThreadingHTTPServer):
    # Edge can connect for several scripts and styles at once when a new editor opens.
    # Let Windows queue that burst while the serving thread starts its next handler.
    request_queue_size = socket.SOMAXCONN
    # On Windows SO_REUSEADDR can bind a second live server to the same port.
    # Exclusive binding makes a port clash an error instead of a silent second editor.
    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def bind(port: int, handler) -> LocalServer:
    """A server on 127.0.0.1:port, or a clear message when the port is taken."""
    try:
        return LocalServer(("127.0.0.1", port), handler)
    except OSError as exc:
        raise SystemExit(f"端口 {port} 已被占用或不能使用（{exc.strerror or exc}），请用 --port 换一个端口") from None
