from .simple import ClockMode, MessageMode, ImageMode, OffMode
from .weather import WeatherMode
from .me import MeMode
from .finance import FinanceMode
from .music import MusicMode
from .postcard import PostcardMode
from .groupchat import GroupChatMode
from .camera import CameraMode
from .reader import ReaderMode
from .crypto import CryptoMode
from .system import SystemMode
from .gps import GpsMode
from .map import MapMode
from .buddy import BuddyMode

MODES = {m.name: m for m in (ClockMode, WeatherMode, MeMode, FinanceMode, MusicMode, PostcardMode, GroupChatMode, MessageMode, ImageMode, ReaderMode, CameraMode, CryptoMode, SystemMode, GpsMode, MapMode, BuddyMode, OffMode)}
