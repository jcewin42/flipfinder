"""
Category registry. Add new categories here so they can be referenced by name
in config.yaml instead of importing classes directly throughout the codebase.
"""
from flipfinder.categories.base import CategoryProfile
from flipfinder.categories.outboard_motors import OutboardMotorProfile

CATEGORY_REGISTRY: dict[str, type[CategoryProfile]] = {
    "outboard_motors": OutboardMotorProfile,
    # "snowblowers": SnowblowerProfile,   # <- add as you expand categories
    # "lawn_mowers": LawnMowerProfile,
    # "motorcycles": MotorcycleProfile,
}


def build_category(name: str, **kwargs) -> CategoryProfile:
    try:
        cls = CATEGORY_REGISTRY[name]
    except KeyError:
        raise ValueError(
            f"Unknown category {name!r}. Registered categories: {list(CATEGORY_REGISTRY)}"
        )
    return cls(**kwargs)


__all__ = ["CategoryProfile", "build_category", "CATEGORY_REGISTRY"]
