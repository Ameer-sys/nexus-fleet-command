"""Interactive warehouse objects and destinations for Fleet Command mode."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class WarehousePackage:
    package_id: str
    name: str
    position: tuple[float, float]
    category: str
    priority: int
    risk: str
    location: str
    status: str = "AVAILABLE"
    current_custodian: str | None = None
    previous_custodian: str | None = None
    active_job_id: str | None = None

    def telemetry(self) -> dict[str, Any]:
        return {
            "package_id": self.package_id,
            "name": self.name,
            "position": list(self.position),
            "category": self.category,
            "priority": self.priority,
            "risk": self.risk,
            "location": self.location,
            "status": self.status,
            "current_custodian": self.current_custodian,
            "previous_custodian": self.previous_custodian,
            "active_job_id": self.active_job_id,
        }


@dataclass(frozen=True)
class WarehouseDestination:
    destination_id: str
    name: str
    position: tuple[float, float]
    kind: str

    def telemetry(self) -> dict[str, Any]:
        return {
            "destination_id": self.destination_id,
            "name": self.name,
            "position": list(self.position),
            "kind": self.kind,
        }


def create_packages() -> dict[str, WarehousePackage]:
    items = (
        WarehousePackage("BOX-101", "Standard Box", (0.30, 1.00), "STANDARD", 4, "LOW", "WEST STAGING"),
        WarehousePackage("BOX-001", "Inbound Box 1", (0.16, 1.46), "STANDARD", 3, "LOW", "INBOUND"),
        WarehousePackage("BOX-002", "Inbound Box 2", (0.38, 1.88), "STANDARD", 4, "LOW", "INBOUND"),
        WarehousePackage("BOX-003", "Packing Box", (1.14, 0.16), "STANDARD", 3, "LOW", "PACKING"),
        WarehousePackage("EL-201", "Electronics", (1.15, 1.55), "FRAGILE", 7, "MEDIUM", "STORAGE B"),
        WarehousePackage("GLASS-01", "Glass Assembly", (1.82, 1.46), "FRAGILE", 8, "HIGH", "STORAGE B"),
        WarehousePackage("SENSOR-RACK-01", "Sensor Rack", (1.02, 0.94), "FRAGILE", 6, "MEDIUM", "ASSEMBLY"),
        WarehousePackage("MED-101", "Medical Kit", (0.45, 1.65), "MEDICAL", 10, "CRITICAL", "MEDICAL"),
        WarehousePackage("MED-SUPPLY-02", "Medical Supplies", (0.17, 0.55), "MEDICAL", 8, "HIGH", "MEDICAL STORAGE"),
        WarehousePackage("MEDICAL-CRITICAL", "Critical Medical Payload", (0.60, 1.88), "MEDICAL", 10, "CRITICAL", "COLD STORAGE"),
        WarehousePackage("FR-301", "Fragile Cargo", (1.70, 1.00), "FRAGILE", 8, "HIGH", "EAST STAGING"),
        WarehousePackage("HV-001", "High-Value Crate", (1.40, 0.75), "HIGH_VALUE", 9, "HIGH", "STORAGE A"),
        WarehousePackage("SERVER-MODULE-01", "Server Module", (1.83, 0.51), "HIGH_VALUE", 9, "HIGH", "SECURE STORAGE"),
        WarehousePackage("PRECISION-PARTS-01", "Precision Parts", (0.58, 0.15), "HIGH_VALUE", 8, "HIGH", "SECURE STORAGE"),
        WarehousePackage("PART-401", "Parts Bin", (1.00, 1.70), "STANDARD", 5, "LOW", "NORTH STAGING"),
        WarehousePackage("SUPPLY-CRATE-01", "Supply Crate", (0.82, 0.62), "STANDARD", 4, "LOW", "ASSEMBLY"),
    )
    return {item.package_id: item for item in items}


def create_destinations() -> dict[str, WarehouseDestination]:
    destinations = (
        WarehouseDestination("INBOUND", "Inbound", (0.12, 1.00), "LOGISTICS"),
        WarehouseDestination("OUTBOUND", "Outbound", (1.88, 1.00), "LOGISTICS"),
        WarehouseDestination("STORAGE_A", "Storage A", (0.28, 0.18), "STORAGE"),
        WarehouseDestination("STORAGE_B", "Storage B", (1.72, 1.82), "STORAGE"),
        WarehouseDestination("MEDICAL", "Medical", (0.20, 1.84), "SPECIAL"),
        WarehouseDestination("SECURE_VAULT", "Secure Vault", (1.80, 0.18), "SECURE"),
        WarehouseDestination("ASSEMBLY", "Assembly", (1.18, 1.08), "PRODUCTION"),
        WarehouseDestination("PACKING", "Packing", (1.47, 0.36), "LOGISTICS"),
    )
    return {item.destination_id: item for item in destinations}
