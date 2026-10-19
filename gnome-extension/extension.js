// On-screen pill for the voice-to-text daemon: cancel (X), live level bars, confirm (check) while
// recording; a shimmering status label ("Loading model…", "Transcribing… 40%") while processing;
// and a short-lived error/warning message when something goes wrong (click the pill to dismiss).
// It polls the daemon's state file and talks back over the daemon's unix socket.

import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import Pango from 'gi://Pango';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const POLL_MS = 50;
const BAR_COUNT = 14;
const BAR_MIN = 3;
const BAR_MAX = 20;
const STALE_RECORDING_SECONDS = 1.5; // the daemon writes ~20x/s while recording
const NOTICE_SECONDS = 4; // how long error/warning messages stay up
const SHIMMER_HALF_WIDTH = 28;
const SHIMMER_STEP = 5; // px per poll tick

const NOTICE_ICONS = {error: 'dialog-error-symbolic', warning: 'dialog-warning-symbolic'};
const NOTICE_COLORS = {error: '#ff8a80', warning: '#ffd180'};
const LABEL_STYLE = 'font-size: 10.5pt; padding: 0 12px; max-width: 460px;';

const RUNTIME_DIR = GLib.getenv('XDG_RUNTIME_DIR') ?? `/tmp/voice-to-text-${GLib.getenv('UID') ?? ''}`;
const STATE_FILE = Gio.File.new_for_path(`${RUNTIME_DIR}/voice-to-text.state`);
const SOCKET_PATH = `${RUNTIME_DIR}/voice-to-text.sock`;

