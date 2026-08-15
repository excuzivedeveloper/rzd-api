from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any, cast

JsonObject = dict[str, Any]


class ModelMixin:
    """Shared serialization helper for public response models."""

    def to_dict(self) -> JsonObject:
        return asdict(cast(Any, self))


@dataclass(slots=True)
class Station(ModelMixin):
    name: str
    code: str
    raw: JsonObject = field(default_factory=dict)
    node_id: str | None = None
    city_id: str | None = None
    timezone: str | None = None
    codes: JsonObject = field(default_factory=dict)
    node_type: str | None = None
    transport_type: str | None = None
    region: str | None = None
    country: str | None = None


class TransferProvider(str, Enum):
    RAILS = "b2brails"
    SUBURBAN = "cbdpr"


@dataclass(slots=True)
class TransferSearchRequest(ModelMixin):
    origin: str
    destination: str
    departure_date: date | datetime | str
    min_trips: int = 1
    max_trips: int = 3
    max_results: int = 3
    providers: tuple[TransferProvider | str, ...] = (
        TransferProvider.RAILS,
        TransferProvider.SUBURBAN,
    )

    def __post_init__(self) -> None:
        self.origin = self.origin.strip()
        self.destination = self.destination.strip()
        if not self.origin or not self.destination:
            raise ValueError("origin and destination node ids must not be empty.")
        if self.min_trips < 1:
            raise ValueError("min_trips must be greater than or equal to 1.")
        if self.max_trips < self.min_trips:
            raise ValueError("max_trips must be greater than or equal to min_trips.")
        if self.max_results < 1:
            raise ValueError("max_results must be greater than or equal to 1.")
        if not self.providers:
            raise ValueError("providers must not be empty.")

    def provider_values(self) -> list[str]:
        return [
            provider.value if isinstance(provider, TransferProvider) else str(provider)
            for provider in self.providers
        ]


@dataclass(slots=True)
class TransferPlace(ModelMixin):
    key: str | None
    name: str | None
    name_en: str | None
    city_name: str | None
    city_key: str | None
    raw: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class TransferProduct(ModelMixin):
    price: float | None
    currency: str | None
    free_places: int | None
    product_type: str | None
    service_classes: list[str] = field(default_factory=list)
    carriers: list[str] = field(default_factory=list)
    ttl_rule_key: str | None = None
    ttl_expire_time: str | None = None
    raw: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class TransferTrip(ModelMixin):
    provider_type: str | None
    number: str | None
    origin: TransferPlace | None
    destination: TransferPlace | None
    departure_time: str | None
    arrival_time: str | None
    price: float | None
    currency: str | None
    max_price: float | None
    available_places: int | None
    distance_km: int | None
    transport_type: str | None
    products: list[TransferProduct] = field(default_factory=list)
    train_pricing: list[TrainRoute] = field(default_factory=list)
    raw: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class TransferLeg(ModelMixin):
    provider_type: str | None
    booking_system: str | None
    origin: TransferPlace | None
    destination: TransferPlace | None
    departure_time: str | None
    arrival_time: str | None
    price: float | None
    currency: str | None
    max_price: float | None
    available_places: int | None
    transport_types: list[str] = field(default_factory=list)
    trips: list[TransferTrip] = field(default_factory=list)
    incomplete: bool | None = None
    ttl_min_expire_time: str | None = None
    ttl_max_expire_time: str | None = None
    raw: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class TransferInterstation(ModelMixin):
    origin: TransferPlace | None
    destination: TransferPlace | None
    price: float | None
    currency: str | None
    duration_minutes: int | None
    duration_seconds: int | None
    raw: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class TransferRoute(ModelMixin):
    legs: list[TransferLeg] = field(default_factory=list)
    transfers: list[TransferInterstation] = field(default_factory=list)
    origin: TransferPlace | None = None
    destination: TransferPlace | None = None
    departure_time: str | None = None
    arrival_time: str | None = None
    price: float | None = None
    currency: str | None = None
    max_price: float | None = None
    available_places: int | None = None
    incomplete: bool = False
    ttl_min_expire_time: str | None = None
    ttl_max_expire_time: str | None = None
    raw: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class TransferSearchResult(ModelMixin):
    routes: list[TransferRoute] = field(default_factory=list)
    request_id: str | None = None
    raw: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class CarGroup(ModelMixin):
    car_type: str | None
    min_price: float | None
    available_places: int | None
    raw: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class TrainRoute(ModelMixin):
    number: str
    display_number: str | None
    origin_name: str | None
    destination_name: str | None
    departure_time: str | None
    arrival_time: str | None
    min_price: float | None
    available_places: int | None
    car_groups: list[CarGroup] = field(default_factory=list)
    raw: JsonObject = field(default_factory=dict)
    route_number: str | None = None
    origin_code: str | None = None
    destination_code: str | None = None
    provider: str | None = None


