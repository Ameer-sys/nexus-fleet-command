"""Interactive warehouse objects and destinations for Fleet Command mode."""

from __future__ import annotations

from dataclasses import dataclass, field
import heapq
import math
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
    access_position: tuple[float, float] | None = None
    status: str = "AVAILABLE"
    current_custodian: str | None = None
    previous_custodian: str | None = None
    active_job_id: str | None = None
    storage_position: tuple[float, float] = field(init=False)

    def __post_init__(self) -> None:
        self.position = (float(self.position[0]), float(self.position[1]))
        self.storage_position = self.position
        if self.access_position is not None:
            self.access_position = (
                float(self.access_position[0]),
                float(self.access_position[1]),
            )

    def telemetry(self) -> dict[str, Any]:
        return {
            "package_id": self.package_id,
            "name": self.name,
            "position": list(self.position),
            "category": self.category,
            "priority": self.priority,
            "risk": self.risk,
            "location": self.location,
            "storage_position": list(self.storage_position),
            "access_position": list(self.access_position or self.position),
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
    approach_position: tuple[float, float] | None = None

    def telemetry(self) -> dict[str, Any]:
        return {
            "destination_id": self.destination_id,
            "name": self.name,
            "position": list(self.position),
            "kind": self.kind,
            "approach_position": list(self.approach_position or self.position),
        }


@dataclass(frozen=True)
class RestrictedArea:
    area_id: str
    x_min: float
    y_min: float
    x_max: float
    y_max: float

    def contains(self, point: tuple[float, float], margin: float = 0.0) -> bool:
        x, y = point
        return (
            self.x_min - margin <= x <= self.x_max + margin
            and self.y_min - margin <= y <= self.y_max + margin
        )


class WarehouseNavigationMap:
    """Small aisle graph shared by routing, traffic prediction, and the dashboard."""

    X_LANES = (0.18, 0.82, 1.18, 1.82)
    Y_LANES = (0.08, 0.98, 1.92)
    RESTRICTED_AREAS = (
        RestrictedArea("RACK_01", 0.38, 1.07, 0.65, 1.77),
        RestrictedArea("RACK_02", 0.38, 0.19, 0.65, 0.89),
        RestrictedArea("RACK_03", 1.35, 1.07, 1.62, 1.77),
        RestrictedArea("RACK_04", 1.35, 0.19, 1.62, 0.89),
        RestrictedArea("PACKING_MACHINE", 1.22, 0.02, 1.55, 0.16),
    )

    def __init__(self) -> None:
        self.nodes = {
            f"N-{column}-{row}": (x, y)
            for column, x in enumerate(self.X_LANES)
            for row, y in enumerate(self.Y_LANES)
        }
        self.edges: dict[str, set[str]] = {node_id: set() for node_id in self.nodes}
        for column in range(len(self.X_LANES)):
            for row in range(len(self.Y_LANES)):
                node = f"N-{column}-{row}"
                if column + 1 < len(self.X_LANES):
                    self._connect(node, f"N-{column + 1}-{row}")
                if row + 1 < len(self.Y_LANES):
                    self._connect(node, f"N-{column}-{row + 1}")
        self.intersections = {
            "X-WEST": (0.82, 0.98),
            "X-EAST": (1.18, 0.98),
        }
        self.stop_lines = (
            ((0.72, 0.92), (0.72, 1.04)),
            ((0.92, 0.92), (0.92, 1.04)),
            ((1.08, 0.92), (1.08, 1.04)),
            ((1.28, 0.92), (1.28, 1.04)),
        )

    def _connect(self, first: str, second: str) -> None:
        self.edges[first].add(second)
        self.edges[second].add(first)

    def nearest_node(self, point: tuple[float, float]) -> str:
        return min(
            self.nodes,
            key=lambda node_id: (
                math.dist(point, self.nodes[node_id]),
                node_id,
            ),
        )

    def route(
        self,
        start: tuple[float, float],
        destination: tuple[float, float],
    ) -> list[tuple[float, float]]:
        """Return an A* route through aisle nodes, including safe endpoints."""

        start_id = self.nearest_node(start)
        goal_id = self.nearest_node(destination)
        frontier: list[tuple[float, str]] = [(0.0, start_id)]
        came_from: dict[str, str | None] = {start_id: None}
        cost: dict[str, float] = {start_id: 0.0}
        while frontier:
            _, current = heapq.heappop(frontier)
            if current == goal_id:
                break
            for neighbour in sorted(self.edges[current]):
                next_cost = cost[current] + math.dist(
                    self.nodes[current], self.nodes[neighbour]
                )
                if neighbour not in cost or next_cost < cost[neighbour]:
                    cost[neighbour] = next_cost
                    priority = next_cost + math.dist(
                        self.nodes[neighbour], self.nodes[goal_id]
                    )
                    heapq.heappush(frontier, (priority, neighbour))
                    came_from[neighbour] = current
        if goal_id not in came_from:
            raise ValueError(f"no warehouse route from {start} to {destination}")
        node_ids: list[str] = []
        current: str | None = goal_id
        while current is not None:
            node_ids.append(current)
            current = came_from[current]
        points = [self.nodes[node_id] for node_id in reversed(node_ids)]
        if math.dist(start, points[0]) <= 0.05:
            points.pop(0)
        if not points or math.dist(points[-1], destination) > 0.01:
            points.append(destination)
        return points

    def point_is_walkable(self, point: tuple[float, float]) -> bool:
        return not any(area.contains(point, margin=0.02) for area in self.RESTRICTED_AREAS)

    def segment_is_walkable(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
    ) -> bool:
        distance = math.dist(start, end)
        samples = max(2, math.ceil(distance / 0.025))
        return all(
            self.point_is_walkable(
                (
                    start[0] + (end[0] - start[0]) * index / samples,
                    start[1] + (end[1] - start[1]) * index / samples,
                )
            )
            for index in range(samples + 1)
        )

    def telemetry(self) -> dict[str, Any]:
        edge_pairs = sorted(
            {
                tuple(sorted((first, second)))
                for first, neighbours in self.edges.items()
                for second in neighbours
            }
        )
        return {
            "nodes": [
                {"id": node_id, "position": list(position)}
                for node_id, position in self.nodes.items()
            ],
            "edges": [
                [list(self.nodes[first]), list(self.nodes[second])]
                for first, second in edge_pairs
            ],
            "intersections": [
                {"id": item_id, "position": list(position)}
                for item_id, position in self.intersections.items()
            ],
            "stop_lines": [[list(start), list(end)] for start, end in self.stop_lines],
            "restricted_areas": [area.__dict__ for area in self.RESTRICTED_AREAS],
        }


def create_packages() -> dict[str, WarehousePackage]:
    items = (
        WarehousePackage("BOX-101", "Standard Box", (0.30, 1.00), "STANDARD", 4, "LOW", "WEST STAGING", (0.18, 0.98)),
        WarehousePackage("BOX-001", "Inbound Box 1", (0.16, 1.46), "STANDARD", 3, "LOW", "INBOUND", (0.18, 1.92)),
        WarehousePackage("BOX-002", "Inbound Box 2", (0.38, 1.88), "STANDARD", 4, "LOW", "INBOUND", (0.82, 1.92)),
        WarehousePackage("BOX-003", "Packing Box", (1.14, 0.16), "STANDARD", 3, "LOW", "PACKING", (1.18, 0.08)),
        WarehousePackage("ELECTRONICS-01", "Electronics", (1.15, 1.55), "FRAGILE", 7, "MEDIUM", "STORAGE B", (1.18, 1.92)),
        WarehousePackage("GLASS-01", "Glass Assembly", (1.82, 1.46), "FRAGILE", 8, "HIGH", "STORAGE B", (1.82, 1.92)),
        WarehousePackage("SENSOR-RACK-01", "Sensor Rack", (1.02, 0.94), "FRAGILE", 6, "MEDIUM", "ASSEMBLY", (1.18, 0.98)),
        WarehousePackage("MED-KIT-01", "Medical Kit", (0.45, 1.65), "MEDICAL", 10, "CRITICAL", "MEDICAL", (0.82, 1.92)),
        WarehousePackage("MED-SUPPLY-02", "Medical Supplies", (0.17, 0.55), "MEDICAL", 8, "HIGH", "MEDICAL STORAGE", (0.18, 0.98)),
        WarehousePackage("MEDICAL-CRITICAL", "Critical Medical Payload", (0.60, 1.88), "MEDICAL", 10, "CRITICAL", "COLD STORAGE", (0.82, 1.92)),
        WarehousePackage("FR-301", "Fragile Cargo", (1.70, 1.00), "FRAGILE", 8, "HIGH", "EAST STAGING", (1.82, 0.98)),
        WarehousePackage("HV-001", "High-Value Crate", (1.40, 0.75), "HIGH_VALUE", 9, "HIGH", "STORAGE A", (1.18, 0.08)),
        WarehousePackage("SERVER-MODULE-01", "Server Module", (1.83, 0.51), "HIGH_VALUE", 9, "HIGH", "SECURE STORAGE", (1.82, 0.08)),
        WarehousePackage("PRECISION-PARTS-01", "Precision Parts", (0.58, 0.15), "HIGH_VALUE", 8, "HIGH", "SECURE STORAGE", (0.82, 0.08)),
        WarehousePackage("PART-401", "Parts Bin", (1.00, 1.70), "STANDARD", 5, "LOW", "NORTH STAGING", (1.18, 1.92)),
        WarehousePackage("SUPPLY-CRATE-01", "Supply Crate", (0.82, 0.62), "STANDARD", 4, "LOW", "ASSEMBLY", (0.82, 0.98)),
    )
    return {item.package_id: item for item in items}


def create_destinations() -> dict[str, WarehouseDestination]:
    destinations = (
        WarehouseDestination("INBOUND", "Inbound", (0.12, 1.00), "LOGISTICS", (0.18, 0.98)),
        WarehouseDestination("OUTBOUND", "Outbound", (1.88, 1.00), "LOGISTICS", (1.82, 0.98)),
        WarehouseDestination("STORAGE_A", "Storage A", (0.28, 0.18), "STORAGE", (0.18, 0.08)),
        WarehouseDestination("STORAGE_B", "Storage B", (1.72, 1.82), "STORAGE", (1.82, 1.92)),
        WarehouseDestination("MEDICAL", "Medical", (0.20, 1.84), "SPECIAL", (0.18, 1.92)),
        WarehouseDestination("SECURE_VAULT", "Secure Vault", (1.80, 0.18), "SECURE", (1.82, 0.08)),
        WarehouseDestination("ASSEMBLY", "Assembly", (1.18, 1.08), "PRODUCTION", (1.18, 0.98)),
        WarehouseDestination("PACKING", "Packing", (1.47, 0.36), "LOGISTICS", (1.18, 0.08)),
    )
    return {item.destination_id: item for item in destinations}
