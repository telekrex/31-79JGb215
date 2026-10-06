import sys, os
import subprocess
from tkinter import *
from tkinter import filedialog
from PIL import ImageTk, Image
from random import randrange
window = Tk()


width = window.winfo_screenwidth() * .6
height = window.winfo_screenheight() * .6
size = f'{round(width)}x{round(height)}'
window.geometry(size)


def grab_files(from_path):
    from_path = os.path.normpath(from_path)
    folder = os.path.dirname(from_path)
    extensions = ('.png', '.jpg', '.jpeg', '.bmp', '.tif', '.webp')
    files = [
        os.path.normpath(os.path.join(folder, item))
        for item in os.listdir(folder)
        if item.lower().endswith(extensions)
    ]
    return files, files.index(from_path)


if len(sys.argv) > 1:
    all_files, current_file_index = grab_files(''.join(sys.argv[1:]))


def open_folder(event):
    global current_file
    global all_files
    global current_file_index
    current_file = filedialog.askopenfilename(
        initialdir="/", 
        title="Open an image and its surrounding images", 
        filetypes = (
            ("images","*jpeg;*.jpg;*.png;*.bmp;*.tif;*.webp"),
            ("all files","*.*")
            )
    )
    if current_file:
        all_files, current_file_index = grab_files(current_file)
        update()


def exit_application(event):
    sys.exit()


window.bind('<Escape>', exit_application) # just to last image in group
update()
window.mainloop()