// The overlay has no core of its own. The parent hands it `__vpinWords` when the frame
// is built; until then - and on any install with no catalog - the English passed here
// is what shows.
function t(key, english, params) {
  const words = window.__vpinWords;
  const said = (words && words[key]) || english;
  return params ? said.replace(/\{(\w+)(:n)?\}/g, (whole, name, spec) => {
    if (params[name] === undefined) return whole;
    return spec && window.__vpinNumber ? window.__vpinNumber(params[name]) : params[name];
  }) : said;
}
let rotationAngle = 0;
// The menu and its dialog are ordered lists with a cursor, so they are core's list
// rather than two more copies of `(i +- 1 + n) % n`. Core owns the arithmetic; the
// wrap, the clamp and the empty case are its problem, not this page's.
let menu = null;
let dialog = null;
let dialogState = null; // 'options' | 'progress' | 'rating' | null

// Built lazily: core creates this iframe, so `vpin` is there by the time anything is
// pressed, but not necessarily while this file is still evaluating.
function navigable(items = [], cursor = 0) {
  const list = window.parent.vpin.createList(items, { cursor: 0 });
  list.moveTo(cursor);          // clamps, which is what the old rebuild did by hand
  return list;
}
let ratingDraft = 0;
let ratingGameIndex = 0;
// Who the open rating dialog is for - null means the owner, read through
// get_game_rating/set_game_rating as it always has been.
let ratingForPlayer = null;
let currentGameIndex = 0;
let ratingLabelRequestSeq = 0;
let audioMuted = false;
let menuConfigLoaded = false;
let relayoutTimer = null;
let remoteQrLoaded = false;
let joinQrLoaded = false;
let managerQrLoaded = false;
// recording_offer's last answer, or null; whether its choices are the list on screen;
// and a count so a late answer for another game is dropped.
let recordOffer = null;
let choosing = false;
// Which choice list is open: 'record' (Record Media's), 'player_up' (Player - Start
// toggles who is up), or 'player_pick' (Rating, asking whose with several up).
let choosingKind = null;
let recordStarting = false;
let offerSeq = 0;

window.parent.vpin.registerOverlayHandler("menu", handleInput);

// Keyboard input is core's, on this window as well as the parent's - see
// #listenForKeysIn in vpinfe-core.js. This file used to carry its own hardcoded
// map, which only ran when focus happened to be in here and could not be configured.

window.addEventListener('message', async (event) => {
  const message = event.data;
  if (!message) return;

  if (message.event === 'menu_open') {
    if (typeof message.table_index === 'number' && Number.isFinite(message.table_index) && message.table_index >= 0) {
      currentGameIndex = Math.floor(message.table_index);
    } else {
      currentGameIndex = resolveCurrentGameIndex();
    }
    menu = navigable();
    await applyMainMenuConfig();
    updateMenu();
    refreshRatingMenuLabel(currentGameIndex);
    refreshAudioMenuLabel();
    loadRecordOffer();
    scheduleMenuRelayout();
    return;
  }

  if (message.event === 'reset state') {
    leaveChoices();
    menu = navigable();
    await applyMainMenuConfig();
    updateMenu();
    refreshRatingMenuLabel(resolveCurrentGameIndex());
    refreshAudioMenuLabel();
    scheduleMenuRelayout();
    return;
  }

  if (message.vpinfeEvent) {
    const ev = message.vpinfeEvent;
    if (
      (ev.type === 'TableIndexUpdate' || ev.type === 'TableDataChange') &&
      typeof ev.index === 'number' &&
      Number.isFinite(ev.index) &&
      ev.index >= 0
    ) {
      currentGameIndex = Math.floor(ev.index);
      refreshRatingMenuLabel(currentGameIndex);
      loadRecordOffer();
    }
    if (ev.type === 'PlayersChanged') {
      refreshPlayerMenuLabel();
      refreshRatingMenuLabel(currentGameIndex);
    }
  }
});

window.addEventListener('DOMContentLoaded', () => {
  rotateMenu(rotationAngle);
});

async function loadRemoteQrPanel() {
  if (remoteQrLoaded) return;

  const panel = document.getElementById('menu-qr-panels');
  const code = document.getElementById('remote-qr-code');
  if (!panel || !code) return;

  try {
    const remoteLink = await window.parent.vpin.call('get_remote_link');
    const rawUrl = remoteLink && typeof remoteLink.url === 'string' ? remoteLink.url.trim() : '';
    const qrSvg = remoteLink && typeof remoteLink.qr_svg === 'string' ? remoteLink.qr_svg.trim() : '';
    if (!rawUrl || !qrSvg) return;

    code.innerHTML = qrSvg;
    panel.hidden = false;
    remoteQrLoaded = true;
  } catch (_e) {
    code.innerHTML = '';
  }
}

