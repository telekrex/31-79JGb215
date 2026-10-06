echo verifying requirements
pip install -r .packages
echo building with pyinstaller - no console, one file, named Viewport, icon icon.ico, pack /assets folder
python -m PyInstaller --noconsole --onefile viewport.py -n Viewport --icon icon.ico --add-data=assets:.
echo copying files & assets
pandoc -s -o "dist/README.html" "../README.md"
copy "%~dp0\..\Screenshot.png" "%~dp0\dist"
copy "%~dp0\NoImage.png" "%~dp0\dist"