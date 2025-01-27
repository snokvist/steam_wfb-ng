#!/usr/bin/env python3

import gi
gi.require_version("Gst", "1.0")
gi.require_version("GObject", "2.0")
from gi.repository import Gst, GObject, GLib
import sys
import argparse

"""
This script is identical to the 'compositor' version except:
  - We replace the aggregator-based "compositor" with "vaapicompositor",
    which uses VA-API for hardware-accelerated mixing.

Everything else remains the same (modes 1..5, geometry, UDP H.265, etc.).
"""

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
        initial_mode=1
    ):
        # Store geometry
        self.feed1_port = feed1_port
        self.feed2_port = feed2_port

        self.main_width  = main_width
        self.main_height = main_height
        self.pip_width   = pip_width
        self.pip_height  = pip_height
        self.pip_x       = pip_x
        self.pip_y       = pip_y

        # Compute side-by-side dims
        self.side_by_side_width  = 2 * self.main_width
        self.side_by_side_height = self.main_height

        # Create pipeline
        self.pipeline = Gst.Pipeline.new("pip-pipeline")

        # FEED1 VIDEO BRANCH
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
        # If "vaapih265dec" isn't suitable on your hardware, switch to e.g. "avdec_h265"
        self.decoder_feed1 = Gst.ElementFactory.make("vaapih265dec", "decoder-feed1")

        self.convert_feed1 = Gst.ElementFactory.make("videoconvert", "convert-feed1")
        self.scale_feed1   = Gst.ElementFactory.make("videoscale",   "scale-feed1")

        # FEED2 VIDEO BRANCH
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
        self.scale_feed2   = Gst.ElementFactory.make("videoscale",   "scale-feed2")

        # REPLACE aggregator-based "compositor" WITH "vaapicompositor"
        self.vacompositor = Gst.ElementFactory.make("vaapicompositor", "vacompositor")
        # Everything else remains the same
        self.compositor_capsfilter = Gst.ElementFactory.make("capsfilter", "compositor-output-caps")

        # Build the final output caps for normal and side-by-side
        self.caps_main = Gst.Caps.from_string(
            f"video/x-raw, width={self.main_width}, height={self.main_height}, framerate=0/1"
        )
        self.caps_side = Gst.Caps.from_string(
            f"video/x-raw, width={self.side_by_side_width}, height={self.side_by_side_height}, framerate=0/1"
        )

        # Decide initial caps
        if initial_mode == 5:
            self.compositor_capsfilter.set_property("caps", self.caps_side)
        else:
            self.compositor_capsfilter.set_property("caps", self.caps_main)

        self.xvsink = Gst.ElementFactory.make("xvimagesink", "xvimagesink")
        self.xvsink.set_property("sync", False)

        # Add elements
        for comp in (
            self.udpsrc_feed1, self.queue_feed1, self.capsfilter_feed1,
            self.jitterbuffer_feed1, self.depay_feed1, self.decoder_feed1,
            self.convert_feed1, self.scale_feed1,

            self.udpsrc_feed2, self.queue_feed2, self.capsfilter_feed2,
            self.jitterbuffer_feed2, self.depay_feed2, self.decoder_feed2,
            self.convert_feed2, self.scale_feed2,

            self.vacompositor, self.compositor_capsfilter, self.xvsink
        ):
            if not comp:
                raise RuntimeError("Failed to create or add a GStreamer element.")
            self.pipeline.add(comp)

        # Link FEED1
        self.udpsrc_feed1.link(self.queue_feed1)
        self.queue_feed1.link(self.capsfilter_feed1)
        self.capsfilter_feed1.link(self.jitterbuffer_feed1)
        self.jitterbuffer_feed1.link(self.depay_feed1)
        self.depay_feed1.link(self.decoder_feed1)
        self.decoder_feed1.link(self.convert_feed1)
        self.convert_feed1.link(self.scale_feed1)

        # Link FEED2
        self.udpsrc_feed2.link(self.queue_feed2)
        self.queue_feed2.link(self.capsfilter_feed2)
        self.capsfilter_feed2.link(self.jitterbuffer_feed2)
        self.jitterbuffer_feed2.link(self.depay_feed2)
        self.depay_feed2.link(self.decoder_feed2)
        self.decoder_feed2.link(self.convert_feed2)
        self.convert_feed2.link(self.scale_feed2)

        # Request aggregator pads from vaapicompositor
        pad_tmpl_1 = self.vacompositor.get_pad_template("sink_%u")
        pad_tmpl_2 = self.vacompositor.get_pad_template("sink_%u")

        self.feed1_pad = self.vacompositor.request_pad(pad_tmpl_1, None, None)
        f1_src_pad = self.scale_feed1.get_static_pad("src")
        f1_src_pad.link(self.feed1_pad)

        self.feed2_pad = self.vacompositor.request_pad(pad_tmpl_2, None, None)
        f2_src_pad = self.scale_feed2.get_static_pad("src")
        f2_src_pad.link(self.feed2_pad)

        # Link vaapicompositor -> capsfilter -> xvimagesink
        if not self.vacompositor.link(self.compositor_capsfilter):
            raise RuntimeError("Could not link vaapicompositor -> capsfilter")
        if not self.compositor_capsfilter.link(self.xvsink):
            raise RuntimeError("Could not link capsfilter -> xvimagesink")

        # Setup bus watch
        self.bus = self.pipeline.get_bus()
        self.bus.add_signal_watch()
        self.bus.connect("message", self.on_bus_message)

        self.loop = None

        # Initialize the requested mode
        self.set_mode(initial_mode)

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
        """
        Five modes (unchanged):
          1) feed1 main, feed2 PiP
          2) feed2 main, feed1 PiP
          3) only feed1
          4) only feed2
          5) side-by-side
        """
        if mode == 5:
            # side-by-side
            self.compositor_capsfilter.set_property("caps", self.caps_side)
        else:
            self.compositor_capsfilter.set_property("caps", self.caps_main)

        if mode == 1:
            # feed1 main, feed2 PiP
            self._set_pad_geometry(
                self.feed1_pad, 0, 0, self.main_width, self.main_height, alpha=1.0, zorder=0
            )
            self._set_pad_geometry(
                self.feed2_pad, self.pip_x, self.pip_y, self.pip_width, self.pip_height, alpha=1.0, zorder=1
            )
        elif mode == 2:
            # feed2 main, feed1 PiP
            self._set_pad_geometry(
                self.feed2_pad, 0, 0, self.main_width, self.main_height, alpha=1.0, zorder=0
            )
            self._set_pad_geometry(
                self.feed1_pad, self.pip_x, self.pip_y, self.pip_width, self.pip_height, alpha=1.0, zorder=1
            )
        elif mode == 3:
            # only feed1
            self._set_pad_geometry(
                self.feed1_pad, 0, 0, self.main_width, self.main_height, alpha=1.0, zorder=0
            )
            self._set_pad_geometry(self.feed2_pad, 0, 0, 1, 1, alpha=0.0, zorder=1)
        elif mode == 4:
            # only feed2
            self._set_pad_geometry(
                self.feed2_pad, 0, 0, self.main_width, self.main_height, alpha=1.0, zorder=0
            )
            self._set_pad_geometry(self.feed1_pad, 0, 0, 1, 1, alpha=0.0, zorder=1)
        elif mode == 5:
            # side-by-side
            half_width = self.side_by_side_width // 2
            self._set_pad_geometry(
                self.feed1_pad, 0, 0, half_width, self.side_by_side_height, alpha=1.0, zorder=0
            )
            self._set_pad_geometry(
                self.feed2_pad, half_width, 0, half_width, self.side_by_side_height, alpha=1.0, zorder=0
            )
        else:
            print(f"[WARN] Unknown mode {mode}")

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

        # Watch stdin for commands 1..5
        GLib.io_add_watch(sys.stdin, GLib.IO_IN, self.on_stdin_command)

        self.start()
        try:
            self.loop.run()
        except KeyboardInterrupt:
            print("[INFO] Interrupted by user.")
            self.stop()

    def on_stdin_command(self, source, condition):
        if condition == GLib.IO_IN:
            line = sys.stdin.readline()
            if not line:
                self.stop()
                return False
            line = line.strip()
            if line in ("1", "2", "3", "4", "5"):
                mode = int(line)
                print(f"[ACTION] Switching to mode {mode}")
                self.set_mode(mode)
            else:
                print("[INFO] Valid commands are 1,2,3,4,5. You typed =", repr(line))
            return True
        return False


