#!/usr/bin/env python3

import gi
gi.require_version("Gst", "1.0")
gi.require_version("GObject", "2.0")
from gi.repository import Gst, GObject, GLib

import sys
import argparse
import signal

try:
    from pynput import keyboard
except ImportError:
    keyboard = None

Gst.init(None)

class PiPCompositor:
    def __init__(
        self,
        feed1_port=5600,
        feed2_port=5601,
        main_width=1440,
        main_height=1080,
        pip_width=360,
        pip_height=270,
        pip_x=50,
        pip_y=50,
        initial_mode=1,
        listen_keys=False
    ):
        self.feed1_port = feed1_port
        self.feed2_port = feed2_port
        self.main_width = main_width
        self.main_height = main_height
        self.pip_width = pip_width
        self.pip_height = pip_height
        self.pip_x = pip_x
        self.pip_y = pip_y

        self.side_by_side_width = 2 * self.main_width
        self.side_by_side_height = self.main_height

        self.pipeline = Gst.Pipeline.new("pip-pipeline")

        self.udpsrc_feed1 = Gst.ElementFactory.make("udpsrc", "udpsrc-feed1")
        self.udpsrc_feed1.set_property("port", self.feed1_port)
        self.queue_feed1 = Gst.ElementFactory.make("queue", "queue-feed1")
        self.queue_feed1.set_property("max-size-time", 1)
        feed1_caps = Gst.Caps.from_string(
            "application/x-rtp, payload=97, clock-rate=90000, encoding-name=H265"
        )
        self.capsfilter_feed1 = Gst.ElementFactory.make("capsfilter", "capsfilter-feed1")
        self.capsfilter_feed1.set_property("caps", feed1_caps)
        self.jitterbuffer_feed1 = Gst.ElementFactory.make("rtpjitterbuffer", "jitterbuffer-feed1")
        self.jitterbuffer_feed1.set_property("latency", 1)
        self.depay_feed1 = Gst.ElementFactory.make("rtph265depay", "depay-feed1")
        self.decoder_feed1 = Gst.ElementFactory.make("vaapih265dec", "decoder-feed1")
        self.convert_feed1 = Gst.ElementFactory.make("videoconvert", "convert-feed1")
        self.scale_feed1 = Gst.ElementFactory.make("videoscale", "scale-feed1")

        self.udpsrc_feed2 = Gst.ElementFactory.make("udpsrc", "udpsrc-feed2")
        self.udpsrc_feed2.set_property("port", self.feed2_port)
        self.queue_feed2 = Gst.ElementFactory.make("queue", "queue-feed2")
        self.queue_feed2.set_property("max-size-time", 1)
        feed2_caps = Gst.Caps.from_string(
            "application/x-rtp, payload=97, clock-rate=90000, encoding-name=H265"
        )
        self.capsfilter_feed2 = Gst.ElementFactory.make("capsfilter", "capsfilter-feed2")
        self.capsfilter_feed2.set_property("caps", feed2_caps)
        self.jitterbuffer_feed2 = Gst.ElementFactory.make("rtpjitterbuffer", "jitterbuffer-feed2")
        self.jitterbuffer_feed2.set_property("latency", 1)
        self.depay_feed2 = Gst.ElementFactory.make("rtph265depay", "depay-feed2")
        self.decoder_feed2 = Gst.ElementFactory.make("vaapih265dec", "decoder-feed2")
        self.convert_feed2 = Gst.ElementFactory.make("videoconvert", "convert-feed2")
        self.scale_feed2 = Gst.ElementFactory.make("videoscale", "scale-feed2")

        self.compositor = Gst.ElementFactory.make("compositor", "compositor")
        self.compositor_capsfilter = Gst.ElementFactory.make("capsfilter", "compositor-output-caps")
        self.caps_main = Gst.Caps.from_string(
            f"video/x-raw, width={self.main_width}, height={self.main_height}, framerate=0/1"
        )
        self.caps_side = Gst.Caps.from_string(
            f"video/x-raw, width={self.side_by_side_width}, height={self.side_by_side_height}, framerate=0/1"
        )
        if initial_mode == 5:
            self.compositor_capsfilter.set_property("caps", self.caps_side)
        else:
            self.compositor_capsfilter.set_property("caps", self.caps_main)

        self.xvsink = Gst.ElementFactory.make("xvimagesink", "xvimagesink")
        self.xvsink.set_property("sync", False)

        for comp in (
            self.udpsrc_feed1, self.queue_feed1, self.capsfilter_feed1,
            self.jitterbuffer_feed1, self.depay_feed1, self.decoder_feed1,
            self.convert_feed1, self.scale_feed1,
            self.udpsrc_feed2, self.queue_feed2, self.capsfilter_feed2,
            self.jitterbuffer_feed2, self.depay_feed2, self.decoder_feed2,
            self.convert_feed2, self.scale_feed2,
            self.compositor, self.compositor_capsfilter, self.xvsink
        ):
            if not comp:
                raise RuntimeError("Failed creating GStreamer element.")
            self.pipeline.add(comp)

        self.udpsrc_feed1.link(self.queue_feed1)
        self.queue_feed1.link(self.capsfilter_feed1)
        self.capsfilter_feed1.link(self.jitterbuffer_feed1)
        self.jitterbuffer_feed1.link(self.depay_feed1)
        self.depay_feed1.link(self.decoder_feed1)
        self.decoder_feed1.link(self.convert_feed1)
        self.convert_feed1.link(self.scale_feed1)

        self.udpsrc_feed2.link(self.queue_feed2)
        self.queue_feed2.link(self.capsfilter_feed2)
        self.capsfilter_feed2.link(self.jitterbuffer_feed2)
        self.jitterbuffer_feed2.link(self.depay_feed2)
        self.depay_feed2.link(self.decoder_feed2)
        self.decoder_feed2.link(self.convert_feed2)
        self.convert_feed2.link(self.scale_feed2)

        pad_tmpl_1 = self.compositor.get_pad_template("sink_%u")
        pad_tmpl_2 = self.compositor.get_pad_template("sink_%u")
        self.feed1_pad = self.compositor.request_pad(pad_tmpl_1, None, None)
        self.feed2_pad = self.compositor.request_pad(pad_tmpl_2, None, None)
        f1_src_pad = self.scale_feed1.get_static_pad("src")
        f2_src_pad = self.scale_feed2.get_static_pad("src")
        f1_src_pad.link(self.feed1_pad)
        f2_src_pad.link(self.feed2_pad)

        if not self.compositor.link(self.compositor_capsfilter):
            raise RuntimeError("Could not link compositor -> capsfilter.")
        if not self.compositor_capsfilter.link(self.xvsink):
            raise RuntimeError("Could not link capsfilter -> xvimagesink.")

        self.bus = self.pipeline.get_bus()
        self.bus.add_signal_watch()
        self.bus.connect("message", self.on_bus_message)

        self.loop = None
        self.set_mode(initial_mode)

        self.listen_keys = listen_keys
        if self.listen_keys and keyboard is None:
            print("pynput not installed, can't enable keyboard listening.")
            self.listen_keys = False

        if self.listen_keys and keyboard is not None:
            self._start_global_keyboard_listener()

    def _start_global_keyboard_listener(self):
        def on_press(key):
            try:
                if key.char in ("1","2","3","4","5"):
                    GLib.idle_add(self._switch_mode_idle, key.char)
            except:
                pass
        self.listener = keyboard.Listener(on_press=on_press)
        self.listener.start()

    def _switch_mode_idle(self, ch):
        print(f"[KEY] {ch}")
        self.set_mode(int(ch))
        return False

    def on_bus_message(self, bus, msg):
        t = msg.type
        if t == Gst.MessageType.ERROR:
            err, dbg = msg.parse_error()
            print(f"[ERROR] {err} (debug={dbg})")
            self.stop()
        elif t == Gst.MessageType.EOS:
            print("[INFO] End-Of-Stream.")
            self.stop()

    def set_mode(self, mode):
        if mode == 5:
            self.compositor_capsfilter.set_property("caps", self.caps_side)
        else:
            self.compositor_capsfilter.set_property("caps", self.caps_main)
        if mode == 1:
            self._set_pad_geometry(self.feed1_pad, 0, 0, self.main_width, self.main_height, 1.0, 0)
            self._set_pad_geometry(self.feed2_pad, self.pip_x, self.pip_y, self.pip_width, self.pip_height, 1.0, 1)
        elif mode == 2:
            self._set_pad_geometry(self.feed2_pad, 0, 0, self.main_width, self.main_height, 1.0, 0)
            self._set_pad_geometry(self.feed1_pad, self.pip_x, self.pip_y, self.pip_width, self.pip_height, 1.0, 1)
        elif mode == 3:
            self._set_pad_geometry(self.feed1_pad, 0, 0, self.main_width, self.main_height, 1.0, 0)
            self._set_pad_geometry(self.feed2_pad, 0, 0, 1, 1, 0.0, 1)
        elif mode == 4:
            self._set_pad_geometry(self.feed2_pad, 0, 0, self.main_width, self.main_height, 1.0, 0)
            self._set_pad_geometry(self.feed1_pad, 0, 0, 1, 1, 0.0, 1)
        elif mode == 5:
            w, h = self.side_by_side_width, self.side_by_side_height
            self._set_pad_geometry(self.feed1_pad, 0, 0, w//2, h, 1.0, 0)
            self._set_pad_geometry(self.feed2_pad, w//2, 0, w//2, h, 1.0, 0)

    def _set_pad_geometry(self, pad, xpos, ypos, width, height, alpha, zorder):
        pad.set_property("xpos", xpos)
        pad.set_property("ypos", ypos)
        pad.set_property("width", width)
        pad.set_property("height", height)
        pad.set_property("alpha", alpha)
        pad.set_property("zorder", zorder)

    def start(self):
        self.pipeline.set_state(Gst.State.PLAYING)

    def stop(self):
        self.pipeline.set_state(Gst.State.NULL)
        if self.loop and self.loop.is_running():
            self.loop.quit()

    def run(self):
        self.loop = GLib.MainLoop()
        self.start()
        try:
            self.loop.run()
        except KeyboardInterrupt:
            print("[INFO] Interrupted by user.")
            self.stop()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("feed1_port", nargs="?", type=int, default=5600)
    parser.add_argument("feed2_port", nargs="?", type=int, default=5601)
    parser.add_argument("--geometry", default="")
    parser.add_argument("--mode", type=int, default=1)
    parser.add_argument("--listen-keys", action="store_true", help="Enable global key listener for 1..5.")
    args = parser.parse_args()

    main_w, main_h = 1440, 1080
    pip_w, pip_h = 360, 270
    pip_x, pip_y = 50, 50
    if args.geometry:
        try:
            parts = args.geometry.split("x")
            if len(parts) != 6:
                raise ValueError
            main_w = int(parts[0])
            main_h = int(parts[1])
            pip_w = int(parts[2])
            pip_h = int(parts[3])
            pip_x = int(parts[4])
            pip_y = int(parts[5])
        except ValueError:
            print("Error parsing --geometry")
            sys.exit(1)

    pip = PiPCompositor(
        feed1_port=args.feed1_port,
        feed2_port=args.feed2_port,
        main_width=main_w,
        main_height=main_h,
        pip_width=pip_w,
        pip_height=pip_h,
        pip_x=pip_x,
        pip_y=pip_y,
        initial_mode=args.mode,
        listen_keys=args.listen_keys
    )

    def signal_handler(sig, frame):
        pip.stop()

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    pip.run()

if __name__ == "__main__":
    main()
