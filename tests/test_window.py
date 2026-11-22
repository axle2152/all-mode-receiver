#!/usr/bin/env python3
"""
test_window.py - does the tuner window build and run, on AM and on FM?

    python3 tests/test_window.py                   # needs a screen
    xvfb-run -a python3 tests/test_window.py       # Linux without a screen
    python3 tests/test_window.py --fonts /usr/share/fonts/truetype/dejavu

Opens the real tuner window against the GNU Radio stand-in (tests/standin), with a pretend
dongle sending a steady signal, for 12 seconds on each band, and fails on any error, crash or
'Dongle lost'. On FM it also clicks Static cut and Noise reduction in Settings and checks each
one changes only its own setting. On AM and FM it presses the Monitor, Advanced, Direct and
S units keys, changes Settings > Size (the panels must be rebuilt at the new scale, and the
choice saved) and checks the scale 'fit' gives 1080p, 4K and 720p screens. A third run is AM
with a pretend add-on installed: its
station data must reach the dot matrix and the Advanced window, an alert must blink, and key I
must open its window. A fourth is FM with a pretend stereo add-on (a station with no pilot
that uses the add-on's own stereo system): STEREO must light, the dot matrix must name the
system, and the Advanced window must show its lamp, meter and line. --fonts limits the window to the fonts in one folder (for example only
Ubuntu's basic DejaVu set, or an empty folder for none at all).

The window runs from a copy of the program in a temporary folder, so it can't touch your
own settings or presets.
"""
import argparse
import glob
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SECS = 12