def main():
    parser = argparse.ArgumentParser(description="Two-stream PiP with VA-API compositor.")
    parser.add_argument("feed1_port", nargs="?", type=int, default=5600)
    parser.add_argument("feed2_port", nargs="?", type=int, default=5601)
    parser.add_argument("--geometry", default="")
    parser.add_argument("--mode", type=int, default=1)
    args = parser.parse_args()

    # Basic geometry defaults
    main_w, main_h = 1440, 1080
    pip_w, pip_h   = 360, 270
    pip_x, pip_y   = 50, 50

    if args.geometry:
        try:
            parts = args.geometry.split("x")
            if len(parts) != 6:
                raise ValueError
            main_w   = int(parts[0])
            main_h   = int(parts[1])
            pip_w    = int(parts[2])
            pip_h    = int(parts[3])
            pip_x    = int(parts[4])
            pip_y    = int(parts[5])
        except ValueError:
            print("[ERROR] --geometry must be MAINxHEIGHTxPIP_WxPIP_HxPIP_XxPIP_Y format.")
            sys.exit(1)

    pip_obj = PiPCompositor(
        feed1_port=args.feed1_port,
        feed2_port=args.feed2_port,
        main_width=main_w,
        main_height=main_h,
        pip_width=pip_w,
        pip_height=pip_h,
        pip_x=pip_x,
        pip_y=pip_y,
        initial_mode=args.mode
    )
    pip_obj.run()


if __name__ == "__main__":
    main()
