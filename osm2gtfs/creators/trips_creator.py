# coding=utf-8

import re
import logging
from datetime import datetime, timedelta
import transporthours
import collections
import transitfeed
from transitfeed import ServicePeriod
from osm2gtfs.core.helper import Helper
from osm2gtfs.core.elements import Line

class TripsCreator(object):

    def __init__(self, config):
        self.config = config.data

    def __repr__(self):
        rep = ""
        if self.config is not None:
            rep += str(self.config) + " | "
        return rep

    def add_trips_to_feed(self, feed, data):
        """
        This function generates and adds trips to the GTFS feed.

        It is the place where geographic information and schedule is
        getting joined to produce a routable GTFS.

        The default format of the schedule information:
        https://github.com/grote/osm2gtfs/wiki/Schedule
        """
        all_trips_count = 0

        if data.schedule is None:
            self.build_trip_without_schedule_file(feed, data)
            return
        
        # Go though all lines
        for line_id, line in sorted(
            data.routes.items(), key=lambda k: k[1].route_id
        ):
            #logging.info("\nGenerating schedule for line: [{}] - {}".format(line.route_id, line.name))
            logging.info("\nGenerating schedule for line: [{}] - {}".format(line_id, line.name))

            # Loop through its itineraries
            itineraries = line.get_itineraries()
            for itinerary in itineraries:
                trips_count = 0

                # Verify data before proceeding
                if self._verify_data(data.schedule, line, itinerary):

                    # Prepare itinerary's trips and schedule
                    prepared_trips = self._prepare_trips(feed, data.schedule,
                                                         itinerary)

                    # Add itinerary shape to feed.
                    shape_id = self._add_shape_to_feed(
                        feed, itinerary.osm_type + "/" + str(
                            itinerary.osm_id), itinerary)

                    # Add trips of each itinerary to the GTFS feed
                    for trip_builder in prepared_trips:

                        trip_builder['all_stops'] = data.get_stops()
                        trips_count += self._add_itinerary_trips(
                            feed, itinerary, line, trip_builder, shape_id)

                # Print out status messge about added trips
                logging.info(
                    " Itinerary: [{} {}] (added {} trips, serving {} stops) - {}".format(
                        itinerary.route_id,
                        itinerary.to,
                        trips_count,
                        len(itinerary.get_stops()),
                        itinerary.osm_url,
                    )
                )
                all_trips_count += trips_count

        logging.info("\nTotal of added trips to this GTFS: %s\n\n", str(all_trips_count))
        return

    def build_trip_without_schedule_file(self, feed, data):
        transport_hours = transporthours.main.Main()
        default_hours = transport_hours.tagsToGtfs(self._DEFAULT_SCHEDULE)

        default_service_period = self._init_service_period(
            feed, default_hours[0])
        feed.SetDefaultServicePeriod(default_service_period)
        default_hours_dict = self._group_hours_by_service_period(
            feed, default_hours)

        lines = data.routes

        for route_id, line in sorted(lines.items()):

            line_gtfs = feed.GetRoute(route_id)
            route_index = 0
            itineraries = line.get_itineraries()

            line_hours_list = transport_hours.tagsToGtfs(line.tags)
            line_hours_dict = self._group_hours_by_service_period(
                feed, line_hours_list)

            for a_route in itineraries:
                itinerary_hours_list = transport_hours.tagsToGtfs(a_route.tags)

                if itinerary_hours_list:
                    itinerary_hours_dict = self._group_hours_by_service_period(
                        feed, itinerary_hours_list)
                elif line_hours_dict:
                    itinerary_hours_dict = line_hours_dict
                else:
                    itinerary_hours_dict = default_hours_dict
                    logging.warning("schedule is missing, using default")
                    logging.warning(
                        " Add opening_hours & interval tags in OSM - %s", line.osm_url)

                for service_id, itinerary_hours in itinerary_hours_dict.items():
                    service_period = feed.GetServicePeriod(service_id)
                    trip_gtfs = line_gtfs.AddTrip(
                        feed, service_period=service_period)
                    trip_gtfs.shape_id = self._add_shape_to_feed(
                        feed, a_route.osm_id, a_route)
                    trip_gtfs.direction_id = route_index % 2
                    route_index += 1

                    if a_route.fr and a_route.to:
                        trip_gtfs.trip_headsign = a_route.to
                        if line_gtfs.route_short_name:
                            # The line.name in the OSM data (route_long_name in the GTFS)
                            # is in the following format:
                            # '{transport mode} {route_short_name if any} :
                            # {A terminus} ↔ {The other terminus}'
                            # But it is good practice to not repeat the route_short_name
                            # in the route_long_name,
                            # so we abridge the route_long_name here if needed
                            line_gtfs.route_long_name =  "{} ↔ {}".format(a_route.fr, a_route.to)

                    for itinerary_hour in itinerary_hours:
                        trip_gtfs.AddFrequency(
                            itinerary_hour['start_time'], itinerary_hour['end_time'],
                            itinerary_hour['headway'])

                    if 'duration' in a_route.tags:
                        try:
                            travel_time = int(a_route.tags['duration'])
                            if not travel_time > 0:
                                logging.warning(
                                    "trip duration %s is invalid - %s",
                                    travel_time,
                                    a_route.osm_url)
                                travel_time = self._DEFAULT_TRIP_DURATION
                        except (ValueError, TypeError) as e:
                            logging.warning(
                                "trip duration %s is not a number - %s",
                                a_route.tags['duration'],
                                a_route.osm_url)
                            travel_time = self._DEFAULT_TRIP_DURATION
                    else:
                        travel_time = self._DEFAULT_TRIP_DURATION
                        logging.warning(
                            "trip duration is missing, using default (%s min)", travel_time)
                        logging.warning(
                            " Add a duration tag in OSM - %s", a_route.osm_url)

                    for index_stop, a_stop in enumerate(a_route.stops):
                        stop_id = a_stop
                        departure_time = datetime(2008, 11, 22, 6, 0, 0)

                        if index_stop == 0:
                            trip_gtfs.AddStopTime(feed.GetStop(
                                str(stop_id)), stop_time=departure_time.strftime(
                                    "%H:%M:%S"))
                        elif index_stop == len(a_route.stops) - 1:
                            departure_time += timedelta(minutes=travel_time)
                            trip_gtfs.AddStopTime(feed.GetStop(
                                str(stop_id)), stop_time=departure_time.strftime(
                                    "%H:%M:%S"))
                        else:
                            trip_gtfs.AddStopTime(feed.GetStop(str(stop_id)))

                    for secs, stop_time, is_timepoint in trip_gtfs.GetTimeInterpolatedStops():
                        if not is_timepoint:
                            stop_time.arrival_secs = secs
                            stop_time.departure_secs = secs
                            trip_gtfs.ReplaceStopTimeObject(stop_time)

                    Helper.interpolate_stop_times(trip_gtfs)

      

    _DAYS_OF_WEEK = ['monday', 'tuesday', 'wednesday',
                     'thursday', 'friday', 'saturday', 'sunday']
    _DEFAULT_SCHEDULE = {
        'opening_hours': 'Mo-Su,PH 05:00-22:00',
        'interval': '01:00'
    }
    _DAY_ABBREVIATIONS = {
        'monday': 'Mo',
        'tuesday': 'Tu',
        'wednesday': 'We',
        'thursday': 'Th',
        'friday': 'Fr',
        'saturday': 'Sa',
        'sunday': 'Su'
    }
    _DEFAULT_TRIP_DURATION = 120  # minutes

    def _service_id_from_transport_hour(self, a_transport_hour):
        service_days = [day_name for day_name in self._DAYS_OF_WEEK
                        if day_name in a_transport_hour and a_transport_hour[day_name]]

        if not service_days:
            logging.warning(
                'Transport_hour missing service days. Assuming 7 days a week.')
            service_days = self._DAYS_OF_WEEK

        def date_range(start, end):
            return self._DAY_ABBREVIATIONS[start] + '-' + self._DAY_ABBREVIATIONS[end]

        if collections.Counter(service_days) == collections.Counter(self._DAYS_OF_WEEK):
            return date_range('monday', 'sunday')
        if collections.Counter(service_days) == collections.Counter(self._DAYS_OF_WEEK[:5]):
            return date_range('monday', 'friday')
        if collections.Counter(service_days) == collections.Counter(self._DAYS_OF_WEEK[:6]):
            return date_range('monday', 'saturday')
        if collections.Counter(service_days) == collections.Counter(self._DAYS_OF_WEEK[-2:]):
            return date_range('saturday', 'sunday')

        return ','.join([self._DAY_ABBREVIATIONS[day_name] for day_name in service_days])

    def _init_service_period(self, feed, hour):
        service_id = self._service_id_from_transport_hour(hour)
        service_period = ServicePeriod(id=service_id)
        service_period.SetStartDate(self.config['feed_info']['start_date'])
        service_period.SetEndDate(self.config['feed_info']['end_date'])
        for i, day_of_week in enumerate(self._DAYS_OF_WEEK):
            if (day_of_week in hour and hour[day_of_week]):
                service_period.SetDayOfWeekHasService(i)
        feed.AddServicePeriodObject(service_period)
        return service_period

    def _group_hours_by_service_period(self, feed, transport_hours_list):
        transport_hours_dict = {}
        for hour in transport_hours_list:
            service_id = self._service_id_from_transport_hour(hour)
            try:
                feed.GetServicePeriod(service_id)
            except KeyError:
                self._init_service_period(feed, hour)

            if service_id in transport_hours_dict.keys():
                transport_hours_dict[service_id].append(hour)
            else:
                transport_hours_dict[service_id] = [hour]
        return transport_hours_dict
        
    def _prepare_trips(self, feed, schedule, itinerary):
        """
        Prepare information necessary to generate trips

        :return trips: List of different objects
        """

        # Define a list with service days of given itinerary.
        services = []
        for trip in schedule['lines'][itinerary.route_id]:
            input_fr = trip["from"]
            input_to = trip["to"]
            input_via = trip["via"] if "via" in trip else None
            if (input_fr == itinerary.fr and
                    input_to == itinerary.to and
                    input_via == itinerary.via):
                trip_services = trip["services"]
                for service in trip_services:
                    if service not in services:
                        services.append(service)

        if not services:
            logging.warning(" From and to values didn't match with schedule.")

        # Loop through all service days
        trips = []
        for service in services:

            # Define GTFS feed service period
            service_period = self._create_gtfs_service_period(feed, service)

            # Get schedule for this itinierary's trips
            trips_schedule = self._load_itinerary_schedule(schedule,
                                                           itinerary, service)

            # Get the stops, which are listed in the schedule
            scheduled_stops = self._load_scheduled_stops(
                schedule, itinerary, service)

            # Prepare a trips builder container with useful data for later
            trips.append({'service_period': service_period,
                          'stops': scheduled_stops, 'schedule': trips_schedule})
        return trips

    def _verify_data(self, schedule, line, itinerary):
        """
        Verifies line, itinerary and it's schedule data for trip creation
        """

        # Check if itinerary and line are having the same reference
        if itinerary.route_id != line.route_id:
            logging.warning(
                "The route id of the itinerary (%s) doesn't match route id of line (%s)",
                itinerary.route_id, line.route_id)
            logging.warning(" %s", itinerary.osm_url)
            logging.warning(" %s", line.osm_url)
            return False

        # Check if time information in schedule can be found for
        # the itinerary
        if itinerary.route_id not in schedule['lines']:
            logging.warning("Route not found in schedule.")
            return False

        # Check if from and to tags are valid and correspond to
        # the actual name of the first and last stop of the itinerary.
        for trip in schedule['lines'][itinerary.route_id]:
            if (trip["from"] == itinerary.fr and trip["to"] == itinerary.to):
                trip_stations = trip["stations"]
                if trip_stations[0] != itinerary.fr:
                    logging.warning(
                        "First station of the route (%s) doesn't match first station of itinerary",
                        itinerary.route_id)
                    logging.warning(" %s", itinerary.osm_url)
                    logging.warning(" Please compare with the schedule file.")
                    return False
                elif trip_stations[-1] != itinerary.to:
                    logging.warning(
                        "Last station of route (%s) doesn't match last station of itinerary.",
                        itinerary.route_id)
                    logging.warning(" %s", itinerary.osm_url)
                    logging.warning(" Please compare with the schedule file.")
                    return False

        return True

    def _add_shape_to_feed(self, feed, shape_id, itinerary):
        """
        Create GTFS shape and return shape_id to add on GTFS trip
        """
        shape_id = str(shape_id)

        # Only add a shape if there isn't one with this shape_id
        try:
            feed.GetShape(shape_id)
        except KeyError:
            shape = transitfeed.Shape(shape_id)
            for point in itinerary.shape:
                shape.AddPoint(
                    lat=float(point["lat"]), lon=float(point["lon"]))
            feed.AddShapeObject(shape)
        return shape_id

    def _add_itinerary_trips(self, feed, itinerary, line, trip_builder,
                             shape_id):
        """
        Add all trips of an itinerary to the GTFS feed.
        """
        # Obtain GTFS route to add trips to it.
        route = feed.GetRoute(line.route_id)
        trips_count = 0

        # Loop through each timeslot for a trip
        for trip in trip_builder['schedule']:
            gtfs_trip = route.AddTrip(feed, headsign=itinerary.to,
                                      service_period=trip_builder['service_period'])
            trips_count += 1
            search_idx = 0

            # Go through all stops of an itinerary
            for itinerary_stop_idx, itinerary_stop_id in enumerate(itinerary.get_stops()):

                # Load full stop object
                try:
                    itinerary_stop = trip_builder[
                        'all_stops']['regular'][itinerary_stop_id]
                except ValueError:
                    logging.warning(
                        "Itinerary (%s) misses a stop:", itinerary.route_url)
                    logging.warning(
                        " Please review: %s", itinerary_stop_id)
                    continue

                try:
                    # Load respective GTFS stop object
                    gtfs_stop = feed.GetStop(str(itinerary_stop.stop_id))
                except ValueError:
                    logging.warning("Stop in itinerary was not found in GTFS.")
                    logging.warning(" %s", itinerary_stop.osm_url)
                    continue

                # Make sure we compare same unicode encoding
                if type(itinerary_stop.name) is str:
                    itinerary_stop.name = itinerary_stop.name

                schedule_stop_idx = -1
                # Check if we have specific time information for this stop.
                try:
                    schedule_stop_idx = trip_builder['stops'].index(itinerary_stop.name, search_idx)
                except ValueError:
                    if itinerary_stop.get_parent_station() is not None:
                        # If stop name not found, check for the parent_station name, too.
                        itinerary_station = trip_builder[
                            'all_stops']['stations'][str(itinerary_stop.get_parent_station())]
                        if type(itinerary_station.name) is str:
                            itinerary_station.name = itinerary_station.name
                        try:
                            schedule_stop_idx = trip_builder[
                                'stops'].index(itinerary_station.name, search_idx)
                        except ValueError:
                            pass

                # Make sure the last stop of itinerary will keep being the last stop in GTFS
                last_stop_schedule = schedule_stop_idx == len(trip_builder['stops']) - 1
                last_stop_itinerary = itinerary_stop_idx == len(itinerary.get_stops()) - 1
                if last_stop_schedule != last_stop_itinerary:
                    schedule_stop_idx = -1

                if schedule_stop_idx != -1:
                    time = trip[schedule_stop_idx]
                    search_idx = schedule_stop_idx + 1

                    # Validate time information
                    try:
                        time_at_stop = str(
                            datetime.strptime(time, "%H:%M").time())
                    except ValueError:
                        logging.warning('Time "%s" for the stop was not valid:', time)
                        logging.warning(" %s - %s", itinerary_stop.name, itinerary_stop.osm_url)
                        break
                    gtfs_trip.AddStopTime(gtfs_stop, stop_time=time_at_stop)

                # Add stop without time information, too (we interpolate later)
                else:
                    try:
                        gtfs_trip.AddStopTime(gtfs_stop)
                    except transitfeed.problems.OtherProblem:
                        logging.warning(
                            "Could not add first stop to trip without time information.")
                        logging.warning(" %s - %s", itinerary_stop.name, itinerary_stop.osm_url)
                        break

                # Add reference to shape
                gtfs_trip.shape_id = shape_id

                # Add empty attributes to make navitia happy
                gtfs_trip.block_id = ""
                gtfs_trip.wheelchair_accessible = ""
                gtfs_trip.bikes_allowed = ""
                gtfs_trip.direction_id = ""

            # Calculate all times of stops, which were added with no time
            Helper.interpolate_stop_times(gtfs_trip)
        return trips_count

    def _create_gtfs_service_period(self, feed, service):
        """
        Generate a transitfeed ServicePeriod object
        from a time string according to the standard schedule:
        https://github.com/grote/osm2gtfs/wiki/Schedule

        :return: ServicePeriod object
        """
        try:
            gtfs_service = feed.GetServicePeriod(service)
            if gtfs_service is not None:
                return gtfs_service
        except KeyError:
            pass

        if service == "Mo-Fr":
            gtfs_service = ServicePeriod(service)
            gtfs_service.SetWeekdayService(True)
            gtfs_service.SetWeekendService(False)
        elif service == "Mo-Sa":
            gtfs_service = ServicePeriod(service)
            gtfs_service.SetWeekdayService(True)
            gtfs_service.SetWeekendService(False)
            gtfs_service.SetDayOfWeekHasService(5, True)
        elif service == "Mo-Su":
            gtfs_service = ServicePeriod(service)
            gtfs_service.SetWeekdayService(True)
            gtfs_service.SetWeekendService(True)
        elif service == "Sa":
            gtfs_service = ServicePeriod(service)
            gtfs_service.SetWeekdayService(False)
            gtfs_service.SetWeekendService(False)
            gtfs_service.SetDayOfWeekHasService(5, True)
        elif service == "Su":
            gtfs_service = ServicePeriod(service)
            gtfs_service.SetWeekdayService(False)
            gtfs_service.SetWeekendService(False)
            gtfs_service.SetDayOfWeekHasService(6, True)
        elif service == "Sa-Su":
            gtfs_service = ServicePeriod(service)
            gtfs_service.SetWeekdayService(False)
            gtfs_service.SetWeekendService(True)
        elif re.search(r'^([0-9]{4})-?(1[0-2]|0[1-9])-?(3[01]|0[1-9]|[12][0-9])$', service):
            service = service.replace('-', '')
            gtfs_service = ServicePeriod(service)
            gtfs_service.SetDateHasService(service)
        else:
            raise KeyError("Unknown service keyword: " + service)

        gtfs_service.SetStartDate(self.config['feed_info']['start_date'])
        gtfs_service.SetEndDate(self.config['feed_info']['end_date'])
        feed.AddServicePeriodObject(gtfs_service)
        return feed.GetServicePeriod(service)

    def _load_itinerary_schedule(self, schedule, itinerary, service):
        """
        Load the part of the provided schedule that fits to a particular
        itinerary.

        :return times: List of strings
        """
        times = []
        for trip in schedule['lines'][itinerary.route_id]:
            trip_services = trip["services"]
            if "via" not in trip:
                trip["via"] = None
            if (trip["from"] == itinerary.fr and
                    trip["to"] == itinerary.to and
                    trip["via"] == itinerary.via and
                    service in trip_services):
                for time in trip["times"]:
                    times.append(time)
        if times is None:
            logging.warning("Couldn't load times from schedule for route")
        return times

    def _load_scheduled_stops(self, schedule, itinerary, service):
        """
        Load the name of stops that have time information in the provided
        schedule.

        :return stops: List of strings
        """
        stops = []
        for trip in schedule['lines'][itinerary.route_id]:
            trip_services = trip["services"]
            trip_fr = trip["from"]
            trip_to = trip["to"]
            trip_via = trip["via"] if "via" in trip else None
            if (trip_fr == itinerary.fr and
                    trip_to == itinerary.to and
                    trip_via == itinerary.via and
                    service in trip_services):
                for stop in trip["stations"]:
                    stops.append(stop)
                break
        return stops