def child(appdir, band, fonts, secs):
    sys.path[:0] = [os.path.join(HERE, 'standin'), appdir]
    import tkinter as tk
    from gnuradio._sim import SIM
    from radio.ui import app as A, fonts as F
    SIM.reset()
    addon = band == 'AM+addon'
    fm_addon = band == 'FM+addon'
    if addon:
        install_pretend_addon()
        band = 'AM'
    if fm_addon:
        install_pretend_addon(PretendStereo)
        band = 'FM'
    if not (addon or fm_addon):                   # the window alone, whatever is installed
        install_pretend_addon(None)
    if fonts is not None:
        F._FONT_DIRS = [fonts]
    log = []
    real_print = print

    def tee(*a, **k):
        log.append(' '.join(str(x) for x in a))
        real_print(*a, **k)
    A.print = tee
    root = tk.Tk()
    done, t_start = {}, time.monotonic()

    def on_error(exc, val, tb):                   # an error in any callback fails the run
        problems.append(f'error in a callback: {exc.__name__}: {val}')
        real_print('INFO error:', ''.join(traceback.format_exception(exc, val, tb)).strip().splitlines()[-1])
    root.report_callback_exception = on_error
    app = A.TunerApp(root, scale=0.6, freq=1080e3 if band == 'AM' else 90.5e6)
    states = []
    problems = []

    def find(w, text):
        for c in w.winfo_children():
            try:
                if c.cget('text') == text:
                    return c
            except tk.TclError:
                pass
            r = find(c, text)
            if r is not None:
                return r

    def settings_clicks():
        """Static cut and Noise reduction each change only their own setting."""
        app._toggle_settings()
        root.update()
        sc, nr = find(root, 'Static cut'), find(root, 'Noise reduction')
        if sc is None or nr is None:
            problems.append('Settings: Static cut or Noise reduction checkbox not found')
            return
        for widget, name in ((sc, 'dip_hicut'), (nr, 'noise_red')):
            for _ in range(2):
                before = (app.dip_hicut, app.noise_red)
                widget.invoke()
                after = (app.dip_hicut, app.noise_red)
                mine = 0 if name == 'dip_hicut' else 1
                if not (isinstance(after[mine], bool) and after[mine] == (not before[mine])
                        and after[1 - mine] == before[1 - mine]):
                    problems.append(f'Settings: clicking {name} went {before} -> {after}')
        app._toggle_settings()
        real_print('INFO Settings clicks:', 'ok' if not problems else '; '.join(problems))

    def meter_checks():
        """The signal meter: S switches it to S units (UNCAL until calibrated), CALIBRATE in
        Settings takes the station tuned now as the level typed, a click on the meter switches
        back, and both are saved."""
        import json
        from radio.ui.theme import SIG_X, VFD_BOX
        mine = []
        if app._vfd_state()['meter'] != 'Q':
            mine.append(f"meter starts as {app._vfd_state()['meter']!r}, not signal quality")
        app._meter_toggle()
        st = app._vfd_state()
        if st['meter'] != 'S' or not st['uncal']:
            mine.append(f"after S: meter {st['meter']!r}, UNCAL {st['uncal']}")
        app._toggle_settings()
        root.update()
        btn = find(root, 'CALIBRATE')
        if btn is None:
            mine.append('Settings: no CALIBRATE button')
        else:
            app._cal_var.set('-73')
            app._tuned = lambda tb, now: True        # the stand-in's station, taken as tuned
            btn.invoke()
            msg = (app._cal_msg or ('',))[0]
            if not msg.startswith('Calibrated'):
                mine.append(f'CALIBRATE said {msg!r}')
            dbm = app._smeter_reading(app.tb)[0]
            if abs(dbm + 73) > 0.5:
                mine.append(f'just calibrated to -73 dBm, it reads {dbm:.1f}')
            app._tick()
            if app._cal_label.cget('text') != msg:
                mine.append(f"Settings shows {app._cal_label.cget('text')!r}")
        app._toggle_settings()

        def after_cal():
            from radio.ui import smeter
            st = app._vfd_state()
            want = smeter.segments(app._smeter_reading(app.tb)[0], app.freq)   # the gain may have moved
            if st['uncal'] or abs(st['sig'] - want) > 1:
                mine.append(f"calibrated: UNCAL {st['uncal']}, {st['sig']} bars for {want:.1f}")

            class Click:
                x, y = (VFD_BOX[0] + SIG_X + 30) * app.s, (VFD_BOX[1] + 50) * app.s
            app._vfd_click(Click())
            if app._vfd_state()['meter'] != 'Q':
                mine.append('a click on the meter did not switch it back')
            app._meter_toggle()
            with open(os.path.join(appdir, 'quartz_tuner_settings.json')) as f:
                saved = json.load(f)
            if saved.get('meter_view', {}).get(band) != 'S' or app._input_path() not in saved.get('smeter_cal', {}):
                mine.append(f"saved: {saved.get('meter_view')!r} {saved.get('smeter_cal')!r}")
            problems.extend(mine)
            real_print('INFO signal meter:', 'ok' if not mine else '; '.join(mine))
        root.after(800, after_cal)

    def sample():
        states.append(app._status)
        root.after(250, sample)

    def finish():
        if 'keys' in done and not done['keys'] and time.monotonic() - t_start < secs + 20:
            root.after(500, finish)                     # the key checks are still going
            return
        path = getattr(F.font('vfd', 20), 'path', None)
        real_print('INFO band', app.band, '| display font:',
                   path if isinstance(path, str) else "Pillow's built-in font")
        seen = [s for i, s in enumerate(states) if i == 0 or s != states[i - 1]]
        real_print('INFO status:', ' > '.join(seen))
        bad = [m for m in log + seen if any(w in m for w in ('Dongle lost', 'Receiver stopped', 'Error'))]
        bad += problems
        real_print('RESULT', 'FAIL' if bad or app.band != band or 'Ready' not in seen else 'PASS')
        app._on_close()
    def addon_checks():
        """The pretend add-on's station data on the dot matrix and in Advanced; alert; key I."""
        now = time.monotonic()
        call, ps, rt = app._rds_fields(app.tb, now)
        if (call.strip(), ps.strip()) != ('WAAA', 'TESTING') or not rt.startswith('Testing one'):
            problems.append(f'add-on: dot matrix shows {(call, ps, rt)!r}')
        app._toggle_advanced()
        root.update()
        root.after(1500, addon_checks_2)

    def addon_checks_2():
        if app._ps_var.get() != 'TESTING' or 'Pretend add-on: decoding' not in app._rdsd_var.get():
            problems.append(f'add-on: Advanced shows {app._ps_var.get()!r} / {app._rdsd_var.get()!r}')
        app.tb.station['alert'] = 2
        t0 = float(int(time.monotonic()) + 1)          # a whole second: the first half shows ALERT
        shown = {app._rds_fields(app.tb, t)[1].strip() for t in (t0, t0 + 0.5)}
        if shown != {'ALERT', ''}:
            problems.append(f'add-on: alert blinks {shown!r}')
        app.tb.station['alert'] = 0
        app._addon_window()
        w = PretendAddon.windows
        if len(w) != 1 or w[0].toggles != 1 or w[0].current() is not app.tb.addons[0].obj:
            problems.append('add-on: key I did not open its window')
        app._toggle_advanced()
        real_print('INFO add-on station data:', 'ok' if not problems else '; '.join(problems))

    def key_checks():
        """The keys that were hints (Monitor, Advanced, Direct, S units), and Settings > Size:
        the panels rebuilt in place at the new scale, the choice saved, and 'fit' right for
        1080p, 4K and 720p screens."""
        import json
        from radio.ui.theme import BASE_W
        mine = []
        done['keys'] = False

        def key_for(fn):
            return next((k for k in app.keys if k.on_press == fn), None)

        from radio.ui import bandplan as BP
        from radio.ui.app import MENU_GROUPS

        class OnTheUnit:
            """A band (or mode) picked on the Bands & Modes unit, as a person would: its group key,
            then its soft key, paging with MORE> (or the group key again) until it's there."""
            def __init__(self, ident, group):
                self.ident, self.group = ident, group

            @property
            def _down(self):
                return self.ident in (app.bandkey, app.mode)

        def key_for_band(kid):
            return OnTheUnit(kid, BP.band(kid).group)

        def key_on(canvas, legend):
            return next((k for k in app.keys if k.canvas is canvas and k.legend == legend), None)

        def soft_keys():
            return [k for k in app.keys if k.canvas is app.bcanvas and k.legend == '' and k.box[1] == 146]

        def pick(ident, group):
            gk = key_on(app.bcanvas, dict(MENU_GROUPS)[group])
            if app.menu_group != group:
                press(gk)
            for _ in range(3):
                items = app._menu_items()
                i = next((i for i, it in enumerate(items) if it[2] == ident), None)
                if i is not None:
                    press(soft_keys()[i])
                    return True
                press(soft_keys()[len(items) - 1])     # MORE>
            return False

        def pick_mode(m):
            return pick(m, 'mode')

        def press(k):
            if isinstance(k, OnTheUnit):
                if not pick(k.ident, k.group):
                    mine.append(f'{k.ident}: not found on the {k.group} soft keys')
                return
            k._press(None)
            k._release(None)
            root.update()
        km, ka, kd, ks = (key_for(f) for f in (app._toggle_monitor, app._toggle_advanced,
                                                app._direct_toggle, app._meter_toggle))
        if None in (km, ka, kd, ks):
            mine.append('a key is missing: Monitor, Advanced, Direct or S units')
        else:
            on = app.monitor_on
            for _ in range(2):                          # off and on again: the key follows both ways
                press(km)
                if km._down != app.monitor_on:
                    mine.append(f'Monitor key: monitor {app.monitor_on}, key down {km._down}')
            if app.monitor_on != on:
                mine.append('Monitor key: two presses did not bring the monitor back')
            press(ka)
            if not app._adv_visible or not ka._down:
                mine.append('Advanced key did not open the window, or did not stay down')
            press(ka)
            if app._adv_visible or ka._down:
                mine.append('Advanced key did not close the window, or stayed down')
            press(kd)
            if app._direct is None or not kd._down:
                mine.append('Direct key did not start frequency entry, or did not stay down')
            app._direct_cancel()
            app._refresh_keys()
            if kd._down:
                mine.append('Direct key stayed down after the entry was cancelled')
            view = app.meter_view.get(app.band, 'Q')
            for _ in range(2):
                press(ks)
                now_s = app.meter_view.get(app.band, 'Q') == 'S'
                if ks._down != now_s:
                    mine.append(f'S units key: meter {app.meter_view.get(app.band)!r}, key down {ks._down}')
            if app.meter_view.get(app.band, 'Q') != view:
                mine.append('S units key: two presses did not bring the meter back')
        n, s0 = len(app.keys), app.s
        for size in ('1.25', '0.75', 'fit'):
            app._size_var.set(size)
            app._size_changed()
            root.update()
            want = app._scale_for('fit' if size == 'fit' else float(size))
            back = app._load_settings().get('ui_size')
            if back != ('fit' if size == 'fit' else float(size)):
                mine.append(f'Size {size}: read back from the settings file as {back!r}')
            if abs(app.s - want) > 1e-9 or abs(root.winfo_width() - round(BASE_W * want)) > 2 or len(app.keys) != n:
                mine.append(f'Size {size}: scale {app.s:.3f} (want {want:.3f}), width {root.winfo_width()}, '
                            f'{len(app.keys)} keys (had {n})')
        if abs(app.s - s0) < 1e-9:
            mine.append('Size never changed the scale')
        with open(os.path.join(appdir, 'quartz_tuner_settings.json')) as f:
            if json.load(f).get('ui_size') != 'fit':
                mine.append('Size: the choice was not saved')
        sw, sh = root.winfo_screenwidth, root.winfo_screenheight
        for (w, h), want in (((1920, 1080), 1.168), ((3840, 2160), 2.335), ((1280, 720), 0.778)):
            root.winfo_screenwidth, root.winfo_screenheight = (lambda w=w: w), (lambda h=h: h)
            got = app._scale_for('fit')
            if abs(got - want) > 0.002:
                mine.append(f'fit on {w}x{h}: {got:.3f}, want {want}')
        root.winfo_screenwidth, root.winfo_screenheight = sw, sh
        # the Bands panel: a band key tunes there in its usual receiver, remembers where it was
        # left; Direct finds the band a frequency is in; a band whose receiver isn't built says so
        kbnd = key_for(app._toggle_bands)
        if kbnd is None:
            mine.append('no Bands key')
        else:
            for _ in range(2):                          # hide the Bands panel, then show it again
                press(kbnd)
                if kbnd._down != app.bands_on or bool(app.bcanvas.winfo_ismapped()) != app.bands_on:
                    mine.append(f'Bands key: shown {app.bands_on}, mapped {app.bcanvas.winfo_ismapped()}, '
                                f'key down {kbnd._down}')
        kb = {kid: key_for_band(kid) for kid in ('SW41', 'FM', 'MW', 'HAM2', 'AIR', 'CB')}
        if len(soft_keys()) != 10 or None in [key_on(app.bcanvas, g) for _, g in MENU_GROUPS]:
            mine.append(f'Bands & Modes keys missing: {len(soft_keys())} soft keys')
        else:
            press(kb['SW41'])
            if (app.bandkey, app.band) != ('SW41', 'AM') or not 7.2e6 <= app.freq <= 7.45e6 or not kb['SW41']._down:
                mine.append(f'41 m key: band {app.bandkey} on {app.band} at {app.freq / 1e6:g} MHz')
            app._step(+1, manual=True)
            if abs(app.freq - 7.205e6) > 1:
                mine.append(f'a step on 41 m went to {app.freq / 1e3:g} kHz, not 7205')
            press(kb['FM'])
            if (app.bandkey, app.band) != ('FM', 'FM'):
                mine.append(f'FM key: band {app.bandkey} on {app.band}')
            press(kb['SW41'])
            if abs(app.freq - 7.205e6) > 1:
                mine.append(f'41 m did not remember 7205 kHz: {app.freq / 1e3:g}')
            press(kb['HAM2'])                           # narrow FM, on its own receiver
            if (app.bandkey, app.band, app.mode) != ('HAM2', 'NFM', 'NFM') or not 144e6 <= app.freq <= 148e6:
                mine.append(f'2 m key: band {app.bandkey} on {app.band} in {app.mode} at {app.freq / 1e6:g} MHz')
            press(kb['SW41'])
            press(kb['AIR'])                            # above 28.8 MHz: the dongle's tuner
            msg = (app._dm_msg or ('',))[0]
            if (app.bandkey, app.band, msg) != ('AIR', 'AM', 'QUADRATURE TUNER'):
                mine.append(f'41 m to Air: band {app.bandkey} on {app.band}, message {msg!r}')
            app._direct_start()
            app._direct = '9580'
            app._direct_enter()                         # and back below: direct sampling
            msg = (app._dm_msg or ('',))[0]
            if (app.bandkey, app.band, round(app.freq), msg) != ('SW31', 'AM', 9580000, 'DIRECT SAMPLING'):
                mine.append(f'typed 9580: band {app.bandkey} on {app.band} at {app.freq / 1e3:g} kHz, message {msg!r}')
            # a channelized band: channel to channel, CH shows the channel, Memory sets home
            ch = lambda: ''.join(app._vfd_state()['ch']).strip()
            press(kb['CB'])
            if (app.bandkey, round(app.freq), ch()) != ('CB', 27185000, '19'):
                mine.append(f'CB key: band {app.bandkey} at {app.freq / 1e6:g} MHz, CH {ch()!r} (want 19)')
            app._pick(22)
            got = []
            for _ in range(2):
                app._step(+1, manual=True)
                got.append(ch())
            if got != ['23', '24'] or round(app.freq) != 27235000:
                mine.append(f'CB steps from 22: CH {got}, at {app.freq / 1e6:g} MHz (want 23 on 27.255, 24 on 27.235)')
            app._digit(4)
            app._digit(0)
            if (round(app.freq), ch()) != (27405000, '40'):
                mine.append(f'4 then 0 on CB: {app.freq / 1e6:g} MHz, CH {ch()!r}')
            app._direct = '27.150'                      # typed between channels: tuned as typed
            app._direct_enter()
            if (app.bandkey, round(app.freq), ch()) != ('CB', 27150000, ''):
                mine.append(f'typed 27.150 on CB: {app.bandkey} at {app.freq / 1e6:g} MHz, CH {ch()!r} '
                            '(want 27.150, no channel)')
            app._step(+1, manual=True)
            if (round(app.freq), ch()) != (27155000, '16'):
                mine.append(f'a step up from 27.150: {app.freq / 1e6:g} MHz, CH {ch()!r} (want 16 on 27.155)')
            app._direct = '1083'                        # and on MW, off the 10 kHz steps
            app._direct_enter()
            if (app.bandkey, round(app.freq)) != ('MW', 1083000):
                mine.append(f'typed 1083 kHz: {app.bandkey} at {app.freq / 1e3:g} kHz (want MW, 1083)')
            app._direct = '27405'
            app._direct_enter()
            app._memory()
            app._memory()
            if app.home_channels.get('CB') != app.freq or 'HOME CHANNEL 40' not in (app._dm_msg or ('',))[0]:
                mine.append(f'Memory twice on CB 40: home {app.home_channels.get("CB")}, message {app._dm_msg!r}')
            app._pick(5)
            press(kb['FM'])
            press(kb['CB'])
            if ch() != '40':
                mine.append(f'back to CB with a home channel: CH {ch()!r} (want 40)')
            app.band_key_goes = 'last'
            app._pick(5)
            press(kb['FM'])
            press(kb['CB'])
            if ch() != '5':
                mine.append(f'back to CB, "where I left it": CH {ch()!r} (want 5)')
            app.channel_steps = False
            app._pick(3) if app._channels_on() else app._tune_to(26.985e6)
            app._step(+1, manual=True)
            if round(app.freq) != 26995000 or ch():
                mine.append(f'ordinary steps on CB from 26.985: {app.freq / 1e6:g} MHz, CH {ch()!r}')
            app.channel_steps, app.band_key_goes = True, 'home'
            # presets are by band: the same number holds a different station on each
            press(kb['FM'])
            app._tune_to(90.9e6)
            app._memory()
            app._channel(3)
            press(kb['MW'])
            app._tune_to(1080e3)
            app._memory()
            app._channel(3)
            press(kb['FM'])
            app._tune_to(98.1e6)
            app._channel(3)
            fm3 = app.freq
            press(kb['MW'])
            app._tune_to(700e3)
            app._channel(3)
            if (round(fm3), round(app.freq)) != (90900000, 1080000):
                mine.append(f'preset 3: FM {fm3 / 1e6:g} MHz (want 90.9), MW {app.freq / 1e3:g} kHz (want 1080)')
            with open(os.path.join(appdir, 'quartz_tuner_settings.json')) as f:
                saved = json.load(f).get('presets', {})
            if saved.get('FM', [None] * 3)[2] != 'FM 90.9' or saved.get('MW', [None] * 3)[2] != 'AM 1080':
                mine.append(f"presets saved as FM {saved.get('FM')!r}, MW {saved.get('MW')!r}")
            old = [{'band': 'FM', 'freq': 90.5e6}, {'band': 'AM', 'freq': 1080e3}, {'band': 'AM', 'freq': 9580e3}]
            got = app._presets_from(old + [None] * 21)
            if [got.get(key, [None] * 3)[i] for key, i in (('FM', 0), ('MW', 1), ('SW31', 2))] != old:
                mine.append(f'an older single list went to {sorted(got)}')
            # modes: a mode key switches receiver on the same frequency; a kind not built says so
            press(kb['FM'])
            f0 = app.freq
            pick_mode('AM')
            if (app.mode, app.band, round(app.freq)) != ('AM', 'AM', round(f0)):
                mine.append(f'AM mode on FM: mode {app.mode} on {app.band} at {app.freq / 1e6:g} MHz')
            app._memory()
            app._channel(7)                             # a preset remembers its mode
            for want in ('SAM_U', 'SAM_L', 'USB', 'LSB', 'CW'):
                pick_mode(want)                         # sync AM, SSB and CW: the HF receiver
                if (app.mode, app.band, round(app.freq)) != (want, 'HF', round(f0)):
                    mine.append(f'{want} soft key: mode {app.mode} on {app.band} at {app.freq / 1e6:g} MHz')
            pick_mode('FM')
            if (app.mode, app.band) != ('FM', 'FM'):
                mine.append(f'FM key: mode {app.mode} on {app.band}')
            app._channel(7)
            if (app.mode, app.band, round(app.freq)) != ('AM', 'AM', round(f0)):
                mine.append(f'preset 7 (stored in AM on FM): mode {app.mode} on {app.band}')
            # the text: the band on the tuner; the mode and filter on the unit, or with it hidden,
            # on the tuner's second row
            press(kb['MW'])
            app._dm_msg = None                          # the DIRECT SAMPLING message takes the row a while
            st, rows = app._vfd_state(), app._menu_state()['rows']
            if st['rds'][2] != 'MEDIUM WAVE' or st['line2'] or 'AM' not in st['legends'] \
                    or not rows[0].startswith('MEDIUM WAVE') or 'FILTER' not in rows[0] \
                    or not rows[2].startswith('FILTER') or not rows[1].startswith('BROADCAST') \
                    or '530-1710 kHz' not in rows[1]:
                mine.append(f"MW text {st['rds']!r} / {st['line2']!r}, unit {rows[:3]!r}")
            app._toggle_bands()
            if not app._vfd_state()['line2'].startswith('AM  FILTER'):
                mine.append(f"unit hidden: tuner's second row {app._vfd_state()['line2']!r}")
            app._toggle_bands()
            # fine tune: off the raster; the next step comes back onto it
            app._tune_to(1080e3)
            app._fine(+1)
            fine = app.freq
            app._step(+1, manual=True)
            if (round(fine), round(app.freq)) != (1080100, 1090000):
                mine.append(f'fine tune from 1080 kHz: {fine:.0f} Hz, then a step: {app.freq:.0f} Hz')
            # a part of a band with its own mode: 10 m's AM part; below it, USB
            app._direct_start()
            app._direct = '29100'
            app._direct_enter()
            app._tune_to(28.999e6)
            if (app.bandkey, app.mode, app.band) != ('HAM10', 'USB', 'HF'):
                mine.append(f'10 m below its AM part: band {app.bandkey}, mode {app.mode} on {app.band} (want USB)')
            press(kb['MW' if band == 'AM' else 'FM'])     # back where this run started
        problems.extend(mine)
        real_print('INFO keys and size:', 'ok' if not mine else '; '.join(mine))
        if None in kb.values():
            done['keys'] = True
        else:
            root.after(100, lambda: nfm_1())       # defined below

        # ---- narrow FM: 2 m on its own receiver, Squelch, Rev, Tone, a repeater preset
        nfm = []
        from radio.ui.theme import FILTER_DN, FILTER_UP, SQL_DN, SQL_UP, TONE_KEY
        line2 = lambda: app._menu_state()['rows'][0]   # the unit's status: band, mode, filter, offset, tone
        filt = lambda: app._menu_state()['rows'][2]    # the filters on offer, and narrow FM's squelch
        key_at = lambda box: next((k for k in app.keys if k.box == box), None)
        msg = lambda: (app._dm_msg or ('',))[0]

        def nfm_1():
            press(kb['HAM2'])
            app._tune_to(146.52e6)
            app._rev_key()                              # a simplex channel has no input
            if app.rev_from is not None or 'SIMPLEX' not in msg():
                nfm.append(f'Rev on 146.52 simplex: input {app.rev_from}, message {msg()!r}')
            app._direct_start()
            app._direct = '146.94'
            app._direct_enter()
            if round(app.freq) != 146940000 or '-600 kHz' not in line2():
                nfm.append(f'146.94: {app.freq / 1e6:g} MHz, second row {line2()!r} (want the -600 kHz offset)')
            app._rev_key()
            krev = next((k for k in app.keys if k.on_press == app._rev_key), None)
            root.update()
            if round(app.freq) != 146340000 or app.rev_from != 146.94e6 or 'REV -600 kHz' not in line2() \
                    or krev is None or not krev._down:
                nfm.append(f'Rev on 146.94: {app.freq / 1e6:g} MHz (want 146.34), row {line2()!r}, '
                           f'key down {krev._down if krev else None}')
            app._rev_key()
            if round(app.freq) != 146940000 or app.rev_from is not None:
                nfm.append(f'Rev again: {app.freq / 1e6:g} MHz (want 146.94)')
            root.after(3000, nfm_2)                    # the narrow FM receiver starts

        def nfm_2():
            tb = app.tb
            if tb is None or tb.BAND != 'NFM':
                nfm.append(f'2 m: the narrow FM receiver is not running ({type(tb).__name__})')
                return nfm_end()
            if (tb.width, tb.squelch_level) != ('NFM', 3):
                nfm.append(f'narrow FM started with width {tb.width}, squelch {tb.squelch_level} (want NFM, 3)')
            app._tone_key()
            if app.tsq_hz is not None or 'NO TONE' not in msg():
                nfm.append(f'Tone with no tone heard: {app.tsq_hz}, message {msg()!r}')
            app._heard = (app.freq, 100.0)              # as if the repeater's tone had been heard
            info = app._menu_state()['rows'][1]
            if 'PL 100.0' not in line2() or 'REPEATER  146.940 out, 146.340 in' not in info \
                    or 'TONE 100.0 HEARD' not in info:
                nfm.append(f'tone heard: status {line2()!r}, second row {info!r}')
            press(key_at(TONE_KEY))
            st = app._vfd_state()
            if (app.tsq_hz, tb.tone_squelch) != (100.0, 100.0) or 'TONE' not in st['legends'] \
                    or 'TSQ 100.0' not in line2():
                nfm.append(f'Tone: tone squelch {app.tsq_hz} (receiver {tb.tone_squelch}), legends '
                           f'{sorted(st["legends"])}, status {line2()!r}')
            app._memory()
            app._channel(5)
            c = app._band_presets()[4]
            text = app._channel_text(c)
            back = app._parse_channel(text)
            if text != 'NFM 146.94 OFS-600 PL100.0 TSQ' or back != {k: v for k, v in c.items() if k != 'mode'}:
                nfm.append(f'repeater preset saved as {text!r}, read back as {back!r}')
            app._step(+1, manual=True)
            app._apply_mode()
            if app.tsq_hz is not None or tb.tone_squelch is not None:
                nfm.append(f'tuned away: tone squelch still {app.tsq_hz} / {tb.tone_squelch}')
            app._channel(5)
            app._apply_mode()
            if (round(app.freq), app.tsq_hz, tb.tone_squelch) != (146940000, 100.0, 100.0):
                nfm.append(f'preset 5: {app.freq / 1e6:g} MHz, tone squelch {app.tsq_hz} / {tb.tone_squelch}')
            for _ in range(3):                         # squelch 3 down to off, then up to 1
                press(key_at(SQL_DN))
            if app.squelch_on or tb.squelch_level != 0 or 'SQUELCH OFF' not in filt():
                nfm.append(f'Squelch - to off: on {app.squelch_on}, receiver level {tb.squelch_level}, {filt()!r}')
            press(key_at(SQL_UP))
            if (app.squelch_on, tb.squelch_level) != (True, 1) or 'SQUELCH #-------- 1' not in filt():
                nfm.append(f'Squelch +: on {app.squelch_on}, receiver level {tb.squelch_level}, {filt()!r}')
            app._sql_var.set(5)                         # Settings > Squelch
            app._squelch_level_changed()
            if (app.squelch_on, tb.squelch_level) != (True, 5):
                nfm.append(f'squelch level 5: on {app.squelch_on}, receiver level {tb.squelch_level}')
            app._sql_var.set(3)
            app._squelch_level_changed()
            press(key_at(FILTER_DN))                   # narrow FM 2.5: no restart
            if (app.mode, tb.width) != ('NFM2', 'NFM2') or 'FM NARROW 2.5' not in line2() or '[2.5]' not in filt():
                nfm.append(f'Filter narrower on narrow FM: mode {app.mode}, receiver {tb.width}, {line2()!r}')
            press(key_at(FILTER_UP))
            app._toggle_advanced()
            root.after(1500, nfm_3)

        def nfm_3():
            if 'Narrow FM' not in app._id_var.get() or 'Squelch' not in app._rdsd_var.get():
                nfm.append(f'Advanced on narrow FM: {app._id_var.get()!r} / {app._rdsd_var.get()[:40]!r}')
            app._toggle_advanced()
            nfm_end()

        def nfm_end():
            press(kb['MW' if band == 'AM' else 'FM'])
            press(key_at(SQL_UP))
            if 'NARROW FM ONLY' not in msg():
                nfm.append(f'Squelch key off narrow FM: {msg()!r}')
            problems.extend(nfm)
            real_print('INFO narrow FM keys:', 'ok' if not nfm else '; '.join(nfm))
            hf_1()

        # ---- SSB, CW and sync AM on the HF receiver; the Width key on every mode
        hf = []

        def hf_1():
            from radio.hf import HFReceiver
            from radio.ui import bandplan as BP
            from radio.ui.frequency import as_text
            if {m: HFReceiver.WIDTHS[m] for m in BP.FILTERS} != BP.FILTERS or \
                    {m: HFReceiver.DEFAULT_WIDTH[m] for m in BP.FILTERS} != BP.DEFAULT_FILTER:
                hf.append('the window\'s filter table differs from the HF receiver\'s')
            kb20 = key_for_band('HAM20')
            press(kb20)
            app._tune_to(14.2e6)
            if (app.bandkey, app.band, app.mode) != ('HAM20', 'HF', 'USB'):
                hf.append(f'20 m key: band {app.bandkey} on {app.band} in {app.mode} (want USB on HF)')
            app._step(+1, manual=True)
            app._fine(+1)
            if as_text(app.freq, app._step_hz()) != '14200.11 kHz':
                hf.append(f'a step (100 Hz) and fine tune (10 Hz) from 14200: {as_text(app.freq, app._step_hz())}')
            app._step(+1, manual=True)
            if round(app.freq) != 14200200:
                hf.append(f'a step from 14200.11 kHz: {app.freq:.0f} Hz (want back on 100 Hz: 14200200)')
            root.after(3000, hf_2)                     # the HF receiver starts

        def hf_2():
            tb = app.tb
            if tb is None or tb.BAND != 'HF':
                hf.append(f'20 m: the HF receiver is not running ({type(tb).__name__})')
                return hf_end()
            if (tb.mode, tb.width) != ('USB', 2700):
                hf.append(f'the HF receiver started in {tb.mode} at {tb.width} Hz (want USB, 2700)')
            press(key_at(FILTER_UP))
            if (tb.width, app.hf_widths['USB']) != (3000, 3000) or 'FILTER 3 kHz' not in line2() \
                    or '[3.0]' not in filt():
                hf.append(f'Filter wider on USB: receiver {tb.width}, {line2()!r} / {filt()!r} (want 3 kHz)')
            for _ in range(3):
                press(key_at(FILTER_DN))
            if tb.width != 1800:
                hf.append(f'Filter narrower three times: {tb.width} Hz (want 1800)')
            pick_mode('LSB')
            pick_mode('CW')
            press(key_at(FILTER_UP))
            if app.tb is not tb or (tb.mode, tb.width) != ('CW', 1000) or 'CW' not in app._vfd_state()['legends']:
                hf.append(f'SSB then CW and Width, without a restart: {tb.mode} at {tb.width} Hz, '
                          f'same receiver {app.tb is tb}')
            app._memory()                              # a CW preset keeps its filter
            app._channel(9)
            text = app._channel_text(app._band_presets()[8])
            if not text.endswith('CW W1000') or app._parse_channel(text).get('width') != 1000:
                hf.append(f'CW preset saved as {text!r}')
            app._tune_to(14.05e6)                      # into 20 m's CW part, then out of it
            app._tune_to(14.25e6)
            app._tick()
            if (app.mode, tb.mode, tb.width) != ('USB', 'USB', 1800):
                hf.append(f'back in the phone part: {app.mode} (receiver {tb.mode} at {tb.width} Hz; want USB 1800)')
            pick_mode('AM')                            # AM: the AM receiver; sync AM: here
            pick_mode('SAM_U')
            if (app.mode, app.band) != ('SAM_U', 'HF') or 'FILTER 4.5 kHz' not in line2():
                hf.append(f'AM then sync AM: {app.mode} on {app.band}, row {line2()!r}')
            app._toggle_advanced()
            root.after(2500, hf_3)

        def hf_3():
            if 'Synchronous AM' not in app._id_var.get() or 'Filter' not in app._rdsd_var.get():
                hf.append(f'Advanced on sync AM: {app._id_var.get()!r} / {app._rdsd_var.get()[:40]!r}')
            app._toggle_advanced()
            press(kb['MW'])                            # the filter on AM: 8 kHz, 10.2, Wide (and no further)
            app.am_normal_hz, app.am_wide = 8000, False
            got = []
            for k in (FILTER_UP, FILTER_UP, FILTER_UP, FILTER_DN):
                press(key_at(k))
                got.append('WIDE' if app.am_wide else app.am_normal_hz)
            app._filter_step(+1, wrap=True)             # W on the keyboard steps round
            app._filter_step(+1, wrap=True)
            got.append('WIDE' if app.am_wide else app.am_normal_hz)
            if got != [10200, 'WIDE', 'WIDE', 10200, 2500] or not line2().startswith('MEDIUM WAVE'):
                hf.append(f'the filter on AM from 8 kHz: {got}, {line2()!r}')
            app.am_normal_hz = 8000
            press(kb['FM'])                            # on FM: narrow, auto, wide
            app.if_mode = 'auto'
            got = []
            for k in (FILTER_UP, FILTER_UP, FILTER_DN, FILTER_DN):
                press(key_at(k))
                got.append(app.if_mode)
            if got != ['wide', 'wide', 'auto', 'narrow']:
                hf.append(f'the filter on FM: {got}')
            app.if_mode = 'auto'
            press(kb['HAM2'])                          # on narrow FM: 5 kHz, then 2.5
            press(key_at(FILTER_DN))
            if app.mode != 'NFM2':
                hf.append(f'Filter narrower on narrow FM: {app.mode} (want NFM2)')
            press(key_at(FILTER_UP))
            with open(os.path.join(appdir, 'quartz_tuner_settings.json')) as f:
                saved = json.load(f)
            if saved.get('hf_widths', {}).get('CW') != 1000 or saved.get('hf_widths', {}).get('USB') != 1800:
                hf.append(f"HF filters saved as {saved.get('hf_widths')!r}")
            hf_end()

        def hf_end():
            # the tuner's band keys: FM, AM, LW; SW back to the last shortwave band, again the next
            tk_ = {l: key_on(app.canvas, l) for l in ('FM', 'AM', 'LW', 'SW')}
            got = []
            for l in ('FM', 'AM', 'LW'):
                press(tk_[l])
                got.append((app.bandkey, tk_[l]._down))
            app.last_sw = 'SW41'
            press(tk_['SW'])
            got.append((app.bandkey, tk_['SW']._down))
            press(tk_['SW'])
            rows = app._menu_state()['rows']
            got.append((app.bandkey, app.menu_group))
            if got != [('FM', True), ('MW', True), ('LW', True), ('SW41', True), ('SW31', 'shortwave')] \
                    or '[31]' not in rows[3]:
                hf.append(f'the tuner band keys: {got}, soft labels {rows[3]!r}')
            ka = key_on(app.bcanvas, 'Amateur')        # a long group: its key again shows its next page
            press(ka)
            first = app._menu_items()[0][2]
            press(ka)
            if (first, app._menu_items()[0][2], app._menu_items()[-1][0]) != ('HAM160', 'HAM10', 'MORE>'):
                hf.append(f'Amateur key and again: pages start {first}, {app._menu_items()[0][2]}')
            press(kb['MW' if band == 'AM' else 'FM'])
            problems.extend(hf)
            real_print('INFO SSB, CW, sync AM and Width:', 'ok' if not hf else '; '.join(hf))
            done['keys'] = True

    def stereo_checks():
        """The pretend stereo add-on: STEREO, the system's name, its lamp, meter and line."""
        tb, now = app.tb, time.monotonic()
        if tb.addon_stereo != 'TEST' or not tb.stereo_indicator:
            problems.append(f'stereo add-on: in use {tb.addon_stereo!r}, STEREO {tb.stereo_indicator}')
        if app._rds_fields(tb, now)[2].strip() != 'TEST STEREO':
            problems.append(f'stereo add-on: dot matrix shows {app._rds_fields(tb, now)!r}')
        app._toggle_advanced()
        root.update()
        root.after(1500, stereo_checks_2)

    def stereo_checks_2():
        if 'TEST STEREO' not in app._id_var.get() or 'Pretend stereo: subcarrier' not in app._rdsd_var.get():
            problems.append(f'stereo add-on: Advanced shows {app._id_var.get()!r} / {app._rdsd_var.get()!r}')
        if 'data0' not in (app._meters._keys or []):
            problems.append('stereo add-on: no meter in the Advanced window')
        app._toggle_advanced()
        real_print('INFO stereo add-on:', 'ok' if not problems else '; '.join(problems))

    root.after(250, sample)
    if band == 'FM' and not fm_addon:
        root.after(2000, settings_clicks)
    if not (addon or fm_addon):
        root.after(6000, meter_checks)
        done['keys'] = False
        root.after(7500, key_checks)
    if addon:
        root.after(5000, addon_checks)
    if fm_addon:
        root.after(5000, stereo_checks)
    root.after(int(secs * 1000), finish)
    root.mainloop()


