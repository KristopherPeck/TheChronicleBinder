"""Small Tkinter desktop interface."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .converter import ConversionCancelled, ConversionError, ConversionResult, convert_url


class ConverterWindow:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("The Haruspis Assistant")
        self.root.minsize(640, 420)
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.url = tk.StringVar()
        self.output = tk.StringVar()
        self.title = tk.StringVar()
        self.author = tk.StringVar()
        self.reading_style = tk.StringVar(value="Official-inspired color")
        self.status = tk.StringVar(value="Ready")
        self.cancel_event = threading.Event()
        self._build()
        self.root.after(100, self._poll)

    def _build(self) -> None:
        style = ttk.Style()
        style.configure("Title.TLabel", font=("Segoe UI", 20, "bold"), foreground="#184f5c")
        style.configure("Hint.TLabel", foreground="#53666d")
        outer = ttk.Frame(self.root, padding=24)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="The Haruspis Assistant", style="Title.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(outer, text="Turn a Halo Waypoint Chronicle into a polished, offline EPUB.", style="Hint.TLabel").grid(row=1, column=0, columnspan=3, sticky="w", pady=(2, 22))

        ttk.Label(outer, text="Article URL").grid(row=2, column=0, sticky="w", pady=5)
        self.url_entry = ttk.Entry(outer, textvariable=self.url)
        self.url_entry.grid(row=2, column=1, columnspan=2, sticky="ew", pady=5)
        ttk.Label(outer, text="Save as").grid(row=3, column=0, sticky="w", pady=5)
        ttk.Entry(outer, textvariable=self.output).grid(row=3, column=1, sticky="ew", pady=5)
        ttk.Button(outer, text="Browse...", command=self._browse).grid(row=3, column=2, sticky="ew", padx=(8, 0), pady=5)
        ttk.Label(outer, text="Title override").grid(row=4, column=0, sticky="w", pady=5)
        ttk.Entry(outer, textvariable=self.title).grid(row=4, column=1, columnspan=2, sticky="ew", pady=5)
        ttk.Label(outer, text="Author override").grid(row=5, column=0, sticky="w", pady=5)
        ttk.Entry(outer, textvariable=self.author).grid(row=5, column=1, columnspan=2, sticky="ew", pady=5)
        ttk.Label(outer, text="Reading style").grid(row=6, column=0, sticky="w", pady=5)
        style_picker = ttk.Combobox(
            outer,
            textvariable=self.reading_style,
            values=("Official-inspired color", "Classic black and white"),
            state="readonly",
        )
        style_picker.grid(row=6, column=1, columnspan=2, sticky="ew", pady=5)
        ttk.Label(outer, text="Title and author are detected automatically; overrides are optional.", style="Hint.TLabel").grid(row=7, column=1, columnspan=2, sticky="w", pady=(0, 18))

        self.progress = ttk.Progressbar(outer, mode="indeterminate")
        self.progress.grid(row=8, column=0, columnspan=3, sticky="ew", pady=(5, 7))
        ttk.Label(outer, textvariable=self.status, style="Hint.TLabel").grid(row=9, column=0, columnspan=2, sticky="w")
        actions = ttk.Frame(outer)
        actions.grid(row=9, column=2, sticky="e")
        self.cancel_button = ttk.Button(actions, text="Cancel", command=self._cancel, state="disabled")
        self.cancel_button.pack(side="left", padx=(0, 8))
        self.convert_button = ttk.Button(actions, text="Create EPUB", command=self._start)
        self.convert_button.pack(side="left")
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(10, weight=1)
        self.url_entry.focus_set()

    def _browse(self) -> bool:
        path = filedialog.asksaveasfilename(title="Save EPUB", defaultextension=".epub", filetypes=[("EPUB books", "*.epub"), ("All files", "*.*")])
        if path:
            self.output.set(path)
            return True
        return False

    def _start(self) -> None:
        url = self.url.get().strip()
        if not url:
            messagebox.showwarning("Article URL required", "Paste a Halo Waypoint article URL first.")
            return
        destination = self.output.get().strip() or None
        if destination is None:
            if not self._browse():
                return
            destination = self.output.get().strip()
        destination_path = Path(destination)
        if destination_path.suffix.lower() != ".epub":
            destination_path = destination_path.with_suffix(".epub")
            destination = str(destination_path)
            self.output.set(destination)
        overwrite = False
        if destination_path.exists():
            if not messagebox.askyesno("Replace existing file?", f"{destination}\n\nThis file already exists. Replace it?"):
                return
            overwrite = True
        self.convert_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.cancel_event.clear()
        self.progress.start(12)
        self.status.set("Starting...")
        title = self.title.get()
        author = self.author.get()
        reading_style = "classic" if self.reading_style.get() == "Classic black and white" else "waypoint"
        worker = threading.Thread(target=self._worker, args=(url, destination, title, author, reading_style, overwrite), daemon=True)
        worker.start()

    def _worker(self, url: str, destination: str, title: str, author: str, reading_style: str, overwrite: bool) -> None:
        try:
            result = convert_url(
                url,
                destination,
                title=title,
                author=author,
                reading_style=reading_style,
                overwrite=overwrite,
                progress=lambda message: self.events.put(("status", message)),
                cancelled=self.cancel_event.is_set,
            )
            self.events.put(("success", result))
        except ConversionCancelled:
            self.events.put(("cancelled", None))
        except (ConversionError, OSError) as exc:
            self.events.put(("error", str(exc)))
        except Exception as exc:  # Keep GUI failures understandable while preserving the exception text.
            self.events.put(("error", f"Unexpected error: {exc}"))

    def _poll(self) -> None:
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event == "status":
                    self.status.set(str(payload))
                elif event == "success":
                    self._finished()
                    result = payload
                    assert isinstance(result, ConversionResult)
                    warning_note = f"\nSkipped artwork: {len(result.warnings)}" if result.warnings else ""
                    messagebox.showinfo("EPUB created", f"Saved {result.title}\n\n{result.output_path}\n\nEmbedded artwork: {result.image_count}{warning_note}")
                elif event == "error":
                    self._finished()
                    self.status.set("Conversion failed")
                    messagebox.showerror("Could not create EPUB", str(payload))
                elif event == "cancelled":
                    self._finished()
                    self.status.set("Cancelled")
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _finished(self) -> None:
        self.progress.stop()
        self.convert_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")
        self.status.set("Done")

    def _cancel(self) -> None:
        self.cancel_event.set()
        self.cancel_button.configure(state="disabled")
        self.status.set("Cancelling...")


def launch() -> None:
    root = tk.Tk()
    ConverterWindow(root)
    root.mainloop()

