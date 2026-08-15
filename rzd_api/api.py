from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .config import Config
from .exceptions import RzdSchemaError
from .models import (
    CarGroup,
    CarImage,
    CarImagesResult,
    Carriage,
    CarriageResult,
    CarScheme,
    JsonObject,
    MinimalPrice,
    MinimalPricingResult,
    RouteStation,
    RouteStationsResult,
    Station,
    TrainAvailability,
    TrainAvailabilityResult,
    TrainRoute,
    TransferInterstation,
    TransferLeg,
    TransferPlace,
    TransferProduct,
    TransferProvider,
    TransferRoute,
    TransferSearchRequest,
    TransferSearchResult,
    TransferTrip,
)
from .query import JsonPayload, RzdTransport


class RzdApi:
    """Internal endpoint adapter for ticket.rzd.ru API v1."""

    def __init__(self, config: Config, transport: RzdTransport | None = None) -> None:
        self.config = config
        self.base_url = config.base_url.rstrip("/")
        parsed_base_url = urlsplit(self.base_url)
        site_url = urlunsplit((parsed_base_url.scheme, parsed_base_url.netloc, "", "", ""))
        self.b2b_base_url = (config.b2b_base_url or f"{site_url}/apib2b/p").rstrip("/")
        self.mmp_base_url = self._derive_mmp_base_url(config.b2b_base_url, site_url)
        self.transport = transport or RzdTransport(config)

    def get_train_routes(
        self,
        *,
        origin: str,
        destination: str,
        departure_date: str,
        adults: int,
        children: int,
    ) -> list[TrainRoute]:
        payload = self.transport.request_json(
            "GET",
            f"{self.base_url}/railway-service/prices/train-pricing",
            params={
                "service_provider": "B2B_RZD",
                "getByLocalTime": "true",
                "carGrouping": "DontGroup",
                "origin": origin,
                "destination": destination,
                "departureDate": departure_date,
                "specialPlacesDemand": "StandardPlacesAndForDisabledPersons",
                "carIssuingType": "Passenger",
                "getTrainsFromSchedule": "true",
                "adultPassengersQuantity": adults,
                "childrenPassengersQuantity": children,
                "hasPlacesForLargeFamily": "false",
            },
        )
        nodes = self._train_nodes(payload)
        return [self._parse_train(node) for node in nodes]

    def get_train_availability(
        self,
        *,
        origin: str,
        destination: str,
        date_from: str,
        date_to: str,
    ) -> TrainAvailabilityResult:
        payload = self.transport.request_json(
            "GET",
            f"{self.base_url}/railway-service/train-availability",
            params={
                "from": date_from,
                "to": date_to,
                "originStationCode": origin,
                "destinationStationCode": destination,
            },
        )
        root = self._object_payload(payload, "train-availability")
        item_nodes = self._object_list(root, "AvailabilityItems", "train-availability")
        items: list[TrainAvailability] = []
        for node in item_nodes:
            value = self._string(node, "Date")
            if value is None:
                raise RzdSchemaError("A train-availability item has no Date field.")
            items.append(TrainAvailability(date=value, raw=node))
        return TrainAvailabilityResult(
            origin_code=self._required_string(root, "OriginCode", "train-availability"),
            destination_code=self._required_string(root, "DestinationCode", "train-availability"),
            items=items,
            raw=root,
        )

    def get_minimal_pricing(
        self,
        *,
        origin: str,
        destination: str,
        date_from: str,
    ) -> MinimalPricingResult:
        payload = self.transport.request_json(
            "GET",
            f"{self.base_url}/railway-service/train-minimal-pricing",
            params={
                "dateFrom": date_from,
                "originCode": origin,
                "destinationCode": destination,
            },
        )
        root = self._object_payload(payload, "train-minimal-pricing")
        price_nodes = self._object_list(root, "PriceByDepartureDates", "train-minimal-pricing")
        prices: list[MinimalPrice] = []
        for node in price_nodes:
            value = self._string(node, "DepatureDate", "DepartureDate")
            if value is None:
                raise RzdSchemaError("A train-minimal-pricing item has no departure date.")
            carriers = node.get("Carriers")
            if not isinstance(carriers, list) or not all(
                isinstance(carrier, dict) for carrier in carriers
            ):
                raise RzdSchemaError(
                    "A train-minimal-pricing item contains an invalid Carriers field."
                )
            prices.append(
                MinimalPrice(
                    date=value,
                    min_price=self._number(node, "MinPrice"),
                    disabled_place_min_price=self._number(node, "DisabledPlaceMinPrice"),
                    carriers=carriers,
                    raw=node,
                )
            )
        return MinimalPricingResult(
            origin_code=self._required_string(root, "OriginStationCode", "train-minimal-pricing"),
            destination_code=self._required_string(
                root, "DestinationStationCode", "train-minimal-pricing"
            ),
            prices=prices,
            raw=root,
        )

    def find_stations(
        self,
        *,
        query: str,
        transport_type: str,
        group_results: bool,
    ) -> list[Station]:
        payload = self.transport.request_json(
            "GET",
            f"{self.base_url}/suggests",
            params={
                "Query": query,
                "TransportType": transport_type,
                "GroupResults": str(group_results).lower(),
                "RailwaySortPriority": "true",
                "SynonymOn": 1,
                "Language": self.config.language,
            },
        )
        nodes = self._suggestion_nodes(payload)
        if not self._station_nodes_match_schema(nodes):
            raise RzdSchemaError(
                "The station suggestion response contains unsupported station nodes."
            )
        stations = list(self._parse_station_nodes(nodes))
        unique: dict[
            tuple[str, str, str | None, str | None, tuple[tuple[str, str], ...]], Station
        ] = {}
        for station in stations:
            unique[self._station_identity_key(station)] = station
        return list(unique.values())

    def search_transfers(self, request: TransferSearchRequest) -> TransferSearchResult:
        payload = self.transport.request_json(
            "POST",
            f"{self.mmp_base_url}/onewayRoutesStream/v2",
            json_body=self._transfer_request_body(request),
            headers={"Cookie": f"LANG_SITE={self.config.language}"},
        )
        return self._parse_transfer_result(payload)

    def get_carriages(
        self,
        *,
        origin: str,
        destination: str,
        departure_date: str,
        train_number: str,
        provider: str,
    ) -> CarriageResult:
        payload = self.transport.request_json(
            "POST",
            f"{self.b2b_base_url}/Railway/V1/Search/CarPricing",
            params={"service_provider": "B2B_RZD", "isBonusPurchase": "false"},
            json_body={
                "OriginCode": origin,
                "DestinationCode": destination,
                "Provider": provider,
                "DepartureDate": departure_date,
                "TrainNumber": train_number,
                "SpecialPlacesDemand": "StandardPlacesAndForDisabledPersons",
                "OnlyFpkBranded": False,
                "HasPlacesForLargeFamily": False,
                "CarIssuingType": "Passenger",
            },
        )
        root = self._object_payload(payload, "carriage")
        car_nodes = self._car_nodes(root)
        train_info = root.get("TrainInfo")
        if not isinstance(train_info, dict):
            raise RzdSchemaError("The carriage response has no supported TrainInfo object.")
        document_types = root.get("AllowedDocumentTypes")
        if not isinstance(document_types, list) or not all(
            isinstance(value, str) for value in document_types
        ):
            raise RzdSchemaError(
                "The carriage response contains an invalid AllowedDocumentTypes field."
            )
        return CarriageResult(
            cars=[self._parse_carriage(node) for node in car_nodes],
            train_number=self._string(train_info, "TrainNumber"),
            origin_code=self._string(root, "OriginCode"),
            destination_code=self._string(root, "DestinationCode"),
            departure_time=self._string(train_info, "DepartureDateTime", "LocalDepartureDateTime"),
            route_policy=self._string(root, "RoutePolicy"),
            booking_system=self._string(root, "BookingSystem"),
            allowed_document_types=document_types,
            origin_retrieval_date=self._string(root, "OriginRetrievalDate"),
            raw=root,
        )

    def get_car_scheme(
        self,
        *,
        car_sub_type: str,
        car_number: str,
        service_class: str,
        carrier: str,
        train_number: str,
        departure_date: str,
        car_numeration: str,
    ) -> CarScheme:
        payload = self.transport.request_json(
            "GET",
            f"{self.base_url}/railway-service/carscheme",
            params=self._car_metadata_params(
                car_sub_type=car_sub_type,
                car_number=car_number,
                service_class=service_class,
                carrier=carrier,
                train_number=train_number,
                departure_date=departure_date,
                car_numeration=car_numeration,
            ),
        )
        root = self._object_payload(payload, "car scheme")
        return self._parse_car_scheme(root)

    def get_car_images(
        self,
        *,
        car_sub_type: str,
        car_number: str,
        service_class: str,
        carrier: str,
        train_number: str,
        departure_date: str,
        car_numeration: str,
    ) -> CarImagesResult:
        payload = self.transport.request_json(
            "GET",
            f"{self.base_url}/railway-service/carimage/list",
            params=self._car_metadata_params(
                car_sub_type=car_sub_type,
                car_number=car_number,
                service_class=service_class,
                carrier=carrier,
                train_number=train_number,
                departure_date=departure_date,
                car_numeration=car_numeration,
            ),
        )
        root = self._object_payload(payload, "car images")
        image_nodes = self._object_list(root, "Images", "car images")
        return CarImagesResult(
            scheme_id=self._integer(root, "SchemeId"),
            car_sub_type=self._string(root, "CarSubType"),
            images=[self._parse_car_image(node) for node in image_nodes],
            raw=root,
        )

    def get_route_stations(
        self,
        *,
        origin: str,
        destination: str,
        departure_date: str,
        train_number: str,
        provider: str,
    ) -> RouteStationsResult:
        payload = self.transport.request_json(
            "GET",
            f"{self.b2b_base_url}/Railway/V1/Search/TrainRoute",
            params={
                "TrainNumber": train_number,
                "Origin": origin,
                "Destination": destination,
                "DepartureDate": departure_date,
                "Provider": provider,
                "GetNewRoute": "true",
                "service_provider": "B2B_RZD",
            },
        )
        root = self._object_payload(payload, "route stations")
        route_nodes = self._object_list(root, "Routes", "route stations")
        if not route_nodes:
            raise RzdSchemaError("The route-stations response contains no routes.")
        station_nodes: list[JsonObject] = []
        for route in route_nodes:
            station_nodes.extend(self._object_list(route, "RouteStops", "route stations"))
        first_route = route_nodes[0]

        return RouteStationsResult(
            train_number=self._string(first_route, "TrainNumber") or train_number,
            stations=[self._parse_route_station(node) for node in station_nodes],
            raw=root,
            route_name=self._string(first_route, "Name"),
            origin_name=self._string(first_route, "OriginName"),
            destination_name=self._string(first_route, "DestinationName"),
        )

    @staticmethod
    def _train_nodes(payload: JsonPayload) -> list[JsonObject]:
        value: Any = payload
        if isinstance(payload, dict):
            if isinstance(payload.get("data"), dict):
                value = payload["data"]
            if isinstance(value, dict):
                value = value.get("Trains") if "Trains" in value else value.get("trains")
        if not isinstance(value, list):
            raise RzdSchemaError("The train-pricing response has no supported trains list.")
        if not all(isinstance(node, dict) for node in value):
            raise RzdSchemaError("Every train-pricing item must be an object.")
        return value

    @classmethod
    def _parse_train(cls, node: JsonObject) -> TrainRoute:
        number = cls._string(node, "TrainNumber", "trainNumber")
        display_number = cls._string(node, "DisplayTrainNumber", "displayTrainNumber")
        if not number and not display_number:
            raise RzdSchemaError("A train item does not contain a train number.")

        group_nodes = node.get("CarGroups") if "CarGroups" in node else node.get("carGroups")
        if group_nodes is None and "CarGroups" not in node and "carGroups" not in node:
            groups: list[CarGroup] = []
            available_places = None
        else:
            if group_nodes is None:
                raise RzdSchemaError("A train item contains an invalid CarGroups field.")
            if not isinstance(group_nodes, list) or not all(
                isinstance(group, dict) for group in group_nodes
            ):
                raise RzdSchemaError("A train item contains an invalid CarGroups field.")
            groups = [cls._parse_car_group(group) for group in group_nodes]
            available_places = cls._aggregate_places(groups)
        min_price = cls._number(node, "MinPrice", "minPrice")
        if min_price is None:
            prices = [group.min_price for group in groups if group.min_price is not None]
            min_price = min(prices) if prices else None

        return TrainRoute(
            number=number or display_number or "",
            display_number=display_number,
            origin_name=cls._string(node, "OriginStationName", "originStationName"),
            destination_name=cls._string(node, "DestinationStationName", "destinationStationName"),
            departure_time=cls._string(
                node,
                "DepartureDateTime",
                "departureDateTime",
                "LocalDepartureDateTime",
                "localDepartureDateTime",
            ),
            arrival_time=cls._string(
                node,
                "ArrivalDateTime",
                "arrivalDateTime",
                "LocalArrivalDateTime",
                "localArrivalDateTime",
            ),
            min_price=min_price,
            available_places=available_places,
            car_groups=groups,
            raw=node,
            route_number=cls._string(node, "TrainNumberToGetRoute", "trainNumberToGetRoute"),
            origin_code=cls._string(node, "OriginStationCode", "originStationCode"),
            destination_code=cls._string(node, "DestinationStationCode", "destinationStationCode"),
            provider=cls._string(node, "Provider", "provider"),
        )

    @classmethod
    def _parse_car_group(cls, node: JsonObject) -> CarGroup:
        return CarGroup(
            car_type=cls._string(node, "CarType", "carType", "Type", "type"),
            min_price=cls._number(node, "MinPrice", "minPrice", "Price", "price"),
            available_places=cls._available_places(node),
            raw=node,
        )

    @classmethod
    def _parse_carriage(cls, node: JsonObject) -> Carriage:
        services = node.get("Services", [])
        if not isinstance(services, list) or not all(isinstance(item, str) for item in services):
            raise RzdSchemaError("A carriage item contains an invalid Services field.")
        return Carriage(
            number=cls._string(node, "CarNumber", "carNumber", "Number", "number"),
            car_type=cls._string(node, "CarType", "carType", "Type", "type"),
            min_price=cls._number(node, "MinPrice", "minPrice", "Price", "price"),
            available_places=cls._available_places(node),
            raw=node,
            max_price=cls._number(node, "MaxPrice"),
            service_cost=cls._number(node, "ServiceCost"),
            car_sub_type=cls._string(node, "CarSubType"),
            car_type_name=cls._string(node, "CarTypeName"),
            service_class=cls._string(node, "ServiceClass"),
            service_class_name=cls._string(node, "ServiceClassNameRu", "ServiceClassNameEn"),
            scheme_id=cls._integer(node, "RailwayCarSchemeId"),
            scheme_name=cls._string(node, "CarSchemeName"),
            carrier=cls._string(node, "Carrier"),
            carrier_display_name=cls._string(node, "CarrierDisplayName"),
            direction=cls._string(node, "CarDirection"),
            numeration=cls._string(node, "CarNumeration"),
            train_number=cls._string(node, "TrainNumber"),
            free_places=cls._string(node, "FreePlaces"),
            services=services,
            has_images=cls._boolean(node, "HasImages"),
        )

    @classmethod
    def _parse_car_scheme(cls, node: JsonObject) -> CarScheme:
        return CarScheme(
            scheme_id=cls._integer(node, "SchemeId"),
            car_sub_type=cls._string(node, "CarSubType"),
            start_date=cls._string(node, "StartDate"),
            end_date=cls._string(node, "EndDate"),
            train_number=cls._string(node, "TrainNumber"),
            carrier=cls._string(node, "Carrier"),
            car_number=cls._string(node, "CarNumber"),
            service_class=cls._string(node, "ServiceClass"),
            first_storey=cls._string(node, "PcSchemeFirstStorey"),
            second_storey=cls._string(node, "PcSchemeSecondStorey"),
            mobile_first_storey=cls._string(node, "MobileSchemeFirstVertStorey"),
            mobile_second_storey=cls._string(node, "MobileSchemeSecondVertStorey"),
            direction=cls._string(node, "Direction"),
            raw=node,
        )

    @classmethod
    def _parse_car_image(cls, node: JsonObject) -> CarImage:
        return CarImage(
            image_id=cls._integer(node, "RailwayCarImageId"),
            title_ru=cls._string(node, "TitleRu"),
            title_en=cls._string(node, "TitleEn"),
            preview=cls._string(node, "Preview"),
            content=cls._string(node, "Content"),
            sequence_number=cls._integer(node, "SequenceNumber"),
            raw=node,
        )

    @classmethod
    def _parse_route_station(cls, node: JsonObject) -> RouteStation:
        return RouteStation(
            name=cls._string(node, "StationName", "stationName", "Name", "name"),
            code=cls._string(node, "StationCode", "stationCode", "Code", "code"),
            arrival_time=cls._string(
                node, "ArrivalDateTime", "arrivalDateTime", "ArrivalTime", "arrivalTime"
            ),
            departure_time=cls._string(
                node,
                "DepartureDateTime",
                "departureDateTime",
                "DepartureTime",
                "departureTime",
            ),
            distance=cls._integer(node, "Distance", "distance"),
            raw=node,
            city_name=cls._string(node, "CityName"),
            local_arrival_time=cls._string(node, "LocalArrivalDateTime", "LocalArrivalTime"),
            local_departure_time=cls._string(node, "LocalDepartureDateTime", "LocalDepartureTime"),
            stop_duration=cls._number(node, "StopDuration"),
            time_description=cls._string(node, "TimeDescription"),
            days_from_origin=cls._integer(node, "DaysFromFormingStation"),
            time_zone_difference=cls._integer(node, "TimeZoneDifference"),
            actual_movement=cls._boolean(node, "ActualMovement"),
            is_cutaway_station=cls._boolean(node, "IsCutawayStation"),
        )

    @classmethod
    def _parse_transfer_result(cls, payload: JsonPayload) -> TransferSearchResult:
        root = cls._object_payload(payload, "transfer search")
        route_nodes = root.get("multi_modal_routes", [])
        if not isinstance(route_nodes, list) or not all(
            isinstance(node, dict) for node in route_nodes
        ):
            raise RzdSchemaError("The transfer-search response has no supported routes list.")
        return TransferSearchResult(
            routes=[cls._parse_transfer_route(node) for node in route_nodes],
            request_id=cls._string(root, "request_id"),
            raw=root,
        )

    @classmethod
    def _parse_transfer_route(cls, node: JsonObject) -> TransferRoute:
        legs = [
            cls._parse_transfer_leg(item)
            for item in cls._optional_object_list(node, "routes", "transfer route")
        ]
        transfers = [
            cls._parse_interstation_transfer(item)
            for item in cls._optional_object_list(node, "transfers", "transfer route")
        ]
        first_trip = next((leg.trips[0] for leg in legs if leg.trips), None)
        last_trip = next((leg.trips[-1] for leg in reversed(legs) if leg.trips), None)
        incomplete = cls._boolean_or_none(node, "incomplete")
        has_incomplete_leg = any(leg.incomplete is True for leg in legs)
        return TransferRoute(
            legs=legs,
            transfers=transfers,
            origin=first_trip.origin if first_trip else (legs[0].origin if legs else None),
            destination=(
                last_trip.destination if last_trip else (legs[-1].destination if legs else None)
            ),
            departure_time=first_trip.departure_time if first_trip else None,
            arrival_time=last_trip.arrival_time if last_trip else None,
            price=cls._money(node, "min_price"),
            currency=cls._currency(node, "min_price"),
            max_price=cls._money(node, "max_price"),
            available_places=cls._integer(node, "free_places"),
            incomplete=True if has_incomplete_leg else incomplete,
            ttl_min_expire_time=cls._string(node, "ttl_min_expire_time")
            or cls._first_string([leg.ttl_min_expire_time for leg in legs]),
            ttl_max_expire_time=cls._string(node, "ttl_max_expire_time")
            or cls._first_string([leg.ttl_max_expire_time for leg in legs]),
            raw=node,
        )

    @classmethod
    def _parse_transfer_leg(cls, node: JsonObject) -> TransferLeg:
        trips: list[TransferTrip] = []
        for segment in cls._optional_object_list(node, "segments", "transfer leg"):
            trips.extend(
                cls._parse_transfer_trip(item)
                for item in cls._optional_object_list(segment, "trips", "transfer segment")
            )
        first_trip = trips[0] if trips else None
        last_trip = trips[-1] if trips else None
        return TransferLeg(
            provider_type=cls._string(cls._nested(node, "provider"), "key"),
            booking_system=cls._string(node, "booking_system"),
            origin=(
                first_trip.origin if first_trip else cls._parse_place(node.get("start_location"))
            ),
            destination=(
                last_trip.destination
                if last_trip
                else cls._parse_place(node.get("finish_location"))
            ),
            departure_time=(
                first_trip.departure_time
                if first_trip
                else cls._string(node, "start_datetime")
            ),
            arrival_time=(
                last_trip.arrival_time if last_trip else cls._string(node, "finish_datetime")
            ),
            price=cls._money(node, "min_price"),
            currency=cls._currency(node, "min_price"),
            max_price=cls._money(node, "max_price"),
            available_places=cls._integer(node, "free_places"),
            transport_types=cls._provider_codes(node, "transport_types"),
            trips=trips,
            incomplete=cls._boolean_or_none(node, "incomplete"),
            ttl_min_expire_time=cls._string(node, "ttl_min_expire_time"),
            ttl_max_expire_time=cls._string(node, "ttl_max_expire_time"),
            raw=node,
        )

    @classmethod
    def _parse_transfer_trip(cls, node: JsonObject) -> TransferTrip:
        raw_data = node.get("raw_data")
        train_pricing: list[TrainRoute] = []
        if isinstance(raw_data, dict):
            pricing = raw_data.get("/Railway/V1/Search/TrainPricing")
            if isinstance(pricing, dict):
                train_nodes = pricing.get("Trains")
                if isinstance(train_nodes, list) and all(
                    isinstance(item, dict) for item in train_nodes
                ):
                    train_pricing = [cls._parse_train(item) for item in train_nodes]
        meters = cls._integer(cls._nested(node, "trip_distance"), "meters")
        return TransferTrip(
            provider_type=cls._string(cls._nested(node, "provider"), "key"),
            number=cls._string(node, "race_number", "train_number", "number"),
            origin=cls._parse_place(node.get("start_location")),
            destination=cls._parse_place(node.get("finish_location")),
            departure_time=cls._string(node, "start_datetime"),
            arrival_time=cls._string(node, "finish_datetime"),
            price=cls._money(node, "min_price"),
            currency=cls._currency(node, "min_price"),
            max_price=cls._money(node, "max_price"),
            available_places=cls._integer(node, "free_places"),
            distance_km=meters // 1000 if meters is not None else None,
            transport_type=cls._string(cls._nested(node, "transport_type"), "provider_code"),
            products=[
                cls._parse_transfer_product(item)
                for item in cls._optional_object_list(node, "products", "transfer trip")
            ],
            train_pricing=train_pricing,
            raw=node,
        )

    @classmethod
    def _parse_transfer_product(cls, node: JsonObject) -> TransferProduct:
        return TransferProduct(
            price=cls._money(node, "price"),
            currency=cls._currency(node, "price"),
            free_places=cls._integer(node, "free_places"),
            product_type=cls._string(cls._nested(node, "train_car_type"), "key"),
            service_classes=cls._provider_codes(node, "common_service_classes"),
            carriers=cls._provider_codes(node, "carriers"),
            ttl_rule_key=cls._string(node, "ttl_rule_key"),
            ttl_expire_time=cls._string(node, "ttl_expire_time"),
            raw=node,
        )

    @classmethod
    def _parse_interstation_transfer(cls, node: JsonObject) -> TransferInterstation:
        seconds = cls._duration_seconds(node, "min_duration")
        return TransferInterstation(
            origin=cls._parse_place(node.get("start_location")),
            destination=cls._parse_place(node.get("finish_location")),
            price=cls._money(node, "min_price"),
            currency=cls._currency(node, "min_price"),
            duration_minutes=seconds // 60 if seconds is not None else None,
            duration_seconds=seconds,
            raw=node,
        )

    @classmethod
    def _parse_place(cls, value: Any) -> TransferPlace | None:
        if not isinstance(value, dict):
            return None
        station = cls._nested(value, "station")
        city = cls._nested(value, "parent_city")
        return TransferPlace(
            key=cls._string(station, "key"),
            name=cls._string(station, "name_ru"),
            name_en=cls._string(station, "name_en"),
            city_name=cls._string(city, "name_ru"),
            city_key=cls._string(city, "key"),
            raw=value,
        )

    @classmethod
    def _suggestion_nodes(cls, payload: JsonPayload) -> list[Any]:
        if isinstance(payload, list):
            return payload
        for key in ("suggestions", "items", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
        category_keys = ("city", "train", "suburban", "bus")
        if any(key in payload for key in category_keys):
            nodes: list[Any] = []
            for key in category_keys:
                if key not in payload:
                    continue
                value = payload[key]
                if not isinstance(value, list):
                    raise RzdSchemaError(f"The station suggestion '{key}' field must be a list.")
                nodes.extend(value)
            return nodes
        if not payload:
            return []
        raise RzdSchemaError("The station suggestion response has no supported items list.")

    @classmethod
    def _parse_station_nodes(cls, nodes: Iterable[Any]) -> Iterable[Station]:
        for node in nodes:
            if not isinstance(node, dict):
                continue
            code = cls._string(node, "ExpressCode", "expressCode", "code", "Code", "c")
            name = cls._string(node, "NameRu", "nameRu", "name", "Name", "n", "title")
            if code and name:
                yield Station(
                    name=name,
                    code=code,
                    raw=node,
                    node_id=cls._string(node, "nodeId", "NodeId", "id", "Id"),
                    city_id=cls._string(node, "cityId", "CityId"),
                    timezone=cls._string(node, "timezone", "Timezone"),
                    codes=cls._codes(node),
                    node_type=cls._string(node, "nodeType", "NodeType"),
                    transport_type=cls._string(node, "transportType", "TransportType"),
                    region=cls._string(node, "region", "Region"),
                    country=cls._string(node, "country", "Country"),
                )
                continue
            for key in ("stations", "items", "children", "Children"):
                nested = node.get(key)
                if isinstance(nested, list):
                    yield from cls._parse_station_nodes(nested)

    @classmethod
    def _station_nodes_match_schema(cls, nodes: Iterable[Any]) -> bool:
        for node in nodes:
            if not isinstance(node, dict):
                return False
            code = cls._string(node, "ExpressCode", "expressCode", "code", "Code", "c")
            name = cls._string(node, "NameRu", "nameRu", "name", "Name", "n", "title")
            if code and name:
                continue

            nested_values = [
                node[key] for key in ("stations", "items", "children", "Children") if key in node
            ]
            if not nested_values:
                return False
            for nested in nested_values:
                if not isinstance(nested, list) or not cls._station_nodes_match_schema(nested):
                    return False
        return True

    @staticmethod
    def _car_nodes(root: JsonObject) -> list[JsonObject]:
        candidates: list[Any] = [root.get("cars"), root.get("Cars")]
        for data_key in ("data", "Data"):
            data = root.get(data_key)
            if isinstance(data, dict):
                candidates.extend([data.get("cars"), data.get("Cars")])
        value = next((candidate for candidate in candidates if candidate is not None), None)
        if not isinstance(value, list):
            raise RzdSchemaError("The carriage response has no supported cars list.")
        if not all(isinstance(node, dict) for node in value):
            raise RzdSchemaError("Every carriage item must be an object.")
        return value

    @staticmethod
    def _object_list(root: JsonObject, key: str, endpoint: str) -> list[JsonObject]:
        value = root.get(key)
        if not isinstance(value, list):
            raise RzdSchemaError(f"The {endpoint} response has no supported {key} list.")
        if not all(isinstance(node, dict) for node in value):
            raise RzdSchemaError(f"Every {endpoint} {key} item must be an object.")
        return value

    @staticmethod
    def _optional_object_list(root: JsonObject, key: str, endpoint: str) -> list[JsonObject]:
        value = root.get(key, [])
        if value is None:
            return []
        if not isinstance(value, list):
            raise RzdSchemaError(f"The {endpoint} response contains an invalid {key} list.")
        if not all(isinstance(node, dict) for node in value):
            raise RzdSchemaError(f"Every {endpoint} {key} item must be an object.")
        return value

    @staticmethod
    def _derive_mmp_base_url(configured_b2b_base_url: str | None, site_url: str) -> str:
        if configured_b2b_base_url is None:
            return f"{site_url}/apib2b/mmp"

        parsed = urlsplit(configured_b2b_base_url.rstrip("/"))
        segments = [segment for segment in parsed.path.split("/") if segment]
        if segments and segments[-1] == "p":
            segments[-1] = "mmp"
        else:
            segments.append("mmp")
        path = "/" + "/".join(segments) if segments else "/mmp"
        return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))

    @staticmethod
    def _object_payload(payload: JsonPayload, endpoint: str) -> JsonObject:
        if not isinstance(payload, dict):
            raise RzdSchemaError(f"The {endpoint} response must be an object.")
        return payload

    @classmethod
    def _required_string(cls, node: JsonObject, key: str, endpoint: str) -> str:
        value = cls._string(node, key)
        if value is None:
            raise RzdSchemaError(f"The {endpoint} response has no {key} field.")
        return value

    @staticmethod
    def _station_identity_key(
        station: Station,
    ) -> tuple[str, str, str | None, str | None, tuple[tuple[str, str], ...]]:
        relevant_codes = {
            key: value
            for key, value in station.codes.items()
            if key in {"Railway", "Cbdpr"} and value not in (None, "")
        }
        code_items = tuple(sorted((key, str(value)) for key, value in relevant_codes.items()))
        return (station.name, station.code, station.node_id, station.city_id, code_items)

    @classmethod
    def _transfer_request_body(cls, request: TransferSearchRequest) -> JsonObject:
        departure = cls._date_only(request.departure_date)
        providers = request.provider_values()
        for provider in providers:
            if provider not in {TransferProvider.RAILS.value, TransferProvider.SUBURBAN.value}:
                raise RzdSchemaError("Transfer provider must be one of: b2brails, cbdpr.")
        return {
            "max_results": request.max_results,
            "min_trips_in_leg": request.min_trips,
            "max_trips_in_leg": request.max_trips,
            "system_params": {
                "search_via_ar": "SVA_DONT_SEARCH",
                "search_via_graph": "SVG_SEARCH_WITH_DETAIL",
                "detailed_location": True,
                "debug": False,
            },
            "start_location": {"city": {"key": request.origin}, "type": "city"},
            "finish_location": {"city": {"key": request.destination}, "type": "city"},
            "start_datetime_range": {
                "from": f"{departure}T00:00:00",
                "to": f"{departure}T23:59:59",
            },
            "filters": [
                {
                    "param_name": "route.provider.key",
                    "exact_filter": {"param_values": providers},
                }
            ],
        }

    @staticmethod
    def _car_metadata_params(
        *,
        car_sub_type: str,
        car_number: str,
        service_class: str,
        carrier: str,
        train_number: str,
        departure_date: str,
        car_numeration: str,
    ) -> dict[str, str]:
        return {
            "CarSubType": car_sub_type,
            "CarNumber": car_number,
            "ServiceClass": service_class,
            "Carrier": carrier,
            "TrainNumber": train_number,
            "DepartureDate": departure_date,
            "CarNumeration": car_numeration,
        }

    @classmethod
    def _available_places(cls, node: JsonObject) -> int | None:
        for key in (
            "TotalPlaceQuantity",
            "totalPlaceQuantity",
            "PlaceQuantity",
            "placeQuantity",
            "FreePlaces",
            "freePlaces",
        ):
            value = cls._coerce_int(node.get(key))
            if value is not None:
                return value

        category_values = [
            cls._coerce_int(node.get(key))
            for key in (
                "LowerPlaceQuantity",
                "lowerPlaceQuantity",
                "UpperPlaceQuantity",
                "upperPlaceQuantity",
                "SideLowerPlaceQuantity",
                "sideLowerPlaceQuantity",
                "SideUpperPlaceQuantity",
                "sideUpperPlaceQuantity",
            )
            if key in node
        ]
        if category_values and all(value is not None for value in category_values):
            return sum(value for value in category_values if value is not None)
        return None

    @staticmethod
    def _aggregate_places(groups: list[CarGroup]) -> int | None:
        if not groups:
            return 0
        values = [group.available_places for group in groups]
        if any(value is None for value in values):
            return None
        return sum(value for value in values if value is not None)

    @staticmethod
    def _string(node: JsonObject, *keys: str) -> str | None:
        for key in keys:
            value = node.get(key)
            if value not in (None, ""):
                return str(value)
        return None

    @staticmethod
    def _nested(node: JsonObject, key: str) -> JsonObject:
        value = node.get(key)
        return value if isinstance(value, dict) else {}

    @classmethod
    def _codes(cls, node: JsonObject) -> JsonObject:
        value = node.get("Codes") if "Codes" in node else node.get("codes", {})
        if not isinstance(value, dict):
            return {}
        return dict(value)

    @classmethod
    def _provider_codes(cls, node: JsonObject, key: str) -> list[str]:
        values = node.get(key, [])
        if values is None:
            return []
        if not isinstance(values, list):
            raise RzdSchemaError(f"Field {key} must be a list.")
        result: list[str] = []
        for item in values:
            if isinstance(item, dict):
                code = cls._string(item, "provider_code", "key")
                if code is not None:
                    result.append(code)
            elif isinstance(item, str):
                result.append(item)
            else:
                raise RzdSchemaError(f"Field {key} contains an invalid item.")
        return result

    @classmethod
    def _money(cls, node: JsonObject, key: str) -> float | None:
        value = node.get(key)
        if isinstance(value, dict):
            kopecks = cls._number(value, "kopecks")
            if kopecks is not None:
                return kopecks / 100
            return cls._number(value, "amount", "value")
        return cls._number(node, key)

    @staticmethod
    def _currency(node: JsonObject, key: str) -> str | None:
        value = node.get(key)
        if isinstance(value, dict):
            currency = value.get("currency") or value.get("currency_code")
            return str(currency) if currency not in (None, "") else "RUB"
        return None

    @classmethod
    def _duration_seconds(cls, node: JsonObject, key: str) -> int | None:
        value = cls._string(node, key)
        if value is None:
            return None
        raw = value[:-1] if value.endswith("s") else value
        try:
            return int(float(raw))
        except ValueError:
            raise RzdSchemaError(f"Field {key} must be a duration in seconds.") from None

    @staticmethod
    def _boolean_or_none(node: JsonObject, key: str) -> bool | None:
        if key not in node or node[key] is None:
            return None
        value = node[key]
        if not isinstance(value, bool):
            raise RzdSchemaError(f"Field {key} must be a boolean.")
        return value

    @staticmethod
    def _date_only(value: date | datetime | str) -> str:
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        raw = str(value).strip()
        if "T" in raw:
            raw = raw.split("T", 1)[0]
        if "." in raw:
            return datetime.strptime(raw, "%d.%m.%Y").date().isoformat()
        return datetime.fromisoformat(raw).date().isoformat()

    @staticmethod
    def _first_number(values: list[float | None]) -> float | None:
        return next((value for value in values if value is not None), None)

    @staticmethod
    def _first_string(values: list[str | None]) -> str | None:
        return next((value for value in values if value is not None), None)

    @classmethod
    def _number(cls, node: JsonObject, *keys: str) -> float | None:
        for key in keys:
            value = node.get(key)
            if value in (None, "") or isinstance(value, bool):
                continue
            try:
                return float(str(value))
            except (TypeError, ValueError):
                raise RzdSchemaError(f"Field {key} must be numeric.") from None
        return None

    @classmethod
    def _integer(cls, node: JsonObject, *keys: str) -> int | None:
        for key in keys:
            if key in node:
                value = cls._coerce_int(node.get(key))
                if value is None and node.get(key) not in (None, ""):
                    raise RzdSchemaError(f"Field {key} must be an integer.")
                return value
        return None

    @staticmethod
    def _boolean(node: JsonObject, *keys: str) -> bool | None:
        for key in keys:
            if key not in node or node[key] is None:
                continue
            value = node[key]
            if not isinstance(value, bool):
                raise RzdSchemaError(f"Field {key} must be a boolean.")
            return value
        return None

    @staticmethod
    def _coerce_int(value: Any) -> int | None:
        if value in (None, "") or isinstance(value, bool):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def close(self) -> None:
        self.transport.close()
