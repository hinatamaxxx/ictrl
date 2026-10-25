// iCtrl viewer: WebCodecs (hardware) HEVC decode, touch/keyboard input, controls.
import { LANG, SETTING, adopt, fromServer, t, translatePage } from "./i18n.js";

translatePage();
const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];

const state = {
  ws: null,
  status: { state: "idle" },
  transport: localStorage.getItem("ictrl.transport") || "usb",
  devices: [],
  config: null,          // {codec, width, height, content_width, content_height, description}
  mode: "webcodecs",
  decoder: null,
  waitingKey: true,
  pending: null,         // newest decoded frame waiting for paint
  frames: 0, bytes: 0, lastStat: performance.now(), fps: 0, mbps: 0, decodeMs: 0,
  kbd: localStorage.getItem("ictrl.kbd") !== "0", keysDown: new Set(),
  pointer: null,
  apps: [],
};

window.ictrl = state;  // for debugging from DevTools
const canvas = $("#screen");
const ctx = canvas.getContext("2d", { alpha: false, desynchronized: true });
// No video: no old picture left behind the overlay (it showed through while
// the frame turned).
function clearScreen() {
  if (state.pending) { state.pending.close(); state.pending = null; }
  ctx.fillStyle = "#000";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
}
const device = $("#device");

// ------------------------------------------------------------------ helpers
// action: optional { label, run, detail, image } — a button, a smaller second
// line and a thumbnail on the left.
function toast(msg, bad = false, action = null) {
  msg = fromServer(msg);
  const t = $("#toast");
  t.textContent = "";
  if (action) {
    if (action.image) {
      const img = document.createElement("img");
      img.className = "toast-thumb";
      img.src = action.image;
      img.alt = "";
      t.append(img);
    }
    const text = document.createElement("div");
    text.className = "toast-text";
    const title = document.createElement("div");
    title.className = "toast-title";
    title.textContent = msg;
    text.append(title);
    if (action.detail) {
      const detail = document.createElement("div");
      detail.className = "toast-detail";
      detail.textContent = action.detail;
      text.append(detail);
    }
    t.append(text);
    const b = document.createElement("button");
    b.className = "toast-action";
    b.textContent = action.label;
    b.addEventListener("click", () => { t.classList.remove("show"); action.run(); });
    t.append(b);
  } else {
    t.textContent = msg;
  }
  t.classList.toggle("bad", bad);
  t.classList.toggle("has-action", !!action);
  t.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.remove("show"), bad ? 5000 : action ? 6000 : 2200);
}

