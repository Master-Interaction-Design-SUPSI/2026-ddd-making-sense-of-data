const assert = require('node:assert/strict');
const { test } = require('node:test');
const { createVideoController } = require('../static/playback.js');

class Video extends EventTarget {
  paused = true;
  muted = true;
  ended = false;
  attempts = [];
  playAction = () => Promise.resolve();
  async play() {
    this.attempts.push({ muted: this.muted });
    await this.playAction();
    this.paused = false;
    this.dispatchEvent(new Event('play'));
  }
  pause() {
    const changed = !this.paused;
    this.paused = true;
    if (changed) this.dispatchEvent(new Event('pause'));
  }
}

const settle = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
};

test('visibility starts muted playback and leaving visibility pauses it', async () => {
  const video = new Video();
  const controller = createVideoController(video, () => {});
  assert.equal(video.attempts.length, 0);
  controller.setActive(true);
  await settle();
  assert.deepEqual(video.attempts, [{ muted: true }]);
  assert.equal(video.paused, false);
  controller.setActive(false);
  assert.equal(video.paused, true);
});

test('refused autoplay exposes Play, avoids retry loops and permits manual playback', async () => {
  const video = new Video();
  let state;
  video.playAction = () => Promise.reject(Object.assign(new Error('blocked'), { name: 'NotAllowedError' }));
  const controller = createVideoController(video, next => { state = next; });
  controller.setActive(true);
  await settle();
  assert.equal(state.playing, false);
  assert.equal(state.pending, false);
  assert.match(state.status, /Press Play/);
  controller.setActive(false);
  controller.setActive(true);
  assert.equal(video.attempts.length, 1);
  video.playAction = () => Promise.resolve();
  controller.toggle();
  await settle();
  assert.equal(video.paused, false);
  assert.equal(state.status, '');
});

test('user pause survives leaving and re-entering visibility', async () => {
  const video = new Video();
  const controller = createVideoController(video, () => {});
  controller.setActive(true);
  await settle();
  controller.toggle();
  controller.setActive(false);
  controller.setActive(true);
  await settle();
  assert.equal(video.paused, true);
  assert.equal(video.attempts.length, 1);
  controller.toggle();
  await settle();
  assert.equal(video.paused, false);
});

test('leaving while play is pending prevents late unwanted playback', async () => {
  const video = new Video();
  const pending = deferred();
  video.playAction = () => pending.promise;
  const controller = createVideoController(video, () => {});
  controller.setActive(true);
  controller.setActive(false);
  pending.resolve();
  await settle();
  assert.equal(video.paused, true);
});

test('user pause cancels a pending autoplay attempt', async () => {
  const video = new Video();
  const pending = deferred();
  video.playAction = () => pending.promise;
  const controller = createVideoController(video, () => {});
  controller.setActive(true);
  controller.toggle();
  pending.resolve();
  await settle();
  assert.equal(video.paused, true);
});

test('an obsolete play completion does not pause a newer requested playback', async () => {
  const video = new Video();
  const first = deferred();
  video.playAction = () => first.promise;
  const controller = createVideoController(video, () => {});
  controller.setActive(true);
  controller.toggle();
  video.playAction = () => Promise.resolve();
  controller.toggle();
  await settle();
  first.resolve();
  await settle();
  assert.equal(video.paused, false);
});

test('sound is explicit and unmuted videos do not resume automatically', async () => {
  const video = new Video();
  const controller = createVideoController(video, () => {});
  controller.setActive(true);
  await settle();
  controller.toggleSound();
  assert.equal(video.muted, false);
  controller.setActive(false);
  controller.setActive(true);
  await settle();
  assert.equal(video.paused, true);
  controller.toggle();
  await settle();
  assert.equal(video.paused, false);
  assert.equal(video.muted, false);
});

test('reduced motion disables automatic playback but preserves manual controls', async () => {
  const video = new Video();
  const controller = createVideoController(video, () => {}, false);
  controller.setActive(true);
  assert.equal(video.attempts.length, 0);
  controller.toggle();
  await settle();
  assert.equal(video.paused, false);
});

test('ended and unavailable media remain paused with a usable retry control', async () => {
  const video = new Video();
  let state;
  const controller = createVideoController(video, next => { state = next; });
  controller.setActive(true);
  await settle();
  video.paused = true;
  video.ended = true;
  video.dispatchEvent(new Event('ended'));
  controller.setActive(false);
  controller.setActive(true);
  assert.equal(video.attempts.length, 1);
  video.dispatchEvent(new Event('error'));
  assert.equal(state.pending, false);
  assert.match(state.status, /Could not load/);
  assert.equal(state.playing, false);
});
