"""Wspólna baza encji – jedno urządzenie na dziecko."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import Child
from .const import DOMAIN
from .coordinator import ChildData, ESzkolaCoordinator


class ESzkolaEntity(CoordinatorEntity[ESzkolaCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: ESzkolaCoordinator, child: Child, key: str) -> None:
        super().__init__(coordinator)
        self.child = child
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{child.child_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{coordinator.config_entry.entry_id}_{child.child_id}")},
            name=f"eSzkoła24 {child.first_name}",
            manufacturer="eSzkoła24",
            model="Panel rodzica",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def child_data(self) -> ChildData | None:
        return (self.coordinator.data or {}).get(self.child.child_id)

    @property
    def available(self) -> bool:
        return super().available and self.child_data is not None