// The page cannot start the iCtrl server itself (it runs in a browser): when
// the server is not running, say who can start it instead of "Failed to fetch".
const SERVER_DOWN = "iCtrl のサーバーが止まっています。Claude などの AI に「iCtrl につないで」と頼むと起動し、この画面も自動でつながります";
async function api(path, body) {
  // The socket already knows the server is gone: answer at once instead of
  // waiting about 2 s for Windows to refuse the connection.
  if (state.serverDown) throw new Error(t(SERVER_DOWN));
  let res;
  try {
    res = await fetch(path, body === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
  } catch (_) {
    throw new Error(t(SERVER_DOWN));
  }
  const ct = res.headers.get("content-type") || "";
  const data = ct.includes("json") ? await res.json() : res;
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

function send(obj) {
  if (state.ws && state.ws.readyState === 1) state.ws.send(JSON.stringify(obj));
}

const STAGES = {
  ownership: "接続を準備中", usbmux: "USB の iPhone を探しています", bonjour: "Wi-Fi で iPhone を探しています", remote_pairing: "Wi-Fi で接続中", wifi_pairing: "Wi-Fi で使う準備中", lockdown: "iPhone に接続中", pairing: "iPhone で「このコンピュータを信頼」を押してください",
  developer_mode: "デベロッパモードを確認中", developer_image: "iPhone の画面機能を準備中",
  tunnel: "iPhone との通信を準備中", display_info: "画面の情報を取得中", display_stream: "画面の映像を開始中",
  hid: "操作の準備中", ready: "準備完了",
};

// ------------------------------------------------------------------ status
// Display corner radius of each iPhone in points (UIKit's
// _displayCornerRadius); the screen is drawn with exactly that curve.
function cornerPoints(model) {
  const m = /^iPhone(\d+),(\d+)$/.exec(model || "");
  if (!m) return 62;
  const major = +m[1], minor = +m[2];
  if (major >= 18) return 62;                                  // iPhone 17 / Air / 17 Pro
  if (major === 17) return minor === 5 ? 47.33 : 62;           // 16e / 16 series
  if (major === 16 || major === 15) return 55;                 // 14 Pro – 15 Pro
  if (major === 14) return [2, 3, 7].includes(minor) ? 47.33 : minor === 8 ? 53.33 : minor === 4 ? 44 : 53.33;
  if (major === 13) return minor === 1 ? 44 : minor === 4 ? 53.33 : 47.33;
  if (major === 12) return minor === 1 ? 41.5 : 39;
  return 39;
}
// Apple's model identifiers ("iPhone18,3") as product names.
const MODEL_NAMES = {
  "iPhone18,1": "iPhone 17 Pro", "iPhone18,2": "iPhone 17 Pro Max", "iPhone18,3": "iPhone 17", "iPhone18,4": "iPhone Air",
  "iPhone17,1": "iPhone 16 Pro", "iPhone17,2": "iPhone 16 Pro Max", "iPhone17,3": "iPhone 16", "iPhone17,4": "iPhone 16 Plus",
  "iPhone17,5": "iPhone 16e", "iPhone16,1": "iPhone 15 Pro", "iPhone16,2": "iPhone 15 Pro Max",
  "iPhone15,4": "iPhone 15", "iPhone15,5": "iPhone 15 Plus", "iPhone15,2": "iPhone 14 Pro", "iPhone15,3": "iPhone 14 Pro Max",
  "iPhone14,7": "iPhone 14", "iPhone14,8": "iPhone 14 Plus", "iPhone14,6": "iPhone SE (第3世代)",
};
function modelName(id) { return t(MODEL_NAMES[id]) || id || "iPhone"; }

function renderCorners(s) {
  if (!s.screen) return;
  const [w, h] = s.screen;
  const scale = w > 900 ? 3 : 2;                               // all Face ID iPhones: @3x except XR / 11
  const r = cornerPoints(s.model) * scale;
  device.style.borderRadius = `${(r / w * 100).toFixed(3)}% / ${(r / h * 100).toFixed(3)}%`;
  device.style.aspectRatio = `${w} / ${h}`;
}

// The status pill widens or narrows smoothly to its new text (width: auto
// cannot be transitioned, so it is animated between measured widths). The
// text is hidden while the width moves and fades in once it has settled, so
// it is never seen cut off letter by letter.
function setStatusText(label) {
  const text = $("#statusText");
  if (text.textContent === label) return;
  const pill = $("#statusPill");
  const from = pill.getBoundingClientRect().width;
  text.textContent = label;
  pill.style.width = "";
  const to = pill.getBoundingClientRect().width;
  if (!from || Math.abs(to - from) < 1 || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  text.classList.add("swapping");
  pill.style.width = from + "px";
  void pill.offsetWidth;
  pill.style.width = to + "px";
  clearTimeout(setStatusText.timer);
  setStatusText.timer = setTimeout(() => {
    pill.style.width = "";
    text.classList.remove("swapping");
  }, 260);
}

function statusLabel(s) {
  // No USB / Wi-Fi here: the transport buttons beside it already show it.
  // Over Wi-Fi the name is not known: show what is (the product, iOS).
  const device = s.name || [t(MODEL_NAMES[s.model]) || "iPhone", s.ios && `iOS ${s.ios}`].filter(Boolean).join(" · ");
  return {
    idle: t("未接続"), closed: t("切断しました"), closing: t("切断中…"),
    // Short and fixed while connecting: the phone area shows the stage, and
    // a label that changed length per stage pushed the top bar's buttons out.
    connecting: t("接続中…"),
    ready: device,
    failed: t("接続が切れました"),
  }[s.state] || s.state;
}

function renderStatus() {
  const s = state.status;
  renderCorners(s);
  renderAgentBadge();
  renderClipboardMode();
  $("#pairingBox").hidden = !(s.state === "ready" && s.transport === "usb" && !s.own_pairing);
  const pill = $("#statusPill");
  pill.className = "pill " + (s.state || "idle");
  setStatusText(statusLabel(s));
  // Details only on hover: iOS version and model.
  pill.title = s.state === "ready" ? [t(MODEL_NAMES[s.model]), `iOS ${s.ios || "?"}`].filter(Boolean).join(" · ") :
    s.state === "connecting" ? t(STAGES[s.stage] || "") : "";

  const live = s.state === "ready";
  device.classList.toggle("live", live);
  const overlay = $("#overlay");
  const paused = live && s.video_paused;
  if (paused || !live) clearScreen();
  overlay.classList.toggle("hidden", live && !!state.config && !paused);
  $("#videoResume").hidden = !paused;
  const videoBtn = $("#videoBtn");
  videoBtn.hidden = !live;
  videoBtn.classList.toggle("off", !!paused);
  videoBtn.title = paused ? t("画面を映す") : t("画面を映すのをやめる（操作・AI・スクリーンショットは続けて使えます）");
  // While connecting, the top bar has 切断 and the phone area shows progress
  // only (no second set of connect buttons).
  $(".empty-actions").hidden = live || s.state === "connecting";
  $("#progress").hidden = s.state !== "connecting";
  $("#progressText").textContent = t(STAGES[s.stage] || "");
  $$(".empty-actions .btn").forEach((b) => (b.disabled = s.state === "connecting"));
  const err = $("#errorText");
  err.hidden = !s.error || live;
  err.textContent = fromServer(s.error) || "";
  $("#emptyTitle").textContent = s.state === "failed" ? t("接続が切れました") :
    paused === "off" ? t("画面を映していません") :
    paused === "camera" ? t("カメラの使用中は画面を映していません") :
    paused ? t("マイクかカメラの使用中は映像を出せません") :
    live ? t("映像を待っています…") : s.state === "connecting" ? t("接続しています") : t("iPhone を接続");
  $("#emptyText").textContent = s.state === "failed" ? t("原因を確認してから、もう一度接続してください。") :
    paused === "off" ? t("操作・AI・スクリーンショットはそのまま使えます。映すときは「画面を映す」を押してください。") :
    paused === "camera" ? t("iOS は、画面の映像を流している間はカメラを使わせません（カメラが真っ暗になります）。カメラを閉じると映像に戻ります。操作・AI・スクリーンショットはそのまま使えます。") :
    paused ? t("iOS は、通話や録音などでマイクかカメラを使っている間、画面の映像を出しません。操作・AI・スクリーンショットは使えます。WDA の起動に必要な XCTest 要求には、メニューで有効にした自動入力を使います。終わったら「画面を映す」を押してください。") :
    live ? t("画面が変化すると表示されます。") :
    s.state === "connecting" ? t("{via} でつないでいます。", { via: s.transport === "wifi" ? "Wi-Fi" : "USB" }) :
    t("USB ケーブルまたは同じ Wi-Fi で接続できます。");

  const busy = live || s.state === "connecting";
  // Not connected: the phone area offers "USB で接続" / "Wi-Fi で接続", so the
  // top bar's button is always 切断 (greyed until connected) and the transport
  // stays in view, greyed: nothing in the top bar moves.
  $("#quickTransport").classList.toggle("off", !busy);
  $$("#quickTransport button").forEach((b) => { b.disabled = !busy; });
  // The dock's buttons act on a connected phone only.
  $("#dock").classList.toggle("idle", !live);
  const quick = $("#quickConnect");
  quick.textContent = t("切断");
  quick.disabled = !busy;
  quick.className = "btn quick-connect " + (busy ? "danger" : "off");
  // While connected, the top bar shows the transport in use.
  if (busy && s.transport && s.transport !== state.transport) setTransport(s.transport);

  const watch = s.clipboard_watch || "";
  $("#clipWatch").textContent = live ? (watch === "on" ? t("監視中") : watch.startsWith("stopped") ? t("監視停止") : "") : "";
  $("#clipWatch").title = watch;
  const facts = $("#facts");
  facts.innerHTML = "";
  if (live) {
    const rows = [[t("端末"), s.name], ["iOS", s.ios],
      [t("画面"), s.screen ? `${s.screen[0]} × ${s.screen[1]}` : "—"],
      [t("映像"), s.video?.codec ? "HEVC (GPU)" : t("待機中")]];
    for (const [k, v] of rows) {
      facts.insertAdjacentHTML("beforeend", `<dt>${k}</dt><dd></dd>`);
      facts.lastElementChild.textContent = v || "—";
    }
  }
  if (!live) { state.config = null; resetDecoder(); }
}

// ------------------------------------------------------------------ video
function resetDecoder() {
  if (state.decoder && state.decoder.state !== "closed") {
    try { state.decoder.close(); } catch {}
  }
  state.decoder = null;
  state.waitingKey = true;
  state.configuring = false;
  if (typeof held !== "undefined") held.length = 0;
}

function b64(s) { return Uint8Array.from(atob(s), (c) => c.charCodeAt(0)); }

// Decoder (re)configuration. The phone changes resolution under motion, which
// arrives as a new config immediately followed by its keyframe. Configuring
// is asynchronous, so chunks arriving meanwhile are held and fed afterwards;
// dropping them lost that keyframe and froze the picture until the phone
// happened to send another one.
let configSeq = 0;
const held = [];            // binary frames that arrived while configuring
let supportCache = new Map();
let lastKeyRequest = 0;
const videoDiag = { configs: 0, decoderErrors: 0, keyRequests: 0, heldMax: 0, lastFrameAt: 0, freezes: 0,
  decoded: 0, output: 0, lastLatency: 0, maxLatency: 0 };
const fedAt = new Map();  // chunk timestamp -> when it was given to the decoder
window.ictrlVideo = videoDiag;

function requestKeyframe(reason) {
  const now = performance.now();
  if (now - lastKeyRequest < 400) return;
  lastKeyRequest = now;
  videoDiag.keyRequests++;
  send({ t: "keyframe", reason });
}

async function decoderConfig(cfg) {
  const config = { codec: cfg.codec, optimizeForLatency: true, hardwareAcceleration: "prefer-hardware" };
  // Edge's decoder (Media Foundation/DXVA) reports a 1280x720 visible rect
  // when the size is not given here, so only the top of the screen is drawn.
  if (cfg.width && cfg.height) { config.codedWidth = cfg.width; config.codedHeight = cfg.height; }
  state.annexb = new URLSearchParams(location.search).get("format") === "annexb"
    || localStorage.getItem("ictrl.format") === "annexb";
  if (!state.annexb) config.description = b64(cfg.description);
  const key = cfg.codec + (state.annexb ? "/annexb" : "");
  if (!supportCache.has(key)) {
    let ok = (await VideoDecoder.isConfigSupported(config).catch(() => ({ supported: false }))).supported;
    let hw = "prefer-hardware";
    if (!ok) {
      config.hardwareAcceleration = hw = "no-preference";
      ok = (await VideoDecoder.isConfigSupported(config).catch(() => ({ supported: false }))).supported;
    }
    supportCache.set(key, ok ? hw : null);
  }
  const hw = supportCache.get(key);
  if (!hw) return null;
  config.hardwareAcceleration = hw;
  return config;
}

function makeDecoder() {
  return new VideoDecoder({
    output: (frame) => {
      if (state.pending) state.pending.close();
      state.pending = frame;
      state.frames++;
      const now = performance.now();
      videoDiag.lastFrameAt = now;
      videoDiag.output++;
      const at = fedAt.get(frame.timestamp);
      if (at !== undefined) {
        fedAt.delete(frame.timestamp);
        videoDiag.lastLatency = Math.round(now - at);
        videoDiag.maxLatency = Math.max(videoDiag.maxLatency, videoDiag.lastLatency);
      }
    },
    error: (e) => {
      console.warn("decoder error", e);
      videoDiag.decoderErrors++;
      send({ t: "client", mode: "decoder-error", hw: String(e && e.message || e).slice(0, 80) });
      if (state.config) configure(state.config);
      requestKeyframe("decoder-error");
    },
  });
}

async function configure(cfg) {
  const seq = ++configSeq;
  state.config = cfg;
  videoDiag.configs++;
  const cw = cfg.content_width || cfg.width, ch = cfg.content_height || cfg.height;
  canvas.width = cw; canvas.height = ch;
  device.style.aspectRatio = `${cw} / ${ch}`;
  layoutRotation();
  state.configuring = true;
  const config = await decoderConfig(cfg);
  if (seq !== configSeq) return;            // a newer config superseded this one
  if (!config) {
    state.configuring = false;
    held.length = 0;
    toast(t("この画面では HEVC の映像を再生できません（GPU で HEVC を再生できるブラウザーが必要です）"), true);
    renderStatus();
    return;
  }
  if (!state.decoder || state.decoder.state === "closed") state.decoder = makeDecoder();
  state.decoder.configure(config);          // reconfigure in place: no teardown gap
  state.waitingKey = true;
  state.configuring = false;
  send({ t: "client", mode: "webcodecs", hw: config.hardwareAcceleration, format: state.annexb ? "annexb" : "hvcc", ua: navigator.userAgent.slice(-60) });
  const queued = held.splice(0);
  for (const buf of queued) feed(buf);
  renderStatus();
}

let ts = 0;
function onBinary(buf) {
  state.bytes += buf.byteLength;
  if (state.configuring) {
    held.push(buf);
    videoDiag.heldMax = Math.max(videoDiag.heldMax, held.length);
    if (held.length > 600) held.splice(0, held.length - 600);
    return;
  }
  feed(buf);
}

function feed(buf) {
  const u8 = new Uint8Array(buf);
  const type = u8[0];
  const dec = state.decoder;
  if (!dec || dec.state !== "configured") return;
  const key = type === 0;
  if (state.waitingKey && !key) {
    // Each skipped delta is evidence the picture is stuck: ask for a key.
    requestKeyframe("waiting");
    return;
  }
  state.waitingKey = false;
  ts += 16667;
  const data = state.annexb ? toAnnexB(u8.subarray(5)) : u8.subarray(5);
  try {
    dec.decode(new EncodedVideoChunk({ type: key ? "key" : "delta", timestamp: ts, data }));
    videoDiag.decoded++;
    fedAt.set(ts, performance.now());
    if (fedAt.size > 300) fedAt.delete(fedAt.keys().next().value);
  } catch (e) {
    state.waitingKey = true;
    requestKeyframe("decode-throw");
  }
}

function toAnnexB(src) {
  // Replace each 4-byte length prefix with a start code, in place on a copy.
  const out = src.slice();
  for (let i = 0; i + 4 <= out.length;) {
    const n = (out[i] << 24 | out[i + 1] << 16 | out[i + 2] << 8 | out[i + 3]) >>> 0;
    out[i] = 0; out[i + 1] = 0; out[i + 2] = 0; out[i + 3] = 1;
    i += 4 + n;
  }
  return out;
}

function paint() {
  const f = state.pending;
  if (f) {
    state.pending = null;
    ctx.drawImage(f, 0, 0, canvas.width, canvas.height, 0, 0, canvas.width, canvas.height);
    f.close();
  }
  const now = performance.now();
  if (now - state.lastStat > 1000) {
    const dt = (now - state.lastStat) / 1000;
    state.fps = Math.round(state.frames / dt);
    state.mbps = (state.bytes * 8 / dt / 1e6).toFixed(1);
    state.frames = 0; state.bytes = 0; state.lastStat = now;
    $("#stats").innerHTML = state.status.state === "ready"
      ? `<span><b>${state.fps}</b> fps</span><span><b>${state.mbps}</b> Mbps</span><span>GPU</span>`
      : "";
  }
  requestAnimationFrame(paint);
}
requestAnimationFrame(paint);

// ------------------------------------------------------------------ socket
function connectSocket() {
  const ws = new WebSocket(`ws://${location.host}/ws`);
  ws.binaryType = "arraybuffer";
  state.ws = ws;
  ws.onopen = () => {
    state.serverDown = false;
    videoOn = null;                         // a new socket: tell the server again
    setVideo(!document.hidden);
  };
  ws.onmessage = (ev) => {
    if (typeof ev.data !== "string") return onBinary(ev.data);
    const msg = JSON.parse(ev.data);
    if (msg.t === "status") {
      const prev = state.status.state;
      const prevRotation = state.status.rotation;
      const passcodeChanged = prev !== msg.state || state.status.udid !== msg.udid ||
        state.status.passcode_state !== msg.passcode_state;
      state.status = msg;
      if (typeof msg.agent_running === "number") agentState.running = msg.agent_running;
      renderStatus();
      renderPasscode();
      if (passcodeChanged) refreshPasscode();
      if (prev !== msg.state && msg.state === "failed") toast(msg.error || t("接続が切れました"), true);
      // The server keeps one PC-keyboard setting for every open window: only
      // the window the user works in states it. Every window sending its own
      // made a background one (keyboard off) switch the iPhone to its
      // on-screen keyboard for the window being typed in.
      if (prev !== msg.state && msg.state === "ready" && document.hasFocus()) {
        send({ t: "pc_keyboard", on: state.kbd });
      }
      // Follow iOS's orientation when it is known/changes (a page reload
      // learns it here).
      if (typeof msg.rotation === "number" && msg.rotation !== prevRotation) setRotation(msg.rotation);
      if (typeof msg.windows_ime === "boolean") showInputMode(msg.windows_ime);
    } else if (msg.t === "ime") {
      showInputMode(!!msg.japanese);          // the Windows IME changed (the iPhone follows it)
    } else if (msg.t === "orientation") {
      if (typeof msg.rotation === "number") {
        setRotation(msg.rotation);          // iOS turned the screen (SpringBoard's log)
      } else {
        // Reported when an agent reads or sets the orientation (WebDriverAgent).
        const o = String(msg.orientation || "").toUpperCase();
        setRotation(o.includes("RIGHT") ? 90 : o.includes("LANDSCAPE") ? -90 : 0);
      }
    } else if (msg.t === "hello") {
      // The server was updated with a newer GUI: load it.
      if (state.build && state.build !== msg.build) {
        send({ t: "reloading" });     // not a closed window (see the server's app_window)
        return location.reload();
      }
      state.build = msg.build;
    } else if (msg.t === "config") {
      configure(msg);
    } else if (msg.t === "video-end") {
      state.config = null; resetDecoder(); renderStatus();
    } else if (msg.t === "clipboard-error") {
      if (msg.reason === "shortcut") $("#shortcutHelp").hidden = false;
    } else if (msg.t === "clipboard-pending") {
      /* silent: only the result is announced */
    } else if (msg.t === "clipboard") {
      onClipboardEvent(msg);
    } else if (msg.t === "settings") {
      applyClipboardSettings(msg);
      applyWdaMode(msg);
      applyVideoOnConnect(msg);
      applyLanguage(msg);
    } else if (msg.t === "passcode") {
      refreshPasscode();
    } else if (msg.t === "agent") {
      showAgent(msg);
    } else if (msg.t === "reply") {
      const resolve = pendingReplies.get(msg.id);
      pendingReplies.delete(msg.id);
      if (!msg.ok) toast(msg.error, true);
      if (resolve) resolve(msg.ok ? msg.result : null);
    }
  };
  ws.onclose = () => {
    pendingReplies.forEach((resolve) => resolve(null));   // no reply will come on this socket
    pendingReplies.clear();
    // The local iCtrl server restarted or is starting: reconnect to it (this
    // is our own process on this PC, not the phone; the phone session is
    // never reopened automatically).
    state.ws = null;
    state.serverDown = true;
    state.status = { state: "idle", error: t(SERVER_DOWN) };
    renderStatus();
    setTimeout(connectSocket, 1000);
  };
}

// ------------------------------------------------------------------ touch
// The video and all touches are in the phone's portrait panel space. In
// landscape the device is shown turned (-90 = LANDSCAPE, the app's top along
// the panel's right edge; 90 = LANDSCAPERIGHT); points on screen are turned
// back before they are sent.
state.rotation = 0;
function toPanel(u, v) {
  if (state.rotation === -90) return { x: 1 - v, y: u };
  if (state.rotation === 90) return { x: v, y: 1 - u };
  return { x: u, y: v };
}
function pointFrom(ev) {
  const r = canvas.getBoundingClientRect();      // the turned box on screen
  const p = toPanel((ev.clientX - r.left) / r.width, (ev.clientY - r.top) / r.height);
  return { x: Math.min(1, Math.max(0, p.x)), y: Math.min(1, Math.max(0, p.y)) };
}
function deltaToPanel(dx, dy) {
  if (state.rotation === -90) return { dx: -dy, dy: dx };
  if (state.rotation === 90) return { dx: dy, dy: -dx };
  return { dx, dy };
}
function layoutRotation() {
  // Upright or turned, the phone is placed the same way (centred, sized,
  // rotated), so a change of orientation animates as one turn (style.css).
  const area = $("#deviceArea").getBoundingClientRect();
  const [aw, ah] = (device.style.aspectRatio || "").split("/").map(Number);
  const [w, h] = aw && ah ? [aw, ah] : (state.status.screen || [1206, 2622]);
  const sideways = state.rotation !== 0;
  device.classList.add("placed");
  device.classList.toggle("rotated", sideways);
  // Use what is visible: in a narrow window the dock makes the page wider
  // than the viewport, and the 8 px frame outline is outside the box.
  const outline = 16;
  const across = Math.min(area.width, innerWidth - Math.max(0, area.left) - 8) - outline;
  const down = area.height - outline;
  // Upright the phone's height runs down the area; turned, across it.
  const height = Math.max(40, sideways ? Math.min(across, down * h / w) : Math.min(down, across * h / w));
  const width = height * w / h;
  if (layoutRotation.last !== undefined && layoutRotation.last !== state.rotation) {
    device.classList.add("turning");
    clearTimeout(layoutRotation.turnTimer);
    layoutRotation.turnTimer = setTimeout(() => device.classList.remove("turning"), 450);
  }
  device.style.height = height + "px";
  // Centre in the visible part of the area.
  const visible = Math.min(area.width, innerWidth - Math.max(0, area.left));
  device.style.left = visible / 2 + "px";
  device.style.transform = `translate(-50%, -50%) rotate(${state.rotation}deg)`;
  // The overlay (video off, connecting...) turns back, so its text reads
  // upright inside a landscape frame.
  const overlay = $("#overlay");
  if (layoutRotation.last !== undefined && layoutRotation.last !== state.rotation &&
      !overlay.classList.contains("hidden")) {
    // Turning, like iOS: a still copy of the old layout turns with the frame
    // underneath, and the new layout fades in over it (style.css).
    device.querySelectorAll(".overlay-snapshot").forEach((old) => old.remove());
    const snapshot = overlay.cloneNode(true);
    snapshot.removeAttribute("id");
    snapshot.querySelectorAll("[id]").forEach((el) => el.removeAttribute("id"));
    snapshot.classList.add("overlay-snapshot");
    overlay.before(snapshot);
    overlay.classList.remove("turn-in");
    void overlay.offsetWidth;
    overlay.classList.add("turn-in");
    setTimeout(() => snapshot.remove(), 450);
  }
  layoutRotation.last = state.rotation;
  overlay.classList.toggle("landscape", sideways);
  const [ow, oh] = sideways ? [height, width] : [width, height];
  Object.assign(overlay.style, {
    inset: "auto", width: `${ow}px`, height: `${oh}px`,
    left: `${(width - ow) / 2}px`, top: `${(height - oh) / 2}px`, transform: `rotate(${-state.rotation}deg)`,
  });
  overlay.style.setProperty("--turn", `${state.rotation}deg`);
}
function setRotation(deg) {
  state.rotation = deg;
  layoutRotation();
}
window.addEventListener("resize", () => layoutRotation());
layoutRotation();

function dot(p, cls = "") {
  const d = document.createElement("div");
  d.className = "touch-dot " + cls;
  d.style.left = p.x * 100 + "%";
  d.style.top = p.y * 100 + "%";
  $("#touches").appendChild(d);
  return d;
}
function fade(d) { d.classList.add("up"); setTimeout(() => d.remove(), 320); }

canvas.addEventListener("pointerdown", (ev) => {
  // Keep keyboard focus in the IME-enabled field: a click on the screen must
  // not move focus to a non-editable element (Chrome then disables the IME).
  ev.preventDefault();
  focusKeyboard();
  if (state.status.state !== "ready" || ev.button !== 0) return;
  endWheel();   // a click lifts the scrolling finger first
  canvas.setPointerCapture(ev.pointerId);
  const p = pointFrom(ev);
  state.pointer = { id: ev.pointerId, dot: dot(p) };
  send({ t: "touch", phase: "down", ...p });
});
canvas.addEventListener("pointermove", (ev) => {
  const ptr = state.pointer;
  if (!ptr || ptr.id !== ev.pointerId) return;
  const p = pointFrom(ev);
  ptr.dot.style.left = p.x * 100 + "%";
  ptr.dot.style.top = p.y * 100 + "%";
  send({ t: "touch", phase: "move", ...p });
});
function endPointer(ev) {
  const ptr = state.pointer;
  if (!ptr || ptr.id !== ev.pointerId) return;
  send({ t: "touch", phase: "up", ...pointFrom(ev) });
  fade(ptr.dot);
  state.pointer = null;
}
canvas.addEventListener("pointerup", endPointer);
canvas.addEventListener("pointercancel", endPointer);
// Windows-style right-click menu: edit commands are sent as the iOS
// keyboard shortcuts (⌘C/⌘X/⌘V/⌘A/⌘Z); a copy on the iPhone then reaches
// the PC clipboard by itself.
const ctxMenu = $("#ctxMenu");
let ctxPoint = null;
canvas.addEventListener("contextmenu", (ev) => {
  ev.preventDefault();
  if (state.status.state !== "ready") return;
  ctxPoint = pointFrom(ev);
  ctxMenu.hidden = false;
  const w = ctxMenu.offsetWidth, h = ctxMenu.offsetHeight;
  ctxMenu.style.left = Math.min(ev.clientX, innerWidth - w - 8) + "px";
  ctxMenu.style.top = Math.min(ev.clientY, innerHeight - h - 8) + "px";
});
function closeCtx() { ctxMenu.hidden = true; focusKeyboard(); }
document.addEventListener("pointerdown", (ev) => { if (!ctxMenu.hidden && !ctxMenu.contains(ev.target)) closeCtx(); }, true);
window.addEventListener("keydown", (ev) => { if (!ctxMenu.hidden && ev.key === "Escape") { ev.stopImmediatePropagation(); closeCtx(); } }, true);
window.addEventListener("blur", () => { ctxMenu.hidden = true; });

function tapAt(p, holdMs = 50) {
  send({ t: "touch", phase: "down", ...p });
  const d = dot(p);
  setTimeout(() => { send({ t: "touch", phase: "up", ...p }); fade(d); }, holdMs);
}
async function copyScreenToPc() {
  try { await api("/api/screenshot/copy", {}); toast(t("iPhone の画面を PC にコピーしました")); }
  catch (e) { toast(e.message, true); }
}
const CTX = {
  copy: () => command({ t: "key", key: "c", modifiers: ["cmd"] }),
  cut: () => command({ t: "key", key: "x", modifiers: ["cmd"] }),
  paste: () => pasteFromPc(),
  selectall: () => command({ t: "key", key: "a", modifiers: ["cmd"] }),
  undo: () => command({ t: "key", key: "z", modifiers: ["cmd"] }),
  redo: () => command({ t: "key", key: "z", modifiers: ["cmd", "shift"] }),
  longpress: () => ctxPoint && tapAt(ctxPoint, 650),
  word: () => { if (ctxPoint) { tapAt(ctxPoint); setTimeout(() => tapAt(ctxPoint), 120); } },
  shot: () => copyScreenToPc(),
  saveshot: () => saveShot(),
  shotfolder: () => api("/api/screenshot/reveal", {}).catch((e) => toast(e.message, true)),
  home: () => pressButton("home"),
};
ctxMenu.addEventListener("click", (ev) => {
  const b = ev.target.closest("[data-ctx]");
  if (!b) return;
  closeCtx();
  CTX[b.dataset.ctx]();
});

// Mouse wheel -> a finger drag (iOS has no wheel scroll for touch UIs).
// A wheel notch is one 100px jump; sent as one move and lifted at once, iOS
// took it as a flick and added momentum, so the distance varied. Instead the
// finger glides to the scrolled distance frame by frame and rests before it
// lifts (no momentum): every notch scrolls the same amount, the page follows
// the wheel. A touchpad's own inertia arrives as further wheel events. Near
// the edge of the screen the finger lifts and grabs again where it started.
// Timer steps, not requestAnimationFrame: animation frames stop while the
// window is hidden, and the finger stayed down on the iPhone (other touches
// then became a second finger). A hidden page lifts the finger at once.
const WHEEL = { frame: 16, follow: 0.3, rest: 80, idle: 120, min: [0.02, 0.03], max: [0.98, 0.95] };
let wheel = null;
function wheelSend(phase) { send({ t: "touch", phase, x: wheel.x, y: wheel.y }); }
function wheelFrame(now) {
  const w = wheel;
  if (!w) return;
  if (w.phase === "regrab") {
    // Lifted at the edge after resting: put the finger down at the start again.
    w.tx += w.ox - w.x; w.ty += w.oy - w.y;
    w.x = w.ox; w.y = w.oy;
    wheelSend("down");
    w.phase = "drag"; w.moved = now;
  } else {
    const ex = w.tx - w.x, ey = w.ty - w.y;
    const far = Math.abs(ex) > 0.0004 || Math.abs(ey) > 0.0004;
    if (far && w.phase === "drag") {
      const nx = w.x + ex * WHEEL.follow, ny = w.y + ey * WHEEL.follow;
      if (nx < WHEEL.min[0] || nx > WHEEL.max[0] || ny < WHEEL.min[1] || ny > WHEEL.max[1]) {
        w.phase = "edge";          // rest here, then lift and grab again
      } else {
        w.x = nx; w.y = ny; w.moved = now;
        wheelSend("move");
      }
    } else if (now - w.moved > WHEEL.rest && (w.phase === "edge" || now - w.input > WHEEL.idle)) {
      wheelSend("up");
      if (w.phase === "edge" && far) w.phase = "regrab";
      else { wheel = null; return; }
    }
  }
  w.timer = setTimeout(() => wheelFrame(performance.now()), WHEEL.frame);
}
// A hidden window (minimized, covered, a background tab) takes no video:
// every open window decoded the 60 fps stream on the same GPU, and the
// one in use fell behind. Shown again, it resumes from a keyframe.
// Hiding takes effect at once; being shown only once it lasts SHOWN_AFTER.
// While hidden, the Claude desktop app's browser pane turns the page visible
// for about 12 ms every 2.7 s: each blip restarted the
// video and, counted as a viewer, kept the connection from idling out.
const SHOWN_AFTER = 300;
let videoOn = null, shownTimer = null;
function setVideo(on) {
  if (on === videoOn) return;
  videoOn = on;
  send({ t: "video", on, mode: "webcodecs" });
}
document.addEventListener("visibilitychange", () => {
  clearTimeout(shownTimer);
  if (document.hidden) {
    endWheel();
    setVideo(false);
  } else {
    shownTimer = setTimeout(() => setVideo(!document.hidden), SHOWN_AFTER);
  }
});
window.addEventListener("blur", () => endWheel());
function endWheel() {
  if (!wheel) return;
  clearTimeout(wheel.timer);
  if (wheel.phase !== "regrab") wheelSend("up");
  wheel = null;
}
canvas.addEventListener("wheel", (ev) => {
  if (state.status.state !== "ready" || state.pointer) return;
  ev.preventDefault();
  const r = canvas.getBoundingClientRect();
  const unit = ev.deltaMode === 1 ? 16 : ev.deltaMode === 2 ? r.height : 1;
  const { dx, dy } = deltaToPanel(ev.deltaX * unit / r.width, ev.deltaY * unit / r.height);
  const now = performance.now();
  if (!wheel) {
    const p = pointFrom(ev);
    const x = Math.min(WHEEL.max[0], Math.max(WHEEL.min[0], p.x));
    const y = Math.min(WHEEL.max[1], Math.max(WHEEL.min[1], p.y));
    wheel = { x, y, ox: x, oy: y, tx: x, ty: y, phase: "drag", moved: now, input: now };
    wheelSend("down");
    wheel.timer = setTimeout(() => wheelFrame(performance.now()), WHEEL.frame);
  }
  // Scrolling down moves the finger up (the content follows the finger).
  wheel.tx -= dx;
  wheel.ty -= dy;
  wheel.input = now;
}, { passive: false });

// ------------------------------------------------------------------ keyboard
// Physical keys (KeyboardEvent.code) -> HID usages. iOS applies its own
// hardware-keyboard layout (設定 > 一般 > キーボード > ハードウェアキーボード).
const HID = {};
"abcdefghijklmnopqrstuvwxyz".split("").forEach((c, i) => (HID["Key" + c.toUpperCase()] = 0x04 + i));
"1234567890".split("").forEach((c, i) => (HID["Digit" + c] = 0x1e + i));
for (let i = 1; i <= 12; i++) HID["F" + i] = 0x3a + i - 1;
Object.assign(HID, {
  Enter: 0x28, NumpadEnter: 0x28, Escape: 0x29, Backspace: 0x2a, Tab: 0x2b, Space: 0x2c,
  Minus: 0x2d, Equal: 0x2e, BracketLeft: 0x2f, BracketRight: 0x30, Backslash: 0x31, IntlYen: 0x89,
  Semicolon: 0x33, Quote: 0x34, Backquote: 0x35, Comma: 0x36, Period: 0x37, Slash: 0x38, IntlRo: 0x87,
  IntlBackslash: 0x64, CapsLock: 0x39, Delete: 0x4c, Insert: 0x49,
  ArrowRight: 0x4f, ArrowLeft: 0x50, ArrowDown: 0x51, ArrowUp: 0x52,
  Home: 0x4a, End: 0x4d, PageUp: 0x4b, PageDown: 0x4e,
  Numpad0: 0x62, Numpad1: 0x59, Numpad2: 0x5a, Numpad3: 0x5b, Numpad4: 0x5c, Numpad5: 0x5d,
  Numpad6: 0x5e, Numpad7: 0x5f, Numpad8: 0x60, Numpad9: 0x61, NumpadDecimal: 0x63,
  NumpadAdd: 0x57, NumpadSubtract: 0x56, NumpadMultiply: 0x55, NumpadDivide: 0x54,
  ControlLeft: 0xe0, ShiftLeft: 0xe1, AltLeft: 0xe2, MetaLeft: 0xe3,
  ControlRight: 0xe4, ShiftRight: 0xe5, AltRight: 0xe6, MetaRight: 0xe7,
  Lang1: 0x90, Lang2: 0x91,  // Mac JIS かな / 英数
});
const CTRL = new Set([0xe0, 0xe4]);
// Windows JIS input-mode keys. iOS switches input mode with the Apple JIS
// かな (LANG1) / 英数 (LANG2) keys, verified on iOS 27.
const IME_KEYS = { KanaMode: true, Convert: true, NonConvert: false, Lang1: true, Lang2: false };
const ZENHAN = new Set(["Zenkaku", "Hankaku", "ZenkakuHankaku", "KanjiMode", "Alphanumeric"]);
// Ctrl+<key> shortcuts a Windows user expects, sent as Cmd+<key> on iOS.
const CMD_SHORTCUTS = new Set(["KeyA", "KeyC", "KeyV", "KeyX", "KeyZ", "KeyY", "KeyF", "KeyB", "KeyI",
  "KeyU", "KeyS", "KeyN", "KeyT", "KeyW", "KeyR", "KeyL", "Backspace", "ArrowLeft", "ArrowRight",
  "ArrowUp", "ArrowDown"]);
state.jp = false;   // shown from the server (the Windows IME)
try { localStorage.removeItem("ictrl.jp"); } catch (_) {}
state.ctrlAsCmd = localStorage.getItem("ictrl.ctrlAsCmd") !== "0";
state.ctrlHeld = false;

function renderKbd() {
  $("#kbdBtn").classList.toggle("on", state.kbd);
  device.classList.toggle("kbd", state.kbd);
  const chip = $("#imeBtn");
  chip.textContent = state.jp ? t("あ") : "A";
  chip.title = (state.jp ? t("日本語入力") : t("英字入力")) + t("（クリック / 半角全角キーで切替。Windows と iPhone の両方が切り替わります）");
}

function syncKeys() { send({ t: "keys", usages: [...state.keysDown] }); }

function setKbd(on, announce = true) {
  state.kbd = on;
  state.ctrlHeld = false;
  localStorage.setItem("ictrl.kbd", on ? "1" : "0");
  send({ t: "pc_keyboard", on });
  if (!on) { state.keysDown.clear(); syncKeys(); releaseKeyboard(); }
  else focusKeyboard();
  renderKbd();
  if (announce) toast(on ? t("PC キーボードを iPhone に接続しました（Ctrl+K で解除）") : t("キーボードの接続を解除しました"));
}

// The iPhone's かな / 英数 follows the Windows IME on the server (manager.
// follow_windows_ime, told by imewatch.py); the page only shows it.
function showInputMode(jp) {
  state.jp = jp;
  renderKbd();
}

// While direct keyboard is on, focus sits in an invisible text field so the
// Windows IME stays enabled and 半角/全角 works as usual. Physical keys go to
// the iPhone (its own IME converts); whatever Windows composes is discarded.
const sink = $("#kbdSink");
function focusKeyboard() {
  if (state.kbd && !$("dialog[open]") && document.activeElement !== sink) sink.focus({ preventScroll: true });
}
const clearSink = () => { if (sink.value) sink.value = ""; };
// Whenever focus leaves the field for something that is not a real input
// (buttons, the page body, the video), put it back.
sink.addEventListener("blur", () => setTimeout(() => {
  if (state.kbd && document.hasFocus() && !isTyping()) focusKeyboard();
}, 0));
document.addEventListener("mousedown", (ev) => {
  if (ev.target.closest?.("dialog")) return;
  if (state.kbd && ev.target.closest && ev.target.closest("button, .dock, .topbar")) ev.preventDefault();
}, true);
sink.addEventListener("input", clearSink);
sink.addEventListener("compositionend", () => setTimeout(clearSink, 0));


const TEXT_KEY = /^(Key[A-Z]|Digit\d|Minus|Equal|BracketLeft|BracketRight|Backslash|Semicolon|Quote|Comma|Period|Slash|IntlRo|IntlYen)$/;
// Diagnostics: IME-related key events go to the server log (key names only).
function logKey(ev) {
  const focusInfo = document.activeElement === sink ? "sink" : (document.activeElement && document.activeElement.tagName);
  if (ev.key === "Process" || ev.keyCode === 229 || isZenHan(ev) || ev.code in IME_KEYS || ev.code === "CapsLock"
      || (logKey.plain = (logKey.plain || 0) + 1) <= 3) {
    ev._focus = focusInfo;
    if (!logKey.n || logKey.n < 400) {
      logKey.n = (logKey.n || 0) + 1;
      send({ t: "client", mode: "key", hw: `${ev.code}/${ev.key}/${ev.keyCode}/${ev._focus}`, format: state.jp ? "jp" : "en" });
    }
  }
}

// The dock's A / あ switches the Windows IME (the iPhone follows it).
async function toggleWindowsIme() {
  focusKeyboard();
  try { await api("/api/ime", { japanese: !state.jp }); } catch (e) { toast(e.message, true); }
}

window.addEventListener("focus", () => {
  // Back in this window: its PC-keyboard setting applies again (another
  // window may have changed the server's one meanwhile).
  if (state.status.state === "ready") send({ t: "pc_keyboard", on: state.kbd });
});

// Registering a hardware keyboard hides the iPhone's on-screen keyboard, so
// release it after a quiet spell; the next key registers it again.
let idleTimer = null;
function keyActivity() {
  clearTimeout(idleTimer);
  // The phone keeps its iCtrl keyboard for the whole session; nothing to release.
}
function releaseKeyboard() { send({ t: "release" }); }

async function pasteFromPc() {
  // Text is typed in directly; an image on the PC clipboard is sent to the
  // iPhone clipboard and pasted with Cmd+V.
  try {
    const r = await api("/api/clipboard/push", {});
    if (!r.ok) return toast(r.error, true);
    command({ t: "key", key: "v", modifiers: ["cmd"] });
  } catch (e) { toast(t("貼り付けできませんでした: ") + fromServer(e.message), true); }
}

async function copyToPc() {
  try { await api("/api/clipboard/pull", {}); } catch { /* nothing copied */ }
}

function isTyping() {
  if ($("dialog[open]")) return true;
  const el = document.activeElement;
  if (!el || el === sink) return false;
  return ["TEXTAREA", "INPUT", "SELECT"].includes(el.tagName) || el.isContentEditable;
}

function isZenHan(ev) {
  // 半角/全角 arrives as an IME key on JIS keyboards (often with no keyup).
  return ZENHAN.has(ev.key) || (ev.code === "Backquote" && ev.key === "Process");
}

window.addEventListener("keydown", (ev) => {
  if ($("dialog[open]")) return;
  if (ev.ctrlKey && !ev.shiftKey && !ev.altKey && ev.code === "KeyK") {
    ev.preventDefault();
    return setKbd(!state.kbd);
  }
  if (!state.kbd) {
    if (ev.ctrlKey && !ev.shiftKey && !ev.altKey && !isTyping()) {
      if (ev.code === "KeyB") { ev.preventDefault(); return togglePanel(); }
      if (ev.code === "KeyH") { ev.preventDefault(); return pressButton("home"); }
      if (ev.code === "KeyS") { ev.preventDefault(); return saveShot(); }
    }
    return;
  }
  if (state.status.state !== "ready" || isTyping()) return;
  keyActivity();
  logKey(ev);
  // IME keys switch the Windows IME; the server sees that and switches the
  // iPhone. They are not sent to the iPhone themselves.
  if (isZenHan(ev) || ev.code in IME_KEYS) {
    // Some apps hosting this page (the Claude app) raise no Windows notice
    // for their IME: tell the server to read it once.
    send({ t: "ime_key" });
    return;
  }
  // Windows-style Ctrl shortcuts -> Cmd on iOS; Ctrl+V pastes the PC clipboard.
  if (state.ctrlAsCmd && ev.ctrlKey && !ev.altKey && !ev.metaKey && CMD_SHORTCUTS.has(ev.code)) {
    ev.preventDefault();
    if (ev.repeat && ev.code !== "Backspace" && !ev.code.startsWith("Arrow")) return;
    if (ev.code === "KeyV" && !ev.shiftKey) return void pasteFromPc();
    if (ev.code === "KeyY") return void command({ t: "key", key: "z", modifiers: ["cmd", "shift"] });
    const mods = ["cmd"].concat(ev.shiftKey ? ["shift"] : []);
    const key = ev.code.startsWith("Key") ? ev.code.slice(3).toLowerCase()
      : ev.code === "Backspace" ? "backspace" : ev.code.replace("Arrow", "").toLowerCase();
    command({ t: "key", key, modifiers: mods });
    if ((ev.code === "KeyC" || ev.code === "KeyX") && state.status.clipboard_watch !== "on") setTimeout(copyToPc, 350);
    return;
  }
  const u = HID[ev.code];
  if (u === undefined) return;
  ev.preventDefault();
  if (state.ctrlAsCmd && CTRL.has(u)) {
    // Ctrl reaches the phone only when combined with a non-shortcut key.
    state.ctrlHeld = true;
    return;
  }
  if (state.ctrlHeld && ev.ctrlKey) state.keysDown.add(0xe0);
  if (isModifier(u)) {
    if (!state.keysDown.has(u)) { state.keysDown.add(u); syncKeys(); }
    return;
  }
  // Other keys go as a complete press on keydown (auto-repeat sends more).
  // With the Windows IME on, Chrome does not always deliver the keyup of a
  // key the IME processed; a key waiting for its keyup stayed "held", so
  // its next press was never sent (dropped characters).
  send({ t: "keys", usages: [...state.keysDown, u] });
  syncKeys();
});
function isModifier(u) { return u >= 0xe0 && u <= 0xe7; }
window.addEventListener("keyup", (ev) => {
  if (!state.kbd) return;
  const u = HID[ev.code];
  if (u === undefined) return;
  if (CTRL.has(u)) state.ctrlHeld = false;
  ev.preventDefault();
  if (!isModifier(u)) return;      // already released on keydown
  let changed = state.keysDown.delete(u);
  if (!ev.ctrlKey && state.keysDown.delete(0xe0)) changed = true;
  if (changed) syncKeys();
});
window.addEventListener("focus", () => { if (state.kbd && !isTyping()) focusKeyboard(); });
window.addEventListener("blur", () => {
  state.ctrlHeld = false;
  if (state.keysDown.size) { state.keysDown.clear(); syncKeys(); }
});

// ------------------------------------------------------------------ controls
let replyId = 0;
// Each command gets a reply (errors are toasted by the socket handler); the
// returned promise resolves to the reply's result, or null on an error.
const pendingReplies = new Map();
function command(obj) {
  const id = ++replyId;
  send({ ...obj, id });
  return new Promise((resolve) => pendingReplies.set(id, resolve));
}
function pressButton(name) {
  if (state.status.state !== "ready") return toast(t("先に iPhone に接続してください"), true);
  command({ t: "button", name });
}

$$("[data-button]").forEach((b) => b.addEventListener("click", () => pressButton(b.dataset.button)));
$("#lockBtn").addEventListener("click", async () => {
  if (state.status.state !== "ready") return toast(t("先に iPhone に接続してください"), true);
  const r = await command({ t: "lock" });
  if (r && r.already_locked) toast(t("iPhone はすでにロックされています"));
});
$("#kbdBtn").addEventListener("click", () => setKbd(!state.kbd));
$("#imeBtn").addEventListener("click", () => toggleWindowsIme());
renderKbd();
if (state.kbd) focusKeyboard();
$("#shotBtn").addEventListener("click", () => saveShot());

// Dock button descriptions above the dock. The text stays in `title` (code
// updates it, e.g. the video button); while hovered it is shown here instead
// of the browser's tooltip.
const dockTip = $("#dockTip");
function hideDockTip() {
  dockTip.hidden = true;
  const btn = hideDockTip.btn;
  if (btn && btn.dataset.tip !== undefined) {
    if (!btn.title) btn.title = btn.dataset.tip;
    delete btn.dataset.tip;
  }
  hideDockTip.btn = null;
}
$("#dock").addEventListener("pointerover", (ev) => {
  const btn = ev.target.closest(".dock-btn");
  if (!btn || btn === hideDockTip.btn) return;
  hideDockTip();
  const text = btn.title;
  if (!text) return;
  btn.dataset.tip = text;
  btn.removeAttribute("title");            // no second, browser tooltip
  btn.setAttribute("aria-label", text);
  hideDockTip.btn = btn;
  dockTip.textContent = text;
  dockTip.hidden = false;
  const b = btn.getBoundingClientRect(), dock = $("#dock").getBoundingClientRect();
  const half = dockTip.offsetWidth / 2;
  dockTip.style.left = `${Math.min(innerWidth - 8 - half, Math.max(8 + half, b.left + b.width / 2))}px`;
  dockTip.style.top = `${dock.top - 8 - dockTip.offsetHeight}px`;
});
$("#dock").addEventListener("pointerleave", hideDockTip);
$("#dock").addEventListener("click", hideDockTip);
$("#videoBtn").addEventListener("click", async () => {
  const paused = !!state.status.video_paused;
  try {
    await api(paused ? "/api/video/start" : "/api/video/stop", {});
    toast(paused ? t("画面を映しました") : t("画面を映すのをやめました"));
  } catch (e) { toast(e.message, true); }
});
$("#videoResume").addEventListener("click", async () => {
  try { await api("/api/video/start", {}); toast(t("画面を映しました")); }
  catch (e) { toast(e.message, true); }
});
async function stopAutomation() {
  try { await api("/api/automation/stop", {}); toast(t("iPhone の自動操作を終了しました")); }
  catch (e) { toast(e.message, true); }
}
$("#pairingBtn").addEventListener("click", async () => {
  toast(t("iPhone で「このコンピュータを信頼」→ パスコードを入力してください"));
  try { await api("/api/pairing", {}); toast(t("iCtrl 専用の信頼を作成しました。次の接続から使います")); }
  catch (e) { toast(e.message, true); }
});
$("#agentBadge").addEventListener("click", () => {
  // End the automation (WebDriverAgent, if running) and close the badge.
  agentState.running = 0;
  agentState.recent = false;
  clearTimeout(agentState.timer);
  renderAgentBadge();
  if (state.status.state === "ready") stopAutomation();
});
$("#automationStop").addEventListener("click", stopAutomation);

// ------------------------------------------------------------------ passcode
// Configuration stays in this window; the server handles pre-approved passcode entry.
state.passcode = { available: false, saved: false, automatic: {} };
state.passcodeBusy = false;
let passcodeRefresh = 0;
async function refreshPasscode() {
  const version = ++passcodeRefresh;
  let info;
  try { info = await api("/api/passcode"); }
  catch (_) { info = { ...state.passcode, error: true }; }
  if (version !== passcodeRefresh) return;
  state.passcode = info;
  renderPasscode();
}
function renderPasscode() {
  const info = state.passcode;
  if (!info) return;
  const automatic = info.automatic || {};
  const connected = state.status.state === "ready";
  const entering = connected && state.status.passcode_state === "entering";
  const canEnable = info.available && info.saved && connected && !info.error;
  $("#passcodeState").textContent = !info.available ? t("この環境では利用不可") : info.saved ? t("保存済み") : t("未保存");
  $("#passcodeEnter").hidden = !(info.available && info.saved && connected);
  $("#passcodeDelete").hidden = !(info.available && info.saved);
  $("#passcodeSaveRow").hidden = !(info.available && !info.saved);
  for (const id of ["#passcodeEnter", "#passcodeSave", "#passcodeDelete"]) {
    $(id).disabled = state.passcodeBusy || !!info.error;
  }
  const toggle = $("#passcodeAutomatic");
  toggle.checked = !!automatic.enabled;
  toggle.disabled = state.passcodeBusy || !!info.error || (!automatic.enabled && !canEnable);
  const reset = $("#passcodeAutomaticReset");
  reset.hidden = entering || !(automatic.blocked || (automatic.enabled && connected && !automatic.device_matches));
  reset.disabled = state.passcodeBusy || !canEnable;
  const status = $("#passcodeAutomaticState");
  status.dataset.state = "";
  if (info.error) status.textContent = t("設定を取得できません。iCtrl サーバーとの接続を確認してください。");
  else if (!info.saved) status.textContent = t("先にパスコードを保存してください。自動入力は未設定です。");
  else if (entering) status.textContent = t("パスコードを入力しています。確認が終わるまで追加の入力はしません。");
  else if (automatic.blocked) {
    status.dataset.state = "blocked";
    status.textContent = t("入力結果が未確認のため、自動入力は停止中です。iPhone の状態とパスコードを確認してから再有効化してください。");
  } else if (!connected) status.textContent = automatic.enabled
    ? t("許可した iPhone の接続を待っています。自動入力はいつでもオフにできます。")
    : t("自動入力はオフです。有効にする iPhone を接続してください。");
  else if (automatic.enabled && !automatic.device_matches) {
    status.dataset.state = "blocked";
    status.textContent = t("接続中の iPhone は許可した端末と違うため、入力しません。この端末で使う場合は再有効化してください。");
  } else if (automatic.enabled) {
    status.dataset.state = "enabled";
    status.textContent = t("この iPhone で有効です。WDA 起動時の XCTest 要求に自動入力します。");
  } else status.textContent = info.available
    ? t("自動入力はオフです。オンにすると、WDA 起動時の XCTest 要求に自動入力します。")
    : t("この環境では Windows 資格情報マネージャーを利用できません。");
}
// Serialise button actions locally; the server also refuses overlapping entry.
async function passcodeAction(path, body) {
  if (state.passcodeBusy) return false;
  state.passcodeBusy = true;
  renderPasscode();
  try { await api(path, body); return true; }
  catch (e) { toast(e.message, true); return false; }
  finally {
    state.passcodeBusy = false;
    renderPasscode();
  }
}
async function savePasscode(input) {
  const value = input.value;
  input.value = "";
  if (await passcodeAction("/api/passcode/save", { passcode: value })) toast(t("パスコードを保存しました"));
  refreshPasscode();
}
$("#passcodeSave").addEventListener("click", () => savePasscode($("#passcodeInput")));
$("#passcodeDelete").addEventListener("click", async () => {
  if (await passcodeAction("/api/passcode/delete", {})) toast(t("削除しました"));
  refreshPasscode();
});
async function configureAutomatic(enabled) {
  if (state.passcodeBusy) return;
  const ok = await passcodeAction("/api/passcode/automatic", { enabled });
  await refreshPasscode();
  if (ok) toast(enabled ? t("この iPhone の XCTest 自動入力を有効にしました") : t("自動入力をオフにしました"));
}
$("#passcodeAutomatic").addEventListener("change", (event) => configureAutomatic(event.target.checked));
$("#passcodeAutomaticReset").addEventListener("click", () => configureAutomatic(true));
async function enterPasscode() {
  if (state.passcodeBusy) return;
  if (state.status.state !== "ready") return toast(t("先に iPhone に接続してください"), true);
  if (!state.passcode.saved) return toast(t("先にパスコードを保存してください"), true);
  if (await passcodeAction("/api/passcode/enter", {})) toast(t("パスコードを入力しました"));
}
$("#passcodeEnter").addEventListener("click", enterPasscode);
refreshPasscode();

async function saveShot() {
  if (state.status.state !== "ready") return toast(t("先に iPhone に接続してください"), true);
  if (saveShot.busy) return;
  saveShot.busy = true;
  try {
    const saved = await api("/api/screenshot/save", { rotation: state.rotation });
    // Like a camera shutter: a white flash over the phone, then the file name.
    const flash = $("#shotFlash");
    const r = device.getBoundingClientRect();
    Object.assign(flash.style, { left: `${r.left}px`, top: `${r.top}px`, width: `${r.width}px`, height: `${r.height}px`,
                                 borderRadius: getComputedStyle(device).borderRadius });
    flash.classList.remove("go"); void flash.offsetWidth; flash.classList.add("go");
    toast(t("スクリーンショットを保存しました"), false, {
      label: t("フォルダーを開く"),
      image: `/api/screenshot/last?name=${encodeURIComponent(saved.name)}`,
      detail: `${saved.name} · ${saved.folder.split("\\").pop()}${saved.copied ? t(" · コピー済み") : ""}`,
      run: () => api("/api/screenshot/reveal", {}).catch((e) => toast(e.message, true)),
    });
  } catch (e) { toast(e.message, true); }
  finally { saveShot.busy = false; }
}

// Where screenshots are saved (Pictures\iCtrl unless the user picked a folder).
function renderShotFolder(info) {
  const box = $("#shotFolder");
  box.textContent = info.path;
  box.title = info.path;
  $("#shotFolderReset").hidden = !info.custom;
}
async function loadShotFolder() {
  try { renderShotFolder(await api("/api/screenshot/folder")); }
  catch (e) { $("#shotFolder").textContent = t("取得できませんでした"); }
}
async function changeShotFolder(action) {
  try {
    const info = await api("/api/screenshot/folder", { action });
    renderShotFolder(info);
    if (!info.cancelled) toast(action === "reset" ? t("保存先を既定に戻しました") : t("保存先を変更しました"));
  } catch (e) { toast(e.message, true); }
}
$("#shotFolderPick").addEventListener("click", () => changeShotFolder("pick"));
$("#shotFolderReset").addEventListener("click", () => changeShotFolder("reset"));
$("#shotFolderOpen").addEventListener("click", () => api("/api/screenshot/reveal", {}).catch((e) => toast(e.message, true)));
loadShotFolder();

// connect / disconnect
function setTransport(t) {
  state.transport = t;
  localStorage.setItem("ictrl.transport", t);
  $$("#quickTransport button").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.transport === t)));
  $("#quickTransport").classList.toggle("wifi", t === "wifi");
}
try { localStorage.removeItem("ictrl.wifiHost"); } catch (_) {}   // retired: connecting from outside
// Whether the iPhone can be reached over t now, looked up at the click the
// way the connect itself finds it (a cable may have been plugged in or out).
// Returns null when it can, else the reason.
async function unreachable(t) {
  const udid = $("#deviceSelect").value || state.status.udid || "";
  try {
    const r = await api(`/api/reachable?transport=${t}&udid=${encodeURIComponent(udid)}`);
    return r.ok ? null : r.reason;
  } catch (_) { return null; }   // cannot tell: the connect itself reports the error
}
function refuse(t, reason) {
  const b = $(`#quickTransport button[data-transport="${t}"]`);
  b.classList.remove("refused");
  void b.offsetWidth;
  b.classList.add("refused");
  toast(reason, true);
}
async function chooseTransport(t) {
  const s = state.status.state;
  const connected = s === "ready" || s === "connecting";
  const current = connected ? state.status.transport : state.transport;
  if (t === current) return;
  // Nothing changes (the connection, the highlight) when t cannot be used.
  const reason = await unreachable(t);
  if (reason) return refuse(t, reason);
  if (connected && state.status.transport && state.status.transport !== t) {
    // Switch over: close the current connection, then connect over the other.
    try { await api("/api/disconnect", {}); } catch (e) { return toast(e.message, true); }
    return doConnect(t);
  }
  setTransport(t);
}
$$("#quickTransport button").forEach((b) => b.addEventListener("click", () => chooseTransport(b.dataset.transport)));