const MANAGER_NAME_POLL_MS = 2000;
let managerNamePolls = 0;

function managerAddressLines(link) {
  const urls = link && Array.isArray(link.urls) ? link.urls : [];
  return urls
    .filter((url) => typeof url === 'string' && !url.toLowerCase().includes('://localhost:'))
    .slice(0, 2);
}

// The device's name can take longer than a page to answer, so the panel opens on the IP
// address and swaps the text when the name arrives. The QR code carries the IP address
// either way, so only the text is redrawn.
function watchManagerName(link) {
  if (!link || !link.resolving || managerNamePolls >= 30) return;
  managerNamePolls += 1;
  setTimeout(async () => {
    try {
      const next = await window.parent.vpin.call('get_manager_link');
      const shown = managerAddressLines(next);
      const address = document.getElementById('manager-address');
      if (address && shown.length) {
        address.replaceChildren(...shown.map((text) => {
          const line = document.createElement('div');
          line.textContent = text;
          return line;
        }));
      }
      watchManagerName(next);
    } catch (_e) {
      // The next page open asks again.
    }
  }, MANAGER_NAME_POLL_MS);
}

async function loadManagerQrPanel() {
  if (managerQrLoaded) return;

  const panels = document.getElementById('menu-qr-panels');
  const panel = document.getElementById('manager-qr-panel');
  const code = document.getElementById('manager-qr-code');
  const address = document.getElementById('manager-address');
  if (!panels || !panel || !code || !address) return;

  try {
    const link = await window.parent.vpin.call('get_manager_link');
    const qrSvg = link && typeof link.qr_svg === 'string' ? link.qr_svg.trim() : '';
    const shown = managerAddressLines(link);
    if (!qrSvg || !shown.length) return;

    code.innerHTML = qrSvg;
    address.replaceChildren(...shown.map((text) => {
      const line = document.createElement('div');
      line.textContent = text;
      return line;
    }));
    panel.hidden = false;
    panels.hidden = false;
    managerQrLoaded = true;
    watchManagerName(link);
  } catch (_e) {
    code.innerHTML = '';
  }
}

async function loadJoinQrPanel() {
  if (joinQrLoaded) return;

  const panel = document.getElementById('menu-qr-panels');
  const code = document.getElementById('join-qr-code');
  if (!panel || !code) return;

  try {
    const joinLink = await window.parent.vpin.call('get_vpinplay_multi_link');
    const rawUrl = joinLink && typeof joinLink.url === 'string' ? joinLink.url.trim() : '';
    const qrSvg = joinLink && typeof joinLink.qr_svg === 'string' ? joinLink.qr_svg.trim() : '';
    if (!rawUrl || !qrSvg) return;

    code.innerHTML = qrSvg;
    panel.hidden = false;
    joinQrLoaded = true;
  } catch (_e) {
    code.innerHTML = '';
  }
}

async function applyMainMenuConfig() {
  const quitItem = document.getElementById('quit-item');
  if (!quitItem) {
    menuConfigLoaded = true;
    return;
  }

  try {
    const config = await window.parent.vpin.call('get_mainmenu_config');
    quitItem.style.display = config && config.hideQuitButton ? 'none' : '';
  } catch (_e) {
    quitItem.style.display = '';
  }

  menuConfigLoaded = true;
  refreshPlayerMenuLabel();
  await Promise.all([loadManagerQrPanel(), loadRemoteQrPanel(), loadJoinQrPanel()]);
}

// -- players: the roster read from the theme window's own copy, kept live by
// PlayersChanged - see vpinfe-core.js. Nothing here calls get_players itself. --------

function playersRoster() {
  try {
    const held = window.parent.vpin && window.parent.vpin.players;
    return (held && Array.isArray(held.players)) ? held.players : [];
  } catch (_e) {
    return [];
  }
}

function playerToken(player) {
  return (player && (player.initials || player.name))
    || t('frontend.mainmenu.no_name', 'No name');
}

function refreshPlayerMenuLabel() {
  const item = document.getElementById('player-item');
  // A choice list owns the menu's rows while it is open; rebuilding under it here
  // would pull its rows into the navigable list. It catches up when the list closes.
  if (!item || choosing) return;
  const roster = playersRoster();
  if (roster.length <= 1) {
    item.style.display = 'none';
  } else {
    const up = roster.filter((player) => player.up);
    item.textContent = up.length <= 1
      ? t('frontend.mainmenu.player_one', 'Player: {name}',
          { name: playerToken(up[0]) })
      : t('frontend.mainmenu.player_many', 'Players: {names}',
          { names: up.map(playerToken).join(', ') });
    item.style.display = '';
  }
  rebuildMenuItems();
  syncMenuWidthFromLongestLabel();
}

