#!/usr/bin/env bash
# PiE-ink installer for Raspberry Pi OS (Bookworm or later).
# Run from the project directory:  ./setup.sh
# It asks one thing — the screen — and installs and sets up only what that screen needs:
#   an e-ink display (Waveshare 2.13" V4 or V3, 2.7" V2 or V1, 3.7"),
#   an LCD by itself (Waveshare 1.69" or 1.9", or a PiSugar Whisplay HAT),
#   an e-ink display with a 1.69" or 1.9" LCD beside it,
#   or a RetroFlag GPi Case 2W.
# --screen=... answers it without asking:
#   --screen=213v4 | 213v3 | 27v2 | 27v1 | 37     an e-ink display
#   --screen=lcd169 | lcd19 | whisplay             an LCD by itself
#   --screen=gpi                                   the GPi Case 2W
#   --lcd=1.69 | --lcd=1.9                         with an e-ink: an LCD beside it (--no-lcd: none)
# Re-run it to change your mind. (The older --whisplay, --gpi, --no-gpi and --lcd still work.)
# Add --autostart to also start PiE-ink automatically at boot.
# Add --no-speech to skip the voice and the listener (saves a few minutes and ~200 MB).
# Add --no-gestures to skip the hand tracker for hand gestures (on a 64-bit Pi; about 50 MB).
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USER_NAME="${SUDO_USER:-$USER}"
AUTOSTART=0
SPEECH=1
GESTURES=1
MEDIAPIPE=1.2.2                              # the hand tracker's version (what pie_ink/gestures.py is written for)
SCREEN=""
SECOND=""
NO_GPI=0
for arg in "$@"; do
  case "$arg" in
    --autostart) AUTOSTART=1 ;;
    --no-speech) SPEECH=0 ;;
    --no-gestures) GESTURES=0 ;;
    --screen=*) SCREEN="${arg#--screen=}" ;;
    --lcd=*) SECOND="${arg#--lcd=}" ;;
    --lcd) SECOND=keep ;;                      # (as before: a second screen, of the model it was)
    --no-lcd) SECOND=none ;;
    --whisplay) SCREEN=whisplay ;;
    --gpi) SCREEN=gpi ;;
    --no-gpi) NO_GPI=1 ;;
    *) echo "Unknown option: $arg (--screen=..., --lcd=1.69, --lcd=1.9, --no-lcd, --autostart, --no-speech, --no-gestures)"; exit 1 ;;
  esac
done

if [ -d /boot/firmware ]; then BOOT=/boot/firmware; else BOOT=/boot; fi
CONFIG="$BOOT/config.txt"
MODEL="$(tr -d '\0' 2>/dev/null < /proc/device-tree/model || true)"
GPI_NOW=0                                     # already set up for the case (by this, or by Pietato's setup.sh)
if grep -q '^# >>> gpi-case setup >>>' "$CONFIG" 2>/dev/null; then GPI_NOW=1; fi

# >>> which screen >>>
# The screen: asked, or answered by --screen= / --lcd=, or — run without a terminal and without
# them — what it is now. Out of it: SCREEN, DRIVER, LCD (a second one beside an e-ink: 0/1),
# LCD_MODEL, and EINK / GPI / WHISPLAY (0/1) for what to install.
EINKS=(213v4 213v3 27v2 27v1 37)
EINK_NAMES=('Waveshare 2.13" e-Paper HAT V4' 'Waveshare 2.13" e-Paper HAT V3' 'Waveshare 2.7" e-Paper HAT V2'
            'Waveshare 2.7" e-Paper HAT V1' 'Waveshare 3.7" e-Paper HAT')
