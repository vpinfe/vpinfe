//wrappers for the page's own log methods, to send them to the Python terminal
const originalConsole = {
  log: console.log,
  info: console.info,
  warn: console.warn,
  error: console.error,
  debug: console.debug,
};

// A media kind to the payload field that carries it. Kind names are snake_case, the
// same strings /api/v1 and vpin.getMedia() take.
// Keyed by canonical kind; the field names are contract 1's payload and are frozen.
const MEDIA_PATH_FIELDS = {
  playfield: "PlayfieldImagePath",
  playfield_fss: "FSSImagePath",
  backglass: "BGImagePath",
  scoreview: "DMDImagePath",
  wheel: "WheelImagePath",
  cab: "CabImagePath",
  real_dmd: "realDMDImagePath",
  real_dmd_color: "realDMDColorImagePath",
  flyer: "FlyerImagePath",
  instruction_card: "InstructionCardImagePath",
  topper: "TopperPath",
  logo: "LogoImagePath",
};

const MEDIA_VIDEO_PATH_FIELDS = {
  playfield: "PlayfieldVideoPath",
  backglass: "BGVideoPath",
  scoreview: "DMDVideoPath",
  loading: "LoadingVideoPath",
};

// Canonical kind names. Everything that reads this does so after normalization, so
// contract 1's `bg` and `dmd` arrive here already translated - keyed the old way, the
// lookup missed and the whole image-or-video preference was dead for those two.
const DEFAULT_MEDIA_PRIORITIES = {
  playfield: "video",
  backglass: "video",
  scoreview: "video",
  real_dmd: "color",
};


// What core does on a theme's behalf. One entry per behavior, with its default stated
// here rather than implied by a constructor - the audio default used to be `true` at
// construction and `false` after init, and nothing said which was meant.
//
// `config` names the config.json keys that turn it on or off, most specific first. The
// extra spellings are contract 1's: they accumulated because nothing declared the real
// one, and they stay only for themes already using them.
const CAPABILITIES = {
  // Contract 1 only, and it retires with contract 1. 2.x core pages for the theme
  // (_corePagingEnabled defaults true there) and 2.x core does *not* move the selection -
  // it hands previous/next to the theme's handler. Those are opposite defaults, so below
  // contract 2 the two behaviors need separate knobs to preserve 2.x.
  //
  // At contract 2 they are one thing: core_navigation owns all four actions and this is
  // not consulted, so a 3.0 theme sees one capability for one concern and a 2.x theme
  // keeps the two behaviors it was written against.
  core_paging: {
    default: true,
    // Declarable, so a theme that pages for itself can say so. It had no key at all,
    // which left carousel-desktop's own page-jump cases losing to core with no way to
    // opt out short of calling enableCorePaging(false) in JavaScript.
    config: ["paging.enabled"],
    describe: "Core handles the paging actions itself. Contract 1 only.",
  },
  core_navigation: {
    // On only for a theme that says it needs 3.0. One that still runs on 2.x drives its
    // own cursor: Revolution, Trinidad and carousel-desktop use previous/next to move
    // their own collection list, and #dispatchAction reaches #shouldHandleCoreNavigation
    // before #triggerInputAction with an else-if between them - so core consumes the
    // press, the theme's handler never runs, and the broadcast behind it reads to those
    // themes as "the user picked a game". Their picker exits onto an unrelated game.
    //
    // `navigation.enabled: true` still turns it on for a theme that has not moved yet.
    default: false,
    defaultFromContract: 2,
    config: ["navigation.enabled"],
    // At contract 2 this owns paging too, so the key that used to name paging alone turns
    // the whole of it off. A theme that said "I page for myself" still gets what it asked.
    legacyConfig: ["paging.enabled"],
    describe: "Core moves the selection - by a step or by a page - wraps it, and "
            + "announces where it went.",
  },
  core_audio: {
    // On from contract 2. A contract 1 theme plays its own audio, and a second source
    // would double it.
    default: false,
    defaultFromContract: 2,
    config: ["use_core_audio", "audio.use_core_audio", "audio.enabled"],
    legacyConfig: ["useCoreAudio", "audio.useCoreAudio"],
    describe: "Core plays, fades and mutes per-game audio.",
  },
  // On from contract 2. A contract 1 theme already lays itself out and must ask before
  // core starts setting attributes its CSS may fight.
  core_layout: {
    default: false,
    defaultFromContract: 2,
    config: ["layout.enabled"],
    describe: "Core turns the UI and the playfield art to suit the cabinet.",
  },
  core_launch_dim: {
    // On by default. VPX is about to own every screen, so every window should step
    // aside - and a theme cannot do this for itself on more than one, because its
    // script only runs in the controller.
    default: true,
    config: ["launch_dim.enabled"],
    describe: "Core dims each window while a game is launching.",
  },
  core_media_window: {
    // On by default. A media kind is named after the display it captures, so a window
    // that is a display has exactly one obvious thing to show, and every theme wrote the
    // same few lines to show it. A theme that wants something else on a secondary window
    // sets `media_window.enabled` false, or simply omits the element core renders into.
    default: true,
    config: ["media_window.enabled"],
    describe: "Core shows each display window the media named for it.",
  },
  core_waiting: {
    default: true,
    config: ["waiting.enabled"],
    describe: "Core covers each window with what an empty library is waiting for.",
  },
  core_preload: {
    // On from contract 2. A contract 1 theme preloads for itself, and turning this on
    // without deleting that loop just doubles the requests.
    default: false,
    defaultFromContract: 2,
    // One spelling. A capability's settings live in a block named after it, and
    // `enabled` is one of them - which is also the only shape `kinds` can travel in.
    config: ["preload.enabled"],
    describe: "Core fetches the media on either side of the selection once it settles.",
  },
  core_launch_indicator: {
    // On from contract 2. A contract 1 theme draws its own overlay on TableLaunching and
    // a second one would sit on top of it. Not named "loading": that is a media kind, a
    // game's own loading video.
    default: false,
    defaultFromContract: 2,
    config: ["launch_indicator.enabled"],
    describe: "Core draws a spinner in each window from the moment a game starts "
            + "launching until it is running.",
  },
  core_attract: {
    // On from contract 2. A contract 1 theme may run its own idle timer, which core cannot
    // intercept, so two of them would both move the wheel. The person's own switch
    // (`behavior.attract_enabled`) is read beside this one and either being off is off.
    default: false,
    defaultFromContract: 2,
    config: ["attract.enabled"],
    describe: "Core moves the selection on its own once nobody has pressed anything, "
            + "and puts it back when someone does.",
  },
  core_hold_ring: {
    // On by default: a hold that shows nothing is a key that appears to do nothing for
    // a second and a half. Styled by `.vpinfe-hold`, which a theme restyles; this is for
    // one that draws its own.
    default: true,
    config: ["hold_ring.enabled"],
    describe: "Core draws a ring while a key or button with a hold is kept down.",
  },
  core_scrub: {
    default: true,
    config: ["scrub.enabled"],
    describe: "After about a second of holding, core leaves every screen and side effect "
            + "alone, and brings them up to date once the wheel has stopped.",
  },
};

// Read a possibly-dotted key out of a theme's config.
function configValue(config, key) {
  return key.split(".").reduce((at, part) =>
    (at && typeof at === "object") ? at[part] : undefined, config);
}

// Only movement repeats when it is held. Holding select must not launch twice and
// holding exit must not ask to quit twice - a hold means "keep going", and going is
// something only these four do.
const REPEATING_ACTIONS = new Set(
  ["previous", "next", "page_previous", "page_next"]);

// A held one of these repeats at a flat `pageRepeatMs`, with no ramp.
const PAGING_ACTIONS = new Set(["page_previous", "page_next"]);

// What the page still answers while a table runs, and only by handing it to core, which
// knows whether the table is frozen. `IN_PLAY` in common/config/input_registry.py.
const PLAY_ACTIONS = new Set(["take_picture", "back"]);

const MISSING_MEDIA_URL = "/core/images/file_missing.png";

// The contract a theme declares in its manifest. 1 is what declaring nothing gets, and
// what this file assumes until the bridge answers: the compatibility layer below is
// installed up front and taken away again at contract 2, so a theme that touches vpin.*
// before the bridge is up still finds its names.
const WAITING_POLL_MS = 5000;
const OLDEST_CONTRACT = 1;
const CURRENT_CONTRACT = 2;


// How to read one item of the payload at contract 2. An entry is a table with its game
// attached: it names the media kinds it has rather than locating them, so a URL is the
// table's id and the kind.
class ContractTwoReader {
  constructor(core) { this.core = core; }

  has(entry, kind) { return Array.isArray(entry.media) && entry.media.includes(kind); }

  url(entry, kind) {
    if (!this.has(entry, kind)) return null;
    // A folder no metadata build has touched has no table id yet, but it still has art
    // and still appears in the wheel. The route accepts either id.
    const id = String(entry.table?.id || entry.game?.id || "");
    if (!id) return null;
    return `${this.core.endpoints.files}/media/${encodeURIComponent(id)}/${kind}`;
  }

  imageURL(entry, kind) { return this.url(entry, kind) || MISSING_MEDIA_URL; }
  // Page 1 is the kind's own URL, so a page-aware theme and a plain one share a cache entry.
  pageURLs(entry, kind) {
    const first = this.url(entry, kind);
    if (!first) return [];
    const count = Math.max(1, Number(entry.pages?.[kind]) || 1);
    return Array.from({ length: count }, (_, n) => (n === 0 ? first : `${first}?page=${n + 1}`));
  }
  // Contract 2 names kinds; it never locates files, so there is no path to hand back.
  path()                { return null; }
  videoURL(entry, kind) {
    const named = VIDEO_KIND[kind] || kind;
    return (VIDEO_KINDS.has(named) && this.url(entry, named)) || MISSING_MEDIA_URL;
  }
  audioURL(entry)       { return this.url(entry, "audio"); }
  logo(entry)           { return entry.game?.manufacturer_logo || null; }
  vpsId(entry)          { return String(entry.game?.vps_id || "").trim(); }
  // The table, not the game: a row is a table, and the collection chose which one.
  identity(entry)       { return String(entry.table?.id || entry.game?.id || ""); }

  imageVideo(entry, kind, priority) {
    const order = priority === "image" ? ["image", "video"] : ["video", "image"];
    for (const want of order) {
      const url = want === "video"
        ? this.url(entry, VIDEO_KIND[kind] || `${kind}_video`)
        // The playfield falls back to its own FSS variant, which is why the two are
        // named as a pair rather than as unrelated kinds.
        : (this.url(entry, kind)
           || (kind === "playfield" ? this.url(entry, "playfield_fss") : null));
      if (url) return { url, kind: want, priority, path: null };
    }
    return { url: MISSING_MEDIA_URL, kind: "missing", priority, path: null };
  }

  realDmd(entry, priority) {
    const order = priority === "standard"
      ? [["standard", "real_dmd"], ["color", "real_dmd_color"]]
      : [["color", "real_dmd_color"], ["standard", "real_dmd"]];
    for (const [variant, kind] of order) {
      const url = this.url(entry, kind);
      if (url) return { url, kind: "image", priority, variant, path: null };
    }
    return { url: MISSING_MEDIA_URL, kind: "missing", priority, variant: "missing", path: null };
  }
}

const VIDEO_KIND = { playfield: "playfield_video", backglass: "backglass_video",
                     scoreview: "scoreview_video", topper: "topper_video",
                     loading: "loading" };
// A video is asked for by name: a kind that is an image (`wheel`) has no video to answer with.
const VIDEO_KINDS = new Set([...Object.values(VIDEO_KIND),
                             "real_dmd_video", "real_dmd_color_video"]);


// ─────────────────────────────────────────────────────────────────────────────
//  CONTRACT 1 COMPATIBILITY
//
//  Everything a theme written before 3.0 depends on, in one place: the vpin.*
//  names it reads, the media kinds it asks for, the payload keys it expects and
//  the window messages it listens for. All of it is keyed on the 2.x spellings,
//  so a theme author arriving from 2.x finds their name here.
//
//  None of it is reachable at contract 2 - #applyContract takes it away. When
//  contract 1 is retired, this whole block goes with it and nothing else moves.
// ─────────────────────────────────────────────────────────────────────────────

// The action names a contract 1 theme's handleInput switches on. Core dispatches the
// current names; a theme that declared nothing is handed these instead, so every
// published `case "joyleft"` keeps matching. PAR-40.
const LEGACY_ACTION_NAMES = {
  previous: "joyleft",
  next: "joyright",
  page_previous: "joypageup",
  page_next: "joypagedown",
  select: "joyselect",
  back: "joyback",
  menu: "joymenu",
  collection_menu: "joycollectionmenu",
  tutorial: "joytutorial",
  exit: "joyexit",
};

// Pre-3.0 names, aliased rather than removed: the payload behind each is identical, so a
// theme written against an earlier build keeps working. The contract projects the payload,
// never this surface, so aliasing is the only mechanism there is. PAR-23.
//
// Only the screen pair is left. The selection members went back to the Table* spelling
// 2.x published, so there is nothing for them to forward to - and an entry mapping a
// name to itself would install an accessor that reads itself.
const VPINFE_RENAMED_MEMBERS = {
  tableRotation: 'playfieldRotation',
  tableOrientation: 'playfieldOrientation',
};

// Every media kind VPinFE serves, in common/media/media_specs.py order. Kept in step by
// tests/theming/test_media_kinds.py, because a name that drifts here fails silently:
// a lookup for a kind nobody serves returns nothing rather than complaining.
const MEDIA_KINDS = [
  "backglass", "scoreview", "playfield", "playfield_fss", "wheel", "cab",
  "real_dmd", "real_dmd_color", "real_dmd_video", "real_dmd_color_video", "flyer",
  "playfield_video", "backglass_video", "scoreview_video", "audio", "instruction_card",
  "topper", "topper_video", "loading", "audio_launch", "rule_sheet", "logo",
];

// Kind names earlier builds accepted, against the canonical snake_case set.
// real_dmd_color is the odd one: at contract 1 both frames collapse onto real_dmd, the
// way 2.x addressed them, while contract 2 keeps it separately addressable.
const MEDIA_KIND_ALIASES = {
  table: "playfield",
  table_video: "playfield_video",
  fss: "playfield_fss",
  bg: "backglass",
  bg_video: "backglass_video",
  dmd: "scoreview",
  dmd_video: "scoreview_video",
  realdmd: "real_dmd",
  "realdmd-color": "real_dmd",
  realdmd_color: "real_dmd",
  real_dmd_color: "real_dmd",
  rulecard: "instruction_card",
  audiolaunch: "audio_launch",
  rulesheet: "rule_sheet",
};

// This file is served to themes of every contract, so it cannot assume which spelling
// the payload carries: contract 1 receives TableImagePath, contract 2 PlayfieldImagePath.
// Look up the current key, fall back to the one the projection sends. PAR-22.
const MEDIA_FIELD_FALLBACK = {
  PlayfieldImagePath: "TableImagePath",
  PlayfieldVideoPath: "TableVideoPath",
};

// Window message types are a theme-facing contract: a theme both listens for these and
// posts them, so at contract 1 every broadcast goes out under both names, and a receipt
// is normalized at either contract. PAR-24.
//
// Empty since the selection surface went back to the Table* spelling 2.x published:
// there is nothing left to translate. The machinery stays because it is what the next
// message rename uses - see VPINFE_RENAMED_MEMBERS, which is still carrying two.
const MESSAGE_TYPE_ALIASES = {};

const MESSAGE_TYPE_CANONICAL = Object.fromEntries(
  Object.entries(MESSAGE_TYPE_ALIASES).map(([current, legacy]) => [legacy, current]),
);


// `chord(key:ShiftLeft+key:ShiftRight)@hold:1500` -> what dispatch needs, or null for
// anything that is not a chord.
//
// Members are device selectors. Every other selector in the grammar names a device, and
// a chord of *actions* would resolve through another action's bindings - so rebinding one
// action would silently change what a chord on a different action means.
function parseChordBinding(action, binding) {
  if (typeof binding !== "string") return null;
  if (!binding.startsWith("chord(") || !binding.includes(")")) return null;
  const inside = binding.slice("chord(".length, binding.lastIndexOf(")"));
  const members = [...new Set(inside.split("+").map(one => downToken(one.trim()))
    .filter(Boolean))];
  // One input is not a chord. Two spellings of the same one are not two.
  if (members.length < 2) return null;
  const hold = /@hold:(\d+)/.exec(binding);
  return {action, members, holdMs: hold ? Number(hold[1]) : 0, firing: false};
}

// One name for "this input is down", whichever kind it is, so a chord can mix a key and
// a button. Keys fold to lower case, which is what the single-key map already stores.
function downToken(selector) {
  if (typeof selector !== "string") return "";
  if (selector.startsWith("key:")) {
    return "key:" + selector.slice(4).trim().toLowerCase();
  }
  if (selector.startsWith("pad:") && selector.includes("/button:")) {
    return "pad:" + selector.split("/button:").pop().trim();
  }
  return "";
}


const SVG_NS = "http://www.w3.org/2000/svg";

// The hold a single-input binding asks for: `key:Escape@hold:1500` -> its input and its
// length, or null for a binding that is not held. A hold of nothing is no hold.
function parseHold(binding) {
  const found = /^(.*)@hold:(\d+)$/.exec(binding);
  if (!found) return null;
  const ms = Number(found[2]);
  return ms > 0 ? { input: found[1].trim(), ms } : null;
}

// Every action and what is bound to it, sorted into the shapes dispatch uses: a key, a
// pad button, a chord, and a hold on a key or a button. Pure, so what a binding *means*
// can be checked without standing up a core - which is the half that used to be
// untestable.
//
// `pad:standard/button:<n>` is a button of the standard layout and is kept apart from
// `pad:<index>/button:<n>`: dispatch fires it only for a pad the browser reports as
// `mapping: "standard"`, where a numbered one fires for any pad. Holds list the actions in
// the order they were declared, which is the order a key's one hold is chosen in.
function buildBindingMaps(bindings) {
  const keys = {}, buttons = {}, chords = [];
  const keyHolds = {}, standard = {}, buttonHolds = {};
  for (const [action, list] of Object.entries(bindings || {})) {
    for (const binding of list || []) {
      if (typeof binding !== "string") continue;
      const chord = parseChordBinding(action, binding);
      if (chord) { chords.push(chord); continue; }
      const hold = parseHold(binding);
      const plain = hold ? hold.input : binding;
      if (plain.startsWith("key:") && !plain.includes("@") && !plain.includes("+")) {
        const token = plain.slice(4).trim().toLowerCase();
        if (hold) (keyHolds[action] ||= []).push({ token, ms: hold.ms });
        else (keys[action] ||= []).push(token);
      } else if (plain.startsWith("pad:") && plain.includes("/button:")
                 && !plain.includes("@") && !plain.includes("chord(")) {
        // An axis is still the binding grammar's next phase, not this one.
        const [pad, button] = plain.slice(4).split("/button:").map(one => one.trim());
        const onStandard = pad === "standard";
        if (hold) {
          (buttonHolds[button] ||= []).push({ action, ms: hold.ms, standard: onStandard });
        } else if (onStandard) {
          (standard[button] ||= []).push(action);
        } else {
          (buttons[button] ||= []).push(action);
        }
      }
    }
  }
  return {keys, buttons, chords, keyHolds, standard, buttonHolds,
          members: new Set(chords.flatMap(one => one.members))};
}

function canonicalMessageType(type) {
  return MESSAGE_TYPE_CANONICAL[type] || type;
}

// Say it once per name, not once per access - a wheel reads tableData every frame. The
// backend keeps the same list in common/deprecations.py and logs there; a theme runs in
// the browser, so this is the only place its use of an old name is visible.
const announcedLegacy = new Set();
function announceLegacy(target, oldName, newName) {
  if (announcedLegacy.has(oldName)) return;
  announcedLegacy.add(oldName);
  console.info(`vpinfe: deprecated theme JavaScript '${oldName}' is in use; the current name is ${newName} (PAR-23)`);
  // Also tell the backend: a browser log line is invisible on a cabinet. Best effort, and
  // caught twice - call() is async, so a rejection needs catching on the promise as well
  // as around the invocation, and the bridge may not be connected yet.
  try {
    Promise.resolve(target.call("report_deprecated_use", "vpin-members", oldName))
      .catch(() => {});
  } catch (err) {
    /* nothing to do; the browser log line already went out */
  }
}

// Core's own calls, refused by vpin.call(). These sort and filter the library for the
// collection-menu overlay, which core ships; no theme is expected to reach them.
//
// A line, not a wall: a theme's iframe is same-origin and can reach whatever the overlays
// reach if it goes looking. What it does is move these from allowed to circumvented.
const INTERNAL_METHODS = new Set([
  "apply_filters",
  "apply_sort",
  "get_collection_picker_items",
  "get_current_filter_state",
  "get_current_sort_state",
  "get_current_order_state",
  "get_paging_state",
  "keep_filter_collection",
  "library_waiting",
  "report_browser",
  "report_window",
  "take_picture",
  "resume_play",
  "stop_recording",
  "recording_offer",
  "record_media",
  "switch_preview",
  "decide_preview",
  "end_preview",
  "signin_pending",
  "signin_show",
  "signin_ignore",
  "signin_dismiss",
  "approval_approve",
  "approval_decline",
  "set_player_up",
  "set_player_rating",
  "get_player_rating",
  "get_rating_halves",
  "set_rating_halves",
  "get_attract_config",
  "input_capture",
  "input_capture_release",
]);

// By the ids `common/host/frontend_browser.py` knows.
const PLAYBACK_TYPES = [
  ["h264", 'video/mp4; codecs="avc1.42E01E"'],
  ["hevc", 'video/mp4; codecs="hvc1.1.6.L93.B0"'],
  ["vp9", 'video/mp4; codecs="vp09.00.10.08"'],
  ["av1", 'video/mp4; codecs="av01.0.05M.08"'],
  ["aac", 'audio/mp4; codecs="mp4a.40.2"'],
  ["mp3", "audio/mpeg"],
  ["vorbis", 'audio/ogg; codecs="vorbis"'],
  ["opus", 'audio/ogg; codecs="opus"'],
];

// 1080p at 60 frames a second, which is what a table's video has to hold.
const DECODE_TYPES = [
  ["h264", 'video/mp4; codecs="avc1.64002a"'],
  ["vp9", 'video/webm; codecs="vp09.00.41.08"'],
];

