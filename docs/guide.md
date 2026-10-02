# PiE-ink — the long version

Everything the [README](../README.md) only mentions: each screen, each
setting, each button, and what to do when something doesn't work. Start the
same way — install, open the page, pick a tab.

## Using it

Pick a tab, adjust, and press the big button. The preview updates as you
change things; the panel only updates when you send. Light or dark for the
page is under Settings → Screen. Under the panel a row of tiles says what is
going on right now — the bot's last line, the weather, what your crypto is
worth, what's playing, the watcher, the GPS, the miners, the TV — swipe it
sideways on a phone. On a wide screen the page is a dashboard: a rail down
the left with the tabs, the Pi's numbers (under its hostname) and the second screen's picture if
you have one; the panel with, under it, a dropdown that puts any screen on
it; the tiles beside them; the tab's card underneath (its button in
the title row); and wider still, the conversation with the bot in a column
of its own on the right (hide it with the arrow in its corner; the
speech-bubble button beside the settings cog brings it back).

- **Clock** — the time, big, in any font; optionally seconds and the date.
- **Weather** — today's conditions in the same shape as the crypto screen:
  the temperature big, a face that reacts to the sky (sunglasses in the sun,
  sweating when it's hot, an umbrella face in the rain, asleep at night),
  feels-like, humidity, sunrise and sunset in the header, today's range, a
  four-day forecast with icons, and wind and rain chance in the footer. The
  location finds itself: a USB GPS sets it exactly, and without one the
  Pi's internet address puts it in the right town at start-up (**Find me**
  on the tab does it on demand); type a town only if you want somewhere
  else. Data from Open-Meteo (free, no key); for US locations the
  National Weather Service's worded forecast is scraped with BeautifulSoup.
- **GPS** — where the Pi is, from a USB receiver: the coordinates big, a
  bar chart of the satellites in view, altitude, speed and heading, the town,
  and the GPS's own UTC time in the footer. Reachable through Cycle, a HAT key,
  Telegram (`/screen gps`) and the button on the Weather tab.
- **Map** — where the Pi is, on OpenStreetMap: a line map traced from the
  tiles (crisp on e-ink; or a shaded, dithered one), a pin in the middle, a
  scale bar, the place across the top and the coordinates below. Centred on
  the GPS fix, its last fix, or the weather location, whichever it has.
  The other PiE-inks on the network that know where they are show as
  lettered dots. See the Map tab, below.
- **Me** — a rotating handful of facts about your day: how far into the work
  block you are, how long you've been awake, the
  temperature, your coin, the Pi's uptime, books on the shelf, how much of the
  year has gone, days until your birthday. A face reads the weather, your
  coins and the time and comments on them, and the bottom bar shows how far
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
- **Finance** — what your salary comes to and how much of today you have
  earned, ticking up through your paid blocks: what one pay packet is worth, a
  bar across the working day, the weekly, monthly and daily equivalents, and
  the year so far. It needs one number — your yearly income, in Settings →
  You — plus your daily schedule, set in the same place. The figure stays on the Pi, in
  `config.yaml`.
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
  small UDP broadcast, nothing leaves the LAN), and **Friends' cameras** shows
  all their feeds at once; System lists them with links. Pi camera via
  `picamera2`, or a USB webcam; a test pattern without either. The view on the
  page is framed exactly like the panel. Fill or whole, mirror, dither, auto
  contrast, and a rotate button for the screen.
  With more than one camera plugged in, the Camera tab lists them all — pick
  one, or press **Next** to flip through; key 4 on the 2.7" HAT does the same
  while the camera screen is up, and the panel shows a small label for a few
  seconds after a switch so you know which one you're looking at. The choice
  is remembered.
- **Crypto** — the Cryptogotchi: prices from CoinGecko with a face that reacts
  to market sentiment, a graph, ranges, percentage changes and headlines,
  and — with **Duino-Coin** or **Verus** on — your own miners as coins,
  rotating through the coins you add. Put in how much of each coin you hold
  and what it all adds up to takes a turn too, with the bot keeping an eye on
  it (a word when it passes the line you set, or a day goes badly). If
  CoinGecko can't be reached it shows the last saved numbers (marked "3h
  old"), and on a fresh install it shows bundled figures marked "old data"
  until the first fetch.
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