function rebuildMenuItems() {
  // Items come and go with the config, so the cursor is carried across and clamped to
  // what is left rather than reset - the same thing the hand-rolled bounds check did.
  menu = navigable(
    Array.from(document.querySelectorAll('.menu-item')).filter(
      (item) => getComputedStyle(item).display !== 'none'
    ),
    menu ? menu.cursor : 0);
}

function rotateMenu(degrees) {
  rotationAngle = degrees;
  document.getElementById('menu-container').style.transform = `rotate(${rotationAngle}deg)`;
}

function resolveCurrentGameIndex() {
  try {
    const evalIndex = Number(window.parent.eval('typeof currentGameIndex !== "undefined" ? currentGameIndex : undefined'));
    if (Number.isFinite(evalIndex) && evalIndex >= 0) {
      currentGameIndex = Math.floor(evalIndex);
      return currentGameIndex;
    }
  } catch (_e) {}

  try {
    const evalSelected = Number(window.parent.eval('typeof selectedIndex !== "undefined" ? selectedIndex : undefined'));
    if (Number.isFinite(evalSelected) && evalSelected >= 0) {
      currentGameIndex = Math.floor(evalSelected);
      return currentGameIndex;
    }
  } catch (_e) {}

  try {
    const themeIndex = Number(window.parent.currentGameIndex);
    if (Number.isFinite(themeIndex) && themeIndex >= 0) {
      currentGameIndex = Math.floor(themeIndex);
      return currentGameIndex;
    }
  } catch (_e) {}

  try {
    const parentIndex = Number(window.parent.vpin.getCurrentTableIndex());
    if (Number.isFinite(parentIndex) && parentIndex >= 0) {
      currentGameIndex = Math.floor(parentIndex);
    }
  } catch (_e) {}

  return currentGameIndex;
}

// A rating is 0 to 10, a half star a step, drawn as five stars.
const RATING_HALVES = 10;
const STAR_LIT = '#ffd84d';
const STAR_OFF = '#666';
const STAR_POINTS = '12,1.5 15.1,8.6 22.8,9.3 17,14.4 18.8,22 12,18 5.2,22 7,14.4 1.2,9.3 8.9,8.6';

function normalizeRating(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return 0;
  return Math.max(0, Math.min(RATING_HALVES, Math.floor(numeric)));
}

function starSvg(color, filled, from, width) {
  return `<svg viewBox="${from} 0 12 24" width="${width}em" height="1em" ` +
    `style="display:block;" aria-hidden="true"><polygon points="${STAR_POINTS}" ` +
    `fill="${filled ? color : 'none'}" stroke="${color}" stroke-width="1.6" ` +
    `stroke-linejoin="round"/></svg>`;
}

// One half of a star, the left or the right, lit or not.
function starHalfSvg(lit, right) {
  return starSvg(lit ? STAR_LIT : STAR_OFF, lit, right ? 12 : 0, 0.5);
}

// Five stars as markup, a half star showing as half lit.
function ratingStarsHtml(rating) {
  const held = normalizeRating(rating);
  let out = '';
  for (let star = 0; star < 5; star++) {
    out += '<span style="display:inline-flex;margin-right:0.1em;">' +
      starHalfSvg(held >= star * 2 + 1, false) + starHalfSvg(held >= star * 2 + 2, true) +
      '</span>';
  }
  return `<span style="display:inline-flex;vertical-align:-0.12em;">${out}</span>`;
}

function ratingText(rating) {
  const stars = normalizeRating(rating) / 2;
  return String(stars);
}

