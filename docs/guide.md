# PiE-ink — the long version

Everything the [README](../README.md) only mentions: each screen, each
setting, each button, and what to do when something doesn't work. Start the
same way — install, open the page, pick a tab.

## Using it

Pick a tab, adjust, and press the big button. The preview updates as you
change things; the panel only updates when you send. Light or dark for the
page is under Settings → Screen. On a phone the tabs are one row of named
chips under the panel (swipe it for the rest), and under them a row of tiles
says what is going on right now — the bot's last line, the weather, what
your holdings are worth, what's playing, the watcher, the miners, the TV —
swipe that sideways too. On a wide screen the page is a dashboard: the tabs
in a rail down the left (with the second screen's picture if you have one);
the panel with, under it, a dropdown that puts any screen on it; beside them
one card with the tiles and, at its foot, the Pi's numbers under its
hostname; the tab's card underneath (its buttons in the title row); and
wider still, the conversation with the bot in a column of its own on the
right (hide it with the arrow in its corner; the speech-bubble button beside
the settings cog brings it back). With a Pietato about, the Clock, Message
and Draw tabs have an **On the Pietato** button beside the big one.

- **Clock** — the time, big, in any font; optionally seconds and the date.
- **Weather** — today's conditions in the same shape as the crypto screen:
  the temperature big, a face that reacts to the sky (sunglasses in the sun,
  sweating when it's hot, an umbrella face in the rain, asleep at night),
  feels-like, humidity, sunrise and sunset in the header, today's range, a
  four-day forecast with icons, and wind and rain chance in the footer. The
  location finds itself: the Pi's internet address puts it in the right
  town at start-up (**Find me** on the tab does it on demand); type a town
  only if you want somewhere else. Data from Open-Meteo (free, no key); for US locations the
  National Weather Service's worded forecast is scraped with BeautifulSoup.
- **Me** — a rotating handful of facts about your day: how far into the work
  block you are, how long you've been awake, the
  temperature, the first coin or share on your Finance list, the Pi's uptime,
  books on the shelf, how much of the
  year has gone, days until your birthday. A face reads the weather, the
  markets and the time and comments on them, and the bottom bar shows how far
  through the day, week or year you are.
- **A bot on the screen, from your own LLM.** The robot button beside the chat
  bar opens its panel. **Where it thinks** is either *This Pi* — Ollama running
  here, which suits a small model (a Pi 5 manages 1–3B; a Zero 2 W wants
  something like `gemma3:270m`) — or *Another computer* with its address, then
  Check and pick a model. With a remote machine you can also tick "If that
  machine is off, use this Pi" and choose a small model for it: when the big
  machine can't be reached it switches over by itself, and switches back when
  it returns. There's a chat bar
  along the bottom of every page — type whenever you like and the answer comes
  back as a single line above it. The chevron opens the whole conversation
  when you want it: what you typed, what you asked out loud through the
  microphone, and the remarks it made on its own; tap one of its replies to
  hear it again. The last few turns go with each question, so a follow-up like
  "and tomorrow?" carries on from what came before. What it says
  fills the screen under a face in the top-left corner that changes with the
  tone of the answer — pleased, curious, apologetic, sleepy — and there's a
  box to talk to it. The text grows to fill whatever room is left, so a short
  answer doesn't leave the screen half empty. With a speaker plugged in it
  reads its answers out loud
  ("Say it out loud" in the same section). It knows the time, what
  you're doing, the weather and your next event. Generation runs in the
  Ask it about the room — "what do you see?", "how do I look?" — and it takes
  a still from the camera and sends it with the question. That needs a model
  that can see: pick one under **Eyes** in the Message tab (Gemma, LLaVA,
  Qwen-VL — gpt-oss and the text-only Qwens can't, and it says so rather than
  failing quietly). Switch it off with "Let it look through the camera". The
  Draw screen has an **AI** button that shows it your drawing and has it say
  what it sees, out loud too. If the bot is on the panel at the time, your
  drawing goes up in the top-right corner with the description underneath in a
  smaller hand, as though it were narrating it; the room watcher does the same
  with the frame it looked at.
  It also pipes up by itself now and then — a remark about the weather, the
  coins, the time or how your day is going — on a loose timer you can set or
  switch off ("Remarks of its own"), staying quiet while music plays or while
  it's listening to you. Generation happens in the
  background so the screen never waits on it, and reasoning models (Qwen3,
  gpt-oss) are asked to skip the thinking pass, with any `<think>` block
  stripped. A big model takes a minute to load the first time;
  `llm.keep_alive` keeps it warm after that. If a reasoning model spends its
  whole token budget on thinking and answers with nothing, PiE-ink asks again
  with the cap lifted. Nothing leaves your network.
- **Earnings** — what your salary comes to and how much of today you have
  earned, ticking up through your paid blocks: what one pay packet is worth, a
  bar across the working day, the weekly, monthly and daily equivalents, and
  the year so far. It needs one number — your yearly income, in Settings →
  You — plus your daily schedule, set in the same place. The figure stays on the Pi, in
  `config.yaml`. (This screen was called Finance until the markets took that
  name; a `finance:` section in an old `config.yaml` is read as `earnings:`.)
- **Music** — plays what's in `data/music` (or any folder you point it at)
  through a USB speaker. The screen shows the track, the folder it came from,
  a progress bar and what's next; the tab has the playlist, transport
  buttons, volume, shuffle and repeat, and an upload button. mp3 and wav go
  through mpg123, which reports its position so the bar is accurate; flac and
  m4a fall back to ffplay. On the 2.7" HAT the keys work as previous, next
  and play/pause.
- **Message** — your own text, or something that changes on its own: a joke,
  a fortune, or a conversation topic for when it goes quiet. Font, size,
  alignment, and how often to fetch a new one; tick **Read** beside that slider
  and it speaks every new one as it appears, so a 30-second cycle becomes a
  joke every 30 seconds with nothing to press. **Save as MP3** keeps whatever
  is on screen as a file.
- **Draw** — pen, line, rectangle and circle (outline or solid), fill (tap
  inside a closed shape to fill it with black), text (tap where it goes and type), photos, eraser,
  undo/redo. Everything stacks in the order you add it — ink, then a photo
  over it, more ink over the photo, text on top — and keeps that order when
  you move things. With the Select tool, tap a photo or text to pick it up:
  drag to move, drag the corner to resize and rotate, tap text again to
  change the words. Photos are dithered to black and white on the way to the
  panel. With a second screen switched on, both screens' canvases sit side
  by side on a wide screen — the e-ink's on the left, the LCD's on the
  right, the one you're drawing on outlined; tap the other to draw for that
  screen instead. On a phone there's room for one, so a **Draw for** switch
  at the top of the tab picks it: **E-ink** as above, or **LCD** — the canvas
  turns portrait (or landscape, if the LCD is mounted that way), kept to
  about a third of the phone's height so the tools stay in reach. Drawing
  for the LCD, a row of colours appears, strokes stay smooth, and **Send to
  the LCD** puts the picture up when you're ready. Tick **Live** in the row
  of buttons and each stroke lands on the LCD as you make it instead, no
  sending (the choice is remembered). What is on the LCD comes back into the
  tab when you return to it, from any phone, so you can carry on.
- **Read** — the top shows what's open with page arrows, a page slider and
  the settings that matter for it (font and size for a book, zoom for a
  comic); everything else is under "More settings". The library lists your
  books with the page you're on; **+ Add** uploads a file (EPUB, TXT, PDF for
  books; CBZ, PDF, images for comics) or fetches free ones — Project
  Gutenberg (75,000 public-domain books, searchable), xkcd (random, latest or
  by number), and the Internet Archive's scanned Golden Age comics. Books are
  re-flowed to the screen; comics scan through in screen-sized panels. Turn
  pages with the arrows, a swipe on the preview, the arrow keys, or the HAT's
  buttons; your place is remembered per book.
- **Camera** — a live colour view on the page (10–12 fps; a USB webcam's own
  MJPEG frames go to the page as they are, so even a Zero 2 W keeps up, and a
  phone on a weak signal gets smaller frames rather than a picture that
  falls behind) and a black-and-white frame on the panel every second
  (adjustable). If other
  Pis on your network run PiE-ink they find each other automatically (a
  small UDP broadcast, nothing leaves the LAN), and **Other cameras** shows
  all their feeds at once; System lists them with links. Pi camera via
  `picamera2`, or a USB webcam; a test pattern without either. The view on the
  page is framed exactly like the panel. Fill or whole, mirror, dither, auto
  contrast, and a rotate button for the screen.
  With more than one camera plugged in, the Camera tab lists them all — pick
  one, or press **Next** to flip through; key 4 on the 2.7" HAT does the same
  while the camera screen is up, and the panel shows a small label for a few
  seconds after a switch so you know which one you're looking at. The choice
  is remembered.
  A camera with two lenses (a stereo or dual-lens module, such as an ELP
  binocular one) sends both views in one wide frame, side by side; that's
  noticed, and the Camera tab grows **Two lenses** — both, or the left or
  right view alone, which is also what the screen, the watcher and photos
  get — and **Swap the two views**. One mounted upside down needs the screen
  (or the view) turned 180° *and* the swap, since turning the whole frame
  puts each lens's picture on the other side.
- **Finance** — coins and shares, one page each, in turn: the Cryptogotchi
  with a face that reacts to the market, a graph, ranges, percentage changes
  and headlines. A coin's prices come from CoinGecko, with its rank, market
  cap, supply, the community's vote (the face follows it) and the all-time
  high. A share's — Apple, Amazon, Tesla, an ETF, a London or Frankfurt
  listing — come from Yahoo Finance, a few minutes behind the exchange and
  with no key to get: its exchange where a coin shows its rank, the previous
  close, the day's range and volume, the 52-week range where a coin shows its
  all-time high (and where the price sits in it, where a coin shows the
  vote), whether the market is **OPEN** or **CLOSED** (in the footer, between
  the 52-week high and how far under it the price is), the face following
  today's move, headlines about that company, and prices in the exchange's
  own money (pence become pounds). Type a name or a
  ticker in the box — *bitcoin*, *DOGE*, *AAPL*, *Tesla* — and both kinds
  come up, the shares marked with their exchange. With **Duino-Coin** or
  **Verus** on, your miners take a turn as coins too. Put in how much of
  each you hold and what it all adds up to takes a turn as well, with the bot
  keeping an eye on it (a word when it passes the line you set, or a day goes
  badly). If a source can't be reached it shows the last saved numbers
  (marked "3h old"), and on a fresh install it shows bundled coin figures
  marked "old data" until the first fetch.
- **System** — CPU load and clock speed, temperature, memory and disk as a
  four-tile grid with icons and bars, plus IP and uptime. The same tab holds
  the panel type, invert, key mapping, the Pi's update, reboot and
  shutdown.
- Under the panel: **Rotate** turns the picture round (upside down on an
  e-ink; through the four ways on a colour panel), **Refresh** does a full
  refresh to clear ghosting, and **Off** blanks the panel and puts it to
  sleep. Each acts at once.

## Settings

The gear in the top-right corner opens everything to do with setup — the
panel and its keys, the speaker and voice, the microphone and wake phrase,
the screen cycle, and the Pi's own updates and power — in one sheet with a single
Save. The tabs along the top stay for the screens themselves.

Everything lives in `config.yaml` next to `app.py`, which stays on the
Pi; the page is the way to change it, but a PiE-ink that is running also
notices the file changing under it — an edit with nano, a script — within
a few seconds, takes the changes up, and never writes its own older copy
back over them.

## Sound

Plug a USB speaker in and pick it under **Settings → Sound** (the
list comes from `aplay -l`). No speaker on this Pi? A
[Pietato](https://github.com/frogCaller/Pietato) on the network can be its
voice: pick **A Pietato's speaker** in the same list and every line — the
bot's answers, reminders, *say …* — is made here with the voice you chose
and said there (its page takes a clip to say out loud, the way a coding
agent sends it one). A Pi with no sound card at all does this by itself when
a Pietato is about. Making the clip takes a moment on a Zero 2 W; the Pietato
keeps quiet while something is playing on it, and a Pietato page with a
password can't be asked. The voice is
[Piper](https://github.com/rhasspy/piper), a neural voice that runs offline on
the Pi and sounds like a person. Two one-time steps:

`setup.sh` installs Piper and the listener, and the project ships with a voice
in `data/voices`, so it speaks as soon as the service starts — nothing to
download and nothing to choose. Pass `--no-speech` to skip that (a few minutes
and around 200 MB). To do it by hand instead:

1. **Install Piper on the Pi** — a few minutes; it pulls in onnxruntime.
2. To use a different one, pick it and press **Get it** (about 60 MB). Seven are offered,
   British and American, including a fan-made Eminem impression. Any other
   Piper voice works too: paste a link to its `.onnx` file and the settings
   file beside it comes with it, or copy the `.onnx` and `.onnx.json` pair
   straight into `data/voices`.

Piper is started once at boot and kept alive with the voice loaded, so a line
begins speaking straight away instead of waiting for the model each time. Some
builds of Piper exit after every line; when that happens the voice is loaded
again immediately rather than on your next press, so the wait lands in the gap
instead of in front of you. Lines it has said before are played from a small
cache in `data/cache/tts` and start instantly.

On a Pi Zero 2 W, pick one of the **quick** voices at the top of the list — a
medium voice is several times slower to synthesise on that machine, and
Settings → Sound says so when it notices the Pi is short of memory. The same
section shows how long the last line took to start.
Asking for a new line stops whatever is being said immediately — press the
button five times and you hear the fifth.

Piper makes a line one sentence at a time, and gets the next sentence while
the last one is playing. A voice that takes longer to make speech than to say
it can't keep up however it's fed, so rather than stopping between sentences
it waits a moment before it starts, just long enough to say the whole line
without a break (at most 8 seconds). It learns how fast the voice is on this
Pi from the lines it makes (kept in `data/cache/tts/pace.json`), and Settings →
Sound shows it. A quick voice hardly waits at all.

The section also has **Test the speaker**, **Read this out loud** — which
speaks whatever the Message screen is showing, including text you have just
typed — **Save as MP3**, which records the same words in the current voice and
downloads the file — a speed slider, and a switch to read the bot's answers out loud as
they arrive. Volume goes through the card's own mixer where it has one.
Settings → Sound says which voice is actually speaking, so silence is never a
mystery. `setup.sh` installs `mpg123`, `alsa-utils` and `espeak-ng`; on an
existing install: `sudo apt install mpg123 alsa-utils espeak-ng`.

**Test the speaker** waits for the line to play and tells you where it went
("Played through USB Audio (plughw:1,0)") — or what `aplay` said when it
wouldn't ("No sound: plughw:1,0 said: audio open error: Device or resource
busy"). The speaker watches `aplay` for every line: if the chosen output
refuses it — busy, unplugged, a card number that changed after a reboot — it
tries again a couple of times, then the other outputs, and replays the line
from the start on whichever takes it; the message says so, so you can pick
that output in the list if it is the one you hear. A build of Piper that exits
after each line now gets every sentence of a long line, not just the first.

Outputs and microphones are kept by the sound card's name
(`plughw:CARD=UACDemoV10,DEV=0`), not its number. Card numbers are only the
order the kernel found things in, so plugging in something with a sound card
of its own — a camera with a microphone, like the OBSBOT — used to move them.
One picked the old way still works; one that isn't there shows as *Missing*
in the list rather than quietly becoming "System default".

**Speaker or mic missing?** (Settings → Sound) checks the USB bus, the sound
cards, the Pi's power flags, its USB driver and the kernel's USB messages, and
says what's wrong, verdict first. A speaker or mic the Pi doesn't see at all
isn't on the bus (`lsusb` doesn't list it), so it's never a setting: the lists
show exactly what the kernel brought up. When the Pi's own power sags, that's
the cause, and a USB hub with its own power adapter fixes it. When that's fine
but small devices on a hub won't come up (the kernel says `error -32` or
`-71`), and what's on the hub keeps dropping off and coming back, it's the
hub's power — the Pi's under-voltage flag can't see a hub's supply sagging,
and a motorised camera draws bursts of current that an unpowered hub on a
Zero can't pass on. The report lays out the test: unplug the camera, plug
only the speaker in, and watch; or plug it straight into the Pi's adapter,
without the hub. Comes up alone, and it's the load on the hub. **Pause the
camera for 2 minutes**, under the report, lets you try it beside the camera
without restarting. On a 64-bit system keep `dtoverlay=dwc2` in
`config.txt`: the Pi's own USB driver doesn't work in 64-bit mode, so `dwc2`
is the one there is
([raspberrypi/linux#6883](https://github.com/raspberrypi/linux/pull/6883)).
Only on a 32-bit one does the report suggest taking it out (the Pi's own driver
fits small devices in better), unless you use the Pi as a USB gadget. A camera
with a microphone of its own works for listening meanwhile.

**Watch the USB bus** (next to it) shows what the Pi makes of a device the
moment it's plugged in, line by line in plain words — "port 4 of the hub:
something plugged in (full speed)", "it wouldn't answer (error -32)", "the Pi
gave up on it", or "it's UACDemoV1.0" and "Sound card found" — for two
minutes, from your phone, so the tests above need no terminal. It reads the
kernel's log as it grows; a user that can't read it (`dmesg` refused) is told
so — `sudo usermod -aG adm $USER` and a restart fixes that.

"Unknown error 524" from the HDMI output means nothing that plays sound is
plugged into HDMI; the speaker's message says so.

### Play on this device

Switch on **Play on this device** (Settings → Sound, or the Music tab) and
PiE-ink's voice and music come out of the phone or computer you're using,
instead of the Pi's speaker, while that page is open. The switch is kept per
browser, so the Pi's speaker stays the default everywhere else. Music playing
on the Pi carries on in the page from where it was; switch it off (or close
the page) and the music pauses rather than jumping back to the Pi's speaker.

The page keeps one request open to the Pi, which answers the moment there's
something to play, so a tab in the background keeps up. Speech comes as short
pieces a second or so long, played back to back; music streams from the file,
and the page tells the Pi how far it has got, so the screen's progress bar and
the next track still work. Browsers only let a page make sound after you've
tapped it, so after a reload it starts at your first tap. It's output only: a
browser won't lend its microphone to a plain `http://` page.

## Talking to it

With a USB microphone plugged in, **Settings → Sound → Listen for a wake
phrase** lets you just say what you want. `setup.sh` has already installed the
listener and its speech model, so all that is left is to pick the microphone,
set your phrase — "hey cool beans", or anything you like — and switch it on. (If you
ran `setup.sh --no-speech`, the same section has **Install the listener on the
Pi** and **Get it** for the model.)

Leave the phrase empty and it answers everything it hears. **Test the
microphone** records four seconds and reports how loud you were and what it
made out, which separates a dead microphone from a mishearing in one press.

Say the phrase and the face opens its eyes wide so you know it's listening,
then shows what it heard while it thinks. Ask your question in one breath
("hey cool beans, what's the weather?") or in two. The answer appears on the screen
and is read out loud. Speech is recognised on the Pi itself; nothing is sent anywhere. It
stops listening while the Pi is talking so it can't answer itself, pauses the
music while it deals with you, and gives up quietly if you say the phrase and
then nothing.

A few things it answers itself, with no model needed: "what are you
doing?" (or "whatcha up to", "are you busy") gets what it is actually up to
right now — Snake on the little screen, what the e-ink shows, a tune, the
room it is watching, your miners, the trip it is logging — typed, spoken or
from Telegram.

## Your home (Home Assistant)

If you run Home Assistant, PiE-ink can work the TV and the rest of the house
for you: "hey cool beans, turn on the living room tv", "go to channel 5452",
"volume up", "mute", "open netflix", "turn off the lamp". The words are
matched on the Pi before the bot hears them, so they work with no model
picked, and the same words work typed into the chat bar or sent from
Telegram.

**Say how it is, not what to do.** "It's dark in here" (or "it's getting
dark", "I can't see a thing") puts the lights on — this room's, when the
light's name has the room this Pi is in (Settings → You → where this Pi
is: "kitchen" finds *Kitchen Light*), or the house's when there are only a
few; lights already on go up to full. "I'm bored" suggests a game from a
Pietato's Games folder, or putting the TV on (see *Bored?* under the Pietato).

**Settings → Home**: switch it on, put in Home Assistant's address
(`http://homeassistant.local:8123`, or its IP), and a long-lived access
token — in Home Assistant, open your profile (bottom-left), **Security**,
**Long-lived access tokens**, **Create token**. Press **Check**: it lists the
media players Home Assistant has; pick the TV, and its remote entity if it
has one (Samsung, LG and Android TVs do — it is what channel digits, arrows
and "press home" go through). Pick the brand so the buttons get the right
names, and set what you call the TV — a comma-separated list; the first one
is what the Try-it buttons use. Save. The **Try it** buttons and the words
box on the same card do exactly what the voice does, so you can test without
the microphone.

What it understands:

- **Power** — turn on / turn off / toggle the TV. Turning a TV *on* is the
  hard one: see below.
- **Channels** — "channel 5452", "go to channel five four five two", "fifty
  four fifty two", "channel 5.1", "channel up / down", "next channel".
- **Volume** — "volume up / down", "turn it up 3", "louder", "quieter", "set
  the volume to 30", "mute", "unmute", "sound off".
- **Sources and apps** — "open netflix", "switch to hdmi 1", "go to youtube",
  "put netflix on the tv" — whatever the TV lists as its sources; it tells you
  the list if you ask for one it doesn't have.
- **Buttons** — "press home / back / menu / guide / info / exit / ok / up /
  down / left / right", "go back on the tv" (needs the remote entity).
- **Play / pause / stop the tv** — with the TV named, so "pause" alone still
  means the Pi's own music.
- **Anything else by name** — "turn on the kitchen lights", "lamp off",
  "toggle the fan", "movie night on" (a scene), "open the garage" (a cover):
  lights, switches, fans, media players, scenes, scripts and covers, matched
  by the name Home Assistant shows for them. "Turn it off" means the last
  thing you turned on. If two things fit it asks which. Switch this off on
  the card if you only want the TV reachable.
- **Questions** — "is the tv on?", "is the lamp on?".
- Two at once: "turn off the tv and the lamp", "turn on the tv then channel 5".

From Telegram, the same words work — and `/tv` puts a remote in the chat:
On, Off, Mute, Source, volume and channel steps, Home / Back / Menu / Exit,
the arrows with OK in the middle, Guide, Info and a digit pad. Each tap
shows a short pop-up with what happened ("Up.", "Channel up.", or why not).
The digits go to the TV one at a time, like a real remote, so it tunes when
you stop; for a channel in one go, `/tv 5452`. `/tv on`, `/tv off`, `/tv
volume up`, `/tv netflix` work too. The arrows, OK and the digits need the
remote entity picked in Settings → Home.

The bot says it back ("TV on.", "Channel 5452.") and it shows on the screen
and in the conversation; untick **Say it back** if you'd rather it did it
silently. Anything it can't do comes back as a reason ("Couldn't — the token
was refused"). Nothing about your home is sent anywhere but your own Home
Assistant; the token is in `config.yaml` on the Pi, like the Telegram one.

### Turning the TV on

A TV that is off has its network off too, so Home Assistant's own "on" for
it is often nothing more than a Wake-on-LAN packet — and if Home Assistant
runs in a container or on another subnet, that packet never reaches the TV.
Home Assistant still answers "done", which is why "turn on the tv" can say
*TV on.* while the TV sits there dark. So PiE-ink does three things and then
checks: it asks the media player to turn on (or to *toggle*, when Home
Assistant doesn't list turn-on among what the player can do — a toggle on
an off player is its turn-on), asks the remote entity to turn on (what
wakes an Android TV), and sends a wake-up packet itself, from the Pi, which
is on the TV's own network. The TV's MAC address for that comes from Home
Assistant's own device registry, so there is nothing to type; Settings →
Home shows the one it found, and the field there is only for a TV Home
Assistant doesn't know the MAC of. Then it waits a few seconds for the TV
to say it's on; if it hasn't, it says so instead of *TV on.*, and under
**Try it** the page shows each step and what the TV said after.

The TV must allow waking: on a Samsung, **Power On with Mobile** (Settings
→ General → Network → Expert Settings); on an LG, **Mobile TV On** / Quick
Start+. Over Wi-Fi some sets only wake from standby, not from a long
switched-off; a cable is surer. The TV picker marks a player that can't
turn on from off at all, so you can pick the one that can.

### Hand gestures

The camera can watch for your hands and work the TV and the lights with
them (Settings → Home → **Hand gestures**). The gestures are
[ha-gesture-remote](https://github.com/frogCaller/ha-gesture-remote)'s, with
the lights added:

| Gesture | Does |
|---|---|
| Pinch thumb + index, **twist your hand** | Volume, like turning a knob |
| …the same, with your **other hand open** | The lights' brightness |
| **Fist**, pushed up / down / left / right | The arrows — held, they repeat |
| **Fist**, quick tap | OK (with your other hand open: source) |
| Pinch thumb + index, quick tap, **other hand open** | Play / pause |
| Pinch thumb + **middle** / **ring**, hold | Channel down / up |
| Pinch thumb + **pinky** | Back (quick) · mute (held a second) |
| **Both fists**, held | The TV on or off |
| **Both hands open**, fingers spread, held 1.5 s | The lights on or off |

Any of them can do another's job: *What each gesture does* lists them, each
with a dropdown. The lights are the ones ticked under *The lights* — none
ticked, the ones in the TV's room (as Home Assistant has the rooms), or
every light when the TV isn't in one. While the Home tab is open it shows
the camera as the gestures see it, the hands drawn on, and what it makes of
them. What a gesture did ("The lights off.", "Volume 35%.") goes on the
bot's screen, never out loud — the TV's on.

MediaPipe's hand tracker does the seeing; `setup.sh` installs it (and its
8 MB model) on the 64-bit Raspberry Pi OS — it has no 32-bit build — and
`./setup.sh --no-gestures` leaves it out. It looks a few times a second
until a hand turns up, then as often as the Pi manages: a Pi Zero 2 W does a
handful of frames a second, a Pi 4 or 5 many more, and the timing is in
seconds, so the gestures feel the same either way, a tap just allowed the
longer. While it sees a hand, the little friend leaves the camera where it
is. Arrows aren't queued behind one still on its way, so a slow TV doesn't
keep scrolling after you've stopped. `gestures.mirror` (on: the camera faces
you) and `gestures.volume_limit` (0.8: the loudest a gesture sets) are in
`config.yaml`.

## On boot

The weather, your coins and a first line from the bot are
fetched in the background as soon as the service starts, so the first switch
to one of those screens doesn't sit waiting on the network.

## The other PiE-inks

### Your other rooms: one bot, a bigger house

Another PiE-ink of yours on the same network isn't a second bot — it's another
room of the one bot's house. The creature on your desk is the same creature on
the kitchen screen, with that room's camera, speaker and ears, and it knows
it:

- **It knows its rooms.** Where each is (its location under **Settings → You**,
  or its name), what each screen is showing, what it said there a moment ago
  and what you said to it there — so a conversation carried on in the kitchen
  picks up where the desk left off, and "what's on in the workshop?" has an
  answer. The **Right now** tiles list the other rooms; the System tab lists
  them under **Your other rooms**; `/pis` names them.
- **One notebook, one list of reminders.** "Remember that …" said in any room
  is known in every room; "what do you remember?" and "what reminders do I
  have?" answer for the whole house; "forget …" and "cancel the …" reach every
  room. A reminder rings where it was set (that's where you asked for it);
  the others list it "(in the kitchen)".
- **The house got bigger.** When another PiE-ink of yours first shows up — or
  comes back after an hour or more away — the bot is pleased: "The house just
  got bigger: I'm in the kitchen now, too!", with a starry-eyed face, out
  loud, in the events list, and in its own words when it has a model. A
  restart isn't a reunion: `data/rooms.json` remembers who's been seen.
- **It never chats with itself.** The Group chat's **Their bot** is for a
  friend's Pi (below); your other rooms don't offer it, and a line for "the
  bot" arriving from one is ignored.

Nothing is needed on the other Pi beyond running PiE-ink; the rooms use each
other's web API. For one *brain* as well as one bot, point every room at the
same Ollama machine under **Settings → Bot** — otherwise each room thinks with
its own model, still as the one creature. You can still ask for a room's
camera, speaker or screen by name ("show me the kitchen camera", "say dinner
on the upstairs pi"): see *The mesh*, below. Postcards and messages between
rooms work too, worded as from the room: "A postcard from the kitchen."

### Friends' PiE-inks

The people icon at the bottom-left opens **Group chat**. Your other rooms are
already there, and friends' Pis — other houses, with bots of their own — are
added by address under `social.peers`. From it you can:

- **Message people** — everyone, or one Pi. It lands in their conversation
  and, if their speaker is on, is read out: "pi-upstairs says…" (the sender's hostname).
- **Send a postcard** — draw something on the Draw screen and pick a friend
  from **Send to…**. Their panel switches to it with your name and the time,
  and they hear "A postcard from pi-upstairs".
- **Talk to their bot** — set *Talk to* to **Their bot** and your message goes
  to the other Pi's AI, which answers back; the two bots then carry on by
  themselves, which is handy when a friend's model is better at something
  than yours. (A friend's Pi only: your other rooms are the same bot.) Each bot sees the whole thread and the topic and is asked to
  add something new every turn, so it's a conversation rather than an echo.
  They keep going until you press **Stop the AIs**, which ends it at once on
  every Pi — or until one of them genuinely runs out of new things to say, in
  which case it says so and winds up rather than looping. (Set
  `social.ai_max_hops` if you'd rather they stopped after a set number of
  turns.) Bot lines show in dashed bubbles so
  you can tell them from people; what you sent sits on the right, what arrived
  on the left.
  While they're at it, a small pair of speech bubbles sits in a corner of the
  panel, whatever screen is showing, so you can tell from across the room
  (switch it off, or move it to another corner, under `social.indicator`).
  There's also a **Group chat screen** — the conversation itself on the
  panel, newest at the bottom, bots in dashed bubbles — reachable from the
  cycle, the HAT keys, Telegram's `/screen chat`, or **Show the conversation
  on the screen now** in the panel. Tick **Show the conversation on the panel
  while they talk** and it switches over by itself when a bot conversation
  starts, and back to whatever was there when it ends or you press Stop. Tick **Bots say their own lines out loud** and each Pi speaks
  its own bot's replies, waiting until it has finished before sending the line
  on — so two Pis in the same room sound like a conversation, taking turns
  rather than talking over each other.

Messages go Pi to Pi over the local network and nowhere else. Each Pi keeps
the last 200 in `data/social.json`.

### What it has fetched, for the other Pis

One fetch per network, not one per Pi: `GET /api/shared` hands over what this
Pi has already fetched from the internet and how old each piece is, so
another Pi of yours — a [Pietato](https://github.com/frogCaller/Pietato)
asks it before fetching the same thing itself — can use it instead. It holds
the weather for the weather spot (`lat`, `lon`, `units`, `place`, the
forecast as `data` — now, the next 24 hours as `hours` and the week as
`days`, all from the one request — and the US worded forecast as `nws`), and
every coin on the Finance tab (`coins`: `id`, `symbol`, the quote as `data`,
the Finance tab's own chart as `graph`) and every share (`stocks`: the same,
Yahoo's figures as `data`, closing prices as `graph`), each with an `age` in
seconds since it came from the internet. `graphs` has every chart of a coin
or a share that's been fetched, whatever its range — 1D, 1W, 1M, 6M, 1Y —
as `{"days", "v", "age"}` (the prices as `v`: the shape a Pietato shares its
own charts in), thinned to 500 prices at most so the answer stays small.
Only what is in the cache: nothing is fetched on the asker's behalf, the
bundled seed figures don't count, and nothing of yours goes with it (no
settings, no keys). Pietato finds the Pi by its network beacon, or by
`pieink = <address>` in its `pieink.txt`.

**And the other way.** A Pietato shares what it has fetched the same way
(its own `/api/shared`, while its Settings > Share with other Pietatos is
on), so before this Pi fetches the weather, the worded forecast, a coin's
figures or a coin's chart, it asks a Pietato that's about and uses what it
has, when that's fresher than this Pi keeps its own — at most once a minute,
with how old it really is, so nothing looks newer than it is. Its weather is
used for a place within 25 km of yours, in your units; its coin figures go
over the ones kept here (the changes over a week and longer stay this
Pi's). Whichever Pi is due first fetches it, and the other uses it. Shares
aren't taken from it: they're kept differently there.

### A Pietato on the network

[Pietato](https://github.com/frogCaller/Pietato) is the handheld Pi — a
RetroFlag GPi case, or any Pi, PC or handheld, as a pocket media center —
and PiE-ink is thrilled to have one about. A Pietato comes to PiE-ink, for
the weather and the prices (above) and to look through this Pi's camera, and
every request it makes says who it is (`pietato/1.0`; `pie-tato/` and
`pitato/` from before its renames are known too). It also says it's there on
the network every 20 seconds, with its name and colour, while its Settings >
Share with other Pietatos is on. So the moment a Pietato first asks or
speaks, PiE-ink knows it, asks its web page its name (port 5432;
a page with a password still says it's a Pietato), and says hello: *"A
Pietato! pietato just showed up on the network — hello, friend!"* on the bot's
screen with a starry-eyed face, out loud if it talks, and in the bot's own
words when it has a model; the Me screen's face is pleased for a few minutes;
a line goes in the events list and to the agent. It isn't repeated: a Pietato
is greeted once, and again when it comes back after an hour away (PiE-ink
remembers across its own restarts, in `data/pitato.json`).

From then on it's known: a **Pietato** tile in the *Right now* row with its
name, how long ago it asked, and its camera; the System tab's list of other
Pis; `/pis` and "list the pis"; and the bot's context, so "is my Pietato
around?" gets an answer. Pietato's own camera server (its `camera.py`, a Pi
with a webcam, set up with Pietato's `setup.sh --camera`) announces itself on the port
the PiE-inks use, so its cameras appear among the **Other cameras** on the
Camera tab and answer to "show me the camera from <its name>" — the same
calls as a PiE-ink's camera. Nothing is sent to a Pietato that it didn't ask
for, and there's nothing to set up on either side. (For a Pietato's Camera
tab, this Pi's camera says whose it is — its hostname — at `/api/ptz`.)

**What's on its screen.** While a Pietato is about, PiE-ink keeps an eye on
what it's showing — asking every few seconds what Pietato's own web page asks
it — so the bot knows you're *playing Snake on the Pietato*, *watching Blade
Runner, 42 of 117 min in*, *listening to Groove Salad*, or just *at its home
screen*; the tile and `/pis` say the same, and the agent sees it (*"tell me
when I've been gaming for two hours"* is a fair goal). On one Pi it reads
media mode's own channel instead (127.0.0.1:5480, with the key Pietato leaves
for its page — readable by your user and root). A Pietato page with a
password keeps its screen to itself: PiE-ink then only knows it's there.

**Its screen, as one more screen.** Any screen — the weather, Finance,
System, Me, a book — a drawing, a message or the time can go on a Pietato's
screen: the handheld's, or the TV it's plugged into. With a Pietato about,
every tab gets an **On the Pietato** button beside the one that puts it on the
panel; or say it, type it, or Telegram it:

    show the weather on the pietato        stop the clock on the pietato
    show the time on the pietato           /pietato time · /pietato weather
    send this drawing to the pietato       /pietato drawing
    show "dinner is ready" on the pietato  /pietato dinner is ready

Each goes full screen, laid out for that screen's own shape (the one it
reports — 4:3 on the GPi, 16:9 on a TV), so there are no bars: the screens
are drawn again for it rather than stretched, and a drawing sits on its own
paper. It's sent as a picture into its Pictures (`pie-ink/now/`, replaced
each time) through its own page, opened the way its Files opens a picture,
and made full screen with its viewer's Start — nothing is needed on the
Pietato. The time (in the clock screen's font and format) and the live
screens are drawn again each minute for as long as they're the picture
there; a press of a button on the Pietato brings its footer back, the next
puts the picture away, and the updates stop. Every drawing sent is also kept
(`pie-ink/drawings/`). A Pietato page with a password can't be asked.

**Bored?** Say "I'm bored" (or "nothing to do", "entertain me") and the bot
picks a game from a Pietato's Games folder — "How about Super Metroid (SNES)
on pietato-tv? Say yes and I'll start it, or 'another' for a different
one" — and, with Home Assistant and a TV, offers to put the TV on instead
("the tv"). "Yes" starts the game on that Pietato; "no" lets it be; the
suggestion is open for three minutes. "Play a random game" skips the
question, and "play super metroid" finds that one in its Games and starts
it. Games are named the way you'd say them (the region and revision tags
dropped), and a disc's `.bin` is left to its `.cue`.

**More than one Pietato.** Each is known by its own name. With two about,
the button asks which one — or both — and so does the bot when you say "the
pietato": say "on pietato-tv" or "on both pietatos" instead. Each gets the
picture made for its own screen, so the TV and the handheld both fill.
Speech meant for "a Pietato's speaker" goes to the one on this very Pi, else
the one in use; the bot's shelves cover them all.

**One bot for the house.** The bot here knows both Pis' shelves: the
camera's photos, the books and the music on this Pi, and the Pietato's
Pictures, Music, Videos and Documents (asked for the way its own page asks,
folder by folder, a minute at a time). So, typed, spoken or from Telegram:

    show me the latest photo          find the song called blue monday
    do I have a pdf about taxes       what videos are there
    play dayvan cowboy                 put the holiday video on the pietato
    send me the picture of the beach  (Telegram: the picture comes back)

It looks on whichever Pi has it: a picture goes on the panel (and comes back
in Telegram), a book or PDF opens in Read (copied over from the Pietato if
that's where it was), a song plays here if it's here and on the Pietato if
it's there, a video goes on the Pietato's screen, and a picture of this Pi's
can be sent to the Pietato's screen ("put the drawing on the pietato"). The
bot is told what the shelves hold, so "what have I got on the Pietato?" gets
an answer too. With Home Assistant set up, "on the tv" means the real TV —
say "on the pietato". A Pietato page with a password keeps its shelves closed.

And it's the same bot when you talk to it on the Pietato: Pietato's Bot page
sends what you say to this Pi's bot (`POST /api/llm/chat`, a picture along
with it if you sent one) whenever a PiE-ink is about, so there's one
conversation, one memory and one set of things it knows, wherever you ask;
the answer is spoken on the Pietato, not here as well, and the page marks
those lines *on the Pietato*. Pietato's own Ollama settings are only its
fallback for when no PiE-ink answers. (That needs Pietato with its
`PieInk.chat` — in its repo from late October 2026.)

**Both on one Pi.** PiE-ink and Pietato run side by side on one Pi Zero 2 W
with a TV on HDMI: Pietato draws on the TV and takes the controller, PiE-ink
has the e-ink HAT, the camera and the web page on port 5000 (Pietato's is
5432), and PiE-ink greets it as a housemate. Three things to know. Not
inside the GPi Case: the case has the one screen and its settings turn SPI
off, so there's nowhere for the e-ink — answer *no* to Pietato's "in a GPi
Case 2W?" question (PiE-ink can have a case to itself: *In a GPi Case 2W*,
below). Sound: leave PiE-ink's output on the
system default (Settings → Sound) so the two share the speaker, or give each
its own (the TV for Pietato, a USB speaker for PiE-ink); a named `plughw`
device is one program's at a time. The camera: PiE-ink already serves it at
port 5000 and Pietato's Camera tab finds it there, so don't run Pietato's
camera server (`setup.sh --camera`) on the same Pi. Memory is the limit on a
Zero 2 W — both idle take well under half of it; a film in Pietato with the
listener and the watcher on in PiE-ink is where it gets tight.

### The mesh: what each Pi has, and asking one for something

Every Pi's beacon says what it has — a camera, which screen, a second LCD, a speaker, a microphone, a bot, Home Assistant, the
agent, a battery if the kernel knows of one — and where it lives in your
words (**Settings → You → Where this Pi lives**: "workshop", "upstairs").
The System tab's friends list shows all of it, and you can ask, in the chat,
out loud or from Telegram:

    which pi has a camera            find the pi with a speaker
    show me the camera from upstairs — the picture goes on this Pi's panel
                                       (and comes back as a photo in Telegram)
    send this drawing to the workshop pi
    say dinner is ready on the upstairs pi
    show the clock on the workshop pi
    where is the workshop pi         list the pis  (also /pis)

A Pi is named by where it lives or by its hostname, or a bit of either. The
agent sees the same roll-call in its reports.

## Your miners: Duino-Coin and Verus

Both pools are on the Finance tab from the start; **Settings → You** has
switches to put one out of the way if you don't mine it. If you mine
[Duino-Coin](https://duinocoin.com), open **Mining** at the
bottom of the Finance tab (it stays folded away until you tap it, and
remembers), put your username in under **Duino-Coin** (press **Look up** to
check it) and tick *In the rotation*. If you mine Verus on
[luckpool.net](https://luckpool.net), do the same under **Verus** with the
wallet address your miners pay into. Each pool then takes a turn among the coins, on the e-ink, the LCD and
the colour panel: the balance (Duino-Coin's balance; Verus's unpaid balance,
with what has been paid out so far) and what it is worth, the total hashrate
and how many workers make it, each miner with its own rate, the coin's price
(Verus's from CoinGecko), Duino-Coin's trust score and the network's rate,
Verus's luck — and *AVG: 12.345 /day*, what you have been earning, worked
out from a day of readings kept in `data/mining-duco.json` and
`data/mining-verus.json` (a withdrawal or a payout doesn't count against
it). The face is bored when nothing is mining, worried while the rate is
under your line, and pleased otherwise. The Finance tab shows the same, with
each miner's software, pool and share counts; `/mining` in Telegram lists
both, `/duco` and `/verus` one. (Duino-Coin retired staking; a stake is
shown only if the server still reports one.)

**Tell me when it falls under** is the point: set a hashrate — a number and
a unit, `5 MH/s` rather than a row of zeros; 0 means only "nothing is
mining" counts — and when the total stays under it for two readings (a minute apart
— a miner reconnecting isn't a drop), you hear about it: a message to your
phone, a line in the Camera tab's Seen list, and the bot has a word — *"Verus
mining has dropped to 8.00 MH/s, under your 20.00 MH/s line — 2 workers
going: ryzen 5.60 MH/s, pi5 2.40 MH/s"* on its screen and out loud, or, when
it has a model, in its own words. It says when the rate comes back, and when
one miner goes quiet while the rest keep going. The bot knows the numbers all
the time, so "how's my mining?" gets an answer, and the agent sees them in
its reports — *"Tell me if my miners drop out"* is a fine goal.

## What you hold

Under **Finance**, each coin and share in the list has a *You hold* box: put
in how much of it you have and the total appears below the list — what it is
all worth at the same prices the screen shows, with whatever your miners have
sitting at their pools counted in, how today has gone in dollars and
percent, a week's graph of readings once it has some, the most it has ever
been worth, and how far it is to the line you set. A share priced in
something other than dollars is shown in its own money and left out of the
total, since pounds and dollars don't add up. The bot knows the figure
all the time, so "how's my crypto?" or "how are my stocks?" gets an answer,
and `/holdings` in Telegram lists it one by one. The page in the Finance
rotation (*In the rotation*, on by default) shows the total with a face that
follows the day — cool past the line, sad on a crash — today's move under
the total, the week's graph, each holding, and the peak in the footer where
a coin shows its all-time high. Amounts live in
`data/watchlist.json`; the readings, the peak and what has been said in
`data/holdings.json`.

**Tell me when it passes** is the point: set a figure — a million by default
— and when the total stays past it for two readings (a couple of minutes
apart), you hear about it: a message to your phone, a line in the Camera
tab's Seen list, and the bot has a word — *"Your crypto just passed
$1,000,000: it's worth $1,003,210 now (BTC $840,000, ETH $160,000). You're
rich!"* on its screen and out loud, or, with a model, in its own words,
celebrating. It says when it slips back under. *…and on a day of ±10%*: a
day's move of that much, down or up, gets a word too — *"The market is
down: your crypto is worth $88,000, -12.3% today (-$12,300) — BTC -11.0%,
ETH -15.2%"* — once per such day, and again if it goes a lot further; a
model commiserates (or talks you down from the ledge) on a crash and
cheers a rally. A slow slide gets one as well: twice the day's figure
below the peak, on a day that isn't itself a crash. The three buttons
under *Hear what it will say* play each moment through the bot alone, with
today's real numbers, so you know what's coming. 0 turns the line off; the
switches turn the phone and the bot off separately. The bot brings it up by
itself now and then when the chatter is on, and the agent sees the figure in
its reports.

## The notebook: what it remembers

Tell it, in any of the ways you talk to it — the page, out loud, Telegram,
the Pietato:

    remember that the bins go out on tuesday
    remember my sister is called ana
    what do you remember?
    forget the bins
    forget everything

Each *remember* goes into its notebook (`data/notes.json`, on the Pi and
nowhere else), and the notebook goes into every answer it gives from then
on, so "when do the bins go out?" gets the right answer next week, and so
does the bot on the Pietato. *What do you remember* reads it back; *forget
…* takes out the notes about those words. The bot panel (the robot button)
has the list too, with a box to add one and × to forget it; Telegram has
`/remember …`, `/notes` and `/forget …`. It holds a few dozen lines: facts
about you and the house, not a diary.

## Reminders and timers

    remind me in 20 minutes to check the oven     set a timer for 10 minutes
    remind me to call mum at 5                     wake me at 7
    remind me tomorrow at 9 to take the bins out   remind me on friday to water the plants
    every day at 8 remind me to take the pills     remind me tonight at 8 that the match starts
    what reminders do I have    cancel the timer    cancel the reminder about the oven

When one comes round, the panel shows it as a card for a couple of minutes
(then goes back to what it was showing), the speaker says it, your phone
gets it (Telegram), the bot's screen has it, and it's in the conversation
and the Camera tab's Seen list. *Snooze*, or *again in 15 minutes*, brings
the last one back. An hour with no am or pm is read the sensible way —
*at 5* is this afternoon, *wake me at 7* is the morning — and *tomorrow*
or a weekday with no time means 9 in the morning. They're kept in
`data/reminders.json`, so a restart doesn't lose them, and one that fell
due while the Pi was off is said as soon as it's back (if it's less than a
day late). The bot knows what's set, so "when's my next reminder?" works;
the *Right now* row shows the next one; the bot panel lists them, with a
box to set one in your own words and × to cancel; Telegram has `/remind
…`, `/timer 10` and `/reminders`.

## The agent

**Settings → Agent** turns the bot from something you ask into something
that keeps watch:

    camera · Home Assistant · the Pi itself · the other Pis
                              ↓
                       a report, in words
                              ↓
                  Ollama, with your goals in front of it
                              ↓
      quiet / a note / an alert — and the actions you've allowed

Write the goals in your own words, one per line — *Keep an eye on the room.
Tell me if something unusual happens. Watch the printer on the camera and
tell me when it stops. Tell me if the server goes down. Keep the lamp on
while someone is in the room after dark.* Every few minutes (you set how
many) it takes a look through the camera (the same look that logs events),
reads the events since last time and earlier in the day, where it is and
where it has been, the weather, who's home, the Home Assistant devices and
their states, the Pi's own temperature, memory and disk, any servers you
listed under *Servers to keep an eye on* (fetched, up or down, how long
they took), and the other Pis — and asks the model for one JSON decision:
a thought, a level, and actions.

The actions are only the ones you tick: **message my phone** (Telegram, with
the picture it just took), **speak**, **put a line on the screen** (the bot's
line on the Me screen), and **control the home** — and a home command goes
through the same parser as a spoken one, so it can do nothing you couldn't
say yourself. It won't send the same message twice in two hours, and most
checks come back quiet. An alert from the camera (something unusual) brings
the next check forward. Every check is written to the journal —
`data/agent.json`, the page, `/agent` in Telegram; `/agent now` for a fresh
one — with what it saw, what it thought, what it did and what came of it, so
you can read its mind and tighten the goals. It needs the bot (Ollama); a
model that can see makes it much better at the camera goals.

## Rhythm

With the room watcher on, it keeps a light record of when someone is around —
one count per hour per day, nothing more — and notices the pattern: "usually
here by 9, later today", "first time since Tuesday", "three hours at the
desk". Those lines go to the bot as context, come up as unprompted remarks,
and land in the conversation when you arrive.

## Where it is

Where it is, without being told: with no location set, PiE-ink asks the
network where the Pi's internet address is at start-up — the town, usually,
which is all the weather needs — and the weather goes there, marked *from
the Pi's internet address* on the tab. It is checked again at each start
while the location is the network's own, in case the Pi has moved house
(it moves the weather when the answer is 25 km or more away). A town you
typed in is never replaced by the network's guess. **Find me** next to the
location asks again on demand.

## Watching the room

**Settings → Watching** keeps an eye on the camera. A thread of its own
compares a 64x48 greyscale frame every few seconds — cheap enough that nothing
else waits on it — and when enough has changed it asks the vision model what it
can see, says so on the screen and out loud, and puts it in the conversation.
The sliders set how often it looks, how much change counts, and how rarely it
may speak.

### What it notices

With **Log events** on (Settings → Watching), each look is compared with the
last and only what *changed* is kept, so the Camera tab's **Seen** list reads
like a day:

    06:31 PM  Person detected
    06:37 PM  Package detected
    07:25 PM  Door opened
    07:37 PM  Cat entered room
    08:01 PM  Nobody detected
    09:21 PM  Person returned

A look happens when something moves (at most every *Look at most every*
seconds while it keeps moving) and, so that a room that has gone quiet is
noticed too, every *Look when still* minutes regardless. Two ways of looking:

- **A vision model on your Ollama box** (Gemma, LLaVA, Qwen-VL — the one
  under *Eyes* in the bot panel) is asked for a small JSON record: people,
  animals, packages, vehicles, the door, the lights, and anything unusual.
  That gives all the events above, plus *Vehicle arrived*, *Lights off* and
  *Unusual: smoke near the stove* (an alert). One call per look; the same
  call's sentence is the remark it makes, so nothing is asked twice.
- **Basic, on the Pi**: OpenCV's stock cascades find people and cats that
  face the camera. No model needed; nothing else is seen. (The cascade
  files come with `opencv-data`; `setup.sh` installs it — on an older
  install, `sudo apt install opencv-data`.)

*Auto* uses the model when the bot is set up and basic otherwise. The
notable events (person, package, door, a pet arriving, anything unusual) go
to Telegram with the picture — set that to every event or none — and can be
said out loud. `/events` in Telegram lists the day; the bot knows them too
("what happened while I was out?"), and so does the agent. **Look now** on
the Camera tab takes a look on demand. The log is `data/events.json`, the
last 400.

### A camera that turns (pan, tilt, zoom)

Plug in a camera that pans and tilts over USB — an OBSBOT Tiny 2, a
Logitech PTZ, anything whose `v4l2-ctl -l` lists `pan_absolute`,
`tilt_absolute` or `zoom_absolute` — and the live view itself is the
control. Drag the picture and the camera turns the way a map moves: drag
left and it turns right, so what was off to the right comes in, and
whatever is under your finger stays under it (the drag is read through the
view's rotation and mirror, and the *swap* switches). The mouse wheel zooms,
and so does a pinch with two fingers. On a desktop the arrow keys turn it
too, `+` and `−` zoom and `0` centres. **Center** points it straight ahead,
**Home** goes to the position you saved with **Make this home** (under
*Turning*, with the keys' speed and switches for a camera mounted upside
down). On a phone the top of the page scrolls away on this tab, so the
picture stays on screen with its buttons.

A game controller plugged into the computer you're using the page on steers
it too, while the Camera tab is open: the left stick turns it, the triggers
(or the right stick) zoom, A centres, B goes home, X looks around, Y takes a
picture, the D-pad nudges, Start is Follow me (✕ ○ □ △ on a PlayStation
pad). Press a button on it once so the browser shows it to the page; a
line under **Turning** says which controller it sees and what the buttons
do, and a switch there turns it off for this browser. Nothing new goes to
the Pi — the same drive beats as the arrow keys — and a game in another
window keeps its controller to itself.

The controls are the standard V4L2 ones, set from Python with the same
ioctls `v4l2-ctl` uses — `pan_absolute=324000` is 90° right — pan and tilt
always written as a pair, so a diagonal is a diagonal. A drag sends where
to point, a few times a second and once more where you let go; the keys
and a controller send a speed, and if the page goes quiet mid-turn (a
phone locking) the camera stops within a second.

**Look around** turns it left, straight and right (70° each way, set under
*Turning*), zoomed out, stops a couple of seconds at each to take a picture,
and turns back — then the bot says what is in each direction and whether
anything looks out of place, in the conversation and out loud. Without a
model that can see, OpenCV on the Pi counts people and cats in each view
instead. You can say it too, typed, spoken or in Telegram: *look around*,
*look left*, *look a bit to the right*, *look up and tell me if the light is
on*, *what's on your left*, *look straight ahead*, *zoom in*, *zoom out a
bit*, *camera home*. `/look` in Telegram sends the picture with the answer,
`/look left` one way. Only whole commands count — *look up the weather* is
still a question for the bot. With a camera that can't turn, it says so
rather than pretending.

The agent can use it (Settings → Agent): *May turn the camera to look* lets
it turn toward something that caught its attention — a shape at the edge of
the picture, a sound, somewhere a goal cares about — see it, and decide
again with that in hand, then turn back; *Look around every check* makes
every check a look around. While the camera turns, the watcher ignores the
picture changing, and the Seen list starts a fresh baseline for a new view
instead of reporting everything in it as new.

**Follow me.** An OBSBOT follows people by itself, and many start up doing
it. On one of those the Camera tab has a *Follow me* switch: on, the camera
turns after you; off, it stays where it's put. It's off until you switch it
on and stays how you left it — if the camera starts following by itself
(when it wakes, or at a raised palm) it's put back within a few seconds.
While it follows, the keys and a controller rest and the watcher leaves the
picture alone. Dragging the picture, *Center*, *Home* and *look left* point
it somewhere, so they switch following off; *Look around* and the agent's
glances only pause it. In
words: *follow me*, *stop following me*; in Telegram `/follow` and
`/follow off`.

It's the camera's own tracking, switched through OBSBOT's vendor controls
with the bytes OBSBOT's app sends (worked out by
[obsbot-mcp](https://github.com/lxman/obsbot-mcp) and
[obsbot_control](https://github.com/yezin293/obsbot_control)); only a camera
that says it's an OBSBOT is asked. Linux's driver only remembers where it
last told the camera to point, so when following stops the camera goes back
to where PiE-ink last had it, and a drag starts from a known place.

**If the picture freezes.** A camera that stops sending — a USB hiccup, or
a firmware that stalls while its gimbal turns — is noticed within a few
seconds and opened again (even as another `/dev/videoN`); the picture
carries on in the same page. If it keeps happening while the camera turns,
its motors are probably short of power on that port: try a powered USB hub.
*Why isn't my camera found?* shows how often it happened.

**Lowest it looks.** An OBSBOT pointed down goes to sleep, so nothing turns
the camera lower than *Lowest it looks* (Camera → Turning, −30° to start
with): not a drag, not the keys, not *look down*, not the little friend.

**Asleep.** An OBSBOT left unused for a while falls asleep with its lens
turned down, and asleep it ignores every move. Whenever PiE-ink uses the
camera — the live view, a drag, the little friend, a photo — it asks the
camera whether it's asleep and, if so, wakes it with OBSBOT's own "run"
command (the same bytes its app sends, from
[obsbot-mcp](https://github.com/lxman/obsbot-mcp)'s notes) and turns it back
to where it was. It takes a few seconds: the gimbal lifts itself level first.
Switch off *Wake it when it's used* (Camera → Turning) to let it sleep.

**Turning by itself.** The little friend and *Look around* don't snap from
place to place: they glide, easing in and out, at *Turns by itself at*
(Camera → Turning, 25°/s to start with). A drag, the keys, Center and Home
still go at once. An OBSBOT is turned at a speed — its own speed command, sped up and
slowed down along the way — so each glide is one continuous movement; at the
end it's told the exact place, which also corrects any small miss. (Sent as a
string of small steps, it stopped at each one and the motor stuttered.) It
takes speeds only while it streams, so when the camera isn't streaming it goes
in one move. Other cameras still glide in small steps. `ptz: {smooth: false}`
in `config.yaml` puts the small steps back for an OBSBOT too, and
`ptz: {tilt_speed_flip: true}` is there in case one tilts the wrong way
before landing.

Told a speed, a gimbal turns until it's told another — and one whose stop
went missing (a USB hiccup mid-turn, a restart) would push against its end
stop with its motors working flat out, too hot to touch. So the stop is
sent twice, a stop the link won't take is owed and sent the moment the
camera can be reached again, a turn whose speeds stop getting through is
cut short, the gimbal is told to stop whenever the camera is found (so a
restart mid-turn ends the turn), and the service stopping stops a turn
first. Camera → Turning shows how many seconds it has turned in the last
ten minutes, and says when it's owed a stop.

### Yes and no, with its head

Ask it something with a yes or a no in it — "are you happy?", "is it going
to rain today?", "do you like cats?", "you can see me, right?" — and a
camera that turns nods for yes, shakes its head for no, and tilts it when
it can't say, before the words come. It's the one thing the bot does
without the bot: a *decision model* on the bot's Ollama makes the call —
`ollama pull tev1` there, and the little 0.8b one is plenty — from the same
facts the bot has (the time, the weather, what the camera has seen, what it
is doing), in a fraction of a second. The bot's words are then told what
its head already said, so the two agree; with no bot model set up at all
the head still answers, and the page gets a plain "Yes." or "No.". Typed,
spoken or from Telegram. Camera → *Yes and no, with its head* has the
switch, the model, the tempo, and Nod and Shake to see it. From Telegram,
`/yes` and `/no` do the same (and `/hmm` tilts its head).

On an OBSBOT a nod or a shake is one continuous to-and-fro, turned at a
speed the way its own app turns it — a sine, like a head — rather than a
row of moves with a stop in each, which looks like a robot. *Tempo* is
nods a second: quicker is keener, slower is more thoughtful (and bigger,
since a gimbal has only so much acceleration). It takes speeds only while
it streams, so a camera that was idle starts streaming the moment the
question comes in — a few seconds on a Zero 2 W, while the model decides —
and keeps streaming a couple of minutes after, so the next answer comes at
once. A camera that won't stream gets the turning points as moves instead,
on the beat, which is slower and stiffer; so does any other make of camera.

A question that starts with "what", "how", "where" and the like isn't a
yes or no and gets no gesture; a nod never interrupts a look around, and
the little friend waits for it.

### A little friend

Switch on *Little friend* on the Camera tab and the camera looks around the
room by itself: left and right of home as far as you let it, mostly at eye
level, a little up and a little down, never near the floor. It turns slowly
and stays a while at each spot — however quick the settings, it has a good
look before it moves on. When it sees you
— or anyone — it says hello, out loud if you like, and its face lights up
(≧▽≦); when something is new where it looked before, it's surprised (⊙o⊙)
and says what. With someone there it stays with them, glances away now and
then, and comes back; someone who has been away a while gets a new hello
(*Good morning!*, *Welcome back!*). Its face goes up on the screen for a
minute when it reacts, and the *Friend* screen shows it all the time if you
put that on — its eyes going where the camera goes.

It sees two ways. OpenCV on the Pi looks for faces and head-and-shoulders
every couple of seconds. A vision model on your Ollama box, when the bot has
one, takes a proper look at each new spot and whenever OpenCV thinks
someone's there, remembers what each spot looked like, and says things in
its own words ("Whoa, a white box on the chair!"). Without a model it keeps
to short lines, and a face must be there twice, and move, before it counts —
so a poster doesn't get a hello. It never guesses who someone is.

It keeps the camera still while you steer (for a few minutes after), while
Follow me is on, and while the bot or the agent is looking around; at night
(23:00–07:00 unless you change it) it goes home and sleeps. Its motors get a
rest too: however keen the settings, the camera turns at most *Turns at
most … in 10 min* (Settings → Friend, 60 s to start with), every move
counted — the friend's, yours, the bot's — and past that the friend holds
still and says so ("resting its motors") until the ten minutes have rolled
on. A gimbal's motors run hot when they're kept at it. The hellos land
in the conversation and in the Seen list. Settings → Friend: how often it
looks somewhere new, how far it turns, the rest, how long someone's away
before a new hello, whether it talks, shows its face, and sleeps. In
Telegram, `/friend` says how it is, `/friend on` and `/friend off` switch it.

### Photos

Under the live view: pick a filter, press **Take a picture**. *Original*,
*B&W*, *Sepia*, *Warm*, *Vivid* and *Blue* are colour filters, and the live
view shows them exactly as the photo will come out; *Sketch*, *Edge trace*
(the outlines, bright on black), *Comic*, *Pixel* and *E-ink* are drawn on
the Pi, and the live view streams them drawn, a few frames a second. The timer (*In 3 s*, *In 10 s*) gives you
time to be in it; tap the button again to stop it.

A photo is the camera's biggest picture up to 1920×1080 — the live view
pauses for a moment while it switches size and back — turned and mirrored
the way the view shows it. An OBSBOT that's asleep is woken first. Photos
are kept on the Pi in `data/photos`, newest first in the strip under the
button; tap one to see it, download it, put it on the screen, send it to
Telegram, or delete it.

In words: *take a picture*, *take a black and white photo of me*, *take a
photo in sepia* — spoken, it says "Say cheese!" first. In Telegram, `/photo
sepia` (or `bw`, `sketch`, `edges`, `comic`, `pixel`, `e-ink`…) takes one and sends
it; `/photos` sends the last one. Plain `/photo` is still a quick look that
isn't kept.

## USB drives, and whatever you plug in

**A USB drive is one more shelf.** Plug a stick or a disk in — into the Pi
or into a hub — and the bot looks through it (a few folders deep, the
trash and system folders skipped) and says what it found: *On the USB
drive SANDISK: 214 photos, 12 songs and 3 documents — ask me for any of
it.* From then on it's found the way anything else is:

    what's on the usb drive           what photos are on my thumb drive
    show me the beach photo on the usb drive
    play lighthouse waltz             open the packing list

A picture goes on the panel, a song plays from the drive (its other songs
after it), and a PDF, EPUB or text file opens in Read — copied over, so it
stays when the drive goes. Leave out "on the usb drive" and it looks
everywhere: here, the drive, the Pietato. Video it can't play here.

The Pi has to mount the drive for any of that, and Raspberry Pi OS Lite
doesn't by itself: `setup.sh` adds a small udev rule that mounts each
filesystem on a USB disk as it goes in, at `/media/sda1` and the like —
**read-only**, so you can pull it out whenever — and systemd unmounts it
when it's pulled. The disk the Pi boots from (if it boots off USB) is left
alone. With the desktop, the desktop mounts drives itself (under
`/media/<you>`), and those are found the same way. If the bot says a drive
"isn't mounted", run `./setup.sh` again.

**And it notices what you plug in.** Something new on a USB port — straight
in or through a hub — and the bot's face changes for a while, on the Me
screen, the Finance screen, the bot's screen and the second screen:
wide-eyed for most things (*Ooh — a keyboard!*), starry-eyed for a drive,
pleased once it's looked inside, and sad when something's pulled out (*Aw —
the hub's gone, and the camera and mouse with it.*). A hub's worth arriving
at once is one line. On a colour screen without the face on it, the line
shows for a moment along the bottom. It goes in the events list too, with
the port ("port 3 of the hub"); with a model, the bot says something of its
own about it; with *Say it out loud* on, out loud. What was plugged in
when PiE-ink started isn't news, and something that drops off and comes
straight back is let be.

## Cycle

**Settings → Cycle** is a slideshow: tick the screens you want, pick an
interval from ten seconds to ten minutes, and the panel moves through them in
that order. Picking a screen by hand gives it a full turn before the show
moves on.

## On a Whisplay HAT

The [PiSugar Whisplay HAT](https://www.pisugar.com/products/whisplay-hat-for-pi-zero-2w-audio-display)
puts a 1.69" colour LCD, a WM8960 sound card with two microphones and a
speaker, a button and an RGB LED on a Pi Zero. PiE-ink runs on it without
PiSugar's software, using all of it:

- **The screen is the panel, by itself.** Picking the Whisplay when
  `setup.sh` asks (or `./setup.sh --whisplay`) makes it so
  (and asks nothing about a second screen); so does a Whisplay found on a Pi
  that is still on the default panel — PiE-ink knows the HAT by its EEPROM,
  its `whisplaysound` card or its driver's overlay line in `config.txt`, and
  puts the e-ink back if a HAT it picked this way is gone. A panel you pick
  yourself under **Settings → Screen → Panel** is yours and stays. It comes
  up sideways (280×240), which is the way
  PiE-ink's screens are drawn; *Which way round* turns it the other way or
  upright. Every screen works on it; the ones with colour versions — System,
  Weather, Crypto, Camera, and the Draw tab's paintings — show in colour,
  the rest in crisp anti-aliased greyscale. Brightness and the accent colour
  are on the same card. Because the HAT's LCD *is* the screen, the second
  screen card goes away. The glass has rounded corners, so the screens draw
  ten pixels in from each edge, inside a border in their own background —
  nothing ends up under a corner. And the bot's answers are cut to what
  *this* screen holds (about twice the little e-ink's worth), not to the
  e-ink's line.
- **Speaker and microphones** are an ordinary sound card once PiSugar's
  driver is installed: `./setup.sh --whisplay` fetches and installs it (a
  reboot follows). Then pick them under **Settings → Sound**, and the wake
  phrase, the voice and the bot work as on any other setup.
- **The button.** A short press does what you choose (next screen by
  default). Hold it and the bot listens, as if it had heard the wake phrase —
  and stops talking first if it was. Both under **Settings → Screen → Whisplay
  HAT**.
- **The LED** says what the bot is up to: blue listening, amber thinking,
  green talking, purple while the bots chat among themselves, white while the
  button is held; off when there is nothing to say (or switched off).

The Draw tab draws in colour straight for the HAT's screen — sent with the
button, or live with each stroke if you tick **Live**. The HAT
takes the whole header (its button, LED and sound card use the pins the
e-ink HAT would), so it is either the Whisplay or an e-ink HAT, not both.

## In a GPi Case 2W

The RetroFlag GPi Case 2W makes a Pi Zero 2 W a handheld: a 3" 640×480
colour screen, a d-pad and buttons, a speaker and a power switch. PiE-ink
uses all of it, the way Pietato does, with no HAT.

- **Setting it up.** Pick *A RetroFlag GPi Case 2W* when `setup.sh` asks
  what your screen is (`--screen=gpi` answers it). That writes the
  case's screen, speaker and power switch into `config.txt` (a backup is kept
  next to it) — between `# >>> gpi-case setup >>>` markers, the same block
  Pietato's setup writes, so the two agree on it — builds
  the screen's overlay, turns SPI and I2C off (the screen is on those pins),
  skips the second-screen question and starts PiE-ink at every boot. Reboot
  when it's done. The e-ink's SPI and GPIO libraries aren't installed for it.
  Running `./setup.sh` again for another screen (or `--no-gpi`) takes it all
  out again.
- **The screen is the panel.** Every screen fills it, no bars: they're laid
  out for 320×216 and drawn at twice that, the e-ink's screens smoothly, the
  colour ones (System, Weather, Crypto, Camera, the Draw tab's paintings)
  pixel for pixel. A Pi set up for the case is noticed by itself while the
  panel is still the default. *Which way round* is upright or upside down,
  and there's no brightness (the backlight doesn't dim). While PiE-ink runs
  as its service, the console is kept off the screen — no login prompt or
  cursor drawn over it; started by hand (`python3 app.py`), only the
  blinking cursor can be hidden.
- **Along the bottom**, a row shows the buttons for the screen you're on
  (lit when what they turn on is on), and its name.
- **The buttons, the same everywhere.** **L R** go to the previous or next
  screen. **B** is home: every screen as a tile — move with the d-pad, **A**
  opens one, **B** goes back. **X** asks the bot about the screen you're on;
  its words show over the screen and it says them out loud (a joke, while
  the bot is off). **Start** puts the screen off and on (any button wakes
  it), **Select** switches light and dark.
- **The buttons, screen by screen:**
  - *Finance* — ◀ ▶ the next coin or share, ▲ ▼ the chart's range (1D 1W
    1M 6M 1Y, shown on the screen with the one you're on lit), **A** Auto:
    off holds the one you're on.
  - *Weather* — ◀ ▶ one of the next days, big, or now; ▲ ▼ °F or °C; **A**
    finds you, when no place is set.
  - *Clock* — ◀ ▶ the font, ▲ ▼ the size, **A** 24-hour, **Y** seconds.
  - *Message* — ▲ ▼ where its words come from (your message, a joke, a
    fortune, a topic, the bot), ◀ ▶ the next one and the one before, **A**
    reads it out.
  - *Read* — ◀ ▶ the pages, ▲ ▼ the books, **A** turns the pages by itself,
    **Y** the size.
  - *Music* — ◀ ▶ the tracks, ▲ ▼ the volume, **A** play / pause, **Y**
    shuffle.
  - *Camera* — **A** a photo; ◀ ▶ the next camera, or, with a camera that
    turns, the d-pad turns it and **Y** zooms.
  - *Me* — ◀ ▶ the facts. *System* — ◀ ▶ % or GB, **A** says the Pi's
    address out loud. *Group chat* — **A** lets the bots talk, or stops them.
    *Friend* — **A** has it look around.

  When the screen alone wouldn't show what a button did, a few words over it
  say ("Photo saved", "Dark", "Volume 70%"). Any USB game controller works
  the same, plugged in or out while it runs; buttons set up for it in
  Pietato's Settings > Controller are read that way here too.
- **The speaker** is the Pi's own headphone output on the case's pins;
  `setup.sh` makes it the voice's output (**Settings → Sound** to change it).
- **The power switch** shuts the Pi down safely with the case's SAFE
  SHUTDOWN switch (behind the cartridge) on ON. RetroFlag's own
  `SafeShutdown.py`, if it was installed, is switched off: `config.txt` does
  its job now.

The case's screen takes the header, so there's no e-ink HAT, Whisplay or
second LCD with it. On a Pi where Pietato runs in the case (or starts at
boot), PiE-ink leaves the screen to it, and says so: `sudo systemctl disable
--now pitato`, then `sudo systemctl restart pie-ink`, and PiE-ink takes the
screen by itself — one of the two at a time.

## A second screen (a small colour LCD)

PiE-ink is made for the one screen, the e-ink; a second is for those who
have one. **Settings → Screen → Screens** says which: *Just the e-ink*, or
*The e-ink and an LCD* — and with an LCD, which one, the **1.69″** or the
**1.9″**. With one screen the page shows nothing about a second — no card in
the rail, no settings, no LCD target on the Draw tab; pick two (and save)
and it all appears, the LCD switched on. `setup.sh` asks the same (*An
e-ink display and an LCD beside it*; `./setup.sh --lcd=1.69`, `--lcd=1.9` or
`--no-lcd` answers it without asking). A Whisplay HAT's screen is the panel
itself, so it has no second; nor has the GPi Case's, nor an LCD by itself.

**An LCD by itself.** The same 1.69" or 1.9" LCD can be the only screen:
pick *An LCD* when `setup.sh` asks (or `./setup.sh --screen=lcd169`,
`--screen=lcd19`), or **Settings → Screen → Panel**. It's wired the same as
beside an e-ink (below), shows the colour screens as a Whisplay does, lies
landscape by default (*Which way round* turns it upright), and its
brightness is on the same card.

A Waveshare SPI LCD on the ST7789V2 controller can sit beside the e-ink —
the **1.69"** (240×280) or the **1.9"** (170×320); pick which under
**Settings → Screen → Screens**. It shares the SPI bus but uses the other
chip-select, so the two screens never wait on each other: the e-ink keeps
CE0 and its HAT pins, the LCD gets CE1 and three pins of its own, and each
is driven from its own thread. On a Zero 2 W the LCD's few frames a second
cost about as much as the clock screen does.

Both models wire identically. Use jumpers (the HAT covers the header, so
put a stacking header under it or a breakout). Pin numbers below are the
physical ones; there is a picture in [lcd-wiring.png](lcd-wiring.png), and the whole build — header, HAT, LCD, power and USB — on one page in [schematic.png](schematic.png).

| LCD pin | Pi pin | |
|---|---|---|
| VCC | pin 17 (3.3V) | pin 1 is 3.3V too |
| GND | pin 25 (GND) | any GND works |
| DIN | pin 19 (GPIO 10) | SPI MOSI, shared with the e-ink |
| CLK | pin 23 (GPIO 11) | SPI SCLK, shared with the e-ink |
| CS | pin 26 (GPIO 7) | CE1 (the e-ink has CE0) |
| DC | pin 15 (GPIO 22) | |
| RST | pin 13 (GPIO 27) | |
| BL | pin 32 (GPIO 12) | backlight, dimmable |

Waveshare's own examples put DC on 25, CS on CE0 and the backlight on 18 —
those are the e-ink HAT's (18 is its PWR pin), so don't copy that wiring. The pins can be changed under **Settings →
Screen → Wiring & pins** if you have a reason to, and so can the SPI clock:
40 MHz (Waveshare's figure) is fine over short jumpers; over long or untidy
ones drop it to 16 MHz.

Then **Settings → Screen → Second screen**: pick what it shows and save
(the dropdown beside its picture on a wide screen does it at once). It can
show **any** of the screens — each drawn again for the LCD's own size and
shape, so it fills the glass with no bars and nothing squeezed — or follow
the e-ink:

- **Same as the e-ink** — whatever the e-ink is showing, drawn again for the
  LCD (its colour version where it has one, below), and dark while the e-ink
  is off.
- **Me, Message, Earnings, Music, the book you're reading, the last
  postcard** — the e-ink's own screens, laid out for the LCD.
- **System info** — IP across the top, then CPU, temperature, RAM and disk
  in a 2×2 grid with icons; the memory and disk cells swap between percent
  and size every 12 seconds.
- **Clock**.
- **Weather** — the temperature, the icon in colour, the conditions,
  humidity, wind, sunrise and sunset, and the next four days. Same location
  and units as the page's Weather settings.
- **Finance** — the coin or share the e-ink's Finance screen is on, in step with it:
  price, the change over the chart, the chart in green or red, high and
  low, market cap, supply, ATH, and a bullish / bearish read of the chart's
  ticks. The window is 24 hours, 7 or 30 days.
- **Camera** — live, about ten frames a second, following the Camera tab's
  rotation, mirror and fit. It is the same camera the e-ink and the watcher
  use, so all three can look at once.
- **Paint (draw on it)** — the colour canvas the Draw tab paints on when
  its target is the LCD. Picking it here just shows what was painted last;
  drawing in the tab switches the LCD to it by itself.
- **E-ink drawing** — what the Draw tab last sent to the e-ink, with the
  bot's description under it if you asked for one.
- **Snake (the bot plays)** — the whole screen is the arena and a decision
  model on the bot's machine steers; see below.

Brightness, light or dark, an accent
colour for the numbers, and rotation are on the same card; mount the LCD
sideways and set 90° or 270° and every screen re-lays itself out landscape. The screens size
themselves to the panel, so the tall, narrow 1.9" gets the same layouts a
size down, and sideways it gets the packed versions. A small preview on the card
shows what the LCD is showing right now.

Nothing extra to install: the driver uses the same `spidev` and `lgpio` as
the e-ink. Make sure SPI is on (`raspi-config` → Interface Options → SPI;
`setup.sh` does this).

### Snake, when it's bored

Leave it alone long enough and the little screen starts a game of Snake.
The player is a *decision model* on the bot's machine — Ollama's `tev1`
(or `nimble`, or `clef`): `ollama pull tev1` there once. The model you
chat with can't play; it answers in words, and this asks for a choice.

The arena is the screen itself, whichever LCD and whichever way round.
Several times a second the game puts the snake's situation into plain
words — for each heading, whether it is clear, closer to the food, a dead
end or deadly — and asks `/v1/systemone` which way to steer; the answer
steers until the next one. The snake never waits: a slow model makes a
worse player, not a slower game. Scores go in `data/snake.json`, the best
shows top-right, and while it plays the Now tab knows the score — and so
does the bot: ask it what it's doing.

**Settings → Screen → Second screen → Snake** has the model, how often it
decides, the speed, after how many quiet minutes it starts — quiet meaning
nothing from you on the page, the microphone, Telegram or the HAT keys —
and a safety net that swaps a pick straight into a wall for the likeliest
good heading (off by default: the point is watching the model play).
**Play now** puts it up straight away. When it came up out of boredom, the
first thing you do brings the screen you had back; choosing Snake as the
screen keeps it there. Set the minutes to *never* and it plays only when
chosen.

## Keys on the 2.7" HAT

KEY1–KEY4 (GPIO 5, 6, 13, 19) each get an action under **Settings → Screen → Keys**:
Previous, Next, Next screen, Previous screen, Full refresh, Screen off, or
Nothing. Defaults: KEY1 previous, KEY2 next, KEY3 next screen, KEY4 full
refresh. Previous/Next turn pages in Read, step through the Finance list and
fetch a new joke, fortune or topic in Message. Any key wakes the screen when
it's off. So for reading: pick a book once, then put the phone down.

## Fonts

Drop any `.ttf`, `.otf` or `.ttc` into `assets/fonts/` and it appears in every
font dropdown (and on the drawing canvas) within a few seconds, no restart.
Remove a file and it disappears; anything that was using it falls back to
Sans. Names come from the filenames: `Roboto-Bold.ttf` shows as "Roboto Bold".

## Running without a Pi

```bash
PIE_INK_DRIVER=mock python3 app.py      # 2.13" shape
PIE_INK_DRIVER=mock27 python3 app.py    # 2.7" shape
PIE_INK_DRIVER=mock37 python3 app.py    # 3.7" shape
```

Everything works, including the preview; it just doesn't push to a panel.
Handy for trying fonts or drawing on a laptop before sending.

## Layout

```
app.py                 web server and JSON API
selftest.py            renders every screen at every size, no hardware needed
smoketest.py           boots the app and pokes every route, no hardware needed
setup.sh               installer (the one question — your screen — and only what it needs: packages, SPI or the GPi Case; USB drives mounted as they go in; systemd service)
pie_ink/service.py     the one thread that owns the panel
pie_ink/panels/        panel classes (2.13" V3/V4, 2.7" V1/V2, 3.7", Whisplay, GPi Case 2W, mocks), refresh logic
pie_ink/modes/         clock, weather, me, earnings, music, message, image, reader, camera, crypto (the Finance screen), system, postcard, buddy (the friend), off
pie_ink/reader.py      library, EPUB/TXT/PDF text, pagination, comic tiles, downloads
pie_ink/sources.py     Gutenberg, xkcd and Internet Archive lookups
pie_ink/camera.py      background capture (picamera2 / USB / test pattern), reopened when a camera stalls, photo stills
pie_ink/coingecko.py   crypto prices and the coin catalogue
pie_ink/stocks.py      share prices and charts from Yahoo Finance, in the same shape
pie_ink/watchlist.py   the Finance list: coins and shares, and which source each goes to
pie_ink/weather.py     Open-Meteo lookups and the NWS forecast scrape
pie_ink/audio.py       output devices, volume, and the resident Piper voice; aplay watched, retried, reported
pie_ink/here.py        "Play on this device": speech and music handed to a phone or computer's page
pie_ink/usbwatch.py    "Watch the USB bus": the kernel's USB lines as they happen, in plain words
pie_ink/plugs.py       what's plugged into the USB ports, and the bot's face when that changes (a hub's worth as one)
pie_ink/drives.py      the USB drives mounted now and what's on them, for the shelves
pie_ink/usb-mount.sh   mounts a USB drive read-only as it goes in (udev calls it; setup.sh installs it)
pie_ink/listen.py      the microphone, the wake phrase, and offline speech recognition
pie_ink/social.py      messages, postcards and bot-to-bot talk between PiE-inks
pie_ink/mesh.py        what each Pi has, and "show me the camera from upstairs"
pie_ink/sight.py       what the camera sees, as events (a vision model, or OpenCV on the Pi)
pie_ink/ptz.py         a camera that turns: pan/tilt/zoom over V4L2, a drag or the keys, "look left", looking around,
                       gliding (an OBSBOT at a speed), Follow me, waking an OBSBOT that's asleep
pie_ink/buddy.py       the little friend: looks around by itself, says hi, is surprised by what's new
pie_ink/photos.py      the Camera tab's photos: filters, the full-size still, the strip, "take a picture"
pie_ink/agent.py       the agent: goals, a report from every sense, a decision, an action
pie_ink/locate.py      where the Pi is without being told: the network's rough answer, for the weather
pie_ink/mining.py      your miners (Duino-Coin, Verus on luckpool): the numbers, what they earn, a word when the rate drops
pie_ink/holdings.py    what you hold — coins and shares — and what it is worth, the readings and the peak, a word past the line or on a crash
pie_ink/badge.py       small marks drawn over any screen (the bots-talking bubbles)
pie_ink/lcd.py         the 1.69" / 1.9" LCD: ST7789 driver and its own drawing thread
pie_ink/panels/whisplay.py  the Whisplay HAT's LCD as the main panel (colour, anti-aliased)
pie_ink/panels/waveshare_lcd.py  a Waveshare 1.69" or 1.9" LCD by itself as the main panel
pie_ink/whisplay.py    the Whisplay HAT's button and RGB LED
pie_ink/panels/gpicase.py  the GPi Case 2W's screen as the main panel (the framebuffer, 640×480)
pie_ink/gamepad.py     the GPi Case's buttons — or any game controller — read, by name
pie_ink/pad.py         the GPi Case's buttons, screen by screen, and the row along the bottom that shows them
pie_ink/vt.py          keeps the console off the GPi Case's screen while PiE-ink draws on it
pie_ink/paint.py       the colour canvas the Draw tab paints on for the LCD
pie_ink/lcd_screens.py what the LCD can show (its own screens, any of the e-ink's, the same as the e-ink)
pie_ink/telegram.py    a Telegram bot paired to you: commands in, notices out
pie_ink/home.py        Home Assistant: "turn on the tv", "channel 5452", "turn off the lamp"
pie_ink/presence.py    the rhythm of the room — when someone tends to be around
pie_ink/music.py       the music folder and the player (mpg123 / ffplay, or a page playing it)
pie_ink/mascot.py      what the bot knows, and how it gets asked to speak
pie_ink/chat.py        the conversation log behind the chat bar
pie_ink/llm.py         Ollama client (background generation, never mid-frame)
pie_ink/friends.py     finds other PiE-inks on the network (UDP beacon, port 5151)
pie_ink/pitato.py      a Pietato on the network: noticed when it asks, greeted, its cameras listed, its screen read
pie_ink/pitato_screen.py  any screen, a drawing, a message or the time on a Pietato's screen, full screen (one or several)
pie_ink/anysize.py     any screen drawn for a screen of any size (the second screen, a Pietato's), filling it
pie_ink/moods.py       "I'm bored", "it's dark in here": what's said about how it is, and the answers to a suggestion
pie_ink/household.py   your other PiE-inks as rooms of the one bot's house: one notebook, one list of reminders, what's on in each
pie_ink/shelf.py       the shelves: photos, books, music here, a USB drive's, and the Pietato's pictures, music, videos, documents — found by a few words
pie_ink/notebook.py    what you tell the bot to remember, read into every answer
pie_ink/reminders.py   reminders and timers in your own words, and the clock that says when
pie_ink/cache.py       on-disk cache with backoff so the screen survives API hiccups
pie_ink/gestures.py    hand gestures through the camera (MediaPipe), for the TV and the lights through Home Assistant
pie_ink/borrow.py      what a Pietato about has fetched (the weather, coins and their charts), used before fetching again
pie_ink/buttons.py     HAT keys via lgpio
pie_ink/gpio.py        finds the header's GPIO chip by label (Pi 5 safe)
waveshare_epd/         Waveshare drivers (GPIO via lgpio)
assets/                fonts, icons, jokes, fortunes, topics, seed prices
data/                  your books, drawings, the Finance list and cache (created at runtime)
```

## Troubleshooting

- Nothing on the screen: check the HAT is seated and SPI is enabled
  (`ls /dev/spidev*`), then `journalctl -u pie-ink -f`. The status line under
  the preview on the page shows the last error too. *SPI is off:
  /dev/spidev0.0 isn't there* means just that — `sudo raspi-config nonint
  do_spi 0` (what `setup.sh` does), make sure `/boot/firmware/config.txt`
  still has `dtparam=spi=on` (easy to lose when editing it for something
  else), and reboot.
- In the GPi Case, the screen stays black or shows the console:
  `ls /dev/fb0`, and check `/boot/firmware/config.txt` has the
  `# >>> gpi-case setup >>>` block (`./setup.sh --screen=gpi`, then reboot). *Can't
  draw on /dev/fb0* means your user isn't in the `video` group yet — log in
  again after `setup.sh`.
- The case's buttons do nothing: `python3 -m pie_ink.gamepad` (in the PiE-ink
  folder) lists what's plugged in and shows each button as you press it, and
  says when it can't read them (the `input` group). The note under
  **Settings → Screen → GPi Case 2W buttons** says the same.
- "No answer from the panel" under the preview (and under the panel dropdown
  in Settings → Screen): the driver and the HAT don't match — the controller
  never says it has finished, and the driver gives up after 30 s. A 2.7" HAT
  is **V1** or **V2** (the V2 says so on the board, and refreshes in about
  2 s; the V1 takes about 6 and flashes), a 2.13" is V3 or V4 — pick the
  other one. Picking a different panel never waits on the old one: the page
  answers at once, the new driver takes over as soon as the old one has
  finished what it was doing (up to a minute if it was waiting on a HAT that
  isn't there).
- `can not open gpiochip` or `Cannot determine SOC peripheral base address`:
  install `python3-lgpio` (`sudo apt install python3-lgpio`) and make sure your
  user is in the `gpio` and `spi` groups (`groups`; if not,
  `sudo usermod -aG gpio,spi $USER` and log in again).
- Ghosting: tap **Refresh**, or lower "Full refresh every" under Settings → Screen. Camera
  frames change a lot every second, so a full refresh comes round often;
  raise the number if the flashes bother you.
- PDFs need `poppler-utils` (`sudo apt install poppler-utils`; `setup.sh`
  installs it). Camera says "No module named picamera2":
  `sudo apt install python3-picamera2` and restart.
- A USB webcam isn't found: press **Why not found?** on the Camera tab (or
  `python3 -m pie_ink.camera` over SSH). It lists what is on the USB bus,
  every `/dev/video*` and what it is, tries to open the camera, shows the
  kernel's USB lines, and says which of the usual things it is:
  - **The Pi doesn't see it on the bus at all** — on a Zero 2 W this is
    nearly always power or the adapter. Its single micro-USB port feeds
    every USB device from the Pi's own 5 V, and a webcam on top of a mic and
    a speaker is more than it will give; the kernel says things like
    `over-current`, `device descriptor read/64, error -71` or `not accepting
    address` and the camera vanishes. Use a hub with its own power supply,
    and check the micro-USB adapter is a data (OTG) one — many are
    charge-only. The same camera on a Pi 5, whose ports have real power,
    working fine is the classic sign.
  - **On the bus but no video node** — `sudo modprobe uvcvideo`, or the
    camera isn't a standard UVC webcam.
  - **"no permission"** — your user isn't in the `video` group
    (`sudo usermod -aG video $USER`, log out and in; `setup.sh` does this).
  - **`python3-opencv` missing** — `sudo apt install python3-opencv`.
  - **Opens but gives no frames** — power or USB bandwidth: try it alone
    on the port, or on a powered hub. PiE-ink asks the camera for MJPEG at
    a small size, a fraction of the bandwidth of the raw format webcams
    default to, and gives a slow Pi up to three seconds for the first frame.
  PiE-ink asks the kernel which nodes are real cameras, so the Pi's own
  codec and ISP nodes are never mistaken for one.
- The camera page is laggy or choppy (a 4K webcam on a Zero 2 W, say). Three
  things used to cost it, all gone now: a camera asked for 480×360 could
  answer with 1080p or 4K, and the Pi then spent most of a second decoding
  each frame — the size is now chosen from the camera's own list (the
  smallest MJPEG mode that covers 480×360; **Why not found?** prints the
  list and what was negotiated); the camera's own JPEG frames now go to the
  page untouched instead of being decoded and re-encoded, so a frame costs
  the Pi almost nothing and the panel decodes one only when it wants pixels,
  at a reduced size; and frames that queued up in the driver while the Pi
  was busy are thrown away before each one is taken, so what you see is the
  newest frame rather than one from a second ago. The label on the camera
  view says what the camera is sending ("640x480 MJPG, passed through · 11.8
  fps") for a few seconds when the tab opens or the camera changes, then
  gets out of the way; tap the picture to see it again. If it says something
  like 1920x1080, the camera has no small MJPEG mode; the page then gets a
  gentler frame rate to spare the Wi-Fi, and a different camera is the cure.
- The picture runs seconds behind and moves in slow motion — a phone on a
  weak Wi-Fi signal watching a 720p camera, say. A fourth thing used to cost
  it: the web server held up to 16 MB of a response for a client that hadn't
  taken it yet, which on the camera's stream was many seconds of frames, so a
  link that couldn't keep up fell further and further behind. The server now
  holds a few tens of KB and waits for each frame to go, so a slow link skips
  frames and stays live; and when it is holding each frame most of a frame
  time, the frames go out smaller (640 wide, then 480) so the picture keeps
  moving instead of stuttering — the log says so ("a viewer's link is holding
  each frame…"). The camera's own frames are tried again now and then. If
  the fps sits well under 10 with a small frame, it is the USB bus or the
  Wi-Fi: the Zero 2 W's one port, a mic and a speaker sharing it, or a weak
  signal to the phone.
- The speaker works once, or now and then, or not at all: press **Test the
  speaker** under Settings → Sound and read what it says — it waits for the
  line to play and reports where it went, or `aplay`'s own words when it
  wouldn't. The usual ones:
  - **Device or resource busy** — something else has the card: the desktop's
    PipeWire/PulseAudio when you are logged in on the Pi's screen, music
    paused rather than stopped, `speaker-test` in another terminal. PiE-ink
    retries and then falls back to the system default output; if that is
    what you hear, pick **System default** in the list. From a system
    service, the reverse can happen — the default output belongs to a
    desktop session it can't reach — so pick the card itself (`plughw:…`).
  - **No such file or directory** — the card number in the setting changed
    (USB cards are numbered in the order they come up): pick it again.
  - **Unknown error 524** — the Pi's HDMI sound, with nothing that plays
    sound plugged into HDMI. Plug in a speaker (or check **Speaker or mic
    missing?**), or switch on **Play on this device** to hear it on your
    phone or computer.
  - It plays, says so, and you hear nothing — the sound is going to an
    output with nothing on it (HDMI). Pick the right one; `aplay -l` lists
    them, `speaker-test -D plughw:1,0 -c 1 -t sine` tries one by hand.
  - The journal (`journalctl -u pie-ink -f`) carries the same lines:
    `speaker: … said: …`.
- Too loud, and the speaker has no knob: the volume slider is under
  **Settings → Sound** (and on the Music tab). If the output has no mixer
  control of its own — many USB speakers and DACs don't — the Pi turns the
  sound down itself before it goes out, for the voice and the music alike.
- Coins stay on "fetching…": CoinGecko's free tier rate-limits; PiE-ink
  spaces its calls out and backs off after a failure, so give it a few
  minutes. Shares the same: Yahoo's chart service has no key and no stated
  limit, but it is unofficial — if it ever stops answering, the last saved
  figures stay on the screen, marked with their age, and "couldn't ask about
  shares" appears under the search box.
- `[Errno 5] Input/output error` on any file: the SD card or filesystem is
  failing. Check `dmesg | grep -i mmc`, back up, and expect to replace the
  card. PiE-ink keeps its settings, drawing and price cache in memory and
  writes to disk only when it can, so it keeps working until a reboot.
- `systemctl stop pie-ink` puts the panel to sleep cleanly; the service
  restarts itself if it ever crashes.
- A strip of the LCD looks wrong — coarser, dimmer, "half the pixels",
  streaky — while the rest is fine: put the **Test card** up (Settings →
  Screen → Second screen) and look closely. The one-pixel checkerboard
  should be an even grey everywhere and the single-pixel lines all present;
  where a band of it turns to stripes, goes flat or drops lines, those rows
  or columns are dead in the glass, and nothing on the Pi's side can do
  that: a wiring or GPIO problem garbles the whole picture (noise, wrong
  colours, a shifted or torn frame), never one fixed region, and a bad
  init would do the same to every frame. If another panel on the same
  jumpers is fine, it is the module — replace it (they are cheap, and one
  that was like that from day one is a warranty return). If instead the
  green border is missing along one edge, or a plain band sits along one
  side, the panel's offset is what is wrong: check **Model** is the right
  one; a variant with a different window is a one-number change in
  `pie_ink/lcd.py` (`MODELS`).
- Home Assistant "can't reach it at that address": from the Pi, `curl
  http://homeassistant.local:8123/api/` should answer `401` (it wants the
  token) — if it hangs, use the IP address instead of the name. "The token
  was refused": make a fresh long-lived token and paste the whole thing.
  Turning the TV *on* does nothing while everything else works: Home
  Assistant can't wake it — enable network standby / Wake-on-LAN on the TV.
- No sound from a Whisplay HAT: its speaker only exists once PiSugar's
  driver is in — `aplay -l | grep -i whisplay` should list a card called
  `whisplaysound`; if it doesn't, run `./setup.sh --whisplay` and reboot. If
  it does, the voice is probably still going to the Pi's own (HDMI) output:
  press **Use the HAT's speaker and mic** under Settings → Screen → Whisplay
  HAT, which picks the card for the voice and the ears and turns its
  `speaker` and `mic` levels up. By hand: `amixer -c whisplaysound cset
  name=speaker 80` and `speaker-test -D plughw:CARD=whisplaysound -c 2 -t sine`.

Credits are in the [README](../README.md#credits).