async function doConnect(transport) {
  setTransport(transport);
  const udid = $("#deviceSelect").value || undefined;
  try {
    await api("/api/connect", { transport, udid });
  } catch (e) {
    toast(e.message, true);
  }
}
$$("[data-connect]").forEach((b) => b.addEventListener("click", () => doConnect(b.dataset.connect)));
async function toggleConnection() {
  const s = state.status.state;
  if (s === "ready" || s === "connecting") {
    try { await api("/api/disconnect", {}); } catch (e) { toast(e.message, true); }
  } else {
    doConnect(state.transport);
  }
}
$("#quickConnect").addEventListener("click", toggleConnection);

async function loadDevices() {
  const sel = $("#deviceSelect");
  try {
    const { devices } = await api("/api/devices");
    state.devices = devices;
    sel.innerHTML = devices.length ? "" : `<option value="">${t("iPhone が見つかりません")}</option>`;
    for (const d of devices) {
      const o = document.createElement("option");
      o.value = d.udid;
      const via = d.transports.map((t) => (t === "usb" ? "USB" : "Wi-Fi")).join(" / ");
      o.textContent = `${d.name || "iPhone"} — ${via}`;
      sel.appendChild(o);
    }
    if (devices[0] && !devices[0].transports.includes(state.transport)) setTransport(devices[0].transports[0]);
  } catch (e) {
    sel.innerHTML = `<option value="">${t("取得できません")}</option>`;
    toast(e.message, true);
  }
}
$("#refreshBtn").addEventListener("click", loadDevices);
$("#deviceSelect").addEventListener("change", () => {
  const d = state.devices.find((x) => x.udid === $("#deviceSelect").value);
  if (d && !d.transports.includes(state.transport)) setTransport(d.transports[0]);
});