class PretendAddon:
    """Stands in for an installed add-on: steady station data on AM."""
    BANDS = ('AM',)
    NAME = 'TEST'
    windows = []

    def __init__(self, rx):
        from gnuradio import blocks, gr
        rx.connect(rx.addon_tap(), blocks.null_sink(gr.sizeof_gr_complex))

    def update(self, now):
        return {'pi': 0x54A8, 'call': 'WAAA', 'ps': 'TESTING', 'rt': 'Testing one two three',
                'pty': 3, 'synced': True, 'rate': 11.0, 'bler': 2.0, 'tp': False, 'ta': False}

    def readout(self):
        return 'Pretend add-on: decoding'

    def meter(self):
        return ('Test data', 0.8, 'good')

    @classmethod
    def make_window(cls, parent, current):
        class Win:
            toggles = 0

            def toggle(self):
                self.toggles += 1
        w = Win()
        w.current = current
        cls.windows.append(w)
        return w


class PretendStereo:
    """Stands in for an installed FM stereo add-on, on a station with no pilot: its stereo is
    on, at full quality."""
    BANDS = ('FM',)
    NAME = 'TEST'

    def __init__(self, rx):
        from gnuradio import blocks
        self.out = blocks.multiply_const_ff(0.0)
        rx.connect(rx.addon_tap(), self.out)

    def stereo_decoder(self):
        return {'out': (self.out, 0), 'main_gain': 2.0, 'mpx_top': 83e3}

    def update(self, now):
        return {'stereo': True, 'stereo_quality': 1.0, 'synced': True}

    def readout(self):
        return 'Pretend stereo: subcarrier found'

    def meter(self):
        return ('Test subcarrier', 1.0, '50%')


