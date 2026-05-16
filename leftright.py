import time
from pynput import keyboard
from pynput.keyboard import Key, Controller

# Create controller
kb = Controller()

# Control flag
running = True

# Callback for key presses
def on_press(key):
    global running
    if key == keyboard.Key.f10:  # stop when F10 is pressed
        running = False
        return False  # stop listener

# Start listener in background
listener = keyboard.Listener(on_press=on_press)
listener.start()

# Main loop
while running:
    # Hold left arrow for 2 sec
    kb.press(Key.left)
    time.sleep(2)
    kb.release(Key.left)

    # Hold right arrow for 2.5 sec
    kb.press(Key.right)
    time.sleep(2.5)
    kb.release(Key.right)

print("Stopped by pressing F10")