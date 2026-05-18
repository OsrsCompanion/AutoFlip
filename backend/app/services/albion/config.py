from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


DEFAULT_DATA_ROOT = Path("/mnt/nvme/autoflip-data")

# Albion Online Data Project hosts.
# Source: https://www.albion-online-data.com/api/
SERVERS: dict[str, str] = {
    "americas": "https://west.albion-online-data.com",
    "asia": "https://east.albion-online-data.com",
    "europe": "https://europe.albion-online-data.com",
}

# Keep scope intentionally small. These are liquid, broadly useful categories:
# city resources, refined materials, bags, capes, mounts, food, and potions.
# This can expand later without changing collector/storage contracts.
ITEM_IDS: tuple[str, ...] = (
    # raw resources
    "T4_ORE", "T5_ORE", "T6_ORE", "T7_ORE", "T8_ORE",
    "T4_WOOD", "T5_WOOD", "T6_WOOD", "T7_WOOD", "T8_WOOD",
    "T4_FIBER", "T5_FIBER", "T6_FIBER", "T7_FIBER", "T8_FIBER",
    "T4_HIDE", "T5_HIDE", "T6_HIDE", "T7_HIDE", "T8_HIDE",
    "T4_ROCK", "T5_ROCK", "T6_ROCK", "T7_ROCK", "T8_ROCK",
    # refined resources
    "T4_METALBAR", "T5_METALBAR", "T6_METALBAR", "T7_METALBAR", "T8_METALBAR",
    "T4_PLANKS", "T5_PLANKS", "T6_PLANKS", "T7_PLANKS", "T8_PLANKS",
    "T4_CLOTH", "T5_CLOTH", "T6_CLOTH", "T7_CLOTH", "T8_CLOTH",
    "T4_LEATHER", "T5_LEATHER", "T6_LEATHER", "T7_LEATHER", "T8_LEATHER",
    "T4_STONEBLOCK", "T5_STONEBLOCK", "T6_STONEBLOCK", "T7_STONEBLOCK", "T8_STONEBLOCK",
    # bags/capes
    "T4_BAG", "T5_BAG", "T6_BAG", "T7_BAG", "T8_BAG",
    "T4_CAPE", "T5_CAPE", "T6_CAPE", "T7_CAPE", "T8_CAPE",
    # common mounts
    "T3_MOUNT_HORSE", "T4_MOUNT_HORSE", "T5_MOUNT_HORSE",
    "T3_MOUNT_OX", "T4_MOUNT_OX", "T5_MOUNT_OX",
    # food/potions starter set
    "T4_MEAL_SOUP", "T5_MEAL_SOUP", "T6_MEAL_SOUP",
    "T4_POTION_HEAL", "T5_POTION_HEAL", "T6_POTION_HEAL",
)

LOCATIONS: tuple[str, ...] = (
    "Bridgewatch",
    "Martlock",
    "Lymhurst",
    "Fort Sterling",
    "Thetford",
    "Caerleon",
)

QUALITIES: tuple[int, ...] = (1, 2, 3, 4, 5)


@dataclass(frozen=True)
class AlbionCollectorConfig:
    data_root: Path
    servers: tuple[str, ...]
    item_ids: tuple[str, ...]
    locations: tuple[str, ...]
    qualities: tuple[int, ...]
    chunk_size: int = 35
    timeout_seconds: int = 30
    polite_sleep_seconds: float = 1.0

    @property
    def albion_root(self) -> Path:
        return self.data_root / "albion"


def data_root() -> Path:
    raw = os.getenv("AUTOFLIP_DATA_ROOT") or os.getenv("OSRS_FLIP_DATA_ROOT")
    if raw:
        return Path(raw).expanduser().resolve()
    return DEFAULT_DATA_ROOT.resolve()


def default_config() -> AlbionCollectorConfig:
    server_env = os.getenv("ALBION_SERVERS", "americas")
    selected_servers = tuple(s.strip().lower() for s in server_env.split(",") if s.strip())
    valid_servers = tuple(s for s in selected_servers if s in SERVERS) or ("americas",)
    return AlbionCollectorConfig(
        data_root=data_root(),
        servers=valid_servers,
        item_ids=ITEM_IDS,
        locations=LOCATIONS,
        qualities=QUALITIES,
    )
