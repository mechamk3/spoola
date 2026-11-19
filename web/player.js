const button = document.querySelector("#play");
const title = document.querySelector("#title");
const artist = document.querySelector("#artist");
const album = document.querySelector("#album");
const cover = document.querySelector("#cover");
const placeholder = document.querySelector("#placeholder");
const upnext = document.querySelector("#upnext");
const upnextName = document.querySelector("#upnext-name");
const bar = document.querySelector("#bar");
const elapsed = document.querySelector("#elapsed");
const total = document.querySelector("#total");
const message = document.querySelector("#message");
const players = [
  document.querySelector("#audio-a"),
  document.querySelector("#audio-b"),
];

let active = players[0];
let warm = players[1];
let listening = false;
let trackId = null;
let requestId = 0;
let latest = null;
let catchUp = false;
let owedPosition = null;
const WARM_LEAD = 30;

function fmt(seconds) {
  const whole = Math.max(0, Math.floor(seconds || 0));
  const minutes = Math.floor(whole / 60);
  return minutes + ":" + String(whole % 60).padStart(2, "0");
}

function paint(position, duration) {
  const ratio = duration ? Math.min(Math.max(position, 0) / duration, 1) : 0;
  bar.style.width = (ratio * 100) + "%";
  elapsed.textContent = fmt(position);
  total.textContent = fmt(duration);
}

function showCover(track) {
  if (!track.artwork) {
    cover.hidden = true;
    cover.removeAttribute("src");
    placeholder.hidden = false;
    return;
  }
  const next = track.artwork;
  if (cover.getAttribute("src") !== next) cover.src = next;
  cover.alt = (track.album || track.title) + " cover";
  cover.hidden = false;
  placeholder.hidden = true;
}

cover.addEventListener("error", () => {
  cover.hidden = true;
  placeholder.hidden = false;
});

function showTrack(track) {
  title.textContent = track.title;
  artist.textContent = track.artist || "";
  album.textContent = track.album || "";
  document.title = track.artist ? track.title + " — " + track.artist : track.title;
  showCover(track);
}

function showNext(upcoming) {
  if (!upcoming) {
    upnext.hidden = true;
    upnextName.textContent = "";
    return;
  }
  upnextName.textContent = upcoming.artist
    ? upcoming.title + " — " + upcoming.artist
    : upcoming.title;
  upnext.hidden = false;
}

function showIdle() {
  title.textContent = "";
  artist.textContent = "";
  album.textContent = "";
  document.title = "Spoola";
  showCover({ artwork: null, title: "", album: "" });
  showNext(null);
  paint(0, 0);
  latest = null;
  trackId = null;
  publishSession(null, false);
}

function render(now) {
  const track = now.track;
  if (!track) {
    title.textContent = "Nothing queued";
    artist.textContent = "";
    album.textContent = "";
    document.title = "Spoola";
    showCover({ artwork: null, title: "", album: "" });
    showNext(null);
    paint(0, 0);
    message.textContent = "Scan a music folder, then refresh this page.";
    clearPlayer(active);
    trackId = null;
    publishSession(null, false);
    return;
  }
  showTrack(track);
  showNext(now.next);
  if (!listening) paint(now.position, track.duration);
  else total.textContent = fmt(track.duration);
  if (!message.textContent.startsWith("Press play")) message.textContent = "";
  publishSession(track, listening);
  if (!listening) publishPosition(now.position, track.duration);
}

function waitForMetadata(element) {
  if (element.readyState >= 1) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const done = () => { cleanup(); resolve(); };
    const fail = () => { cleanup(); reject(new Error("unplayable")); };
    const cleanup = () => {
      element.removeEventListener("loadedmetadata", done);
      element.removeEventListener("error", fail);
      clearTimeout(timer);
    };
    const timer = setTimeout(done, 4000);
    element.addEventListener("loadedmetadata", done);
    element.addEventListener("error", fail);
  });
}

function seekTo(element, position) {
  const duration = element.duration;
  if (!Number.isFinite(duration) || duration <= 0) {
    owedPosition = position;
    return;
  }
  owedPosition = null;
  if (position >= duration - 0.25) return;
  element.currentTime = Math.max(position, 0);
}

