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

test('refused autoplay reports its status and avoids retry loops', async () => {
  const video = new Video();
  let state;
  video.playAction = () => Promise.reject(Object.assign(new Error('blocked'), { name: 'NotAllowedError' }));
  const controller = createVideoController(video, next => { state = next; });
  controller.setActive(true);
  await settle();
  assert.equal(state.playing, false);
  assert.equal(state.pending, false);
  assert.match(state.status, /Automatic playback is unavailable/);
  controller.setActive(false);
  controller.setActive(true);
  assert.equal(video.attempts.length, 1);
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

test('an obsolete play completion does not pause a newer requested playback', async () => {
  const video = new Video();
  const first = deferred();
  video.playAction = () => first.promise;
  const controller = createVideoController(video, () => {});
  controller.setActive(true);
  controller.setActive(false);
  video.playAction = () => Promise.resolve();
  controller.setActive(true);
  await settle();
  first.resolve();
  await settle();
  assert.equal(video.paused, false);
});

test('unmuted videos do not resume automatically', async () => {
  const video = new Video();
  const controller = createVideoController(video, () => {});
  controller.setActive(true);
  await settle();
  video.muted = false;
  assert.equal(video.muted, false);
  controller.setActive(false);
  controller.setActive(true);
  await settle();
  assert.equal(video.paused, true);
});

test('reduced motion disables automatic playback', async () => {
  const video = new Video();
  const controller = createVideoController(video, () => {}, false);
  controller.setActive(true);
  assert.equal(video.attempts.length, 0);
  controller.setActive(false);
  controller.setActive(true);
  await settle();
  assert.equal(video.attempts.length, 0);
  assert.equal(video.paused, true);
});

test('ended and unavailable media remain paused with an error status', async () => {
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

test('disposing a controller cancels pending play and prevents remounting removed media', async () => {
  const video = new Video();
  const pending = deferred();
  video.playAction = () => pending.promise;
  const controller = createVideoController(video, () => {});
  controller.setActive(true);
  controller.dispose();
  pending.resolve();
  await settle();
  assert.equal(video.paused, true);
  controller.setActive(true);
  assert.equal(video.attempts.length, 1);
});
