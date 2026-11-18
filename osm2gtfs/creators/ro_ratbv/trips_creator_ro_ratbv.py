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
import json
import os

class TripsCreatorRoRatbv(TripsCreator):

    BIKES_ALLOWED_VALUES = ("0", "1", "2")

    def _bikes_allowed_rules(self):
        """
        Per-route bikes_allowed seasons from the JSON file named by the config key "bikes_allowed"
        (path relative to the working directory, like "schedule_source"); {} when the key is absent.

        File format:
          {"routes": {"20": {"default": "2",
                             "seasons": [{"start": "2027-04-01", "end": "2027-10-31",
                                          "bikes_allowed": "1"}]}}}
        "default" applies outside the seasons; dates are inclusive. For a listed route the file
        replaces the route relation's bicycle= tag. Any error in the file stops the run.
        """
        if hasattr(self, "_bikes_rules"):
            return self._bikes_rules
        rules = {}
        path = self.config.get("bikes_allowed")
        if path:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            routes = data.get("routes")
            if not isinstance(routes, dict):
                raise ValueError("%s: 'routes' must be an object" % path)
            for route_id, rule in routes.items():
                where = "%s: route %s" % (path, route_id)
                default = rule.get("default")
                if default not in self.BIKES_ALLOWED_VALUES:
                    raise ValueError("%s: 'default' must be one of %s" % (where, self.BIKES_ALLOWED_VALUES))
                seasons = []
                for season in rule.get("seasons", []):
                    value = season.get("bikes_allowed")
                    if value not in self.BIKES_ALLOWED_VALUES:
                        raise ValueError("%s: season 'bikes_allowed' must be one of %s" % (where, self.BIKES_ALLOWED_VALUES))
                    try:
                        start = datetime.strptime(season["start"], "%Y-%m-%d").date()
                        end = datetime.strptime(season["end"], "%Y-%m-%d").date()
                    except (KeyError, TypeError, ValueError) as e:
                        raise ValueError("%s: season %r needs 'start' and 'end' as YYYY-MM-DD dates (%s)"
                                         % (where, season, e))
                    if end < start:
                        raise ValueError("%s: season %s..%s ends before it starts" % (where, season["start"], season["end"]))
                    seasons.append((start, end, value))
                seasons.sort()
                for (s1, e1, _v1), (s2, _e2, _v2) in zip(seasons, seasons[1:]):
                    if s2 <= e1:
                        raise ValueError("%s: seasons starting %s and %s overlap" % (where, s1, s2))
                rules[str(route_id)] = {"default": default, "seasons": seasons}
                logging.info("bikes_allowed for route %s: default %s, seasons %s", route_id, default,
                             ", ".join("%s..%s=%s" % s for s in seasons) or "none")
        self._bikes_rules = rules
        return rules

    def _bikes_allowed_periods(self, feed, base, rule):
        """
        Split the service period base by the route's bike seasons: a list of
        (service period, bikes_allowed). One period with the same value everywhere keeps the base
        service; otherwise every part is a copy of base limited to that part's dates, with the id
        "<base id>@<YYYYMMDD>-<YYYYMMDD>" (generate_calendar_dates.py gives it the base's exceptions).
        """
        lo = datetime.strptime(base.start_date, "%Y%m%d").date()
        hi = datetime.strptime(base.end_date, "%Y%m%d").date()
        added = [datetime.strptime(d, "%Y%m%d").date()
                 for d, (etype, _c) in base.date_exceptions.items() if etype == 1]
        if added:
            lo = min([lo] + added)
            hi = max([hi] + added)
        parts = []
        cursor = lo
        for start, end, value in rule["seasons"]:
            if end < lo or start > hi:
                continue
            if start > cursor:
                parts.append([cursor, start - timedelta(days=1), rule["default"]])
            parts.append([max(start, lo), min(end, hi), value])
            cursor = min(end, hi) + timedelta(days=1)
        if cursor <= hi:
            parts.append([cursor, hi, rule["default"]])
        merged = []
        for part in parts:
            if merged and merged[-1][2] == part[2] and merged[-1][1] + timedelta(days=1) == part[0]:
                merged[-1][1] = part[1]
            else:
                merged.append(part)
        if len(merged) == 1:
            return [(base, merged[0][2])]
        result = []
        for start, end, value in merged:
            s_ymd = start.strftime("%Y%m%d")
            e_ymd = end.strftime("%Y%m%d")
            exceptions = {d: x for d, x in base.date_exceptions.items() if s_ymd <= d <= e_ymd}
            runs_weekly = any(base.day_of_week) and not (e_ymd < base.start_date or s_ymd > base.end_date)
            if not runs_weekly and not any(x[0] == 1 for x in exceptions.values()):
                continue
            service_id = "%s@%s-%s" % (base.service_id, s_ymd, e_ymd)
            try:
                period = feed.GetServicePeriod(service_id)
            except KeyError:
                period = None
            if period is None:
                period = ServicePeriod(service_id)
                period.day_of_week = list(base.day_of_week)
                for d, (etype, _c) in exceptions.items():
                    period.SetDateHasService(d, etype == 1)
                period_start = max(s_ymd, base.start_date)
                period_end = min(e_ymd, base.end_date)
                if period_start > period_end:
                    # only added dates of base fall into this part
                    period_start, period_end = s_ymd, e_ymd
                period.SetStartDate(period_start)
                period.SetEndDate(period_end)
                feed.AddServicePeriodObject(period)
                period = feed.GetServicePeriod(service_id)
            result.append((period, value))
        return result

    def _add_itinerary_trips(self, feed, itinerary, line, trip_builder,
                             shape_id):
        """
        Add all trips of an itinerary to the GTFS feed; a route with bikes_allowed seasons gets its
        trips once per season part (see _bikes_allowed_periods).
        """
        rule = self._bikes_allowed_rules().get(str(line.route_id))
        if rule is None:
            return self._add_itinerary_trips_for_period(feed, itinerary, line, trip_builder, shape_id, None)
        trips_count = 0
        for period, bikes_allowed in self._bikes_allowed_periods(feed, trip_builder['service_period'], rule):
            part_builder = dict(trip_builder)
            part_builder['service_period'] = period
            trips_count += self._add_itinerary_trips_for_period(feed, itinerary, line, part_builder,
                                                                shape_id, bikes_allowed)
        return trips_count

    def _add_itinerary_trips_for_period(self, feed, itinerary, line, trip_builder,
                                        shape_id, bikes_allowed):
        """
        Add all trips of an itinerary to the GTFS feed for trip_builder's service period;
        bikes_allowed, when not None, replaces the value derived from the route's bicycle= tag.
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
            id_string = unicodedata.normalize('NFD', id_string)
            id_string = id_string.encode("ascii", "ignore").decode()
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
                if bikes_allowed is not None:
                    gtfs_trip.bikes_allowed = bikes_allowed
                elif line.route_bikes_allowed is not None:
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
        elif service == "TV:Mo-Fr":
            # Weekday trips that run only on school-holiday weekdays (the counterpart of TE:Mo-Fr).
            # Weekly pattern Mo-Fr; gtfs-gen's generate_calendar_dates.py removes it on every
            # weekday with courses (calendar_dates.txt), and on public holidays.
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
        elif re.search(r'^[0-9]{4}-?[0-9]{2}-?[0-9]{2}(,[0-9]{4}-?[0-9]{2}-?[0-9]{2})+$', service):
            # A list of single days ("2026-11-02,2026-11-04"), e.g. a line that runs only on some days of
            # its validity period. Service id: the days joined with '_' (no ',' in the id).
            days = sorted(set(d.replace('-', '') for d in service.split(',')))
            for d in days:
                datetime.strptime(d, "%Y%m%d")  # rejects impossible dates
            service = '_'.join(days)
            gtfs_service = ServicePeriod(service)
            gtfs_service.set_id = service
            gtfs_service.SetWeekdayService(False)
            gtfs_service.SetWeekendService(False)
            for d in days:
                gtfs_service.SetDateHasService(d)
            start_date = days[0]
            end_date = days[-1]
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
