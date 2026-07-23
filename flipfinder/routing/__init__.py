from flipfinder.routing.base import RoundTripEstimate, RoutingBackend
from flipfinder.routing.google_routes import GoogleRoutesBackend
from flipfinder.routing.haversine import HaversineRoutingBackend

ROUTING_REGISTRY: dict[str, type[RoutingBackend]] = {
    "haversine": HaversineRoutingBackend,
    "google_routes": GoogleRoutesBackend,
}


def build_routing_backend(name: str, **kwargs) -> RoutingBackend:
    try:
        cls = ROUTING_REGISTRY[name]
    except KeyError:
        raise ValueError(f"Unknown routing backend {name!r}. Registered: {list(ROUTING_REGISTRY)}")
    return cls(**kwargs)


__all__ = ["RoutingBackend", "RoundTripEstimate", "build_routing_backend", "ROUTING_REGISTRY"]