def install_pretend_addon(cls=PretendAddon):
    import radio.addons

    class EntryPoint:
        name = 'pretend'

        def load(self):
            return cls
    radio.addons._entry_points = lambda: [EntryPoint()] if cls is not None else []


def run(band, fonts):
    tmp = tempfile.mkdtemp(prefix='tuner_test_')
    try:
        for f in glob.glob(os.path.join(REPO, '*.py')):
            shutil.copy(f, tmp)
        shutil.copytree(os.path.join(REPO, 'radio'), os.path.join(tmp, 'radio'),
                        ignore=shutil.ignore_patterns('__pycache__'))
        env = dict(os.environ, HOME=tmp, USERPROFILE=tmp)
        cmd = [sys.executable, os.path.abspath(__file__), '--child', band, '--appdir', tmp]
        if fonts is not None:
            cmd += ['--fonts', fonts]
        try:
            p = subprocess.run(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, timeout=SECS + 60)
            out = p.stdout
        except subprocess.TimeoutExpired as e:
            out = (e.stdout or '') + '\nRESULT FAIL (timed out)'
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    ok = 'RESULT PASS' in out
    print(f"{'ok  ' if ok else 'FAIL'} {band}")
    for line in out.splitlines():
        if line.startswith('INFO '):
            print('       ' + line[5:])
        elif not ok and not line.startswith('RESULT'):
            print('     | ' + line)
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument('--fonts', default=None, help='use only the fonts in this folder')
    ap.add_argument('--child', choices=('AM', 'FM', 'AM+addon', 'FM+addon'), help=argparse.SUPPRESS)
    ap.add_argument('--appdir', help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a.child:
        child(a.appdir, a.child, a.fonts, SECS)
        return 0
    results = [run(band, a.fonts) for band in ('AM', 'FM', 'AM+addon', 'FM+addon')]
    print('\nAll passed.' if all(results) else f'\n{results.count(False)} failed.')
    return 0 if all(results) else 1


if __name__ == '__main__':
    sys.exit(main())
