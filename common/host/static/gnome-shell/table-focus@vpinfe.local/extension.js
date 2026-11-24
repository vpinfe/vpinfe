import Gio from 'gi://Gio';
import Meta from 'gi://Meta';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

// Asked over the session bus by common/host/front_gnome.py.
const INTERFACE = `
<node>
  <interface name="org.vpinfe.Focus">
    <method name="List">
      <arg type="s" direction="out" name="windows"/>
    </method>
    <method name="Activate">
      <arg type="u" direction="in" name="id"/>
      <arg type="b" direction="out" name="ok"/>
    </method>
  </interface>
</node>`;

// Desktop, panels and pop-ups are never a program's window.
const KINDS = [Meta.WindowType.NORMAL, Meta.WindowType.DIALOG,
               Meta.WindowType.MODAL_DIALOG, Meta.WindowType.UTILITY];

export default class TableFocus extends Extension {
    enable() {
        this._exported = Gio.DBusExportedObject.wrapJSObject(INTERFACE, this);
        this._exported.export(Gio.DBus.session, '/org/vpinfe/Focus');
        this._owner = Gio.bus_own_name_on_connection(
            Gio.DBus.session, 'org.vpinfe.Focus', Gio.BusNameOwnerFlags.NONE, null, null);
    }

    disable() {
        if (this._owner) {
            Gio.bus_unown_name(this._owner);
            this._owner = null;
        }
        if (this._exported) {
            this._exported.unexport();
            this._exported = null;
        }
    }

    _windows() {
        return global.get_window_actors().map(actor => actor.meta_window)
            .filter(w => KINDS.includes(w.get_window_type()));
    }

    List() {
        const front = global.display.focus_window;
        return JSON.stringify({
            active: front ? String(front.get_id()) : '',
            windows: this._windows().map(w => ({
                id: String(w.get_id()), pid: w.get_pid(), title: w.get_title() ?? '',
            })),
        });
    }

    Activate(id) {
        const target = this._windows().find(w => w.get_id() === id);
        if (!target)
            return false;
        target.activate(global.get_current_time());
        return true;
    }
}
