import os
import re
import shutil
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


# ------------------------------------------------------------
# Configuration
# ------------------------------------------------------------

MIN_MB = 1
MAX_MB = 250

# Leave a little room below the requested size so the resulting
# file is reliably <= the target.
TARGET_RATIO = 0.9

# Audio bitrate used for the output.
AUDIO_BITRATE = 128_000

# FFmpeg video codec.
VIDEO_CODEC = "libx264"


# ------------------------------------------------------------
# FFmpeg / FFprobe helpers
# ------------------------------------------------------------

def find_executable(name):
    """Find an executable in PATH."""
    path = shutil.which(name)
    if path:
        return path

    # Windows fallback
    if os.name == "nt":
        path = shutil.which(name + ".exe")
        if path:
            return path

    return None


FFMPEG = find_executable("ffmpeg")
FFPROBE = find_executable("ffprobe")


def get_duration(filename):
    """Return video duration in seconds."""
    command = [
        FFPROBE,
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        filename
    ]

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    try:
        return float(result.stdout.strip())
    except (ValueError, TypeError):
        raise RuntimeError(
            "Could not determine the video's duration."
        )


def get_video_info(filename):
    """Return basic video information."""
    command = [
        FFPROBE,
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=s=x:p=0",
        filename
    ]

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    match = re.search(r"(\d+)x(\d+)", result.stdout)

    if not match:
        return None, None

    return int(match.group(1)), int(match.group(2))


# ------------------------------------------------------------
# Bitrate calculation
# ------------------------------------------------------------

def calculate_video_bitrate(duration, target_mb):
    """
    Calculate an approximate video bitrate that should result
    in a file close to the requested target size.

    A little space is reserved for the audio stream and container.
    """

    target_bytes = target_mb * 1_000_000 * TARGET_RATIO
    target_bits = target_bytes * 8

    # Reserve space for audio.
    audio_bits = AUDIO_BITRATE * duration

    video_bits = target_bits - audio_bits

    # Prevent impossible/negative bitrates.
    video_bitrate = max(video_bits / duration, 20_000)

    return int(video_bitrate)


# ------------------------------------------------------------
# Output filename
# ------------------------------------------------------------

def make_output_filename(input_file, target_mb):
    directory = os.path.dirname(input_file)
    base = os.path.splitext(os.path.basename(input_file))[0]

    output = os.path.join(
        directory,
        f"{base}_{target_mb}MB.mp4"
    )

    # Avoid overwriting an existing file.
    if not os.path.exists(output):
        return output

    counter = 2

    while True:
        output = os.path.join(
            directory,
            f"{base}_{target_mb}MB_{counter}.mp4"
        )

        if not os.path.exists(output):
            return output

        counter += 1


# ------------------------------------------------------------
# FFmpeg encoding
# ------------------------------------------------------------