// text

// clipboard: iPhone copies reach the PC by themselves (server-side, even
// when this window is in the background); history keeps text and images.
const clipList = $("#clips");
function applyClipboardSettings(settings) {
  if ("auto_copy_to_pc" in settings) $("#autoCopy").checked = !!settings.auto_copy_to_pc;
  renderClipboardMode();
}
// ictrlcopy runs on the virtual keyboard's connection (the only method).
function renderClipboardMode() {
  $("#shKeyboardStep").hidden = false;
  $("#shTest").disabled = state.status.state !== "ready";
}
function clipRow(item, fresh = false) {
  const li = document.createElement("li");
  li.dataset.id = item.id;
  if (fresh) li.classList.add("fresh");
  const when = new Date(item.time * 1000).toLocaleTimeString(LANG === "en" ? "en-US" : "ja-JP", { hour: "2-digit", minute: "2-digit" });
  li.innerHTML = `<span class="src"></span><div class="body"></div><div class="act">
    <button data-a="to-pc" title="${t("PC にコピー")}">PC</button><button data-a="to-iphone" title="${t("iPhone にコピー")}">📱</button></div>`;
  li.querySelector(".src").textContent = item.source === "iphone" ? "📱→" : "PC→";
  const body = li.querySelector(".body");
  if (item.kind === "image") {
    body.innerHTML = `<img alt=""><div class="m"></div>`;
    body.querySelector("img").src = item.thumb;
    body.querySelector(".m").textContent = `${t("画像")} ${item.width}×${item.height} · ${when}`;
  } else {
    body.innerHTML = `<div class="t"></div><div class="m"></div>`;
    body.querySelector(".t").textContent = item.text;
    body.querySelector(".m").textContent = t("{n} 文字", { n: item.chars }) + ` · ${when}`;
  }
  li.addEventListener("click", async (ev) => {
    const action = ev.target.dataset.a || "to-pc";
    try {
      await api(`/api/clipboard/item/${item.id}/${action}`, {});
      toast(action === "to-pc" ? t("PC にコピーしました") : t("iPhone にコピーしました"));
    } catch (e) { toast(e.message, true); }
  });
  return li;
}
function addClip(item, fresh) {
  clipList.querySelector(".muted")?.remove();
  clipList.querySelector(`[data-id="${item.id}"]`)?.remove();
  clipList.prepend(clipRow(item, fresh));
  while (clipList.children.length > 30) clipList.lastElementChild.remove();
}
async function loadClips() {
  try {
    const data = await api("/api/clipboard/history");
    const { items } = data;
    applyClipboardSettings({ clipboard_shortcut_mode: "keyboard", ...data });
    if (items.length) { clipList.innerHTML = ""; items.slice().reverse().forEach((i) => addClip(i, false)); }
  } catch { /* server not ready */ }
}
function onClipboardEvent(msg) {
  addClip(msg.item, true);
  if (msg.item.source === "iphone") {
    const what = msg.item.kind === "image" ? t("画像") : t("テキスト");
    if (msg.copied_to_pc) toast(t("コピーしました"));
  } else if (msg.to_iphone) {
    toast(t("iPhone にコピーしました"));
  }
}
// WebDriverAgent mode: on demand / off (OCR only).
const WDA_MODE_HELP = {
  on_demand: "AI が要素を読むときに起動し、作業が終わると止めます。起動のたびに iPhone がパスコードを求めることがあります。",
  off: "WebDriverAgent を使わず、画面の文字を Windows の文字認識で読みます。パスコード要求は出ませんが、遅く、文字のないアイコンは読めません。",
};
function applyWdaMode(settings) {
  if (!("wda_mode" in settings) && settings.t === "settings") return;
  const mode = settings.wda_mode || "on_demand";
  $("#wdaMode").value = mode;
  $("#wdaModeHelp").textContent = t(WDA_MODE_HELP[mode] || "");
}
$("#wdaMode").addEventListener("change", async (ev) => {
  try { applyWdaMode(await api("/api/settings", { wda_mode: ev.target.value })); }
  catch (e) { toast(e.message, true); }
});
api("/api/settings").then(applyWdaMode).catch(() => {});