screen_of() {
  case "$1" in
    epd2in13_V3) echo 213v3 ;; epd2in7_V2|mock27) echo 27v2 ;; epd2in7) echo 27v1 ;; epd3in7|mock37) echo 37 ;;
    whisplay|mock_whisplay) echo whisplay ;; lcd169|mock_lcd169) echo lcd169 ;; lcd19|mock_lcd19) echo lcd19 ;;
    gpicase|mock_gpicase) echo gpi ;; *) echo 213v4 ;;
  esac
}
driver_of() {
  case "$1" in
    213v4) echo epd2in13_V4 ;; 213v3) echo epd2in13_V3 ;; 27v2) echo epd2in7_V2 ;; 27v1) echo epd2in7 ;; 37) echo epd3in7 ;;
    whisplay) echo whisplay ;; lcd169) echo lcd169 ;; lcd19) echo lcd19 ;; gpi) echo gpicase ;; *) echo "" ;;
  esac
}
pick() {    # pick QUESTION DEFAULT CHOICE...: the number picked (asked until it's one of them)
  local question="$1" default="$2" i=1 answer
  shift 2
  echo >&2
  echo "$question" >&2
  for choice in "$@"; do echo "  $i) $choice" >&2; i=$((i + 1)); done
  while true; do
    read -r -p "Pick 1-$# [$default]: " answer || answer=""
    answer="${answer:-$default}"
    if [[ "$answer" =~ ^[0-9]+$ ]] && (( answer >= 1 && answer <= $# )); then echo "$answer"; return; fi
    echo "  (a number from 1 to $#)" >&2
  done
}
NOW="$(python3 -c "import sys; sys.path.insert(0, sys.argv[1]); from pie_ink import settings as s; c = s.load(); l = c.get('lcd', {}); print(c['display']['driver'], 1 if l.get('available') else 0, l.get('model', '1.69'))" "$DIR" 2>/dev/null || echo "epd2in13_V4 0 1.69")"
read -r NOW_DRIVER NOW_LCD NOW_MODEL <<< "$NOW"
NOW_SCREEN="$(screen_of "$NOW_DRIVER")"
if [[ "$GPI_NOW" == "1" && "$NO_GPI" != "1" ]]; then NOW_SCREEN=gpi; fi          # (a Pi set up for the case: that, by default)
if [[ "$NO_GPI" == "1" && "$NOW_SCREEN" == "gpi" ]]; then NOW_SCREEN=213v4; fi
if [[ "$NO_GPI" == "1" && "$SCREEN" == "gpi" ]]; then SCREEN=""; fi

if [[ -z "$SCREEN" ]] && [[ -t 0 || "${PIE_INK_ASK:-}" == "1" ]]; then     # (PIE_INK_ASK=1: ask with the answers piped in)
  case "$NOW_SCREEN" in
    gpi) KIND=4 ;; whisplay|lcd169|lcd19) KIND=2 ;; *) if [[ "$NOW_LCD" == "1" ]]; then KIND=3; else KIND=1; fi ;;
  esac
  KIND="$(pick "What screen is PiE-ink for?" "$KIND" "An e-ink display" "An LCD" \
    "An e-ink display and an LCD beside it" "A RetroFlag GPi Case 2W")"
  if [[ "$KIND" == "1" || "$KIND" == "3" ]]; then
    E=1
    for i in "${!EINKS[@]}"; do if [[ "${EINKS[$i]}" == "$NOW_SCREEN" ]]; then E=$((i + 1)); fi; done
    E="$(pick "Which e-ink display?" "$E" "${EINK_NAMES[@]}")"
    SCREEN="${EINKS[$((E - 1))]}"
    if [[ "$KIND" == "3" ]]; then
      L=1; if [[ "$NOW_MODEL" == "1.9" ]]; then L=2; fi
      L="$(pick "Which LCD beside it?" "$L" 'Waveshare 1.69" LCD (240×280)' 'Waveshare 1.9" LCD (170×320)')"
      if [[ "$L" == "2" ]]; then SECOND=1.9; else SECOND=1.69; fi
    else
      SECOND=none
    fi
  elif [[ "$KIND" == "2" ]]; then
    case "$NOW_SCREEN" in lcd19) L=2 ;; whisplay) L=3 ;; *) L=1 ;; esac
    L="$(pick "Which LCD?" "$L" 'Waveshare 1.69" LCD (240×280)' 'Waveshare 1.9" LCD (170×320)' \
      'PiSugar Whisplay HAT (1.69", with a speaker, microphones and a button)')"
    SCREEN="$(case "$L" in 1) echo lcd169 ;; 2) echo lcd19 ;; *) echo whisplay ;; esac)"
    SECOND=none
  else
    SCREEN=gpi
    SECOND=none
  fi
