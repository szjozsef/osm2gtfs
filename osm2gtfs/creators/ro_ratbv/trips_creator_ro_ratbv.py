# coding=utf-8

#from datetime import timedelta, datetime

import logging
from osm2gtfs.creators.trips_creator import TripsCreator
from osm2gtfs.core.helper import Helper
from osm2gtfs.core.elements import Line
import transitfeed
from transitfeed import ServicePeriod
from pprint import pprint
import re
from datetime import datetime, date, timedelta
import unicodedata

class TripsCreatorRoRatbv(TripsCreator):


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
            if itinerary.via:
                headsign = itinerary.via + " - " + itinerary.to
            else:
                headsign = itinerary.to
            trip_id_sign = itinerary.fr + " - " + headsign
            service_period = trip_builder['service_period']
            id_string = line.route_id + '_' + service_period.service_id + '_' + trip_id_sign + '_' + str(trips_count + 1)
            #id_string = id_string.replace('Ș','S').replace('ș','s').replace('Ț','T').replace('ț','t').replace('Ă','A').replace('ă','a').replace('Î','I').replace('î','i').replace('Â','A').replace('â','a')
            id_string = unicodedata.normalize('NFD', id_string)
            id_string = id_string.encode("ascii", "ignore").decode()
            #.replace(' ', '')
            id_string = re.sub('[ ]+', '', id_string)
            id_string = re.sub('[^0-9a-zA-Z_:-]', '', id_string)
            gtfs_trip = route.AddTrip(feed, headsign, service_period, id_string)
            trips_count += 1
            search_idx = 0
            
            # Go through all stops of an itinerary
            for itinerary_stop_idx, itinerary_stop_id in enumerate(itinerary.get_stops()):
                if 'stop_name' in itinerary_stop_id:
                    itinerary_stop_ref = itinerary_stop_id['stop_name']
                else:
                    itinerary_stop_ref = itinerary_stop_id

                # Load full stop object
                try:
                    itinerary_stop = trip_builder[
                        'all_stops']['regular'][itinerary_stop_ref]
                except ValueError:
                    logging.warning(
                        "Itinerary (%s) misses a stop:", itinerary.route_url)
                    logging.warning(
                        " Please review: %s", itinerary_stop_ref)
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

                drop_off_type = "0"
                pickup_type = "0"
                if 'stop_role' in itinerary_stop_id:
                    if itinerary_stop_id['stop_role'] == "platform_entry_only":
                        drop_off_type = "1"
                    if itinerary_stop_id['stop_role'] == "platform_exit_only":
                        pickup_type = "1"

                # Make sure the last stop of itinerary will keep being the last stop in GTFS
                last_stop_schedule = schedule_stop_idx == len(trip_builder['stops']) - 1
                last_stop_itinerary = itinerary_stop_idx == len(itinerary.get_stops()) - 1
                if last_stop_schedule != last_stop_itinerary:
                    schedule_stop_idx = -1

                if schedule_stop_idx != -1:
                    time = str(trip[schedule_stop_idx])
                    a_d = False
                    if "-" in time:
                        a_d = True
                        times = time.split("-")
                        a_time = times[0]
                        if len(a_time) == 5:
                            a_time = a_time+':00'
                        d_time = times[1]
                        if len(d_time) == 5:
                            d_time = d_time+':00'
                    if len(time) == 5:
                        time = time+':00'

                    search_idx = schedule_stop_idx + 1

                    # arrival_time: str in the form HH:MM:SS
                    # departure_time: str in the form HH:MM:SS
                    # stop_time: str in the form HH:MM:SS, the only time given for this stop.  If present, it is used for both arrival and departure time.

                    # Validate time information
                    #try:
                    #    time_at_stop = str(
                    #        datetime.strptime(time, "%H:%M").time())
                    #except ValueError:
                    #    logging.warning('Time "%s" for the stop was not valid:', time)
                    #    logging.warning(" %s - %s", itinerary_stop.name, itinerary_stop.osm_url)
                    #    break
                    #gtfs_trip.AddStopTime(gtfs_stop, stop_time=time_at_stop)
                    if a_d:
                        gtfs_trip.AddStopTime(gtfs_stop, arrival_time=a_time, departure_time=d_time, timepoint="1", drop_off_type=drop_off_type, pickup_type=pickup_type)
                    else:
                        gtfs_trip.AddStopTime(gtfs_stop, stop_time=time, timepoint="1", drop_off_type=drop_off_type, pickup_type=pickup_type)

                # Add stop without time information, too (we interpolate later)
                else:
                    try:
                        logging.warning("Add stop time to a non-matched stop %s - %s", itinerary_stop.name, itinerary_stop.osm_url)
                        gtfs_trip.AddStopTime(gtfs_stop, timepoint="0", drop_off_type=drop_off_type, pickup_type=pickup_type)
                    except transitfeed.problems.OtherProblem as e:
                        logging.warning(
                            "Could not add first stop to trip without time information.")
                        logging.warning(" %s - %s", itinerary_stop.name, itinerary_stop.osm_url)
                        logging.error(e)
                        break

                # Add reference to shape
                gtfs_trip.shape_id = shape_id

                # Add empty attributes to make navitia happy
                gtfs_trip.block_id = ""
                gtfs_trip.wheelchair_accessible = "1"
                if line.route_bikes_allowed is not None:
                    if line.route_bikes_allowed == "yes":
                        gtfs_trip.bikes_allowed = "1"
                    else:
                        gtfs_trip.bikes_allowed = "2"
                else:
                    gtfs_trip.bikes_allowed = ""

                gtfs_trip.direction_id = ""
                if 'direction' in trip_builder:
                    gtfs_trip.direction_id = trip_builder['direction']

            # Calculate all times of stops, which were added with no time
            Helper.interpolate_stop_times(gtfs_trip)
        return trips_count

    def _create_gtfs_service_period(self, feed, service):
        try:
            gtfs_service = feed.GetServicePeriod(service)
            if gtfs_service is not None:
                return gtfs_service
        except KeyError:
            pass
        start_date = self.config['feed_info']['start_date']
        end_date = self.config['feed_info']['end_date']
        if service == "Mo-Fr":
            gtfs_service = ServicePeriod(service)
            gtfs_service.SetWeekdayService(True)
            gtfs_service.SetWeekendService(False)
        elif service == "TE:Mo-Fr":
            logging.warning("service = %s", service)
            gtfs_service = ServicePeriod(service)
            gtfs_service.set_id = service
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
            gtfs_service.set_id = service
            gtfs_service.SetDateHasService(service)
            start_date = service
            end_date = service
            gtfs_service.SetWeekdayService(True)
            gtfs_service.SetWeekendService(True)
        elif re.search(r'^([0-9]{4})-?(1[0-2]|0[1-9])-?(3[01]|0[1-9]|[12][0-9]):([0-9]{4})-?(1[0-2]|0[1-9])-?(3[01]|0[1-9]|[12][0-9])$', service):
            service = service.replace('-', '')
            logging.warning("service = %s", service)
            year = int(service[0:4])
            month = int(service[4:6])
            day = int(service[6:8])
            start_date = service[0:8]
            startdate = date(year, month, day)
            year = int(service[9:13])
            month = int(service[13:15])
            day = int(service[15:17])
            end_date = service[9:17]
            enddate = date(year, month, day)
            gtfs_service = ServicePeriod(service)
            gtfs_service.set_id = service
            gtfs_service.SetWeekdayService(True)
            gtfs_service.SetWeekendService(True)
            # delta = timedelta(days=1)
            # while (startdate <= enddate):
            #    gtfs_service.SetDateHasService(startdate.strftime("%Y%m%d"))
            #    startdate += delta
        else:
            raise KeyError("Unknown service keyword: " + service)
        try:
            gtfs_service_2 = feed.GetServicePeriod(service)
            if gtfs_service_2 is not None:
                return gtfs_service_2
        except KeyError:
            pass
        gtfs_service.SetStartDate(start_date)
        gtfs_service.SetEndDate(end_date)
        feed.AddServicePeriodObject(gtfs_service)
        return feed.GetServicePeriod(service)