// Screen video at connect: off by default.
function applyVideoOnConnect(settings) {
  if ("video_on_connect" in settings) $("#videoOnConnect").checked = !!settings.video_on_connect;
}
$("#videoOnConnect").addEventListener("change", async (ev) => {
  try { applyVideoOnConnect(await api("/api/settings", { video_on_connect: ev.target.checked })); }
  catch (e) { toast(e.message, true); }
});
api("/api/settings").then(applyVideoOnConnect).catch(() => {});

// The dock's A / あ (switches the Windows IME): shown only when turned on.
function applyShowImeButton(settings) {
  if (!("show_ime_button" in settings)) return;
  const show = settings.show_ime_button === true;
  $("#showImeButton").checked = show;
  $("#imeBtn").hidden = !show;
}
$("#showImeButton").addEventListener("change", async (ev) => {
  try { applyShowImeButton(await api("/api/settings", { show_ime_button: ev.target.checked })); }
  catch (e) { toast(e.message, true); }
});
api("/api/settings").then(applyShowImeButton).catch(() => {});

// "Keep the connection even when unused (not recommended)": on turns the
// automatic disconnects (auto_disconnect) off.
function applyAutoDisconnect(settings) {
  if ("auto_disconnect" in settings) $("#autoDisconnect").checked = settings.auto_disconnect === false;
}
$("#autoDisconnect").addEventListener("change", async (ev) => {
  try { applyAutoDisconnect(await api("/api/settings", { auto_disconnect: !ev.target.checked })); }
  catch (e) { toast(e.message, true); }
});
api("/api/settings").then(applyAutoDisconnect).catch(() => {});

