"""Report the outer transport's end before its services are torn down."""
from __future__ import annotations

import asyncio
from collections.abc import Callable

from pymobiledevice3.remote.userspace_tunnel import UserspaceRsdTunnel


class SessionTunnel(UserspaceRsdTunnel):
    def __init__(self, *, on_transport_closed: Callable[[], None], **kwargs):
        super().__init__(**kwargs)
        self._on_transport_closed = on_transport_closed

    async def aclose(self) -> None:
        # The library's existing watcher calls aclose only after wait_closed.
        # Its done callback is too late: aclose has already ended the media
        # and service streams by then. Notify before that unwind, while still
        # letting the library serialize and join the entire teardown.
        if (asyncio.current_task() is self._transport_watcher
                and self._exit_stack is not None):
            self._on_transport_closed()
        await super().aclose()
