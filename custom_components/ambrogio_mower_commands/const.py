"""Constants for the Ambrogio Mower Commands integration."""

DOMAIN = "ambrogio_mower_commands"

# API configuration
API_BASE_URI = "https://api-de.devicewise.com/api"
API_APP_TOKEN = "DJMYYngGNEit40vA"
API_CLIENT_KEY_DEFAULT = "x" * 28
API_CLIENT_KEY_LENGTH = 28
API_ACK_TIMEOUT = 30

# Datetime formats (from API responses)
API_DATETIME_FORMAT_DEFAULT = "%Y-%m-%dT%H:%M:%S.%f%z"
API_DATETIME_FORMAT_FALLBACK = "%Y-%m-%dT%H:%M:%S%z"

# Config entry keys
CONF_IMEI = "imei"
CONF_CLIENT_KEY = "client_key"
CONF_CLIENT_NAME = "client_name"

# Service names
SERVICE_SET_PROFILE = "set_profile"
SERVICE_WORK_NOW = "work_now"
SERVICE_BORDER_CUT = "border_cut"
SERVICE_CHARGE_NOW = "charge_now"
SERVICE_CHARGE_UNTIL = "charge_until"
SERVICE_TRACE_POSITION = "trace_position"
SERVICE_KEEP_OUT = "keep_out"
SERVICE_WAKE_UP = "wake_up"
SERVICE_THING_FIND = "thing_find"
SERVICE_THING_LIST = "thing_list"

# Common attributes
ATTR_PROFILE = "profile"
ATTR_HOURS = "hours"
ATTR_MINUTES = "minutes"
ATTR_WEEKDAY = "weekday"
ATTR_LOCATION = "location"
ATTR_LATITUDE = "latitude"
ATTR_LONGITUDE = "longitude"
ATTR_RADIUS = "radius"
ATTR_INDEX = "index"

# -----------------------------
# Integration runtime data keys
# -----------------------------
# These keys index into hass.data[DOMAIN][entry_id]
KEY_CLIENT = "client"
KEY_IMEI = "imei"
KEY_CLIENT_NAME = "client_name"
KEY_QUEUE = "queue"
KEY_STATE = "state"  # dict storing latest lat/lon, connected, loc_updated, info, source

# -----------------------------
# Dispatcher signal for sensors
# -----------------------------
SIGNAL_STATE_UPDATED = "ambrogio_mower_commands_state_updated"

# -----------------------------
# Sensor unique_id suffixes
# -----------------------------
UNIQUE_SUFFIX_LOCATION = "location"
UNIQUE_SUFFIX_INFO = "info"
