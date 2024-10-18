# coding=utf-8

from osm2gtfs.creators.stops_creator import StopsCreator
import logging
import transitfeed


class StopsCreatorRoRatbv(StopsCreator):

    # Override _add_stop_to_feed
    def _add_stop_to_feed(self, stop, feed):
        """
        This function adds a single Stop or Station object as a stop to GTFS.
        It can be overridden by custom creators to change how stop_ids are made
        up.

        :return stop_id: A string with the stop_id in the GTFS
        """
        try:
            parent_station = stop.get_parent_station()
        except AttributeError:
            parent_station = None

        # Send stop_id creation through overridable function
        gtfs_stop_id = self._define_stop_id(stop)

        # Save defined stop_id to the object for further use in other creators
        stop.set_stop_id(gtfs_stop_id)

        # Set stop name
        stop_name = self._define_stop_name(stop)

        if "fare_zone" in stop.tags:
            zone_id = stop.tags['fare_zone']
        #elif parent_station is not None and "fare_zone" in parent_station.tags:
        #    zone_id = parent_station.tags['fare_zone']
        else:
            zone_id = "Brasov"

        if "local_ref" in stop.tags:
            stop_code = stop.tags['local_ref']
        elif "gtfs_stop_code" in stop.tags:
            stop_code = stop.tags['gtfs_stop_code']
        else:
            stop_code = None
        if "wheelchair" in stop.tags:
            wheelchair_boarding_str = stop.tags['wheelchair']
            if wheelchair_boarding_str.lower() == "limited":
                wheelchair_boarding = "1"
            elif wheelchair_boarding_str.lower() == "designated":
                wheelchair_boarding = "1"
            elif wheelchair_boarding_str.lower() == "yes":
                wheelchair_boarding = "1"
            elif wheelchair_boarding_str.lower() == "no":
                wheelchair_boarding = "2"
            else:
                wheelchair_boarding = None
        else:
            wheelchair_boarding = None


        # Collect all data together for the stop creation
        field_dict = {
            "stop_id": self._define_stop_id(stop),
            "stop_name": stop_name,
            "stop_lat": round(float(stop.lat),10),
            "stop_lon": round(float(stop.lon),10),
            "location_type": stop.location_type,
            "parent_station": parent_station,
            "zone_id": zone_id,
            "wheelchair_boarding": wheelchair_boarding,
            "platform_code": stop_code,
        }

        # Add stop to GTFS object
        feed.AddStopObject(transitfeed.Stop(field_dict=field_dict))

        if type(stop_name).__name__ == "unicode":
            stop_name = stop_name.encode('utf-8')

        logging.info("Added stop: %s - %s", stop_name, stop.osm_url)

        # Return the stop_id of the stop added
        return field_dict['stop_id']
