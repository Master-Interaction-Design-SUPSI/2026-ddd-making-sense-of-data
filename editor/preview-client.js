((root) => {
  const protocol = typeof module !== 'undefined' && module.exports ? require('./protocol.js') : root.CoursePreviewProtocol;

  class PreviewClient {
    constructor({ frame, host = window, origin, contract, onState }) {
      this.frame = frame; this.host = host; this.origin = origin; this.contract = contract; this.onState = onState;
      this.requestedRevision = 0; this.acknowledgedRevision = 0; this.sentRevision = 0;
      this.ready = false; this.disposed = false; this.builder = null;
      this.visible = true;
      this.receive = event => {
        if (this.disposed || event.source !== frame.contentWindow || event.origin !== origin || !protocol.validReply(event.data)) return;
        const message = event.data;
        if (message.type === 'ready') {
          if (this.ready) return;
          this.ready = true; this.clearTimer();
          this.sendVisibility();
          if (this.builder) this.deliver(this.requestedRevision);
          else this.report('waiting', 'Open a group to preview.');
          return;
        }
        if (message.revision !== this.requestedRevision || message.revision !== this.sentRevision) return;
        this.clearTimer();
        if (message.type === 'ack') {
          this.acknowledgedRevision = message.revision;
          this.report('current', 'Preview is current.');
        } else this.report('unavailable', `Preview unavailable: ${message.message}`);
      };
      this.reload = () => this.reconnect();
      host.addEventListener('message', this.receive);
      frame.addEventListener('load', this.reload);
      this.connect();
    }
    report(status, message) {
      this.onState({ status, message, requestedRevision: this.requestedRevision, acknowledgedRevision: this.acknowledgedRevision });
    }
    clearTimer() { this.host.clearTimeout(this.timer); }
    sendVisibility() {
      this.frame.contentWindow.postMessage({ version: protocol.version, type: 'visibility', visible: this.visible }, this.origin);
    }
    setVisible(visible) {
      if (this.disposed || this.visible === visible) return;
      this.visible = visible;
      if (this.ready) this.sendVisibility();
      if (visible && this.builder) this.request(this.builder);
    }
    deadline() {
      this.clearTimer();
      this.timer = this.host.setTimeout(() => this.report('unavailable', 'Preview unavailable. Retry to render the current draft.'), 10000);
    }
    connect() {
      this.report(this.builder ? 'stale' : 'waiting', 'Connecting to preview…');
      this.deadline();
      this.frame.contentWindow.postMessage({ version: protocol.version, type: 'hello' }, this.origin);
    }
    request(builder, delay = 0) {
      if (this.disposed) return;
      this.host.clearTimeout(this.pendingBuild);
      this.builder = builder; this.requestedRevision++;
      this.report('stale', 'Preview is updating; the displayed version may be older.');
      this.deadline();
      if (!this.ready) return;
      const revision = this.requestedRevision;
      if (delay) this.pendingBuild = this.host.setTimeout(() => this.deliver(revision), delay);
      else this.deliver(revision);
    }
    async deliver(revision) {
      this.deadline();
      try {
        const payload = await this.builder();
        if (this.disposed || !this.ready || revision !== this.requestedRevision) return;
        const message = { ...payload, version: protocol.version, type: 'render', revision };
        protocol.validateRender(message, this.contract);
        this.sentRevision = revision;
        this.frame.contentWindow.postMessage(message, this.origin);
      } catch (error) {
        if (!this.disposed && revision === this.requestedRevision) {
          this.clearTimer(); this.report('unavailable', `Preview unavailable: ${error.message}`);
        }
      }
    }
    retry() {
      if (this.disposed) return;
      if (!this.ready) this.frame.src = this.frame.src;
      this.reconnect();
    }
    reconnect() {
      if (this.disposed) return;
      this.host.clearTimeout(this.pendingBuild);
      this.ready = false; this.sentRevision = 0;
      // Every reconnect resends the full asset set under a fresh revision.
      if (this.builder) this.requestedRevision++;
      this.connect();
    }
    dispose() {
      this.disposed = true; this.clearTimer();
      this.host.clearTimeout(this.pendingBuild);
      this.host.removeEventListener('message', this.receive);
      this.frame.removeEventListener('load', this.reload);
    }
  }
  const api = { PreviewClient };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.CoursePreviewClient = api;
})(globalThis);
