from __future__ import annotations

from collections.abc import Callable

from .errors import DiscoveryError, INTERNAL_ERROR
from .service import DiscoveryService


class DiscoveryAPI:
    def __init__(self, service: DiscoveryService) -> None:
        self.service = service

    def list_available_remotes(self) -> dict[str, object]:
        return self._sync(self.service.list_available_remotes)

    def list_all_remotes(self) -> dict[str, object]:
        return self._sync(self.service.list_all_remotes)

    def list_available_capabilities(self, remote: str) -> dict[str, object]:
        return self._sync(lambda: self.service.list_available_capabilities(remote))

    def list_all_capabilities(self, remote: str) -> dict[str, object]:
        return self._sync(lambda: self.service.list_all_capabilities(remote))

    def get_remote_status(self, remote: str) -> dict[str, object]:
        return self._sync(lambda: self.service.get_remote_status(remote))

    async def refresh_health(
        self,
        remote: str | None = None,
        capability: str | None = None,
    ) -> dict[str, object]:
        try:
            return await self.service.refresh_health(remote, capability)
        except DiscoveryError as exc:
            return exc.as_dict()
        except Exception:
            return DiscoveryError(INTERNAL_ERROR, "internal error").as_dict()

    @staticmethod
    def _sync(callback: Callable[[], dict[str, object]]) -> dict[str, object]:
        try:
            return callback()
        except DiscoveryError as exc:
            return exc.as_dict()
        except Exception:
            return DiscoveryError(INTERNAL_ERROR, "internal error").as_dict()
