# coding=utf-8

import logging
from osm2gtfs.core.helper import Helper


class RoutesCreator(object):

    def __init__(self, config):
        self.config = config.data

    def __repr__(self):
        rep = ""
        if self.config is not None:
            rep += str(self.config) + " | "
        return rep

    def add_routes_to_feed(self, feed, data):
        """
        This function adds the routes from the data to the GTFS feed.
        """
        # Get route information
        routes = data.get_routes()

        # Loop through all routes
        for route_osm_id, route in sorted(routes.items(), key=lambda k: k[1].route_id):

            # Add route information
            gtfs_route = feed.AddRoute(
                route_id=self._define_route_id(route),
                route_type=self._define_route_type(route),
                short_name=self._define_short_name(route),
                long_name=self._define_long_name(route)
            )
            gtfs_route.agency_id = self._define_route_agency(route, feed).agency_id
            gtfs_route.route_desc = self._define_route_description(route)
            gtfs_route.route_url = self._define_route_url(route)
            gtfs_route.route_color = self._define_route_color(route)
            gtfs_route.route_text_color = self._define_route_text_color(route)
        return

    def remove_unused_routes_from_feed(self, feed):
        """
        This function removes every route which does not contain any trip
        from the final GTFS.
        It is called after the whole GTFS creation inside the main program.
        """
        removed = 0
        for route_id, route in feed.routes.items():
            if len(route.GetPatternIdTripDict()) == 0:
                removed += 1
                del feed.routes[route_id]
        if removed == 0:
            pass
        elif removed == 1:
            logging.info("Removed 1 unused route")
        else:
            logging.info("Removed %d unused routes", removed)

    def _define_route_id(self, route):
        """
        Returns the route_id for the use in the GTFS feed.
        Can be easily overridden in any creator.
        """
        if self.config.get("use_osm_id_as_route_id") \
            and self.config["use_osm_id_as_route_id"] == "yes":
            return str(route.osm_id)
        return route.route_id

    def _define_short_name(self, route):
        """
        Returns the short name for the use in the GTFS feed.
        Can be easily overridden in any creator.
        """
        return route.route_id

    def _define_long_name(self, route):
        """
        Returns the long name for the use in the GTFS feed.
        Can be easily overridden in any creator.
        """
        return route.name

    def _define_route_type(self, route):
        """
        Returns the route_id for the use in the GTFS feed.
        Can be easily overridden in any creator.
        """
        return route.route_type

    def _define_route_description(self, route):
        """
        Returns the route_desc for the use in the GTFS feed.
        Can be easily overridden in any creator.
        """
        return route.route_desc

    def _define_route_url(self, route):
        """
        Returns the route_url for the use in the GTFS feed.
        Can be easily overridden in any creator.
        """
        return route.osm_url

    def _define_route_agency(self, route, feed):
        """
        Returns the agency of the route for the use in the GTFS feed.
        If `use_osm_to_create_agency` parameter is set in config file,
        will try to use OSM tags to create new agencies.
        If not, default to the one defined in agency creator.
        Can be easily overridden in any creator.
        """

        default_agency = feed.GetDefaultAgency()
        if self.config.get("use_osm_to_create_agency") is None:
            return default_agency

        if 'network' in route.tags and route.tags['network']:
            try:
                agency = feed.GetAgency(route.tags['network'])
            except KeyError:
                agency = feed.AddAgency(route.tags['network'],
                                        default_agency.agency_url,
                                        default_agency.agency_timezone,
                                        agency_id=route.tags['network'])
                logging.info("Added agency: %s", agency.agency_name)
                if not agency.Validate():
                    logging.error("Agency data not valid for %s in line",
                        route.tags['network'])
            if 'operator:website' in route.tags and route.tags['operator:website']:
                agency.agency_url = route.tags['operator:website']
                if not agency.Validate():
                    logging.error(
                        'Url is not valid for agency: %s', agency.agency_url)
        else:
            agency = default_agency
        return agency

    def _define_route_color(self, route):
        """
        Returns the route_color for the use in the GTFS feed.
        Can be easily overridden in any creator.
        """
        return route.route_color[1:]

    def _define_route_text_color(self, route):
        """
        Returns the route_text_color for the use in the GTFS feed.
        Can be easily overridden in any creator.
        """
        if route.route_text_color is None:
            # Auto calculate text color with high contrast
            route.route_text_color = Helper.calculate_color_of_contrast(
                route.route_color)

        return route.route_text_color[1:]
