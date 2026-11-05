from .simple import ClockMode, MessageMode, ImageMode, OffMode
from .weather import WeatherMode
from .me import MeMode
from .earnings import EarningsMode
from .music import MusicMode
from .postcard import PostcardMode
from .groupchat import GroupChatMode
from .camera import CameraMode
from .reader import ReaderMode
from .crypto import CryptoMode
from .system import SystemMode
from .buddy import BuddyMode

MODES = {m.name: m for m in (ClockMode, WeatherMode, MeMode, EarningsMode, MusicMode, PostcardMode, GroupChatMode, MessageMode, ImageMode, ReaderMode, CameraMode, CryptoMode, SystemMode, BuddyMode, OffMode)}
