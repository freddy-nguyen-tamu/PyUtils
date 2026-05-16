import time
from pynput import keyboard, mouse

mouse_controller = mouse.Controller()

clicking = False  # State to control auto-clicking

def on_press(key):
    global clicking
    if key == keyboard.Key.f10:
        clicking = False
        print("Stopped clicking.")
        return False  # Stop listener
    elif key == keyboard.Key.space:  # Start with spacebar
        clicking = True
        print("Started clicking.")

# Function to click repeatedly
def autoclick():
    global clicking
    while clicking:
        mouse_controller.click(mouse.Button.left, 1)
        print("Click")
        time.sleep(0.1)  # Adjust speed (0.1s = 10 clicks per second)

# Listen for keys
with keyboard.Listener(on_press=on_press) as listener:
    while True:
        if clicking:
            autoclick()