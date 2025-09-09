import tkinter as tk, ttkbootstrap as ttk
from tkinter import messagebox, filedialog
import json
from pathlib import Path
import os, sys, math
import threading, multiprocessing

import sync
import speedtest as hf_speedtest

if getattr(sys, 'frozen', False):
    sys.stdout = open(os.devnull, 'w')
    sys.stderr = open(os.devnull, 'w')

ICON_PATH = Path(__file__).parent / "Icon.ico"
CONFIG_FILE = Path(sys.executable).parent / "config.json"

class UpdaterApp:
    def __init__(self, root):
        self.root = root
        if ICON_PATH.exists():
            self.root.iconbitmap(str(ICON_PATH))

        self.root.title("Texture++ Installer")
        self.root.resizable(False, False)

        self.selected_variant = tk.StringVar(value="Core")
        self.mods_folder_path = tk.StringVar()
        self.use_mirror = tk.BooleanVar(value=False)
        self.status_message = tk.StringVar(value="Initializing...")
        self.stop_event = threading.Event()
        self.display_install_path = tk.StringVar()
        self.estimated_download_time = tk.StringVar(value="")
        self.download_speed_mbps = None # To store the measured download speed
        self.use_mirror_from_config = False # Flag to check if mirror setting is from config

        self.load_config()

        self.selected_variant.trace_add('write', self.update_display_path)
        self.mods_folder_path.trace_add('write', self.update_display_path)
        self.update_display_path()

        container = ttk.Frame(self.root)
        container.pack(fill="both", expand=True)
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)

        self.frames = {}
        for F in (StartFrame, SelectionFrame, ProgressFrame):
            frame = F(container, self)
            self.frames[F] = frame
            frame.grid(row=0, column=0, sticky="nsew")

        if self.mods_folder_path.get() and Path(self.mods_folder_path.get()).is_dir():
            self.show_frame(SelectionFrame)
        else:
            self.show_frame(StartFrame)

        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.root.after(100, self.start_speedtest_thread)

    def start_speedtest_thread(self):
        """Starts the network and speed test process in a background thread."""
        threading.Thread(target=self.perform_speed_test, daemon=True).start()

    def perform_speed_test(self):
        """
        Determines mirror usage (if not set in config) and measures download speed.
        This method is designed to run in a separate thread.
        """
        try:
            use_mirror_val = self.use_mirror.get()
            if not self.use_mirror_from_config:
                # If 'use_mirror' was not in the config file, auto-detect the best setting.
                use_mirror_val = hf_speedtest.determine_mirror_setting()
                self.root.after(0, self.use_mirror.set, use_mirror_val)

            # Perform the speed test using the determined mirror setting.
            speed = hf_speedtest.measure_download_speed(use_mirror=use_mirror_val)
            self.download_speed_mbps = speed

        except Exception:
            self.download_speed_mbps = 0
        finally:
            # Once the test is complete (or failed), update the UI.
            self.root.after(0, self.calculate_estimated_download_time)

    def update_window_size(self):
        """Forces the window to update its size to fit all widgets."""
        self.root.update_idletasks()
        self.center_window()

    def update_display_path(self, *args):
        mods_path = self.mods_folder_path.get()
        variant_name = self.selected_variant.get()
        if mods_path and variant_name in sync.VARIANT_MAP:
            variant_details = sync.VARIANT_MAP[variant_name]
            full_path = Path(mods_path) / variant_details["local_dir"]
            self.display_install_path.set(str(full_path))
            self.root.after(100, self.calculate_estimated_download_time)
            self.root.after(110, self.update_window_size)
        elif mods_path:
            self.display_install_path.set(mods_path)
            self.estimated_download_time.set("")
            self.root.after(110, self.update_window_size)
        else:
            self.display_install_path.set("Please select a Mods folder...")
            self.estimated_download_time.set("")
            self.root.after(110, self.update_window_size)

    def calculate_estimated_download_time(self):
        if self.selected_variant.get() == "Advanced":
            self.estimated_download_time.set("Estimate unavailable for Advanced.")
            return
        variant_details = sync.VARIANT_MAP.get(self.selected_variant.get())
        mods_folder = self.mods_folder_path.get()
        if variant_details and mods_folder:
            install_path = Path(mods_folder) / variant_details["local_dir"]
            if install_path.exists():
                self.estimated_download_time.set("Existing installation found. It will be updated and repaired.")
                return

            if self.download_speed_mbps is None:
                self.estimated_download_time.set("Measuring internet speed...")
                return
            if self.download_speed_mbps <= 0:
                self.estimated_download_time.set("Cannot estimate internet speed.")
                return

            if "size_gb" in variant_details:
                base_seconds = variant_details["size_gb"] * 8 * 1000 / self.download_speed_mbps
                folders = variant_details.get("repo_folders", [])
                extra_seconds = ( 30 * ("Base_4X" in folders) + 60 * (("Core_2X" in folders) or ("Core_4X" in folders)))
                total_seconds = base_seconds + extra_seconds
                if total_seconds < 60:
                    time_str = f"{round(total_seconds)} seconds"
                elif total_seconds < 3600:
                    time_str = f"{round(total_seconds/60)} minutes"
                else:
                    time_str = f"{round(total_seconds/3600)} hours"
                self.estimated_download_time.set(f"Estimated download time: {time_str}")
            else:
                self.estimated_download_time.set("No variant selected or size not available.")

    def on_closing(self):
        self.stop_event.set()
        self.root.destroy()

    def show_frame(self, frame_class):
        frame = self.frames[frame_class]
        frame.tkraise()
        self.update_window_size()

    def center_window(self):
        self.root.update_idletasks()
        w, h = self.root.winfo_reqwidth(), self.root.winfo_reqheight()
        x, y = (self.root.winfo_screenwidth() - w) // 2, (self.root.winfo_screenheight() - h) // 2
        self.root.geometry(f'{w}x{h}+{x}+{y}')

    def load_config(self):
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, 'r') as f:
                    config = json.load(f)
                self.mods_folder_path.set(config.get("mods_folder", ""))
                variant_from_config = config.get("variant", "Core")
                # Fallback to "Core" if the loaded variant is "Advanced" or invalid
                if variant_from_config == "Advanced" or variant_from_config not in sync.VARIANT_MAP:
                    self.selected_variant.set("Core")
                else:
                    self.selected_variant.set(variant_from_config)

                if 'use_mirror' in config:
                    self.use_mirror_from_config = True
                self.use_mirror.set(config.get("use_mirror", False))
            except (json.JSONDecodeError, IOError):
                self.selected_variant.set("Core")
                self.use_mirror.set(False)
        else:
            # If no config file, ensure defaults are set
            self.selected_variant.set("Core")
            self.use_mirror.set(False)

    def save_config(self):
        config = {
            "mods_folder": self.mods_folder_path.get(),
            "variant": self.selected_variant.get(),
            "use_mirror": self.use_mirror.get()
        }
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(config, f, indent=4)
        except IOError:
            messagebox.showwarning("Config Error", "Could not save settings.")

    def prompt_for_folder_then_show_variants(self):
        if folder_path := filedialog.askdirectory(title="Select your main Mods folder"):
            self.mods_folder_path.set(folder_path)
            self.save_config()
            self.show_frame(SelectionFrame)

    def change_mods_folder_path(self):
        if folder_path := filedialog.askdirectory(title="Select your main Mods folder"):
            self.mods_folder_path.set(folder_path)
            self.save_config()

    def start_process(self):
        variant, mods_folder = self.selected_variant.get(), self.mods_folder_path.get()
        if not (variant and mods_folder):
            messagebox.showwarning("Incomplete", "Please select a variant and a Mods folder.")
            return

        if variant == "Advanced":
            selection_frame = self.frames[SelectionFrame]
            selected_components = [opt for opt, var in selection_frame.advanced_vars.items() if var.get()]
            if not selected_components:
                messagebox.showwarning("Incomplete", "Please select at least one component for the Advanced variant.")
                return
            sync.VARIANT_MAP["Advanced"]["repo_folders"] = selected_components

        self.save_config()
        self.show_frame(ProgressFrame)

        threading.Thread(
            target=self.run_sync_worker,
            args=(mods_folder, variant, self.use_mirror.get(), self.stop_event),
            daemon=True
        ).start()

    def run_sync_worker(self, mods_folder, variant, use_mirror, stop_event):
        try:
            success, message = sync.sync_repo(
                mods_folder, variant, use_mirror,
                status_callback=lambda msg: self.root.after(0, self.status_message.set, msg),
                stop_event=stop_event, download_speed_mbps=self.download_speed_mbps
            )
        except Exception as e:
            success, message = False, f"A critical error occurred: {e}"
        finally:
            if not stop_event.is_set():
                self.root.after(0, self.on_sync_complete, success, message)
            else:
                self.root.after(0, self.status_message.set, "Process interrupted by user.")
                self.root.after(1000, self.root.destroy)

    def on_sync_complete(self, success, message):
        if success:
            messagebox.showinfo("Success", message)
        else:
            messagebox.showerror("Error", message)
        self.root.destroy()