// Display language (i18n.js): "auto" follows the browser. A change, here or
// in another window, relabels the page in place: no reload, so the video,
// the connection and the open menu stay as they are.
$("#language").value = SETTING;
function applyLanguage(settings) {
  if (!("language" in settings)) return;
  $("#language").value = settings.language;
  if (!adopt(settings.language)) return;
  translatePage();
  renderStatus();
  renderKbd();
  renderAgentBadge();
  renderApps();
  applyWdaMode({ wda_mode: $("#wdaMode").value });
  refreshPasscode();
  loadDevices();
  loadClips();
}
$("#language").addEventListener("change", async (ev) => {
  try { applyLanguage(await api("/api/settings", { language: ev.target.value })); }
  catch (e) { toast(e.message, true); }
});
api("/api/settings").then(applyLanguage).catch(() => {});

$("#autoCopy").addEventListener("change", async (ev) => {
  try { await api("/api/settings", { auto_copy_to_pc: ev.target.checked }); }
  catch (e) { toast(e.message, true); }
});
$("#clipPull").addEventListener("click", async () => {
  try {
    const r = await api("/api/clipboard/pull", {});
    if (!r.ok) return toast(r.error, true);
    toast(r.image ? t("iPhone の画像を PC にコピーしました") : t("iPhone のテキストを PC にコピーしました"));
  } catch (e) { toast(e.message, true); }
});

