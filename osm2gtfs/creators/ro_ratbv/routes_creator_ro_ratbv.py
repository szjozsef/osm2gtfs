# coding=utf-8

import logging

from osm2gtfs.core.elements import Line, Itinerary, Station, Stop
from osm2gtfs.creators.routes_creator import RoutesCreator


class RoutesCreatorRoRatbv(RoutesCreator):

    def add_routes_to_feed(self, feed, data):
        """
        This function adds the routes from the data to the GTFS feed.
        """
        # Get route information
        routes = data.get_routes()

        # Loop through all routes
        for route_osm_id, route in sorted(routes.items(), key=lambda k: k[1].route_id):

            if self._define_short_name(route) == self._define_route_description(route):
                lname = ''
            else:
                lname = self._define_route_description(route)

            # Add route information
            gtfs_route = feed.AddRoute(
                route_id = self._define_route_id(route),
                route_type = self._define_route_type(route),
                short_name = self._define_short_name(route),
                long_name = lname
            )
            gtfs_route.agency_id = feed.GetDefaultAgency().agency_id
            # gtfs_route.route_desc = self._define_route_description(route)
            gtfs_route.route_desc = self._define_long_name(route),
            gtfs_route.route_url = self._define_route_url(route)
            gtfs_route.route_color = self._define_route_color(route)
            gtfs_route.route_text_color = self._define_route_text_color(route)
        return
