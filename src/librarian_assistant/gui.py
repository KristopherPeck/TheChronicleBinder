"""Small Tkinter desktop interface."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .catalog import KNOWN_CHRONICLES, Chronicle
from .converter import ConversionCancelled, ConversionError, ConversionResult, convert_url


CUSTOM_SOURCE = "Custom URL..."
BATCH_COOLDOWN_SECONDS = 2.0


@dataclass(frozen=True)
class BatchResult:
    completed: tuple[ConversionResult, ...]
    failures: tuple[tuple[str, str], ...]


class ConverterWindow:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("The Haruspis Assistant")
        self.root.minsize(720, 500)
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.chronicle = tk.StringVar(value=CUSTOM_SOURCE)
        self.url = tk.StringVar()
        self.folder = tk.StringVar()
        self.title = tk.StringVar()
        self.author = tk.StringVar()
        self.reading_style = tk.StringVar(value="Official-inspired color")
        self.replace_existing = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="Ready")
        self.cancel_event = threading.Event()
        self.catalog_by_title = {entry.title: entry for entry in KNOWN_CHRONICLES}
        self.catalog_urls = {entry.url for entry in KNOWN_CHRONICLES}
        self._build()
        self.root.after(100, self._poll)

    def _build(self) -> None:
        style = ttk.Style()
        style.configure("Title.TLabel", font=("Segoe UI", 20, "bold"), foreground="#184f5c")
        style.configure("Hint.TLabel", foreground="#53666d")
        outer = ttk.Frame(self.root, padding=24)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="The Haruspis Assistant", style="Title.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(outer, text="Archive Halo Waypoint Chronicles as polished, offline EPUBs.", style="Hint.TLabel").grid(row=1, column=0, columnspan=3, sticky="w", pady=(2, 22))

        ttk.Label(outer, text="Chronicle").grid(row=2, column=0, sticky="w", pady=5)
        self.chronicle_picker = ttk.Combobox(
            outer,
            textvariable=self.chronicle,
            values=(CUSTOM_SOURCE, *(entry.title for entry in KNOWN_CHRONICLES)),
            state="readonly",
        )
        self.chronicle_picker.grid(row=2, column=1, columnspan=2, sticky="ew", pady=5)
        self.chronicle_picker.bind("<<ComboboxSelected>>", self._source_changed)

        ttk.Label(outer, text="Article URL").grid(row=3, column=0, sticky="w", pady=5)
        self.url_entry = ttk.Entry(outer, textvariable=self.url)
        self.url_entry.grid(row=3, column=1, columnspan=2, sticky="ew", pady=5)

        ttk.Label(outer, text="Save folder").grid(row=4, column=0, sticky="w", pady=5)
        ttk.Entry(outer, textvariable=self.folder).grid(row=4, column=1, sticky="ew", pady=5)
        ttk.Button(outer, text="Browse...", command=self._browse).grid(row=4, column=2, sticky="ew", padx=(8, 0), pady=5)

        ttk.Label(outer, text="Title override").grid(row=5, column=0, sticky="w", pady=5)
        ttk.Entry(outer, textvariable=self.title).grid(row=5, column=1, columnspan=2, sticky="ew", pady=5)
        ttk.Label(outer, text="Author override").grid(row=6, column=0, sticky="w", pady=5)
        ttk.Entry(outer, textvariable=self.author).grid(row=6, column=1, columnspan=2, sticky="ew", pady=5)

        ttk.Label(outer, text="Reading style").grid(row=7, column=0, sticky="w", pady=5)
        style_picker = ttk.Combobox(
            outer,
            textvariable=self.reading_style,
            values=("Official-inspired color", "Classic black and white"),
            state="readonly",
        )
        style_picker.grid(row=7, column=1, columnspan=2, sticky="ew", pady=5)

        replace = ttk.Checkbutton(
            outer,
            text="Replace existing EPUBs with the same detected title",
            variable=self.replace_existing,
        )
        replace.grid(row=8, column=1, columnspan=2, sticky="w", pady=(5, 2))
        ttk.Label(
            outer,
            text="Files use the detected title automatically. Overrides apply only to a single export.",
            style="Hint.TLabel",
        ).grid(row=9, column=1, columnspan=2, sticky="w", pady=(0, 18))

        self.progress = ttk.Progressbar(outer, mode="indeterminate")
        self.progress.grid(row=10, column=0, columnspan=3, sticky="ew", pady=(5, 7))
        ttk.Label(outer, textvariable=self.status, style="Hint.TLabel").grid(row=11, column=0, columnspan=2, sticky="w")
        actions = ttk.Frame(outer)
        actions.grid(row=11, column=2, sticky="e")
        self.cancel_button = ttk.Button(actions, text="Cancel", command=self._cancel, state="disabled")
        self.cancel_button.pack(side="left", padx=(0, 8))
        self.all_button = ttk.Button(actions, text=f"Download All ({len(KNOWN_CHRONICLES)})", command=self._start_all)
        self.all_button.pack(side="left", padx=(0, 8))
        self.convert_button = ttk.Button(actions, text="Create EPUB", command=self._start)
        self.convert_button.pack(side="left")
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(12, weight=1)
        self.url_entry.focus_set()

    def _source_changed(self, _event: object = None) -> None:
        selected = self.chronicle.get()
        self.url_entry.configure(state="normal")
        if selected == CUSTOM_SOURCE:
            if self.url.get() in self.catalog_urls:
                self.url.set("")
            self.url_entry.focus_set()
            return
        entry = self.catalog_by_title[selected]
        self.url.set(entry.url)
        self.url_entry.configure(state="readonly")

    def _browse(self) -> bool:
        path = filedialog.askdirectory(title="Choose EPUB archive folder", mustexist=True)
        if path:
            self.folder.set(path)
            return True
        return False

    def _selected_folder(self) -> Path | None:
        value = self.folder.get().strip()
        if not value:
            if not self._browse():
                return None
            value = self.folder.get().strip()
        folder = Path(value).expanduser()
        if not folder.is_dir():
            messagebox.showwarning("Folder required", "Choose an existing folder for the generated EPUB files.")
            return None
        return folder

    def _reading_style(self) -> str:
        return "classic" if self.reading_style.get() == "Classic black and white" else "waypoint"

    def _begin(self, status: str) -> None:
        self.convert_button.configure(state="disabled")
        self.all_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.cancel_event.clear()
        self.progress.start(12)
        self.status.set(status)

    def _start(self) -> None:
        url = self.url.get().strip()
        if not url:
            messagebox.showwarning("Article URL required", "Choose a Chronicle or enter a custom Halo Waypoint article URL.")
            return
        folder = self._selected_folder()
        if folder is None:
            return
        self._begin("Starting...")
        worker = threading.Thread(
            target=self._worker,
            args=(
                url,
                folder,
                self.title.get(),
                self.author.get(),
                self._reading_style(),
                self.replace_existing.get(),
            ),
            daemon=True,
        )
        worker.start()

    def _start_all(self) -> None:
        folder = self._selected_folder()
        if folder is None:
            return
        replace_note = " Existing files will be replaced." if self.replace_existing.get() else " Existing files will be skipped."
        if not messagebox.askyesno(
            "Download all Chronicles?",
            f"Download {len(KNOWN_CHRONICLES)} known Waypoint Chronicles into:\n\n{folder}\n\n"
            f"The app will pause {BATCH_COOLDOWN_SECONDS:g} seconds between articles.{replace_note}",
        ):
            return
        self._begin(f"Starting archive of {len(KNOWN_CHRONICLES)} Chronicles...")
        worker = threading.Thread(
            target=self._batch_worker,
            args=(KNOWN_CHRONICLES, folder, self._reading_style(), self.replace_existing.get()),
            daemon=True,
        )
        worker.start()

    def _worker(
        self,
        url: str,
        folder: Path,
        title: str,
        author: str,
        reading_style: str,
        overwrite: bool,
    ) -> None:
        try:
            result = convert_url(
                url,
                output_directory=folder,
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

    def _batch_worker(
        self,
        chronicles: tuple[Chronicle, ...],
        folder: Path,
        reading_style: str,
        overwrite: bool,
    ) -> None:
        completed: list[ConversionResult] = []
        failures: list[tuple[str, str]] = []
        total = len(chronicles)
        try:
            for index, chronicle in enumerate(chronicles, start=1):
                if self.cancel_event.is_set():
                    raise ConversionCancelled("Conversion cancelled.")
                self.events.put(("status", f"[{index}/{total}] Starting {chronicle.title}..."))
                try:
                    result = convert_url(
                        chronicle.url,
                        output_directory=folder,
                        reading_style=reading_style,
                        overwrite=overwrite,
                        progress=lambda message, position=index: self.events.put(
                            ("status", f"[{position}/{total}] {chronicle.title}: {message}")
                        ),
                        cancelled=self.cancel_event.is_set,
                    )
                    completed.append(result)
                except ConversionCancelled:
                    raise
                except (ConversionError, OSError) as exc:
                    failures.append((chronicle.title, str(exc)))
                except Exception as exc:  # Continue the archive when one article has an unexpected failure.
                    failures.append((chronicle.title, f"Unexpected error: {exc}"))
                if index < total:
                    self.events.put(("status", f"[{index}/{total}] Waiting {BATCH_COOLDOWN_SECONDS:g} seconds..."))
                    if self.cancel_event.wait(BATCH_COOLDOWN_SECONDS):
                        raise ConversionCancelled("Conversion cancelled.")
            self.events.put(("batch_success", BatchResult(tuple(completed), tuple(failures))))
        except ConversionCancelled:
            self.events.put(("cancelled", None))

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
                elif event == "batch_success":
                    summary = payload
                    assert isinstance(summary, BatchResult)
                    self._finished()
                    self._show_batch_result(summary)
                elif event == "error":
                    self._finished("Conversion failed")
                    messagebox.showerror("Could not create EPUB", str(payload))
                elif event == "cancelled":
                    self._finished("Cancelled")
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _show_batch_result(self, summary: BatchResult) -> None:
        warning_count = sum(len(result.warnings) for result in summary.completed)
        self.status.set(f"Archive complete: {len(summary.completed)} saved, {len(summary.failures)} skipped or failed")
        details = ""
        if summary.failures:
            shown = "\n".join(f"- {title}: {reason}" for title, reason in summary.failures[:5])
            remainder = len(summary.failures) - 5
            details = f"\n\nSkipped or failed:\n{shown}"
            if remainder:
                details += f"\n- ...and {remainder} more"
        messagebox.showinfo(
            "Archive complete",
            f"Saved: {len(summary.completed)}\nSkipped or failed: {len(summary.failures)}\n"
            f"Skipped artwork: {warning_count}\n\nFolder: {self.folder.get()}{details}",
        )

    def _finished(self, status: str = "Done") -> None:
        self.progress.stop()
        self.convert_button.configure(state="normal")
        self.all_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")
        self.status.set(status)

    def _cancel(self) -> None:
        self.cancel_event.set()
        self.cancel_button.configure(state="disabled")
        self.status.set("Cancelling...")


def launch() -> None:
    root = tk.Tk()
    ConverterWindow(root)
    root.mainloop()