class StartFrame(ttk.Frame):
    def __init__(self, parent, controller):
        super().__init__(parent, padding=25)
        msg = ("\nWelcome to the Texture++ Installer. \n\n"
               "It will handle install, update, and repair for the Texture++ Mod. \n\n"
               "Click 'Start' to select your Mods folder.\n\n")
        ttk.Label(self, text=msg, wraplength=600, font="-size 10", justify="left").pack(pady=(0, 20))
        ttk.Button(self, text="Start", command=controller.prompt_for_folder_then_show_variants, bootstyle="success").pack()

class SelectionFrame(ttk.Frame):
    def __init__(self, parent, controller):
        super().__init__(parent, padding=25)
        self.controller = controller
        variants = [
            {"value": "Mini", "text": "Mini (~1GB)"},
            {"value": "Base", "text": "Base (~12GB)"},
            {"value": "Core", "text": "Core (~15GB)"},
            {"value": "Core 4K", "text": "Core 4K (~26GB)"},
            {"value": "Advanced", "text": "Advanced (Custom)"},
        ]

        ttk.Label(self, text="Choose a Texture Variant", font="-size 14 -weight bold").pack(pady=(0, 15))
        radio_frame = ttk.Frame(self)
        radio_frame.pack(pady=2)
        for variant_info in variants:
            ttk.Radiobutton(
                radio_frame,
                text=variant_info["text"],
                variable=controller.selected_variant,
                value=variant_info["value"]
            ).pack(anchor="w", padx=20, pady=2)

        self.advanced_frame = ttk.Frame(self)
        self.advanced_options = ["Base_4X", "Core_2X", "Core_4X"]
        self.advanced_vars = {opt: tk.BooleanVar(value=False) for opt in self.advanced_options}
        
        for option in self.advanced_options:
            ttk.Checkbutton(
                self.advanced_frame,
                text=option,
                variable=self.advanced_vars[option]
            ).pack(anchor="w", padx=20)
            
        self.advanced_frame.pack_forget()
        controller.selected_variant.trace_add('write', self.toggle_advanced_options)

        self.separator = ttk.Separator(self)
        self.separator.pack(fill='x', pady=15, padx=20)
        
        ttk.Label(self, textvariable=controller.estimated_download_time,
                  font="-size 10", wraplength=500, justify="center").pack(pady=(5, 10))

        lf = ttk.Labelframe(self, text="Selected Install Location", bootstyle="info")
        lf.pack(fill="x", padx=20, pady=5)
        folder_display_frame = ttk.Frame(lf)
        folder_display_frame.pack(fill="x", padx=10, pady=5)
        ttk.Label(folder_display_frame, textvariable=controller.display_install_path, wraplength=450).pack(side="left", fill="x", expand=True)
        ttk.Button(folder_display_frame, text="Change", command=controller.change_mods_folder_path, bootstyle="secondary").pack(side="right", padx=(10, 0))

        ttk.Checkbutton(self, text="Use Mirror", variable=controller.use_mirror, bootstyle="primary").pack(pady=10)

        btn_frame = ttk.Frame(self)
        btn_frame.pack(pady=10)
        ttk.Button(btn_frame, text="Confirm", command=controller.start_process, bootstyle="success").pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Quit", command=controller.root.destroy, bootstyle="secondary").pack(side="left", padx=5)

    def toggle_advanced_options(self, *args):
        if self.controller.selected_variant.get() == "Advanced":
            self.advanced_frame.pack(pady=5, before=self.separator)
        else:
            self.advanced_frame.pack_forget()
        self.controller.update_window_size()

class ProgressFrame(ttk.Frame):
    def __init__(self, parent, controller):
        super().__init__(parent, padding=40)
        ttk.Label(self, text="Installing...", font="-size 24 -weight bold").pack(pady=10)
        progress = ttk.Progressbar(self, mode='indeterminate', bootstyle="success-striped")
        progress.pack(fill="x", pady=10)
        progress.start()
        ttk.Label(self, textvariable=controller.status_message, wraplength=500, justify="center").pack(pady=(10, 0))

if __name__ == '__main__':
    multiprocessing.freeze_support()

    root = ttk.Window(themename="litera")
    app = UpdaterApp(root)
    root.mainloop()
