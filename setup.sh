#!/usr/bin/env bash
# PiE-ink installer for Raspberry Pi OS (Bookworm or later).
# Run from the project directory:  ./setup.sh
# Add --autostart to also start PiE-ink automatically at boot.
# Add --no-speech to skip the voice and the listener (saves a few minutes and ~200 MB).
# Add --whisplay if the screen is a PiSugar Whisplay HAT: installs its sound card driver.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USER_NAME="${SUDO_USER:-$USER}"
AUTOSTART=0
SPEECH=1
WHISPLAY=0
for arg in "$@"; do
  case "$arg" in
    --autostart) AUTOSTART=1 ;;
    --no-speech) SPEECH=0 ;;
    --whisplay) WHISPLAY=1 ;;
    *) echo "Unknown option: $arg"; exit 1 ;;
  esac
done

if ! python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)"; then
  echo "PiE-ink needs Python 3.9 or newer (this is $(python3 --version))."; exit 1
fi

echo "==> Installing packages"
sudo apt-get update -qq
sudo apt-get install -y -qq \
  python3-pil python3-numpy python3-flask python3-yaml python3-requests \
  python3-psutil python3-spidev python3-gpiozero python3-lgpio fortune-mod \
  python3-picamera2 python3-opencv opencv-data poppler-utils python3-waitress python3-bs4 \
  mpg123 alsa-utils espeak-ng lame python3-pip curl unzip git python3-serial

# gpiozero must use lgpio (RPi.GPIO cannot drive a Pi 5)
python3 -c "import lgpio" 2>/dev/null || echo "!! python3-lgpio failed to install; the panel will not work on a Pi 5"

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

echo "==> Enabling SPI"
if command -v raspi-config >/dev/null; then
  sudo raspi-config nonint do_spi 0
fi
sudo usermod -aG spi,gpio,i2c,video,audio,plugdev "$USER_NAME" 2>/dev/null || true

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
      echo "    Reboot once when this is done; then pick the Whisplay under Settings → Screen → Panel,"
      echo "    and its speaker and microphone under Settings → Sound."
    else
      echo "!! couldn't find PiSugar's installer in their repository; run it by hand from /tmp/whisplay"
    fi
  else
    echo "!! couldn't fetch https://github.com/PiSugar/whisplay (no network?); the screen still works, the sound card won't"
  fi
fi

echo "==> Setting up for a USB GPS receiver (plug one in whenever you like)"
# reading a serial port needs the dialout group; a u-blox dongle gets a steady name, /dev/gps0,
# and ModemManager (if installed) is told to leave it alone rather than probe it as a modem
sudo usermod -aG dialout "$USER_NAME" 2>/dev/null || true
printf '%s\n' \
  '# PiE-ink: a u-blox USB GPS (6/7/8/M8) is /dev/gps0, readable by dialout, not a modem' \
  'SUBSYSTEM=="tty", ATTRS{idVendor}=="1546", SYMLINK+="gps0", MODE="0660", GROUP="dialout", ENV{ID_MM_DEVICE_IGNORE}="1"' \
  | sudo tee /etc/udev/rules.d/99-pie-ink-gps.rules >/dev/null
sudo udevadm control --reload-rules 2>/dev/null || true
sudo udevadm trigger 2>/dev/null || true
if systemctl is-enabled --quiet gpsd.socket 2>/dev/null || systemctl is-active --quiet gpsd 2>/dev/null; then
  echo "    gpsd is installed and will grab the receiver; that is fine — PiE-ink reads through gpsd then."
fi

echo "==> Allowing reboot, shutdown and apt updates from the web page without a password"
printf '%s ALL=(root) NOPASSWD: /sbin/reboot, /sbin/shutdown, /usr/bin/apt-get, /usr/bin/systemctl restart pie-ink\n' "$USER_NAME" \
  | sudo tee /etc/sudoers.d/pie-ink >/dev/null
sudo chmod 440 /etc/sudoers.d/pie-ink

echo "==> Installing systemd service (not started at boot unless you ask)"
sed -e "s|__DIR__|$DIR|g" -e "s|__USER__|$USER_NAME|g" "$DIR/pie-ink.service" \
  | sudo tee /etc/systemd/system/pie-ink.service >/dev/null
sudo systemctl daemon-reload
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
echo "GPS:            plug a USB receiver in and switch it on in the Weather tab"
if [[ "$WHISPLAY" == "1" ]]; then
  echo "Whisplay:       reboot, then Settings → Screen → Panel → PiSugar Whisplay HAT"
fi