fi
SCREEN="${SCREEN:-$NOW_SCREEN}"
DRIVER="$(driver_of "$SCREEN")"
if [[ -z "$DRIVER" ]]; then
  echo "Unknown screen: $SCREEN (213v4, 213v3, 27v2, 27v1, 37, lcd169, lcd19, whisplay or gpi)"; exit 1
fi
EINK=0; GPI=0; WHISPLAY=0
case "$SCREEN" in gpi) GPI=1 ;; whisplay) WHISPLAY=1 ;; lcd169|lcd19) ;; *) EINK=1 ;; esac
LCD=0; LCD_MODEL="$NOW_MODEL"
case "$SECOND" in
  ""|keep|none|1.69|1.9) ;;
  *) echo "Unknown LCD: $SECOND (--lcd=1.69 or --lcd=1.9)"; exit 1 ;;
esac
if [[ "$EINK" == "1" ]]; then
  case "$SECOND" in
    "") LCD="$NOW_LCD" ;;
    keep) LCD=1 ;;
    1.69|1.9) LCD=1; LCD_MODEL="$SECOND" ;;
  esac
elif [[ "$SECOND" == "keep" || "$SECOND" == "1.69" || "$SECOND" == "1.9" ]]; then
  echo "    (An LCD beside it goes with an e-ink display; this screen is the only one.)"
fi
if [[ "$GPI" == "1" && "$MODEL" != *Zero* && -n "$MODEL" ]]; then
  echo "!! The GPi Case 2W takes a Pi Zero 2 W; this is a $MODEL. Setting up for the case anyway."
fi
# <<< which screen <<<

if ! python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)"; then
  echo "PiE-ink needs Python 3.9 or newer (this is $(python3 --version))."; exit 1
fi

echo "==> Installing packages"
# what every screen needs; the SPI and GPIO libraries only for the screens on the header's SPI
# (the e-ink, the LCDs, the Whisplay) — the GPi Case's screen is the Pi's framebuffer
PACKAGES=(python3-pil python3-numpy python3-flask python3-yaml python3-requests python3-psutil fortune-mod
          python3-picamera2 python3-opencv opencv-data poppler-utils python3-waitress python3-bs4
          mpg123 alsa-utils espeak-ng lame python3-pip curl unzip git)
if [[ "$GPI" != "1" ]]; then
  PACKAGES+=(python3-spidev python3-gpiozero python3-lgpio)
fi
sudo apt-get update -qq
sudo apt-get install -y -qq "${PACKAGES[@]}"

if [[ "$GPI" != "1" ]]; then
  # gpiozero must use lgpio (RPi.GPIO cannot drive a Pi 5)
  python3 -c "import lgpio" 2>/dev/null || echo "!! python3-lgpio failed to install; the panel will not work on a Pi 5"
fi