$("#shClose").addEventListener("click", () => { $("#shortcutHelp").hidden = true; focusKeyboard(); });
// After adding the shortcut: run ictrlcopy once (keyboard connection).
$("#shTest").addEventListener("click", async () => {
  $("#shortcutHelp").hidden = true;
  if (state.status.state !== "ready") return toast(t("先に iPhone に接続してください"), true);
  try {
    const r = await api("/api/clipboard/plain", {});
    if (r.ok === false) throw new Error(r.error || t("ショートカットを呼び出せませんでした"));
    toast(t("ictrlcopy の実行を要求しました。iPhone の表示とコピー結果を確認してください"));
  } catch (e) { toast(e.message, true); }
});

// apps
function renderApps() {
  const q = $("#appSearch").value.trim().toLowerCase();
  const list = $("#apps");
  const apps = state.apps.filter((a) => !q || a.name.toLowerCase().includes(q) || a.bundle_id.toLowerCase().includes(q));
  list.innerHTML = apps.length ? "" : `<li class="muted">${state.apps.length ? t("該当なし") : t("接続後に「読み込む」")}</li>`;
  for (const a of apps.slice(0, 300)) {
    const li = document.createElement("li");
    li.innerHTML = `<span class="n"></span><span class="b"></span>`;
    li.querySelector(".n").textContent = a.name;
    li.querySelector(".b").textContent = a.bundle_id;
    li.title = t("クリックで起動");
    li.addEventListener("click", async () => {
      try { await api("/api/launch", { bundle_id: a.bundle_id }); toast(t("{name} を起動しました", { name: a.name })); }
      catch (e) { toast(e.message, true); }
    });
    list.appendChild(li);
  }
}
$("#appSearch").addEventListener("input", renderApps);
$("#loadApps").addEventListener("click", async () => {
  const btn = $("#loadApps");
  btn.textContent = t("読み込み中…");
  try { state.apps = (await api("/api/apps")).apps; renderApps(); }
  catch (e) { toast(e.message, true); }
  finally { btn.textContent = t("再読み込み"); }
});

