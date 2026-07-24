NE INDIA // SECURITY INTEL DIGEST  —  Windows quick start
==========================================================

WHAT THIS IS
  A local dashboard of Northeast India news (52 sources, 8 states),
  refreshing itself every 30 minutes. Runs entirely on your PC.

ONE-TIME: INSTALL PYTHON
  1. Download Python 3.12 from  https://www.python.org/downloads/
  2. Run the installer. On the FIRST screen, TICK the box
     "Add python.exe to PATH".  Then click Install.

RUN THE APP
  1. Unzip this folder anywhere, e.g.  C:\ne-intel-digest
  2. Double-click  start-windows.bat
       - The first time, it installs everything (a few minutes).
       - Your browser opens automatically at  http://127.0.0.1:8642
       - The first news load takes 2-3 minutes, then it fills in and
         refreshes every 30 minutes by itself.
  3. To stop: close the black command window.
     To start again later: double-click start-windows.bat again.

OPEN IT MANUALLY (if the browser didn't open)
  Go to:  http://127.0.0.1:8642

VIEW FROM ANOTHER PC ON THE SAME NETWORK (optional)
  Instead of the .bat, open Command Prompt in this folder and run:
     python run.py serve --host 0.0.0.0
  Then on the other device browse to:  http://<this-PC-IP>:8642
  (find the IP with the command  ipconfig  -> IPv4 Address)

NOTES
  * The X/Twitter feeds are optional. To enable them, put your bearer
    token in:  C:\Users\<you>\Documents\x_bearer_token.txt
  * Everything else works with no keys.
  * Newspaper thumbnails load from the news sites (needs internet).
    The map works fully offline.