export default class VoiceToTextOverlay extends Extension {
    enable() {
        this._levels = new Array(BAR_COUNT).fill(0);
        this._tick = 0;
        this._shownKey = null;
        this._dismissedTs = null;
        this._noticeTs = null;
        this._buildPill();
        this._timeoutId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, POLL_MS, () => {
            this._poll();
            return GLib.SOURCE_CONTINUE;
        });
    }

    disable() {
        if (this._timeoutId) {
            GLib.source_remove(this._timeoutId);
            this._timeoutId = null;
        }
        this._pill?.destroy();
        this._pill = null;
        this._bars = null;
    }

    _buildPill() {
        this._pill = new St.BoxLayout({
            style: 'background-color: rgba(10,10,10,0.94); border-radius: 999px; padding: 6px 8px; spacing: 10px;',
            reactive: true,
            visible: false,
        });
        this._pill.connect('button-release-event', () => {
            if (this._noticeTs !== null) {
                this._dismissedTs = this._noticeTs;
                this._hide();
            }
            return Clutter.EVENT_PROPAGATE;
        });

        this._cancelButton = new St.Button({
            style: 'background-color: rgba(120,120,120,0.6); border-radius: 999px; width: 26px; height: 26px;',
            child: new St.Icon({icon_name: 'window-close-symbolic', icon_size: 14, style: 'color: white;'}),
            reactive: true,
            can_focus: false,
        });
        this._cancelButton.connect('clicked', () => this._send('cancel'));

        this._barBox = new St.BoxLayout({style: 'spacing: 3px;', y_align: Clutter.ActorAlign.CENTER});
        this._bars = [];
        for (let i = 0; i < BAR_COUNT; i++) {
            const bar = new St.Widget({style: this._barStyle(BAR_MIN, 0.9), y_align: Clutter.ActorAlign.CENTER});
            this._bars.push(bar);
            this._barBox.add_child(bar);
        }

        this._confirmButton = new St.Button({
            style: 'background-color: white; border-radius: 999px; width: 26px; height: 26px;',
            child: new St.Icon({icon_name: 'object-select-symbolic', icon_size: 14, style: 'color: black;'}),
            reactive: true,
            can_focus: false,
        });
        this._confirmButton.connect('clicked', () => this._send('toggle'));

        // Status text with a highlight band sweeping across it (clipped to the label area).
        this._noticeIcon = new St.Icon({icon_size: 16, y_align: Clutter.ActorAlign.CENTER, style: 'padding-left: 6px;'});
        this._label = new St.Label({y_align: Clutter.ActorAlign.CENTER, style: LABEL_STYLE});
        this._label.clutter_text.ellipsize = Pango.EllipsizeMode.END;
        this._shimmer = new St.BoxLayout({x_align: Clutter.ActorAlign.START});
        const half = `width: ${SHIMMER_HALF_WIDTH}px; background-gradient-direction: horizontal;`;
        this._shimmer.add_child(new St.Widget({
            style: `${half} background-gradient-start: rgba(255,255,255,0); background-gradient-end: rgba(255,255,255,0.2);`,
        }));
        this._shimmer.add_child(new St.Widget({
            style: `${half} background-gradient-start: rgba(255,255,255,0.2); background-gradient-end: rgba(255,255,255,0);`,
        }));
        this._labelBox = new St.Widget({
            layout_manager: new Clutter.BinLayout(),
            clip_to_allocation: true,
            style: 'min-height: 26px;',
        });
        this._labelBox.add_child(this._label);
        this._labelBox.add_child(this._shimmer);

        this._pill.add_child(this._cancelButton);
        this._pill.add_child(this._barBox);
        this._pill.add_child(this._noticeIcon);
        this._pill.add_child(this._labelBox);
        this._pill.add_child(this._confirmButton);
        Main.layoutManager.addTopChrome(this._pill);
    }

    _barStyle(height, opacity) {
        return `background-color: rgba(255,255,255,${opacity}); width: 3px; height: ${height}px; border-radius: 2px;`;
    }

    _readState() {
        try {
            const [ok, contents] = STATE_FILE.load_contents(null);
            if (!ok)
                return {state: 'idle', level: 0};
            const data = JSON.parse(new TextDecoder().decode(contents));
            const age = GLib.get_real_time() / 1e6 - (data.ts ?? 0);
            if (data.state === 'recording' && age > STALE_RECORDING_SECONDS)
                return {state: 'idle', level: 0}; // daemon died mid-recording
            if (data.state in NOTICE_ICONS && (age > NOTICE_SECONDS || data.ts === this._dismissedTs))
                return {state: 'idle', level: 0};
            return data;
        } catch (_e) {
            return {state: 'idle', level: 0};
        }
    }

    _hide() {
        this._pill.hide();
        this._levels.fill(0);
        this._shownKey = null;
    }

    _poll() {
        const {state, level, message, ts} = this._readState();
        this._tick++;

        if (state === 'idle') {
            if (this._pill.visible)
                this._hide();
            return;
        }

        const recording = state === 'recording';
        const notice = state in NOTICE_ICONS;
        this._noticeTs = notice ? ts : null;

        const key = `${state}|${message ?? ''}`;
        if (key !== this._shownKey) {
            this._shownKey = key;
            this._cancelButton.visible = recording;
            this._barBox.visible = recording;
            this._confirmButton.visible = recording;
            this._labelBox.visible = !recording;
            this._shimmer.visible = !recording && !notice;
            this._noticeIcon.visible = notice;
            if (notice) {
                this._noticeIcon.icon_name = NOTICE_ICONS[state];
                this._noticeIcon.style = `padding-left: 6px; color: ${NOTICE_COLORS[state]};`;
            }
            this._label.text = message || 'Processing…';
            this._label.style = `${LABEL_STYLE} color: ${notice ? NOTICE_COLORS[state] : 'rgba(255,255,255,0.85)'};`;
            this._pill.show();
            this._place();
        }

        if (recording) {
            this._levels.shift();
            this._levels.push(level ?? 0);
            for (let i = 0; i < BAR_COUNT; i++) {
                const height = Math.round(BAR_MIN + this._levels[i] * (BAR_MAX - BAR_MIN));
                this._bars[i].set_style(this._barStyle(height, 0.9));
            }
        } else if (!notice) {
            const band = 2 * SHIMMER_HALF_WIDTH;
            const travel = this._labelBox.width + band;
            this._shimmer.translation_x = ((this._tick * SHIMMER_STEP) % travel) - band;
        }
    }

    _place() {
        const monitor = Main.layoutManager.primaryMonitor;
        if (!monitor)
            return;
        const [, width] = this._pill.get_preferred_width(-1);
        const [, height] = this._pill.get_preferred_height(width);
        this._pill.set_position(
            monitor.x + Math.floor((monitor.width - width) / 2),
            monitor.y + monitor.height - height - 48);
    }

    _send(command) {
        try {
            const client = new Gio.SocketClient();
            client.connect_async(new Gio.UnixSocketAddress({path: SOCKET_PATH}), null, (c, res) => {
                try {
                    const conn = c.connect_finish(res);
                    conn.get_output_stream().write_all(new TextEncoder().encode(command), null);
                    conn.close(null);
                } catch (e) {
                    console.error(`voice-to-text overlay: send failed: ${e.message}`);
                }
            });
        } catch (e) {
            console.error(`voice-to-text overlay: ${e.message}`);
        }
    }
}
