import time
import math
import pyautogui
from pynput import mouse

last_click_time = 0
last_click_pos = None

DOUBLE_CLICK_TIME = 0.4
DOUBLE_CLICK_DISTANCE = 5


def on_click(x, y, button, pressed):
    global last_click_time, last_click_pos

    if not pressed or button != mouse.Button.left:
        return

    now = time.time()

    if last_click_pos is not None:
        distance = math.hypot(
            x - last_click_pos[0],
            y - last_click_pos[1]
        )

        if (
            now - last_click_time <= DOUBLE_CLICK_TIME
            and distance <= DOUBLE_CLICK_DISTANCE
        ):
            r, g, b = pyautogui.pixel(int(x), int(y))

            hex_color = f"#{r:02X}{g:02X}{b:02X}"

            print(hex_color)

            # Prevent a triple-click from counting as another double-click
            last_click_time = 0
            last_click_pos = None
            return

    last_click_time = now
    last_click_pos = (x, y)


print("Color picker running.")
print("Double-click anywhere to get the HTML color.")
print("Press Ctrl+C to quit.\n")

with mouse.Listener(on_click=on_click) as listener:
    listener.join()