function syncMenuWidthFromLongestLabel() {
  const menu = document.getElementById('menu');
  const container = document.getElementById('menu-container');
  if (!menu) return;
  if (!container) return;

  const menuItems = Array.from(menu.querySelectorAll('.menu-item')).filter(
    (item) => getComputedStyle(item).display !== 'none'
  );
  if (menuItems.length === 0) return;

  // Reserve left/right "cap" space in the button image so labels stay centered.
  const sizingBasis = Math.min(container.clientWidth, container.clientHeight);
  const sideInsetPx = Math.max(28, Math.round(sizingBasis * 0.09));
  const edgeBufferPx = Math.max(5, Math.round(sizingBasis * 0.01));
  menu.style.setProperty('--menu-item-side-inset', `${sideInsetPx}px`);

  const styleProbe = getComputedStyle(menuItems[0]);
  const ruler = document.createElement('span');
  ruler.style.position = 'absolute';
  ruler.style.visibility = 'hidden';
  ruler.style.whiteSpace = 'pre';
  ruler.style.pointerEvents = 'none';
  ruler.style.font = styleProbe.font;
  ruler.style.fontSize = styleProbe.fontSize;
  ruler.style.fontWeight = styleProbe.fontWeight;
  ruler.style.fontFamily = styleProbe.fontFamily;
  ruler.style.letterSpacing = styleProbe.letterSpacing;
  ruler.style.textTransform = styleProbe.textTransform;
  document.body.appendChild(ruler);

  let maxLabelWidth = 0;
  menuItems.forEach((item) => {
    ruler.textContent = item.textContent || '';
    const avatar = item.querySelector('.player-avatar');
    const avatarWidth = avatar
      ? avatar.getBoundingClientRect().width + parseFloat(getComputedStyle(avatar).marginRight || '0')
      : 0;
    maxLabelWidth = Math.max(
      maxLabelWidth,
      Math.ceil(ruler.getBoundingClientRect().width + avatarWidth)
    );
  });
  ruler.remove();

  const computedContainer = getComputedStyle(container);
  const containerInnerWidth =
    container.clientWidth
    - parseFloat(computedContainer.paddingLeft || '0')
    - parseFloat(computedContainer.paddingRight || '0');
  const labelWidthWithExtra = Math.ceil(maxLabelWidth * 1.4);
  const rawTargetWidth = Math.ceil(labelWidthWithExtra + sideInsetPx * 2 + edgeBufferPx * 2);
  const targetWidth = Math.max(180, Math.min(rawTargetWidth, Math.floor(containerInnerWidth)));
  menu.style.width = `${targetWidth}px`;

  menuItems.forEach((item) => {
    item.style.whiteSpace = 'nowrap';
    item.style.width = '100%';
  });
}

function scheduleMenuRelayout() {
  if (relayoutTimer) {
    clearTimeout(relayoutTimer);
    relayoutTimer = null;
  }

  requestAnimationFrame(() => {
    syncMenuWidthFromLongestLabel();
    requestAnimationFrame(() => {
      syncMenuWidthFromLongestLabel();
      relayoutTimer = setTimeout(() => {
        syncMenuWidthFromLongestLabel();
        relayoutTimer = null;
      }, 120);
    });
  });
}

// The player the Rating stars are for, or null for the owner.
function asRatingTarget(player) {
  return player ? { id: player.id, owner: !!player.owner, token: playerToken(player) } : null;
}

function ratingLabelTarget() {
  const roster = playersRoster();
  if (roster.length <= 1) return null;
  const up = roster.filter((player) => player.up);
  return up.length === 1 ? asRatingTarget(up[0]) : null;
}

async function readRating(target, idx) {
  return target
    ? window.parent.vpin.callInternal('get_player_rating', target.id, idx)
    : window.parent.vpin.callInternal('get_rating_halves', idx);
}

async function refreshRatingMenuLabel(indexHint = null) {
  const ratingItem = document.getElementById('rating-item');
  if (!ratingItem) return;

  const requestSeq = ++ratingLabelRequestSeq;
  try {
    let idx = Number(indexHint);
    if (!Number.isFinite(idx) || idx < 0) {
      idx = resolveCurrentGameIndex();
    } else {
      idx = Math.floor(idx);
      currentGameIndex = idx;
    }

    const savedRating = await readRating(ratingLabelTarget(), idx);
    if (requestSeq !== ratingLabelRequestSeq) return;
    ratingItem.innerHTML = `${t('word.rating', 'Rating')} (${ratingStarsHtml(savedRating)})`;
    syncMenuWidthFromLongestLabel();
  } catch (_e) {
    if (requestSeq !== ratingLabelRequestSeq) return;
    ratingItem.textContent = t('word.rating', 'Rating');
    syncMenuWidthFromLongestLabel();
  }
}

window.__vpinWordsChanged = () => {
  refreshRatingMenuLabel();
};