// Whether a real file shows a picture: true, false, or null when it neither loaded nor
// failed in time. The width is the answer, not the load event.
function decodesPicture(url, timeoutMs = 5000) {
  return new Promise((resolve) => {
    const video = document.createElement("video");
    let settled = false;
    const finish = (answer) => {
      if (settled) return;
      settled = true;
      video.removeAttribute("src");
      video.load();
      resolve(answer);
    };
    video.muted = true;
    video.preload = "auto";
    video.addEventListener("loadeddata", () => finish(video.videoWidth > 0));
    video.addEventListener("error", () => finish(false));
    setTimeout(() => finish(null), timeoutMs);
    video.src = url;
  });
}

// What this browser can draw and decode with, for a support summary. Every answer is true,
// false, or null where it was not tested; nothing here names a unit (a gamepad is counted,
// not named).
async function probeFeatures() {
  const started = performance.now();
  const context = (kind) => {
    try {
      return document.createElement("canvas").getContext(kind);
    } catch (e) {
      return null;
    }
  };
  const gl2 = context("webgl2");
  const gl = gl2 || context("webgl");
  // Only the unmasked name is a graphics chip; without the extension the browser says
  // "WebKit WebGL", which names nothing.
  let renderer = "";
  if (gl) {
    try {
      const info = gl.getExtension("WEBGL_debug_renderer_info");
      if (info) renderer = String(gl.getParameter(info.UNMASKED_RENDERER_WEBGL) || "");
    } catch (e) {
      renderer = "";
    }
  }
  // An adapter that never came back in time is not tested, which is not the same as none.
  let webgpu = false;
  if (navigator.gpu) {
    const timedOut = Symbol("timed out");
    try {
      const adapter = await Promise.race([
        navigator.gpu.requestAdapter(),
        new Promise((resolve) => setTimeout(() => resolve(timedOut), 2000)),
      ]);
      webgpu = adapter === timedOut ? null : adapter !== null;
    } catch (e) {
      webgpu = null;
    }
  }
  const decodes = {};
  for (const [id, contentType] of DECODE_TYPES) {
    try {
      const info = await navigator.mediaCapabilities.decodingInfo({
        type: "file",
        video: { contentType, width: 1920, height: 1080, bitrate: 8000000, framerate: 60 },
      });
      decodes[id] = { supported: info.supported, smooth: info.smooth, power_efficient: info.powerEfficient };
    } catch (e) {
      decodes[id] = null;
    }
  }
  // Chromium lists no pad until a button has been pressed on it, so none seen is not none
  // connected.
  const seen = navigator.getGamepads ? Array.from(navigator.getGamepads()).filter(Boolean).length : 0;
  const pads = seen > 0 ? seen : null;
  return {
    webgl: gl !== null, webgl2: gl2 !== null, renderer, webgpu,
    offscreen_canvas: typeof OffscreenCanvas === "function",
    decodes, gamepads: pads,
    screen: {
      scale: window.devicePixelRatio, width: window.screen.width, height: window.screen.height,
      extended: typeof window.screen.isExtended === "boolean" ? window.screen.isExtended : null,
    },
    took_ms: Math.round(performance.now() - started),
  };
}

// Same once-per-name reporting as announceLegacy, and for the same reason: without it the
// only evidence about who still calls these is a browser log nobody reads on a cabinet.
const announcedInternal = new Set();
function announceInternal(target, method) {
  if (announcedInternal.has(method)) return;
  announcedInternal.add(method);
  console.info(`vpinfe: '${method}' is not part of the theme API; the call was refused (PAR-84)`);
  try {
    Promise.resolve(target.callInternal("report_deprecated_use", "theme-internal-methods", method))
      .catch(() => {});
  } catch (err) {
    /* nothing to do; the browser log line already went out */
  }
}

// Contract 1's overlay surface, all nine names of it. Derived rather than forwarded: three
// booleans over one string, and six methods over two that take the overlay's name - so
// VPINFE_RENAMED_MEMBERS, which maps a name to a name, cannot express them.
const VPINFE_OVERLAY_ALIASES = {
  menuUP:           { overlay: "menu" },
  collectionMenuUP: { overlay: "collectionMenu" },
  tutorialUP:       { overlay: "tutorial" },
  toggleMenu:                         { overlay: "menu",           call: "toggleOverlay" },
  toggleCollectionMenu:               { overlay: "collectionMenu", call: "toggleOverlay" },
  toggleTutorial:                     { overlay: "tutorial",       call: "toggleOverlay" },
  registerInputHandlerMenu:           { overlay: "menu",           call: "registerOverlayHandler" },
  registerInputHandlerCollectionMenu: { overlay: "collectionMenu", call: "registerOverlayHandler" },
  registerInputHandlerTutorial:       { overlay: "tutorial",       call: "registerOverlayHandler" },
};

// An ordered sequence and a cursor, and nothing else. The wheel is one of these; so is
// the main menu, its dialog, the collection menu and its dropdown - seven hand-rolled
// copies of the same four lines before this existed, three of which shipped the same
// bail-out bug.
//
// Stepping, wrapping and moving to a position are the common methods. A surface whose
// contents cannot be navigated by them keeps its own handling and stays out, rather than
// these widening to fit it - a method that means something to one consumer and nothing
// to the rest is what makes a shared abstraction worse than none.
class NavigableList {
  // `kind` says what the items are - "table" or "collection" - so a theme reading an
  // index message knows which list it is about without tracking a mode of its own.
  // That is what retires leaveCollectionMode(): a broadcast says which list moved.
  constructor(items = [], { id = "", cursor = 0, kind = "table" } = {}) {
    this.id = id;
    this.kind = kind;
    this.items = Array.isArray(items) ? items : [];
    this.cursor = cursor;
  }

  get length() { return this.items.length; }

  get current() { return this.items[this.cursor]; }

  // Where a step of `delta` lands. Wraps both ways: a wheel is circular, and so is
  // paging - page_jump_index walks to the adjacent letter group with wraparound, so
  // forward from the last Z reaches the first A rather than crawling back through 26.
  // An empty list answers with the cursor it has, so no caller counts first.
  indexAfter(delta) {
    const count = this.length;
    if (!count) return this.cursor;
    return ((this.cursor + delta) % count + count) % count;
  }

  // Clamps, and that is a guard rather than a policy: every caller that wants to wrap
  // has already done it - paging wraps in page_jump_index before the index gets here.
  // What this stops is an out-of-range index landing somewhere arbitrary. Anything
  // wanting circular movement uses moveBy, which is what circular movement is.
  moveTo(index) {
    const count = this.length;
    if (!count) return this.cursor;
    this.cursor = Math.max(0, Math.min(count - 1, Number(index) || 0));
    return this.cursor;
  }

  moveBy(delta) { return this.moveTo(this.indexAfter(delta)); }
}


function installOverlayAliases(target) {
  for (const [oldName, spec] of Object.entries(VPINFE_OVERLAY_ALIASES)) {
    Object.defineProperty(target, oldName, {
      get() {
        announceLegacy(this, oldName, spec.call || "overlay");
        if (!spec.call) return this.overlay === spec.overlay;
        return (...args) => this[spec.call](spec.overlay, ...args);
      },
      // Nothing we ship writes these, but a theme could, and silently ignoring it would
      // be worse than acting on it: assigning false closes the overlay it names.
      set(value) {
        announceLegacy(this, oldName, "overlay");
        if (spec.call || !!value === (this.overlay === spec.overlay)) return;
        this.toggleOverlay(spec.overlay);
      },
      configurable: true,
    });
  }
}

function installLegacyAliases(target) {
  for (const [oldName, newName] of Object.entries(VPINFE_RENAMED_MEMBERS)) {
    if (oldName in target) continue;
    Object.defineProperty(target, oldName, {
      get() {
        announceLegacy(this, oldName, newName);
        const value = this[newName];
        return typeof value === 'function' ? value.bind(this) : value;
      },
      set(value) { announceLegacy(this, oldName, newName); this[newName] = value; },
      configurable: true,
    });
  }
}

function removeLegacyAliases(target) {
  for (const oldName of Object.keys(VPINFE_OVERLAY_ALIASES)) {
    if (Object.getOwnPropertyDescriptor(target, oldName)) delete target[oldName];
  }
  for (const oldName of Object.keys(VPINFE_RENAMED_MEMBERS)) {
    if (Object.getOwnPropertyDescriptor(target, oldName)) delete target[oldName];
  }
}


// How to read one item of the payload at contract 1. A game carries a filesystem path
// per media kind, so every URL here is built by taking that path apart - which is the
// reason contract 2 stopped serving paths at all.
class ContractOneReader {
  constructor(core) { this.core = core; }

  // The current key, falling back to the spelling the projection sends. PAR-22.
  field(game, name) {
    if (!name) return null;
    if (game[name] != null) return game[name];
    const legacy = MEDIA_FIELD_FALLBACK[name];
    return legacy ? game[legacy] : null;
  }

  has(game, kind)      { return !!this.field(game, MEDIA_PATH_FIELDS[kind]); }
  path(game, kind)     { return this.field(game, MEDIA_PATH_FIELDS[kind]) || null; }
  imageURL(game, kind) { return this.pathToURL(this.field(game, MEDIA_PATH_FIELDS[kind])); }
  // Contract 1 is the 2.x shape: one path per kind, so one page.
  pageURLs(game, kind) { return this.has(game, kind) ? [this.imageURL(game, kind)] : []; }
  videoURL(game, kind) { return this.pathToURL(this.field(game, MEDIA_VIDEO_PATH_FIELDS[kind])); }
  audioURL(game)       { return game.AudioPath ? this.pathToURL(game.AudioPath) : null; }
  logo(game)           { return game.ManufacturerLogoPath || null; }
  vpsId(game)          { return String(game?.meta?.Info?.VPSId || "").trim(); }
  // The table's id where the row has one, which is what contract 2 names it by; the folder
  // is the name a row with no id at all has left.
  identity(game)       {
    return String(game.meta?.VPXFile?.id || game.meta?.VPinFE?.game_id || game.tableDirName || "");
  }

  imageVideo(game, kind, priority) {
    const image = this.field(game, MEDIA_PATH_FIELDS[kind]);
    const video = this.field(game, MEDIA_VIDEO_PATH_FIELDS[kind]);
    const imagePath = kind === "playfield" ? (image || game.FSSImagePath) : image;
    const candidates = priority === "image"
      ? [{ kind: "image", path: imagePath }, { kind: "video", path: video }]
      : [{ kind: "video", path: video }, { kind: "image", path: imagePath }];
    const picked = candidates.find(c => !!c.path) || { kind: "missing", path: null };
    return { url: this.pathOrMissing(picked.path), kind: picked.kind, priority,
             path: picked.path || null };
  }

  realDmd(game, priority) {
    const candidates = priority === "standard"
      ? [{ variant: "standard", path: game.realDMDImagePath },
         { variant: "color", path: game.realDMDColorImagePath }]
      : [{ variant: "color", path: game.realDMDColorImagePath },
         { variant: "standard", path: game.realDMDImagePath }];
    const picked = candidates.find(c => !!c.path) || { variant: "missing", path: null };
    return { url: this.pathOrMissing(picked.path),
             kind: picked.variant === "missing" ? "missing" : "image",
             priority, variant: picked.variant, path: picked.path || null };
  }

  pathOrMissing(localPath) { return localPath ? this.pathToURL(localPath) : MISSING_MEDIA_URL; }

  pathToURL(localPath) {
    if (!localPath || typeof localPath !== 'string') return MISSING_MEDIA_URL;
    const normalized = localPath.replace(/\\/g, '/');       // Windows separators
    const parts = normalized.split('/');
    const file = parts[parts.length - 1];
    const files = this.core.endpoints.files;
    // The file may sit deeper than medias/ itself - wheel sets live in
    // medias/wheels/<set>/ - so keep everything from medias/ down.
    const mediasIndex = parts.lastIndexOf('medias');
    if (mediasIndex > 0) {
      const gameDir = parts[mediasIndex - 1];
      const rest = parts.slice(mediasIndex).map(encodeURIComponent).join('/');
      return `${files}/tables/${encodeURIComponent(gameDir)}/${rest}`;
    }
    const dir = parts[parts.length - 2];        // media sitting in the game folder
    return `${files}/tables/${encodeURIComponent(dir)}/${encodeURIComponent(file)}`;
  }
}

// ─────────────────────────── end contract 1 ──────────────────────────────────


class VPinFECore {
  // Core's own words, keyed by catalog key. Empty until #loadWords lands, which is
  // why the markup keeps its English.
  #words = {};

  // `ready` waits on it, so a theme's first t() after `await vpin.ready` has its words.
  #wordsLoaded = Promise.resolve();

  // Resolves the confirm dialog when one is up, and is null when one is not - which is
  // also how the input handler knows whether it owns the next select or back.
  #pendingConfirm = null;

  // The notice currently on screen, so a second one replaces it rather than stacking.
  #lifecycleNotice = null;

  // The recording run this device is doing, while it is running, as the event stream
  // last said; null otherwise. Its line, once drawn.
  #recording = null;
  #recordingLine = null;
  #recordingText = null;
  #capture = null;
  #captureLine = null;
  #captureText = null;
  #captureHint = null;
  #captureBar = null;
  #captureFill = null;
  #captureBarId = "";

  // A recording shown for a decision, as core last sent it, and what this window drew.
  #preview = null;
  #previewLayer = null;

  // The sign-in request the machine last said it is showing, or null, and the timer that
  // drops it when its time is up. Held here whether or not it can be drawn yet.
  #signIn = null;
  #signInExpiry = null;
  #signInOpening = false;

  // Keyed on the game's row as well as the URL: a reload hands out new rows, and a picture
  // replaced under the same URL is only fetched again if the node is built again.
  #mediaShown = new WeakMap();

