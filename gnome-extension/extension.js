// On-screen pill for the voice-to-text daemon: cancel (X), live level bars, confirm (check),
// and a pulsing animation while Whisper is transcribing.
// It polls the daemon's state file and talks back over the daemon's unix socket.

import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const POLL_MS = 50;
const BAR_COUNT = 14;
const BAR_MIN = 3;
const BAR_MAX = 20;
const STALE_RECORDING_SECONDS = 1.5; // the daemon writes ~20x/s while recording

const RUNTIME_DIR = GLib.getenv('XDG_RUNTIME_DIR') ?? `/tmp/voice-to-text-${GLib.getenv('UID') ?? ''}`;
const STATE_FILE = Gio.File.new_for_path(`${RUNTIME_DIR}/voice-to-text.state`);
const SOCKET_PATH = `${RUNTIME_DIR}/voice-to-text.sock`;

export default class VoiceToTextOverlay extends Extension {
    enable() {
        this._levels = new Array(BAR_COUNT).fill(0);
        this._state = 'idle';
        this._tick = 0;
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

        this._cancelButton = new St.Button({
            style: 'background-color: rgba(120,120,120,0.6); border-radius: 999px; width: 26px; height: 26px;',
            child: new St.Icon({icon_name: 'window-close-symbolic', icon_size: 14, style: 'color: white;'}),
            reactive: true,
            can_focus: false,
        });
        this._cancelButton.connect('clicked', () => this._send('cancel'));

        const barBox = new St.BoxLayout({style: 'spacing: 3px;', y_align: 2 /* Clutter.ActorAlign.CENTER */});
        this._bars = [];
        for (let i = 0; i < BAR_COUNT; i++) {
            const bar = new St.Widget({style: this._barStyle(BAR_MIN, 0.9), y_align: 2});
            this._bars.push(bar);
            barBox.add_child(bar);
        }

        this._confirmButton = new St.Button({
            style: 'background-color: white; border-radius: 999px; width: 26px; height: 26px;',
            child: new St.Icon({icon_name: 'object-select-symbolic', icon_size: 14, style: 'color: black;'}),
            reactive: true,
            can_focus: false,
        });
        this._confirmButton.connect('clicked', () => this._send('toggle'));

        this._pill.add_child(this._cancelButton);
        this._pill.add_child(barBox);
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
            return data;
        } catch (_e) {
            return {state: 'idle', level: 0};
        }
    }

    _poll() {
        const {state, level} = this._readState();
        this._state = state;
        this._tick++;

        if (state === 'idle') {
            if (this._pill.visible)
                this._pill.hide();
            this._levels.fill(0);
            return;
        }

        const processing = state === 'processing';
        this._cancelButton.visible = !processing;
        this._confirmButton.visible = !processing;

        if (processing) {
            // Travelling wave so it is obvious something is still working.
            for (let i = 0; i < BAR_COUNT; i++)
                this._levels[i] = 0.25 + 0.2 * Math.sin(this._tick / 3 - i / 1.5);
        } else {
            this._levels.shift();
            this._levels.push(level ?? 0);
        }

        for (let i = 0; i < BAR_COUNT; i++) {
            const height = Math.round(BAR_MIN + this._levels[i] * (BAR_MAX - BAR_MIN));
            this._bars[i].set_style(this._barStyle(height, processing ? 0.6 : 0.9));
        }

        if (!this._pill.visible) {
            this._pill.show();
            this._place();
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