function handleInput(input) {
  if (!menuConfigLoaded || !menu || !menu.length) return;

  if (dialogState === 'options' || dialogState === 'rating') {
    handleDialogInput(input);
    return;
  }

  if (choosing) {
    handleChoiceInput(input);
    return;
  }

  if (dialogState === 'progress') {
    if (input === 'back' || input === 'select') {
      const closeBtn = document.getElementById('buildmeta-close');
      if (closeBtn.style.display !== 'none') {
        hideBuildMetaDialog();
      }
    }
    return;
  }

  switch (input) {
    case 'page_previous':
    case 'previous':
      menu.moveBy(-1);
      break;
    case 'page_next':
    case 'next':
      menu.moveBy(1);
      break;
    case 'select': {
      const selectedItem = menu.current;
      // Through requestLifecycle, not the close_app/shutdown_system bridge calls it used
      // to make. Those are the 2.x spellings and they go straight to the backend - which
      // cannot raise a dialog, because the bridge to a window only goes one way. So
      // "Confirm Before Exit" did nothing from the one menu that can shut the cabinet
      // down. The confirm is drawn here and answered with select and back.
      if (selectedItem.id === 'quit-item') {
        window.parent.vpin.requestLifecycle('vpinfe', 'stop');
      } else if (selectedItem.id === 'shutdown-item') {
        window.parent.vpin.requestLifecycle('system', 'stop');
      } else if (selectedItem.id === 'rating-item') {
        startRatingFlow();
      } else if (selectedItem.id === 'player-item') {
        showPlayerChoices('player_up',
                          t('frontend.mainmenu.who_is_up', "Who's Up"));
        return;
      } else if (selectedItem.id === 'audio-item') {
        toggleAudioMute();
      } else if (selectedItem.id === 'buildmeta-item') {
        showBuildMetaDialog();
      } else if (selectedItem.id === 'record-item' && recordOffer) {
        showRecordChoices();
        return;
      }
      break;
    }
    case 'back':
      window.parent.vpin.toggleOverlay("menu");
      break;
  }
  updateMenu();
}

// Hidden where this device records nothing for the game; its label says whether the
// game lacks anything, and its choices are the ones that would do something.
async function loadRecordOffer() {
  const item = document.getElementById('record-item');
  if (!item) return;
  const asked = ++offerSeq;
  let offer = null;
  try {
    offer = await window.parent.vpin.callInternal('recording_offer', currentGameIndex);
  } catch (_e) {
    offer = null;
  }
  if (asked !== offerSeq || choosing) return;
  recordOffer = offer && offer.label && Array.isArray(offer.choices) && offer.choices.length
    ? offer : null;
  item.textContent = recordOffer ? recordOffer.label : '';
  // The answer can land after a press has moved the cursor, so the item under it stays
  // under it rather than the one that took its place.
  const selected = menu ? menu.current : null;
  item.style.display = recordOffer ? '' : 'none';
  menu = null;
  rebuildMenuItems();
  const at = selected ? menu.items.indexOf(selected) : -1;
  if (at >= 0) menu.moveTo(at);
  updateMenu();
  scheduleMenuRelayout();
}

// The same list, holding the choices under the item's name.
function showRecordChoices() {
  const list = document.getElementById('menu');
  for (const choice of recordOffer.choices) {
    const item = document.createElement('li');
    item.className = 'menu-item record-choice';
    item.dataset.existing = choice.existing;
    item.textContent = choice.label;
    list.appendChild(item);
  }
  list.classList.add('choosing');
  const heading = document.getElementById('menu-heading');
  heading.textContent = recordOffer.label;
  heading.hidden = false;
  choosing = true;
  choosingKind = 'record';
  menu = null;
  updateMenu();
  scheduleMenuRelayout();
}

// Player and Rating's "whose" both open the same kind of list: every kept player and
// guest, under a heading, in the roster's own order. `kind` says what Start does with
// a row - see handleChoiceInput.
function showPlayerChoices(kind, heading) {
  const list = document.getElementById('menu');
  for (const player of playersRoster()) {
    const item = document.createElement('li');
    item.className = 'menu-item player-choice';
    item.dataset.playerId = player.id;
    item.dataset.name = playerToken(player);
    if (player.up) item.classList.add('player-choice-up');
    const label = document.createElement('span');
    label.className = 'player-choice-label';
    label.textContent = playerChoiceLabel(item);
    item.append(playerAvatar(player), label);
    list.appendChild(item);
  }
  list.classList.add('choosing');
  const headingEl = document.getElementById('menu-heading');
  headingEl.textContent = heading;
  headingEl.hidden = false;
  choosing = true;
  choosingKind = kind;
  menu = null;
  updateMenu();
  scheduleMenuRelayout();
}

// The player's circle: their picture, or their initials where there is none or it will
// not load.
function playerAvatar(player) {
  const avatar = document.createElement('span');
  avatar.className = 'player-avatar';
  avatar.setAttribute('aria-hidden', 'true');
  const initials = String(player.initials || '').trim();
  if (initials) avatar.dataset.initials = initials;
  else avatar.classList.add('player-avatar-none');
  if (player.picture) {
    const image = document.createElement('img');
    image.alt = '';
    image.draggable = false;
    image.addEventListener('error', () => image.remove());
    image.src = player.picture;
    avatar.appendChild(image);
  }
  return avatar;
}