  constructor() {
    this.tableData = {};
    // The folders an empty library is waiting on, by server where they are on one.
    this.libraryWaiting = [];
    this._watchingLibrary = false;
    // What the running theme declared. Contract 1 until the bridge says otherwise.
    this.contract = OLDEST_CONTRACT;
    // The theme's windows, controller first. Replaced once the bridge answers; until
    // then the three VPinFE has always opened, under contract 1's names.
    this.windows = ["table", "bg", "dmd"];
    // Which collection the entry list came from. Seeded so a theme reading it before
    // the first payload gets the documented type rather than undefined.
    this.collection = "";
    this.groupBy = "";
    this._reader = new ContractOneReader(this);
    installLegacyAliases(this);
    installOverlayAliases(this);
    this.monitors = [];
    // Everyone on this install and who is up. Loaded during init() and kept live by
    // PlayersChanged - see handleEvent().
    this.players = { players: [] };
    this._resolveReady = null;
    this.ready = new Promise(resolve => this._resolveReady = resolve);
    this.inputHandlers = []; // gamepad and joystick input handlers for theme
    // Input handlers per overlay, keyed by its name in OVERLAYS. One map rather than an
    // array per overlay, so adding a fourth is a registry entry and nothing else.
    this.overlayHandlers = {};

    // Gamepad mapping
    this.joyButtonMap = {}
    // Standard-layout buttons, which fire for a pad that reports itself as standard only.
    this.standardButtonMap = {}
    // A button's hold, whichever pad it is on: [{action, ms, standard}] by button.
    this.joyHoldMap = {}
    this.keyActionMap = {
      previous: ['arrowleft', 'shiftleft'],
      next: ['arrowright', 'shiftright'],
      page_previous: ['pageup', 'arrowup'],
      page_next: ['pagedown', 'arrowdown'],
      select: ['enter'],
      back: ['escape', 'keyb'],
      menu: ['keym'],
      collection_menu: ['keyc'],
      tutorial: ['keyt'],
      exit: ['keyq'],
      take_picture: [],
    };
    this.keyHoldMap = { exit: [{ token: 'escape', ms: 1500 }] };
    // An input with a hold, from the moment it goes down until it is let go.
    this._pending = new Map();
    this.previousButtonStates = {};
    // Held input repeats here, not wherever the press came from. A press acts once;
    // holding starts repeating after the delay and speeds up to the floor, so one
    // control both nudges by a single game and travels across a large library.
    //
    // One curve for every producer. The keyboard used to lean on the OS repeat - a rate
    // the machine picks, with no ramp - a held gamepad button did nothing at all because
    // only the press edge counted, and a phone would have had to invent a third. Three
    // controls that feel different doing one thing is the defect.
    this.repeatDelayMs = 400;   // a deliberate press must never become a repeat
    this.repeatStartMs = 260;   // the first repeat: a comfortable browse
    this.repeatAccel = 0.82;    // each repeat is this much shorter than the last
    this.repeatFloorMs = 70;    // as fast as this goes: quick, still trackable
    this.pageRepeatMs = 350;    // a held page button: one group per this, never faster
    // One at a time. You cannot ask to go left and right at once, and a wheel that tried
    // would answer neither.
    this._hold = null;
    // A hold that has gone on this long is a scrub: the wheel is travelling, not choosing.
    // It ends this long after the key comes up, so a player who lets go and presses again
    // does not make every screen redraw twice.
    this.scrubAfterMs = 1000;
    this.scrubSettleMs = 200;
    this._scrub = null;
    // A press that follows another within this long is part of a run, and the media drawn
    // in this window waits for the run to pause. 0 draws on every press.
    this.mediaSettleMs = 150;
    this._mediaAt = null;
    this._mediaMovedAt = 0;
    this._mediaTimer = null;
    // A theme that draws the wheel while it scrubs says so (`vpin.scrubAware = true`) and
    // is sent a `TableIndexUpdate` with `scrubbing: true` for every step. One that does
    // not is sent nothing until the scrub ends.
    this.scrubAware = false;
    // How long after a move the wheel still counts as travelling, which is what the
    // `moving` flag on an index message tells a theme. Its own constant now: it used to
    // borrow the keyboard repeat throttle, which no longer exists.
    this.movingWindowMs = 300;
    this.gamepadEnabled = true;
    this.frontendInputEnabled = true;
    this._launchInputSuppressedByLifecycle = false;

    // What core is doing on the theme's behalf, seeded from CAPABILITIES so the
    // default is stated in one place.
    this._capabilities = Object.fromEntries(
      Object.entries(CAPABILITIES).map(([name, spec]) => [name, spec.default]));
    this._pagingInFlight = false;

    // Attract mode. `settings` is what the person set, null until the bridge answers.
    // `suspended` holds a reason per cause, so a launch and a picker overlapping do not
    // race over one flag. `origin` is the index to put the wheel back to while attract
    // is moving it, valid only for the list `originList` it was taken from.
    this._attract = {
      settings: null, suspended: new Set(), timer: null, active: false,
      origin: null, originList: null, bag: [], bagFor: null,
    };

    // The overlay that is open, or null. One-of by construction - #toggleOverlay closes
    // any other before opening one - so three booleans could only ever disagree.
    this.overlay = null;

    // Lists core has descended into, innermost last. Empty means the wheel is what is on
    // screen. The wheel itself is not held here - it is derived from tableData, so there
    // is still one place the entries live.
    this._listStack = [];

    // Event handling
    this.eventHandlers = {}; // Custom event handlers registered by themes

    // Where the services are, resolved once before any theme code runs. Ports travel in
    // the url because the page cannot ask for them: asking needs the bridge, and finding
    // the bridge needs a port. Absent - an older launcher, a page opened by hand - these
    // fall back to what the frontend has always assumed.
    const params = new URLSearchParams(window.location.search);
    const port = (name, fallback) => Number(params.get(name)) || fallback;
    this.themeAssetsPort = port('themeAssetsPort', 8001);
    this.libraryPort = port('libraryPort', 8001);
    this.wsPort = port('wsPort', 8002);
    // Set only when the library is on another machine. This install's own services stay
    // loopback: this names where the library and its art are, not where the page is.
    this.libraryHost = params.get('libraryHost') || '';
    // What this install serves on, which is not what a remote library answers on -
    // `libraryPort` carries that one's, so the two cannot be the same number.
    this.devicePort = port('devicePort', this.libraryPort);

    // Display config, as the ini states it. Raw values - `layout` below is what a theme
    // should read.
    this.playfieldOrientation = 'landscape'; // default, will be updated from config
    this.playfieldRotation = 0; // default, will be updated from config

    // The three questions every theme was answering for itself, answered once. Seeded so
    // a theme laying out before the bridge answers gets the documented types.
    this.layout = { cabinet: false, uprightRotation: 0, surface: 'landscape' };
    // How far to turn playfield art. "auto" measures each image instead.
    this.playfieldMediaRotation = 'auto';

    // Remote launch state tracking
    this.remoteLaunchActive = false;

    // Theme config and centralized audio state
    this.themeConfig = {};
    this.mediaPriorities = Object.assign({}, DEFAULT_MEDIA_PRIORITIES);
    this._currentTableIndex = 0;
    this._initialGameRestored = false;
    // The controller's own state, once its restore has settled; windows that asked before
    // that are answered when it does.
    this._restoreSettled = false;
    this._selectionAsks = new Set();
    // The game a window that is not the controller was told is selected, by id, and where
    // that sits in this window's own list (-1 when it is not there). null until told. Its
    // list can be ordered or filtered differently from the controller's, so the index the
    // controller holds can be another game here; such a window draws nothing until it can
    // name the game in its own list.
    this._followedId = null;
    this._followedAt = -1;
    this._audioMuted = false;
    this._audio = Object.assign(new Audio(), { loop: true });
    this._audioFadeId = null;
    this._audioFadeDuration = 500;
    this._audioMaxVolume = 0.8;
    this._audioCurrentUrl = null;
    this._lastSelectedIndex = null;
    // Preloading waits for the wheel to stop. Fetching on every step is what made a
    // two-second hold ask for hundreds of images that were obsolete before they decoded;
    // the settle delay is the whole mechanism, not a nicety.
    this._preloadSettleMs = 180;
    // Canonical names: a contract 1 theme has these translated for it on the way
    // out, while `bg` here would only ever have worked for contract 1.
    this._preloadKinds = ["playfield", "backglass", "wheel"];
    this._preloadTimer = null;
    this._preloaded = new Set();

    this._onSelection = [];
    // The base mode is never popped; overlays and dialogs push on top.
    this._inputModes = ['navigation'];
    this._lastMoveAt = 0;
    this.onSelection(() => this.#notifySelectedGame().catch(() => {}));
    this.onSelection(() => this.#schedulePreload());

    // WebSocket bridge
    this._ws = null;
    this._reconnectTimer = null;
    this._reconnectDelayMs = 0;
    this._shuttingDown = false;
    this._pendingCalls = {}; // {callId: {resolve, reject}}
    this._callIdCounter = 0;
    this._windowName = this.#detectWindowName();
    this.#applyWindowIdentity();

  }

  #detectWindowName() {
    const queryWindow = new URLSearchParams(window.location.search).get('window');
    if (queryWindow) return queryWindow;

    // Any window name, not a fixed three: a theme declares its own, and the bootstrap is
    // served at /app/<name> for whatever it declared. Same character class the server
    // accepts there.
    const match = window.location.pathname.match(/^\/app\/([A-Za-z0-9_-]+)\/?$/);
    if (match) return match[1];

    return 'unknown';
  }

  // ***********************************
  // Public api
  // ***********************************

  // ***********************************
  // Words
  // ***********************************

  /**
   * What a key says, in the language this install speaks.
   *
   * Core's own chrome - the menus, the overlays, the handful of strings every theme was
   * writing out for itself - and the theme's own words from its locales/ folder, which
   * are keyed `theme.<key>`.
   *
   * `english` is what shows before the catalog arrives, which is a real window for
   * anything drawn before `ready`. Unknown keys with no English answer with the key,
   * never blank.
   */
  t(key, english = "", params = {}) {
    const said = VPinFECore.pluralForm(this.#words[key], params.count)
      ?? VPinFECore.pluralForm(english, params.count);
    if (!said) return key;
    return said.replace(/\{(\w+)(:n)?\}/g, (whole, name, spec) => {
      if (params[name] === undefined) return whole;
      return spec ? this.number(params[name]) : params[name];
    });
  }

  /**
   * A number as this language writes it: the catalog's own group and decimal separators.
   * Anything that is not a finite number comes back as it was given.
   */
  number(value) {
    if (typeof value !== "number" || !Number.isFinite(value)) return String(value);
    const group = this.#words["number.group"] ?? ",";
    const decimal = this.#words["number.decimal"] ?? ".";
    return value.toLocaleString("en-US", { maximumFractionDigits: 10 })
      .replace(/[,.]/g, (mark) => (mark === "," ? group : decimal));
  }

  static pluralForm(entry, count) {
    if (entry === null || typeof entry !== "object") return entry;
    return (Math.abs(Number(count)) === 1 && entry.one) || entry.other;
  }

  /**
   * Fill every [data-i18n] element from the catalog.
   *
   * The markup keeps its English between the tags, so a page that loads before the
   * catalog does - or with no catalog at all - reads correctly rather than blank.
   */
  applyWords(root = document) {
    for (const el of root.querySelectorAll("[data-i18n]")) {
      const key = el.getAttribute("data-i18n");
      const said = this.#words[key];
      if (typeof said === "string") el.textContent = said;
    }
  }

  /**
   * Fill an overlay's words once its document exists.
   *
   * An overlay is an iframe, so it is its own document and the page's own applyWords
   * never reaches it. Same origin, so reading into it is allowed - the same thing
   * #listenForKeysIn already does.
   */
  #applyWordsIn(iframe) {
    const fill = () => {
      try {
        if (iframe.contentDocument) this.applyWords(iframe.contentDocument);
        // An overlay does not load core, so anything it writes at runtime reads this.
        // Its own `t()` falls back to the English in the page when it is not here yet.
        if (iframe.contentWindow) {
          iframe.contentWindow.__vpinWords = this.#words;
          iframe.contentWindow.__vpinNumber = (value) => this.number(value);
          iframe.contentWindow.__vpinWordsChanged?.();
        }
      } catch (error) {
        console.warn("[i18n] Could not fill an overlay's words", error);
      }
    };
    iframe.addEventListener("load", fill);
    fill();   // already loaded from cache, in which case the event has been and gone
  }

  async #loadWords() {
    // Same origin as the page core is served from. A failure is not fatal: every
    // [data-i18n] element already holds its English.
    try {
      // A theme page lives at /themes/<folder>/, and its locales/ words come with core's.
      const theme = window.location.pathname.match(/^\/themes\/([^/]+)\//);
      const url = theme ? `/core/i18n.json?theme=${encodeURIComponent(decodeURIComponent(theme[1]))}` : "/core/i18n.json";
      // `ready` waits on this, so a catalog that never answers must not hold the theme.
      const response = await fetch(url, { cache: "no-store", signal: AbortSignal.timeout(5000) });
      if (response.ok) this.#words = await response.json();
    } catch (error) {
      console.warn("[i18n] Could not load the catalog; using the markup's English", error);
    }
    this.applyWords();
    for (const spec of Object.values(VPinFECore.OVERLAYS)) {
      const frame = document.getElementById(spec.frameId);
      if (frame) this.#applyWordsIn(frame);
    }
  }

  init() {
    this.#applyWindowIdentity();
    this.#wordsLoaded = this.#loadWords();
    // The theme's own page, for the same reason an overlay gets it: a theme that throws
    // at startup renders nothing, which looks exactly like a blank screen.
    this.#reportUncaughtFrom(window, "");
    window.__vpinCoreResumeAudio = () => this.#audioResumePlay();

    // Set up keyboard listener
    window.addEventListener('keydown', (e) => this.#onKeyDown(e));
    window.addEventListener('keyup', (e) => this.#onKeyUp(e));
    // A window that loses focus mid-hold never sees the keyup, and the wheel would keep
    // travelling behind whatever took the focus. The same failure the install guards a
    // network press against by expiring it.
    window.addEventListener('blur', () => this.#loseFocus());
    // Back from a table, the controller window is asked again: a request made while it
    // was running was held, and nothing else says it is still waiting.
    window.addEventListener('focus', () => { if (this.isController()) this.#pullSignIn(); });
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) this.#loseFocus();
    });

    // Connect to WebSocket bridge
    this.#connectWebSocket();
  }

  // Input goes to the controller window only. The round trip stays because it also
  // sets the global a theme's own example reads; the decision comes from the window
  // list, not from a name.
  //
  // `window.windowName`, not a bare assignment: a class body is strict mode, so writing
  // an undeclared name throws. Every published theme opens with `windowName = ""` and
  // hid that - a theme reading vpin.windowName instead has no reason to, and crashed
  // here on the first call.
  async registerInputHandler(handler) {
    window.windowName = await this.call("get_my_window_name");
    if (typeof handler === 'function' && this.isController()) {
      this.call("console_out", "registered gamepad handler");
      this.inputHandlers.push(handler);
    }
  }

  // An overlay page registers the handler that receives every action while it is open.
  // Not guarded on isController: an overlay only ever exists in the controller window.
  async registerOverlayHandler(name, handler) {
    if (!VPinFECore.OVERLAYS[name]) {
      console.warn(`[vpinfe] registerOverlayHandler("${name}") names no overlay`);
      return;
    }
    if (typeof handler !== 'function') return;
    this.call("console_out", `registered ${name} overlay handler`);
    (this.overlayHandlers[name] ||= []).push(handler);
  }

  async call(method, ...args) {
    if (INTERNAL_METHODS.has(method)) {
      announceInternal(this, method);
      throw new Error(`Method not allowed: ${method}`);
    }
    return this.callInternal(method, ...args);
  }

  // The door core's own overlays come in by. Not part of the theme surface - see
  // INTERNAL_METHODS for what that is worth and what it is not.
  async callInternal(method, ...args) {
    if (!this._ws || this._ws.readyState !== WebSocket.OPEN) {
      throw new Error(`WebSocket not connected, cannot call ${method}`);
    }
    const callId = String(++this._callIdCounter);
    return new Promise((resolve, reject) => {
      this._pendingCalls[callId] = { resolve, reject };
      this._ws.send(JSON.stringify({
        type: 'api_call',
        id: callId,
        method: method,
        args: args
      }));
    });
  }

  getImageURL(index, kind) {
    const item = this.tableData[index];
    return item ? this._reader.imageURL(item, this.#canonicalKind(kind)) : MISSING_MEDIA_URL;
  }

  getMediaURL(index, kind) {
    return this.getMedia(index, kind).url;
  }

  // A picture at the 256 pixel size, for a wheel that draws many of them small. A
  // contract 1 theme has file paths rather than table ids, which the server cannot
  // resize, so it gets the picture whole.
  getThumbnailURL(index, kind) {
    const url = this.getImageURL(index, kind);
    return url.includes("/media/") ? `${url}${url.includes("?") ? "&" : "?"}size=256` : url;
  }

  // URL of the game manufacturer's logo, or null when none is installed
  getManufacturerLogoURL(index) {
    const item = this.tableData[index];
    const path = item ? this._reader.logo(item) : null;
    return path ? `${this.endpoints.files}${path}` : null;
  }

  getMediaPages(index, kind) {
    const item = this.tableData[index];
    return item ? this._reader.pageURLs(item, this.#canonicalKind(kind)) : [];
  }

  getPreferredMediaURL(index, kind) {
    return this.getMediaURL(index, kind);
  }

  getMedia(index, kind) {
    const item = this.tableData[index];
    if (!item) return { url: MISSING_MEDIA_URL, kind: "missing", priority: null, path: null };

    const canonical = this.#canonicalKind(kind);
    if (canonical === "real_dmd") {
      return this.#resolveRealDmdMedia(item);
    }
    // Canonical, because `canonical` is. Written as `bg`/`dmd` this never matches,
    // and a video silently loses to its still.
    if (["playfield", "backglass", "scoreview"].includes(canonical)) {
      return this.#resolveImageVideoMedia(item, canonical);
    }

    // Every other kind is a single image, whichever contract it came from.
    const url = this._reader.imageURL(item, canonical);
    const has = this._reader.has(item, canonical);
    return {
      url,
      kind: has ? "image" : "missing",
      priority: null,
      path: this._reader.path(item, canonical),
    };
  }

  // The URL of a game's audio, or null when it has none
  getAudioURL(index) {
    const item = this.tableData[index];
    return item ? this._reader.audioURL(item) : null;
  }

  /**
   * What this build does on your behalf, and whether each is on right now. A name that
   * is absent is a behavior this build does not have - check before using it.
   */
  get capabilities() {
    return { ...this._capabilities };
  }

  /**
   * Where the things this page talks to actually are. Build urls from these rather than
   * assuming a host or a port: the halves can be separate machines, and only this knows.
   *
   * Three are addresses you fetch from - take one, add a path, get an answer back:
   *   `library`  the catalog and what is known about it: games, collections, uploads
   *   `device`   this machine: launching, play state, its hardware
   *   `files`    the files themselves: theme packages, table media, manufacturer logos
   *
   * One is a line held open instead, so it takes no path:
   *   `frontend_channel`  how this page and VPinFE talk to each other, both ways
   *
   * `library` and `device` are one address today because one `/api/v1` answers for both.
   * They are separate keys because they are separate questions, and a theme built
   * against them keeps working when the two are separate machines.
   *
   * Derived on read, so correcting a port after startup corrects every url built from it.
   */
  get endpoints() {
    const host = '127.0.0.1';
    // The library's services follow the library. `device` and `frontend_channel` never
    // do: they are this machine's own, and a page dialling another host for them would
    // be asking a different install to answer for this one's windows.
    const libraryHost = this.libraryHost || host;
    return {
      library: `http://${libraryHost}:${this.libraryPort}`,
      device: `http://${host}:${this.devicePort}`,
      files: this.libraryHost
        ? `http://${host}:${this.devicePort}/library`
        : `http://${host}:${this.themeAssetsPort}`,
      frontend_channel: `ws://${host}:${this.wsPort}`,
    };
  }

  enabled(name) {
    return !!this._capabilities[name];
  }

  #setCapability(name, on) {
    if (!(name in CAPABILITIES)) return;
    this._capabilities[name] = !!on;
  }

  // Whatever the theme's config says, against the keys each capability declares.
  #applyCapabilityConfig() {
    const config = this.themeConfig || {};
    for (const [name, spec] of Object.entries(CAPABILITIES)) {
      const keys = [...(spec.config || []), ...(spec.legacyConfig || [])];
      const stated = keys.map((key) => configValue(config, key))
                         .find((value) => value !== undefined);
      // `defaultFromContract` reads ">= this contract", not "== it", so a contract 3
      // theme inherits the newer default rather than falling back to the older one.
      const fallback = spec.defaultFromContract === undefined
        ? spec.default
        : this.contract >= spec.defaultFromContract;
      this.#setCapability(name, stated === undefined ? fallback : !!stated);
    }
    if (!this.enabled("core_audio")) this.stopTableAudio({ immediate: true });

    // A theme that shows a cab shot, or shows no wheel, preloads a different set. The
    // names go through the same normalization as everywhere else, so a contract 1 theme
    // can still say `table` and mean the playfield.
    const kinds = configValue(config, "preload.kinds");
    if (Array.isArray(kinds) && kinds.length) {
      const asked = kinds.map((kind) => this.#canonicalKind(kind));
      const unknown = asked.filter((kind) => !MEDIA_KINDS.includes(kind));
      if (unknown.length) {
        console.warn(`[vpinfe] preload.kinds names ${unknown.join(", ")}, which `
          + `${unknown.length === 1 ? "is not a media kind" : "are not media kinds"}. `
          + `Nothing preloads for ${unknown.length === 1 ? "it" : "them"}.`);
      }
      this._preloadKinds = asked.filter((kind) => MEDIA_KINDS.includes(kind));
    }
  }

  // Turn core's paging off and receive the actions in handleInput instead.
  //
  // At contract 2 paging is part of core_navigation, so this turns off all four actions -
  // which is what the call asks for in the only sense that still exists there. Below it
  // the two are separate and this means paging alone, as it always did.
  enableCorePaging(enabled = true) {
    const capability = this.contract < CURRENT_CONTRACT ? "core_paging" : "core_navigation";
    this.#setCapability(capability, enabled);
  }

  isCorePagingEnabled() {
    return this.enabled(this.contract < CURRENT_CONTRACT ? "core_paging" : "core_navigation");
  }

  // Ask the backend where a page next/prev press should land. Available to
  // themes doing their own paging animation.
  async getPageIndex(direction = "next", index = this._currentTableIndex) {
    return await this.call("get_page_index", index, direction);
  }

  enableCoreAudio(enabled = true) {
    this.#setCapability("core_audio", enabled);
    if (!this.enabled("core_audio")) this.stopTableAudio({ immediate: true });
  }

  isCoreAudioEnabled() {
    return this.enabled("core_audio");
  }

  setAudioMuted(muted = true) {
    this._audioMuted = !!muted;
    if (this._audio) this._audio.muted = this._audioMuted;
    if (this._audioMuted) {
      this.stopTableAudio({ immediate: true });
      return;
    }
    if (this.enabled("core_audio") && this.isController()) {
      this.playTableAudio(this._currentTableIndex);
    }
  }

  isAudioMuted() {
    return !!this._audioMuted;
  }

  setAudioOptions(options = {}) {
    if (typeof options !== 'object' || options === null) return;

    const fadeMs = this.#coerceNumber(
      options.fadeDuration,
      options.fade_duration_ms,
      options.fadeMs
    );
    if (fadeMs !== null) this._audioFadeDuration = Math.max(0, fadeMs);

    const volume = this.#coerceNumber(
      options.maxVolume,
      options.max_volume,
      options.volume
    );
    if (volume !== null) this._audioMaxVolume = Math.min(1, Math.max(0, volume));

    if (typeof options.loop === 'boolean') this._audio.loop = options.loop;
  }

  playTableAudio(indexOrUrl = this._currentTableIndex, retries = 3) {
    if (!this.enabled("core_audio") || this._audioMuted || !this.isController()) return;
    const url = this.#resolveAudioUrl(indexOrUrl);
    if (!url) {
      this.stopTableAudio();
      return;
    }
    if (this._audioCurrentUrl === url && !this._audio.paused) return;

    clearInterval(this._audioFadeId);
    this._audio.pause();
    this._audio.volume = 0;
    this._audio.src = url;
    this._audioCurrentUrl = url;

    this._audio.play().then(() => {
      if (this._audioCurrentUrl === url) this.#fadeAudio(0, this._audioMaxVolume);
    }).catch((e) => {
      // Autoplay refused: nothing to retry against, the browser wants a gesture. The
      // frontend launches Chromium with --autoplay-policy=no-user-gesture-required, so
      // this is only reachable outside that launcher.
      if (e && e.name === "NotAllowedError") return;
      if (retries > 0 && this._audioCurrentUrl === url) {
        setTimeout(() => this.playTableAudio(url, retries - 1), 1000);
      }
    });
  }

  stopTableAudio(options = {}) {
    const immediate = !!(options && options.immediate);
    if (!this._audio || this._audio.paused) {
      clearInterval(this._audioFadeId);
      this._audioCurrentUrl = null;
      return;
    }
    if (immediate) {
      clearInterval(this._audioFadeId);
      this._audio.volume = 0;
      this._audio.pause();
      this._audioCurrentUrl = null;
      return;
    }
    this.#fadeAudio(this._audio.volume, 0, () => {
      this._audio.pause();
      this._audioCurrentUrl = null;
    });
  }

  // The URL of a game's video, by media kind
  getVideoURL(index, kind) {
    const item = this.tableData[index];
    return item ? this._reader.videoURL(item, this.#canonicalKind(kind)) : MISSING_MEDIA_URL;
  }

  // The list, under the name that describes what is in it. At contract 2 the items are
  // entries - a table with its game attached - so `entries` is what a theme iterates.
  get entries() {
    return this.tableData;
  }

  getTableMeta(index) {
    return this.tableData[index];
  }

  getTableCount() {
    return this.tableData.length;
  }

  getCurrentTableIndex() {
    return this._currentTableIndex;
  }

  /**
   * What VPinPlay's players have rated this table, or null.
   *
   * Read off the entry, which core filled in. The page used to call VPinPlay itself:
   * every window on a cabinet asked the same question about the same game, and every
   * reload threw the answers away. Kept because published themes call it by name;
   * `entry.ext.vpinplay` is the same value and is what a theme should read now.
   */
  getCachedVPinPlayRating(index = this._currentTableIndex) {
    const item = this.#itemByIndex(index);
    if (!item || typeof item !== "object") return null;
    const contributed = (item.ext && item.ext.vpinplay) || null;
    return contributed || null;
  }

  /** As above. Async because it always was, and a theme awaits it. */
  async getVPinPlayRating(index = this._currentTableIndex, options = {}) {
    if (options && options.forceRefresh) return this.refreshVPinPlayRating(index);
    return this.getCachedVPinPlayRating(index);
  }

  /**
   * Ask again for this game, rather than taking what is held.
   *
   * The answer is core's now, so this asks core to forget and re-fetch. What comes back
   * arrives as an entry update like any other; this returns what is known once it has.
   */
  async refreshVPinPlayRating(index = this._currentTableIndex) {
    const item = this.#itemByIndex(index);
    if (!item || typeof item !== "object") return null;
    const gameId = String((item.game && item.game.id) || "");
    if (!gameId) return this.getCachedVPinPlayRating(index);
    try {
      const found = await this.call("refresh_entry_data", gameId);
      if (found && typeof found === "object") {
        item.ext = Object.assign({}, item.ext || {}, found);
      }
    } catch (error) {
      this.call("console_out", `Could not refresh the rating: ${error.message}`)
        .catch(() => {});
    }
    return this.getCachedVPinPlayRating(index);
  }

  // The legacy copy goes out by the SAME delivery as the message it mirrors. Both
  // spellings leave from here, so they cannot disagree about how: sent without _incself
  // the copy reaches bg and dmd but never the window that sent it.
  #broadcast(method, message) {
    this.#nameGame(message);
    this.#syncLocalIndexFromOutgoingMessage(message);
    this.#syncSelectionFromMessage(message);
    this.call(method, message);
    const legacy = this.#servesLegacyNames() && MESSAGE_TYPE_ALIASES[message.type];
    if (legacy) this.call(method, { ...message, type: legacy });
  }

