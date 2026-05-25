"""
Taskbar Text Clipboard

A small Windows app for storing reusable text snippets, copying one to the
clipboard, and adding new snippets.

How to use:
1. Save this file somewhere permanent, for example:
   C:\\Users\\YOUR_NAME\\Apps\\taskbar_text_clipboard.pyw
2. Double-click it to run.
3. To pin it to the taskbar:
   - Right-click the file and choose "Create shortcut".
   - Right-click the shortcut, choose "Properties".
   - In Target, use:
       pythonw.exe "C:\\path\\to\\taskbar_text_clipboard.pyw"
     If pythonw.exe is not found, use the full path to pythonw.exe.
   - Right-click the shortcut and choose "Pin to taskbar".

The text list is saved beside this file as snippets.json.
"""

from __future__ import annotations

import json
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, simpledialog

APP_TITLE = "Text Clipboard"
DATA_FILE = Path(__file__).with_name("snippets.json")
DEFAULT_SNIPPETS = [
    "Example text 1",
    "Example text 2",
]


class ClipboardApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("420x360")
        self.root.minsize(360, 280)
        self.root.attributes("-topmost", True)
        self.root.after(300, lambda: self.root.attributes("-topmost", False))

        self.snippets = self.load_snippets()

        self.header = tk.Label(
            root,
            text="Choose text to copy, or add a new item",
            font=("Segoe UI", 11, "bold"),
            anchor="w",
        )
        self.header.pack(fill="x", padx=12, pady=(12, 6))

        self.listbox = tk.Listbox(root, font=("Segoe UI", 10), activestyle="dotbox")
        self.listbox.pack(fill="both", expand=True, padx=12, pady=6)
        self.listbox.bind("<Double-Button-1>", lambda _event: self.copy_selected())
        self.listbox.bind("<Return>", lambda _event: self.copy_selected())

        button_frame = tk.Frame(root)
        button_frame.pack(fill="x", padx=12, pady=(6, 12))

        self.copy_button = tk.Button(
            button_frame,
            text="Copy selected",
            command=self.copy_selected,
            height=2,
        )
        self.copy_button.pack(side="left", fill="x", expand=True, padx=(0, 6))

        self.add_button = tk.Button(
            button_frame,
            text="Add new text",
            command=self.add_snippet,
            height=2,
        )
        self.add_button.pack(side="left", fill="x", expand=True, padx=(6, 0))

        bottom_frame = tk.Frame(root)
        bottom_frame.pack(fill="x", padx=12, pady=(0, 12))

        self.delete_button = tk.Button(
            bottom_frame,
            text="Delete selected",
            command=self.delete_selected,
        )
        self.delete_button.pack(side="left")

        self.status = tk.Label(bottom_frame, text="", anchor="e", fg="green")
        self.status.pack(side="right", fill="x", expand=True)

        self.refresh_list()

    def load_snippets(self) -> list[str]:
        if not DATA_FILE.exists():
            self.save_snippets(DEFAULT_SNIPPETS)
            return DEFAULT_SNIPPETS.copy()

        try:
            data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [str(item) for item in data if str(item).strip()]
        except Exception:
            messagebox.showwarning(
                APP_TITLE,
                f"Could not read {DATA_FILE.name}. Starting with an empty list.",
            )
        return []

    def save_snippets(self, snippets: list[str] | None = None) -> None:
        items = self.snippets if snippets is None else snippets
        DATA_FILE.write_text(
            json.dumps(items, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def refresh_list(self) -> None:
        self.listbox.delete(0, tk.END)
        for item in self.snippets:
            preview = item.replace("\n", "  /  ")
            if len(preview) > 120:
                preview = preview[:117] + "..."
            self.listbox.insert(tk.END, preview)

        if self.snippets:
            self.listbox.select_set(0)
            self.status.config(text=f"{len(self.snippets)} saved")
        else:
            self.status.config(text="No saved text yet")

    def selected_index(self) -> int | None:
        selection = self.listbox.curselection()
        if not selection:
            messagebox.showinfo(APP_TITLE, "Select an item first.")
            return None
        return int(selection[0])

    def copy_selected(self) -> None:
        index = self.selected_index()
        if index is None:
            return

        text = self.snippets[index]
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()
        self.status.config(text="Copied to clipboard")

    def add_snippet(self) -> None:
        text = simpledialog.askstring(
            APP_TITLE,
            "Enter the text to save:",
            parent=self.root,
        )
        if text is None:
            return

        text = text.strip()
        if not text:
            messagebox.showinfo(APP_TITLE, "Empty text was not added.")
            return

        self.snippets.append(text)
        self.save_snippets()
        self.refresh_list()
        self.listbox.select_clear(0, tk.END)
        self.listbox.select_set(tk.END)
        self.status.config(text="Added")

    def delete_selected(self) -> None:
        index = self.selected_index()
        if index is None:
            return

        item = self.snippets[index]
        preview = item.replace("\n", "  /  ")
        if len(preview) > 80:
            preview = preview[:77] + "..."

        confirm = messagebox.askyesno(APP_TITLE, f"Delete this item?\n\n{preview}")
        if not confirm:
            return

        del self.snippets[index]
        self.save_snippets()
        self.refresh_list()
        self.status.config(text="Deleted")


def main() -> None:
    root = tk.Tk()
    ClipboardApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