function whenDuration(element) {
  if (Number.isFinite(element.duration) && element.duration > 0) return Promise.resolve();
  return new Promise((resolve) => {
    const finish = () => {
      if (!(Number.isFinite(element.duration) && element.duration > 0)) return;
      element.removeEventListener("durationchange", finish);
      element.removeEventListener("loadedmetadata", finish);
      resolve();
    };
    element.addEventListener("durationchange", finish);
    element.addEventListener("loadedmetadata", finish);
    setTimeout(() => {
      element.removeEventListener("durationchange", finish);
      element.removeEventListener("loadedmetadata", finish);
      resolve();
    }, 2000);
  });
}

async function playElement(element) {
  try {
    await element.play();
    return true;
  } catch (error) {
    return false;
  }
}

function clearPlayer(element) {
  element.pause();
  element.muted = false;
  element.src = "";
  element.removeAttribute("src");
  delete element.dataset.id;
  delete element.dataset.primed;
  element.load();
}

function clearWarm() {
  clearPlayer(warm);
}

function waitingForStation(now) {
  if (!latest || !latest.next || !latest.track || !now.track) return false;
  if (trackId !== latest.next.id || now.track.id !== latest.track.id) return false;
  if (latest.next.id === latest.track.id && now.position < 2) return false;
  return true;
}

let attachJob = Promise.resolve();

function enqueueAttach(now) {
  const run = attachJob.then(() => attach(now));
  attachJob = run.catch(() => {});
  return run;
}

async function attach(now) {
  const track = now.track;
  if (!track) return;
  // Pressing Play joins the live position. Once a song is already
  // playing, leave the playhead alone — correcting it is the jump.
  if (track.id !== trackId) {
    if (!catchUp && warm.dataset.id === track.id && warm.readyState >= 2) {
      beginIncoming();
    } else {
      trackId = track.id;
      active.preload = "auto";
      active.src = track.audio;
      active.dataset.id = track.id;
      active.load();
      await waitForMetadata(active);
      if (!listening) return;
      if (catchUp) {
        await whenDuration(active);
        if (!listening) return;
        seekTo(active, now.position);
      } else {
        owedPosition = null;
        active.currentTime = 0;
      }
    }
  } else if (catchUp) {
    seekTo(active, now.position);
  }
  if (!listening) return;
  catchUp = false;
  if (active.paused) {
    const started = await playElement(active);
    if (!listening) return;
    if (!started) stopListening("Press play to listen.");
  }
}

function considerWarm(now) {
  const upcoming = now && now.next;
  const track = now && now.track;
  if (!listening || !upcoming || !track) return;
  if (upcoming.id === trackId || track.id !== trackId) return;
  if (warm.dataset.id && warm.dataset.id !== upcoming.id) clearWarm();
  if (warm.dataset.id === upcoming.id) return;
  const heard = Number.isFinite(active.currentTime) ? active.currentTime : now.position;
  const remaining = (track.duration || 0) - heard;
  if (remaining > WARM_LEAD) return;
  warm.preload = "auto";
  warm.src = upcoming.audio;
  warm.dataset.id = upcoming.id;
  warm.load();
  warm.addEventListener("loadeddata", () => prime(warm), { once: true });
}

function prime(element) {
  if (element.dataset.primed === "1" || element === active) return;
  element.dataset.primed = "1";
  element.muted = true;
  const pending = element.play();
  if (!pending) return;
  pending.then(() => {
    if (element === active) {
      element.muted = false;
      return;
    }
    element.pause();
    try { element.currentTime = 0; } catch (error) { return; }
    element.muted = false;
  }).catch(() => {
    element.muted = false;
  });
}

function beginIncoming() {
  if (!warm.dataset.id || warm.readyState < 2) return false;
  const incoming = warm;
  const outgoing = active;
  owedPosition = null;
  incoming.muted = false;
  if (incoming.currentTime > 0.05) {
    try { incoming.currentTime = 0; } catch (error) { return false; }
  }
  warm = outgoing;
  active = incoming;
  trackId = incoming.dataset.id;
  const pending = incoming.play();
  const release = () => {
    if (outgoing !== active) outgoing.pause();
  };
      if (pending) pending.then(release).catch(() => {
        if (listening) stopListening("Press play to listen.");
      });
  else release();
  setTimeout(() => {
    if (outgoing !== active) clearPlayer(outgoing);
  }, 800);
  return true;
}