// A check on whoever is up - the roster always has someone - a blank space of the
// same width on everyone else, so the names still line up.
function playerChoiceLabel(item) {
  const mark = item.classList.contains('player-choice-up') ? '✓ ' : '  ';
  return mark + item.dataset.name;
}

function leaveChoices(backTo = null) {
  document.querySelectorAll('.record-choice, .player-choice').forEach((item) => item.remove());
  document.getElementById('menu')?.classList.remove('choosing');
  for (const id of ['menu-heading', 'menu-note']) {
    const element = document.getElementById(id);
    if (element) {
      element.hidden = true;
      element.textContent = '';
    }
  }
  const was = choosing;
  const wasKind = choosingKind;
  choosing = false;
  choosingKind = null;
  recordStarting = false;
  if (!was) return;
  menu = null;
  rebuildMenuItems();
  const at = backTo ? menu.items.indexOf(backTo) : -1;
  if (at >= 0) menu.moveTo(at);
  updateMenu();
  scheduleMenuRelayout();
  // Start's toggle already updated the row; this catches the label up without waiting
  // on the PlayersChanged round trip.
  if (wasKind === 'player_up') refreshPlayerMenuLabel();
}

function handleChoiceInput(input) {
  switch (input) {
    case 'page_previous':
    case 'previous':
      menu.moveBy(-1);
      break;
    case 'page_next':
    case 'next':
      menu.moveBy(1);
      break;
    case 'select':
      if (choosingKind === 'player_up') togglePlayerUp(menu.current);
      else if (choosingKind === 'player_pick') pickPlayerForRating(menu.current);
      else startRecording(menu.current);
      return;
    case 'back':
      leaveChoices(document.getElementById(
        choosingKind === 'player_up' ? 'player-item'
          : choosingKind === 'player_pick' ? 'rating-item' : 'record-item'));
      return;
  }
  updateMenu();
}

async function startRecording(choice) {
  if (recordStarting || !choice || !choice.dataset.existing) return;
  recordStarting = true;
  try {
    await window.parent.vpin.callInternal('record_media', currentGameIndex,
                                          choice.dataset.existing);
  } catch (err) {
    const note = document.getElementById('menu-note');
    note.textContent = (err && err.message) || '';
    note.hidden = !note.textContent;
    recordStarting = false;
    scheduleMenuRelayout();
    return;
  }
  leaveChoices();
  window.parent.vpin.toggleOverlay('menu');
}

// Start on a Player row toggles it, live - there is no separate save step, since
// several players can be up at once. A refusal (the id is stale) leaves the row as
// it was.
async function togglePlayerUp(item) {
  if (!item || !item.dataset.playerId) return;
  const wantUp = !item.classList.contains('player-choice-up');
  try {
    await window.parent.vpin.callInternal('set_player_up', item.dataset.playerId, wantUp);
  } catch (_e) {
    return;
  }
  item.classList.toggle('player-choice-up', wantUp);
  const label = item.querySelector('.player-choice-label');
  if (label) label.textContent = playerChoiceLabel(item);
}

// Start on a Rating "whose" row picks that player and moves straight to the stars -
// there is nothing else to do with the choice.
function pickPlayerForRating(item) {
  if (!item || !item.dataset.playerId) return;
  const chosen = playersRoster().find((player) => player.id === item.dataset.playerId);
  leaveChoices(null);
  showRatingDialog(asRatingTarget(chosen));
}

function handleDialogInput(input) {
  switch (input) {
    case 'page_previous':
    case 'previous':
      dialog.moveBy(-1);
      updateDialogSelection();
      break;
    case 'page_next':
    case 'next':
      dialog.moveBy(1);
      updateDialogSelection();
      break;
    case 'select': {
      const selectedElement = dialog.current;
      if (selectedElement.type === 'checkbox') {
        selectedElement.checked = !selectedElement.checked;
      } else if (selectedElement.tagName === 'BUTTON') {
        selectedElement.click();
      }
      break;
    }
    case 'back':
      closeActiveDialog();
      break;
  }
}

function closeActiveDialog() {
  if (dialogState === 'options' || dialogState === 'progress') {
    hideBuildMetaDialog();
  } else if (dialogState === 'rating') {
    hideRatingDialog();
  }
}