@dataclass(slots=True)
class RoundTripResult(ModelMixin):
    forward: list[TrainRoute] = field(default_factory=list)
    back: list[TrainRoute] = field(default_factory=list)
    raw: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class Carriage(ModelMixin):
    number: str | None
    car_type: str | None
    min_price: float | None
    available_places: int | None
    raw: JsonObject = field(default_factory=dict)
    max_price: float | None = None
    service_cost: float | None = None
    car_sub_type: str | None = None
    car_type_name: str | None = None
    service_class: str | None = None
    service_class_name: str | None = None
    scheme_id: int | None = None
    scheme_name: str | None = None
    carrier: str | None = None
    carrier_display_name: str | None = None
    direction: str | None = None
    numeration: str | None = None
    train_number: str | None = None
    free_places: str | None = None
    services: list[str] = field(default_factory=list)
    has_images: bool | None = None


@dataclass(slots=True)
class CarriageResult(ModelMixin):
    cars: list[Carriage] = field(default_factory=list)
    train_number: str | None = None
    origin_code: str | None = None
    destination_code: str | None = None
    departure_time: str | None = None
    route_policy: str | None = None
    booking_system: str | None = None
    allowed_document_types: list[str] = field(default_factory=list)
    origin_retrieval_date: str | None = None
    raw: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class TrainAvailability(ModelMixin):
    date: str
    raw: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class TrainAvailabilityResult(ModelMixin):
    origin_code: str
    destination_code: str
    items: list[TrainAvailability] = field(default_factory=list)
    raw: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class MinimalPrice(ModelMixin):
    date: str
    min_price: float | None
    disabled_place_min_price: float | None
    carriers: list[JsonObject] = field(default_factory=list)
    raw: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class MinimalPricingResult(ModelMixin):
    origin_code: str
    destination_code: str
    prices: list[MinimalPrice] = field(default_factory=list)
    raw: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class CarScheme(ModelMixin):
    scheme_id: int | None
    car_sub_type: str | None
    start_date: str | None
    end_date: str | None
    train_number: str | None
    carrier: str | None
    car_number: str | None
    service_class: str | None
    first_storey: str | None
    second_storey: str | None
    mobile_first_storey: str | None
    mobile_second_storey: str | None
    direction: str | None
    raw: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class CarImage(ModelMixin):
    image_id: int | None
    title_ru: str | None
    title_en: str | None
    preview: str | None
    content: str | None
    sequence_number: int | None
    raw: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class CarImagesResult(ModelMixin):
    scheme_id: int | None
    car_sub_type: str | None
    images: list[CarImage] = field(default_factory=list)
    raw: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class RouteStation(ModelMixin):
    name: str | None
    code: str | None
    arrival_time: str | None
    departure_time: str | None
    distance: int | None
    raw: JsonObject = field(default_factory=dict)
    city_name: str | None = None
    local_arrival_time: str | None = None
    local_departure_time: str | None = None
    stop_duration: float | None = None
    time_description: str | None = None
    days_from_origin: int | None = None
    time_zone_difference: int | None = None
    actual_movement: bool | None = None
    is_cutaway_station: bool | None = None


@dataclass(slots=True)
class RouteStationsResult(ModelMixin):
    train_number: str | None
    stations: list[RouteStation] = field(default_factory=list)
    raw: JsonObject = field(default_factory=dict)
    route_name: str | None = None
    origin_name: str | None = None
    destination_name: str | None = None
