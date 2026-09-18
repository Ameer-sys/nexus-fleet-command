"""Native MuJoCo viewer with press-and-hold drive controls."""

import time
from dataclasses import dataclass

import glfw
import mujoco
import numpy as np
from OpenGL.GL import glViewport

from .config import DIRECTION_KEYS
from .drive import DriveControls

MOVEMENT_KEYS = {glfw.KEY_W: "W", glfw.KEY_A: "A", glfw.KEY_S: "S", glfw.KEY_D: "D"}
ACTION_KEYS = {
    glfw.KEY_SPACE: "stop",
    glfw.KEY_X: "stop",
    glfw.KEY_P: "pause",
    glfw.KEY_R: "reset",
    glfw.KEY_M: "arms",
    glfw.KEY_L: "lean",
    glfw.KEY_LEFT_BRACKET: "angle-",
    glfw.KEY_RIGHT_BRACKET: "angle+",
}


@dataclass
class Button:
    action: str
    text: str
    x: float
    y: float
    width: float
    height: float = 40
    active: bool = False

    def contains(self, x, y):
        return self.x <= x < self.x + self.width and self.y <= y < self.y + self.height


class DriveWindow:
    PANEL = 320
    INSTRUCTIONS = (
        "Hold WASD or the on-screen buttons to drive. Release to stop. Space: stop; R: reset."
    )

    def __init__(self, sim, controls=None, title=None):
        self.sim = sim
        self.controls = controls if controls is not None else DriveControls(sim)
        self.window = None
        self.context = None
        self.drag = None
        self.mouse = (0.0, 0.0)
        self.buttons = []
        if not glfw.init():
            raise RuntimeError("GLFW could not initialize the desktop display")
        try:
            glfw.window_hint(glfw.SAMPLES, 4)
            self.window = glfw.create_window(
                1180,
                740,
                title or f"BracketBot | {sim.kind.title()} policy | WASD drive",
                None,
                None,
            )
            if not self.window:
                raise RuntimeError("Could not create the BracketBot drive window")
            glfw.set_window_size_limits(self.window, 940, 690, glfw.DONT_CARE, glfw.DONT_CARE)
            glfw.make_context_current(self.window)
            glfw.swap_interval(1)
            self._dimensions()
            font = (
                mujoco.mjtFontScale.mjFONTSCALE_200
                if self.sx > 1
                else mujoco.mjtFontScale.mjFONTSCALE_100
            )
            self.context = mujoco.MjrContext(sim.model, font)
            self.scene = mujoco.MjvScene(sim.model, maxgeom=2000)
            self.option = mujoco.MjvOption()
            self.camera = mujoco.MjvCamera()
            self.camera.distance, self.camera.azimuth, self.camera.elevation = (
                2.65,
                135,
                -12,
            )
            glfw.set_key_callback(self.window, self._key)
            glfw.set_window_focus_callback(self.window, self._focus)
            glfw.set_mouse_button_callback(self.window, self._mouse_button)
            glfw.set_cursor_pos_callback(self.window, self._cursor)
            glfw.set_scroll_callback(self.window, self._scroll)
            glfw.focus_window(self.window)
        except BaseException:
            self.close()
            raise

    def close(self):
        self.controls.stop()
        if self.context:
            self.context.free()
            self.context = None
        if self.window:
            glfw.destroy_window(self.window)
            self.window = None
        glfw.terminate()

    def _dimensions(self):
        self.width, self.height = glfw.get_window_size(self.window)
        self.pixel_width, self.pixel_height = glfw.get_framebuffer_size(self.window)
        self.sx = self.pixel_width / max(1, self.width)
        self.sy = self.pixel_height / max(1, self.height)

    def _rect(self, x, y, width, height):
        return mujoco.MjrRect(
            round(x * self.sx),
            round((self.height - y - height) * self.sy),
            round(width * self.sx),
            round(height * self.sy),
        )

    def _fill(self, x, y, width, height, color):
        mujoco.mjr_rectangle(self._rect(x, y, width, height), *color)

    def _text(self, x, y, text, right="", big=False):
        # Draw text directly so buttons and cards keep their own background.
        glViewport(0, 0, self.pixel_width, self.pixel_height)
        font = mujoco.mjtFont.mjFONT_BIG if big else mujoco.mjtFont.mjFONT_NORMAL
        baseline = (self.context.charHeightBig if big else self.context.charHeight) / self.sy
        for column, dx in [(text, 0), (right, 76)]:
            for i, line in enumerate(column.splitlines()):
                mujoco.mjr_text(
                    font,
                    line,
                    self.context,
                    (x + dx) / self.width,
                    (self.height - y - baseline - i * 22) / self.height,
                    0.88,
                    0.93,
                    0.97,
                )

    def _focus(self, window, focused):
        self.controls.focus(focused)
        self.drag = None

    def _key(self, window, key, scancode, action, mods):
        if key in MOVEMENT_KEYS:
            if action == glfw.RELEASE:
                self.controls.release(MOVEMENT_KEYS[key])
            elif action == glfw.PRESS and not mods & (
                glfw.MOD_SUPER | glfw.MOD_CONTROL | glfw.MOD_ALT
            ):
                self.controls.press(MOVEMENT_KEYS[key])
        elif action == glfw.PRESS:
            if key in ACTION_KEYS:
                self.controls.activate(ACTION_KEYS[key])
            elif key == glfw.KEY_ESCAPE:
                glfw.set_window_should_close(window, True)

    def _mouse_button(self, window, button, action, mods):
        x, y = glfw.get_cursor_pos(window)
        if action == glfw.RELEASE:
            if button == glfw.MOUSE_BUTTON_LEFT:
                self.controls.release_mouse()
            self.drag = None
            return
        if x >= self.width - self.PANEL:
            if button == glfw.MOUSE_BUTTON_LEFT:
                for item in self.buttons:
                    if item.contains(x, y):
                        if item.action in DIRECTION_KEYS:
                            self.controls.press(item.action, "mouse")
                        else:
                            self.controls.activate(item.action)
                        break
        else:
            self.drag = button
        self.mouse = (x, y)

    def _cursor(self, window, x, y):
        dx, dy = x - self.mouse[0], y - self.mouse[1]
        self.mouse = (x, y)
        if self.drag is None or not self.height:
            return
        action = (
            mujoco.mjtMouse.mjMOUSE_ROTATE_V
            if self.drag == glfw.MOUSE_BUTTON_LEFT
            else mujoco.mjtMouse.mjMOUSE_ZOOM
        )
        mujoco.mjv_moveCamera(
            self.sim.model,
            action,
            dx / self.height,
            dy / self.height,
            self.scene,
            self.camera,
        )

    def _scroll(self, window, dx, dy):
        if self.mouse[0] < self.width - self.PANEL:
            mujoco.mjv_moveCamera(
                self.sim.model,
                mujoco.mjtMouse.mjMOUSE_ZOOM,
                0,
                -0.05 * dy,
                self.scene,
                self.camera,
            )

    def _status_text(self):
        if self.sim.failed:
            return "FALLEN - press R to reset"
        if self.controls.paused:
            return "PAUSED"
        if not self.controls.focused:
            return "CLICK WINDOW TO DRIVE"
        if self.controls.lean_contact:
            return "LEAN CONTACT"
        return "READY TO DRIVE"

    def _draw_button(self, button):
        if button.active:
            color = (0.08, 0.40, 0.34, 1)
        elif button.action == "stop":
            color = (0.30, 0.12, 0.15, 1)
        elif button.contains(*self.mouse):
            color = (0.14, 0.20, 0.27, 1)
        else:
            color = (0.095, 0.14, 0.20, 1)
        self._fill(button.x, button.y, button.width, button.height, color)
        self._text(button.x + 6, button.y + 9, button.text)

    def _panel(self):
        c, sim = self.controls, self.sim
        left = self.width - self.PANEL
        x = left + 22
        self._fill(left, 0, self.PANEL, self.height, (0.045, 0.065, 0.09, 1))
        self._fill(left, 0, 2, self.height, (0.13, 0.20, 0.27, 1))
        self._text(x, 20, "BRACKETBOT", big=True)
        self._text(x, 57, f"{sim.kind.upper()} POLICY  /  DRIVE")
        self._fill(
            x,
            94,
            276,
            32,
            (0.12, 0.27, 0.24, 1) if not sim.failed else (0.40, 0.15, 0.16, 1),
        )
        self._text(x + 6, 99, self._status_text())
        pitch, _, _, speed = sim.state()
        self._text(
            x,
            143,
            "Speed\nCommand\nPitch",
            f"{speed:+.2f} m/s\n{sim.command[0]:+.2f} m/s   {sim.command[1]:+.2f} rad/s\n{np.rad2deg(pitch):+.1f} deg",
        )
        self._text(x, 220, "HOLD TO DRIVE")
        self.buttons = [
            Button("W", "W  Forward", x + 88, 250, 100, 46, "W" in c.keys),
            Button("A", "A  Left", x, 304, 84, 46, "A" in c.keys),
            Button("S", "S  Back", x + 92, 304, 92, 46, "S" in c.keys),
            Button("D", "D  Right", x + 192, 304, 84, 46, "D" in c.keys),
            Button("speed-", "-", x + 192, 395, 38, 32),
            Button("speed+", "+", x + 238, 395, 38, 32),
            Button("turn-", "-", x + 192, 437, 38, 32),
            Button("turn+", "+", x + 238, 437, 38, 32),
            Button("stop", "SPACE  /  STOP", x, 489, 276, 44),
            Button("pause", "P  Resume" if c.paused else "P  Pause", x, 544, 132),
            Button("reset", "R  Reset", x + 144, 544, 132),
        ]
        self._text(x, 360, "Release keys to stop the drive command.")
        self._text(x, 399, f"Drive speed   {c.speed:.2f} m/s")
        self._text(x, 441, f"Turn speed    {c.turn_speed:.1f} rad/s")
        if sim.kind == "arms":
            self.buttons.append(
                Button(
                    "arms",
                    "M  Arm motion: " + ("ON" if sim.arm_motion else "OFF"),
                    x,
                    596,
                    276,
                    active=sim.arm_motion,
                )
            )
        elif sim.kind == "lean":
            self.buttons.append(
                Button(
                    "lean",
                    "L  " + ("Return to driving" if c.lean_contact else "Try table lean"),
                    x,
                    596,
                    276,
                    active=c.lean_contact,
                )
            )
            self.buttons.extend(
                [
                    Button("angle-", "-", x + 192, 643, 38, 30),
                    Button("angle+", "+", x + 238, 643, 38, 30),
                ]
            )
            self._text(x, 646, f"Lean target   {c.lean_angle:.0f} deg")
        else:
            self._text(x, 601, "Terrain balance policy\nArms held at their training pose.")
        for button in self.buttons:
            self._draw_button(button)
        self._text(
            18,
            self.height - 35,
            "WASD: drive   Space: stop   Mouse drag: orbit   Scroll: zoom   Esc: close",
        )
        self._text(18, 16, f"{sim.data.time:6.1f} s  |  200 Hz  |  Chopped model")

    def _lookat(self):
        return self.sim.data.qpos[:3] + [0, 0, 0.64]

    def _decorate_scene(self):
        pass

    def render(self, capture=None):
        self._dimensions()
        if self.pixel_width == 0 or self.pixel_height == 0:
            return
        self.camera.lookat[:] = self._lookat()
        mujoco.mjv_updateScene(
            self.sim.model,
            self.sim.data,
            self.option,
            None,
            self.camera,
            mujoco.mjtCatBit.mjCAT_ALL,
            self.scene,
        )
        self._decorate_scene()
        mujoco.mjr_render(
            self._rect(0, 0, self.width - self.PANEL, self.height),
            self.scene,
            self.context,
        )
        self._panel()
        if capture is not None:
            from PIL import Image

            pixels = np.empty((self.pixel_height, self.pixel_width, 3), np.uint8)
            mujoco.mjr_readPixels(
                pixels,
                None,
                mujoco.MjrRect(0, 0, self.pixel_width, self.pixel_height),
                self.context,
            )
            Image.fromarray(np.flipud(pixels)).save(capture)
        glfw.swap_buffers(self.window)

    def run(self, duration, tick):
        print(self.INSTRUCTIONS, flush=True)
        deadline = time.monotonic()
        dt = self.sim.model.opt.timestep
        while not glfw.window_should_close(self.window) and self.sim.data.time < duration:
            glfw.poll_events()
            now = time.monotonic()
            self.controls.update()
            if not self.controls.paused and not self.sim.failed:
                steps = min(round(0.04 / dt), max(0, int((now - deadline) / dt) + 1))
                for _ in range(steps):
                    if self.sim.data.time >= duration or self.sim.failed:
                        break
                    tick()
                    deadline += dt
                if deadline < now - 0.1:
                    deadline = now
            else:
                deadline = now
            self.render()
            # Vsync normally waits; this also avoids a busy loop when minimized.
            time.sleep(max(0.0, 1 / 60 - (time.monotonic() - now)))


def run_viewer(sim, duration, tick):
    viewer = DriveWindow(sim)
    try:
        viewer.run(duration, tick)
    finally:
        viewer.close()