function showHandoff() {
  if (!latest || !latest.next || latest.next.id !== trackId) return;
  showTrack(latest.next);
  paint(0, latest.next.duration || 0);
  publishSession(latest.next, true);
}

function publishSession(track, playing) {
  if (!("mediaSession" in navigator)) return;
  if (!track) {
    navigator.mediaSession.metadata = null;
    navigator.mediaSession.playbackState = "none";
    return;
  }
  const artwork = [];
  if (track.artwork) {
    const item = { src: new URL(track.artwork, location.origin).href };
    if (track.artworkType) item.type = track.artworkType;
    artwork.push(item);
  }
  navigator.mediaSession.metadata = new MediaMetadata({
    title: track.title || "",
    artist: track.artist || "",
    album: track.album || "",
    artwork,
  });
  navigator.mediaSession.playbackState = playing ? "playing" : "paused";
}

function publishPosition(position, duration) {
  if (!("mediaSession" in navigator) || !navigator.mediaSession.setPositionState) return;
  if (!duration || duration <= 0) return;
  const safe = Math.min(Math.max(position || 0, 0), Math.max(duration - 0.1, 0));
  try {
    navigator.mediaSession.setPositionState({
      duration: duration,
      playbackRate: 1,
      position: safe,
    });
  } catch (error) {
    return;
  }
}

async function refresh() {
  const token = ++requestId;
  let now;
  try {
    const response = await fetch("/now", { cache: "no-store" });
    if (!response.ok) throw new Error(String(response.status));
    now = await response.json();
  } catch (error) {
    if (token === requestId) message.textContent = "The station is not responding.";
    return;
  }
  if (token !== requestId || !listening || waitingForStation(now)) return;
  latest = now;
  render(now);
  if (!listening || !now.track) return;
  try {
    await enqueueAttach(now);
    considerWarm(now);
  } catch (error) {
    if (listening) message.textContent = "This file could not be played.";
  }
}

let pollTimer = 0;

function startPolling() {
  stopPolling();
  pollTimer = setInterval(refresh, 4000);
}

function stopPolling() {
  if (!pollTimer) return;
  clearInterval(pollTimer);
  pollTimer = 0;
}

function stopListening(note) {
  owedPosition = null;
  listening = false;
  requestId += 1;
  stopPolling();
  document.body.classList.remove("listening");
  players.forEach(clearPlayer);
  showIdle();
  button.textContent = "Play";
  message.textContent = note || "";
}

async function startListening() {
  catchUp = true;
  listening = true;
  document.body.classList.add("listening");
  button.textContent = "Stop";
  message.textContent = "";
  startPolling();
  await refresh();
}

let handingOff = false;

function handoff() {
  if (handingOff || !listening) return false;
  if (!warm.dataset.id || warm.dataset.id === trackId || warm.readyState < 2) return false;
  handingOff = true;
  const started = beginIncoming();
  handingOff = false;
  if (!started) return false;
  showHandoff();
  refresh();
  return true;
}

function onTime(event) {
  if (event.target !== active || !listening || !Number.isFinite(active.duration)) return;
  paint(active.currentTime, active.duration);
  publishPosition(active.currentTime, active.duration);
  if (latest) considerWarm(latest);
  if (active.duration - active.currentTime <= 0.4) handoff();
}

function onEnded(event) {
  if (event.target !== active || !listening) return;
  if (!handoff()) refresh();
}

button.addEventListener("click", () => {
  if (listening) stopListening();
  else startListening();
});

players.forEach((element) => {
  element.addEventListener("timeupdate", onTime);
  element.addEventListener("ended", onEnded);
  element.addEventListener("durationchange", () => {
    if (element === active && owedPosition !== null) seekTo(element, owedPosition);
  });
});

document.addEventListener("keydown", (event) => {
  if (event.key === " " && event.target === document.body) {
    event.preventDefault();
    button.click();
  }
});

if ("mediaSession" in navigator) {
  try {
    navigator.mediaSession.setActionHandler("play", () => {
      if (!listening) startListening();
    });
    navigator.mediaSession.setActionHandler("pause", () => {
      if (listening) stopListening();
    });
  } catch (error) {
    // This browser offers the lock screen without action handlers.
  }
}

showIdle();