// MCP setup snippets
$$("[data-copy]").forEach((b) => b.addEventListener("click", async () => {
  const { python, root } = await api("/api/setup");
  const text = b.dataset.copy === "claude"
    ? `claude mcp add --scope user ictrl -- "${python}" -m ictrl mcp`
    : `[mcp_servers.ictrl]\ncommand = ${JSON.stringify(python)}\nargs = ["-m", "ictrl", "mcp"]\ncwd = ${JSON.stringify(root)}`;
  await navigator.clipboard.writeText(text);
  toast(b.dataset.copy === "claude" ? t("コマンドをコピーしました。ターミナルに貼り付けて実行してください")
    : t("~/.codex/config.toml に貼り付けてください"));
}));

// agent activity
// The badge is shown for exactly as long as an agent (MCP, script) has a
// request running, plus a short linger so quick taps stay readable. The
// server reports each request's start and end ("phase").
const agentState = { running: 0, timer: null, recent: false };
// One badge, always "AI が操作中": shown while an agent request runs (plus a
// short linger) and for as long as iOS UI automation (WebDriverAgent) is on;
// then it has a ✕ that ends the automation. The current action is in the
// tooltip.
function renderAgentBadge() {
  const badge = $("#agentBadge");
  const automation = !!(state.status.automation && state.status.state === "ready");
  badge.hidden = !(agentState.running || agentState.recent || automation);
  // In a narrow top bar the name "iCtrl" makes room for the badge.
  $(".topbar").classList.toggle("agent", !badge.hidden);
  // The ✕ shows whenever the badge does (it was missing while an agent
  // worked without WebDriverAgent, or before it had started).
  badge.classList.add("can-stop");
  $("#agentStop").hidden = false;
  if (automation) badge.title = t("iPhone の UI 自動操作（Automation Running）が有効です。クリックで終了");
}
function showAgent(msg) {
  const labels = { tap: t("タップ"), swipe: t("スワイプ"), button: t("ボタン操作"), text: t("文字入力"),
    key: t("キー入力"), keys: t("キー入力"), touch: t("タッチ操作"), launch: t("アプリを起動"),
    screenshot: t("画面を確認"), apps: t("アプリ一覧を取得"), clipboard: t("クリップボードを操作"),
    connect: t("接続を操作"), elements: t("画面の要素を確認") };
  // The server's count, not a tally of start/end notices: a page that reads
  // slowly (hidden) has its oldest notices dropped by the server's queue,
  // and a missed "end" kept the badge on after the agent had stopped.
  if (typeof msg.running === "number") agentState.running = msg.running;
  if (msg.op) $("#agentBadge").title = "AI: " + (labels[msg.op] || t("操作"));
  clearTimeout(agentState.timer);
  agentState.recent = true;
  if (!agentState.running) agentState.timer = setTimeout(() => { agentState.recent = false; renderAgentBadge(); }, 1500);
  renderAgentBadge();
  if (msg.op === "tap" && msg.x !== undefined) {
    const d = dot({ x: msg.x, y: msg.y }, "agent");
    setTimeout(() => fade(d), 260);
  } else if (msg.op === "swipe" && msg.x1 !== undefined) {
    const d = dot({ x: msg.x1, y: msg.y1 }, "agent");
    d.style.transition = `left ${msg.duration}s ease-in-out, top ${msg.duration}s ease-in-out, opacity .3s, transform .12s`;
    requestAnimationFrame(() => { d.style.left = msg.x2 * 100 + "%"; d.style.top = msg.y2 * 100 + "%"; });
    setTimeout(() => fade(d), msg.duration * 1000 + 100);
  }
}

// panel
// The menu (detailed settings) slides in over the phone; a click outside it
// (on the dimmed backdrop) closes it without reaching the phone.
function setMenu(open) {
  $(".workspace").classList.toggle("panel-open", open);
  $("#panelToggle").setAttribute("aria-expanded", String(open));
  if (open) refreshPasscode();
}
function togglePanel() { setMenu(!$(".workspace").classList.contains("panel-open")); }
$("#panelToggle").addEventListener("click", togglePanel);
$("#menuBackdrop").addEventListener("pointerdown", (ev) => { ev.preventDefault(); setMenu(false); });

// boot
// The app window is WebView2 (window/iCtrlWindow.cs); Chrome works too. The
// video is HEVC decoded by WebCodecs on the GPU.
if (typeof VideoDecoder === "undefined") toast(t("この画面では映像を再生できません（WebCodecs に対応したブラウザーが必要です）"), true);
setTransport(state.transport);
renderStatus();
connectSocket();
loadDevices();
loadClips();