## Sound

Plug a USB speaker in and pick it under **Settings → Sound** (the
list comes from `aplay -l`). The voice is
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

## Your home (Home Assistant)

If you run Home Assistant, PiE-ink can work the TV and the rest of the house
for you: "hey cool beans, turn on the living room tv", "go to channel 5452",
"volume up", "mute", "open netflix", "turn off the lamp". The words are
matched on the Pi before the bot hears them, so they work with no model
picked, and the same words work typed into the chat bar or sent from
Telegram.

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

- **Power** — turn on / turn off / toggle the TV. (Turning a TV *on* only works
  if Home Assistant can wake it — most can over the network once it has been
  on once; if yours doesn't, that's a Home Assistant setting, not a PiE-ink one.)
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

## On boot

The weather, your coins and a first line from the bot are
fetched in the background as soon as the service starts, so the first switch
to one of those screens doesn't sit waiting on the network.

## The other PiE-inks

The people icon at the bottom-left opens **Group chat**. Every PiE-ink on the
network is already there, and Pis elsewhere can be added by address under
`social.peers`. From it you can:

- **Message people** — everyone, or one Pi. It lands in their conversation
  and, if their speaker is on, is read out: "coolbeans says…".
- **Send a postcard** — draw something on the Draw screen and pick a friend
  from **Send to…**. Their panel switches to it with your name and the time,
  and they hear "A postcard from coolbeans".
- **Talk to their bot** — set *Talk to* to **Their bot** and your message goes
  to the other Pi's AI, which answers back; the two bots then carry on by
  themselves, which is handy when a friend's model is better at something
  than yours. Each bot sees the whole thread and the topic and is asked to
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

### The mesh: what each Pi has, and asking one for something

Every Pi's beacon says what it has — a camera, a GPS with a fix, which
screen, a second LCD, a speaker, a microphone, a bot, Home Assistant, the
agent, a battery if the kernel knows of one — and where it lives in your
words (**Settings → You → Where this Pi lives**: "workshop", "upstairs").
The System tab's friends list shows all of it, and you can ask, in the chat,
out loud or from Telegram:

    which pi has a camera            find the pi with a gps
    show me the camera from upstairs — the picture goes on this Pi's panel
                                       (and comes back as a photo in Telegram)
    send this drawing to the workshop pi
    say dinner is ready on the upstairs pi
    show the clock on the workshop pi
    where is the workshop pi         list the pis  (also /pis)

A Pi is named by where it lives or by its hostname, or a bit of either. The
agent sees the same roll-call in its reports.

## Your miners: Duino-Coin and Verus