if [[ "$SPEECH" == "1" ]]; then
  echo "==> Installing the voice (Piper) and the listener (Vosk) — this takes a few minutes"
  pip3 install --break-system-packages --quiet --no-input piper-tts vosk \
    || echo "!! couldn't install piper-tts/vosk; the plain voice still works"

  MODEL_DIR="$DIR/data/stt"
  if [[ ! -d "$MODEL_DIR/vosk-model-small-en-us-0.15" ]]; then
    echo "==> Fetching the speech model for listening (about 40 MB)"
    mkdir -p "$MODEL_DIR"
    if curl -fsSL -o /tmp/vosk-model.zip \
        https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip; then
      unzip -q -o /tmp/vosk-model.zip -d "$MODEL_DIR" && rm -f /tmp/vosk-model.zip
    else
      echo "!! couldn't fetch the speech model; you can get it later in Settings → Sound"
    fi
  fi

  VOICES="$(ls "$DIR"/data/voices/*.onnx 2>/dev/null | wc -l)"
  if [[ "$VOICES" -gt 0 ]]; then
    echo "==> Voice ready: $(basename "$(ls "$DIR"/data/voices/*.onnx | head -1)" .onnx)"
  else
    echo "!! no voice in data/voices — pick one in Settings → Sound"
  fi
fi

if [[ "$GESTURES" == "1" ]]; then
  # hand gestures (Settings → Home): MediaPipe's hand tracker, on the 64-bit OS (it has no 32-bit build).
  # Without its own extra packages: numpy, OpenCV and Pillow are the system's, as above — pip's would
  # stand in front of them and could unsettle the camera.
  if [[ "$(uname -m)" == "aarch64" ]]; then
    echo "==> Installing the hand tracker (MediaPipe), for hand gestures"
    sudo apt-get install -y -qq python3-matplotlib >/dev/null || true
    pip3 install --break-system-packages --quiet --no-input --no-deps "mediapipe==$MEDIAPIPE" \
      || echo "!! couldn't install MediaPipe; hand gestures won't be offered"
    HAND_MODEL="$DIR/data/models/hand_landmarker.task"
    if [[ ! -s "$HAND_MODEL" ]]; then
      mkdir -p "$DIR/data/models"
      if curl -fsSL -o "$HAND_MODEL.part" \
          https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task; then
        mv "$HAND_MODEL.part" "$HAND_MODEL"
      else
        rm -f "$HAND_MODEL.part"
        echo "!! couldn't fetch the hand model; it's fetched when hand gestures are switched on"
      fi
    fi
  else
    echo "    (Hand gestures need the 64-bit Raspberry Pi OS — skipped.)"
  fi
fi

echo "==> Screens"
# the screen picked above, kept in config.yaml (./setup.sh again changes it); with one screen the page
# shows nothing about a second at all
python3 - "$DIR" "$DRIVER" "$LCD" "$LCD_MODEL" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
from pie_ink import settings as s
driver, second, model = sys.argv[2], sys.argv[3] == "1", sys.argv[4]
config = s.load()
was = config["display"].get("driver")
patch = {"display": {"driver": driver, "auto": False}, "lcd": {"available": second}}   # the screen you said
if not second:
    patch["lcd"]["enabled"] = False          # one screen: a second one switched on earlier stays off
elif model in ("1.69", "1.9"):
    patch["lcd"]["model"] = model
CASE_SPEAKER = "plughw:CARD=Headphones,DEV=0"   # the GPi Case's speaker: the Pi's own headphone output, on the case's pins
if driver == "gpicase":
    if not config["audio"].get("device"):
        patch["audio"] = {"device": CASE_SPEAKER}
elif was == "gpicase" and config["audio"].get("device") == CASE_SPEAKER:
    patch["audio"] = {"device": ""}           # out of the case: the default sound again
s.save(s.merge(config, patch))
PY
case "$SCREEN" in
  gpi) echo "    The GPi Case's screen is the panel; each screen has buttons of its own, shown along its bottom." ;;
  whisplay) echo "    The Whisplay HAT's screen is the panel." ;;
  lcd169) echo "    The 1.69\" LCD is the panel, by itself (wired to CE1, DC 22, RST 27, BL 12 — docs/guide.md)." ;;
  lcd19) echo "    The 1.9\" LCD is the panel, by itself (wired to CE1, DC 22, RST 27, BL 12 — docs/guide.md)." ;;
  *)
    if [[ "$LCD" == "1" ]]; then
      echo "    The e-ink, and the $LCD_MODEL\" LCD beside it (Settings → Screen → Second screen)."
    else
      echo "    The e-ink, by itself. With an LCD beside it later: ./setup.sh --lcd=1.69 (or --lcd=1.9)"
    fi ;;
esac

SPI_REBOOT=0
GPI_REBOOT=0
if [[ "$GPI" == "1" ]]; then
  echo "==> Setting up for the GPi Case 2W: its screen, speaker and power switch"
  # the case's screen is on the Pi's pins (DPI, GPIO 0-17 and 20-25): SPI and I2C would take some of
  # them, so they're off; the speaker is PWM on GPIO 18/19, the power switch on 26/27. The same
  # block in config.txt as Pietato's setup.sh writes (the markers too), so the two agree on it.
  sudo apt-get install -y -qq device-tree-compiler
  if command -v raspi-config >/dev/null; then
    sudo raspi-config nonint do_spi 1 || true
    sudo raspi-config nonint do_i2c 1 || true
  fi
  TMP="$(mktemp -d)"
  cat > "$TMP/gpicase-dpi.dts" <<'DTS'
/dts-v1/;
/plugin/;
/ {
    compatible = "brcm,bcm2835";
    fragment@0 {
        target = <&fb>;
        __overlay__ {
            pinctrl-names = "default";
            pinctrl-0 = <&gpicase_dpi_pins>;
        };
    };
    fragment@1 {
        target = <&vc4>;
        __overlay__ { status = "disabled"; };
    };
    fragment@2 {
        target = <&gpio>;
        __overlay__ {
            gpicase_dpi_pins: gpicase_dpi_pins {
                brcm,pins = <0 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 20 21 22 23 24 25>;
                brcm,function = <6>;
                brcm,pull = <0>;
            };
        };
    };
};
DTS
  sudo dtc -@ -q -I dts -O dtb -o "$BOOT/overlays/gpicase-dpi.dtbo" "$TMP/gpicase-dpi.dts"
  rm -rf "$TMP"
  if [[ "$GPI_NOW" != "1" ]]; then
    sudo cp "$CONFIG" "$CONFIG.bak-$(date +%Y%m%d-%H%M%S)"
    # settings for the case added by hand earlier: out, so nothing is there twice
    sudo sed -i -E '/^\s*dtoverlay=(dpi24|gpicase-dpi|audremap)/d;
               /^\s*(enable_dpi_lcd|display_default_lcd|dpi_group|dpi_mode|dpi_output_format|hdmi_timings|framebuffer_width|framebuffer_height)=/d;
               /^\s*dtoverlay=gpio-shutdown.*gpio_pin=26/d;
               /^\s*gpio=27=op,dh/d;
               /^\s*disable_audio_dither=/d;
               /^\s*dtparam=spi=on/d;
               /^# GPi Case 2W screen/d;
               /^# case speaker/d' "$CONFIG"
    # the case's screen settings only work with KMS off
    sudo sed -i -E 's/^\s*dtoverlay=vc4-kms-v3d/#dtoverlay=vc4-kms-v3d/' "$CONFIG"
    sudo tee -a "$CONFIG" >/dev/null <<'GPICONF'
# >>> gpi-case setup >>>
[all]
# GPi Case 2W screen
dtoverlay=gpicase-dpi
enable_dpi_lcd=1
display_default_lcd=1
dpi_group=2
dpi_mode=87
dpi_output_format=0x00016
hdmi_timings=640 0 1 1 20 480 0 1 1 2 0 0 0 60 0 19200000 1
framebuffer_width=640
framebuffer_height=480
disable_overscan=1
# GPi Case 2W speaker (PWM on GPIO 18/19); no hiss under quiet sound
dtparam=audio=on
dtoverlay=audremap,pins_18_19,enable_jack
disable_audio_dither=1
# GPi Case 2W power switch: GPIO 27 keeps the case on while the Pi shuts down, and
# switching off (GPIO 26 goes low) shuts it down safely. The case's SAFE SHUTDOWN
# switch, behind the cartridge, goes to ON.
gpio=27=op,dh
dtoverlay=gpio-shutdown,gpio_pin=26,active_low=1,gpio_pull=up
# <<< gpi-case setup <<<
GPICONF
    GPI_REBOOT=1
    echo "    $CONFIG has the case's screen, speaker and power switch now (a backup is next to it)."
  else
    echo "    $CONFIG is set up for the case already."
  fi
  # RetroFlag's own safe-shutdown script would fight over the same pins
  if [ -f /etc/rc.local ] && grep -qE '^[^#]*RetroFlag/SafeShutdown\.py' /etc/rc.local; then
    sudo sed -i -E 's|^([^#]*RetroFlag/SafeShutdown\.py.*)$|# \1   # (the power switch is in config.txt now)|' /etc/rc.local
    echo "    Turned off RetroFlag's SafeShutdown.py in /etc/rc.local: config.txt looks after the power switch."
  fi
  if [ -f /etc/systemd/system/pitato.service ]; then
    echo "!! Pietato is set up on this Pi too. Only one of them can have the case's screen and buttons:"
    echo "   sudo systemctl disable --now pitato   (for PiE-ink on the screen), or leave PiE-ink's service off."
  fi
else
  if [[ "$GPI_NOW" == "1" ]]; then
    if [ -f /etc/systemd/system/pitato.service ]; then
      echo "    (This Pi's config.txt is set up for a GPi Case — by Pietato's setup.sh — and is left as it is.)"
    else
      echo "==> Taking the GPi Case settings out of $CONFIG (a backup is next to it)"
      sudo cp "$CONFIG" "$CONFIG.bak-$(date +%Y%m%d-%H%M%S)"
      sudo sed -i '/^# >>> gpi-case setup >>>/,/^# <<< gpi-case setup <<</d' "$CONFIG"
      sudo sed -i -E 's/^#dtoverlay=vc4-kms-v3d/dtoverlay=vc4-kms-v3d/' "$CONFIG"
      GPI_REBOOT=1
    fi
  fi
  echo "==> Enabling SPI"
  if command -v raspi-config >/dev/null; then
    sudo raspi-config nonint do_spi 0
  fi
  # the e-ink is on /dev/spidev0.0; raspi-config switches SPI on live, but not on every kernel —
  # then it needs the reboot, and the screen stays blank until it gets one
  if ! ls /dev/spidev0.0 >/dev/null 2>&1; then
    sudo dtparam spi=on 2>/dev/null || true
    sleep 1
  fi
  if ! ls /dev/spidev0.0 >/dev/null 2>&1; then
    SPI_REBOOT=1
    echo "!! /dev/spidev0.0 isn't there yet: SPI takes effect at the next boot — reboot when this is done, then the screen works"
  fi
fi
sudo usermod -aG spi,gpio,i2c,video,audio,input,tty,plugdev "$USER_NAME" 2>/dev/null || true

if [[ "$WHISPLAY" == "1" ]]; then
  echo "==> Installing the Whisplay HAT's sound card driver (PiSugar's, from GitHub)"
  # the screen, button and LED need nothing extra (spidev and lgpio, above); the WM8960 sound
  # card is a kernel module and overlay from PiSugar's own repository
  if command -v raspi-config >/dev/null; then
    sudo raspi-config nonint do_i2c 0
  fi
  rm -rf /tmp/whisplay
  if git clone --depth 1 https://github.com/PiSugar/whisplay /tmp/whisplay 2>/dev/null; then
    INSTALLER=""
    for f in install_driver.sh audio/whisplay-soundcard/install_driver.sh script/install_rpi.sh; do
      if [[ -f "/tmp/whisplay/$f" ]]; then INSTALLER="$f"; break; fi
    done
    if [[ -n "$INSTALLER" ]]; then
      (cd /tmp/whisplay && sudo bash "$INSTALLER") || echo "!! the Whisplay sound card driver did not install; see https://github.com/PiSugar/whisplay"
      echo "    Reboot once when this is done; the screen is the panel already, and its speaker and"
      echo "    microphone are picked under Settings → Sound."
    else
      echo "!! couldn't find PiSugar's installer in their repository; run it by hand from /tmp/whisplay"
    fi
  else
    echo "!! couldn't fetch https://github.com/PiSugar/whisplay (no network?); the screen still works, the sound card won't"
  fi
fi

# a udev rule from an earlier version (for a USB GPS, no longer part of this) is cleared away
sudo rm -f /etc/udev/rules.d/99-pie-ink-gps.rules

echo "==> USB drives: mounted as they're plugged in, so the bot can find what's on them"
# a desktop mounts drives itself (under /media/$USER_NAME); without one nothing does, so this rule
# mounts each filesystem on a USB disk at /media/sda1 and the like — read-only, so it can be pulled out
# any time — and systemd unmounts it when it's pulled. The disk the Pi runs from is left alone.
if [[ "$(systemctl get-default 2>/dev/null)" == "graphical.target" ]]; then
  echo "    (the desktop mounts them already)"
  sudo rm -f /etc/udev/rules.d/99-pie-ink-usb.rules /usr/local/sbin/pie-ink-usb-mount
else
  sudo install -m 755 "$DIR/pie_ink/usb-mount.sh" /usr/local/sbin/pie-ink-usb-mount
  printf 'ACTION=="add", SUBSYSTEMS=="usb", SUBSYSTEM=="block", ENV{ID_FS_USAGE}=="filesystem", RUN+="/usr/local/sbin/pie-ink-usb-mount $devnode %%k $env{ID_FS_TYPE} %s %s"\n' \
    "$(id -u "$USER_NAME")" "$(id -g "$USER_NAME")" | sudo tee /etc/udev/rules.d/99-pie-ink-usb.rules >/dev/null
  sudo udevadm control --reload-rules 2>/dev/null || true
  sudo udevadm trigger --action=add --subsystem-match=block 2>/dev/null || true    # (one in already: mounted now)
fi

echo "==> Allowing reboot, shutdown and apt updates from the web page without a password"
printf '%s ALL=(root) NOPASSWD: /sbin/reboot, /sbin/shutdown, /usr/bin/apt-get, /usr/bin/systemctl restart pie-ink\n' "$USER_NAME" \
  | sudo tee /etc/sudoers.d/pie-ink >/dev/null
sudo chmod 440 /etc/sudoers.d/pie-ink

echo "==> Installing systemd service (not started at boot unless you ask)"
sed -e "s|__DIR__|$DIR|g" -e "s|__USER__|$USER_NAME|g" "$DIR/pie-ink.service" \
  | sudo tee /etc/systemd/system/pie-ink.service >/dev/null
DROPIN=/etc/systemd/system/pie-ink.service.d
if [[ "$GPI" == "1" ]]; then
  # in the case, the console is kept off its screen while PiE-ink draws on it (pie_ink/vt.py, as root)
  sudo install -d "$DROPIN"
  printf '[Service]\nExecStartPre=+/usr/bin/python3 %s/pie_ink/vt.py graphics\nExecStopPost=+/usr/bin/python3 %s/pie_ink/vt.py text\n' "$DIR" "$DIR" \
    | sudo tee "$DROPIN/gpi-console.conf" >/dev/null
else
  sudo rm -f "$DROPIN/gpi-console.conf"
fi
sudo systemctl daemon-reload
if [[ "$GPI" == "1" && "$AUTOSTART" != "1" ]] && ! systemctl is-enabled --quiet pitato 2>/dev/null; then
  AUTOSTART=1                                   # in the case, PiE-ink is what's on its screen: there from every boot
  echo "    In the GPi Case it starts at every boot (sudo systemctl disable pie-ink stops that)."
fi
if [[ "$AUTOSTART" == "1" ]]; then
  sudo systemctl enable pie-ink.service
  echo "==> Will start at boot"
else
  sudo systemctl disable pie-ink.service 2>/dev/null || true
fi
sudo systemctl restart pie-ink.service
sleep 2
if systemctl is-active --quiet pie-ink; then
  echo "==> Service running"
else
  echo "!! Service failed to start; see: journalctl -u pie-ink -n 50"
fi

IP="$(hostname -I | awk '{print $1}')"
echo
echo "Done. Open http://${IP}:5000 (or http://$(hostname).local:5000)"
echo "Start / stop:   sudo systemctl start pie-ink   |   sudo systemctl stop pie-ink"
echo "Start at boot:  sudo systemctl enable pie-ink  (or re-run ./setup.sh --autostart)"
echo "Logs:           journalctl -u pie-ink -f"
if [[ "$SPEECH" == "1" ]]; then
  echo "Speech:         voice and listening are installed; say your wake phrase or press Test the speaker"
fi
if [[ -f /etc/udev/rules.d/99-pie-ink-usb.rules ]]; then
  echo "USB drives:     mounted as they go in (read-only, so pull them out whenever); ask the bot what's on them"
fi
if [[ "$WHISPLAY" == "1" ]]; then
  echo "Whisplay:       its screen is the panel; reboot once for the sound card, then Settings → Sound"
fi
if [[ "$GPI" == "1" ]]; then
  echo "GPi Case 2W:    its screen is the panel, its d-pad changes the screens, its switch shuts the Pi down"
fi
if [[ "$GPI_REBOOT" == "1" ]]; then
  echo "Screen:         config.txt changed for the case — sudo reboot, and its screen comes up"
fi
if [[ "$SPI_REBOOT" == "1" ]]; then
  echo "Screen:         SPI was only just switched on — sudo reboot, and the screen comes up"
fi
if [[ "$LCD" == "1" ]]; then
  echo "Second screen:  the $LCD_MODEL\" LCD — Settings → Screen → Second screen (wiring in docs/guide.md)"
elif [[ "$EINK" == "1" ]]; then
  echo "Second screen:  none — ./setup.sh --lcd=1.69 (or --lcd=1.9) if you add one"
fi