function updateDialogSelection() {
  dialog.items.forEach((item, i) => {
    if (item.tagName === 'BUTTON') {
      if (i === dialog.cursor) {
        item.style.outline = '3px solid #2196F3';
        item.style.outlineOffset = '2px';
      } else {
        item.style.outline = 'none';
      }
    } else if (item.parentElement && item.parentElement.tagName === 'LABEL') {
      if (i === dialog.cursor) {
        item.parentElement.style.outline = '3px solid #2196F3';
        item.parentElement.style.outlineOffset = '2px';
      } else {
        item.parentElement.style.outline = 'none';
      }
    }
  });
}

function showBuildMetaDialog() {
  document.getElementById('buildmeta-overlay').style.display = 'block';
  document.getElementById('buildmeta-options').style.display = 'block';
  document.getElementById('buildmeta-progress').style.display = 'none';
  dialogState = 'options';
  dialog = navigable([
    document.getElementById('update-all-check'),
    document.getElementById('download-media-check'),
    document.getElementById('buildmeta-cancel'),
    document.getElementById('buildmeta-start'),
  ]);
  updateDialogSelection();
}

function hideBuildMetaDialog() {
  document.getElementById('buildmeta-overlay').style.display = 'none';
  dialogState = null;
  dialog = navigable();
}

function renderRatingStars() {
  const row = document.getElementById('rating-stars-row');
  if (!row.children.length) {
    row.style.fontSize = '5vh';
    for (let half = 1; half <= RATING_HALVES; half++) {
      const btn = document.createElement('button');
      btn.className = 'rating-star';
      btn.dataset.rating = String(half);
      btn.style.cssText = 'background: transparent; border: none; padding: 0; line-height: 0; cursor: pointer;' +
        (half % 2 === 0 && half < RATING_HALVES ? ' margin-right: 1vh;' : '');
      btn.addEventListener('click', () => {
        ratingDraft = half;
        renderRatingStars();
      });
      row.appendChild(btn);
    }
  }
  Array.from(row.children).forEach((btn) => {
    const half = Number(btn.dataset.rating);
    btn.innerHTML = starHalfSvg(half <= ratingDraft, half % 2 === 0);
  });

  document.getElementById('rating-current-text').innerHTML =
    t('frontend.mainmenu.current_rating', 'Current: {stars} ({rating}/5)', {
      stars: ratingStarsHtml(ratingDraft),
      rating: ratingText(ratingDraft),
    });
}

// With several up there is no single player to rate for without asking; the picked
// answer lands back here through pickPlayerForRating.
function startRatingFlow() {
  const roster = playersRoster();
  if (roster.length <= 1) {
    showRatingDialog(null);
    return;
  }
  const up = roster.filter((player) => player.up);
  if (up.length === 1) {
    showRatingDialog(asRatingTarget(up[0]));
    return;
  }
  showPlayerChoices('player_pick', t('frontend.mainmenu.rating_whose', 'Whose rating?'));
}

async function showRatingDialog(forPlayer) {
  ratingForPlayer = forPlayer;
  ratingGameIndex = resolveCurrentGameIndex();
  try {
    ratingDraft = normalizeRating(await readRating(forPlayer, ratingGameIndex));
  } catch (_e) {
    ratingDraft = 0;
  }

  const forText = document.getElementById('rating-for-text');
  forText.hidden = !forPlayer;
  forText.textContent = forPlayer
    ? t('frontend.mainmenu.rating_for', 'Rating for {name}', { name: forPlayer.token })
    : '';

  renderRatingStars();
  document.getElementById('rating-overlay').style.display = 'block';
  dialogState = 'rating';
  dialog = navigable([
    ...Array.from(document.querySelectorAll('.rating-star')),
    document.getElementById('rating-clear'),
    document.getElementById('rating-cancel'),
    document.getElementById('rating-save'),
  ], Math.max(0, ratingDraft - 1));
  updateDialogSelection();
}

function hideRatingDialog() {
  document.getElementById('rating-overlay').style.display = 'none';
  dialogState = null;
  dialog = navigable();
  ratingForPlayer = null;
}

async function saveRatingDialog() {
  try {
    if (ratingForPlayer && !ratingForPlayer.owner) {
      await window.parent.vpin.callInternal(
        'set_player_rating', ratingForPlayer.id, ratingGameIndex, ratingDraft);
    } else {
      await window.parent.vpin.callInternal('set_rating_halves', ratingGameIndex, ratingDraft);
    }
    window.parent.vpin.sendMessageToAllWindowsIncSelf({
      type: 'TableDataChange',
      index: ratingGameIndex,
    });
    await refreshRatingMenuLabel(ratingGameIndex);
  } catch (_e) {}
  hideRatingDialog();
}