If you mine [Duino-Coin](https://duinocoin.com), open **Mining** at the
bottom of the Crypto tab (it stays folded away until you tap it, and
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
under your line, and pleased otherwise. The Crypto tab shows the same, with
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

## Your crypto: what you hold

Under **Crypto**, each coin in the list has a *You hold* box: put in how much
of it you have and the total appears below the list — what it is all worth
at the same CoinGecko prices the screen shows, with whatever your miners have
sitting at their pools counted in, how today has gone in dollars and
percent, a week's graph of readings once it has some, the most it has ever
been worth, and how far it is to the line you set. The bot knows the figure
all the time, so "how's my crypto?" gets an answer, and `/holdings` in
Telegram lists it coin by coin. The page in the crypto rotation (*In the
rotation*, on by default) shows the total with a face that follows the day
— cool past the line, sad on a crash — today's move under the total, the
week's graph, each coin, and the peak in the footer where a coin shows its
all-time high. Amounts live in
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

## The agent

**Settings → Agent** turns the bot from something you ask into something
that keeps watch:

    camera · GPS · Home Assistant · the Pi itself · the other Pis
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

## Where it is (a USB GPS)

Plug a USB GPS receiver into the Pi — a u-blox 7 dongle is the usual one; any
receiver that talks NMEA over a serial port works — and that is all: it is
used the moment it appears, at boot or plugged in later (**Use it: Auto**
on the Weather tab, the default; **On** insists and says so when
there isn't one; **Off** leaves it alone). Until a receiver has been seen,
Auto keeps quiet — nothing about a GPS on the tab, in the bot's head or on
the Now list. `setup.sh` has already done the plumbing: the `dialout`
group for the serial port, a udev rule that names a u-blox `/dev/gps0` and
keeps ModemManager from poking it, and `python3-serial`. Nothing else is
needed; gpsd is not required (if it is installed and holding the port,
PiE-ink reads through it instead).

Where it is, without being told: with no location set, PiE-ink asks the
network where the Pi's internet address is at start-up — the town, usually,
which is all the weather needs — and the weather goes there, marked *from
the Pi's internet address* on the tab. It is checked again at each start
while the location is the network's own, in case the Pi has moved house
(it moves the weather when the answer is 25 km or more away). A GPS fix
replaces it, exactly; a town you typed in is never replaced by the network's
guess. **Find me** next to the location does the same on demand: the GPS's
fix when it has one, the network otherwise.

The receiver is read on a thread of its own, found again when it is
unplugged and plugged back in, and its position goes three places: the
**Weather** (with *Move the weather to wherever it is* ticked, the location
follows the fix — the town name comes from OpenStreetMap, once per spot,
and the forecast is fetched for it straight away), the **bot** (ask it where
you are, how fast you're going, how high up), and the **GPS screen** on the
e-ink and the LCD. The tab shows what the receiver is doing: no receiver,
searching (with how many satellites it can see), or the fix.

A cold receiver needs a view of the sky and a minute or two to find
satellites; on a window sill it usually settles in well under that. Indoors,
away from windows, it may never get a fix — that is the receiver, not the Pi.

If the tab says *No receiver found* with the dongle plugged in, press **Why
not working?** next to it (or `python3 -m pie_ink.gps` over SSH). It lists
what is on the USB bus and which serial ports exist, whether your user may
open them, whether gpsd or ModemManager is in the way, then pauses the reader
for a few seconds and listens to each port itself at 9600, 4800, 38400 and
115200 baud, and says which of the usual things it is — see Troubleshooting, below.
The reader itself finds the speed on its own now (a serial-chip dongle that
isn't a u-blox often talks 4800 or 38400), reads through gpsd when gpsd has
the port (two readers on one port each get fragments), keeps going through
an odd sentence, and shows how many sentences it has read.

### Journey mode — where it has been

With the receiver on, the day's track is kept: a point every time the Pi has
moved twenty metres (`journey.min_metres`), one file a day under
`data/journey`, a month kept. The Map tab draws it as a trail with a ring
where it set off — pick another day from the list, **Forget this day** to
drop one — and says the day in a sentence: *set off at 8:12, 12.4 miles in
all, 48 min on the move, 3 stops (longest 42 min at 10:15), top speed 54
mph, furthest 6.1 miles from the start, back where it started by 5:40 PM*.
The e-ink and LCD map screens draw the trail too (*Today's trail on it*,
under the Map tab). Ask the bot where you've been; `/journey` in Telegram
sends the map and the sentence, `/journey 2026-09-20` another day's.

### The Map tab

The **Map** tab is a proper map (Leaflet, bundled — nothing loaded from
elsewhere — over OpenStreetMap's tiles, which your phone fetches itself): a
teal dot for the Pi where the GPS says it is (grey when it is only the
weather location or the last fix), purple dots for the other PiE-inks on your
network that have a position, and the numbers under it — position, place,
what the fix is, altitude, speed, heading. It follows the Pi as it moves;
drag the map and it stops, the crosshair button puts it back. **Show map**
puts the Map screen on the panel.

For the panel the Pi fetches the tiles itself — a handful for the view,
with a proper User-Agent as OpenStreetMap asks — and keeps them for a week
under `data/cache/tiles`, so a place it has shown stays on the screen
without the network. **Zoom** (8, a region … 18, a street) and **Style**
(lines or shaded) are on the tab; **Fetch the tiles now** gets the current
view ahead of time. Each PiE-ink tells the others where it is in its
network beacon (position, place name — never anything else), which is how
the friends' dots work; it is the same LAN-only broadcast the friends list
already uses, nothing leaves your network. Telegram's `/map` sends the map
as a picture with a link to the spot on openstreetmap.org.

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
`tilt_absolute` or `zoom_absolute` — and the Camera tab grows a joystick
and a zoom slider under the live view. Push the stick and the camera turns
that way, faster the further you push (gentler when zoomed in); let go and
it stops where it is. On a desktop the arrow keys work too, `+` and `−`
zoom and `0` centres. **Center** points it straight ahead, **Home** goes to
the position you saved with **Make this home** (under *Turning*, with the
stick's speed and switches for a camera mounted upside down). On a phone
the top of the page scrolls away on this tab, so the picture and the stick
stay on screen together.

The controls are the standard V4L2 ones, set from Python with the same
ioctls `v4l2-ctl` uses — `pan_absolute=324000` is 90° right — pan and tilt
always written as a pair, so a diagonal is a diagonal. If the page goes
quiet mid-drag (a phone locking), the camera stops within a second.

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
While it follows, the stick rests and the watcher leaves the picture alone.
*Center*, *Home* and *look left* point it somewhere, so they switch
following off; *Look around* and the agent's glances only pause it. In
words: *follow me*, *stop following me*; in Telegram `/follow` and
`/follow off`.

It's the camera's own tracking, switched through OBSBOT's vendor controls
with the bytes OBSBOT's app sends (worked out by
[obsbot-mcp](https://github.com/lxman/obsbot-mcp) and
[obsbot_control](https://github.com/yezin293/obsbot_control)); only a camera
that says it's an OBSBOT is asked. Linux's driver only remembers where it
last told the camera to point, so when following stops the camera goes back
to where PiE-ink last had it, and the stick starts from a known place.

**If the picture freezes.** A camera that stops sending — a USB hiccup, or
a firmware that stalls while its gimbal turns — is noticed within a few
seconds and opened again (even as another `/dev/videoN`); the picture
carries on in the same page. If it keeps happening while the camera turns,
its motors are probably short of power on that port: try a powered USB hub.
*Why isn't my camera found?* shows how often it happened.

**Lowest it looks.** An OBSBOT pointed down goes to sleep, so nothing turns
the camera lower than *Lowest it looks* (Camera → Turning, −30° to start
with): not the stick, not *look down*, not the little friend.

**Asleep.** An OBSBOT left unused for a while falls asleep with its lens
turned down, and asleep it ignores every move. Whenever PiE-ink uses the
camera — the live view, the stick, the little friend, a photo — it asks the
camera whether it's asleep and, if so, wakes it with OBSBOT's own "run"
command (the same bytes its app sends, from
[obsbot-mcp](https://github.com/lxman/obsbot-mcp)'s notes) and turns it back
to where it was. It takes a few seconds: the gimbal lifts itself level first.
Switch off *Wake it when it's used* (Camera → Turning) to let it sleep.

**Turning by itself.** The little friend and *Look around* don't snap from
place to place: they glide, easing in and out, at *Turns by itself at*
(Camera → Turning, 25°/s to start with). The stick, Center and Home still go
at once. An OBSBOT is turned at a speed — its own speed command, sped up and
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

- **The screen is the panel.** Pick *PiSugar Whisplay HAT* under **Settings →
  Screen → Panel**. It comes up sideways (280×240), which is the way
  PiE-ink's screens are drawn; *Which way round* turns it the other way or
  upright. Every screen works on it; the ones with colour versions — System,
  Weather, Crypto, GPS, Camera, and the Draw tab's paintings — show in colour,
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

## A second screen (a small colour LCD)

A Waveshare SPI LCD on the ST7789V2 controller can sit beside the e-ink —
the **1.69"** (240×280) or the **1.9"** (170×320); pick which under
**Settings → Screen → Model**. It shares the SPI bus but uses the other
chip-select, so the two screens never wait on each other: the e-ink keeps
CE0 and its HAT pins, the LCD gets CE1 and three pins of its own, and each
is driven from its own thread. On a Zero 2 W the LCD's few frames a second
cost about as much as the clock screen does.

Both models wire identically. Use jumpers (the HAT covers the header, so
put a stacking header under it or a breakout). Pin numbers below are the
physical ones; there is a picture in [lcd-wiring.png](lcd-wiring.png).

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

Then **Settings → Screen → Second screen**: switch it on, pick what it shows
and save. The screens:

- **System info** — IP across the top, then CPU, temperature, RAM and disk
  in a 2×2 grid with icons; the memory and disk cells swap between percent
  and size every 12 seconds.
- **Clock**.
- **Weather** — the temperature, the icon in colour, the conditions,
  humidity, wind, sunrise and sunset, and the next four days. Same location
  and units as the page's Weather settings.
- **Map (where it is)** — the map in colour, with the pin, the friends and
  the scale bar; the same tiles and settings as the e-ink's Map screen.
- **GPS** — the position, the satellites as coloured bars, altitude, speed,
  heading and the town, with the fix state in green, amber or red.
- **Crypto** — the coin the e-ink's Crypto screen is on, in step with it:
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

Brightness, light or dark, an accent
colour for the numbers, and rotation are on the same card; mount the LCD
sideways and set 90° or 270° and every screen re-lays itself out landscape
— the mirror screen in particular fills it then. The screens size
themselves to the panel, so the tall, narrow 1.9" gets the same layouts a
size down, and sideways it gets the packed versions. A small preview on the card
shows what the LCD is showing right now.

Nothing extra to install: the driver uses the same `spidev` and `lgpio` as
the e-ink. Make sure SPI is on (`raspi-config` → Interface Options → SPI;
`setup.sh` does this).

## Keys on the 2.7" HAT

KEY1–KEY4 (GPIO 5, 6, 13, 19) each get an action under **Settings → Screen → Keys**:
Previous, Next, Next screen, Previous screen, Full refresh, Screen off, or
Nothing. Defaults: KEY1 previous, KEY2 next, KEY3 next screen, KEY4 full
refresh. Previous/Next turn pages in Read, step through coins in Crypto and
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
setup.sh               installer (packages, SPI, systemd service)
pie_ink/service.py     the one thread that owns the panel
pie_ink/panels/        panel classes (2.13" V3/V4, 2.7" V1/V2, 3.7", mocks), refresh logic
pie_ink/modes/         clock, weather, me, finance, music, message, image, reader, camera, crypto, system, postcard, buddy (the friend), off
pie_ink/reader.py      library, EPUB/TXT/PDF text, pagination, comic tiles, downloads
pie_ink/sources.py     Gutenberg, xkcd and Internet Archive lookups
pie_ink/camera.py      background capture (picamera2 / USB / test pattern), reopened when a camera stalls, photo stills
pie_ink/coingecko.py   crypto prices and the coin catalogue
pie_ink/weather.py     Open-Meteo lookups and the NWS forecast scrape
pie_ink/audio.py       output devices, volume, and the resident Piper voice; aplay watched, retried, reported
pie_ink/here.py        "Play on this device": speech and music handed to a phone or computer's page
pie_ink/usbwatch.py    "Watch the USB bus": the kernel's USB lines as they happen, in plain words
pie_ink/listen.py      the microphone, the wake phrase, and offline speech recognition
pie_ink/social.py      messages, postcards and bot-to-bot talk between PiE-inks
pie_ink/mesh.py        what each Pi has, and "show me the camera from upstairs"
pie_ink/sight.py       what the camera sees, as events (a vision model, or OpenCV on the Pi)
pie_ink/ptz.py         a camera that turns: pan/tilt/zoom over V4L2, the joystick, "look left", looking around,
                       gliding (an OBSBOT at a speed), Follow me, waking an OBSBOT that's asleep
pie_ink/buddy.py       the little friend: looks around by itself, says hi, is surprised by what's new
pie_ink/photos.py      the Camera tab's photos: filters, the full-size still, the strip, "take a picture"
pie_ink/journey.py     the day's GPS track: points, stops, distance, a sentence
pie_ink/agent.py       the agent: goals, a report from every sense, a decision, an action
pie_ink/locate.py      where the Pi is without being told: the network's rough answer, for the weather (the GPS says exactly)
pie_ink/mining.py      your miners (Duino-Coin, Verus on luckpool): the numbers, what they earn, a word when the rate drops
pie_ink/holdings.py    your crypto: what you hold and what it is worth, the readings and the peak, a word past the line or on a crash
pie_ink/badge.py       small marks drawn over any screen (the bots-talking bubbles)
pie_ink/lcd.py         the 1.69" / 1.9" LCD: ST7789 driver and its own drawing thread
pie_ink/panels/whisplay.py  the Whisplay HAT's LCD as the main panel (colour, anti-aliased)
pie_ink/whisplay.py    the Whisplay HAT's button and RGB LED
pie_ink/paint.py       the colour canvas the Draw tab paints on for the LCD
pie_ink/gps.py         the USB GPS receiver: NMEA off the port (or gpsd), the place name, the "why not working" probe
pie_ink/maps.py        where the Pi is, and OpenStreetMap tiles for the panels (cached a week)
pie_ink/lcd_screens.py what the LCD can show (system info, clock, bot, chat, mirror)
pie_ink/telegram.py    a Telegram bot paired to you: commands in, notices out
pie_ink/home.py        Home Assistant: "turn on the tv", "channel 5452", "turn off the lamp"
pie_ink/presence.py    the rhythm of the room — when someone tends to be around
pie_ink/music.py       the music folder and the player (mpg123 / ffplay, or a page playing it)
pie_ink/mascot.py      what the bot knows, and how it gets asked to speak
pie_ink/chat.py        the conversation log behind the chat bar
pie_ink/llm.py         Ollama client (background generation, never mid-frame)
pie_ink/friends.py     finds other PiE-inks on the network (UDP beacon, port 5151)
pie_ink/cache.py       on-disk cache with backoff so the screen survives API hiccups
pie_ink/buttons.py     HAT keys via lgpio
pie_ink/gpio.py        finds the header's GPIO chip by label (Pi 5 safe)
waveshare_epd/         Waveshare drivers (GPIO via lgpio)
assets/                fonts, icons, jokes, fortunes, topics, seed prices
data/                  your books, drawings, coin list and cache (created at runtime)
```

## Troubleshooting

- Nothing on the screen: check the HAT is seated and SPI is enabled
  (`ls /dev/spidev*`), then `journalctl -u pie-ink -f`. The status line under
  the preview on the page shows the last error too.
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
- The USB GPS "isn't working": press **Why not working?** next to it on the
  Weather tab (or `python3 -m pie_ink.gps` over SSH) and read the verdict:
  - **Nothing on the USB bus looks like a receiver** — on a Zero 2 W, the
    single micro-USB port again: a data (OTG) adapter, not a charge cable,
    and a powered hub if a mic or speaker shares it. `lsusb` should show
    `U-Blox AG` (ID 1546:01a7).
  - **On the bus but no serial port** — the driver didn't bind:
    `sudo modprobe cdc_acm` (u-blox) or the serial chip's module, replug.
  - **Permission denied** — your user isn't in `dialout`:
    `sudo usermod -aG dialout $USER`, then `sudo systemctl restart pie-ink`
    (a group added while the service was running doesn't reach it until it
    restarts; a shell you were already logged into doesn't get it either).
  - **The port is held by another program** — gpsd (fine: PiE-ink reads
    through it; set the device to *gpsd* if it doesn't pick that up) or
    ModemManager, which probes every new serial port as a modem and can hold
    a u-blox for half a minute after it is plugged in. `setup.sh` installs
    the udev rule that tells it to leave u-blox alone; for another brand,
    `sudo systemctl disable --now ModemManager`.
  - **Bytes but nothing readable at any speed** — two programs reading the
    same port (each gets fragments), or it isn't a GPS: `cat /dev/ttyACM0`
    shows what it is saying.
  - **Opens but stays silent** — power, or the dongle has gone quiet:
    replug it.
  - **It keeps dropping off the USB bus** — it comes back each time (the
    reader looks for it every second after a drop), but has to find
    satellites again. The report says how often, whether the camera had just
    turned each time (its motors draw bursts of current), and whether other
    devices on the same hub dropped off too. That's the hub or its power: a
    hub with its own power adapter usually ends it. The Weather tab's GPS line
    says how many times this hour.
  - **The receiver works, N sentences** — the dongle is fine; it just needs
    sky. Indoors, away from a window, some never get a fix.
- Too loud, and the speaker has no knob: the volume slider is under
  **Settings → Sound** (and on the Music tab). If the output has no mixer
  control of its own — many USB speakers and DACs don't — the Pi turns the
  sound down itself before it goes out, for the voice and the music alike.
- Coins stay on "fetching…": CoinGecko's free tier rate-limits; PiE-ink
  spaces its calls out and backs off after a failure, so give it a few
  minutes.
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