  // An index message from a theme names the game too, from this window's own list.
  #nameGame(message) {
    if (!message || message.type !== "TableIndexUpdate" || typeof message.id === "string") return;
    if (typeof message.index !== "number" || message.kind === "collection" || !this.atRoot) return;
    const id = this.#identityAt(Math.floor(message.index));
    if (id) message.id = id;
  }

  // send a message to all windows except "self"
  sendMessageToAllWindows(message) {
    this.#broadcast("send_event_all_windows", message);
  }

  // send a message to all windows including "self"
  sendMessageToAllWindowsIncSelf(message) {
    this.#broadcast("send_event_all_windows_incself", message);
  }

  // One window only, with the legacy copy delivered the same way as in #broadcast.
  #sendToWindow(windowName, message) {
    this.call("send_event", windowName, message, true);
    const legacy = this.#servesLegacyNames() && MESSAGE_TYPE_ALIASES[message.type];
    if (legacy) this.call("send_event", windowName, { ...message, type: legacy }, true);
  }

  // The controller answers this window alone; the others see nothing.
  async #askForSelection() {
    await this.#waitForReceiveEvent();
    this.sendMessageToAllWindows({ type: "SelectionRequest", window: this._windowName });
  }

  // Ask until the controller answers. A request sent before the controller's page has
  // loaded is dropped, so one ask is not enough, and a window never draws a game it was
  // not told about, so it keeps asking at a slower pace after the first few seconds.
  async #awaitSelection() {
    this.#showHeld();
    const quickUntil = Date.now() + VPinFECore.SELECTION_QUICK_MS;
    while (this._followedId === null) {
      await this.#askForSelection();
      const pause = Date.now() < quickUntil
        ? VPinFECore.SELECTION_ASK_MS : VPinFECore.SELECTION_SLOW_MS;
      await new Promise(resolve => setTimeout(resolve, pause));
    }
  }

  // A window that is not the controller draws only a game it can find in its own list.
  #selectionHeld() {
    return !this.isController() && this._followedAt < 0;
  }

  // The same hold, on the document, so a theme that draws its own art is covered as well
  // as the elements core fills. Nothing to hide when the list is empty.
  #showHeld() {
    const root = document.documentElement;
    if (!root) return;
    if (this.#selectionHeld() && this.tableData?.length) root.dataset.vpinfeHeld = "";
    else delete root.dataset.vpinfeHeld;
  }

  // Where the game with this id sits in this window's list, or -1.
  #indexOfId(id) {
    if (!id || !Array.isArray(this.tableData)) return -1;
    return this.tableData.findIndex(item =>
      item && typeof item === "object" && this._reader.identity(item) === id);
  }

  // Follow the game the controller named. Returns where it sits here, -1 if the list does
  // not hold it (yet), in which case nothing is drawn until a reload brings it.
  #follow(id) {
    this._followedId = id;
    this._followedAt = this.#indexOfId(id);
    if (this._followedAt >= 0) this._currentTableIndex = this._followedAt;
    return this._followedAt;
  }

  // For a message that cannot name its game: take its index, and keep the game it lands on.
  #followIndex() {
    this._followedId = this.#identityAt(this._currentTableIndex) || "";
    this._followedAt = this._currentTableIndex;
  }

  #answerSelectionAsks() {
    this._restoreSettled = true;
    const waiting = [...this._selectionAsks];
    this._selectionAsks.clear();
    if (!this.atRoot) return;
    for (const name of waiting) {
      this.#announceIndex(this._currentTableIndex, { reason: "restore", to: name });
    }
  }

  // Open the named overlay, or close it if it is the one that is open. Callable from an
  // overlay's own page, which is how each of them closes itself.
  toggleOverlay(name) {
    return this.#toggleOverlay(name);
  }

  // Launch a game
  async launchTable(index) {
    // The windows and the audio catch up before the launch stops them, not after.
    this.#settleScrub();
    this.#setFrontendInputEnabled(false);
    try {
      await this.call("launch_table", index);
    } catch (e) {
      // The call will timeout after 30s while VPX is still running - that's expected
      this.call("console_out", `launch_table call ended: ${e.message}`);
    } finally {
      if (!this._launchInputSuppressedByLifecycle && !this.remoteLaunchActive) {
        this.#setFrontendInputEnabled(true);
      }
    }
  }

  async getTableData(reset=false) {
    const payload = JSON.parse(await this.call("get_tables", reset));
    if (Array.isArray(payload)) {
      this.tableData = payload;                       // contract 1: a row per game
    } else {
      // Contract 2 wraps the list so the collection it belongs to travels with it.
      this.tableData = payload.entries || [];
      this.#applyVPinPlayShim();
      this.collection = payload.collection || "";
      // What kind of group each entry carries - "letter", "year", "rating" - or "" when
      // the order has none. The group itself rides on the entry, so a move costs no
      // round trip to know where the wheel is sitting.
      this.groupBy = payload.group_by || "";
    }
    const maxIndex = Math.max(0, this.tableData.length - 1);
    if (this._currentTableIndex > maxIndex) this._currentTableIndex = maxIndex;
    if (!this.tableData.length) this.#watchForLibrary();
    if (this.isController()) {
      if (this.tableData.length > 0) {
        if (!this._initialGameRestored) {
          this._initialGameRestored = true;
          await this.#restoreInitialGame();
        }
        this.#selectionChanged();
      } else {
        this._lastSelectedIndex = null;
      }
      this.#answerSelectionAsks();
    } else if (this._followedId !== null) {
      // The list can have been reordered or filtered since the game was named, and a
      // name that beat the list here could only be an index until now.
      if (this._followedId) this.#follow(this._followedId);
      else this.#followIndex();
      this.#renderWindowMedia();
    }
  }

  // On the first game-data load, ask the backend for the last-launched game's
  // index and, if it isn't already first, move the wheel there. Sending a
  // TableIndexUpdate (inc self) drives the theme through the same path its own
  // input uses, so no theme changes are needed to honor the restored position.
  async #restoreInitialGame() {
    try {
      const index = await this.call("get_initial_table_index");
      if (typeof index === "number" && index > 0 && index < this.tableData.length) {
        // Captured before moveTo assigns it, or `previous` reports the index we are
        // moving to. This used to assign _currentTableIndex here for the same reason
        // moveTo does it now.
        const previous = this._currentTableIndex;
        // Themes register window.receiveEvent at varying points in their startup
        // (some only after a couple of awaits inside vpin.ready.then). Wait for it
        // so the restore broadcast isn't dropped by the guard in #connectWebSocket.
        await this.#waitForReceiveEvent();
        this.moveTo(index, { previous, reason: "restore" });
      }
    } catch (e) {
      this.call("console_out", `restoreInitialTable failed: ${e.message}`);
    }
  }

  // Resolve once window.receiveEvent is a function, or after the timeout so a
  // theme that never registers one can't hang startup.
  async #waitForReceiveEvent(timeoutMs = 2000, intervalMs = 25) {
    const deadline = Date.now() + timeoutMs;
    while (typeof window.receiveEvent !== "function" && Date.now() < deadline) {
      await new Promise(resolve => setTimeout(resolve, intervalMs));
    }
    return typeof window.receiveEvent === "function";
  }

  // Register an event handler for a specific event type
  // eventType: string (e.g., "TableIndexUpdate", "TableDataChange", etc.)
  // handler: function to call when event is received
  registerEventHandler(eventType, handler) {
    if (typeof handler === 'function') {
      if (!this.eventHandlers[eventType]) {
        this.eventHandlers[eventType] = [];
      }
      this.eventHandlers[eventType].push(handler);
      this.call("console_out", `Registered event handler for ${eventType}`);
    }
  }

  // Handle incoming events from window.receiveEvent
  // This should be called from the theme's receiveEvent function
  async handleEvent(message) {
    // A data change raised by the backend carries no index - the wheel position only
    // exists in the browser, and a theme assigns message.index straight to its wheel.
    // The game under the wheel is held rather than the number, because the refresh can
    // reorder: a finished session moves a game up a last-played wheel, and a Manager edit
    // can filter it out of the list entirely.
    const raised = message && typeof message === "object"
      && typeof message.index !== "number"
      && canonicalMessageType(message.type) === "TableDataChange";
    // A window that follows a named game holds that game, not the one its stale index
    // lands on, which is another game while the controller's is not in its list.
    const held = !raised ? null
      : (!this.isController() && this._followedId) || this.#identityAt(this._currentTableIndex);
    // Set now as well as after the refresh, so the message carries a usable index even
    // if the held game is gone. `raw` is the object the theme itself will read: the
    // alias copy below is core's, and writing only to that would leave a theme matching
    // on the 2.x spelling with the index this had before the refresh.
    const raw = message;
    if (raised) message.index = this._currentTableIndex;
    // A theme may post the pre-3.0 spelling; normalize before anything matches on it.
    if (message && MESSAGE_TYPE_CANONICAL[message.type]) {
      message = { ...message, type: canonicalMessageType(message.type) };
    }
    // A picker's index is a place in the picker, not on the wheel. A list a theme pushes
    // names its own id, so it is told from the wheel's by that.
    const onWheel = message.list === undefined
      || message.list === (this.collection || "builtin:all");
    if (typeof message.index === "number" && message.kind !== "collection") {
      if (message.type === "TableIndexUpdate" && typeof message.id === "string"
          && !this.isController()) {
        // The controller's index is into its list; this window finds the game in its own.
        const at = this.#follow(message.id);
        if (at < 0) {
          this.#renderWindowMedia();
          return;
        }
        raw.index = message.index = at;
      } else if (this.isController() || onWheel) {
        this._currentTableIndex = message.index;
        if (!this.isController() && message.type === "TableIndexUpdate") this.#followIndex();
      }
    }
    // A data change names the game it lands on in the list it is about to rebuild, and
    // there is no game to draw from yet: hold the window until the reload has the list.
    if (message.type === "TableDataChange" && !raised && !this.isController()
        && typeof message.index === "number" && this._followedId !== null) {
      this._followedId = typeof message.id === "string" ? message.id : "";
      this._followedAt = -1;
    }
    if (message.type === "AudioMuteChanged") {
      this.setAudioMuted(!!message.muted);
      return;
    }
    if (message.type === "AttractSettingsChanged") {
      this.#readAttractSettings();
      return;
    }
    // A press produced outside the browser - a phone, a button board. It is broadcast to
    // every window because Python does not know which one is the controller and should
    // not learn; that is knowledge this side already has, so the decision is made here.
    // Returning either way: this is core's message, and a theme sees only the action, by
    // the same path a keystroke reaches it.
    if (message.type === "InputAction") {
      if (this.isController()) this.#applyRemoteInput(message);
      return;
    }
    if (message.type === "CaptureArm") {
      this.#armCapture(!!message.armed, Number(message.ttl_ms) || 0,
                       { browser: message.browser, device: message.device });
      return;
    }
    if (message.type === "SelectionRequest") {
      if (this.isController() && message.window) {
        if (!this._restoreSettled) this._selectionAsks.add(message.window);
        else if (this.atRoot) {
          this.#announceIndex(this._currentTableIndex, { reason: "restore", to: message.window });
        }
      }
      return;
    }
    if (message.type === "ProposalPreview") {
      this.#showPreview(message.preview || null);
      return;
    }
    // Core's, like the preview: the digits are the machine's to show, so a theme's own
    // handlers never get them from here.
    if (message.type === "SignInRequest") {
      this.#showSignIn(message.request || null);
      return;
    }
    if (message.type === "LifecycleActing") {
      this.#showLifecycleNotice(message);
      // Still offered to the theme below: a theme may want to say it in its own voice,
      // and core's notice is what happens when none does.
    }
    this.#handleFrontendInputLifecycleEvent(message);

    // What an extension answered about one game. Applied by id rather than by index:
    // the wheel may have moved while core was waiting on somebody else's server, and
    // the entry this belongs to is not necessarily the one in front of the player.
    if (message.type === "EntryDataChange") {
      this.#applyEntryData(message);
    }

    if (message.type === "PlayersChanged") {
      this.players = message.state || { players: [] };
    }

    // Default handling for TableDataChange
    if (message.type === "TableDataChange") {
      if (this.isController()) this._lastSelectedIndex = null;
      await this.#handleTableDataChange(message);
      if (raised) {
        // A game the controller named while this reload was waiting is the one to land on,
        // and getTableData has already found it in the rebuilt list.
        const index = !this.isController() && this._followedId && this._followedAt >= 0
          ? this._followedAt : this.#indexOfIdentity(held);
        this._currentTableIndex = index;
        raw.index = message.index = index;
        // The controller's game can have left its list; the others still hold it, and
        // would go on showing a game the playfield no longer does.
        if (this.isController() && held && this.tableData.length && this.atRoot
            && this.#identityAt(index) !== held) {
          this.#announceIndex(index, { reason: "restore", others: true });
        }
      } else if (!this.isController() && this._followedId && this._followedAt >= 0) {
        raw.index = message.index = this._followedAt;
      }
      // The index the data change carried is in the list it just rebuilt.
      if (!this.isController() && this._followedId === "") this.#followIndex();
    }
    // A step of a scrub is for the theme's wheel and nothing else: the media, the audio
    // and everything that follows the selection wait for the settle.
    if (message.scrubbing) return this.#runEventHandlers(message);
    this.#syncSelectionFromMessage(message);
    this.#applyLaunchDim(message.type);
    this.#applyLaunchIndicator(message.type);
    if (VPinFECore.SELECTION_MESSAGES.has(message.type)) this.#renderWindowMedia();
    await this.#handleCoreAudioEvent(message);
    await this.#runEventHandlers(message);
  }

  // Call any custom handlers registered by the theme
  async #runEventHandlers(message) {
    if (this.eventHandlers[message.type]) {
      for (const handler of this.eventHandlers[message.type]) {
        await handler(message);
      }
    }
  }

  #applyEntryData(message) {
    const gameId = String((message && message.game_id) || "");
    const added = (message && message.ext) || null;
    if (!gameId || !added) return;
    // Merged rather than replaced: two extensions contribute two keys about the same
    // game, and they arrive as two messages.
    for (const item of this.tables || []) {
      if (String((item && item.game && item.game.id) || "") !== gameId) continue;
      item.ext = Object.assign({}, item.ext || {}, added);
      this.#setGameVPinPlayRating(item, item.ext.vpinplay || null);
      break;
    }
  }

  #applyWindowIdentity() {
    const windowName = this._windowName;
    const windowLabel = this.#getWindowLabel(windowName);

    window.name = windowName;
    document.title = `VPinFE ${windowLabel}`;
  }

  #getWindowLabel(windowName) {
    // Only the ones a title-cased name gets wrong. Everything else - table, playfield,
    // a declared topper - reads correctly from the name itself. Kept in step with
    // theme_windows.TITLES, which does the same job for the bootstrap page.
    const known = { bg: "BG", dmd: "DMD", scoreview: "Score View" };
    if (known[windowName]) return known[windowName];
    if (!windowName || windowName === "unknown") return "Window";
    return windowName.charAt(0).toUpperCase() + windowName.slice(1);
  }


  // The three overlays differ in four things: which flag says they are up, which iframe
  // they own, what they load, and what they are told when opened. Everything else - the
  // fade class, creating the frame once, the ten-millisecond wait so it does not flash,
  // hiding rather than destroying - was written out three times and drifted.
  static OVERLAYS = {
    menu: {
      frameId: "menu-frame",
      src: "/core/mainmenu/mainmenu.html",
      // table_index is mainmenu.js's own key, not ours to rename.
      opened: (core) => ({ event: "menu_open", table_index: core._currentTableIndex }),
    },
    collectionMenu: {
      frameId: "collection-menu-frame",
      src: "/core/collectionmenu/collectionmenu.html",
    },
    // Held back while a table runs, so it is never drawn over one. Its frame fills the
    // screen and dims it, because it is asking for attention rather than offering a menu.
    signIn: {
      frameId: "signin-frame",
      src: "/core/signin/signin.html",
      size: ["100vw", "100vh"],
      prepare: (core) => core.#signInToDraw(),
    },
    tutorial: {
      frameId: "tutorial-frame",
      src: "/core/tutorial/tutorial.html",
      // There is nothing to show without a URL, and this runs before anything is
      // closed - a missing tutorial must leave whatever is open alone.
      prepare: (core) => core.getCurrentTutorialUrl() || null,
      opened: (core, url) => ({
        event: "tutorial_open",
        tutorial_url: url,
        tutorial_proxy_url: core.buildTutorialProxyUrl(url),
        table_rotation: core.playfieldRotation,
      }),
    },
  };

  // The overlays are core's, so their mount is core's too. A theme that does not carry
  // the element used to take core down on the menu key - getElementById returned null and
  // the next line read classList off it.
  #overlayRoot() {
    let root = document.getElementById("overlay-root");
    if (!root) {
      root = document.createElement("div");
      root.id = "overlay-root";
      document.body.appendChild(root);
    }
    return root;
  }

  async #toggleOverlay(name) {
    const spec = VPinFECore.OVERLAYS[name];
    const overlayRoot = this.#overlayRoot();
    let iframe = document.getElementById(spec.frameId);

    if (this.overlay === name) {
      this.overlay = null;
      this.resumeAttract(name);
      overlayRoot.classList.remove("active");          // fade out
      if (iframe) {
        iframe.style.display = "none";                 // hide, never destroy
        if (iframe.contentWindow) {
          iframe.contentWindow.postMessage({ event: "overlay_close", overlay: name }, "*");
          // What the three pages we ship have always matched on. PAR-24's shape: the
          // current spelling first, the older one behind it.
          iframe.contentWindow.postMessage({ event: "reset state" }, "*");
        }
      }
      return;
    }

    const context = spec.prepare ? spec.prepare(this) : undefined;
    if (spec.prepare && context === null) return;

    if (this.overlay) await this.#toggleOverlay(this.overlay);

    this.overlay = name;
    this.suspendAttract(name);
    overlayRoot.classList.add("active");               // fade in
    if (!iframe) {
      iframe = document.createElement("iframe");
      iframe.src = spec.src;
      iframe.id = spec.frameId;
      iframe.className = "vpinfe-overlay-frame";
      iframe.setAttribute("allowTransparency", "true");
      iframe.style.display = "none";                   // start hidden to prevent a flash
      if (spec.size) [iframe.style.width, iframe.style.height] = spec.size;
      overlayRoot.appendChild(iframe);
      await new Promise(resolve => setTimeout(resolve, 10));   // let the DOM catch up
      this.#listenForKeysIn(iframe);
      this.#applyWordsIn(iframe);
    }

    iframe.style.display = "block";
    if (!iframe.contentWindow) return;
    iframe.contentWindow.postMessage({ event: "overlay_open", overlay: name, context }, "*");
    const legacy = spec.opened && spec.opened(this, context);
    if (legacy) iframe.contentWindow.postMessage(legacy, "*");
  }

  /**
   * Start, stop or restart the frontend, VPinFE or the machine.
   *
   * Scope is "frontend", "vpinfe" or "system"; action is "start", "stop" or "restart".
   * Confirms first when the user asked to be asked, and resolves false when they say no
   * or the build cannot do it, so a caller can leave its menu open.
   */
  async requestLifecycle(scope, action, reason = "") {
    if (!await this.#confirmLifecycle(scope, action)) return false;
    return await this.call("lifecycle_request", scope, action, reason, true);
  }

  /**
   * Ask before starting, stopping or restarting something, if the user asked to be asked.
   *
   * A theme that draws its own is welcome to: it calls lifecycle_request with
   * confirmed:true and never reaches this. This is the fallback, so turning the setting
   * on works on every theme, including the ones written before it existed.
   *
   * Built from the input actions rather than window.confirm, because a cabinet has no
   * keyboard and no mouse - select confirms and back cancels, on the buttons already
   * bound to them.
   */
  async #confirmLifecycle(scope, action) {
    const asking = await this.call("lifecycle_needs_confirmation", scope, action);
    if (!asking || !asking.confirm) return true;
    return this.#ask(asking.description);
  }

  // Select answers yes and Back no. Built node by node rather than from innerHTML: the
  // question may be wording from the bridge, and textContent cannot become markup.
  async #ask(what) {
    const root = document.createElement("div");
    root.className = "vpinfe-confirm";
    const card = document.createElement("div");
    card.className = "vpinfe-confirm-card";
    const question = document.createElement("p");
    question.className = "vpinfe-confirm-question";
    // One entry, so the question mark is the translator's - Spanish opens with
    // an inverted one and French puts a space before it.
    question.textContent = this.t("frontend.confirm.question", "{what}?", { what });
    const hint = document.createElement("p");
    hint.className = "vpinfe-confirm-hint";
    hint.textContent = this.t("frontend.confirm.hint",
                              "Select to confirm, Back to cancel");
    card.appendChild(question);
    card.appendChild(hint);
    root.appendChild(card);
    document.body.appendChild(root);

    const releaseMode = this.pushInputMode("modal");
    try {
      return await new Promise(resolve => { this.#pendingConfirm = resolve; });
    } finally {
      this.#pendingConfirm = null;
      releaseMode?.();
      root.remove();
      this.#syncSignIn();
    }
  }

  /**
   * Say what is about to happen, on a window that did not ask for it.
   *
   * A notice, never a question: this window has no say, and the surface that asked has
   * already had one. It is not dismissible and does not take the input, because there
   * is nothing to answer and the app is usually about to go away underneath it.
   */
  #showLifecycleNotice(message) {
    const existing = this.#lifecycleNotice;
    if (existing) existing.remove();

    const root = document.createElement("div");
    root.className = "vpinfe-notice";
    const text = document.createElement("p");
    text.className = "vpinfe-notice-text";
    text.textContent = message.description || "";
    root.appendChild(text);
    document.body.appendChild(root);
    this.#lifecycleNotice = root;
  }


  async #handleTableDataChange(message) {
    // Check if a collection filter was applied
    if (message.collection) {
      // if collection is "None" then reset to all tables, otherwise set to the selected collection.
      if (message.collection === "None") {
        await this.getTableData(true);
      } else {
        await this.call("set_tables_by_collection", message.collection);
        await this.getTableData();
      }
    } else if (message.filters) {
      // VPSdb filters - apply them to this window's API instance
      await this.callInternal("apply_filters",
        message.filters.letter,
        message.filters.theme,
        message.filters.type,
        message.filters.manufacturer,
        message.filters.year,
        message.filters.rating,
        message.filters.rating_or_higher
      );
      // If a sort order is also specified, apply it after filters
      if (message.sort) {
        await this.callInternal("apply_sort", message.sort, message.order);
      }
      await this.getTableData();
    } else if (message.sort) {
      // Sort order change - apply it to this window's API instance
      await this.callInternal("apply_sort", message.sort, message.order);
      await this.getTableData();
    } else {
      // No filters specified - just refresh the game data
      await this.getTableData();
    }
  }

  async #handleCoreAudioEvent(message) {
    if (!this.enabled("core_audio") || !this.isController()) return;

    if (message.type === "TableIndexUpdate") {
      // An attract step is not a person choosing a game; each one starting a track is
      // noise unless the person asked to hear them. What was playing stops with it.
      if (message.source === "attract" && !this._attract.settings?.audio) {
        this.stopTableAudio();
        return;
      }
      this.playTableAudio(this._currentTableIndex);
      return;
    }
    if (message.type === "TableLaunching" || message.type === "RemoteLaunching") {
      this.stopTableAudio();
      return;
    }
    if (message.type === "TableLaunchComplete" || message.type === "RemoteLaunchComplete") {
      this.playTableAudio(this._currentTableIndex);
      return;
    }
    if (message.type === "TableDataChange" && typeof message.index === "number") {
      this.playTableAudio(this._currentTableIndex);
    }
  }

  #resolveAudioUrl(indexOrUrl) {
    if (typeof indexOrUrl === "number" && Number.isFinite(indexOrUrl)) {
      this._currentTableIndex = indexOrUrl;
      return this.getAudioURL(indexOrUrl);
    }
    if (typeof indexOrUrl === "string") return indexOrUrl;
    return null;
  }

  #syncLocalIndexFromOutgoingMessage(message) {
    if (!message || typeof message !== "object") return;
    if (typeof message.index !== "number" || !Number.isFinite(message.index)) return;
    if (message.index < 0 || message.kind === "collection") return;
    if (message.type === "TableIndexUpdate" || message.type === "TableDataChange") {
      this._currentTableIndex = Math.floor(message.index);
    }
  }

  // The longest we wait between reconnect attempts. A cabinet can sleep for hours, so
  // there is no point trying every half second for all of it.
  static RECONNECT_CEILING_MS = 10000;

  // Which messages mean "the wheel is now on something else". TableIndexUpdate is an
  // ordinary step; the others mean the list or the game underneath may have changed, so
  // the notify has to fire again even when the index did not move.
  static SELECTION_MESSAGES = new Set([
    "TableIndexUpdate", "TableDataChange", "TableLaunchComplete", "RemoteLaunchComplete",
  ]);

  // How often a window that is not the controller asks which game is selected: quickly
  // for the first few seconds, then slowly for as long as no controller answers.
  static SELECTION_QUICK_MS = 5000;
  static SELECTION_ASK_MS = 400;
  static SELECTION_SLOW_MS = 5000;

  // How many preloaded URLs to remember. Generous enough to cover a page of the wheel
  // in both directions; small enough that forgetting all of them costs one page.
  static PRELOAD_MEMORY = 300;

  // How many wheel pictures are fetched ahead of the direction of travel and behind it.
  static SCRUB_AHEAD = 14;
  static SCRUB_BEHIND = 3;

  #syncSelectionFromMessage(message) {
    if (!message || typeof message !== "object") return;
    if (!this.isController()) return;
    if (!VPinFECore.SELECTION_MESSAGES.has(message.type)) return;
    if (message.type !== "TableIndexUpdate") this._lastSelectedIndex = null;
    this.#selectionChanged();
  }

  /**
   * Run something whenever the selection changes. This is how anything that follows the
   * wheel attaches - the rating fetch, the backend notify and preloading all do - rather
   * than being added to a list of message types by hand.
   */
  onSelection(listener) {
    if (typeof listener !== "function") return () => {};
    this._onSelection.push(listener);
    return () => {
      const at = this._onSelection.indexOf(listener);
      if (at >= 0) this._onSelection.splice(at, 1);
    };
  }

  #selectionChanged() {
    this.#renderWindowMedia();
    for (const listener of this._onSelection) {
      // One listener throwing must not stop the others, the same rule the backend's
      // event bus follows.
      try {
        listener(this._currentTableIndex);
      } catch (err) {
        console.warn("vpinfe: a selection listener failed", err);
      }
    }
  }

  /**
   * Ask what an empty library is waiting on, and ask again until nothing. The backend
   * sends every window the library once a folder answers.
   */
  async #watchForLibrary() {
    if (this._watchingLibrary) return;
    this._watchingLibrary = true;
    try {
      for (;;) {
        const waiting = await this.callInternal("library_waiting").catch(() => []);
        this.libraryWaiting = Array.isArray(waiting) ? waiting : [];
        this.#showWaiting();
        if (!this.libraryWaiting.length) return;
        await new Promise((resolve) => setTimeout(resolve, WAITING_POLL_MS));
      }
    } finally {
      this._watchingLibrary = false;
    }
  }

  #showWaiting() {
    const root = document.documentElement;
    if (!root) return;
    if (this.enabled("core_waiting") && this.libraryWaiting.length) {
      root.dataset.vpinfeWaiting = `${this.t("frontend.theme.waiting_for_tables",
        "Waiting for your tables")}\n${this.libraryWaiting.join(" · ")}`;
    } else {
      delete root.dataset.vpinfeWaiting;
    }
  }

  /**
   * Step this window aside while VPX has the screens.
   *
   * Set on the document element rather than the body, so it is there before a theme's
   * own stylesheet loads and a theme can key off it without guessing a class name.
   * TableLaunchComplete clears it - the same message that already tells every window the
   * game came back.
   */
  #applyLaunchDim(type) {
    if (!this.enabled("core_launch_dim")) return;
    const root = document.documentElement;
    if (!root) return;
    // dataset, like every other data-vpinfe-* this file sets.
    if (type === "TableLaunching") root.dataset.vpinfeLaunching = "true";
    else if (type === "TableLaunchComplete") delete root.dataset.vpinfeLaunching;
  }

  // The spinner stays up until the table says it is running, which is later than the dim
  // clears: TableLaunchComplete is the cleanup for a launch that never got that far.
  // `.vpinfe-launch-indicator` is what a theme restyles, and `launch_indicator.enabled`
  // false is for one that draws its own.
  #applyLaunchIndicator(type) {
    if (type === "TableLaunching") {
      if (!this.enabled("core_launch_indicator")) return;
      if (this._launchIndicator) return;
      const indicator = document.createElement("div");
      indicator.className = "vpinfe-launch-indicator";
      indicator.setAttribute("role", "status");
      indicator.setAttribute("aria-label",
        this.t("frontend.theme.loading_table", "Loading Table..."));
      const spinner = document.createElement("div");
      spinner.className = "vpinfe-launch-spinner";
      indicator.appendChild(spinner);
      // On <html>, not <body>: core_launch_dim fades the body to nothing while a game
      // launches, and a child of it would go with it.
      document.documentElement.appendChild(indicator);
      this._launchIndicator = indicator;
    } else if (type === "TableRunning" || type === "TableLaunchComplete") {
      this._launchIndicator?.remove();
      this._launchIndicator = null;
    }
  }

  /**
   * Show the selected game's media in every `[data-vpin-media]` element on the page.
   *
   * The attribute's value names the kind (`data-vpin-media="playfield"`); an empty value
   * means this window's own media. A theme opts in with the attribute and opts out by
   * leaving it off or setting `media_window.enabled` false. Nothing is guessed: an empty
   * value in a window that is not a display, or a kind that is not one, draws nothing
   * rather than picking something to draw.
   */
  #renderWindowMedia() {
    this.#showHeld();
    if (!this.enabled("core_media_window")) return;
    if (this.#mediaWaits()) return;
    const held = this.#selectionHeld();
    for (const target of document.querySelectorAll("[data-vpin-media]")) {
      if (held) {
        this.#mediaShown.delete(target);
        target.replaceChildren();
        continue;
      }
      const named = target.getAttribute("data-vpin-media").trim();
      const kind = named ? this.#canonicalKind(named) : this.windowMediaKind;
      if (kind && MEDIA_KINDS.includes(kind)) this.#renderMediaInto(target, kind);
    }
  }

  // The first press of a run draws at once; the presses after it draw once, when it pauses.
  #mediaWaits() {
    const at = this._currentTableIndex;
    const now = Date.now();
    if (at === this._mediaAt) return this._mediaTimer !== null;
    const inRun = now - this._mediaMovedAt < this.mediaSettleMs;
    this._mediaAt = at;
    this._mediaMovedAt = now;
    clearTimeout(this._mediaTimer);
    this._mediaTimer = null;
    if (!inRun) return false;
    this._mediaTimer = setTimeout(() => {
      this._mediaTimer = null;
      this.#renderWindowMedia();
    }, this.mediaSettleMs);
    return true;
  }

  #renderMediaInto(target, kind) {
    const index = this._currentTableIndex;
    const prefer = target.getAttribute("data-vpin-media-prefer")?.trim();
    const media = this.#windowMedia(index, kind, prefer);
    if (!media) return target.replaceChildren();

    const row = this.tableData[index];
    const shown = this.#mediaShown.get(target);
    if (shown && shown.url === media.url && shown.row === row
        && target.children?.[0] === shown.node) return;

    const node = document.createElement(media.video ? "video" : "img");
    node.src = media.url;
    node.alt = this.tableData[index]?.game?.name || "";
    if (media.video) {
      Object.assign(node, { autoplay: true, loop: true, muted: true, playsInline: true });
    }
    if (kind.startsWith("playfield") && this.enabled("core_layout")) {
      node.classList.add("vpinfe-playfield-media");
      this.applyPlayfieldMediaRotation(node);
    }
    this.#mediaShown.set(target, { node, url: media.url, row });
    target.replaceChildren(node);
  }

  /**
   * What `[data-vpin-media]` draws for a kind: `{ url, video }`, or null when there is
   * nothing to draw. The user's image-or-video priority decides unless the element says
   * `data-vpin-media-prefer` "image" or "video"; either falls back to the other.
   */
  #windowMedia(index, kind, prefer) {
    const item = this.tableData[index];
    if (!item) return null;

    if (["playfield", "backglass", "scoreview"].includes(kind)) {
      const media = prefer === "image" || prefer === "video"
        ? this._reader.imageVideo(item, kind, prefer)
        : this.getMedia(index, kind);
      return media.kind === "image" || media.kind === "video"
        ? { url: media.url, video: media.kind === "video" }
        : null;
    }

    // Every other kind is named in a pair with its video: the still, then `<kind>_video`.
    const order = prefer === "image" ? [kind, `${kind}_video`] : [`${kind}_video`, kind];
    const wanted = order.find((name) => {
      if (!MEDIA_KINDS.includes(name)) return false;
      // `missing` still answers with the placeholder URL, so truthiness is not enough -
      // it would put the file-missing image on screen inside a <video>.
      const media = this.getMedia(index, name);
      return media.url && media.kind !== "missing";
    });
    return wanted
      ? { url: this.getMediaURL(index, wanted), video: wanted.endsWith("_video") }
      : null;
  }

  // Neighbors only, and only once the wheel has stopped moving.
  #schedulePreload() {
    if (!this.enabled("core_preload")) return;
    clearTimeout(this._preloadTimer);
    this._preloadTimer = setTimeout(() => this.#preloadNeighbors(), this._preloadSettleMs);
  }

  #preloadNeighbors() {
    this._preloadTimer = null;
    const at = this._currentTableIndex;
    for (const index of [at - 1, at, at + 1]) {
      if (index < 0 || index >= this.tableData.length) continue;
      for (const kind of this._preloadKinds) this.#preload(this.getImageURL(index, kind));
    }
  }

  // The small wheel pictures on both sides of the wheel, further in the direction it is
  // travelling, so the cards it is about to show are there before it gets to them. A
  // theme that sets scrubAware draws its wheel from these.
  #preloadAround(at, direction) {
    const list = this.activeList();
    const sign = direction === "previous" ? -1 : 1;
    for (let step = -VPinFECore.SCRUB_BEHIND; step <= VPinFECore.SCRUB_AHEAD; step++) {
      this.#preload(this.getThumbnailURL(list.indexAfter(sign * step), "wheel"));
    }
  }

  #preload(url) {
    if (!url || url === MISSING_MEDIA_URL || this._preloaded.has(url)) return;
    // Emptied wholesale rather than evicted one at a time. It exists to stop a re-fetch
    // on the way back through the wheel, and a large library would otherwise grow it
    // without limit.
    if (this._preloaded.size >= VPinFECore.PRELOAD_MEMORY) this._preloaded.clear();
    this._preloaded.add(url);
    Object.assign(new Image(), { decoding: "async", src: url });
  }

  async #notifySelectedGame() {
    if (!this.isController()) return;
    if (!Array.isArray(this.tableData) || this.tableData.length === 0) return;

    const index = Math.floor(this._currentTableIndex);
    if (!Number.isFinite(index) || index < 0 || index >= this.tableData.length) return;
    if (this._lastSelectedIndex === index) return;

    this._lastSelectedIndex = index;
    try {
      await this.call("notify_table_selected", index);
    } catch (e) {
      this._lastSelectedIndex = null;
      this.call("console_out", `notify_table_selected failed: ${e.message}`);
    }
  }

  #coerceNumber(...values) {
    for (const value of values) {
      if (value === undefined || value === null || value === "") continue;
      const numeric = Number(value);
      if (!Number.isNaN(numeric) && Number.isFinite(numeric)) return numeric;
    }
    return null;
  }

  #itemByIndex(index) {
    const numeric = Number(index);
    if (!Number.isFinite(numeric)) return null;
    const normalized = Math.floor(numeric);
    if (!Array.isArray(this.tableData) || normalized < 0 || normalized >= this.tableData.length) return null;
    return this.tableData[normalized];
  }

  #vpsIdOf(item) {
    return item && typeof item === "object" ? this._reader.vpsId(item) : "";
  }

  /** What the wheel is standing on, in terms that survive the list being rebuilt. */
  #identityAt(index) {
    const item = this.#itemByIndex(index);
    return item && typeof item === "object" ? (this._reader.identity(item) || null) : null;
  }

  /**
   * Where a held game sits now, or the index we already had.
   *
   * A game can leave the list - an edit filters it out, a collection drops it - and
   * there is no good answer for where the player then is. Staying put is the least
   * surprising of the bad answers, and the clamp in getTableData keeps it in range.
   */
  #indexOfIdentity(held) {
    if (!held || !Array.isArray(this.tableData)) return this._currentTableIndex;
    const found = this.tableData.findIndex(item =>
      item && typeof item === "object" && this._reader.identity(item) === held);
    return found >= 0 ? found : this._currentTableIndex;
  }



  /**
   * Keep `item.vpinplay` written from `entry.ext.vpinplay`.
   *
   * A shim, and it stays until contract 1 retires: twelve published themes read
   * `item.vpinplay` and were built before there was an `ext` slot to read instead. New
   * themes should read the slot - it is where every extension's contribution arrives,
   * not just this one's.
   */
  #applyVPinPlayShim() {
    if (!Array.isArray(this.tableData)) return;
    for (const item of this.tableData) {
      if (!item || typeof item !== "object") continue;
      this.#setGameVPinPlayRating(item, (item.ext && item.ext.vpinplay) || null);
    }
  }

  #setGameVPinPlayRating(item, payload) {
    if (!item || typeof item !== "object") return;
    item.vpinplay = payload ? { ...payload } : null;
  }





  #fadeAudio(from, to, onComplete) {
    clearInterval(this._audioFadeId);
    if (!this._audio) {
      if (onComplete) onComplete();
      return;
    }
    this._audio.volume = from;
    if (this._audioFadeDuration <= 0 || from === to) {
      this._audio.volume = to;
      if (onComplete) onComplete();
      return;
    }
    const steps = this._audioFadeDuration / 20;
    const delta = (to - from) / steps;
    this._audioFadeId = setInterval(() => {
      const next = this._audio.volume + delta;
      if ((delta > 0 && next >= to) || (delta < 0 && next <= to) || delta === 0) {
        this._audio.volume = to;
        clearInterval(this._audioFadeId);
        if (onComplete) onComplete();
      } else {
        this._audio.volume = next;
      }
    }, 20);
  }

  #audioResumePlay() {
    if (this._audioMuted) return;
    const url = this._audioCurrentUrl;
    if (!url) return;
    this._audio.play().then(() => {
      if (this._audioCurrentUrl === url) this.#fadeAudio(0, this._audioMaxVolume);
    }).catch(() => {});
  }

  // **********************************************
  // private functions
  // **********************************************

  #connectWebSocket() {
    const wsUrl = `${this.endpoints.frontend_channel}?window=${this._windowName}`;
    console.log(`[WS] Connecting to ${wsUrl}`);
    this._ws = new WebSocket(wsUrl);

    this._ws.onopen = async () => {
      console.log("[WS] Connected to bridge");
      this._reconnectDelayMs = 0;
      await this.#onBridgeReady();
      await this.#wordsLoaded;
      // ready is a one-shot: a theme awaits it once at startup, and resolving an
      // already-resolved promise on every reconnect is a no-op rather than a second
      // startup.
      this._resolveReady();
    };

    this._ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.type === 'api_response') {
        const pending = this._pendingCalls[data.id];
        if (pending) {
          delete this._pendingCalls[data.id];
          if (data.error) {
            pending.reject(new Error(data.error));
          } else {
            pending.resolve(data.result);
          }
        }
      } else if (data.type === 'event') {
        this.#handleFrontendInputLifecycleEvent(data.message);
        // Handle pushed events from Python. A theme's receiveEvent is expected to call
        // vpin.handleEvent itself, so calling both would handle the message twice.
        // With no theme script on the page there is nobody to call it - and a window
        // core draws by itself has no reason to carry one - so core pumps its own.
        if (typeof window.receiveEvent === 'function') {
          window.receiveEvent(data.message);
        } else {
          this.handleEvent(data.message);
        }
        // Forward to iframes if requested
        if (data.forward_iframe) {
          const iframes = document.querySelectorAll('iframe');
          iframes.forEach(iframe => {
            try {
              iframe.contentWindow.postMessage({ vpinfeEvent: data.message }, '*');
            } catch (e) { /* cross-origin, ignore */ }
          });
        }
      }
    };

    this._ws.onclose = () => {
      console.log("[WS] Disconnected from bridge");
      this.#scheduleReconnect();
    };

    this._ws.onerror = (err) => {
      console.error("[WS] WebSocket error:", err);
    };
  }

  /**
   * Come back after the bridge goes away.
   *
   * Without this a cabinet that sleeps wakes up dead: the sockets close with 1006, the
   * three windows stay on screen showing the last game, and nothing reconnects - so no
   * button reaches the backend and the frontend looks fine while doing nothing. The same
   * hole swallowed a backend restart under a running frontend.
   *
   * Backs off to a ceiling rather than hammering a bridge that may be gone for hours,
   * and reconnecting re-runs #onBridgeReady, so config, game data and this window's
   * media are all re-read rather than assumed to have survived.
   */
  #scheduleReconnect() {
    if (this._reconnectTimer || this._shuttingDown) return;
    this._reconnectDelayMs = Math.min((this._reconnectDelayMs || 500) * 2,
                                      VPinFECore.RECONNECT_CEILING_MS);
    const delay = this._reconnectDelayMs;
    console.log(`[WS] Reconnecting in ${delay}ms`);
    this._reconnectTimer = setTimeout(() => {
      this._reconnectTimer = null;
      this.#connectWebSocket();
    }, delay);
  }

  async #onBridgeReady() {
    console.log("WebSocket bridge is ready!");
    try {
      this.#applyContract(await this.call("get_theme_contract"));
    } catch (_e) {
      this.#applyContract(OLDEST_CONTRACT);   // an older build cannot answer; assume 1
    }
    try {
      const windows = await this.call("get_theme_windows");
      if (Array.isArray(windows) && windows.length) this.windows = windows;
    } catch (_e) {
      /* an older build cannot answer; the default below already covers it */
    }
    try {
      this.setAudioMuted(await this.call("get_audio_muted"));
    } catch (_e) {
      this.setAudioMuted(false);
    }
    try {
      this.themeConfig = await this.call("get_theme_config");
    } catch (e) {
      this.themeConfig = {};
    }
    if (!this.themeConfig || typeof this.themeConfig !== "object") {
      this.themeConfig = {};
    }

    const audioCfg = (this.themeConfig && typeof this.themeConfig.audio === "object")
      ? this.themeConfig.audio
      : {};
    this.#applyCapabilityConfig();
    this.setAudioOptions(audioCfg);
    this.#startAttract();

    // Load network config. The url already carried these, so this now only corrects a
    // page opened without them - `endpoints` derives from the ports, so correcting one
    // corrects every url built from it.
    this.themeAssetsPort = await this.call("get_theme_assets_port");
    try {
      const ownPort = await this.call("get_http_port");
      // What this install serves on. It is the library's port too, unless one
      // elsewhere said otherwise in the url - correcting that here would point the page
      // back at itself for the library.
      this.devicePort = ownPort;
      if (!this.libraryHost) this.libraryPort = ownPort;
    } catch (_e) {
      /* an older build cannot answer; the 8001 default already covers it */
    }
    try {
      this.mediaPriorities = this.#normalizeMediaPriorities(await this.call("get_media_priorities"));
    } catch (_e) {
      this.mediaPriorities = Object.assign({}, DEFAULT_MEDIA_PRIORITIES);
    }
    // Load display config
    this.playfieldOrientation = await this.call("get_playfield_orientation");
    this.playfieldRotation = await this.call("get_playfield_rotation");
    this.layout = this.#resolveLayout(await this.call("get_cab_mode"));
    this.playfieldMediaRotation = await this.call("get_playfield_media_rotation");
    this.#publishLayout();
    await this.#loadMonitors();
    await this.#loadPlayers();
    await this.getTableData();

    // The controller only broadcasts a restore when the remembered index is past the
    // first game, so a window that waited to be told would stay empty at index 0. It asks
    // instead, and draws once the answer names the game.
    if (this.isController()) this.#renderWindowMedia();
    else this.#awaitSelection();
    this.#reportWindow();
   //this.#overrideConsole(); //disabled for now...

    // only run on the table window.. Its the master controller for all screens/windows
    if (this.isController()) {
      await this.#initKeyboardMapping();
      await this.#initGamepadMapping();
      this.#setupGamepadListeners();
      this.#updateGamepads();           // No await needed here — runs loop
      this.#watchPlayState();           // Subscribe to remote launch events
      this.#pullSignIn();
      this.#probePlayback();
    }
  }

  // Where this window is, for a support summary. Once per page load, a moment after the
  // window has been placed. A native Wayland window reports 0,0 for its position, because the
  // compositor does not tell a window where it is.
  async #reportWindow() {
    if (this._windowReported) return;
    this._windowReported = true;
    await new Promise((resolve) => setTimeout(resolve, 2000));
    try {
      await this.callInternal("report_window", {
        window: this._windowName, x: window.screenX, y: window.screenY,
        width: window.innerWidth, height: window.innerHeight, scale: window.devicePixelRatio,
      });
    } catch (e) {
      this._windowReported = false;
      console.warn("[VPinFE] Could not report where this window opened", e);
    }
  }

  // Once per page load, although its caller runs again on every reconnect.
  async #probePlayback() {
    if (this._playbackProbed) return;
    this._playbackProbed = true;
    await new Promise((resolve) => setTimeout(resolve, 3000));
    const probe = document.createElement("video");
    const canPlay = {};
    for (const [id, type] of PLAYBACK_TYPES) canPlay[id] = probe.canPlayType(type);
    const decoded = {};
    for (const id of ["h264", "vp9"]) decoded[id] = await decodesPicture(`/core/probe/${id}.mp4`);
    let brands = [];
    try {
      const hints = await navigator.userAgentData?.getHighEntropyValues(["fullVersionList"]);
      brands = hints?.fullVersionList || [];
    } catch (e) {
      brands = [];
    }
    let features = {};
    try {
      features = await probeFeatures();
    } catch (e) {
      features = {};
    }
    try {
      await this.callInternal("report_browser", {
        user_agent: navigator.userAgent, brands, can_play: canPlay, decoded, features,
      });
    } catch (e) {
      console.warn("[VPinFE] Could not report what this browser plays", e);
    }
  }

  #setupGamepadListeners() {
    // Listen for gamepad connection events
    window.addEventListener("gamepadconnected", (e) => {
      this.call("console_out", `Gamepad connected: ${e.gamepad.id} (index ${e.gamepad.index})`);
      // Reset button states for this gamepad
      this.previousButtonStates[e.gamepad.index] = new Array(e.gamepad.buttons.length).fill(false);
    });

    window.addEventListener("gamepaddisconnected", (e) => {
      this.call("console_out", `Gamepad disconnected: ${e.gamepad.id} (index ${e.gamepad.index})`);
      delete this.previousButtonStates[e.gamepad.index];
      this.#dropHeld();
    });

    // Check if gamepad is already connected (may have been connected before page load)
    this.#waitForGamepad();
  }

  #waitForGamepad(attempts = 0, maxAttempts = 30) {
    const gamepads = navigator.getGamepads();
    const hasGamepad = Array.from(gamepads).some(gp => gp !== null);

    if (hasGamepad) {
      this.call("console_out", "Gamepad detected and ready");
      return;
    }

    if (attempts < maxAttempts) {
      // Retry every 500ms for up to 15 seconds
      setTimeout(() => this.#waitForGamepad(attempts + 1, maxAttempts), 500);
    } else {
      this.call("console_out", "No gamepad detected after waiting. Will detect when connected.");
    }
  }

  /**
   * Hold attract mode off for `reason`, and stop it if it is moving the wheel. Keyed by
   * reason: attract runs again only once every reason that held it has resumed. A theme
   * that draws its own picker calls this while the picker is up.
   */
  suspendAttract(reason = "theme") {
    this._attract.suspended.add(String(reason));
    this.#endAttract({ restore: false });
    this.#armAttract();
  }

  resumeAttract(reason = "theme") {
    this._attract.suspended.delete(String(reason));
    this.#armAttract();
  }

  get attractActive() { return this._attract.active; }

  async #startAttract() {
    if (!this.isController()) return;
    await this.#readAttractSettings();
  }

  // Also the answer to the Manager changing one. A run in progress goes on with the new
  // timings, keeping where it began; one that is no longer allowed ends as a press would.
  async #readAttractSettings() {
    if (!this.isController()) return;
    try {
      this._attract.settings = await this.callInternal("get_attract_config");
    } catch (_e) {
      return;   // an older build cannot answer; there is no attract mode to run
    }
    if (!this.#attractAllowed()) this.#endAttract({ restore: true });
    this.#armAttract();
  }

  // Both the person and the theme have to allow it. The person's off is final, and a
  // theme that opted out is not overridden by the person's on.
  #attractAllowed() {
    const settings = this._attract.settings;
    return !!settings && settings.enabled && this.enabled("core_attract")
      && this.isController();
  }

  // Whether this moment is one the wheel may move in: the entry list is what is on
  // screen, there is more than one game to show, and nothing has asked for the person's
  // attention. The library's size is checked here and not in `#attractAllowed` because
  // the bridge answers before the library has loaded, and the timer has to be running
  // by then.
  #attractMayStep() {
    return this.tableData.length > 1 && this.atRoot && !this.overlay && this.inputMode === "navigation"
      && !this.#pendingConfirm && !this.#preview && this.frontendInputEnabled
      && !this._attract.suspended.size;
  }

  #armAttract() {
    const state = this._attract;
    clearTimeout(state.timer);
    state.timer = null;
    if (!this.#attractAllowed() || state.suspended.size) return;
    const wait = state.active ? state.settings.step_ms : state.settings.idle_ms;
    state.timer = setTimeout(() => this.#attractTick(), wait);
  }

  #attractTick() {
    const state = this._attract;
    state.timer = null;
    if (this.#attractAllowed() && this.#attractMayStep()) {
      if (!state.active) {
        state.active = true;
        state.origin = this._currentTableIndex;
        state.originList = this.tableData;
      }
      this.#attractStep();
    }
    this.#armAttract();
  }

  #attractStep() {
    const list = this.activeList();
    const previous = list.cursor;
    if (this._attract.settings.order === "in_order") {
      this.moveTo(list.indexAfter(1), { previous, direction: "next", source: "attract" });
      return;
    }
    const index = this.#nextFromBag(previous, list.length);
    if (index === null) return;
    this.moveTo(index, { previous, reason: "page", source: "attract" });
  }

  // The bag is rebuilt when the list it was shuffled from is replaced.
  #nextFromBag(current, length) {
    const state = this._attract;
    if (state.bagFor !== this.tableData || state.bag.length === 0) {
      state.bagFor = this.tableData;
      state.bag = Array.from({ length }, (_, i) => i).filter((i) => i !== current);
      for (let i = state.bag.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [state.bag[i], state.bag[j]] = [state.bag[j], state.bag[i]];
      }
    }
    while (state.bag.length) {
      const index = state.bag.pop();
      if (index !== current && index < length) return index;
    }
    return null;
  }

  /**
   * Someone pressed something. Returns true when attract was moving the wheel and the
   * press only woke it: the game on screen was not the person's, so a press that went on
   * to act on it could launch one they never chose.
   */
  #attractActivity() {
    const woke = this.#endAttract({ restore: true });
    this.#armAttract();
    return woke;
  }

  #endAttract({ restore }) {
    const state = this._attract;
    if (!state.active) return false;
    state.active = false;
    const origin = state.origin;
    state.origin = null;
    const returning = restore && state.settings && state.settings.on_return === "restore"
      && origin !== null && origin !== this._currentTableIndex
      && state.originList === this.tableData;
    if (returning) {
      this.moveTo(origin, { reason: "restore" });
    } else if (restore && this.enabled("core_audio")) {
      this.playTableAudio(this._currentTableIndex);   // it was silent while attract moved
    }
    return restore;
  }

  #setFrontendInputEnabled(enabled) {
    this.frontendInputEnabled = !!enabled;
    this.gamepadEnabled = this.frontendInputEnabled;
  }

  #handleFrontendInputLifecycleEvent(message) {
    if (!message || typeof message !== "object") return;

    if (message.type === "TableLaunching" || message.type === "RemoteLaunching") {
      this._launchInputSuppressedByLifecycle = true;
      this.#armCapture(false, 0);
      this.#setFrontendInputEnabled(false);
      this.suspendAttract("launch");
    } else if (message.type === "TableLaunchComplete" || message.type === "RemoteLaunchComplete") {
      this._launchInputSuppressedByLifecycle = false;
      this.#setFrontendInputEnabled(true);
      this.resumeAttract("launch");
    } else {
      return;
    }
    this.#syncSignIn();
    this.#drawRecording();
  }

  async #handleCorePaging(action) {
    // One page request in flight at a time; presses during the round trip are
    // dropped rather than queued against a stale index.
    if (this._pagingInFlight) return;
    this._pagingInFlight = true;
    try {
      const direction = action === "page_previous" ? "prev" : "next";
      const index = await this.call("get_page_index", this._currentTableIndex, direction);
      if (typeof index === "number" && index >= 0 && index !== this._currentTableIndex) {
        // Through moveTo like every other index path, so a page carries previous,
        // direction and reason rather than an index a theme cannot place.
        this.moveTo(index, {
          direction: direction === "prev" ? "previous" : "next",
          reason: "page",
        });
      }
    } catch (e) {
      this.call("console_out", `Core paging failed: ${e.message}`);
    } finally {
      this._pagingInFlight = false;
    }
  }

  async #triggerInputAction(action) {
    if (!this.frontendInputEnabled) return;

    let handlers, named = action;
    if (this.overlay) handlers = this.overlayHandlers[this.overlay] || [];
    else {
      handlers = this.inputHandlers;   // no menu is up: the theme's own handler
      // The theme's handler is the only one outside this repo, so it is the only one
      // that gets the name its contract published. Every `case "joyleft"` in the twelve
      // registry themes keeps matching; the overlays above take the current names.
      if (this.contract < 2) named = LEGACY_ACTION_NAMES[action] || action;
    }

    // Handlers are async, so a rejection needs catching as well as a throw. Dropped, a
    // theme's own "is animating" guard never clears and the wheel stays dead.
    for (const handler of handlers) {
      try {
        const result = handler(named);
        if (result && typeof result.catch === "function") {
          result.catch(e => this.call("console_out",
            `Theme input handler failed on ${action}: ${e && e.message}`));
        }
      } catch (e) {
        this.call("console_out", `Theme input handler threw on ${action}: ${e && e.message}`);
      }
    }
  }

  #normalizeKeyboardToken(token) {
    const normalized = String(token || '').trim().toLowerCase();
    const aliases = {
      esc: 'escape',
      return: 'enter',
      ' ': 'space',
      spacebar: 'space',
    };
    return aliases[normalized] || normalized;
  }

  #parseKeyboardBinding(value) {
    if (typeof value !== 'string') return [];
    return value
      .split(',')
      .map(token => this.#normalizeKeyboardToken(token))
      .filter(Boolean);
  }

  #eventKeyboardTokens(e) {
    return new Set([
      this.#normalizeKeyboardToken(e.key),
      this.#normalizeKeyboardToken(e.code),
    ].filter(Boolean));
  }

  // --- Chords -------------------------------------------------------------------
  //
  // The members fire, and their repeat stops once every one of them
  // is down. So holding both flippers moves the wheel one step each way and then exits,
  // rather than spinning it for the length of the hold - and no latency is added to a
  // flipper, which is the one thing that must not happen here.

  // Recording and deciding are separate, because they happen at different moments. What
  // is down has to be known *before* an unbound key returns early, or a chord of two
  // buttons that do nothing on their own could never complete; the chord itself is
  // considered *after* the member has fired, so both flippers fire in the order they
  // were pressed and the chord follows.
  #noteDown(token) {
    if (!token || !this.chordMembers || !this.chordMembers.has(token)) return;
    (this._inputsDown ||= new Set()).add(token);
  }

  #inputUp(token) {
    if (!token || !this._inputsDown) return;
    this._inputsDown.delete(token);
    this.#reconsiderChords();
  }

  // Whether every member of a chord is currently held.
  #chordComplete(chord) {
    const down = this._inputsDown || new Set();
    return chord.members.every(one => down.has(one));
  }

  // True while some complete chord claims this input, which is when its own action
  // stops repeating.
  #heldByChord(token) {
    return (this.chordBindings || []).some(
      chord => chord.members.includes(token) && this.#chordComplete(chord));
  }

  #reconsiderChords() {
    for (const chord of this.chordBindings || []) {
      const complete = this.#chordComplete(chord);
      if (!complete) {
        // Let go of any member and the hold is off. Cleared rather than left to expire,
        // so a second press does not inherit the first one's timer.
        if (chord.timer) { clearTimeout(chord.timer); chord.timer = null; }
        chord.firing = false;
        continue;
      }
      if (chord.firing) continue;
      chord.firing = true;
      if (!chord.holdMs) { this.#dispatchAction(chord.action); continue; }
      chord.timer = setTimeout(() => {
        chord.timer = null;
        // Checked again: the timer outlives a release by however long is left on it.
        if (this.#chordComplete(chord)) this.#dispatchAction(chord.action);
      }, chord.holdMs);
    }
  }

  #actionForKeyboardEvent(e) {
    const eventTokens = this.#eventKeyboardTokens(e);
    for (const [action, bindings] of Object.entries(this.keyActionMap)) {
      if (bindings.some(binding => eventTokens.has(binding))) {
        return action;
      }
    }
    return null;
  }

  // Key events go to whichever document has focus, so once a touch or a click moves focus
  // into an overlay's iframe the window listener stops seeing them. Core listens on the
  // overlay's own window too - same origin, so this is allowed - which is what makes one
  // dispatch and one set of bindings work wherever focus happens to be.
  #listenForKeysIn(iframe) {
    // Nothing here may throw: this runs while an overlay is opening, and the socket is
    // not necessarily up - so it cannot report a failure through the bridge either.
    try {
      const frameWindow = iframe && iframe.contentWindow;
      if (!frameWindow || typeof frameWindow.addEventListener !== "function") return;
      frameWindow.addEventListener("keydown", (e) => this.#onKeyDown(e));
      frameWindow.addEventListener("keyup", (e) => this.#onKeyUp(e));
      this.#reportUncaughtFrom(
        frameWindow, (iframe.id || "").replace(/-frame$/, "") || "overlay");
    } catch {
      // Cross-origin, or a frame that went away while opening. The parent window's
      // listener still covers every case except focus being inside the frame.
    }
  }

  /**
   * Forward what a frame throws to the log, because otherwise nobody sees it.
   *
   * An overlay is an iframe with its own browser log, and nothing reads it on a cabinet.
   * A ReferenceError in the menu left it silently not responding to any key, with a
   * clean vpinfe.log - the only clue was pressing a button and watching nothing happen.
   * console_out already reaches the log; what was missing was anything calling it when
   * the code threw before it could.
   */
  #reportUncaughtFrom(frameWindow, label) {
    // The window is prefixed by console_out from the connection it arrived on; `label`
    // is the overlay within it, so a fault reads [playfield/menu] rather than as the
    // theme's own.
    const report = (what) => {
      // Never through this.call: the socket may be down, and a reporter that throws
      // while reporting is worse than the fault it was sent to describe.
      try { this.call("console_out", `threw: ${what}`, label); } catch { /* ignore */ }
    };
    frameWindow.addEventListener("error", (e) => {
      // A setTimeout callback has no script URL, and "(?:1)" is punctuation pretending
      // to be a location.
      const file = (e.filename || "").split("/").pop();
      report(`${e.message}${file ? ` (${file}:${e.lineno})` : ""}`);
    });
    frameWindow.addEventListener("unhandledrejection", (e) =>
      report(`unhandled rejection: ${(e.reason && e.reason.message) || e.reason}`));
  }

  // ── Input modes ───────────────────────────────────────────────────────────
  // What a keypress means depends on what is on screen. Without this each overlay
  // hand-rolls its own flag, and the one that has none takes the arrows for the menu
  // behind it.
  //
  //   navigation  the default; actions reach the theme or the top overlay
  //   modal       a dialog owns them; select activates, back dismisses
  //   text        keystrokes belong to the focused field; back and select still act
  pushInputMode(mode) {
    if (!["navigation", "modal", "text"].includes(mode)) return null;
    this._inputModes.push(mode);
    return () => this.popInputMode(mode);
  }

  popInputMode(mode) {
    const at = this._inputModes.lastIndexOf(mode);
    if (at > 0) this._inputModes.splice(at, 1);   // never pop the base mode
  }

  get inputMode() {
    return this._inputModes[this._inputModes.length - 1];
  }

  // The wheel as a list. Built per call rather than held, so `tableData` stays the one
  // place the entries live - two copies of the list is the bug this is meant to retire,
  // not one to introduce.
  wheelList() {
    return new NavigableList(this.tableData, {
      id: this.collection || "builtin:all",
      cursor: this._currentTableIndex,
    });
  }

  // A list a theme's own surface can borrow, reached the same way the overlays reach
  // everything else. Core keeps the arithmetic so a fourth copy is never written.
  createList(items, options) { return new NavigableList(items, options); }

  /**
   * The list the input actions are moving. The wheel until something is pushed on top.
   *
   * Derived rather than stored so the wheel's cursor cannot fall out of step with
   * `_currentTableIndex`, which thirty-odd places still read.
   */
  activeList() { return this._listStack[this._listStack.length - 1] || this.wheelList(); }

  /** Whether the wheel is what the cursor is on. */
  get atRoot() { return this._listStack.length === 0; }

  /**
   * Descend into a list. The wheel keeps its cursor untouched underneath, so coming back
   * lands where the player left rather than where they were browsing.
   */
  pushList(list) {
    if (!(list instanceof NavigableList)) return this.activeList();
    // The settle is announced for the wheel, so it goes out before the wheel is covered.
    this.#settleScrub();
    this._listStack.push(list);
    this.#announceIndex(list.cursor, { previous: list.cursor, reason: "enter" });
    return list;
  }

  /**
   * Open the collection picker as a list core holds and moves.
   *
   * The theme renders it from the index messages, which now say which list they are
   * about - so a theme needs no mode flag of its own, and no `leaveCollectionMode`.
   */
  async openCollectionPicker() {
    const rows = await this.callInternal("get_collection_picker_items");
    if (!Array.isArray(rows) || rows.length < 2) return null;
    const allGames = this.t("frontend.collectionmenu.all_games", "All Games");
    const items = rows.map((row) => ({ ...row, label: row.name || allGames }));
    return this.pushList(new NavigableList(items, {
      id: "collections", kind: "collection",
      cursor: Math.max(0, items.findIndex((row) => row.showing)),
    }));
  }

  /**
   * Act on what the cursor is on. A collection is applied and the picker closes; at the
   * root the theme still owns select, because that is where launching lives.
   */
  async selectCurrent() {
    if (this.atRoot) return false;
    const item = this.activeList().current;
    this.popList();
    if (!item) return true;
    if (!item.name) {
      await this.getTableData(true);
      this.sendMessageToAllWindowsIncSelf({
        type: "TableDataChange", index: 0, collection: "None", ...this.#firstGame(),
      });
      return true;
    }

    // Three steps, and skipping either of the last two is why this looked wired and was
    // not: the backend swaps the view, this window still holds the previous list, and
    // the other windows have heard nothing at all.
    await this.call("set_tables_by_collection", item.name);
    await this.getTableData();
    this.sendMessageToAllWindowsIncSelf({
      type: "TableDataChange", index: 0, collection: item.name, ...this.#firstGame(),
    });
    return true;
  }

  // The game a freshly loaded list opens on, named so a window with a different order
  // lands on it and not on whatever its own first row is.
  #firstGame() {
    const id = this.#identityAt(0);
    return id ? { id } : {};
  }

  /** Come back out. Returns false at the root, where there is nothing to pop. */
  popList() {
    if (this.atRoot) return false;
    this._listStack.pop();
    const list = this.activeList();
    this.#announceIndex(list.cursor, { previous: list.cursor, reason: "leave" });
    return true;
  }

  // Move the selection, wrap it, and tell everyone where it went. Whichever list is on
  // top: the wheel, or a picker core has descended into.
  moveBy(delta) {
    const list = this.activeList();
    if (!list.length) return list.cursor;
    const previous = list.cursor;
    return this.moveTo(list.indexAfter(delta),
                       { previous, direction: delta < 0 ? "previous" : "next" });
  }

  /**
   * Move the selection to `index` and announce it. Every index path goes through here,
   * so a theme is told the same things however the cursor moved.
   *
   * `reason` is how far and why: `step`, `page`, `restore`. A page press moves to the
   * next group when group paging is on and a fixed number of rows otherwise, and both
   * say `page` - a theme reads this to choose between sliding and cutting, and both want
   * a cut, so splitting them would be a distinction nothing acts on. `source` is who
   * moved it: `user`, or `attract` once core advances on a timer.
   * Two fields rather than one because they are independent - a random attract advance
   * is a jump *and* not a person, and one enum cannot say both.
   *
   * `group` and `groupKind` say where it landed - "T"/"letter", "1985"/"year". Both are
   * empty when the order has no groups, so a theme can tell "no grouping here" from
   * "the group happens to be empty".
   */
  moveTo(index, { previous = this.activeList().cursor, direction = "",
                  reason = "step", source = "user" } = {}) {
    const list = this.activeList();
    if (!list.length) return list.cursor;
    const at = list.moveTo(index);
    // Only the wheel has a selected game. Moving a picker's cursor must not fetch a
    // rating, re-render window media or tell the backend a game was selected - every one
    // of those follows `_currentTableIndex`, and this is the one place it is assigned.
    // Asked before the assignment: a settle forced by this press reports where the wheel
    // stopped, not where this press is taking it.
    const scrubbing = this.atRoot && this.#scrubbing();
    if (this.atRoot) this._currentTableIndex = at;
    if (this.atRoot && this.scrubAware) {
      this.#preloadAround(at, direction || this._scrub?.direction || "next");
    }
    if (scrubbing) {
      this.#scrubTo(at, { previous, direction, reason, source });
      return at;
    }
    this.#announceIndex(at, { previous, direction, reason, source });
    if (this.atRoot) this.#selectionChanged();
    return at;
  }

  // True from the first repeat after `scrubAfterMs` of holding until the wheel settles.
  // A press that arrives while the settle is still waiting ends the scrub first, so its
  // own step is announced in full.
  #scrubbing() {
    if (!this._scrub) return false;
    if (this._scrub.released) {
      this.#settleScrub();
      return false;
    }
    return true;
  }

  #beginScrub(held) {
    if (!this.enabled("core_scrub") || !this.isController() || !this.atRoot) return;
    clearTimeout(this._mediaTimer);
    this._mediaTimer = null;
    this._scrub = { released: false, timer: null, from: this._currentTableIndex,
                    source: "user", held,
                    direction: held.action.endsWith("previous") ? "previous" : "next" };
  }

  // Every step of a scrub goes to this window alone, and only if the theme draws it:
  // the other windows and everything that follows the selection wait for the settle.
  #scrubTo(at, { previous, direction, reason, source }) {
    this._scrub.source = source;
    if (!this.scrubAware) return;
    this.#announceIndex(at, { previous, direction, reason, source, scrubbing: true });
  }

  #releaseScrub(held) {
    const scrub = this._scrub;
    if (!scrub || scrub.held !== held) return;
    scrub.released = true;
    scrub.timer = setTimeout(() => this.#settleScrub(), this.scrubSettleMs);
  }

  // One announcement to every window, which is what the other windows, the audio, the
  // backend and the preload each take as the selection having changed.
  #settleScrub() {
    const scrub = this._scrub;
    if (!scrub) return;
    clearTimeout(scrub.timer);
    this._scrub = null;
    if (!this.atRoot) return;
    this.#announceIndex(this._currentTableIndex,
      { previous: scrub.from, reason: "settle", source: scrub.source });
  }

  // One shape for every index message, whichever list moved.
  #announceIndex(at, { previous = at, direction = "", reason = "step",
                       source = "user", to = "", others = false, scrubbing = false } = {}) {
    const list = this.activeList();
    const item = this.atRoot ? (this.tableData[at] || {}) : {};
    if (scrubbing) to = this._windowName;
    const send = to ? (message) => this.#sendToWindow(to, message)
                    : others ? (message) => this.sendMessageToAllWindows(message)
                    : (message) => this.sendMessageToAllWindowsIncSelf(message);
    // Which game that is, so a window with a different list does not draw another one.
    // Left off when the row has no identity, and then the index is all there is.
    const id = this.atRoot ? this._reader.identity(item) : "";
    send({
      type: "TableIndexUpdate", index: at, previous, direction, reason, source,
      ...(id ? { id } : {}),
      // Which list this index is in. A theme reads it instead of tracking a mode, so a
      // picker no longer has to guess that an index message means "a game was picked".
      list: list.id, kind: list.kind,
      group: item.group || "", groupKind: this.atRoot ? (this.groupBy || "") : "",
      // True while the wheel is still settling - what a theme needs to decide whether to
      // load full art or wait. It cannot serve as a jump signal: it is time-based, so one
      // distant jump reports false.
      // An answer to one window is not a move, so it neither reads nor restarts the clock.
      // The settle is the end of a move, so it is not one either.
      moving: scrubbing ? true : (to || reason === "settle") ? false : this.#stillMoving(),
      // Present only while the wheel is being scrubbed, so a theme that never looks for
      // it sees the message it always did.
      ...(scrubbing ? { scrubbing: true } : {}),
    });
  }

  #stillMoving() {
    const now = Date.now();
    const moving = (now - (this._lastMoveAt || 0)) < this.movingWindowMs;
    this._lastMoveAt = now;
    return moving;
  }

  // A key typed into a text field is text, never an action. This matters most inside an
  // overlay, where core now listens: b, c, m, q and t are bound by default, so without
  // this the collection menu's save-filter box could not accept half the alphabet, and
  // Enter would fire select instead of reaching the field.
  #isTextEntry(target) {
    if (!target) return false;
    const tag = String(target.tagName || "").toLowerCase();
    if (tag === "textarea" || target.isContentEditable) return true;
    if (tag !== "input") return false;
    const type = String(target.type || "text").toLowerCase();
    return !["button", "checkbox", "radio", "range", "submit", "reset", "file", "color",
             "image"].includes(type);
  }

  // Keyboard input processing to handlers
  async #onKeyDown(e) {
    if (this._captureArmed) return this.#relayKey(e, true);
    if (!this.frontendInputEnabled) return;
    if (!this.isController()) return;
    if (this.#isTextEntry(e.target)) {
      if (e.key === "Escape" && this.inputMode === "text" && !e.repeat) {
        e.preventDefault();
        this.#dispatchAction("back");
      }
      return;
    }

    const token = downToken("key:" + (e.code || e.key || ""));
    if (!e.repeat) this.#noteDown(token);

    const action = this.#actionForKeyboardEvent(e);
    const hold = this.#holdForKeyboardEvent(e);
    if (!action && !hold) { this.#reconsiderChords(); return; }

    // A bound key belongs to us. Without this the arrows also scroll the theme's page
    // and Space activates whatever the browser thinks is focused.
    e.preventDefault();

    // The OS repeat is not ours. It fires at whatever rate the machine is set to, with
    // no ramp, and it is the one producer a flipper and a phone cannot match - so it is
    // ignored and the hold below does the repeating instead.
    if (e.repeat) return;

    // A key with a hold says nothing until it is let go or the hold is reached.
    if (hold) {
      this.#awaitHold(token, action ? [action] : [], hold);
      this.#reconsiderChords();
      return;
    }

    this.#dispatchAction(action);
    // A chord's members must not also walk the wheel for the length of the hold: what
    // the player asked for is the chord's action. Checked after reconsidering, because
    // the chord only completes once this member is down.
    this.#reconsiderChords();
    if (!this.#heldByChord(token)) this.#startHold(action, token);
  }

  // A key coming back up ends its hold and lets any chord it belonged to fall apart.
  #onKeyUp(e) {
    if (this._captureArmed) return this.#relayKey(e, false);
    const token = downToken("key:" + (e.code || e.key || ""));
    this.#endHold(this.#actionForKeyboardEvent(e));
    this.#releaseInput(token);
    this.#inputUp(token);
  }

  // --- Holds --------------------------------------------------------------------
  //
  // An input with a hold has one press and one hold, and cannot say which until it is
  // let go: released early it is the press, kept down for the hold's length it is the
  // hold and only the hold. So it fires nothing on the way down. An input with no hold
  // never comes through here.

  // The key's one hold, whatever its length. The first action declared with one wins,
  // and the rest are never heard.
  #holdForKeyboardEvent(e) {
    const tokens = this.#eventKeyboardTokens(e);
    for (const [action, holds] of Object.entries(this.keyHoldMap || {})) {
      const found = holds.find(one => tokens.has(one.token));
      if (found) return { action, ms: found.ms };
    }
    return null;
  }

  #awaitHold(id, press, hold) {
    this.#cancelHold(id);
    const waiting = { press, hold, fired: false, timer: null };
    waiting.timer = setTimeout(() => {
      waiting.timer = null;
      waiting.fired = true;
      this.#hideHoldRing();
      if (this.frontendInputEnabled) this.#dispatchAction(hold.action);
    }, hold.ms);
    this._pending.set(id, waiting);
    this.#showHoldRing(hold.ms);
  }

  // The input came back up: the press if the hold was not reached, nothing if it was.
  #releaseInput(id) {
    const waiting = this._pending.get(id);
    if (!waiting) return;
    this.#cancelHold(id);
    if (waiting.fired || !this.frontendInputEnabled) return;
    for (const action of waiting.press) this.#dispatchAction(action);
  }

  #cancelHold(id) {
    const waiting = this._pending.get(id);
    if (!waiting) return;
    if (waiting.timer) clearTimeout(waiting.timer);
    this._pending.delete(id);
    this.#hideHoldRing();
  }

  // A window that loses focus never sees the release, so what was waiting on one is
  // dropped without firing: neither the press nor the hold was asked for.
  #loseFocus() {
    this.#dropHeld();
    this.#endHold();
    for (const id of [...this._pending.keys()]) this.#cancelHold(id);
  }

  // The ring that fills while a hold is kept down. Core draws it so every theme has one;
  // `.vpinfe-hold` is what a theme restyles, and `hold_ring.enabled` false is for one
  // that draws its own. Only the element comes and goes: the fill is the stylesheet's.
  #showHoldRing(ms) {
    if (!this.enabled("core_hold_ring")) return;
    this.#hideHoldRing();
    const ring = document.createElement("div");
    ring.className = "vpinfe-hold";
    ring.setAttribute("style", `--vpinfe-hold-ms: ${ms}ms`);
    const svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("viewBox", "0 0 40 40");
    svg.setAttribute("aria-hidden", "true");
    for (const part of ["track", "fill"]) {
      const circle = document.createElementNS(SVG_NS, "circle");
      circle.setAttribute("class", `vpinfe-hold-${part}`);
      for (const [name, value] of [["cx", 20], ["cy", 20], ["r", 16], ["pathLength", 100]]) {
        circle.setAttribute(name, String(value));
      }
      svg.appendChild(circle);
    }
    ring.appendChild(svg);
    document.body.appendChild(ring);
    this._holdRing = ring;
  }

  #hideHoldRing() {
    if (!this._holdRing) return;
    this._holdRing.remove();
    this._holdRing = null;
  }

  // A press acts once and then, if it is still down after the delay, keeps going.
  #startHold(action, token = "") {
    if (!REPEATING_ACTIONS.has(action)) return;
    this.#endHold();
    const held = { action, token, interval: this.repeatStartMs, timer: null, since: Date.now() };
    held.timer = setTimeout(() => this.#onRepeat(held), this.repeatDelayMs);
    this._hold = held;
  }

  // Released - or the window lost focus mid-hold, which is a release nobody sees.
  // Called with no action to stop whatever is running.
  #endHold(action) {
    if (!this._hold) return;
    if (action && this._hold.action !== action) return;
    clearTimeout(this._hold.timer);
    this.#releaseScrub(this._hold);
    this._hold = null;
  }

  async #onRepeat(held) {
    if (this._hold !== held) return;
    // Muted, not cancelled, while a complete chord owns this input: what the player
    // asked for is the chord's action. Letting go of the other member brings the walk
    // back, because still holding this one is still asking for it - and the interval starts
    // again rather than resuming at whatever speed it had reached.
    const muted = held.token && this.#heldByChord(held.token);
    // Wait for the step to be handled before pacing the next one. Without this the ramp
    // outruns the theme's animation, the theme drops what arrives mid-move, and the
    // wheel travels in lurches instead of accelerating.
    const started = Date.now();
    if (!muted && !this._scrub && started - held.since >= this.scrubAfterMs) {
      this.#beginScrub(held);
    }
    if (!muted) await this.#dispatchAction(held.action);
    if (this._hold !== held) return;

    held.interval = PAGING_ACTIONS.has(held.action) ? this.pageRepeatMs
      : muted ? this.repeatStartMs
      : Math.max(this.repeatFloorMs, held.interval * this.repeatAccel);
    const left = Math.max(0, held.interval - (Date.now() - started));
    held.timer = setTimeout(() => this.#onRepeat(held), left);
  }

  // A press that arrived over the bus rather than off a key or a pad. Not a chord member
  // and never will be: a chord is two physical inputs held together, and what arrives
  // here is already an action.
  //
  // Only the press acts. The release is accepted and does nothing yet - it is what the
  // hold engine reads - so until then a held button steps once, which is what a client
  // sending a press and a release straight after already means.
  #applyRemoteInput(message) {
    const action = String(message && message.action || "");
    if (!this.frontendInputEnabled) {
      if (message && message.phase !== "release") this.#inPlay(action);
      return;
    }
    if (!action) return;
    if (message.phase === "release") return this.#endHold(action);
    this.#dispatchAction(action);
    this.#startHold(action);
  }

  // Take Picture and Back while a table runs, from a gamepad or any other producer; the
  // page's own keys are the table's then. What happened decides the tone.
  async #inPlay(action) {
    if (!this._launchInputSuppressedByLifecycle || !PLAY_ACTIONS.has(action)) return;
    let answer = null;
    try {
      answer = await this.callInternal(
        action === "take_picture" ? "take_picture" : "resume_play");
    } catch (err) {
      this.call("console_out", `Take Picture: ${err.message}`);
      return;
    }
    const state = answer && answer.state;
    if (state === "frozen") this.#tone("ready");
    else if (state === "taken") this.#tone("shutter");
  }

  #tone(kind) {
    const Context = window.AudioContext || window.webkitAudioContext;
    if (!Context) return;
    try {
      this._toneContext = this._toneContext || new Context();
      const ctx = this._toneContext;
      if (ctx.state === "suspended" && ctx.resume) ctx.resume();
      const start = ctx.currentTime + 0.01;
      if (kind === "ready") {
        [[880, 0], [1320, 0.11]].forEach(([pitch, at]) => {
          const osc = ctx.createOscillator();
          const gain = ctx.createGain();
          osc.frequency.value = pitch;
          gain.gain.setValueAtTime(0.0001, start + at);
          gain.gain.exponentialRampToValueAtTime(0.3, start + at + 0.01);
          gain.gain.exponentialRampToValueAtTime(0.0001, start + at + 0.09);
          osc.connect(gain).connect(ctx.destination);
          osc.start(start + at);
          osc.stop(start + at + 0.1);
        });
        return;
      }
      const length = Math.floor(ctx.sampleRate * 0.05);
      const noise = ctx.createBuffer(1, length, ctx.sampleRate);
      const samples = noise.getChannelData(0);
      for (let i = 0; i < length; i++) samples[i] = (Math.random() * 2 - 1) * (1 - i / length);
      [0, 0.08].forEach((at) => {
        const burst = ctx.createBufferSource();
        const gain = ctx.createGain();
        burst.buffer = noise;
        gain.gain.value = at ? 0.25 : 0.5;
        burst.connect(gain).connect(ctx.destination);
        burst.start(start + at);
      });
    } catch (err) {
      console.warn("vpinfe: a tone could not be played", err);
    }
  }

  // What an action does, whichever input produced it. One place, so the keyboard and
  // the gamepad cannot drift apart again.
  #dispatchAction(action) {
    // Core's own, and answered only while a table runs; never a theme's.
    if (action === "take_picture") return;
    if (this.isController() && this.#attractActivity()) return;
    if (!this.isController() && action !== "select") return this.#triggerInputAction(action);

    // A field owns its keystrokes; back and select still reach the dialog, so a cabinet
    // button can dismiss it or finish it while the field has focus.
    if (this.inputMode === "text" && action !== "back" && action !== "select") return;
    // A confirm owns every action while it is up. Answering it is the only thing the
    // buttons can do, so an unmapped press cannot dismiss it and cannot act behind it.
    if (this.#pendingConfirm) {
      if (action === "select") this.#pendingConfirm(true);
      else if (action === "back" || action === "exit") this.#pendingConfirm(false);
      return;
    }
    if (this.#preview && this.isController()) return this.#answerPreview(action);
    // A dialog owns the actions while it is up: nothing reaches the menu behind it, and
    // nothing opens an overlay on top of it.
    if (this.inputMode === "modal" && !["select", "back", "previous", "next"].includes(action)) {
      return;
    }
    const overlay = this.overlay;
    // A sign-in request owns every action but exit: the menu keys must not open another
    // overlay over it and strand the request.
    if (overlay === "signIn" && action !== "exit") return this.#triggerInputAction(action);
    if (action === "exit") {
      // Escape and q default to exit, and an overlay's own Escape handler never runs -
      // nothing focuses the iframe - so this used to quit VPinFE from inside a menu.
      // Closing the overlay is what every overlay's own map already meant by it.
      if (overlay === "signIn") this.#dismissSignIn();
      else if (overlay) this.#toggleOverlay(overlay);
      else this.#leave();
    }
    else if (action === "menu") this.#toggleOverlay("menu");
    else if (action === "collection_menu") this.#toggleOverlay("collectionMenu");
    else if (action === "tutorial") this.#toggleOverlay("tutorial");

    else if (this.#shouldHandleCoreNavigation(action)) {
      if (action === "previous" || action === "next") this.moveBy(action === "previous" ? -1 : 1);
      else this.#handleCorePaging(action);
    }
    // Core owns select and back for as long as it is holding a list. It has to: the
    // theme has no way to pop a stack it does not know about, so handing back either
    // action would strand the player inside the picker. Not while an overlay is up: that
    // is what the action is for then, and the overlay's own handler closes it.
    else if (!overlay && !this.atRoot && (action === "select" || action === "back")) {
      if (action === "select") this.selectCurrent();
      else this.popList();
    }
    // Back with nothing to go back from does nothing, and the theme is not handed it:
    // leaving is exit's. Contract 2 only - below it back is the theme's own action - and
    // only while navigating, because a dialog and a text field both mean back for dismiss.
    else if (action === "back" && this.inputMode === "navigation"
             && this.contract >= CURRENT_CONTRACT
             && this.enabled("core_navigation") && !overlay && this.isController()) {
      return;
    }
    else this.#triggerInputAction(action);
  }

  // Quit VPinFE, or while this device is recording a run, ask to stop the run instead:
  // between two recordings the wheel is on screen, and leaving would end the run with it.
  async #leave() {
    if (!this.#recording) return this.requestLifecycle("vpinfe", "stop");
    if (!await this.#ask(this.t("frontend.recording.stop", "Stop recording"))) return false;
    try {
      await this.callInternal("stop_recording");
    } catch (err) {
      this.call("console_out", `Stop recording: ${err.message}`);
    }
    return true;
  }

  // True when core moves the selection itself rather than handing the action to the
  // theme. One guard for all four actions, because at contract 2 they are one capability.
  //
  // Below contract 2 they are not: 2.x core pages for the theme but leaves the cursor to
  // it, so a page falls to core_paging while a step falls to the theme. Preserving that
  // is the whole reason core_paging still exists, and it retires with contract 1.
  #shouldHandleCoreNavigation(action) {
    const paging = action === "page_previous" || action === "page_next";
    if (!paging && action !== "previous" && action !== "next") return false;
    if (!this.isController()) return false;
    if (this.inputMode !== "navigation") return false;
    if (this.overlay) return false;   // an open overlay owns every action
    if (paging && this.contract < CURRENT_CONTRACT) return this.enabled("core_paging");
    return this.enabled("core_navigation");
  }

  async #loadMonitors() {
    this.monitors = await this.call("get_monitors");
  }

  async #loadPlayers() {
    try {
      this.players = await this.call("get_players");
    } catch (_e) {
      /* an older build cannot answer; the empty roster already covers it */
    }
  }

  // One branch, once, for the whole surface. A theme declares what it was written
  // against and gets that and nothing else, which is what makes the legacy layer
  // removable as a unit instead of one alias at a time.
  #applyContract(level) {
    const declared = Number(level) || OLDEST_CONTRACT;
    this.contract = Math.min(Math.max(declared, OLDEST_CONTRACT), CURRENT_CONTRACT);
    this._reader = this.contract > OLDEST_CONTRACT
      ? new ContractTwoReader(this)
      : new ContractOneReader(this);
    if (this.contract > OLDEST_CONTRACT) removeLegacyAliases(this);
    console.info(`vpinfe: serving theme contract ${this.contract}`);
  }

  /**
   * The three layout answers, from the ini and this window's identity.
   *
   * `surface` is the shape you design for, and on the controller it is the *mounting*,
   * not the window: a portrait playfield reads portrait whether the OS turned the screen
   * (window already portrait, no rotation) or VPinFE turns the UI itself (window
   * landscape, rotated a quarter). Collapsing those two cabinet setups into one design
   * target is the point of this object.
   *
   * Other windows have no mounting declared, and nothing rotates them, so they measure.
   */
  #resolveLayout(cabMode) {
    const declared = String(this.playfieldOrientation || "").trim().toLowerCase();
    const orientation = (declared === "portrait" || declared === "landscape")
      ? declared
      : this.#unusable("playfieldorientation", this.playfieldOrientation, "landscape");

    const degrees = Number(this.playfieldRotation);
    const turned = Number.isFinite(degrees) ? ((degrees % 360) + 360) % 360 : NaN;
    // A quarter turn or nothing. Anything else would need a theme to guess which way to
    // round it, and every theme would guess differently.
    const uprightRotation = [0, 90, 180, 270].includes(turned)
      ? turned
      : this.#unusable("playfieldrotation", this.playfieldRotation, 0);

    return {
      cabinet: !!cabMode,
      uprightRotation: this.isController() ? uprightRotation : 0,
      surface: this.isController() ? orientation : this.#measuredSurface(),
    };
  }

  /**
   * Publish the layout to CSS, for themes that asked core to handle it.
   *
   * Attributes and custom properties rather than inline styles: the rules live in
   * vpinfe-style.css, which every theme already links, so a theme opts in with a class
   * and writes no JavaScript.
   */
  #publishLayout() {
    const root = document.documentElement;
    if (!root || !this.enabled("core_layout")) return;

    root.dataset.vpinfeSurface = this.layout.surface;
    root.dataset.vpinfeUpright = String(this.layout.uprightRotation);
    if (this.layout.cabinet) root.dataset.vpinfeCabinet = "true";
    root.style.setProperty("--vpinfe-upright-rotation", `${this.layout.uprightRotation}deg`);
  }

  /**
   * Turn one playfield media element to fit the surface, once it knows its own size.
   *
   * The art is measured rather than assumed. There is no reliable authoring convention -
   * a library may be landscape desktop captures, portrait FSS renders, or a mix - and the
   * image itself carries the answer, so nothing has to be declared. `playfieldmediarotation`
   * overrides it for what measurement cannot see, like art that is upside down.
   */
  applyPlayfieldMediaRotation(element) {
    if (!element || !this.enabled("core_layout")) return;

    const measure = () => {
      const turn = this.#playfieldTurn(element);
      element.dataset.vpinfeTurned = String(turn === 90 || turn === 270);
      element.style.setProperty("--vpinfe-playfield-media-rotation", `${turn}deg`);
    };

    if (element.naturalWidth || element.videoWidth) measure();
    else element.addEventListener("load", measure, { once: true });
    element.addEventListener("loadedmetadata", measure, { once: true });
  }

  // Degrees to turn a loaded playfield picture or video so it fills the surface.
  #playfieldTurn(element) {
    const wide = (element.naturalWidth || element.videoWidth || 0)
               > (element.naturalHeight || element.videoHeight || 0);
    const fits = wide === (this.layout.surface === "landscape");
    // The setting arrives as a string, so it is coerced before it is compared.
    const stated = String(this.playfieldMediaRotation ?? "auto").trim().toLowerCase();
    return stated === "auto" ? (fits ? 0 : 90) : Number(stated) || 0;
  }

  /** The window's own shape. Read before any transform, so it is the untouched box. */
  #measuredSurface() {
    return window.innerHeight > window.innerWidth ? "portrait" : "landscape";
  }

  #unusable(key, value, fallback) {
    console.warn(`vpinfe: [Displays] ${key} is ${JSON.stringify(value)}, `
      + `which is not a value this build understands - using ${JSON.stringify(fallback)}.`);
    return fallback;
  }

  /**
   * The media kind this window shows, or null when it is not a display we hold art for.
   *
   * A media kind is named after the display it captures, so a window that is a display
   * finds its artwork under its own name. The mapping lives here rather than in every
   * theme: a contract 1 window is translated the same way its media names are, and a
   * window a theme invented resolves to null instead of a lookup that finds nothing.
   */
  get windowMediaKind() {
    const kind = this.#canonicalKind(this._windowName);
    return MEDIA_KINDS.includes(kind) ? kind : null;
  }

  /**
   * This window's name - which page it loaded.
   *
   * Known before the socket opens. Every published theme asks the backend for it instead,
   * because until now there was nothing else to ask.
   */
  get windowName() {
    return this._windowName;
  }

  /**
   * Whether this window is the one that owns input, audio and the selection. It is the
   * first window the theme declared - `table` for a theme that declares nothing, and
   * `playfield` at contract 2 - so nothing here has to know a name.
   */
  isController() {
    return this._windowName === (this.windows[0] || "table");
  }

  #servesLegacyNames() {
    return this.contract <= OLDEST_CONTRACT;
  }

  #canonicalKind(kind) {
    const value = String(kind || "").trim().toLowerCase();
    if (!this.#servesLegacyNames()) return value;
    return MEDIA_KIND_ALIASES[value] || value;
  }

  #normalizeMediaPriorities(priorities) {
    const normalized = Object.assign({}, DEFAULT_MEDIA_PRIORITIES);
    if (!priorities || typeof priorities !== "object") return normalized;

    for (const key of ["playfield", "backglass", "scoreview", "bg", "dmd"]) {
      const value = String(priorities[key] || "").trim().toLowerCase();
      if (value === "image" || value === "video") normalized[key] = value;
    }

    const realDmd = String(priorities.real_dmd || "").trim().toLowerCase();
    if (["standard", "color"].includes(realDmd)) normalized.real_dmd = realDmd;
    return normalized;
  }

  #resolveImageVideoMedia(item, kind) {
    const priority = this.mediaPriorities[kind] || DEFAULT_MEDIA_PRIORITIES[kind] || "video";
    return this._reader.imageVideo(item, kind, priority);
  }

  #resolveRealDmdMedia(item) {
    return this._reader.realDmd(item, this.mediaPriorities.real_dmd
                                     || DEFAULT_MEDIA_PRIORITIES.real_dmd);
  }

  // Gamepad handling
 async #initKeyboardMapping() {
  // One call, one shape: every action and what is bound to it. The keyboard map and the
  // gamepad map used to arrive separately, with a table translating key* to joy* - which
  // existed only because a stored value could not name its own input.
  const {keys, buttons, chords, members, keyHolds, standard, buttonHolds} =
    buildBindingMaps(await this.call("get_bindings") || {});
  if (Object.keys(keys).length) {
    this.keyActionMap = keys;
    this.keyHoldMap = keyHolds;
  }
  this.joyButtonMap = buttons;
  this.standardButtonMap = standard;
  this.joyHoldMap = buttonHolds;
  this.chordBindings = chords;
  // Which single inputs belong to some chord. Held so a key press does not walk every
  // chord to find out it belongs to none.
  this.chordMembers = members;
}



 async #initGamepadMapping() {
  // Nothing to fetch: gamepad buttons arrive in the same binding list the keyboard
  // does, so joyButtonMap is already built by the time this runs.
}

  // What a button does as a plain press, on a pad that is or is not in the standard layout.
  // A numbered binding fires for any pad; a standard one only for a pad that says it is.
  #padPresses(buttonIndex, standard) {
    const key = buttonIndex.toString();
    const actions = [...(this.joyButtonMap[key] || []),
                     ...(standard ? this.standardButtonMap[key] || [] : [])];
    return [...new Set(actions)];
  }

  // The button's one hold, in the order the bindings were declared.
  #padHold(buttonIndex, standard) {
    return (this.joyHoldMap[buttonIndex.toString()] || [])
      .find(one => standard || !one.standard) || null;
  }

  async #onButtonPressed(buttonIndex, gamepadIndex, standard = false) {
    const presses = this.#padPresses(buttonIndex, standard);
    if (!this.frontendInputEnabled) {
      for (const action of presses) this.#inPlay(action);
      return;
    }

    // A button with a hold waits to be let go, as a key does.
    const hold = this.#padHold(buttonIndex, standard);
    if (hold) {
      this.#awaitHold(`pad:${gamepadIndex}:${buttonIndex}`, presses, hold);
      this.#reconsiderChords();
      return;
    }

    // Every action bound to this button. The branching is #dispatchAction's, shared with
    // the keyboard so the two cannot drift apart again.
    for (const action of presses) {
      this.#dispatchAction(action);
    }
    this.#reconsiderChords();
    const token = "pad:" + buttonIndex;
    if (this.#heldByChord(token)) return;
    for (const action of presses) {
      this.#startHold(action, token);
    }
  }

  // While a Manager page asks for them, the controller's buttons and the keys go to it and
  // do nothing here, and a line on the controller window says where they went. Armed for
  // `ttlMs` and re-armed by each message; buttons already down when it starts are ignored
  // until let go, as the Manager's own listener does. A table playing keeps its buttons, so
  // an ask that arrives then is not taken, and nor is one that follows a take-back within a
  // moment, which is the ask already on its way when the person took them.
  static CAPTURE_TAKE_MS = 5000;
  static CAPTURE_SHOW_MS = 500;
  static CAPTURE_SETTLE_MS = 1500;

  #armCapture(armed, ttlMs, who) {
    clearTimeout(this._captureTimer);
    if (armed && (this._launchInputSuppressedByLifecycle || this.remoteLaunchActive
        || Date.now() < (this._captureSettleUntil || 0))) {
      armed = false;
    }
    if (!armed) {
      this._captureArmed = false;
      this.#dropHeld();
      this.#capture = null;
      this.#drawCapture();
      return;
    }
    if (!this._captureArmed) {
      // What was held or waiting never sees its release now, so it is dropped here.
      this.#loseFocus();
      this._inputsDown?.clear();
      this._captureIgnored = new Set();
      [...navigator.getGamepads()].filter(Boolean).forEach((gp) => gp.buttons.forEach((b, i) => {
        if (b.pressed) this._captureIgnored.add(gp.index + ":" + i);
      }));
    }
    this._captureArmed = true;
    this.#capture = { browser: String(who?.browser || ""), device: String(who?.device || "") };
    this._captureTimer = setTimeout(() => this.#armCapture(false, 0), ttlMs || 6000);
    this.#drawCapture();
  }

  // A key does nothing here while captured. Only the controller window reports it, and a
  // repeat is the operating system's, not another press. A text field keeps its keys: what
  // is typed in one is neither a binding nor something to send to another device.
  #relayKey(e, isDown) {
    if (!this.isController() || this.#isTextEntry(e.target)) return;
    e.preventDefault();
    if (e.repeat || !/^[A-Za-z0-9]{1,32}$/.test(e.code || "")) return;
    this.#heldForCapture("key:" + e.code, isDown);
    this.callInternal("input_capture", "key:" + e.code, isDown ? "down" : "up").catch(() => {});
  }

  #relayCapture(pad, index, isPressed, wasPressed, standard) {
    if (isPressed === wasPressed) return;
    const seat = pad + ":" + index;
    if (isPressed ? this._captureIgnored.has(seat) : this._captureIgnored.delete(seat)) return;
    const token = standard ? "pad:standard/button:" + index : "pad:" + pad + "/button:" + index;
    this.#heldForCapture(seat, isPressed);
    this.callInternal("input_capture", token, isPressed ? "down" : "up").catch(() => {});
  }

  // Any button or key kept down for five seconds takes them back, from every page.
  _captureHeld = new Map();

  #heldForCapture(id, isDown) {
    if (!isDown) {
      const held = this._captureHeld.get(id);
      if (held) {
        clearTimeout(held.timer);
        clearTimeout(held.show);
        this._captureHeld.delete(id);
      }
      this.#drawCapture();
      return;
    }
    if (this._captureHeld.has(id)) return;
    this._captureHeld.set(id, {
      at: Date.now(),
      timer: setTimeout(() => this.#takeBack(), VPinFECore.CAPTURE_TAKE_MS),
      show: setTimeout(() => this.#drawCapture(), VPinFECore.CAPTURE_SHOW_MS),
    });
    this.#flashCapture();
    this.#drawCapture();
  }

  #dropHeld() {
    this._captureHeld.forEach((held) => {
      clearTimeout(held.timer);
      clearTimeout(held.show);
    });
    this._captureHeld.clear();
    this.#drawCapture();
  }

  #takeBack() {
    this._captureSettleUntil = Date.now() + VPinFECore.CAPTURE_SETTLE_MS;
    this.#armCapture(false, 0);
    // The pages are only told once the call lands, so a failed one is tried again.
    this.callInternal("input_capture_release").catch(() =>
      this.callInternal("input_capture_release").catch(() => {}));
  }

  #updateGamepads() {
    const gamepads = navigator.getGamepads();
    for (let i = 0; i < gamepads.length; i++) {
      const gp = gamepads[i];
      if (!gp) continue;
      const standard = gp.mapping === "standard";

      if (!this.previousButtonStates[i]) {
        this.previousButtonStates[i] = new Array(gp.buttons.length).fill(false);
      }

      gp.buttons.forEach((button, index) => {
        const wasPressed = this.previousButtonStates[i][index];
        const isPressed = button.pressed;

        if (this._captureArmed) {
          this.#relayCapture(i, index, isPressed, wasPressed, standard);
          this.previousButtonStates[i][index] = isPressed;
          return;
        }
        if (this.frontendInputEnabled && isPressed !== wasPressed) {
          // Both edges: a chord is about what is held, so it has to hear the release -
          // and so does the hold, which is why a held flipper can scroll the wheel at
          // all. Only the press edge counted before, so holding one did nothing.
          if (isPressed) {
            this.#noteDown("pad:" + index);
          } else {
            this.#releaseInput(`pad:${i}:${index}`);
            this.#inputUp("pad:" + index);
            for (const action of this.#padPresses(index, standard)) {
              this.#endHold(action);
            }
          }
        }
        if (isPressed && !wasPressed
            && (this.frontendInputEnabled || this._launchInputSuppressedByLifecycle)) {
          this.#onButtonPressed(index, i, standard); // new press
        }
        this.previousButtonStates[i][index] = isPressed;
      });
    }
    requestAnimationFrame(() => this.#updateGamepads());
  }

  // convert the hard full local path to the web servers url map
  getCurrentTutorialUrl() {
    const index = Math.floor(this._currentTableIndex);
    if (!Array.isArray(this.tableData) || index < 0 || index >= this.tableData.length) {
      return "";
    }

    const item = this.tableData[index];
    const meta = (item && typeof item === "object") ? item.meta : null;
    const info = (meta && typeof meta === "object" && meta.Info && typeof meta.Info === "object")
      ? meta.Info
      : null;
    const tutorialUrl = info ? info.PinballPrimerTut : "";
    return (typeof tutorialUrl === "string") ? tutorialUrl.trim() : "";
  }

  buildTutorialProxyUrl(tutorialUrl) {
    if (!tutorialUrl) return "";
    return `/proxy/pinballprimer?url=${encodeURIComponent(tutorialUrl)}`;
  }


  // Subscribe to play state on the Manager's event stream
  #watchPlayState() {
    // Don't subscribe from a file:// origin - CORS blocks it on Chromium/QWebEngine.
    // The theme page loads over http://, where this works.
    if (window.location.protocol === 'file:') return;

    const streamUrl = `${this.endpoints.device}/api/v1/events`
      + "?events=play.state_changed,capture.run_changed";
    console.log("[RemoteLaunch] Subscribing to:", streamUrl);

    const source = new EventSource(streamUrl);
    let reportedOffline = false;

    source.addEventListener("play.state_changed", (message) => {
      reportedOffline = false;
      const state = JSON.parse(message.data).state || {};

      // Our own launches arrive as TableLaunching over the bridge. Acting on them
      // here as well would raise the remote overlay on a launch from the wheel.
      if (state.source === "frontend" || state.source === "capture") return;

      if (state.launching && !this.remoteLaunchActive) {
        this.remoteLaunchActive = true;
        // `game_name` is what the state carries. This read `table_name` and had done
        // since the API started talking about games, so every theme has been shown
        // "undefined" on a remote launch - the field is still sent under both names
        // because the 2.x one works and twelve themes read it.
        console.log("[RemoteLaunch] Launch detected:", state.game_name);
        this.call("console_out", `Remote launching: ${state.game_name}`);
        this.sendMessageToAllWindowsIncSelf({
          type: "RemoteLaunching",
          game_name: state.game_name,
          table_name: state.game_name
        });
      } else if (!state.launching && this.remoteLaunchActive) {
        this.remoteLaunchActive = false;
        console.log("[RemoteLaunch] Launch completed");
        this.call("console_out", "Remote launch completed");
        this.sendMessageToAllWindowsIncSelf({
          type: "RemoteLaunchComplete"
        });
      }
    });

    source.addEventListener("capture.run_changed", (message) => {
      reportedOffline = false;
      const run = JSON.parse(message.data).run || null;
      this.#recording = run && run.state === "running" ? run : null;
      this.#drawRecording();
    });

    // EventSource reconnects on its own, and the stream sends the current state on
    // connect, so the server being down or restarting needs nothing here beyond
    // not filling the log with it.
    source.onerror = () => {
      if (reportedOffline) return;
      reportedOffline = true;
      console.log("[RemoteLaunch] Event stream unavailable (the server may not be running)");
    };
  }

  // One line at the top of the controller window while this device records a run.
  // Hidden, not removed, while a table is up.
  #drawRecording() {
    const run = this.#recording;
    if (!run || !this.isController()) {
      this.#recordingLine?.remove();
      this.#recordingLine = this.#recordingText = null;
      return;
    }
    if (!this.#recordingLine) {
      const line = document.createElement("div");
      line.className = "vpinfe-recording";
      const text = document.createElement("p");
      text.className = "vpinfe-recording-text";
      line.appendChild(text);
      document.body.appendChild(line);
      this.#recordingLine = line;
      this.#recordingText = text;
    }
    const line = this.#recordingLine;
    const turn = this.layout.uprightRotation || 0;
    line.setAttribute("data-upright", String(turn));
    line.style.transform = `translate(-50%, -50%) rotate(${turn}deg)`;
    line.hidden = !!this._launchInputSuppressedByLifecycle;
    this.#recordingText.textContent = Number(run.of) > 1
      ? this.t("frontend.recording.line_of", "Recording media - {at} of {of}",
               { at: Number(run.done) + 1, of: run.of })
      : this.t("frontend.recording.line", "Recording “{game}”",
               { game: run.game?.name || "" });
  }

  // One line at the bottom of the controller window while input events are going to a Manager,
  // with what to do to take them back. Hidden, not removed, while a table is up.
  #drawCapture() {
    const who = this.#capture;
    if (!who || !this.isController()) {
      this.#captureLine?.remove();
      this.#captureLine = this.#captureText = this.#captureHint = null;
      this.#captureBar = this.#captureFill = null;
      this.#captureBarId = "";
      return;
    }
    if (!this.#captureLine) {
      const line = document.createElement("div");
      line.className = "vpinfe-capture";
      const card = document.createElement("div");
      card.className = "vpinfe-capture-card";
      const text = document.createElement("p");
      text.className = "vpinfe-capture-text";
      const hint = document.createElement("p");
      hint.className = "vpinfe-capture-hint";
      const bar = document.createElement("div");
      bar.className = "vpinfe-capture-bar";
      const fill = document.createElement("span");
      bar.appendChild(fill);
      card.appendChild(text);
      card.appendChild(hint);
      card.appendChild(bar);
      line.appendChild(card);
      document.body.appendChild(line);
      this.#captureLine = line;
      this.#captureText = text;
      this.#captureHint = hint;
      this.#captureBar = bar;
      this.#captureFill = fill;
    }
    const line = this.#captureLine;
    const turn = this.layout.uprightRotation || 0;
    line.setAttribute("data-upright", String(turn));
    line.style.transform = `translate(-50%, -50%) rotate(${turn}deg)`;
    line.hidden = !!this._launchInputSuppressedByLifecycle;
    const browser = who.browser || this.t("frontend.capture.browser", "a browser");
    this.#captureText.textContent = who.device
      ? this.t("frontend.capture.line", "Input events are going to the Manager in {browser} at {device}",
               { browser, device: who.device })
      : this.t("frontend.capture.line_here",
               "Input events are going to the Manager in {browser} on this device", { browser });
    let longest = null;
    this._captureHeld.forEach((held, id) => {
      if (!longest || held.at < longest.held.at) longest = { id, held };
    });
    const elapsed = longest ? Date.now() - longest.held.at : 0;
    const holding = !!longest && elapsed >= VPinFECore.CAPTURE_SHOW_MS;
    this.#captureHint.textContent = holding
      ? this.t("frontend.capture.holding", "Keep holding to take them back")
      : this.t("frontend.capture.hint", "Hold any button 5 seconds to take them back");
    this.#captureBar.hidden = !holding;
    if (!holding) {
      this.#captureBarId = "";
    } else if (this.#captureBarId !== longest.id) {
      this.#captureBarId = longest.id;
      const fill = this.#captureFill;
      fill.style.animation = "none";
      void fill.offsetWidth;
      fill.style.animation = `vpinfe-capture-fill ${VPinFECore.CAPTURE_TAKE_MS}ms linear `
        + `${-elapsed}ms forwards`;
    }
  }

  // The dot beside the line flashes on each press that is sent.
  #flashCapture() {
    const text = this.#captureText;
    if (!text) return;
    text.classList.remove("vpinfe-capture-pressed");
    void text.offsetWidth;
    text.classList.add("vpinfe-capture-pressed");
  }

  // What each button does while a recording is shown for a decision. Core decides; the
  // page only asks.
  static PREVIEW_ANSWERS = {
    previous: ["switch_preview", "before"],
    next: ["switch_preview", "after"],
    select: ["decide_preview", true],
    back: ["decide_preview", false],
    exit: ["end_preview"],
  };

  async #answerPreview(action) {
    const answer = VPinFECore.PREVIEW_ANSWERS[action];
    if (!answer) return;
    try {
      await this.callInternal(...answer);
    } catch (err) {
      this.call("console_out", `Preview: ${err.message}`);
    }
  }

  // Drawn by the window whose screen the recording's kind belongs on, or the controller
  // where the theme has none; the controller says which it is and what the buttons do.
  #showPreview(preview) {
    this.#previewLayer?.remove();
    this.#previewLayer = null;
    this.#preview = preview && preview.proposal ? preview : null;
    if (!this.#preview) {
      this.#syncSignIn();
      return;
    }
    if (this.isController() && this.overlay) this.#toggleOverlay(this.overlay);

    const kind = String(preview.kind || "");
    const screen = kind === "audio" ? "" : kind.replace(/_video$/, "");
    const held = !!screen && this.windows.some((name) => this.#canonicalKind(name) === screen);
    const mine = held ? this.windowMediaKind === screen : this.isController();
    if (!mine && !this.isController()) return;

    const showing = preview.showing === "before" ? "before" : "after";
    const side = showing === "before" ? this.t("frontend.preview.before", "Before")
      : this.t("frontend.preview.after", "After");
    const layer = document.createElement("div");
    layer.className = "vpinfe-preview";
    layer.setAttribute("data-shows", mine && kind !== "audio" ? "media" : "words");
    const surface = document.createElement("div");
    surface.className = "vpinfe-preview-surface";
    const turn = this.layout.uprightRotation || 0;
    surface.setAttribute("data-upright", String(turn));
    surface.style.transform = `translate(-50%, -50%) rotate(${turn}deg)`;
    layer.appendChild(surface);
    if (mine) surface.appendChild(this.#previewMedia(preview, showing, screen));

    const words = document.createElement("div");
    words.className = this.isController() ? "vpinfe-preview-bar" : "vpinfe-preview-tag";
    const title = document.createElement("p");
    title.className = "vpinfe-preview-title";
    title.textContent = this.isController()
      ? this.t("frontend.preview.title", "{kind} - {showing}",
               { kind: preview.label || kind, showing: side })
      : side;
    words.appendChild(title);
    if (this.isController()) {
      const hint = document.createElement("p");
      hint.className = "vpinfe-preview-hint";
      hint.textContent = preview.refused || this.t(
        "frontend.preview.hint", "Left and Right to compare, Select to keep, Back to discard");
      words.appendChild(hint);
    }
    surface.appendChild(words);
    document.body.appendChild(layer);
    this.#previewLayer = layer;
  }

  // Waiting on a table, a recording shown for a decision or a question to answer.
  #signInHeld() {
    return this._launchInputSuppressedByLifecycle || this.remoteLaunchActive
      || !!this.#preview || !!this.#pendingConfirm;
  }

  #signInToDraw() {
    return this.#signIn && !this.#signInHeld() ? this.#signIn : null;
  }

  #showSignIn(request) {
    clearTimeout(this.#signInExpiry);
    this.#signIn = request && request.id ? request : null;
    if (this.#signIn) {
      // The machine sends nothing when a request runs out, so this is what closes it - and
      // asks what is waiting behind it, which the machine has not pushed either.
      const left = Math.max(0, Number(request.expires_in) || 0) * 1000;
      this.#signInExpiry = setTimeout(() => {
        this.#showSignIn(null);
        setTimeout(() => this.#pullSignIn(), 500);
      }, left);
    }
    this.#syncSignIn();
  }

  // Brings the screen into line with the request: opens it when it can be drawn, hands
  // it the new state when it is up, and closes it when it has gone.
  #syncSignIn() {
    if (!this.isController()) return;
    if (!this.#signIn) {
      if (this.overlay === "signIn") this.#toggleOverlay("signIn");
      return;
    }
    if (this.#signInHeld()) return;
    if (this.overlay === "signIn") {
      const frame = document.getElementById(VPinFECore.OVERLAYS.signIn.frameId);
      frame?.contentWindow?.postMessage({ event: "sign_in_state", request: this.#signIn }, "*");
      return;
    }
    if (this.#signInOpening) return;
    this.#signInOpening = true;
    this.#toggleOverlay("signIn").finally(() => {
      this.#signInOpening = false;
      this.#syncSignIn();
    });
  }

  async #pullSignIn() {
    try {
      this.#showSignIn(await this.callInternal("signin_pending"));
    } catch (_e) {
      /* an older build has no sign-in to ask about */
    }
  }

  // Close ends the request on the machine, and the machine's answer closes the screen.
  async #dismissSignIn() {
    if (!this.#signIn) return;
    try {
      await this.callInternal("signin_dismiss", this.#signIn.id);
    } catch (err) {
      this.call("console_out", `Sign-in: ${err.message}`);
      this.#showSignIn(null);
    }
  }

  #previewMedia(preview, showing, screen) {
    const kind = String(preview.kind);
    const game = `${this.endpoints.files}/api/v1/games/${encodeURIComponent(preview.game_id)}`;
    const table = preview.table_id ? `/tables/${encodeURIComponent(preview.table_id)}` : "";
    const url = showing === "after"
      ? `${this.endpoints.device}/api/v1/capture/proposals/`
        + `${encodeURIComponent(preview.proposal)}/file`
      : (preview.before ? `${game}${table}/media/${encodeURIComponent(kind)}` : null);
    if (!url) {
      const missing = document.createElement("p");
      missing.className = "vpinfe-preview-missing";
      missing.textContent = this.t("frontend.preview.missing", "Missing");
      return missing;
    }
    const tag = kind === "audio" ? "audio" : kind.endsWith("_video") ? "video" : "img";
    const media = document.createElement(tag);
    media.className = "vpinfe-preview-media";
    if (tag !== "img") {
      Object.assign(media, { autoplay: true, loop: true, playsInline: true,
                             muted: tag === "video" });
    }
    if (screen === "playfield" && tag !== "audio") {
      const measure = () => {
        const turn = this.#playfieldTurn(media);
        media.setAttribute("data-turned", String(turn === 90 || turn === 270));
        media.style.transform = `translate(-50%, -50%) rotate(${turn}deg)`;
      };
      media.addEventListener("load", measure, { once: true });
      media.addEventListener("loadedmetadata", measure, { once: true });
    }
    media.src = url;
    return media;
  }

  // override the page's logging and send it to the Python terminal instead
  #overrideConsole() {
    Object.keys(originalConsole).forEach(method => {
      console[method] = function (...args) {
        // Call original method
        originalConsole[method].apply(console, args);
        // Append to page
        vpin.call("console_out", method + ':' + args);
      };
    });
  }

}


// A window core draws needs no theme script, and this file only defines the class - a
// theme is what constructs core and opens its socket. So a page with no script would sit
// black forever waiting for someone to start it.
//
// Scoped to pages that asked: no `[data-vpin-media]`, no bootstrap. A theme builds its
// own `vpin` while its script runs, which is before this fires, so a page that has one is
// left alone and can never end up with two.
if (typeof document !== "undefined" && typeof window !== "undefined") {
  const bootstrapCoreOnlyWindow = () => {
    if (window.vpin || !document.querySelector("[data-vpin-media]")) return;
    window.vpin = new VPinFECore();
    window.vpin.init();
  };
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bootstrapCoreOnlyWindow);
  } else {
    bootstrapCoreOnlyWindow();
  }
}