function startBuildMeta() {
  const updateAll = document.getElementById('update-all-check').checked;
  const downloadMedia = document.getElementById('download-media-check').checked;

  document.getElementById('buildmeta-options').style.display = 'none';
  document.getElementById('buildmeta-progress').style.display = 'block';
  document.getElementById('log-container').innerHTML = '';
  document.getElementById('progress-bar').style.width = '0%';
  document.getElementById('progress-text').textContent =
    t('frontend.mainmenu.starting', 'Starting...');
  document.getElementById('buildmeta-close').style.display = 'none';

  dialogState = 'progress';
  dialog = navigable();

  window.parent.vpin.call('build_metadata', downloadMedia, updateAll);
}

window.receiveEvent = function(event) {
  if (event.type === 'buildmeta_progress') {
    const percent = event.total > 0 ? Math.round((event.current / event.total) * 100) : 0;
    document.getElementById('progress-bar').style.width = `${percent}%`;
    document.getElementById('progress-text').textContent =
      t('frontend.mainmenu.progress', '{message} - {percent}%',
        { message: event.message, percent });
  } else if (event.type === 'buildmeta_log') {
    const logContainer = document.getElementById('log-container');
    const logLine = document.createElement('div');
    logLine.textContent = event.message;
    logLine.style.marginBottom = '0.3vh';
    logContainer.appendChild(logLine);
    logContainer.scrollTop = logContainer.scrollHeight;
  } else if (event.type === 'buildmeta_complete') {
    document.getElementById('progress-bar').style.width = '100%';
    const missed = event.result.not_found;
    document.getElementById('progress-text').textContent = missed
      ? t('frontend.mainmenu.done_with_misses', 'Done. {count} could not be matched or read.',
          { count: missed })
      : t('word.done', 'Done');
    document.getElementById('buildmeta-close').style.display = 'block';
  } else if (event.type === 'buildmeta_error') {
    document.getElementById('progress-text').textContent = event.error;
    document.getElementById('progress-text').style.color = '#f44336';
    document.getElementById('buildmeta-close').style.display = 'block';
  }
};

function updateMenu() {
  rebuildMenuItems();
  if (!menu.length) return;

  menu.items.forEach((item, i) => {
    item.classList.toggle('selected', i === menu.cursor);
  });
  syncMenuWidthFromLongestLabel();
}

function audioMenuLabel(muted) {
  return t('frontend.mainmenu.frontend_audio', 'Frontend Audio: {state}',
           { state: muted ? t('frontend.mainmenu.off', 'Off')
                          : t('frontend.mainmenu.on', 'On') });
}

async function refreshAudioMenuLabel() {
  const audioItem = document.getElementById('audio-item');
  if (!audioItem) return;

  try {
    const muted = await window.parent.vpin.call('get_audio_muted');
    audioMuted = !!muted;
  } catch (_e) {
    audioMuted = false;
  }
  audioItem.textContent = audioMenuLabel(audioMuted);
  syncMenuWidthFromLongestLabel();
}

async function toggleAudioMute() {
  const nextMuted = !audioMuted;
  try {
    const savedMuted = await window.parent.vpin.call('set_audio_muted', nextMuted);
    audioMuted = !!savedMuted;
  } catch (_e) {
    audioMuted = nextMuted;
  }

  const audioItem = document.getElementById('audio-item');
  if (audioItem) {
    audioItem.textContent = audioMenuLabel(audioMuted);
    syncMenuWidthFromLongestLabel();
  }

  if (window.parent.vpin && typeof window.parent.vpin.setAudioMuted === 'function') {
    window.parent.vpin.setAudioMuted(audioMuted);
  }
}

window.onload = async () => {
  currentGameIndex = resolveCurrentGameIndex();
  await applyMainMenuConfig();
  await loadRemoteQrPanel();
  rebuildMenuItems();
  syncMenuWidthFromLongestLabel();
  await refreshRatingMenuLabel(currentGameIndex);
  await refreshAudioMenuLabel();
  updateMenu();
  scheduleMenuRelayout();

  document.getElementById('buildmeta-cancel').addEventListener('click', hideBuildMetaDialog);
  document.getElementById('buildmeta-start').addEventListener('click', startBuildMeta);
  document.getElementById('buildmeta-close').addEventListener('click', hideBuildMetaDialog);

  document.getElementById('rating-clear').addEventListener('click', () => {
    ratingDraft = 0;
    renderRatingStars();
  });
  document.getElementById('rating-cancel').addEventListener('click', hideRatingDialog);
  document.getElementById('rating-save').addEventListener('click', saveRatingDialog);
};

window.addEventListener('resize', () => {
  syncMenuWidthFromLongestLabel();
});