def encode_video(input_file, output_file, target_mb, progress_callback):
    """
    Encode using two-pass H.264.

    Two-pass encoding gives FFmpeg much better control over
    achieving a specific final filesize.
    """

    duration = get_duration(input_file)

    video_bitrate = calculate_video_bitrate(
        duration,
        target_mb
    )

    bitrate_k = max(1, video_bitrate // 1000)

    # --------------------------------------------------------
    # Pass 1
    # --------------------------------------------------------

    null_output = "NUL" if os.name == "nt" else "/dev/null"

    passlog = os.path.join(
        os.path.dirname(output_file),
        ".compression_pass"
    )

    pass1_command = [
        FFMPEG,
        "-y",
        "-i", input_file,

        "-map", "0:v:0",

        "-c:v", VIDEO_CODEC,
        "-b:v", f"{bitrate_k}k",

        "-pass", "1",
        "-passlogfile", passlog,

        "-an",

        "-f", "null",
        null_output
    ]

    process = subprocess.Popen(
        pass1_command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        universal_newlines=True
    )

    for line in process.stderr:
        update_progress_from_ffmpeg(
            line,
            duration,
            progress_callback,
            pass_number=1
        )

    return_code = process.wait()

    if return_code != 0:
        cleanup_pass_files(passlog)
        raise RuntimeError(
            "FFmpeg failed during the first encoding pass."
        )

    # --------------------------------------------------------
    # Pass 2
    # --------------------------------------------------------

    pass2_command = [
        FFMPEG,
        "-y",
        "-i", input_file,

        "-map", "0:v:0",
        "-map", "0:a:0?",

        "-c:v", VIDEO_CODEC,
        "-b:v", f"{bitrate_k}k",

        "-preset", "medium",

        "-pass", "2",
        "-passlogfile", passlog,

        "-c:a", "aac",
        "-b:a", f"{AUDIO_BITRATE // 1000}k",

        "-movflags", "+faststart",

        output_file
    ]

    process = subprocess.Popen(
        pass2_command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        universal_newlines=True
    )

    for line in process.stderr:
        update_progress_from_ffmpeg(
            line,
            duration,
            progress_callback,
            pass_number=2
        )

    return_code = process.wait()

    cleanup_pass_files(passlog)

    if return_code != 0:
        if os.path.exists(output_file):
            try:
                os.remove(output_file)
            except OSError:
                pass

        raise RuntimeError(
            "FFmpeg failed during the second encoding pass."
        )


def update_progress_from_ffmpeg(
    line,
    duration,
    callback,
    pass_number
):
    """Extract FFmpeg's current timestamp from stderr."""

    match = re.search(
        r"time=(\d+):(\d+):(\d+)\.(\d+)",
        line
    )

    if not match:
        return

    hours = int(match.group(1))
    minutes = int(match.group(2))
    seconds = int(match.group(3))

    current_time = (
        hours * 3600 +
        minutes * 60 +
        seconds
    )

    fraction = min(
        1.0,
        current_time / max(duration, 0.01)
    )

    # Pass 1 = 0-50%
    # Pass 2 = 50-100%
    if pass_number == 1:
        progress = fraction * 50
    else:
        progress = 50 + fraction * 50

    callback(progress)


def cleanup_pass_files(passlog):
    """Delete FFmpeg's temporary two-pass files."""

    directory = os.path.dirname(passlog)
    filename = os.path.basename(passlog)

    for name in os.listdir(directory):
        if name.startswith(filename):
            try:
                os.remove(os.path.join(directory, name))
            except OSError:
                pass


# ------------------------------------------------------------
# GUI
# ------------------------------------------------------------

class VideoCompressor:

    def __init__(self, root):
        self.root = root

        self.root.title("31-79JGb215")
        self.root.resizable(False, False)

        self.files = []
        self.processing = False

        self.target_var = tk.StringVar(value="5")
        self.status_var = tk.StringVar(
            value="Select one or more videos."
        )

        self.build_ui()

    # --------------------------------------------------------
    # UI
    # --------------------------------------------------------

    def build_ui(self):

        main = ttk.Frame(
            self.root,
            padding=18
        )

        main.grid(row=0, column=0)

        title = ttk.Label(
            main,
            text="shrink-ray",
            font=("TkDefaultFont", 10, "bold")
        )

        title.grid(
            row=0,
            column=0,
            columnspan=2,
            pady=(0, 15)
        )

        # ----------------------------------------------------
        # Select files
        # ----------------------------------------------------

        self.select_button = ttk.Button(
            main,
            text="Select Videos...",
            command=self.select_files
        )

        self.select_button.grid(
            row=1,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(0, 12)
        )

        # ----------------------------------------------------
        # Target size
        # ----------------------------------------------------

        ttk.Label(
            main,
            text="Target size:"
        ).grid(
            row=2,
            column=0,
            sticky="w"
        )

        target_frame = ttk.Frame(main)

        target_frame.grid(
            row=2,
            column=1,
            sticky="e"
        )

        self.target_entry = ttk.Spinbox(
            target_frame,
            from_=MIN_MB,
            to=MAX_MB,
            textvariable=self.target_var,
            width=7
        )

        self.target_entry.pack(
            side="left"
        )

        ttk.Label(
            target_frame,
            text=" MB"
        ).pack(
            side="left",
            padx=(5, 0)
        )

        # ----------------------------------------------------
        # Selected file count
        # ----------------------------------------------------

        self.file_label = ttk.Label(
            main,
            text="No files selected."
        )

        self.file_label.grid(
            row=3,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(12, 5)
        )

        # ----------------------------------------------------
        # Progress
        # ----------------------------------------------------

        self.progress = ttk.Progressbar(
            main,
            orient="horizontal",
            length=300,
            mode="determinate",
            maximum=100
        )

        self.progress.grid(
            row=4,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(8, 5)
        )

        # ----------------------------------------------------
        # Status
        # ----------------------------------------------------

        self.status_label = ttk.Label(
            main,
            textvariable=self.status_var,
            wraplength=320
        )

        self.status_label.grid(
            row=5,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(0, 12)
        )

        # ----------------------------------------------------
        # Compress button
        # ----------------------------------------------------

        self.compress_button = ttk.Button(
            main,
            text="Blast",
            command=self.start_compression
        )

        self.compress_button.grid(
            row=6,
            column=0,
            columnspan=2,
            sticky="ew"
        )

    # --------------------------------------------------------
    # File selection
    # --------------------------------------------------------

    def select_files(self):

        if self.processing:
            return

        files = filedialog.askopenfilenames(
            title="Select videos",
            filetypes=[
                (
                    "Video files",
                    "*.mp4 *.mkv *.avi *.mov *.webm *.wmv *.flv *.m4v *.mpeg *.mpg"
                ),
                (
                    "All files",
                    "*.*"
                )
            ]
        )

        if not files:
            return

        self.files = list(files)

        count = len(self.files)

        if count == 1:
            self.file_label.config(
                text=os.path.basename(self.files[0])
            )
        else:
            self.file_label.config(
                text=f"{count} videos selected."
            )

        self.status_var.set(
            "Ready to blast."
        )

    # --------------------------------------------------------
    # Target validation
    # --------------------------------------------------------

    def get_target_mb(self):

        try:
            value = int(self.target_var.get())
        except ValueError:
            raise ValueError(
                "Target size must be a whole number."
            )

        if value < MIN_MB or value > MAX_MB:
            raise ValueError(
                f"Target size must be between "
                f"{MIN_MB} and {MAX_MB} MB."
            )

        return value

    # --------------------------------------------------------
    # Start compression
    # --------------------------------------------------------

    def start_compression(self):

        if self.processing:
            return

        if not self.files:
            messagebox.showwarning(
                "No videos",
                "Select one or more videos first."
            )
            return

        try:
            target_mb = self.get_target_mb()
        except ValueError as error:
            messagebox.showerror(
                "Invalid target size",
                str(error)
            )
            return

        if not FFMPEG or not FFPROBE:
            messagebox.showerror(
                "FFmpeg not found",
                "FFmpeg and FFprobe could not be found.\n\n"
                "Install FFmpeg and make sure ffmpeg.exe "
                "and ffprobe.exe are available in your PATH."
            )
            return

        self.processing = True

        self.select_button.config(
            state="disabled"
        )

        self.compress_button.config(
            state="disabled"
        )

        self.target_entry.config(
            state="disabled"
        )

        self.progress["value"] = 0

        thread = threading.Thread(
            target=self.compress_all,
            args=(target_mb,),
            daemon=True
        )

        thread.start()

    # --------------------------------------------------------
    # Compress all files
    # --------------------------------------------------------

    def compress_all(self, target_mb):

        total_files = len(self.files)

        completed = 0

        for index, input_file in enumerate(self.files):

            filename = os.path.basename(input_file)

            self.set_status(
                f"Blasting {index + 1} of "
                f"{total_files}: {filename}"
            )

            output_file = make_output_filename(
                input_file,
                target_mb
            )

            try:

                def callback(progress):
                    overall = (
                        (completed + progress / 100)
                        / total_files
                    ) * 100

                    self.set_progress(overall)

                encode_video(
                    input_file,
                    output_file,
                    target_mb,
                    callback
                )

                completed += 1

            except Exception as error:

                self.root.after(
                    0,
                    lambda e=str(error), f=filename:
                    self.show_error(f, e)
                )

                self.processing = False

                self.root.after(
                    0,
                    self.enable_controls
                )

                return

        self.set_progress(100)

        self.processing = False

        self.root.after(
            0,
            self.compression_finished
        )

    # --------------------------------------------------------
    # Thread-safe GUI updates
    # --------------------------------------------------------

    def set_progress(self, value):

        self.root.after(
            0,
            lambda: self.progress.configure(
                value=value
            )
        )

    def set_status(self, text):

        self.root.after(
            0,
            lambda: self.status_var.set(text)
        )

    # --------------------------------------------------------
    # Completion
    # --------------------------------------------------------

    def compression_finished(self):

        self.enable_controls()

        self.status_var.set(
            "Compression complete."
        )

        messagebox.showinfo(
            "Complete",
            f"Compressed {len(self.files)} "
            f"video(s) successfully."
        )

    # --------------------------------------------------------
    # Error
    # --------------------------------------------------------

    def show_error(self, filename, error):

        messagebox.showerror(
            "Compression failed",
            f"Failed to compress:\n\n"
            f"{filename}\n\n"
            f"{error}"
        )

    # --------------------------------------------------------
    # Re-enable controls
    # --------------------------------------------------------

    def enable_controls(self):

        self.select_button.config(
            state="normal"
        )

        self.compress_button.config(
            state="normal"
        )

        self.target_entry.config(
            state="normal"
        )


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------

def main():

    if not FFMPEG or not FFPROBE:

        # Still launch the GUI so the user gets a useful
        # error message rather than a Python console traceback.
        pass

    root = tk.Tk()

    VideoCompressor(root)

    root.mainloop()


if __name__ == "__main__":
    main()