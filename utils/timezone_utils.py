from datetime import datetime
from zoneinfo import ZoneInfo
from timezonefinder import TimezoneFinder

tf = TimezoneFinder()

def to_local_time(utc_str, lat, lon):
    try:
        utc_dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
        tz_name = tf.timezone_at(lat=lat, lng=lon)
        if tz_name:
            local_dt = utc_dt.astimezone(ZoneInfo(tz_name))
            return local_dt.strftime("%Y-%m-%d %I:%M %p %Z")
    except:
        return utc_str
    return utc_str
